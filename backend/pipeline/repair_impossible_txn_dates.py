"""Repair CLI: delete ``fact_insider_txn`` rows whose transaction date CANNOT be true.

THE GAP: ``fact_insider_txn.valid_from`` is the filer's ``<transactionDate>``, taken verbatim, and some
filers serialize garbage. MEASURED on the dev copy of prod (595,376 rows, 2026-09-15): **29 rows across 15
distinct facts** carry an impossible date — a leading-zero year (``0023-03-23`` on an accession filed 2024)
or a year AHEAD of the filing (``2027-02-17`` on one filed 2024-02-20). It is FILER GARBAGE, confirmed
against the SEC documents themselves: ``0001628280-24-010030`` really contains
``<transactionDate><value>0023-03-23</value></transactionDate>``, and ``0001437749-24-004874`` really says
``2027-02-17``. Our parser reproduces both faithfully — there is nothing to re-derive. Both documents are
COMMITTED as fixtures (``tests/fixtures/edgar/form4_bbai_leading_zero_year.xml`` / ``form4_lscc_future_year
.xml``) and a test asserts the claim, so the disposition rests on an artifact in the repo rather than on
this paragraph. The 29/15 figure was re-measured independently on 2026-09-16 (unchanged), and the CLI's
SQL pre-filter was run against the same copy and returns exactly those 29 rows.

Live effect, and why the rows are not harmless: ``valid_from`` is the event-time half of the as-of gate, so
an ancient-dated row is visible to EVERY as-of read forever, and a future-dated one is invisible until that
date arrives and then wrongly visible from then on. The measured set carries ``txn_code`` A/F/M/S — none is
``P``, so none reaches the insider-BUY conviction detector, but the ``S`` rows can reach the ``insider_sell``
risk detector.

THE RULE is ``ingest.edgar.form4.implausible_txn_date`` — IMPORTED, never re-stated, so this tool and the
live ingest bound cannot drift apart (the ``repair_reconstructed_precreation`` pattern). A row is a candidate
iff that function returns a reason for its ``(valid_from, accession)``.

WHY DELETE, and not a correction (the question the table's discipline forces):
- We cannot write a corrected date. The filing IS the source and it says the impossible value; inferring the
  "intended" one would invent a fact (#3). ``periodOfReport`` is NOT a substitute — it is the EARLIEST
  reportable transaction date in the filing, not this row's, and on the BBAI / SNEX / GS filings it is itself
  corrupt.
- We could not re-VERSION it even if we wanted to: since migration 0037 the natural key is
  ``(tenant, security_id, accession, insider_name, valid_from, txn_seq)`` — ``valid_from`` is IN the key, so a
  row with a different date is a DIFFERENT fact, not a new version of this one. (The table also carries a
  live ``no_update`` trigger, so an in-place fix is impossible by construction; that trigger guards UPDATE
  only, so a DELETE needs no trigger dance.)
- Deleting every version of an impossible key leaves the table in exactly the state a fresh ingest under the
  new bound would produce — and the bound means a re-ingest of the same accession re-rejects it loudly rather
  than re-adding it. Every version of one key shares its ``valid_from`` (it is a key column), so the whole
  ``supersedes`` chain goes together and the self-referencing FK stays satisfied.

SCOPE: impossible DATES only. The 16-versions-for-6-facts ratio the platform note observed is the seed
pile-up's byte-identical re-versions; ``pipeline.dedup_identical_versions`` owns those, not this tool.

SAFETY (the ``repair_reconstructed_precreation`` shape):
- **Dry-run is the DEFAULT** and prints EVERY row it would delete; ``--apply`` deletes and prints per-reason
  counts + a SUMMARY line.
- ``DATABASE_URL`` MUST be set explicitly (no dev-default fallback for a destructive tool); the target is
  printed (host:port/db, credentials redacted) as the first line of every run.
- One transaction for the whole delete, and the rowcount must match what was printed or the run ROLLS BACK.

    DATABASE_URL=... python -m pipeline.repair_impossible_txn_dates            # dry-run (default)
    DATABASE_URL=... python -m pipeline.repair_impossible_txn_dates --apply    # delete + counts

Never run against prod by an agent — the operator runs it, after a backup.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from ingest.edgar.form4 import implausible_txn_date
from pipeline.dedup_identical_versions import require_database_url

# The Section 16 epoch, mirrored here as the SQL pre-filter's lower edge. It must stay in lockstep with
# ``ingest.edgar.form4._SECTION_16_EPOCH_YEAR``: the pre-filter is only allowed to be WIDER than the rule
# (an over-selected row is dropped by the imported rule; an under-selected one would be invisible), and a
# test pins that a 1993 transaction reported on a 2006 filing survives both.
_PREFILTER_FLOOR = date(1934, 1, 1)


@dataclass(frozen=True)
class ImpossibleRow:
    """One stored row whose transaction date cannot be true — everything the dry-run prints."""

    row_id: UUID
    security_id: UUID
    ticker: str | None
    accession: str
    insider_name: str | None
    txn_code: str | None
    txn_seq: int
    valid_from: date
    reason: str


def find_impossible_rows(conn: psycopg.Connection) -> list[ImpossibleRow]:
    """EVERY stored version whose ``valid_from`` fails ``implausible_txn_date``, ordered for reading.

    ALL versions, not the latest-version view: an impossible date is IN the natural key, so every version of
    such a key carries it and every one of them must go (a surviving older version would still be returned by
    an as-of read pinned before the newest).

    The SQL is a cheap SUPERSET pre-filter, not the decision — it mirrors the rule's two anchors so the scan
    stays indexed instead of pulling 595k rows into Python (below the Section 16 epoch, or above the year the
    row's own EDGAR accession encodes) — and the IMPORTED rule is the authority on every candidate it
    returns. A pre-filter that over-selects is safe (the rule drops it); one that under-selects would hide a
    row, so it is kept deliberately wider than the rule.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.id, f.security_id, f.accession, f.insider_name, f.txn_code, f.txn_seq, "
            "       f.valid_from, sm.ticker "
            "  FROM fact_insider_txn f "
            "  LEFT JOIN security_master sm "
            "         ON sm.id = f.security_id AND sm.tenant_id = f.tenant_id "
            " WHERE f.valid_from < %s "
            "    OR (f.accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$' "
            "        AND extract(year from f.valid_from) > "
            "            (CASE WHEN substring(f.accession from 12 for 2)::int BETWEEN 93 AND 99 "
            "                  THEN 1900 ELSE 2000 END) + substring(f.accession from 12 for 2)::int) "
            " ORDER BY f.accession, f.valid_from, f.txn_seq, f.id",
            (_PREFILTER_FLOOR,),
        )
        rows = cur.fetchall()
    out: list[ImpossibleRow] = []
    for r in rows:
        reason = implausible_txn_date(r["valid_from"], r["accession"])
        if reason is None:  # the pre-filter is a superset — the imported rule is the authority
            continue
        out.append(
            ImpossibleRow(
                row_id=r["id"],
                security_id=r["security_id"],
                ticker=r["ticker"],
                accession=r["accession"],
                insider_name=r["insider_name"],
                txn_code=r["txn_code"],
                txn_seq=r["txn_seq"],
                valid_from=r["valid_from"],
                reason=reason,
            )
        )
    return out


