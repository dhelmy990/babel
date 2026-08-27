# Colab Distillation Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a remotely streamable 2016 pilot dataset and a top-to-bottom Colab package that trains, checkpoints, reloads, and resumes the pinned Qwen student.

**Architecture:** Acquisition code converts the verified Figshare teacher and October 2016 Wikipedia snapshot into validated Parquet shards under an external data root, then incrementally publishes them to one private Hugging Face dataset. A separate installable `babel_training` package streams a pinned Hub revision and owns model, loss, checkpoint, and validation behavior; the notebook only orchestrates those modules.

**Tech Stack:** Python 3.10+, PyTorch, Transformers, PEFT, Accelerate, Datasets, Hugging Face Hub, PyArrow, TorchData, Safetensors, NumPy, pip-tools, pytest, Google Colab, Google Drive.

## Global Constraints

- Base model and tokenizer: `Qwen/Qwen3-Embedding-0.6B` at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
- Input: title, one blank line, then lead/first useful section.
- Student projection: `Linear(1024, 100)` followed by L2 normalization.
- Pooling: pinned Qwen last non-padding token behavior with left padding.
- Initial LoRA targets `[q_proj, v_proj]`, rank 16, alpha 32, dropout 0.05, bias none.
- Base parameters remain frozen; only LoRA and projection receive gradients.
- Default `lambda_rel = 0.5`; loss math and normalization run in FP32.
- Pilot default maximum length is 512 tokens; 1024 remains configurable.
- Teacher file: `2016-09-01_2016-09-30_en_100.zip`, MD5 `ac70acfc41aff7a23cc9439e3bb1771f`.
- Wikipedia text: `enwiki-20161001-pages-articles-multistream.xml.bz2`, MD5 `5df8e610829c336138dcb9191071b283`.
- Bulk data root: `/home/dhelmy990/Data/babel-data`; no bulk file enters Git.
- Dataset repository: private `dhelmy990/babel-wikipedia-experiment`, configuration `distillation_2016`.
- Distilled model repository: private `dhelmy990/babel-qwen-navigation-2016`.
- Colab reads the token from Secrets, resolves the selected dataset ref once, then pins the commit SHA.
- Split assignment is stable hash 98% train, 1% validation, 1% test.
- Use test-first development and commit after every task gate.

---

## Orchestrator Fleet Map

Maximum concurrency is four active agents: one orchestrator and three workers.
Execution should begin in an isolated worktree created with the
`using-git-worktrees` skill.

```text
Orchestrator
  Task 1: contracts, schemas, package skeleton, debug fixture
        |
        +---------------- Wave 1 ----------------+
        |                    |                    |
  Agent A / Tasks 2–3  Agent B / Task 6    Agent C / Task 7
  sources + teacher    remote data loader  Qwen model + losses
        |                    |                    |
        +------------- Integration gate ----------+
        |
  Orchestrator / Task 4: Wikipedia reconciliation contract
        |
        +---------------- Wave 2 ----------------+
        |                    |                    |
  Agent A / Task 5     Agent B / Task 8    Agent C / Task 9
  shards + Hub publish trainer/checkpoint  validation package
        |                    |                    |
        +------------- Integration gate ----------+
                             |
                    Orchestrator / Task 10
                    notebook, runbook, handoff
```

Ownership rules:

| Lane | Owned files | Must not edit |
|---|---|---|
| Orchestrator | `schemas/`, shared fixtures, notebook integration, root docs | Worker implementation files during active waves |
| Agent A | `data_pipeline/` | `training/`, notebook |
| Agent B | `training/src/babel_training/data.py`, `trainer.py`, `checkpointing.py` and matching tests | model/loss files owned by Agent C |
| Agent C | `training/src/babel_training/model.py`, `pooling.py`, `losses.py`, `validation.py` and matching tests | data/checkpoint files owned by Agent B |

Agents commit only owned files. A worker that needs a shared-signature change
must stop and send the proposed change to the orchestrator. At each integration
gate the orchestrator reviews commits, runs the combined suite, and freezes the
interfaces before launching the next wave.

## Target File Map

