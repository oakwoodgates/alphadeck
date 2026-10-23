"""THE NIGHTLY PAGE (editions S5) — ``python -m editions.run nightly [--apply]``.

An edition is cut once (``editions.run publish``) and then GROWS: every night the CURRENT edition (the newest,
not closed) gains one page per live thesis for every market session it has no page for yet. This module is
that job. The cron sidecar fires it after the nightly backup (``scripts/daily_cron.sh``, the scheduling layer
like ``pipeline.backup``), never inside ``run_daily_pass`` — so the Admin "Run daily now" never fires it, and
nothing on the call-of-record path imports it (the import-graph tests).

WHAT ONE RUN DOES, in order:

1. **The gate.** It runs only when the newest run artifact (``data/cron_runs``) is a FINISHED, LIVE, NIGHTLY
   pass; that pass's as-of is the night it pages through (``end``). A running / crashed pass, an onboarding
   or other non-nightly artifact, a cache-only (``--no-live``) pass or no artifact at all: nothing runs, the
   reason is printed (and, for ``--apply``, written to the artifact). ``--end`` bypasses it (a manual run).
2. **The locks.** Its own advisory lock (``pipeline.run_lock.LOCK_ID_EDITIONS``, holder ``editions``); it
   REFUSES (exit 75) while another editions job holds it, and while the call-of-record lock (a daily pass, a
   backfill, a repair) is held — read from ``pg_locks``, never taken. The probe runs again just before the
   write, so a pass that started mid-run costs this run, never the pass.
3. **One consistent read** (``engine.consistent_snapshot``, ``REPEATABLE READ`` read-only): the public-clock
   mirror cut at ``known_by`` = the database instant this job started, the join dates (``editions.entry``), the
   edition's own pages and frozen join dates, the onboarding queue. Never ``calls``.
4. **The roster per night** is the engine's (``engine.night_members``): the latest ``basket_snapshot`` taken by
   the night's cap — exactly ``thesis_repo.get_asof``'s rule — so a company added by a promote joins from that
   night and one removed drops off from the night it left. A company NEW to the edition (a promote after the
   cut, or a whole thesis created after it) gets its join date frozen as an ``edition_entry`` (method
   ``snapshot``, exact); a company already in the edition keeps the join date the edition was cut with.
5. **The missing sessions, per thesis**: every real session (the engine's rule: a date with bars on the
   thesis's tape — a market holiday has no bars and no page) after the thesis's own newest page, through
   ``end``; a thesis with no page yet starts at its first join date, which is never before it existed (a
   join date is never earlier than the thesis's creation). Tonight's page is ``nightly``; an earlier missed
   session is ``catchup``. One run pages at most ``MAX_NIGHTS_PER_RUN`` sessions per thesis, the oldest
   first, so a catch-up too big for one run's timeout still finishes over the next nights (listed as
   ``behind``). An ARCHIVED thesis gets no new page at all (the live pass no longer records it); a deleted one
   is gone with its pages (the FK cascade).
6. **The hold.** A thesis with any member on those nights whose history is still loading (a
   ``security_onboarding`` row not ``done`` — a COLD name, X1) gets NO page: on the public clock its page
   would be computed on a partial filing history and then frozen. It is listed as held (the artifact, the
   Admin line); the next run's catch-up fills every night it missed once the name is loaded. An ORDINARY
   ingest fault (a name's leg errored tonight) is not held: the page is computed and the fault noted.
7. **The pages** — the engine's own assembly (``engine.run_jobs`` over the mirror), each stamped with its
   watermark and engine fingerprint, appended in ONE transaction with any new join dates and a ``paged``
   event. Every append is ``ON CONFLICT DO NOTHING`` on its unique key: a second run the same night appends
   0 rows (the tests count the table).
8. **The ink** (migration 0064): every episode whose signal window ENDED (``exit_by``, the window end — the
   operator's decision, never the de-arm) and whose closes are all in (the market tape has printed a session
   past ``exit_by``, and the window reads complete) is scored once and frozen in ``edition_outcome``; the
   Scoreboard's edition read uses it from then on. An episode still waiting on prices is listed, not inked.
9. **The self-check** (``editions.selfcheck.live_edge``): tonight's page against tonight's recorded call of
   record — canonical full-card equality, money to the cent. A row recorded under another policy or build
   than the running engine (a deploy day) is SKIPPED and says why; a held thesis and a night only one side has
   (a holiday row) are reported, never compared; a real mismatch pages through ``notify_health`` (an ops
   alarm, never a call alert; counts only, no thesis names).
10. **The artifact** (``pipeline.edition_run_log``): ``data/edition_runs/<started>.json`` — timings, pages per
   thesis, held and noted theses, the ink, the self-check, the table counts — and ONE plain-English line the
   Admin page shows (``admin_line``).

DRY-RUN by default: the connection stays READ-ONLY throughout, nothing is written to the database, nothing
pages, and the artifact is marked ``dry_run`` (the Admin reads only applied runs). The ``calls`` table is only
ever read.
"""

