"""``pipeline.annotate_no_session_rows`` — F10: find the call-of-record rows dated on a day with NO market
session, and annotate them — REVERSIBLY — so the Scoreboard can set them aside.

THE PROBLEM. The call-of-record is "what the system said on the night of ``asof``". Before the September fix,
a daytime HAND run recorded the card under the date it ran, so some rows are dated a Saturday or Sunday
(MEASURED on dev: 49 non-reconstructed rows, 2026-08-16 .. 2026-09-05) and some a market holiday (11 rows,
Labor Day 2026-09-07). A night with no session has no price bar and no "next session" to score against, yet
the Scoreboard has no weekday filter, so these rows sit in the scored record (7 of the 15 zero-session arms
are on them). They stay in the immutable log — they ARE what the system said — but they should not be scored.

THE RULE. A row is a **no-session night** when ``not domain.trading_days.is_scheduled_day(row.asof)`` — the
ONE session calendar the call path and the cron schedule already share. This CLI keeps NO calendar of its own:
today the predicate is Mon-Fri, so it finds the weekend rows; when the holiday-aware calendar lands it finds
the holiday rows with no change here. Only HONEST rows are looked at (``NOT reconstructed``): a reconstructed
row is already set aside by its own flag, and a second judgment on it would add nothing.

THE ANNOTATION (the D3b shape — ``pipeline.annotate_phantom_arms``, no migration). ``calls`` is immutable (the
``no_update`` trigger). This CLI appends ``exclude`` rows to ``call_annotation`` (migration 0045) with
``reason = no_session_day`` and ``rule = no-session-night@<image sha>``. The Scoreboard's ONE
``include_annotated`` reading covers EVERY active ``exclude`` whatever its rule, so these nights are set aside
by default and scored again under the include reading (or the setting off, or a revoke) — byte-for-byte the
pre-annotation record. The annotation carries no ``affected_security_ids``: the whole NIGHT is the judgment,
so the drawer caption shows on every member's episode that walks across it (``scoreboard/record.py``).

SAFETY (the ``annotate_phantom_arms`` shape):
- **Dry-run is the DEFAULT**: its connection is READ ONLY, so it physically cannot write; it prints every row
  (thesis, as-of, weekday, recorded_at, run_kind, state/verdict). ``--report-json PATH`` writes the evidence.
  A dry run takes no lock.
- ``--apply`` appends the annotations in ONE transaction after checking the inserted count, and writes a
  provenance JSON under ``data/annotations/``. It holds the call-of-record advisory lock
  (``pipeline/run_lock.py``) for the whole run — exit 75 if a daily pass or backfill holds it.
- **Idempotent**: a (call, action) this rule FAMILY already judged — active or revoked — is never judged
  again, so a re-run appends nothing and a revoke the operator made sticks across re-runs and image versions.
- It annotates ``calls`` rows only (it rewrites no FACT history), so — like ``annotate_phantom_arms`` — it is
  not journaled in ``data_repair``.
- Run ``--apply`` against prod only after a backup, from the deployed image (``rule`` records its sha).

    python -m pipeline.annotate_no_session_rows                                # dry-run, every thesis
    python -m pipeline.annotate_no_session_rows --thesis <uuid> --report-json /tmp/f10.json
    python -m pipeline.annotate_no_session_rows --apply                        # append the annotations
    python -m pipeline.annotate_no_session_rows --revoke <annotation-id> --reason "..." [--apply]
    python -m pipeline.annotate_no_session_rows --revoke-all --reason "..." [--apply]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import connect
from domain.settings import get_settings
from domain.trading_days import is_scheduled_day
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock
from repositories import call_annotations_repo

_log = logging.getLogger("alphadeck.annotate_no_session_rows")

RULE_FAMILY = call_annotations_repo.RULE_FAMILY_NO_SESSION  # named once, in the repo
REASON = "no_session_day"
REVOKE_RULE_FAMILY = "operator-revoke"

# Provenance artifacts live beside D3b's (the repo's gitignored data/ == the container's /data); the file name
# is prefixed so the two annotators' artifacts are told apart at a glance.
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "annotations"


def rule_identity() -> str:
    """``no-session-night@<image sha>`` — the code identity the annotation records. ``unknown`` when the
    image carries no sha (never fabricated — the 0044 rule)."""
    return f"{RULE_FAMILY}@{get_settings().image_sha or 'unknown'}"


# --- the rows -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowResult:
    """One honest ``calls`` row dated a day with no market session."""

    call_id: UUID
    seq: int
    thesis_id: UUID
    thesis_name: str
    asof: date
    recorded_at: datetime
    run_kind: str | None
    code_sha: str | None
    state: str
    verdict: str

    @property
    def weekday(self) -> str:
        return self.asof.strftime("%A")

    def detail(self, *, rule: str) -> dict[str, Any]:
        """The annotation's evidence (``call_annotation.detail``) — also the provenance JSON's row. No
        ``affected_security_ids``: the judgment is about the NIGHT, so no member's caption is filtered out.
        """
        return {
            "rule": rule,
            "classification": "exclude",
            "why": f"{self.asof} is a {self.weekday} with no market session "
            "(domain.trading_days.is_scheduled_day)",
            "summary": "dated a day with no market session",
            "weekday": self.weekday,
            "call_seq": self.seq,
            "recorded_at": self.recorded_at.isoformat(),
            "run_kind": self.run_kind,
            "code_sha": self.code_sha,
            "recorded": {"state": self.state, "verdict": self.verdict},
        }


def find_rows(
    conn: psycopg.Connection, *, thesis_id: UUID | None = None, asof_from: date | None = None
) -> list[RowResult]:
    """Every honest (``NOT reconstructed``) row whose as-of is not a scheduled session day, oldest first by
    thesis. Read-only. The calendar is ``is_scheduled_day`` ALONE: the distinct as-of dates are read, the
    predicate picks the no-session ones, and only those dates' rows are fetched."""
    where = ["NOT c.reconstructed"]
    params: dict[str, Any] = {}
    if thesis_id is not None:
        where.append("c.thesis_id = %(tid)s")
        params["tid"] = thesis_id
    if asof_from is not None:
        where.append("c.asof >= %(from)s")
        params["from"] = asof_from
    clause = " AND ".join(where)
    with conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT c.asof FROM calls c WHERE {clause}", params)  # noqa: S608
        no_session = sorted(r["asof"] for r in cur.fetchall() if not is_scheduled_day(r["asof"]))
        if not no_session:
            conn.rollback()
            return []
        cur.execute(
            "SELECT c.id, c.seq, c.thesis_id, t.name AS thesis_name, c.asof, c.recorded_at, "
            "c.run_kind, c.code_sha, c.state, c.verdict "
            "FROM calls c JOIN thesis t ON t.id = c.thesis_id "
            f"WHERE {clause} AND c.asof = ANY(%(dates)s) "  # noqa: S608 — clause is built above
            "ORDER BY t.name, c.asof, c.seq",
            {**params, "dates": no_session},
        )
        rows = [RowResult(call_id=r.pop("id"), **r) for r in cur.fetchall()]
    conn.rollback()  # a read-only transaction: end it, hold no snapshot
    return rows


