"""Controlled population, workload, cohort and stop-rule contracts."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import FrozenContract
from .matrix import LoadMode, Topology


_SHA256 = r"^[a-f0-9]{64}$"
_COMMIT = r"^[a-f0-9]{40}$"


class FrozenPopulationReceipt(FrozenContract):
    dataset_repository: str = Field(min_length=1)
    dataset_revision: str = Field(pattern=_COMMIT)
    model_id: UUID
    model_revision: str = Field(pattern=_COMMIT)
    june_created: int = Field(ge=0)
    july_created: int = Field(ge=0)
    distinct_babel_count: int = Field(ge=0)
    indexed_count: int = Field(ge=0)
    formal_threshold: int = Field(gt=0)
    ordered_manifest_path: str = Field(min_length=1)
    ordered_manifest_sha256: str = Field(pattern=_SHA256)
    vector_bytes_sha256: str = Field(pattern=_SHA256)
    creator_source_unique: bool
    creator_count: int = Field(gt=0)
    round_robin_verified: bool
    cross_month_used_source_invariant_verified: bool
    creator_round_robin_manifest_sha256: str = Field(pattern=_SHA256)
    cross_month_used_sources_manifest_sha256: str = Field(pattern=_SHA256)

    def validate_for_formal(self) -> "FrozenPopulationReceipt":
        if (
            self.june_created != 5_000
            or self.july_created != 5_000
            or self.creator_count not in {50, 100, 500}
            or self.formal_threshold != 10_000
            or not self.round_robin_verified
            or not self.cross_month_used_source_invariant_verified
            or self.creator_round_robin_manifest_sha256
            == self.cross_month_used_sources_manifest_sha256
        ):
            raise ValueError(
                "formal population requires 5,000 rows per month, a supported cohort, "
                "round-robin placement, and the cross-month used-source invariant"
            )
        if (
            self.june_created + self.july_created != self.distinct_babel_count
            or self.distinct_babel_count != self.indexed_count
            or self.indexed_count < self.formal_threshold
        ):
            raise ValueError(
                "formal measurement requires created and indexed population equality "
                "at or above the threshold"
            )
        if not self.creator_source_unique:
            raise ValueError("formal population contains a repeated creator/source pair")
        return self

    @property
    def formal_measurement_ready(self) -> bool:
        try:
            self.validate_for_formal()
        except ValueError:
            return False
        return True


class FrozenWorkloadReceipt(FrozenContract):
    request_count: int = Field(gt=0)
    request_corpus_sha256: str = Field(pattern=_SHA256)
    feedback_sha256: str = Field(pattern=_SHA256)
    creator_schedule_sha256: str = Field(pattern=_SHA256)
    event_mix_sha256: str = Field(pattern=_SHA256)
    start_draws_sha256: str = Field(pattern=_SHA256)
    continuation_draws_sha256: str = Field(pattern=_SHA256)
    creator_schedule_scope: Literal["creator_local"]
    start_probability: Literal[0.4]
    continuation_probability: Literal[0.4]
    independent_draw_streams: Literal[True]

    @model_validator(mode="after")
    def draw_streams_are_independent(self) -> "FrozenWorkloadReceipt":
        if self.start_draws_sha256 == self.continuation_draws_sha256:
            raise ValueError("start and continuation draws must be independent")
        return self

    @property
    def replay_identity(self) -> tuple[str, ...]:
        return (
            self.request_corpus_sha256,
            self.feedback_sha256,
            self.creator_schedule_sha256,
            self.event_mix_sha256,
            self.start_draws_sha256,
            self.continuation_draws_sha256,
        )


@dataclass(frozen=True, slots=True)
class ScaleCondition:
    cohort_size: int
    topology: Topology
    load_mode: LoadMode

    @property
    def condition_id(self) -> str:
        return f"cohort-{self.cohort_size}.{self.topology}.{self.load_mode}.pgvector"


SelectedSplit = Literal["same_host_split", "same_host_isolated"]


def cohort_condition_matrix(
    cohort_size: int, *, selected_split: SelectedSplit = "same_host_split"
) -> tuple[ScaleCondition, ...]:
    allowed = {50, 100, 500, 1_000, 5_000, 10_000}
    if cohort_size not in allowed:
        raise ValueError("cohort must be one of the manual scaling ladder values")
    if selected_split not in {"same_host_split", "same_host_isolated"}:
        raise ValueError("selected split must be a split-service topology")
    topologies: tuple[Topology, ...] = (
        ("same_process", "same_host_split", "same_host_isolated")
        if cohort_size == 50
        else ("same_process", selected_split)
    )
    modes: tuple[LoadMode, ...] = (
        "serving_only",
        "training_no_activation",
        "training_and_activation",
    )
    return tuple(
        ScaleCondition(cohort_size, topology, mode)
        for topology in topologies
        for mode in modes
    )


class ManualCohortGate:
    """Require one explicit operator decision between every scale step."""

    ladder = (50, 100, 500, 1_000, 5_000, 10_000)

    def __init__(self) -> None:
        self.current_cohort = self.ladder[0]

    @property
    def required_ladder_reached(self) -> bool:
        return self.current_cohort >= 500

    def advance(
        self,
        *,
        completed_cohort: int,
        operator_approved: bool,
        stop_reasons: tuple[str, ...] = (),
    ) -> int:
        if completed_cohort != self.current_cohort:
            raise ValueError("completed cohort does not match the active manual gate")
        if stop_reasons:
            raise RuntimeError("a stop rule prohibits cohort advancement")
        if not operator_approved:
            raise PermissionError("operator approval is required for cohort advancement")
        position = self.ladder.index(self.current_cohort)
        if position == len(self.ladder) - 1:
            raise RuntimeError("the optional cohort ladder is exhausted")
        self.current_cohort = self.ladder[position + 1]
        return self.current_cohort


class ScaleWindow(FrozenContract):
    duration_seconds: float = Field(gt=0)
    memory_percent: float = Field(ge=0, le=100)
    disk_free_bytes: int = Field(ge=0)
    error_timeout_rate: float = Field(ge=0, le=1)
    kafka_lag: int = Field(ge=0)
    maximum_backpressure_verified: bool
    process_healthy: bool = True
    checkpoint_healthy: bool = True
    activation_healthy: bool = True


@dataclass(frozen=True, slots=True)
class ScaleStopDecision:
    stop: bool
    reasons: tuple[str, ...]


class ScaleStopEvaluator:
    def __init__(self, *, memory_safety_window_seconds: float = 30.0) -> None:
        if memory_safety_window_seconds <= 0:
            raise ValueError("memory safety window must be positive")
        self._memory_window = memory_safety_window_seconds
        self._memory_over_seconds = 0.0
        self._error_windows = 0
        self._previous_lag: int | None = None
        self._increasing_lag_windows = 0

    def observe(self, window: ScaleWindow) -> ScaleStopDecision:
        reasons: list[str] = []
        if window.disk_free_bytes < 10 * 1024**3:
            reasons.append("disk free space is below 10 GiB")
        if not window.process_healthy:
            reasons.append("process failed")
        if not window.checkpoint_healthy:
            reasons.append("checkpoint failed")
        if not window.activation_healthy:
            reasons.append("activation failed")
        if window.memory_percent > 90:
            self._memory_over_seconds += window.duration_seconds
        else:
            self._memory_over_seconds = 0.0
        if self._memory_over_seconds >= self._memory_window:
            seconds = int(self._memory_window)
            reasons.append(f"memory exceeded 90% for {seconds} seconds")
        self._error_windows = (
            self._error_windows + 1 if window.error_timeout_rate > 0.05 else 0
        )
        if self._error_windows >= 2:
            reasons.append("errors and timeouts exceeded 5% for two windows")
        lag_increased = (
            self._previous_lag is not None
            and window.kafka_lag > self._previous_lag
            and window.maximum_backpressure_verified
        )
        self._increasing_lag_windows = (
            self._increasing_lag_windows + 1 if lag_increased else 0
        )
        self._previous_lag = window.kafka_lag
        if self._increasing_lag_windows >= 2:
            reasons.append(
                "Kafka lag increased for two windows at verified maximum backpressure"
            )
        return ScaleStopDecision(bool(reasons), tuple(reasons))


class ControlledConditionReceipt(FrozenContract):
    condition_id: str = Field(min_length=1)
    cohort_size: int = Field(gt=0)
    ordered_population_sha256: str = Field(pattern=_SHA256)
    vector_bytes_sha256: str = Field(pattern=_SHA256)
    workload_identity: tuple[str, ...] = Field(min_length=6, max_length=6)
    created_count: int = Field(ge=0)
    indexed_count: int = Field(ge=0)

    @field_validator("workload_identity")
    @classmethod
    def workload_hashes_are_sha256(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(item) != 64
            or any(c not in "0123456789abcdef" for c in item)
            for item in value
        ):
            raise ValueError("workload identity must contain six SHA-256 values")
        return value


def validate_controlled_cohort(
    receipts: tuple[ControlledConditionReceipt, ...],
    *,
    expected_condition_ids: set[str],
    expected_population: FrozenPopulationReceipt,
    expected_workload: FrozenWorkloadReceipt,
    formal_threshold: int | None = None,
) -> tuple[ControlledConditionReceipt, ...]:
    receipt_ids = tuple(row.condition_id for row in receipts)
    if (
        not receipts
        or len(receipts) != len(expected_condition_ids)
        or len(set(receipt_ids)) != len(receipt_ids)
        or set(receipt_ids) != expected_condition_ids
    ):
        raise ValueError(
            "controlled cohort must contain each expected condition exactly once"
        )
    expected_population.validate_for_formal()
    if (
        formal_threshold is not None
        and formal_threshold != expected_population.formal_threshold
    ):
        raise ValueError("controlled cohort threshold cannot weaken frozen population")
    try:
        expected_cohorts = {
            int(condition_id.split(".", 1)[0].removeprefix("cohort-"))
            for condition_id in expected_condition_ids
        }
    except ValueError as error:
        raise ValueError("controlled cohort condition identity is malformed") from error
    if len(expected_cohorts) != 1:
        raise ValueError("controlled cohort condition identities mix cohort sizes")
    expected_cohort = next(iter(expected_cohorts))
    if any(row.cohort_size != expected_cohort for row in receipts):
        raise ValueError("receipt cohort differs from its expected condition cohort")
    baseline = receipts[0]
    identity = (
        baseline.cohort_size,
        baseline.ordered_population_sha256,
        baseline.vector_bytes_sha256,
        baseline.workload_identity,
    )
    if any(
        (
            row.cohort_size,
            row.ordered_population_sha256,
            row.vector_bytes_sha256,
            row.workload_identity,
        )
        != identity
        for row in receipts[1:]
    ):
        raise ValueError("controlled cohort population/workload drift detected")
    if (
        baseline.ordered_population_sha256
        != expected_population.ordered_manifest_sha256
        or baseline.vector_bytes_sha256 != expected_population.vector_bytes_sha256
        or baseline.workload_identity != expected_workload.replay_identity
    ):
        raise ValueError("controlled cohort differs from its expected frozen identity")
    if any(
        row.created_count != expected_population.distinct_babel_count
        or row.indexed_count != expected_population.indexed_count
        for row in receipts
    ):
        raise ValueError("controlled cohort counts differ from frozen population")
    return receipts


__all__ = [
    "ControlledConditionReceipt",
    "FrozenPopulationReceipt",
    "FrozenWorkloadReceipt",
    "ManualCohortGate",
    "ScaleCondition",
    "ScaleStopDecision",
    "ScaleStopEvaluator",
    "ScaleWindow",
    "cohort_condition_matrix",
    "validate_controlled_cohort",
]
