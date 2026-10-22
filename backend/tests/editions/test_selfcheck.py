"""S2 — the LIVE-EDGE self-check: the engine's card at the latest recorded night equals that night's recorded
call of record (canonical full-card equality); a difference is reported with WHERE it is, never hidden.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, config_hash
from editions.engine import build_job, open_mirror, run_job
from editions.selfcheck import live_edge
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo
from tests.editions.fixtures import seed_big_buy, seed_tape
from tests.editions.test_engine import _cut, _entry, real_roster
from tests.scoreboard.helpers import persist_thesis

NIGHT = date(2026, 7, 10)


def _edge_cards(db, security_id, tmp_path):
    thesis = persist_thesis(db, security_id)
    seed_tape(db, security_id)
    seed_big_buy(db, security_id, recorded_at=datetime(2026, 6, 18, tzinfo=timezone.utc))
    db.commit()
    # the night's call of record, as the cron writes it (everything it reads is public by that night)
    call_for_thesis(db, thesis.id, NIGHT, record=True, run_kind="cron")
    db.commit()
    kb = _cut(db, tmp_path / "mirror")
    roster = real_roster(db, thesis.id)
    entries = {security_id: _entry(thesis.id, security_id, date(2026, 6, 1))}
    con = open_mirror(tmp_path / "mirror")
    try:
        job = build_job(db, con, roster, entries, end=NIGHT, known_by=kb)
        db.commit()
        cards = {thesis.id: run_job(con, job)}
    finally:
        con.close()
    return thesis, cards, kb


def test_the_engine_at_the_live_edge_equals_the_recorded_card(db, security_id, tmp_path):
    thesis, cards, kb = _edge_cards(db, security_id, tmp_path)
    (r,) = live_edge(db, cards, known_by=kb, night=NIGHT)
    assert r.outcome == "match", r
    assert r.same_policy is True and r.differs == []


def test_a_difference_is_reported_with_where_it_is(db, security_id, tmp_path):
    thesis, cards, kb = _edge_cards(db, security_id, tmp_path)
    mine = cards[thesis.id][-1]
    assert mine.asof == NIGHT
    tampered: CallCard = mine.model_copy(update={"counter_case": "a different counter-case"})
    calls_repo.append(db, tampered, thesis.tenant_id, config_hash=config_hash(DEFAULT_CONFIG))
    db.commit()
    later = datetime.now(timezone.utc)
    (r,) = live_edge(db, cards, known_by=later, night=NIGHT)
    assert r.outcome == "mismatch" and r.differs == ["counter_case"]
    # the cut decides which recorded row is the answer key: at the earlier cut, the cron's row still stands
    (r0,) = live_edge(db, cards, known_by=kb, night=NIGHT)
    assert r0.outcome == "match"


def test_a_night_the_engine_has_no_page_for_says_so(db, security_id, tmp_path):
    thesis, cards, kb = _edge_cards(db, security_id, tmp_path)
    (r,) = live_edge(db, {thesis.id: []}, known_by=kb, night=NIGHT)
    assert r.outcome == "no_page"
