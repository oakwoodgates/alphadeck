"""Every user-facing screen prints a stored cash/burn value in ITS currency (GAP 3 of the extractor correctness
follow-up; operator decision D4 and the operator-approved rule for a missing currency).

ONE backend function labels a stored value's figures, ``domain.cash_currency.currency_symbol``: the stored ISO code's
symbol (``CN¥``, ``¥``, ``C$``, ... or the code itself), ``$`` for USD, ``?`` for a value with NO currency only when it
came from the annual statements (its currency was never established), and ``$`` for a quarterly or older row with no
currency. Never a conversion; the runway in months is a ratio and unchanged. Every backend printer calls it: the
Filing Stream row, the steward page's pace caveat, the scored runway meter's caveat, the Peer Table cell's caveat.
Where there is room (a detail line, the provenance detail) the investment split rides beside the cash.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.routers import admin_platform_values as apv
from dashboards.filings import Who, rows_from_ratified
from dashboards.peer_table import runway_cell
from domain.config import DEFAULT_CONFIG
from domain.scopes import DATA_TENANT_ID
from platform_values import state, steward
from repositories import platform_value_repo as repo
from workbench import scoring

STEWARD = "steward@example.test"
REF = "https://www.sec.gov/Archives/edgar/data/9/0009-20f-2025.htm"
_W = Who(security_id=uuid4(), ticker="BIDU", name="Baidu", cik=None)


def _symbol(*a, **kw):
    from domain.cash_currency import currency_symbol

    return currency_symbol(*a, **kw)


@pytest.mark.parametrize(
    ("currency", "source", "flags", "want"),
    [
        ("CNY", "annual-statements", (), "CN¥"),
        ("JPY", "annual-statements", (), "¥"),
        ("USD", "10-q", (), "$"),
        ("MXN", "annual-statements", (), "MXN"),  # no symbol known: the code itself, never "$"
        (
            None,
            "annual-statements",
            (),
            "?",
        ),  # an annual value whose currency was never established
        (None, "20-f", ("annual-statements", "currency-unread"), "?"),  # a candidate flagged unread
        (None, "10-q", (), "$"),  # a quarterly row stored before the currency was recorded
        (None, None, (), "$"),  # a V1 row
    ],
)
def test_the_one_label_rule(currency, source, flags, want):
    assert _symbol(currency, source=source, flags=flags) == want


def _row(**kw: Any) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "cash_usd": 1_200_000_000.0,
        "quarterly_burn_usd": 50_000_000.0,
        "currency": "CNY",
        "investments_usd": 300_000_000.0,
        "source": "annual-statements",
        "source_ref": REF,
        "note": "cash and short-term deposits",
        "ratified_by": STEWARD,
        "valid_from": date(2025, 12, 31),
        "recorded_at": datetime(2026, 4, 30, 12, tzinfo=timezone.utc),
        "ytd_quarterly_burn_usd": None,
        "ytd_burn_days": None,
        **kw,
    }


# --- the Filing Stream -------------------------------------------------------------------------------------------


def test_the_filing_stream_prints_a_cny_value_in_yuan_with_its_split():
    (r,) = rows_from_ratified([_row()], [], [], _W)
    assert r.headline.endswith("cash CN¥1.2B · burn CN¥50.0M/qtr")
    assert "Of which short-term investments CN¥300.0M" in r.detail


def test_the_filing_stream_prints_a_usd_quarterly_value_in_dollars():
    (r,) = rows_from_ratified(
        [_row(currency="USD", source="10-q", investments_usd=None, cash_usd=51_800_000.0)],
        [],
        [],
        _W,
    )
    assert r.headline.endswith("cash $51.8M · burn $50.0M/qtr")
    assert not any("short-term investments" in line for line in r.detail)


def test_the_filing_stream_marks_an_annual_value_without_a_currency_and_not_a_quarterly_one():
    (annual,) = rows_from_ratified([_row(currency=None, investments_usd=None)], [], [], _W)
    assert annual.headline.endswith("cash ?1.2B · burn ?50.0M/qtr")
    (old,) = rows_from_ratified(
        [_row(currency=None, source="10-q", investments_usd=None)], [], [], _W
    )
    assert old.headline.endswith("cash $1.2B · burn $50.0M/qtr")


def _company(db) -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, "CNYCO", "CNY Co", "0000000009", date(2020, 1, 1)),
        )
    db.commit()
    return sid


def test_an_approved_cny_candidate_reads_in_yuan_on_the_filing_stream(db):
    """The plan's PR 6 test: approving a CNY candidate stores currency='CNY' and the Filing Stream row prints CN¥."""
    sid = _company(db)
    cid = repo.append_candidate(
        db,
        security_id=sid,
        fact_type="cash_burn",
        outcome="flag",
        flags=["annual-statements"],
        source="annual-statements",
        source_ref=REF,
        event_date=date(2025, 12, 31),
        cash_usd=1_200_000_000,
        quarterly_burn_usd=50_000_000,
        statement_currency="CNY",
        investments_usd=300_000_000,
    )
    db.commit()
    steward.approve(db, cid, by=STEWARD)
    db.rollback()
    shown = state.read_state(db, sid, "cash_burn").value
    db.rollback()
    assert shown is not None and shown["currency"] == "CNY"
    (r,) = rows_from_ratified([shown], [], [], _W)
    assert "cash CN¥1.2B · burn CN¥50.0M/qtr" in r.headline
    assert "$" not in r.headline


