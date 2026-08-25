from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    TeacherAudit,
    TeacherExclusion,
    TeacherRecord,
    UnsafeTeacherOutput,
    build_teacher_inventory,
    iter_teacher,
    normalize_teacher_title,
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


def raw_teacher_body(rows: list[bytes], *, count: int | None = None) -> bytes:
    declared_count = len(rows) if count is None else count
    return f"{declared_count} {DIMENSION}\n".encode("ascii") + b"\n".join(rows) + b"\n"


def raw_vector_row(title: bytes, *, trailing_space: bool = True) -> bytes:
    row = title + b" " + b" ".join([b"1"] * DIMENSION)
    return row + (b" " if trailing_space else b"")


def test_numpy_is_a_direct_data_pipeline_runtime_dependency() -> None:
    pyproject = REPOSITORY_ROOT / "data_pipeline" / "pyproject.toml"

    assert '"numpy==2.2.6"' in pyproject.read_text(encoding="utf-8")


def test_fixture_preserves_real_source_trailing_space_bytes() -> None:
    expected = raw_teacher_body(
        [
            raw_vector_row(b"Virtual_memory"),
            b"Caf\xc3\xa9 " + b" ".join([b"3"] + [b"0"] * 99) + b" ",
            b"Quantum__mechanics " + b" ".join([b"-0.5"] * 100) + b" ",
        ]
    )

    with zipfile.ZipFile(FIXTURE) as archive:
        assert archive.namelist() == ["teacher-small.txt"]
        assert archive.read("teacher-small.txt") == expected


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


def corrupt_utf8_central_directory_filename(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    central_header = payload.index(b"PK\x01\x02")
    flags = struct.unpack_from("<H", payload, central_header + 8)[0]
    struct.pack_into("<H", payload, central_header + 8, flags | 0x800)
    filename_length = struct.unpack_from("<H", payload, central_header + 28)[0]
    assert filename_length > 0
    payload[central_header + 46] = 0xFF
    path.write_bytes(payload)


def declare_excessive_eocd_entries(path: Path, count: int) -> None:
    payload = bytearray(path.read_bytes())
    eocd = payload.rindex(b"PK\x05\x06")
    struct.pack_into("<HH", payload, eocd + 8, count, count)
    path.write_bytes(payload)


def promote_to_canonical_zip64(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    eocd_offset = payload.rindex(b"PK\x05\x06")
    (
        _signature,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_size,
    ) = struct.unpack_from("<4s4H2LH", payload, eocd_offset)
    assert disk_number == central_disk == 0
    assert comment_size == 0
    zip64_eocd = struct.pack(
        "<4sQ2H2L4Q",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
    )
    zip64_locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, eocd_offset, 1)
    classic_eocd = bytearray(payload[eocd_offset:])
    struct.pack_into("<HHLL", classic_eocd, 8, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    path.write_bytes(
        payload[:eocd_offset] + zip64_eocd + zip64_locator + classic_eocd
    )


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


@pytest.mark.parametrize("ending", [" \n", " \r\n"])
def test_real_source_style_row_accepts_one_trailing_ascii_space(
    ending: str,
) -> None:
    values = ["-0.125", "2.5e-03", "+4"] + ["0.25"] * 97
    record = parse_vector_line(vector_row("Virtual_memory", values) + ending)

    assert record.title == "Virtual_memory"
    assert record.vector.shape == (100,)
    assert record.vector[:3].tolist() == pytest.approx([-0.125, 0.0025, 4.0])


@pytest.mark.parametrize(
    "line",
    [
        " " + vector_row("Leading"),
        vector_row("Internal").replace(" 1 ", "  1 ", 1),
        vector_row("Trailing") + "  \n",
    ],
)
def test_only_one_trailing_ascii_space_is_tolerated(line: str) -> None:
    with pytest.raises(InvalidTeacherVector, match="exactly 100|nonblank"):
        parse_vector_line(line)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("virtual_memory", "Virtual memory"),
        ("  not representable as token  ", "Not representable as token"),
        ("cafe\u0301", "Café"),
        ("multiple__\u00a0spaces", "Multiple spaces"),
        ("IndiGo", "IndiGo"),
        ("ACID", "ACID"),
    ],
)
def test_teacher_title_normalization_matches_mediawiki_first_letter(
    title: str, expected: str
) -> None:
    assert normalize_teacher_title(title) == expected


