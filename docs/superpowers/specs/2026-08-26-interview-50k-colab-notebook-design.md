# Interview 50k Colab Notebook Design

## Goal

Create a standalone Colab notebook for the pinned 2016 interview dataset. Use
`training/notebooks/train_distillation_colab.ipynb` as the implementation
reference while treating `prompts/interview-50k-training-handoff.md` as the
authority for dataset identity and protocol.

The new notebook will be saved as
`training/notebooks/train_interview_50k_colab.ipynb`. The reference pilot
notebook remains unchanged.

## Immutable inputs

- Source commit: `92f3ac697d78eb827d75b033df92dcbed887def7`
- Dataset repository: `dhelmy990/babel-wikipedia-experiment`
- Dataset configuration: `distillation_2016_interview`
- Dataset revision: `b440e98b04ab77afed7caf0455eca3189235fc3b`
- Qwen base revision: `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`
- Maximum sequence length: 384
- Ordered train/validation identities and artifact checksums: exactly those in
  the authoritative interview handoff

The notebook must not use a moving revision, substitute `distillation_2016`,
or open test examples.

## Notebook flow

1. Validate Colab and install the hash-locked environment from the pinned
   Babel source commit.
2. Read `HF_TOKEN` from Colab Secrets and mount Google Drive.
3. define a complete, explicit training configuration and output locations.
4. Resolve and assert the exact Qwen and dataset revisions.
5. Verify the remote configuration manifest, counts, ordered identities, and
   Parquet checksums without iterating the test split.
6. Preview train and validation examples and build an ordered streaming
   dataloader with restartable row progress.
7. Run the existing one-batch numerical/gradient gate.
8. Run a short first-1,000-row smoke path.
9. Reinitialize model, optimizer, scheduler, scaler, and RNG state before the
   production epoch so smoke updates cannot contaminate the full run.
10. Train exactly one epoch over the ordered 50,000-row train split.
11. Evaluate the fixed 5,000-row validation split and record invalid-vector
    counts.
12. Save, reload, and verify the final restartable checkpoint, including a
    one-step resume check that does not alter the exported final checkpoint.
13. Export LoRA weights, the 100-dimensional projection, complete training
    configuration, validation report, and an immutable truthful
    `distillation_2016_interview` artifact manifest.
14. Commit the artifact snapshot to a private Hugging Face model repository.

## Quick-test safety mode

The notebook defaults to `QUICK_TEST_MODE = True`. In this mode the training
loop performs one optimizer step, saves a restartable checkpoint, and exits the
loop. The notebook prints a prominent quick-test banner and must not label this
run as a completed epoch.

The exact 50,000-example production epoch is enabled only by explicitly setting
`QUICK_TEST_MODE = False`. The production path asserts the expected ordered row
count before it may be labeled complete.

## Checkpoint semantics

Checkpoints are written frequently to Google Drive and contain model/LoRA and
projection state, optimizer, scheduler, mixed-precision scaler, Python/NumPy/
Torch RNG state, epoch, global step, and next ordered row offset. Resume must
continue without repeating or skipping examples.

Smoke, quick-test, production-final, and resume-verification outputs use
separate directories so one cannot overwrite another.

## Validation and publishing

Validation uses only the fixed 5,000 validation rows. Reports include the
existing retrieval metrics and explicit invalid-vector counts. Test remains
unopened and is not part of the notebook's automatic run.

The final manifest records exact model and dataset revisions, configuration,
ordered checksums, artifact hashes, checkpoint identity, validation results,
invalid-vector counts, and the private model-repository commit. Publishing must
fail closed if the repository is not private or any required identity is
missing.

## Verification

Local verification must prove that the notebook:

- parses as valid notebook JSON and has Python-compilable code cells;
- retains the reference notebook's environment, gate, validation, checkpoint,
  reload, resume, and export stages;
- contains every immutable pin and the interview configuration;
- contains no active pilot/full-2016 configuration or moving revision;
- defaults to the one-step quick-test break;
- keeps production training at exactly one ordered 50,000-row epoch; and
- never loads or iterates test examples.

Colab execution is intentionally left to the user.