from __future__ import annotations

import dataclasses
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, config_hash
from domain.settings import get_settings
from editions.engine import ThesisJob, build_job, consistent_snapshot, open_mirror, run_jobs
from editions.entry import Entry, derive_all
from editions.fingerprint import engine_fingerprint
from editions.selfcheck import EdgeResult, live_edge
from notify import HealthEvent, Notifier, get_notifier
from pipeline import edition_run_log
from pipeline.cron_run_log import PASS_KIND_NIGHTLY, list_run_logs, run_status
from pipeline.run_lock import (
    EXIT_ALREADY_RUNNING,
    EditionsJobRunning,
    JobLock,
    acquire_editions_lock,
)
from replay.export import export_snapshot
from repositories import editions_repo, thesis_repo
from repositories.editions_repo import Edition, JoinDate
from scoreboard.prices import market_tape_edge
from scoreboard.record import EditionSource, derive_thesis_record

HOLDER = "editions"
_WORK_DIR = Path(__file__).resolve().parents[2] / "data" / "editions" / "nightly"
# One run pages at most this many sessions per thesis, the OLDEST first — the pages are written only at the
# end of a run, so a catch-up too big for the cron's timeout must still make progress every night rather than
# never finish. MEASURED on dev (2026-10-23): ~1.2 s per page on one worker, so 30 nights x 20 theses ≈ 12 min.
MAX_NIGHTS_PER_RUN = 30


class NightlyRefused(Exception):
    """The job did not run (another editions job, or a pass / backfill / repair holds the call-of-record
    lock). Nothing was written. The CLI exits ``EXIT_ALREADY_RUNNING`` (75)."""


# --- the gate ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    ok: bool
    end: date | None
    reason: str
    artifact: dict[str, Any] | None = (
        None  # the newest pass artifact (a summary), when one was read
    )

    def as_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "end": self.end.isoformat() if self.end else None,
            "reason": self.reason,
            "artifact": self.artifact,
        }


def pass_gate(*, cron_runs_dir: Path | None = None) -> Gate:
    """Is the NEWEST run artifact a finished, live, nightly pass? Its as-of is the night to page through."""
    newest = list_run_logs(base_dir=cron_runs_dir, limit=1)
    if not newest:
        return Gate(False, None, "no nightly pass has run yet")
    doc = newest[0]
    summary = {
        k: doc.get(k)
        for k in ("started_at", "finished_at", "asof", "status", "pass_kind", "run_kind", "mode")
    }
    kind = doc.get("pass_kind") or PASS_KIND_NIGHTLY  # an artifact older than X1 is the nightly's
    if kind != PASS_KIND_NIGHTLY:
        return Gate(
            False, None, f"the newest run is not a nightly pass (pass_kind {kind})", summary
        )
    status = run_status(doc)
    if status != "finished":
        return Gate(False, None, f"the nightly pass has not finished (status {status})", summary)
    if doc.get("mode") == "no-live":
        return Gate(False, None, "the newest pass was cache-only (--no-live)", summary)
    try:
        end = date.fromisoformat(str(doc.get("asof")))
    except ValueError:
        return Gate(False, None, "the newest pass artifact carries no readable as-of", summary)
    return Gate(True, end, "the newest run is a finished nightly pass", summary)


# --- the plan: everything read inside one consistent snapshot --------------------------------------------


@dataclass(frozen=True)
class Hold:
    thesis_id: UUID
    name: str
    nights: tuple[date, ...]
    names: tuple[dict[str, str], ...]  # [{security_id, ticker, status}] — the members still loading

    def as_json(self) -> dict[str, Any]:
        return {
            "thesis_id": str(self.thesis_id),
            "name": self.name,
            "nights": [d.isoformat() for d in self.nights],
            "loading": list(self.names),
        }


@dataclass
class Plan:
    edition: Edition | None
    known_by: datetime
    end: date
    jobs: list[ThesisJob] = field(default_factory=list)
    new_entries: list[Entry] = field(default_factory=list)
    held: list[Hold] = field(default_factory=list)
    archived: list[dict[str, Any]] = field(default_factory=list)
    names: dict[UUID, str] = field(default_factory=dict)
    live_theses: int = 0  # theses that could have a page tonight (not archived, with a roster)
    behind: list[dict[str, Any]] = field(default_factory=list)  # owed more than one run's nights