# --- apply / revoke -------------------------------------------------------------------------------------


@dataclass
class ApplyOutcome:
    appended: list[tuple[UUID, UUID]]  # (annotation id, call id)
    skipped_existing: list[UUID]  # call ids the rule family already judged


def apply_annotations(
    conn: psycopg.Connection, results: list[RowResult], *, rule: str
) -> ApplyOutcome:
    """Append one ``exclude`` per row in ONE transaction (the caller commits) — skipping any (call,
    ``exclude``) this rule FAMILY already judged, active or revoked (a revoke sticks)."""
    family = call_annotations_repo.rule_family(rule)
    judged = {
        (a.call_id, a.action)
        for a in call_annotations_repo.all_for_rule_family(conn, family)
        if a.action != "revoke"
    }
    out = ApplyOutcome(appended=[], skipped_existing=[])
    for r in results:
        if (r.call_id, "exclude") in judged:
            out.skipped_existing.append(r.call_id)
            continue
        aid = call_annotations_repo.append_annotation(
            conn,
            r.call_id,
            action="exclude",
            reason=REASON,
            rule=rule,
            detail=r.detail(rule=rule),
        )
        out.appended.append((aid, r.call_id))
    return out


def _annotation_table_exists(conn: psycopg.Connection) -> bool:
    """Is migration 0045 applied here? Checked BEFORE a real run — never discover a missing table at the
    write."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('call_annotation') IS NOT NULL AS ok")
        ok = cur.fetchone()["ok"]
    conn.rollback()
    return ok


def _count_ids(conn: psycopg.Connection, ids: list[UUID]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM call_annotation WHERE id = ANY(%s)", (ids,))
        return cur.fetchone()["n"]


# --- provenance + report --------------------------------------------------------------------------------


def build_payload(
    results: list[RowResult],
    *,
    rule: str,
    dry_run: bool,
    started_at: datetime,
    finished_at: datetime,
    outcome: ApplyOutcome | None,
) -> dict[str, Any]:
    """The provenance / report payload — PURE (no I/O)."""
    per: Counter[str] = Counter(r.thesis_name for r in results)
    return {
        "kind": "annotate_no_session_rows",
        "rule": rule,
        "dry_run": dry_run,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_s": round((finished_at - started_at).total_seconds(), 3),
        "summary": {
            "rows": len(results),
            "dates": len({r.asof for r in results}),
            "by_weekday": dict(Counter(r.weekday for r in results)),
        },
        "per_thesis": dict(sorted(per.items())),
        "appended": (
            [{"annotation_id": str(a), "call_id": str(c)} for a, c in outcome.appended]
            if outcome
            else []
        ),
        "skipped_existing": [str(c) for c in outcome.skipped_existing] if outcome else [],
        "rows": [
            {
                "call_id": str(r.call_id),
                "thesis_id": str(r.thesis_id),
                "thesis": r.thesis_name,
                "asof": r.asof.isoformat(),
                **r.detail(rule=rule),
            }
            for r in results
        ],
    }


def write_payload(payload: dict[str, Any], path: Path) -> Path | None:
    """Write the payload as JSON (fail-open: a provenance fault is logged, never a failed run)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract
        _log.exception("annotation provenance write failed (fail-open)")
        return None


