from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_stooq_csv
from signals.base import PointInTimeData

_F = Path(__file__).resolve().parent.parent / "fixtures"  # test-only EDGAR/price samples
_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"  # shared HIMS demo samples
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

    xml = (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8")
    ingest_form4(db, security_id, xml, "0001773751-26-000086")
    pit = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN)
    ev = insider_conviction.detect(pit, security_id, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.grade is Grade.CORE
    assert "1,172,974" in ev.label


def test_security_name_reads_the_master(db):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "KYOCY", "0000054321", "KYOCERA CORP", "2020-01-01"),
        )
    db.commit()
    pit = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN)
    assert pit.security_name(sid) == "KYOCERA CORP"
    assert pit.security_name(uuid.uuid4()) is None  # unknown id -> None (screen keeps the row)


def test_pit_self_filing_excluded_from_call_but_kept_on_tape(db, security_id):
    # the issuer files a Form 4 on ITSELF (owner CIK == issuer CIK): a big at-market code-P block that the
    # price screen would NOT catch. It must NOT feed Key-1 — but it must STAY in the txn stream (the tape).
    from domain.config import DEFAULT_CONFIG
    from signals import insider_conviction

    xml = (_F / "edgar" / "form4_sample.xml").read_text(encoding="utf-8")
    self_xml = xml.replace("0007654321", "0001234567").replace("Doe Jane", "Devco Inc")
    # make the buy a large at-market block so only the self-screen (not size/price) can suppress it
    self_xml = self_xml.replace(
        "<transactionShares><value>10000</value></transactionShares>",
        "<transactionShares><value>30000000</value></transactionShares>",
    )
    assert ingest_form4(db, security_id, self_xml, "acc-self") == 2
    db.commit()

    pit = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN)
    # kept on the tape: the self-filing rows are still returned by the point-in-time read (recall #9)
    txns = pit.insider_txns(security_id)
    assert any(t["accession"] == "acc-self" for t in txns)
    # but the call does not fire — the only buy is the issuer's self-filing, screened out of Key-1
    assert insider_conviction.detect(pit, security_id, date(2026, 6, 1), DEFAULT_CONFIG) is None


def test_pit_price_history_has_no_lookahead(db, security_id):
    rows = parse_stooq_csv((_F / "prices" / "DEVCO.csv").read_text(encoding="utf-8"))
    assert ingest_prices(db, security_id, rows) == 10

    hist = PointInTimeData(db, asof=date(2026, 5, 22), known_at=_KNOWN).price_history(security_id)
    assert len(hist) == 5  # 05-18 .. 05-22
    assert hist[-1]["d"] == date(2026, 5, 22)  # nothing after asof

    full = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN).price_history(security_id)
    assert len(full) == 10
