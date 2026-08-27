# Post-Interview Production Hardening Backlog

The Friday pilot deliberately targets one trusted, bounded, manually observed
Colab run. The following historical hardening work was deferred from that
pilot; the promotion receipt distinguishes the scaled-experiment subset. None
should be improvised in the demo notebook.

## Scaled-experiment promotion receipt (2026-08-26)

The original backlog remains below as useful history. The following items are
now promoted into the canonical scaled experiment instead of being treated as
unbounded production hardening:

- **Task 3:** complete 2016 truth, bounded validation storage/search, and the
  user-launched training handoff (including the pilot's zero-row split
  verification).
- **Task 4:** authoritative real June/July releases, replacing the synthetic
  Friday temporal fixture.
- **Task 5:** real artifact/online-runtime compatibility contract.
- **Task 8:** durable graph edges and correctness for recommendation walks.
- **Tasks 11--12:** concurrent engineering measurement validity, resource
  evidence, and normal checkpoint-resume/fault experiments.
- **Task 13:** immutable publication compatibility and final handoff.

Malicious-artifact defenses, non-cooperating/multi-writer publication,
distributed or multi-worker training, credentials/governance, and production
release automation remain deferred. The experiment review is demo/experiment
focused; it does not claim those production controls.

## Known concrete follow-ups from the demo review

- Remove the cooperative-only `flock` fallback used when encrypted filesystems
  reject `renameat2(RENAME_NOREPLACE)`, or replace it with a portable truly
  no-clobber primitive. The Friday run has one trusted writer, so no
  non-cooperating publication race exists in the demo.
- When a final release declares a zero-row split, enumerate the pinned remote
  split directory and prove that it contains no stale nonempty shard. The pilot
  revision was manually verified and contains only its zero-row test sentinel.
  **Promoted: Task 3.**
- Re-run clean Colab installation and GPU smoke tests against the exact runtime
  image before promoting the pilot package to a long-lived training release.
  **Deferred:** the user-launched Task 3 / Gate A training run is experiment
  evidence, but automated compatibility testing across Colab runtime images is
  not owned by the scaled experiment.
- Complete the full 2016 inventory and authoritative June/July dumps; the
  Friday temporal release is explicitly a representative fixture and must not
  be reported as a complete historical corpus. **Promoted: Task 3 (2016) and
  Task 4 (June/July).**
- Replace single-operator Hub publication assumptions with tested concurrent
  writer, partial-response, retry, and recovery behavior before automation.

## Validation scale and observability

- Replace exact NumPy search with a tested FAISS path once the held-out pool no
  longer fits the bounded exact-validation budget; retain an exact comparison
  oracle and recall tolerance. **Promoted: Task 3.**
- Stream validation embeddings to bounded local storage instead of retaining a
  production-sized pool in RAM. **Promoted: Task 3.**
- Add confidence intervals, segment metrics, drift thresholds, and historical
  comparisons; keep v1 metric meanings frozen. **Deferred:** valuable
  observability hardening, but not required by a canonical scaled task.
- Persist query-level metric evidence and investigate invalid-vector causes
  without exposing private text. **Deferred:** not required to validate the
  engineering architecture experiment.

## Managed training and recovery

- Move long runs from an interactive Colab runtime to a managed, monitored GPU
  job with retry policy, quotas, durable logs, and alerting.
- Add checkpoint retention/garbage collection, corruption drills, cross-runtime
  restore tests, and explicit disk-space preflight.
  **Promoted only for normal resume and bounded fault evidence: Tasks 5 and
  12. Retention/garbage collection and broader production drills remain deferred.**
- Tune batch size, accumulation, checkpoint cadence, and validation frequency
  from measured T4/A10/A100 memory and throughput data. **Deferred:** hardware
  tuning is not a canonical scaled-task criterion.
- Add multi-worker/stateful-loader compatibility and distributed training only
  after deterministic single-worker resume remains the oracle.

## Credentials, provenance, and release

- Replace a personal read token with least-privilege workload identity or a
  short-lived managed secret; add rotation and access audit procedures.
- Add signed artifact attestations, SBOM/vulnerability scans, dependency update
  policy, and provenance verification in deployment.
- Automate immutable promotion from candidate to accepted model revision with
  independent validation and rollback; never overwrite a published artifact.
  **Promoted for immutable artifact compatibility and final handoff: Tasks 5
  and 13; automated production promotion remains deferred.**
- Add production upload/publish automation only behind explicit approvals,
  parent-CAS semantics, private-repository verification, and secret-redacted
  logs. The pilot performs no Hub writes.

## Notebook and operator experience

- Generate the notebook from a versioned orchestration API or replace it with a
  thin CLI once the training surface stabilizes.
- Add automated Colab smoke execution on supported GPU images and fail on stale
  cell tags, placeholders, dependency locks, or changed runtime defaults.
  **Deferred:** automated Colab image compatibility testing is outside the
  user-launched Task 3 / Gate A evidence.
- Add resumable Drive copy verification, checksum display, structured support
  bundles, and localization/accessibility review.

## Model-quality gates

- Define acceptance thresholds from real pilot evidence rather than inventing
  Friday-demo numbers. **Deferred:** formal model-quality thresholds are not
  required for the engineering architecture experiment.
- Add leakage audits, bias/safety evaluation, adversarial/long-text cases,
  regression suites, and downstream retrieval/business metrics before serving.
- Establish a model card, dataset card, retention policy, deletion workflow,
  and review for licensing/privacy obligations.
