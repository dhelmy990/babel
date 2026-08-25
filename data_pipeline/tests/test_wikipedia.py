from __future__ import annotations

import bz2
import gc
import os
import sys
import tempfile
import tracemalloc
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.teacher import normalize_teacher_title  # noqa: E402
from babel_data.wikipedia import (  # noqa: E402
    READ_CHUNK_BYTES,
    CorruptWikipediaBzip,
    DuplicateWikipediaPageId,
    DuplicateWikipediaTitle,
    InvalidWikipediaPage,
    InvalidWikipediaSource,
    InvalidWikipediaUtf8,
    InvalidWikipediaXml,
    WikipediaLimitExceeded,
    WikipediaPage,
    extract_lead,
    iter_wikipedia_pages,
    normalize_title,
    resolve_redirect,
    wikitext_to_plain_text,
)


FIXTURE = Path(__file__).parent / "fixtures" / "enwiki-small.xml.bz2"
XMLNS = "http://www.mediawiki.org/xml/export-0.10/"


def write_dump(tmp_path: Path, pages: str, *, prolog: bytes | None = None) -> Path:
    payload = prolog or (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        + f'<mediawiki xmlns="{XMLNS}">'.encode()
    )
    if prolog is None:
        payload += pages.encode("utf-8") + b"</mediawiki>"
    path = tmp_path / "dump.xml.bz2"
    path.write_bytes(bz2.compress(payload))
    return path


def xml_page(
    title: str,
    page_id: int,
    text: str = "Lead.",
    *,
    namespace: int = 0,
    revision_id: int | None = 1,
) -> str:
    revision = ""
    if revision_id is not None:
        revision = f"<revision><id>{revision_id}</id><text>{text}</text></revision>"
    return (
        f"<page><title>{title}</title><ns>{namespace}</ns><id>{page_id}</id>"
        f"{revision}</page>"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("virtual_memory", "Virtual memory"),
        ("  multiple__\u00a0spaces ", "Multiple spaces"),
        ("cafe\u0301", "Café"),
        ("IndiGo", "IndiGo"),
        ("Indigo", "Indigo"),
        ("chorizo_\u200bde", "Chorizo \u200bde"),
    ],
)
def test_title_normalization_exactly_matches_teacher_contract(
    raw: str, expected: str
) -> None:
    assert normalize_title(raw) == expected
    assert normalize_title(raw) == normalize_teacher_title(raw)


def test_wikipedia_reexports_the_single_teacher_normalizer() -> None:
    assert normalize_title is normalize_teacher_title


def test_normalization_does_not_casefold_distinct_titles() -> None:
    assert normalize_title("IndiGo") != normalize_title("Indigo")


def test_fixture_streams_only_usable_namespace_zero_revision_pages() -> None:
    pages = list(iter_wikipedia_pages(FIXTURE))

    assert [page.page_id for page in pages] == [10, 11, 12, 16]
    assert pages[0].revision_id == 100
    assert pages[0].page_id != 9999
    assert all(isinstance(page, WikipediaPage) for page in pages)
    with pytest.raises(FrozenInstanceError):
        pages[0].page_id = 99


def test_last_dump_revision_is_used_and_revision_id_is_optional(tmp_path: Path) -> None:
    pages = (
        "<page><title>Newest</title><ns>0</ns><id>7</id>"
        "<revision><id>1</id><text>Old.</text></revision>"
        "<revision><id>2</id><text>New.</text></revision></page>"
        "<page><title>No revision ID</title><ns>0</ns><id>8</id>"
        "<revision><text>Useful.</text></revision></page>"
    )
    path = write_dump(tmp_path, pages)

    newest, optional = iter_wikipedia_pages(path)
    assert (newest.revision_id, newest.article_text) == (2, "New.")
    assert (optional.revision_id, optional.article_text) == (None, "Useful.")


def test_markup_conversion_and_lead_are_conservative_and_readable() -> None:
    pages = {page.canonical_title: page for page in iter_wikipedia_pages(FIXTURE)}
    page = pages["Virtual memory"]

    assert page.article_text == (
        "Virtual memory is a memory-management technique.\n\n"
        "History\nLater history with and ."
    )
    assert page.lead_text == "Virtual memory is a memory-management technique."
    assert page.model_text == (
        "Virtual memory\n\nVirtual memory is a memory-management technique."
    )
    assert "Citation" not in page.article_text
    assert "short description" not in page.article_text


