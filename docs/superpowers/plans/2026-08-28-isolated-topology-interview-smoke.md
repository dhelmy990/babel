# Isolated Topology Interview Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add, deploy, and run a fail-closed, explicitly non-formal three-condition `same_host_isolated` smoke that maps its fresh internal conditions 1–3 to formal matrix positions 7–9 and preserves the evidence under `results/28-august-morning-run/isolated-smoke/`.

**Architecture:** Extend the existing representative-rerun path with one `isolated-smoke` selector and `representative_isolated_smoke` evidence scope. The new trial reuses the failed formal trial's immutable frozen workload and already-imported 10,000-vector population, executes only the isolated serving/training/activation trio, and flows through the existing worker, backend, export, and representative-publication boundaries. The implementation must not resume or mutate the failed formal trial and must never rebuild the population.

**Tech Stack:** Python 3.12, pytest, FastAPI, PostgreSQL/pgvector, Kafka, C++20/Drogon/Catch2, Node.js test runner, Docker Compose, GitHub Actions, Google Compute Engine L4, Artifact Registry.

## Global Constraints

- Work only in `/home/dhelmy990/.config/superpowers/worktrees/babel/gcp-matrix-integration-e096-20260828` from committed base `f0ff0fb`.
- Preserve failed formal trial `dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28` and all evidence beneath `results/28-august-morning-run/formal-partial-failed/`.
- Use fresh UUIDs for the representative trial and every condition run; never resume the failed formal matrix.
- Reuse the existing imported 10,000 embeddings and frozen workload unchanged; do not export, import, recreate, or re-encode the population.
- Require `BABEL_ONLINE_ALLOW_POPULATION_BUILD=false`; hard-fail if the guard or immutable population/workload identity differs.
- Use evidence scope `representative_isolated_smoke`, matrix selector `isolated-smoke`, and exactly three `same_host_isolated` conditions.
- Use 5-second warmup, 25-second measurement, 5 RPS, 50 concurrent users, micro-batch 8, synchronization every 10 steps, pgvector retrieval, and 5,000 ms safety threshold.
- CUDA remains limited to Qwen serving and encoding new recommendation requests. The online head, Kafka, PostgreSQL/pgvector, checkpoints, controller, import/export, and index work remain on CPU.
- Any request error fails the condition and run closed. There is no automatic retry, topology substitution, or fallback to re-encoding.
- All artifacts and UI labels must say representative/interview-grade and non-formal. Internal condition indices 1–3 map to formal positions 7–9 only for comparison labels.
- Every code task uses red-green-refactor, then receives a specification review and a code-quality review before the next task begins.
- Do not mutate GCP until every local test and final diff audit in Task 5 passes.
- Use only project `chloe-tutoring-bot`, VM `babel-gpu-serving`, zone `asia-southeast1-b`, and IAP access. Do not provision another GPU.
- Stop the VM after successful evidence retrieval or a terminal failure.

---

## File Map

- `online/src/babel_online/runtime/performance_rerun.py`: declares the new immutable evidence scope and accepts it during frozen-input validation.
- `online/src/babel_online/runtime/performance_worker.py`: validates the isolated representative contract and maps the worker selector to the scope.
- `online/src/babel_online/runtime/database.py`: creates exactly the three isolated conditions while retaining fresh IDs and the source population binding.
- `online/src/babel_online/runtime/cli.py`: exposes `isolated-smoke` through the operator CLI and reports the correct condition count.
- `online/src/babel_online/runtime/performance_export.py`: accepts only a completed isolated trio and records formal comparison positions 7–9 without making a formal claim.
- `benchmark/src/babel_benchmark/representative_publication.py`: validates and packages either the existing six-condition representative run or the new isolated trio using a scope-specific closed inventory.
- `backend/src/application/experiment_service.cpp`: accepts the new matrix selector and rejects every other unknown value.
- `backend/admin/index.html`: offers the isolated selector.
- `backend/admin/scalability-dashboard.js`: labels the scope as a three-condition non-formal isolated smoke.
- `online/tests/runtime/test_performance_rerun.py`, `test_database.py`, `test_performance_worker.py`, and `test_performance_rerun_cli.py`: Python rerun contract coverage.
- `online/tests/runtime/test_performance_export.py`: exact isolated export acceptance and drift rejection.
- `benchmark/tests/test_representative_publication.py`: closed three-condition bundle, formal-position mapping, lag, and inventory tests.
- `backend/tests/unit/experiment_service_test.cpp` and `backend/tests/integration/experiment_http_contract_test.cpp`: backend selector forwarding and rejection tests.
- `tests/js/scalability-dashboard.test.js`: selector request and non-formal label tests.
- `docs/runbooks/scaled-experiment.md`: exact isolated-smoke execution, export, evidence-copy, and stop commands.

---

### Task 1: Model the isolated representative scope and condition trio

**Files:**

- Modify: `online/src/babel_online/runtime/performance_rerun.py`
- Modify: `online/src/babel_online/runtime/performance_worker.py:135-169`
- Modify: `online/src/babel_online/runtime/database.py:491-660`
- Test: `online/tests/runtime/test_performance_rerun.py`
- Test: `online/tests/runtime/test_database.py`

**Interfaces:**

- Consumes: `RepresentativeRerunBinding.evidence_scope: str`, existing frozen population and workload identity checks, and `RuntimeDatabase.create_representative_performance_rerun(binding)`.
- Produces: `ISOLATED_SMOKE_SCOPE = "representative_isolated_smoke"`; a runnable contract containing exactly `same_host_isolated × {(false,false),(true,false),(true,true)}`; three fresh condition IDs `uuid5(rerun_id, f"condition:{index}")`.

- [ ] **Step 1: Write failing scope-validation and runnable-contract tests**

Add the new constant import and tests that use the existing `_trial()`, `_population_manifest()`, and `replace()` fixtures:

```python
from babel_online.runtime.performance_rerun import ISOLATED_SMOKE_SCOPE


def test_isolated_smoke_scope_reuses_exact_frozen_inputs(tmp_path: Path):
    source = _ready_source(tmp_path / "population")
    binding = validate_representative_reuse(
        source=source,
        manifest=_population_manifest(),
        workload=FrozenWorkload(
            path=tmp_path,
            identity=("1" * 64,) * 6,
        ),
        rerun_id=RERUN_ID,
        evidence_scope=ISOLATED_SMOKE_SCOPE,
    )
    assert binding.evidence_scope == ISOLATED_SMOKE_SCOPE
    assert binding.population_run_id == POPULATION_RUN_ID
    assert binding.request_limit == 150


def test_isolated_smoke_is_exactly_the_runnable_isolated_trio(tmp_path: Path):
    conditions = tuple(
        PerformanceCondition(
            id=uuid5(RERUN_ID, f"condition:{index}"),
            condition_index=index,
            topology="same_host_isolated",
            training_enabled=mode >= 1,
            activation_enabled=mode == 2,
            run_id=None,
            status="pending",
        )
        for index, mode in enumerate(range(3), start=1)
    )
    trial = replace(
        _trial(),
        id=RERUN_ID,
        evidence_scope=ISOLATED_SMOKE_SCOPE,
        source_trial_id=SOURCE_ID,
        source_workload_path=str(tmp_path / "workload"),
        source_workload_identity=("1" * 64,) * 6,
        replay_request_limit=150,
        population_ready=True,
        population_run_id=POPULATION_RUN_ID,
        population_bundle_path=str(tmp_path / "population"),
        population_manifest_sha256="2" * 64,
        conditions=conditions,
    )
    trial.validate_runnable_contract()
    with pytest.raises(ValueError, match="formal"):
        trial.validate_formal_defaults()
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_rerun.py \
  -k 'isolated_smoke'
```

