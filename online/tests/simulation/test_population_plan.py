from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from babel_online.contracts import RunConfigV2
from babel_online.runtime.dataset_bundle import (
    DEMO_DATASET_REPOSITORY,
    SCALE_DATASET_CONFIG,
    SCALE_DATASET_REVISION,
    DatasetBundle,
)
from babel_online.simulation.population_plan import plan_population

MODEL = UUID("00000000-0000-5000-8000-000000000002")


def formal_config(*, run_id: UUID = UUID("00000000-0000-5000-8000-000000000001")):
    return RunConfigV2(
        schemaVersion=2,
        runId=run_id,
        datasetRepo=DEMO_DATASET_REPOSITORY,
        datasetConfig=SCALE_DATASET_CONFIG,
        datasetRevision=SCALE_DATASET_REVISION,
        startingModelId=MODEL,
        creatorCount=50,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 5_000, "2026-07": 5_000},
        runSeed=73,
        sourceArticlesPerMonth=5_000,
        targetCreatedBabels=10_000,
        concurrentUsers=50,
    )


def real_bundle() -> DatasetBundle:
    def catalog(period: str):
        return tuple(
            {
                "article_key": f"enwiki:{number}",
                "canonical_title": f"{period} article {number}",
                "lead_text": f"Observable {period} text {number}",
                "article_text": f"Long observable {period} text {number}",
                "content_hash": f"{number:064x}",
            }
            for number in range(1, 5_001)
        )

    return DatasetBundle(
        root=Path("/dataset"),
        dataset_repository=DEMO_DATASET_REPOSITORY,
        dataset_config=SCALE_DATASET_CONFIG,
        dataset_revision=SCALE_DATASET_REVISION,
        release_scope="timeboxed_engineering_snapshot",
        snapshot_claim="real_timeboxed_engineering_snapshot",
        configs={
            "catalog_2026_06": catalog("June"),
            "catalog_2026_07": catalog("July"),
            SCALE_DATASET_CONFIG: (),
            "simulator_2026_06_hidden": (),
            "simulator_2026_07_hidden": (),
        },
        manifest_sha256="a" * 64,
    )


def test_formal_population_plan_freezes_exact_cross_month_roots_and_schedule() -> None:
    config = formal_config()
    plan = plan_population(config, real_bundle())

    assert len(plan.babels) == 10_000
    assert plan.period_counts == {"2026-06": 5_000, "2026-07": 5_000}
    assert len(plan.creator_ids) == 50
    assert [row.ordinal for row in plan.babels] == list(range(10_000))
    assert [row.scheduled.schedule_index for row in plan.babels] == list(range(10_000))
    assert all(row.babel.runId == config.runId for row in plan.babels)
    assert all(row.babel.babelId == row.scheduled.root_babel_id for row in plan.babels)
    assert [row.event_number for row in plan.babels] == list(range(10_000))
    assert all(row.babel.text == row.source_row["lead_text"] for row in plan.babels)
    creator_sources = [
        (row.babel.creatorId, row.babel.sourceArticleKey) for row in plan.babels
    ]
    assert len(set(creator_sources)) == 10_000
    for creator_id in plan.creator_ids:
        assert [
            row.scheduled.creator_event_number
            for row in plan.babels
            if row.babel.creatorId == creator_id
        ] == list(range(200))


def test_population_source_draw_is_seed_stable_but_run_scoped_ids_are_not() -> None:
    bundle = real_bundle()
    first = plan_population(formal_config(), bundle)
    second = plan_population(formal_config(run_id=uuid4()), bundle)

    assert [row.babel.sourceArticleKey for row in first.babels] == [
        row.babel.sourceArticleKey for row in second.babels
    ]
    assert [row.babel.babelId for row in first.babels] != [
        row.babel.babelId for row in second.babels
    ]
    assert first.schedule != second.schedule


@pytest.mark.parametrize(
    "change, message",
    [
        ({"creatorCount": 49, "concurrentUsers": 49}, "50 creators"),
        (
            {
                "perMonthEventBudget": {"2026-06": 4_999, "2026-07": 5_000},
                "targetCreatedBabels": 9_999,
            },
            "10,000",
        ),
        ({"datasetRevision": "f" * 40}, "real scale dataset"),
    ],
)
def test_formal_population_plan_rejects_noncanonical_launch(change, message) -> None:
    with pytest.raises(ValueError, match=message):
        plan_population(formal_config().model_copy(update=change), real_bundle())


def test_population_plan_rejects_creator_source_exhaustion_across_months() -> None:
    bundle = real_bundle()
    july = list(bundle.configs["catalog_2026_07"])
    bundle.configs["catalog_2026_07"] = tuple(july[:100])

    with pytest.raises(ValueError, match="source support exhausted"):
        plan_population(formal_config(), bundle)
