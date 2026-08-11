"""§2.2 fundamentals ingest — extraction (pure) + the two load-bearing DB guarantees.

- **Knowability (R17):** a quarter is stamped ``valid_from = filed``, so a filed-lagged period is INVISIBLE
  to an as-of read between its period end and its filing, VISIBLE at/after the filing (the no-backfill trap
  closed — proves the FILED stamp, not today).
- **Idempotency:** the real fetch->extract->store path run twice appends ZERO the second time — asserted by
  COUNTING THE TABLE (the as-of read dedups, so a duplicate append would hide behind a correct read).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from uuid import UUID

import psycopg

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.edgar.client import EdgarClient
from ingest.fundamentals import (
    QuarterPoint,
    extract_revenue_quarters,
    ingest_fundamentals_for_security,
    store_quarters,
)

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _rev_row(start: str, end: str, val: float, filed: str, accn: str, fy: int, fp: str) -> dict:
    return {
        "start": start,
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "filed": filed,
    }


def _companyfacts(rows: list[dict], concept: str = "Revenues") -> dict:
    return {"cik": 1234567, "facts": {"us-gaap": {concept: {"units": {"USD": rows}}}}}


def _count(conn: psycopg.Connection, sid: UUID) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_fundamentals WHERE security_id = %s", (sid,))
        return cur.fetchone()["n"]


# --------------------------------------------------------------------------------------------------------
# extraction (pure — no DB, the extract_facts precedent)
# --------------------------------------------------------------------------------------------------------


def test_extract_native_quarters_carry_their_own_filed_and_accession():
    cf = _companyfacts(
        [
            _rev_row("2023-01-01", "2023-03-31", 100, "2023-05-10", "acc-q1", 2023, "Q1"),
            _rev_row("2023-04-01", "2023-06-30", 110, "2023-08-09", "acc-q2", 2023, "Q2"),
            _rev_row("2023-07-01", "2023-09-30", 120, "2023-11-08", "acc-q3", 2023, "Q3"),
        ]
    )
    qs = extract_revenue_quarters(cf)
    assert [q.period_end for q in qs] == [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30)]
    assert all(q.basis == "native" and q.metric_key == "revenue" for q in qs)
    assert qs[0].filed == date(2023, 5, 10) and qs[0].accession == "acc-q1" and qs[0].value == 100.0


def test_extract_derives_q4_from_fy_minus_9mo_stamped_at_the_10k():
    cf = _companyfacts(
        [
            _rev_row(
                "2023-01-01", "2023-09-30", 330, "2023-11-08", "acc-q3", 2023, "Q3"
            ),  # 9mo YTD
            _rev_row("2023-01-01", "2023-12-31", 500, "2024-02-20", "acc-fy", 2023, "FY"),  # 10-K
        ]
    )
    q4 = [q for q in extract_revenue_quarters(cf) if q.period_end == date(2023, 12, 31)]
    assert len(q4) == 1
    assert q4[0].basis == "derived" and q4[0].value == 170.0  # 500 − 330
    # knowability + provenance = the LATER filing (the 10-K), never the earlier 9-month 10-Q
    assert q4[0].filed == date(2024, 2, 20) and q4[0].accession == "acc-fy"


def test_extract_declines_a_non_positive_derived_quarter():
    # FY total < the 9-month YTD -> a negative "Q4" -> a bad pair, never fabricated as revenue (#9/#3)
    cf = _companyfacts(
        [
            _rev_row("2023-01-01", "2023-09-30", 600, "2023-11-08", "acc-q3", 2023, "Q3"),
            _rev_row("2023-01-01", "2023-12-31", 500, "2024-02-20", "acc-fy", 2023, "FY"),
        ]
    )
    assert all(q.period_end != date(2023, 12, 31) for q in extract_revenue_quarters(cf))


def test_extract_drops_rows_that_cannot_be_stamped():
    cf = _companyfacts(
        [
            _rev_row("2023-01-01", "2023-03-31", 100, "2023-05-10", "acc-q1", 2023, "Q1"),
            {
                "start": "2023-04-01",
                "end": "2023-06-30",
                "val": 110,
                "fy": 2023,
                "fp": "Q2",
            },  # no filed/accn
        ]
    )
    assert [q.period_end for q in extract_revenue_quarters(cf)] == [date(2023, 3, 31)]


# --------------------------------------------------------------------------------------------------------
# knowability (R17) — the filed-vs-period-end stamp
# --------------------------------------------------------------------------------------------------------


def test_store_stamps_valid_from_at_filed_not_period_end(db, security_id):
    """A Q1 fact (period ends 2023-03-31) filed 2023-05-10 is INVISIBLE to an as-of read at any T in
    [period_end, filed) and VISIBLE at T >= filed — the FILED stamp, not today, not the period end.
    """
    q = QuarterPoint(
        metric_key="revenue",
        period_end=date(2023, 3, 31),
        value=1_000_000.0,
        filed=date(2023, 5, 10),
        accession="acc-1",
        basis="native",
        fiscal_period="Q1",
        fiscal_year=2023,
    )
    assert store_quarters(db, security_id, [q]).appended == 1
    db.commit()

    def rd(asof: date) -> list[dict]:
        return as_of(
            db,
            "fact_fundamentals",
            security_id=security_id,
            asof=asof,
            known_at=_PIN,
            tenant_id=DEFAULT_TENANT_ID,
        )

    assert rd(date(2023, 3, 31)) == []  # the period end — not yet filed -> invisible (no lookahead)
    assert rd(date(2023, 5, 9)) == []  # the day before the filing -> still invisible
    rows = rd(date(2023, 5, 10))  # the filing day -> visible
    assert len(rows) == 1
    assert rows[0]["valid_from"] == date(
        2023, 5, 10
    )  # stamped at FILED, not the 2023-03-31 period end
    assert rows[0]["period_end"] == date(2023, 3, 31) and float(rows[0]["value"]) == 1_000_000.0


# --------------------------------------------------------------------------------------------------------
# idempotency — COUNT the table (the real fetch->extract->store path, twice)
# --------------------------------------------------------------------------------------------------------


def test_ingest_is_idempotent_count_the_table(db, security_id, tmp_path):
    cik = "0001234567"  # matches the security_id fixture's cik
    rows = [
        _rev_row("2023-01-01", "2023-03-31", 100, "2023-05-10", "q1", 2023, "Q1"),
        _rev_row("2023-04-01", "2023-06-30", 110, "2023-08-09", "q2", 2023, "Q2"),
        _rev_row("2023-07-01", "2023-09-30", 120, "2023-11-08", "q3", 2023, "Q3"),
        _rev_row("2023-01-01", "2023-09-30", 330, "2023-11-08", "q3", 2023, "Q3"),  # 9mo YTD
        _rev_row(
            "2023-01-01", "2023-12-31", 500, "2024-02-20", "fy", 2023, "FY"
        ),  # FY -> derived Q4
    ]
    fc = tmp_path / "companyfacts"
    fc.mkdir(parents=True)
    (fc / f"CIK{int(cik):010d}.json").write_text(json.dumps(_companyfacts(rows)), encoding="utf-8")
    client = EdgarClient(
        cache_dir=tmp_path, allow_live=False
    )  # cache-first: reads the fixture, no network
    sec = Security(id=security_id, tenant_id=DEFAULT_TENANT_ID, ticker="DEVCO", cik=cik)

    r1 = ingest_fundamentals_for_security(db, sec, client=client, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    n1 = _count(db, security_id)
    assert n1 == 4  # native Q1/Q2/Q3 + one derived Q4
    assert r1.appended == n1 and r1.skipped == 0

    r2 = ingest_fundamentals_for_security(db, sec, client=client, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert _count(db, security_id) == n1  # COUNT-the-table: the re-run appended NOTHING
    assert r2.appended == 0 and r2.skipped == n1
