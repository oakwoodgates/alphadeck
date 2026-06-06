from __future__ import annotations

from datetime import date, datetime, timezone

from domain.enums import State, Verdict
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import HIMS_THESIS_ID, seed_hims
from repositories import thesis_repo

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_seed_hims_produces_a_curlable_armed_call(db):
    tid = seed_hims(db)
    db.commit()
    assert tid == HIMS_THESIS_ID
    # the seeded thesis computes the real Armed call (what the operator will curl at Checkpoint A)
    card = call_for_thesis(db, tid, date(2026, 6, 1), known_at=_KNOWN, record=False)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.STARTER_ENTRY
    assert card.armed_security_id is not None


def test_seed_is_idempotent(db):
    seed_hims(db)
    db.commit()
    seed_hims(db)  # second run must not error or duplicate the thesis
    db.commit()
    thesis = thesis_repo.get(db, HIMS_THESIS_ID)
    assert thesis is not None
    assert len(thesis.basket) == 1
    assert len(thesis.evidence) == 1