def test_plain_text_handles_entities_links_tables_and_section_boundaries() -> None:
    raw = "Lead &amp; [[Page|label]].\n\n{|\n| hidden\n|}\n== Section ==\nBody"
    assert wikitext_to_plain_text(raw) == "Lead & label.\n\nSection\nBody"
    assert extract_lead(raw) == "Lead & label."


@pytest.mark.parametrize(
    "raw",
    [
        "#REDIRECT [[Target]]",
        "  #redirect: [[Target|ignored label]]\n",
    ],
)
def test_redirect_fallback_is_case_insensitive_and_normalized(raw: str) -> None:
    assert extract_lead(raw) == ""
    page = WikipediaPage(1, "Source", 2, "", "", "Target")
    assert page.model_text == "Source\n\n"


def test_extract_lead_excludes_deterministic_disambiguation_boilerplate() -> None:
    raw = "Mercury may refer to:\n\n* [[Mercury (planet)]]\n== Science ==\nLater"
    assert extract_lead(raw) == ""


def test_extract_lead_ignores_heading_markup_inside_removed_constructs() -> None:
    raw = (
        "First sentence.\n"
        "{{box|\n== Not a section ==\n}}\n"
        "Second sentence.\n"
        "<!--\n== Also not a section ==\n-->\n"
        "== Actual section ==\nLater"
    )

    assert extract_lead(raw) == "First sentence.\n\nSecond sentence."


def test_title_filter_keeps_redirect_closure_without_unrelated_article_text() -> None:
    pages = list(iter_wikipedia_pages(FIXTURE, title_filter={"VM alias"}))

    assert [page.canonical_title for page in pages] == [
        "Virtual memory",
        "VM overview",
        "VM alias",
    ]


def test_filtered_identity_state_is_candidate_bounded_in_python_memory(
    tmp_path: Path,
) -> None:
    pages = [xml_page(f"Unrelated {index}", index) for index in range(1, 5001)]
    pages.append(xml_page("Wanted", 6000))
    path = write_dump(tmp_path, "".join(pages))
    del pages
    gc.collect()

    tracemalloc.start()
    try:
        result = list(iter_wikipedia_pages(path, title_filter={"Wanted"}))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert [page.canonical_title for page in result] == ["Wanted"]
    assert peak < 4 * 1024 * 1024


def test_completed_non_page_subtrees_do_not_accumulate_in_dom(tmp_path: Path) -> None:
    children = [f"<junk>{'x' * 4096}</junk>" for _ in range(2000)]
    payload = (
        f'<mediawiki xmlns="{XMLNS}">'.encode()
        + "".join(children).encode()
        + b"</mediawiki>"
    )
    path = tmp_path / "non-page.xml.bz2"
    path.write_bytes(bz2.compress(payload))
    del children, payload
    gc.collect()

    tracemalloc.start()
    try:
        assert list(iter_wikipedia_pages(path)) == []
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 3 * 1024 * 1024


def test_redirect_metadata_and_fallback_form_consistent_chain() -> None:
    pages = list(iter_wikipedia_pages(FIXTURE))
    first = resolve_redirect("VM alias", pages)

    assert first.status == "resolved"
    assert first.page is not None
    assert first.page.page_id == 10
    assert first.chain == ("VM alias", "VM overview", "Virtual memory")


def test_redirect_outcomes_cover_cycle_missing_depth_and_ambiguity() -> None:
    article = WikipediaPage(1, "Target", 1, "text", "text", None)
    cycle_a = WikipediaPage(2, "A", 1, "", "", "B")
    cycle_b = WikipediaPage(3, "B", 1, "", "", "A")
    missing = WikipediaPage(4, "Missing source", 1, "", "", "Absent")
    duplicate = WikipediaPage(5, "target", 1, "other", "other", None)

    assert resolve_redirect("A", [cycle_a, cycle_b]).status == "redirect_cycle"
    assert (
        resolve_redirect("Missing source", [missing]).status
        == "redirect_target_missing"
    )
    assert (
        resolve_redirect("A", [cycle_a, cycle_b], max_depth=1).status
        == "redirect_depth_exceeded"
    )
    assert (
        resolve_redirect("Target", [article, duplicate]).status
        == "duplicate/ambiguous_title"
    )


