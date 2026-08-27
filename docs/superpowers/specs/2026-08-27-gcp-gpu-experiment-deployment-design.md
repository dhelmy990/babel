# GCP GPU Experiment Deployment Design

Date: 2026-08-27
Status: approved for implementation planning
Deadline: four hours from operator approval

## Purpose

Run the current formal Babel performance experiment on a Google Cloud GPU while
the active local CPU experiment continues independently. The GCP deployment is
a complete second installation, not a migration or extension of the local
runtime.

The target outcome is a completed 10,000-Babel GPU population and formal 3-by-3
matrix. A CUDA acceptance receipt and a durable first population batch are the
minimum smoke evidence, but they do not redefine completion if the full run is
still incomplete.

## Current Evidence

The design was based on the following inspected state:

- The active local worker runs from the separate
  `/home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2` worktree.
- Its source branch, `codex/slices-1-2`, is public at exact commit
  `f8b2a290e86d28256294807bec4d8d26ac6c04e6`.
- The local trial is `ce8e54ff-e317-4a89-b7db-90327e02dc43`; it was healthy and
  had committed 4,544 of 10,000 vectors at the last design inspection.
- Local PostgreSQL and Kafka were healthy. They are out of scope for GCP
  deployment commands.
- `chloe-tutoring-bot` has active billing, Compute Engine enabled, and the
  operator has project Owner permissions.
- Singapore has regional quota for one L4, but the project-wide
  `GPUS_ALL_REGIONS` quota is zero. A global quota adjustment to one is therefore
  the first operational gate.
- The online lock contains Torch 2.6 and its CUDA 12 dependencies, and
  `Qwen100Encoder` accepts `cuda[:index]` as a supported device.

The local counts above are a point-in-time observation, not a deployment input.
The GCP experiment never reads or copies the local database.

## Goals

- Run Qwen base-model, LoRA-adapter, and 1024-to-100 projection inference on
  CUDA.
- Populate a fresh GCP pgvector store with 10,000 finite, normalized 100d
  vectors from the existing pinned model and dataset revisions.
- Run the existing nine formal serving/training/activation conditions without
  changing their semantics.
- Keep the NumPy online trainer on CPU and disclose that placement in every
  result summary.
- Keep the dashboard private and make it available through an IAP SSH tunnel.
- Preserve immutable source, model, dataset, trial, and evidence identities.
- Produce GPU identity, throughput, latency, cost, and cleanup receipts.
- Leave the active local experiment untouched and usable as a fallback.

## Non-goals

- Public internet deployment, TLS, or end-user authentication.
- Rewriting Kafka, its event schema, or its consumer protocol.
- Porting the NumPy online trainer to PyTorch or CUDA.
- Reusing, moving, or completing the local trial on GCP.
- Sharing the local PostgreSQL, Kafka, worker token, ports, state, or Docker
  volumes with GCP.
- Building a reusable production container platform before tonight's run.
- Automatically deleting the VM or evidence disk before the operator reviews
  the receipts.

## Selected Approach

Run the entire stack on one isolated GCP VM. This costs more setup time than a
remote-worker tunnel, but it removes local port, database, Kafka-advertisement,
worker-token, and lifecycle collisions. It also preserves the application's
intentional loopback security boundary.

Rejected alternatives:

1. A GCP worker tunneled into a second local control plane has more moving parts
   and can collide with the live worker and backend ports.
2. A GPU encoder-only job is a useful diagnostic but cannot demonstrate the
   dashboard, recommendation serving, Kafka training path, or formal matrix.

## Cloud Resource Design

Use the following initial resource shape:

| Resource | Selection |
|---|---|
| Project | `chloe-tutoring-bot` |
| Region | `asia-southeast1` |
| Zone order | `asia-southeast1-b`, then `-a`, then `-c` |
| VM | `g2-standard-8` |
| Accelerator | one NVIDIA L4 with 24 GB VRAM |
| CPU and RAM | 8 vCPUs and 32 GB RAM |
| Image | Google Deep Learning VM, Ubuntu 22.04, NVIDIA 580 driver |
| Boot disk | 100 GB balanced persistent disk |
| Provisioning | on-demand, not Spot |
| Runtime limit | stop after six hours |
| Cost ceiling | USD 10 for this session |

Every new resource uses a `babel-` prefix and labels identifying the application,
experiment purpose, date, and source commit. Existing VMs, networks, firewall
rules, disks, addresses, and service accounts are not modified.

If no L4 is available in any Singapore zone after quota approval, the operator
may use one T4 on an `n1-standard-8` only after recording that fallback in the
hardware receipt. Both accelerators must pass the same 32-record CUDA gate. A
CPU VM is not an acceptable fallback for the GCP experiment.

## Network and Access Design

Create a dedicated auto-mode-disabled VPC, one Singapore subnet, and one ingress
rule allowing TCP 22 only from the IAP TCP-forwarding range
`35.235.240.0/20`. Do not add ingress rules for application ports.

