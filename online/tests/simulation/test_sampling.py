from __future__ import annotations

import pytest

from babel_online.simulation.sampling import EligibleSupportExhausted, SourceSampler


def test_creator_never_samples_one_source_twice() -> None:
    first = SourceSampler(["enwiki:1", "enwiki:2", "enwiki:3"], seed=7)
    second = SourceSampler(["enwiki:1", "enwiki:2", "enwiki:3"], seed=7)

    drawn = [first.take(), first.take(), first.take()]

    assert drawn == [second.take(), second.take(), second.take()]
    assert len(set(drawn)) == 3
    with pytest.raises(EligibleSupportExhausted):
        first.take()
