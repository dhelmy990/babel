from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


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


def _exec_interview_definitions(
    tag: str, names: set[str], namespace: dict[str, object]
) -> dict[str, object]:
    tree = ast.parse(_interview_cell(tag))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names
    ]
    assert {node.name for node in definitions} == names
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "<notebook>", "exec"), namespace)
    return namespace


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
    assert "list_repo_tree(" in identity
    assert "remote_file.lfs.sha256 == shard['sha256']" in identity
    assert "remote_file.lfs.size == shard['bytes']" in identity
    assert "TRAIN_ORDERED_SHA256" in identity
    assert "VALIDATION_ORDERED_SHA256" in identity
    assert "TEST_ORDERED_SHA256" in identity
    assert "islice" in preview
    assert "train" in preview and "validation" in preview


def test_interview_notebook_runs_gate_smoke_then_rebuilds_all_production_state() -> None:
    config = _interview_cell("runtime-configuration")
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
    assert "smoke_gradient_accumulation_steps = 4" in config
    assert "gradient_accumulation_steps=smoke_gradient_accumulation_steps" in model
    assert "smoke_microbatches % smoke_gradient_accumulation_steps == 0" in smoke
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


def test_interview_notebook_can_resume_an_interrupted_production_run() -> None:
    config = _interview_cell("runtime-configuration")
    reset = _interview_cell("production-reinitialization")
    resume = _interview_cell("isolated-resume-verification")

    assert "RESUME_CHECKPOINT_DIR = None" in config
    assert "if RESUME_CHECKPOINT_DIR is not None:" in reset
    assert "NOTEBOOK_CHECKPOINT_COMPLETE" in reset
    assert "production_trainer.reload(" in reset
    assert "notebook_restart_metadata.json" in reset
    assert "weights_only=False" not in reset
    assert "restored_checkpoint_manifest['training_config'] != training_config" in reset
    assert "production_trainer.global_step != restored_restart_state['global_step']" in reset
    assert "production_train_stream.next_ordered_row != restored_restart_state['next_ordered_row']" in reset
    assert "if RESUME_CHECKPOINT_DIR is not None:" in resume
    assert "resume_candidates.append(Path(RESUME_CHECKPOINT_DIR))" in resume
    assert "def candidate_global_step(path):" in resume
    assert "candidate_global_step(path) < production_optimizer_steps" in resume
    assert ".name.removeprefix('step-')" not in resume


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
    assert "hf_hub_download," in publish
    assert "remote_artifact_hashes" in publish
    assert "existing_artifact_paths == expected_complete_paths" in publish
    assert "existing_artifact_paths == payload_path_set" in publish
    assert "Remote artifact prefix is partial or conflicting" in publish


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


def test_interview_notebook_fail_closed_gates_survive_optimized_python() -> None:
    for cell in _interview_notebook()["cells"]:
        python_source = "\n".join(
            line for line in _source(cell).splitlines() if not line.startswith("!")
        )
        tree = ast.parse(python_source)
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), (
            cell["metadata"]["tags"],
            "use an explicit exception instead of optimization-removable assert",
        )


