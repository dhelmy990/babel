# Interview 50k Training Handoff

Use the private Hugging Face dataset exactly as pinned below. Do not substitute
`main`, a moving branch, `distillation_2016`, or a locally regenerated sample.

## Immutable dataset identity

- Repository: `dhelmy990/babel-wikipedia-experiment`
- Configuration: `distillation_2016_interview`
- Revision: `b440e98b04ab77afed7caf0455eca3189235fc3b`
- Example schema: `distillation-example-v1`
- Selection seed: `babel-interview-2016-v1`
- Train: 50,000 rows
- Validation: 5,000 fixed exact rows
- Test: 5,000 untouched rows
- Smoke: first 1,000 rows of the ordered train selection

The revision is recorded with mode `0600` at
`/home/dhelmy990/Data/babel-data/receipts/interview-2016-corrected-revision.txt`.
Authenticated streaming validation at that exact revision produced one valid,
finite 100-dimensional row from each split:

- train: `enwiki:2016-10-01:25058509`
- validation: `enwiki:2016-10-01:11583811`
- test: `enwiki:2016-10-01:2138624`

Each split verification ran in a separate short process and exited 0. This
avoids a PyArrow 17/Python 3.12 native garbage-collection teardown abort seen
only after validating several remote streams in one process; validation itself
completed before that teardown.

Revision `3eb87a0a8cf4a1feb73e3a326fb7d619048f69f1` and its preserved
`interview-2016-revision.txt` receipt are superseded because their manifest
recorded the nonexistent producer SHA
`436f171649c0d8d917e0d29216673b798c883a54`. Do not train from that revision.
The correction commit changes only the interview manifest and readiness
provenance metadata; all Parquet bytes, counts, frontier evidence, selection
identities and checksums, the dataset card, the complete configuration
manifest, and prior Hub history remain unchanged.

## Frozen partial frontier disclosure

This is a deterministic sample of an incomplete extraction frontier, not a
sample chosen after the complete corpus was known.

- Frozen UTC: `2026-08-26T12:24:39.924126Z`
- Committed selected-text rows: 1,310,000
- Maximum page ID: 25,850,280
- Maximum selected-text journal row: 1,310,000
- Database identity SHA-256:
  `8bb8fe046ec962aa8bf4b58ee99ed7cc8eee3d1a50723181df3e400e6a3f9c85`
- Exporter code commit: `436f1714a4034a11544bbc16cf94072bb56feff0`
- Teacher source revision: `ee01785fc4cf3d7f25c90917f41e3962f93e9370`
- Teacher SHA-256:
  `5508a20088e0c5a2af4128f9aa80c675230c43b4538d42f89fb79ec324caaf56`
- Wikipedia source revision: `d949e81abe9fd4e1daf930bfe5990f9914c74b2e`
- Wikipedia SHA-256:
  `dbe52efb14e85049fcb0b88970b413f6e85972a76fd19b224514368b9b0e3df6`

The complete extractor remained active and advanced to at least 1,320,000
selected-text rows immediately after this export.

## Ordered selection and artifact checksums

Ordered identity SHA-256:

- train: `518c30f10859a88681c3708ab0236bd104fdde96acff09089515d871d9600a1e`
- validation: `64cd7c82c58d73947f24b8120ef3c2e5c3a4a8f145bf0a7a6522175bcd1b2cd6`
- test: `d2cd61ee895c2f6386c708d7884666b4aa579174674e8bf70e876ee891956bf5`

Parquet SHA-256:

- train: `11a217879913305a88b0bfaafffa39f132883d2b6f27252a02054ba95ea6b2c5`
- validation: `a925eb795f253635f3a80a76994a7139a0f81f4e784beb61dfc93f8b662dc8f0`
- test: `103f22b38b048973f8ab6ba52efca41f667f37c305b5dfea8b752dc492d7ac03`
- config manifest:
  `33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09`

## Training protocol

Read `HF_TOKEN` from Colab Secrets; never paste it into notebook source, output,
checkpoints, or logs. Load with `streaming=True`, the exact configuration name,
and the exact revision above.

1. Run the first 1,000 ordered train rows as a smoke test.
2. Use Qwen with max sequence length 384.
3. Train exactly one epoch over all fixed 50,000 train rows.
4. Evaluate against the fixed exact 5,000-row validation set.
5. Do not inspect or tune against the 5,000-row test set until final evaluation.
6. Save resumable checkpoints frequently. Persist optimizer, scheduler, scaler,
   RNG, epoch, and ordered-row progress so a Colab interruption resumes without
   repeating or skipping examples.
7. Record the pinned dataset revision and ordered checksums in every run record.

This handoff does not authorize launching training from the export lane.
