"""The daily cron's run-of-record — one WRITE-ONLY JSON artifact per cron pass.

The cron went ~11 days silently frozen (the R1 submissions-cache freeze) and it was found only because the
OPERATOR happened to look at a thesis and think "that doesn't seem right." The cron had no memory of itself:
the exit code is swallowed by the sleep-loop wrapper, the notifier falls back to stdout, and stdout dies on
the next `docker compose up`. Answering "did it run last night, and did it do anything?" took forensics on the
`calls` table. This closes that gap the same way the DISCOVER stage closed its own (`draft_run_log.py`) and the
back half closed its call gap (the immutable `calls` log): an append-only run record the platform writes about
itself, so the next freeze is noticed by the platform, not by eye.

BOUNDS (a file is not a fact — the `draft_run_log.py` discipline):

- **Write-only, no DB.** This module WRITES a file and opens no connection; it cannot touch a spine row. It is
  called by `pipeline.daily.main` AFTER the run completes, from the already-collected results.
- **Fail-open, logged.** A run-log write that fails (disk full, permissions) is a logged exception and `None`,
  NEVER a failed cron. The record is best-effort; the ingest + call-of-record it records are not.
- **Value-free.** It records counts + outcomes the run already produced (appended / unchanged / errored,
  per-thesis ingest tallies); it computes no number and reads no fact (#3).

The home mirrors the caches + the draft log: the repo's gitignored `data/` locally, `/data` in the container
(the compose `appdata:/data` volume), so the record survives rebuilds — the very thing whose absence made the
cron invisible.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing the pipeline module at import time (keeps the layering one-way)
    from pipeline.daily import ThesisRunResult

_log = logging.getLogger("alphadeck.cron")

# Runtime artifacts live under the repo's gitignored data/ (== the container's volume-mounted /data);
# tests pass an explicit base_dir.
_DEFAULT_CRON_RUNS = Path(__file__).resolve().parents[2] / "data" / "cron_runs"


def build_run_payload(
    results: list[ThesisRunResult],
    *,
    asof: date,
    allow_live: bool,
    started_at: datetime,
    finished_at: datetime,
    catch_up: bool = False,
    benchmark_errors: int = 0,
    benchmark_leg_failed: bool = False,
    tape_evaluated: bool = False,
    tape_stale_new: tuple[str, ...] = (),
) -> dict:
    """The run-of-record payload — PURE (no I/O), extracted from the artifact writer so the admin
    "run now" job can shape its poll result IDENTICALLY to a parsed artifact (one schema, two readers).

    The payload answers, from a file read, every question the freeze investigation answered by forensics:
    *did it run* (`started_at`/`finished_at`), *for real or cache-only* (`mode` — the R2 no-live signal),
    *did the network actually happen* (`edgar_fetches` — THE FREEZE DETECTOR), *did it do anything*
    (per-thesis fact tallies + `names_ingested`/`names_errored`), and *what moved*
    (`recorded`/`transition`/`error`).

    Two distinct do-nothing shapes the counts alone could NOT tell apart, now separable:
    - **a FREEZE** (the R1 bug: a stale index served forever) and **a healthy quiet day** (nothing filed)
      produce IDENTICAL fact tallies (0 appended, N skipped, names_ingested>0, names_errored 0). The ONLY
      difference is whether a request went out — so `edgar_fetches` is recorded: **0 on a `live` run = the
      freeze**, a healthy night is in the hundreds. Without this the module built to fix "unfalsifiable —
      indistinguishable from we stopped looking" would have reproduced that very blindness.
    - a **total-ingest failure** (`names_errored == names_ingested`, the Source-C shape R2 gates on).

    `catch_up` marks a `--catch-up` pass (the sidecar's boot / late-wake catch-up): the admin history
    re-derives health from this payload and must NOT page a catch-up's legitimate ~0 fetches as a freeze
    (it runs inside the EDGAR TTL). An artifact written before the key existed reads as `False`.

    `benchmark_errors` / `benchmark_leg_failed` (G4) record the SHARED-INPUT refresh leg's outcome. They are
    here, not only in the live page, for the same reason `catch_up` is: **the admin history re-derives health
    from this file** (`app/routers/admin.py::_admin_run_out` → `assess_health`), so a count that reaches only
    the notifier would make the night the platform PAGED about re-read forever as a green row. An artifact
    written before the keys existed reads as `0` / `False` (the reader uses `.get` — a new key must never
    make an old artifact unparseable, which would blank the whole history).

    `tape_evaluated` / `tape_stale_new` + the per-thesis `tape_stale` list (G5a) record the PRICE-TAPE
    RECENCY monitor. Three jobs, which is why all three live on the artifact:
    - the per-thesis `tape_stale` rows (`security_id`, `ticker`, `edge`) are the DURABLE inventory the Admin
      freshness panel renders and the next run's diff reads (`previous_stale_tapes`, below) — keyed on
      `security_id`, never the ticker, because a ticker-less name must still be able to page and a ticker
      changing under a name is half of why the monitor exists;
    - `tape_stale_new` is that diff's OUTPUT for the night (display labels — the ticker, or the id when
      ticker-less), so the admin history re-derives the same page this run emitted;
    - `tape_evaluated` says the pass actually LOOKED (live + the monitor enabled). A `--no-live` pass records
      `false` and is therefore skipped as a diff baseline — otherwise a hand-run cache-only pass would reset
      the baseline and silence the next real page.
    An artifact written before these keys existed reads as `false` / `[]` / no rows (`.get` again).
    """
    recorded = sum(1 for r in results if r.recorded)
    edgar_fetches = sum(r.edgar_fetches for r in results)
    return {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_s": round((finished_at - started_at).total_seconds(), 3),
        "asof": asof.isoformat(),
        "mode": "live" if allow_live else "no-live",  # the R2 recording-gate signal
        "catch_up": catch_up,  # a --catch-up pass: the freeze page is skipped for it (~0 fetches is correct)
        # THE FREEZE DETECTOR: total EDGAR network pulls this run. A frozen index and a healthy
        # nothing-filed night both show 0 new facts — this is the number that differs. 0 on a `live`
        # run = the cache never refreshed = a freeze (R4 pages on it); a healthy night is in the hundreds.
        "edgar_fetches": edgar_fetches,
        # G4 — the shared-input refresh leg (SPY/IWM, feeding benchmark_rs): individual pulls that failed,
        # and whether the leg itself died before producing any result. Run-LEVEL, like edgar_fetches: the
        # leg runs once per pass on its own connection, outside the per-thesis loop.
        "benchmark_errors": benchmark_errors,
        "benchmark_leg_failed": benchmark_leg_failed,
        # G5a — the price-tape recency monitor. `tape_evaluated` = this pass looked at all (live + enabled);
        # `tape_stale_new` = the names that became stale since the previous EVALUATED live pass, as display
        # labels (ticker, or the security id when ticker-less — never dropped, #9).
        "tape_evaluated": tape_evaluated,
        "tape_stale_new": list(tape_stale_new),
        "summary": {
            "theses": len(results),
            "appended": recorded,
            "unchanged": sum(1 for r in results if r.recorded is False),
            # R2a — calls WITHHELD (no-live / total ingest failure); R4 pages on this
            "withheld": sum(1 for r in results if r.withheld_reason),
            "errored": sum(1 for r in results if r.error),
            "transitions": sum(1 for r in results if r.transition),
        },
        "theses": [
            {
                "id": str(r.thesis_id),
                "name": r.name,
                "recorded": r.recorded,
                "withheld_reason": r.withheld_reason,  # R2a — why the call was NOT recorded (or None)
                "transition": r.transition,
                "error": r.error,
                "edgar_fetches": r.edgar_fetches,  # per-thesis freeze detector
                "names_ingested": len(r.ingested),
                "names_errored": sum(1 for x in r.ingested if x.error),
                "form4_appended": sum(x.form4_appended for x in r.ingested),
                "price_bars_appended": sum(x.price_bars_appended for x in r.ingested),
                "form4_skipped": sum(x.form4_skipped for x in r.ingested),
                # the ETF sleeves' fund-shares samples (net flow F2) — 0 on an all-equity thesis
                "fund_shares_appended": sum(x.fund_shares_appended for x in r.ingested),
                # G5a — this thesis's STALE price tapes: the durable inventory the Admin panel renders and
                # the next run's diff keys on. `edge` is the latest stored bar date (null = no bars at all).
                "tape_stale": [
                    {
                        "security_id": str(s.security_id),
                        "ticker": s.ticker,
                        "edge": s.edge.isoformat() if s.edge else None,
                    }
                    for s in r.tape_stale
                ],
            }
            for r in results
        ],
    }


def write_cron_run_log(
    results: list[ThesisRunResult],
    *,
    asof: date,
    allow_live: bool,
    started_at: datetime,
    finished_at: datetime,
    base_dir: Path | None = None,
    catch_up: bool = False,
    benchmark_errors: int = 0,
    benchmark_leg_failed: bool = False,
    tape_evaluated: bool = False,
    tape_stale_new: tuple[str, ...] = (),
) -> Path | None:
    """Dump one cron pass (``build_run_payload``, above — the payload's meaning lives there) to
    ``<base>/<utc-timestamp>.json``; return the path (or ``None`` fail-open). The whole write — payload
    build included — stays inside the fail-open try: a run-log fault is a logged exception, never a
    failed cron."""
    try:
        payload = build_run_payload(
            results,
            asof=asof,
            allow_live=allow_live,
            started_at=started_at,
            finished_at=finished_at,
            catch_up=catch_up,
            benchmark_errors=benchmark_errors,
            benchmark_leg_failed=benchmark_leg_failed,
            tape_evaluated=tape_evaluated,
            tape_stale_new=tape_stale_new,
        )
        run_dir = base_dir or _DEFAULT_CRON_RUNS
        run_dir.mkdir(parents=True, exist_ok=True)
        # %H%M%S, no colons — legal on Windows too; the started-at instant names the run
        path = run_dir / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract: log, never fail the cron
        _log.exception("cron run log write failed (fail-open — the cron run is unaffected)")
        return None


def already_ran_live(asof: date, *, run_at: time, tz: tzinfo, base_dir: Path | None = None) -> bool:
    """Did a LIVE cron pass for ``asof`` already run **for the night** — one that STARTED at or after
    ``asof``'s ``run_at`` in market time? The catch-up guard (R6): a sidecar that boots after the scheduled
    time, or wakes late, must catch the missed run up — but ONLY if it truly hasn't run, or every rebuild
    would re-fire the whole job (and R3 said the run log is the memory that answers this).

    ``mode == "live"`` is load-bearing: a ``--no-live`` dev run writes a run log too, but it must NOT count as
    "the night ran" — otherwise a hand-run ``--no-live`` (like the R4 page test) would suppress the real
    nightly catch-up, and the real run would silently never happen.

    **Started at/after ``run_at`` is load-bearing too (2026-09-10).** A live pass that started BEFORE that
    night's ``run_at`` — a pre-open Admin "Run daily now" at 09:15 — ran on the PRIOR session's bars: it lacks
    the night's close and is not the night's pass, so it must not satisfy the guard. MEASURED on prod: two
    manual passes for as-of 2026-09-09 at 09:09 / 09:15 ET (the host was then OFF at the 22:30 run) made the
    old any-live-pass rule report "already ran", so even a wider boot catch-up would have been a no-op, and
    Sep 9's record stayed a pre-open call. A late-wake or boot catch-up the NEXT morning started AFTER the
    cutoff and does count (a ``--catch-up`` pass is a live pass). The cutoff is
    ``datetime.combine(asof, run_at, tzinfo=tz)``, compared as AWARE datetimes (the artifact's ``started_at``
    is an aware UTC ISO stamp). PURE over its arguments — no settings read, no ambient clock (the
    ``schedule.py`` discipline); ``daily.main`` supplies ``run_at`` / ``tz`` from config.

    Fail-open = **err toward running**: an unreadable artifact, or a live one whose ``started_at`` is missing /
    unparseable / NAIVE (a naive stamp cannot be compared honestly against a market-time cutoff), is skipped,
    so a bad artifact can never make the guard falsely report "ran" and cancel a needed catch-up (a repeated
    run is safe — ``record_if_changed`` — a skipped one is the silent gap R6 exists to close).

    KNOWN INTERACTION (accepted, not a bug): the run-log write itself (``write_cron_run_log``) is **fail-open**
    by contract — a cron pass runs fine but its log write fails (disk full / permissions). Then this guard sees
    no entry for the night and a post-``run_at`` restart **re-fires the ~65-min ingest**. Not corrupting —
    ``record_if_changed`` suppresses the duplicate call-of-record — just wasteful. We accept it: a re-run beats a
    silent skip, and the failure mode is bounded to a disk problem that would page anyway.
    """
    run_dir = base_dir or _DEFAULT_CRON_RUNS
    if not run_dir.exists():
        return False
    target = asof.isoformat()
    # the night's scheduled instant in market time — the bar a live pass must have STARTED at/after
    cutoff = datetime.combine(asof, run_at, tzinfo=tz)
    for p in run_dir.glob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (
            Exception
        ):  # noqa: BLE001 — a bad artifact never suppresses a catch-up (err toward running)
            continue
        if not isinstance(doc, dict) or doc.get("asof") != target or doc.get("mode") != "live":
            continue
        try:
            started = datetime.fromisoformat(doc["started_at"])
        except (KeyError, TypeError, ValueError):
            continue  # no honest start instant -> not evidence the night ran
        if started.tzinfo is None or started.utcoffset() is None:
            continue  # a naive stamp cannot be compared against a market-time cutoff
        if started >= cutoff:
            return True
    return False


def previous_stale_tapes(*, base_dir: Path | None = None) -> set[str] | None:
    """The set of ``security_id`` strings whose price tape was STALE on the most recent pass that actually
    EVALUATED recency — the baseline the current pass diffs against so only NEWLY stale tapes page (G5a).

    ``None`` (distinct from an empty set!) = **no evaluated live artifact exists yet** — the first pass after
    this monitor deployed, or a run home with nothing in it. The caller treats that as "everything currently
    stale is news" so the operator gets the inventory ONCE, loudly, instead of a silent first night; an empty
    SET means a pass did look and found nothing stale, so a name appearing now is genuinely new.

    Two filters, both load-bearing. ``mode == "live"``: a ``--no-live`` pass cannot see a tape's current edge
    honestly and must never become the baseline, or a hand-run cache-only pass would silence the next real
    page. ``tape_evaluated``: an artifact written BEFORE this monitor existed, or by a pass with the monitor
    disabled (``tape_stale_days = 0``), carries no stale inventory — reading its absent list as "nothing was
    stale" would make every already-known dead tape page again as if new.

    Newest-first, first match wins. Fail-open PER ARTIFACT like its siblings (an unreadable or malformed file
    is skipped, never an exception) — and the whole read is best-effort: the caller's worst case is a repeat
    page, never a missed one. Pure file read — no DB, no network, nothing written."""
    for doc in list_run_logs(base_dir=base_dir):
        if doc.get("mode") != "live" or not doc.get("tape_evaluated", False):
            continue
        out: set[str] = set()
        for t in doc.get("theses") or []:
            if not isinstance(t, dict):
                continue
            for s in t.get("tape_stale") or []:
                if isinstance(s, dict) and s.get("security_id"):
                    out.add(str(s["security_id"]))
        return out
    return None


def list_run_logs(*, base_dir: Path | None = None, limit: int | None = None) -> list[dict]:
    """Parsed run-of-record payloads, NEWEST-FIRST — the admin surface's read side (``/admin/status`` +
    ``/admin/runs``). The artifact filename IS the started-at instant (``%Y%m%dT%H%M%SZ``), so the name
    sort is the time sort. Fail-open PER ARTIFACT, mirroring ``already_ran_live``: an unreadable or
    non-object file is SKIPPED (a corrupt night must not blank the whole history), and a missing dir is
    an empty history. ``limit`` bounds how many parseable payloads are returned (None = all). Pure file
    read — no DB, no network, nothing written."""
    run_dir = base_dir or _DEFAULT_CRON_RUNS
    if not run_dir.exists():
        return []
    out: list[dict] = []
    for p in sorted(run_dir.glob("*.json"), reverse=True):
        if limit is not None and len(out) >= limit:
            break
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — skip-unreadable, never a failed read
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out
