from __future__ import annotations

from uuid import UUID

import pytest

from babel_online.observable import CreatedBabel, DuplicateCreatorSource, ensure_unique_sources


RUN = UUID("00000000-0000-5000-8000-000000000001")
CREATOR = UUID("00000000-0000-5000-8000-000000000101")


def created(identifier: int, source: str, creator: UUID = CREATOR) -> CreatedBabel:
    return CreatedBabel(
        babelId=UUID(f"00000000-0000-5000-8000-{identifier:012d}"),
        runId=RUN,
        creatorId=creator,
        sourceArticleKey=source,
        title="Observable note",
        text="Observable text.",
        createdAtNs=identifier,
    )


def test_creator_cannot_create_the_same_source_twice() -> None:
    with pytest.raises(DuplicateCreatorSource):
        ensure_unique_sources(
            [created(1, "enwiki:593"), created(2, "enwiki:593")]
        )

    other = UUID("00000000-0000-5000-8000-000000000102")
    ensure_unique_sources(
        [created(1, "enwiki:593"), created(2, "enwiki:593", other)]
    )