def test_real_title_preserves_zero_width_format_characters(tmp_path: Path) -> None:
    title = "Chorizo_\u200b\u200bde_Pamplona"
    path = one_row_zip(tmp_path, vector_row(title))

    [record] = iter_teacher(path)

    assert record.title == title
    assert normalize_teacher_title(title) == "Chorizo \u200b\u200bde Pamplona"


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


def test_invalid_utf8_titles_are_quarantined_with_reversible_audit(
    tmp_path: Path,
) -> None:
    invalid_titles = [
        b"Bad\xff",
        b"Truncated\xc2",
        b"Truncated\xe2\x82",
        b"Truncated\xf0\x9f\x92",
        b"Bad\xe2(\xa1",
    ]
    rows = [raw_vector_row(b"Valid_first")]
    rows.extend(raw_vector_row(title) for title in invalid_titles)
    rows.append(raw_vector_row(b"Valid_last"))
    path = write_zip(tmp_path / "teacher.zip", raw_teacher_body(rows))
    audit = TeacherAudit()

    records = list(iter_teacher(path, audit=audit))

    assert [record.title for record in records] == ["Valid_first", "Valid_last"]
    assert audit.declared_count == 7
    assert audit.raw_record_count == 7
    assert audit.emitted_count == 2
    assert audit.exclusion_count == 5
    assert audit.exclusions_by_reason == {"invalid_title_utf8": 5}
    assert audit.exclusions_truncated is False
    assert all(isinstance(item, TeacherExclusion) for item in audit.exclusions)
    assert [item.row for item in audit.exclusions] == [2, 3, 4, 5, 6]
    assert [item.reason for item in audit.exclusions] == [
        "invalid_title_utf8"
    ] * 5
    assert [item.raw_title_hex for item in audit.exclusions] == [
        title.hex() for title in invalid_titles
    ]
    assert all("byte offset" in item.detail for item in audit.exclusions)


def test_default_iteration_deterministically_skips_only_invalid_utf8_titles(
    tmp_path: Path,
) -> None:
    rows = [raw_vector_row(b"Bad\xff"), raw_vector_row(b"Valid")]
    path = write_zip(tmp_path / "teacher.zip", raw_teacher_body(rows))

    assert [record.title for record in iter_teacher(path)] == ["Valid"]


def test_invalid_utf8_in_numeric_payload_remains_fatal(tmp_path: Path) -> None:
    numeric = [b"1"] * DIMENSION
    numeric[-1] = b"\xff"
    row = b"Valid " + b" ".join(numeric) + b" "
    path = write_zip(tmp_path / "teacher.zip", raw_teacher_body([row]))
    audit = TeacherAudit()

    with pytest.raises(InvalidTeacherVector, match="numeric.*ASCII|decimal"):
        list(iter_teacher(path, audit=audit))

    assert audit.raw_record_count == 1
    assert audit.exclusion_count == 0


def test_header_count_compares_raw_records_not_emitted_records(tmp_path: Path) -> None:
    rows = [raw_vector_row(b"Bad\xff"), raw_vector_row(b"Valid")]
    path = write_zip(tmp_path / "teacher.zip", raw_teacher_body(rows, count=2))
    audit = TeacherAudit()

    assert [record.title for record in iter_teacher(path, audit=audit)] == ["Valid"]
    assert audit.raw_record_count == audit.declared_count == 2
    assert audit.emitted_count == 1


def test_exclusion_records_are_bounded_but_counts_remain_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(teacher, "MAX_EXCLUSION_RECORDS", 2)
    rows = [raw_vector_row(b"Bad\xff" + bytes([index])) for index in range(5)]
    path = write_zip(tmp_path / "teacher.zip", raw_teacher_body(rows))
    audit = TeacherAudit()

    assert list(iter_teacher(path, audit=audit)) == []
    assert audit.exclusion_count == 5
    assert len(audit.exclusions) == 2
    assert audit.exclusions_truncated is True


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
        ("cafe\u0301", "café"),
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


@pytest.mark.parametrize(
    ("first", "second"),
    [("IndiGo", "Indigo"), ("Acid", "ACID"), ("Café", "CAFÉ")],
)
def test_remainder_case_does_not_collapse_distinct_mediawiki_titles(
    tmp_path: Path, first: str, second: str
) -> None:
    path = write_zip(
        tmp_path / "teacher.zip",
        teacher_body([vector_row(first), vector_row(second)]),
    )

    assert [record.title for record in iter_teacher(path)] == [first, second]


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


