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
  called by `pipeline.daily.run_daily_pass` from the already-collected results.
- **Fail-open, logged.** A run-log write that fails (disk full, permissions) is a logged exception and `None`,
  NEVER a failed cron. The record is best-effort; the ingest + call-of-record it records are not.
- **Value-free.** It records counts + outcomes the run already produced (appended / unchanged / errored,
  per-thesis ingest tallies); it computes no number and reads no fact (#3).

M1 — ONE FILE PER PASS, WRITTEN TWICE (2026-10-16). The artifact used to be written only at the END of a
pass, so a pass that died mid-loop left NO trace: MEASURED on 2026-10-12 the 22:30 run died at ~22:42 when
the host VM went down, 4 of 12 theses had rows, there was no artifact, and `/admin/status` read healthy for
~18 h off the previous night's file. Now the pass writes a START artifact (``status: "running"``,
``started_at``, ``finished_at: null``, the run identity + env) the moment it holds the run lock
(``write_run_start``), and FINALIZES the same file — same name, the started-at instant — to
``status: "finished"`` with the full payload at the end (``write_cron_run_log``). A pass that dies on an
exception Python sees is re-marked ``"crashed"`` (``mark_run_crashed``); a HARD kill (the VM, ``SIGKILL``,
the C2 timeout) leaves ``"running"`` behind, which the Admin page reads as "did not finish" once it is older
than the run timeout. An artifact written before M1 has no ``status`` key and reads as FINISHED
(``run_status``) — it was only ever written at the end of a completed pass. Every writer here is atomic
(``tmp`` + ``os.replace``), so the start file is never seen half-overwritten.

**THE GUARD TRAP (load-bearing):** every reader that treats an artifact as "this pass happened" —
``already_ran_live`` above all — must count only a FINISHED artifact. A ``running`` one credited by the
``--catch-up`` guard would make a killed night block its own recovery.

The home mirrors the caches + the draft log: the repo's gitignored `data/` locally, `/data` in the container
(the compose `appdata:/data` volume), so the record survives rebuilds — the very thing whose absence made the
cron invisible.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any

from domain.feed_kinds import StaleFeedLabel, feed_kind
from ingest.skips import dedup_skips
from pipeline.edgar_coverage import EdgarCoverage, pass_fresh
from pipeline.ingest_skips import SkipDiff
from pipeline.insider_amendment_scan import AmendmentScan
from pipeline.missing_closes import MissingCloses
from pipeline.name_errors import NameIngestError, name_errors_of
from pipeline.rename_probe import RenameProbe
from pipeline.split_basis import SplitScan, defect_keys_of
from pipeline.successor_cik import SuccessorScan

if TYPE_CHECKING:  # avoid importing the pipeline module at import time (keeps the layering one-way)
    from pipeline.daily import ThesisRunResult
    from pipeline.self_audit import AuditResult

_log = logging.getLogger("alphadeck.cron")

# Runtime artifacts live under the repo's gitignored data/ (== the container's volume-mounted /data);
# tests pass an explicit base_dir.
_DEFAULT_CRON_RUNS = Path(__file__).resolve().parents[2] / "data" / "cron_runs"

# M1 — the pass status an artifact carries. "running" is written at the start, "finished" at the end, and
# "crashed" when an exception escaped the pass; a hard kill leaves "running".
RUN_STATUS_RUNNING = "running"
RUN_STATUS_FINISHED = "finished"
RUN_STATUS_CRASHED = "crashed"
# X1 — the run's KIND (what it does), stamped on every artifact this module writes: always the nightly here
PASS_KIND_NIGHTLY = "nightly"


def run_status(doc: dict) -> str:
    """The pass status an artifact records. A pre-M1 artifact has NO ``status`` key and reads
    ``"finished"`` (back-compat: it was only ever written at the end of a completed pass). A present but
    non-string value reads ``"unknown"`` — never finished, so a damaged status can never credit a night.
    """
    s = doc.get("status", RUN_STATUS_FINISHED)
    return s if isinstance(s, str) else "unknown"


def is_finished(doc: dict) -> bool:
    """Did this artifact's pass FINISH (or is it a legacy artifact with no status)? The one test every
    "this pass happened" reader applies — the ``--catch-up`` guard, the stale-tape / name-error baselines,
    the Admin ``last_run``. (The backfill's record-clock ``next-run`` pin read it too, until the backfill
    moved to the public clock, 2026-10-21.)"""
    return run_status(doc) == RUN_STATUS_FINISHED


def _artifact_path(run_dir: Path, started_at: datetime) -> Path:
    # %H%M%S, no colons — legal on Windows too; the started-at instant names the run, so the start write
    # and the finalize land on the SAME file (one file per pass)
    return run_dir / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"