The VM may have an ephemeral external address for outbound GitHub, Hugging Face,
Ubuntu, Python, and vcpkg downloads. Possessing that address does not make the
application reachable because ingress remains denied except for IAP SSH.

All application listeners remain on IPv4 loopback:

| Component | Address |
|---|---|
| Dashboard/backend | `127.0.0.1:8787` |
| Recommendation server | `127.0.0.1:8791` |
| Performance worker | `127.0.0.1:8792` |
| PostgreSQL/pgvector | `127.0.0.1:54329` |
| Kafka host listener | `127.0.0.1:29092` |

The operator opens the dashboard with an IAP SSH local forward, using local port
`18787` to avoid any local Babel listener:

```text
127.0.0.1:18787 -> IAP SSH -> VM 127.0.0.1:8787
```

No reverse tunnel is created, and the VM receives no route to local Babel
services.

## Runtime Layout

Clone the public repository, fetch `codex/slices-1-2`, detach at the exact full
SHA, and verify `git rev-parse HEAD` before building. Uncommitted notebook,
artifact, build, node-module, state, and environment files from the local
worktree are not transferred.

On the VM:

- Docker Compose project `babel-gpu` owns fresh PostgreSQL, Kafka, and named
  volumes.
- The C++ backend is built on the VM and applies migrations to the GCP database.
- `uv sync` uses `online/uv.lock` with the development, Kafka, Parquet,
  pgvector, and Qwen extras.
- Backend and performance worker run as supervised services with independent
  logs and restart-on-failure behavior.
- Runtime state, Hugging Face caches, model caches, evidence, and journals live
  under a dedicated persistent root on the boot disk.
- `BABEL_ONLINE_QWEN_DEVICE=cuda` is set for the performance worker and inherited
  by serving subprocesses.
- The trainer receives no GPU assignment and remains the existing CPU NumPy
  implementation.

The performance worker loads one Qwen model, and condition subprocesses can load
additional serving state. The L4's 24 GB VRAM is deliberately larger than the
0.6B model, adapter, projection, and 32-record inference batch requirement.

## Secret Handling

The Git repository is public and needs no GitHub credential. The private Hugging
Face token is the only pre-existing secret required.

- Read `HF_TOKEN` from the local environment without printing it.
- Transfer it through SSH standard input, never a command argument.
- Store it in a mode-`0600` runtime environment file on the encrypted VM disk.
- Generate the 64-hex performance-worker token on the VM and store it in the
  same protected file.
- Never put secrets in Git, VM metadata, startup-script arguments, dashboard
  responses, receipts, or command logs.
- Remove the protected file during approved resource cleanup.

## Data Flow

```text
Browser through IAP tunnel
  -> loopback C++ dashboard/backend
  -> authenticated loopback performance worker
  -> pinned Hugging Face model and dataset acquisition
  -> Qwen CUDA inference
  -> fresh GCP PostgreSQL/pgvector rows
  -> formal recommendation condition
  -> Kafka feedback
  -> CPU NumPy trainer
  -> immutable child state and evidence
```

The GCP trial receives a fresh trial ID, population run ID, condition run IDs,
worker token, Kafka group identities, state root, and evidence root. It shares
only immutable Git and Hugging Face revisions with the local experiment.

## Execution Gates

### 1. Quota

Request `GPUS_ALL_REGIONS=1` and poll for at most 30 minutes. Recheck both the
global quota and regional L4 quota immediately before VM creation. Do not create
partial deployment resources while the global quota remains zero.

If the request remains pending after 30 minutes, stop and report the external
blocker. Do not silently switch providers or weaken the GCP goal.

### 2. Provisioning

Create and inspect the isolated network, firewall, disk, and time-limited VM.
Verify labels, zone, machine type, source image, accelerator, stop deadline, and
firewall exposure before installing application software.

### 3. Source and dependencies

Verify the detached source SHA. Install only the system tools required by the
existing CMake/vcpkg and Python workflows. Build the backend on the VM, install
the locked Python environment, and start the dedicated database and Kafka.

### 4. CUDA acceptance

Capture `nvidia-smi` and require all of the following:

- `torch.cuda.is_available()` is true;
- the recorded Torch device is the provisioned accelerator;
- the exact pinned Qwen base, LoRA adapter, and projection load successfully;
- one real article produces shape `(1, 100)` with finite float32 values and norm
  approximately 1.0;
- a 32-article inference batch succeeds without out-of-memory or non-finite
  output;
- elapsed time and effective vectors per second are saved.

The 32-record check matches the current population batch size and is mandatory;
a one-vector-only check cannot prove population viability.

### 5. Service acceptance

Require healthy PostgreSQL, Kafka, backend, performance worker, and
recommendation-server boundaries. Verify the dashboard through the IAP tunnel
and verify that no public application port is reachable.

