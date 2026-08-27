"""Resource observations for services, host, GPU and learning health."""

from __future__ import annotations

import asyncio
import time
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import FrozenContract


class ResourceObservationV1(FrozenContract):
    schemaVersion: Literal[1]
    benchmarkRunId: UUID
    conditionId: str = Field(min_length=1)
    observedAtMonotonicNs: int = Field(ge=0)
    service: str = Field(min_length=1)
    pid: int | None = Field(default=None, gt=0)
    cpuPercent: float | None = Field(default=None, ge=0)
    rssBytes: int | None = Field(default=None, ge=0)
    threadCount: int | None = Field(default=None, ge=0)
    processReadBytes: int | None = Field(default=None, ge=0)
    processWriteBytes: int | None = Field(default=None, ge=0)
    hostMemoryUsedBytes: int | None = Field(default=None, ge=0)
    hostDiskReadBytes: int | None = Field(default=None, ge=0)
    hostDiskWriteBytes: int | None = Field(default=None, ge=0)
    hostNetworkRxBytes: int | None = Field(default=None, ge=0)
    hostNetworkTxBytes: int | None = Field(default=None, ge=0)
    gpuAvailable: bool
    gpuUtilizationPercent: float | None = Field(default=None, ge=0, le=100)
    gpuMemoryUsedBytes: int | None = Field(default=None, ge=0)
    kafkaLag: int | None = Field(default=None, ge=0)
    trainingStepRate: float | None = Field(default=None, ge=0)
    checkpointVersion: int | None = Field(default=None, ge=0)
    activationVersion: int | None = Field(default=None, ge=0)
    activationDurationNs: int | None = Field(default=None, ge=0)
    trainerVersion: int | None = Field(default=None, ge=0)
    servingVersion: int | None = Field(default=None, ge=0)
    versionStaleness: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def availability_and_versions_are_consistent(self) -> "ResourceObservationV1":
        if not self.gpuAvailable and (
            self.gpuUtilizationPercent is not None
            or self.gpuMemoryUsedBytes is not None
        ):
            raise ValueError("unavailable GPU metrics must be null")
        if self.versionStaleness is not None:
            if self.trainerVersion is None or self.servingVersion is None:
                raise ValueError("version staleness requires both versions")
            if self.versionStaleness != self.trainerVersion - self.servingVersion:
                raise ValueError(
                    "version staleness does not match trainer-serving versions"
                )
        return self


HostProvider = Callable[[], Mapping[str, int]]
ProcessProvider = Callable[[str, int], Mapping[str, int | float]]
GpuProvider = Callable[[], Mapping[str, int | float] | None]


class ResourceSampler:
    def __init__(
        self,
        *,
        benchmark_run_id: UUID,
        condition_id: str,
        clock: Callable[[], int] = time.monotonic_ns,
        host_provider: HostProvider,
        process_provider: ProcessProvider,
        gpu_provider: GpuProvider,
    ) -> None:
        self._run_id = benchmark_run_id
        self._condition = condition_id
        self._clock = clock
        self._host = host_provider
        self._process = process_provider
        self._gpu = gpu_provider

    def sample(
        self,
        *,
        services: Mapping[str, int],
        kafka_lag: int | None = None,
        training_step_rate: float | None = None,
        checkpoint_version: int | None = None,
        activation_version: int | None = None,
        activation_duration_ns: int | None = None,
        trainer_version: int | None = None,
        serving_version: int | None = None,
    ) -> tuple[ResourceObservationV1, ...]:
        observed = self._clock()
        gpu = self._gpu()
        staleness = None
        if trainer_version is not None and serving_version is not None:
            staleness = max(0, trainer_version - serving_version)
        common = {
            "schemaVersion": 1,
            "benchmarkRunId": self._run_id,
            "conditionId": self._condition,
            "observedAtMonotonicNs": observed,
            "gpuAvailable": gpu is not None,
            "gpuUtilizationPercent": None
            if gpu is None
            else gpu.get("gpuUtilizationPercent"),
            "gpuMemoryUsedBytes": None
            if gpu is None
            else gpu.get("gpuMemoryUsedBytes"),
        }
        host = ResourceObservationV1(
            **common,
            service="host",
            **self._host(),
            kafkaLag=kafka_lag,
            trainingStepRate=training_step_rate,
            checkpointVersion=checkpoint_version,
            activationVersion=activation_version,
            activationDurationNs=activation_duration_ns,
            trainerVersion=trainer_version,
            servingVersion=serving_version,
            versionStaleness=staleness,
        )
        processes = tuple(
            ResourceObservationV1(
                **common,
                service=service,
                pid=pid,
                **self._process(service, pid),
            )
            for service, pid in sorted(services.items())
        )
        return (host, *processes)