```text
schemas/
  distillation-example-v1.json       Observable row contract
  dataset-readiness-v1.json          Rolling publication state
  provenance-v1.json                 Source and artifact identity
fixtures/distillation/
  debug-examples.jsonl               Tiny deterministic paired rows
  readiness.json                     Complete debug readiness document
data_pipeline/
  pyproject.toml                      Acquisition package and test config
  requirements.lock                  Hashed acquisition/test dependency lock
  src/babel_data/
    contracts.py                     Schema loading and row validation
    sources.py                       Verified source manifests/downloads
    teacher.py                       Word2Vec teacher parsing
    wikipedia.py                     Multistream page/redirect extraction
    reconcile.py                     Canonical identity join
    shard.py                         Split assignment and Parquet writing
    hub.py                           Incremental private-Hub publication
    cli.py                           `babel-data` commands
  tests/                              Unit and pipeline tests
training/
  pyproject.toml                      Installable Colab package
  requirements-colab.lock            Hashed Colab dependency lock
  src/babel_training/
    config.py                         Frozen defaults and validated overrides
    data.py                           Pinned remote streaming loader
    collator.py                       Input formatting/tokenization
    pooling.py                        Official last-token pooling
    model.py                          Qwen, projection, and LoRA composition
    losses.py                         Direct and relational objectives
    trainer.py                        Accelerate training loop
    checkpointing.py                  Complete restart state
    validation.py                     Neighbor and vector metrics
    hub.py                            Revision resolution/model export
  tests/                              CPU and integration tests
  notebooks/train_distillation_colab.ipynb
docs/runbooks/colab-distillation-pilot.md
```

### Task 1: Freeze Contracts, Skeletons, and Debug Fixture

**Files:**
- Create: `schemas/distillation-example-v1.json`
- Create: `schemas/dataset-readiness-v1.json`
- Create: `schemas/provenance-v1.json`
- Create: `fixtures/distillation/debug-examples.jsonl`
- Create: `fixtures/distillation/readiness.json`
- Create: `data_pipeline/pyproject.toml`
- Create: `data_pipeline/requirements.lock`
- Create: `data_pipeline/src/babel_data/contracts.py`
- Create: `data_pipeline/tests/test_contracts.py`
- Create: `training/pyproject.toml`
- Create: `training/requirements-colab.lock`
- Create: `training/src/babel_training/__init__.py`
- Create: `training/src/babel_training/config.py`
- Create: `training/tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: JSON Schema v1 files, `load_schema(name: str) -> dict`, `validate_document(schema_name: str, value: Mapping[str, object]) -> None`, and `DistillationConfig`.
- Consumes: nothing; every later task depends on these contracts.

- [ ] **Step 1: Write failing contract/config tests**

```python
def test_debug_rows_match_v1_schema():
    for row in read_jsonl(FIXTURE):
        validate_document("distillation-example-v1", row)

def test_training_defaults_are_frozen():
    cfg = DistillationConfig()
    assert cfg.model_revision == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert cfg.teacher_dimension == 100
    assert cfg.max_length == 512
    assert cfg.lambda_rel == 0.5
    assert cfg.lora_targets == ("q_proj", "v_proj")
```

- [ ] **Step 2: Run tests and verify missing modules fail**

Run: `python3 -m pytest data_pipeline/tests/test_contracts.py training/tests/test_config.py -v`

Expected: collection fails because `babel_data.contracts` and
`babel_training.config` do not exist.

- [ ] **Step 3: Add exact schemas and configuration types**

```python
@dataclass(frozen=True)
class DistillationConfig:
    model_id: str = "Qwen/Qwen3-Embedding-0.6B"
    model_revision: str = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    teacher_dimension: int = 100
    max_length: int = 512
    lambda_rel: float = 0.5
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_targets: tuple[str, ...] = ("q_proj", "v_proj")
```

The row schema requires `article_key`, `page_id`, `canonical_title`, nullable
`wikidata_id`, `lead_text`, `article_text`, exactly 100 finite numbers in
`teacher_vector`, positive `teacher_norm`, nullable `source_revision_id`,
`snapshot_date == "2016-10-01"`, split enum, and reconciliation status.

- [ ] **Step 4: Add package metadata and ignore rules**

Use Python `>=3.10,<3.13`; add pytest/jsonschema/PyArrow to the data package
and Torch/Transformers/PEFT/Accelerate/Datasets/Hub/Safetensors/NumPy/TorchData
to training. Generate resolved, hashed lock files from each `pyproject.toml`
and commit them:

```bash
python3 -m piptools compile --generate-hashes --resolver=backtracking --extra dev --output-file data_pipeline/requirements.lock data_pipeline/pyproject.toml
python3 -m piptools compile --generate-hashes --resolver=backtracking --extra dev --output-file training/requirements-colab.lock training/pyproject.toml
```

The Colab setup installs `training/requirements-colab.lock` before installing
the editable package, so the notebook never resolves a new dependency graph
mid-run. Add only these ignore entries, preserving existing content:

```gitignore
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
.ipynb_checkpoints/
data/
checkpoints/
```

- [ ] **Step 5: Run both package tests**

Run: `python3 -m pytest data_pipeline/tests training/tests -v`

Expected: all Task 1 tests pass using only the checked-in debug fixture.

- [ ] **Step 6: Commit**

```bash
git add schemas fixtures/distillation data_pipeline training .gitignore
git commit -m "feat: define distillation data and training contracts"
```

### Task 2: Verify and Acquire Authoritative Sources

**Files:**
- Create: `data_pipeline/src/babel_data/sources.py`
- Create: `data_pipeline/tests/test_sources.py`
- Create: `data_pipeline/manifests/2016-sources.json`

**Interfaces:**
- Consumes: `provenance-v1.json` from Task 1.
- Produces: `SourceSpec`, `verify_file(path, spec)`, and `download_source(spec, data_root, resume=True)`.

- [ ] **Step 1: Write checksum and resume tests**

```python
def test_verify_file_rejects_wrong_md5(tmp_path):
    path = tmp_path / "source.bin"
    path.write_bytes(b"corrupt")
    with pytest.raises(ChecksumMismatch):
        verify_file(path, SourceSpec(name="x", url="https://invalid", size=7, md5="0" * 32))

