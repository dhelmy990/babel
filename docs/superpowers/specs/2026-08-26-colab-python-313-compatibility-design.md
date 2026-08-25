# Colab Python 3.13 Compatibility Design

## Goal

Make the checked-in distillation notebook install and start on current Google
Colab CPython 3.13 without dropping the existing Python 3.10–3.12 development
path.

## Root cause

The training package excludes Python 3.13 and its Colab lock was compiled for
CPython 3.12. It pins NumPy 1.26.4, which publishes no CPython 3.13 wheel.
Current Colab therefore fails before editable package installation.

## Compatibility contract

- Expand `requires-python` to `>=3.10,<3.14`.
- Keep NumPy 1.26.4 for Python versions below 3.13.
- Pin NumPy 2.2.6 for Python 3.13.
- Regenerate `training/requirements-colab.lock` for CPython 3.13 and retain
  hash-checked installation from the CUDA 12.8/PyPI indexes.
- Update lock-contract tests to prove the Python range, conditional pins, and
  CPython 3.13 lock target cannot regress.
- Do not change model, CUDA, dataset, tokenizer, or training configuration.

## Verification

Use an isolated CPython 3.13 environment to install the exact lock with
`--require-hashes`, install `babel-training` with `--no-deps`, import its core
runtime dependencies, and run the notebook-contract/training tests. Existing
Python 3.10 tests must continue to pass with NumPy 1.26.4.

Only training compatibility files belong in the implementation commit. Active
backend and online Slice 3 changes in the shared worktree remain untouched.
