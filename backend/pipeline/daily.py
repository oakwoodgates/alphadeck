"""The daily call-of-record cron (M2b) — the platform feeding itself.

Once a day, for every thesis (tenant intrinsic per-thesis):
  1. refresh its back-half facts — ``ingest_thesis`` (incremental + fail-visible; M2a);
  2. assemble TODAY's call WITHOUT writing — ``call_for_thesis(asof=today, known_at=now, record=False)``;
  3. append the call-of-record ONLY if it changed — ``calls_repo.record_if_changed``.

Discipline:
- **Per-thesis isolation** — each thesis is its own unit; one thesis's failure is captured + skipped,
  never fatal to the run (the cron must finish the rest).
- **Idempotent** — a same-day re-run on unchanged facts appends ZERO rows; a genuine change appends EXACTLY
  ONE new versioned row (``record_if_changed``). Safe to re-run / catch up a missed day.
- **No-lookahead** — ``asof=today``, ``known_at=now`` (never backdated).
- **Option B** — it ingests FACTS and appends the call-of-record (the write-only log); it builds NO
  read-serving signal/score cache. Calls still re-derive on read.
- **Scoreboard-ready, not coupled** — one clean versioned row per (thesis, day); same-day re-runs collapse
  via ``latest_for_thesis``' DISTINCT ON. No Scoreboard code here.

    python -m pipeline.daily                 # asof=today, live ingest
    python -m pipeline.daily --asof 2026-06-10 --no-live
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import UUID

import psycopg

from db.session import connect
from ingest.edgar.client import EdgarClient
from notify import Notifier, TransitionEvent, get_notifier
from pipeline.call_for_thesis import call_for_thesis
from pipeline.cron_run_log import write_cron_run_log
from pipeline.ingest_thesis import NameResult, ingest_thesis
from repositories import calls_repo, thesis_repo
from securities import master


@dataclass
class ThesisRunResult:
    """Per-thesis outcome. ``recorded``: True = a new call-of-record was appended, False = unchanged (no
    row), None = the call step failed (see ``error``). ``transition``: the state/verdict move vs the
    PRIOR as-of's call-of-record (None = no move — the overwhelmingly common, quiet case)."""

    thesis_id: UUID
    name: str
    ingested: list[NameResult] = field(default_factory=list)
    recorded: bool | None = None
    transition: str | None = None
    error: str | None = None
    # EDGAR network pulls during this thesis's ingest — the FREEZE DETECTOR. A frozen index and a healthy
    # nothing-filed night produce identical fact tallies (0 appended); this is the one number that differs.
    # 0 across the whole run = the cache never refreshed = the R1 freeze, visible instead of hiding behind
    # a plausible "quiet day". Recorded into the cron run log (R3); R4 pages on it.
    edgar_fetches: int = 0
    # R2a — why the call-of-record was WITHHELD (not recorded), or None if it was recorded/attempted. A run
    # that didn't meaningfully refresh has no business writing the log of record: "no-live" (a cache-only dev
    # run, Source A) or "total ingest failure" (the ingest raised, or EVERY name errored, Source C). A healthy
    # OR partial run still records (the partial one marked via the calls ingest_fresh column, R2b).
    withheld_reason: str | None = None


