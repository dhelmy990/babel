# GCP GPU Experiment Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the complete 10,000-vector Babel performance experiment and nine-condition matrix on an isolated GCP NVIDIA L4 VM without touching the active local experiment.

**Architecture:** Provision one time-limited `g2-standard-8` in a dedicated Singapore VPC. Run fresh PostgreSQL, Kafka, C++ backend, Python performance worker, CUDA Qwen encoder, and condition subprocesses on that VM; reach the loopback dashboard only through IAP SSH. Capture every gate in a separate handoff directory, stop the VM after evidence retrieval, and leave deletion for a separately confirmed cleanup action.

**Tech Stack:** Google Cloud CLI and Cloud Quotas API, Compute Engine G2/L4, IAP, Ubuntu 22.04 Deep Learning VM, NVIDIA 580 driver, Docker Compose, PostgreSQL 18 with pgvector, Kafka 4.3.1, CMake 3.31.10, vcpkg baseline `127402f1c75bb3d5ff6bce04b285faa4930a5aca`, uv 0.12.3, Python 3.10, Torch 2.6/CUDA 12, systemd, Bash, jq, curl, Git.

## Global Constraints

- Approved design: `docs/superpowers/specs/2026-08-27-gcp-gpu-experiment-deployment-design.md` at or after commit `e2f75ee`.
- GCP project: `chloe-tutoring-bot`; region: `asia-southeast1`.
- VM: on-demand `g2-standard-8`, one NVIDIA L4, 100 GB `pd-balanced`, exact image `common-cu129-ubuntu-2204-nvidia-580-v20260818`.
- Zone order: `asia-southeast1-b`, `asia-southeast1-a`, `asia-southeast1-c`; retry only a capacity-exhaustion failure.
- Application source must equal `f8b2a290e86d28256294807bec4d8d26ac6c04e6` from `origin/codex/slices-1-2`.
- Model: `dhelmy990/babel-qwen-navigation-2016-interview@57d949cd634b920cc1a46f27c9b21df094b5240e`, artifact `3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8`.
- Base model/tokenizer: `Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
- Dataset: `dhelmy990/babel-wikipedia-experiment@0d1ab2c7f0e2295682288fcf10077d2d776bf559`, configuration `crosswalk_2026_06_07`.
- Qwen encoding and recommendation serving use `BABEL_ONLINE_QWEN_DEVICE=cuda`; the NumPy online trainer stays on CPU.
- Formal defaults stay unchanged: 50 creators, 10,000 source/created Babels, 50 concurrent users, pgvector, 0.40 start/continuation, depth 2, request cap 10, training batch 8, sync every 10 steps, 30-second warmup, 120-second measurement, 5 RPS, no auto-advance.
- No command may target the local trial `ce8e54ff-e317-4a89-b7db-90327e02dc43`, its run ID, local ports, local Docker resources, or its active worktree.
- No local database or Kafka tunnel is allowed. The GCP trial receives fresh runtime identities and storage.
- Never print or pass `HF_TOKEN` as a command argument. Transfer it only through SSH standard input and store it mode `0640`, readable by root and the `babel-gpu` service group.
- No application port is public. Only TCP 22 from `35.235.240.0/20` reaches the VM, and the dashboard uses IAP forwarding.
- Operational cost ceiling: USD 10. VM automatic stop: six hours. Explicitly stop it after evidence retrieval; do not delete it or its disk in this plan.
- Backend may restart on failure. Performance worker uses `Restart=no`. Population can resume explicitly from its committed journal; an interrupted matrix cannot resume and requires a fresh trial.
- Do not publish the bundle to Hugging Face in this plan. Export and build it locally on the VM; publication is a separately authorized action.

## File and Evidence Map

Repository files are read-only during deployment. This plan creates operational files outside Git:

- Local handoff root: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/`
- Local execution environment: `operator.env` (mode `0600`, contains no `HF_TOKEN`)
- Local preflight: `preflight.json`, `local-baseline-before.json`, `local-baseline-after.json`
- Local cloud receipts: `quota/`, `infrastructure/`, `remote/`, `handoff/`
- Remote application checkout: `/opt/babel-gpu/repo`
- Remote protected configuration: `/etc/babel-gpu/runtime.env`, `/etc/babel-gpu/{hf,performance-worker,instance}.token`
- Remote persistent state: `/var/lib/babel-gpu/{cache,state,evidence,runs}`
- Remote units: `/etc/systemd/system/babel-gpu-{foundation,backend,worker}.service`
- Remote CUDA receipt: `/var/lib/babel-gpu/evidence/cuda-acceptance.json`
- Remote formal evidence: `/var/lib/babel-gpu/state/performance/$trial_id/`
- Remote export/build roots: `/var/lib/babel-gpu/runs/$trial_id/{export,handoff,accepted}`

Every task consumes receipts from the previous task. If an assertion fails, stop that task and preserve its outputs; do not skip forward.

---

### Task 1: Freeze execution identity and local-isolation baseline

**Files:**
- Create remote: `/var/lib/babel-gpu/evidence/population-progress.jsonl`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/operator.env`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/preflight.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/local-baseline-before.json`
- Read: `/home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2/docs/runbooks/scaled-experiment.md`

**Interfaces:**
- Consumes: approved design and authenticated local `gcloud`.
- Produces: immutable variable file and proof of the untouched local experiment before cloud mutation.

- [ ] **Step 1: Create a protected handoff root and operator variables**

Run locally:

```bash
install -d -m 0700 /home/dhelmy990/Data/babel-data/gcp-gpu-20260827/{quota,infrastructure,remote,handoff}
install -m 0600 /dev/null /home/dhelmy990/Data/babel-data/gcp-gpu-20260827/operator.env
```

Use `apply_patch` to put these exact non-secret values in `operator.env`:

```bash
export BABEL_GCP_PROJECT='chloe-tutoring-bot'
export BABEL_GCP_REGION='asia-southeast1'
export BABEL_GCP_ZONES='asia-southeast1-b asia-southeast1-a asia-southeast1-c'
export BABEL_GCP_NETWORK='babel-gpu-net-20260827'
export BABEL_GCP_SUBNET='babel-gpu-sg-20260827'
export BABEL_GCP_FIREWALL='babel-gpu-iap-ssh-20260827'
export BABEL_GCP_VM='babel-gpu-20260827'
export BABEL_GCP_IMAGE='common-cu129-ubuntu-2204-nvidia-580-v20260818'
export BABEL_GCP_SOURCE_SHA='f8b2a290e86d28256294807bec4d8d26ac6c04e6'
export BABEL_GCP_HANDOFF_ROOT='/home/dhelmy990/Data/babel-data/gcp-gpu-20260827'
```

Expected: `stat -c '%a' operator.env` prints `600`; `rg 'HF_TOKEN' operator.env` finds nothing.

- [ ] **Step 2: Assert the remote application source**

```bash
source /home/dhelmy990/Data/babel-data/gcp-gpu-20260827/operator.env
remote_sha="$(git ls-remote https://github.com/dhelmy990/babel.git refs/heads/codex/slices-1-2 | awk '{print $1}')"
test "$remote_sha" = "$BABEL_GCP_SOURCE_SHA"
```

Expected: exit 0 and no output.

- [ ] **Step 3: Capture the local worker and database without exposing environment values**

Run the following, replacing no IDs:

```bash
local_pid="$(pgrep -f '/slices-1-2/online/.venv/bin/babel-online performance-worker' | head -n1)"
test -n "$local_pid"
local_cwd="$(readlink -f "/proc/$local_pid/cwd")"
test "$local_cwd" = '/home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2'

docker exec babel-postgres-1 psql -U babel -d babel -At -F $'\t' -c \
  "SELECT id,status,population_ready,COALESCE(population_vector_count,0),COALESCE(failure,'')
     FROM performance_experiments
    WHERE id='ce8e54ff-e317-4a89-b7db-90327e02dc43';" \
  > "$BABEL_GCP_HANDOFF_ROOT/local-trial-before.tsv"

jq -n \
  --arg capturedAt "$(date --iso-8601=seconds)" \
  --arg pid "$local_pid" \
  --arg cwd "$local_cwd" \
  --arg command "$(tr '\0' ' ' < "/proc/$local_pid/cmdline")" \
  --arg trial "$(cat "$BABEL_GCP_HANDOFF_ROOT/local-trial-before.tsv")" \
  '{capturedAt:$capturedAt,pid:($pid|tonumber),cwd:$cwd,command:$command,trialRow:$trial}' \
  > "$BABEL_GCP_HANDOFF_ROOT/local-baseline-before.json"
```

Expected: the JSON contains the worker PID/cwd and the local trial row; it contains no environment variables or tokens.

- [ ] **Step 4: Capture read-only GCP preflight**

```bash
active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
test -n "$active_account"
billing="$(gcloud beta billing projects describe "$BABEL_GCP_PROJECT" --format='value(billingEnabled)')"
test "$billing" = 'True'
role_count="$(gcloud projects get-iam-policy "$BABEL_GCP_PROJECT" --format=json | \
  jq --arg member "user:$active_account" '[.bindings[] | select(.members[]? == $member and .role == "roles/owner")] | length')"
test "$role_count" -ge 1

jq -n \
  --arg capturedAt "$(date --iso-8601=seconds)" \
  --arg project "$BABEL_GCP_PROJECT" \
  --arg sourceSha "$BABEL_GCP_SOURCE_SHA" \
  --arg billing "$billing" \
  --argjson ownerRoleCount "$role_count" \
  '{capturedAt:$capturedAt,project:$project,sourceSha:$sourceSha,billingEnabled:($billing=="True"),ownerRoleCount:$ownerRoleCount}' \
  > "$BABEL_GCP_HANDOFF_ROOT/preflight.json"
```

Expected: `billingEnabled` is true and `ownerRoleCount` is at least one.

---

### Task 2: Request and verify the global GPU quota

**Files:**
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/quota/quota-info.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/quota/preference.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/quota/effective.json`

**Interfaces:**
- Consumes: `operator.env` and authenticated Owner account.
- Produces: effective global GPU quota at least one and regional L4 quota at least one.

- [ ] **Step 1: Reconfirm the current quota before changing it**

```bash
source /home/dhelmy990/Data/babel-data/gcp-gpu-20260827/operator.env
gcloud compute project-info describe --project="$BABEL_GCP_PROJECT" --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/quota/project-before.json"
jq -e '.quotas[] | select(.metric == "GPUS_ALL_REGIONS" and .limit == 0)' \
  "$BABEL_GCP_HANDOFF_ROOT/quota/project-before.json" >/dev/null
```

Expected: confirms the design-time blocker is still present. If quota is already at least one, skip only the preference creation and continue to Step 4.

- [ ] **Step 2: Enable Cloud Quotas API and resolve the quota ID from metadata**

```bash
gcloud services enable cloudquotas.googleapis.com --project="$BABEL_GCP_PROJECT" --quiet
gcloud beta quotas info list \
  --service=compute.googleapis.com \
  --project="$BABEL_GCP_PROJECT" \
  --billing-project="$BABEL_GCP_PROJECT" \
  --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/quota/quota-info.json"