### 6. Formal population

Create a fresh trial with the existing formal defaults: 50 creators, 10,000
created Babels, 50 concurrent users, pgvector retrieval, 0.40 start and
continuation probabilities, depth two, ten-request traversal cap, micro-batch
eight, synchronization every ten steps, 30-second warmup, 120-second measured
duration, and 5 RPS.

The first durably journaled 32-vector batch is the end-to-end GPU population
smoke receipt. Continue the same population instead of discarding valid work.
Before approval, require:

- exactly 10,000 distinct created rows;
- exactly 10,000 indexed vectors;
- every vector finite and exactly 100-dimensional;
- exact model, adapter, projection, dataset, and source revisions;
- population, assignment, used-source, ordered-vector, and vector-snapshot
  checksums;
- no duplicate creator/source pair;
- zero unresolved failures.

### 7. Formal matrix

Approve the matrix only after the population gate passes. Run the existing nine
conditions sequentially without changing topology, training, activation,
warmup, measurement, request, or workload semantics. Require each condition to
produce durable evidence and require zero final Kafka lag for training
conditions.

The worker may be restarted against its GCP journal after a recoverable service
failure. It may not reuse local state or regenerate accepted immutable inputs.

### 8. Evidence and handoff

Save:

- project, resource names, zone, image, machine, CPU, RAM, accelerator, driver,
  CUDA, Torch, and source SHA;
- pinned model, adapter, projection, and dataset identities;
- single-vector and 32-vector CUDA receipts;
- population duration, throughput, counts, and checksums;
- live recommendation latency receipt;
- nine condition results and Kafka/trainer evidence;
- dashboard tunnel command;
- elapsed wall time and estimated cloud cost;
- comparison with the local CPU population throughput;
- explicit disclosure that online training remained CPU-based;
- stop and cleanup instructions.

## Failure Handling

- **Quota pending or denied:** stop after the 30-minute gate and report it.
- **L4 capacity unavailable:** try the three approved zones, then the recorded
  T4 fallback. Do not use Spot for the formal run.
- **Source SHA mismatch:** stop before build.
- **Pinned Hub identity or checksum mismatch:** stop before encoding.
- **CUDA unavailable or 32-record OOM:** stop before creating the formal trial;
  do not claim GPU acceptance.
- **Dependency or backend build failure:** diagnose only on the GCP VM; do not
  borrow or alter the live local build.
- **Population interruption:** retain the GCP journal and restart from its last
  committed boundary.
- **Condition failure:** retain evidence and state, do not publish a completed
  matrix, and do not substitute smoke evidence for formal results.
- **Approaching USD 10:** stop the VM and preserve the disk for review.
- **Six-hour limit:** the VM stops automatically. It is not automatically
  deleted.

No failure path sends a signal, database command, Docker command, filesystem
write, or network request to the active local runtime.

## Verification and Acceptance

The deployment goal is complete only when authoritative current-state evidence
proves all of the following:

1. The VM used a real GCP NVIDIA GPU and Torch executed Qwen inference on CUDA.
2. The source, model, adapter, projection, and dataset match their exact pins.
3. The independent GCP trial contains 10,000 valid vectors and a complete
   population receipt.
4. All nine formal conditions completed with their required evidence and final
   Kafka state.
5. A GPU-backed recommendation latency receipt exists.
6. The dashboard is reachable locally only through the documented IAP tunnel.
7. The local experiment was not stopped, restarted, reconfigured, or written by
   the GCP workflow.
8. The observed cost remains within USD 10 and the VM is stopped after evidence
   collection.
9. The handoff explicitly states that Qwen encoding/serving used GPU while the
   NumPy online trainer remained on CPU.

A successful CUDA vector or first population batch is valuable progress but
does not, by itself, satisfy the full deployment objective.

## Time Budget

| Phase | Target elapsed time |
|---|---:|
| Quota approval | 0-30 minutes |
| VM/network creation | 5-15 minutes |
| Source, dependencies, build, model download | 35-70 minutes |
| CUDA and service acceptance | 10-20 minutes |
| GPU population | 10-30 minutes |
| Formal matrix | 25-40 minutes |
| Evidence, comparison, and handoff | 15-30 minutes |

The schedule retains a modest buffer inside four hours if quota approval is
automatic. These are planning targets rather than performance claims.

## Cost and Cleanup

The selected `g2-standard-8` was approximately USD 0.85 per hour at design time.
Six hours of compute is therefore expected to remain below the USD 10 ceiling,
including a small disk allowance. Actual billing data is authoritative.

Automatic stop ends GPU compute charges but retains the disk and evidence. After
the operator has copied and reviewed all receipts, cleanup is a separate,
explicitly confirmed action that deletes only the exact `babel-*` VM, disk,
address, firewall, subnet, and VPC resolved by read-only inspection. Existing
project resources and the local experiment are never cleanup targets.