def test_download_uses_part_file_until_verified(fake_http, tmp_path):
    result = download_source(fake_http.spec, tmp_path, resume=True)
    assert result.name == fake_http.spec.filename
    assert not (tmp_path / f"{fake_http.spec.filename}.part").exists()
```

- [ ] **Step 2: Verify tests fail**

Run: `python3 -m pytest data_pipeline/tests/test_sources.py -v`

Expected: FAIL because source types are undefined.

- [ ] **Step 3: Implement streaming digest and atomic promotion**

```python
def verify_file(path: Path, spec: SourceSpec) -> None:
    if path.stat().st_size != spec.size:
        raise SizeMismatch(spec.filename)
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != spec.md5:
        raise ChecksumMismatch(spec.filename)
```

Download to `.part`, use HTTP range resume only when the server confirms the
range, verify size and MD5, then atomically rename. Never overwrite a verified
file.

- [ ] **Step 4: Encode the three approved sources exactly**

The manifest contains exactly:

```json
[
  {
    "filename": "2016-09-01_2016-09-30_en_100.zip",
    "url": "https://ndownloader.figshare.com/files/7455673",
    "size": 727429988,
    "md5": "ac70acfc41aff7a23cc9439e3bb1771f"
  },
  {
    "filename": "enwiki-20161001-pages-articles-multistream.xml.bz2",
    "url": "https://archive.org/download/enwiki-20161001/enwiki-20161001-pages-articles-multistream.xml.bz2",
    "size": 14178624372,
    "md5": "5df8e610829c336138dcb9191071b283",
    "sha1": "86ba305ecc41dafcf03ba3e67c2eacb95724d5ca"
  },
  {
    "filename": "enwiki-20161001-pages-articles-multistream-index.txt.bz2",
    "url": "https://archive.org/download/enwiki-20161001/enwiki-20161001-pages-articles-multistream-index.txt.bz2",
    "size": 185177516,
    "md5": "7c9486cde3f9c43ff4e23443dd2323f3",
    "sha1": "f13aebe90c8bea2157d826659e0320157a1978d9"
  }
]
```

- [ ] **Step 5: Test the manifest and mocked acquisition**

Run: `python3 -m pytest data_pipeline/tests/test_sources.py -v`

Expected: PASS without live network access.

- [ ] **Step 6: Commit**

```bash
git add data_pipeline/src/babel_data/sources.py data_pipeline/tests/test_sources.py data_pipeline/manifests/2016-sources.json
git commit -m "feat: add verified 2016 source acquisition"
```

### Task 3: Parse and Inventory the Navigation Teacher

**Files:**
- Create: `data_pipeline/src/babel_data/teacher.py`
- Create: `data_pipeline/tests/test_teacher.py`
- Create: `data_pipeline/tests/fixtures/teacher-small.zip`

**Interfaces:**
- Consumes: verified teacher ZIP from Task 2.
- Produces: `TeacherRecord(title: str, vector: np.ndarray)`, `iter_teacher(path)`, and `build_teacher_inventory(path, output)`.

- [ ] **Step 1: Write parser validation tests**

```python
def test_teacher_parser_requires_100_finite_values():
    records = list(iter_teacher(FIXTURE))
    assert records[0].title == "Virtual_memory"
    assert records[0].vector.shape == (100,)
    assert records[0].vector.dtype == np.float32

