# Complete 2016 Qwen Distillation Training Handoff

Do not launch this handoff while any frozen identity below says `PENDING` or
while dataset readiness is not remotely verified as `complete`. The data
release agent must not execute these training commands; they are for a
separate, user-launched training agent after Gate A.

## Frozen identities

```text
SOURCE_COMMIT_SHA=PENDING_IMPLEMENTATION_COMMIT
DATASET_REPO_ID=dhelmy990/babel-wikipedia-experiment
DATASET_CONFIG=distillation_2016
DATASET_REVISION_SHA=PENDING_FINAL_DATASET_COMMIT
DATASET_MANIFEST_SHA256=PENDING_FINAL_DATASET_MANIFEST
DATASET_READINESS_SHA256=PENDING_FINAL_DATASET_READINESS
DATASET_ACTIVE_RELEASE_ROOT=PENDING_FINAL_ACTIVE_RELEASE_ROOT
DATASET_SUPERSEDES_COMMIT_SHA=PENDING_FINAL_PREDECESSOR
DATASET_PUBLICATION_COMMITS=PENDING_FINAL_PUBLICATION_COMMITS
INDEX_SOURCE_COMMIT_SHA=240a8d906c4faeafb60b877190976941148d1747
INDEX_SOURCE_SHA256=d669ce29a96cfd306fbbb06debf4660819f6b5c35c4a0c006d2102433a616d41
TEACHER_SOURCE_COMMIT_SHA=ee01785fc4cf3d7f25c90917f41e3962f93e9370
TEACHER_SOURCE_SHA256=5508a20088e0c5a2af4128f9aa80c675230c43b4538d42f89fb79ec324caaf56
WIKIPEDIA_SOURCE_COMMIT_SHA=PENDING_REQUIRED_MIRROR
WIKIPEDIA_SOURCE_SHA256=PENDING_REQUIRED_MIRROR
MODEL_REPO_ID=dhelmy990/babel-qwen-navigation-2016
MODEL_ID=Qwen/Qwen3-Embedding-0.6B
MODEL_REVISION_SHA=97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
TOKENIZER_REVISION_SHA=97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

## Colab setup

Create a fresh Colab runtime, choose a T4 GPU, mount Google Drive, and add a
read/write Hugging Face token as the Colab Secret named exactly `HF_TOKEN`.
Enable notebook access. Never paste it into a cell, command, log, checkpoint,
report, prompt, or commit. Do not print the environment or enable shell tracing.

Run these setup commands after replacing no identity above:

```bash
!git clone https://github.com/dhelmy990/babel.git /content/babel
%cd /content/babel
!git checkout --detach PENDING_IMPLEMENTATION_COMMIT
!python -m pip install --require-hashes -r training/requirements-colab.lock
!python -m pip install faiss-cpu==1.12.0 hnswlib==0.8.0
```

Open `training/notebooks/train_distillation_colab.ipynb` from that checkout.
Set its frozen identity cell to the dataset/model values above. The dataset
resolver must return exactly `DATASET_REVISION_SHA`, and locally downloaded
manifest/readiness bytes must hash to their two frozen SHA-256 values before a
row is streamed. Require manifest and readiness to agree on
`DATASET_ACTIVE_RELEASE_ROOT` and `DATASET_SUPERSEDES_COMMIT_SHA`. Confirm every
configured `data_files` glob is beneath that active root; historical pilot
Parquet paths may exist in repository history but must not be selected,
counted, or streamed.

## Complete-run settings

Keep these exact settings:

```text
max_length=512
per_device_batch_size=2
gradient_accumulation_steps=8
effective_batch_size=16
mixed_precision=fp16 on T4
learning_rate=2e-4
lambda_rel=0.5
max_grad_norm=1.0
lora_rank=16
lora_alpha=32
lora_dropout=0.05
lora_targets=q_proj,v_proj
teacher_dimension=100
student_dimension=100
pooling=last_non_padding_token
student_output=L2-normalized float32
max_steps=ceil(complete train rows / 16), exactly one epoch
checkpoint_interval=250 optimizer steps
checkpoint_root=/content/drive/MyDrive/babel-distillation-full
```

Run the notebook through its one-batch gate first. Require a real Qwen
forward, real backward, finite total/vector/relational losses, finite gradient
norm, and gradients only on all expected LoRA tensors plus the 1024-to-100
projection. Then save and report the first complete checkpoint before
continuing the full epoch.

## Checkpoint and resume

Only directories whose atomic checkpoint manifest is complete may be resumed.
Never rename or accept a `.partial` directory. After a disconnect, rebuild the
same pinned model/dataset, load the latest complete Drive checkpoint, verify
its model revision, dataset commit, configuration, optimizer/scheduler,
accelerator, RNG, loader, epoch, and global-step identities, then continue to
the same absolute `max_steps`. A resumed run must not repeat or skip training
rows.

## Frozen validation

Persist the deterministic article-key selection and its checksum before
training. Use `FullValidationPlanV1.for_corpus(complete_rows)`:

- monitoring: 2,000 deterministic queries against 50,000 candidates, clamped
  to available rows;
- final queries: deterministic 5%, clamped to 10,000..50,000 and available;
- final candidates: `min(100000, complete_rows)`, the entire corpus when
  smaller;
- exact oracle: L2-normalized contiguous float32
  `faiss.IndexFlatIP`;
- metrics: Recall@10/50, NDCG@10/50, paired cosine, invalid/NaN count, norm
  statistics, and structured examples; and
- build the serving HNSW index over the full corpus, then audit it against
  2,000 exact-oracle queries (clamped only when fewer rows exist).

Monitoring may reuse only the persisted monitoring subset. Final artifact
acceptance must use the persisted final subset. Save the validation selection,
report, and SHA-256 checksums.

## Final publication

Export only LoRA adapter tensors, the float32 1024-to-100 projection,
configuration, and the closed validation report. Pin dataset/model/source
identities and all file checksums in the artifact manifest. Publish append-only
to `dhelmy990/babel-qwen-navigation-2016`, resolve the returned 40-character
commit, download that exact revision, verify every checksum, reload it, and
confirm the final validation fingerprint within the documented numeric
tolerance. Never upload a base-model tensor or token.

## Required report back

Return only non-secret evidence:

1. all frozen identities above and the validation-selection checksum;
2. GPU name, fp16, exact settings, train-row count, and absolute target steps;
3. first real forward/backward losses and finite gradient norm;
4. first complete checkpoint path, step, manifest checksum, and successful
   reload/resume evidence;
5. finite loss range and final optimizer step;
6. full exact metrics, invalid/NaN count, norm statistics, examples, and full
   HNSW audit metrics;
7. final validation report SHA-256;
8. final model commit SHA and artifact-manifest SHA-256; and
9. exact-revision remote download/reload checksum and fingerprint proof.

Stop and report the exception type if any identity, readiness, checksum,
finite-loss, checkpoint, exact-validation, HNSW-audit, or publication gate
fails. Do not relax a gate or switch to a floating revision.