def run_daily(
    conn: psycopg.Connection,
    *,
    asof: date | None = None,
    known_at: datetime | None = None,
    allow_live: bool = True,
    force_refresh: bool = True,
    user_agent: str | None = None,
    notifier: Notifier | None = None,
) -> list[ThesisRunResult]:
    """Run the daily pass over every thesis. ``asof`` defaults to today, ``known_at`` to now (a live read).
    Returns one ``ThesisRunResult`` per thesis. Never raises for a single thesis — failures are captured.

    ``force_refresh`` defaults to **True**: the daily path is recurring, so it re-pulls fresh bars (bypassing
    a stale cache hit) — otherwise the cron would re-ingest the same frozen cache every day and never see a
    new bar. It threads to the price source (``eod_loader.fetch_eod``).
    """
    asof = asof or date.today()
    notifier = notifier or get_notifier()
    # The canonical-primary health guard: a master with multi-row CIKs but ZERO is_primary flags resolves
    # every multi-sibling CIK to an ARBITRARY row (warrant / preferred / OTC foreign ordinary) — and nothing
    # errors, so the state is invisible unless something says it. The cron is the daily surface; the line
    # prints only in the broken state (loudness marks the exception) and names the one-command fix.
    for gap in master.primary_flag_gaps(conn):
        if gap["flagged_rows"] == 0:
            print(
                f"WARNING: tenant {gap['tenant_id']}: {gap['multi_row_ciks']} multi-row CIKs but ZERO "
                "is_primary flags — CIK->security resolution is picking ARBITRARY siblings; "
                "run `python -m pipeline.populate_master --live` to stamp the canonical primaries"
            )
    out: list[ThesisRunResult] = []
    for thesis in thesis_repo.list_all(conn):
        res = ThesisRunResult(thesis_id=thesis.id, name=thesis.name)
        # (1) refresh facts — ingest_thesis already isolates per-name; wrap defensively so even a thesis-level
        # failure (e.g. a malformed thesis) is captured, not fatal. A fact failure does NOT block the call.
        # a fresh client PER THESIS so its live_fetches count is that thesis's own (the freeze detector)
        edgar_client = EdgarClient(allow_live=allow_live, user_agent=user_agent)
        try:
            res.ingested = ingest_thesis(
                conn,
                thesis.id,
                allow_live=allow_live,
                force_refresh=force_refresh,
                user_agent=user_agent,
                edgar_client=edgar_client,
            )
        except Exception as e:  # noqa: BLE001 — one thesis's ingest never aborts the cron
            conn.rollback()
            res.error = f"ingest: {e}"
        # capture the count even on a thesis-level failure — a mid-ingest raise still made real network
        # pulls, and "0 fetches" must mean the freeze, not "we bailed before the counter was read"
        res.edgar_fetches = edgar_client.live_fetches
        # R2a — THE RECORDING GATE: a run that didn't meaningfully refresh must not write the log of record.
        # TWO conditions, closing two do-nothing shapes that a fact-count test can't (a --no-live run over a
        # warm cache is fast, clean, appends nothing, and does NOT error):
        #   - no-live (Source A) → allow_live False. A cache-only dev run has no business recording, period.
        #   - total ingest failure (Source C) → the ingest raised (res.error), OR names existed and EVERY one
        #     errored. NOT "appended 0" — a current thesis appends 0 on a HEALTHY run and MUST still record.
        # A partial failure (some errored, some clean) DOES record, marked via ingest_fresh (R2b).
        ingest_errors = sum(1 for x in res.ingested if x.error)
        if res.error:
            res.withheld_reason = "total ingest failure"  # Source C (the ingest raised)
        elif not allow_live:
            res.withheld_reason = "no-live"  # Source A
        elif res.ingested and ingest_errors == len(res.ingested):
            res.withheld_reason = "total ingest failure"  # Source C (every name errored)
        if res.withheld_reason is not None:
            # the missing `continue`: skip assemble/notify/record entirely — a call built on a failed or
            # cache-only ingest is exactly the record the freeze investigation found masquerading as real
            out.append(res)
            continue
        # (2)+(3) assemble today's call WITHOUT writing, then append only if it changed.
        try:
            card = call_for_thesis(conn, thesis.id, asof, known_at=known_at, record=False)
            # (4) TRANSITION DETECTION (the notify seam): compare state/verdict against the PRIOR
            # as-of's call-of-record — the material-change line (trigger churn / provenance noise
            # version the log via record_if_changed without being transitions; a state or verdict
            # MOVE is what an operator would want to be told about). First-ever call = no prior =
            # no event. Delivery is the adapter's concern (v1: a loud log line).
            prior = next(
                (c for c in calls_repo.latest_for_thesis(conn, thesis.id) if c.asof < asof), None
            )
            if prior is not None and (
                prior.state is not card.state or prior.verdict is not card.verdict
            ):
                evt = TransitionEvent(
                    thesis_id=thesis.id,
                    thesis_name=thesis.name,
                    asof=asof,
                    from_state=prior.state.value,
                    to_state=card.state.value,
                    from_verdict=prior.verdict.value,
                    to_verdict=card.verdict.value,
                )
                notifier.notify(evt)
                res.transition = evt.label
            # R2b — stamp the run's ingest health on the recorded row (PROVENANCE, off the card). We reach
            # here only on a healthy OR partial ingest; ingest_fresh False marks the partial one so the
            # Scoreboard can discount a call resting on names that failed to refresh.
            res.recorded = calls_repo.record_if_changed(
                conn,
                card,
                thesis.tenant_id,
                ingest_fresh=(ingest_errors == 0),
                ingest_errors=ingest_errors,
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — one thesis's call never aborts the cron
            conn.rollback()
            res.error = (f"{res.error}; " if res.error else "") + f"call: {e}"
        out.append(res)
    return out


def _report(results: list[ThesisRunResult]) -> int:
    """Print a per-thesis summary; return the number that errored (the process exit signal)."""
    appended = sum(1 for r in results if r.recorded)
    unchanged = sum(1 for r in results if r.recorded is False)
    withheld = [r for r in results if r.withheld_reason]
    errored = [r for r in results if r.error]
    for r in results:
        if r.error:
            mark = f"ERROR: {r.error}"
        elif r.withheld_reason:
            mark = f"WITHHELD ({r.withheld_reason}) — call NOT recorded"
        elif r.recorded:
            mark = "call-of-record APPENDED"
        else:
            mark = "unchanged (no new row)"
        facts = sum(x.form4_appended + x.price_bars_appended for x in r.ingested)
        skipped = sum(x.form4_skipped for x in r.ingested)
        sk = f" · {skipped} form4 skipped" if skipped else ""  # loudness marks the exception
        rv = sum(x.price_bars_reversioned for x in r.ingested)
        rvs = (
            f" · {rv} bars RE-VERSIONED (restated)" if rv else ""
        )  # a split re-base — loud only then
        print(f"  {r.name}: +{facts} facts{sk}{rvs} · {mark}")
    # transitions get their own LOUD block — and only when there ARE any (loudness marks the
    # exception; the common all-quiet night prints nothing here)
    transitions = [r.transition for r in results if r.transition]
    if transitions:
        print("TRANSITIONS:")
        for t in transitions:
            print(f"  {t}")
    wh = f" · {len(withheld)} withheld" if withheld else ""  # loud only when it happens
    print(
        f"done: {len(results)} theses · {appended} appended · {unchanged} unchanged{wh} · "
        f"{len(transitions)} transitions · {len(errored)} errored"
    )
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Daily cron: refresh facts + append the call-of-record per thesis."
    )
    p.add_argument("--asof", default=None, help="as-of date YYYY-MM-DD (default: today)")
    p.add_argument("--no-live", action="store_true", help="cache-only ingest (no network)")
    args = p.parse_args(argv)
    asof = date.fromisoformat(args.asof) if args.asof else date.today()
    allow_live = not args.no_live

    started_at = datetime.now(timezone.utc)
    conn = connect()
    try:
        results = run_daily(conn, asof=asof, allow_live=allow_live)
    finally:
        conn.close()
    # R3 — the cron's run-of-record, so the next freeze is noticed by the platform, not by eye. Written
    # AFTER the run from the collected results (write-only, no DB); fail-open, so it never fails the cron.
    write_cron_run_log(
        results,
        asof=asof,
        allow_live=allow_live,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
    )
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a scheduler / wrapper, non-silently


if __name__ == "__main__":
    main()
