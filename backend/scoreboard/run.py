from __future__ import annotations

import argparse
from datetime import date
from uuid import UUID

import psycopg

from db.session import connect
from scoreboard.record import scoreboard_records
from scoreboard.schema import ScoreboardResult, ScoredEpisode

# The SB1 checkpoint CLI — the record, scored, readable from the terminal (the ``replay.run``
# precedent, minus the Parquet mirror: this reads the live SoR and writes NOTHING).
#
#   python -m scoreboard.run --asof 2026-07-11            # the human-readable ledger
#   python -m scoreboard.run --asof 2026-07-11 --json     # the full analytical dump


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1%}"


def _tickers(conn: psycopg.Connection, sids: set[UUID]) -> dict[UUID, str]:
    """sid -> ticker for display (the security master is canonical; a name later removed from its
    basket still resolves). Display-only — the analytical records stay pure sids."""
    if not sids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, ticker FROM security_master WHERE id = ANY(%s)", (list(sids),))
        return {r["id"]: r["ticker"] for r in cur.fetchall() if r["ticker"]}


def _episode_lines(e: ScoredEpisode, ticker: str) -> list[str]:
    ep, out = e.episode, e.outcome
    head = (
        f"  {ticker:<6} {e.status.upper():<6} armed {ep.arm_date}"
        + ("*" if e.censored_start else "")
        + (f" -> dearmed {ep.dearm_date} ({ep.close_reason})" if ep.dearm_date else "")
        + (f"  exit-by {ep.exit_by}" if ep.exit_by else "")
        + ("  MATURED" if e.matured else "")
    )
    ret = "running" if e.status == "open" or not e.matured else "realized"
    body = (
        f"         {ret} {_pct(out.forward_return)}"
        + (
            f" (entry {out.entry_close} -> {out.exit_close} @ {out.exit_date})"
            if out.entry_close is not None and out.exit_close is not None
            else ""
        )
        + (" [truncated]" if out.truncated else "")
        + (" [insufficient prices]" if out.insufficient_prices else "")
        + (f"  grade {ep.entry_grade.value}" if ep.entry_grade else "")
        + (f"  conf {ep.confidence:.2f}" if ep.confidence is not None else "")
    )
    lines = [head, body]
    if e.censored_start:
        lines.append("         * censored: the record began mid-arm (true arm date unknowable)")
    for t in e.triggers_at_arm:
        lines.append(f"         why: {t.label} [{t.kind.value if t.kind else '?'}]")
    return lines


def render(result: ScoreboardResult, tickers: dict[UUID, str]) -> str:
    lines = [
        f"SCOREBOARD as-of {result.asof} — the record, scored (never a recompute)",
        f"theses {result.n_theses} (with record {result.n_with_record}) · "
        f"episodes {result.n_episodes} (open {result.n_open}, matured {result.n_matured}, "
        f"censored {result.n_censored})",
    ]
    for t in result.theses:
        lines.append("")
        head = f"{t.name}"
        if t.archived:
            head += "  [ARCHIVED]"
        if t.current_state:
            head += f"  [{t.current_state} / {t.current_verdict}]"
        if t.first_call_asof:
            head += f"  record {t.first_call_asof} -> {t.last_call_asof}"
        lines.append(head)
        if t.error:
            lines.append(f"  RECORD ERROR: {t.error}")
        elif not t.first_call_asof:
            lines.append("  (no call-of-record yet)")
        elif not t.episodes:
            note = f" — warming since {t.warming_since}" if t.warming_since else ""
            lines.append(f"  (no arm episodes{note})")
        for e in t.episodes:
            sid = e.episode.security_id
            lines.extend(_episode_lines(e, tickers.get(sid, str(sid)[:8])))
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        description="The live Scoreboard (SCORE) — score the call-of-record as-of a date. Read-only."
    )
    p.add_argument("--asof", default=date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    p.add_argument("--json", action="store_true", help="print the full analytical dump")
    p.add_argument(
        "--exclude-archived",
        action="store_true",
        help="drop archived theses (the record includes them by default)",
    )
    args = p.parse_args()
    conn = connect()
    try:
        result, _ = scoreboard_records(
            conn, date.fromisoformat(args.asof), include_archived=not args.exclude_archived
        )
        if args.json:
            print(result.model_dump_json(indent=2))
        else:
            sids = {e.episode.security_id for t in result.theses for e in t.episodes}
            print(render(result, _tickers(conn, sids)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
