# Qwen training-to-serving adapter

This adapter consumes the trained interview artifact without copying the Qwen
base model into Babel's model repository. It downloads the base from its exact
upstream revision, attaches the trained LoRA tensors, loads the 1024-to-100
projection, and emits finite L2-normalized float32 vectors.

## Pinned identities

- Private artifact repository: `dhelmy990/babel-qwen-navigation-2016-interview`
- Artifact repository commit: `57d949cd634b920cc1a46f27c9b21df094b5240e`
- Artifact ID: `3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8`
- Base/tokenizer: `Qwen/Qwen3-Embedding-0.6B` at `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`
- Dataset: `dhelmy990/babel-wikipedia-experiment`, configuration
  `distillation_2016_interview`, commit
  `b440e98b04ab77afed7caf0455eca3189235fc3b`
- Training source commit: `92f3ac697d78eb827d75b033df92dcbed887def7`

The upstream `artifact_manifest.json` does **not** contain input formatting,
pooling, padding, or normalization fields. `DistilledServingArtifactV1` is the
explicit immutable serving binding for that gap. It derives these semantics
from the artifact's pinned training source commit:

- `training/src/babel_training/collator.py`: `canonical_title + "\n\n" + lead_text`,
  left padding, truncation at 384 tokens.
- `training/src/babel_training/pooling.py`: final non-padding token.
- `training/src/babel_training/model.py`: biased `Linear(1024, 100)` then L2
  normalization.

This is a source-bound derivation, not a claim that the upstream manifest
already recorded those fields. The binding also carries the adapter,
projection, and validation-report SHA-256 values from the immutable artifact.

## Install and verify the artifact contract

Install the online package in its environment, place `HF_TOKEN` in the process
environment through a secret manager, then run:

```bash
cd online
uv sync --extra dev --extra qwen
PYTHONPATH=src python -m pytest \
  tests/model/test_distilled_artifact.py \
  tests/model/test_qwen_encoder.py -v
```

The standard suite skips private-Hub acceptance if `HF_TOKEN` is absent. With
the token set, the real-model test resolves the exact private commit, requires
the exact seven-file artifact directory, and verifies every declared payload
hash. The token is never passed to the browser, printed, or stored in a model
manifest.

## Construct the encoder

```python
import os
from babel_online.model import DistilledArtifactV1, Qwen100Encoder
from babel_online.model.distilled_artifact import (
    REAL_ARTIFACT_ID,
    REAL_ARTIFACT_REVISION,
    REAL_MODEL_REPO,
)

token = os.environ["HF_TOKEN"]
artifact = DistilledArtifactV1.load(
    repo_id=REAL_MODEL_REPO,
    revision=REAL_ARTIFACT_REVISION,
    artifact_id=REAL_ARTIFACT_ID,
    token=token,
)
artifact.assert_real_acceptance()
encoder = Qwen100Encoder.from_artifact(
    artifact,
    token=token,
    device="cuda",  # use "cpu" when CUDA is unavailable
)
vectors = encoder.encode(["Virtual memory\n\nA memory-management technique."])
assert vectors.shape == (1, 100)
```

`Qwen100Encoder.from_artifact` rejects local fixtures by default. A test may
set `require_real_acceptance=False` only when injecting fixture components.

Task 5 establishes the artifact and encoder contract. It does not claim that
the recommendation endpoint uses Qwen yet; Task 6 replaces the deterministic
item/query path and measures real inference.
