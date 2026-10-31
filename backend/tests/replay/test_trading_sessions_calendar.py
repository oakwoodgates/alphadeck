"""F5 — ``replay.harness.trading_sessions`` pages only SESSIONS: the stored bar dates intersected with the calendar.

MEASURED on dev (the orchestrator's read, 2026-10-30): the vendor stored 10 flat OTC bars dated Labor Day
2026-09-07, and Edition 1 holds 4 pages dated that day because the harness paged every stored bar date. The
editions engine, the editions nightly and the backtest's timing null all page through this function. Pure
DuckDB in memory (the mirror's own table shape for the columns the query reads), no Postgres.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

duckdb = pytest.importorskip("duckdb")

from replay.harness import trading_sessions  # noqa: E402

TENANT = uuid.uuid4()
OTC, LISTED = uuid.uuid4(), uuid.uuid4()


def _con(rows: list[tuple[uuid.UUID, date]]):
    con = duckdb.connect()
    con.execute("CREATE TABLE fact_price_eod (tenant_id VARCHAR, security_id VARCHAR, d DATE)")
    for sid, d in rows:
        con.execute("INSERT INTO fact_price_eod VALUES (?, ?, ?)", [str(TENANT), str(sid), d])
    return con


def test_a_holiday_bar_never_makes_the_holiday_a_page():
    week = [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8), date(2026, 9, 9)]
    rows = [(LISTED, d) for d in week] + [(OTC, d) for d in week] + [(OTC, date(2026, 9, 7))]
    con = _con(rows)
    got = trading_sessions(con, [LISTED, OTC], date(2026, 9, 1), date(2026, 9, 30), TENANT)
    assert got == week  # Labor Day is gone though an OTC name stored a bar for it
    # the OTC name alone (the shape behind the 4 Edition 1 pages): still no Labor Day page
    assert date(2026, 9, 7) not in trading_sessions(
        con, [OTC], date(2026, 9, 1), date(2026, 9, 30), TENANT
    )


def test_a_weekend_dated_bar_is_not_a_page_either_and_real_sessions_all_stay():
    rows = [(LISTED, date(2026, 10, d)) for d in (26, 27, 28, 29, 30)] + [(OTC, date(2026, 10, 31))]
    con = _con(rows)
    got = trading_sessions(con, [LISTED, OTC], date(2026, 10, 26), date(2026, 11, 1), TENANT)
    assert got == [date(2026, 10, d) for d in (26, 27, 28, 29, 30)]


def test_no_securities_is_no_sessions():
    assert trading_sessions(_con([]), [], date(2026, 9, 1), date(2026, 9, 30), TENANT) == []