def test_eocd_entry_limit_is_checked_before_zipfile_allocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    declare_excessive_eocd_entries(path, 50_000)
    constructed = False

    class UnexpectedZipFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal constructed
            constructed = True
            raise AssertionError("ZipFile allocated before EOCD preflight")

    monkeypatch.setattr(teacher.zipfile, "ZipFile", UnexpectedZipFile)

    with pytest.raises(InvalidTeacherArchive, match="50000.*1024|too many"):
        list(iter_teacher(path))

    assert constructed is False


def test_forged_low_eocd_count_is_rejected_before_zipfile_allocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "many-members.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(teacher.MAX_ARCHIVE_MEMBERS + 1):
            archive.writestr(f"entry-{index:04d}", b"")
    declare_excessive_eocd_entries(path, 1)
    constructed = False
    pread_sizes: list[int] = []
    real_pread = os.pread

    def bounded_pread(file_descriptor: int, size: int, offset: int) -> bytes:
        pread_sizes.append(size)
        assert size <= 65_557
        return real_pread(file_descriptor, size, offset)

    class UnexpectedZipFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal constructed
            constructed = True
            raise AssertionError("ZipFile allocated before header-count preflight")

    monkeypatch.setattr(teacher.os, "pread", bounded_pread)
    monkeypatch.setattr(teacher.zipfile, "ZipFile", UnexpectedZipFile)

    with pytest.raises(InvalidTeacherArchive, match="entry count|too many"):
        list(iter_teacher(path))

    assert constructed is False
    assert len(pread_sizes) <= 4


def test_eocd_declared_central_directory_must_fit_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    payload = bytearray(path.read_bytes())
    eocd = payload.rindex(b"PK\x05\x06")
    struct.pack_into("<L", payload, eocd + 12, len(payload) + 1)
    path.write_bytes(payload)
    constructed = False

    class UnexpectedZipFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal constructed
            constructed = True
            raise AssertionError("ZipFile allocated before bounds preflight")

    monkeypatch.setattr(teacher.zipfile, "ZipFile", UnexpectedZipFile)

    with pytest.raises(InvalidTeacherArchive, match="central directory.*bounds"):
        list(iter_teacher(path))

    assert constructed is False


def test_canonical_zip64_central_directory_preflight_accepts_safe_archive(
    tmp_path: Path,
) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    promote_to_canonical_zip64(path)
    payload = path.read_bytes()

    assert b"PK\x06\x06" in payload
    assert b"PK\x06\x07" in payload

    assert [record.title for record in iter_teacher(path)] == ["Only"]


@pytest.mark.parametrize(
    ("field_offset", "message"),
    [
        (32, "ZIP64 end-of-directory"),
        (40, "central directory.*bounds"),
    ],
)
def test_canonical_zip64_rejects_forged_count_and_bounds(
    tmp_path: Path, field_offset: int, message: str
) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    promote_to_canonical_zip64(path)
    payload = bytearray(path.read_bytes())
    zip64_eocd = payload.index(b"PK\x06\x06")
    original = struct.unpack_from("<Q", payload, zip64_eocd + field_offset)[0]
    struct.pack_into("<Q", payload, zip64_eocd + field_offset, original + 1)
    path.write_bytes(payload)

    with pytest.raises(InvalidTeacherArchive, match=message):
        list(iter_teacher(path))


def test_corrupt_deflate_stream_is_reported_as_typed_archive_error(
    tmp_path: Path,
) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    corrupt_first_compressed_byte(path)

    with pytest.raises(InvalidTeacherArchive, match="cannot read teacher archive"):
        list(iter_teacher(path))