global_quota_id="$(jq -r '[.[] | select(.metric == "compute.googleapis.com/gpus_all_regions") | .quotaId] | unique | .[]' \
  "$BABEL_GCP_HANDOFF_ROOT/quota/quota-info.json")"
test -n "$global_quota_id"
test "$(printf '%s\n' "$global_quota_id" | wc -l)" -eq 1
```

Expected: exactly one nonblank quota ID is discovered from the API. Do not hard-code a guessed quota ID if discovery returns none.

- [ ] **Step 3: Submit or update one preference for value 1**

```bash
contact_email="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
preference_id='babel-gpus-all-regions-20260827'

if gcloud beta quotas preferences describe "$preference_id" \
     --project="$BABEL_GCP_PROJECT" --billing-project="$BABEL_GCP_PROJECT" \
     --format=json > "$BABEL_GCP_HANDOFF_ROOT/quota/preference.json" 2>/dev/null; then
  gcloud beta quotas preferences update "$preference_id" \
    --service=compute.googleapis.com \
    --quota-id="$global_quota_id" \
    --preferred-value=1 \
    --project="$BABEL_GCP_PROJECT" \
    --billing-project="$BABEL_GCP_PROJECT" \
    --email="$contact_email" \
    --justification='One on-demand NVIDIA L4 for a time-limited 10,000-vector inference experiment; VM auto-stops after six hours.' \
    --format=json > "$BABEL_GCP_HANDOFF_ROOT/quota/preference.json"
else
  gcloud beta quotas preferences create \
    --preference-id="$preference_id" \
    --service=compute.googleapis.com \
    --quota-id="$global_quota_id" \
    --preferred-value=1 \
    --project="$BABEL_GCP_PROJECT" \
    --billing-project="$BABEL_GCP_PROJECT" \
    --email="$contact_email" \
    --justification='One on-demand NVIDIA L4 for a time-limited 10,000-vector inference experiment; VM auto-stops after six hours.' \
    --format=json > "$BABEL_GCP_HANDOFF_ROOT/quota/preference.json"
fi
```

Expected: preference JSON has `preferredValue` 1. This request creates no billable compute resource.

- [ ] **Step 4: Poll the effective global quota for at most 30 minutes**

```bash
quota_deadline=$((SECONDS + 1800))
while (( SECONDS < quota_deadline )); do
  effective="$(gcloud compute project-info describe --project="$BABEL_GCP_PROJECT" --format=json | \
    jq -r '.quotas[] | select(.metric == "GPUS_ALL_REGIONS") | .limit')"
  if awk -v value="$effective" 'BEGIN { exit !(value >= 1) }'; then
    break
  fi
  sleep 30
done
awk -v value="${effective:-0}" 'BEGIN { exit !(value >= 1) }'
```

Expected: exit 0 within 30 minutes. If it fails, stop the implementation and report the pending quota; do not provision CPU-only infrastructure.

- [ ] **Step 5: Verify both global and Singapore L4 capacity quotas**

```bash
gcloud compute project-info describe --project="$BABEL_GCP_PROJECT" --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/quota/global-effective.json"
gcloud compute regions describe "$BABEL_GCP_REGION" --project="$BABEL_GCP_PROJECT" --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/quota/regional-effective.json"

global_available="$(jq -r '.quotas[] | select(.metric=="GPUS_ALL_REGIONS") | (.limit-.usage)' \
  "$BABEL_GCP_HANDOFF_ROOT/quota/global-effective.json")"
l4_available="$(jq -r '.quotas[] | select(.metric=="NVIDIA_L4_GPUS") | (.limit-.usage)' \
  "$BABEL_GCP_HANDOFF_ROOT/quota/regional-effective.json")"
awk -v value="$global_available" 'BEGIN { exit !(value >= 1) }'
awk -v value="$l4_available" 'BEGIN { exit !(value >= 1) }'

jq -n --arg capturedAt "$(date --iso-8601=seconds)" \
  --argjson globalAvailable "$global_available" --argjson l4Available "$l4_available" \
  '{capturedAt:$capturedAt,globalGpuAvailable:$globalAvailable,singaporeL4Available:$l4Available}' \
  > "$BABEL_GCP_HANDOFF_ROOT/quota/effective.json"
```

Expected: both available values are at least one.

---

### Task 3: Provision and audit isolated GCP infrastructure

**Files:**
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/infrastructure/network.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/infrastructure/firewall.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/infrastructure/vm.json`
- Modify: `operator.env` to append the successful zone only.

**Interfaces:**
- Consumes: verified quota from Task 2.
- Produces: one stopped-after-six-hours L4 VM reachable only by IAP SSH.

- [ ] **Step 1: Assert resource names do not already exist**

```bash
for name in "$BABEL_GCP_NETWORK" "$BABEL_GCP_SUBNET" "$BABEL_GCP_FIREWALL" "$BABEL_GCP_VM"; do
  test -n "$name"
  case "$name" in babel-*) ;; *) exit 1 ;; esac
done

! gcloud compute networks describe "$BABEL_GCP_NETWORK" --project="$BABEL_GCP_PROJECT" >/dev/null 2>&1
! gcloud compute firewall-rules describe "$BABEL_GCP_FIREWALL" --project="$BABEL_GCP_PROJECT" >/dev/null 2>&1
! gcloud compute instances list --project="$BABEL_GCP_PROJECT" --filter="name=($BABEL_GCP_VM)" --format='value(name)' | rg .
```

Expected: all assertions pass. If a same-named resource exists, inspect it and choose a new date-suffixed `babel-*` name before any create call.

- [ ] **Step 2: Create the dedicated VPC, subnet, and IAP-only SSH rule**

```bash
gcloud compute networks create "$BABEL_GCP_NETWORK" \
  --project="$BABEL_GCP_PROJECT" --subnet-mode=custom --bgp-routing-mode=regional

gcloud compute networks subnets create "$BABEL_GCP_SUBNET" \
  --project="$BABEL_GCP_PROJECT" --region="$BABEL_GCP_REGION" \
  --network="$BABEL_GCP_NETWORK" --range=10.42.0.0/24

gcloud compute firewall-rules create "$BABEL_GCP_FIREWALL" \
  --project="$BABEL_GCP_PROJECT" --network="$BABEL_GCP_NETWORK" \
  --direction=INGRESS --priority=1000 --action=ALLOW --rules=tcp:22 \
  --source-ranges=35.235.240.0/20 --target-tags=babel-gpu-iap
```

Expected: three creates succeed; no other ingress rule is added to the VPC.

- [ ] **Step 3: Resolve and verify the exact image before VM creation**

```bash
image_self_link="$(gcloud compute images describe "$BABEL_GCP_IMAGE" \
  --project=deeplearning-platform-release --format='value(selfLink)')"
test -n "$image_self_link"
test "$(basename "$image_self_link")" = "$BABEL_GCP_IMAGE"
```

Expected: exact versioned image, not a moving family.

- [ ] **Step 4: Create the L4 VM in the first available approved zone**

Run the create command first with `asia-southeast1-b`:

```bash
BABEL_GCP_ZONE='asia-southeast1-b'
gcloud compute instances create "$BABEL_GCP_VM" \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
  --machine-type=g2-standard-8 \
  --image="$BABEL_GCP_IMAGE" --image-project=deeplearning-platform-release \
  --boot-disk-size=100GB --boot-disk-type=pd-balanced --boot-disk-auto-delete \
  --network-interface="subnet=$BABEL_GCP_SUBNET,network-tier=PREMIUM" \
  --tags=babel-gpu-iap \
  --labels="application=babel,purpose=gpu-experiment,run-date=20260827,source-commit=f8b2a29" \
  --metadata=enable-oslogin=TRUE \
  --provisioning-model=STANDARD --maintenance-policy=TERMINATE \
  --no-restart-on-failure --max-run-duration=6h --instance-termination-action=STOP \
  --no-service-account --no-scopes --deletion-protection \
  --shielded-vtpm --shielded-integrity-monitoring
```

Expected: VM creation succeeds. If and only if stderr contains a resource-pool/capacity-exhaustion error, rerun the identical command with `asia-southeast1-a`, then `asia-southeast1-c`. Any quota, permission, flag, image, or network error stops the task instead of advancing zones.

- [ ] **Step 5: Persist the selected zone and infrastructure receipts**

Use `apply_patch` to append exactly one of these lines to `operator.env`, matching the zone whose create command succeeded:

```bash
export BABEL_GCP_ZONE='asia-southeast1-b'
export BABEL_GCP_ZONE='asia-southeast1-a'
export BABEL_GCP_ZONE='asia-southeast1-c'
```

Append only one line, source the file again, and require `case "$BABEL_GCP_ZONE" in asia-southeast1-b|asia-southeast1-a|asia-southeast1-c) ;; *) exit 1 ;; esac` before continuing.

Then run:

```bash
source "$BABEL_GCP_HANDOFF_ROOT/operator.env"
gcloud compute networks describe "$BABEL_GCP_NETWORK" --project="$BABEL_GCP_PROJECT" --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/infrastructure/network.json"
gcloud compute firewall-rules describe "$BABEL_GCP_FIREWALL" --project="$BABEL_GCP_PROJECT" --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/infrastructure/firewall.json"
gcloud compute instances describe "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm.json"
```

Expected: receipts exist before remote mutation.

- [ ] **Step 6: Audit GPU, timer, identity, and firewall invariants**

```bash
jq -e '
  .machineType | endswith("/g2-standard-8")
' "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm.json" >/dev/null
jq -e '
  (.guestAccelerators | length) == 1 and
  (.guestAccelerators[0].acceleratorType | endswith("/nvidia-l4"))
' "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm.json" >/dev/null
jq -e '.scheduling.automaticRestart == false and .scheduling.onHostMaintenance == "TERMINATE"' \
  "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm.json" >/dev/null
jq -e '.sourceRanges == ["35.235.240.0/20"] and .allowed == [{"IPProtocol":"tcp","ports":["22"]}]' \
  "$BABEL_GCP_HANDOFF_ROOT/infrastructure/firewall.json" >/dev/null
```

Also inspect `terminationTimestamp` in `vm.json`; it must be approximately six hours after creation. Expected: every assertion passes and there is no firewall rule for 8787, 8791, 8792, 54329, or 29092.

---

### Task 4: Bootstrap the immutable VM software environment

**Files:**
- Create remote: `/opt/babel-gpu/repo`
- Create remote: `/var/lib/babel-gpu/{cache,state,evidence,runs}`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/remote/bootstrap.log`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/remote/source-and-tools.json`

**Interfaces:**
- Consumes: running VM and selected zone from Task 3.
- Produces: exact source, pinned vcpkg, locked Python environment, compiled backend, and passing dependency-boundary tests.

- [ ] **Step 1: Wait for IAP SSH without opening public SSH**

```bash
for attempt in $(seq 1 24); do
  if gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
       --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command='true' --quiet; then
    break
  fi
  sleep 10
done
test "$attempt" -lt 24
```

Expected: IAP SSH succeeds within four minutes.

- [ ] **Step 2: Install required system packages and create the service account**