def _to_entry(j: JoinDate) -> Entry:
    return Entry(j.thesis_id, j.security_id, j.entry_date, j.estimated, j.method, dict(j.evidence))


def loading_names(conn: psycopg.Connection, *, tenant_id: UUID) -> dict[UUID, str]:
    """``security_id -> status`` for every name whose history is NOT loaded yet (``security_onboarding``,
    migration 0059: any status but ``done`` — queued, in progress, or failed and waiting for a retry).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, status FROM security_onboarding "
            "WHERE tenant_id = %s AND status <> 'done'",
            (tenant_id,),
        )
        return {r["security_id"]: r["status"] for r in cur.fetchall()}


def plan_pages(
    conn: psycopg.Connection,
    mirror_dir: Path,
    *,
    end: date,
    known_by: datetime,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    max_nights: int = MAX_NIGHTS_PER_RUN,
) -> Plan:
    """Read everything tonight's pages need inside ONE consistent snapshot (see the module docstring, steps
    3-6): the current edition, its pages and frozen join dates, the onboarding queue, the public-clock mirror
    at ``known_by`` and the join dates as known then; then build one engine job per thesis that has missing
    sessions and is not held — at most ``max_nights`` of them per thesis, oldest first (a thesis further
    behind is listed in ``behind`` and finishes over the next runs). Writes only ``mirror_dir``."""
    with consistent_snapshot(conn) as snapshot_at:
        if known_by > snapshot_at:
            raise ValueError(
                f"a cut ({known_by}) cannot be later than its snapshot ({snapshot_at})"
            )
        edition = editions_repo.current_edition(conn, tenant_id=tenant_id)
        plan = Plan(edition=edition, known_by=known_by, end=end)
        if edition is None:
            return plan
        last_pages = editions_repo.last_page_by_thesis(conn, edition.id)
        frozen = editions_repo.entries_by_thesis(conn, edition.id)
        loading = loading_names(conn, tenant_id=tenant_id)
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM thesis WHERE tenant_id = %s", (tenant_id,))
            plan.names = {r["id"]: r["name"] for r in cur.fetchall()}
        export_snapshot(
            conn, mirror_dir, tenant_id=tenant_id, clock="public", known_by=known_by, identity=True
        )
        rosters, derived = derive_all(conn, known_by=known_by)
        con = open_mirror(mirror_dir)
        try:
            for r in rosters:
                if r.tenant_id != tenant_id:
                    continue
                name = plan.names.get(r.thesis_id, str(r.thesis_id))
                if r.archived_at is not None:
                    plan.archived.append({"thesis_id": str(r.thesis_id), "name": name})
                    continue
                kept = frozen.get(r.thesis_id, {})
                new = {sid: e for sid, e in derived.get(r.thesis_id, {}).items() if sid not in kept}
                plan.new_entries.extend(new[s] for s in sorted(new, key=lambda u: u.int))
                entries = {**new, **{sid: _to_entry(j) for sid, j in kept.items()}}
                if not entries:
                    continue
                plan.live_theses += 1
                last = last_pages.get(r.thesis_id)
                start = last + timedelta(days=1) if last is not None else None
                if start is not None and start > end:
                    continue  # up to date
                job = build_job(conn, con, r, entries, end=end, known_by=known_by, start=start)
                if job is None or not job.nights:
                    continue
                if len(job.nights) > max_nights:  # the OLDEST first, so every run makes progress
                    plan.behind.append(
                        {
                            "thesis_id": str(r.thesis_id),
                            "name": name,
                            "owed": len(job.nights),
                            "this_run": max_nights,
                        }
                    )
                    job = dataclasses.replace(job, nights=job.nights[:max_nights])
                members = {
                    m.security_id: m.ticker
                    for n in job.nights
                    for m in n.basket
                    if m.security_id is not None
                }
                cold = sorted(
                    (sid for sid in members if sid in loading), key=lambda u: (members[u], u.int)
                )
                if cold:
                    plan.held.append(
                        Hold(
                            thesis_id=r.thesis_id,
                            name=name,
                            nights=tuple(n.asof for n in job.nights),
                            names=tuple(
                                {
                                    "security_id": str(s),
                                    "ticker": members[s] or str(s),
                                    "status": loading[s],
                                }
                                for s in cold
                            ),
                        )
                    )
                    continue
                plan.jobs.append(job)
        finally:
            con.close()
    return plan


# --- the ink ----------------------------------------------------------------------------------------------


@dataclass
class InkPlan:
    rows: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)


def _episode_rows(
    conn: psycopg.Connection, edition: Edition, night: date, *, tenant_id: UUID
) -> Iterable[tuple[UUID, Any, set[tuple[UUID, date]], date | None]]:
    """Every thesis with pages in ``edition``, read as the Scoreboard reads it at ``night`` (``EditionSource``,
    prices as known now): ``(thesis_id, ScoredEpisode, already-inked keys, market tape edge)``."""
    edge = market_tape_edge(conn, tenant_id=tenant_id, cap=night)
    src = EditionSource(edition)
    for tid in sorted(editions_repo.last_page_by_thesis(conn, edition.id), key=str):
        thesis = thesis_repo.get(conn, tid)
        if thesis is None:
            continue
        inked = set(editions_repo.outcomes_for_thesis(conn, edition.id, tid))
        record, _ = derive_thesis_record(conn, thesis, night, source=src, market_edge=edge)
        for e in record.episodes:
            yield tid, e, inked, edge


def plan_ink(
    conn: psycopg.Connection,
    edition: Edition,
    night: date,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> InkPlan:
    """The episodes to ink tonight: MATURED (the signal window ended by ``night``), not inked yet, and with
    their closes all in — the market tape has printed a session past ``exit_by`` and the window reads complete
    (not ``truncated``, not ``insufficient_prices``). The rest that matured are listed as waiting.
    """
    out = InkPlan()
    known = db_now(conn)
    for tid, e, inked, edge in _episode_rows(conn, edition, night, tenant_id=tenant_id):
        ep = e.episode
        if (ep.security_id, ep.arm_date) in inked or not e.matured:
            continue
        why = None
        if edge is None or ep.exit_by is None or ep.exit_by >= edge:
            why = "the market has not printed a session past its window end yet"
        elif e.outcome.insufficient_prices or e.outcome.truncated:
            why = "its window's closes are not all in"
        if why is not None:
            out.waiting.append(
                {
                    "thesis_id": str(tid),
                    "security_id": str(ep.security_id),
                    "arm_date": ep.arm_date.isoformat(),
                    "exit_by": ep.exit_by.isoformat() if ep.exit_by else None,
                    "why": why,
                }
            )
            continue
        out.rows.append(
            {
                "thesis_id": tid,
                "security_id": ep.security_id,
                "arm_date": ep.arm_date,
                "exit_by": ep.exit_by,
                "kind": "matured",
                "outcome": e.outcome,
                "night": night,
                "prices_known_by": known,
            }
        )
    return out


def ink_running_at_close(
    conn: psycopg.Connection, edition: Edition, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> int:
    """When an edition CLOSES (S7 cuts a newer one), ink every episode it has not inked yet, as of its last
    night: a matured one as ``matured`` (with whatever its window holds by then), a running one as
    ``running_at_close``. Reading a closed edition then touches only stored rows (G13). Writes inside the
    CALLER's transaction; idempotent (the episode key). Returns the rows written. No caller yet (S7).
    """
    if edition.last_night is None:
        return 0
    night = edition.last_night
    known = db_now(conn)
    rows = [
        {
            "thesis_id": tid,
            "security_id": e.episode.security_id,
            "arm_date": e.episode.arm_date,
            "exit_by": e.episode.exit_by,
            "kind": "matured" if e.matured else "running_at_close",
            "outcome": e.outcome,
            "night": night,
            "prices_known_by": known,
        }
        for tid, e, inked, _ in _episode_rows(conn, edition, night, tenant_id=tenant_id)
        if (e.episode.security_id, e.episode.arm_date) not in inked
    ]
    return editions_repo.ink_outcomes(conn, tenant_id=tenant_id, edition_id=edition.id, rows=rows)


# --- the self-check -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EditionCheckPage(HealthEvent):
    """The editions self-check's ops alarm: tonight's page differs from tonight's recorded call of record.
    A ``HealthEvent`` so it rides ``Notifier.notify_health`` unchanged (Slack when configured, else the loud
    log line). Counts only — never a thesis name (the operator's theses are private)."""

    edition_number: int = 0
    mismatches: int = 0
    compared: int = 0

    @property
    def label(self) -> str:
        return (
            f"editions self-check {self.asof.isoformat()}: Edition {self.edition_number}'s page differs "
            f"from the recorded call for {self.mismatches} of {self.compared} theses — see "
            "data/edition_runs"
        )


def check_page(
    asof: date, *, edition_number: int, mismatches: int, compared: int
) -> EditionCheckPage:
    return EditionCheckPage(
        asof=asof,
        theses=0,
        withheld_no_live=0,
        withheld_failure=0,
        errored=0,
        edgar_fetches=0,
        frozen=False,
        edition_number=edition_number,
        mismatches=mismatches,
        compared=compared,
    )


def classify(results: list[EdgeResult], *, held: set[UUID]) -> list[dict[str, Any]]:
    """One verdict per recorded row: ``held`` (no page tonight, by the hold rule), ``no_page`` (only one side
    has the night — a holiday row, an empty roster), ``skipped`` (recorded under another policy or build than
    the running engine — a deploy day; said, never compared), ``match`` or ``mismatch``."""
    out = []
    for r in results:
        row = r.as_json()
        if r.thesis_id in held:
            row.update(verdict="held", why="held: a member's history is still loading")
        elif r.outcome == "no_page":
            row.update(verdict="no_page", why="the edition has no page for this night")
        elif r.same_policy is False:
            row.update(verdict="skipped", why="recorded under another call policy (config hash)")
        elif r.same_image is False:
            row.update(verdict="skipped", why="recorded by another build (code sha): a deploy day")
        else:
            row.update(verdict=r.outcome, why=None)
            if r.outcome == "match" and r.money_within_cent:
                row["why"] = "money values within one cent (a float-summation order difference)"
        out.append(row)
    return out


def self_check_status(rows: list[dict[str, Any]]) -> str:
    verdicts = {r["verdict"] for r in rows}
    if "mismatch" in verdicts:
        return "mismatch"
    if "match" in verdicts:
        return "passed"
    if "skipped" in verdicts:
        return "skipped"
    return "nothing_to_check"


# --- the run ----------------------------------------------------------------------------------------------


@dataclass
class NightlyResult:
    mode: str
    status: str  # done | skipped | crashed
    started_at: datetime
    gate: Gate
    end: date | None = None
    known_by: datetime | None = None
    edition: Edition | None = None
    engine: dict[str, Any] | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)  # per thesis: nights + kinds
    n_pages: int = 0  # computed (dry run) / appended (apply)
    held: list[Hold] = field(default_factory=list)
    noted: list[dict[str, Any]] = field(default_factory=list)
    archived: list[dict[str, Any]] = field(default_factory=list)
    behind: list[dict[str, Any]] = field(default_factory=list)
    entries_appended: int = 0
    new_entries: int = 0
    live_theses: int = 0
    ink: dict[str, Any] = field(default_factory=dict)
    self_check: dict[str, Any] = field(default_factory=dict)
    counts_before: dict[str, int] | None = None
    counts_after: dict[str, int] | None = None
    timings: dict[str, float] = field(default_factory=dict)
    finished_at: datetime | None = None
    reason: str | None = None
    artifact_path: str | None = None

    def admin_line(self) -> str:
        return admin_line(self)

    def payload(self) -> dict[str, Any]:
        ed = self.edition
        return {
            "kind": edition_run_log.KIND,
            "mode": self.mode,
            "status": self.status,
            "reason": self.reason,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "end": self.end.isoformat() if self.end else None,
            "known_by": self.known_by.isoformat() if self.known_by else None,
            "edition": (
                {
                    "id": str(ed.id),
                    "number": ed.number,
                    "label": ed.label,
                    "engine_fingerprint": ed.engine_fingerprint,
                }
                if ed
                else None
            ),
            "engine": self.engine,
            "engine_changed": bool(
                ed and self.engine and self.engine["fingerprint"] != ed.engine_fingerprint
            ),
            "gate": self.gate.as_json(),
            "n_pages": self.n_pages,
            "pages": self.pages,
            "held": [h.as_json() for h in self.held],
            "noted": self.noted,
            "archived": self.archived,
            "behind": self.behind,
            "live_theses": self.live_theses,
            "new_entries": self.new_entries,
            "entries_appended": self.entries_appended,
            "ink": self.ink,
            "self_check": self.self_check,
            "counts_before": self.counts_before,
            "counts_after": self.counts_after,
            "timings": self.timings,
            "admin_line": admin_line(self),
            "attention": self.status == "crashed" or self.self_check.get("status") == "mismatch",
        }