def _write_atomic(path: Path, payload: dict) -> None:
    """Write ``payload`` to ``path`` via a sibling temp file + ``os.replace`` — a reader (or a kill) never
    sees a half-written artifact. The temp name does not end in ``.json``, so ``*.json`` readers skip it.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def build_start_payload(
    *,
    asof: date,
    allow_live: bool,
    started_at: datetime,
    catch_up: bool = False,
    code_sha: str | None = None,
    config_hash: str | None = None,
    run_kind: str | None = None,
    env: dict | None = None,
) -> dict:
    """M1 — the START artifact's payload (PURE): who is running, for which night, under which identity —
    and nothing a reader could mistake for an outcome. Deliberately NO ``theses`` / ``summary`` /
    ``tape_evaluated`` / ``name_errors_evaluated`` keys: the guard's "recorded for some thesis" test, the
    stale-tape and name-error baselines and the Admin run row all find nothing to credit in it, on top of
    the explicit ``status`` check each of them makes."""
    return {
        "status": RUN_STATUS_RUNNING,
        "pass_kind": PASS_KIND_NIGHTLY,  # X1 — see `build_run_payload`
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "asof": asof.isoformat(),
        "mode": "live" if allow_live else "no-live",
        "catch_up": catch_up,
        "code_sha": code_sha,
        "config_hash": config_hash,
        "run_kind": run_kind,
        "env": dict(env) if env is not None else None,
    }


def write_run_start(
    *,
    asof: date,
    allow_live: bool,
    started_at: datetime,
    base_dir: Path | None = None,
    catch_up: bool = False,
    code_sha: str | None = None,
    config_hash: str | None = None,
    run_kind: str | None = None,
    env: dict | None = None,
) -> Path | None:
    """M1 — write the pass's START artifact (``status: "running"``) at the file the finalize will
    overwrite. Fail-open like every writer here: a fault is a logged exception and ``None``, never a failed
    pass (the pass still finalizes the same path at its end).

    It NEVER overwrites an existing file: at start time a file at this path can only be ANOTHER pass's
    (the name is the started-at second), and clobbering a finished artifact would erase that pass's record
    and the baselines the stale-tape / name-error diffs read. Two passes cannot start in the same second
    under the run lock, so in practice this never fires; when it does, the pass runs without a start record
    and its final write lands on that path exactly as every pre-M1 pass did."""
    try:
        payload = build_start_payload(
            asof=asof,
            allow_live=allow_live,
            started_at=started_at,
            catch_up=catch_up,
            code_sha=code_sha,
            config_hash=config_hash,
            run_kind=run_kind,
            env=env,
        )
        run_dir = base_dir or _DEFAULT_CRON_RUNS
        run_dir.mkdir(parents=True, exist_ok=True)
        path = _artifact_path(run_dir, started_at)
        if path.exists():
            _log.warning(
                "cron start artifact skipped — %s already exists (another pass's)", path.name
            )
            return None
        _write_atomic(path, payload)
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract: log, never fail the cron
        _log.exception("cron start artifact write failed (fail-open — the pass is unaffected)")
        return None


def mark_run_crashed(
    *,
    started_at: datetime,
    crashed_at: datetime,
    error: str,
    base_dir: Path | None = None,
    timings: dict | None = None,
) -> Path | None:
    """M1 — re-mark a pass's START artifact ``status: "crashed"`` (+ ``crashed_at`` and the one-line
    ``error``) when an exception escaped the pass. ONLY a file still reading ``running`` is touched: a pass
    that already FINALIZED and then raised in a post-leg keeps its finished record (overwriting it would
    throw the night's summary away), and a missing or unreadable start file is left alone. Fail-open —
    called from the crash path, so it must never raise over the exception being reported.

    X5 — ``timings`` (the pass meter's snapshot at the crash) replaces the file's ``timings`` block when
    given; ``None`` keeps whatever the last per-thesis checkpoint left (the Admin parent, which stops a pass
    from outside, has no meter)."""
    try:
        path = _artifact_path(base_dir or _DEFAULT_CRON_RUNS, started_at)
        if not path.exists():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or run_status(doc) != RUN_STATUS_RUNNING:
            return None
        if doc.get("started_at") != started_at.isoformat():
            return None  # not THIS pass's start record — never re-mark another pass

        doc["status"] = RUN_STATUS_CRASHED
        doc["crashed_at"] = crashed_at.isoformat()
        doc["error"] = error
        if timings is not None:
            doc["timings"] = timings
        _write_atomic(path, doc)
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract
        _log.exception("cron crash marker write failed (fail-open)")
        return None


def amend_run_timings(
    path: Path,
    *,
    started_at: datetime,
    timings: dict,
    status: str = RUN_STATUS_FINISHED,
) -> Path | None:
    """X5 — replace the ``timings`` block of THIS pass's artifact at ``path`` in place, touching nothing
    else. Two writers: the per-thesis CHECKPOINT onto the start artifact (``status`` ``"running"`` — a pass
    killed mid-loop still leaves where it spent its time) and the FINAL block after the post-artifact legs
    (``"finished"`` — the health page, the shell sweep and the SPAC radar run after the artifact is first
    written). Only a file that reads ``status`` AND carries this pass's ``started_at`` is rewritten, so it
    can never re-open a finished record as running, revive a crashed one, or touch another pass's file.
    Atomic like every writer here; fail-open (a logged exception and ``None``, never a failed pass).
    """
    try:
        if not path.exists():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or run_status(doc) != status:
            return None
        if doc.get("started_at") != started_at.isoformat():
            return None  # not THIS pass's artifact
        doc["timings"] = timings
        _write_atomic(path, doc)
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract: measurement never fails the cron
        _log.exception("cron run timings write failed (fail-open — the pass is unaffected)")
        return None


def _split_reads(results) -> dict[str, dict]:
    """The pass's per-name split reads (S2) — ``ingest_thesis.split_reads_of`` over every thesis's names
    (imported lazily: this module is the artifact writer, kept free of the ingest leg's import graph).
    """
    from pipeline.ingest_thesis import split_reads_of

    return split_reads_of(x for r in results for x in getattr(r, "ingested", None) or ())


def previous_split_reads(*, base_dir: Path | None = None) -> dict[str, dict[str, int]] | None:
    """``{security_id: {reason: n}}`` — the split list's per-name reads on the most recent LIVE, FINISHED pass
    that recorded them (``split_reads``): the baseline the pass summary compares against, so a divide-back
    that stands night after night is named once, not every night (``ingest_thesis.print_split_lines``, the
    re-review's N2). A ``--no-live`` pass, a killed pass's start artifact, and an artifact written before the
    key existed are skipped. ``None`` = no such artifact yet: every divide-back is news, so each prints once.
    Newest-first, first match wins; fail-open per artifact (``.get`` throughout). Pure file read."""
    for doc in list_run_logs(base_dir=base_dir):
        if doc.get("mode") != "live" or not is_finished(doc) or "split_reads" not in doc:
            continue
        reads = doc.get("split_reads")
        if not isinstance(reads, dict):
            continue
        return {
            str(k): dict((v or {}).get("reasons") or {})
            for k, v in reads.items()
            if isinstance(v, dict)
        }
    return None


def _sum_reasons(counts) -> dict[str, int]:
    """Per-reason totals over the names' split-normalization counts (S2), key-sorted for a stable artifact."""
    out: dict[str, int] = {}
    for c in counts:
        for k, n in c.items():
            out[k] = out.get(k, 0) + int(n)
    return dict(sorted(out.items()))


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
    tape_stale_new: tuple[StaleFeedLabel, ...] = (),
    tape_stale_days: int = 0,
    fund_shares_stale_days: int = 0,
    code_sha: str | None = None,
    config_hash: str | None = None,
    run_kind: str | None = None,
    env: dict | None = None,
    name_errors_evaluated: bool = False,
    name_errors_new: Sequence[NameIngestError] = (),
    name_errors_known: int = 0,
    audit: Sequence[AuditResult] = (),
    split: SplitScan | None = None,
    rename_probe: RenameProbe | None = None,
    successor: SuccessorScan | None = None,
    skips: SkipDiff | None = None,
    timings: dict | None = None,
    amendments: AmendmentScan | None = None,
    edgar_coverage: EdgarCoverage | None = None,
    split_detect: Any = None,
    missing_closes: MissingCloses | None = None,
) -> dict:
    """The run-of-record payload — PURE (no I/O), extracted from the artifact writer so the admin
    "run now" job can shape its poll result IDENTICALLY to a parsed artifact (one schema, two readers).

    The payload answers, from a file read, every question the freeze investigation answered by forensics:
    *did it run* (`started_at`/`finished_at`), *for real or cache-only* (`mode` — the R2 no-live signal),
    *did the network actually happen* (`edgar_fetches` attempted, `edgar_fetches_completed` — THE FREEZE
    DETECTOR since O5), *did it do anything*
    (per-thesis fact tallies + `names_ingested`/`names_errored`), and *what moved*
    (`recorded`/`transition`/`error`).

    Two distinct do-nothing shapes the counts alone could NOT tell apart, now separable:
    - **a FREEZE** (the R1 bug: a stale index served forever) and **a healthy quiet day** (nothing filed)
      produce IDENTICAL fact tallies (0 appended, N skipped, names_ingested>0, names_errored 0). The ONLY
      difference is whether data came back — so `edgar_fetches_completed` is recorded (O5; `edgar_fetches`,
      the attempts, beside it): **0 completed on a `live` run = the freeze**, a healthy night is in the
      hundreds. Without this the module built to fix "unfalsifiable — indistinguishable from we stopped
      looking" would have reproduced that very blindness.
    - a **total-ingest failure** (`names_errored == names_ingested`, the Source-C shape R2 gates on).

    THE FREEZE PAGE's RATIO FLOOR (`pipeline/edgar_coverage.py`) — run-level `edgar_companies` (the distinct
    EDGAR-eligible companies, by CIK, the processed theses ingested), `edgar_companies_fresh` (how many of
    them got >= 1 completed EDGAR fetch anywhere in the pass) and `freeze_completed_floor` (the floor the pass
    was judged under): FROZEN when fresh < floor x eligible. The Admin history re-judges the floor from these
    three keys, so a later change of the setting never recolors a past night; an artifact without them (written
    before the floor) keeps the rule it was paged under. Per thesis, `edgar_companies` / `edgar_companies_fresh`
    are the same two counts over THAT thesis's companies — fresh meaning fresh anywhere in the pass, because a
    company another thesis fetched minutes earlier is a cache hit in this one (diagnostic only; the verdict
    reads the deduplicated run-level pair). `null` on a thesis a catch-up skipped.

    `catch_up` marks a `--catch-up` pass (the sidecar's boot / late-wake catch-up). Since O5 a catch-up's
    freeze verdict judges the theses it processed like any pass (its M2-skipped theses are left out); the
    flag still decides an artifact written BEFORE O5, which the admin history re-derives by the rule it was
    paged under (a catch-up exempt wholesale). An artifact written before the key existed reads as `False`.

    `benchmark_errors` / `benchmark_leg_failed` (G4) record the SHARED-INPUT refresh leg's outcome. They are
    here, not only in the live page, for the same reason `catch_up` is: **the admin history re-derives health
    from this file** (`app/routers/admin.py::_admin_run_out` → `assess_health`), so a count that reaches only
    the notifier would make the night the platform PAGED about re-read forever as a green row. An artifact
    written before the keys existed reads as `0` / `False` (the reader uses `.get` — a new key must never
    make an old artifact unparseable, which would blank the whole history).

    `tape_evaluated` / `tape_stale_new` / the two threshold keys + the per-thesis `tape_stale` list
    (G5a price tapes · F1 fund shares) record the FEED-RECENCY monitor. Four jobs, which is why they all
    live on the artifact:
    - the per-thesis `tape_stale` rows (`security_id`, `ticker`, `edge`, `kind`, `closed_at`) are the
      DURABLE inventory the Admin freshness panel renders and the next run's diff reads
      (`previous_stale_tapes`, below) — keyed on `kind` + `security_id`, never the ticker, because a
      ticker-less name must still be able to page, a ticker changing under a name is half of why the monitor
      exists, and one name can be stale on both feeds at once with a different repair for each. `closed_at`
      (G5b/T1) is the date of the delisting form filed near the tape's last bar when the stopped tape is a
      name that CLOSED (delisted / acquired / deregistered), so the panel renders it quietly and the
      newly-stale page skips it (#7/WB#3); `renamed` + `edgar_tickers` (T1) mark a repair row whose company
      EDGAR lists under other symbols, for display;
    - `tape_stale_new` is that diff's OUTPUT for the night ({kind, label} entries — the label being the
      ticker, or the id when ticker-less), so the admin history re-derives the same per-feed page this run
      emitted;
    - `tape_evaluated` says the pass actually LOOKED (live + at least one feed's monitor enabled). A
      `--no-live` pass records `false` and is therefore skipped as a diff baseline — otherwise a hand-run
      cache-only pass would reset the baseline and silence the next real page;
    - `tape_stale_days` / `fund_shares_stale_days` are the THRESHOLDS the pass judged under (F6), so the
      panel states the number its list was actually computed with rather than whatever the setting happens
      to say when someone opens the page.
    An artifact written before these keys existed reads as `false` / `[]` / no rows, and its `tape_stale`
    rows read as `price` (`.get` + `feed_kind`, never a raise — a new key must not blank the history).

    I1/I2 — THE RUN IDENTITY + a NON-SECRET env fingerprint. `code_sha` (`Settings.image_sha` — `null` when
    unknown, never fabricated; the same value stamped on every row the pass wrote), `config_hash` (the
    CallConfig fingerprint the assembler ran with), `run_kind` (`cron` | `manual`), and `env`: the clock
    config the pass ran under (`tz`, `market_tz`, `run_at`, `cron_at`), set/unset BOOLEANS for the secrets
    and switches (`user_agent_set`, `slack_webhook_set`, `polygon_api_key_set`, `heartbeat_url_set`) and
    the notifier class actually in use. **No secret value is ever written** — only whether it was set. The
    Admin page reads this to flag an unknown / mismatched sha, a live pass that paged into the log, an
    unset User-Agent. A pre-I1 artifact lacks the keys and reads as "not recorded", never as an alarm.

    O1 — THE PER-NAME ERRORS. Each thesis carries `name_errors` (`security_id`, `ticker`, `leg`, `error`
    — one row per failed LEG, `pipeline/name_errors.py`), and the run carries the cross-pass diff the way
    G5a carries `tape_stale_new`: `name_errors_evaluated` (a LIVE pass — the only kind that is a baseline),
    `name_errors_new` (the `security_id:leg` keys absent from the last-known inventory — what PAGED) and
    `name_errors_known` (the quiet count of the rest). `previous_name_errors` (below) reads the baseline.

    V1 — THE SELF-AUDIT. `audit` is the `[{check, ok, detail}]` list `pipeline/self_audit.run_audit` left
    (`ok` null = not applicable to this pass — never a pass); the Admin page re-derives its alarm from it.

    M1 — ``status: "finished"``: this is the FINALIZED artifact, overwriting the pass's start artifact
    (``build_start_payload``) at the same path. A pre-M1 artifact lacks the key and reads finished.

    T4 — THE SPLIT-BASIS MONITOR (`pipeline/split_basis.SplitScan.payload`): `split_evaluated` (the scan
    ran — the only kind of pass that is a diff baseline), `split_error`, `split_window_days`,
    `split_duration_s`, `split_defects` (`[{security_id, ticker, kind, dates, factor, events}]` — the
    inventory the Admin panel renders and the next pass diffs, keyed on `kind:security_id`; a kept flap
    carries `evidence`) and `split_defects_new` (what PAGED); `split_settled` (the same row shape plus
    `reason` — flaps no reader sees on two bases: named, never paged, never in the diff baseline),
    `split_settle_error` and `split_settle_duration_s`. `split=None` writes the not-evaluated block; readers
    use `.get`.

    T2 / Q8 / T3 — THE PRICE-FEED REPAIRS. `rename_probe` (`pipeline/rename_probe.RenameProbe.payload`) is
    the nightly splice probe of every renamed tape's EDGAR symbols — `{evaluated, error, fetches,
    duration_s, rows: [{security_id, ticker, edge, edgar_tickers, recommended, candidates: [{symbol,
    verified, overlap, ratio, bars, last_bar, error}], edgar_name, former_names, name_change_filings}]}` —
    a pending recommendation the Admin panel offers for the operator's Adopt. Each `tape_stale` row also
    carries `unpriceable` (Q8: no stored bar ever, neither closed nor renamed) and `acknowledged` (the
    operator's "keep it, unpriced" — quiet, never paged); each thesis carries `expected_price_misses`
    (`[{security_id, ticker, reason, error}]` — the vendor 404s T3 excused as expected, `reason` `closed` or
    `acknowledged`). An artifact written before these keys reads them absent / false / empty (`.get`).

    T7 — THE SUCCESSOR-CIK DETECTOR. `successor_cik` (`pipeline/successor_cik.SuccessorScan.payload`) —
    `{evaluated, error, judged, no_ticker, fetches, duration_s, rows: [{security_id, ticker, stored_cik,
    candidates: [{cik, entity_name, succession_filings, identity, ...}], recommended, deregistrations, key,
    label, ...}], new: [{key, label}]}` — the members whose company now files under another CIK (a pending
    recommendation the Admin panel offers for the operator's Adopt), and `new` = what PAGED. `successor=None`
    writes the not-evaluated block; readers use `.get`.

    R8-A / R8-B — THE FORM 4/A INVENTORY. `insider_amendments` (`pipeline/insider_amendment_scan.AmendmentScan
    .payload`) — `{evaluated, error, baseline, duration_s, pending: [{key, security_id, ticker, accession,
    disposition, suppressing, touches_buy, in_window, candidates, label}], new: [{key, label, in_window,
    named}], applied: {basis: n}, swept, sweep_error}` — every amendment owed the operator's decision (read
    whole from the amendment tables; since R8-B only what the system cannot resolve), `new` = what PAGED
    (`named` = named on the page, else counted), `applied` = the SYSTEM applications recorded during the pass
    (the pre-loop sweep + tonight's ingest), `swept` / `sweep_error` = the sweep's own outcome.
    `amendments=None` writes the not-evaluated block; readers use `.get`.

    Q18 — THE SKIPPED FILINGS (`pipeline/ingest_skips.SkipDiff.payload`): `ingest_skips_evaluated` (a
    LIVE pass — the only kind that is a baseline) and `ingest_skips`, one block per kind
    (`form4_unparseable` / `schedule13_outbound` / `schedule13_identity`) with `total`, `known`, `new`
    (`[{accession, security_id, ticker, reason}]` — the filings whose line printed tonight),
    `previous_total`, `delta` and the full run-level `accessions` inventory. Each THESIS also carries its own
    `ingest_skips` (`[{kind, accession}]`, the filings ITS legs skipped — attributed like `name_errors`):
    that per-thesis list is what `previous_ingest_skips` (below) carries forward as the next baseline.
    `skips=None` writes the not-evaluated empty block; readers use `.get`.

    W1 — THE PER-COMPANY WRITE LOCK (`db/write_lock.py`): each thesis carries `company_lock_waits`
    (`[{security_id, ticker, waited_s, ingested, holder}]`) — the names its ingest waited for because another
    writer held the company, `ingested: false` for one still held after the bounded wait (its legs did not run
    in this pass; the thesis recorded with `ingest_fresh=false`). Empty on the common night; readers use `.get`.

    X5 — WHERE THE PASS SPENT ITS TIME (`pipeline/pass_timings.py`, measurement only): `timings` =
    `{v, total_s, legs: {leg: s}, counts: {leg: {kind: n}}, theses: [{id, name, total_s, legs, counts,
    rss_kb, peak_rss_kb}], peak_rss_kb, peak_rss_children_kb, rss_kb, errors}` — exclusive seconds per leg
    (each thesis's legs sum to its `total_s`, the pass's to the pass's; unclaimed time is `other`), request
    counts per leg (`sec_live` / `sec_cached` / `sec_cached_404`, `http:<host>`, `http_retry:<host>`,
    `rate_wait_s`, `backoff_s`), peak RSS in KB (`null` where unmeasurable). The pass writes its snapshot
    here, then REPLACES it in place with the final block once the post-artifact legs ran
    (`amend_run_timings`). `null` on a pass that carried no meter and on every artifact written before X5.
    """
    recorded = sum(1 for r in results if r.recorded)
    edgar_fetches = sum(r.edgar_fetches for r in results)
    # O5 — None per thesis = not measured (a thesis a catch-up SKIPPED builds no client); the run sums the rest
    edgar_fetches_completed = sum(
        r.edgar_fetches_completed for r in results if r.edgar_fetches_completed is not None
    )
    # the ratio floor's per-thesis diagnostic: a thesis's company is fresh if ANY thesis fetched it this pass
    fresh_anywhere = pass_fresh(results)
    return {
        "status": RUN_STATUS_FINISHED,
        # X1 — WHAT this run does (the server-move note's `pass_kind`), beside `run_kind` (how it was
        # triggered). Every artifact in this directory is the nightly's; the staged onboarding job writes its
        # own (`pass_kind: "onboard"`) under `data/onboard_runs/`, so no reader here ever sees one.
        "pass_kind": PASS_KIND_NIGHTLY,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_s": round((finished_at - started_at).total_seconds(), 3),
        "asof": asof.isoformat(),
        "mode": "live" if allow_live else "no-live",  # the R2 recording-gate signal
        "catch_up": catch_up,  # a --catch-up pass (M2 skips its on-time theses; O5 judges the rest)
        # I1/I2 — the run identity + the NON-SECRET env fingerprint (booleans for every secret, never a value)
        "code_sha": code_sha,
        "config_hash": config_hash,
        "run_kind": run_kind,
        "env": dict(env) if env is not None else None,
        # O1 — the newly-failing diff (the per-thesis rows are under each thesis below)
        "name_errors_evaluated": name_errors_evaluated,
        "name_errors_new": [
            {"security_id": str(e.security_id), "ticker": e.ticker, "leg": e.leg}
            for e in name_errors_new
        ],
        "name_errors_known": name_errors_known,
        # V1 — the self-audit (ok: true / false / null = not applicable)
        "audit": [a.to_json() for a in audit],
        # T4 — the split-basis monitor: the inventory + the new-defect diff (flag only, never a call input)
        **(split or SplitScan()).payload(),
        # S2 — the split list's reads PER NAME, deduped across theses (``ingest_thesis.split_reads_of``): the
        # baseline the next pass reads (``previous_split_reads``) so a standing divide-back is not re-announced
        # nightly (the re-review's N2). Readers use `.get` (an older artifact lacks it).
        "split_reads": _split_reads(results),
        # T2 — the rename probe: splice-verified successor recommendations (pending; never applied)
        **(rename_probe or RenameProbe()).payload(),
        # T7 — the successor-CIK detector: members now filing under another CIK (pending; never applied)
        **(successor or SuccessorScan()).payload(),
        # Q18 — the skipped-filing inventory + its diff (known ones counted, new ones named)
        **(skips or SkipDiff()).payload(),
        # R8-A — the Form 4/A inventory owed an operator decision + what paged (pending; never applied)
        **(amendments or AmendmentScan()).payload(),
        # S4 — the nightly split detection (``pipeline/detect_splits.SplitDetection``): the new split-list rows,
        # the filings it could not place, the Polygon source's status; absent = not run (readers use ``.get``)
        **(split_detect.payload() if split_detect is not None else {}),
        # MISSING CLOSES (``pipeline/missing_closes.MissingCloses.payload``) — one block: ``evaluated``,
        # ``error``, ``session``, ``is_session``, ``benchmarks``, ``judged``, ``excluded``, ``missing`` (the
        # session's missing closes, quiet), ``still_missing`` (older sessions' closes still absent; ``loud`` per
        # row), ``arrived`` (late closes now stored, each with ``lag_h``), ``resolved`` (handed to the stale flag
        # / left the basket), ``usual_late_window``, ``duration_s``. ``None`` writes the not-evaluated block.
        **(missing_closes or MissingCloses()).payload(),
        # EDGAR network pulls this run ATTEMPTED (a pull that then failed counts too) — "times we reached out".
        "edgar_fetches": edgar_fetches,
        # O5 — THE FREEZE DETECTOR: the attempts that COMPLETED (a 2xx body arrived). A frozen index and a
        # healthy nothing-filed night both show 0 new facts — this is the number that differs, and unlike the
        # attempt count it is also 0 when every pull failed (2026-07-22: 255 attempted, 0 arrived). 0 on a
        # `live` run over processed theses = a freeze (R4 pages on it); a healthy night is in the hundreds.
        # Its PRESENCE on the per-thesis entries marks the O5 rule: the Admin history re-derives an artifact
        # without it by the pre-O5 rule (attempts; a catch-up exempt wholesale) — the verdict it paged.
        "edgar_fetches_completed": edgar_fetches_completed,
        # THE RATIO FLOOR — the per-company freshness the freeze verdict reads beside the zero rule, and the
        # floor THIS pass was judged under (the Admin history re-judges with it). Null = not measured.
        **(
            edgar_coverage.payload()
            if edgar_coverage is not None
            else {
                "edgar_companies": None,
                "edgar_companies_fresh": None,
                "freeze_completed_floor": None,
            }
        ),
        # G4 — the shared-input refresh leg (SPY/IWM, feeding benchmark_rs): individual pulls that failed,
        # and whether the leg itself died before producing any result. Run-LEVEL, like edgar_fetches: the
        # leg runs once per pass on its own connection, outside the per-thesis loop.
        "benchmark_errors": benchmark_errors,
        "benchmark_leg_failed": benchmark_leg_failed,
        # G5a/F1 — the feed-recency monitor. `tape_evaluated` = this pass looked at all (live + at least
        # one feed's monitor enabled); `tape_stale_new` = the names that became stale since the previous
        # EVALUATED live pass, as {kind, label} entries (the label being the ticker, or the security id
        # when ticker-less — never dropped, #9). The pair, not a bare string, so the Admin history can
        # re-derive the SAME per-feed page the run emitted; a bare string from an older artifact reads as
        # a price entry (`_admin_run_out`).
        "tape_evaluated": tape_evaluated,
        "tape_stale_new": [{"kind": s.kind, "label": s.label} for s in tape_stale_new],
        # F6 — the THRESHOLDS this pass judged under, one per feed. The Admin panel renders an inventory
        # and states the number it was computed with; reading that number live instead would misdescribe
        # the list whenever the setting changed between the pass and the read. 0 is a REAL value (that
        # feed's monitor was disabled), so the reader checks presence, never truthiness.
        "tape_stale_days": tape_stale_days,
        "fund_shares_stale_days": fund_shares_stale_days,
        "summary": {
            "theses": len(results),
            "appended": recorded,
            "unchanged": sum(1 for r in results if r.recorded is False),
            # R2a — calls WITHHELD (no-live / total ingest failure); R4 pages on this
            "withheld": sum(1 for r in results if r.withheld_reason),
            "errored": sum(1 for r in results if r.error),
            "transitions": sum(1 for r in results if r.transition),
            # M2 — theses a --catch-up left alone because an on-time row already stood
            "skipped": sum(1 for r in results if r.skipped),
        },
        # X5 — where the pass spent its time, requests and memory (null = not measured)
        "timings": timings,
        "theses": [
            {
                "id": str(r.thesis_id),
                "name": r.name,
                "recorded": r.recorded,
                "withheld_reason": r.withheld_reason,  # R2a — why the call was NOT recorded (or None)
                # M2 — why a --catch-up SKIPPED this thesis (its on-time row stands), or None
                "skipped": r.skipped,
                "transition": r.transition,
                "error": r.error,
                "edgar_fetches": r.edgar_fetches,  # per-thesis ATTEMPTS
                # O5 — per-thesis COMPLETED fetches (the freeze detector); null = a skipped thesis
                "edgar_fetches_completed": r.edgar_fetches_completed,
                # the RATIO FLOOR, per thesis (diagnostic): its distinct EDGAR-eligible companies, and how
                # many got fresh SEC data ANYWHERE in the pass; null = a skipped thesis (not measured)
                "edgar_companies": len(r.edgar_ciks) if r.edgar_ciks is not None else None,
                "edgar_companies_fresh": (
                    len(r.edgar_ciks & fresh_anywhere) if r.edgar_ciks is not None else None
                ),
                "names_ingested": len(r.ingested),
                "names_errored": sum(1 for x in r.ingested if x.error),
                # O1 — WHY each errored name errored, one row per failed leg (the count above kept only
                # the number; the reasons were discarded). Never a secret: the captured ingest message.
                "name_errors": [
                    {
                        "security_id": str(e.security_id),
                        "ticker": e.ticker,
                        "leg": e.leg,
                        "error": e.error,
                    }
                    for e in name_errors_of(r.ingested)
                ],
                # Q18 — the FILINGS this thesis's legs skipped tonight, one row per (kind, accession): the
                # per-thesis inventory the next pass's skip baseline carries forward (`previous_ingest_skips`
                # through `carried_inventory`), so a catch-up counts for the theses it covered. Empty for a
                # thesis a catch-up skipped (nothing was walked) — the carry-forward reads past it.
                "ingest_skips": [
                    {"kind": s.kind, "accession": s.accession}
                    for s in dedup_skips(
                        sk for x in r.ingested for sk in getattr(x, "ingest_skips", ())
                    ).values()
                ],
                "form4_appended": sum(x.form4_appended for x in r.ingested),
                "price_bars_appended": sum(x.price_bars_appended for x in r.ingested),
                # Q11 / T5 — older restated bars HELD this pass (a vendor revert awaiting a second
                # agreeing pull); 0 on a normal night. Readers use `.get` (an older artifact lacks it).
                "price_bars_held": sum(x.price_bars_held for x in r.ingested),
                # S2 — bars READ onto the split list's scale this pass, per reason (vendor_raw / vendor_doubled
                # / mis_factored / first_night; most write nothing) and the splits applied to stored history
                # for the first time — the B1 review's "never silent". `{}` / `[]` on a normal night; readers
                # use `.get` (an older artifact lacks both).
                "price_bars_split_normalized": _sum_reasons(
                    getattr(x, "price_bars_split_normalized", None) or {} for x in r.ingested
                ),
                "price_split_first_night": sorted(
                    {
                        f"{x.ticker or x.security_id}: {label}"
                        for x in r.ingested
                        for label in getattr(x, "price_split_first_night", ()) or ()
                    }
                ),
                "form4_skipped": sum(x.form4_skipped for x in r.ingested),
                # the ETF sleeves' fund-shares samples (net flow F2) — 0 on an all-equity thesis
                "fund_shares_appended": sum(x.fund_shares_appended for x in r.ingested),
                # G5a/F1 — this thesis's STALE feeds: the durable inventory the Admin panel renders and the
                # next run's diff keys on. `edge` is the latest stored date for that feed (null = none at
                # all); `kind` says WHICH feed stopped, because the two are repaired differently. A row
                # without `kind` is a pre-F1 artifact's row and reads as `price` (`feed_kind`).
                # G5b — `closed_at` is the delisting-form filing date when this stopped tape belongs to a
                # name that CLOSED (delisted / acquired / deregistered), else null (a feed gap to repair).
                # The panel reads it to render the row quietly as "closed" instead of loud "stale — repair"
                # (`_tape_out`); a pre-G5b artifact's row lacks the key and reads null (`.get`, never a raise).
                "tape_stale": [
                    {
                        "security_id": str(s.security_id),
                        "ticker": s.ticker,
                        "edge": s.edge.isoformat() if s.edge else None,
                        "kind": s.kind,
                        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
                        # T1 — a REPAIR row whose company EDGAR lists under other symbols (the rename-
                        # starved shape), plus EDGAR's list as read this pass (null = not read). Display
                        # only; readers use `.get` so an older artifact reads not-renamed / null.
                        "renamed": s.renamed,
                        "edgar_tickers": (
                            list(s.edgar_tickers) if s.edgar_tickers is not None else None
                        ),
                        # Q8 — never priced (and neither closed nor renamed), and whether the operator
                        # acknowledged it ("keep it, unpriced" — a quiet row that never pages)
                        "unpriceable": s.unpriceable,
                        "acknowledged": s.acknowledged,
                    }
                    for s in r.tape_stale
                ],
                # T3 — the vendor 404s excused as EXPECTED (a closed or acknowledged name): not name
                # errors, so they do not spoil `ingest_fresh` — and recorded here, never dropped (#9)
                "expected_price_misses": [
                    m.to_json() for m in getattr(r, "expected_price_misses", ()) or ()
                ],
                # W1 — the per-company write lock (`db/write_lock.py`): one row per name this thesis's
                # ingest had to WAIT for because another writer (a Workbench ingest, a CLI run) held the
                # company. `ingested` false = still held after the bounded wait, so this pass did not run
                # its legs (not an error; the thesis recorded with `ingest_fresh=false`); `holder` names
                # who held it. Empty on the common night. Readers use `.get` (older artifacts lack it).
                "company_lock_waits": [
                    {
                        "security_id": str(x.security_id),
                        "ticker": x.ticker,
                        "waited_s": getattr(x, "lock_waited_s", 0.0),
                        "ingested": not getattr(x, "lock_busy", None),
                        "holder": getattr(x, "lock_busy", None),
                    }
                    for x in r.ingested
                    if getattr(x, "lock_busy", None) or getattr(x, "lock_waited_s", 0.0)
                ],
                # X1 — the names whose HISTORY was still loading in the staged onboarding job
                # (`pipeline/onboarding.py`): walked with their per-filing legs windowed to filings since
                # `filed_since` (null = not walked here: `deferred` past the pass's onboarding cap, or another
                # writer held the company); the thesis recorded with `ingest_fresh=false`. Listed, never
                # dropped (#9). Readers use `.get`.
                "onboarding": _onboarding_rows(r.ingested),
                # the revenue rows the fundamentals data-quality screen kept OUT of this thesis's names'
                # series tonight (`ingest.fundamentals.ScreenedRow`) — never stored, so recorded here, one
                # row each, never dropped (#9). Readers use `.get` (`fundamentals_screened`, below): an
                # artifact written before the key existed reads as none screened.
                "fundamentals_screened": [
                    {
                        "security_id": str(f.security_id),
                        "ticker": f.ticker,
                        "reason": x.reason,
                        "period_end": x.period_end.isoformat(),
                        "filed": x.filed.isoformat(),
                        "value": x.value,
                        "reference": x.reference,
                        "accession": x.accession,
                    }
                    for f in getattr(r, "fundamentals", ()) or ()
                    for x in getattr(f, "screened", ()) or ()
                ],
            }
            for r in results
        ],
    }


def _onboarding_rows(names: Sequence) -> list[dict]:
    """X1 — one row per security still onboarding (deduped: a name in N segments is N basket rows)."""
    out: dict[str, dict] = {}
    for x in names:
        status = getattr(x, "onboarding", None)
        if not status:
            continue
        since = getattr(x, "filed_since", None)
        out.setdefault(
            str(x.security_id),
            {
                "security_id": str(x.security_id),
                "ticker": x.ticker,
                "status": status,
                "filed_since": since.isoformat() if since else None,
                # past the pass's onboarding cap: listed, not ingested at all tonight
                "deferred": bool(getattr(x, "onboarding_deferred", False)),
            },
        )
    return list(out.values())


def fundamentals_screened(doc: dict) -> dict:
    """The fundamentals data-quality screen's record on one run artifact: ``{count, by_reason, rows}``,
    summed over the theses. Read with ``.get`` throughout (the G4 rule): an artifact written before the key
    existed — or a thesis entry without it — reads as nothing screened, never a raise."""
    rows = [
        x
        for t in (doc.get("theses") or [])
        if isinstance(t, dict)
        for x in (t.get("fundamentals_screened") or [])
        if isinstance(x, dict)
    ]
    by_reason: dict[str, int] = {}
    for x in rows:
        reason = str(x.get("reason") or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {"count": len(rows), "by_reason": by_reason, "rows": rows}


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
    tape_stale_new: tuple[StaleFeedLabel, ...] = (),
    tape_stale_days: int = 0,
    fund_shares_stale_days: int = 0,
    code_sha: str | None = None,
    config_hash: str | None = None,
    run_kind: str | None = None,
    env: dict | None = None,
    name_errors_evaluated: bool = False,
    name_errors_new: Sequence[NameIngestError] = (),
    name_errors_known: int = 0,
    audit: Sequence[AuditResult] = (),
    split: SplitScan | None = None,
    rename_probe: RenameProbe | None = None,
    successor: SuccessorScan | None = None,
    skips: SkipDiff | None = None,
    timings: dict | None = None,
    amendments: AmendmentScan | None = None,
    edgar_coverage: EdgarCoverage | None = None,
    split_detect: Any = None,
    missing_closes: MissingCloses | None = None,
) -> Path | None:
    """Dump one cron pass (``build_run_payload``, above — the payload's meaning lives there) to
    ``<base>/<utc-timestamp>.json``; return the path (or ``None`` fail-open). The whole write — payload
    build included — stays inside the fail-open try: a run-log fault is a logged exception, never a
    failed cron. M1: the path is the started-at instant, so this FINALIZES (atomically overwrites) the
    start artifact ``write_run_start`` left for the same pass. If THIS write fails, the start file stays
    ``running`` — the guard then lets a catch-up re-run the night (safe: ``record_if_changed``) and the Admin
    page reads "did not finish" after the run timeout: loud and recoverable, never a silent credit.
    """
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
            tape_stale_days=tape_stale_days,
            fund_shares_stale_days=fund_shares_stale_days,
            code_sha=code_sha,
            config_hash=config_hash,
            run_kind=run_kind,
            env=env,
            name_errors_evaluated=name_errors_evaluated,
            name_errors_new=name_errors_new,
            name_errors_known=name_errors_known,
            audit=audit,
            split=split,
            rename_probe=rename_probe,
            successor=successor,
            skips=skips,
            timings=timings,
            amendments=amendments,
            edgar_coverage=edgar_coverage,
            split_detect=split_detect,
            missing_closes=missing_closes,
        )
        run_dir = base_dir or _DEFAULT_CRON_RUNS
        run_dir.mkdir(parents=True, exist_ok=True)
        path = _artifact_path(run_dir, started_at)
        _write_atomic(path, payload)
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract: log, never fail the cron
        _log.exception("cron run log write failed (fail-open — the cron run is unaffected)")
        return None


def _recorded_for_some_thesis(doc: dict) -> bool:
    """Did this artifact's pass get as far as the CALL step for at least one thesis (G2b)?

    ``True`` when some per-thesis entry's ``recorded`` is a **bool** — ``True`` (a new call-of-record was
    appended) or ``False`` (unchanged, so no row, which is the common healthy-quiet outcome and absolutely
    counts). ``None`` there means the call step FAILED for that thesis, and a ``withheld_reason`` means it
    never ran at all (a cache-only pass, or a total ingest failure), so an artifact where EVERY thesis reads
    ``None`` is a pass that recorded nothing.

    Why the guard needs this: ``already_ran_live`` used to credit any live post-``RUN_AT`` artifact
    *regardless of health*, so a pass that completed with every thesis withheld or errored — the DB down
    after connect, the User-Agent empty so every name errored — left behind an artifact that blocked both the
    retry and a later boot catch-up. The night then had no honest post-close row and nothing would ever try
    again.

    Fail-open toward RUNNING, like every other branch in the guard: a missing, non-list or malformed
    ``theses`` entry reads as "no evidence", so a damaged or pre-schema artifact lets the catch-up run. A
    repeated run is safe (``record_if_changed`` appends nothing on unchanged facts); a skipped one is the
    silent gap. Note the corollary for a fresh install: an artifact with ZERO theses credits nothing, which
    is correct — a pass over no theses recorded nothing — and the re-run is instant.

    ``isinstance(..., bool)`` on purpose, not ``in (True, False)``: in Python ``1 in (True, False)`` is True,
    so an integer that wandered into the field would be mistaken for an outcome.

    M2 — a thesis a ``--catch-up`` SKIPPED (a non-empty ``skipped`` reason: its on-time row already stands)
    counts too: the night HAS that record. The live twin ``daily._failed_to_record`` reads it the same way.
    """
    theses = doc.get("theses")
    if not isinstance(theses, list):
        return False
    return any(
        isinstance(t, dict)
        and (
            isinstance(t.get("recorded"), bool)
            or (isinstance(t.get("skipped"), str) and t["skipped"])
        )
        for t in theses
    )


def is_full_roster(doc: dict) -> bool:
    """Did this artifact's pass process EVERY thesis on its roster? ``False`` when a ``--catch-up`` SKIPPED
    some (M2 — their on-time rows stood, so they were never ingested; ``thesis_covered`` reads one entry).
    Such a pass is never a baseline ON ITS OWN — ``carried_inventory`` (below) reads it only for the theses
    it covered, carries each skipped thesis forward from the last pass that did cover it, and ends its
    look-back at the newest full-roster pass — because its
    stale-tape and name-error inventories lack the skipped theses' names, so diffing the next pass against
    it would re-page every known-dead feed and known-failing name those theses hold as if new (and the
    Admin freshness panel would show a partial inventory). A pre-M2 artifact has no ``skipped`` keys and
    reads full. (The SPLIT baseline does not apply this: its scan reads the whole basket union, so a
    partial catch-up's split inventory is complete — ``previous_split_defects``.)"""
    theses = doc.get("theses")
    if not isinstance(theses, list):
        return True
    return not any(isinstance(t, dict) and t.get("skipped") for t in theses)


def already_ran_live(asof: date, *, run_at: time, tz: tzinfo, base_dir: Path | None = None) -> bool:
    """Did a LIVE cron pass for ``asof`` already run **for the night** — one that STARTED at or after
    ``asof``'s ``run_at`` in market time? The catch-up guard (R6): a sidecar that boots after the scheduled
    time, or wakes late, must catch the missed run up — but ONLY if it truly hasn't run, or every rebuild
    would re-fire the whole job (and R3 said the run log is the memory that answers this).

    ``mode == "live"`` is load-bearing: a ``--no-live`` dev run writes a run log too, but it must NOT count as
    "the night ran" — otherwise a hand-run ``--no-live`` (like the R4 page test) would suppress the real
    nightly catch-up, and the real run would silently never happen.

    **And the pass must have RECORDED for some thesis (G2b).** The guard used to credit a live post-``run_at``
    artifact regardless of health, so a pass that COMPLETED with every thesis withheld or errored blocked both
    the sidecar's retry and any later boot catch-up — the night kept no honest post-close row and nothing ever
    tried again. ``_recorded_for_some_thesis`` (above) is that test; it is deliberately generous (``recorded``
    False — unchanged, no row — counts, because that is the common healthy-quiet night).

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

    **And the pass must have FINISHED (M1, 2026-10-16) — THE GUARD TRAP.** A pass now writes a START
    artifact (``status: "running"``) the moment it holds the lock and finalizes it at the end; a pass killed
    mid-loop leaves the ``running`` file behind (MEASURED shape: 10-12, the VM died at 22:42 with 4 of 12
    theses recorded). Crediting that file would make the killed night BLOCK ITS OWN RECOVERY — the retry and
    every later catch-up would no-op over a partial night. So only ``is_finished`` counts: ``"finished"``, or
    a pre-M1 artifact with no ``status`` at all (it was only ever written at the end). ``running``,
    ``crashed`` and any unknown status read as "did not run" — the catch-up re-runs the night, and
    ``record_if_changed`` keeps the theses that DID record from appending twice. The G2b test (some thesis
    recorded) still applies on top, to finished artifacts. The explicit status check is the rule; that a
    start artifact also carries no ``theses`` list is only belt-and-suspenders.

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
        if not is_finished(doc):
            continue  # M1 — a running / crashed pass never credits the night (the guard trap)
        try:
            started = datetime.fromisoformat(doc["started_at"])
        except (KeyError, TypeError, ValueError):
            continue  # no honest start instant -> not evidence the night ran
        if started.tzinfo is None or started.utcoffset() is None:
            continue  # a naive stamp cannot be compared against a market-time cutoff
        if started >= cutoff and _recorded_for_some_thesis(doc):
            return True
    return False


def thesis_covered(entry: dict) -> bool:
    """Did the pass that wrote this per-thesis ``entry`` COVER that thesis — ingest it, so its stale feeds and
    its failing names are an OBSERVATION? ``False`` only for a thesis a ``--catch-up`` SKIPPED (M2 — a
    ``skipped`` reason: its on-time row stood, so nothing was ingested and its inventories are empty by
    construction, not because anything cleared). Coverage comes from the artifact alone. An artifact written
    before M2 carries no ``skipped`` keys, so it covers exactly the theses its per-thesis results list — and
    no other: a thesis it does not list was not on that pass's roster. Truthiness, the ``is_full_roster``
    reading, so a pass is full-roster exactly when it covers every thesis it lists."""
    return not entry.get("skipped")


@dataclass(frozen=True)
class CarriedInventory:
    """The per-thesis inventory a baseline reads (``carried_inventory``): for every thesis on the NEWEST
    qualifying pass's roster, its entry from the newest qualifying pass that COVERED it.

    ``passes`` are the qualifying passes read, newest first — from the newest one back to (and including) the
    newest FULL-roster one, where the look-back ends. ``theses`` pairs each carried per-thesis entry with the
    index (into ``passes``) of the pass it came from, in the newest pass's roster order."""

    passes: tuple[dict, ...]
    theses: tuple[tuple[dict, int], ...]

    @property
    def newest(self) -> dict:
        """The newest qualifying pass: the roster, the night the inventory is "as of", what it paged."""
        return self.passes[0]

    def entries(self) -> list[dict]:
        """The carried per-thesis entries (each its own pass's ``theses[]`` element, unmodified)."""
        return [t for t, _ in self.theses]

    def as_doc(self) -> dict:
        """The newest pass's payload with its ``theses`` replaced by the carried entries — the view an
        artifact-shaped reader (the Price feed repairs panel) walks. Built in memory, never written.
        """
        return {**self.newest, "theses": self.entries()}


def _inventory_pass(doc: dict, evaluated_key: str) -> bool:
    """Can ``doc`` contribute to an inventory baseline? A LIVE pass (a ``--no-live`` pass reads cache misses,
    not tonight's feed), that EVALUATED this inventory (``evaluated_key`` — a pre-monitor artifact, or a
    pass with the monitor off, carries none: read as "nothing flagged" it would re-page everything known) and
    FINISHED (M1 — a killed pass's start artifact is never a baseline)."""
    return doc.get("mode") == "live" and bool(doc.get(evaluated_key, False)) and is_finished(doc)


def carried_inventory(payloads: Sequence[dict], *, evaluated_key: str) -> CarriedInventory | None:
    """THE PER-THESIS CARRY-FORWARD (2026-10-17) — the ONE rule the stale-tape and name-error baselines
    (``previous_stale_tapes`` / ``previous_name_errors``) AND the Admin panels that render those inventories
    (``app/routers/admin.py::_tape_out``, ``admin_price_feed.inventory_doc``) read, so a name the panel
    lists never re-pages and the panel marks NEW exactly what paged. ``payloads`` are parsed artifacts
    NEWEST-FIRST (``list_run_logs``); ``evaluated_key`` is the inventory's evaluated flag
    (``tape_evaluated`` / ``name_errors_evaluated``).

    Both inventories are PER THESIS — every entry belongs to a thesis the pass ingested — and an M2
    ``--catch-up`` covers only the theses whose on-time row was missing. So the baseline is, for each thesis
    on the NEWEST qualifying pass's roster (full or partial), its entry from the newest qualifying pass that
    COVERED it (``thesis_covered``); a thesis that pass skipped keeps its entry from the last pass that did
    cover it. The union is the last-known inventory of every thesis. Consequences, each pinned in
    ``tests/pipeline/test_catchup_baseline.py``:

    - a name FIRST flagged by a catch-up pages there ONCE (the next pass reads the catch-up's entry for that
      thesis). The old FULL-ROSTER rule skipped the catch-up, so the next full pass diffed against the pass
      before it and paged the name AGAIN;
    - a name the catch-up saw CLEAR and that comes back pages again — a genuine clear-and-return (the old
      rule still held it from the pass before the catch-up, so its return stayed silent);
    - a name in a thesis the catch-up SKIPPED never re-pages (that thesis's entry is carried).

    BOUNDS. The look-back ENDS at the newest qualifying FULL-roster pass (``is_full_roster``): it covered every
    thesis then active, so nothing older is newer evidence about any of them, and a thesis it did not list
    (created later, or archived then) has no older entry to carry. The ROSTER is the newest qualifying pass's
    (a catch-up lists its skipped theses too), so a thesis no longer listed — archived — takes its entries
    with it and can never mask a name elsewhere.

    FAIL LOUD. ``None`` = no qualifying pass at all (the first pass after a monitor ships) — the caller pages
    the whole inventory once. A thesis on the roster that no readable qualifying pass in the look-back
    covered (a new thesis skipped by a catch-up, or its covering artifact unreadable — ``list_run_logs``
    skips it) contributes NOTHING, so each of its names reads as news: a repeat page at worst, never a
    silenced one. A roster entry without an id is used only when the newest pass covered it.

    KNOWN LIMIT (accepted): the artifact records a thesis's FLAGGED names, not its members, so when one name
    is held by a thesis the catch-up skipped AND by one it covered and saw clear, the skipped thesis's
    carried entry still holds it — if it comes back by the next pass, that return does not page. The full
    pass after that reads both theses fresh. Pure over its input — no I/O."""
    passes: list[dict] = []
    for doc in payloads:
        if not isinstance(doc, dict) or not _inventory_pass(doc, evaluated_key):
            continue
        passes.append(doc)
        if is_full_roster(doc):
            break  # a full roster covered every thesis then active: nothing older is newer evidence
    if not passes:
        return None
    slots: list[tuple[dict, int] | None] = []
    pending: dict[str, int] = {}  # thesis id -> its slot, still owed an older covering pass
    roster = passes[0].get("theses")
    for t in roster if isinstance(roster, list) else []:
        if not isinstance(t, dict):
            continue
        if thesis_covered(t):
            slots.append((t, 0))
            continue
        tid = t.get("id")
        if isinstance(tid, str) and tid and tid not in pending:
            pending[tid] = len(slots)
            slots.append(None)
    for i, doc in enumerate(passes[1:], start=1):
        if not pending:
            break
        older = doc.get("theses")
        for t in older if isinstance(older, list) else []:
            if not isinstance(t, dict) or not thesis_covered(t):
                continue
            tid = t.get("id")
            if isinstance(tid, str) and tid in pending:
                slots[pending.pop(tid)] = (t, i)
    return CarriedInventory(passes=tuple(passes), theses=tuple(s for s in slots if s is not None))


def previous_stale_tapes(*, base_dir: Path | None = None) -> set[str] | None:
    """The set of ``"<kind>:<security_id>"`` keys whose feed was STALE — and NOT classified closed — in the
    last-known inventory of every thesis: the baseline the current pass diffs against so only NEWLY stale
    feeds page (G5a/F1). Closed rows are left out on purpose (T1): they never paged, so a name reclassified
    closed → repair must read as news and page once.

    WHICH entries: the PER-THESIS CARRY-FORWARD (``carried_inventory``, its rule and bounds live there) —
    each thesis's entry from the newest finished, live, recency-evaluated pass that COVERED it, so a
    ``--catch-up`` that skipped theses (M2) counts for the theses it did cover. The Admin freshness panel and
    the Price feed repairs panel read the same carried inventory.

    ``None`` (distinct from an empty set!) = **no evaluated live artifact exists yet** — the first pass after
    this monitor deployed, or a run home with nothing in it. The caller treats that as "everything currently
    stale is news" so the operator gets the inventory ONCE, loudly, instead of a silent first night; an empty
    SET means a pass did look and found nothing stale, so a name appearing now is genuinely new.

    Two filters, both load-bearing. ``mode == "live"``: a ``--no-live`` pass cannot see a tape's current edge
    honestly and must never become the baseline, or a hand-run cache-only pass would silence the next real
    page. ``tape_evaluated``: an artifact written BEFORE this monitor existed, or by a pass with the monitor
    disabled (``tape_stale_days = 0``), carries no stale inventory — reading its absent list as "nothing was
    stale" would make every already-known dead tape page again as if new.

    THE KEY IS ``kind:security_id`` (F1), and a row with NO ``kind`` counts as ``price``. That default is
    load-bearing, not defensive: every artifact written before the fund-shares feed existed carries rows
    without the field, and they ARE price rows — read any other way, the first pass after this deploys would
    find none of its known-dead price tapes in the baseline and re-page the entire existing inventory as if
    new. Keying on the kind as well as the id is what lets one name go stale on its price tape now and its
    fund-shares sample later and have the second page as the news it is.

    Fail-open PER ARTIFACT like its siblings (an unreadable or malformed file is skipped, never an
    exception) — and the whole read is best-effort: a thesis no readable pass covered contributes nothing,
    so the caller's worst case is a repeat page, never a missed one. Pure file read — no DB, no network,
    nothing written."""
    inv = carried_inventory(list_run_logs(base_dir=base_dir), evaluated_key="tape_evaluated")
    if inv is not None:
        out: set[str] = set()
        for t in inv.entries():
            for s in t.get("tape_stale") or []:
                # T1 — a CLOSED row (`closed_at` set) is NOT in the baseline: it never paged, so the
                # operator was never told about it as a feed gap. The current side of the diff excludes
                # closed rows too (`run_daily_pass`), so a name RECLASSIFIED closed → repair finds no
                # matching key here and pages ONCE as newly stale — VRME's shape, the first night the
                # proximity rule ships. Without this skip, its silenced night would count as "already
                # told" and the reclassification would never page.
                # Q8 — an ACKNOWLEDGED unpriceable row is out of the baseline for the same reason: it never
                # paged (the operator said "keep it, unpriced"), so if the acknowledgment is REVOKED the
                # name finds no key here and pages ONCE as newly stale — the operator asked for it loud.
                if (
                    isinstance(s, dict)
                    and s.get("security_id")
                    and not s.get("closed_at")
                    and not s.get("acknowledged")
                ):
                    out.add(f"{feed_kind(s.get('kind')).key}:{s['security_id']}")
        return out
    return None


def previous_name_errors(*, base_dir: Path | None = None) -> set[str] | None:
    """The ``"<security_id>:<leg>"`` keys of every name that ERRORED in the last-known inventory of every
    thesis — the baseline the current pass diffs against so only NEWLY failing names page (O1), the exact
    twin of ``previous_stale_tapes`` above and read through the SAME per-thesis carry-forward
    (``carried_inventory``): each thesis's entry from the newest finished, live, name-error-evaluated pass
    that COVERED it, so a ``--catch-up`` that skipped theses (M2) counts for the theses it did cover.

    ``None`` (distinct from an empty set) = no evaluated artifact yet — the first pass after O1 deploys. The
    caller treats that as "everything currently failing is news", so the known inventory pages ONCE instead
    of the first night being silent. ``mode == "live"`` AND ``name_errors_evaluated`` are both required: a
    ``--no-live`` pass's errors are cache misses, not tonight's feed, and an artifact written before O1
    carries no per-name rows — read as "nothing errored", it would re-page every known-dead name as new.
    Fail-open per artifact (a malformed row is skipped). Pure file read.
    """
    inv = carried_inventory(list_run_logs(base_dir=base_dir), evaluated_key="name_errors_evaluated")
    if inv is not None:
        out: set[str] = set()
        for t in inv.entries():
            for e in t.get("name_errors") or []:
                if isinstance(e, dict) and e.get("security_id") and e.get("leg"):
                    out.add(f"{e['security_id']}:{e['leg']}")
        return out
    return None


def previous_ingest_skips(*, base_dir: Path | None = None) -> dict[str, set[str]] | None:
    """Q18 — ``kind -> {accession}`` for every filing a leg SKIPPED in the last-known inventory of every
    thesis: the baseline that makes a filing "known" (counted, not re-printed) tonight. The twin of
    ``previous_name_errors``, read through the SAME per-thesis carry-forward (``carried_inventory`` with
    ``ingest_skips_evaluated``): each thesis's ``ingest_skips`` from the newest finished, live, skip-evaluated
    pass that COVERED it. So a filing FIRST skipped by a ``--catch-up`` prints there once and is known at the
    next full pass, and a thesis a catch-up SKIPPED keeps its filings from the last pass that walked it (the
    full-roster rule this replaced read past the catch-up, and re-printed what it had printed).

    FALL BACK LOUD, never silent. ``None`` (distinct from an empty dict) = no qualifying artifact yet, OR no
    carried entry carries a per-thesis ``ingest_skips`` list (every one predates the attribution — the first
    Q18 build recorded the inventory only run-level): the caller treats that as "nothing is known", so every
    skip prints once. A carried entry WITHOUT the list (one thesis carried from such an artifact) contributes
    nothing, so ITS filings read as new and print once — a repeat line at worst, never a muted one. Fail-open
    per artifact and per row (a malformed row is skipped, never an exception). Pure file read."""
    inv = carried_inventory(
        list_run_logs(base_dir=base_dir), evaluated_key="ingest_skips_evaluated"
    )
    if inv is None:
        return None
    out: dict[str, set[str]] = {}
    attributed = False
    for t in inv.entries():
        rows = t.get("ingest_skips")
        if not isinstance(rows, list):
            continue  # pre-attribution entry: unknown, so its filings read as new (print once)
        attributed = True
        for r in rows:
            if isinstance(r, dict) and r.get("kind") and r.get("accession"):
                out.setdefault(str(r["kind"]), set()).add(str(r["accession"]))
    return out if attributed else None


def previous_split_defects(*, base_dir: Path | None = None) -> set[str] | None:
    """The ``"<kind>:<security_id>"`` keys of every split-basis defect (T4, ``pipeline/split_basis.py``) on
    the most recent pass that EVALUATED the scan — the baseline the current pass diffs against so only NEW
    defects page, the twin of ``previous_stale_tapes`` / ``previous_name_errors``: a LIVE pass
    (``--no-live`` never re-pulled a tape), ``split_evaluated`` (a pre-T4 artifact or a pass whose scan
    FAILED carries no inventory — read as "nothing was flagged" it would re-page every known defect) and
    FINISHED (M1 — a killed pass's start artifact is never a baseline).

    A PARTIAL ``--catch-up`` (M2 — it skipped the theses whose on-time row stood) IS a baseline here IN
    WHOLE — the two twins count it only for the theses it covered, because their inventories are per thesis
    (the per-thesis carry-forward, ``carried_inventory``) — because its split inventory is COMPLETE: the
    scan reads the whole active basket union
    from the table (``scan_split_defects``), never the processed theses' names, so a skipped thesis's names
    are in it. Excluding it (the T4 rule until 2026-10-17) re-paged a defect the catch-up had already paged
    — the next full pass diffed against the pass BEFORE the catch-up — and, the other way, kept a defect the
    catch-up saw CLEARED in the baseline, so it did not page when it came back. With the catch-up as the
    baseline a defect pages once, and again only after a pass genuinely found it gone.

    ``None`` (distinct from an empty set) = no qualifying artifact yet — the first pass after T4 deploys —
    which the caller treats as "everything flagged is news", so the inventory pages ONCE. Newest-first,
    first match wins; fail-open per artifact. Pure file read."""
    for doc in list_run_logs(base_dir=base_dir):
        if (
            doc.get("mode") != "live"
            or not doc.get("split_evaluated", False)
            or not is_finished(doc)
        ):
            continue
        return defect_keys_of(doc)
    return None


def previous_settle_audit_end(*, base_dir: Path | None = None) -> datetime | None:
    """The ``finished_at`` of the newest FINISHED pass whose self-audit JUDGED the settle check
    (``self_audit.SETTLE_CHECK``, ``ok`` true or false) — where tonight's settle window starts
    (``self_audit.settle_window_since``), so each stored bar is judged by one pass and a violation pages once.

    Skipped, newest first: a pass that did not finish (M1 — a killed pass's audit never ran); a pass whose
    audit did not carry the check (every artifact written before it shipped) or found it not applicable;
    and a pass where the check RAISED (``ok`` false with ``self_audit.COULD_NOT_RUN``'s detail) — it judged
    nothing, so its window is still owed. Any mode counts: the check judges the table, not the pass.
    ``None`` = no such pass (the caller falls back to a bounded lookback). Fail-open per artifact, like its
    siblings (an unreadable file, or a ``finished_at`` that is missing / unparseable / naive, is skipped —
    the window then reaches further back: a repeat judgment at worst, never a gap). Pure file read.
    """
    from pipeline.self_audit import (  # lazy: keep the import graph one-way
        COULD_NOT_RUN,
        SETTLE_CHECK,
    )

    for doc in list_run_logs(base_dir=base_dir):
        if not is_finished(doc):
            continue
        audit = doc.get("audit")
        judged = any(
            isinstance(a, dict)
            and a.get("check") == SETTLE_CHECK
            and isinstance(a.get("ok"), bool)
            and not str(a.get("detail") or "").startswith(COULD_NOT_RUN)
            for a in (audit if isinstance(audit, list) else ())
        )
        if not judged:
            continue
        try:
            end = datetime.fromisoformat(doc["finished_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if end.tzinfo is None or end.utcoffset() is None:
            continue
        return end
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