# --- the scored runway meter's provenance (the Workbench rail, the Cockpit name panel) -----------------------


class _Pit:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def cash_burn_facts(self, sid):
        return [self.row]


def test_the_meter_provenance_carries_the_currency_and_the_split():
    fig = scoring._runway(_Pit(_row()), uuid4(), DEFAULT_CONFIG)
    d = fig.provenance[0].detail
    assert (d["currency"], d["investments_usd"]) == ("CNY", 300_000_000.0)
    assert fig.value == 72.0  # the months are a ratio: unchanged by the currency


def test_the_meter_caveat_speaks_the_values_currency():
    row = _row(ytd_quarterly_burn_usd=10_000_000.0, ytd_burn_days=272)
    d = scoring._runway(_Pit(row), uuid4(), DEFAULT_CONFIG).provenance[0].detail
    assert "CN¥50.0M" in d["pace_caveat"] and "$" not in d["pace_caveat"]


# --- the Peer Table ----------------------------------------------------------------------------------------------


def test_the_peer_table_cell_carries_the_currency_and_the_split():
    c = runway_cell(_row(ytd_quarterly_burn_usd=10_000_000.0, ytd_burn_days=272))
    assert (c.currency, c.source, c.investments_usd) == ("CNY", "annual-statements", 300_000_000.0)
    assert c.pace_caveat is not None and "CN¥" in c.pace_caveat and "$" not in c.pace_caveat


# --- the steward page's pace -------------------------------------------------------------------------------------


def test_the_steward_pace_caveat_speaks_the_values_and_the_candidates_currency():
    paced = {"ytd_quarterly_burn_usd": 10_000_000.0, "ytd_burn_days": 272}
    fact = apv._pace(_row(**paced))
    assert fact is not None and "CN¥" in (fact.caveat or "")
    cand = {
        **_row(currency=None, **paced),
        "statement_currency": "JPY",
        "flags": ["annual-statements"],
    }
    p = apv._pace(cand)
    assert p is not None and "¥50.0M" in (p.caveat or "") and "$" not in (p.caveat or "")


# --- the MCP agent tools ----------------------------------------------------------------------------------------


def test_the_agent_triage_view_carries_the_currency_and_the_split_beside_the_cash():
    """The read-only agent tools print no symbol: they copy the API's figures, and the runway provenance's detail now
    carries the stored currency and the investment part beside ``cash_usd`` (``get_triage`` section=member).
    """
    from agent_tools.views.research import triage

    fig = scoring._runway(_Pit(_row()), uuid4(), DEFAULT_CONFIG)
    out = triage._figure(fig.model_dump(mode="json"))
    d = out["provenance"][0]["detail"]
    assert (d["cash_usd"], d["currency"], d["investments_usd"]) == (
        1_200_000_000.0,
        "CNY",
        300_000_000.0,
    )
