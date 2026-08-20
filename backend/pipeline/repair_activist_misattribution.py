"""One-time repair: delete the SELF-FILED rows the pre-fix ingest fanned onto the wrong subject.

THE DEFECT (root-caused + fixed at ingest in ``ingest/edgar/schedule13.py``): EDGAR indexes a 13D/G
under BOTH the filer's and the subject's submissions feed, so the per-SUBJECT enumeration also picked
up the schedules a basket member FILED **about other companies** — its OUTBOUND stakes — and stored
them under the member as if it were the subject. The self-evidently-wrong residue on the tape is the
**self-filed** shape: ``filer_cik`` == the SUBJECT security's own cik (a company is never its own 13D
subject). It is the SAME shape ``signals/activist_stake._is_misattributed`` screens out of the FIRE;
this CLI removes it from the TAPE so the screen is no longer load-bearing for it.

WHAT IT DELETES — only the self-filed logical facts. A ``(security, accession)`` whose LATEST version
resolves ``filer_cik`` == the subject's cik is a mis-attributed filing; ALL its versions are deleted
(the resolved self-filed row AND any earlier NULL-identity version of the same wrong filing — a
complete removal, no lingering wrong-subject row). A row whose filer is NOT the subject is left
untouched — including a **correctly-attributed sub-5%** row (a real filing about a real subject that the
fire-side screen drops on the pct rule, e.g. GameStop's 13D correctly on eBay's tape at 0.01%): that is
a real stake on the right subject, never deleted here.

WHY DELETE, not re-version. The bitemporal tables correct by APPENDING a new version (the ``no_update``
guard); but a self-filed row is not a fact that was true-then-corrected — it never belonged on this
subject at all (a mis-ROUTED write, not a mis-VALUED one). The true subject's own feed carries the
filing correctly. So the honest repair is removal; ``fact_activist_stake`` carries no delete guard, and
the operator takes a labeled backup first (the recovery point — restore, never re-ingest, per the
bitemporal no-backfill lesson). Idempotent by construction: after ``--apply`` no self-filed group
remains, so a re-run deletes zero (count the table, not the read).

    python -m pipeline.repair_activist_misattribution              # DRY-RUN (default): count + list
    python -m pipeline.repair_activist_misattribution --apply      # execute the deletes (own txn)

DO NOT run ``--apply`` against prod from a build — the main loop runs it on prod AFTER a backup, and a
data repair is reviewed against the LIVE constraint first (the 0037 lesson). The build validates it on
the auto-derived test DB only.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

from db.session import DEFAULT_TENANT_ID, database_url
from signals.activist_stake import _norm_cik  # the SAME normalization _is_misattributed uses

# The latest version per logical filing (tenant, security, accession) — the same grain the as-of read
# dedups on — joined to the SUBJECT's master identity so we can compare the filer to the subject's own
# cik. security_master.id is a unique PK (identity, updated in place, never read as-of), so the join
# is 1:1 — no fan-out.
_LATEST_PER_FILING = """
    SELECT DISTINCT ON (f.security_id, f.accession)
           f.security_id, f.accession, f.form, f.filer_cik, f.filer_name, f.pct_owned,
           m.cik AS subject_cik, m.ticker AS subject_ticker
    FROM fact_activist_stake f
    JOIN security_master m ON m.id = f.security_id AND m.tenant_id = f.tenant_id
    WHERE f.tenant_id = %s
    ORDER BY f.security_id, f.accession, f.recorded_at DESC, f.id DESC
"""


@dataclass
class RepairResult:
    """One run's full accounting — printed verbatim so the operator can count the table, not just trust
    the read. ``groups`` lists each self-filed filing (ticker / accession / filer) it targets."""

    applied: bool
    table_rows_before: int = 0
    table_rows_after: int = 0
    filings_targeted: int = 0  # distinct (security, accession) self-filed logical facts
    rows_deleted: int = 0  # ALL versions across the targeted filings
    groups: list[dict] = field(default_factory=list)


def _redact(url: str) -> str:
    """The DSN with its password masked — printed on every run so the target is explicit."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url)