Run through IAP SSH and tee output to `remote/bootstrap.log` locally:

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command="
    set -euo pipefail
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
      build-essential ca-certificates curl git jq ninja-build openssl pkg-config \
      python3 python3-venv tar unzip zip docker.io docker-compose-v2
    sudo systemctl enable --now docker
    if ! id babel-gpu >/dev/null 2>&1; then
      sudo useradd --system --user-group --create-home --home-dir /var/lib/babel-gpu \
        --shell /usr/sbin/nologin babel-gpu
    fi
    sudo usermod -aG babel-gpu "$USER"
    sudo install -d -m 0755 -o babel-gpu -g babel-gpu /opt/babel-gpu
    sudo install -d -m 0770 -o babel-gpu -g babel-gpu \
      /var/lib/babel-gpu/cache /var/lib/babel-gpu/state \
      /var/lib/babel-gpu/evidence /var/lib/babel-gpu/runs
    nvidia-smi
    docker compose version
  " | tee "$BABEL_GCP_HANDOFF_ROOT/remote/bootstrap.log"
```

Expected: `nvidia-smi` names an L4 and Docker Compose reports a version. Reconnect before Task 5 and require `id -nG | tr ' ' '\n' | rg -x babel-gpu`; this grants the operator write access to evidence paths and read access to protected configuration without granting the service account sudo. If the package install or `docker compose version` fails, preserve `bootstrap.log` and stop with a bootstrap blocker; do not substitute legacy `docker-compose`.

- [ ] **Step 3: Install exact uv and CMake versions**

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command="
    set -euo pipefail
    curl -LsSf https://astral.sh/uv/0.12.3/install.sh | \
      sudo env UV_INSTALL_DIR=/usr/local/bin sh
    sudo -u babel-gpu -H /usr/local/bin/uv tool install 'cmake==3.31.10'
    test \"\$(/usr/local/bin/uv --version)\" = 'uv 0.12.3'
    test \"\$(/var/lib/babel-gpu/.local/bin/cmake --version | head -n1)\" = 'cmake version 3.31.10'
  "
```

Expected: exact versions match.

- [ ] **Step 4: Clone and detach the exact application source**

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command="
    set -euo pipefail
    sudo -u babel-gpu -H git clone --branch codex/slices-1-2 --single-branch \
      https://github.com/dhelmy990/babel.git /opt/babel-gpu/repo
    sudo -u babel-gpu -H git -C /opt/babel-gpu/repo checkout --detach '$BABEL_GCP_SOURCE_SHA'
    test \"\$(sudo -u babel-gpu -H git -C /opt/babel-gpu/repo rev-parse HEAD)\" = '$BABEL_GCP_SOURCE_SHA'
    test -z \"\$(sudo -u babel-gpu -H git -C /opt/babel-gpu/repo status --porcelain)\"
  "
```

Expected: detached exact SHA and clean worktree.

- [ ] **Step 5: Bootstrap exact vcpkg baseline and build the backend**

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command="
    set -euo pipefail
    sudo -u babel-gpu -H git clone https://github.com/microsoft/vcpkg.git /var/lib/babel-gpu/.cache/vcpkg
    sudo -u babel-gpu -H git -C /var/lib/babel-gpu/.cache/vcpkg checkout --detach \
      127402f1c75bb3d5ff6bce04b285faa4930a5aca
    sudo -u babel-gpu -H /var/lib/babel-gpu/.cache/vcpkg/bootstrap-vcpkg.sh -disableMetrics
    sudo -u babel-gpu -H env \
      PATH=/var/lib/babel-gpu/.local/bin:/usr/local/bin:/usr/bin:/bin \
      VCPKG_ROOT=/var/lib/babel-gpu/.cache/vcpkg \
      /var/lib/babel-gpu/.local/bin/cmake --preset dev -S /opt/babel-gpu/repo
    sudo -u babel-gpu -H env \
      PATH=/var/lib/babel-gpu/.local/bin:/usr/local/bin:/usr/bin:/bin \
      VCPKG_ROOT=/var/lib/babel-gpu/.cache/vcpkg \
      /var/lib/babel-gpu/.local/bin/cmake --build /opt/babel-gpu/repo/build/dev --parallel 8
    test -x /opt/babel-gpu/repo/build/dev/backend/babel_backend
  "
```

Expected: build succeeds and backend executable exists.

- [ ] **Step 6: Install the locked online and benchmark packages**

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command="
    set -euo pipefail
    cd /opt/babel-gpu/repo
    sudo -u babel-gpu -H /usr/local/bin/uv sync --frozen --project online \
      --extra dev --extra kafka --extra parquet --extra pgvector --extra qwen
    sudo -u babel-gpu -H /usr/local/bin/uv pip install \
      --python online/.venv/bin/python -e benchmark
    sudo -u babel-gpu -H online/.venv/bin/python -c \
      'import torch; print(torch.__version__, torch.version.cuda)'
  "
```

Expected: Torch version is in `[2.6,2.7)` and reports a CUDA build.

- [ ] **Step 7: Run bounded dependency-contract tests before secrets**

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command="
    set -euo pipefail
    cd /opt/babel-gpu/repo
    sudo -u babel-gpu -H env PYTHONPATH=benchmark/src:online/src \
      online/.venv/bin/python -m pytest \
      online/tests/model/test_qwen_encoder.py \
      online/tests/model/test_population.py \
      online/tests/runtime/test_performance_worker.py -q
  "
```

Expected: all selected tests pass. No Hugging Face token is needed for these fixture-bound tests.

- [ ] **Step 8: Save source/tool identity**

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command="
    jq -n \
      --arg sourceSha \"\$(git -C /opt/babel-gpu/repo rev-parse HEAD)\" \
      --arg vcpkgSha \"\$(git -C /var/lib/babel-gpu/.cache/vcpkg rev-parse HEAD)\" \
      --arg uv \"\$(/usr/local/bin/uv --version)\" \
      --arg cmake \"\$(/var/lib/babel-gpu/.local/bin/cmake --version | head -n1)\" \
      '{sourceSha:\$sourceSha,vcpkgSha:\$vcpkgSha,uv:\$uv,cmake:\$cmake}'
  " > "$BABEL_GCP_HANDOFF_ROOT/remote/source-and-tools.json"
```

Expected: all identities equal the global constraints.

---

### Task 5: Install protected runtime configuration and supervised services

**Files:**
- Create local staging: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/staging/`
- Create remote: `/etc/babel-gpu/{hf,performance-worker,instance}.token`
- Create remote: `/etc/babel-gpu/runtime.env`
- Create remote: `/usr/local/libexec/babel-gpu/{run-backend,run-worker}`
- Create remote: `/etc/systemd/system/babel-gpu-{foundation,backend,worker}.service`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/remote/service-units.txt`

**Interfaces:**
- Consumes: compiled checkout, locked Python environment, and local `HF_TOKEN`.
- Produces: protected, non-public services with backend auto-recovery and worker `Restart=no`.

- [ ] **Step 1: Transfer the Hugging Face token and generate two independent runtime tokens**

Run locally without `set -x`:

```bash
set -a
source /home/dhelmy990/Code/babel/.env
set +a
test -n "${HF_TOKEN:-}"
printf '%s' "$HF_TOKEN" | gcloud compute ssh "$BABEL_GCP_VM" \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" --tunnel-through-iap \
  --command="sudo install -d -m 0750 -o root -g babel-gpu /etc/babel-gpu &&
             sudo install -m 0640 -o root -g babel-gpu /dev/stdin /etc/babel-gpu/hf.token"
unset HF_TOKEN

gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command='
    set -euo pipefail
    openssl rand -hex 32 | sudo install -m 0640 -o root -g babel-gpu \
      /dev/stdin /etc/babel-gpu/performance-worker.token
    openssl rand -hex 32 | sudo install -m 0640 -o root -g babel-gpu \
      /dev/stdin /etc/babel-gpu/instance.token
    for path in /etc/babel-gpu/hf.token \
                /etc/babel-gpu/performance-worker.token \
                /etc/babel-gpu/instance.token; do
      test "$(sudo stat -c "%a %U %G" "$path")" = "640 root babel-gpu"
    done
    sudo awk "length != 64 || /[^0-9a-f]/ { exit 1 }" \
      /etc/babel-gpu/performance-worker.token /etc/babel-gpu/instance.token
  '
```

Expected: all three files are `0640 root:babel-gpu`; generated tokens are 64 lowercase hex characters. No token value is printed or written locally.

- [ ] **Step 2: Stage the non-secret runtime environment locally**

Run `install -d -m 0700 "$BABEL_GCP_HANDOFF_ROOT/staging"`, then use `apply_patch` to create `$BABEL_GCP_HANDOFF_ROOT/staging/runtime.env` with this exact key set:

```bash
BABEL_DATABASE_URL=postgresql://babel:babel-local-dev@127.0.0.1:54329/babel
BABEL_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:29092
BABEL_ONLINE_DATASET_REPOSITORY=dhelmy990/babel-wikipedia-experiment
BABEL_ONLINE_DATASET_CONFIG=crosswalk_2026_06_07
BABEL_ONLINE_DATASET_REVISION=0d1ab2c7f0e2295682288fcf10077d2d776bf559
BABEL_ONLINE_MODEL_MODE=real_qwen
BABEL_ONLINE_QWEN_DEVICE=cuda
BABEL_RUNTIME_TOPOLOGY=same_host_split
BABEL_PERFORMANCE_STATE_ROOT=/var/lib/babel-gpu/state/performance
BABEL_ONLINE_HF_CACHE=/var/lib/babel-gpu/cache/dataset
BABEL_ONLINE_MODEL_ARTIFACT_CACHE=/var/lib/babel-gpu/cache/model-artifact
BABEL_ONLINE_QWEN_CACHE=/var/lib/babel-gpu/cache/qwen-base
BABEL_DATA_ROOT=/var/lib/babel-gpu
BABEL_ONLINE_EXECUTABLE=/opt/babel-gpu/repo/online/.venv/bin/babel-online
BABEL_RECOMMENDATION_PORT=8791
BABEL_PERFORMANCE_WORKER_ENDPOINT=http://127.0.0.1:8792
PATH=/opt/babel-gpu/repo/online/.venv/bin:/usr/local/bin:/usr/bin:/bin
```

Expected: `rg '(^HF_TOKEN=|TOKEN=)' "$BABEL_GCP_HANDOFF_ROOT/staging/runtime.env"` finds nothing. The two runtime tokens remain in their separate protected remote files.

- [ ] **Step 3: Stage token-loading launchers**

Use `apply_patch` to create `$BABEL_GCP_HANDOFF_ROOT/staging/run-backend`:

```bash
#!/usr/bin/env bash
set -euo pipefail
export HF_TOKEN="$(< /etc/babel-gpu/hf.token)"
export BABEL_PERFORMANCE_WORKER_TOKEN="$(< /etc/babel-gpu/performance-worker.token)"
export BABEL_INSTANCE_TOKEN="$(< /etc/babel-gpu/instance.token)"
exec /opt/babel-gpu/repo/build/dev/backend/babel_backend "$@"
```

Use `apply_patch` to create `$BABEL_GCP_HANDOFF_ROOT/staging/run-worker`:

```bash
#!/usr/bin/env bash
set -euo pipefail
export HF_TOKEN="$(< /etc/babel-gpu/hf.token)"
export BABEL_PERFORMANCE_WORKER_TOKEN="$(< /etc/babel-gpu/performance-worker.token)"
exec /opt/babel-gpu/repo/online/.venv/bin/babel-online performance-worker
```

Expected: `bash -n` passes for both staged files and neither contains a token value.

- [ ] **Step 4: Stage the foundation systemd unit**

Use `apply_patch` to create `$BABEL_GCP_HANDOFF_ROOT/staging/babel-gpu-foundation.service`:

```ini
[Unit]
Description=Babel GPU PostgreSQL and Kafka
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/babel-gpu/repo
ExecStart=/usr/bin/docker compose -p babel-gpu up -d --wait postgres kafka
ExecStop=/usr/bin/docker compose -p babel-gpu stop postgres kafka
TimeoutStartSec=180
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Stage the backend systemd unit**

