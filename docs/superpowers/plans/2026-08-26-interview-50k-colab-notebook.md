# Interview 50k Colab Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a complete, standalone Colab notebook for the pinned 50,000-row interview training run, with a one-step quick-test default.

**Architecture:** Copy the proven cell-level workflow from `train_distillation_colab.ipynb` into a new notebook and adapt only the data identity, max length, run control, checkpoint cadence, validation scale, manifest, and publishing behavior. Keep orchestration in notebook cells and reuse `babel_training` package APIs instead of duplicating model, loss, loader, validation, or checkpoint implementations.

**Tech Stack:** Jupyter notebook JSON, Python 3.10–3.13, PyTorch, Transformers, PEFT, Hugging Face Datasets/Hub, Google Colab Secrets and Drive, pytest.

## Global Constraints

- Create `training/notebooks/train_interview_50k_colab.ipynb`; do not modify the reference pilot notebook.
- Pin Babel source `92f3ac697d78eb827d75b033df92dcbed887def7`.
- Pin Qwen revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
- Pin dataset `dhelmy990/babel-wikipedia-experiment`, config `distillation_2016_interview`, revision `b440e98b04ab77afed7caf0455eca3189235fc3b`.
- Use `streaming=True`, max length 384, the first ordered 1,000 train rows for smoke, exactly one ordered 50,000-row production epoch, and the fixed 5,000-row validation split.
- Never load, iterate, preview, validate, or tune against test examples.
- Default `QUICK_TEST_MODE = True`; run one optimizer step, save a restartable checkpoint, and break without claiming epoch completion.
- Require an explicit `QUICK_TEST_MODE = False` for production.
- Read `HF_TOKEN` only from Colab Secrets and never print or persist it.
- Preserve optimizer, scheduler, scaler, RNG, epoch, global step, and next ordered row offset in checkpoints.
- Export LoRA, 100-dimensional projection, full configuration, validation report, invalid-vector counts, immutable hashes, final checkpoint identity, and private model-repository commit.

---

### Task 1: Add the interview notebook contract

**Files:**
- Modify: `training/tests/test_notebook_contract.py`
- Test: `training/tests/test_notebook_contract.py`

**Interfaces:**
- Consumes: notebook JSON and the existing `_source` helper.
- Produces: structural and semantic assertions for `train_interview_50k_colab.ipynb`.

- [ ] **Step 1: Write failing contract tests**

Add `INTERVIEW_NOTEBOOK = ROOT / "training/notebooks/train_interview_50k_colab.ipynb"` and an `_interview_notebook()` loader. Add focused tests that assert:

```python
def test_interview_notebook_is_valid_python_notebook() -> None:
    notebook = _interview_notebook()
    assert notebook["nbformat"] == 4
    assert notebook["cells"]
    assert all(cell["cell_type"] == "code" for cell in notebook["cells"])
    for cell in notebook["cells"]:
        source = _source(cell)
        python_source = "\n".join(
            line for line in source.splitlines() if not line.startswith("!")
        )
        ast.parse(python_source)
```

Add a pins/protocol test which joins all cell sources and asserts all five exact source/model/dataset identifiers, `max_length=384`, `streaming=True`, counts 1,000/50,000/5,000, and the handoff manifest and ordered train/validation SHA-256 strings. Assert active source does not assign `distillation_2016` or a moving dataset ref.

Add a quick-test test which finds the tagged run-control and train cells and asserts `QUICK_TEST_MODE = True`, a one-step limit, a loop `break`, a separate quick-test checkpoint directory, an explicit false-mode production branch, and an assertion that production rows equal 50,000 before completion is printed.

Add stage tests for Secrets, Drive, identity gates, preview, GPU precision, model construction, numerical gate, smoke, reinitialization, ordered production train, validation, invalid-vector counts, periodic checkpointing, final save, reload, isolated resume check, immutable export, and private Hub publish.

Add a no-test-access test that rejects executable calls such as `load_dataset(..., split="test")`, `load_test_stream`, `dataset["test"]`, or test-row preview/iteration. Constants containing test checksums/counts are allowed for manifest identity only.

- [ ] **Step 2: Run the new tests and observe the expected failure**

Run:

```bash
training/.venv/bin/pytest training/tests/test_notebook_contract.py -q
```

Expected: the new interview tests fail because `train_interview_50k_colab.ipynb` does not exist; existing pilot tests remain passing.

- [ ] **Step 3: Commit the red contract**

```bash
git add training/tests/test_notebook_contract.py
git commit -m "test: define interview colab notebook contract"
```

### Task 2: Build the standalone interview notebook

