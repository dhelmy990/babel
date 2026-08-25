from __future__ import annotations

import ast
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "training/notebooks/train_distillation_colab.ipynb"
EXPECTED_TAGS = [
    "environment-check",
    "package-install",
    "hf-token-secret",
    "optional-drive-mount",
    "configuration",
    "resolve-and-pin-revision",
    "remote-row-preview",
    "gpu-precision",
    "model-construction",
    "one-batch-gate",
    "train",
    "validate",
    "save-checkpoint",
    "reload-checkpoint",
    "resume-checkpoint",
    "export",
]


def _notebook() -> dict[str, object]:
    return json.loads(NOTEBOOK.read_bytes())


def _source(cell: dict[str, object]) -> str:
    source = cell["source"]
    return "".join(source) if isinstance(source, list) else str(source)


def test_notebook_has_ordered_single_purpose_handoff_cells() -> None:
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    cells = notebook["cells"]
    assert len(cells) == len(EXPECTED_TAGS)
    assert [cell["metadata"]["tags"] for cell in cells] == [
        [tag] for tag in EXPECTED_TAGS
    ]
    assert all(cell["cell_type"] == "code" for cell in cells)
    for cell in cells:
        python_source = "\n".join(
            line for line in _source(cell).splitlines() if not line.startswith("!")
        )
        ast.parse(python_source)


def test_notebook_pins_python_313_compatible_source() -> None:
    cells = _notebook()["cells"]
    environment = _source(cells[EXPECTED_TAGS.index("environment-check")])
    package_install = _source(cells[EXPECTED_TAGS.index("package-install")])

    assert "sys.version_info[:2] < (3, 14)" in environment
    assert (
        "SOURCE_COMMIT_SHA = '92f3ac697d78eb827d75b033df92dcbed887def7'"
        in package_install
    )


def test_notebook_reads_colab_secret_without_printing_or_embedding_it() -> None:
    cells = _notebook()["cells"]
    secret_source = _source(cells[EXPECTED_TAGS.index("hf-token-secret")])
    all_source = "\n".join(_source(cell) for cell in cells)

    assert "from google.colab import userdata" in secret_source
    assert re.search(r"userdata\.get\([\"']HF_TOKEN[\"']\)", secret_source)
    assert "getpass" not in all_source
    assert not re.search(r"print\([^\n]*HF_TOKEN", all_source, re.IGNORECASE)
    assert not re.search(r"hf_[A-Za-z0-9]{12,}", all_source)


def test_notebook_pins_revisions_and_uses_package_implementations() -> None:
    cells = _notebook()["cells"]
    all_source = "\n".join(_source(cell) for cell in cells)
    revision_source = _source(cells[EXPECTED_TAGS.index("resolve-and-pin-revision")])
    preview_source = _source(cells[EXPECTED_TAGS.index("remote-row-preview")])

    assert "resolve_dataset_revision" in revision_source
    assert "dataset_revision" in preview_source
    assert "revision=dataset_revision" in preview_source
    assert "from babel_training.model import" in all_source
    assert "from babel_training.trainer import" in all_source
    assert "build_stateful_train_loader" in all_source
    assert "from babel_training.validation import validate_embeddings" in all_source
    assert not re.search(r"^\s*(class|def)\s+", all_source, re.MULTILINE)
    assert "AutoModel.from_pretrained" not in all_source
    assert "StatefulDataLoader(" not in all_source
    assert "def distillation_loss" not in all_source


def test_notebook_has_t4_safe_defaults_and_complete_restart_flow() -> None:
    cells = _notebook()["cells"]
    config = _source(cells[EXPECTED_TAGS.index("configuration")])
    precision = _source(cells[EXPECTED_TAGS.index("gpu-precision")])
    train = _source(cells[EXPECTED_TAGS.index("train")])
    save = _source(cells[EXPECTED_TAGS.index("save-checkpoint")])
    reload_cell = _source(cells[EXPECTED_TAGS.index("reload-checkpoint")])
    resume = _source(cells[EXPECTED_TAGS.index("resume-checkpoint")])

    assert "per_device_batch_size = 2" in config
    assert "gradient_accumulation_steps = 8" in config
    assert "max_length=512" in config
    assert "major >= 8" in precision
    assert 'mixed_precision = "fp16"' in precision
    assert "max_runtime_minutes" in train
    assert ".save(" in save
    assert ".reload(" in reload_cell
    assert ".train(" in resume