Use `apply_patch` to create `$BABEL_GCP_HANDOFF_ROOT/staging/babel-gpu-backend.service`:

```ini
[Unit]
Description=Babel GPU dashboard backend
Requires=babel-gpu-foundation.service
After=babel-gpu-foundation.service

[Service]
Type=simple
User=babel-gpu
Group=babel-gpu
WorkingDirectory=/opt/babel-gpu/repo
EnvironmentFile=/etc/babel-gpu/runtime.env
ExecStartPre=/usr/local/libexec/babel-gpu/run-backend migrate
ExecStart=/usr/local/libexec/babel-gpu/run-backend serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/babel-gpu

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Stage the non-restarting worker systemd unit**

Use `apply_patch` to create `$BABEL_GCP_HANDOFF_ROOT/staging/babel-gpu-worker.service`:

```ini
[Unit]
Description=Babel GPU performance worker
Requires=babel-gpu-foundation.service
After=babel-gpu-foundation.service babel-gpu-backend.service

[Service]
Type=simple
User=babel-gpu
Group=babel-gpu
WorkingDirectory=/opt/babel-gpu/repo
EnvironmentFile=/etc/babel-gpu/runtime.env
ExecStart=/usr/local/libexec/babel-gpu/run-worker
Restart=no
KillSignal=SIGTERM
TimeoutStopSec=45
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/babel-gpu

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 7: Transfer and install the staged configuration**

```bash
chmod 0600 "$BABEL_GCP_HANDOFF_ROOT/staging/runtime.env"
chmod 0750 "$BABEL_GCP_HANDOFF_ROOT/staging/run-backend" \
  "$BABEL_GCP_HANDOFF_ROOT/staging/run-worker"
gcloud compute scp --recurse --tunnel-through-iap \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
  "$BABEL_GCP_HANDOFF_ROOT/staging" "$BABEL_GCP_VM:/tmp/babel-gpu-staging"
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command='
    set -euo pipefail
    sudo install -d -m 0750 -o root -g babel-gpu /usr/local/libexec/babel-gpu
    sudo install -m 0640 -o root -g babel-gpu \
      /tmp/babel-gpu-staging/runtime.env /etc/babel-gpu/runtime.env
    sudo install -m 0750 -o root -g babel-gpu \
      /tmp/babel-gpu-staging/run-backend /usr/local/libexec/babel-gpu/run-backend
    sudo install -m 0750 -o root -g babel-gpu \
      /tmp/babel-gpu-staging/run-worker /usr/local/libexec/babel-gpu/run-worker
    for unit in foundation backend worker; do
      sudo install -m 0644 -o root -g root \
        "/tmp/babel-gpu-staging/babel-gpu-${unit}.service" \
        "/etc/systemd/system/babel-gpu-${unit}.service"
    done
  '
```

Expected: all destination paths have the stated owner and mode; `runtime.env` contains no secrets.

- [ ] **Step 8: Verify unit syntax and safety invariants before starting**

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify \
  /etc/systemd/system/babel-gpu-foundation.service \
  /etc/systemd/system/babel-gpu-backend.service \
  /etc/systemd/system/babel-gpu-worker.service
test "$(sudo systemctl show babel-gpu-backend -p Restart --value)" = 'on-failure'
test "$(sudo systemctl show babel-gpu-worker -p Restart --value)" = 'no'
sudo systemctl cat babel-gpu-foundation babel-gpu-backend babel-gpu-worker \
  > /tmp/babel-gpu-service-units.txt
```

Copy `/tmp/babel-gpu-service-units.txt` to local `remote/service-units.txt` through IAP SCP. Expected: verification reports no errors and unit receipt contains no secret values.

---

### Task 6: Prove CUDA acceptance with one and 32 vectors

**Files:**
- Create local staging: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/staging/cuda_acceptance.py`
- Create remote: `/opt/babel-gpu/cuda_acceptance.py`
- Create remote: `/var/lib/babel-gpu/evidence/hardware.json`
- Create remote: `/var/lib/babel-gpu/evidence/cuda-acceptance.json`
- Copy local: `remote/hardware.json`, `remote/cuda-acceptance.json`

**Interfaces:**
- Consumes: protected token, exact Python environment, pinned model constants.
- Produces: authoritative L4/Torch identity and finite normalized 100d receipts for batch sizes 1 and 32.

- [ ] **Step 1: Record hardware and driver identity**

On the VM:

```bash
nvidia-smi --query-gpu=timestamp,name,uuid,driver_version,memory.total \
  --format=csv,noheader,nounits | \
  jq -R 'split(", ") | {timestamp:.[0],name:.[1],uuid:.[2],driverVersion:.[3],memoryMiB:(.[4]|tonumber)}' \
  > /var/lib/babel-gpu/evidence/hardware.json
jq -e '.name == "NVIDIA L4" and .memoryMiB >= 22000' \
  /var/lib/babel-gpu/evidence/hardware.json >/dev/null
```

Expected: real L4 with approximately 24 GB VRAM.

- [ ] **Step 2: Run exact real-model acceptance and save metrics, not vectors**

Use `apply_patch` to save this exact program as `$BABEL_GCP_HANDOFF_ROOT/staging/cuda_acceptance.py`:

```python
import json
import os
import time
from pathlib import Path

import numpy as np
import torch

from babel_online.model import DistilledArtifactV1, Qwen100Encoder
from babel_online.model.distilled_artifact import (
    REAL_ARTIFACT_ID,
    REAL_ARTIFACT_REVISION,
    REAL_MODEL_REPO,
)

assert torch.cuda.is_available()
artifact = DistilledArtifactV1.load(
    repo_id=REAL_MODEL_REPO,
    revision=REAL_ARTIFACT_REVISION,
    artifact_id=REAL_ARTIFACT_ID,
    token=os.environ["HF_TOKEN"],
    cache_dir=os.environ["BABEL_ONLINE_MODEL_ARTIFACT_CACHE"],
)
artifact.assert_real_acceptance()
encoder = Qwen100Encoder.from_artifact(
    artifact,
    token=os.environ["HF_TOKEN"],
    device="cuda",
    model_cache_dir=os.environ["BABEL_ONLINE_QWEN_CACHE"],
)
texts = [
    f"Virtual memory {index}\n\nA memory-management technique that gives a process an isolated address space."
    for index in range(32)
]

def measure(values):
    torch.cuda.synchronize()
    started = time.perf_counter()
    vectors = encoder.encode(values)
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    norms = np.linalg.norm(vectors, axis=1)
    assert vectors.shape == (len(values), 100)
    assert vectors.dtype == np.float32
    assert np.isfinite(vectors).all()
    assert np.allclose(norms, 1.0, atol=1e-5)
    return {
        "count": len(values),
        "shape": list(vectors.shape),
        "dtype": str(vectors.dtype),
        "elapsedSeconds": elapsed,
        "vectorsPerSecond": len(values) / elapsed,
        "minimumNorm": float(norms.min()),
        "maximumNorm": float(norms.max()),
    }

receipt = {
    "schemaVersion": 1,
    "capturedAtUnixNs": time.time_ns(),
    "torchVersion": torch.__version__,
    "torchCudaVersion": torch.version.cuda,
    "deviceName": torch.cuda.get_device_name(0),
    "modelRepository": REAL_MODEL_REPO,
    "modelRevision": REAL_ARTIFACT_REVISION,
    "artifactId": REAL_ARTIFACT_ID,
    "single": measure(texts[:1]),
    "populationBatch": measure(texts),
    "maximumAllocatedBytes": torch.cuda.max_memory_allocated(),
    "maximumReservedBytes": torch.cuda.max_memory_reserved(),
}
Path("/var/lib/babel-gpu/evidence/cuda-acceptance.json").write_text(
    json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
)
```

Transfer and execute it exactly:

```bash
gcloud compute scp --tunnel-through-iap \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
  "$BABEL_GCP_HANDOFF_ROOT/staging/cuda_acceptance.py" \
  "$BABEL_GCP_VM:/tmp/cuda_acceptance.py"
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap --command='
    set -euo pipefail
    sudo install -m 0644 -o root -g root \
      /tmp/cuda_acceptance.py /opt/babel-gpu/cuda_acceptance.py
    sudo -u babel-gpu -H bash -c '\''
      set -a
      source /etc/babel-gpu/runtime.env
      set +a
      export HF_TOKEN="$(< /etc/babel-gpu/hf.token)"
      cd /opt/babel-gpu/repo
      exec online/.venv/bin/python /opt/babel-gpu/cuda_acceptance.py
    '\''
  '
```

Expected: exit 0; receipt contains no token or raw vectors.

- [ ] **Step 3: Validate the receipt independently**

```bash
jq -e '
  .deviceName == "NVIDIA L4" and
  .modelRevision == "57d949cd634b920cc1a46f27c9b21df094b5240e" and
  .single.shape == [1,100] and
  .populationBatch.shape == [32,100] and
  .single.dtype == "float32" and
  .populationBatch.dtype == "float32" and
  .populationBatch.vectorsPerSecond > 0 and
  .populationBatch.minimumNorm > 0.9999 and
  .populationBatch.maximumNorm < 1.0001
' /var/lib/babel-gpu/evidence/cuda-acceptance.json >/dev/null
```

Expected: exit 0. Any CUDA error, OOM, wrong shape, non-finite output, or norm failure stops the deployment before trial creation.

- [ ] **Step 4: Copy both receipts locally immediately**

Use `gcloud compute scp --tunnel-through-iap` for the two explicit files into `$BABEL_GCP_HANDOFF_ROOT/remote/`. Verify `sha256sum` locally and remotely match.

---

### Task 7: Start and verify the isolated control plane

