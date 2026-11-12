"""Loaders over ``tests/fixtures/edgar/held/`` — REAL Form 4 / 4/A ownership documents whose transaction dates
cannot be true (rule ``2026-11-insider-rows-dated-after-filing``), never hand-made: every XML is the document
EDGAR serves for that accession, content unedited. ``FILINGS`` (form, filing date, SEC acceptance instant) is
the stored acceptance and filing date of each accession, as the orchestrator read them on 2026-11-11.

What each one is (times in New York):

- ``on_time_a`` — accepted 2015-01-02 08:54, period of report 2014-12-31, signed 2015-01-02: three buys all dated
  2015-12-31 (after the filing). On time -> each counts from 2015-01-02.
- ``on_time_b`` — accepted 2010-08-10 13:17, period 2010-08-10: one buy dated 2010-10-09. On time -> counts from
  2010-08-10.
- ``late`` — accepted 2021-03-18 16:47, period 2020-11-16: three buys dated 2021-11-16 / 12-03 / 12-04. A late
  filing (122 days) -> held, NOT returned until a steward decides.
- ``signed_ahead`` — accepted 2014-07-24 20:27, signed 2014-07-25: one sale dated 2014-07-25 — after the
  acceptance's market date but not after its own signature date (a filer in a time zone ahead of New York).
  NOT held: it stays counted as stored.
- ``impossible_year`` — accepted 2024-05-28 19:30, period 2024-05-24: six rows, rows 0 and 1 sales dated
  2028-05-24 (the year rule). On time -> both count from 2024-05-28; the other four read as stored.
- ``amended_after_filing`` — accepted 2020-03-20 16:56, period 2020-03-18: buys dated 03-18, 03-20, 03-30 (the
  last after the filing). Its 4/A (accepted 2020-03-20 21:34) restates all three with the last on 03-20: the
  FILER'S OWN CORRECTION — once the 4/A counts, that trade reads once, from the amendment.
- ``amended_impossible_year`` — accepted 2023-11-17 20:52, period 2023-11-16: row 1 a sale dated 2027-11-17
  (the year rule). Its 4/A (accepted 2023-11-28 20:47) carries that sale dated 2023-11-17.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import ingest_form4
from ingest.edgar.form4a import ingest_form4a

HELD_DIR = Path(__file__).parent / "fixtures" / "edgar" / "held"


@dataclass(frozen=True)
class Filing:
    form: str
    filed: date
    accepted: datetime
    file: str
    group: str


def _f(form: str, filed: str, accepted: str, file: str, group: str) -> Filing:
    return Filing(form, date.fromisoformat(filed), datetime.fromisoformat(accepted), file, group)


FILINGS: dict[str, Filing] = {
    "0001354488-15-000004": _f(
        "4",
        "2015-01-02",
        "2015-01-02T13:54:38+00:00",
        "form4_after_filing_on_time_a.xml",
        "on_time_a",
    ),
    "0001179110-10-012412": _f(
        "4",
        "2010-08-10",
        "2010-08-10T17:17:21+00:00",
        "form4_after_filing_on_time_b.xml",
        "on_time_b",
    ),
    "0000899243-21-012557": _f(
        "4", "2021-03-18", "2021-03-18T20:47:47+00:00", "form4_after_filing_late.xml", "late"
    ),
    "0001019687-14-002802": _f(
        "4",
        "2014-07-24",
        "2014-07-25T00:27:09+00:00",
        "form4_signed_ahead_of_new_york.xml",
        "signed_ahead",
    ),
    "0001140361-24-027755": _f(
        "4",
        "2024-05-28",
        "2024-05-28T23:30:23+00:00",
        "form4_impossible_year_on_time.xml",
        "impossible_year",
    ),
    "0001179110-20-004028": _f(
        "4",
        "2020-03-20",
        "2020-03-20T20:56:12+00:00",
        "form4_after_filing_then_amended.xml",
        "amended_after_filing",
    ),
    "0001179110-20-004036": _f(
        "4/A",
        "2020-03-20",
        "2020-03-21T01:34:31+00:00",
        "form4a_restates_after_filing_row.xml",
        "amended_after_filing",
    ),
    "0001628280-23-039558": _f(
        "4",
        "2023-11-17",
        "2023-11-18T01:52:27+00:00",
        "form4_impossible_year_then_amended.xml",
        "amended_impossible_year",
    ),
    "0001628280-23-040161": _f(
        "4/A",
        "2023-11-28",
        "2023-11-29T01:47:31+00:00",
        "form4a_restates_impossible_year_row.xml",
        "amended_impossible_year",
    ),
}

# each group's issuer CIK (the filings' own ``issuerCik``) — the member screen and the other-issuer screen read it
CIKS: dict[str, str] = {
    "on_time_a": "0001532619",
    "on_time_b": "0000793733",
    "late": "0001425450",
    "signed_ahead": "0001343009",
    "impossible_year": "0001283699",
    "amended_after_filing": "0001551152",
    "amended_impossible_year": "0001807794",
}
TICKERS: dict[str, str] = {g: f"HLD{i}" for i, g in enumerate(CIKS)}

# the ingest stamps a minute after the acceptance (a nightly that read the filing as soon as it was public), so
# the record clock and the public clock agree and a test can pin either
INGEST_LAG = timedelta(minutes=1)


def xml(accession: str) -> str:
    return (HELD_DIR / FILINGS[accession].file).read_text(encoding="utf-8")


def of_group(group: str, form: str) -> list[str]:
    return sorted(a for a, f in FILINGS.items() if f.group == group and f.form == form)


def make_security(conn: psycopg.Connection, group: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                sid,
                DEFAULT_TENANT_ID,
                TICKERS[group],
                CIKS[group],
                f"{group} (fixture)",
                "2000-01-01",
            ),
        )
    conn.commit()
    return sid


def ingest(conn: psycopg.Connection, sid: uuid.UUID, accession: str) -> None:
    """Ingest one filing the way the nightly legs do (member-screened, the acceptance threaded), recorded
    ``INGEST_LAG`` after its acceptance."""
    f = FILINGS[accession]
    rec = f.accepted + INGEST_LAG
    if f.form == "4":
        ingest_form4(
            conn,
            sid,
            xml(accession),
            accession,
            accepted=f.accepted,
            recorded_at=rec,
            member_cik=CIKS[f.group],
            filed=f.filed,
        )
    else:
        originals = of_group(f.group, "4")
        ingest_form4a(
            conn,
            sid,
            xml(accession),
            accession,
            member_cik=CIKS[f.group],
            filed_by_accession={a: FILINGS[a].filed for a in originals},
            accepted_by_accession={a: FILINGS[a].accepted for a in originals},
            filed=f.filed,
            accepted=f.accepted,
            recorded_at=rec,
        )
    conn.commit()


def seed(conn: psycopg.Connection, group: str, *, amendments: bool = True) -> uuid.UUID:
    """A security with the group's original Form 4 ingested (and its 4/A, unless ``amendments=False``)."""
    sid = make_security(conn, group)
    for acc in of_group(group, "4"):
        ingest(conn, sid, acc)
    if amendments:
        for acc in of_group(group, "4/A"):
            ingest(conn, sid, acc)
    return sid


def decide(
    conn: psycopg.Connection,
    sid: uuid.UUID,
    accession: str,
    txn_seq: int,
    action: str,
    *,
    at: datetime,
    ratified: date | None = None,
    revokes: uuid.UUID | None = None,
) -> uuid.UUID:
    """A steward's decision row, inserted directly (the writer is the next step), recorded ``at``."""
    did = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO insider_txn_hold_decision (id, tenant_id, security_id, accession, txn_seq, form, "
            "action, ratified_date, reason, basis, revokes, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, '4', %s, %s, %s, %s, %s, %s)",
            (
                did,
                DEFAULT_TENANT_ID,
                sid,
                accession,
                txn_seq,
                action,
                ratified,
                "fixture",
                f"steward:{action}",
                revokes,
                at,
            ),
        )
    conn.commit()
    return did


FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
