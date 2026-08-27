"""Monotonic nanosecond stage timing helpers."""

from __future__ import annotations

from time import perf_counter_ns


TIMING_STAGES = (
    "queue",
    "encode",
    "context",
    "ann",
    "filtering",
    "serialization",
    "serverTotal",
)


def measured(operation):
    start = perf_counter_ns()
    result = operation()
    return result, perf_counter_ns() - start


def server_timing_header(timings_ns: dict[str, int]) -> str:
    return ", ".join(
        f"{stage};dur={timings_ns[stage] / 1_000_000:.6f}"
        for stage in TIMING_STAGES
    )


__all__ = ["TIMING_STAGES", "measured", "perf_counter_ns", "server_timing_header"]
