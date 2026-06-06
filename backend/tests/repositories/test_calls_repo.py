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