**Files:**
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/remote/service-health.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/remote/listeners.txt`

**Interfaces:**
- Consumes: valid units and CUDA receipt.
- Produces: healthy loopback PostgreSQL, Kafka, backend, worker, and a private dashboard tunnel command.

- [ ] **Step 1: Start foundation, backend, then worker**

```bash
sudo systemctl enable --now babel-gpu-foundation
sudo systemctl enable --now babel-gpu-backend
sudo systemctl enable --now babel-gpu-worker
sudo systemctl is-active --quiet babel-gpu-foundation
sudo systemctl is-active --quiet babel-gpu-backend
sudo systemctl is-active --quiet babel-gpu-worker
```

Expected: all active. If worker startup fails, capture `journalctl -u babel-gpu-worker` and stop; do not create a trial.

- [ ] **Step 2: Verify database, Kafka, backend, and worker health**

On the VM:

```bash
cd /opt/babel-gpu/repo
sudo docker compose -p babel-gpu ps --format json > /tmp/babel-gpu-compose.json
curl --fail --silent --show-error http://127.0.0.1:8787/health > /tmp/backend-health.json
curl --fail --silent --show-error http://127.0.0.1:8792/health > /tmp/worker-health.json
sudo docker compose -p babel-gpu exec -T postgres pg_isready -U babel -d babel
sudo docker compose -p babel-gpu exec -T kafka \
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null
jq -s '{compose:.[0],backend:.[1],worker:.[2]}' \
  /tmp/babel-gpu-compose.json /tmp/backend-health.json /tmp/worker-health.json \
  > /tmp/service-health.json
```

Expected: both containers healthy; backend and worker HTTP return success.

- [ ] **Step 3: Prove listeners are loopback-only**

```bash
sudo ss -ltnp | rg ':(8787|8792|54329|29092)\b' > /tmp/listeners.txt
test "$(wc -l < /tmp/listeners.txt)" -eq 4
! rg '(^|\s)(0\.0\.0\.0|\[::\]):(8787|8792|54329|29092)' /tmp/listeners.txt
! sudo ss -ltnp | rg ':8791\b'
```

Expected: four required loopback listeners; recommendation port 8791 is absent before a condition, as designed.

- [ ] **Step 4: Verify the dashboard through IAP**

Run locally in a dedicated terminal:

```bash
gcloud compute ssh "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --tunnel-through-iap \
  --ssh-flag='-N' --ssh-flag='-L 18787:127.0.0.1:8787'
```

In another local shell:

```bash
curl --fail --silent --show-error http://127.0.0.1:18787/admin >/dev/null
```

Expected: dashboard loads through local port 18787. No public address/port URL is used.

- [ ] **Step 5: Copy health/listener receipts locally**

Copy `/tmp/service-health.json` and `/tmp/listeners.txt` through IAP SCP to `$BABEL_GCP_HANDOFF_ROOT/remote/`.

---

### Task 8: Create the formal GCP trial and prove the first durable GPU batch

**Files:**
- Create remote: `/var/lib/babel-gpu/evidence/trial-create.json`
- Create remote: `/var/lib/babel-gpu/evidence/trial-id.txt`
- Create remote: `/var/lib/babel-gpu/evidence/first-batch.json`
- Create local: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/remote/trial-id.txt`

**Interfaces:**
- Consumes: healthy backend/worker and exact immutable original model ID.
- Produces: fresh formal trial ID and end-to-end committed 32-vector smoke receipt.

- [ ] **Step 1: Capture the process nonce without printing it**

On the VM as `babel-gpu`:

```bash
admin_nonce="$(curl -fsS http://127.0.0.1:8787/admin | \
  sed -n 's/.*name="babel-admin-nonce" content="\([^"]*\)".*/\1/p' | head -n1)"
test "${#admin_nonce}" -eq 64
case "$admin_nonce" in *[!0-9a-f]*) exit 1 ;; esac
```

Expected: validation succeeds; nonce is not echoed or persisted.

- [ ] **Step 2: Submit the exact formal launch payload once**

```bash
curl --fail --silent --show-error \
  -X POST http://127.0.0.1:8787/admin/api/v1/performance \
  -H 'Host: 127.0.0.1:8787' \
  -H 'Origin: http://127.0.0.1:8787' \
  -H "X-Babel-Admin-Nonce: $admin_nonce" \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  > /var/lib/babel-gpu/evidence/trial-create.json <<'JSON'
{
  "startingModelId":"2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67",
  "topology":"same_host_split",
  "modelRepository":"dhelmy990/babel-qwen-navigation-2016-interview",
  "modelRevision":"57d949cd634b920cc1a46f27c9b21df094b5240e",
  "datasetRepository":"dhelmy990/babel-wikipedia-experiment",
  "datasetRevision":"0d1ab2c7f0e2295682288fcf10077d2d776bf559",
  "retrievalBackend":"pgvector",
  "creatorCount":50,
  "seededArticles":10000,
  "targetCreatedBabels":10000,
  "concurrentUsers":50,
  "recommendationStartProbability":0.4,
  "continuationProbability":0.4,
  "maximumTraversalDepth":2,
  "maximumRequestsPerTraversal":10,
  "trainingMicroBatchSize":8,
  "syncEverySteps":10,
  "interleaveCreationAndRecommendations":true,
  "autoAdvance":false,
  "warmupSeconds":30,
  "durationSeconds":120,
  "targetRps":5.0,
  "latencySafetyThresholdMs":5000.0
}
JSON

jq -r '.trial.experimentId' /var/lib/babel-gpu/evidence/trial-create.json \
  > /var/lib/babel-gpu/evidence/trial-id.txt
trial_id="$(cat /var/lib/babel-gpu/evidence/trial-id.txt)"
test "$(python3 -c 'import sys,uuid; print(uuid.UUID(sys.argv[1]))' "$trial_id")" = "$trial_id"
test "$trial_id" != 'ce8e54ff-e317-4a89-b7db-90327e02dc43'
```

Expected: HTTP 201, fresh UUID, status `population_pending`. Never resubmit merely because a later poll times out.

- [ ] **Step 3: Wait for the first committed population batch**

```bash
first_batch_deadline=$((SECONDS + 600))
while (( SECONDS < first_batch_deadline )); do
  curl -fsS "http://127.0.0.1:8787/admin/api/v1/performance/$trial_id" \
    > /var/lib/babel-gpu/evidence/trial-latest.json
  indexed="$(jq -r '.trial.progress.indexedBabels // 0' /var/lib/babel-gpu/evidence/trial-latest.json)"
  if (( indexed >= 32 )); then break; fi
  status="$(jq -r '.trial.status' /var/lib/babel-gpu/evidence/trial-latest.json)"
  test "$status" != 'failed'
  sleep 5
done
test "${indexed:-0}" -ge 32
```

Expected: at least 32 indexed within ten minutes and trial not failed.

- [ ] **Step 4: Bind the batch to the GCP journal and GPU telemetry**

```bash
journal="$(find "/var/lib/babel-gpu/state/performance/$trial_id" -name journal.json -type f -print -quit)"
test -n "$journal"
committed="$(jq -r '.committed_count' "$journal")"
population_run_id="$(jq -r '.identity.run_id' "$journal")"
test "$committed" -ge 32
test "$(python3 -c 'import sys,uuid; print(uuid.UUID(sys.argv[1]))' "$population_run_id")" = "$population_run_id"

jq -n --arg trialId "$trial_id" --arg populationRunId "$population_run_id" \
  --arg journal "$journal" --argjson committed "$committed" \
  --slurpfile cuda /var/lib/babel-gpu/evidence/cuda-acceptance.json \
  '{trialId:$trialId,populationRunId:$populationRunId,journal:$journal,committedCount:$committed,cuda:$cuda[0]}' \
  > /var/lib/babel-gpu/evidence/first-batch.json
```

Expected: smoke receipt binds a new trial, the journal's fresh GCP population run UUID, and the accepted L4 receipt. An absent or malformed journal run ID stops the gate; never substitute the local run ID.

- [ ] **Step 5: Persist the fresh trial identity locally**

Run locally:

```bash
gcloud compute scp --tunnel-through-iap \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
  "$BABEL_GCP_VM:/var/lib/babel-gpu/evidence/trial-id.txt" \
  "$BABEL_GCP_HANDOFF_ROOT/remote/trial-id.txt"
trial_id="$(cat "$BABEL_GCP_HANDOFF_ROOT/remote/trial-id.txt")"
test "$(python3 -c 'import sys,uuid; print(uuid.UUID(sys.argv[1]))' "$trial_id")" = "$trial_id"
```

Expected: later local tasks can recover the exact GCP trial ID without querying or reusing the local trial.

---

### Task 9: Complete and approve the 10,000-vector population gate

**Files:**
- Create remote: `/var/lib/babel-gpu/evidence/population-api.json`
- Create remote: `/var/lib/babel-gpu/evidence/population-sql.json`
- Create remote: `/var/lib/babel-gpu/evidence/population-manifest.json`
- Create remote: `/var/lib/babel-gpu/evidence/approval.json`

**Interfaces:**
- Consumes: fresh active GCP trial and first-batch smoke.
- Produces: verified `population_ready` state and one explicit formal-measurement approval.

- [ ] **Step 1: Monitor population without restarting services**

```bash
: > /var/lib/babel-gpu/evidence/population-progress.jsonl
population_deadline=$((SECONDS + 7200))
while (( SECONDS < population_deadline )); do
  curl -fsS "http://127.0.0.1:8787/admin/api/v1/performance/$trial_id" \
    > /tmp/population-poll.json
  jq -c --arg capturedAt "$(date --iso-8601=seconds)" '
    {capturedAt:$capturedAt,status:.trial.status,
     indexedBabels:(.trial.progress.indexedBabels // 0),
     recentRate:(.trial.progress.recentRate // 0),
     elapsedSeconds:(.trial.progress.elapsedSeconds // 0)}
  ' /tmp/population-poll.json >> /var/lib/babel-gpu/evidence/population-progress.jsonl
  status="$(jq -r '.trial.status' /tmp/population-poll.json)"
  case "$status" in
    population_ready) break ;;
    failed|interrupted) exit 1 ;;
    population_pending) ;;
    *) exit 1 ;;
  esac
  sudo systemctl is-active --quiet babel-gpu-worker
  sleep 15
done
test "${status:-}" = 'population_ready'
```

Expected: indexed count increases in durable batches and reaches `population_ready` within two hours. On failure, save `journalctl -u babel-gpu-worker -u babel-gpu-backend` and stop. Recovery is a separate reviewed decision: only before approval, inspect the journal, explicitly restart the worker, and invoke `babel-online performance-command --experiment-id "$trial_id" --action start` once with the protected worker token loaded. Never use that recovery after matrix approval.

- [ ] **Step 2: Save the authoritative ready response**

```bash
curl -fsS "http://127.0.0.1:8787/admin/api/v1/performance/$trial_id" \
  > /var/lib/babel-gpu/evidence/population-api.json
jq -e '
  .trial.status == "population_ready" and
  .trial.populationReady == true and
  .trial.populationEvidence.vectorCount == 10000 and
  .trial.populationEvidence.requiredVectorCount == 10000 and
  (.trial.populationEvidence.vectorSha256 | test("^[0-9a-f]{64}$")) and
  (.trial.populationEvidence.modelSha256 | test("^[0-9a-f]{64}$")) and
  (.trial.populationEvidence.datasetSha256 | test("^[0-9a-f]{64}$"))
' /var/lib/babel-gpu/evidence/population-api.json >/dev/null
```

