from __future__ import annotations

import ast
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "training/notebooks/train_distillation_colab.ipynb"
INTERVIEW_NOTEBOOK = ROOT / "training/notebooks/train_interview_50k_colab.ipynb"
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
INTERVIEW_EXPECTED_TAGS = [
    "environment-check",
    "package-install",
    "kernel-version-check",
    "hf-token-secret",
    "drive-mount",
    "immutable-inputs",
    "runtime-configuration",
    "identity-gates",
    "ordered-stream",
    "train-validation-preview",
    "gpu-precision",
    "model-construction",
    "one-batch-gate",
    "smoke",
    "production-reinitialization",
    "run-control",
    "ordered-production-train",
    "validation",
    "final-save",
    "reload-checkpoint",
    "isolated-resume-verification",
    "immutable-export",
    "private-hub-publish",
]


def _notebook() -> dict[str, object]:
    return json.loads(NOTEBOOK.read_bytes())


def _interview_notebook() -> dict[str, object]:
    return json.loads(INTERVIEW_NOTEBOOK.read_bytes())


def _source(cell: dict[str, object]) -> str:
    source = cell["source"]
    return "".join(source) if isinstance(source, list) else str(source)


def _interview_cell(tag: str) -> str:
    cells = _interview_notebook()["cells"]
    return _source(cells[INTERVIEW_EXPECTED_TAGS.index(tag)])


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


def test_interview_notebook_is_valid_python_notebook() -> None:
    notebook = _interview_notebook()
    assert notebook["nbformat"] == 4
    assert notebook["cells"]
    assert len(notebook["cells"]) == len(INTERVIEW_EXPECTED_TAGS)
    assert [cell["metadata"]["tags"] for cell in notebook["cells"]] == [
        [tag] for tag in INTERVIEW_EXPECTED_TAGS
    ]
    assert all(cell["cell_type"] == "code" for cell in notebook["cells"])
    for cell in notebook["cells"]:
        source = _source(cell)
        python_source = "\n".join(
            line for line in source.splitlines() if not line.startswith("!")
        )
        ast.parse(python_source)


def test_interview_notebook_pins_handoff_identity_and_protocol() -> None:
    all_source = "\n".join(
        _source(cell) for cell in _interview_notebook()["cells"]
    )
    for identity in (
        "92f3ac697d78eb827d75b033df92dcbed887def7",
        "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "dhelmy990/babel-wikipedia-experiment",
        "distillation_2016_interview",
        "b440e98b04ab77afed7caf0455eca3189235fc3b",
        "33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09",
        "518c30f10859a88681c3708ab0236bd104fdde96acff09089515d871d9600a1e",
        "64cd7c82c58d73947f24b8120ef3c2e5c3a4a8f145bf0a7a6522175bcd1b2cd6",
        "d2cd61ee895c2f6386c708d7884666b4aa579174674e8bf70e876ee891956bf5",
        "11a217879913305a88b0bfaafffa39f132883d2b6f27252a02054ba95ea6b2c5",
        "a925eb795f253635f3a80a76994a7139a0f81f4e784beb61dfc93f8b662dc8f0",
        "103f22b38b048973f8ab6ba52efca41f667f37c305b5dfea8b752dc492d7ac03",
    ):
        assert identity in all_source
    assert "DistillationConfig(max_length=384)" in all_source
    assert "streaming=True" in all_source
    assert re.search(r"SMOKE_ROWS\s*=\s*1_000", all_source)
    assert re.search(r"TRAIN_ROWS\s*=\s*50_000", all_source)
    assert re.search(r"VALIDATION_ROWS\s*=\s*5_000", all_source)
    assert re.search(r"EPOCHS\s*=\s*1", all_source)
    assert not re.search(
        r"dataset_config\s*=\s*[\"']distillation_2016[\"']", all_source
    )
    assert not re.search(r"dataset_(?:ref|revision)\s*=\s*[\"']main[\"']", all_source)


def test_interview_notebook_has_secrets_drive_identity_and_preview_gates() -> None:
    secret = _interview_cell("hf-token-secret")
    drive = _interview_cell("drive-mount")
    identity = _interview_cell("identity-gates")
    preview = _interview_cell("train-validation-preview")

    assert "from google.colab import userdata" in secret
    assert re.search(r"userdata\.get\([\"']HF_TOKEN[\"']\)", secret)
    assert not re.search(r"print\([^\n]*HF_TOKEN", secret, re.IGNORECASE)
    assert "drive.mount(" in drive and "MyDrive" in drive
    assert "dataset_info(" in identity and "hf_hub_download(" in identity
    assert "dataset_revision == DATASET_REVISION" in identity
    assert "manifest_sha256 == MANIFEST_SHA256" in identity
    assert "manifest['counts'] == EXPECTED_COUNTS" in identity
    assert "TRAIN_ORDERED_SHA256" in identity
    assert "VALIDATION_ORDERED_SHA256" in identity
    assert "TEST_ORDERED_SHA256" in identity
    assert "islice" in preview
    assert "train" in preview and "validation" in preview


