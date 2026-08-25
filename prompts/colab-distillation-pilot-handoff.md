# Colab Distillation Pilot Handoff Prompt

The message below is ready to use with the remotely verified Friday pilot. Do
not include a Hugging Face token when sharing results.

---

Please run the Babel 2016 distillation pilot in Google Colab using the attached
`train_distillation_colab.ipynb` and
`docs/runbooks/colab-distillation-pilot.md`.

Frozen identities:

```text
SOURCE_COMMIT_SHA=92f3ac697d78eb827d75b033df92dcbed887def7
DATASET_REPO_ID=dhelmy990/babel-wikipedia-experiment
DATASET_CONFIG=distillation_2016
DATASET_REVISION_SHA=c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b
DATASET_MANIFEST_SHA256=6d99276635ec76f58c945dc3b2eb32273f113a4c9163dc926b9a6fc18300ff6a
DATASET_READINESS_SHA256=763b40f911c34a0479efdcbe2851f6c5151656d25f2e23d167c4d4560ef9acc2
MODEL_ID=Qwen/Qwen3-Embedding-0.6B
MODEL_REVISION_SHA=97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
TOKENIZER_REVISION_SHA=97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

The source commit is publicly reachable from
`https://github.com/dhelmy990/babel.git` through branch
`codex/colab-pilot`. An unauthenticated clean clone and exact commit checkout
were verified before this handoff; no GitHub credential is required in Colab.

Use **Runtime > Change runtime type > T4 GPU**. Add a read-only private-Hub
credential under the exact Colab Secret name `HF_TOKEN`, enable notebook
access, and never paste or print its value. Keep the demo defaults: max length
512, batch size 2, gradient accumulation 8, fp16 on T4, 20 optimizer steps,
45-minute runtime budget, and a complete checkpoint every 5 optimizer steps.
The pinned training package and lock support Colab Python 3.13 and select
NumPy 2.2.6 there.

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

Local trusted-path acceptance already passed against these identities on a
CPU-only host: the actual Qwen model produced a finite loss, exactly 112 LoRA
tensors plus projection weight/bias received gradients, an atomic checkpoint
reloaded in a fresh Python process with an identical 100d fingerprint, and the
trainer resumed one step. The Colab run remains the GPU handoff and should
report its own evidence using the checklist above.
