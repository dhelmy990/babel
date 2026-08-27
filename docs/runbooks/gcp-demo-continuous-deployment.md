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

No GitHub secret is required for GCP authentication. Do not create or upload a
service-account JSON key.

## One-time GCP setup

Create a new Artifact Registry Docker repository named `babel-demo`, a new
service account named `babel-github-deployer`, and a new provider named
`babel-demo` in the existing `github` workload identity pool. Restrict its
attribute condition to both:

```text
assertion.repository == 'dhelmy990/babel'
assertion.ref == 'refs/heads/demo'
```

Grant the deployer only Artifact Registry write, Compute instance read, IAP
tunnel access, and OS Login permissions required to reach the named VM. Grant
the VM's own service account Artifact Registry read. Do not grant either
principal project Owner or Editor.

The VM requires Docker Engine, Compose v2, the NVIDIA container toolkit,
Python 3, curl, `sha256sum`, an Artifact Registry credential helper configured
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
index are provisioned separately. CD never populates or re-encodes them.

## Operation and rollback

Push the audited deployment commit to `demo`. A failed test, build, push,
checksum, migration, CUDA probe, or pre-promotion validation leaves the current
application running. A failed post-restart health check restarts the previous
release. Successful receipts are stored at:

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
