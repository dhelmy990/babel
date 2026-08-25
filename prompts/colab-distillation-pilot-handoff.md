# Colab Distillation Pilot Handoff Prompt

Paste the message below to the pilot operator only after replacing every exact
placeholder. Do not send it with an unresolved placeholder and do not include a
Hugging Face token.

---

Please run the Babel 2016 distillation pilot in Google Colab using the attached
`train_distillation_colab.ipynb` and
`docs/runbooks/colab-distillation-pilot.md`.

Frozen identities:

```text
SOURCE_COMMIT_SHA=<SOURCE_COMMIT_SHA_40_HEX>
DATASET_REPO_ID=dhelmy990/babel-wikipedia-experiment
DATASET_CONFIG=distillation_2016
DATASET_REVISION_SHA=<DATASET_REVISION_SHA_40_HEX>
DATASET_MANIFEST_SHA256=<DATASET_MANIFEST_SHA256_64_HEX>
DATASET_READINESS_SHA256=<DATASET_READINESS_SHA256_64_HEX>
MODEL_ID=Qwen/Qwen3-Embedding-0.6B
MODEL_REVISION_SHA=97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
TOKENIZER_REVISION_SHA=97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

Before sending this prompt, replace these strings in the notebook too:

```text
<SOURCE_COMMIT_SHA_40_HEX>
<DATASET_REVISION_SHA_40_HEX>
<DATASET_MANIFEST_SHA256_64_HEX>
<DATASET_READINESS_SHA256_64_HEX>
```

Use **Runtime > Change runtime type > T4 GPU**. Add a read-only private-Hub
credential under the exact Colab Secret name `HF_TOKEN`, enable notebook
access, and never paste or print its value. Keep the demo defaults: max length
512, batch size 2, gradient accumulation 8, fp16 on T4, 20 optimizer steps,
45-minute runtime budget, and a complete checkpoint every 5 optimizer steps.

Run all ordered cells. The expected milestones are:

1. the dataset resolves to exactly `DATASET_REVISION_SHA`;
2. the row preview exposes only permitted preview fields;
3. `ONE-BATCH GATE PASS` appears with finite loss and only allowed gradients;
4. training stops normally at its step/runtime boundary;
5. exact held-out validation writes Recall@10/50, NDCG@10/50, mean paired
   cosine, invalid-vector count, norm statistics, and deterministic examples;
6. the complete checkpoint saves and reloads with an equal validation
   fingerprint;
7. the reloaded trainer advances by one optimizer step; and
8. the distilled artifact exports.

If any milestone fails, stop at that cell and follow the runbook. Do not change
revisions, bypass readiness, resume a `.partial` checkpoint, increase T4 batch
size, or upload anything to Hugging Face.

Report back only non-secret evidence: the three frozen revision SHAs, two
dataset checksums, GPU/precision, one-batch and final losses, final step,
validation metrics plus invalid count, checkpoint path, reload fingerprint
result, resumed step, artifact ID/path, and the exception type for any failure.

---

Handoff owner preflight: verify both 40-character placeholders and both
64-character placeholders use lowercase hexadecimal and refer to one coherent,
locally verified release before sending this prompt.
