"""V2 W6a: the SOLE writer of the counting-catalyst store (``repositories.thesis_catalyst_repo.append_version``).

The version rules (the W6 plan's Q2, operator decision N5 (a)): turning the switch on, or changing the grade or
the members, counts from that save's night; changing only the label, citation or relevant-until date keeps the
entry's night; switching off stops it from that save's night with the other fields copied; a save that changes
nothing appends nothing (COUNT THE TABLE). The entry time is the database's ``now()``: no caller can pass one, and
the typed calendar date never moves when an entry counts (R5).
"""

from __future__ import annotations

import inspect
import uuid
from datetime import date

import pytest

from repositories import thesis_catalyst_repo as repo
from tests.thesis_catalyst_fixtures import card, count

_FAR = date(2100, 1, 1)
_PAST = date(2000, 1, 1)


def _save(db, tid, entry, **kw):
    args = {
        "counts": True,
        "grade": "flip",
        "security_ids": [],
        "citation": "https://example.com/doe",
        "relevant_until": None,
        "label": "DOE loan",
        "user_id": None,
    }
    return repo.append_version(db, tid, entry, **{**args, **kw})


def _versions(db, tid, entry) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM fact_thesis_catalyst WHERE thesis_id = %s AND entry_id = %s "
            "ORDER BY recorded_at, id",
            (tid, entry),
        )
        return cur.fetchall()


def _new_transaction(db) -> None:
    """Commit, so the next save gets a later ``now()`` (one transaction = one instant)."""
    db.commit()


def test_no_caller_can_supply_the_entry_time():
    params = set(inspect.signature(repo.append_version).parameters)
    for forbidden in ("entered_at", "recorded_at", "valid_from", "when_date", "at", "now"):
        assert forbidden not in params
    with pytest.raises(TypeError):
        repo.append_version(  # type: ignore[call-arg]
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            counts=True,
            grade="flip",
            security_ids=[],
            citation="c",
            relevant_until=None,
            label="l",
            user_id=None,
            entered_at=None,
        )


def test_a_typed_past_date_never_moves_when_it_counts(db):
    """The calendar row is typed 2026-03-01; the version counts from the night it was SAVED, set by the database
    (``entered_at`` = ``recorded_at`` = the transaction's ``now()``)."""
    tid, _, entry = card(db)
    assert _save(db, tid, entry) is not None
    (v,) = _versions(db, tid, entry)
    assert v["entered_at"] == v["recorded_at"]
    assert v["valid_from"] == repo.entry_night(v["recorded_at"])
    assert v["valid_from"] != date(2026, 3, 1)
    assert v["source"] == "thesis_catalyst" and v["supersedes"] is None


def test_store_on_diff_appends_nothing_for_the_same_state(db):
    tid, sids, entry = card(db)
    state = {"grade": "core", "security_ids": [sids[1], sids[0]], "relevant_until": _FAR}
    assert _save(db, tid, entry, **state) is not None
    _new_transaction(db)
    before = count(db)
    # the same statement (members reordered and duplicated, citation padded): nothing
    again = {
        **state,
        "security_ids": [sids[0], sids[1], sids[0]],
        "citation": " https://example.com/doe ",
    }
    assert _save(db, tid, entry, **again) is None
    assert count(db) == before == 1


def test_switch_on_grade_and_members_re_date_label_citation_relevant_until_keep_the_night(db):
    tid, sids, entry = card(db)
    state: dict = {"grade": "flip", "security_ids": [sids[0]]}
    first = _save(db, tid, entry, **state)
    _new_transaction(db)
    for change in (
        {"label": "DOE loan, closed"},
        {"citation": "0001234567-26-000001"},
        {"relevant_until": _FAR},
    ):
        state.update(change)  # each save sends the whole statement, as the form does
        assert _save(db, tid, entry, **state) is not None
        _new_transaction(db)
    vs = _versions(db, tid, entry)
    assert len(vs) == 4
    assert {v["valid_from"] for v in vs} == {
        vs[0]["valid_from"]
    }  # every edit kept the entry's night
    assert [v["supersedes"] for v in vs] == [None, first, vs[1]["id"], vs[2]["id"]]
    # a later edit carries every earlier field it does not change
    assert (vs[-1]["label"], vs[-1]["citation"], vs[-1]["relevant_until"]) == (
        "DOE loan, closed",
        "0001234567-26-000001",
        _FAR,
    )
    for change in ({"grade": "core"}, {"security_ids": [sids[0], sids[1]]}):
        keep = {
            "label": "DOE loan, closed",
            "citation": "0001234567-26-000001",
            "relevant_until": _FAR,
            "grade": "flip",
            "security_ids": [sids[0]],
        }
        if "grade" in change:
            keep["security_ids"] = [sids[0]]
        else:
            keep["grade"] = "core"
        assert _save(db, tid, entry, **{**keep, **change}) is not None
        _new_transaction(db)
        v = _versions(db, tid, entry)[-1]
        assert v["valid_from"] == repo.entry_night(
            v["recorded_at"]
        )  # re-dated to this save's night
    assert count(db) == 6


def test_switch_off_copies_the_fields_and_switch_on_again_re_dates(db):
    tid, sids, entry = card(db)
    _save(db, tid, entry, grade="core", security_ids=[sids[0]], relevant_until=_FAR)
    _new_transaction(db)
    # the off request's other fields are ignored: the version copies the one it supersedes
    assert _save(db, tid, entry, counts=False, grade="flip", security_ids=[]) is not None
    _new_transaction(db)
    on, off = _versions(db, tid, entry)
    assert off["counts"] is False and off["supersedes"] == on["id"]
    assert (off["grade"], off["security_ids"], off["citation"], off["relevant_until"]) == (
        "core",
        [sids[0]],
        on["citation"],
        _FAR,
    )
    assert off["valid_from"] == repo.entry_night(off["recorded_at"])
    assert repo.counting_entry_ids(db, tid) == set()
    assert _save(db, tid, entry, counts=False) is None  # already off: nothing
    assert _save(db, tid, entry, grade="core", security_ids=[sids[0]], relevant_until=_FAR)
    again = _versions(db, tid, entry)[-1]
    assert again["counts"] and again["valid_from"] == repo.entry_night(again["recorded_at"])
    assert repo.counting_entry_ids(db, tid) == {entry}
    assert [r["entry_id"] for r in repo.current(db, tid)] == [entry]
    assert count(db) == 3


def test_the_writer_refuses_what_the_rule_does_not_allow(db):
    tid, sids, entry = card(db)
    stranger = uuid.uuid4()
    cases = [
        {"grade": "strong"},
        {"citation": "   "},
        {"label": ""},
        {"grade": "core", "security_ids": []},  # N2: a whole-card entry is flip only
        {"security_ids": [stranger]},  # not on this card
        {"relevant_until": _PAST},  # on or before tonight
    ]
    for bad in cases:
        with pytest.raises(repo.CountingEntryError):
            _save(db, tid, entry, **bad)
    with pytest.raises(repo.UnknownEntry):
        _save(db, tid, uuid.uuid4())  # not a calendar row of this card
    with pytest.raises(repo.UnknownEntry):
        _save(db, uuid.uuid4(), entry)  # no such card
    other_tid, _, other_entry = card(db, n_members=1)
    with pytest.raises(repo.UnknownEntry):
        _save(db, tid, other_entry)  # another card's calendar row
    assert count(db) == 0
    # the members check accepts every member of the card
    assert _save(db, tid, entry, grade="core", security_ids=sids) is not None
    assert other_tid != tid
