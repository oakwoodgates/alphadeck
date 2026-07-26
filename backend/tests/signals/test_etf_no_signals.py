from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from signals import dilution_clock, insider_conviction
from signals.base import PointInTimeData

# ETF Sleeve, Slice 1 — the sleeve is an EXPRESSION, never a call input (#4/#6). A `fund` has no insider
# Form 4 and no convertible notes, so the equity-only detectors read empty facts and return None: no error,
# no nonsense "no insider activity" signal. The events-keyed assembler then never enters a fund (which fires
# nothing) into any armed/risk set — no instrument_kind gate is needed inside calls/. This test PROVES the
# no-op on a real, marked ETF row read through the real point-in-time view.


def _etf(db, ticker="LIT"):
    """Insert a fund sleeve row the surface-ETF way: cik=None, instrument_kind='etf'."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "etf", date(2026, 1, 1)),
        )
    db.commit()
    return sid


def test_equity_detectors_noop_on_an_etf(db):
    sid = _etf(db)
    asof = date(2026, 6, 1)
    pit = PointInTimeData(db, asof=asof, tenant_id=DEFAULT_TENANT_ID)
    # insider conviction (Key-1): no code-P open-market buys on a fund -> silent, no error
    assert insider_conviction.detect(pit, sid, asof) is None
    # dilution risk: no convertible-note facts on a fund -> silent, no error
    assert dilution_clock.detect(pit, sid, asof) is None
