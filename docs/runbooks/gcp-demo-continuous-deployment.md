# GCP Demo Continuous Deployment

## Required GitHub environment

Create a protected GitHub environment named `gcp-demo` and define these
environment variables:

- `GCP_PROJECT_ID`: the Babel GCP project ID.
- `GCP_WORKLOAD_IDENTITY_PROVIDER`: full provider resource name for the new
  `babel-demo` provider.
- `GCP_DEPLOY_SERVICE_ACCOUNT`: the new
  `babel-github-deployer@PROJECT_ID.iam.gserviceaccount.com` address.
- `GCP_ARTIFACT_REGION`: Artifact Registry region, for example `us-central1`.
- `GCP_ARTIFACT_REPOSITORY`: the new Docker repository `babel-demo`.
- `GCP_VM_NAME`: private Compute Engine GPU VM name.
- `GCP_VM_ZONE`: exact VM zone.
- `BABEL_GCP_RUN_ID`: canonical UUID of the migrated/imported GCP run.
- `BABEL_GCP_TRIAL_ID`: fresh UUIDv4 allocated by the verified importer. The
  run ID must be exactly `uuid5(trial_id, "population")`.
- `BABEL_POPULATION_VECTOR_SHA256`: ordered-vector SHA-256 from the ready
  import receipt.
- `BABEL_POPULATION_SNAPSHOT_SHA256`: canonical snapshot SHA-256 from that
  same ready import receipt.

No GitHub secret is required for GCP authentication. Do not create or upload a
service-account JSON key.

## One-time GCP setup

Create a new Artifact Registry Docker repository named `babel-demo`, a new
service account named `babel-github-deployer`, and a new provider named
`babel-demo` in the existing `github` workload identity pool. Restrict its
attribute condition to both:

```text
assertion.repository_id == '1244081200' &&
assertion.repository_owner_id == '120252306' &&
assertion.repository == 'dhelmy990/babel' &&
assertion.ref == 'refs/heads/demo'
```

Configure mappings for `google.subject=assertion.sub`,
`attribute.repository=assertion.repository`, `attribute.ref=assertion.ref`,
`attribute.repository_id=assertion.repository_id`, and
`attribute.repository_owner_id=assertion.repository_owner_id`.
Bind `roles/iam.workloadIdentityUser` only to the resulting repository/ref
principal set. The protected `gcp-demo` environment must allow only `demo`;
the workflow repeats this guard for manual dispatches.

Grant the deployer `roles/artifactregistry.writer` on only the `babel-demo`
repository, `roles/compute.viewer`, `roles/iap.tunnelResourceAccessor`, and
`roles/compute.osAdminLogin` (the rollout invokes root-owned files through
`sudo`). Grant the VM's own service account `roles/artifactregistry.reader` on
only `babel-demo`. Do not grant either principal project Owner or Editor.

The VM requires Docker Engine, Compose v2, the NVIDIA container toolkit,
Python 3, curl, `sha256sum`, `flock` (util-linux), an Artifact Registry credential helper configured
for root, and `/var/lib/babel-online` on persistent storage. Provision
`/etc/babel/runtime.env` mode `0600`, owned by root, with at least:

```dotenv
BABEL_DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:54329/babel
BABEL_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:29092
BABEL_INSTANCE_TOKEN=64-lowercase-hex-digits
BABEL_ONLINE_WORKER_TOKEN=64-lowercase-hex-digits
HF_TOKEN=private-hugging-face-read-token
```

PostgreSQL/pgvector, Kafka, the imported 10,000-vector population, and its HNSW
index are provisioned separately. CD never populates or re-encodes them. Before
stopping the prior release, it recomputes the ordered-vector and snapshot
hashes and checks fresh IDs, model/dataset provenance, 10,000 finite 100d
vectors, catalog identity, and a valid/ready HNSW index. A mismatch is terminal.

## Operation and rollback

Push the audited deployment commit to `demo`. A VM-side `flock` serializes
rollouts even when GitHub cancels an older job; TERM, INT, and SSH HUP trigger
rollback after promotion starts, while monotonic GitHub run IDs prevent an
older attempt from winning. A failed test, build, push, checksum, migration,
CUDA probe, or pre-promotion gate leaves the current application running.

After restart, CD requires fresh run-bound trainer readiness, exact running
image digests and source labels, exact model identity/version, and one bounded
CUDA Qwen recommendation. Readiness must be newer than both the rollout and the
current trainer container's Docker `StartedAt`; the container ID, PID, start
time, and restart count must also remain unchanged throughout verification.
This rejects a readiness file left behind by a SIGKILL/OOM restart.

Rollback is an application-image rollback, not a database down-migration. Demo
CI rejects every migration-file change relative to `326b840`; future schema
work requires an explicitly reviewed expand-contract sequence that keeps the
previous images compatible. Every restore operation is checked explicitly,
including readiness removal, previous-image attestation, and both symlink
operations. A failed restore remains failed and never repoints `current`.

A first deployment is allowed only when `/opt/babel/current` does not exist.
Once it exists, its `release.env` must satisfy the current 12-key attestation
contract and be older than the candidate. The earlier seven-key prototype is
intentionally rejected rather than treated as a rollback target; an operator
must explicitly remove a confirmed never-deployed prototype link before the
first real deployment. Successful receipts are stored at:

```text
/opt/babel/current/deployment-receipt.json
```

The receipt records the source commit, backend/serving/trainer digests, fixed
model and dataset revisions, run ID, and deployment time. To inspect the local
dashboard without opening a public firewall rule:

```bash
gcloud compute ssh VM_NAME --zone VM_ZONE --tunnel-through-iap \
  -- -N -L 8787:127.0.0.1:8787
```

The backend dashboard is read-only in this topology. Both its online-worker
and performance-worker endpoints are forced to closed loopback port 9, so
population, approval, and matrix-control actions fail closed even if the VM
contains old worker credentials. Add the performance worker only after the
reviewed no-reencoding guard is replayed; recommendation serving remains available.