def _md(d: date) -> str:
    return f"{d:%b} {d.day}"


def _theses(n: int) -> str:
    return f"{n} {'thesis' if n == 1 else 'theses'}"


def admin_line(r: NightlyResult) -> str:
    """The ONE plain-English line the Admin page shows for this run (quiet unless something needs a look)."""
    head = f"Edition {r.edition.number}" if r.edition else "Editions"
    if r.status == "crashed":
        return f"{head} · tonight's page job failed ({r.reason}) · the next run catches up"
    if r.status == "skipped":
        night = f" for {_md(r.end)}" if r.end else ""
        return f"{head} · no page{night}: {r.reason}"
    if r.edition is None:
        return "Editions · no open edition to add pages to"
    parts = [head]
    nightly = sum(1 for p in r.pages for k in p["kinds"] if k == "nightly")
    catchup = sum(1 for p in r.pages for k in p["kinds"] if k == "catchup")
    verb = "added" if r.mode == edition_run_log.MODE_APPLY else "to add (dry run)"
    if nightly or catchup:
        what = f"page for {_md(r.end)} {verb}" if nightly else f"no new page for {_md(r.end)}"
        if catchup:
            what += f" · {catchup} catch-up page{'s' if catchup != 1 else ''} {verb}"
        parts.append(what)
    elif r.held:
        parts.append(f"no new page for {_md(r.end)}")
    else:
        parts.append(f"up to date through {_md(r.end)}")
    parts.append(_theses(r.live_theses))
    if r.held:
        parts.append(f"{_theses(len(r.held))} held: history still loading")
    if r.behind:
        parts.append(f"{_theses(len(r.behind))} still catching up (the oldest nights first)")
    inked = r.ink.get("inked", 0)
    if inked:
        done = "inked" if r.mode == edition_run_log.MODE_APPLY else "to ink (dry run)"
        parts.append(f"{inked} final score{'s' if inked != 1 else ''} {done}")
    status = r.self_check.get("status")
    if status == "passed":
        parts.append("self-check passed")
    elif status == "mismatch":
        n = r.self_check.get("mismatches", 0)
        parts.append(
            f"self-check: {n} {'thesis differs' if n == 1 else 'theses differ'} from the recorded call"
        )
    elif status == "skipped":
        parts.append("self-check skipped: recorded by another build or policy")
    elif status == "nothing_to_check":
        parts.append("self-check: no recorded call to compare tonight")
    return " · ".join(parts)