@pytest.mark.parametrize("line", ["Bad 1 2", "Bad " + " ".join(["nan"] * 100)])
def test_invalid_teacher_rows_are_reported(line, tmp_zip):
    with pytest.raises(InvalidTeacherVector):
        list(iter_teacher(tmp_zip(line)))
```

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m pytest data_pipeline/tests/test_teacher.py -v`

Expected: FAIL because the parser is missing.

- [ ] **Step 3: Implement header-aware streaming Word2Vec parsing**

```python
def parse_vector_line(line: str, dimension: int = 100) -> TeacherRecord:
    title, *raw = line.rstrip("\n").split(" ")
    if len(raw) != dimension:
        raise InvalidTeacherVector(title)
    vector = np.asarray(raw, dtype=np.float32)
    if not np.isfinite(vector).all():
        raise InvalidTeacherVector(title)
    return TeacherRecord(title=title, vector=vector)
```

Read directly from the ZIP member, verify the declared Word2Vec header, reject
duplicate normalized titles, and emit counts/norm statistics to JSON.

- [ ] **Step 4: Run parser tests and a fixture inventory**

Run: `python3 -m pytest data_pipeline/tests/test_teacher.py -v`

Expected: PASS and inventory count equals the fixture header.

- [ ] **Step 5: Commit**

```bash
git add data_pipeline/src/babel_data/teacher.py data_pipeline/tests/test_teacher.py data_pipeline/tests/fixtures/teacher-small.zip
git commit -m "feat: parse 2016 navigation teacher vectors"
```

### Task 4: Extract and Reconcile 2016 Wikipedia Text

**Files:**
- Create: `data_pipeline/src/babel_data/wikipedia.py`
- Create: `data_pipeline/src/babel_data/reconcile.py`
- Create: `data_pipeline/tests/test_wikipedia.py`
- Create: `data_pipeline/tests/test_reconcile.py`
- Create: `data_pipeline/tests/fixtures/enwiki-small.xml.bz2`

**Interfaces:**
- Consumes: `TeacherRecord` from Task 3 and verified XML/index from Task 2.
- Produces: `WikipediaPage`, `normalize_title`, `resolve_redirect`, `extract_lead`, and `reconcile(teacher, pages) -> ReconciliationResult`.

- [ ] **Step 1: Write title, redirect, text, and join tests**

```python
def test_redirect_resolves_to_snapshot_page_id():
    pages = fixture_pages("Virtual memory", redirect_from="Virtual_memory")
    result = reconcile([teacher("Virtual_memory")], pages)
    assert result.rows[0].page_id == pages.canonical_id

def test_fuzzy_title_is_never_silently_matched():
    result = reconcile([teacher("Virtul_memory")], fixture_pages("Virtual memory"))
    assert result.rows == []
    assert result.exclusions[0].reason == "title_not_found"

def test_creator_input_uses_title_blank_line_lead():
    page = parse_fixture_page("Lead sentence.\n\n== History ==\nLater")
    assert page.model_text == "Virtual memory\n\nLead sentence."
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/test_wikipedia.py data_pipeline/tests/test_reconcile.py -v`

Expected: FAIL because extraction and reconciliation modules are absent.

- [ ] **Step 3: Implement streaming page and redirect extraction**

```python
@dataclass(frozen=True)
class WikipediaPage:
    page_id: int
    canonical_title: str
    revision_id: int | None
    article_text: str
    lead_text: str
    redirect_target: str | None
```

Stream namespace-zero pages, normalize Unicode NFC and underscores/spaces,
strip unsupported wikitext deterministically, follow redirect chains with a
visited set, and report cycles/missing targets.

- [ ] **Step 4: Implement strict identity reconciliation**

```python
def split_for(article_key: str) -> str:
    bucket = int.from_bytes(hashlib.sha256(article_key.encode()).digest()[:8], "big") % 100
    return "train" if bucket < 98 else "validation" if bucket == 98 else "test"
```

Join exact normalized teacher title to page/redirect identity, attach the 100d
vector, validate nonempty lead text, and emit explicit exclusion rows for every
failure class. Do not call a live Wikipedia API.

- [ ] **Step 5: Run reconciliation tests**

Run: `python3 -m pytest data_pipeline/tests/test_wikipedia.py data_pipeline/tests/test_reconcile.py -v`

Expected: PASS, including cycles, ambiguous targets, empty text, and split
stability.

- [ ] **Step 6: Commit**

```bash
git add data_pipeline/src/babel_data/wikipedia.py data_pipeline/src/babel_data/reconcile.py data_pipeline/tests
git commit -m "feat: reconcile 2016 Wikipedia text and teacher vectors"
```

