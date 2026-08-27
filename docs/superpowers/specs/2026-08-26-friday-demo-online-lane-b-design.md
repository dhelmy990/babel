# Friday Demo Online Lane B Design Amendment

## Status and scope

This amendment narrows the approved Slice 3 online recommendation design to a
Friday-demo implementation of feedback transport, hidden simulation, online
training, atomic synchronization, graceful shutdown, and bounded export. The
approved design in
`2026-08-24-wikipedia-training-and-online-experiment-design.md` remains
authoritative for cross-lane contracts and product semantics.

Lane B owns only `babel_online.feedback`, `babel_online.simulation`,
`babel_online.training`, the minimal supervisor needed to compose those
modules, matching tests, a coordinated Kafka Compose addition, and the online
worker runbook. It consumes Lane A contracts, model registry, created-Babel
candidate index, serving state, and recommendation POST without modifying
them.

## Architecture

The implementation is an in-memory-first core with thin external adapters.
`InMemoryFeedbackBus` and the Kafka adapter implement the same producer,
consumer, offset, and high-watermark behavior. The continuously running
trainer therefore uses identical checkpoint and recovery logic in unit tests
and the real broker smoke. Kafka carries validated `FeedbackEventV1` JSON only,
keyed by creator ID on `babel.feedback.v1`; model state moves exclusively
through atomic filesystem directories.

The simulator synchronously invokes Lane A's recommendation POST before making
private include, exclude, or ignore decisions. It samples each creator's source
articles without replacement, supplies only created synthetic Babels as
candidates through Lane A, acknowledges one observable feedback event, and
only then records accepted include edges. Hidden graph, profile, relevance,
and random-draw values never enter the request, event, checkpoint metrics, or
export.

The online trainer constructs weighted pairs from each event: include is the
positive, exclude is a weight-1 hard negative, and ignore is a weight-0.25
negative. The distilled encoder/base vectors are immutable inputs. A working
copy trains only context parameters and item residuals. Each atomic checkpoint
contains working state, optimizer state, RNG, metrics, and per-partition next
offsets. The consumer commits those offsets only after the checkpoint rename
is durable.

Synchronization writes `sync-vN.partial`, fsyncs files and the directory, and
renames it to `sync-vN` before asking `ServingState.apply_sync` to activate it.
The original artifact is never mutated. Graceful completion creates a separate
immutable child artifact whose manifest names the original parent and
producing run; both remain selectable in Lane A's registry.

## Runtime flow

1. Start one consumer with automatic commits disabled and restore the newest
   complete checkpoint's next offsets.
2. For each simulator step, create or reuse the deterministic staged Babel,
   synchronously POST the recommendation request, decide privately, publish
   one acknowledged observable feedback event, then expose include edges.
3. Train acknowledged events in order. Events without includes advance the
   processed offset without a gradient update.
4. At the checkpoint interval, atomically save complete state, then manually
   commit the matching offsets. At the synchronization interval, publish and
   activate a versioned state directory.
5. On graceful stop, stop producing, capture high watermarks, drain exactly to
   those bounds, checkpoint and commit, export the processed range to JSONL
   and Parquet, create the immutable child, and close producer and consumer.

## Recovery and failure behavior

- Incomplete `.partial` checkpoint, sync, export, or child directories are
  ignored on restart.
- A restart seeks to durable checkpoint offsets, even if Kafka has a later
  committed offset; normal recovery tests prove one uncommitted event replays
  and one checkpointed event does not.
- Publish failure does not expose an activity or accepted edge and retains the
  same deterministic event identity for retry.
- Synchronization failure leaves serving on its previous complete version.
- Kafka unavailability pauses event production; it does not trigger an
  alternate transport or carry weights.

## Verification boundary

The deterministic tiny acceptance uses fixture 100-dimensional vectors and a
small trainable working model. It proves feedback causes a finite parameter
change, synchronization produces a selectable child, the original checksum is
unchanged, and restart resumes at the durable next offset. A separate smoke
uses one local Apache Kafka KRaft broker, one topic, acknowledged keyed
production, manual consumption, checkpoint-before-commit ordering, restart,
drain, and bounded export. No production-HA or adversarial broker hardening is
part of the Friday scope.
