from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_stooq_csv
from signals.base import PointInTimeData

_F = Path(__file__).resolve().parent.parent / "fixtures"
# A far-future known_at: "we know everything recorded so far" — isolates the valid-time axis.
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_pit_insider_txns_have_no_lookahead(db, security_id):
    xml = (_F / "edgar" / "form4_sample.xml").read_text(encoding="utf-8")
    assert ingest_form4(db, security_id, xml, "0001234567-26-000123") == 2

    early = PointInTimeData(db, asof=date(2026, 5, 20), known_at=_KNOWN).insider_txns(security_id)
    assert {r["txn_code"] for r in early} == {"S"}  # only the 05-15 sale; the 06-01 buy is future

    later = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN).insider_txns(security_id)
    assert len(later) == 2


def test_ingest_form4_allows_multiple_same_day_txns(db, security_id):
    # one filing, same insider + same date (an exercise + a sale): both stored (txn_seq distinguishes)
    xml = (_F / "edgar" / "form4_multi_sameday.xml").read_text(encoding="utf-8")
    assert ingest_form4(db, security_id, xml, "acc-multi") == 2


def test_real_hims_wells_buy_fires_core_via_pit(db, security_id):
    # the real committed Wells Form 4: ingest -> read via PIT -> Key 1 fires CORE (single-strong calibration)
    from domain.config import DEFAULT_CONFIG
    from domain.enums import Grade
    from signals import insider_conviction

    xml = (_F / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8")
    ingest_form4(db, security_id, xml, "0001773751-26-000086")
    pit = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN)
    ev = insider_conviction.detect(pit, security_id, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.grade is Grade.CORE
    assert "1,172,974" in ev.label


def test_pit_price_history_has_no_lookahead(db, security_id):
    rows = parse_stooq_csv((_F / "prices" / "DEVCO.csv").read_text(encoding="utf-8"))
    assert ingest_prices(db, security_id, rows) == 10

    hist = PointInTimeData(db, asof=date(2026, 5, 22), known_at=_KNOWN).price_history(security_id)
    assert len(hist) == 5  # 05-18 .. 05-22
    assert hist[-1]["d"] == date(2026, 5, 22)  # nothing after asof

    full = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN).price_history(security_id)
    assert len(full) == 10
