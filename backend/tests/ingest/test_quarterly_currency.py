"""The quarterly (10-Q / 10-K companyfacts) cash/burn value records its currency EXPLICITLY (GAP 2 of the extractor
correctness follow-up; operator decision D4).

``ingest.edgar.extract._cash_burn`` reads the cash instant, the investments and the operating cash flow ONLY from
companyfacts' ``USD`` unit (``_pick_instant``/``_newest``/``_compose``/``_quarter``/``_ytd_pace`` all pass
``"USD"``), so a value it reads is a USD value and says so: ``statement_currency='USD'``. A value it could not read
(no companyfacts at all) carries none: USD is never written as a guess. The machine stores what the extractor says.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from domain.config import ExtractorConfig
from domain.extraction import ExtractionResult
from domain.scopes import DATA_TENANT_ID
from ingest.edgar import extract as ex
from platform_values import machine, state

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
_REF = "https://www.sec.gov/Archives/edgar/data/9/0009-10q.htm"


def _facts(ticker: str, folder: str = "burn_pace") -> dict:
    return json.loads((_FX / folder / f"{ticker}.json").read_text(encoding="utf-8"))["facts"]


def _cash_burn(facts: dict):
    return ex._cash_burn(facts, "", _REF, date(2026, 9, 30), ExtractorConfig())


def test_a_quarterly_value_read_from_the_usd_unit_records_usd():
    cb = _cash_burn(_facts("UP"))
    assert cb.cash_usd is not None and cb.quarterly_burn_usd is not None
    assert cb.statement_currency == "USD"


def test_a_burn_without_a_cash_instant_still_records_usd():
    """Only the burn was read (``no-cash-instant``): it came from the USD unit, so the value is USD."""
    facts = {
        tax: {c: node for c, node in concepts.items() if not c.startswith("Cash")}
        for tax, concepts in _facts("UP").items()
    }
    cb = _cash_burn(facts)
    assert cb.cash_usd is None and cb.quarterly_burn_usd is not None
    assert "no-cash-instant" in cb.flags
    assert cb.statement_currency == "USD"


def test_nothing_read_records_no_currency():
    """No companyfacts: nothing was read from any unit, so no currency is claimed (never a USD guess)."""
    cb = _cash_burn({})
    assert (cb.cash_usd, cb.quarterly_burn_usd) == (None, None)
    assert cb.statement_currency is None


def test_a_value_in_another_unit_only_is_not_read_as_usd():
    """A filer whose cash and cash flow sit only under another unit (CNY) is not read by this path at all: nothing
    is valued, and no currency is claimed."""
    facts = json.loads(json.dumps(_facts("UP")).replace('"USD"', '"CNY"'))
    cb = _cash_burn(facts)
    assert (cb.cash_usd, cb.quarterly_burn_usd, cb.statement_currency) == (None, None, None)


def _company(db) -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, "USDQ", "USDQ Corp", "0000000009", date(2020, 1, 1)),
        )
    db.commit()
    return sid


def test_the_machine_stores_usd_on_an_applied_quarterly_value_once(db):
    sid = _company(db)
    cb = _cash_burn(_facts("RXRX"))
    assert cb.tier.value == "auto"
    result = ExtractionResult(facts=[cb])
    out = machine.record_extraction(db, sid, result, wait_s=0, today=date(2026, 11, 13))
    assert out["cash_burn"].applied
    db.rollback()
    shown = state.read_state(db, sid, "cash_burn", asof=date(2026, 11, 13)).value
    db.rollback()
    assert shown is not None and shown["currency"] == "USD"
    machine.record_extraction(db, sid, result, wait_s=0, today=date(2026, 11, 13))  # idempotent
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_cash_burn WHERE security_id = %s", (sid,))
        assert cur.fetchone()["n"] == 1
    db.rollback()
