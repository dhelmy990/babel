# Colab Distillation Pilot Runbook

This is the trusted Friday-demo path for the private 2016 pilot shard. It runs
the checked-in notebook without requiring repository knowledge. Do not change
the pinned revisions in the notebook unless the handoff owner supplies all new
checksums together.

## Before opening Colab

You need:

- access to the private Hugging Face dataset
  `dhelmy990/babel-wikipedia-experiment`;
- a read-only Hugging Face token (dataset read permission only); and
- enough Google Drive space for complete checkpoints (recommended: 10 GB).

The checked-in notebook already pins source commit
`9d4c0c5e6a191a6a33e5319b21dc7a389d06b6c3`, dataset commit
`c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`, manifest SHA-256
`6d99276635ec76f58c945dc3b2eb32273f113a4c9163dc926b9a6fc18300ff6a`,
and readiness SHA-256
`763b40f911c34a0479efdcbe2851f6c5151656d25f2e23d167c4d4560ef9acc2`.
Do not substitute a branch name or floating revision.

## Launch: exact clicks

1. Open <https://colab.research.google.com/>.
2. Click **File > Upload notebook**, then select
   `training/notebooks/train_distillation_colab.ipynb` from the handoff bundle.
3. Click **Runtime > Change runtime type**.
4. Choose **T4 GPU** under **Hardware accelerator**, then click **Save**.
5. In the left sidebar, click the **key** icon (**Secrets**).
6. Click **Add new secret**. Set **Name** to exactly `HF_TOKEN`, paste the
   read-only token into **Value**, enable **Notebook access**, and never place
   the token in a code cell or output.
7. Click **Runtime > Run all**. Approve Google Drive access when prompted if
   `USE_DRIVE = True`.

The install cell checks out the exact source commit and uses the hash-locked
Colab requirements. A successful GPU cell prints `Tesla T4` and `fp16`. The
token value is never printed.

## Demo configuration

Use these T4-safe values in the `configuration` cell:

| Setting | Debug/demo value |
| --- | ---: |
| `max_length` | 512 |
| `per_device_batch_size` | 2 |
| `gradient_accumulation_steps` | 8 |
| `max_steps` | 20 |
| `max_runtime_minutes` | 45 |
| checkpoint interval | 5 optimizer steps |
| mixed precision on T4 | `fp16` |

Leave the model at `Qwen/Qwen3-Embedding-0.6B` and its frozen revision. Leave
the dataset config at `distillation_2016`. The resolve cell must print the same
SHA as `dataset_ref`; a branch name such as `pilot_ready` is not accepted as a
training identity.

For a two-minute smoke check, set `max_steps = 1` and
`max_runtime_minutes = 5`. Do not call that a completed pilot.

## Expected checkpoints and output

Before training, the one-batch cell must print `ONE-BATCH GATE PASS` with a
finite loss. It also verifies that gradients are limited to LoRA and projection
parameters. Stop and report a failure if this line does not appear.

With Drive enabled, durable output is under:

```text
/content/drive/MyDrive/babel-distillation/
  checkpoint-step-NNNNNNNN/
  validation-report.json
  distilled-artifact/
```

The training cell normally stops after `max_steps` or after the runtime budget
is checked immediately following an optimizer step. Complete periodic
checkpoints are written atomically. A `.partial` checkpoint is incomplete and
must not be resumed.

## Normal stop and save

The safest normal stop is to let the training cell return at its step or
runtime limit, then run the `validate`, `save-checkpoint`, and
`reload-checkpoint` cells. Confirm these messages before closing the tab:

```text
Saved complete checkpoint: .../checkpoint-step-NNNNNNNN
Reload equivalence PASS
```

If you must interrupt, wait for a periodic checkpoint message, then click the
square **Stop** button beside the running cell. Colab can terminate a runtime
without warning; an in-progress optimizer step cannot be promised durable.
Never move or rename a `.partial` directory into the checkpoint path.

## Resume after a disconnect

1. Reopen the notebook and select **Runtime > Change runtime type > T4 GPU**.
2. Confirm `HF_TOKEN` still has **Notebook access** in **Secrets**.
3. Run cells from `environment-check` through `model-construction` in order.
4. Skip the fresh `train` cell.
5. Run `reload-checkpoint`. It rejects a checkpoint whose model revision,
   dataset SHA, or schema version differs from the current configuration.
6. Set the desired absolute target step in `resume-checkpoint`, then run it.
   Success prints `Resume PASS` with a step greater than the saved step.
7. Run `validate`, `save-checkpoint`, `reload-checkpoint`, and `export` again.

## Error diagnosis

| Symptom | Meaning and action |
| --- | --- |
| `Open this notebook in Google Colab` | Upload it to Colab; local execution is not the supported pilot path. |
| No CUDA / CPU runtime | Select **Runtime > Change runtime type > T4 GPU**, save, and rerun from the top. |
| Secret missing | Create exactly `HF_TOKEN`, enable **Notebook access**, then rerun only the secret cell. |
| 401 or 403 from Hugging Face | Use a read-only token whose account can read the private dataset. Do not paste it into output. |
| Pinned SHA mismatch | Stop. Ask the handoff owner for one coherent dataset SHA, manifest checksum, and readiness checksum. |
| Dataset contract/readiness error | Stop. Do not bypass the gate or switch to an unpinned ref. Report the exception type and pinned SHA. |
| CUDA out of memory | Restart the runtime, keep T4 `fp16`, batch size 2, and length 512. Close other notebooks. Do not increase batch size. |
| Non-finite loss or gradient gate failure | Stop before training. Report the source/dataset/model revisions and the one-batch output. |
| Only a `.partial` checkpoint exists | It is not restartable. Resume from the last complete checkpoint or restart the smoke run. |
| Checkpoint identity mismatch | Use the exact source, model, and dataset revisions that created it; never edit its manifest. |
| Reload fingerprint differs | Stop before resume/export and report both fingerprints and GPU type. |

## Report-back checklist

Copy no tokens or private row text. Send only:

- source commit SHA;
- dataset commit SHA, manifest SHA-256, and readiness SHA-256;
- model and tokenizer revision;
- GPU name and mixed precision;
- one-batch loss and final step/loss;
- `validation-report.json` metrics and invalid-vector count;
- complete checkpoint path and reload/resume result; and
- exported artifact ID/path.

The Friday demo is accepted when the one-batch gate passes, a complete
checkpoint reloads with the same validation fingerprint, one additional step
resumes, and the structured validation report/export are produced.

## Pre-handoff acceptance evidence

Before this Colab handoff, the trusted path was exercised locally against the
same private dataset SHA using the actual pinned Qwen model. The CPU-only smoke
used batch size 1 and length 8. All 112 LoRA tensors plus projection weight and
bias—and no frozen base tensors—received gradients. Manual loss was
`0.9819909`; optimizer-step loss was `0.9663436`. A 28 MB complete checkpoint
reloaded in a fresh Python process with a 100-dimensional validation
fingerprint at `max_abs=0.0`, then resumed from step 1 to step 2 with finite
loss `0.9487882`. This evidence validates the package path; the Colab run
validates the intended T4 settings and longer sequences.