def test_invalid_utf8_zip_filename_is_typed_and_closes_archive_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = one_row_zip(tmp_path, vector_row("Only"))
    corrupt_utf8_central_directory_filename(path)
    opened: list[object] = []
    original_open = zipfile.io.open

    def tracking_open(*args: object, **kwargs: object):
        handle = original_open(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(teacher.zipfile.io, "open", tracking_open)

    with pytest.raises(InvalidTeacherArchive) as raised:
        list(iter_teacher(path))

    assert isinstance(raised.value.__cause__, UnicodeDecodeError)
    assert opened
    assert all(getattr(handle, "closed") for handle in opened)


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
            "NFC, underscores-to-spaces, Unicode-whitespace-collapse, "
            "MediaWiki-first-letter-capitalization"
        ),
        "emitted_count": 3,
        "exclusion_count": 0,
        "exclusions": [],
        "exclusions_by_reason": {},
        "exclusions_truncated": False,
        "norm_statistics": {"max": 10.0, "mean": 6.0, "min": 3.0},
        "raw_record_count": 3,
        "schema_version": "teacher-inventory-v1",
        "source_byte_size": FIXTURE.stat().st_size,
        "source_filename": FIXTURE.name,
        "source_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "valid_count": 3,
        "vector_dtype": "float32",
    }


def test_inventory_streams_teacher_records_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = teacher._iter_teacher_descriptor
    calls = 0

    def counting_iter(source: object, audit: TeacherAudit):
        nonlocal calls
        calls += 1
        yield from original(source, audit)

    monkeypatch.setattr(teacher, "_iter_teacher_descriptor", counting_iter)

    build_teacher_inventory(FIXTURE, tmp_path / "inventory.json")

    assert calls == 1


def test_inventory_accounts_for_raw_rows_and_title_exclusions(
    tmp_path: Path,
) -> None:
    invalid_title = b"Broken\xe2\x82"
    rows = [
        raw_vector_row(b"Valid_one"),
        raw_vector_row(invalid_title),
        raw_vector_row(b"Valid_two"),
    ]
    source = write_zip(tmp_path / "teacher.zip", raw_teacher_body(rows))
    output = tmp_path / "inventory.json"

    build_teacher_inventory(source, output)

    inventory = json.loads(output.read_text(encoding="utf-8"))
    assert inventory["declared_count"] == 3
    assert inventory["raw_record_count"] == 3
    assert inventory["actual_count"] == 2
    assert inventory["emitted_count"] == 2
    assert inventory["valid_count"] == 2
    assert inventory["exclusion_count"] == 1
    assert inventory["exclusions_by_reason"] == {"invalid_title_utf8": 1}
    assert inventory["exclusions_truncated"] is False
    assert inventory["exclusions"] == [
        {
            "detail": "unexpected end of data at title byte offset 6",
            "raw_title_hex": invalid_title.hex(),
            "reason": "invalid_title_utf8",
            "row": 2,
        }
    ]


def test_inventory_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "inventory.json"
    output.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="inventory output already exists"):
        build_teacher_inventory(FIXTURE, output)

    assert output.read_text(encoding="utf-8") == "keep me\n"


def test_existing_inventory_is_rejected_before_reading_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"
    output.write_text("keep me\n", encoding="utf-8")

    def unexpected_hash(file_descriptor: int) -> str:
        raise AssertionError(f"hashed source fd {file_descriptor}")

    monkeypatch.setattr(teacher, "_sha256_descriptor", unexpected_hash)

    with pytest.raises(FileExistsError, match="already exists"):
        build_teacher_inventory(FIXTURE, output)


