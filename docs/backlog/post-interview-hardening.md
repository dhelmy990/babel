# Post-Interview Production Hardening Backlog

The Friday pilot deliberately targets one trusted, bounded, manually observed
Colab run. The following work is deferred; none should be improvised in the
demo notebook.

## Known concrete follow-ups from the demo review

- Remove the cooperative-only `flock` fallback used when encrypted filesystems
  reject `renameat2(RENAME_NOREPLACE)`, or replace it with a portable truly
  no-clobber primitive. The Friday run has one trusted writer, so no
  non-cooperating publication race exists in the demo.
- When a final release declares a zero-row split, enumerate the pinned remote
  split directory and prove that it contains no stale nonempty shard. The pilot
  revision was manually verified and contains only its zero-row test sentinel.
- Re-run clean Colab installation and GPU smoke tests against the exact runtime
  image before promoting the pilot package to a long-lived training release.
- Complete the full 2016 inventory and authoritative June/July dumps; the
  Friday temporal release is explicitly a representative fixture and must not
  be reported as a complete historical corpus.
- Replace single-operator Hub publication assumptions with tested concurrent
  writer, partial-response, retry, and recovery behavior before automation.

## Validation scale and observability

- Replace exact NumPy search with a tested FAISS path once the held-out pool no
  longer fits the bounded exact-validation budget; retain an exact comparison
  oracle and recall tolerance.
- Stream validation embeddings to bounded local storage instead of retaining a
  production-sized pool in RAM.
- Add confidence intervals, segment metrics, drift thresholds, and historical
  comparisons; keep v1 metric meanings frozen.
- Persist query-level metric evidence and investigate invalid-vector causes
  without exposing private text.

## Managed training and recovery

- Move long runs from an interactive Colab runtime to a managed, monitored GPU
  job with retry policy, quotas, durable logs, and alerting.
- Add checkpoint retention/garbage collection, corruption drills, cross-runtime
  restore tests, and explicit disk-space preflight.
- Tune batch size, accumulation, checkpoint cadence, and validation frequency
  from measured T4/A10/A100 memory and throughput data.
- Add multi-worker/stateful-loader compatibility and distributed training only
  after deterministic single-worker resume remains the oracle.

## Credentials, provenance, and release

- Replace a personal read token with least-privilege workload identity or a
  short-lived managed secret; add rotation and access audit procedures.
- Add signed artifact attestations, SBOM/vulnerability scans, dependency update
  policy, and provenance verification in deployment.
- Automate immutable promotion from candidate to accepted model revision with
  independent validation and rollback; never overwrite a published artifact.
- Add production upload/publish automation only behind explicit approvals,
  parent-CAS semantics, private-repository verification, and secret-redacted
  logs. The pilot performs no Hub writes.

## Notebook and operator experience

- Generate the notebook from a versioned orchestration API or replace it with a
  thin CLI once the training surface stabilizes.
- Add automated Colab smoke execution on supported GPU images and fail on stale
  cell tags, placeholders, dependency locks, or changed runtime defaults.
- Add resumable Drive copy verification, checksum display, structured support
  bundles, and localization/accessibility review.

## Model-quality gates

- Define acceptance thresholds from real pilot evidence rather than inventing
  Friday-demo numbers.
- Add leakage audits, bias/safety evaluation, adversarial/long-text cases,
  regression suites, and downstream retrieval/business metrics before serving.
- Establish a model card, dataset card, retention policy, deletion workflow,
  and review for licensing/privacy obligations.
