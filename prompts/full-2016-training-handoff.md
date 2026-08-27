# Complete 2016 Qwen Distillation Training Handoff

Do not launch this handoff while any frozen identity below says `PENDING` or
while dataset readiness is not remotely verified as `complete`. The data
release agent must not execute these training commands; they are for a
separate, user-launched training agent after Gate A.

## Frozen identities

```text
SOURCE_COMMIT_SHA=ed8d0973cdf4e23f2b20c98258ec6743d30d906c
DATASET_REPO_ID=dhelmy990/babel-wikipedia-experiment
DATASET_CONFIG=distillation_2016
DATASET_REVISION_SHA=aaab5069c84a99837b562cb4b80ccc8ee2a9b786
DATASET_RELEASE_COMMIT_SHA=aaab5069c84a99837b562cb4b80ccc8ee2a9b786
DATASET_MANIFEST_SHA256=9db6d6afe5749c0a0c18ae69beed0921fc4dcb86aa482939600208fe076ceb27
DATASET_READINESS_SHA256=e981720ebaffb8ce462db99131b09139d6c5093d0e7c088d7fe368dbb27ab13d
DATASET_README_SHA256=bc93bc590e812440f56e376dbc3d982a20d26507e167564778411f1d0f8b4662
DATASET_ACTIVE_RELEASE_ROOT=distillation_2016/releases/07e7ab55ce18a3ec8d534c142b47dc472705805e20c73e7488f62ab037ae8f54
DATASET_SUPERSEDES_COMMIT_SHA=a2f9d812a6ffd47664794bcf3dbdbe3cef7753bb
DATASET_PUBLICATION_JOURNAL_SHA256=ebb4919111c85faaa5c6e3d05aa93d5e8716eb3a7339170b04fb036dc0b43c17
DATASET_PUBLICATION_COMMITS=10d0b4dcd20d38073ab8c1563e4a133dfdb674e6,fe1bacaca4272c4576f9d92812164d22af1beb9a,516802c16473890001cd181cbeb5058234bbf19b,ed83311068e11b80f9022d806f75e09c5a29380c,9c7c1360d460d83134e3979229d9f0d597e8cb9d,fc3bd4c626e75baf263fa444c2ca33886fc749f9,22a55f27dc22a960b315744b984a063d6893a0f8,82a7d92ac1a2b8ad2e8c40b683d921e3c88ac8d9,e29b816d53a0ccb07886f4a5cbad8c80c0ee914a,5ac7af2e63fd1bac71b1cf2155d4b0272499ad82,daaf727764e7825bc2f7a0384e96868712f36b98,357b36ef767d8414f18146cb1f498ca4bf90db6b,2449035f57172caff363fa22d8ce7fb39f33a33e,b2f5c37775d736030a94653f704fe6d911595755,a6115ba8dc47732d8318deb2b9da0d0800c3768a,e0617b11ad0dc16f609b80b5d811d1e9a98207f3,dc4ffdcf27f988de6328bbfd43fe87e8f74a3541,51bf72c84c8e5052433b7431fd0244ab95805014,88ff58fbe93fdb18a5b5f61343fc83ea1b409c51,6d7f7c7ae8a88de0fd56ca5e91b264eb5fc6eaf6,b81515db1b321e0be5a29aa0c49296bf6a93a7ec,14b14b3a2119f45fe6cc40e7369ee25885351345,43fc7c5267219c1a62e1007db2fa908717138b7d,8ce1cc9ec329f09fbbf994cc7bfa4a4f211f9f72,721b6f36a712fffd77381e09fcfb83b48a84297b,f60173ad2e5cf84832739255073dc02b6172a44b,9fbb0e0bbd1d12a51efc3500f658eca48f3b0d7d,df39a7d73598be58e4f3da9bc3949f99ccbfe53c,adbb5032e4d76843d178c636cc6fd8dce771b816,254c4703aa989dd03fe960d02bd65f44706b8bbc,d401de7f075297ef55395959930072a2840bc26c,c5b8495209e06251db2e9d096bd65fe995a32fa8,dfdb92d924ee6d9870a1618db8f9ac1fc7befb35,0e99497eacb561a7e991f3830dffc6ef48353b5a,28a7288d554edeb953b9c8405cd6aaf7cde79dff,c60549fd50de046d3add9c5872279447561335e4,72ee0b625342a8b2125900e18b57424b9840fdb2,3a1eb5ebc0275fee179432aa748fa9e4bbbb9f39,a2f9d812a6ffd47664794bcf3dbdbe3cef7753bb,aaab5069c84a99837b562cb4b80ccc8ee2a9b786
INTERVIEW_DATASET_CONFIG=distillation_2016_interview
INTERVIEW_DATASET_REVISION_SHA=b440e98b04ab77afed7caf0455eca3189235fc3b
INTERVIEW_MANIFEST_PATH=distillation_2016_interview/manifest.json
INTERVIEW_MANIFEST_SHA256=33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09
INTERVIEW_READINESS_PATH=distillation_2016_interview/readiness.json
INTERVIEW_READINESS_SHA256=e0ce2f29a30760d807340c1ca901e271de9403a28621cf5ba93b8c8dfa4b6650
INTERVIEW_SELECTION_SEED=babel-interview-2016-v1
INTERVIEW_TRAIN_IDS_SHA256=518c30f10859a88681c3708ab0236bd104fdde96acff09089515d871d9600a1e
INTERVIEW_SMOKE_IDS_SHA256=1dd2560197d901c7050827301bbfbb9a570b1776d6ca34f2d492883230772c64
INTERVIEW_VALIDATION_IDS_SHA256=64cd7c82c58d73947f24b8120ef3c2e5c3a4a8f145bf0a7a6522175bcd1b2cd6
INTERVIEW_TEST_IDS_SHA256=d2cd61ee895c2f6386c708d7884666b4aa579174674e8bf70e876ee891956bf5
INDEX_SOURCE_COMMIT_SHA=240a8d906c4faeafb60b877190976941148d1747
INDEX_SOURCE_SHA256=d669ce29a96cfd306fbbb06debf4660819f6b5c35c4a0c006d2102433a616d41
TEACHER_SOURCE_COMMIT_SHA=ee01785fc4cf3d7f25c90917f41e3962f93e9370
TEACHER_SOURCE_SHA256=5508a20088e0c5a2af4128f9aa80c675230c43b4538d42f89fb79ec324caaf56
WIKIPEDIA_SOURCE_COMMIT_SHA=d949e81abe9fd4e1daf930bfe5990f9914c74b2e
WIKIPEDIA_SOURCE_SHA256=dbe52efb14e85049fcb0b88970b413f6e85972a76fd19b224514368b9b0e3df6
MODEL_REPO_ID=dhelmy990/babel-qwen-navigation-2016-interview
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
!git checkout --detach ed8d0973cdf4e23f2b20c98258ec6743d30d906c
!python -m pip install --require-hashes -r training/requirements-colab.lock
!python -m pip install faiss-cpu==1.12.0
```