def test_duplicate_page_ids_and_normalized_titles_are_explicit(tmp_path: Path) -> None:
    duplicate_id = write_dump(
        tmp_path, xml_page("One", 1) + xml_page("Two", 1)
    )
    with pytest.raises(DuplicateWikipediaPageId):
        list(iter_wikipedia_pages(duplicate_id))

    duplicate_title = write_dump(
        tmp_path, xml_page("Same_title", 1) + xml_page("Same title", 2)
    )
    with pytest.raises(DuplicateWikipediaTitle):
        list(iter_wikipedia_pages(duplicate_title))


def test_public_redirect_resolution_treats_duplicate_page_ids_as_ambiguous() -> None:
    pages = [
        WikipediaPage(1, "First", 1, "text", "text", None),
        WikipediaPage(1, "Second", 2, "text", "text", None),
        WikipediaPage(3, "Alias", 3, "", "", "Second"),
    ]

    assert resolve_redirect("First", pages).status == "duplicate/ambiguous_title"
    assert resolve_redirect("Alias", pages).status == "duplicate/ambiguous_title"


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"not-bzip", CorruptWikipediaBzip),
        (bz2.compress(b"<mediawiki><page></mediawiki>"), InvalidWikipediaXml),
        (
            bz2.compress(
                b'<?xml version="1.0" encoding="UTF-8"?><mediawiki>\xff</mediawiki>'
            ),
            InvalidWikipediaUtf8,
        ),
    ],
)
def test_corrupt_inputs_have_typed_errors(
    tmp_path: Path, payload: bytes, error: type[Exception]
) -> None:
    path = tmp_path / "bad.xml.bz2"
    path.write_bytes(payload)
    with pytest.raises(error):
        list(iter_wikipedia_pages(path))


@pytest.mark.parametrize("suffix", [b"garbage", b"BZh-not-a-valid-member"])
def test_trailing_non_bzip_bytes_are_rejected(tmp_path: Path, suffix: bytes) -> None:
    path = tmp_path / "trailing.xml.bz2"
    xml = f'<mediawiki xmlns="{XMLNS}"></mediawiki>'.encode()
    path.write_bytes(bz2.compress(xml) + suffix)

    with pytest.raises(CorruptWikipediaBzip):
        list(iter_wikipedia_pages(path))


def test_valid_concatenated_bzip_members_are_supported(tmp_path: Path) -> None:
    xml = (
        f'<mediawiki xmlns="{XMLNS}">'.encode()
        + xml_page("One", 1).encode()
        + b"</mediawiki>"
    )
    midpoint = len(xml) // 2
    path = tmp_path / "multistream.xml.bz2"
    path.write_bytes(bz2.compress(xml[:midpoint]) + bz2.compress(xml[midpoint:]))

    assert [page.page_id for page in iter_wikipedia_pages(path)] == [1]


def test_truncated_utf8_at_decompressed_eof_is_typed_as_utf8(tmp_path: Path) -> None:
    path = tmp_path / "truncated-utf8.xml.bz2"
    path.write_bytes(bz2.compress(f'<mediawiki xmlns="{XMLNS}">'.encode() + b"\xc3"))

    with pytest.raises(InvalidWikipediaUtf8):
        list(iter_wikipedia_pages(path))


@pytest.mark.parametrize(
    "page",
    [
        "<page><ns>0</ns><id>1</id><revision><text>x</text></revision></page>",
        "<page><title>X</title><id>1</id><revision><text>x</text></revision></page>",
        "<page><title>X</title><ns>0</ns><revision><text>x</text></revision></page>",
    ],
)
def test_missing_required_namespace_zero_identity_fields_are_typed(
    tmp_path: Path, page: str
) -> None:
    path = write_dump(tmp_path, page)
    with pytest.raises(InvalidWikipediaPage):
        list(iter_wikipedia_pages(path))


