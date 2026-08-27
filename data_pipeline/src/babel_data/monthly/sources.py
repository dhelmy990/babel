"""Pinned private-Hub source contracts for monthly semantic processing."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


PRIVATE_DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED = {
    ("clickstream", "streamable"),
    ("multistream_index", "indexed"),
    ("selected_multistream_ranges", "range_addressable"),
}


class SourcePolicyError(ValueError):
    """A monthly semantic source violates the remote-only source policy."""


@dataclass(frozen=True, slots=True)
class MonthlySourcePin:
    period: str
    repository: str
    revision: str
    path: str
    sha256: str
    kind: str
    access: str
    authoritative_url: str


@dataclass(frozen=True, slots=True)
class IndexedCandidate:
    period: str
    page_id: int
    canonical_title: str
    traffic: int
    offset: int
    end_exclusive: int


def normalize_dump_title(value: str) -> str:
    value = " ".join(value.replace("_", " ").split())
    if value and "a" <= value[0] <= "z":
        value = value[0].upper() + value[1:]
    return value


def read_clickstream_traffic(path: Path) -> dict[str, int]:
    """Aggregate real internal-link traffic by endpoint title."""
    traffic: dict[str, int] = defaultdict(int)
    with gzip.open(path, "rt", encoding="utf-8", errors="strict") as source:
        for line_number, line in enumerate(source, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 4:
                raise ValueError(f"malformed Clickstream line {line_number}")
            previous, current, kind, raw_count = fields
            if kind != "link":
                continue
            try:
                count = int(raw_count)
            except ValueError as error:
                raise ValueError(f"malformed Clickstream count at line {line_number}") from error
            if count <= 0:
                raise ValueError(f"nonpositive Clickstream count at line {line_number}")
            previous = normalize_dump_title(previous)
            current = normalize_dump_title(current)
            if previous and current and previous != current:
                traffic[previous] += count
                traffic[current] += count
    return dict(traffic)


def resolve_indexed_candidates(
    index_path: Path,
    traffic: Mapping[str, int],
    *,
    period: str,
    dump_size: int,
) -> list[IndexedCandidate]:
    """Resolve candidate titles through an exact multistream index in one pass."""
    if period not in {"2026-06", "2026-07"}:
        raise ValueError("period must be 2026-06 or 2026-07")
    if dump_size <= 0:
        raise ValueError("dump_size must be positive")
    wanted = {normalize_dump_title(title): count for title, count in traffic.items()}
    matches: dict[int, tuple[int, str, int]] = {}
    end_by_offset: dict[int, int] = {}
    selected_offsets: set[int] = set()
    previous_offset: int | None = None
    with bz2.open(index_path, "rt", encoding="utf-8", errors="strict") as source:
        for line_number, line in enumerate(source, 1):
            fields = line.rstrip("\n").split(":", 2)
            if len(fields) != 3:
                raise ValueError(f"malformed multistream index line {line_number}")
            try:
                offset, page_id = int(fields[0]), int(fields[1])
            except ValueError as error:
                raise ValueError(f"malformed multistream identity at line {line_number}") from error
            if offset < 0 or page_id <= 0:
                raise ValueError(f"invalid multistream identity at line {line_number}")
            if previous_offset is not None and offset != previous_offset:
                if previous_offset in selected_offsets:
                    end_by_offset[previous_offset] = offset
            previous_offset = offset
            title = normalize_dump_title(fields[2])
            count = wanted.get(title)
            if count is None:
                continue
            prior = matches.get(page_id)
            if prior is not None and prior != (offset, title, count):
                raise ValueError(f"duplicate multistream page ID: {page_id}")
            matches[page_id] = (offset, title, count)
            selected_offsets.add(offset)
    for offset in selected_offsets:
        end_by_offset.setdefault(offset, dump_size)
    return [
        IndexedCandidate(period, page_id, title, count, offset, end_by_offset[offset])
        for page_id, (offset, title, count) in sorted(matches.items())
    ]


def _http_range(url: str, start: int, end_exclusive: int) -> bytes:
    expected = end_exclusive - start
    request = urllib.request.Request(
        url,
        headers={
            "Range": f"bytes={start}-{end_exclusive - 1}",
            "User-Agent": "babel-monthly-snapshot/1.0",
        },
    )
    last_error: BaseException | None = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                status = getattr(response, "status", response.getcode())
                payload = response.read(expected + 1)
                if status != 206 or len(payload) != expected:
                    raise ValueError(
                        f"Wikimedia range response mismatch: status={status}, "
                        f"bytes={len(payload)}, expected={expected}"
                    )
                return payload
        except (OSError, urllib.error.HTTPError, ValueError) as error:
            last_error = error
            if attempt == 5:
                break
            time.sleep(min(2**attempt, 16))
    raise ValueError(f"cannot fetch authoritative byte range {start}:{end_exclusive}") from last_error


def download_selected_ranges(
    authoritative_url: str,
    ranges: Sequence[tuple[int, int]],
    output_path: Path,
    *,
    fetch: Callable[[str, int, int], bytes] = _http_range,
    workers: int = 12,
) -> dict[str, object]:
    """Fetch exact complete multistream members without scanning the full XML."""
    if not authoritative_url.startswith("https://dumps.wikimedia.org/"):
        raise ValueError("selected ranges require an authoritative Wikimedia URL")
    unique = sorted(set(ranges))
    if not unique or any(start < 0 or end <= start for start, end in unique):
        raise ValueError("selected ranges must be nonempty positive intervals")
    if any(left[1] > right[0] for left, right in zip(unique, unique[1:])):
        raise ValueError("selected multistream ranges overlap")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.partial")
    digest = hashlib.sha256()
    size = 0
    with temporary.open("wb") as output:
        for batch_start in range(0, len(unique), 64):
            batch = unique[batch_start : batch_start + 64]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                payloads = pool.map(
                    lambda interval: fetch(authoritative_url, interval[0], interval[1]),
                    batch,
                )
                for interval, payload in zip(batch, payloads, strict=True):
                    expected = interval[1] - interval[0]
                    if len(payload) != expected:
                        raise ValueError("selected range fetch returned the wrong byte count")
                    output.write(payload)
                    digest.update(payload)
                    size += len(payload)
        output.flush()
    temporary.replace(output_path)
    return {
        "authoritative_url": authoritative_url,
        "range_count": len(unique),
        "bytes": size,
        "sha256": digest.hexdigest(),
    }


def assert_semantic_read_allowed(source: MonthlySourcePin) -> None:
    """Reject live, floating, fixture, and full-dump discovery inputs."""
    if source.period not in {"2026-06", "2026-07"}:
        raise SourcePolicyError("monthly source period is unsupported")
    if source.repository != PRIVATE_DATASET_REPOSITORY:
        raise SourcePolicyError("semantic reads require the private dataset repository")
    if not _HEX40.fullmatch(source.revision):
        raise SourcePolicyError("semantic reads require an exact 40-character commit")
    if not _HEX64.fullmatch(source.sha256):
        raise SourcePolicyError("semantic reads require a SHA-256 receipt")
    path = source.path.casefold()
    if any(word in path for word in ("fixture", "demo", "monthly-80")):
        raise SourcePolicyError("demo fixtures are forbidden for engineering snapshots")
    if (source.kind, source.access) not in _ALLOWED:
        raise SourcePolicyError("source is not indexed or streamable under policy v1")
    if source.kind in {"wikipedia_xml", "pagelinks_sql"} or (
        (path.endswith(".xml.bz2") or path.endswith(".sql.gz"))
        and source.kind != "selected_multistream_ranges"
    ):
        raise SourcePolicyError("full XML/SQL discovery scans are forbidden")
    if not source.path.startswith(f"sources/monthly/{source.period}/"):
        raise SourcePolicyError("monthly source path is outside the pinned source namespace")
    if not source.authoritative_url.startswith("https://dumps.wikimedia.org/"):
        raise SourcePolicyError("source origin is not an authoritative Wikimedia dump")


__all__ = [
    "MonthlySourcePin",
    "IndexedCandidate",
    "PRIVATE_DATASET_REPOSITORY",
    "SourcePolicyError",
    "assert_semantic_read_allowed",
    "download_selected_ranges",
    "normalize_dump_title",
    "read_clickstream_traffic",
    "resolve_indexed_candidates",
]
