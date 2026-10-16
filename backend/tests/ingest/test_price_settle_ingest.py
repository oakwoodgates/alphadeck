"""D2 on the WRITE path — the thesis-name price leg (``ingest_bars_for_security``, shared by the per-thesis
ingest, the daily cron and the Workbench pull) stores no in-progress bar, and withholding loses nothing.

DB-backed (the ``db`` fixture). Bars are REAL vendor data in the vendor's chart shape (see
``tests/fixtures/prices/benchmark_tape_2026_08.json``), served through a canned ``PriceSource``; every
pass runs at a FIXED aware instant. Every assertion COUNTS THE TABLE — the as-of read dedups, so a stray
version would hide behind a correct read.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart, stored_bars
from ingest.prices.ingest_security import ingest_bars_for_security

ET = ZoneInfo("America/New_York")
_FIX = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "benchmark_tape_2026_08.json"
    ).read_text(encoding="utf-8")
)
D25 = date(2026, 8, 25)


def _bars(cut: str, *, through: date | None = None) -> list[dict]:
    rows = parse_yahoo_chart(_FIX["SPY"][cut])
    return [r for r in rows if through is None or r["d"] <= through]


class _Source:
    def __init__(self, bars: list[dict]):
        self.bars = bars

    def get_bars(self, ticker: str, *, allow_live: bool = True, force_refresh: bool = False):
        return list(self.bars)


def _sec(security_id) -> Security:
    return Security(id=security_id, tenant_id=DEFAULT_TENANT_ID, ticker="DEVCO", name="Dev Co")


def _count(db, security_id) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (security_id,)
        )
        return cur.fetchone()["n"]


def _pass(db, security_id, bars, now):
    res = ingest_bars_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        source=_Source(bars),
        now=now,
    )
    db.commit()
    return res


def test_a_daytime_pass_stores_nothing_of_today_then_the_evening_pass_appends_it(db, security_id):
    """The whole D2 story on one name. 12:07 ET: the payload's last bar is the session so far (765.27) —
    the prior sessions land, today's is WITHHELD (counted, not stored). 22:35 ET: the final bar is an
    ordinary tail bar now (it is newer than the latest stored), so withholding cost nothing."""
    day = _pass(
        db, security_id, _bars("midsession_2026_08_25"), datetime(2026, 8, 25, 12, 7, tzinfo=ET)
    )

    assert (day.appended, day.reversioned, day.withheld) == (3, 0, 1)
    assert _count(db, security_id) == 3
    assert D25 not in stored_bars(db, security_id)

    night = _pass(
        db, security_id, _bars("final", through=D25), datetime(2026, 8, 25, 22, 35, tzinfo=ET)
    )

    assert (night.appended, night.reversioned, night.withheld) == (1, 0, 0)
    assert _count(db, security_id) == 4
    assert float(stored_bars(db, security_id)[D25]["close"]) == pytest.approx(765.909973)


def test_a_daytime_pass_never_restates_a_stored_bar_with_an_in_progress_value(db, security_id):
    """A LEGACY intraday version of today (what an unguarded market-hours pass left — the real 765.27) is
    not touched by a later daytime pass, even when the vendor's value has moved: an in-progress value must
    not re-version a stored bar either. The first SETTLED pass restates it to the final close (the
    existing exact-compare re-version, unchanged) — exactly one new version."""
    ingest_prices(db, security_id, _bars("midsession_2026_08_25"))  # the pre-guard state
    db.commit()
    assert _count(db, security_id) == 4

    later = _pass(
        db, security_id, _bars("final", through=D25), datetime(2026, 8, 25, 17, 30, tzinfo=ET)
    )
    assert (later.appended, later.reversioned, later.withheld) == (0, 0, 1)
    assert _count(db, security_id) == 4  # no version written from an unsettled pull

    settled = _pass(
        db, security_id, _bars("final", through=D25), datetime(2026, 8, 25, 22, 35, tzinfo=ET)
    )
    assert (settled.appended, settled.reversioned, settled.withheld) == (0, 1, 0)
    assert _count(db, security_id) == 5
    assert float(stored_bars(db, security_id)[D25]["close"]) == pytest.approx(765.909973)


def test_a_post_midnight_catch_up_stores_the_previous_session(db, security_id):
    """00:30 ET on 08-26 (market_today is 08-26, pre-open): the 08-25 bar is a prior day's — stored."""
    res = _pass(
        db, security_id, _bars("final", through=D25), datetime(2026, 8, 26, 0, 30, tzinfo=ET)
    )

    assert (res.appended, res.withheld) == (4, 0)
    assert _count(db, security_id) == 4
    assert D25 in stored_bars(db, security_id)


def test_a_rerun_of_a_withholding_pass_appends_zero(db, security_id):
    """Idempotent under the guard: the same daytime pass twice writes the prior sessions ONCE."""
    now = datetime(2026, 8, 25, 12, 7, tzinfo=ET)
    _pass(db, security_id, _bars("midsession_2026_08_25"), now)
    before = _count(db, security_id)

    again = _pass(db, security_id, _bars("midsession_2026_08_25"), now)

    assert (again.appended, again.reversioned, again.withheld) == (0, 0, 1)
    assert _count(db, security_id) == before