Expected: all readiness/checksum assertions pass.

- [ ] **Step 3: Independently verify PostgreSQL counts and vector validity**

Run against the GCP container only:

```bash
sudo docker compose -p babel-gpu exec -T postgres \
  psql -U babel -d babel -v ON_ERROR_STOP=1 -v trial_id="$trial_id" -At \
  > /var/lib/babel-gpu/evidence/population-sql.json <<'SQL'
WITH trial AS (
  SELECT run_id
  FROM performance_experiments
  WHERE id = :'trial_id'
), checks AS (
  SELECT
    count(*) AS vector_count,
    count(*) FILTER (WHERE vector_dims(embedding) = 100) AS dimension_100_count,
    count(*) FILTER (WHERE embedding::text !~ 'NaN|Infinity|-Infinity') AS finite_text_count
  FROM babel_embeddings
  WHERE run_id = (SELECT run_id FROM trial)
)
SELECT json_build_object(
  'runId', (SELECT run_id FROM trial),
  'vectorCount', vector_count,
  'dimension100Count', dimension_100_count,
  'finiteTextCount', finite_text_count
)
FROM checks;
SQL

jq -e '
  (.runId | test("^[0-9a-f-]{36}$")) and
  .vectorCount == 10000 and
  .dimension100Count == 10000 and
  .finiteTextCount == 10000
' /var/lib/babel-gpu/evidence/population-sql.json >/dev/null
```

Expected: all three counts are 10,000 and run ID is a UUID.

- [ ] **Step 4: Validate the frozen population manifest and journal**

```bash
population_bundle_path="$(sudo docker compose -p babel-gpu exec -T postgres \
  psql -U babel -d babel -Atc \
  "SELECT population_bundle_path FROM performance_experiments WHERE id='$trial_id';")"
case "$population_bundle_path" in
  "/var/lib/babel-gpu/state/performance/$trial_id/"*) ;;
  *) exit 1 ;;
esac

sudo -u babel-gpu -H env \
  PYTHONPATH=/opt/babel-gpu/repo/online/src \
  /opt/babel-gpu/repo/online/.venv/bin/python - "$population_bundle_path" \
  > /var/lib/babel-gpu/evidence/population-manifest.json <<'PY'
import json
import sys
from babel_online.model.frozen_population import load_frozen_population

manifest = load_frozen_population(sys.argv[1])
print(json.dumps(manifest.model_dump(mode="json"), sort_keys=True))
PY

journal="$(find "/var/lib/babel-gpu/state/performance/$trial_id/state" \
  -path '*/population/journal.json' -type f -print -quit)"
test -n "$journal"
jq -e '
  .complete == true and .committed_count == 10000 and
  .failure_count == 0 and .unresolved_failure_count == 0 and
  (.snapshot_sha256 | test("^[0-9a-f]{64}$"))
' "$journal" >/dev/null
```

Expected: the repository's validator accepts every frozen-population file and the final journal has 10,000 committed vectors with no current or unresolved failure.

- [ ] **Step 5: Approve exactly once through the authenticated admin contract**

Capture and validate a fresh backend process nonce, without printing or persisting it:

```bash
admin_nonce="$(curl -fsS http://127.0.0.1:8787/admin | \
  sed -n 's/.*name="babel-admin-nonce" content="\([^"]*\)".*/\1/p' | head -n1)"
test "${#admin_nonce}" -eq 64
case "$admin_nonce" in *[!0-9a-f]*) exit 1 ;; esac

curl --fail --silent --show-error \
  -X POST "http://127.0.0.1:8787/admin/api/v1/performance/$trial_id/approve-next-scale" \
  -H 'Host: 127.0.0.1:8787' \
  -H 'Origin: http://127.0.0.1:8787' \
  -H "X-Babel-Admin-Nonce: $admin_nonce" \
  > /var/lib/babel-gpu/evidence/approval.json
```

Expected: HTTP 202 and trial becomes `approved` or `running`. Never send a second approval unless the first request's HTTP result is known to have failed before reaching the backend.

---

### Task 10: Run and verify the formal nine-condition matrix

**Files:**
- Create remote: `/var/lib/babel-gpu/evidence/matrix-progress.jsonl`
- Create remote: `/var/lib/babel-gpu/evidence/matrix-final-api.json`
- Create remote: `/var/lib/babel-gpu/evidence/matrix-final-sql.json`
- Create remote: `/var/lib/babel-gpu/evidence/gpu-dmon.csv`

**Interfaces:**
- Consumes: approved immutable population.
- Produces: nine completed conditions, per-condition recommendation health/evidence, and zero final Kafka lag for training conditions.

- [ ] **Step 1: Start bounded GPU telemetry capture**

```bash
sudo -u babel-gpu timeout 3600 nvidia-smi dmon -s pucvmet -d 1 -o DT \
  > /var/lib/babel-gpu/evidence/gpu-dmon.csv 2>&1 &
gpu_monitor_pid=$!
```

Expected: monitor PID exists; this process reads GPU telemetry only.

- [ ] **Step 2: Poll matrix progress without issuing mutations**

```bash
: > /var/lib/babel-gpu/evidence/matrix-progress.jsonl
matrix_deadline=$((SECONDS + 3600))
while (( SECONDS < matrix_deadline )); do
  curl -fsS "http://127.0.0.1:8787/admin/api/v1/performance/$trial_id" \
    > /tmp/matrix-poll.json
  jq -c --arg capturedAt "$(date --iso-8601=seconds)" '
    {capturedAt:$capturedAt,status:.trial.status,
     phase:(.trial.progress.phase // null),
     conditionIndex:(.trial.progress.conditionIndex // null),
     conditionCount:(.trial.progress.conditionCount // 0),
     requested:(.trial.progress.requested // 0),
     completed:(.trial.progress.completed // 0),
     elapsedSeconds:(.trial.progress.elapsedSeconds // 0),
     recentRate:(.trial.progress.recentRate // 0),
     telemetry:(.trial.progress.telemetry // {}),
     resultCount:(.trial.results | length)}
  ' /tmp/matrix-poll.json >> /var/lib/babel-gpu/evidence/matrix-progress.jsonl

  status="$(jq -r '.trial.status' /tmp/matrix-poll.json)"
  case "$status" in
    completed) break ;;
    failed|interrupted) exit 1 ;;
    approved|running) ;;
    *) exit 1 ;;
  esac
  jq -e '(.trial.progress.conditionCount // 0) <= 9 and (.trial.results | length) <= 9' \
    /tmp/matrix-poll.json >/dev/null
  sudo systemctl is-active --quiet babel-gpu-worker
  sudo systemctl is-active --quiet babel-gpu-backend
  sudo docker compose -p babel-gpu exec -T postgres pg_isready -U babel -d babel >/dev/null
  sudo docker compose -p babel-gpu exec -T kafka \
    /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null
  if sudo ss -ltn | rg -q ':8791\b'; then
    curl -fsS http://127.0.0.1:8791/health >/dev/null
  fi
  sleep 10
done
test "${status:-}" = 'completed'
```

Expected: completion within one hour, condition and result counts never exceed nine, and each live recommendation subprocess answers health checks. Do not restart worker, backend, PostgreSQL, or Kafka after the matrix begins; any assertion failure preserves evidence and ends this trial.

- [ ] **Step 3: Save and validate final API state**

```bash
curl -fsS "http://127.0.0.1:8787/admin/api/v1/performance/$trial_id" \
  > /var/lib/babel-gpu/evidence/matrix-final-api.json
jq -e '
  .trial.status == "completed" and
  .trial.operatorApproved == true and
  (.trial.results | length) == 9 and
  ([.trial.results[].conditionIndex] | sort) == [1,2,3,4,5,6,7,8,9]
' /var/lib/babel-gpu/evidence/matrix-final-api.json >/dev/null
```

Expected: complete trial and exactly nine uniquely indexed results.

- [ ] **Step 4: Verify condition/result rows in PostgreSQL**

Run and save as `matrix-final-sql.json`:

```bash
sudo docker compose -p babel-gpu exec -T postgres \
  psql -U babel -d babel -v ON_ERROR_STOP=1 -v trial_id="$trial_id" -At \
  > /var/lib/babel-gpu/evidence/matrix-final-sql.json <<'SQL'
SELECT json_build_object(
  'conditionCount', (SELECT count(*) FROM performance_conditions WHERE experiment_id = :'trial_id'),
  'completedConditionCount', (SELECT count(*) FROM performance_conditions WHERE experiment_id = :'trial_id' AND status = 'completed'),
  'resultCount', (SELECT count(*) FROM performance_results WHERE experiment_id = :'trial_id'),
  'conditionIndexes', (SELECT json_agg(condition_index ORDER BY condition_index) FROM performance_conditions WHERE experiment_id = :'trial_id')
);
SQL

jq -e '
  .conditionCount == 9 and .completedConditionCount == 9 and
  .resultCount == 9 and .conditionIndexes == [1,2,3,4,5,6,7,8,9]
' /var/lib/babel-gpu/evidence/matrix-final-sql.json >/dev/null
```

Expected: condition count 9, completed count 9, result count 9, indexes 1 through 9.

- [ ] **Step 5: Stop the telemetry process and preserve logs**

```bash
wait "$gpu_monitor_pid" || test "$?" -eq 124
sudo journalctl -u babel-gpu-worker --since '4 hours ago' --no-pager \
  > /var/lib/babel-gpu/evidence/worker.log
sudo journalctl -u babel-gpu-backend --since '4 hours ago' --no-pager \
  > /var/lib/babel-gpu/evidence/backend.log
```

Expected: telemetry and service logs are nonempty and contain no printed token.

---

### Task 11: Export, validate, and retrieve the evidence bundle

**Files:**
- Create remote: `/var/lib/babel-gpu/runs/$trial_id/export/`
- Create remote: `/var/lib/babel-gpu/runs/$trial_id/handoff/`
- Create remote: `/var/lib/babel-gpu/runs/$trial_id/accepted/`
- Create local: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/handoff/$trial_id/`

**Interfaces:**
- Consumes: completed nine-condition matrix.
- Produces: validated local accepted bundle, GPU/CPU comparison, checksums, and tunnel/cleanup instructions.

- [ ] **Step 1: Export the completed trial and selected condition 6**

On the VM with protected runtime environment and token:

```bash
run_root="/var/lib/babel-gpu/runs/$trial_id"
export_root="$run_root/export"
handoff_root="$run_root/handoff"
accepted_root="$run_root/accepted"
sudo install -d -m 0770 -o babel-gpu -g babel-gpu "$export_root" "$handoff_root" "$accepted_root"

sudo -u babel-gpu -H env \
  BABEL_DATABASE_URL=postgresql://babel:babel-local-dev@127.0.0.1:54329/babel \
  BABEL_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:29092 \
  BABEL_PERFORMANCE_STATE_ROOT=/var/lib/babel-gpu/state/performance \
  PATH=/opt/babel-gpu/repo/online/.venv/bin:/usr/local/bin:/usr/bin:/bin \
  /opt/babel-gpu/repo/online/.venv/bin/babel-online performance-export \
    --experiment-id "$trial_id" \
    --evidence-root "/var/lib/babel-gpu/state/performance/$trial_id/conditions" \
    --output-root "$export_root" \
    --selected-condition-index 6 \
    --bundle-inputs "$handoff_root/trial-bundle-inputs.json" \
    > "$handoff_root/export-receipt.json"