Expected: collection or assertion failure because `ISOLATED_SMOKE_SCOPE` and its runnable topology are not supported.

- [ ] **Step 3: Add the constant and minimal validation mapping**

In `performance_rerun.py`, add and export the constant:

```python
ISOLATED_SMOKE_SCOPE = "representative_isolated_smoke"

SUPPORTED_REPRESENTATIVE_SCOPES = {
    REPRESENTATIVE_SCOPE,
    SPLIT_SMOKE_SCOPE,
    ISOLATED_SMOKE_SCOPE,
}
```

Use `SUPPORTED_REPRESENTATIVE_SCOPES` in `validate_representative_reuse()` and include both names in `__all__`. In `PerformanceExperiment.validate_runnable_contract()`, select topologies without changing formal handling:

```python
representative_topologies = {
    "representative_same_process_vs_split": ("same_process", "same_host_split"),
    "representative_split_smoke": ("same_host_split",),
    "representative_isolated_smoke": ("same_host_isolated",),
}
try:
    topologies = representative_topologies[self.evidence_scope]
except KeyError as error:
    raise ValueError("saved trial has an unsupported evidence scope") from error
```

Keep the existing creator/population/workload/replay validations and change the terminal error text to `saved representative trial does not contain its exact topology matrix`.

- [ ] **Step 4: Run the scope tests and confirm green**

Run:

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_rerun.py
```

Expected: all tests pass, including the existing 2×3 and split-smoke cases.

- [ ] **Step 5: Write the failing database condition-construction test**

Add a second database test using the existing recording cursor helpers:

```python
def test_database_creates_only_the_isolated_smoke_trio(tmp_path: Path) -> None:
    from babel_online.runtime.performance_rerun import (
        ISOLATED_SMOKE_SCOPE,
        RepresentativeRerunBinding,
    )

    binding = RepresentativeRerunBinding(
        rerun_id=UUID(int=202),
        source_trial_id=UUID(int=201),
        evidence_scope=ISOLATED_SMOKE_SCOPE,
        population_run_id=UUID(int=203),
        population_path=(tmp_path / "population").resolve(),
        population_manifest_sha256="a" * 64,
        workload_path=(tmp_path / "workload").resolve(),
        workload_identity=("b" * 64,) * 6,
        warmup_seconds=5,
        duration_seconds=25,
        target_rps=5.0,
        request_limit=150,
    )
    cursor = RecordingCursor(rows=[(binding.rerun_id,)])
    database = RuntimeDatabase(
        "unused", connect=lambda: RecordingConnection(cursor)
    )

    database.create_representative_performance_rerun(binding)

    rows = cursor.queries[1][1]
    assert [row[2] for row in rows] == [1, 2, 3]
    assert [row[3] for row in rows] == ["same_host_isolated"] * 3
    assert [(row[4], row[5]) for row in rows] == [
        (False, False), (True, False), (True, True)
    ]
```

- [ ] **Step 6: Run the database test and confirm it fails on split topology**

Run:

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_database.py \
  -k 'isolated_smoke_trio'
```

Expected: FAIL because the fallback currently creates `same_host_split` conditions.

- [ ] **Step 7: Implement the explicit database scope-to-topology mapping**

Replace the binary conditional with an exhaustive local mapping:

```python
topologies_by_scope = {
    "representative_same_process_vs_split": ("same_process", "same_host_split"),
    "representative_split_smoke": ("same_host_split",),
    "representative_isolated_smoke": ("same_host_isolated",),
}
try:
    topologies = topologies_by_scope[binding.evidence_scope]
except KeyError as error:
    raise ValueError("representative rerun evidence scope is unsupported") from error
```

Leave source locking, copied population fields, condition UUID derivation, launch hashes, and `operator_approved=false` unchanged.

- [ ] **Step 8: Run both affected suites**

Run:

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_rerun.py \
  online/tests/runtime/test_database.py
```

Expected: PASS.

- [ ] **Step 9: Commit the reviewed domain/database slice**

```bash
git add \
  online/src/babel_online/runtime/performance_rerun.py \
  online/src/babel_online/runtime/performance_worker.py \
  online/src/babel_online/runtime/database.py \
  online/tests/runtime/test_performance_rerun.py \
  online/tests/runtime/test_database.py
git commit -m "feat: model isolated representative smoke"
```

---

### Task 2: Route `isolated-smoke` through the Python worker and CLI

**Files:**

- Modify: `online/src/babel_online/runtime/performance_worker.py:1525-1556`
- Modify: `online/src/babel_online/runtime/cli.py:648-696`
- Test: `online/tests/runtime/test_performance_worker.py:1310-1365`
- Test: `online/tests/runtime/test_performance_rerun_cli.py`

**Interfaces:**

- Consumes: `ISOLATED_SMOKE_SCOPE` from Task 1 and `PerformanceJobManager.prepare_representative_rerun(...)`.
- Produces: worker/CLI matrix value `isolated-smoke` mapped exactly to `representative_isolated_smoke`; a CLI receipt with `conditionCount: 3` and `formalPerformanceClaim: false`.

- [ ] **Step 1: Write failing worker routing tests**

Parameterize the existing control-app rerun assertion so each selector reaches the manager unchanged:

```python
@pytest.mark.parametrize("matrix", ["2x3", "split-smoke", "isolated-smoke"])
def test_control_app_forwards_supported_representative_matrix(
    tmp_path: Path, matrix: str
):
    manager = PerformanceJobManager(
        database=FakeDatabase(),
        output_root=tmp_path,
        population_builder=lambda *_args: (_population_manifest(), tmp_path),
        workload_freezer=lambda *_args: None,
        condition_runner=lambda *_args: None,
    )
    prepared = []
    manager.prepare_representative_rerun = lambda **values: prepared.append(values)
    client = TestClient(create_performance_control_app(manager, token="a" * 64))
    rerun_id = UUID("dddddddd-dddd-5ddd-8ddd-dddddddddddd")
    response = client.post(
        f"/v1/performance/{EXPERIMENT_ID}/prepare-rerun/{rerun_id}",
        params={
            "matrix": matrix,
            "warmup_seconds": 5,
            "duration_seconds": 25,
            "target_rps": 5.0,
        },
        headers={"X-Babel-Worker-Token": "a" * 64},
    )
    assert response.status_code == 202
    assert prepared[0]["matrix"] == matrix
```

Add a manager test that patches `create_representative_rerun` and asserts `evidence_scope == ISOLATED_SMOKE_SCOPE` for `matrix="isolated-smoke"`.

- [ ] **Step 2: Run the worker tests and confirm red**

Run:

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_worker.py \
  -k 'representative_matrix or isolated_smoke'
```

Expected: HTTP 409 or `ValueError("representative rerun matrix is unsupported")` for `isolated-smoke`.

- [ ] **Step 3: Implement an exhaustive selector mapping in the manager**

Replace the two-way conditional in `prepare_representative_rerun()`:

```python
from .performance_rerun import (
    ISOLATED_SMOKE_SCOPE,
    REPRESENTATIVE_SCOPE,
    SPLIT_SMOKE_SCOPE,
    create_representative_rerun,
)

scopes_by_matrix = {
    "2x3": REPRESENTATIVE_SCOPE,
    "split-smoke": SPLIT_SMOKE_SCOPE,
    "isolated-smoke": ISOLATED_SMOKE_SCOPE,
}
try:
    evidence_scope = scopes_by_matrix[matrix]
except KeyError as error:
    raise ValueError("representative rerun matrix is unsupported") from error
```