def test_interview_notebook_runs_gate_smoke_then_rebuilds_all_production_state() -> None:
    model = _interview_cell("model-construction")
    gate = _interview_cell("one-batch-gate")
    smoke = _interview_cell("smoke")
    reset = _interview_cell("production-reinitialization")
    all_source = "\n".join(
        _source(cell) for cell in _interview_notebook()["cells"]
    )

    assert "from babel_training.model import DistilledQwenEncoder" in model
    assert "from babel_training.trainer import" in model
    assert "build_stateful_train_loader" in model
    assert "one_batch_gate()" in gate
    assert "math.isfinite" in gate
    assert "SMOKE_ROWS" in smoke and "islice" in smoke
    assert "smoke_trainer.train(" in smoke
    assert "SMOKE CHECKPOINT" in smoke
    for reset_marker in (
        "del smoke_trainer",
        "random.seed(TRAINING_SEED)",
        "np.random.seed(TRAINING_SEED)",
        "torch.manual_seed(TRAINING_SEED)",
        "torch.cuda.manual_seed_all(TRAINING_SEED)",
        "production_model = DistilledQwenEncoder.from_pretrained(config)",
        "production_optimizer",
        "production_scheduler",
        "production_scaler",
        "production_train_loader",
    ):
        assert reset_marker in reset
    assert "projection_output_dimension': 100" in all_source


def test_interview_notebook_defaults_to_restartable_one_step_quick_test() -> None:
    control = _interview_cell("run-control")
    train = _interview_cell("ordered-production-train")

    assert "QUICK_TEST_MODE = True" in control
    assert "if QUICK_TEST_MODE:" in train
    assert "optimizer_step_limit = 1" in train
    assert "quick-test" in train
    assert ".save(" in train
    assert "break" in train
    assert "QUICK TEST ONLY" in train
    assert "FULL EPOCH NOT COMPLETED" in train
    assert "if not QUICK_TEST_MODE:" in train
    assert "next_ordered_row == TRAIN_ROWS" in train
    assert "ordered 50,000-row epoch complete" in train
    for restart_field in (
        "optimizer",
        "scheduler",
        "scaler",
        "rng",
        "epoch",
        "global_step",
        "next_ordered_row",
    ):
        assert restart_field in train.lower()
    assert "checkpoint_interval" in train
    assert "production-checkpoints" in train


def test_interview_notebook_validates_saves_reloads_and_resumes_in_isolation() -> None:
    validation = _interview_cell("validation")
    final_save = _interview_cell("final-save")
    reload_cell = _interview_cell("reload-checkpoint")
    resume = _interview_cell("isolated-resume-verification")

    assert "VALIDATION_ROWS" in validation
    assert "validate_embeddings" in validation
    assert "invalid_student_vector_count" in validation
    assert "invalid_teacher_vector_count" in validation
    assert "invalid_vector_count" in validation
    assert "production-final" in final_save and ".save(" in final_save
    assert "validation_fingerprint" in reload_cell and ".reload(" in reload_cell
    assert "copytree" in resume and "resume-verification" in resume
    assert ".train(" in resume
    assert "final_checkpoint_fingerprint" in resume


def test_interview_notebook_exports_truthful_hashes_and_publishes_privately() -> None:
    export = _interview_cell("immutable-export")
    publish = _interview_cell("private-hub-publish")

    assert "export_components()" in export
    assert "projection.safetensors" in export
    assert "adapter_model.safetensors" in export
    assert "training_config.json" in export
    assert "validation_report.json" in export
    assert "artifact_manifest.json" in export
    assert "distillation_2016_interview" in export
    assert "artifact_hashes" in export
    assert "hashlib.sha256" in export
    assert "private=True" in publish
    assert "create_repo(" in publish
    assert "create_commit(" in publish
    assert "model_info(" in publish
    assert "hub_commit_sha" in publish
    assert "HF_TOKEN" in publish


def test_interview_notebook_never_opens_or_iterates_test_examples() -> None:
    all_source = "\n".join(
        _source(cell) for cell in _interview_notebook()["cells"]
    )

    forbidden = (
        r"load_dataset\([^)]*split\s*=\s*[\"']test[\"']",
        r"load_test_stream\s*\(",
        r"dataset\s*\[\s*[\"']test[\"']\s*\]",
        r"test_(?:stream|loader|rows|batch|preview|iterator)\s*=",
        r"for\s+\w+\s+in\s+test_",
        r"islice\(\s*test_",
    )
    for pattern in forbidden:
        assert not re.search(pattern, all_source, re.IGNORECASE | re.DOTALL)
