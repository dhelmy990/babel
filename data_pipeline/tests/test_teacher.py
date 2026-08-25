from __future__ import annotations

import hashlib
import json
import stat
import struct
import sys
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data import teacher  # noqa: E402
from babel_data.teacher import (  # noqa: E402
    DuplicateTeacherTitle,
    InvalidTeacherArchive,
    InvalidTeacherHeader,
    InvalidTeacherVector,
    TeacherRecord,
    build_teacher_inventory,
    iter_teacher,
    parse_vector_line,
)


FIXTURE = Path(__file__).parent / "fixtures" / "teacher-small.zip"
DIMENSION = 100


def vector_row(title: str, values: list[str] | None = None) -> str:
    return f"{title} {' '.join(values or ['1'] * DIMENSION)}"


def teacher_body(
    rows: list[str], *, count: int | None = None, dimension: int = DIMENSION
) -> bytes:
    declared_count = len(rows) if count is None else count
    return (f"{declared_count} {dimension}\n" + "\n".join(rows) + "\n").encode(
        "utf-8"
    )


def write_zip(
    path: Path,
    body: bytes,
    *,
    member_name: str = "teacher.txt",
    compression: int = zipfile.ZIP_DEFLATED,
    extra_members: tuple[tuple[str, bytes], ...] = (),
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(member_name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = compression
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, body)
        for name, payload in extra_members:
            extra = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            extra.compress_type = compression
            extra.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(extra, payload)
    return path


def one_row_zip(tmp_path: Path, row: str) -> Path:
    return write_zip(tmp_path / "teacher.zip", teacher_body([row]))


def mark_members_encrypted(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while (position := payload.find(signature, start)) >= 0:
            flags = struct.unpack_from("<H", payload, position + flag_offset)[0]
            struct.pack_into("<H", payload, position + flag_offset, flags | 1)
            start = position + 4
    path.write_bytes(payload)


def corrupt_first_compressed_byte(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    local_header = payload.index(b"PK\x03\x04")
    filename_length, extra_length = struct.unpack_from(
        "<HH", payload, local_header + 26
    )
    compressed_data = local_header + 30 + filename_length + extra_length
    payload[compressed_data] ^= 0xFF
    path.write_bytes(payload)


def test_fixture_streams_immutable_owned_float32_records() -> None:
    records = list(iter_teacher(FIXTURE))

    assert [record.title for record in records] == [
        "Virtual_memory",
        "Café",
        "Quantum__mechanics",
    ]
    assert all(isinstance(record, TeacherRecord) for record in records)
    assert all(record.vector.shape == (DIMENSION,) for record in records)
    assert all(record.vector.dtype == np.float32 for record in records)
    assert all(record.vector.flags.c_contiguous for record in records)
    assert all(record.vector.flags.owndata for record in records)
    assert all(not record.vector.flags.writeable for record in records)
    with pytest.raises(FrozenInstanceError):
        records[0].title = "changed"
    with pytest.raises(ValueError):
        records[0].vector[0] = 2


def test_parse_vector_line_accepts_crlf_and_preserves_title() -> None:
    record = parse_vector_line(vector_row("Virtual_memory") + "\r\n")

    assert record.title == "Virtual_memory"
    np.testing.assert_array_equal(record.vector, np.ones(DIMENSION, dtype=np.float32))


def test_public_line_parser_rejects_non_teacher_dimension() -> None:
    with pytest.raises(InvalidTeacherVector, match="exactly 100"):
        parse_vector_line(vector_row("Wrong", ["1"] * 99), dimension=99)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"1 100 3\n",
        b"1\t100\n",
        b"-1 100\n",
        b"0 100\n",
        b"100000001 100\n",
        b"one 100\n",
        b"1 99\n",
        b"1 101\n",
    ],
)
def test_malformed_headers_are_rejected(tmp_path: Path, body: bytes) -> None:
    path = write_zip(tmp_path / "teacher.zip", body)

    with pytest.raises(InvalidTeacherHeader, match="header|count|dimension"):
        list(iter_teacher(path))


def test_absurd_declared_count_is_rejected_before_allocating_title_index(
    tmp_path: Path,
) -> None:
    path = write_zip(tmp_path / "teacher.zip", b"10000001 100\n")

    with pytest.raises(InvalidTeacherHeader, match="between 1 and 10000000"):
        list(iter_teacher(path))


@pytest.mark.parametrize(
    "row",
    [
        vector_row("Short", ["1"] * 99),
        vector_row("Long", ["1"] * 101),
        vector_row("Bad", ["1"] * 99 + ["not-a-number"]),
        vector_row("Nan", ["1"] * 99 + ["nan"]),
        vector_row("Inf", ["1"] * 99 + ["inf"]),
        vector_row("NegativeInf", ["1"] * 99 + ["-inf"]),
        vector_row("FloatOverflow", ["1"] * 99 + ["3.5e38"]),
        vector_row("ParserOverflow", ["1"] * 99 + ["1e10000"]),
        vector_row("Zero", ["0"] * 100),
        vector_row("UnderflowZero", ["1e-100"] * 100),
        "",
    ],
)
def test_invalid_vector_rows_report_row_and_title(
    tmp_path: Path, row: str
) -> None:
    path = one_row_zip(tmp_path, row)

    with pytest.raises(InvalidTeacherVector) as raised:
        list(iter_teacher(path))

    assert raised.value.row == 2
    assert "row 2" in str(raised.value)


@pytest.mark.parametrize("title", ["Bad\x00Title", "Bad\tTitle", "Bad\x7fTitle"])
def test_control_characters_in_titles_are_rejected(tmp_path: Path, title: str) -> None:
    path = one_row_zip(tmp_path, vector_row(title))

    with pytest.raises(InvalidTeacherVector, match="control"):
        list(iter_teacher(path))


def test_invalid_utf8_is_rejected_with_row_context(tmp_path: Path) -> None:
    body = b"1 100\nBad\xff " + b"1 " * 99 + b"1\n"
    path = write_zip(tmp_path / "teacher.zip", body)

    with pytest.raises(InvalidTeacherVector, match="row 2.*UTF-8"):
        list(iter_teacher(path))


def test_overlong_line_is_rejected_before_unbounded_buffering(tmp_path: Path) -> None:
    path = write_zip(
        tmp_path / "teacher.zip",
        teacher_body([vector_row("A" * 70_000)]),
        compression=zipfile.ZIP_STORED,
    )

    with pytest.raises(InvalidTeacherVector, match="row 2.*too long"):
        list(iter_teacher(path))


@pytest.mark.parametrize(
    ("first", "duplicate"),
    [
        ("Virtual_memory", "virtual__memory"),
        ("Café", "CAFE\u0301"),
    ],
)
def test_duplicate_titles_use_documented_normalization(
    tmp_path: Path, first: str, duplicate: str
) -> None:
    path = write_zip(
        tmp_path / "teacher.zip",
        teacher_body([vector_row(first), vector_row(duplicate)]),
    )

    with pytest.raises(DuplicateTeacherTitle) as raised:
        list(iter_teacher(path))

    assert raised.value.row == 3
    assert raised.value.first_row == 2
    assert raised.value.title == duplicate
    assert "row 2" in str(raised.value)
    assert "row 3" in str(raised.value)


def test_declared_count_rejects_premature_eof(tmp_path: Path) -> None:
    path = write_zip(
        tmp_path / "teacher.zip", teacher_body([vector_row("Only")], count=2)
    )

    with pytest.raises(InvalidTeacherHeader, match="declared 2.*found 1"):
        list(iter_teacher(path))


def test_declared_count_rejects_extra_nonblank_row(tmp_path: Path) -> None:
    body = teacher_body([vector_row("First"), vector_row("Extra")], count=1)
    path = write_zip(tmp_path / "teacher.zip", body)

    with pytest.raises(InvalidTeacherHeader, match="extra.*row 3"):
        list(iter_teacher(path))


def test_blank_lines_after_declared_records_are_allowed(tmp_path: Path) -> None:
    body = teacher_body([vector_row("Only")]) + b" \r\n\n"
    path = write_zip(tmp_path / "teacher.zip", body)

    assert [record.title for record in iter_teacher(path)] == ["Only"]


def test_empty_archive_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w"):
        pass

    with pytest.raises(InvalidTeacherArchive, match="no regular data member"):
        list(iter_teacher(path))


def test_directory_only_archive_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "directories.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("vectors/", b"")

    with pytest.raises(InvalidTeacherArchive, match="no regular data member"):
        list(iter_teacher(path))


def test_multiple_regular_members_are_rejected_as_ambiguous(tmp_path: Path) -> None:
    body = teacher_body([vector_row("Only")])
    path = write_zip(
        tmp_path / "ambiguous.zip", body, extra_members=(("second.txt", body),)
    )

    with pytest.raises(InvalidTeacherArchive, match="multiple regular data members"):
        list(iter_teacher(path))


@pytest.mark.parametrize(
    "member_name", ["../teacher.txt", "/teacher.txt", r"dir\teacher.txt"]
)
def test_suspicious_member_names_are_rejected(
    tmp_path: Path, member_name: str
) -> None:
    path = write_zip(
        tmp_path / "suspicious.zip",
        teacher_body([vector_row("Only")]),
        member_name=member_name,
    )

    with pytest.raises(InvalidTeacherArchive, match="unsafe member name"):
        list(iter_teacher(path))


def test_symlink_member_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("teacher.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")

    with pytest.raises(InvalidTeacherArchive, match="not a regular file"):
        list(iter_teacher(path))


def test_encrypted_member_is_rejected_before_opening(tmp_path: Path) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    mark_members_encrypted(path)

    with pytest.raises(InvalidTeacherArchive, match="encrypted"):
        list(iter_teacher(path))


def test_unsupported_compression_is_rejected(tmp_path: Path) -> None:
    path = write_zip(
        tmp_path / "bzip2.zip",
        teacher_body([vector_row("Only")]),
        compression=zipfile.ZIP_BZIP2,
    )

    with pytest.raises(InvalidTeacherArchive, match="compression"):
        list(iter_teacher(path))


def test_suspicious_compression_ratio_is_rejected(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "bomb.zip", b"A" * 1_000_000)

    with pytest.raises(InvalidTeacherArchive, match="compression ratio"):
        list(iter_teacher(path))


def test_corrupt_deflate_stream_is_reported_as_typed_archive_error(
    tmp_path: Path,
) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    corrupt_first_compressed_byte(path)

    with pytest.raises(InvalidTeacherArchive, match="cannot read teacher archive"):
        list(iter_teacher(path))


def test_member_reads_are_bounded_and_never_use_readlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[GuardedMember] = []
    original_open = zipfile.ZipFile.open

    class GuardedMember:
        def __init__(self, raw: zipfile.ZipExtFile) -> None:
            self.raw = raw
            self.sizes: list[int] = []
            self.closed = False

        def readline(self, size: int = -1) -> bytes:
            assert size > 0
            self.sizes.append(size)
            return self.raw.readline(size)

        def __enter__(self) -> GuardedMember:
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def close(self) -> None:
            self.closed = True
            self.raw.close()

    def guarded_open(
        archive: zipfile.ZipFile, *args: object, **kwargs: object
    ) -> GuardedMember:
        wrapped = GuardedMember(original_open(archive, *args, **kwargs))
        opened.append(wrapped)
        return wrapped

    monkeypatch.setattr(zipfile.ZipFile, "open", guarded_open)

    assert len(list(iter_teacher(FIXTURE))) == 3
    assert opened and opened[0].sizes
    assert opened[0].closed


def test_closing_generator_early_closes_member_and_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archives: list[TrackingZipFile] = []
    members: list[TrackingMember] = []

    class TrackingMember:
        def __init__(self, raw: zipfile.ZipExtFile) -> None:
            self.raw = raw
            self.closed = False

        def readline(self, size: int = -1) -> bytes:
            return self.raw.readline(size)

        def __enter__(self) -> TrackingMember:
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def close(self) -> None:
            self.closed = True
            self.raw.close()

    class TrackingZipFile(zipfile.ZipFile):
        closed_by_parser = False

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            archives.append(self)

        def open(self, *args: object, **kwargs: object) -> TrackingMember:
            member = TrackingMember(super().open(*args, **kwargs))
            members.append(member)
            return member

        def close(self) -> None:
            self.closed_by_parser = True
            super().close()

    monkeypatch.setattr(teacher.zipfile, "ZipFile", TrackingZipFile)
    records = iter_teacher(FIXTURE)

    assert next(records).title == "Virtual_memory"
    records.close()

    assert archives[0].closed_by_parser
    assert members[0].closed


def test_inventory_is_deterministic_complete_and_uses_stable_norms(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    result = build_teacher_inventory(FIXTURE, first)
    build_teacher_inventory(FIXTURE, second)

    assert result == first
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    inventory = json.loads(first.read_text(encoding="utf-8"))
    assert inventory == {
        "actual_count": 3,
        "declared_count": 3,
        "dimension": 100,
        "duplicate_policy": (
            "NFC, underscores-to-spaces, whitespace-collapse, Unicode-casefold"
        ),
        "norm_statistics": {"max": 10.0, "mean": 6.0, "min": 3.0},
        "schema_version": "teacher-inventory-v1",
        "source_byte_size": FIXTURE.stat().st_size,
        "source_filename": FIXTURE.name,
        "source_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "vector_dtype": "float32",
    }


def test_inventory_streams_teacher_records_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = teacher.iter_teacher
    calls = 0

    def counting_iter(path: Path):
        nonlocal calls
        calls += 1
        yield from original(path)

    monkeypatch.setattr(teacher, "iter_teacher", counting_iter)

    build_teacher_inventory(FIXTURE, tmp_path / "inventory.json")

    assert calls == 1


def test_inventory_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "inventory.json"
    output.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="inventory output already exists"):
        build_teacher_inventory(FIXTURE, output)

    assert output.read_text(encoding="utf-8") == "keep me\n"


def test_inventory_validates_source_before_hashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "directory"
    source.mkdir()

    def unexpected_hash(path: Path) -> str:
        raise AssertionError(f"hashed invalid source {path}")

    monkeypatch.setattr(teacher, "_sha256", unexpected_hash)

    with pytest.raises(InvalidTeacherArchive, match="not a regular file"):
        build_teacher_inventory(source, tmp_path / "inventory.json")


def test_inventory_wraps_hash_io_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failed_hash(path: Path) -> str:
        raise OSError(f"cannot hash {path}")

    monkeypatch.setattr(teacher, "_sha256", failed_hash)

    with pytest.raises(InvalidTeacherArchive, match="cannot hash teacher archive"):
        build_teacher_inventory(FIXTURE, tmp_path / "inventory.json")


def test_inventory_failure_leaves_no_output_or_temporary_file(tmp_path: Path) -> None:
    invalid = write_zip(tmp_path / "invalid.zip", b"1 99\n")
    output = tmp_path / "inventory.json"

    with pytest.raises(InvalidTeacherHeader):
        build_teacher_inventory(invalid, output)

    assert not output.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["invalid.zip"]
