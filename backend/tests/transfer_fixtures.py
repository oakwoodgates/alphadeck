"""Loaders over ``tests/fixtures/edgar/transfers/`` (+ the AMKR purchase filings already in ``joint/``) — REAL
Form 4 ownership documents behind the INTERNAL-TRANSFER screen (``domain/insider_internal_transfers.py``) and the
private-purchase note (``domain/insider_private_purchases.py``), never hand-made.

PROVENANCE. Every XML is the document the app itself fetched from EDGAR, copied READ-ONLY and byte-for-byte out of
prod's EDGAR cache (``/data/edgar_cache/forms/<accession>/<doc>`` in the cron container — ``tar`` out of it,
nothing written into it) on 2026-10-21; content unedited. ``ACCEPTED`` is each filing's SEC acceptance instant as
prod's ``fact_insider_txn.accepted`` holds it (read-only, the same day).

What each set is (MEASURED 2026-10-21 on prod, read-only):
- AMKR 2025-02-24 / 2025-08-01 — the reported symptom. James J. Kim SELLS (direct) 869,565 / 441,589 sh @ $21.85
  and Sujoda Investments, LP BUYS them (the five Sujoda filers of ``joint/``); Agnes C. Kim re-reports her
  husband's sale "By Spouse". Linked by the filings' own natures: Susan Y. Kim's purchase filing holds shares "By
  James J. Kim 2024 GRAT" / "By Agnes C. Kim 2024 GRAT"; James Kim's sale filing holds "By John T. Kim Family
  Trust".
- AMKR 2021-12-30 — Sujoda transfers 1,187,087 sh @ $24.68 to James Kim "in payment of the principal of a Note";
  the day's own filings name NO counterparty holding (their natures read "By GRATs", "By trusts", "By limited
  partnerships"), so the pair-local evidence cannot link it (the linking natures first appear in later filings).
- SLSN 2012-03-14 / 2012-09-12 — Bradford T. Whitmore's ONE filing holds his purchase AND Grace Brothers' sale
  ("purchased ... from Grace Brothers, Ltd. in a private transaction"; he is its general partner); Spurgeon Corp
  re-reports the sale with a different holding.
- Pershing Square (ESI, 2015-07-01 / 2018-08-06) and ValueAct (BHC, 2020-06-05) — one filing, one filer, a buy and
  a sale: funds that "purchased and sold Subject Securities among themselves"; "an internal restructuring".
- MRLN 2025-12-05/08/09 — Goldman Sachs' matched market-making fills (P and S of the same size at the same price),
  one filing; on 2025-12-08 TWO 63-share buys at $10.98 against ONE 63-share sale.
- FTHM 2024-09-12, RDNW 2021-12-30, ASTH 2021-04-28 / 2021-08-17 / 2022-06-17, FRMM 2023-12-15 — private sales
  between SEPARATE people (a director buying the founder's block; a Stock Purchase Agreement between two holders;
  a medical group buying doctors' shares): real conviction, the ledger note only.
- MGM 2022-02-14 — IAC buys 4.5M sh @ $45 from Corvex's funds (Keith Meister's filing): no linking evidence, no
  private-purchase footnote on IAC's buy — counted, no note.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import psycopg

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import ingest_form4

_ROOT = Path(__file__).resolve().parent / "fixtures" / "edgar"
TRANSFERS = _ROOT / "transfers"
JOINT = _ROOT / "joint"


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


# accession -> (the case's ticker, the SEC acceptance instant as prod stores it)
ACCEPTED: dict[str, tuple[str, datetime]] = {
    # AMKR 2025 — the five Sujoda purchase filers (joint/) + James Kim's sale + Agnes Kim's "By Spouse"
    "0001509340-25-000002": ("AMKR", _t("2025-02-26T22:27:08+00:00")),
    "0001158922-25-000006": ("AMKR", _t("2025-02-26T22:16:01+00:00")),
    "0001158924-25-000002": ("AMKR", _t("2025-02-26T22:31:00+00:00")),
    "0001158926-25-000004": ("AMKR", _t("2025-02-26T22:18:37+00:00")),
    "0001902339-25-000002": ("AMKR", _t("2025-02-26T22:24:56+00:00")),
    "0001158839-25-000004": ("AMKR", _t("2025-02-26T22:13:57+00:00")),
    "0001158840-25-000004": ("AMKR", _t("2025-02-26T22:40:05+00:00")),
    "0001509340-25-000007": ("AMKR", _t("2025-08-05T23:21:58+00:00")),
    "0001158922-25-000013": ("AMKR", _t("2025-08-05T23:09:10+00:00")),
    "0001158924-25-000005": ("AMKR", _t("2025-08-05T23:27:48+00:00")),
    "0001158926-25-000009": ("AMKR", _t("2025-08-05T23:16:16+00:00")),
    "0001902339-25-000007": ("AMKR", _t("2025-08-05T23:20:48+00:00")),
    "0001158839-25-000011": ("AMKR", _t("2025-08-05T23:32:13+00:00")),
    "0001158840-25-000012": ("AMKR", _t("2025-08-05T23:30:36+00:00")),
    # AMKR 2021-12-30 — Sujoda's note-payment transfers (the day's own filings name no counterparty holding)
    "0000899243-22-000398": ("AMKR21", _t("2022-01-04T19:20:30+00:00")),
    "0000899243-22-000407": ("AMKR21", _t("2022-01-04T19:43:57+00:00")),
    "0000899243-22-000425": ("AMKR21", _t("2022-01-04T20:21:38+00:00")),
    "0000899243-22-000435": ("AMKR21", _t("2022-01-04T20:32:45+00:00")),
    "0000899243-22-000440": ("AMKR21", _t("2022-01-04T20:40:29+00:00")),
    "0000899243-22-000442": ("AMKR21", _t("2022-01-04T20:43:13+00:00")),
    "0001158924-22-000001": ("AMKR21", _t("2022-01-03T22:23:51+00:00")),
    # SLSN 2012 — Whitmore's purchase + Grace Brothers' sale in ONE filing; Spurgeon Corp's re-report
    "0000933430-12-000003": ("SLSN", _t("2012-03-16T20:09:32+00:00")),
    "0001061060-12-000001": ("SLSN", _t("2012-03-16T20:26:54+00:00")),
    "0000933430-12-000009": ("SLSN", _t("2012-09-14T20:39:52+00:00")),
    "0001061060-12-000002": ("SLSN", _t("2012-09-14T20:49:13+00:00")),
    # same-filer rebalancing
    "0000899243-15-000170": ("ESI", _t("2015-07-02T21:03:34+00:00")),
    "0000899243-18-021562": ("ESI", _t("2018-08-07T20:39:56+00:00")),
    "0001418812-20-000049": ("BHC", _t("2020-06-10T00:24:36+00:00")),
    "0001193125-26-077683": ("MRLN", _t("2026-02-26T23:01:08+00:00")),
    # private purchases between separate people (kept; the ledger note)
    "0001578563-24-000412": ("FTHM", _t("2024-09-16T20:07:22+00:00")),
    "0001578563-24-000413": ("FTHM", _t("2024-09-16T20:16:06+00:00")),
    "0001642247-22-000001": ("RDNW", _t("2022-01-05T21:36:53+00:00")),
    "0001642247-22-000002": ("RDNW", _t("2022-01-05T22:30:07+00:00")),
    "0001104659-21-056716": ("ASTH", _t("2021-04-28T23:17:28+00:00")),
    "0001104659-21-056717": ("ASTH", _t("2021-04-28T23:18:17+00:00")),
    "0001628280-21-017366": ("ASTH", _t("2021-08-19T22:53:19+00:00")),
    "0001628280-21-017368": ("ASTH", _t("2021-08-19T22:55:44+00:00")),
    "0001104659-22-072513": ("ASTH", _t("2022-06-18T00:24:43+00:00")),
    "0001104659-22-072514": ("ASTH", _t("2022-06-18T00:26:03+00:00")),
    "0001213900-23-096502": ("FRMM", _t("2023-12-18T21:15:16+00:00")),
    "0001213900-23-096503": ("FRMM", _t("2023-12-18T21:15:24+00:00")),
    # MGM — IAC buys from Corvex's funds (no linking evidence, no footnote)
    "0000899243-22-006471": ("MGM", _t("2022-02-15T23:00:05+00:00")),
    "0001104659-22-024271": ("MGM", _t("2022-02-16T21:19:28+00:00")),
}


def xml(accession: str) -> str:
    """The real document — ``transfers/`` first, else the joint-report set it shares (the AMKR purchases)."""
    for base in (TRANSFERS, JOINT):
        p = base / f"{accession}.xml"
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(accession)


def issuer_cik(accession: str) -> str:
    return (ET.fromstring(xml(accession)).findtext("issuer/issuerCik") or "").strip()


def forms_of(case: str) -> list[str]:
    return sorted(a for a, (c, _) in ACCEPTED.items() if c == case)


def make_security(conn: psycopg.Connection, case: str) -> uuid.UUID:
    """One ``security_master`` row carrying the REAL issuer CIK (the other-issuer screen reads it)."""
    sid = uuid.uuid4()
    cik = issuer_cik(forms_of(case)[0])
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, case, cik, f"{case} (fixture)", "2000-01-01"),
        )
    conn.commit()
    return sid


def seed(conn: psycopg.Connection, case: str, *, recorded_at: datetime | None = None) -> uuid.UUID:
    """A security with every real Form 4 of the case stored the way the nightly leg stores it."""
    sid = make_security(conn, case)
    for acc in forms_of(case):
        ingest_form4(
            conn,
            sid,
            xml(acc),
            acc,
            accepted=ACCEPTED[acc][1],
            recorded_at=recorded_at,
            member_cik=issuer_cik(acc),
        )
    conn.commit()
    return sid
