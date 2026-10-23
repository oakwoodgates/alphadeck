"""S2 — the LIVE-EDGE self-check: the engine's card at the latest recorded night equals that night's recorded
call of record (canonical full-card equality); a difference is reported with WHERE it is, never hidden.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from domain.call import CallCard, TriggerRef
from domain.config import DEFAULT_CONFIG, config_hash
from domain.enums import Kind
from domain.signal import Provenance
from editions.engine import build_job, open_mirror, run_job
from editions.selfcheck import (
    card_differences,
    cards_match,
    equal_within_cents,
    is_money_key,
    live_edge,
)
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo
from repositories.calls_repo import _canonical
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


# --- S5: money compared to the cent ------------------------------------------------------------------------


def _with_sell(card: CallCard, total_usd: float, *, score: float = 0.5) -> CallCard:
    """``card`` carrying one insider-sell risk whose provenance holds a detector-rounded money total (the
    MEASURED AI Memory shape: the same value on every source of the risk) and a non-money float."""
    detail = {"total_usd": total_usd, "planned_screened_usd": 37290090.64, "score": score}
    risk = TriggerRef(
        label="3 insiders incl. senior officer sold $2,371,495 open-market (code S) across 3 txns",
        kind=Kind.INSIDER_SELL,
        event_date=date(2026, 7, 1),
        security_id=card.thesis_id,
        sources=[
            Provenance(source="form4", ref=f"0000000000-26-00000{i}", detail=dict(detail))
            for i in range(3)
        ],
    )
    return card.model_copy(update={"risk_signals": [*card.risk_signals, risk]})


def test_money_one_cent_apart_matches_and_says_so(db, security_id, tmp_path):
    thesis, cards, kb = _edge_cards(db, security_id, tmp_path)
    base = cards[thesis.id][-1]
    mine, theirs = _with_sell(base, 2371494.57), _with_sell(base, 2371494.58)
    assert _canonical(mine) != _canonical(theirs)  # the cron's own compare still sees the cent
    assert cards_match(mine, theirs) == (True, True)
    assert cards_match(mine, _with_sell(base, 2371494.57)) == (True, False)  # canonical-equal
    assert card_differences(mine, theirs) == []
    # the self-check end to end: the recorded row carries the other cent
    calls_repo.append(db, theirs, thesis.tenant_id, config_hash=config_hash(DEFAULT_CONFIG))
    db.commit()
    (r,) = live_edge(db, {thesis.id: [mine]}, known_by=datetime.now(timezone.utc), night=NIGHT)
    assert r.outcome == "match" and r.money_within_cent is True


def test_more_than_a_cent_or_a_non_money_float_is_still_a_mismatch(db, security_id, tmp_path):
    thesis, cards, kb = _edge_cards(db, security_id, tmp_path)
    base = cards[thesis.id][-1]
    mine = _with_sell(base, 2371494.57)
    assert cards_match(mine, _with_sell(base, 2371494.59)) == (False, False)  # two cents
    assert cards_match(mine, _with_sell(base, 2371494.57, score=0.51)) == (False, False)
    assert card_differences(mine, _with_sell(base, 2371494.60)) == ["risk_signals"]
    assert is_money_key("usd") and is_money_key("total_usd") and not is_money_key("usd_rank")
    assert equal_within_cents({"close": 0.051}, {"close": 0.055}) is False  # a price, not money