### Task 5: Build Pilot Shards and Publish Incrementally

**Files:**
- Create: `data_pipeline/src/babel_data/shard.py`
- Create: `data_pipeline/src/babel_data/hub.py`
- Create: `data_pipeline/src/babel_data/cli.py`
- Create: `data_pipeline/tests/test_shard.py`
- Create: `data_pipeline/tests/test_hub.py`

**Interfaces:**
- Consumes: reconciled rows from Task 4 and v1 schemas from Task 1.
- Produces: `write_shards`, `build_readiness`, `publish_verified_shards`, and CLI commands `prepare-2016`, `publish-2016`, `verify-remote`.

- [ ] **Step 1: Write deterministic sharding/readiness tests**

```python
def test_pilot_sample_is_hash_selected_not_input_order(rows, tmp_path):
    first = write_shards(rows, tmp_path, pilot_size=8)
    second = write_shards(list(reversed(rows)), tmp_path / "again", pilot_size=8)
    assert first.pilot_article_keys == second.pilot_article_keys

def test_delete_is_blocked_until_remote_verification(state):
    assert state.can_delete_local is False
    state.mark_remote_verified(commit_sha="a" * 40)
    assert state.can_delete_local is True
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/test_shard.py data_pipeline/tests/test_hub.py -v`

Expected: FAIL because sharder and publisher are missing.

- [ ] **Step 3: Implement Parquet output and readiness transitions**

Write separate train/validation/test paths, target 256–512 MB shards, fixed
PyArrow schema, SHA-256 per shard, and readiness transitions
`building -> pilot_ready -> complete`. Reject state regression and replacement
of an already published checksum.

- [ ] **Step 4: Implement private-Hub staging and remote verification**

```python
def publish_verified_shards(api: HfApi, repo_id: str, files: list[Path], token: str) -> str:
    for path in files:
        api.upload_file(path_or_fileobj=path, path_in_repo=hub_path(path), repo_id=repo_id, repo_type="dataset", token=token)
    return api.dataset_info(repo_id, token=token).sha
```

After upload, load the exact returned SHA with `load_dataset(...,
revision=sha, streaming=True)`, validate at least one row from each available
split, compare manifests, then record `remote_verified=true`. The CLI option
`--revision-out PATH` atomically writes only the accepted 40-character SHA so
later commands never scrape human-readable output.

- [ ] **Step 5: Run offline tests and a token-gated remote smoke test**

Run: `python3 -m pytest data_pipeline/tests/test_shard.py data_pipeline/tests/test_hub.py -v`

If the host Python has unrelated globally installed pytest plugins, isolate
this check with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; the data-pipeline tests do
not require third-party pytest plugins.

Run when `HF_TOKEN` is present:
`babel-data verify-remote --repo dhelmy990/babel-wikipedia-experiment --revision "$BABEL_PILOT_SHA"`

Expected: fixture tests pass; remote command prints pinned SHA and validated row
counts without downloading a full shard. Set `BABEL_PILOT_SHA` to the exact
40-character SHA written by `publish-2016 --revision-out` in Step 4.

- [ ] **Step 6: Commit**

```bash
git add data_pipeline/src/babel_data/shard.py data_pipeline/src/babel_data/hub.py data_pipeline/src/babel_data/cli.py data_pipeline/tests
git commit -m "feat: publish rolling 2016 distillation shards"
```

### Task 6: Implement Pinned Remote Data Loading and Collation

**Files:**
- Create: `training/src/babel_training/data.py`
- Create: `training/src/babel_training/collator.py`
- Create: `training/src/babel_training/hub.py`
- Create: `training/tests/test_data.py`
- Create: `training/tests/test_collator.py`
- Create: `training/tests/test_hub.py`

**Interfaces:**
- Consumes: v1 row/readiness schemas from Task 1.
- Produces: `resolve_dataset_revision`, `load_distillation_stream`, `DistillationCollator`, `export_distilled_artifact`, `publish_model_artifact`, and stateful loader state.

- [ ] **Step 1: Write revision, hidden-config, and formatting tests**