**Files:**
- Create: `training/notebooks/train_interview_50k_colab.ipynb`
- Reference: `training/notebooks/train_distillation_colab.ipynb`
- Reference: `prompts/interview-50k-training-handoff.md`
- Test: `training/tests/test_notebook_contract.py`

**Interfaces:**
- Consumes: `DistillationConfig`, dataset loaders, `DistillationTrainer`, validation, checkpointing, and Hub APIs already exposed by `babel_training`.
- Produces: a user-uploadable notebook whose tagged cells execute in dependency order.

- [ ] **Step 1: Create notebook metadata and setup cells**

Copy the reference notebook metadata and its setup pattern. Create single-purpose tagged code cells for environment check, pinned package install, kernel version check, `HF_TOKEN`, Drive mount, immutable constants, runtime configuration, and identity verification. Keep shell installs as leading `!` lines so the contract parser can exclude them.

- [ ] **Step 2: Add immutable remote identity gates**

Use authenticated Hub metadata and streaming datasets at the exact config/revision. Assert manifest SHA `33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09`, train ordered SHA `518c30f10859a88681c3708ab0236bd104fdde96acff09089515d871d9600a1e`, validation ordered SHA `64cd7c82c58d73947f24b8120ef3c2e5c3a4a8f145bf0a7a6522175bcd1b2cd6`, and 50,000/5,000/5,000 manifest counts. Verify test identity from manifest metadata only; do not construct or iterate a test stream.

- [ ] **Step 3: Add smoke and quick-test execution cells**

Build ordered train and fixed validation streams with `max_length=384`. Run the existing one-batch gate. Limit smoke input to the first 1,000 ordered train rows. Define `QUICK_TEST_MODE = True`; in the training loop set the effective optimizer-step limit to one, save all restart state to a `quick-test` directory, and execute `break` after the saved first optimizer step. Print `QUICK TEST ONLY — FULL EPOCH NOT COMPLETED`.

- [ ] **Step 4: Add uncontaminated production path**

After smoke, reconstruct the model/trainer and reset optimizer, scheduler, scaler, loader cursor, and RNG from the configured seed. When `QUICK_TEST_MODE` is false, train exactly one ordered 50,000-row epoch, save periodic Drive checkpoints, and assert the next ordered row offset is 50,000 before marking the epoch complete.

- [ ] **Step 5: Add validation, restart, export, and publish cells**

Evaluate exactly 5,000 validation rows and record invalid student/teacher vector counts. Save the production-final checkpoint. Reload and compare fingerprints. Perform one resume step from a copied/isolated verification checkpoint so production-final remains immutable. Export LoRA and projection state plus a custom truthful `distillation_2016_interview` manifest. Hash every artifact, require a private destination repository, upload, and print the resulting immutable Hub commit SHA.

- [ ] **Step 6: Run the contract tests until green**

Run:

```bash
training/.venv/bin/pytest training/tests/test_notebook_contract.py -q
```

Expected: all pilot and interview notebook contract tests pass.

- [ ] **Step 7: Commit the notebook**

```bash
git add training/notebooks/train_interview_50k_colab.ipynb
git commit -m "feat: add interview 50k colab notebook"
```

### Task 3: Verify the complete handoff

**Files:**
- Verify: `training/notebooks/train_interview_50k_colab.ipynb`
- Verify: `training/tests/test_notebook_contract.py`

**Interfaces:**
- Consumes: the completed notebook and contract.
- Produces: fresh evidence that the notebook is safe to upload.

- [ ] **Step 1: Run focused notebook verification**

```bash
training/.venv/bin/pytest training/tests/test_notebook_contract.py -q
python -m json.tool training/notebooks/train_interview_50k_colab.ipynb >/dev/null
```

Expected: exit 0 for both commands.

- [ ] **Step 2: Run the full training test suite**

```bash
training/.venv/bin/pytest training/tests -q
```

Expected: exit 0 with no failures.

- [ ] **Step 3: Audit pins and forbidden patterns**

```bash
rg -n "distillation_2016_interview|b440e98b04ab77afed7caf0455eca3189235fc3b|97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3|QUICK_TEST_MODE" training/notebooks/train_interview_50k_colab.ipynb
```

Read the matching cells and confirm the exact config/revisions, true quick-test default, and explicit production opt-in. Search executable cell source for test loading and confirm there is none.

- [ ] **Step 4: Report the user handoff**

Provide the absolute notebook path, focused/full test results, cell count, `QUICK_TEST_MODE` default, the single setting required for the production epoch, and the fact that Colab execution has not been claimed or performed.