def delete_rows(conn: psycopg.Connection, rows: list[ImpossibleRow]) -> int:
    """DELETE exactly the given rows by id (uncommitted — the caller commits). Returns the row count the
    DELETE reported, which the caller checks against ``len(rows)``."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fact_insider_txn WHERE id = ANY(%s)", ([r.row_id for r in rows],))
        return cur.rowcount


def _redact(url: str) -> str:
    """host:port/dbname only — NEVER the credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port}{parts.path}"


def _class_of(row: ImpossibleRow) -> str:
    """Which of the rule's two anchors rejected this row — the summary's bucket. Derived from the DATE
    against the epoch, never by parsing the reason string (the message is human copy; the classification
    must not depend on its wording)."""
    return (
        "below the Section 16 epoch"
        if row.valid_from < _PREFILTER_FLOOR
        else "after the accession's filing year"
    )


def _print_rows(rows: list[ImpossibleRow]) -> None:
    for r in rows:
        who = r.insider_name or "?"
        print(
            f"  {r.ticker or r.security_id} {r.accession} seq={r.txn_seq} {who} "
            f"({r.txn_code or '?'}): transactionDate {r.valid_from.isoformat()} — {r.reason} "
            f"[row {r.row_id}]"
        )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Delete fact_insider_txn rows whose transaction date cannot be true (a leading-zero "
        "year, or a year after the filing). Filer garbage, verified against the SEC documents — no "
        "corrected date is invented. Dry-run by default; --apply deletes."
    )
    p.add_argument(
        "--apply", action="store_true", help="delete the rows (default: dry-run, print only)"
    )
    args = p.parse_args(argv)

    url = require_database_url()
    print(f"target DB : {_redact(url)}")

    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            print(f"connected : current_database() = {cur.fetchone()['db']}")

        rows = find_impossible_rows(conn)
        per_reason = Counter(_class_of(r) for r in rows)
        facts = {
            (r.security_id, r.accession, r.insider_name, r.valid_from, r.txn_seq) for r in rows
        }

        if not args.apply:
            print("\n=== repair_impossible_txn_dates — DRY-RUN (nothing written) ===")
            _print_rows(rows)
            conn.rollback()  # read-only; nothing to commit
            print(
                f"\nSUMMARY: dry-run only, nothing written — {len(rows)} row(s) across {len(facts)} "
                "distinct fact(s); rerun with --apply to delete them"
            )
            return

        print("\n=== repair_impossible_txn_dates — APPLY ===")
        _print_rows(rows)
        deleted = delete_rows(conn, rows)
        if deleted != len(rows):
            conn.rollback()
            print(
                f"\nABORTED: expected to delete {len(rows)} row(s) but the DELETE reported {deleted} — "
                "ROLLED BACK, nothing written"
            )
            raise SystemExit(1)
        conn.commit()
        for reason, n in sorted(per_reason.items()):
            print(f"  {reason}: -{n} row(s)")
        print(f"\nSUMMARY: total_deleted={deleted} distinct_facts={len(facts)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