Pass `evidence_scope=evidence_scope` into `create_representative_rerun()` and keep the automatic `start(rerun_id)` preparation behavior unchanged.

- [ ] **Step 4: Run the worker tests and confirm green**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Write the failing isolated CLI receipt test**

Add:

```python
def test_cli_labels_isolated_smoke_as_three_condition_non_formal(
    monkeypatch, tmp_path: Path, capsys
):
    source_id = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    rerun_id = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
    calls = []
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(runtime_cli, "RuntimeDatabase", lambda _dsn: object())
    monkeypatch.setattr(
        "babel_online.runtime.performance_rerun.create_representative_rerun",
        lambda **values: calls.append(values) or SimpleNamespace(
            rerun_id=rerun_id,
            source_trial_id=source_id,
            evidence_scope="representative_isolated_smoke",
            population_manifest_sha256="a" * 64,
            workload_identity=("b" * 64,) * 6,
            request_limit=150,
            warmup_seconds=5,
            duration_seconds=25,
            target_rps=5.0,
        ),
    )
    assert runtime_cli.main([
        "performance-rerun-create",
        "--source-trial-id", str(source_id),
        "--state-root", str(tmp_path),
        "--nonce", "isolated-interview-smoke",
        "--matrix", "isolated-smoke",
    ]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert calls[0]["evidence_scope"] == "representative_isolated_smoke"
    assert receipt["conditionCount"] == 3
    assert receipt["formalPerformanceClaim"] is False
```

- [ ] **Step 6: Run the CLI test and confirm argparse rejects the selector**

Run:

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_rerun_cli.py \
  -k 'isolated_smoke'
```

Expected: `SystemExit` from the current matrix choices.

- [ ] **Step 7: Extend CLI choices and use the same explicit mapping**

Import `ISOLATED_SMOKE_SCOPE`, set:

```python
parser.add_argument(
    "--matrix",
    choices=("2x3", "split-smoke", "isolated-smoke"),
    default="2x3",
)
scope = {
    "2x3": REPRESENTATIVE_SCOPE,
    "split-smoke": SPLIT_SMOKE_SCOPE,
    "isolated-smoke": ISOLATED_SMOKE_SCOPE,
}[arguments.matrix]
condition_count = 6 if receipt.evidence_scope == REPRESENTATIVE_SCOPE else 3
```

Use `condition_count` in the JSON receipt.

- [ ] **Step 8: Run both Python interface suites**

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_worker.py \
  online/tests/runtime/test_performance_rerun_cli.py
```

Expected: PASS.

- [ ] **Step 9: Commit the reviewed Python interface slice**

```bash
git add \
  online/src/babel_online/runtime/performance_worker.py \
  online/src/babel_online/runtime/cli.py \
  online/tests/runtime/test_performance_worker.py \
  online/tests/runtime/test_performance_rerun_cli.py
git commit -m "feat: route isolated smoke through worker"
```

---

### Task 3: Expose the selector through the backend and dashboard

**Files:**

- Modify: `backend/src/application/experiment_service.cpp:337-355`
- Modify: `backend/admin/index.html:181-186`
- Modify: `backend/admin/scalability-dashboard.js:99-109`
- Test: `backend/tests/unit/experiment_service_test.cpp:577-610`
- Test: `backend/tests/integration/experiment_http_contract_test.cpp:495-535`
- Test: `tests/js/scalability-dashboard.test.js`

**Interfaces:**

- Consumes: `PerformanceRerunRequest.matrix: std::string`, the existing generic worker HTTP client, and the dashboard rerun mutation body.
- Produces: accepted backend matrix `isolated-smoke`; unchanged forwarding to `/prepare-rerun/...?...`; UI option value `isolated-smoke`; label `representative · non-formal isolated smoke` with condition count 3.

- [ ] **Step 1: Add failing service and HTTP contract cases**

Extend the existing successful preparation tests with an `isolated-smoke` request and retain an unknown-selector rejection assertion:

```cpp
const auto isolated = service.preparePerformanceRerun(
    source_id,
    PerformanceRerunRequest{
        .rerun_id = "55555555-5555-4555-8555-555555555555",
        .matrix = "isolated-smoke",
        .warmup_seconds = 5,
        .duration_seconds = 25,
        .target_rps = 5,
    });
REQUIRE(isolated.has_value());
CHECK(performance_worker.prepared_request->matrix == "isolated-smoke");
```

For the controller contract, send JSON containing `"matrix":"isolated-smoke"`, expect HTTP 201, and assert the fake worker saw the same string. Keep the existing malformed request test for any value outside `2x3`, `split-smoke`, and `isolated-smoke`.

- [ ] **Step 2: Build/run the focused C++ tests and confirm red**

Run:

```bash
cmake --build --preset test
ctest --preset test -R 'experiment_service|experiment_http_contract' --output-on-failure
```

Expected: isolated request rejected by `ExperimentService::preparePerformanceRerun`.

- [ ] **Step 3: Extend only the backend allowlist**

Change the matrix validation to:

```cpp
const bool supported_matrix =
    request.matrix == "2x3" || request.matrix == "split-smoke" ||
    request.matrix == "isolated-smoke";
if (!supported_matrix || request.warmup_seconds > 3600 ||
    request.duration_seconds == 0 || request.duration_seconds > 3600 ||
    !std::isfinite(request.target_rps) || request.target_rps <= 0) {
  return tl::unexpected(ExperimentError::invalid_request);
}
```

Do not change the controller payload schema or worker HTTP client: both already forward the string generically.

- [ ] **Step 4: Rebuild and confirm the focused C++ suites pass**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Add failing dashboard selector and label tests**

Add a unit assertion for the label:

```javascript
test('isolated smoke is labeled as a three-condition non-formal run', () => {
  assert.deepEqual(dashboard.evidenceScope({
    requestIdentity: { evidenceScope: 'representative_isolated_smoke' },
  }), {
    label: 'representative · non-formal isolated smoke',
    conditionCount: 3,
  });
});
```

In the controller fixture, set the select element to `isolated-smoke`, click `performance-rerun`, and assert the request body contains:

```javascript
{
  rerunId: '11111111-1111-4111-8111-111111111111',
  matrix: 'isolated-smoke',
  warmupSeconds: 5,
  durationSeconds: 25,
  targetRps: 5,
}
```

- [ ] **Step 6: Run the JavaScript test and confirm red**

```bash
node --test tests/js/scalability-dashboard.test.js
```

Expected: unknown scope returns `formal`, and the HTML lacks an isolated option.

- [ ] **Step 7: Add the option and exact non-formal label**

Add to `index.html`:

```html
<option value="isolated-smoke">1×3 · isolated interview smoke</option>
```

Add before the formal fallback in `evidenceScope()`:

```javascript
if (scope === 'representative_isolated_smoke') {
  return {
    label: 'representative · non-formal isolated smoke',
    conditionCount: 3,
  };
}
```

- [ ] **Step 8: Run all three interface suites**

```bash
cmake --build --preset test
ctest --preset test -R 'experiment_service|experiment_http_contract' --output-on-failure
node --test tests/js/scalability-dashboard.test.js
```

Expected: PASS.

- [ ] **Step 9: Commit the reviewed backend/dashboard slice**