Open `training/notebooks/train_interview_50k_colab.ipynb` from that checkout.
Before running it, resolve the complete repository to exactly
`DATASET_REVISION_SHA`; download its manifest, readiness, and README and verify
their three frozen SHA-256 values. Require manifest and readiness to agree on
`DATASET_ACTIVE_RELEASE_ROOT` and `DATASET_SUPERSEDES_COMMIT_SHA`, and confirm
every `distillation_2016` data-files glob is beneath that active root.
Historical pilot Parquet paths may exist in repository history but must not be
selected, counted, or streamed. Then use the notebook's separately frozen
interview identity cell. Download `INTERVIEW_MANIFEST_PATH` and
`INTERVIEW_READINESS_PATH` from exactly `INTERVIEW_DATASET_REVISION_SHA`,
verify their frozen SHA-256 values and the ordered-ID checksums in the
manifest, and load only `INTERVIEW_DATASET_CONFIG`. The smoke population is
the first 1,000 ordered train rows; verify its checksum while streaming that
prefix. The manifest explicitly records that the sample came from a frozen
incomplete 1,310,000-row extraction frontier. Never recalculate a different
subset or substitute the complete-corpus configuration for this fixed run.

## Complete-run settings

Keep these exact settings:

```text
max_length=384
per_device_batch_size=2
gradient_accumulation_steps=8 (smoke uses 4)
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
smoke_rows=1,000 (the exact prefix of selected train IDs)
train_rows=50,000
validation_rows=5,000
test_rows=5,000 (untouched until the 50k checkpoint is complete)
epochs=1
max_steps=ceil(50,000 / 16)=3,125 optimizer steps
checkpoint_interval=100 optimizer steps
checkpoint_root=/content/drive/MyDrive/babel-distillation/interview-50k/{UTC_RUN_ID}
```

