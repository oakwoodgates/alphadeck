"""``pipeline.rederive_amendment_matches`` — the R8 re-derivation (2026-10-21): re-record, append-only, the LEGACY
system decisions that replace a WHOLE original where R8-B's row match replaces only the rows it accounts for.

WHAT IT REPAIRS. R8-B (#419) applies a resolvable Form 4/A by replacing only the original rows it ACCOUNTS for
(``domain.insider_amendments.match_rows``), stored on the decision as ``replaced_rows`` (migration 0053); NULL means
"the WHOLE original". The first live night (2026-10-20) wrote its system applications BEFORE the row match existed,
so every one carries NULL — and the resolver reads NULL as whole: an original row the amendment never mentions
leaves the read, and (since #420 composes every applied amendment of one original in SEC acceptance order) a later
NULL also supersedes every earlier amendment's rows. Rows silently vanish from the read — a recall issue (#9).

WHICH. A standing SYSTEM ``apply`` with ``replaced_rows`` NULL on a MATCHED disposition (partial / consolidation /
addition / restatement / field change — ``is_legacy_whole``; never a 1:1 correction, a void, a duplicates set or a
keep). Its row match is computed exactly as R8-B computes it at ingest (``match_for`` against the FULL stored
originals, with the filer's own words), and it is re-derived only when that match CHANGES the read of an original
it amends (``rederive_scope``: the whole-history 4/A resolution with the legacy decisions re-derived vs as stored)
— a legacy decision whose match reads the same is left exactly as it is. An UNRESOLVED match (R8-B would owe it to
the operator) is reported, never moved; an amendment the operator ever decided is never touched.

HOW — append-only (``repositories.insider_amendment_repo.rederive``): per decision a SYSTEM ``revoke`` (basis
``system:rederive``) of the legacy decision and the system's ``apply`` carrying the row match (``via`` =
``rederive``, the basis by its signal effect under the match), both stamped at the re-derivation (``recorded_at =
now``), so a read pinned before it sees exactly what the system held then (#1).

SAFETY (the ``relink_amendments`` shape):
- **Dry-run is the DEFAULT** — a READ ONLY connection (it physically cannot write), no lock, prints the plan.
- ``--apply`` holds the call-of-record advisory lock (``pipeline/run_lock.py`` — exit 75 if a nightly pass or a
  backfill holds it), appends everything in ONE transaction and COUNTS THE TABLE before commit (the decision row
  count must grow by exactly the planned appends, else it rolls back).
- **Idempotent** — a re-derived decision carries its row match, so a second run plans nothing.
- Run ``--apply`` on prod only after a labeled backup, from the deployed image.

    python -m pipeline.rederive_amendment_matches                 # dry run (default): the plan, nothing written
    python -m pipeline.rederive_amendment_matches --apply         # append the re-derivations
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import connect
from domain.insider_amendments import AMENDS_ACCESSION
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock
from repositories import insider_amendment_repo as repo


def _count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment_decision")
        return int(cur.fetchone()["n"])


def _num(v: Any) -> str:
    if v is None:
        return "-"
    f = float(v)
    return f"{f:,.0f}" if f == int(f) else f"{f:,.4f}".rstrip("0")


def _row(r: dict[str, Any]) -> str:
    via = f" (4/A {r['accession']})" if r.get(AMENDS_ACCESSION) else ""
    return f"{r.get('txn_code')} {r.get('valid_from')} {_num(r.get('shares'))} sh{via}"


def _line(it: repo.RederiveItem) -> str:
    n = len(it.replaced_rows) + len(it.kept_rows)
    return (
        f"  {it.ticker or it.security_id} {it.accession} [{it.disposition}] -> "
        f"{','.join(it.original_accessions)}: replaces {len(it.replaced_rows)} of {n} original row(s), "
        f"{len(it.kept_rows)} stand, {it.added} added · reading {it.reading} · "
        f"{it.stale['basis']} NULL -> {it.basis}"
    )


def _original_of(r: dict[str, Any]) -> str:
    return str(r.get(AMENDS_ACCESSION) or r.get("accession"))


def report(plan: repo.RederivePlan) -> None:
    by_basis = Counter(str(i.basis) for i in plan.items)
    by_reading = Counter(i.reading for i in plan.items)
    print(
        f"considered {plan.considered} legacy whole-original system decision(s) · re-derive "
        f"{len(plan.items)} ({', '.join(f'{b} {n}' for b, n in sorted(by_basis.items())) or '-'}; reading "
        f"{', '.join(f'{r} {n}' for r, n in sorted(by_reading.items())) or '-'}) · unchanged read — left as is "
        f"{len(plan.unchanged)} · unresolved match — reported, not moved {len(plan.unresolved)} · operator's — "
        f"untouched {len(plan.operator_held)}"
    )
    for it in plan.items:
        print(_line(it))
    for title, rows in (
        ("unresolved match — R8-B owes these to the operator (not moved):", plan.unresolved),
        ("operator's — untouched:", plan.operator_held),
    ):
        if rows:
            print(title)
            for it in rows:
                print(_line(it))
    for title, rows in (
        ("rows the read GAINS (by original):", plan.gained),
        ("rows the read LOSES (by original):", plan.lost),
    ):
        groups: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
        for ticker, _sid, r in rows:
            groups.setdefault((ticker, _original_of(r)), []).append(r)
        composed = sum(1 for rs in groups.values() if any(r.get(AMENDS_ACCESSION) for r in rs))
        codes = Counter(str(r.get("txn_code")) for _, _, r in rows)
        print(
            f"{title} {len(rows)} row(s) on {len(groups)} original(s) "
            f"({composed} via an amendment's rows — the composition) · by code: "
            + (", ".join(f"{c} {n}" for c, n in sorted(codes.items())) or "-")
        )
        for (ticker, orig), rs in sorted(groups.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
            print(f"  {ticker} {orig}: " + "; ".join(_row(r) for r in rs))


def _jsonable(x: Any) -> Any:
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, (UUID, Decimal)):
        return str(x)
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def plan_json(plan: repo.RederivePlan) -> dict[str, Any]:
    def item(it: repo.RederiveItem) -> dict[str, Any]:
        return {
            "security_id": it.security_id,
            "ticker": it.ticker,
            "accession": it.accession,
            "disposition": it.disposition,
            "original_accessions": it.original_accessions,
            "stale": {k: it.stale.get(k) for k in ("id", "action", "basis", "recorded_at")},
            "replaced_rows": it.replaced_rows,
            "kept_rows": it.kept_rows,
            "added": it.added,
            "reading": it.reading,
            "passage": it.passage,
            "basis": it.basis,
            "signal_effect": it.signal_effect,
        }

    def row(t: tuple[str | None, UUID, dict[str, Any]]) -> dict[str, Any]:
        ticker, sid, r = t
        keep = (
            "accession",
            "txn_seq",
            "insider_name",
            "txn_code",
            "valid_from",
            "shares",
            "price",
            AMENDS_ACCESSION,
        )
        return {"ticker": ticker, "security_id": sid, **{k: r.get(k) for k in keep}}

    return _jsonable(
        {
            "considered": plan.considered,
            "items": [item(i) for i in plan.items],
            "unchanged": [item(i) for i in plan.unchanged],
            "unresolved": [item(i) for i in plan.unresolved],
            "operator_held": [item(i) for i in plan.operator_held],
            "gained": [row(t) for t in plan.gained],
            "lost": [row(t) for t in plan.lost],
        }
    )


def run(*, apply: bool, report_json: Path | None) -> repo.RederiveResult | None:
    lock = acquire_pass_lock(holder="rederive_amendment_matches") if apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not apply  # a dry run PHYSICALLY cannot write
            before = _count(conn)
            plan = repo.rederive_plan(conn)
            report(plan)
            if report_json is not None:
                report_json.write_text(json.dumps(plan_json(plan), indent=2), encoding="utf-8")
                print(f"report: {report_json}")
            if not apply:
                conn.rollback()
                print("DRY-RUN — nothing written (pass --apply to append the re-derivations)")
                return None
            result = repo.rederive(conn, plan)
            after = _count(conn)
            if after - before != result.revokes + result.decisions:
                conn.rollback()
                raise SystemExit(
                    f"ABORTED: count mismatch (insider_amendment_decision {before}->{after}, planned "
                    f"+{result.revokes + result.decisions}) — ROLLED BACK, nothing written"
                )
            conn.commit()
            print(
                f"APPLIED: {result.revokes} system revoke(s), {result.decisions} re-derived system "
                f"decision(s) · skipped (moved since the plan) {len(result.skipped)} · "
                f"insider_amendment_decision {before} -> {after}"
            )
            return result
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="R8 re-derivation: re-record the legacy system Form 4/A decisions that replace a WHOLE "
        "original (written before the row match) with the row match, where it changes the read. Append-only; "
        "dry-run by default."
    )
    p.add_argument("--apply", action="store_true", help="append (default: dry-run, read-only)")
    p.add_argument("--report-json", default=None, help="write the plan as JSON to this path")
    args = p.parse_args(argv)
    try:
        run(apply=args.apply, report_json=Path(args.report_json) if args.report_json else None)
    except PassAlreadyRunning as exc:
        print(
            f"rederive_amendment_matches: ALREADY RUNNING — {exc}. Nothing was written "
            f"(exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None


if __name__ == "__main__":
    main()