```bash
git add \
  backend/src/application/experiment_service.cpp \
  backend/admin/index.html \
  backend/admin/scalability-dashboard.js \
  backend/tests/unit/experiment_service_test.cpp \
  backend/tests/integration/experiment_http_contract_test.cpp \
  tests/js/scalability-dashboard.test.js
git commit -m "feat: expose isolated representative smoke"
```

---

### Task 4: Export and package an exact non-formal isolated trio

**Files:**

- Modify: `online/src/babel_online/runtime/performance_export.py:228-265`
- Modify: `online/src/babel_online/runtime/performance_export.py:305-330`
- Modify: `benchmark/src/babel_benchmark/representative_publication.py`
- Test: `online/tests/runtime/test_performance_export.py`
- Test: `benchmark/tests/test_representative_publication.py`

**Interfaces:**

- Consumes: completed `representative_isolated_smoke` trial; three condition evidence files; exact Kafka ranges; zero final lag for training conditions.
- Produces: non-formal export manifest with `conditionCount: 3`; condition mappings `{conditionIndex: 1..3, formalConditionIndex: 7..9}`; a closed representative bundle whose inventory contains exactly three evidence files.

- [ ] **Step 1: Write failing exact-export acceptance and drift tests**

Create an isolated trial from the existing completed-trial helper by replacing its conditions with:

```python
conditions = tuple(
    replace(
        original.conditions[index - 1],
        condition_index=index,
        topology="same_host_isolated",
        training_enabled=index >= 2,
        activation_enabled=index == 3,
    )
    for index in range(1, 4)
)
trial = SimpleNamespace(
    **{
        **original.__dict__,
        "conditions": conditions,
        "evidence_scope": "representative_isolated_smoke",
        "creator_count": 50,
    }
)
```

Export it and assert:

```python
assert manifest["evidenceScope"] == "representative_isolated_smoke"
assert manifest["formalPerformanceClaim"] is False
assert manifest["conditionCount"] == 3
assert [row["conditionIndex"] for row in manifest["conditions"]] == [1, 2, 3]
assert [row["formalConditionIndex"] for row in manifest["conditions"]] == [7, 8, 9]
```

Add a rejection case that changes one topology to `same_host_split` and expects `ValueError` matching `exact completed condition matrix`.

- [ ] **Step 2: Run isolated export tests and confirm red**

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_export.py \
  -k 'isolated'
```

Expected: the new scope is rejected.

- [ ] **Step 3: Add the isolated topology profile and comparison indices**

In `_completed_representative_conditions()` use:

```python
topologies_by_scope = {
    "representative_same_process_vs_split": ("same_process", "same_host_split"),
    "representative_split_smoke": ("same_host_split",),
    "representative_isolated_smoke": ("same_host_isolated",),
}
try:
    topologies = topologies_by_scope[scope]
except KeyError as error:
    raise ValueError(
        "performance trial is not an explicitly representative rerun"
    ) from error
```

When serializing condition bindings, retain IDs and add indices:

```python
condition_document = {
    "conditionIndex": row.condition_index,
    "conditionId": str(row.id),
    "runId": str(row.run_id),
}
if evidence_scope == "representative_isolated_smoke":
    condition_document["formalConditionIndex"] = row.condition_index + 6
```

Build the manifest condition list from that helper. Do not set `formalPerformanceClaim` to true.

- [ ] **Step 4: Run the complete export suite**

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_export.py
```

Expected: PASS for formal, 2×3 representative, and isolated representative cases.

- [ ] **Step 5: Add a failing three-condition publication fixture**

Parameterize the test helper with scope/topology/count/formal positions. For the isolated case, use:

```python
scope = "representative_isolated_smoke"
condition_count = 3
topologies = ("same_host_isolated",)
formal_indices = (7, 8, 9)
```

Build a bundle and assert its closed inventory includes only:

```python
{
    "conditions/01/live-evidence.json",
    "conditions/02/live-evidence.json",
    "conditions/03/live-evidence.json",
}
```

for condition evidence, and that `trial-results.json` contains `formalConditionIndex` values `[7, 8, 9]`. Add fail-closed tests for a fourth evidence file, nonzero Kafka lag in conditions 2 or 3, and a `same_host_split` identity.

- [ ] **Step 6: Run the isolated publication tests and confirm hardcoded-six failures**

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  benchmark/tests/test_representative_publication.py \
  -k 'isolated'
```

Expected: failures on the current six-condition count, identities, and inventory.

- [ ] **Step 7: Generalize publication with a closed scope profile**

Replace the single hardcoded identity tuple with immutable profiles:

```python
_SCOPE_PROFILES = {
    "representative_same_process_vs_split": {
        "identities": _SAME_PROCESS_AND_SPLIT_IDENTITIES,
        "formal_indices": (1, 2, 3, 4, 5, 6),
    },
    "representative_isolated_smoke": {
        "identities": tuple(
            {
                "topology": "same_host_isolated",
                "trainingEnabled": mode >= 1,
                "activationEnabled": mode == 2,
                "retrievalBackend": "pgvector",
            }
            for mode in range(3)
        ),
        "formal_indices": (7, 8, 9),
    },
}
```

Do not add a publication profile for `representative_split_smoke`; it is not accepted by the current closed publisher. `_validate_sources()` must derive `condition_count`, expected identities, evidence paths, and formal indices exclusively from the exact 2×3 or isolated scope profile. Return the profile data with the validated sources so `build_representative_run_bundle()` uses the same count for copying files, summaries, results, manifests, checksum inventories, and local reload validation.

Add to each derived condition result:

```python
"conditionIndex": index,
"formalConditionIndex": formal_indices[index - 1],
```

For training-disabled condition 1, continue requiring a present final trainer state only if that is the existing evidence contract; for conditions 2 and 3, require `available is True`, `kafkaLag == 0`, and `offsetsCoverPublishedRanges is True` exactly.

- [ ] **Step 8: Run exporter and publication suites together**

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_export.py \
  benchmark/tests/test_representative_publication.py
```

Expected: PASS.

- [ ] **Step 9: Commit the reviewed evidence slice**

```bash
git add \
  online/src/babel_online/runtime/performance_export.py \
  online/tests/runtime/test_performance_export.py \
  benchmark/src/babel_benchmark/representative_publication.py \
  benchmark/tests/test_representative_publication.py
git commit -m "feat: package isolated smoke evidence"
```

---

### Task 5: Document the operator path and pass the aggregate review gate

**Files:**

- Modify: `docs/runbooks/scaled-experiment.md`
- Verify: all Task 1–4 source and test files

**Interfaces:**

- Consumes: reviewed `isolated-smoke` selector and the existing deployment workflow.
- Produces: a copy-paste operator sequence that creates, approves, exports, packages, retrieves, verifies, and stops one isolated smoke; a final audited SHA ready for deployment.

- [ ] **Step 1: Add the exact isolated-smoke runbook section**

Document these immutable values and commands:

```bash
SOURCE_TRIAL_ID='dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28'
ISOLATED_TRIAL_ID="$(python3 -c 'from uuid import uuid4; print(uuid4())')"
PERF_ROOT='/var/lib/babel-online/performance'
RUN_ROOT="/var/lib/babel-online/results/28-august-morning-run/isolated-smoke/${ISOLATED_TRIAL_ID}"

CURRENT_RELEASE=/opt/babel/current
source "$CURRENT_RELEASE/release.env"
compose=(docker compose --project-name babel-gcp-demo \
  --profile matrix \
  --env-file "$CURRENT_RELEASE/release.env" \
  --file "$CURRENT_RELEASE/compose.yaml")
guard="$("${compose[@]}" run --rm --no-deps --entrypoint /bin/sh performance-worker \
  -c 'printf %s "$BABEL_ONLINE_ALLOW_POPULATION_BUILD"')"
test "$guard" = false
ADMIN_NONCE="$(curl --fail --silent --show-error http://127.0.0.1:8787/admin \
  | python3 -c 'import re,sys; text=sys.stdin.read(); match=re.search(r"name=\"babel-admin-nonce\" content=\"([0-9a-f]+)\"", text); assert match; print(match.group(1))')"
python3 - "$ISOLATED_TRIAL_ID" >/tmp/isolated-rerun.json <<'PY'
import json
import sys
print(json.dumps({
    "rerunId": sys.argv[1],
    "matrix": "isolated-smoke",
    "warmupSeconds": 5,
    "durationSeconds": 25,
    "targetRps": 5.0,
}))
PY
curl --fail --silent --show-error \
  --header 'Origin: http://127.0.0.1:8787' \
  --header "X-Babel-Admin-Nonce: $ADMIN_NONCE" \
  --header 'Content-Type: application/json' \
  --data-binary @/tmp/isolated-rerun.json \
  "http://127.0.0.1:8787/admin/api/v1/performance/$SOURCE_TRIAL_ID/representative-rerun"
curl --fail --silent --show-error \
  --request POST \
  --header 'Origin: http://127.0.0.1:8787' \
  --header "X-Babel-Admin-Nonce: $ADMIN_NONCE" \
  "http://127.0.0.1:8787/admin/api/v1/performance/$ISOLATED_TRIAL_ID/approve-next-scale"
```

State explicitly that the three local evidence directories map 01→formal 7, 02→formal 8, and 03→formal 9, while remaining non-formal. Include the existing `performance-export --representative` and `representative-run-build` commands, checksum verification, IAP `gcloud compute scp`, and VM stop command from Task 6.

- [ ] **Step 2: Run the focused aggregate suites**

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/runtime/test_performance_rerun.py \
  online/tests/runtime/test_database.py \
  online/tests/runtime/test_performance_worker.py \
  online/tests/runtime/test_performance_rerun_cli.py \
  online/tests/runtime/test_performance_export.py \
  benchmark/tests/test_representative_publication.py
cmake --build --preset test
ctest --preset test -R 'experiment_service|experiment_http_contract|experiment_job_runner' --output-on-failure
node --test tests/js/scalability-dashboard.test.js
```

Expected: PASS with no skips in the selected tests.

- [ ] **Step 3: Run the required regression suites from the approved design**

```bash
PYTHONPATH=online/src:benchmark/src:. online/.venv/bin/python -m pytest -q \
  online/tests/training/test_torch_online.py \
  online/tests/training/test_checkpoint.py \
  online/tests/feedback/test_kafka.py \
  online/tests/runtime/test_performance_condition.py \
  online/tests/runtime/test_standalone_roles.py \
  online/tests/runtime/test_topology.py \
  online/tests/runtime/test_topology_acceptance.py \
  benchmark/tests/test_matrix.py \
  benchmark/tests/test_representative_publication.py \
  tests/deploy/test_gcp_deployment.py \
  tests/deploy/test_gcp_predeploy.py \
  tests/deploy/test_rollout_supervisor.py
npm test
```

Expected: PASS. Any failure blocks cloud mutation and is reported with its exact command and output.

- [ ] **Step 4: Audit the final diff and deployment invariants**

```bash
git diff --check 381a20fd5005973c8d452ed3674926e293b5d2e1..HEAD
git diff --stat 381a20fd5005973c8d452ed3674926e293b5d2e1..HEAD
git diff --name-status 381a20fd5005973c8d452ed3674926e293b5d2e1..HEAD
git diff --exit-code 326b8403ec166f44099cd8950d6ff05b7d2d75cb..HEAD -- backend/migrations
rg -n 'BABEL_ONLINE_ALLOW_POPULATION_BUILD: "false"|BABEL_ONLINE_QWEN_DEVICE: cuda' deploy/gcp/compose.yaml
git status --short
```

Expected: no whitespace errors, no migration changes, guard remains false, Qwen remains CUDA, and the worktree is clean after committing the runbook.

- [ ] **Step 5: Commit documentation and record the audited SHA**

```bash
git add docs/runbooks/scaled-experiment.md
git commit -m "docs: run isolated interview smoke"
FINAL_SHA="$(git rev-parse HEAD)"
git show --stat --oneline "$FINAL_SHA"
git status --short
```

Expected: clean worktree and a 40-character `FINAL_SHA` descending from `381a20f` through only the reviewed commits.

---

### Task 6: Deploy, execute the isolated trio, retrieve evidence, and stop the VM

**Files:**

- Create locally after the run: `results/28-august-morning-run/isolated-smoke/<trial-id>/`
- Preserve unchanged: `results/28-august-morning-run/formal-partial-failed/`
- Read remotely: `/opt/babel/current/release.env`
- Create remotely: `/var/lib/babel-online/results/28-august-morning-run/isolated-smoke/<trial-id>/`

**Interfaces:**

- Consumes: audited `FINAL_SHA`, GitHub `demo` workflow, existing GCP imported population, failed formal trial as immutable source, and IAP SSH.
- Produces: deployed SHA and image digests; three complete evidence receipts; interference ratios; checksum-verified local bundle; final VM status `TERMINATED`.

- [ ] **Step 1: Start only the existing VM and fast-forward `demo`**

```bash
gcloud compute instances start babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b
git fetch origin demo
git merge-base --is-ancestor origin/demo HEAD
git push origin HEAD:demo
```

Expected: the ancestor check and non-force push succeed. If either fails, stop and report instead of merging another branch or force-pushing.

- [ ] **Step 2: Monitor the exact GitHub deployment to completion**

```bash
FINAL_SHA="$(git rev-parse HEAD)"
RUN_ID="$(gh run list \
  --workflow deploy-gcp-demo.yml \
  --branch demo \
  --commit "$FINAL_SHA" \
  --json databaseId \
  --jq '.[0].databaseId')"
gh run watch "$RUN_ID" --exit-status
gh run view "$RUN_ID" --json headSha,conclusion,url
```

Expected: `headSha == FINAL_SHA` and `conclusion == success`. A failed workflow blocks the run; preserve its logs and stop the VM if it cannot be corrected inside the timebox.

- [ ] **Step 3: Verify the deployed attestation and smoke without rerunning population audit**

```bash
gcloud compute ssh babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --tunnel-through-iap \
  --command "sudo bash -lc '
    set -euo pipefail
    source /opt/babel/current/release.env
    test \"\$BABEL_SOURCE_COMMIT\" = \"$FINAL_SHA\"
    test -s /opt/babel/current/smoke-response.json
    compose=(docker compose --project-name babel-gcp-demo \
      --profile matrix \
      --env-file /opt/babel/current/release.env \
      --file /opt/babel/current/compose.yaml)
    guard=\"\$(\"\${compose[@]}\" run --rm --no-deps --entrypoint /bin/sh performance-worker \
      -c '\''printf %s \"\$BABEL_ONLINE_ALLOW_POPULATION_BUILD\"'\'')\"
    test \"\$guard\" = false
    \"\${compose[@]}\" ps
    python3 /opt/babel/current/release.py validate-serving-health \
      /opt/babel/current/smoke-response.json
  '"
