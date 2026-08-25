# Friday Demo Online Lane B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove observable feedback travels through one manually committed Kafka path, changes only a working online model, synchronizes an immutable child, and restarts from durable offsets.

**Architecture:** Pure in-memory ports own deterministic behavior; Kafka, synchronous HTTP, filesystem synchronization, and Parquet are thin adapters. The trainer checkpoints model state and next offsets atomically before committing transport offsets, while the simulator remains the only hidden-data consumer.

**Tech Stack:** Python 3.10+, PyTorch, NumPy, PyArrow, httpx, confluent-kafka, Apache Kafka KRaft, pytest.

## Global Constraints

- Consume Lane A's frozen contracts/model/serving APIs without modifying them.
- Use only topic `babel.feedback.v1`, keyed by creator ID, with automatic commits disabled.
- Kafka carries observable feedback JSON only and never carries model weights.
- Only created synthetic Babels are candidates; one creator cannot create the same source twice.
- Keep the encoder/original immutable; train a working copy and publish an immutable child with parent lineage.
- Commit offsets only after a complete atomic checkpoint is durable.
- Trusted Friday-demo path only; no multi-broker, adversarial, or HA hardening.

---

### Task 1: Feedback bus, Kafka adapter, and bounded export

**Files:**
- Create: `online/src/babel_online/feedback/bus.py`
- Create: `online/src/babel_online/feedback/kafka.py`
- Create: `online/src/babel_online/feedback/export.py`
- Create: `online/tests/feedback/test_bus.py`
- Create: `online/tests/feedback/test_export.py`
- Create: `online/tests/feedback/test_kafka_integration.py`

**Interfaces:**
- Consumes: Lane A `FeedbackEventV1` and `validate_contract`.
- Produces: `TopicPartition`, `FeedbackRecord`, `OffsetRange`, `FeedbackProducer`, `FeedbackConsumer`, `InMemoryFeedbackBus`, `KafkaFeedbackProducer`, `KafkaFeedbackConsumer`, `capture_high_watermarks`, and `export_offset_ranges`.

- [ ] **Step 1: Write the failing acknowledged-message test**

```python
def test_feedback_is_keyed_and_consumed_once_before_commit(event):
    bus = InMemoryFeedbackBus(topic="babel.feedback.v1")
    bus.publish(event.creatorId, event)
    record = bus.consumer(group_id="trainer", auto_commit=False).poll()
    assert record.key == event.creatorId
    assert record.event == event
    assert record.offset == 0
```

- [ ] **Step 2: Run the focused test and confirm import failure**

Run: `PYTHONPATH=online/src pytest online/tests/feedback/test_bus.py -q`

- [ ] **Step 3: Implement the in-memory producer/consumer and manual offsets**

Store append-only records per partition. Consumers expose `poll`, `seek`,
`position`, `committed`, `commit`, `high_watermarks`, and `close`; reject
automatic commit and any topic other than `babel.feedback.v1`.

- [ ] **Step 4: Add the checkpoint-independent bounded export test and implementation**

```python
def test_export_is_exactly_start_inclusive_end_exclusive(bus, tmp_path):
    result = export_offset_ranges(bus, [OffsetRange(0, 1, 3)], tmp_path)
    assert [row.offset for row in result.records] == [1, 2]
    assert result.jsonl_path.exists() and result.parquet_path.exists()
```

- [ ] **Step 5: Add thin confluent-kafka adapters and the marked real-broker test**

Configure `enable.auto.commit=false`, require delivery acknowledgement, assign
and seek explicitly, and commit supplied next offsets synchronously.

- [ ] **Step 6: Run feedback tests**

Run: `PYTHONPATH=online/src pytest online/tests/feedback -m 'not kafka' -q`

### Task 2: Hidden decisions and synchronous simulation

**Files:**
- Create: `online/src/babel_online/simulation/sampling.py`
- Create: `online/src/babel_online/simulation/decisions.py`
- Create: `online/src/babel_online/simulation/client.py`
- Create: `online/src/babel_online/simulation/engine.py`
- Create: `online/tests/simulation/test_sampling.py`
- Create: `online/tests/simulation/test_decisions.py`
- Create: `online/tests/simulation/test_engine.py`

