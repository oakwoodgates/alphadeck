"""Loaders over ``tests/fixtures/edgar/joint/`` — REAL Form 4 / 4/A ownership documents behind the JOINT-REPORT
rule (``domain/insider_joint_reports.py``), never hand-made.

PROVENANCE. Every XML is the document the app itself fetched from EDGAR, copied READ-ONLY and byte-for-byte out of
prod's EDGAR cache (``/data/edgar_cache/forms/<accession>/<doc>`` in the backend container — ``docker cp`` / ``tar``
out of it, nothing written into it) on 2026-10-21; content unedited (git normalizes line endings like every other
committed fixture). ``FILINGS`` below (form, EDGAR filing date, SEC acceptance instant, issuer CIK, ticker) is each
issuer's cached submissions index (``filings.recent``), copied the same way the same day.

What each set is (MEASURED 2026-10-21 on prod, read-only):
- INFQ — the reported symptom. Maverick Capital's JOINT filings (three reporting owners: Maverick Capital Ltd,
  Maverick Capital Management LLC, Lee S. Ainslie III — stored once, under the first) and David B. Singer's OWN
  filings (a director; a managing partner of the funds' general partner) report the same fund trades:
  ``-000481`` / ``-000486`` (12 of Maverick's 17 sales of 2026-05-21/22, every number identical, all held
  indirectly; four leave their fund at 0), ``-000492`` / ``-000493`` (code-J transfers of 2026-05-27) and their
  4/As ``-000507`` / ``-000508`` (each ADDS the same four 2026-05-27 sales, $47.6M — "$95.2M across 2 insiders").
- AMKR — one purchase by Sujoda Investments, LP (the DIRECT holder) re-reported by Sujoda Management, LLC, Susan
  Y. Kim, David D. Kim and John T. Kim (indirect): 869,565 sh @ $21.85 on 2025-02-24 ($19.0M, stored 5x) and
  441,589 sh @ $21.85 on 2025-08-01 ($9.65M, stored 5x).
- SMCI — Sara Liu's sale (direct) re-reported by her husband Charles Liang "By Spouse" (2026-10-07).
- OKLO — Jacob DeWitte and Caroline Cochran (married): each reports his/her own sales (direct) AND the other's
  (indirect) — two real trades per price, each stored twice (2026-10-01).
- ALAB — the NEGATIVE: two co-founders' automatic sell-to-cover sales, identical in every number including the
  post-trade holding, each of his OWN shares (both direct) — two trades.
- TSM — the NEGATIVE: two VPs' ESPP-trust buys of the same size at the same price (indirect) leaving DIFFERENT
  holdings — two positions, two trades.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import psycopg

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import ingest_form4
from ingest.edgar.form4a import AmendmentIngest, ingest_form4a
from ingest.edgar.submissions import parse_acceptance

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar" / "joint"


@dataclass(frozen=True)
class Filing:
    form: str
    filed: date
    accepted: datetime
    cik: str
    ticker: str


def _f(form: str, filed: str, accepted: str, cik: str, ticker: str) -> Filing:
    acc = parse_acceptance(accepted)
    assert acc is not None
    return Filing(form, date.fromisoformat(filed), acc, cik, ticker)


FILINGS: dict[str, Filing] = {
    # INFQ — Infleqtion (CIK 2007825): Maverick's joint filings + Singer's own, two 4/As
    "0001012975-26-000481": _f("4", "2026-05-26", "2026-05-26T22:57:48.000Z", "0002007825", "INFQ"),
    "0001012975-26-000486": _f("4", "2026-05-27", "2026-05-28T00:10:40.000Z", "0002007825", "INFQ"),
    "0001012975-26-000492": _f("4", "2026-05-28", "2026-05-29T00:00:34.000Z", "0002007825", "INFQ"),
    "0001012975-26-000493": _f("4", "2026-05-28", "2026-05-29T00:00:55.000Z", "0002007825", "INFQ"),
    "0001012975-26-000507": _f(
        "4/A", "2026-05-29", "2026-05-30T01:36:26.000Z", "0002007825", "INFQ"
    ),
    "0001012975-26-000508": _f(
        "4/A", "2026-05-29", "2026-05-30T01:37:20.000Z", "0002007825", "INFQ"
    ),
    # AMKR — Amkor (CIK 1047127): one Sujoda Investments purchase, five filers, twice
    "0001509340-25-000002": _f("4", "2025-02-26", "2025-02-27T03:27:08.000Z", "0001047127", "AMKR"),
    "0001158922-25-000006": _f("4", "2025-02-26", "2025-02-27T03:16:01.000Z", "0001047127", "AMKR"),
    "0001158924-25-000002": _f("4", "2025-02-26", "2025-02-27T03:31:00.000Z", "0001047127", "AMKR"),
    "0001158926-25-000004": _f("4", "2025-02-26", "2025-02-27T03:18:37.000Z", "0001047127", "AMKR"),
    "0001902339-25-000002": _f("4", "2025-02-26", "2025-02-27T03:24:56.000Z", "0001047127", "AMKR"),
    "0001509340-25-000007": _f("4", "2025-08-05", "2025-08-06T03:21:58.000Z", "0001047127", "AMKR"),
    "0001158922-25-000013": _f("4", "2025-08-05", "2025-08-06T03:09:10.000Z", "0001047127", "AMKR"),
    "0001158924-25-000005": _f("4", "2025-08-05", "2025-08-06T03:27:48.000Z", "0001047127", "AMKR"),
    "0001158926-25-000009": _f("4", "2025-08-05", "2025-08-06T03:16:16.000Z", "0001047127", "AMKR"),
    "0001902339-25-000007": _f("4", "2025-08-05", "2025-08-06T03:20:48.000Z", "0001047127", "AMKR"),
    # SMCI — Super Micro (CIK 1375365): a wife's sale, her husband's "By Spouse"
    "0001392941-26-000015": _f("4", "2026-10-09", "2026-10-10T00:44:36.000Z", "0001375365", "SMCI"),
    "0001392942-26-000014": _f("4", "2026-10-09", "2026-10-10T00:44:09.000Z", "0001375365", "SMCI"),
    # OKLO — Oklo (CIK 1849056): two spouses, each reporting both
    "0002021433-26-000021": _f("4", "2026-10-05", "2026-10-06T00:01:57.000Z", "0001849056", "OKLO"),
    "0002021786-26-000021": _f("4", "2026-10-05", "2026-10-06T00:02:15.000Z", "0001849056", "OKLO"),
    # ALAB — Astera Labs (CIK 1736297): two co-founders' own identical sales (the negative)
    "0001736297-25-000115": _f("4", "2025-08-20", "2025-08-21T04:28:11.000Z", "0001736297", "ALAB"),
    "0001736297-25-000117": _f("4", "2025-08-20", "2025-08-21T04:30:21.000Z", "0001736297", "ALAB"),
    # TSM — Taiwan Semiconductor (CIK 1046179): two VPs' ESPP-trust buys, different holdings (the negative)
    "0001046179-26-000692": _f("4", "2026-10-08", "2026-10-08T14:21:25.000Z", "0001046179", "TSM"),
    "0001046179-26-000714": _f("4", "2026-10-08", "2026-10-08T14:32:22.000Z", "0001046179", "TSM"),
}

CIKS = {f.ticker: f.cik for f in FILINGS.values()}


def xml(accession: str) -> str:
    return (FIXTURES / f"{accession}.xml").read_text(encoding="utf-8")


def forms_of(ticker: str, form: str = "4") -> list[str]:
    return sorted(a for a, f in FILINGS.items() if f.ticker == ticker and f.form == form)


def make_security(conn: psycopg.Connection, ticker: str) -> uuid.UUID:
    """One ``security_master`` row carrying the REAL issuer CIK (the other-issuer screen reads it)."""
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, CIKS[ticker], f"{ticker} (fixture)", "2020-01-01"),
        )
    conn.commit()
    return sid


def ingest_forms(
    conn: psycopg.Connection,
    sid: uuid.UUID,
    accessions: list[str],
    *,
    recorded_at: datetime | None = None,
) -> int:
    """Append real Form 4s the way the nightly leg does (member-screened, the SEC acceptance threaded)."""
    n = 0
    for acc in accessions:
        f = FILINGS[acc]
        n += ingest_form4(
            conn,
            sid,
            xml(acc),
            acc,
            accepted=f.accepted,
            recorded_at=recorded_at,
            member_cik=f.cik,
        ).appended
    conn.commit()
    return n


def ingest_amendment(
    conn: psycopg.Connection,
    sid: uuid.UUID,
    accession: str,
    *,
    recorded_at: datetime | None = None,
) -> AmendmentIngest:
    """Store + link one real 4/A against what is stored for ``sid``, as the nightly 4/A leg does."""
    f = FILINGS[accession]
    originals = [a for a, g in FILINGS.items() if g.ticker == f.ticker and g.form == "4"]
    out = ingest_form4a(
        conn,
        sid,
        xml(accession),
        accession,
        member_cik=f.cik,
        filed_by_accession={a: FILINGS[a].filed for a in originals},
        accepted_by_accession={a: FILINGS[a].accepted for a in originals},
        filed=f.filed,
        accepted=f.accepted,
        recorded_at=recorded_at,
    )
    conn.commit()
    return out


def seed(conn: psycopg.Connection, ticker: str) -> uuid.UUID:
    """A security with every real Form 4 of the set stored, then every real 4/A stored + linked."""
    sid = make_security(conn, ticker)
    ingest_forms(conn, sid, forms_of(ticker))
    for acc in forms_of(ticker, "4/A"):
        ingest_amendment(conn, sid, acc)
    return sid