class PeriodicResourceCollector:
    """Bounded periodic sampler suitable for one benchmark condition."""

    def __init__(
        self,
        sampler: ResourceSampler,
        *,
        services: Mapping[str, int],
        interval_seconds: float = 1.0,
        maximum_samples: int = 100_000,
        health_provider: Callable[[], Mapping[str, int | float | None]] = lambda: {},
    ) -> None:
        if interval_seconds <= 0 or maximum_samples <= 0:
            raise ValueError("resource collection bounds must be positive")
        self._sampler = sampler
        self._services = dict(services)
        self._interval = interval_seconds
        self._maximum = maximum_samples
        self._health = health_provider
        self.rows: list[ResourceObservationV1] = []
        self.sample_count = 0

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set() and self.sample_count < self._maximum:
            health = self._health()
            self.rows.extend(
                self._sampler.sample(
                    services=self._services,
                    kafka_lag=health.get("kafka_lag"),
                    training_step_rate=health.get("training_step_rate"),
                    checkpoint_version=health.get("checkpoint_version"),
                    trainer_version=health.get("trainer_version"),
                    serving_version=health.get("serving_version"),
                    activation_version=health.get("activation_version"),
                    activation_duration_ns=health.get("activation_duration_ns"),
                )
            )
            self.sample_count += 1
            if self.sample_count >= self._maximum:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass


def psutil_host_provider() -> Mapping[str, int]:
    import psutil

    memory = psutil.virtual_memory()
    disk = psutil.disk_io_counters()
    network = psutil.net_io_counters()
    return {
        "hostMemoryUsedBytes": int(memory.used),
        "hostDiskReadBytes": int(disk.read_bytes),
        "hostDiskWriteBytes": int(disk.write_bytes),
        "hostNetworkRxBytes": int(network.bytes_recv),
        "hostNetworkTxBytes": int(network.bytes_sent),
    }


def psutil_process_provider(service: str, pid: int) -> Mapping[str, int | float]:
    import psutil

    process = psutil.Process(pid)
    io = process.io_counters()
    return {
        "cpuPercent": float(process.cpu_percent(interval=None)),
        "rssBytes": int(process.memory_info().rss),
        "threadCount": int(process.num_threads()),
        "processReadBytes": int(io.read_bytes),
        "processWriteBytes": int(io.write_bytes),
    }


def nvidia_smi_gpu_provider() -> Mapping[str, int | float] | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    result = subprocess.run(
        [
            executable,
            "--query-gpu=utilization.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    utilization, memory_mib = result.stdout.splitlines()[0].split(",", 1)
    return {
        "gpuUtilizationPercent": float(utilization.strip()),
        "gpuMemoryUsedBytes": int(float(memory_mib.strip()) * 1024 * 1024),
    }


def default_resource_sampler(
    benchmark_run_id: UUID, condition_id: str
) -> ResourceSampler:
    return ResourceSampler(
        benchmark_run_id=benchmark_run_id,
        condition_id=condition_id,
        host_provider=psutil_host_provider,
        process_provider=psutil_process_provider,
        gpu_provider=nvidia_smi_gpu_provider,
    )


__all__ = [
    "PeriodicResourceCollector",
    "ResourceObservationV1",
    "ResourceSampler",
    "default_resource_sampler",
    "nvidia_smi_gpu_provider",
    "psutil_host_provider",
    "psutil_process_provider",
]
