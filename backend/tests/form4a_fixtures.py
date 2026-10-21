"""Loaders over ``tests/fixtures/edgar/form4a/`` — REAL Form 4 / 4/A ownership documents (R8-A), never
hand-made: every XML is the document EDGAR serves for that accession (content unedited; git normalizes line
endings like every other committed fixture).

PROVENANCE
- The 4/A documents (``0001628280-26-037326`` FLS, ``0001193125-26-166819`` AVEX, ``0001493152-26-044627``
  .. ``-044631`` ATCH, ``0001213900-26-107326`` ARBE, ``0001193125-25-282945`` AZTA,
  ``0001352573-26-000011`` TDG, ``0001104659-26-041960`` RPAY, ``0001856680-26-000016`` SKYT) were fetched
  from ``https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/<primary doc>`` with the declared
  User-Agent, rate-limited, on 2026-10-17 by the R8-A measurement (the 553-amendment research corpus).
- The original Form 4s (and the VWAV other-issuer Form 4 ``0001731122-26-000909``) were copied READ-ONLY
  from prod's EDGAR cache (``/data/edgar_cache/forms/<accession>/<doc>``) on 2026-10-17 — the same bytes the
  nightly Form 4 leg ingested.
- ``FILINGS`` below (form, EDGAR filing date, SEC acceptance instant, issuer CIK, ticker) is the companies'
  submissions index (``filings.recent``) for each accession, as cached on 2026-10-17.

What each one is (MEASURED 2026-10-17, see ``domain/insider_amendments.py``):
- FLS: a director's $164,275 code-P buy (``-035913``) the 4/A says the broker CANCELLED -> a zero-row VOID.
  With ``-035630`` (another director, same week) the original pair read CORE.
- AVEX: the CEO's 500-share row (``-164679``) the 4/A says he never held -> VOID.
- ATCH: five Form 4s coding open-market purchases ``A``; five 4/As recode them ``P`` -> CORRECTIONS (CORE).
- ARBE: 50 shares misreported; the 4/A says 50,000 -> CORRECTION.
- AZTA: a zero-row 4/A that only attaches a power of attorney — the CFO's $69,900 buy (``-107291``) STANDS
  (the case for routing a void to the operator instead of applying it).
- TDG: three byte-identical Form 4s (``-000008`` .. ``-000010``, a filing agent's duplicates); the 4/A names
  ``-000008`` the sole correct report -> DUPLICATES (a 1->N void).
- RPAY: a 4/A that changes only the acquired/disposed flag -> CLERICAL (Table I identical).
- SKYT: two different Form 4s by the same owner on the original date -> AMBIGUOUS.
- VWAV: VisionWave's own Form 4 as REPORTING OWNER of SaverOne stock -> the other-issuer screen.

Added for the signal-effect rule (2026-10-18; the 4/As from the same research corpus, the originals from prod's
cache, same provenance as above):
- FRHC ``0000929638-26-003399``: Table I identical to ``-003183``, but the reporting owner's role on its code-S
  rows goes "Chief Financial Officer" -> "Special Advisor to CEO" -> FIELD_CHANGE, routed (never clerical).
- AVAV ``0001538012-26-000006``: a zero-row 4/A voiding ``-000004``, whose only rows are code ``A`` (grants) ->
  a void with NO signal effect, resolved by the system.
- MASS ``0002115341-26-000008``: adds a code-``M`` row beside ``-000005``'s unchanged sale -> an addition with
  NO signal effect, resolved by the system.

Added for R8-B (2026-10-21; the filer's own correction applied by default). Every document — 4/A and original —
was copied READ-ONLY from prod's EDGAR cache (``/data/edgar_cache/forms/<accession>/<doc>``, the bytes the live
4/A leg fetched on its first night, 2026-10-20), and the FILINGS rows below are the companies' cached
submissions index (``filings.recent``) as of that night:
- IEHC ``0001174947-26-000849``: restates ``-000817``'s two director sales (2026-08-13/14) with the correct dates
  and prices and adds more shares sold -> an ADDITION touching code ``S`` inside the sell window at asof
  2026-10-20; applied by the system.
- MRAM ``0001628280-26-047722``: the CEO's tax-withholding sale ``-047404`` restated (date, price, shares) -> a
  RESTATEMENT touching code ``S`` inside the sell window; applied by the system.
- ELTP ``0001465825-12-000008``: two different Form 4s by the owner were filed on its original date
  (``-000004``, ``-000005`` — the latter a 2012 code-``S`` sale) -> AMBIGUOUS, its signal rows dated 2012:
  outside the review horizon -> "unresolved — historical"; the originals stand.

Under the R8-B ROW MATCH (``domain.insider_amendments.match_rows`` — an amendment replaces only the original rows
it accounts for), same provenance:
- IEHC and MRAM above change BOTH the date and the share count of their sales -> UNRESOLVED (restated, or an
  added sale?): owed to the operator, the original standing.
- MRCY ``0001049521-26-000071``: "to correct the inadvertent omission of the grant" — it reports only the grant
  (code ``A``, 2026-08-17) and none of ``-000054``'s two sales -> applied, the sales STAND, the grant is added.
- BAH ``0001443646-24-000094``: recodes ``-000061``'s ``S`` 2,142 sh to ``F`` (same date, same shares) -> the
  sale is replaced by the withholding; the original's grant (code ``A``, not restated) stands.
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

FIXTURES = Path(__file__).parent / "fixtures" / "edgar" / "form4a"


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
    # FLS — Flowserve (CIK 30625)
    "0001628280-26-035630": _f("4", "2026-05-15", "2026-05-16T00:27:48.000Z", "0000030625", "FLS"),
    "0001628280-26-035913": _f("4", "2026-05-18", "2026-05-19T00:01:38.000Z", "0000030625", "FLS"),
    "0001628280-26-037326": _f(
        "4/A", "2026-05-21", "2026-05-22T00:33:56.000Z", "0000030625", "FLS"
    ),
    # AVEX (CIK 2096300)
    "0001193125-26-164679": _f("4", "2026-04-20", "2026-04-21T01:09:27.000Z", "0002096300", "AVEX"),
    "0001193125-26-166819": _f(
        "4/A", "2026-04-21", "2026-04-21T20:15:05.000Z", "0002096300", "AVEX"
    ),
    # ATCH (CIK 1963088) — five originals, five 4/As
    "0001493152-26-044465": _f("4", "2026-09-28", "2026-09-28T17:22:42.000Z", "0001963088", "ATCH"),
    "0001493152-26-044467": _f("4", "2026-09-28", "2026-09-28T17:24:09.000Z", "0001963088", "ATCH"),
    "0001493152-26-044469": _f("4", "2026-09-28", "2026-09-28T17:25:26.000Z", "0001963088", "ATCH"),
    "0001493152-26-044471": _f("4", "2026-09-28", "2026-09-28T17:26:49.000Z", "0001963088", "ATCH"),
    "0001493152-26-044475": _f("4", "2026-09-28", "2026-09-28T17:29:49.000Z", "0001963088", "ATCH"),
    "0001493152-26-044627": _f(
        "4/A", "2026-09-28", "2026-09-29T01:01:26.000Z", "0001963088", "ATCH"
    ),
    "0001493152-26-044628": _f(
        "4/A", "2026-09-28", "2026-09-29T01:01:24.000Z", "0001963088", "ATCH"
    ),
    "0001493152-26-044629": _f(
        "4/A", "2026-09-28", "2026-09-29T01:01:27.000Z", "0001963088", "ATCH"
    ),
    "0001493152-26-044630": _f(
        "4/A", "2026-09-28", "2026-09-29T01:01:29.000Z", "0001963088", "ATCH"
    ),
    "0001493152-26-044631": _f(
        "4/A", "2026-09-28", "2026-09-29T01:01:30.000Z", "0001963088", "ATCH"
    ),
    # ARBE (CIK 1861841)
    "0001213900-26-106000": _f("4", "2026-10-01", "2026-10-02T03:28:04.000Z", "0001861841", "ARBE"),
    "0001213900-26-107326": _f(
        "4/A", "2026-10-06", "2026-10-07T03:00:11.000Z", "0001861841", "ARBE"
    ),
    # AZTA (CIK 933974)
    "0000950170-25-107291": _f("4", "2025-08-12", "2025-08-13T00:05:13.000Z", "0000933974", "AZTA"),
    "0001193125-25-282945": _f(
        "4/A", "2025-11-14", "2025-11-15T02:10:05.000Z", "0000933974", "AZTA"
    ),
    # TDG (CIK 1260221) — three duplicates and the 4/A naming the sole correct one
    "0001352573-26-000008": _f("4", "2026-06-22", "2026-06-22T20:50:18.000Z", "0001260221", "TDG"),
    "0001352573-26-000009": _f("4", "2026-06-22", "2026-06-22T20:50:59.000Z", "0001260221", "TDG"),
    "0001352573-26-000010": _f("4", "2026-06-22", "2026-06-22T20:57:35.000Z", "0001260221", "TDG"),
    "0001352573-26-000011": _f(
        "4/A", "2026-06-24", "2026-06-24T20:57:27.000Z", "0001260221", "TDG"
    ),
    # RPAY (CIK 1720592)
    "0001104659-26-041600": _f("4", "2026-04-09", "2026-04-10T02:33:54.000Z", "0001720592", "RPAY"),
    "0001104659-26-041960": _f(
        "4/A", "2026-04-10", "2026-04-11T00:01:14.000Z", "0001720592", "RPAY"
    ),
    # SKYT (CIK 1819974) — two different originals on the original date
    "0001856680-26-000010": _f("4", "2026-03-18", "2026-03-18T21:24:13.000Z", "0001819974", "SKYT"),
    "0001856680-26-000013": _f("4", "2026-03-18", "2026-03-18T22:13:13.000Z", "0001819974", "SKYT"),
    "0001856680-26-000016": _f(
        "4/A", "2026-03-23", "2026-03-23T21:01:39.000Z", "0001819974", "SKYT"
    ),
    # VWAV (CIK 2038439) — its own Form 4 as reporting owner of SaverOne (issuer CIK 1894693)
    "0001731122-26-000909": _f("4", "2026-07-06", "2026-07-06T14:07:15.000Z", "0002038439", "VWAV"),
    # FRHC (CIK 924805) — Table I identical, the CFO's role restated "Special Advisor to CEO" (field change)
    "0000929638-26-003183": _f("4", "2026-08-21", "2026-08-21T20:00:27.000Z", "0000924805", "FRHC"),
    "0000929638-26-003399": _f(
        "4/A", "2026-09-01", "2026-09-01T13:15:44.000Z", "0000924805", "FRHC"
    ),
    # AVAV (CIK 1368622) — a zero-row 4/A voiding a GRANT-only (code A) original: no signal effect
    "0001538012-26-000004": _f("4", "2026-07-06", "2026-07-07T01:04:59.000Z", "0001368622", "AVAV"),
    "0001538012-26-000006": _f(
        "4/A", "2026-07-15", "2026-07-15T21:51:57.000Z", "0001368622", "AVAV"
    ),
    # MASS (CIK 1555279) — an addition of a code-M row beside an UNCHANGED sale: no signal effect
    "0002115341-26-000005": _f("4", "2026-03-04", "2026-03-05T02:07:13.000Z", "0001555279", "MASS"),
    "0002115341-26-000008": _f(
        "4/A", "2026-03-05", "2026-03-06T02:50:42.000Z", "0001555279", "MASS"
    ),
    # R8-B — IEHC (CIK 50292): an ADDITION restating a director's sales (code S, in the sell window)
    "0001174947-26-000817": _f("4", "2026-08-20", "2026-08-20T20:55:54.000Z", "0000050292", "IEHC"),
    "0001174947-26-000849": _f(
        "4/A", "2026-09-08", "2026-09-08T21:02:23.000Z", "0000050292", "IEHC"
    ),
    # R8-B — MRAM (CIK 1438423): a RESTATEMENT of the CEO's tax-withholding sale (code S, in the sell window)
    "0001628280-26-047404": _f("4", "2026-07-06", "2026-07-07T02:42:41.000Z", "0001438423", "MRAM"),
    "0001628280-26-047722": _f(
        "4/A", "2026-07-08", "2026-07-09T04:10:16.000Z", "0001438423", "MRAM"
    ),
    # R8-B — ELTP (CIK 1053369): AMBIGUOUS — two different originals on the date, its signal rows from 2012
    "0001465825-12-000004": _f("4", "2012-01-30", "2012-01-31T04:38:37.000Z", "0001053369", "ELTP"),
    "0001465825-12-000005": _f("4", "2012-01-30", "2012-01-31T04:49:05.000Z", "0001053369", "ELTP"),
    "0001465825-12-000008": _f(
        "4/A", "2012-02-08", "2012-02-08T21:44:04.000Z", "0001053369", "ELTP"
    ),
    # R8-B row match — MRCY (CIK 1049521): a 4/A that only ADDS an omitted grant; the two sales stand
    "0001049521-26-000054": _f("4", "2026-08-19", "2026-08-20T02:31:45.000Z", "0001049521", "MRCY"),
    "0001049521-26-000071": _f(
        "4/A", "2026-09-15", "2026-09-16T02:10:43.000Z", "0001049521", "MRCY"
    ),
    # R8-B row match — BAH (CIK 1443646): a code S -> F recode (same date, same shares); the grant stands
    "0001443646-24-000061": _f("4", "2024-05-23", "2024-05-23T20:39:39.000Z", "0001443646", "BAH"),
    "0001443646-24-000094": _f(
        "4/A", "2024-05-28", "2024-05-28T20:38:00.000Z", "0001443646", "BAH"
    ),
}

CIKS = {f.ticker: f.cik for f in FILINGS.values()}


def xml(accession: str) -> str:
    return (FIXTURES / f"{accession}.xml").read_text(encoding="utf-8")


def originals_of(ticker: str) -> list[str]:
    return sorted(a for a, f in FILINGS.items() if f.ticker == ticker and f.form == "4")


def amendments_of(ticker: str) -> list[str]:
    return sorted(a for a, f in FILINGS.items() if f.ticker == ticker and f.form == "4/A")


def filed_by_accession(ticker: str) -> dict[str, date]:
    """What the submissions index says each of the ticker's Form 4s was FILED on (the link key)."""
    return {a: f.filed for a, f in FILINGS.items() if f.ticker == ticker and f.form == "4"}