def _noted(gate: Gate, cron_runs_dir: Path | None, theses: set[UUID]) -> list[dict[str, Any]]:
    """The theses whose ingest had an ORDINARY fault tonight (a name's leg errored, the call withheld or
    errored), from the gate's pass artifact — paged anyway (never held), and noted."""
    if not gate.ok or gate.artifact is None:
        return []
    newest = list_run_logs(base_dir=cron_runs_dir, limit=1)
    if not newest:
        return []
    out = []
    for t in newest[0].get("theses") or []:
        if not isinstance(t, dict):
            continue
        try:
            tid = UUID(str(t.get("id")))
        except ValueError:
            continue
        if tid not in theses:
            continue
        note = {
            k: t.get(k)
            for k in ("withheld_reason", "error", "names_errored")
            if t.get(k) not in (None, 0, "")
        }
        if note:
            out.append({"thesis_id": str(tid), "name": t.get("name"), **note})
    return out


def run_nightly(
    conn: psycopg.Connection,
    *,
    apply: bool = False,
    end: date | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    cron_runs_dir: Path | None = None,
    runs_dir: Path | None = None,
    work_dir: Path | None = None,
    workers: int = 1,
    notifier: Notifier | None = None,
    keep_mirror: bool = False,
    write_artifact: bool = True,
    max_nights: int = MAX_NIGHTS_PER_RUN,
) -> NightlyResult:
    """One nightly run (the module docstring). ``end`` bypasses the gate (a manual run through that night).
    Raises ``NightlyRefused`` (nothing written, no artifact) on a held lock."""
    mode = edition_run_log.MODE_APPLY if apply else edition_run_log.MODE_DRY_RUN
    started = datetime.now(timezone.utc)
    t_all = time.perf_counter()
    if end is None:
        gate = pass_gate(cron_runs_dir=cron_runs_dir)
    else:
        gate = Gate(True, end, "bypassed: a manual run through --end")
    result = NightlyResult(mode=mode, status="done", started_at=started, gate=gate, end=gate.end)
    if not gate.ok:
        result.status, result.reason = "skipped", gate.reason
        return _finish(result, runs_dir, write_artifact and apply, t_all)

    try:
        lock: JobLock = acquire_editions_lock(holder=HOLDER)
    except EditionsJobRunning as exc:
        raise NightlyRefused(str(exc)) from exc
    mirror_dir = (work_dir or _WORK_DIR) / f"{started:%Y%m%dT%H%M%SZ}" / "mirror"
    prior_ro = conn.read_only
    try:
        holder = lock.call_of_record_holder()
        if holder:
            raise NightlyRefused(
                f"a daily pass, backfill or repair holds the call-of-record lock ({holder})"
            )
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.commit()
        if not apply:
            conn.read_only = True  # a dry run writes no row, anywhere
        result.known_by = db_now(conn)  # the cut: the database instant this job started
        conn.commit()
        result.engine = engine_fingerprint(DEFAULT_CONFIG)

        t0 = time.perf_counter()
        plan = plan_pages(
            conn,
            mirror_dir,
            end=gate.end,
            known_by=result.known_by,
            tenant_id=tenant_id,
            max_nights=max_nights,
        )
        result.timings["prepare_s"] = round(time.perf_counter() - t0, 1)
        result.edition, result.held, result.archived = plan.edition, plan.held, plan.archived
        result.behind = plan.behind
        result.live_theses, result.new_entries = plan.live_theses, len(plan.new_entries)
        if plan.edition is None:
            return _finish(result, runs_dir, write_artifact and apply, t_all)
        edition = plan.edition

        t0 = time.perf_counter()
        cards = run_jobs(mirror_dir, plan.jobs, cfg=DEFAULT_CONFIG, workers=workers)
        result.timings["assemble_s"] = round(time.perf_counter() - t0, 1)
        pages = [
            (c, "nightly" if c.asof == gate.end else "catchup")
            for j in plan.jobs
            for c in cards[j.thesis_id]
        ]
        result.pages = [
            {
                "thesis_id": str(j.thesis_id),
                "name": plan.names.get(j.thesis_id),
                "nights": [c.asof.isoformat() for c in cards[j.thesis_id]],
                "kinds": [
                    "nightly" if c.asof == gate.end else "catchup" for c in cards[j.thesis_id]
                ],
            }
            for j in plan.jobs
            if cards[j.thesis_id]
        ]
        result.noted = _noted(gate, cron_runs_dir, {j.thesis_id for j in plan.jobs})

        # the write — re-probing the call-of-record lock first (a pass that started mid-run wins)
        t0 = time.perf_counter()
        result.counts_before = editions_repo.table_counts(conn)
        conn.commit()
        if apply:
            holder = lock.call_of_record_holder()
            if holder:
                raise NightlyRefused(
                    f"a daily pass started while the pages were computed ({holder}); nothing written"
                )
            result.n_pages = _write_pages(conn, result, edition, pages, plan, tenant_id=tenant_id)
        else:
            result.n_pages = len(pages)

        # the ink, then the self-check (both read the edition as stored)
        ink = plan_ink(conn, edition, gate.end, tenant_id=tenant_id)
        inked = 0
        if apply and ink.rows:
            inked = editions_repo.ink_outcomes(
                conn, tenant_id=tenant_id, edition_id=edition.id, rows=ink.rows
            )
            conn.commit()
        conn.commit()
        result.ink = {
            "inked": inked if apply else len(ink.rows),
            "waiting": ink.waiting,
            "episodes": [
                {
                    "thesis_id": str(r["thesis_id"]),
                    "security_id": str(r["security_id"]),
                    "arm_date": r["arm_date"].isoformat(),
                    "exit_by": r["exit_by"].isoformat() if r["exit_by"] else None,
                }
                for r in ink.rows
            ],
        }
        result.timings["write_ink_s"] = round(time.perf_counter() - t0, 1)

        t0 = time.perf_counter()
        _self_check(conn, result, edition, pages, apply=apply, notifier=notifier)
        result.timings["self_check_s"] = round(time.perf_counter() - t0, 1)
        result.counts_after = editions_repo.table_counts(conn)
        conn.commit()
    finally:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        conn.read_only = prior_ro
        lock.release()
        if not keep_mirror:
            shutil.rmtree(mirror_dir.parent, ignore_errors=True)
    return _finish(result, runs_dir, write_artifact, t_all)


