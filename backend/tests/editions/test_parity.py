"""S2 — PARITY with main's history view: on a night where every basket member has joined (a full roster), the
edition engine's card equals the Postgres past-asof card — the path ``/theses/{id}/call?asof=X`` takes
(``serve_view``: the public clock, capped at the end of X's market day). One public-clock definition, two
engines, the same card."""

from __future__ import annotations

from datetime import date, datetime, timezone

from domain.market_time import known_at_for_asof, serve_view
from editions.engine import build_job, open_mirror, run_job
from pipeline.call_for_thesis import call_for_thesis
from repositories.calls_repo import _canonical
from tests.editions.fixtures import seed_big_buy, seed_tape
from tests.editions.test_engine import _cut, _entry, real_roster
from tests.scoreboard.helpers import persist_thesis


def test_the_engine_card_is_the_postgres_past_asof_card_on_a_full_roster_night(
    db, security_id, tmp_path
):
    thesis = persist_thesis(db, security_id)
    seed_tape(db, security_id)
    seed_big_buy(
        db, security_id
    )  # accepted 06-17, stored 07-19: the public clock must count it from 06-17
    seed_big_buy(
        db,
        security_id,
        accession="0000000000-26-000002",
        valid_from=date(2026, 7, 1),
        accepted=datetime(
            2026, 7, 7, 21, 0, tzinfo=timezone.utc
        ),  # public 07-07 — after the 07-02 night
        recorded_at=datetime(2026, 7, 8, 2, 30, tzinfo=timezone.utc),
    )
    db.commit()
    kb = _cut(db, tmp_path / "mirror")
    roster = real_roster(db, thesis.id)
    entries = {security_id: _entry(thesis.id, security_id, date(2026, 6, 1))}
    con = open_mirror(tmp_path / "mirror")
    try:
        job = build_job(db, con, roster, entries, end=date(2026, 7, 10), known_by=kb)
        db.commit()
        cards = {c.asof: c for c in run_job(con, job)}
    finally:
        con.close()
    nights = [
        date(2026, 6, 16),
        date(2026, 6, 17),
        date(2026, 7, 2),
        date(2026, 7, 7),
        date(2026, 7, 10),
    ]
    for night in nights:
        known_at, clock = serve_view(night)
        assert clock == "public" and known_at == known_at_for_asof(night)
        pg = call_for_thesis(db, thesis.id, night, known_at=known_at, clock=clock, record=False)
        db.commit()
        assert _canonical(cards[night]) == _canonical(pg), night
    # not vacuous: the two buys really change the card across these nights
    assert cards[date(2026, 6, 16)].conviction_grade is None
    assert cards[date(2026, 6, 17)].conviction_grade is not None