def test_absurd_page_id_and_malformed_present_revision_id_are_typed(
    tmp_path: Path,
) -> None:
    huge_id = write_dump(
        tmp_path,
        "<page><title>Huge</title><ns>0</ns><id>"
        + "9" * 5000
        + "</id><revision><id>1</id><text>x</text></revision></page>",
    )
    with pytest.raises(InvalidWikipediaPage):
        list(iter_wikipedia_pages(huge_id))

    malformed = write_dump(
        tmp_path,
        "<page><title>Bad revision</title><ns>0</ns><id>1</id>"
        "<revision><id>not-a-number</id><text>x</text></revision></page>",
    )
    with pytest.raises(InvalidWikipediaPage):
        list(iter_wikipedia_pages(malformed))


@pytest.mark.parametrize(
    "declaration",
    [
        b'<!DOCTYPE mediawiki [<!ENTITY x "boom">]>',
        b'<!ENTITY x "boom">',
    ],
)
def test_dtd_and_entity_declarations_are_rejected_before_parsing(
    tmp_path: Path, declaration: bytes
) -> None:
    path = tmp_path / "dtd.xml.bz2"
    path.write_bytes(
        bz2.compress(
            b'<?xml version="1.0"?>' + declaration + b"<mediawiki></mediawiki>"
        )
    )
    with pytest.raises(InvalidWikipediaXml, match="DTD|entity"):
        list(iter_wikipedia_pages(path))


def test_source_must_be_regular_single_link_and_not_symlink(tmp_path: Path) -> None:
    symlink = tmp_path / "link.xml.bz2"
    symlink.symlink_to(FIXTURE)
    with pytest.raises(InvalidWikipediaSource):
        list(iter_wikipedia_pages(symlink))

    with tempfile.TemporaryDirectory(dir=FIXTURE.parent) as directory:
        hardlink = Path(directory) / "hard.xml.bz2"
        os.link(FIXTURE, hardlink)
        with pytest.raises(InvalidWikipediaSource, match="single link"):
            list(iter_wikipedia_pages(hardlink))


def test_generator_close_releases_source_descriptors() -> None:
    before = len(os.listdir("/proc/self/fd"))
    pages = iter_wikipedia_pages(FIXTURE)
    next(pages)
    during = len(os.listdir("/proc/self/fd"))
    pages.close()
    after = len(os.listdir("/proc/self/fd"))

    assert during > before
    assert after == before


def test_decompressed_source_is_only_requested_with_bounded_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import babel_data.wikipedia as wikipedia

    requested_sizes: list[int] = []
    real_read = wikipedia._read_compressed

    def recording_read(source: object, size: int) -> bytes:
        requested_sizes.append(size)
        return real_read(source, size)

    monkeypatch.setattr(wikipedia, "_read_compressed", recording_read)

    assert list(iter_wikipedia_pages(FIXTURE))
    assert requested_sizes
    assert all(0 < size <= READ_CHUNK_BYTES for size in requested_sizes)


def test_valid_utf8_may_cross_the_preflight_chunk_boundary(tmp_path: Path) -> None:
    prefix = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        + f'<mediawiki xmlns="{XMLNS}">'.encode()
    )
    payload = (
        prefix
        + b"x" * (READ_CHUNK_BYTES - len(prefix) - 1)
        + "é".encode("utf-8")
        + b"</mediawiki>"
    )
    path = tmp_path / "boundary.xml.bz2"
    path.write_bytes(bz2.compress(payload))

    assert list(iter_wikipedia_pages(path)) == []


def test_decompressed_and_pre_dom_page_sizes_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.wikipedia as wikipedia

    path = write_dump(tmp_path, xml_page("Large", 1, "x" * 4096))
    monkeypatch.setattr(wikipedia, "MAX_XML_PAGE_BYTES", 1024)
    with pytest.raises(WikipediaLimitExceeded, match="page"):
        list(iter_wikipedia_pages(path))

    monkeypatch.setattr(wikipedia, "MAX_XML_PAGE_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(wikipedia, "MAX_DECOMPRESSED_BYTES", 100)
    with pytest.raises(WikipediaLimitExceeded, match="decompressed"):
        list(iter_wikipedia_pages(path))


@pytest.mark.parametrize(
    "embedded_markup",
    [
        "<!-- </page> -->",
        "<![CDATA[</page>]]>",
        "<?fake </page> ?>",
        '<x attr=">">still-page-content</x>',
    ],
)
def test_page_size_bound_uses_xml_lexical_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    embedded_markup: str,
) -> None:
    import babel_data.wikipedia as wikipedia

    page = (
        "<page><title>Large</title><ns>0</ns><id>1</id>"
        "<revision><id>1</id><text>"
        + embedded_markup
        + "x" * 4096
        + "</text></revision></page>"
    )
    path = write_dump(tmp_path, page)
    monkeypatch.setattr(wikipedia, "MAX_XML_PAGE_BYTES", 512)

    with pytest.raises(WikipediaLimitExceeded, match="page"):
        list(iter_wikipedia_pages(path))