def _count(conn: psycopg.Connection, tenant_id) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_activist_stake WHERE tenant_id = %s", (tenant_id,)
        )
        return cur.fetchone()["n"]


def self_filed_groups(conn: psycopg.Connection, tenant_id=DEFAULT_TENANT_ID) -> list[dict]:
    """Every mis-attributed logical filing: a ``(security, accession)`` whose LATEST version resolves
    ``filer_cik`` == the subject's own cik (leading-zero-insensitive, the ``_is_misattributed``
    normalization). NULL-safe: a missing filer or subject cik is never "self-filed" (kept, #9)."""
    with conn.cursor() as cur:
        cur.execute(_LATEST_PER_FILING, (tenant_id,))
        latest = cur.fetchall()
    groups = []
    for r in latest:
        fc, sc = _norm_cik(r["filer_cik"]), _norm_cik(r["subject_cik"])
        if fc and sc and fc == sc:  # the filer IS the subject company — a self-filed mis-fan
            groups.append(r)
    return groups


def run_repair(
    conn: psycopg.Connection,
    *,
    tenant_id=DEFAULT_TENANT_ID,
    apply: bool,
    log=print,
) -> RepairResult:
    """Find + (optionally) delete the self-filed rows. ``apply=False`` computes and reports what WOULD
    be deleted, touching nothing (the caller rolls back); ``apply=True`` deletes each targeted filing's
    rows and commits. Idempotent: a second ``--apply`` finds no self-filed group and deletes zero.
    """
    res = RepairResult(applied=apply)
    res.table_rows_before = _count(conn, tenant_id)
    res.groups = self_filed_groups(conn, tenant_id)
    res.filings_targeted = len(res.groups)
    for g in res.groups:
        if apply:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM fact_activist_stake "
                    "WHERE tenant_id = %s AND security_id = %s AND accession = %s",
                    (tenant_id, g["security_id"], g["accession"]),
                )
                res.rows_deleted += cur.rowcount
        else:
            # dry-run: count every version of the filing it WOULD delete (no write)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM fact_activist_stake "
                    "WHERE tenant_id = %s AND security_id = %s AND accession = %s",
                    (tenant_id, g["security_id"], g["accession"]),
                )
                res.rows_deleted += cur.fetchone()["n"]
    if apply:
        conn.commit()
    res.table_rows_after = _count(conn, tenant_id)
    return res


def _print(res: RepairResult) -> None:
    mode = "APPLY (rows deleted)" if res.applied else "DRY-RUN (nothing deleted)"
    print(f"\n=== repair activist self-filed mis-attribution — {mode} ===")
    print(f"  table rows before  : {res.table_rows_before}")
    print(f"  table rows after   : {res.table_rows_after}")
    print(f"  self-filed filings : {res.filings_targeted}")
    verb = "deleted" if res.applied else "WOULD delete"
    print(f"  rows {verb:12}: {res.rows_deleted}")
    if res.groups:
        print("  self-filed filings (ticker · accession · filer == subject · form · pct):")
        for g in res.groups:
            tk = g["subject_ticker"] or "?"
            print(
                f"    {tk:8} {g['accession']}  filer={g['filer_cik']} (== subject {g['subject_cik']})"
                f"  {g['form']}  pct={g['pct_owned']}"
            )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Delete self-filed (filer==subject) activist-stake rows the pre-fix ingest fanned "
        "onto the wrong subject (dry-run by default)."
    )
    p.add_argument("--apply", action="store_true", help="execute the deletes (default: dry-run)")
    p.add_argument(
        "--database-url",
        default=None,
        help="explicit target DSN (default: $DATABASE_URL, else the dev default)",
    )
    p.add_argument(
        "--tenant", default=str(DEFAULT_TENANT_ID), help="tenant id to scope the repair to"
    )
    args = p.parse_args(argv)

    url = args.database_url or database_url()
    print(f"target DB : {_redact(url)}")
    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            print(f"connected : current_database() = {cur.fetchone()['db']}")
        res = run_repair(conn, tenant_id=args.tenant, apply=args.apply)
        _print(res)
        if not args.apply:
            conn.rollback()  # a dry-run writes nothing
    finally:
        conn.close()


if __name__ == "__main__":
    main()
