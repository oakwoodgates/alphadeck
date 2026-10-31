"""F5 — a bar dated a day with NO session is never a close: withheld at write, skipped on read, deleted never.

MEASURED on dev (the orchestrator's read, 2026-10-30): 10 bars dated Labor Day 2026-09-07, all OTC names, each a
flat carry — open = high = low, close = the prior session's close, a token volume. The carry bars below are
SYNTHETIC in exactly that shape (the measured values are not in this repo); the real sessions around them are
REAL vendor data (``tests/fixtures/prices/benchmark_tape_2026_08.json``, SPY, Thanksgiving week 2025, parsed by
the production ``parse_yahoo_chart``). Pure tests first; the write path's DB test COUNTS THE TABLE.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart, stored_bars
from ingest.prices.ingest_security import ingest_bars_for_security
from ingest.prices.settle import withhold_closed_days, withhold_unsettled
from pipeline.ingest_benchmarks import plan_benchmark_bars
from signals.base import window_prices

ET = ZoneInfo("America/New_York")
_FIX = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "benchmark_tape_2026_08.json"
    ).read_text(encoding="utf-8")
)
THANKSGIVING = date(2025, 11, 27)
WED, FRI = date(2025, 11, 26), date(2025, 11, 28)


def _real_week() -> list[dict]:
    rows = parse_yahoo_chart(_FIX["SPY"]["thanksgiving_2025"])
    assert THANKSGIVING not in [r["d"] for r in rows]  # the real vendor serves no holiday bar
    return [r for r in rows if r["d"] <= FRI]


def _carry(d: date, prior: dict) -> dict:
    """A SYNTHETIC holiday carry in the measured shape: O = H = L = C = the prior close, a token volume."""
    c = prior["close"]
    return {"d": d, "open": c, "high": c, "low": c, "close": c, "volume": 100.0}


def _with_carry() -> list[dict]:
    week = _real_week()
    wed = next(r for r in week if r["d"] == WED)
    out = [r for r in week if r["d"] <= WED] + [_carry(THANKSGIVING, wed)]
    return out + [r for r in week if r["d"] > WED]


def _dates(rows: list[dict]) -> list[date]:
    return [r["d"] for r in rows]


# --- the write guard (pure) ---------------------------------------------------------------------------------


def test_a_holiday_carry_is_withheld_and_counted_and_the_sessions_pass():
    fresh = _with_carry()
    kept, closed = withhold_closed_days(fresh)
    assert closed == 1
    assert THANKSGIVING not in _dates(kept)
    assert _dates(kept) == _dates(_real_week())  # every real session untouched, order kept


def test_a_weekend_dated_bar_is_withheld_too():
    week = _real_week()
    sat = dict(week[-1], d=date(2025, 11, 29))
    kept, closed = withhold_closed_days(week + [sat])
    assert closed == 1 and _dates(kept) == _dates(week)


def test_the_half_day_after_thanksgiving_is_a_session_not_a_closure():
    kept, closed = withhold_closed_days(_real_week())
    assert closed == 0 and FRI in _dates(kept)


def test_the_benchmark_planner_drops_and_counts_it():
    fresh = _with_carry()
    plan = plan_benchmark_bars(
        fresh, {}, now=datetime(2025, 11, 28, 22, 30, tzinfo=ET), window_sessions=5
    )
    assert plan.closed_day == 1 and plan.withheld == 0
    assert THANKSGIVING not in _dates(plan.tail)


def test_the_settle_guard_still_runs_first_and_is_unchanged():
    # a daytime pass ON the holiday: the carry is "today" (unsettled) AND a closed day — withheld by D2 first,
    # so it is counted once, as unsettled, and the closed-day guard sees nothing more
    fresh = _with_carry()
    settled, held = withhold_unsettled(
        [r for r in fresh if r["d"] <= THANKSGIVING], now=datetime(2025, 11, 27, 12, 0, tzinfo=ET)
    )
    kept, closed = withhold_closed_days(settled)
    assert (held, closed) == (1, 0) and _dates(kept)[-1] == WED


# --- the read guard (pure): window_prices, the one post-process every point-in-time price view shares -------


def test_a_stored_holiday_bar_is_not_in_the_point_in_time_view():
    view = window_prices(_with_carry(), FRI, None)
    assert THANKSGIVING not in _dates(view)
    assert _dates(view) == sorted(_dates(_real_week()))


def test_the_read_keeps_weekend_dated_rows_it_only_skips_market_holidays():
    """The read skips the NYSE's closures, not weekends (synthetic tapes are often calendar-day series; the
    stored anomaly was the holiday carry). The write guard refuses both."""
    rows = [{"d": date(2026, 10, 31), "close": 1.0}, {"d": date(2026, 9, 7), "close": 1.0}]
    assert _dates(window_prices(rows, date(2026, 11, 2), None)) == [date(2026, 10, 31)]


# --- the write path, end to end (DB) ----------------------------------------------------------------------


class _Source:
    def __init__(self, bars: list[dict]):
        self.bars = bars

    def get_bars(self, ticker: str, *, allow_live: bool = True, force_refresh: bool = False):
        return list(self.bars)


def _count(db, security_id) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (security_id,)
        )
        return cur.fetchone()["n"]


def test_the_price_leg_never_stores_a_holiday_carry_and_leaves_a_stored_one_alone(db, security_id):
    """The thesis / Workbench price leg: the carry is withheld (``closed_day`` = 1), every real session lands,
    a re-run appends nothing — and a carry that was ALREADY stored (the dev shape) is never deleted, only
    skipped by the read."""
    sec = Security(id=security_id, tenant_id=DEFAULT_TENANT_ID, ticker="DEVCO", name="Dev Co")
    night = datetime(2025, 11, 28, 22, 35, tzinfo=ET)

    res = ingest_bars_for_security(
        db, sec, tenant_id=DEFAULT_TENANT_ID, source=_Source(_with_carry()), now=night
    )
    db.commit()
    assert res.closed_day == 1 and res.withheld == 0
    assert res.appended == len(_real_week()) and _count(db, security_id) == len(_real_week())
    assert THANKSGIVING not in stored_bars(db, security_id)

    again = ingest_bars_for_security(
        db, sec, tenant_id=DEFAULT_TENANT_ID, source=_Source(_with_carry()), now=night
    )
    db.commit()
    assert (again.appended, again.reversioned, again.closed_day) == (0, 0, 1)
    assert _count(db, security_id) == len(_real_week())  # COUNT THE TABLE

    # a carry stored before F5 (written directly, as the old leg did): it stays in the table...
    wed = next(r for r in _real_week() if r["d"] == WED)
    ingest_prices(db, security_id, [_carry(THANKSGIVING, wed)], tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert _count(db, security_id) == len(_real_week()) + 1
    # ...a later pass neither re-versions nor removes it...
    ingest_bars_for_security(
        db, sec, tenant_id=DEFAULT_TENANT_ID, source=_Source(_with_carry()), now=night
    )
    db.commit()
    assert _count(db, security_id) == len(_real_week()) + 1
    # ...and the point-in-time view never shows it
    stored = list(stored_bars(db, security_id).values())
    assert THANKSGIVING in _dates(stored)
    assert THANKSGIVING not in _dates(window_prices(stored, FRI, None))
