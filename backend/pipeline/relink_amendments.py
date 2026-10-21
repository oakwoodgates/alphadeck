"""``pipeline.relink_amendments`` — the R8 linker fix (2026-10-21): RE-LINK the stored Form 4/As the old cascade
linked to the wrong original, append-only.

WHAT IT REPAIRS. The 4/A linker (``domain.insider_amendments._cascade``) trusted ``dateOfOriginalSubmission``
alone in step 1. IEHC 0001174947-26-000336 states its OWN filing date there (2026-03-10; its footnote names the
real original, "filed on July 1, 2025"), so step 1 linked it to the owner's only Form 4 filed that day,
``-000338`` (two March 2026 sales) — a filing SEC accepted 49 seconds AFTER the 4/A. The fixed cascade refuses
any candidate made public after the amendment (``made_public_after``) and settles an ambiguous candidate set
when exactly one candidate holds a row the amendment accounts for by exact shape (``row_tie_break`` — SKYT).
New 4/As link under the fixed cascade at ingest; this CLI moves the STORED links those two rules change
(``relink_rule``) and nothing else.

HOW — append-only, the existing mechanics (``repositories.insider_amendment_repo.relink``): per amendment a NEW
``insider_amendment_link`` version, a SYSTEM ``revoke`` (basis ``system:relink``) of the standing system decision
that was computed against the wrong original, and the system's decision against the new original when it
resolves one — every row stamped at the re-link (``recorded_at = now``), so a read pinned before it sees exactly
what the system held then (#1). An amendment the operator ever decided or linked is NEVER touched (listed as
"operator's — untouched"). A stored link the fixed cascade reads differently for any OTHER reason (rows or
filing dates stored since) is reported as drift and never moved here.

THE FILING DATES. Step 1 matches ``dateOfOriginalSubmission`` against each stored Form 4's EDGAR filing date,
which only the company's submissions index holds — read from the EDGAR cache, CACHE-ONLY (``allow_live=False``:
no SEC request, ever): the very document the nightly Form 4 / 4/A legs fetched. A security with no cached index
is listed and not judged.

SAFETY (the ``repair_benchmark_bars`` / ``annotate_phantom_arms`` shape):
- **Dry-run is the DEFAULT** — a READ ONLY connection (it physically cannot write), no lock, prints the plan.
- ``--apply`` holds the call-of-record advisory lock (``pipeline/run_lock.py`` — exit 75 if a nightly pass or a
  backfill holds it), appends everything in ONE transaction and COUNTS THE TABLES before commit (link and
  decision row counts must grow by exactly the planned appends, else it rolls back).
- **Idempotent** — once applied, the stored link IS the fixed cascade's, so a second run plans nothing.
- Run ``--apply`` on prod only after a labeled backup, from the deployed image.

    python -m pipeline.relink_amendments                  # dry run (default): the plan, nothing written
    python -m pipeline.relink_amendments --apply          # append the re-links
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import connect
from domain.insider_amendments import RELINK_ORDER, RELINK_TIE_BREAK
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.edgar.form4a import accepted_times, filed_dates
from ingest.edgar.submissions import fetch_submissions, filings_of
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_repair_lock
from repositories import insider_amendment_repo as repo


def cached_index(client: EdgarClient) -> Callable[[UUID, str | None], repo.FilingIndex | None]:
    """``(security_id, cik) -> ({accession: filed}, {accession: accepted})`` from the CACHED submissions index —
    the ingest's own source (``filings_of(subs, "4")``); ``None`` when the security has no CIK or no cached
    index."""

    def get(_sid: UUID, cik: str | None) -> repo.FilingIndex | None:
        if not cik:
            return None
        try:
            subs = fetch_submissions(client, cik)
        except (CacheMiss, OSError, ValueError):
            return None
        originals = filings_of(subs, "4")
        return filed_dates(originals), accepted_times(originals)

    return get


def _count(conn: psycopg.Connection, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — two fixed table names
        return int(cur.fetchone()["n"])


def _line(it: repo.RelinkItem) -> str:
    old, new = it.old, it.new
    was = ",".join(old["original_accessions"] or []) or ",".join(old["candidate_accessions"] or [])
    now = ",".join(new.original_accessions) or "-"
    revoke = f" · revokes {it.revoke['action']}/{it.revoke['basis']}" if it.revoke else ""
    decide = f" · decides {new.system_action}/{new.basis}" if new.basis else ""
    return (
        f"  {it.ticker or it.security_id} {it.accession} [{it.rule}] {old['disposition']} -> {was or '-'}  "
        f"==>  {new.disposition} -> {now} ({new.method}){revoke}{decide}"
    )


def report(plan: repo.RelinkPlan) -> None:
    print(
        f"considered {plan.considered} stored link(s) · re-link {len(plan.items)} "
        f"({sum(1 for i in plan.items if i.rule == RELINK_ORDER)} made public after the amendment, "
        f"{sum(1 for i in plan.items if i.rule == RELINK_TIE_BREAK)} row tie-break) · operator's — untouched "
        f"{len(plan.operator_held)} · drift (reported, not moved) {len(plan.drift)} · securities with no cached "
        f"index {len(plan.no_index)}"
    )
    for it in plan.items:
        print(_line(it))
    if plan.operator_held:
        print(
            "operator's — untouched (the rule would move these; the operator decided or linked them):"
        )
        for it in plan.operator_held:
            print(_line(it))
    if plan.drift:
        print("drift — the fixed cascade reads these differently for ANOTHER reason (not moved):")
        for ticker, acc, was, now in plan.drift:
            print(f"  {ticker} {acc}: stored {was} vs fresh {now}")
    if plan.no_index:
        print("not judged — no cached submissions index:")
        for ticker, sid, n in plan.no_index:
            print(f"  {ticker or sid}: {n} amendment(s)")
    by = Counter((i.old["disposition"], i.new.disposition) for i in plan.items)
    if by:
        print(
            "disposition moves: " + ", ".join(f"{a}->{b} {n}" for (a, b), n in sorted(by.items()))
        )


def _jsonable(x: Any) -> Any:
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, UUID):
        return str(x)
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def plan_json(plan: repo.RelinkPlan) -> dict[str, Any]:
    def item(it: repo.RelinkItem) -> dict[str, Any]:
        return {
            "security_id": str(it.security_id),
            "ticker": it.ticker,
            "accession": it.accession,
            "rule": it.rule,
            "old": {
                k: it.old.get(k)
                for k in (
                    "id",
                    "method",
                    "disposition",
                    "original_accessions",
                    "candidate_accessions",
                )
            },
            "new": {
                "method": it.new.method,
                "disposition": it.new.disposition,
                "original_accessions": list(it.new.original_accessions),
                "candidate_accessions": list(it.new.candidate_accessions),
                "status": it.new.status,
                "basis": it.new.basis,
                "system_action": it.new.system_action,
                "replaced_rows": (
                    list(it.new.replaced_rows) if it.new.replaced_rows is not None else None
                ),
                "why": it.new.why,
            },
            "revokes": (
                {k: it.revoke.get(k) for k in ("id", "action", "basis")} if it.revoke else None
            ),
        }

    return _jsonable(
        {
            "considered": plan.considered,
            "items": [item(i) for i in plan.items],
            "operator_held": [item(i) for i in plan.operator_held],
            "drift": [list(d) for d in plan.drift],
            "no_index": [list(d) for d in plan.no_index],
        }
    )


def run(
    *, apply: bool, cache_dir: Path | None, report_json: Path | None
) -> repo.RelinkResult | None:
    client = EdgarClient(cache_dir=cache_dir, allow_live=False)  # cache-only: never an SEC request
    lock = acquire_repair_lock(holder="relink_amendments") if apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not apply  # a dry run PHYSICALLY cannot write
            links_before = _count(conn, "insider_amendment_link")
            decisions_before = _count(conn, "insider_amendment_decision")
            plan = repo.relink_plan(conn, index=cached_index(client))
            report(plan)
            if report_json is not None:
                report_json.write_text(json.dumps(plan_json(plan), indent=2), encoding="utf-8")
                print(f"report: {report_json}")
            if not apply:
                conn.rollback()
                print("DRY-RUN — nothing written (pass --apply to append the re-links)")
                return None
            result = repo.relink(conn, plan)
            links_after = _count(conn, "insider_amendment_link")
            decisions_after = _count(conn, "insider_amendment_decision")
            if (
                links_after - links_before != result.links
                or decisions_after - decisions_before != result.revokes + result.decisions
            ):
                conn.rollback()
                raise SystemExit(
                    f"ABORTED: count mismatch (links {links_before}->{links_after}, planned +{result.links}; "
                    f"decisions {decisions_before}->{decisions_after}, planned "
                    f"+{result.revokes + result.decisions}) — ROLLED BACK, nothing written"
                )
            conn.commit()
            print(
                f"APPLIED: {result.links} link version(s), {result.revokes} system revoke(s), "
                f"{result.decisions} system decision(s) · skipped (moved since the plan) "
                f"{len(result.skipped)} · insider_amendment_link {links_before} -> {links_after}, "
                f"insider_amendment_decision {decisions_before} -> {decisions_after}"
            )
            return result
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="R8 linker fix: re-link the stored Form 4/As the old cascade linked to the wrong original "
        "(an original made public after its amendment; an ambiguous set one row settles). Append-only; "
        "dry-run by default."
    )
    p.add_argument("--apply", action="store_true", help="append (default: dry-run, read-only)")
    p.add_argument(
        "--cache-dir",
        default=None,
        help="the EDGAR cache to read the submissions index from (default: the app's own)",
    )
    p.add_argument("--report-json", default=None, help="write the plan as JSON to this path")
    args = p.parse_args(argv)
    try:
        run(
            apply=args.apply,
            cache_dir=Path(args.cache_dir) if args.cache_dir else None,
            report_json=Path(args.report_json) if args.report_json else None,
        )
    except PassAlreadyRunning as exc:
        print(
            f"relink_amendments: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None


if __name__ == "__main__":
    main()