```

Expected: exact source SHA, guard false, CUDA serving smoke valid, and backend/performance-worker healthy. Use the rollout/predeploy receipt's 10,000-vector assertion; do not run another population export/import/audit.

- [ ] **Step 4: Create and approve the fresh isolated representative trial**

Generate the UUID locally and use the tested backend boundary so durable approval and worker activation occur in the required order:

```bash
ISOLATED_TRIAL_ID="$(python3 -c 'from uuid import uuid4; print(uuid4())')"
gcloud compute ssh babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --tunnel-through-iap \
  --command "sudo env ISOLATED_TRIAL_ID='$ISOLATED_TRIAL_ID' bash -lc '
    set -euo pipefail
    source /opt/babel/current/release.env
    compose=(docker compose --project-name babel-gcp-demo \
      --profile matrix \
      --env-file /opt/babel/current/release.env \
      --file /opt/babel/current/compose.yaml)
    guard=\"\$(\"\${compose[@]}\" run --rm --no-deps --entrypoint /bin/sh performance-worker \
      -c '\''printf %s \"\$BABEL_ONLINE_ALLOW_POPULATION_BUILD\"'\'')\"
    test \"\$guard\" = false
    \"\${compose[@]}\" stop serving trainer
    \"\${compose[@]}\" up --detach backend performance-worker
    curl --fail --silent --show-error --retry 60 --retry-delay 1 --retry-connrefused \
      http://127.0.0.1:8792/health >/dev/null
    nonce=\"\$(curl --fail --silent --show-error http://127.0.0.1:8787/admin \
      | python3 -c '\''import re,sys; text=sys.stdin.read(); match=re.search(r\"name=\\\"babel-admin-nonce\\\" content=\\\"([0-9a-f]+)\\\"\", text); assert match; print(match.group(1))'\'')\"
    request=\"\$(python3 - \"\$ISOLATED_TRIAL_ID\" <<'PY'
import json
import sys
print(json.dumps({
    "rerunId": sys.argv[1],
    "matrix": "isolated-smoke",
    "warmupSeconds": 5,
    "durationSeconds": 25,
    "targetRps": 5.0,
}))
PY
)\"
    run_root=/var/lib/babel-online/results/28-august-morning-run/isolated-smoke/\"\$ISOLATED_TRIAL_ID\"
    install -d -m 0750 -o 10001 -g 10001 \"\$run_root\"
    curl --fail --silent --show-error \
      --header '\''Origin: http://127.0.0.1:8787'\'' \
      --header \"X-Babel-Admin-Nonce: \$nonce\" \
      --header '\''Content-Type: application/json'\'' \
      --data-binary \"\$request\" \
      http://127.0.0.1:8787/admin/api/v1/performance/dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28/representative-rerun \
      | tee \"\$run_root/create-receipt.json\"
    curl --fail --silent --show-error \
      --request POST \
      --header '\''Origin: http://127.0.0.1:8787'\'' \
      --header \"X-Babel-Admin-Nonce: \$nonce\" \
      http://127.0.0.1:8787/admin/api/v1/performance/\"\$ISOLATED_TRIAL_ID\"/approve-next-scale \
      | tee \"\$run_root/approval-receipt.json\"
  '"
```

Expected: the backend creates a fresh trial from the exact source, durably records approval, and tells the already-running worker to start it. Do not substitute a direct database update or a second worker process.

- [ ] **Step 5: Wait for a terminal state without changing configuration or retrying**

```bash
gcloud compute ssh babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --tunnel-through-iap \
  --command "sudo env ISOLATED_TRIAL_ID='$ISOLATED_TRIAL_ID' bash -lc '
    set -euo pipefail
    source /etc/babel/runtime.env
    token=\"\$(python3 /opt/babel/current/release.py runtime-token /etc/babel/runtime.env)\"
    for attempt in \$(seq 1 1200); do
      phase=\"\$(printf '\''header = \"X-Babel-Worker-Token: %s\"\\n'\'' \"\$token\" \
        | curl --config - --fail --silent --show-error --max-time 2 \
          http://127.0.0.1:8792/v1/performance/status \
        | python3 -c '\''import json,sys; document=json.load(sys.stdin); expected=sys.argv[1]; assert document.get(\"experimentId\") == expected, \"worker status belongs to another trial\"; print(document[\"phase\"])'\'' \"\$ISOLATED_TRIAL_ID\")\"
      case \"\$phase\" in
        completed) exit 0 ;;
        failed|interrupted) echo \"isolated smoke ended: \$phase\" >&2; exit 1 ;;
      esac
      sleep 1
    done
    echo isolated-smoke-timeout >&2
    exit 1
  '"
```

Expected: `completed`. On `failed`, preserve all evidence and do not silently rerun.

If the wait command exits nonzero, run this terminal-failure path instead of Step 6: copy any condition state and worker logs into the morning-run directory, checksum and retrieve it using Step 7, label it failed, then execute Step 9 to stop the VM.

```bash
gcloud compute ssh babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --tunnel-through-iap \
  --command "sudo env ISOLATED_TRIAL_ID='$ISOLATED_TRIAL_ID' bash -lc '
    set -euo pipefail
    run_root=/var/lib/babel-online/results/28-august-morning-run/isolated-smoke/\"\$ISOLATED_TRIAL_ID\"
    state_root=/var/lib/babel-online/performance/\"\$ISOLATED_TRIAL_ID\"
    install -d -m 0750 -o 10001 -g 10001 \"\$run_root\"
    printf '\''# Failed isolated topology smoke\n\nNon-formal run failed or timed out; no retry was performed.\n'\'' \
      >\"\$run_root/FAILED.md\"
    chown 10001:10001 \"\$run_root/FAILED.md\"
    if test -d \"\$state_root\"; then
      cp --archive \"\$state_root\" \"\$run_root/condition-state\"
    fi
    docker compose --project-name babel-gcp-demo \
      --profile matrix \
      --env-file /opt/babel/current/release.env \
      --file /opt/babel/current/compose.yaml \
      logs --no-color --timestamps performance-worker \
      >\"\$run_root/performance-worker.log\" 2>&1
    chown --recursive 10001:10001 \"\$run_root\"
    (
      cd \"\$run_root\"
      find . -type f ! -name SHA256SUMS -print0 \
        | sort -z \
        | xargs -0 sha256sum >SHA256SUMS
    )
  '"
```

- [ ] **Step 6: Export and build the immutable representative bundle**

On the VM, create the run directory, export the exact Kafka ranges, generate `summary.json` and `REPORT.md` from the three verified evidence files, then build the closed bundle:

```bash
RUN_ROOT="/var/lib/babel-online/results/28-august-morning-run/isolated-smoke/$ISOLATED_TRIAL_ID"
mkdir -p "$RUN_ROOT"
CURRENT_RELEASE=/opt/babel/current
compose=(docker compose --project-name babel-gcp-demo \
  --profile matrix \
  --env-file "$CURRENT_RELEASE/release.env" \
  --file "$CURRENT_RELEASE/compose.yaml")
"${compose[@]}" exec -T performance-worker babel-online performance-export \
  --representative \
  --experiment-id "$ISOLATED_TRIAL_ID" \
  --evidence-root "/var/lib/babel-online/performance/$ISOLATED_TRIAL_ID/conditions" \
  --output-root "$RUN_ROOT/export" \
  >"$RUN_ROOT/export-receipt.json"