Run the notebook through the fixed 1,000-row smoke prefix first. Its first
batch must perform a real Qwen forward and backward with finite
total/vector/relational losses, finite gradient norm, and gradients only on all
expected LoRA tensors plus the 1024-to-100 projection. The smoke artifact does
not pass the training gate. Restart from the unchanged base identity for the
one-epoch ordered 50,000-row run, then save and report the first normal
complete checkpoint before continuing to step 3,125.

## Checkpoint and resume

Only directories whose atomic checkpoint manifest is complete may be resumed.
Never rename or accept a `.partial` directory. After a disconnect, rebuild the
same pinned model/dataset, load the latest complete Drive checkpoint, verify
its model revision, dataset commit, configuration, optimizer/scheduler,
accelerator, RNG, loader, epoch, and global-step identities, then continue to
the same absolute `max_steps`. A resumed run must not repeat or skip training
rows.

## Frozen interview selection and validation

Use `InterviewTrainingPlanV1()` and the remotely verified selection document.
The release's train/validation/test assignments are preserved. Within each
split the stored order is:

- `SHA-256("babel-interview-2016-v1" + NUL + article_key)` ascending, with
  `article_key` as the tie-breaker;
- first 50,000 train IDs, whose first 1,000 are the smoke prefix;
- first 5,000 validation IDs; and
- first 5,000 test IDs, untouched until the 50k checkpoint is complete.

Run final validation as exactly the 5,000 held-out validation queries against
exactly those 5,000 candidates:

- exact oracle: L2-normalized contiguous float32
  `faiss.IndexFlatIP`;
- metrics: Recall@10/50, NDCG@10/50, paired cosine, invalid/NaN count, norm
  statistics, and structured examples.

Do not add a 100,000/200,000-example expansion or a full-corpus HNSW/ANN gate;
those are post-interview experiments. Only after the complete 50k checkpoint
is durable may the untouched 5,000 test identities be opened. Save the
selection document, exact-validation report, any post-checkpoint test report,
and their SHA-256 checksums.

## Final publication

Export only LoRA adapter tensors, the float32 1024-to-100 projection,
configuration, and the closed validation report. Pin dataset/model/source
identities and all file checksums in the artifact manifest. Publish append-only
to `dhelmy990/babel-qwen-navigation-2016-interview`, resolve the returned
40-character commit, download that exact revision, verify every checksum,
reload it, and confirm the final validation fingerprint within the documented
numeric tolerance. Never upload a base-model tensor or token.

## Required report back

Return only non-secret evidence:

1. all frozen identities above and the validation-selection checksum;
2. interview revision, manifest/readiness checksums, ordered
   train/smoke/validation/test ID checksums, GPU name, fp16, exact settings,
   50,000 train rows, and 3,125 target steps;
3. first real smoke forward/backward losses and finite gradient norm;
4. first normal 50k-run checkpoint path, step, manifest checksum, and successful
   reload/resume evidence;
5. finite loss range and final optimizer step;
6. exact 5k validation metrics, invalid/NaN count, norm statistics, examples,
   and evidence the 5k test set stayed unopened until the final checkpoint;
7. final validation report SHA-256;
8. final model commit SHA and artifact-manifest SHA-256; and
9. exact-revision remote download/reload checksum and fingerprint proof.

Stop and report the exception type if any identity, readiness, checksum,
finite-loss, checkpoint, exact-validation, or publication gate fails. Do not
relax a gate or switch to a floating revision.