```

Expected: exporter validates acknowledged Kafka ranges, zero final lag, checkpoints, database edges, and selected child.

- [ ] **Step 2: Build the accepted bundle without publishing it**

```bash
sudo -u babel-gpu -H env \
  PATH=/opt/babel-gpu/repo/online/.venv/bin:/usr/local/bin:/usr/bin:/bin \
  /opt/babel-gpu/repo/online/.venv/bin/babel-friday-benchmark trial-bundle-build \
    --output-root "$accepted_root" \
    --inputs "$handoff_root/trial-bundle-inputs.json" \
    > "$handoff_root/build-receipt.json"
test -f "$accepted_root/runs/$trial_id/manifest.json"
test -f "$accepted_root/runs/$trial_id/checksums.json"
```

Expected: bundle validation succeeds. Do not run `trial-bundle-publish`.

- [ ] **Step 3: Create a concise GPU experiment summary**

Run locally to recover the trial identity and copy the infrastructure receipt:

```bash
trial_id="$(cat "$BABEL_GCP_HANDOFF_ROOT/remote/trial-id.txt")"
gcloud compute scp --tunnel-through-iap \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
  "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm.json" \
  "$BABEL_GCP_VM:/tmp/babel-gpu-vm.json"
```

Then, in the IAP SSH shell on the VM, recover `trial_id`, set the run paths, and build the summary only from durable receipts:

```bash
trial_id="$(cat /var/lib/babel-gpu/evidence/trial-id.txt)"
run_root="/var/lib/babel-gpu/runs/$trial_id"
handoff_root="$run_root/handoff"

population_journal="$(find "/var/lib/babel-gpu/state/performance/$trial_id/state" \
  -path '*/population/journal.json' -type f -print -quit)"
test -n "$population_journal"
running_hours="$(python3 - /tmp/babel-gpu-vm.json <<'PY'
import datetime as dt
import json
import sys

created = dt.datetime.fromisoformat(
    json.load(open(sys.argv[1], encoding="utf-8"))["creationTimestamp"].replace("Z", "+00:00")
)
print((dt.datetime.now(dt.timezone.utc) - created).total_seconds() / 3600)
PY
)"

jq -n \
  --arg capturedAt "$(date --iso-8601=seconds)" \
  --arg trialId "$trial_id" \
  --arg project "$BABEL_GCP_PROJECT" --arg zone "$BABEL_GCP_ZONE" \
  --arg vm "$BABEL_GCP_VM" --arg image "$BABEL_GCP_IMAGE" \
  --arg sourceSha "$BABEL_GCP_SOURCE_SHA" \
  --arg tunnel "gcloud compute ssh $BABEL_GCP_VM --project=$BABEL_GCP_PROJECT --zone=$BABEL_GCP_ZONE --tunnel-through-iap --ssh-flag=-N --ssh-flag=-L=18787:127.0.0.1:8787" \
  --argjson runningHours "$running_hours" \
  --slurpfile hardware /var/lib/babel-gpu/evidence/hardware.json \
  --slurpfile cuda /var/lib/babel-gpu/evidence/cuda-acceptance.json \
  --slurpfile population /var/lib/babel-gpu/evidence/population-api.json \
  --slurpfile journal "$population_journal" \
  --slurpfile matrix /var/lib/babel-gpu/evidence/matrix-final-api.json \
  --slurpfile exportReceipt "$handoff_root/export-receipt.json" \
  --slurpfile buildReceipt "$handoff_root/build-receipt.json" \
  --slurpfile vmReceipt /tmp/babel-gpu-vm.json '
  {
    schemaVersion:1,capturedAt:$capturedAt,trialId:$trialId,sourceSha:$sourceSha,
    pins:{
      modelRepository:"dhelmy990/babel-qwen-navigation-2016-interview",
      modelRevision:"57d949cd634b920cc1a46f27c9b21df094b5240e",
      artifactId:"3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
      baseModel:"Qwen/Qwen3-Embedding-0.6B",
      baseModelRevision:"97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
      datasetRepository:"dhelmy990/babel-wikipedia-experiment",
      datasetRevision:"0d1ab2c7f0e2295682288fcf10077d2d776bf559"
    },
    gcp:{project:$project,zone:$zone,vm:$vm,image:$image,machine:"g2-standard-8",
         creationTimestamp:$vmReceipt[0].creationTimestamp},
    hardware:$hardware[0],
    cuda:{torchVersion:$cuda[0].torchVersion,torchCudaVersion:$cuda[0].torchCudaVersion,
          single:$cuda[0].single,populationBatch:$cuda[0].populationBatch},
    population:{vectorCount:$population[0].trial.populationEvidence.vectorCount,
                vectorSha256:$population[0].trial.populationEvidence.vectorSha256,
                elapsedSeconds:$journal[0].duration_seconds,
                vectorsPerSecond:$journal[0].rows_per_second},
    matrix:{conditionsCompleted:($matrix[0].trial.results|length),
            latencyRatios:[$matrix[0].trial.results[] |
              {conditionIndex,Itraining,Ifull,IActivationIncrement}]},
    finalValidation:{exportReceipt:$exportReceipt[0],buildReceipt:$buildReceipt[0]},
    trainerPlacement:"CPU NumPy",dashboardTunnelCommand:$tunnel,
    cost:{hourlyComputeUsd:0.853624312,runningHours:$runningHours,
          estimatedComputeUsd:($runningHours*0.853624312)},
    publication:{performed:false}
  }
  ' > "$handoff_root/summary.json"

jq -e '
  (.trialId | test("^[0-9a-f-]{36}$")) and
  (.sourceSha | test("^[0-9a-f]{40}$")) and
  .hardware.name == "NVIDIA L4" and
  .cuda.single.shape == [1,100] and .cuda.populationBatch.shape == [32,100] and
  .population.vectorCount == 10000 and
  (.population.vectorSha256 | test("^[0-9a-f]{64}$")) and
  .population.elapsedSeconds > 0 and .population.vectorsPerSecond > 0 and
  .matrix.conditionsCompleted == 9 and
  .trainerPlacement == "CPU NumPy" and .publication.performed == false and
  .cost.runningHours > 0 and .cost.estimatedComputeUsd < 10
' "$handoff_root/summary.json" >/dev/null
```

Expected: every identity, count, checksum, timing, placement, and non-publication assertion passes.

- [ ] **Step 4: Compare local CPU and GCP GPU population throughput**

Run locally; this performs only one local SQL read and one remote receipt copy:

```bash
trial_id="$(cat "$BABEL_GCP_HANDOFF_ROOT/remote/trial-id.txt")"
docker exec babel-postgres-1 psql -U babel -d babel -At \
  > "$BABEL_GCP_HANDOFF_ROOT/local-cpu-progress.json" <<'SQL'
SELECT json_build_object(
  'trialId', 'ce8e54ff-e317-4a89-b7db-90327e02dc43',
  'device', 'CPU',
  'committedVectorCount', indexed_babels,
  'elapsedSeconds', elapsed_seconds,
  'vectorsPerSecond', recent_rate
)
FROM performance_progress_snapshots
WHERE experiment_id = 'ce8e54ff-e317-4a89-b7db-90327e02dc43'
  AND phase = 'population'
ORDER BY sequence DESC LIMIT 1;
SQL

gcloud compute scp --tunnel-through-iap \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
  "$BABEL_GCP_VM:/var/lib/babel-gpu/runs/$trial_id/handoff/summary.json" \
  "$BABEL_GCP_HANDOFF_ROOT/remote/gpu-summary.json"
jq -n --slurpfile cpu "$BABEL_GCP_HANDOFF_ROOT/local-cpu-progress.json" \
  --slurpfile gpu "$BABEL_GCP_HANDOFF_ROOT/remote/gpu-summary.json" '
  {comparisonScope:"same pinned model, dataset, and target size; independent fresh trial identities",
   cpu:$cpu[0],
   gpu:{trialId:$gpu[0].trialId,device:"NVIDIA L4",
        committedVectorCount:$gpu[0].population.vectorCount,
        elapsedSeconds:$gpu[0].population.elapsedSeconds,
        vectorsPerSecond:$gpu[0].population.vectorsPerSecond},
   gpuToCpuThroughputRatio:($gpu[0].population.vectorsPerSecond/$cpu[0].vectorsPerSecond)}
  ' > "$BABEL_GCP_HANDOFF_ROOT/remote/cpu-gpu-comparison.json"
jq -e '
  .cpu.committedVectorCount > 0 and .cpu.elapsedSeconds > 0 and .cpu.vectorsPerSecond > 0 and
  .gpu.committedVectorCount == 10000 and .gpu.elapsedSeconds > 0 and
  .gpu.vectorsPerSecond > 0 and .gpuToCpuThroughputRatio > 0
' "$BABEL_GCP_HANDOFF_ROOT/remote/cpu-gpu-comparison.json" >/dev/null
gcloud compute scp --tunnel-through-iap \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
  "$BABEL_GCP_HANDOFF_ROOT/remote/cpu-gpu-comparison.json" \
  "$BABEL_GCP_VM:/var/lib/babel-gpu/runs/$trial_id/handoff/cpu-gpu-comparison.json"
```

Expected: both measurements are positive and the comparison explicitly discloses independent trial identities.

- [ ] **Step 5: Create checksums and retrieve the complete handoff**

In the IAP SSH shell on the VM:

```bash
trial_id="$(cat /var/lib/babel-gpu/evidence/trial-id.txt)"
cd "/var/lib/babel-gpu/runs/$trial_id"
find export handoff accepted -type f -print0 | sort -z | xargs -0 sha256sum \
  > handoff/retrieval-sha256.txt
```

Copy the exact roots and validate from the same relative root:

```bash
trial_id="$(cat "$BABEL_GCP_HANDOFF_ROOT/remote/trial-id.txt")"
local_trial_root="$BABEL_GCP_HANDOFF_ROOT/handoff/$trial_id"
install -d -m 0700 "$local_trial_root"
for remote_name in evidence export handoff accepted; do
  case "$remote_name" in
    evidence) remote_path="/var/lib/babel-gpu/evidence" ;;
    *) remote_path="/var/lib/babel-gpu/runs/$trial_id/$remote_name" ;;
  esac
  gcloud compute scp --recurse --tunnel-through-iap \
    --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" \
    "$BABEL_GCP_VM:$remote_path" "$local_trial_root/"
done
(
  cd "$local_trial_root"
  sha256sum -c handoff/retrieval-sha256.txt
)
```

Expected: every checksum passes before stopping the VM.

---

### Task 12: Stop compute and prove local isolation

**Files:**
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/local-baseline-after.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/infrastructure/vm-stop.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/handoff/cost.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/handoff/final-audit.json`
- Create: `/home/dhelmy990/Data/babel-data/gcp-gpu-20260827/handoff/cleanup-commands.md`

