"""Context and Bench — the two fixed, protected, silent links (pure domain; no DB).

A company the operator places in either link stays on the card but NEVER counts toward the call. These pin the
vocabulary every other layer reads (the labels, the verbatim descriptors, the canonical spelling), the chain
helper that keeps both links last and fixed, the per-row call filter, and the "silent or voting, never both"
conflict finder the promote route refuses on.
"""

from __future__ import annotations

import uuid

import pytest

from domain.thesis import (
    BENCH_DESCRIPTOR,
    BENCH_LABEL,
    CONTEXT_DESCRIPTOR,
    CONTEXT_LABEL,
    SILENT_LABELS,
    BasketMember,
    Segment,
    Thesis,
    is_silent,
    reserved_label,
    silent_conflicts,
    voting_thesis,
    with_silent_links,
)


def _m(ticker: str, segment: str | None = None, sid: uuid.UUID | None = None) -> BasketMember:
    return BasketMember(ticker=ticker, role="r", segment=segment, security_id=sid)


def _links(segments: list[Segment]) -> list[tuple[str, str | None]]:
    return [(s.label, s.descriptor) for s in segments]


_FIXED = [(CONTEXT_LABEL, CONTEXT_DESCRIPTOR), (BENCH_LABEL, BENCH_DESCRIPTOR)]


def test_the_vocabulary_is_fixed_and_the_descriptors_are_verbatim():
    assert SILENT_LABELS == ("Context", "Bench")  # display order
    assert CONTEXT_DESCRIPTOR == (
        "Watched for context: buyers, counter-case names, giants where the theme is a slice. "
        "Never counts toward the call."
    )
    assert BENCH_DESCRIPTOR == (
        "Speculative names: pivots, distressed or thinly traded. Never counts toward the call."
    )


@pytest.mark.parametrize(
    ("label", "canonical"),
    [
        ("Context", "Context"),
        ("context", "Context"),
        ("CONTEXT", "Context"),
        ("  cOnTeXt ", "Context"),
        ("Bench", "Bench"),
        ("bench", "Bench"),
        (" BENCH", "Bench"),
    ],
)
def test_reserved_label_returns_the_canonical_spelling_in_any_case(label, canonical):
    assert reserved_label(label) == canonical


@pytest.mark.parametrize(
    "label", [None, "", "Discovered", "Contexts", "Bench press", "Con text", "Reactor developers"]
)
def test_reserved_label_is_none_for_everything_else(label):
    assert reserved_label(label) is None


def test_is_silent_is_an_exact_match_on_the_stored_label():
    assert is_silent(_m("NVDA", "Context")) and is_silent(_m("NVDA", "Bench"))
    assert not is_silent(_m("NVDA", "Discovered"))
    assert not is_silent(_m("NVDA", None))
    assert not is_silent(_m("NVDA", "Reactor developers"))
    # canonicalization is the WRITE's job: a stray spelling is not silent here (promote respells it first)
    assert not is_silent(_m("NVDA", "context"))


def test_with_silent_links_appends_both_last_with_the_fixed_descriptors():
    out = with_silent_links([Segment(label="Reactors", descriptor="d"), Segment(label="Fuel")])
    assert _links(out) == [("Reactors", "d"), ("Fuel", None), *_FIXED]


def test_with_silent_links_on_an_empty_chain_is_just_the_two_links():
    assert _links(with_silent_links([])) == _FIXED


def test_with_silent_links_is_idempotent():
    once = with_silent_links([Segment(label="Reactors")])
    assert with_silent_links(once) == once
    assert with_silent_links(with_silent_links(once)) == once


def test_with_silent_links_drops_case_variants_and_wrong_descriptors_and_moves_them_last():
    out = with_silent_links(
        [
            Segment(label="context", descriptor="my own words"),  # lowercased, wrong descriptor
            Segment(label="Reactors"),
            Segment(label="BENCH"),  # upper-cased
            Segment(label="Fuel"),
            Segment(label="Context", descriptor=CONTEXT_DESCRIPTOR),  # right spelling, mid-list
        ]
    )
    assert _links(out) == [("Reactors", None), ("Fuel", None), *_FIXED]


def test_with_silent_links_leaves_discovered_where_it_is():
    """Discovered is NOT pinned and its behavior is unchanged: it stays wherever the chain put it (here first,
    here in the middle); only Context and Bench are forced last."""
    first = with_silent_links([Segment(label="Discovered", descriptor="x"), Segment(label="Fuel")])
    assert [s.label for s in first] == ["Discovered", "Fuel", "Context", "Bench"]
    middle = with_silent_links(
        [Segment(label="Reactors"), Segment(label="Discovered"), Segment(label="Fuel")]
    )
    assert [s.label for s in middle] == ["Reactors", "Discovered", "Fuel", "Context", "Bench"]
    assert first[0].descriptor == "x"  # untouched


