"""Thesis and as-of resolution: exact matches only, never a fuzzy pick. Synthetic data; no DB, no API."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from agent_tools.errors import ToolFailure  # noqa: E402
from agent_tools.resolve import check_asof, match_thesis  # noqa: E402
from tests.agent_tools import fixtures as fx  # noqa: E402

THESES = [
    {"id": fx.uid("thesis", 0), "name": "Thesis 01 Alpha"},
    {"id": fx.uid("thesis", 1), "name": "Thesis 02 Alpha Beta"},
    {"id": fx.uid("thesis", 2), "name": "thesis 03 gamma"},
    {"id": fx.uid("thesis", 3), "name": "Thesis 03 Gamma"},
]


def code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.code


def test_a_uuid_resolves_to_its_thesis_in_any_case():
    got = match_thesis(THESES, f"  {THESES[1]['id'].upper()} ")
    assert got == {"id": THESES[1]["id"], "name": "Thesis 02 Alpha Beta"}


def test_an_unlisted_uuid_is_unknown_thesis():
    with pytest.raises(ToolFailure) as exc:
        match_thesis(THESES, fx.uid("missing"))
    assert code(exc) == "unknown_thesis"


def test_an_exact_name_matches_case_insensitively_after_trimming():
    assert match_thesis(THESES, "  thesis 01 ALPHA ")["id"] == THESES[0]["id"]


def test_a_partial_name_is_unknown_with_containing_candidates_only():
    with pytest.raises(ToolFailure) as exc:
        match_thesis(THESES, "alpha")
    assert code(exc) == "unknown_thesis"
    assert [c["id"] for c in exc.value.extra["candidates"]] == [THESES[0]["id"], THESES[1]["id"]]


def test_candidates_are_capped_at_five():
    many = [{"id": fx.uid("t", i), "name": f"Card {i} delta"} for i in range(9)]
    with pytest.raises(ToolFailure) as exc:
        match_thesis(many, "delta")
    assert len(exc.value.extra["candidates"]) == 5


def test_two_exact_matches_are_ambiguous_with_both_listed():
    with pytest.raises(ToolFailure) as exc:
        match_thesis(THESES, "THESIS 03 GAMMA")
    assert code(exc) == "ambiguous_thesis"
    assert {c["id"] for c in exc.value.extra["candidates"]} == {THESES[2]["id"], THESES[3]["id"]}


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_thesis_is_invalid(text):
    with pytest.raises(ToolFailure) as exc:
        match_thesis(THESES, text)
    assert code(exc) == "invalid_arguments"


def test_asof_passes_through_as_given_and_rejects_a_bad_date():
    assert check_asof(None) is None
    assert check_asof("2026-11-05") == "2026-11-05"
    for bad in ("2026-11-5", "2026-02-30", "11/05/2026", "2026-11-05T00:00"):
        with pytest.raises(ToolFailure) as exc:
            check_asof(bad)
        assert code(exc) == "invalid_arguments"