def _line(r: RowResult) -> str:
    return (
        f"  {r.thesis_name} · asof {r.asof} ({r.weekday}) · seq {r.seq} · "
        f"recorded {r.recorded_at.isoformat()} · run {r.run_kind or 'NULL'} · "
        f"{r.state}/{r.verdict} · code {(r.code_sha or 'NULL')[:7]}"
    )


def report(results: list[RowResult]) -> None:
    """Print every row, then the per-date and per-thesis counts."""
    print(f"\n=== NO-SESSION ROWS (exclude): {len(results)} ===")
    for r in results:
        print(_line(r))
    by_date = Counter(r.asof for r in results)
    print("\n=== per as-of date ===")
    for d, n in sorted(by_date.items()):
        print(f"  {d} ({d.strftime('%A')}): {n} row(s)")
    print("\n=== per thesis ===")
    for name, n in sorted(Counter(r.thesis_name for r in results).items()):
        print(f"  {name[:48]:<48} {n:>5}")
    print(
        f"\nTOTAL: {len(results)} honest row(s) dated a day with no market session · {len(by_date)} date(s)"
    )


# --- the CLI --------------------------------------------------------------------------------------------


def _run_revoke(args: argparse.Namespace) -> None:
    if not args.reason:
        raise SystemExit("--revoke / --revoke-all need --reason (the why is part of the record)")
    rule = f"{REVOKE_RULE_FAMILY}@{get_settings().image_sha or 'unknown'}"
    lock = acquire_pass_lock(holder="annotate") if args.apply else None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply
            if args.revoke_all:
                ids = [
                    a.id for a in call_annotations_repo.active_for_rule_family(conn, RULE_FAMILY)
                ]
            else:
                ids = [UUID(x) for x in args.revoke]
            print(
                f"revoke: {len(ids)} annotation(s){'' if args.apply else ' · DRY-RUN (nothing written)'}"
            )
            for i in ids:
                print(f"  {i}")
            if not args.apply:
                conn.rollback()
                return
            new = [
                call_annotations_repo.revoke(conn, i, reason=args.reason, rule=rule) for i in ids
            ]
            if _count_ids(conn, new) != len(ids):
                conn.rollback()
                raise SystemExit("ABORTED: revoke count mismatch — ROLLED BACK, nothing written")
            conn.commit()
            print(f"SUMMARY: revoked={len(new)}")
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="F10: find every honest call-of-record row dated a day with no market session "
        "(not domain.trading_days.is_scheduled_day) and, with --apply, append REVERSIBLE `exclude` "
        "annotations (call_annotation) so the Scoreboard sets them aside. Dry-run by default."
    )
    p.add_argument(
        "--apply", action="store_true", help="write (default: dry-run, read-only, print only)"
    )
    p.add_argument("--thesis", default=None, help="one thesis id (uuid); default = every thesis")
    p.add_argument("--asof-from", default=None, help="only rows with asof >= YYYY-MM-DD")
    p.add_argument("--report-json", default=None, help="write the full evidence JSON to this path")
    p.add_argument("--revoke", nargs="+", default=None, help="revoke these annotation id(s)")
    p.add_argument(
        "--revoke-all", action="store_true", help=f"revoke every active {RULE_FAMILY} annotation"
    )
    p.add_argument("--reason", default=None, help="the why, required for a revoke")
    args = p.parse_args(argv)

    try:
        if args.revoke or args.revoke_all:
            _run_revoke(args)
            return
        thesis_id = UUID(args.thesis) if args.thesis else None
        asof_from = date.fromisoformat(args.asof_from) if args.asof_from else None
    except PassAlreadyRunning as exc:
        print(
            f"annotate: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    except ValueError as exc:
        p.error(str(exc))

    rule = rule_identity()
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    print(f"annotate_no_session_rows · rule={rule} · {mode}")
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    try:
        lock = acquire_pass_lock(holder="annotate") if args.apply else None
    except PassAlreadyRunning as exc:
        print(
            f"annotate: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    outcome: ApplyOutcome | None = None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            if args.apply and not _annotation_table_exists(conn):
                raise SystemExit(
                    "annotate: call_annotation does not exist — apply migration 0045 first "
                    "(python -m db.migrate); nothing was written"
                )
            results = find_rows(conn, thesis_id=thesis_id, asof_from=asof_from)
            report(results)
            if args.apply:
                outcome = apply_annotations(conn, results, rule=rule)
                ids = [a for a, _ in outcome.appended]
                if ids and _count_ids(conn, ids) != len(ids):
                    conn.rollback()
                    raise SystemExit(
                        "ABORTED: inserted-count mismatch — ROLLED BACK, nothing written"
                    )
                conn.commit()
                print(
                    f"\nAPPLIED: appended {len(outcome.appended)} exclude annotation(s) · "
                    f"{len(outcome.skipped_existing)} already judged by {RULE_FAMILY} (skipped)"
                )
        finally:
            conn.close()
    finally:
        if args.apply and lock is not None:
            lock.release()
    finished_at = datetime.now(timezone.utc)
    print(f"\nruntime: {time.monotonic() - t0:.1f}s for {len(results)} rows")
    payload = None
    if args.apply or args.report_json:
        payload = build_payload(
            results,
            rule=rule,
            dry_run=not args.apply,
            started_at=started_at,
            finished_at=finished_at,
            outcome=outcome,
        )
    if args.report_json:
        print(f"report: {write_payload(payload, Path(args.report_json))}")
    if args.apply:
        path = _DEFAULT_LOG_DIR / f"no-session-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        print(f"provenance: {write_payload(payload, path)}")


if __name__ == "__main__":
    main()