def test_output_parent_is_bound_before_source_streaming(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    moved_parent = tmp_path / "moved"
    output = output_parent / "inventory.json"
    original = teacher._iter_teacher_descriptor

    def substituting_iter(opened: object, audit: TeacherAudit):
        output_parent.rename(moved_parent)
        output_parent.mkdir()
        yield from original(opened, audit)

    monkeypatch.setattr(teacher, "_iter_teacher_descriptor", substituting_iter)

    with pytest.raises(UnsafeTeacherOutput, match="parent identity changed"):
        build_teacher_inventory(FIXTURE, output)

    assert not output.exists()
    assert not (moved_parent / "inventory.json").exists()


def test_teacher_source_symlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "teacher.zip"
    source.symlink_to(FIXTURE)

    with pytest.raises(InvalidTeacherArchive, match="safely open|symbolic|regular"):
        list(iter_teacher(source))


def test_teacher_source_with_external_hard_link_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "teacher.zip"
    shutil.copyfile(FIXTURE, source)
    os.link(source, tmp_path / "external-alias.zip")

    with pytest.raises(InvalidTeacherArchive, match="hard.?link|single link"):
        list(iter_teacher(source))


def test_inventory_rejects_symlinked_output_parent(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(UnsafeTeacherOutput, match="output parent"):
        build_teacher_inventory(FIXTURE, linked_parent / "inventory.json")

    assert not (real_parent / "inventory.json").exists()


def test_inventory_detects_temporary_file_hard_link_injection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"
    attacker_alias = tmp_path / "attacker-alias.json"
    real_link = os.link

    def aliasing_link(
        source: object,
        destination: object,
        *args: object,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        **kwargs: object,
    ) -> None:
        real_link(
            source,
            attacker_alias.name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=True,
        )
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=True,
        )

    monkeypatch.setattr(teacher.os, "link", aliasing_link)

    with pytest.raises(UnsafeTeacherOutput, match="hard.?link"):
        build_teacher_inventory(FIXTURE, output)

    assert attacker_alias.exists()
    assert output.exists()
    assert output.samefile(attacker_alias)
    assert not any(path.name.endswith(".tmp") for path in tmp_path.iterdir())


def test_inventory_never_unlinks_racer_that_replaces_published_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"
    evidence = tmp_path / "published-evidence.json"
    attacker_alias = tmp_path / "attacker-alias.json"
    real_link = os.link
    real_entry_at = teacher._entry_at
    output_checks = 0
    published_stat: os.stat_result | None = None

    def aliasing_link(
        source: object,
        destination: object,
        *args: object,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        **kwargs: object,
    ) -> None:
        real_link(
            source,
            attacker_alias.name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=True,
        )
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=True,
        )

    def racing_entry_at(parent_descriptor: int, filename: str):
        nonlocal output_checks, published_stat
        if filename != output.name:
            return real_entry_at(parent_descriptor, filename)
        output_checks += 1
        if output_checks == 3:
            published_stat = real_entry_at(parent_descriptor, filename)
            assert published_stat is not None
            os.rename(
                output.name,
                evidence.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            racer = os.open(
                output.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_descriptor,
            )
            try:
                os.write(racer, b"racing evidence\n")
            finally:
                os.close(racer)
            return published_stat
        if output_checks == 4 and published_stat is not None:
            return published_stat
        return real_entry_at(parent_descriptor, filename)

    monkeypatch.setattr(teacher.os, "link", aliasing_link)
    monkeypatch.setattr(teacher, "_entry_at", racing_entry_at)

    with pytest.raises(UnsafeTeacherOutput, match="hard.?link"):
        build_teacher_inventory(FIXTURE, output)

    assert output.read_bytes() == b"racing evidence\n"
    assert evidence.exists()
    assert attacker_alias.exists()


def test_inventory_no_clobber_race_preserves_racing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"

    def racing_link(
        source: object,
        destination: object,
        *args: object,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        **kwargs: object,
    ) -> None:
        assert dst_dir_fd is not None
        raced = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(raced, b"racing output\n")
        finally:
            os.close(raced)
        raise FileExistsError(destination)

    monkeypatch.setattr(teacher.os, "link", racing_link)

    with pytest.raises(FileExistsError, match="already exists"):
        build_teacher_inventory(FIXTURE, output)

    assert output.read_bytes() == b"racing output\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["inventory.json"]


def test_inventory_detects_output_parent_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    moved_parent = tmp_path / "moved"
    output = output_parent / "inventory.json"
    real_link = os.link

    def replacing_parent_link(
        source: object,
        destination: object,
        *args: object,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        **kwargs: object,
    ) -> None:
        output_parent.rename(moved_parent)
        output_parent.mkdir()
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=True,
        )

    monkeypatch.setattr(teacher.os, "link", replacing_parent_link)

    with pytest.raises(UnsafeTeacherOutput, match="parent identity changed"):
        build_teacher_inventory(FIXTURE, output)

    assert not output.exists()
    assert (moved_parent / "inventory.json").exists()
    assert not any(path.name.endswith(".tmp") for path in moved_parent.iterdir())


def test_inventory_temporary_write_failure_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"

    def failed_write(file_descriptor: int, payload: bytes) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(teacher, "_write_all", failed_write)

    with pytest.raises(OSError, match="injected write failure"):
        build_teacher_inventory(FIXTURE, output)

    assert list(tmp_path.iterdir()) == []