@pytest.mark.parametrize(
    "xml",
    [
        (
            '<!-- <mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/"> -->'
            "<evil><page><title>X</title><ns>0</ns><id>1</id>"
            "<revision><id>1</id><text>x</text></revision></page></evil>"
        ),
        (
            '<mediawiki xmlns="urn:not-mediawiki"><page><title>X</title><ns>0</ns>'
            "<id>1</id><revision><id>1</id><text>x</text></revision></page></mediawiki>"
        ),
        (
            f'<mediawiki xmlns="{XMLNS}"><wrapper><page><title>X</title><ns>0</ns>'
            "<id>1</id><revision><id>1</id><text>x</text></revision>"
            "</page></wrapper></mediawiki>"
        ),
        (
            f'<mediawiki xmlns="{XMLNS}" xmlns:bad="urn:not-mediawiki">'
            "<bad:page><bad:title>X</bad:title><bad:ns>0</bad:ns><bad:id>1</bad:id>"
            "<bad:revision><bad:id>1</bad:id><bad:text>x</bad:text></bad:revision>"
            "</bad:page></mediawiki>"
        ),
    ],
)
def test_actual_root_namespace_and_direct_page_placement_are_required(
    tmp_path: Path, xml: str
) -> None:
    path = tmp_path / "wrong-root.xml.bz2"
    path.write_bytes(bz2.compress(xml.encode()))

    with pytest.raises(InvalidWikipediaXml, match="root|namespace|direct"):
        list(iter_wikipedia_pages(path))


def test_xml_depth_and_per_page_element_count_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.wikipedia as wikipedia

    deep = write_dump(
        tmp_path,
        xml_page("Deep", 1, "<a><b><c><d><e>x</e></d></c></b></a>"),
    )
    monkeypatch.setattr(wikipedia, "MAX_XML_DEPTH", 6)
    with pytest.raises(WikipediaLimitExceeded, match="depth"):
        list(iter_wikipedia_pages(deep))

    many = write_dump(
        tmp_path,
        xml_page("Many", 1, "".join("<i>x</i>" for _ in range(20))),
    )
    monkeypatch.setattr(wikipedia, "MAX_PAGE_ELEMENTS", 10)
    with pytest.raises(WikipediaLimitExceeded, match="elements"):
        list(iter_wikipedia_pages(many))


def test_filtered_passes_detect_path_replacement_and_keep_bound_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.wikipedia as wikipedia

    source = write_dump(tmp_path, xml_page("Wanted", 1))
    replacement = tmp_path / "replacement.xml.bz2"
    replacement.write_bytes(
        bz2.compress(
            b"<mediawiki>" + xml_page("Different", 2).encode() + b"</mediawiki>"
        )
    )
    real_closure = wikipedia._candidate_closure

    def replacing_closure(*args: object, **kwargs: object) -> set[str]:
        result = real_closure(*args, **kwargs)
        os.replace(replacement, source)
        return result

    monkeypatch.setattr(wikipedia, "_candidate_closure", replacing_closure)

    with pytest.raises(InvalidWikipediaSource, match="identity"):
        list(iter_wikipedia_pages(source, title_filter={"Wanted"}))


def test_wikitext_limits_reject_pathological_size_and_nesting() -> None:
    with pytest.raises(InvalidWikipediaXml, match="large"):
        wikitext_to_plain_text("x" * (16 * 1024 * 1024 + 1))
    with pytest.raises(InvalidWikipediaXml, match="nesting"):
        wikitext_to_plain_text("{{" * 65 + "x" + "}}" * 65)
