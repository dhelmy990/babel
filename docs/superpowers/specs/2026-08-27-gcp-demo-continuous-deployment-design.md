# GCP Demo Continuous Deployment Design

The `demo` branch deploys only commits that pass the JavaScript, deployment,
online-runtime, and native-backend gates. GitHub Actions builds three named
targets from one Dockerfile: the loopback C++ backend, CUDA Qwen serving, and
the CPU PyTorch/Kafka trainer. Every pushed image is tagged with the source
commit, but Compute Engine receives only resolved `@sha256:` references.

Authentication uses a Babel-specific Workload Identity Federation provider
and service account. Long-lived service-account JSON keys are forbidden. The
provider condition is exactly repository `dhelmy990/babel` and ref
`refs/heads/demo`, additionally pinned to repository ID `1244081200` and owner
ID `120252306` to prevent name-reuse takeover; it must not reuse the existing tutoring-bot provider,
`amath-bot` repository, or `github-deployer` service account.

The VM remains private. Services retain host networking and loopback binds so
the dashboard is available only through IAP/SSH forwarding. The VM owns a
root-readable `/etc/babel/runtime.env` containing private runtime credentials;
GitHub neither reads nor transfers those secrets. A successful local embedding
import must already have created the canonical fresh GCP trial/run and supplied
both independently verified population hashes.

Rollout holds a VM lock, rejects superseded GitHub run IDs, validates checksums,
exact keys, CUDA, fresh IDs, exact model/dataset provenance, 10,000 vectors,
recomputed population hashes, and HNSW readiness before it interrupts the
current application. It then performs a bounded restart, rejects stale trainer
readiness, attests running image digests/source labels, and executes one CUDA
Qwen recommendation before promotion. ERR/TERM/INT/HUP restores and attests the
previous application revision; a failed restore remains visible. Fixed host
ports make this bounded restart rather than zero-downtime blue/green.

Migrations are forward-only. Demo CI rejects migration changes relative to
`326b840`, preserving compatibility with the prior application image without
claiming a database rollback. Future schema work requires a separately reviewed
expand-contract rollout.

The performance worker is intentionally excluded. At base `326b840`, it owns a
`RealPopulationBuilder` path that can encode a population. It may be added only
after the reviewed fail-closed no-reencoding guard is replayed and reviewed on
the audited integration SHA. Until then both backend worker clients target a
closed loopback port and fail read-only; recommendation serving is unaffected.