python3 - \
  "$ISOLATED_TRIAL_ID" \
  "/var/lib/babel-online/performance/$ISOLATED_TRIAL_ID/conditions" \
  "$RUN_ROOT" \
  /opt/babel/current/release.env <<'PY'
import json
import math
import sys
from pathlib import Path
from statistics import mean

trial_id, evidence_arg, run_arg, release_arg = sys.argv[1:]
evidence_root = Path(evidence_arg)
run_root = Path(run_arg)

release = {}
for line in Path(release_arg).read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        release[key] = value
required_release = {
    "BABEL_SOURCE_COMMIT",
    "BABEL_BACKEND_IMAGE",
    "BABEL_SERVING_IMAGE",
    "BABEL_TRAINER_IMAGE",
    "BABEL_PERFORMANCE_WORKER_IMAGE",
    "BABEL_MODEL_REVISION",
    "BABEL_DATASET_REVISION",
    "BABEL_POPULATION_VECTOR_SHA256",
    "BABEL_POPULATION_SNAPSHOT_SHA256",
}
if not required_release.issubset(release):
    raise SystemExit("release attestation is incomplete")

def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]

rows = []
expected_modes = ((False, False), (True, False), (True, True))
for internal_index, (training, activation) in enumerate(expected_modes, start=1):
    candidates = [
        evidence_root / f"{internal_index:02d}" / "live-evidence.json",
        evidence_root / str(internal_index) / "live-evidence.json",
    ]
    present = [path for path in candidates if path.is_file()]
    if len(present) != 1:
        raise SystemExit(f"condition {internal_index} evidence is missing or ambiguous")
    document = json.loads(present[0].read_text(encoding="utf-8"))
    raw = document.get("rawEvidence")
    identity = {
        "topology": "same_host_isolated",
        "trainingEnabled": training,
        "activationEnabled": activation,
        "retrievalBackend": "pgvector",
    }
    if not isinstance(raw, dict) or raw.get("evidenceScope") != "representative_isolated_smoke":
        raise SystemExit(f"condition {internal_index} scope differs")
    if raw.get("conditionIdentity") != identity:
        raise SystemExit(f"condition {internal_index} identity differs")
    measurements = raw.get("measurements")
    if not isinstance(measurements, list):
        raise SystemExit(f"condition {internal_index} measurements are missing")
    warmup = [row for row in measurements if row.get("isWarmup") is True]
    measured = [row for row in measurements if row.get("isWarmup") is False]
    if len(warmup) != 25 or len(measured) != 125 or document.get("requestCount") != 125:
        raise SystemExit(f"condition {internal_index} request schedule differs")
    errors = [row for row in measured if row.get("outcome") != "success"]
    if errors:
        raise SystemExit(f"condition {internal_index} contains request failures")
    latencies = [row["clientTotalNs"] / 1_000_000 for row in measured]
    elapsed_ns = max(row["completedAtMonotonicNs"] for row in measured) - min(
        row["actualStartMonotonicNs"] for row in measured
    )
    resources = raw.get("resources")
    if not isinstance(resources, list) or not resources:
        raise SystemExit(f"condition {internal_index} resources are missing")
    cpu = [float(row["cpuPercent"]) for row in resources if row.get("cpuPercent") is not None]
    host_memory = [int(row["hostMemoryUsedBytes"]) for row in resources if row.get("hostMemoryUsedBytes") is not None]
    gpu = [float(row["gpuUtilizationPercent"]) for row in resources if row.get("gpuUtilizationPercent") is not None]
    gpu_memory = [int(row["gpuMemoryUsedBytes"]) for row in resources if row.get("gpuMemoryUsedBytes") is not None]
    training_rate = [float(row["trainingStepRate"]) for row in resources if row.get("trainingStepRate") is not None]
    activation_ns = [int(row["activationDurationNs"]) for row in resources if row.get("activationDurationNs") is not None]
    if not cpu or not host_memory or not gpu or not gpu_memory:
        raise SystemExit(f"condition {internal_index} resource metrics are incomplete")
    final = raw.get("feedbackKafka", {}).get("finalTrainerState")
    if not isinstance(final, dict) or final.get("available") is not True:
        raise SystemExit(f"condition {internal_index} final trainer state is missing")
    if training and (
        final.get("kafkaLag") != 0
        or final.get("offsetsCoverPublishedRanges") is not True
    ):
        raise SystemExit(f"condition {internal_index} Kafka drain is incomplete")
    if activation and (
        not raw.get("observedActivationTargets") or not activation_ns
    ):
        raise SystemExit("condition 3 activation evidence is incomplete")
    rows.append({
        "formalConditionIndex": internal_index + 6,
        "conditionIndex": internal_index,
        "conditionId": document["conditionId"],
        "runId": document["runId"],
        "trainingEnabled": training,
        "activationEnabled": activation,
        "requests": len(measured),
        "p50Ms": percentile(latencies, 0.50),
        "p95Ms": percentile(latencies, 0.95),
        "p99Ms": percentile(latencies, 0.99),
        "maxMs": max(latencies),
        "achievedRps": len(measured) / (elapsed_ns / 1_000_000_000),
        "errors": 0,
        "timeouts": 0,
        "meanCpuPercent": mean(cpu),
        "maxHostMemoryBytes": max(host_memory),
        "meanGpuUtilizationPercent": mean(gpu),
        "maxGpuMemoryBytes": max(gpu_memory),
        "maxTrainingStepsPerSecond": max(training_rate, default=0.0),
        "finalKafkaLag": final.get("kafkaLag"),
        "maxActivationMs": max(activation_ns, default=0) / 1_000_000,
    })

