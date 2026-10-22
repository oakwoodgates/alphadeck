"""``pipeline.backfill`` — record a MISSED night's call-of-record, reconstructed on the PUBLIC clock: a
reconstruction that is faithful on ONE axis (the facts: what was PUBLIC by the end of that night), enforced on a
second (thesis existence), and NOT reconstructable on a third (basket composition). Read the honesty note
before trusting a row.

The cron sidecar missed nights (a laptop-sleep drift, closed by the target-at-schedule-time fix in
``scripts/daily_cron.sh``). The ``calls`` log is immutable and bitemporal: a row's ``recorded_at`` is
always now. A naive backfill — ``python -m pipeline.daily --asof <past>`` — computes the past night with
``known_at = now`` on the record clock, i.e. TODAY's knowledge: MEASURED on dev, a thesis backfilled as ARMED
on nights whose real nightly neighbors recorded INCUBATING, because its facts arrived AFTER those nights. This
module reconstructs the night on the PUBLIC clock instead (operator decision 2026-10-21, ``docs/INVARIANTS.md``
§4): every fact counts from when it became public — a Form 4 from its SEC acceptance, a bar from its settled
session, an 8-K / 13D from its filing, a correction from its OWN publication — up to the end of that night's
MARKET day (``domain.market_time.known_at_for_asof``). So the row is how TODAY's rules judge what was PUBLIC
that night: a filing published later can never leak in, and a fact our database happened to ingest late (an
outage, a name onboarded afterwards) is not hidden for it. The assembly is ``call_for_thesis(clock="public")``;
this CLI adds nothing to it.

**The three axes of a reconstruction — what a reconstructed row IS and IS NOT (the honesty note).**

1. **The facts — the public clock, faithful.** Nothing published after the night leaks in. (The record-clock
   ``--known-at`` pin this CLI used to take — "what our database held at the next live run" — is RETIRED: it
   replayed our own ingest history, not the market's. ``--known-at`` is refused with that explanation.)
2. **Thesis existence — ENFORCED here.** A thesis created AFTER the night was not in that night's cron,
   so it gets NO row: ``run_backfill`` skips it (``thesis_existed_on``: ``thesis.created_at`` in market
   time vs the as-of day — a thesis created during the day of ``asof`` WAS in that night's run) and
   reports the skip LOUDLY, in the per-run summary, in ``--dry-run``, and in the provenance artifact —
   never silently (recall is sacred; a dropped thesis is visible, not vanished). MEASURED before this gate
   existed: 37 of 144 reconstructed rows on dev were dated before their thesis's creation, 20 of them
   warming/armed, two of them arm episodes for a thesis that did not exist.
3. **Basket composition — read as of the night where a snapshot exists, otherwise NOT reconstructable.** The
   roster and the operator's logged position are OPERATOR decisions, read as of the end of the night
   (``basket_snapshot`` since 2026-09-15; before that, TODAY's roster stands in). A reconstructed row can
   therefore never be shown to be the night's record, and the Scoreboard's record path scores NONE of them:
   every row this module writes carries ``calls.reconstructed = true`` (migration 0042), and
   ``scoreboard/record.py`` reads with ``include_reconstructed=False`` — a reconstructed row never opens or
   closes an arm episode; it is reported (the ledger banner names the nights), not scored.

    python -m pipeline.backfill --asof 2026-09-08
    python -m pipeline.backfill --asof 2026-09-08 --thesis <uuid> --dry-run

Discipline (``daily.run_daily``'s, minus everything that is not a recompute):
- **A pure recompute-and-record over facts already in the store.** NO ingest, NO refresh legs, NO SPAC legs,
  NO notifier — the module imports none of them (pinned structurally by the import guard in
  ``tests/pipeline/test_backfill.py``). No ``TransitionEvent`` either: a reconstructed row is a RECORD, never
  a nag — a Slack "ARMED" for a night two weeks ago would be exactly the wrong loudness (#7).
- **Per-thesis isolation** — own try, commit-on-success / rollback-on-failure; one thesis's failure is
  captured and the rest still record.
- **Idempotent** — ``record_if_changed``'s canonical compare: a re-run over the same facts appends ZERO rows.
  A late ingest of a filing that was public before the night is a genuine change and appends one.
- **The markers.** ``reconstructed = true`` on every row written here — the explicit provenance the
  Scoreboard filters on (the cron never sets it). ``ingest_fresh`` / ``ingest_errors`` stay ``None``: there
  is no ingest in a backfill, NULL is the honest stamp. ``run_kind = 'backfill'`` (0044) rides beside them:
  the SCORING filter and the RUN IDENTITY are two different questions about the same row, and both are
  answered explicitly rather than one being derived from the other.
- **Provenance, write-only, fail-open** — one JSON per invocation under ``data/backfills/``
  (``pipeline/backfill_log.py``), skips included, carrying the cap the night was judged at (``known_at``)
  and ``known_at_policy = "public-clock"``. It is NOT a cron run artifact: ``already_ran_live`` stays False
  for the night. A ``--dry-run`` writes NOTHING — neither a row nor an artifact.
- **One writer at a time (C1).** A real (non-dry) run takes the SAME cross-process call-of-record lock
  as the nightly pass (``pipeline/run_lock.py``) for its whole write: a backfill and a daily pass never run
  ``record_if_changed`` against each other. Another holder refuses the run before any row is written: one
  loud line, exit ``EXIT_ALREADY_RUNNING`` (75). A ``--dry-run`` writes nothing and takes no lock.
- **Refusals, all loud (exit 2), all BEFORE a connection is opened:** ``asof >= market_today()`` (tonight is
  the cron's job); the retired ``--known-at``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path
from uuid import UUID

import psycopg

from db.session import connect
from domain.config import DEFAULT_CONFIG, config_hash
from domain.market_time import known_at_for_asof, market_today, market_tz
from domain.settings import get_settings
from pipeline.backfill_log import write_backfill_log
from pipeline.call_for_thesis import call_for_thesis
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock
from repositories import calls_repo, thesis_repo

# the provenance artifact's ``known_at_policy``: the night is judged on the PUBLIC clock, capped at the end of
# its market day (the retired record-clock pins were ``explicit`` / ``next-run``)
PUBLIC_CLOCK_POLICY = "public-clock"


@dataclass
class BackfillResult:
    """Per-thesis outcome. ``recorded``: True = a reconstructed call-of-record was appended, False = an
    identical row was already logged for this as-of (no row), None = dry-run, skipped, or the call step
    failed (see ``skipped`` / ``error``). ``skipped``: the reason the thesis got NO row (it did not exist
    on the night) — reported, never silent. ``prior_state`` / ``prior_verdict``: the row already logged
    for this as-of before the run (None = the night had no row — the missed-night case)."""

    thesis_id: UUID
    name: str
    state: str | None = None
    verdict: str | None = None
    armed: int = 0
    prior_state: str | None = None
    prior_verdict: str | None = None
    recorded: bool | None = None
    skipped: str | None = None
    error: str | None = None


# --- the existence gate (pure; no DB, no clock) ----------------------------------------------------


def thesis_existed_on(created_at: datetime, asof: date, tz: tzinfo) -> bool:
    """Did a thesis created at ``created_at`` exist on the night of ``asof``? PURE, and the ONE rule both
    the backfill's existence gate and ``pipeline.repair_reconstructed_precreation`` apply, so the two
    tools cannot disagree. Market time, by the calendar day: a thesis created at any point DURING the
    as-of day was in that night's cron (the cron fires at 22:30 market time, after the day's work), so
    it existed; one created 00:00:00 the next market day did not. ``created_at`` must be aware (the
    column is ``timestamptz``); a naive instant is refused — the same discipline as the pin."""
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware — an instant, never a wall time")
    return created_at.astimezone(tz).date() <= asof


# --- the run -----------------------------------------------------------------------------------------------


def reconstruction_known_at(asof: date) -> datetime:
    """The instant a missed night is judged at: the END of ``asof``'s MARKET day (``known_at_for_asof``) —
    on the PUBLIC clock, "everything public by the end of that night" (the night's settled bar, every filing
    EDGAR accepted that day — it accepts until 22:00 ET, before the 22:30 nightly). The roster and position
    (operator decisions) are read as of the same instant."""
    return known_at_for_asof(asof)


def run_backfill(
    conn: psycopg.Connection,
    *,
    asof: date,
    thesis_id: UUID | None = None,
    dry_run: bool = False,
) -> list[BackfillResult]:
    """Reconstruct the call-of-record for ``asof`` on the PUBLIC clock (``reconstruction_known_at``) for every
    non-archived thesis (``thesis_repo.list_all``, the cron's set) or the one named — exactly ``daily.run_daily``'s per-thesis
    isolation: each thesis in its own try, commit on success, rollback on failure, never fatal to the run.
    A thesis that did NOT exist on the night (``thesis_existed_on``) is SKIPPED before any assembly — no
    row, no compare — and the skip is carried on its result (``skipped``), so the report and the
    provenance artifact both say so. ``dry_run`` assembles and reports (skips included) but writes
    NOTHING (the read transaction is rolled back). Raises ``LookupError`` for an unknown ``thesis_id``
    (before any thesis runs)."""
    known_at = reconstruction_known_at(asof)
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis not found: {thesis_id}")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)
    tz = market_tz()
    created = thesis_repo.created_at_for(conn, [t.id for t in theses])
    # ONE explicit cfg for the whole reconstruction, used for BOTH the assemble and its fingerprint (0044) —
    # the same honesty rule `daily.run_daily` holds: the stamp must follow the cfg that produced the card,
    # never a separately-hashed DEFAULT_CONFIG that merely happens to match today.
    cfg = DEFAULT_CONFIG
    cfg_hash = config_hash(cfg)
    code_sha = get_settings().image_sha
    out: list[BackfillResult] = []
    for thesis in theses:
        res = BackfillResult(thesis_id=thesis.id, name=thesis.name)
        # THE EXISTENCE GATE (axis 2): a thesis created after the night was not in that night's cron.
        # Decided before any read of the night, reported loudly, never silent. A missing created_at
        # (impossible for a loaded row — the column is NOT NULL) is treated as "existed" so the gate can
        # never drop a thesis on a data surprise (recall over precision).
        born = created.get(thesis.id)
        if born is not None and not thesis_existed_on(born, asof, tz):
            res.skipped = (
                f"did not exist on {asof} — created {born.astimezone(tz).date()} (market time)"
            )
            out.append(res)
            continue
        try:
            # the row this night already has (the missed-night case has none) — reported, never a gate:
            # record_if_changed does its own canonical compare against exactly this row
            prior = next(
                (c for c in calls_repo.latest_for_thesis(conn, thesis.id) if c.asof == asof), None
            )
            if prior is not None:
                res.prior_state, res.prior_verdict = prior.state.value, prior.verdict.value
            # THE reconstruction: the same assembly the cron runs, judged on the PUBLIC clock at the end of
            # the night. record=False — the append below is the ONLY write, and it is the idempotent one.
            card = call_for_thesis(
                conn, thesis.id, asof, known_at=known_at, clock="public", cfg=cfg, record=False
            )
            res.state, res.verdict = card.state.value, card.verdict.value
            res.armed = len(card.armed_members)
            if dry_run:
                conn.rollback()  # nothing to keep — end the read transaction cleanly
            else:
                # reconstructed=True: the explicit marker the Scoreboard's record path filters on (0042).
                # ingest_fresh / ingest_errors stay None ON PURPOSE: there was no ingest (the NULL stamp)
                # run_kind='backfill' (0044) is the PROVENANCE twin of that marker, not a duplicate of it:
                # `reconstructed` is the Scoreboard's scoring FILTER, `run_kind` is the run's identity, and
                # both being written here is what keeps the two readings of this row consistent.
                res.recorded = calls_repo.record_if_changed(
                    conn,
                    card,
                    thesis.tenant_id,
                    reconstructed=True,
                    config_hash=cfg_hash,
                    code_sha=code_sha,
                    run_kind="backfill",
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001 — one thesis's failure never aborts the backfill
            conn.rollback()
            res.error = f"call: {e}"
        out.append(res)
    return out


@dataclass
class BackfillPassOutcome:
    """One COMPLETED backfill invocation: the per-thesis results + the run metadata the artifact carries.
    ``log_path`` is the written provenance artifact, or ``None`` (a dry run, or the fail-open write).
    """

    results: list[BackfillResult]
    asof: date
    known_at: datetime
    known_at_policy: str
    dry_run: bool
    started_at: datetime
    finished_at: datetime
    log_path: Path | None


def run_backfill_pass(
    *,
    asof: date,
    thesis_id: UUID | None = None,
    dry_run: bool = False,
    log_dir: Path | None = None,
) -> BackfillPassOutcome:
    """The backfill's FULL unit of work: connect → ``run_backfill`` → write the provenance artifact
    (fail-open; SKIPPED on a dry run — a dry run writes nothing anywhere). ``main`` keeps the CLI concerns
    only (args, the refusals, the printed report + exit code).

    C1 — a real run holds the cross-process call-of-record lock (``pipeline/run_lock.py``) for the whole
    write, the same one the nightly pass holds; ``PassAlreadyRunning`` propagates before any row is
    written (``main`` turns it into one loud line + exit 75). A dry run writes nothing, so it takes none.
    """
    started_at = datetime.now(timezone.utc)
    lock = None if dry_run else acquire_pass_lock(holder="backfill")
    try:
        conn = connect()
        try:
            results = run_backfill(conn, asof=asof, thesis_id=thesis_id, dry_run=dry_run)
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()
    finished_at = datetime.now(timezone.utc)
    known_at, known_at_policy = reconstruction_known_at(asof), PUBLIC_CLOCK_POLICY
    log_path = None
    if not dry_run:
        log_path = write_backfill_log(
            results,
            asof=asof,
            known_at=known_at,
            known_at_policy=known_at_policy,
            dry_run=dry_run,
            started_at=started_at,
            finished_at=finished_at,
            base_dir=log_dir,
        )
    return BackfillPassOutcome(
        results=results,
        asof=asof,
        known_at=known_at,
        known_at_policy=known_at_policy,
        dry_run=dry_run,
        started_at=started_at,
        finished_at=finished_at,
        log_path=log_path,
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def _report(results: list[BackfillResult], *, dry_run: bool) -> int:
    """Print a per-thesis summary (``daily._report``'s shape); return the number that errored (the process
    exit signal). A skip prints its own loud line and its own count — it is neither an error nor an
    "unchanged"."""
    appended = sum(1 for r in results if r.recorded)
    unchanged = sum(1 for r in results if r.recorded is False)
    skipped = [r for r in results if r.skipped]
    errored = [r for r in results if r.error]
    for r in results:
        if r.error:
            print(f"  {r.name}: ERROR: {r.error}")
            continue
        if r.skipped:
            print(f"  {r.name}: SKIPPED — {r.skipped} (no row written)")
            continue
        if dry_run:
            mark = "DRY-RUN (nothing written)"
        elif r.recorded:
            mark = "APPENDED"
        else:
            mark = "unchanged (identical row already logged)"
        prior = (
            f"prior row: {r.prior_state} / {r.prior_verdict}"
            if r.prior_state
            else "no row for this as-of yet"
        )
        print(f"  {r.name}: {r.state} / {r.verdict} · {r.armed} armed · {prior} · {mark}")
    dr = " · DRY-RUN (nothing written)" if dry_run else ""
    print(
        f"done: {len(results)} theses · {appended} appended · {unchanged} unchanged · "
        f"{len(skipped)} skipped (did not exist on the night) · {len(errored)} errored{dr}"
    )
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill a MISSED night's call-of-record, reconstructed on the PUBLIC clock: every fact "
        "counts from when it became public, up to the end of that night's market day (a pure "
        "recompute-and-record over facts already in the store: no ingest, no notify). Every row it writes is "
        "marked reconstructed (the Scoreboard reports, never scores, such rows); a thesis that did not exist "
        "on the night is skipped and reported."
    )
    p.add_argument(
        "--asof", required=True, help="the missed night, YYYY-MM-DD (must be in the past)"
    )
    # RETIRED (operator decision 2026-10-21): the record-clock pin. Kept only to refuse it with the reason.
    p.add_argument("--known-at", dest="known_at", default=None, help=argparse.SUPPRESS)
    p.add_argument(
        "--thesis", default=None, help="one thesis id (uuid); default = every live thesis"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="assemble and print (skips included); write NOTHING (no row, no artifact)",
    )
    args = p.parse_args(argv)

    # --- the refusals: every one fires BEFORE a connection is opened ---
    if args.known_at is not None:
        p.error(
            "--known-at is retired: a missed night is reconstructed on the PUBLIC clock (every fact from "
            "when it became public, up to the end of that night's market day) — not on what our database "
            "held at some later instant. Drop --known-at."
        )
    try:
        asof = date.fromisoformat(args.asof)
    except ValueError:
        p.error(f"--asof {args.asof!r} is not a YYYY-MM-DD date")
    today = market_today()
    if asof >= today:
        p.error(
            f"--asof {asof} is not in the past (market today is {today}) — tonight is the cron's job; "
            "a backfill is for a MISSED night"
        )
    tz = market_tz()
    thesis_id: UUID | None = None
    if args.thesis:
        try:
            thesis_id = UUID(args.thesis)
        except ValueError:
            p.error(f"--thesis {args.thesis!r} is not a uuid")

    # the cap the night is judged at, in BOTH clocks, before anything happens
    known_at = reconstruction_known_at(asof)
    dr = " · DRY-RUN" if args.dry_run else ""
    print(
        f"backfill: asof={asof} · public clock, capped at {known_at.isoformat()} (UTC) = "
        f"{known_at.astimezone(tz).isoformat()} (market){dr}"
    )
    try:
        outcome = run_backfill_pass(asof=asof, thesis_id=thesis_id, dry_run=args.dry_run)
    except LookupError as exc:
        p.error(str(exc))
    except PassAlreadyRunning as exc:
        print(
            f"backfill: ALREADY RUNNING — {exc}. Nothing was written; re-run once it finishes "
            f"(exit {EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    if outcome.log_path is not None:
        print(f"provenance: {outcome.log_path}")
    if _report(outcome.results, dry_run=args.dry_run):
        raise SystemExit(1)  # surface partial failure non-silently (the daily discipline)


if __name__ == "__main__":
    main()
