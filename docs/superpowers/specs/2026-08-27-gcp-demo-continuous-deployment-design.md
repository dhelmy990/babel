# GCP Demo Continuous Deployment Design

The `demo` branch deploys only commits that pass the JavaScript, deployment,
online-runtime, and native-backend gates. GitHub Actions builds three named
targets from one Dockerfile: the loopback C++ backend, CUDA Qwen serving, and
the CPU PyTorch/Kafka trainer. Every pushed image is tagged with the source
commit, but Compute Engine receives only resolved `@sha256:` references.

Authentication uses a Babel-specific Workload Identity Federation provider
and service account. Long-lived service-account JSON keys are forbidden. The
provider condition is exactly repository `dhelmy990/babel` and ref
`refs/heads/demo`; it must not reuse the existing tutoring-bot provider,
`amath-bot` repository, or `github-deployer` service account.

The VM remains private. Services retain host networking and loopback binds so
the dashboard is available only through IAP/SSH forwarding. The VM owns a
root-readable `/etc/babel/runtime.env` containing private runtime credentials;
GitHub neither reads nor transfers those secrets. A successful local embedding
import must already have created the canonical GCP run in
`BABEL_GCP_RUN_ID`.

Rollout validates checksums, exact keys, fixed model/dataset revisions, image
digests, the run UUID, and CUDA before it interrupts the current application.
It pulls images and applies idempotent migrations first, then performs a
bounded restart. If the new backend, serving process, or trainer does not
become ready, the previous release is restarted. The application cannot run
two revisions simultaneously because its private host ports are fixed, so this
is bounded restart with automatic rollback rather than zero-downtime blue/green.

The performance worker is intentionally excluded. At base `326b840`, it owns a
`RealPopulationBuilder` path that can encode a population. It may be added only
after the reviewed fail-closed no-reencoding guard is replayed and reviewed on
the audited integration SHA.