```python
def test_revision_is_resolved_once(fake_hub):
    resolved = resolve_dataset_revision(fake_hub, "main")
    assert resolved == fake_hub.commit_sha

def test_distillation_loader_rejects_hidden_config():
    with pytest.raises(ForbiddenDatasetConfiguration):
        load_distillation_stream(config_name="simulator_2026_06_hidden", revision="a" * 40)

def test_collator_formats_title_and_lead(tokenizer):
    batch = DistillationCollator(tokenizer, max_length=512)([fixture_row()])
    assert tokenizer.last_text == "Virtual memory\n\nLead sentence."
    assert batch["teacher_vector"].shape == (1, 100)

def test_export_manifest_pins_model_and_dataset_revisions(tmp_path):
    manifest = export_distilled_artifact(fixture_model(), fixture_run(), tmp_path)
    assert manifest.model_revision == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert len(manifest.dataset_commit_sha) == 40
    assert manifest.projection_sha256 and manifest.adapter_sha256
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest training/tests/test_data.py training/tests/test_collator.py training/tests/test_hub.py -v`

Expected: FAIL because loader and collator are absent.

- [ ] **Step 3: Implement pinned streaming and readiness gates**

```python
def load_distillation_stream(repo_id: str, revision: str, split: str, token: str):
    if len(revision) != 40:
        raise UnpinnedDatasetRevision(revision)
    return load_dataset(repo_id, "distillation_2016", split=split, revision=revision, token=token, streaming=True)
```

Read readiness first, reject unavailable states, remove the test split from the
training API, seed buffered shuffle, and expose iterable/stateful loader state.

- [ ] **Step 4: Implement left-padded collation**

Set `tokenizer.padding_side = "left"`, call the pinned tokenizer with
truncation, validate finite `(batch, 100)` teacher tensors, and preserve article
keys for validation/reporting.

- [ ] **Step 5: Implement model artifact export/publication**

Export projection and LoRA Safetensors, adapter config, tokenizer/model revision,
dataset commit/readiness, training config, validation report, and per-file
SHA-256 into an atomic artifact directory. Upload to an immutable revision path
in private `dhelmy990/babel-qwen-navigation-2016`, reload its manifest at the
returned commit SHA, and reject replacement of an existing artifact ID.

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest training/tests/test_data.py training/tests/test_collator.py training/tests/test_hub.py -v`

Expected: PASS with fake Hub and tokenizer; no network.

- [ ] **Step 7: Commit**

```bash
git add training/src/babel_training/data.py training/src/babel_training/collator.py training/src/babel_training/hub.py training/tests
git commit -m "feat: stream pinned distillation data"
```

### Task 7: Implement the Qwen Student and Losses

**Files:**
- Create: `training/src/babel_training/pooling.py`
- Create: `training/src/babel_training/model.py`
- Create: `training/src/babel_training/losses.py`
- Create: `training/tests/test_pooling.py`
- Create: `training/tests/test_model.py`
- Create: `training/tests/test_losses.py`

**Interfaces:**
- Consumes: `DistillationConfig` and collated tensors.
- Produces: `last_token_pool`, `DistilledQwenEncoder`, `distillation_loss`, and `LossBreakdown`.

- [ ] **Step 1: Write shape, gradient, and finite-loss tests**

```python
def test_projection_is_100d_and_normalized(tiny_backbone):
    model = DistilledQwenEncoder(tiny_backbone, hidden_size=1024, teacher_dimension=100)
    output = model(**batch())
    assert output.shape == (2, 100)
    torch.testing.assert_close(output.norm(dim=-1), torch.ones(2))

def test_only_lora_and_projection_receive_gradients(qwen_student):
    distillation_loss(qwen_student(**batch()), teacher()).total.backward()
    trainable = {n for n, p in qwen_student.named_parameters() if p.grad is not None}
    assert trainable and all("lora_" in n or "projection" in n for n in trainable)

def test_pooling_finds_last_real_token_with_left_padding():
    hidden = torch.arange(2 * 4).reshape(2, 4, 1).float()
    mask = torch.tensor([[0, 0, 1, 1], [0, 1, 1, 1]])
    torch.testing.assert_close(last_token_pool(hidden, mask).squeeze(-1), torch.tensor([3.0, 7.0]))
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest training/tests/test_pooling.py training/tests/test_model.py training/tests/test_losses.py -v`

Expected: FAIL because model modules are missing.

- [ ] **Step 3: Implement official last-token pooling**

```python
def last_token_pool(hidden: Tensor, attention_mask: Tensor) -> Tensor:
    positions = torch.arange(attention_mask.shape[1], device=hidden.device)
    positions = positions.unsqueeze(0).expand_as(attention_mask)
    sequence_lengths = positions.masked_fill(attention_mask == 0, -1).max(dim=1).values
    if torch.any(sequence_lengths < 0):
        raise ValueError("attention_mask contains an empty sequence")
    rows = torch.arange(hidden.shape[0], device=hidden.device)
    return hidden[rows, sequence_lengths]
