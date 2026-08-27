# Scaled experiment baseline audit

**Status:** DONE_WITH_CONCERNS
**Audit date:** 2026-08-26
**Baseline branch and commit:** `codex/slices-1-2` at `f488ab6f45dfb97d7f3c95154ccb83965136221e` (`docs: unify scaled experiment execution plan`)

## Scope and rule of interpretation

This receipt freezes the working Friday miniature before the scaled experiment
replaces its scientific and scale inputs. A preserved capability is evidence of
an already-working systems path, not evidence that the miniature's data,
encoder, vectors, graph, load, or measurements represent scale. The 80-row
pilot, synthetic June/July scenario, NumPy working encoder, sequential load,
and 51-candidate performance result are therefore **smoke-only**.

The review scope is the demonstrable experiment: it does not recast the Friday
demo as adversarial-production hardening.

## Frozen identities

| Kind | Frozen identity | Scope label |
| --- | --- | --- |
| Git source | `f488ab6f45dfb97d7f3c95154ccb83965136221e` on `codex/slices-1-2` | audit baseline |
| Friday verified implementation source | `95a78120a682b4cc1647b57304800e41db5a7f95` | prior demo handoff baseline |
| Friday demo dataset | `dhelmy990/babel-wikipedia-experiment`, `demo_crosswalk`, `e1acc648fcace8820dd5ee70bae9216ea4334555` | immutable, non-scale demo catalog |
| 2016 training dataset | `dhelmy990/babel-wikipedia-experiment`, `distillation_2016`, `c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b` | 80-row pilot only: 64 train, 16 validation, 0 test |
| Teacher/model input | `Qwen/Qwen3-Embedding-0.6B`, `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | pilot input, not the local online encoder |
| Online original | model `00000000-0000-5000-8000-000000000002`, fixture encoder revision `cccccccccccccccccccccccccccccccccccccccc`, embedding space `00000000-0000-5000-8000-000000000003`, 100 dimensions | deterministic Friday stand-in; explicitly non-Qwen and non-scale |
| Reference dashboard run | `fd435049-1848-4824-b833-c72be72220e9` | 50 creators, 100 Babels/feedback; demo only |
| Reference performance run | benchmark `3cb917ff-409f-580e-b3c2-52956cf4eee9`; source `596e191a-957e-43bb-ad27-1a109696378d`; model `6e54f425-290c-5d5f-a451-60f8260d0d96`, version 48; serving source `d5852b359fc58148076f008abe890be02ea403ff`; locked training/sync source `f90f22ecd66fdec01c2d9b6d7135dee6e6abd434` | single-client, 51-candidate result only |

The Friday monthly fixture reuses representative 2016 text while assigning
simulated `2026-06`/`2026-07` metadata. It is not an official June or July
Wikipedia snapshot. The performance report's pgvector candidate universe has
51 rows and uses `DISTINCT` plus top-N, not HNSW; it cannot support a scale
claim.

## Preserved baseline

The following remains intact while later tasks replace scale inputs:

- C++ dashboard security boundary, loopback-only operation, nonce-protected
  mutations, typed dashboard activity, and graceful-stop flow.
- Hugging Face-pinned seeding with no live-Wikipedia fallback.
- PostgreSQL/pgvector as the durable candidate path and Kafka as the feedback
  path.
- Synchronous POST stage timings, immutable parent/child model lineage,
  checkpoint/export and online serving synchronization, activity logs, and raw
  benchmark timing retention.

## Closed gap receipt

`preserve` means retain the verified systems behavior. `replace` means the
Friday substitute is explicitly insufficient and is owned by the named task.
`new` is a scaled-experiment capability not claimed by the miniature.
`deferred` is intentionally outside Tasks 2--13 and remains in the hardening
backlog.

| State | Receipt item | Canonical owner and closure |
| --- | --- | --- |
| new | Mirror the authoritative source material with immutable manifests and access-independent verification. | Task 2 — source mirror |
| replace | Replace the 80-row 2016 pilot with complete 2016 acquisition, validation, and training handoff. | Task 3 — complete 2016/full validation/training handoff |
| replace | Replace synthetic June/July labels and reused rows with real authoritative monthly releases. | Task 4 — real June/July |
| replace | Convert the trained Qwen artifact into the online manifest/checkpoint/embedding contract. | Task 5 — adapter contract |
| replace | Replace the deterministic NumPy item-tower stand-in with real Qwen serving. | Task 6 — real Qwen serving |
| replace | Replace stand-in/frozen small-universe vectors with real Qwen pgvector rows and cache behavior. | Task 7 — real pgvector/cache |
| replace | Make graph edges durable and derive recommendation walks from the scaled graph; create users concurrently. | Task 8 — edges/walks/concurrent creators |
| new | Provide topology modes and a distributor that make the scaled creator/graph experiment controllable. | Task 9 — topology/distributor |
| new | Persist trial definitions/results and expose scaled progress in the dashboard. | Task 10 — dashboard/progress/saved trials |
| replace | Replace the one-client, 500-POST/condition smoke result with concurrent load, resource capture, and raw timing analysis. | Task 11 — concurrent benchmark/resources |
| new | Run and compare smoke, scale, checkpoint-resume, and bounded fault experiments against frozen metrics. | Task 12 — smoke/scale/fault experiments |
| new | Publish immutable scaled artifacts and complete the reproducible final handoff. | Task 13 — HF publication/final handoff |
| preserve | Keep the C++ dashboard/security boundary, HF pin/no-live fallback, Postgres/pgvector, Kafka, synchronous timing instrumentation, immutable lineage, graceful stop, checkpoint/sync, activity logs, and raw benchmark timing. | All Tasks 2--13 consume these paths; no replacement is authorized by this receipt. |
| deferred | Malicious artifact handling, non-cooperating/multi-writer publication, distributed/multi-worker training, credentials/governance, attestations, and production release operations. | Retained in `docs/backlog/post-interview-hardening.md`; not evidence required for the demo/experiment review. |

## Preserved-baseline verification (no bulk jobs)

All commands below were run from this worktree on 2026-08-26. No command
started a dataset download, training job, benchmark replay, or publication.

| Command | Exact result | Receipt |
| --- | --- | --- |
| `python3 -m pytest data_pipeline/tests training/tests -q` | Exit 1 before collection: global pytest entry-point plugin `hydra` imports `get_ref_type` from incompatible `/home/dhelmy990/.local/lib/python3.10/site-packages/omegaconf/_utils.py`. | **Concern:** environment dependency conflict; no tests ran and this is not a skip. |
| `PYTHONPATH=online/src python3 -m pytest online/tests -q` | Exit 1 before collection with the same global `hydra`/`omegaconf` `ImportError`. | **Concern:** environment dependency conflict; no tests ran and this is not a skip. |
| `PYTHONPATH=benchmark/src:online/src python3 -m pytest benchmark/tests -q` | Exit 1 before collection with the same global `hydra`/`omegaconf` `ImportError`. | **Concern:** environment dependency conflict; no tests ran and this is not a skip. |
| `cmake --build --preset test` | Exit 0; built `babel_backend`, `babel_backend_cli`, all named C++ test targets, and `babel_tests` (100%). | pass |
| `ctest --preset test --output-on-failure` | Exit 0; **182/182 passed**, 0 failed, total real time 10.16 s. | pass; no skips reported |
| `npm test` | Exit 0; **57/57 passed**, 0 failed, 0 skipped, duration 85.064228 ms. | pass |

The three Python failures have one common, diagnosable pre-collection cause in
the host interpreter's auto-loaded pytest plugin set: `hydra-core==1.0.7`
registers `hydra.extra.pytest_plugin`, while the same interpreter supplies
`omegaconf==2.3.0`, which no longer exports the symbol the plugin imports. A
controlled no-autoload diagnostic then reached collection and exposed a second
environment fact: this host interpreter has neither the installed
`babel_data` package nor `torch`. This receipt does not disable plugin
autoloading, replace an interpreter, or claim the affected subsystems passed;
the environment must be repaired before the next baseline test claim.

## Backlog reconciliation

The following historical hardening items are promoted by annotation in
`docs/backlog/post-interview-hardening.md`: data truth (Tasks 3--4), bounded
validation storage/search (Task 3), concurrent engineering measurement
validity (Tasks 11--12), normal checkpoint resume and artifact compatibility
(Tasks 5, 12--13), and graph correctness (Task 8). The original backlog
wording remains as history. Automated Colab image compatibility, confidence
intervals/segment/drift/historical analytics, query-level metric evidence,
hardware tuning, and formal model-quality thresholds remain deferred with the
other malicious-artifact, multi-writer, distributed,
credential/governance, and production-operation items.

## Self-review receipt

- The receipt names every canonical Task 2--13 and assigns each
  vision/engineering-critical replacement exactly once as its primary owner;
  deferred hardening enhancements are not represented as executable task work.
- The miniature limitations are repeated at the point of use: 80 rows,
  synthetic June/July, deterministic NumPy stand-in, 51 candidates, and
  sequential client load.
- No credential value is recorded. References to `HF_TOKEN` are variable names
  only; no `.env` value was read or printed.
- Documentation changes are limited to this audit and the historical hardening
  backlog annotations. `git diff --check` and the credential-pattern scan were
  performed for the committed receipt.
