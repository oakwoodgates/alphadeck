from __future__ import annotations

from datetime import date

import psycopg
import pytest

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import State, Verdict
from repositories import calls_repo, thesis_repo
from tests.calls.factories import make_thesis


def _persist_minimal_thesis(db):
    # basket=[] avoids the security_master FK; the calls log only needs the thesis to exist
    thesis = make_thesis(basket=[])
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis


def test_append_and_read_back_roundtrips(db):
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)  # incubating (no events)
    calls_repo.append(db, card)
    db.commit()

    logged = calls_repo.list_for_thesis(db, thesis.id)
    assert len(logged) == 1
    assert logged[0].thesis_id == thesis.id
    assert logged[0].state is State.INCUBATING
    assert logged[0].model_dump_json() == card.model_dump_json()  # full round-trip through jsonb


def test_calls_log_is_immutable(db):
    """The accountability log is write-only: a real DB trigger blocks UPDATE (not a convention)."""
    thesis = _persist_minimal_thesis(db)
    calls_repo.append(db, assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG))
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE calls SET verdict = 'tampered' WHERE thesis_id = %s", (thesis.id,))
    db.rollback()


def test_latest_for_thesis_dedups_to_the_call_of_record(db):
    """``latest_for_thesis`` returns one row per as-of (the latest append wins); ``list_for_thesis``
    keeps the full append-only history. This is what stops a future scoreboard reading duplicates.
    """
    thesis = _persist_minimal_thesis(db)
    asof = date(2026, 6, 1)
    base = assemble_call(thesis, [], asof, DEFAULT_CONFIG)  # incubating -> verdict WATCHING
    # two re-runs at the SAME asof (e.g. after a fact correction) — the later seq supersedes
    calls_repo.append(db, base.model_copy(update={"verdict": Verdict.NOT_YET}))
    calls_repo.append(db, base.model_copy(update={"verdict": Verdict.WATCHING}))
    calls_repo.append(db, base.model_copy(update={"asof": date(2026, 6, 2)}))  # a different asof
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 3  # history keeps every row

    by_asof = {c.asof: c for c in calls_repo.latest_for_thesis(db, thesis.id)}
    assert set(by_asof) == {date(2026, 6, 1), date(2026, 6, 2)}  # one row per as-of
    assert by_asof[date(2026, 6, 1)].verdict is Verdict.WATCHING  # the latest append, not NOT_YET


def test_record_if_changed_appends_first_then_skips_identical(db):
    """A re-run on UNCHANGED data appends NOTHING — asserted by COUNTING the table, not the read (the read
    dedups, so a duplicate append would hide behind a correct read while the log silently grows)."""
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)

    assert calls_repo.record_if_changed(db, card) is True  # first time -> append
    db.commit()
    assert calls_repo.record_if_changed(db, card) is False  # identical -> NO append
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 1  # the TABLE has one row, not two


def test_record_if_changed_appends_exactly_one_on_a_real_change(db):
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)
    calls_repo.record_if_changed(db, card)
    db.commit()

    changed = card.model_copy(update={"verdict": Verdict.NOT_YET})
    assert calls_repo.record_if_changed(db, changed) is True  # differs -> one new versioned row
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 2  # exactly one new row
    assert calls_repo.latest_for_thesis(db, thesis.id)[0].verdict is Verdict.NOT_YET  # latest wins


def test_record_if_changed_ignores_a_pure_list_reorder(db):
    """The compare is canonical (order-independent), so reordering an unordered card list is NOT a change —
    the determinism guard: a flapping serialize would re-append every run."""
    thesis = _persist_minimal_thesis(db)
    base = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)

    assert calls_repo.record_if_changed(db, base.model_copy(update={"missing": ["x", "y"]})) is True
    db.commit()
    # same set, reversed order -> must NOT append
    assert (
        calls_repo.record_if_changed(db, base.model_copy(update={"missing": ["y", "x"]})) is False
    )
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 1
