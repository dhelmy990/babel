# Colab Python 3.13 Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pinned Colab training installation work on CPython 3.13 while preserving Python 3.10–3.12 development compatibility.

**Architecture:** Express NumPy compatibility with Python-version markers in the training package, then regenerate the hash-locked CUDA 12.8 environment for CPython 3.13. Commit that executable source first; a second notebook-only commit pins Colab to the executable source commit without creating a self-referential Git identity.

**Tech Stack:** Python packaging metadata, pip/uv, pip-tools-compatible hashed requirements, pytest, Google Colab.

## Global Constraints

- Training package supports `>=3.10,<3.14`.
- Python below 3.13 resolves NumPy 1.26.4; Python 3.13 resolves NumPy 2.2.6.
- Torch remains exactly `2.11.0+cu128`; CUDA/index directives remain unchanged.
- Do not stage or modify active backend/online Slice 3 work.
- Use CPython 3.13 for the clean Colab lock-install proof.

---

### Task 1: Package and Lock Compatibility

**Files:**
- Modify: `training/pyproject.toml`
- Modify: `training/requirements-colab.lock`
- Modify: `training/tests/test_lock_compatibility.py`

**Interfaces:**
- Consumes: current Colab CUDA 12.8 dependency set.
- Produces: a CPython 3.13 hash-locked environment and dual-version NumPy package metadata.

- [ ] **Step 1: Write failing metadata/lock tests**

Add assertions that parse `training/pyproject.toml` and require:

```python
assert project["project"]["requires-python"] == ">=3.10,<3.14"
assert 'numpy==1.26.4; python_version < "3.13"' in dependencies
assert 'numpy==2.2.6; python_version >= "3.13"' in dependencies
assert "--python-version=3.13" in lock_header
assert "numpy==2.2.6" in lock
assert "numpy==1.26.4" not in lock
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 training/.venv/bin/python -m pytest training/tests/test_lock_compatibility.py -q
```

Expected: failure because the package excludes 3.13 and the lock targets 3.12/NumPy 1.26.4.

- [ ] **Step 3: Update package metadata and regenerate the lock**

Set the exact Python range and conditional NumPy pins, then regenerate the lock for CPython 3.13 using the existing CUDA 12.8 index and binary-wheel constraints. Retain hashes and every existing direct dependency.

- [ ] **Step 4: Verify GREEN and clean CPython 3.13 installation**

Run the focused test, provision an isolated CPython 3.13 environment outside the repository, install the lock with `pip --require-hashes`, install the training package with `--no-deps`, then import NumPy, Torch, Transformers, PEFT, Accelerate, Datasets, and `babel_training`. Assert NumPy 2.2.6 and Python 3.13.

- [ ] **Step 5: Run regression suites and commit executable source**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 training/.venv/bin/python -m pytest training/tests -q
python3 -m compileall -q training/src training/tests
git diff --check
```

Commit only the three Task 1 files.

### Task 2: Pin the Notebook to Compatible Source

**Files:**
- Modify: `training/notebooks/train_distillation_colab.ipynb`
- Modify: `training/tests/test_notebook_contract.py`
- Modify: `prompts/colab-distillation-pilot-handoff.md`
- Modify: `docs/runbooks/colab-distillation-pilot.md`

**Interfaces:**
- Consumes: Task 1 executable-source commit SHA.
- Produces: a notebook that checks out the Python 3.13-compatible commit.

- [ ] **Step 1: Write a failing notebook pin test**

Require the package-install cell to contain Task 1's exact 40-character commit SHA and require the environment cell to accept Python 3.13 but reject Python 3.14+.

- [ ] **Step 2: Run the notebook contract and verify RED**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 training/.venv/bin/python -m pytest training/tests/test_notebook_contract.py -q
```

Expected: failure because the notebook still checks out the pre-fix source commit.

- [ ] **Step 3: Update the notebook and handoff documentation**

Pin `SOURCE_COMMIT_SHA` to Task 1's commit, add a clear Python `3.10 <= version < 3.14` environment assertion, and record current Colab Python 3.13 compatibility in the handoff/runbook.

- [ ] **Step 4: Verify and commit the handoff update**

Run notebook-contract tests, parse the notebook as JSON, scan for unresolved placeholders, run `git diff --check`, and commit only Task 2 files.