def _write_pages(
    conn: psycopg.Connection,
    result: NightlyResult,
    edition: Edition,
    pages: list[tuple[CallCard, str]],
    plan: Plan,
    *,
    tenant_id: UUID,
) -> int:
    """ONE transaction: the new join dates, the pages, and (when anything was appended) a ``paged`` event.
    Refused when the edition was closed meanwhile (nothing written). Returns the pages written."""
    try:
        now_current = editions_repo.current_edition(conn, tenant_id=tenant_id)
        if now_current is None or now_current.id != edition.id:
            raise NightlyRefused(
                f"Edition {edition.number} is no longer the current edition; nothing written"
            )
        result.entries_appended = editions_repo.append_entries(
            conn,
            tenant_id=tenant_id,
            edition_id=edition.id,
            entries=[e.as_json() for e in plan.new_entries],
        )
        assert result.engine is not None and result.known_by is not None
        written = editions_repo.append_pages(
            conn,
            tenant_id=tenant_id,
            edition_id=edition.id,
            pages=pages,
            data_known_by=result.known_by,
            engine_fingerprint=result.engine["fingerprint"],
            computed_at=datetime.now(timezone.utc),
        )
        if written or result.entries_appended:
            editions_repo.append_event(
                conn,
                tenant_id=tenant_id,
                edition_id=edition.id,
                kind="paged",
                detail={
                    "end": result.end.isoformat() if result.end else None,
                    "known_by": result.known_by.isoformat(),
                    "pages": written,
                    "nightly": sum(1 for _, k in pages if k == "nightly"),
                    "catchup": sum(1 for _, k in pages if k == "catchup"),
                    "entries": result.entries_appended,
                    "held": len(result.held),
                    "engine_fingerprint": result.engine["fingerprint"],
                },
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return written


def _self_check(
    conn: psycopg.Connection,
    result: NightlyResult,
    edition: Edition,
    pages: list[tuple[CallCard, str]],
    *,
    apply: bool,
    notifier: Notifier | None,
) -> None:
    night = result.end
    assert night is not None and result.known_by is not None
    stored = editions_repo.pages_at(conn, edition.id, night)
    computed = {c.thesis_id: c for c, _ in pages if c.asof == night}
    cards = {tid: [c] for tid, c in {**computed, **stored}.items()}
    edge = live_edge(
        conn,
        cards,
        known_by=result.known_by,
        night=night,
        cfg=DEFAULT_CONFIG,
        code_sha=get_settings().image_sha,
    )
    conn.commit()
    rows = classify(edge, held={h.thesis_id for h in result.held})
    status = self_check_status(rows)
    mismatches = sum(1 for r in rows if r["verdict"] == "mismatch")
    compared = sum(1 for r in rows if r["verdict"] in ("match", "mismatch"))
    result.self_check = {
        "status": status,
        "night": night.isoformat(),
        "engine_config_hash": config_hash(DEFAULT_CONFIG),
        "engine_code_sha": get_settings().image_sha,
        "compared": compared,
        "mismatches": mismatches,
        "counts": {
            v: sum(1 for r in rows if r["verdict"] == v)
            for v in sorted({r["verdict"] for r in rows})
        },
        "rows": rows,
        "paged": False,
    }
    if status == "mismatch" and apply:
        sink = notifier if notifier is not None else get_notifier()
        try:
            sink.notify_health(
                check_page(
                    night, edition_number=edition.number, mismatches=mismatches, compared=compared
                )
            )
            result.self_check["paged"] = True
        except (
            Exception
        ):  # noqa: BLE001 — a page that fails never fails the job (the artifact says so)
            result.self_check["page_error"] = "the notifier raised"


def _finish(
    result: NightlyResult, runs_dir: Path | None, write: bool, t_all: float
) -> NightlyResult:
    result.finished_at = datetime.now(timezone.utc)
    result.timings["total_s"] = round(time.perf_counter() - t_all, 1)
    if write:
        p = edition_run_log.write_edition_run(result.payload(), base_dir=runs_dir)
        result.artifact_path = str(p) if p else None
    return result


def crashed_artifact(
    *, started_at: datetime, error: BaseException, runs_dir: Path | None = None
) -> None:
    """The job died on an exception Python saw: one ``crashed`` artifact so the Admin line says so (quietly
    loud — it needs a look), never a silent night. Fail-open."""
    r = NightlyResult(
        mode=edition_run_log.MODE_APPLY,
        status="crashed",
        started_at=started_at,
        gate=Gate(False, None, "crashed"),
        reason=type(error).__name__,
    )
    _finish(r, runs_dir, True, time.perf_counter())


def print_result(r: NightlyResult) -> None:
    print(r.admin_line())
    if r.status != "done" or r.edition is None:
        return
    nightly = sum(1 for p in r.pages for k in p["kinds"] if k == "nightly")
    catchup = sum(1 for p in r.pages for k in p["kinds"] if k == "catchup")
    print(
        f"  end {r.end} · cut {r.known_by.isoformat() if r.known_by else '-'} · "
        f"{'appended' if r.mode == edition_run_log.MODE_APPLY else 'would append'} {r.n_pages} pages "
        f"({nightly} nightly, {catchup} catch-up) over {len(r.pages)} theses · "
        f"{r.new_entries} new join dates · {len(r.held)} held · ink {r.ink.get('inked', 0)} "
        f"(waiting {len(r.ink.get('waiting', []))}) · self-check {r.self_check.get('status')} "
        f"{r.self_check.get('counts')} · timings {r.timings}"
    )
    for h in r.held:
        print(
            f"  held: {h.name} — {len(h.nights)} night(s); loading "
            + ", ".join(f"{n['ticker']} ({n['status']})" for n in h.names)
        )
    if r.counts_before and r.counts_after:
        for k in ("edition_event", "edition_entry", "edition_call", "edition_outcome", "calls"):
            print(f"  {k:16} {r.counts_before[k]:>7} -> {r.counts_after[k]:>7}")
    if r.artifact_path:
        print(f"  artifact {r.artifact_path}")


__all__ = [
    "EXIT_ALREADY_RUNNING",
    "Gate",
    "Hold",
    "NightlyRefused",
    "NightlyResult",
    "classify",
    "crashed_artifact",
    "ink_running_at_close",
    "pass_gate",
    "plan_ink",
    "plan_pages",
    "run_nightly",
]
