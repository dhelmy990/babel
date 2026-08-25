# Friday Demo Online Worker

This worker runs the trusted Slice 3 path: the simulator posts a synchronous
recommendation, publishes one observable event to `babel.feedback.v1`, the
single trainer updates its working copy, and shutdown creates a bounded export
and immutable child model. Kafka carries feedback JSON only. Model state moves
through local atomic directories.

## Install and start Kafka

From the repository root:

```bash
cd online
uv sync --extra dev --extra kafka --extra parquet
cd ..
docker compose up -d kafka
docker compose ps kafka
```

The Compose service is Apache Kafka 4.3.1 in single-node KRaft mode. Its only
host listener is `127.0.0.1:29092`. The demo topic has one partition and is
created on the first acknowledged publication.

## Assemble the worker

Use the package APIs rather than copying transport or checkpoint logic:

```python
from babel_online.feedback import KafkaFeedbackConsumer, KafkaFeedbackProducer
from babel_online.runtime import OnlineDemoSupervisor
from babel_online.simulation import RecommendationClient, SimulationEngine, SourceSampler
from babel_online.training import AtomicSynchronizer, NumpyWorkingModel, OnlineTrainer

producer = KafkaFeedbackProducer("127.0.0.1:29092")
consumer = KafkaFeedbackConsumer(
    "127.0.0.1:29092",
    group_id="babel-online-trainer-v1",
)
trainer = OnlineTrainer(
    model=working_model,
    consumer=consumer,
    checkpoint_root="state/online/checkpoints",
)
trainer.restore_latest()
trainer.run_until_stopped(
    stop_requested=stop_event.is_set,
    checkpoint_every_events=100,
)
```

`working_model` is a `NumpyWorkingModel` constructed from the fixture's frozen
100-dimensional item vectors. Only its residuals change. Use
`materialized_vectors()` when constructing Lane A `VectorRecord` replacements
for `ServingState.apply_sync`.

Construct `SimulationEngine` with the hidden-rank callback and fixture-backed
store. The store boundary must expose `source_is_available(...)`,
`creator_history(...)`, `pending_babel(...)`, `stage_babel(...)`,
`finalize_babel(...)`, and `created_babel_ids(run_id)`. Staging reserves the
source but does not add it to completed creator history; finalization happens
only after acknowledged feedback publication. A persisted pending Babel lets a
crashed worker reconstruct the same request and event IDs for retry.
Persistent creator history restores the event sequence and request history;
persisted source identity lets a restart skip already-created sources. Its
public `step()` order is fixed:

1. take one creator-local source without replacement;
2. stage its synthetic Babel;
3. synchronously call `RecommendationClient.recommend()`;
4. privately decide include, exclude, or ignore;
5. publish an acknowledged creator-keyed `FeedbackEventV1`;
6. expose accepted include edges.

The engine rejects catalog/non-created candidates and the request creator's
own Babels. Hidden ranks and draws do not appear in the event or export.

## Checkpoint, synchronization, and stop

`OnlineTrainer.checkpoint_and_commit()` writes
`checkpoint-step-N.partial`, fsyncs its state and checksum manifest, renames it,
and only then commits the matching next offsets. `restore_latest()` ignores
partial directories, restores the working state and RNG, then seeks to those
durable offsets.

Use `AtomicSynchronizer.publish(...)` with Lane A's `MaterializedServingState`,
candidate index, and replacement `VectorRecord` list. It publishes
`sync-vNNNNNNNN` completely before calling `ServingState.apply_sync`.

On SIGINT/SIGTERM, first stop simulator calls to `step()`, then invoke
`OnlineDemoSupervisor.graceful_stop()`. The supervisor:

1. flushes the producer and captures fixed Kafka high watermarks;
2. drains the trainer exactly to those bounds;
3. checkpoints, then commits offsets;
4. exports the exact start-inclusive/end-exclusive ranges to JSONL and
   Parquet;
5. publishes the final sync and calls the supplied child exporter;
6. closes the consumer and producer.

Create the child with `export_immutable_child(...)`. The resulting directory
contains `manifest.json` and `working-state.json`, is read-only, records the
original `parentModelId` and producing run, and passes Lane A's
`load_artifact(path)` integrity check. The original remains registered and
selectable.

## Verification

Run the service-independent suite:

```bash
cd online
.venv/bin/python -m pytest tests -m "not kafka and not pgvector" -q
```

Run the real Kafka smoke after the broker is healthy:

```bash
cd online
BABEL_KAFKA_BOOTSTRAP=127.0.0.1:29092 \
  .venv/bin/python -m pytest tests/feedback/test_kafka_smoke.py -q -s
```

The smoke proves acknowledged creator-keyed publication, manual commit,
bounded JSONL/Parquet export, and a same-group restart at the committed next
offset. Stop the local service with `docker compose stop kafka`.