**Interfaces:**
- Consumes: Lane A request/response contracts, created-Babel index, and Task 1 producer.
- Produces: `SourceSampler`, `action_probabilities`, `decide_candidate`, `RecommendationClient`, `SimulationEngine.step`, and `reconstruct_accepted_edges`.

- [ ] **Step 1: Write and run the failing no-replacement test**

```python
def test_creator_never_samples_one_source_twice():
    sampler = SourceSampler(["enwiki:1", "enwiki:2"], seed=7)
    assert len({sampler.take(), sampler.take()}) == 2
    with pytest.raises(EligibleSupportExhausted):
        sampler.take()
```

- [ ] **Step 2: Implement deterministic sampling and three-way probabilities**

Use the approved include/exclude/ignore formula and a deterministic draw keyed
by run, creator, event number, and candidate ID.

- [ ] **Step 3: Write and run the failing synchronous engine test**

```python
def test_step_posts_before_publishing_and_exposes_only_include_edges(engine):
    result = engine.step()
    assert engine.client.calls[0].requestId == result.request_id
    assert engine.producer.events[0].requestId == result.request_id
    assert {edge.target for edge in result.accepted_edges} == result.included_ids
```

- [ ] **Step 4: Implement the client and event loop**

The engine stages one deterministic Babel, POSTs synchronously, evaluates
hidden ranks privately, builds only the frozen observable event, waits for
publication acknowledgement, then records accepted include edges.

- [ ] **Step 5: Add publish-failure and candidate-universe recovery tests**

Assert failure exposes no edge/activity and retry preserves event identity;
assert candidates are a subset of Lane A's persisted created Babel IDs.

- [ ] **Step 6: Run simulation tests**

Run: `PYTHONPATH=online/src pytest online/tests/simulation -q`

### Task 3: Pairwise training, durable checkpoints, and restart

**Files:**
- Create: `online/src/babel_online/training/pairs.py`
- Create: `online/src/babel_online/training/loss.py`
- Create: `online/src/babel_online/training/checkpoint.py`
- Create: `online/src/babel_online/training/consumer.py`
- Create: `online/tests/training/test_pairs.py`
- Create: `online/tests/training/test_checkpoint.py`
- Create: `online/tests/training/test_recovery.py`

**Interfaces:**
- Consumes: Lane A `FeedbackEventV1`, Task 1 consumer records, and an injected working PyTorch model/optimizer.
- Produces: `TrainingPair`, `pairs_from_event`, `weighted_pairwise_loss`, `CheckpointState`, `save_online_checkpoint`, `load_latest_checkpoint`, and `OnlineTrainer`.

- [ ] **Step 1: Write and run the failing pair-label test**

```python
def test_include_pairs_with_hard_and_soft_negatives(event):
    assert {(p.positive_id, p.negative_id, p.weight) for p in pairs_from_event(event)} == {
        ("included", "excluded", 1.0),
        ("included", "ignored", 0.25),
    }
```

- [ ] **Step 2: Implement pair construction and finite weighted loss**

Use `softplus(-(positive-negative)) * weight`, normalized by total weight.
No-positive events advance offsets without optimizer updates.

- [ ] **Step 3: Write and run the failing checkpoint-before-commit test**

```python
def test_commit_happens_only_after_complete_checkpoint(trainer, consumer):
    trainer.process_available()
    assert consumer.committed() == {TopicPartition("babel.feedback.v1", 0): 0}
    trainer.checkpoint_and_commit()
    assert consumer.committed() == trainer.next_offsets
```

- [ ] **Step 4: Implement atomic checkpoint save/load**

Save model, optimizer, RNG, step/version, metrics, and next offsets under
`.partial`; fsync files and directory, rename, then invoke consumer commit.

- [ ] **Step 5: Add restart replay and parameter-change tests**