**Interfaces:**
- Consumes: locally verified handoff bundle.
- Produces: stopped GPU compute, final cost/isolation audit, and recoverable cleanup instructions.

- [ ] **Step 1: Capture final remote resource state before stop**

```bash
gcloud compute instances describe "$BABEL_GCP_VM" --project="$BABEL_GCP_PROJECT" \
  --zone="$BABEL_GCP_ZONE" --format=json \
  > "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm-before-stop.json"
```

Expected: VM still RUNNING and labels/identity match Task 3.

- [ ] **Step 2: Stop the exact VM and verify TERMINATED**

```bash
test "$BABEL_GCP_VM" = 'babel-gpu-20260827'
gcloud compute instances stop "$BABEL_GCP_VM" \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" --quiet
test "$(gcloud compute instances describe "$BABEL_GCP_VM" \
  --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" --format='value(status)')" = 'TERMINATED'
jq -n --arg capturedAt "$(date --iso-8601=seconds)" \
  --arg status "$(gcloud compute instances describe "$BABEL_GCP_VM" \
    --project="$BABEL_GCP_PROJECT" --zone="$BABEL_GCP_ZONE" --format='value(status)')" \
  --arg project "$BABEL_GCP_PROJECT" --arg zone "$BABEL_GCP_ZONE" --arg vm "$BABEL_GCP_VM" \
  '{capturedAt:$capturedAt,status:$status,project:$project,zone:$zone,vm:$vm}' \
  > "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm-stop.json"
```

Expected: GPU compute charges stop; disk remains recoverable.

- [ ] **Step 3: Capture the local experiment after-state**

```bash
local_pid="$(pgrep -f '/slices-1-2/online/.venv/bin/babel-online performance-worker' | head -n1 || true)"
local_cwd=''
local_command=''
if test -n "$local_pid"; then
  local_cwd="$(readlink -f "/proc/$local_pid/cwd")"
  local_command="$(tr '\0' ' ' < "/proc/$local_pid/cmdline")"
  test "$local_cwd" = '/home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2'
fi

docker exec babel-postgres-1 psql -U babel -d babel -At -F $'\t' -c \
  "SELECT id,status,population_ready,COALESCE(population_vector_count,0),COALESCE(failure,'')
     FROM performance_experiments
    WHERE id='ce8e54ff-e317-4a89-b7db-90327e02dc43';" \
  > "$BABEL_GCP_HANDOFF_ROOT/local-trial-after.tsv"

jq -n --arg capturedAt "$(date --iso-8601=seconds)" \
  --arg pid "$local_pid" --arg cwd "$local_cwd" --arg command "$local_command" \
  --arg trial "$(cat "$BABEL_GCP_HANDOFF_ROOT/local-trial-after.tsv")" \
  '{capturedAt:$capturedAt,pid:(if $pid=="" then null else ($pid|tonumber) end),
    cwd:$cwd,command:$command,trialRow:$trial}' \
  > "$BABEL_GCP_HANDOFF_ROOT/local-baseline-after.json"

before_pid="$(jq -r '.pid' "$BABEL_GCP_HANDOFF_ROOT/local-baseline-before.json")"
after_status="$(cut -f2 "$BABEL_GCP_HANDOFF_ROOT/local-trial-after.tsv")"
after_count="$(cut -f4 "$BABEL_GCP_HANDOFF_ROOT/local-trial-after.tsv")"
before_count="$(cut -f4 "$BABEL_GCP_HANDOFF_ROOT/local-trial-before.tsv")"
test "$after_status" != 'failed'
test "$after_count" -ge "$before_count"
if test -n "$local_pid"; then
  test "$local_pid" = "$before_pid"
else
  test "$after_status" = 'completed'
fi
```

Expected: the original PID and worktree remain when the CPU job is still active, or the durable local trial naturally completed; its population count never regressed and it did not fail.

- [ ] **Step 4: Calculate the bounded cost estimate**

```bash
python3 - \
  "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm-before-stop.json" \
  "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm-stop.json" \
  > "$BABEL_GCP_HANDOFF_ROOT/handoff/cost.json" <<'PY'
import datetime as dt
import json
import sys

vm = json.load(open(sys.argv[1], encoding="utf-8"))
stop = json.load(open(sys.argv[2], encoding="utf-8"))
created = dt.datetime.fromisoformat(vm["creationTimestamp"].replace("Z", "+00:00"))
stopped = dt.datetime.fromisoformat(stop["capturedAt"].replace("Z", "+00:00"))
hours = max(0.0, (stopped - created).total_seconds() / 3600)
hourly = 0.853624312
disk_allowance = 0.10
compute = hours * hourly
document = {
    "schemaVersion": 1,
    "creationTimestamp": vm["creationTimestamp"],
    "stopTimestamp": stop["capturedAt"],
    "runningHours": hours,
    "hourlyComputeUsd": hourly,
    "estimatedComputeUsd": compute,
    "conservativeDiskAllowanceUsd": disk_allowance,
    "estimatedTotalUsd": compute + disk_allowance,
    "billingCaveat": "Billing export can lag and is authoritative when available.",
}
print(json.dumps(document, sort_keys=True, indent=2))
PY
jq -e '.runningHours > 0 and .estimatedTotalUsd < 10' \
  "$BABEL_GCP_HANDOFF_ROOT/handoff/cost.json" >/dev/null
```

Expected: bounded estimate remains below USD 10; the receipt explicitly notes that delayed billing export is authoritative.

- [ ] **Step 5: Write final requirement-by-requirement audit**

Recover the trial path, assert the key evidence again, and write the nine approved acceptance items:

```bash
trial_id="$(cat "$BABEL_GCP_HANDOFF_ROOT/remote/trial-id.txt")"
local_trial_root="$BABEL_GCP_HANDOFF_ROOT/handoff/$trial_id"
jq -e '.deviceName == "NVIDIA L4" and .populationBatch.shape == [32,100]' \
  "$local_trial_root/evidence/cuda-acceptance.json" >/dev/null
jq -e '.trial.populationEvidence.vectorCount == 10000' \
  "$local_trial_root/evidence/population-api.json" >/dev/null
jq -e '.trial.status == "completed" and (.trial.results|length) == 9 and
       ([.trial.results[].servingP95Ms] | all(. > 0))' \
  "$local_trial_root/evidence/matrix-final-api.json" >/dev/null
jq -e '.publication.performed == false and .trainerPlacement == "CPU NumPy"' \
  "$local_trial_root/handoff/summary.json" >/dev/null
jq -e '.status == "TERMINATED"' \
  "$BABEL_GCP_HANDOFF_ROOT/infrastructure/vm-stop.json" >/dev/null

jq -n --arg root "$local_trial_root" --arg handoff "$BABEL_GCP_HANDOFF_ROOT" '
  {schemaVersion:1,items:[
    {id:1,status:"proven",claim:"GCP GPU and CUDA Qwen inference",
     evidence:($root+"/evidence/cuda-acceptance.json")},
    {id:2,status:"proven",claim:"Exact source, model, artifact, base, and dataset pins",
     evidence:($root+"/handoff/summary.json")},
    {id:3,status:"proven",claim:"Independent 10000-vector population complete",
     evidence:($root+"/evidence/population-api.json")},
    {id:4,status:"proven",claim:"Nine conditions and final Kafka/export evidence complete",
     evidence:[($root+"/evidence/matrix-final-api.json"),($root+"/handoff/export-receipt.json")]},
    {id:5,status:"proven",claim:"GPU-backed recommendation latency recorded",
     evidence:($root+"/evidence/matrix-final-api.json")},
    {id:6,status:"proven",claim:"Dashboard reachable only through IAP tunnel",
     evidence:[($handoff+"/remote/listeners.txt"),($handoff+"/infrastructure/firewall.json")]},
    {id:7,status:"proven",claim:"Local experiment remained isolated",
     evidence:[($handoff+"/local-baseline-before.json"),($handoff+"/local-baseline-after.json")]},
    {id:8,status:"proven",claim:"Cost below USD 10 and VM stopped",
     evidence:[($handoff+"/handoff/cost.json"),($handoff+"/infrastructure/vm-stop.json")]},
    {id:9,status:"proven",claim:"GPU encoder/serving and CPU NumPy trainer disclosed",
     evidence:($root+"/handoff/summary.json")}
  ]}
  ' > "$BABEL_GCP_HANDOFF_ROOT/handoff/final-audit.json"
jq -e '(.items|length)==9 and ([.items[].status] | all(.=="proven"))' \
  "$BABEL_GCP_HANDOFF_ROOT/handoff/final-audit.json" >/dev/null
```

Expected: all nine items are proven by explicit receipt paths. If any preceding assertion fails, write that item as `contradicted` or `missing` and do not claim the deployment goal complete.

- [ ] **Step 6: Write exact but unexecuted cleanup commands**

Use `apply_patch` to create `cleanup-commands.md` with this exact, unexecuted content:

````markdown
# GCP GPU cleanup (requires separate user approval)

```bash
gcloud compute instances describe babel-gpu-20260827 --project=chloe-tutoring-bot --zone="$BABEL_GCP_ZONE"
gcloud compute firewall-rules describe babel-gpu-iap-ssh-20260827 --project=chloe-tutoring-bot
gcloud compute networks subnets describe babel-gpu-sg-20260827 --project=chloe-tutoring-bot --region=asia-southeast1
gcloud compute networks describe babel-gpu-net-20260827 --project=chloe-tutoring-bot

gcloud compute instances update babel-gpu-20260827 --project=chloe-tutoring-bot --zone="$BABEL_GCP_ZONE" --no-deletion-protection
gcloud compute instances delete babel-gpu-20260827 --project=chloe-tutoring-bot --zone="$BABEL_GCP_ZONE" --quiet
gcloud compute firewall-rules delete babel-gpu-iap-ssh-20260827 --project=chloe-tutoring-bot --quiet
gcloud compute networks subnets delete babel-gpu-sg-20260827 --project=chloe-tutoring-bot --region=asia-southeast1 --quiet
gcloud compute networks delete babel-gpu-net-20260827 --project=chloe-tutoring-bot --quiet
```

Quota lowering is intentionally omitted until separately approved. The stopped boot disk continues to incur storage charges until the VM is deleted.
````

Expected: documentation exists, but none of its destructive commands has run.

---

## Plan Self-Review Checklist

- Every design goal maps to Tasks 2-12.
- Local isolation is captured before cloud mutation and after VM stop.
- Quota, capacity, source, dependency, CUDA, service, population, matrix, export, retrieval, cost, and stop gates have explicit evidence.
- Recommendation health is checked per condition, not before its subprocess exists.
- Worker automatic restart is disabled, matching the non-resumable matrix contract.
- The one-vector smoke cannot substitute for the mandatory 32-vector batch or full trial.
- No task executes artifact publication or cloud deletion; cleanup commands are documentation only and require separate approval.
- Token reads occur only inside protected launch/acceptance environments; no command prints a token or passes one as an argument.
- Application source is exact and unmodified; operational evidence remains outside Git.