def test_inventory_uses_unnamed_temp_without_cleanup_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"
    real_open = os.open
    used_unnamed_temp = False

    def tracking_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal used_unnamed_temp
        if isinstance(path, str) and path.startswith(f".{output.name}."):
            raise AssertionError("named inventory temp reintroduces unlink race")
        if path == "." and flags & getattr(os, "O_TMPFILE", 0):
            used_unnamed_temp = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(teacher.os, "open", tracking_open)

    build_teacher_inventory(FIXTURE, output)

    assert used_unnamed_temp is True
    assert output.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["inventory.json"]


def test_inventory_initial_temp_fstat_failure_closes_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"
    real_open_temp = teacher._open_inventory_temp
    real_fstat = os.fstat
    temp_descriptor: int | None = None
    failed = False

    def tracking_open_temp(parent_descriptor: int, output_name: str):
        nonlocal temp_descriptor
        result = real_open_temp(parent_descriptor, output_name)
        temp_descriptor = result[0]
        return result

    def failing_once_fstat(file_descriptor: int):
        nonlocal failed
        if file_descriptor == temp_descriptor and not failed:
            failed = True
            raise OSError("injected initial temp fstat failure")
        return real_fstat(file_descriptor)

    monkeypatch.setattr(teacher, "_open_inventory_temp", tracking_open_temp)
    monkeypatch.setattr(teacher.os, "fstat", failing_once_fstat)

    with pytest.raises(OSError, match="initial temp fstat"):
        build_teacher_inventory(FIXTURE, output)

    assert temp_descriptor is not None
    with pytest.raises(OSError):
        real_fstat(temp_descriptor)
    assert list(tmp_path.iterdir()) == []


def test_inventory_hashes_and_parses_one_open_source_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "teacher.zip"
    shutil.copyfile(FIXTURE, source)
    output = tmp_path / "inventory.json"
    real_open = os.open
    source_open_count = 0

    def tracking_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal source_open_count
        if path == source.name and dir_fd is not None:
            source_open_count += 1
            assert flags & getattr(os, "O_NOFOLLOW", 0)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(teacher.os, "open", tracking_open)

    build_teacher_inventory(source, output)

    assert source_open_count == 1


def test_inventory_detects_source_mutation_between_parse_and_rehash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "teacher.zip"
    shutil.copyfile(FIXTURE, source)
    output = tmp_path / "inventory.json"
    original = teacher._iter_teacher_descriptor

    def mutating_iter(opened: object, audit: TeacherAudit):
        yield from original(opened, audit)
        with source.open("ab") as target:
            target.write(b"hostile mutation")

    monkeypatch.setattr(teacher, "_iter_teacher_descriptor", mutating_iter)

    with pytest.raises(InvalidTeacherArchive, match="changed"):
        build_teacher_inventory(source, output)

    assert not output.exists()


def test_inventory_fsyncs_stable_output_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "inventory.json"
    real_fsync = os.fsync
    fsynced_modes: list[int] = []

    def tracking_fsync(file_descriptor: int) -> None:
        fsynced_modes.append(os.fstat(file_descriptor).st_mode)
        real_fsync(file_descriptor)

    monkeypatch.setattr(teacher.os, "fsync", tracking_fsync)

    build_teacher_inventory(FIXTURE, output)

    assert any(stat.S_ISREG(mode) for mode in fsynced_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)


def test_inventory_validates_source_before_hashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "directory"
    source.mkdir()

    def unexpected_hash(file_descriptor: int) -> str:
        raise AssertionError(f"hashed invalid source fd {file_descriptor}")

    monkeypatch.setattr(teacher, "_sha256_descriptor", unexpected_hash)

    with pytest.raises(InvalidTeacherArchive, match="not a regular file"):
        build_teacher_inventory(source, tmp_path / "inventory.json")


def test_inventory_wraps_hash_io_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failed_hash(file_descriptor: int) -> str:
        raise OSError(f"cannot hash fd {file_descriptor}")

    monkeypatch.setattr(teacher, "_sha256_descriptor", failed_hash)

    with pytest.raises(InvalidTeacherArchive, match="cannot hash teacher archive"):
        build_teacher_inventory(FIXTURE, tmp_path / "inventory.json")


def test_inventory_failure_leaves_no_output_or_temporary_file(tmp_path: Path) -> None:
    invalid = write_zip(tmp_path / "invalid.zip", b"1 99\n")
    output = tmp_path / "inventory.json"

    with pytest.raises(InvalidTeacherHeader):
        build_teacher_inventory(invalid, output)

    assert not output.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["invalid.zip"]
