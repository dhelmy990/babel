# Real 2016 pilot release

The private Hugging Face dataset `dhelmy990/babel-wikipedia-experiment`,
configuration `distillation_2016`, is `pilot_ready` at the immutable commit
`c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`.

## Source identity and bounded acquisition

No Hugging Face dataset with exact English Wikipedia `2016-10-01` snapshot
identity was available. The official Wikimedia Hugging Face dataset was a 2026
snapshot, so the pilot used the repository's approved immutable 2016 sources:

- September teacher ZIP: 727,429,988 bytes, MD5
  `ac70acfc41aff7a23cc9439e3bb1771f`, SHA-256
  `5508a20088e0c5a2af4128f9aa80c675230c43b4538d42f89fb79ec324caaf56`.
- October multistream index: 185,177,516 bytes, MD5
  `7c9486cde3f9c43ff4e23443dd2323f3`, SHA-1
  `f13aebe90c8bea2157d826659e0320157a1978d9`.
- October XML dump identity: 14,178,624,372 bytes, MD5
  `5df8e610829c336138dcb9191071b283`, SHA-1
  `86ba305ecc41dafcf03ba3e67c2eacb95724d5ca`.

The full XML was not downloaded. The deterministic selector intersected the
first 250,000 emitted teacher rows with the complete multistream index, removed
2,507 contracted test candidates, grouped candidates by shared stream offset,
and acquired 13 strict HTTP byte ranges totaling 9,682,825 compressed bytes.
Every request required HTTP 206 and an exact `Content-Range`. The range ledger
is `/home/dhelmy990/Data/babel-data/work/2016-pilot/selection-plan.json`, SHA-256
`84e5f82bc2961c93be1f6ffa4fb16397ce12e2bb666ca6c6069090e6eb965e4e`.

The production parser and reconciler processed 160 real teacher/snapshot
candidates: 159 matched and one was excluded for `empty_lead`. The final
accepted JSONL contains exactly 80 rows (64 train, 16 validation, 0 test),
SHA-256 `72709f50989269e63694eaf9a66429d413dce6595c9ac93b02088c43a1171e4b`.
The remaining valid candidates were deterministic acquisition headroom, not
published rows.

## Uploaded bundle

| Path | Rows | SHA-256 |
| --- | ---: | --- |
| `distillation_2016/train/part-00000.parquet` | 64 | `1379d2f3aa3873f9fddd08d1d6d3d4182e5d0417bdbd5f427cf06f8cb6888ae1` |
| `distillation_2016/validation/part-00000.parquet` | 16 | `82eefaed7b4a7fea4980c79062e25e7efd221263a425a12f439773ee20af2680` |
| `distillation_2016/test/empty.parquet` | 0 | `d0d2b3d7e44785dbed1ce376b9c045bf7ed2bb7fcf38d8f1ee4a97c5d16647ba` |

The zero-row test sentinel has the exact `distillation-example-v1` physical
schema. It lets `datasets==3.3.2` infer the fixed card's test glob without
making a test example accessible. Manifest SHA-256 is
`6d99276635ec76f58c945dc3b2eb32273f113a4c9163dc926b9a6fc18300ff6a`;
uploaded readiness SHA-256 is
`763b40f911c34a0479efdcbe2851f6c5151656d25f2e23d167c4d4560ef9acc2`.

Publication was append-only. Commit
`99a61038ba1f687fc69c8b0247c155ba93c4d41c` added the initial release bytes;
the final commit `c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b` added only the zero-row sentinel.

## Reproduction and verification commands

Source `HF_TOKEN` from `/home/dhelmy990/Code/babel/.env` without echoing it,
then run from this repository worktree:

```bash
PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli prepare-2016 \
  --input /home/dhelmy990/Data/babel-data/work/2016-pilot/accepted.jsonl \
  --provenance /home/dhelmy990/Data/babel-data/work/2016-pilot/provenance.json \
  --output-root /home/dhelmy990/Data/babel-data/prepared/2016-pilot-v2 \
  --pilot-size 80 --target-shard-bytes 268435456

PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli publish-2016 \
  --repo dhelmy990/babel-wikipedia-experiment \
  --input-root /home/dhelmy990/Data/babel-data/prepared/2016-pilot-v2 \
  --state pilot_ready \
  --revision-out /home/dhelmy990/Data/babel-data/prepared/2016-pilot-v2/revision.txt

PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli verify-remote \
  --repo dhelmy990/babel-wikipedia-experiment \
  --revision c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b \
  --input-root /home/dhelmy990/Data/babel-data/prepared/2016-pilot-v2
```

The pinned verifier reported one valid remote example from each nonempty split.
`babel_training` then loaded separate 8-row train and validation batches at the
same SHA; both had `[8, 100]` finite teacher tensors and split-pure metadata.