def test_with_silent_links_does_not_mutate_its_argument():
    given = [Segment(label="context"), Segment(label="Reactors")]
    with_silent_links(given)
    assert [s.label for s in given] == ["context", "Reactors"]


def _thesis(basket: list[BasketMember]) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        name="t",
        narrative="n",
        segments=[Segment(label="Reactors"), Segment(label="Discovered")],
        basket=basket,
    )


def test_voting_thesis_removes_every_silent_row_and_keeps_the_rest_in_order():
    keep = [_m("AAA", "Reactors"), _m("BBB", "Discovered"), _m("CCC", None)]
    t = _thesis([keep[0], _m("SIL1", "Context"), keep[1], _m("SIL2", "Bench"), keep[2]])
    voting = voting_thesis(t)
    assert [m.ticker for m in voting.basket] == [
        "AAA",
        "BBB",
        "CCC",
    ]  # Discovered and unplaced still vote
    assert [m.ticker for m in t.basket] == [
        "AAA",
        "SIL1",
        "BBB",
        "SIL2",
        "CCC",
    ]  # the original is untouched
    assert voting.id == t.id and voting.segments == t.segments  # nothing else changes


def test_voting_thesis_of_a_thesis_with_no_silent_rows_has_the_same_roster():
    t = _thesis([_m("AAA", "Reactors"), _m("BBB", None)])
    assert voting_thesis(t).basket == t.basket


def test_the_validator_accepts_the_silent_labels_even_when_the_segment_list_omits_them():
    t = Thesis(
        id=uuid.uuid4(),
        name="t",
        narrative="n",
        segments=[Segment(label="Reactors")],
        basket=[_m("AAA", "Context"), _m("BBB", "Bench")],
    )
    assert [m.segment for m in t.basket] == ["Context", "Bench"]


def test_the_validator_still_rejects_an_unknown_link_and_a_lookalike_spelling():
    for bad in (
        "Nowhere",
        "context",
    ):  # the lookalike must be respelled by the write, never tolerated here
        with pytest.raises(ValueError, match="unknown segment"):
            Thesis(
                id=uuid.uuid4(),
                name="t",
                narrative="n",
                segments=[Segment(label="Reactors")],
                basket=[_m("AAA", bad)],
            )


def test_silent_conflicts_is_empty_when_every_company_has_one_row():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert silent_conflicts([_m("AAA", "Context", a), _m("BBB", "Reactors", b)]) == []
    assert silent_conflicts([]) == []


def test_silent_conflicts_names_a_company_in_a_silent_link_and_a_real_one():
    sid = uuid.uuid4()
    (msg,) = silent_conflicts([_m("NVDA", "Context", sid), _m("NVDA", "Reactors", sid)])
    assert msg == "NVDA is in Context and also in another link"


@pytest.mark.parametrize("other", ["Discovered", None])
def test_silent_conflicts_counts_discovered_and_an_unplaced_row_as_the_other_row(other):
    sid = uuid.uuid4()
    assert silent_conflicts([_m("NVDA", "Bench", sid), _m("NVDA", other, sid)]) == [
        "NVDA is in Bench and also in another link"
    ]


def test_silent_conflicts_names_a_company_in_both_silent_links():
    sid = uuid.uuid4()
    assert silent_conflicts([_m("NVDA", "Context", sid), _m("NVDA", "Bench", sid)]) == [
        "NVDA is in both Context and Bench"
    ]


def test_silent_conflicts_names_a_duplicate_silent_row():
    sid = uuid.uuid4()
    assert silent_conflicts([_m("NVDA", "Bench", sid), _m("NVDA", "Bench", sid)]) == [
        "NVDA is listed more than once in Bench"
    ]


def test_silent_conflicts_does_not_flag_a_company_that_is_only_in_real_links_twice():
    """Multi-link membership of the chain (N rows, same security) is real and untouched by this rule."""
    sid = uuid.uuid4()
    assert silent_conflicts([_m("NVDA", "Reactors", sid), _m("NVDA", "Discovered", sid)]) == []


def test_silent_conflicts_keys_by_id_and_falls_back_to_the_ticker_only_without_one():
    a, b = uuid.uuid4(), uuid.uuid4()
    # same ticker text, different securities: two companies, no conflict
    assert silent_conflicts([_m("XYZ", "Context", a), _m("XYZ", "Reactors", b)]) == []
    # no id on either row: the upper-cased, stripped ticker is the key
    assert silent_conflicts([_m("nvda ", "Context"), _m("NVDA", "Reactors")]) == [
        "nvda is in Context and also in another link"
    ]


def test_silent_conflicts_reports_each_offender_in_first_seen_order():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    out = silent_conflicts(
        [
            _m("BBB", "Context", b),
            _m("AAA", "Bench", a),
            _m("CCC", "Reactors", c),
            _m("AAA", "Reactors", a),
            _m("BBB", "Discovered", b),
        ]
    )
    assert out == [
        "BBB is in Context and also in another link",
        "AAA is in Bench and also in another link",
    ]