```

This finds the actual final non-padding position for left- or right-padded
batches. Test variable sequence lengths, both padding sides, and an all-padding
row.

- [ ] **Step 4: Compose pinned Qwen, LoRA, and projection**

Load with the exact revision, set `use_cache=False`, attach PEFT LoRA to
`q_proj`/`v_proj`, freeze the base, create `nn.Linear(1024, 100)`, and normalize
the FP32 projection output. Call the backbone's input-gradient-enabling method
before activating gradient checkpointing so frozen embeddings do not sever the
LoRA graph. Fail if trainable-parameter names violate the gate.

- [ ] **Step 5: Implement the two losses in FP32**

```python
def distillation_loss(student: Tensor, teacher: Tensor, lambda_rel: float = 0.5) -> LossBreakdown:
    s = F.normalize(student.float(), dim=-1)
    t = F.normalize(teacher.float(), dim=-1)
    vector = (1.0 - (s * t).sum(dim=-1)).mean()
    relational = F.mse_loss(s @ s.T, t @ t.T)
    total = vector + lambda_rel * relational
    if not torch.isfinite(total):
        raise NonFiniteLoss()
    return LossBreakdown(total, vector, relational)
```

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m pytest training/tests/test_pooling.py training/tests/test_model.py training/tests/test_losses.py -v`

```bash
git add training/src/babel_training/pooling.py training/src/babel_training/model.py training/src/babel_training/losses.py training/tests
git commit -m "feat: add distilled Qwen student objective"
```

### Task 8: Add Accelerate Training and Complete Checkpoints

**Files:**
- Create: `training/src/babel_training/trainer.py`
- Create: `training/src/babel_training/checkpointing.py`
- Create: `training/tests/test_trainer.py`
- Create: `training/tests/test_checkpointing.py`

**Interfaces:**
- Consumes: loader/collator from Task 6 and model/loss from Task 7.
- Produces: `DistillationTrainer.train`, `save_checkpoint`, `load_checkpoint`, `CheckpointManifest`.

- [ ] **Step 1: Write overfit and round-trip tests**

```python
def test_tiny_batch_overfits(trainer):
    losses = trainer.train(max_steps=20, repeat_fixture=True)
    assert losses[-1] < losses[0]

def test_checkpoint_restores_step_rng_and_loader(trainer, tmp_path):
    trainer.train(max_steps=3)
    expected = trainer.validation_fingerprint()
    trainer.save(tmp_path)
    restored = trainer.reload(tmp_path)
    assert restored.global_step == 3
    assert restored.validation_fingerprint() == pytest.approx(expected, rel=1e-6, abs=1e-6)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest training/tests/test_trainer.py training/tests/test_checkpointing.py -v`

Expected: FAIL because trainer/checkpoint modules are absent.

- [ ] **Step 3: Implement the Accelerate loop**

Use gradient accumulation, autocast, gradient checkpointing, AdamW, scheduler,
finite gradient-norm checks, configurable checkpoint intervals, and
`max_runtime_minutes` checked after every optimizer step.

- [ ] **Step 4: Save all restart state atomically**

Write into `<checkpoint>.partial`, call `Accelerator.save_state`, serialize
`CheckpointManifest` with revisions/config/metrics/data-loader state, fsync,
then rename. Register the stateful data loader with Accelerate. Reject resume
when model revision, dataset SHA, or schema version differs.

- [ ] **Step 5: Run trainer/checkpoint tests**

Run: `python3 -m pytest training/tests/test_trainer.py training/tests/test_checkpointing.py -v`

Expected: PASS, including an interrupted partial directory that is ignored.

- [ ] **Step 6: Commit**

```bash
git add training/src/babel_training/trainer.py training/src/babel_training/checkpointing.py training/tests
git commit -m "feat: add restartable distillation training"
```

### Task 9: Implement Held-Out Validation

**Files:**
- Create: `training/src/babel_training/validation.py`
- Create: `training/tests/test_validation.py`
- Create: `fixtures/distillation/expected-neighbors.json`

**Interfaces:**
- Consumes: student model and validation stream.
- Produces: `validate_embeddings`, `recall_at_k`, `ndcg_at_k`, and JSON report.

- [ ] **Step 1: Write exact metric tests**

```python
def test_recall_excludes_self_and_matches_teacher_topk():
    assert recall_at_k(student_neighbors=[2, 3], teacher_neighbors=[2, 4], k=2) == 0.5

def test_validation_reports_invalid_vectors():
    report = validate_embeddings(article_keys=["a"], student=np.array([[np.nan] * 100]), teacher=np.ones((1, 100)))
    assert report.invalid_vector_count == 1
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest training/tests/test_validation.py -v`

