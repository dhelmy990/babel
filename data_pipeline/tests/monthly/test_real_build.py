from __future__ import annotations

import bz2
import copy
import gzip
from pathlib import Path

import pytest

from babel_data.monthly.build import (
    build_hidden_relations,
    build_observable_catalog,
    extract_selected_articles,
    read_induced_clickstream,
)
from babel_data.monthly.sources import (
    MonthlySourcePin,
    SourcePolicyError,
    assert_semantic_read_allowed,
    download_selected_ranges,
    read_clickstream_traffic,
    resolve_indexed_candidates,
)


def source_pin(**overrides: object) -> MonthlySourcePin:
    values: dict[str, object] = {
        "period": "2026-06",
        "repository": "dhelmy990/babel-wikipedia-experiment",
        "revision": "a" * 40,
        "path": "sources/monthly/2026-06/clickstream-enwiki-2026-06.tsv.gz",
        "sha256": "b" * 64,
        "kind": "clickstream",
        "access": "streamable",
        "authoritative_url": (
            "https://dumps.wikimedia.org/other/clickstream/2026-06/"
            "clickstream-enwiki-2026-06.tsv.gz"
        ),
    }
    values.update(overrides)
    return MonthlySourcePin(**values)  # type: ignore[arg-type]


def test_semantic_reads_require_exact_private_hf_pin() -> None:
    assert_semantic_read_allowed(source_pin())

    for changes in (
        {"revision": "main"},
        {"repository": "wikimedia/wikipedia"},
        {"path": "fixtures/monthly-80.jsonl"},
        {"path": "enwiki-20260601-pages-articles.xml.bz2", "kind": "wikipedia_xml"},
        {"path": "enwiki-20260601-pagelinks.sql.gz", "kind": "pagelinks_sql"},
        {"access": "unindexed"},
    ):
        with pytest.raises(SourcePolicyError):
            assert_semantic_read_allowed(source_pin(**changes))


def article(page_id: int, period: str = "2026-06") -> dict[str, object]:
    return {
        "page_id": page_id,
        "canonical_title": f"Article {page_id}",
        "wikidata_id": None,
        "lead_text": f"Lead {page_id}.",
        "first_section_text": f"Section {page_id}.",
        "source_revision_id": page_id + 100,
        "redirect_titles": [],
        "period": period,
    }


def test_observable_catalog_has_only_safe_bounded_text_and_no_hidden_fields() -> None:
    rows = build_observable_catalog([article(1), article(2)], source_snapshot="2026-06-01")

    assert rows[0]["article_text"] == "Lead 1.\n\nSection 1."
    assert rows[0]["release_scope"] == "10k_timeboxed_engineering_snapshot"
    assert not ({"graph_neighbors", "clickstream_n", "hidden_relevance"} & rows[0].keys())

    leaked = article(3)
    leaked["hidden_relevance"] = 0.9
    with pytest.raises(ValueError, match="hidden field"):
        build_observable_catalog([leaked], source_snapshot="2026-06-01")


def test_hidden_relations_are_induced_real_deduplicated_and_capped() -> None:
    catalog = build_observable_catalog(
        [article(1), article(2), article(3)], source_snapshot="2026-06-01"
    )
    links = [(1, 2), (1, 2), (2, 3), (3, 99), (2, 2)]
    clickstream = [(1, 2, 40), (2, 3, 10), (3, 99, 500), (2, 2, 99)]

    hidden = build_hidden_relations(catalog, links, clickstream, cap=1)

    assert hidden.edges == ({
        "period": "2026-06",
        "source_article_key": "enwiki:1",
        "target_article_key": "enwiki:2",
    },)
    assert len(hidden.clickstream) == 1
    assert hidden.clickstream[0]["n"] == 40
    assert hidden.edge_available == 2
    assert hidden.clickstream_available == 2
    assert hidden.edge_cap == hidden.clickstream_cap == 1


def test_catalog_rejects_duplicate_keys_and_period_drift() -> None:
    duplicate = article(1)
    with pytest.raises(ValueError, match="duplicate page_id"):
        build_observable_catalog(
            [duplicate, copy.deepcopy(duplicate)], source_snapshot="2026-06-01"
        )
    with pytest.raises(ValueError, match="one period"):
        build_observable_catalog(
            [article(1), article(2, "2026-07")], source_snapshot="2026-06-01"
        )


def test_real_source_scans_clickstream_and_multistream_index(tmp_path: Path) -> None:
    clickstream = tmp_path / "click.tsv.gz"
    with gzip.open(clickstream, "wt", encoding="utf-8") as output:
        output.write("Alpha\tBeta\tlink\t10\n")
        output.write("Alpha\tGamma\tlink\t7\n")
        output.write("other-search\tBeta\texternal\t999\n")
    traffic = read_clickstream_traffic(clickstream)
    assert traffic == {"Alpha": 17, "Beta": 10, "Gamma": 7}

    index = tmp_path / "index.txt.bz2"
    index.write_bytes(
        bz2.compress(b"0:1:Alpha\n0:2:Other\n100:3:Beta\n200:4:Gamma\n300:5:Tail\n")
    )
    resolved = resolve_indexed_candidates(
        index, traffic, period="2026-06", dump_size=500
    )
    assert [(row.page_id, row.offset, row.end_exclusive) for row in resolved] == [
        (1, 0, 100),
        (3, 100, 200),
        (4, 200, 300),
    ]


def test_extracts_selected_real_pages_and_induced_clickstream(tmp_path: Path) -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/">
<page><title>Alpha</title><ns>0</ns><id>1</id><revision><id>11</id><text>Alpha lead.\n\n== History ==\nUseful history.\n\n== Notes ==\nLater.</text></revision></page>
<page><title>Beta</title><ns>0</ns><id>2</id><revision><id>12</id><text>Beta lead.\n\n== Details ==\nUseful details.</text></revision></page>
</mediawiki>"""
    ranges = tmp_path / "ranges.xml.bz2"
    ranges.write_bytes(bz2.compress(xml))

    articles = extract_selected_articles(
        ranges, {1, 2}, period="2026-06"
    )

    assert articles[0]["lead_text"] == "Alpha lead."
    assert articles[0]["first_section_text"] == "Useful history."
    assert articles[0]["source_revision_id"] == 11

    clickstream = tmp_path / "clickstream.tsv.gz"
    with gzip.open(clickstream, "wt", encoding="utf-8") as output:
        output.write("Alpha\tBeta\tlink\t20\n")
        output.write("Beta\tOutside\tlink\t30\n")
        output.write("Alpha\tBeta\tother\t999\n")
    assert read_induced_clickstream(clickstream, {"Alpha": 1, "Beta": 2}) == [
        (1, 2, 20)
    ]


def test_selected_range_download_is_exact_ordered_and_deduplicated(tmp_path: Path) -> None:
    source = b"AAAABBBBCCCCDDDD"
    output = tmp_path / "selected.bz2"

    receipt = download_selected_ranges(
        "https://dumps.wikimedia.org/enwiki/snapshot.xml.bz2",
        [(8, 12), (0, 4), (8, 12)],
        output,
        fetch=lambda _url, start, end: source[start:end],
    )

    assert output.read_bytes() == b"AAAACCCC"
    assert receipt["range_count"] == 2
    assert receipt["bytes"] == 8
