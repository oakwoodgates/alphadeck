"""F5 — the SCORING reads never use a bar dated an NYSE full-day closure, in either twin.

MEASURED on dev (the orchestrator's read, 2026-10-30): the vendor stored 10 flat OTC bars dated Labor Day
2026-09-07 (open = high = low, close = the prior close). The carry below is SYNTHETIC in that shape; the
sessions around it are synthetic too (this file pins the rule, not a vendor's values). The live Scoreboard's
Postgres reader (``scoreboard.prices.PgRealizedPrices``) and the replay lab's DuckDB reader
(``replay.scoring.RealizedPrices``) bypass ``signals.base.window_prices``, so each skips the closure itself:
no entry, horizon, exit or tape-edge bar is ever a holiday, and the market edge is never a holiday either.
Lives under tests/replay/ so the duckdb collect-guard applies in a lean venv.
"""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from replay.export import export_snapshot
from replay.pit import connect_mirror
from replay.schema import Episode
from replay.scoring import RealizedPrices, score_episode
from scoreboard.prices import PgRealizedPrices, market_tape_edge
from tests.scoreboard.helpers import bar

FRI, LABOR_DAY, TUE, WED = date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)


def _seed(db, sid, *, newest_is_the_carry: bool = False):
    bar(db, sid, FRI, 10.0, high=10.5, low=9.5)
    bar(db, sid, LABOR_DAY, 10.0, high=10.0, low=10.0)  # the flat holiday carry
    if not newest_is_the_carry:
        bar(db, sid, TUE, 11.0, high=11.5, low=10.5)
        bar(db, sid, WED, 12.0, high=12.5, low=11.5)


def _readers(db, tmp_path):
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    return (
        con,
        RealizedPrices(con),
        PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=date(2099, 1, 1)),
    )


def test_neither_twin_ever_reads_a_holiday_bar(db, security_id, tmp_path):
    _seed(db, security_id)
    con, duck, pg = _readers(db, tmp_path)
    try:
        for r in (pg, duck):
            # an entry asked for ON the holiday rolls to the next session, never the carry
            assert r.first_close_on_or_after(security_id, LABOR_DAY) == (TUE, 11.0)
            assert r.last_close_through(security_id, LABOR_DAY) == (FRI, 10.0)
            assert [d for d, _ in r.closes_between(security_id, FRI, WED)] == [FRI, TUE, WED]
            assert [b["d"] for b in r.bars_between(security_id, FRI, WED)] == [FRI, TUE, WED]
            assert r.tape_edge(security_id, LABOR_DAY) == WED
        assert pg.bars_between(security_id, FRI, WED) == duck.bars_between(security_id, FRI, WED)

        ep = Episode(
            thesis_id=uuid.uuid4(),
            security_id=security_id,
            is_headline=True,
            arm_date=LABOR_DAY,
            last_armed_date=TUE,
            dearm_date=None,
            close_reason="window_end",
            warm_date=FRI,
            exit_by=WED,
            arm_until=TUE,
        )
        assert score_episode(ep, pg) == score_episode(ep, duck)
    finally:
        con.close()


def test_a_holiday_carry_is_never_the_market_edge(db, security_id, tmp_path):
    """The newest bar anywhere is the Labor Day carry: the market edge is the Friday before it, in both twins
    and in the request-level read."""
    _seed(db, security_id, newest_is_the_carry=True)
    con, duck, pg = _readers(db, tmp_path)
    try:
        assert duck.market_tape_edge(security_id) == FRI
        assert pg.market_tape_edge(security_id) == FRI
        assert market_tape_edge(db, tenant_id=DEFAULT_TENANT_ID, cap=date(2099, 1, 1)) == FRI
    finally:
        con.close()