def test_interview_ordered_stream_runtime_resumes_without_repeat_or_test_calls() -> None:
    calls: list[dict[str, object]] = []
    rows = [
        {
            "article_key": f"key-{index}",
            "page_id": index + 1,
            "canonical_title": f"title-{index}",
            "lead_text": f"lead-{index}",
            "teacher_vector": [0.1] * 100,
            "teacher_norm": 1.0,
            "split": "train",
        }
        for index in range(6)
    ]

    class FakeDataset(list[dict[str, object]]):
        def skip(self, count: int) -> "FakeDataset":
            return FakeDataset(self[count:])

    def fake_load_dataset(*args: object, **kwargs: object) -> FakeDataset:
        calls.append({"args": args, **kwargs})
        return FakeDataset(rows)

    def fake_validate(row: object, *, expected_split: str) -> object:
        assert isinstance(row, dict) and row["split"] == expected_split
        return row

    def fake_require(condition: object, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    namespace = _exec_interview_definitions(
        "ordered-stream",
        {"OrderedInterviewStream"},
        {
            "DATASET_REPO_ID": "repo",
            "DATASET_CONFIG": "distillation_2016_interview",
            "dataset_revision": "a" * 40,
            "HF_TOKEN": "secret",
            "load_dataset": fake_load_dataset,
            "islice": __import__("itertools").islice,
            "validate_distillation_row": fake_validate,
            "require": fake_require,
        },
    )
    stream_type = namespace["OrderedInterviewStream"]
    first = stream_type("train", 6)
    iterator = iter(first)
    consumed = [next(iterator), next(iterator)]
    checkpoint = first.state_dict()
    iterator.close()
    resumed = stream_type("train", 6)
    resumed.load_state_dict(checkpoint)
    consumed.extend(list(resumed))

    assert [row["article_key"] for row in consumed] == [f"key-{index}" for index in range(6)]
    assert all(call["split"] == "train" for call in calls)
    assert all(call["streaming"] is True for call in calls)
    before = len(calls)
    with pytest.raises(ValueError, match="train or validation"):
        stream_type("test", 1)
    assert len(calls) == before


def test_interview_ordered_stream_accepts_valid_long_rows_then_projects_fields() -> None:
    raw_row = {
        "article_key": "enwiki:2016-10-01:12964030",
        "page_id": 12964030,
        "canonical_title": "Long article",
        "lead_text": "x" * 33_205,
        "article_text": "hidden full article",
        "teacher_vector": [0.1] * 100,
        "teacher_norm": 1.0,
        "split": "train",
        "wikidata_id": None,
        "source_revision_id": None,
        "snapshot_date": "2016-10-01",
        "reconciliation_status": "matched",
    }
    calls: list[tuple[object, str]] = []

    class FakeDataset(list[dict[str, object]]):
        def skip(self, count: int) -> "FakeDataset":
            return FakeDataset(self[count:])

    def fake_load_dataset(*args: object, **kwargs: object) -> FakeDataset:
        del args, kwargs
        return FakeDataset([raw_row])

    def fake_validate_distillation_row(
        row: object, *, expected_split: str
    ) -> dict[str, object]:
        calls.append((row, expected_split))
        assert row is raw_row
        return dict(raw_row)

    def fake_require(condition: object, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    namespace = _exec_interview_definitions(
        "ordered-stream",
        {"OrderedInterviewStream"},
        {
            "DATASET_REPO_ID": "repo",
            "DATASET_CONFIG": "distillation_2016_interview",
            "dataset_revision": "a" * 40,
            "HF_TOKEN": "secret",
            "load_dataset": fake_load_dataset,
            "islice": __import__("itertools").islice,
            "validate_distillation_row": fake_validate_distillation_row,
            "require": fake_require,
        },
    )
    result = list(namespace["OrderedInterviewStream"]("train", 1))

    assert calls == [(raw_row, "train")]
    assert result == [
        {
            name: raw_row[name]
            for name in (
                "article_key",
                "page_id",
                "canonical_title",
                "lead_text",
                "teacher_vector",
                "teacher_norm",
                "split",
            )
        }
    ]
    assert len(str(result[0]["lead_text"]).encode("utf-8")) > 16 * 1024
    assert "article_text" not in result[0]


def test_interview_quick_cell_runtime_takes_one_step_saves_and_breaks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    train_calls: list[int] = []

    class FakeTrainer:
        global_step = 0
        epoch = 0

        def train(self, *, max_steps: int) -> list[float]:
            train_calls.append(max_steps)
            self.global_step = max_steps
            stream.next_ordered_row = 16
            return [0.25]

        def save(self, path: str, *, metrics: object) -> SimpleNamespace:
            Path(path).mkdir(parents=True)
            return SimpleNamespace(global_step=self.global_step)

    stream = SimpleNamespace(next_ordered_row=0)

    def complete(path: str, trainer: object, stream_value: object, scaler: object, manifest: object) -> object:
        del trainer, scaler
        Path(path, "notebook_restart_metadata.json").write_text(
            json.dumps(
                {
                    "components": ["optimizer", "scheduler", "scaler", "rng"],
                    "global_step": 1,
                    "next_ordered_row": stream_value.next_ordered_row,
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def fake_require(condition: object, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    namespace = {
        "QUICK_TEST_MODE": True,
        "production_trainer": FakeTrainer(),
        "production_train_stream": stream,
        "production_scaler": None,
        "run_root": str(tmp_path),
        "complete_restartable_checkpoint": complete,
        "os": __import__("os"),
        "json": json,
        "Path": Path,
        "require": fake_require,
    }
    exec(compile(_interview_cell("ordered-production-train"), "<quick-cell>", "exec"), namespace)

    assert train_calls == [1]
    assert namespace["production_trainer"].global_step == 1
    assert Path(tmp_path, "quick-test", "notebook_restart_metadata.json").is_file()
    assert "QUICK TEST ONLY" in capsys.readouterr().out


def test_interview_publication_helpers_recover_and_verify_remote_bytes(tmp_path: Path) -> None:
    namespace = _exec_interview_definitions(
        "private-hub-publish",
        {"classify_publication_state", "verify_remote_artifact_hashes"},
        {"Path": Path, "hashlib": __import__("hashlib")},
    )
    classify = namespace["classify_publication_state"]
    verify = namespace["verify_remote_artifact_hashes"]
    payload = {"artifacts/id/a.bin", "artifacts/id/b.json"}
    manifest_path = "artifacts/id/artifact_manifest.json"

    assert classify(set(), payload, manifest_path) == "new"
    assert classify(payload, payload, manifest_path) == "payload_only"
    assert classify(payload | {manifest_path}, payload, manifest_path) == "complete"
    with pytest.raises(RuntimeError, match="partial or conflicting"):
        classify({"artifacts/id/a.bin"}, payload, manifest_path)

    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    remote_files: dict[str, Path] = {}
    for repo_path, raw in {"artifacts/id/a.bin": b"abc", "artifacts/id/b.json": b"{}"}.items():
        path = remote_root / Path(repo_path).name
        path.write_bytes(raw)
        remote_files[repo_path] = path
    download_calls: list[str] = []

    def fake_download(repo_id: str, *, filename: str, **kwargs: object) -> str:
        del repo_id, kwargs
        download_calls.append(filename)
        return str(remote_files[filename])

    hashlib_module = __import__("hashlib")
    expected_hashes = {
        repo_path: hashlib_module.sha256(path.read_bytes()).hexdigest()
        for repo_path, path in remote_files.items()
    }
    expected_sizes = {repo_path: path.stat().st_size for repo_path, path in remote_files.items()}
    verified = verify(
        "repo", "b" * 40, expected_hashes, expected_sizes, "secret", fake_download
    )
    assert set(verified) == set(remote_files)
    assert download_calls == sorted(remote_files)
    remote_files["artifacts/id/a.bin"].write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="[Rr]emote artifact byte mismatch"):
        verify("repo", "b" * 40, expected_hashes, expected_sizes, "secret", fake_download)


def test_interview_publication_state_machine_runtime_recovers_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    namespace = _exec_interview_definitions(
        "private-hub-publish",
        {
            "classify_publication_state",
            "verify_remote_artifact_hashes",
            "publish_private_artifact",
        },
        {"Path": Path, "hashlib": __import__("hashlib"), "json": json, "re": re},
    )
    publish = namespace["publish_private_artifact"]

    class FakeAdd:
        def __init__(self, *, path_in_repo: str, path_or_fileobj: str) -> None:
            self.path_in_repo = path_in_repo
            self.path_or_fileobj = path_or_fileobj

    class FakeHub:
        def __init__(self) -> None:
            self.head = "0" * 40
            self.commits: dict[str, dict[str, bytes]] = {self.head: {}}
            self.counter = 0
            self.fail_manifest_once = True

        def create_repo(self, **kwargs: object) -> None:
            assert kwargs["private"] is True

        def model_info(self, repo_id: str, *, revision: str, token: str) -> SimpleNamespace:
            del repo_id, token
            sha = self.head if revision == "main" else revision
            return SimpleNamespace(
                private=True,
                sha=sha,
                siblings=[SimpleNamespace(rfilename=path) for path in self.commits[sha]],
            )

        def create_commit(self, **kwargs: object) -> SimpleNamespace:
            parent = kwargs["parent_commit"]
            assert parent == self.head
            operations = kwargs["operations"]
            if self.fail_manifest_once and any(
                operation.path_in_repo.endswith("artifact_manifest.json")
                for operation in operations
            ):
                self.fail_manifest_once = False
                raise RuntimeError("injected manifest commit failure")
            snapshot = dict(self.commits[parent])
            for operation in operations:
                snapshot[operation.path_in_repo] = Path(operation.path_or_fileobj).read_bytes()
            self.counter += 1
            self.head = f"{self.counter:040x}"
            self.commits[self.head] = snapshot
            return SimpleNamespace(oid=self.head)

        def download(self, repo_id: str, *, filename: str, revision: str, **kwargs: object) -> str:
            del repo_id, kwargs
            raw = self.commits[revision][filename]
            destination = tmp_path / f"download-{revision}-{Path(filename).name}"
            destination.write_bytes(raw)
            return str(destination)

    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    (artifact_dir / "a.bin").write_bytes(b"abc")
    (artifact_dir / "b.json").write_bytes(b"{}")
    artifact_hashes = {
        path.name: __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        for path in artifact_dir.iterdir()
    }
    manifest_path = artifact_dir / "artifact_manifest.json"

    def fresh_manifest() -> dict[str, object]:
        value = {
            "artifact_id": "id",
            "identity": {"dataset_config": "distillation_2016_interview"},
            "artifact_hashes": artifact_hashes,
            "publication": {
                "repo_id": "owner/model",
                "private": True,
                "artifact_payload_commit_sha": None,
            },
        }
        manifest_path.write_bytes(
            (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        return value

    def canonical(value: object) -> bytes:
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()

    hub = FakeHub()
    with pytest.raises(RuntimeError, match="injected manifest commit failure"):
        publish(
            hub,
            "owner/model",
            "id",
            artifact_dir,
            fresh_manifest(),
            manifest_path,
            artifact_hashes,
            "secret",
            FakeAdd,
            hub.download,
            canonical,
        )
    assert set(hub.commits[hub.head]) == {"artifacts/id/a.bin", "artifacts/id/b.json"}

    recovered = publish(
        hub,
        "owner/model",
        "id",
        artifact_dir,
        fresh_manifest(),
        manifest_path,
        artifact_hashes,
        "secret",
        FakeAdd,
        hub.download,
        canonical,
    )
    assert recovered["publication_state"] == "payload_only"
    complete = publish(
        hub,
        "owner/model",
        "id",
        artifact_dir,
        fresh_manifest(),
        manifest_path,
        artifact_hashes,
        "secret",
        FakeAdd,
        hub.download,
        canonical,
    )
    assert complete["publication_state"] == "complete"

    manifest_repo_path = "artifacts/id/artifact_manifest.json"
    valid_snapshot = dict(hub.commits[hub.head])
    contradictory = json.loads(valid_snapshot[manifest_repo_path])
    contradictory["publication"]["private"] = False
    hub.commits[hub.head][manifest_repo_path] = canonical(contradictory)
    with pytest.raises(RuntimeError, match="manifest identity conflicts"):
        publish(
            hub, "owner/model", "id", artifact_dir, fresh_manifest(), manifest_path,
            artifact_hashes, "secret", FakeAdd, hub.download, canonical,
        )
    hub.commits[hub.head] = valid_snapshot
    payload_commit = complete["artifact_payload_commit_sha"]
    hub.commits[payload_commit]["artifacts/id/a.bin"] = b"corrupt"
    with pytest.raises(RuntimeError, match="[Rr]emote artifact byte mismatch"):
        publish(
            hub, "owner/model", "id", artifact_dir, fresh_manifest(), manifest_path,
            artifact_hashes, "secret", FakeAdd, hub.download, canonical,
        )
