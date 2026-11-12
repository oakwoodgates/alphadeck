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

The pairs below (rule ``2026-11-amendment-linker-sees-held-rows``) each pair a Form 4 holding a held row with the
filer's own 4/A; the acceptance instants are the stored ones the orchestrator read from prod on 2026-11-11, and
each filing date is the acceptance's New York date (every one accepted before 22:00 ET — the date each 4/A's
``dateOfOriginalSubmission`` names for its original):

- ``impossible_year_second_of_two`` — accepted 2023-10-24 17:51: row 0 a sale dated 2023-10-23, row 1 a sale
  dated 2024-10-24 (the year rule). Its 4/A (2024-01-31 19:42) carries ONLY row 1, dated 2023-10-24.
- ``after_filing_one_of_eighteen`` — accepted 2005-11-16 21:35: eighteen rows, row 7 a 300-share sale at $3.37
  dated 2005-11-24 (after the filing); row 13 is another 300-share sale (at $3.04). Its 4/A (2005-11-29 21:30)
  carries ONLY row 7's sale, dated 2005-11-14.
- ``two_after_filing_gifts`` — accepted 2021-02-12 19:14: eight rows, rows 4 and 5 gifts dated 2020-11-30, rows 6
  and 7 gifts of the same size dated 2021-12-31 (after the filing). Its 4/A (2021-07-20 15:45) carries ONLY rows 6
  and 7, dated 2020-12-31.
- ``impossible_year_first_of_three`` — accepted 2018-09-21 17:32: row 0 a sale dated 2019-09-19 (the year rule),
  rows 1 and 2 sales dated 2018-09-20 / 09-21. Its 4/A (2020-03-13 16:06) carries ONLY row 0, dated 2018-09-19.
- ``after_filing_sale_beside_exercises`` — accepted 2015-11-03 16:08: two exercises dated 2015-11-02 and a sale
  dated 2015-11-20 (after the filing). Its 4/A (2016-03-02 10:19) restates all three, the sale dated 2015-11-02.
- ``only_row_after_filing`` — accepted 2022-01-03 17:05: one row, an award dated 2022-12-30 (after the filing).
  Its 4/A (2022-01-05 16:49) restates it dated 2021-12-30.
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
    # --- rule 2026-11-amendment-linker-sees-held-rows ---
    "0001628280-23-035012": _f(
        "4",
        "2023-10-24",
        "2023-10-24T21:51:01+00:00",
        "form4_impossible_year_second_of_two.xml",
        "impossible_year_second_of_two",
    ),
    "0001628280-24-002791": _f(
        "4/A",
        "2024-01-31",
        "2024-02-01T00:42:57+00:00",
        "form4a_corrects_impossible_year_second_of_two.xml",
        "impossible_year_second_of_two",
    ),
    "0000903651-05-000023": _f(
        "4",
        "2005-11-16",
        "2005-11-17T02:35:37+00:00",
        "form4_after_filing_one_of_eighteen.xml",
        "after_filing_one_of_eighteen",
    ),
    "0000903651-05-000029": _f(
        "4/A",
        "2005-11-29",
        "2005-11-30T02:30:55+00:00",
        "form4a_corrects_one_after_filing_row_of_eighteen.xml",
        "after_filing_one_of_eighteen",
    ),
    "0001209191-21-010430": _f(
        "4",
        "2021-02-12",
        "2021-02-13T00:14:13+00:00",
        "form4_two_after_filing_gifts.xml",
        "two_after_filing_gifts",
    ),
    "0001209191-21-047333": _f(
        "4/A",
        "2021-07-20",
        "2021-07-20T19:45:26+00:00",
        "form4a_corrects_two_after_filing_gifts.xml",
        "two_after_filing_gifts",
    ),
    "0001209191-18-051954": _f(
        "4",
        "2018-09-21",
        "2018-09-21T21:32:17+00:00",
        "form4_impossible_year_first_of_three.xml",
        "impossible_year_first_of_three",
    ),
    "0001209191-20-018843": _f(
        "4/A",
        "2020-03-13",
        "2020-03-13T20:06:29+00:00",
        "form4a_corrects_impossible_year_first_of_three.xml",
        "impossible_year_first_of_three",
    ),
    "0001209191-15-078036": _f(
        "4",
        "2015-11-03",
        "2015-11-03T21:08:51+00:00",
        "form4_after_filing_sale_beside_exercises.xml",
        "after_filing_sale_beside_exercises",
    ),
    "0001209191-16-104229": _f(
        "4/A",
        "2016-03-02",
        "2016-03-02T15:19:07+00:00",
        "form4a_restates_every_row_sale_redated.xml",
        "after_filing_sale_beside_exercises",
    ),
    "0001209191-22-000235": _f(
        "4",
        "2022-01-03",
        "2022-01-03T22:05:00+00:00",
        "form4_only_row_after_filing.xml",
        "only_row_after_filing",
    ),
    "0001209191-22-001751": _f(
        "4/A",
        "2022-01-05",
        "2022-01-05T21:49:20+00:00",
        "form4a_restates_only_row_redated.xml",
        "only_row_after_filing",
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
    "impossible_year_second_of_two": "0001807794",
    "after_filing_one_of_eighteen": "0000903651",
    "two_after_filing_gifts": "0000108312",
    "impossible_year_first_of_three": "0000866829",
    "after_filing_sale_beside_exercises": "0000903129",
    "only_row_after_filing": "0000030305",
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