Expected: FAIL because validation functions are missing.

- [ ] **Step 3: Implement exact pilot cosine search and metrics**

Normalize FP32 matrices, compute chunked cosine similarity, mask the diagonal,
derive teacher and student top-50 lists, compute Recall@10/50 and NDCG@10/50
using `(teacher_cosine + 1) / 2` relevance, and calculate mean paired cosine.

- [ ] **Step 4: Emit structured reports**

Include dataset/model revisions, pool size, metrics, invalid count, norm
statistics, and fixed article/neighbor examples. Sort examples by article key
for deterministic diffs.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m pytest training/tests/test_validation.py -v`

```bash
git add training/src/babel_training/validation.py training/tests/test_validation.py fixtures/distillation/expected-neighbors.json
git commit -m "feat: validate distilled neighborhood recovery"
```

### Task 10: Build and Prove the Colab Handoff

**Files:**
- Create: `training/notebooks/train_distillation_colab.ipynb`
- Create: `docs/runbooks/colab-distillation-pilot.md`
- Create: `training/tests/test_notebook_contract.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: all Tasks 1–9 and a remotely verified pilot commit SHA.
- Produces: user-runnable notebook, smoke checkpoint, runbook, and acceptance report.

- [ ] **Step 1: Write a notebook contract test**

```python
def test_notebook_has_ordered_handoff_cells():
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    tags = [tag for cell in notebook.cells for tag in cell.metadata.get("tags", [])]
    assert tags == EXPECTED_TAGS
    assert "hf-token-secret" in tags
    assert "resolve-and-pin-revision" in tags
    assert "resume-checkpoint" in tags
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest training/tests/test_notebook_contract.py -v`

Expected: FAIL because the notebook is absent.

- [ ] **Step 3: Create thin orchestration cells**

Cells, in order: environment check; package install; Colab Secrets token;
optional Drive mount; configuration form; resolve/pin revision; remote row
preview; GPU precision choice; model construction; one-batch gate; train;
validate; save; reload; resume; export. No model/loss implementation is copied
into notebook cells.

- [ ] **Step 4: Write the runbook**

Document exact clicks for GPU selection, secret name `HF_TOKEN`, Drive mount,
debug/pilot choices, expected first-batch output, checkpoint paths, safe stop,
resume, error diagnosis, and the revisions/checksums to report back.

- [ ] **Step 5: Run the complete local gate**

Run: `python3 -m pytest data_pipeline/tests training/tests -v`

Run: `python3 -m compileall data_pipeline/src training/src`

Expected: all tests pass; notebook parses; no live token required for default
tests.

- [ ] **Step 6: Run the real pilot acceptance**

With `HF_TOKEN` present, resolve a `pilot_ready` commit, load one remote shard,
run actual pinned Qwen forward/backward, save a checkpoint, create a new trainer
process, reload, validate within tolerance, resume one step, and record the
evidence in the runbook's acceptance appendix.

- [ ] **Step 7: Commit**

```bash
git add training/notebooks training/tests/test_notebook_contract.py docs/runbooks/colab-distillation-pilot.md README.md
git commit -m "feat: deliver Colab distillation pilot"
```

## Slice Acceptance Gate

- [ ] One private-Hub pilot shard loads from a pinned commit.
- [ ] Actual Qwen produces exactly 100-dimensional normalized outputs.
- [ ] Forward/backward losses are finite and tiny-overfit loss decreases.
- [ ] Only LoRA and projection parameters receive gradients.
- [ ] A complete checkpoint saves, reloads, validates, and resumes.
- [ ] The Colab notebook runs top-to-bottom with T4-safe defaults.
- [ ] The runbook lets the user repeat the handoff without repository knowledge.
- [ ] No hidden 2026 configuration can be opened by the distillation loader.

## Orchestrator Context for the Next Slice

Slice 2 begins while the user runs the pilot in Colab. The orchestrator should
review pilot failures only for defects in contracts, data quality, memory use,
or checkpointing; do not block continued reconciliation on training duration.
Before launching Slice 2 agents, freeze the v1 schemas, the pilot Hub commit,
the Qwen revision, and the accepted exclusion taxonomy. Slice 2 may add shards
and monthly configurations, but it must not rewrite published pilot shards or
change the meaning of existing fields. Its first review should compare actual
pilot match rates, text lengths, GPU memory, and loss behavior against the
assumptions used to size full 2016 shards.