ratios = {
    "Itraining": rows[1]["p95Ms"] / rows[0]["p95Ms"],
    "Ifull": rows[2]["p95Ms"] / rows[0]["p95Ms"],
    "IActivationIncrement": rows[2]["p95Ms"] / rows[1]["p95Ms"],
}
summary = {
    "schemaVersion": 1,
    "trialId": trial_id,
    "label": "representative isolated smoke — non-formal interview evidence",
    "evidenceScope": "representative_isolated_smoke",
    "formalPerformanceClaim": False,
    "sourceTrialId": "dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28",
    "sourceCommit": release["BABEL_SOURCE_COMMIT"],
    "images": {
        "backend": release["BABEL_BACKEND_IMAGE"],
        "serving": release["BABEL_SERVING_IMAGE"],
        "trainer": release["BABEL_TRAINER_IMAGE"],
        "performanceWorker": release["BABEL_PERFORMANCE_WORKER_IMAGE"],
    },
    "modelRevision": release["BABEL_MODEL_REVISION"],
    "datasetRevision": release["BABEL_DATASET_REVISION"],
    "populationVectorSha256": release["BABEL_POPULATION_VECTOR_SHA256"],
    "populationSnapshotSha256": release["BABEL_POPULATION_SNAPSHOT_SHA256"],
    "populationVectorCount": 10_000,
    "conditions": rows,
    "interference": ratios,
}
(run_root / "summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
lines = [
    "# 28 August Morning Run — Isolated Topology Smoke",
    "",
    "Representative isolated smoke — non-formal interview evidence.",
    "",
    "| Formal # | Internal # | Training | Activation | p50 ms | p95 ms | p99 ms | max ms | RPS | errors | CPU mean % | memory max GiB | GPU mean % | GPU memory max GiB | trainer steps/s max | Kafka lag | activation max ms |",
    "|---:|---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for row in rows:
    lines.append(
        f"| {row['formalConditionIndex']} | {row['conditionIndex']} | "
        f"{str(row['trainingEnabled']).lower()} | {str(row['activationEnabled']).lower()} | "
        f"{row['p50Ms']:.3f} | {row['p95Ms']:.3f} | {row['p99Ms']:.3f} | "
        f"{row['maxMs']:.3f} | {row['achievedRps']:.3f} | {row['errors']} | "
        f"{row['meanCpuPercent']:.2f} | {row['maxHostMemoryBytes'] / 2**30:.3f} | "
        f"{row['meanGpuUtilizationPercent']:.2f} | {row['maxGpuMemoryBytes'] / 2**30:.3f} | "
        f"{row['maxTrainingStepsPerSecond']:.3f} | {row['finalKafkaLag']} | "
        f"{row['maxActivationMs']:.3f} |"
    )
lines += [
    "",
    f"- Itraining: {ratios['Itraining']:.4f}",
    f"- Ifull: {ratios['Ifull']:.4f}",
    f"- IActivationIncrement: {ratios['IActivationIncrement']:.4f}",
    f"- Source SHA: `{release['BABEL_SOURCE_COMMIT']}`",
    f"- Model revision: `{release['BABEL_MODEL_REVISION']}`",
    f"- Dataset revision: `{release['BABEL_DATASET_REVISION']}`",
    f"- Population vectors: 10,000 (`{release['BABEL_POPULATION_VECTOR_SHA256']}`)",
    "",
]
(run_root / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
PY
"${compose[@]}" exec -T performance-worker \
  babel-friday-benchmark representative-run-build \
  --trial-id "$ISOLATED_TRIAL_ID" \
  --export-root "$RUN_ROOT/export/feedback-export" \
  --evidence-root "/var/lib/babel-online/performance/$ISOLATED_TRIAL_ID/conditions" \
  --report "$RUN_ROOT/REPORT.md" \
  --output-root "$RUN_ROOT/accepted" \
  >"$RUN_ROOT/build-receipt.json"
(
  cd "$RUN_ROOT"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)
```

Expected: report rows 7–9 are derived from internal conditions 1–3, both training rows have zero final Kafka lag, activation evidence exists for row 9, and the report states `representative isolated smoke — non-formal interview evidence` at the top.

- [ ] **Step 7: Retrieve into the easy-to-find local morning-run directory**

```bash
LOCAL_ROOT='/home/dhelmy990/Code/babel/results/28-august-morning-run/isolated-smoke'
mkdir -p "$LOCAL_ROOT"
gcloud compute ssh babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --tunnel-through-iap \
  --command "sudo env ISOLATED_TRIAL_ID='$ISOLATED_TRIAL_ID' bash -lc '
    set -euo pipefail
    source_dir=/var/lib/babel-online/results/28-august-morning-run/isolated-smoke/\"\$ISOLATED_TRIAL_ID\"
    stage=/tmp/babel-isolated-\"\$ISOLATED_TRIAL_ID\"
    test -d \"\$source_dir\"
    test ! -e \"\$stage\"
    cp --archive \"\$source_dir\" \"\$stage\"
    chown --recursive \"\$SUDO_USER:\$SUDO_USER\" \"\$stage\"
  '"
gcloud compute scp --recurse --tunnel-through-iap \
  "babel-gpu-serving:/tmp/babel-isolated-$ISOLATED_TRIAL_ID" \
  "$LOCAL_ROOT/" \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b
test ! -e "$LOCAL_ROOT/$ISOLATED_TRIAL_ID"
mv "$LOCAL_ROOT/babel-isolated-$ISOLATED_TRIAL_ID" "$LOCAL_ROOT/$ISOLATED_TRIAL_ID"
cd "$LOCAL_ROOT/$ISOLATED_TRIAL_ID"
sha256sum --check SHA256SUMS
```

Expected: every checksum passes and `formal-partial-failed/` remains unchanged beside this directory.

- [ ] **Step 8: Record deployment metadata and final VM cost/status**

Create `deployment.json` from the verified summary and live GCP/GitHub metadata, then rebuild the relative checksum inventory:

```bash
RUN_DIR="$LOCAL_ROOT/$ISOLATED_TRIAL_ID"
STARTED_AT="$(gcloud compute instances describe babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --format='value(lastStartTimestamp)')"
RECORDED_AT="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
GITHUB_URL="$(gh run view "$RUN_ID" --json url --jq .url)"
python3 - "$RUN_DIR" "$STARTED_AT" "$RECORDED_AT" "$GITHUB_URL" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

root = Path(sys.argv[1])
started = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
recorded = datetime.fromisoformat(sys.argv[3].replace("Z", "+00:00"))
summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
hours = max(0.0, (recorded - started).total_seconds() / 3600)
document = {
    "schemaVersion": 1,
    "sourceCommit": summary["sourceCommit"],
    "githubRunUrl": sys.argv[4],
    "images": summary["images"],
    "modelRevision": summary["modelRevision"],
    "datasetRevision": summary["datasetRevision"],
    "project": "chloe-tutoring-bot",
    "vm": "babel-gpu-serving",
    "zone": "asia-southeast1-b",
    "machineType": "g2-standard-4",
    "gpu": "NVIDIA L4",
    "vmStartedAt": sys.argv[2],
    "metadataRecordedAt": sys.argv[3],
    "estimatedGpuVmHours": hours,
    "estimatedComputeUsd": hours * 0.71,
    "storageAndNetworkExcluded": True,
}
(root / "deployment.json").write_text(
    json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
(
  cd "$RUN_DIR"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
  sha256sum --check SHA256SUMS
)
```

Expected: all four digest-qualified images, revisions, timestamps, and the `$0.71/hour` compute estimate are present without credentials.

- [ ] **Step 9: Stop the VM and verify termination**

```bash
gcloud compute instances stop babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b
gcloud compute instances describe babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --format='value(status)' | tee /tmp/babel-gpu-serving-final-status
STOPPED_AT="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
python3 - "$LOCAL_ROOT/$ISOLATED_TRIAL_ID/deployment.json" \
  /tmp/babel-gpu-serving-final-status "$STOPPED_AT" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

path = Path(sys.argv[1])
document = json.loads(path.read_text(encoding="utf-8"))
status = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
if status != "TERMINATED":
    raise SystemExit(f"VM did not terminate: {status}")
started = datetime.fromisoformat(document["vmStartedAt"].replace("Z", "+00:00"))
stopped = datetime.fromisoformat(sys.argv[3].replace("Z", "+00:00"))
hours = max(0.0, (stopped - started).total_seconds() / 3600)
document.update(
    vmStoppedAt=sys.argv[3],
    finalVmStatus=status,
    estimatedGpuVmHours=hours,
    estimatedComputeUsd=hours * 0.71,
)
path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
(
  cd "$LOCAL_ROOT/$ISOLATED_TRIAL_ID"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
  sha256sum --check SHA256SUMS
)
```

Expected: `TERMINATED`.

- [ ] **Step 10: Run the completion evidence check**

```bash
test -s "$LOCAL_ROOT/$ISOLATED_TRIAL_ID/REPORT.md"
test -s "$LOCAL_ROOT/$ISOLATED_TRIAL_ID/build-receipt.json"
test -s "$LOCAL_ROOT/$ISOLATED_TRIAL_ID/deployment.json"
find "$LOCAL_ROOT/$ISOLATED_TRIAL_ID" -path '*/conditions/*/live-evidence.json' -type f | sort
git status --short
```

Expected: three condition evidence files, complete report and receipts, terminated VM, and no accidental source changes from evidence retrieval.