Prove a checkpoint reload seeks to durable next offsets, exactly one
uncheckpointed event replays, encoder/base parameters remain unchanged, and a
tiny batch changes a working parameter with finite loss.

- [ ] **Step 6: Run training tests**

Run: `PYTHONPATH=online/src pytest online/tests/training -q`

### Task 4: Atomic synchronization and immutable child lineage

**Files:**
- Create: `online/src/babel_online/training/synchronizer.py`
- Create: `online/tests/training/test_synchronizer.py`

**Interfaces:**
- Consumes: Lane A `ServingState.apply_sync`, `ModelRegistry`, and manifests.
- Produces: `AtomicStateSynchronizer.publish`, `export_child_artifact`, and versioned `sync-vN` directories.

- [ ] **Step 1: Write and run the failing atomic-version test**

```python
def test_complete_sync_changes_serving_but_keeps_original_selectable(sync, registry):
    original_checksum = registry.select(ORIGINAL).checksum
    child = sync.publish(version=1)
    assert sync.serving.snapshot().version == 1
    assert registry.select(ORIGINAL).checksum == original_checksum
    assert registry.select(child.modelId).parentModelId == ORIGINAL
```

- [ ] **Step 2: Implement `.partial` publication and activation**

Write state and checksum manifest, fsync, rename to `sync-vN`, then call the
Lane A activation API. Never mutate or relabel the original.

- [ ] **Step 3: Add incomplete-sync and incompatible-sync tests**

Restart ignores `.partial`; rejected activation leaves the prior serving
snapshot and `LATEST` unchanged.

- [ ] **Step 4: Implement immutable final child export**

Create a separate atomic child artifact with parent ID/checksum, run ID,
training counts, checkpoint checksum, and final version, then call
`ModelRegistry.register_child`.

- [ ] **Step 5: Run synchronization tests**

Run: `PYTHONPATH=online/src pytest online/tests/training/test_synchronizer.py -q`

### Task 5: Graceful supervisor, Kafka smoke, and runbook

**Files:**
- Create: `online/src/babel_online/runtime/supervisor.py`
- Create: `online/tests/runtime/test_supervisor.py`
- Create: `online/tests/e2e/test_tiny_online_run.py`
- Modify: `compose.yaml` only after parent approval of the exact Kafka service diff
- Create: `docs/runbooks/online-demo-worker.md`

**Interfaces:**
- Consumes: Tasks 1–4 and Lane A serving/model registry.
- Produces: `OnlineDemoSupervisor.run`, `request_stop`, `drain_and_finalize`, and complete acceptance evidence.

- [ ] **Step 1: Write and run the failing graceful-stop test**

```python
def test_stop_drains_to_bound_checkpoints_exports_and_registers_child(runtime):
    runtime.request_stop()
    result = runtime.drain_and_finalize()
    assert result.trained_offsets == result.captured_high_watermarks
    assert result.exported_offsets == result.captured_high_watermarks
    assert result.child.parentModelId == result.original.modelId
```

- [ ] **Step 2: Implement bounded drain and close ordering**

Stop simulator production, capture high watermarks, train through those exact
bounds, checkpoint and commit, export JSONL/Parquet, publish child, flush and
close producer, then close consumer.

- [ ] **Step 3: Run the deterministic in-memory tiny acceptance**

Prove feedback → finite parameter change → sync-v1 → immutable child, unchanged
original checksum, bounded export, and normal restart offset.

- [ ] **Step 4: Coordinate and add one loopback Kafka KRaft service**

Use `apache/kafka:4.3.1`, one broker/controller, replication factor 1, one
loopback client listener, auto topic creation disabled, and topic initializer
for `babel.feedback.v1`.

- [ ] **Step 5: Run the marked real Kafka smoke**

Produce keyed events, consume manually, checkpoint, commit, restart at the next
offset, drain the captured watermark, and verify exported offsets/checksums.

- [ ] **Step 6: Write the operator runbook and run full gates**

Document dependencies, start/stop, expected offsets, artifacts, recovery, and
the immutable original/child selection. Run focused online tests, full online
tests, compileall, and the real Kafka smoke before committing owned paths.