def as_issuer(conn: psycopg.Connection, security_id: uuid.UUID, cik: str) -> None:
    """Make a fixture security the ISSUER of the Form 4 a test ingests onto it (its ``security_master.cik``).

    Since R8-A the read screens a Form 4 whose issuer is ANOTHER company (the member was its reporting
    owner), so a test that ingests, say, the real HIMS Wells Form 4 onto the generic ``security_id`` fixture
    (CIK 0001234567) must give that security HIMS's CIK — the shape every real member has (its own Form 4s
    name it as issuer). The asserted outcomes are unchanged; only the fixture's identity is made coherent.
    """
    with conn.cursor() as cur:
        cur.execute("UPDATE security_master SET cik = %s WHERE id = %s", (cik, security_id))
    conn.commit()


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


def ingest_originals(
    conn: psycopg.Connection,
    sid: uuid.UUID,
    ticker: str,
    *,
    recorded_at: datetime | None = None,
) -> int:
    """Append the ticker's real original Form 4s the way the nightly leg does (member-screened, with the
    SEC acceptance threaded). Returns rows appended."""
    n = 0
    for acc in originals_of(ticker):
        out = ingest_form4(
            conn,
            sid,
            xml(acc),
            acc,
            accepted=FILINGS[acc].accepted,
            recorded_at=recorded_at,
            member_cik=CIKS[ticker],
        )
        n += out.appended
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
    out = ingest_form4a(
        conn,
        sid,
        xml(accession),
        accession,
        member_cik=f.cik,
        filed_by_accession=filed_by_accession(f.ticker),
        filed=f.filed,
        accepted=f.accepted,
        recorded_at=recorded_at,
    )
    conn.commit()
    return out


def seed(conn: psycopg.Connection, ticker: str, *, amend_at: datetime | None = None) -> uuid.UUID:
    """A security with its real originals stored, then every one of its real 4/As stored + linked."""
    sid = make_security(conn, ticker)
    ingest_originals(conn, sid, ticker)
    for acc in amendments_of(ticker):
        ingest_amendment(conn, sid, acc, recorded_at=amend_at)
    return sid
