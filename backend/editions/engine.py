"""THE EDITIONS ENGINE (S2) — re-assemble every (thesis, night) with TODAY's rules over history.

ONE ENGINE, ONE CLOCK. It is the replay engine — the Parquet mirror read by ``ReplayPointInTimeData`` and
assembled by the SAME ``pipeline.core.assemble_from_pit`` the live call uses — over a ``clock="public"``
mirror (``replay.export``), the ONE public-clock definition every history view shares (``db.public_clock``,
the operator's 2026-10-21 rule, ``docs/INVARIANTS.md`` §4): each fact version counts from when it became
PUBLIC (an SEC acceptance, a filing date, a bar's settle; a correction from its own publication), never from
when this system happened to ingest it. There is no edition-specific clock and no per-table timing rule.

WHAT IS EDITION-SPECIFIC (and only this):

- **the freeze ceiling** (``known_by``): an edition is frozen at its CUT — the mirror holds only rows
  recorded by then (``replay.export``'s ``known_by``, applied in SQL before any rewrite), so the same cut
  reproduces the same edition later on the append-only store. It is the edition's freeze, not a history
  gate: on every night the public instant still decides what is visible;
- **one consistent snapshot**: the export, the join dates, the thesis bodies and every night's position are
  all read inside ONE ``REPEATABLE READ`` read-only transaction, so a write landing mid-run cannot give an
  edition from two moments;
- **the identity aux** (``security_identity``): main's mirror answers ``None`` for ``security_name`` /
  ``security_cik``; the edition must read what the Postgres call reads;
- **the roster by join date** (``editions.entry``): before a company's join date it is not in the basket;
- **the position**: the operator-decisions log at each night's cap (an operator decision counts from when
  it was logged — exactly ``pipeline.call_for_thesis.load_call_inputs``);
- **parallel per-thesis sweeps**: every input is read in the parent, so a worker is a pure function of the
  mirror and its job (no database), and the output never depends on the worker count.

WHAT IT READS PER NIGHT ``T`` (no lookahead, #1). The cap is ``known_at_for_asof(T, now=known_by)`` — the end
of T's night (EDGAR's next open, 06:00 ET the next weekday, F6), never later than the cut — exactly the cap the Postgres past-asof card
(``/theses/{id}/call?asof=T``) reads with:

- the facts: the public mirror at that cap (``recorded_at`` there IS the public instant);
- the roster: the latest ``basket_snapshot`` taken by the cap; before the first one, the 0043 seed's members
  whose JOIN DATE is on/before T; nothing for a thesis that had no roster yet;
- the position: the decisions log at the same cap;
- the thesis body (narrative, kill criteria, the catalyst list): today's, as every recompute reads it — unless
  the caller hands in a FROZEN body (``bodies``, S7): the two parts of the body a card reads (the kill
  criteria -> ``counter_case``, the catalysts -> ``catalyst_surface``) as an edition stored them at its cut, so
  the automatic cut's rules trial holds them fixed like every other input (``editions.cut``).

S7 also lets a caller pin the JOIN DATES (``frozen``: an edition's own ``edition_entry`` rows win over the rule's
output, so a new edition carries its parent's join dates over) and narrow a thesis to exact NIGHTS (``nights``:
the rules trial re-runs only the previous edition's re-run nights).

It writes NOTHING to Postgres. Nothing on the call-of-record path imports it (a test pins the import graph).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.market_time import known_at_for_asof, market_tz
from domain.thesis import BasketMember, Catalyst, KillCriterion, Position, Thesis
from editions.entry import Entry, ThesisRoster, derive_all
from pipeline.core import assemble_from_pit
from replay.export import export_snapshot
from replay.harness import trading_sessions
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories import decisions_repo, thesis_repo
from repositories.mappers import _row_to_basket_member
from signals.horizons import call_bounds


@dataclass(frozen=True)
class MirrorCut:
    """A materialized public-clock mirror and its CUT: ``known_by`` = the freeze ceiling (the system time of
    the newest row it may hold), ``snapshot_at`` = when the consistent snapshot it was read from began.
    """

    path: Path
    known_by: datetime
    snapshot_at: datetime
    manifest: dict[str, Any]


@contextmanager
def consistent_snapshot(conn: psycopg.Connection) -> Iterator[datetime]:
    """ONE consistent read of Postgres (G10): a ``REPEATABLE READ`` read-only transaction for the body of the
    ``with``; yields the snapshot's own start instant (``now()``). A caller's idle-or-open transaction is
    committed first (the engine only reads); the connection's settings are restored after. Named limitation:
    a writer transaction that began before the snapshot and committed after it carries a ``recorded_at``
    below the snapshot instant yet is invisible to it — the job runs when nothing writes (never 22:20-23:00
    ET, the nightly's window)."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.commit()
    prior_iso, prior_ro = conn.isolation_level, conn.read_only
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT now() AS t")
            snapshot_at = cur.fetchone()["t"]
        yield snapshot_at
        conn.commit()
    finally:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        conn.isolation_level, conn.read_only = prior_iso, prior_ro


def open_mirror(path: str | Path):
    """``connect_mirror`` without DuckDB's console progress bar (a cron log is not a terminal)."""
    con = connect_mirror(path)
    con.execute("SET enable_progress_bar = false")
    return con


def night_cap(night: date, known_by: datetime) -> datetime:
    """The cap every read for ``night`` is bounded by: the end of the night (F6), never past the cut."""
    return known_at_for_asof(night, known_by)


# --- the roster on a night -------------------------------------------------------------------------------


def _sid(member: dict[str, Any]) -> UUID | None:
    s = member.get("security_id")
    return UUID(str(s)) if s is not None else None


def _joined(entries: dict[UUID, Entry], sid: UUID | None, night: date) -> bool:
    """A member is in the basket on ``night`` once its join date has come. An unresolved member (no
    security) carries nothing to call and is kept as stored; a resolved member with no join date (cannot
    happen for a roster the entries were derived from) is KEPT rather than silently dropped (#9)."""
    if sid is None:
        return True
    e = entries.get(sid)
    return e is None or e.entry_date <= night


def night_members(
    roster: ThesisRoster, entries: dict[UUID, Entry], night: date, *, known_by: datetime
) -> list[dict[str, Any]] | None:
    """The thesis's basket rows on ``night``, as known at the night's cap. ``None`` = the thesis has no
    snapshot at all (the caller falls back to the live basket, join-filtered); ``[]`` = no roster yet.
    """
    cap = night_cap(night, known_by)
    taken = [s for s in roster.snapshots if s.taken_at <= cap]
    if taken:
        latest = taken[-1]
        rows = list(latest.members)
        if roster.seed is not None and latest is roster.seed:
            rows = [m for m in rows if _joined(entries, _sid(m), night)]
        return rows
    if roster.seed is not None:  # before the seed snapshot: its members, by join date
        return [m for m in roster.seed.members if _joined(entries, _sid(m), night)]
    if not roster.snapshots:
        return None
    return []  # the thesis's first roster was written later (a thesis born after the 0043 deploy)


def basket_for_night(
    base: Thesis, roster: ThesisRoster, entries: dict[UUID, Entry], night: date, known_by: datetime
) -> list[BasketMember]:
    rows = night_members(roster, entries, night, known_by=known_by)
    if rows is None:  # no snapshot ever: the live basket, join-filtered
        return [m for m in base.basket if _joined(entries, m.security_id, night)]
    return [_row_to_basket_member(b) for b in sorted(rows, key=lambda b: b.get("ordinal", 0))]


def thesis_window(
    roster: ThesisRoster, entries: dict[UUID, Entry], end: date
) -> tuple[date, date] | None:
    """``(first night, last night)`` for a thesis: its earliest join date to ``end`` (or its archive date).
    ``None`` = nothing to re-run (no member has ever joined)."""
    if not entries:
        return None
    start = min(e.entry_date for e in entries.values())
    last = end
    if roster.archived_at is not None:
        last = min(last, roster.archived_at.astimezone(market_tz()).date())
    return (start, last) if start <= last else None


# --- the per-thesis job: every input read in the parent, inside the snapshot ----------------------------


@dataclass(frozen=True)
class NightInput:
    """One night's inputs besides the mirror: the roster on that night and the position at its cap."""

    asof: date
    basket: tuple[BasketMember, ...]
    position: Position | None


@dataclass(frozen=True)
class ThesisJob:
    """Everything one thesis's sweep needs besides the mirror. Travels to a worker as JSON (``spawn`` on
    Windows inherits nothing), and the in-process path takes the SAME round trip, so a worker's output can
    never depend on how many there are."""

    thesis_id: UUID
    tenant_id: UUID
    known_by: datetime
    base: Thesis
    nights: tuple[NightInput, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "thesis_id": str(self.thesis_id),
                "tenant_id": str(self.tenant_id),
                "known_by": self.known_by.isoformat(),
                "base": self.base.model_dump(mode="json"),
                "nights": [
                    {
                        "asof": n.asof.isoformat(),
                        "basket": [m.model_dump(mode="json") for m in n.basket],
                        "position": n.position.model_dump(mode="json") if n.position else None,
                    }
                    for n in self.nights
                ],
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, blob: str) -> "ThesisJob":
        d = json.loads(blob)
        return cls(
            thesis_id=UUID(d["thesis_id"]),
            tenant_id=UUID(d["tenant_id"]),
            known_by=datetime.fromisoformat(d["known_by"]),
            base=Thesis.model_validate(d["base"]),
            nights=tuple(
                NightInput(
                    asof=date.fromisoformat(n["asof"]),
                    basket=tuple(BasketMember.model_validate(m) for m in n["basket"]),
                    position=Position.model_validate(n["position"]) if n["position"] else None,
                )
                for n in d["nights"]
            ),
        )


def thesis_body(thesis: Thesis) -> dict[str, Any]:
    """The parts of a thesis body a card reads — the kill criteria (``counter_case``) and the catalysts
    (``catalyst_surface``) — as JSON, the form an edition freezes them in (S7)."""
    return {
        "kill_criteria": [k.model_dump(mode="json") for k in thesis.kill_criteria],
        "catalysts": [c.model_dump(mode="json") for c in thesis.catalysts],
    }


def with_body(thesis: Thesis, body: dict[str, Any] | None) -> Thesis:
    """``thesis`` with a frozen body (``thesis_body``'s form) in place of today's; ``None`` = today's."""
    if body is None:
        return thesis
    return thesis.model_copy(
        update={
            "kill_criteria": [KillCriterion.model_validate(k) for k in body["kill_criteria"]],
            "catalysts": [Catalyst.model_validate(c) for c in body["catalysts"]],
        }
    )


def build_job(
    conn: psycopg.Connection,
    con,
    roster: ThesisRoster,
    entries: dict[UUID, Entry],
    *,
    end: date,
    known_by: datetime,
    start: date | None = None,
    nights: set[date] | None = None,
    body: dict[str, Any] | None = None,
) -> ThesisJob | None:
    """One thesis's job: every real trading session (on the union of its rosters, the harness rule — bar dates
    intersected with the session calendar, so a market holiday has no page even when a vendor stored a bar
    for it, F5) from its first join date to ``end``, each with its roster and its
    position at the night's cap. A night with no resolved member has no page. ``start`` / ``nights`` narrow
    the sweep (a diagnostic; S7's rules trial). ``body`` = a frozen thesis body (``thesis_body``'s form) in
    place of today's. ``None`` = nothing to re-run."""
    window = thesis_window(roster, entries, end)
    if window is None:
        return None
    first, last = window
    if start is not None:
        first = max(first, start)
    base = thesis_repo.get(conn, roster.thesis_id)
    if base is None:
        return None
    base = with_body(base, body)
    every = set(roster.live_security_ids)
    for s in roster.snapshots:
        every |= s.security_ids
    sessions = trading_sessions(
        con, sorted(every, key=lambda u: u.int), first, last, roster.tenant_id
    )
    out: list[NightInput] = []
    for t in sessions:
        if nights is not None and t not in nights:
            continue
        basket = basket_for_night(base, roster, entries, t, known_by)
        if not any(m.security_id is not None for m in basket):
            continue
        on_night = base.model_copy(update={"basket": basket})
        position = decisions_repo.effective_position(
            conn, on_night, asof=t, known_at=night_cap(t, known_by)
        )
        out.append(NightInput(asof=t, basket=tuple(basket), position=position))
    return ThesisJob(
        thesis_id=roster.thesis_id,
        tenant_id=roster.tenant_id,
        known_by=known_by,
        base=base,
        nights=tuple(out),
    )


# --- the assembly: pure over the mirror -------------------------------------------------------------------


def assemble_night(
    con,
    thesis: Thesis,
    night: date,
    *,
    known_by: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> CallCard:
    """The engine's card for ONE thesis-night: ``thesis`` already carries that night's roster and position;
    the facts are the public mirror at the night's cap. The ONE per-night seam (the sweep and the parity
    measurement both call it)."""
    pit = ReplayPointInTimeData(
        con,
        asof=night,
        known_at=night_cap(night, known_by),
        tenant_id=thesis.tenant_id,
        basket=[m.security_id for m in thesis.basket if m.security_id is not None],
        bounds=call_bounds(cfg),
    )
    return assemble_from_pit(pit, thesis, night, cfg)


def run_job(con, job: ThesisJob, cfg: CallConfig = DEFAULT_CONFIG) -> list[CallCard]:
    """Every night's card for one job, ascending."""
    return [
        assemble_night(
            con,
            job.base.model_copy(update={"basket": list(n.basket), "position": n.position}),
            n.asof,
            known_by=job.known_by,
            cfg=cfg,
        )
        for n in job.nights
    ]


def _worker(args: tuple[str, str, str]) -> tuple[str, list[str]]:
    """Run ONE job: ``(mirror path, job JSON, cfg JSON) -> (thesis id, [card JSON])``. No database — the job
    carries every non-mirror input — so it is the same function in a pool worker and in the parent.
    """
    mirror, job_json, cfg_json = args
    job = ThesisJob.from_json(job_json)
    cfg = CallConfig.model_validate_json(cfg_json)
    con = open_mirror(mirror)
    try:
        return str(job.thesis_id), [c.model_dump_json() for c in run_job(con, job, cfg)]
    finally:
        con.close()


def run_jobs(
    mirror: str | Path,
    jobs: list[ThesisJob],
    *,
    cfg: CallConfig = DEFAULT_CONFIG,
    workers: int = 1,
) -> dict[UUID, list[CallCard]]:
    """Every job's cards on ``mirror``. ``workers > 1`` fans out by thesis over the SAME mirror
    (``backtest/parallel.py``'s pattern, biggest job first); results are keyed by thesis and returned in
    ``jobs`` order, so the output never depends on completion order or worker count (a test pins
    workers=1 vs N byte-identical)."""
    args = [(str(mirror), j.to_json(), cfg.model_dump_json()) for j in jobs]
    if workers <= 1:
        done = dict(_worker(a) for a in args)
    else:
        biggest_first = sorted(
            range(len(jobs)),
            key=lambda i: sum(len(n.basket) for n in jobs[i].nights),
            reverse=True,
        )
        with ProcessPoolExecutor(max_workers=workers) as ex:
            done = dict(ex.map(_worker, [args[i] for i in biggest_first]))
    return {
        j.thesis_id: [CallCard.model_validate_json(c) for c in done[str(j.thesis_id)]] for j in jobs
    }


# --- the whole cut ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    """One edition's inputs, all read from one consistent snapshot."""

    cut: MirrorCut
    rosters: list[ThesisRoster]
    entries: dict[UUID, dict[UUID, Entry]]
    jobs: list[ThesisJob]
    end: date


def latest_recorded_night(conn: psycopg.Connection, *, known_by: datetime) -> date | None:
    """The newest as-of with an HONEST (non-reconstructed) call-of-record row recorded by the cut."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(asof) AS a FROM calls WHERE NOT reconstructed AND recorded_at <= %s",
            (known_by,),
        )
        row = cur.fetchone()
    return row["a"] if row else None


def prepare(
    conn: psycopg.Connection,
    out_dir: str | Path,
    *,
    end: date | None = None,
    known_by: datetime | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    start: date | None = None,
    only: set[UUID] | None = None,
    frozen: dict[UUID, dict[UUID, Entry]] | None = None,
    nights: dict[UUID, set[date]] | None = None,
    bodies: dict[UUID, dict[str, Any]] | None = None,
) -> Prepared:
    """Read EVERYTHING an edition needs inside ONE consistent snapshot: the public-clock mirror (with the
    identity aux) cut at ``known_by`` (default: the snapshot's own instant), the join dates as known at that
    cut, and every thesis's job. ``end`` defaults to the latest recorded night. ``only`` / ``start`` narrow
    the run (a diagnostic). Writes only ``out_dir``.

    S7 (``editions.cut``): ``frozen`` = join dates that WIN over the rule's (an edition's own, carried over;
    a company it never had keeps the rule's exact date); ``nights`` = per thesis, the only nights to run (a
    thesis absent from it is not run); ``bodies`` = per thesis, a frozen thesis body (``thesis_body``).
    """
    with consistent_snapshot(conn) as snapshot_at:
        kb = known_by or snapshot_at
        if kb > snapshot_at:
            raise ValueError(f"a cut ({kb}) cannot be later than the snapshot it is read from")
        manifest = export_snapshot(
            conn, out_dir, tenant_id=tenant_id, clock="public", known_by=kb, identity=True
        )
        cut = MirrorCut(path=Path(out_dir), known_by=kb, snapshot_at=snapshot_at, manifest=manifest)
        last = end or latest_recorded_night(conn, known_by=kb)
        if last is None:
            raise LookupError("no recorded night to run through (pass end=)")
        rosters, entries = derive_all(conn, known_by=kb)
        if frozen:
            entries = {
                tid: {**entries.get(tid, {}), **frozen.get(tid, {})} for tid in {*entries, *frozen}
            }
        rosters = [
            r
            for r in rosters
            if r.tenant_id == tenant_id
            and (only is None or r.thesis_id in only)
            and (nights is None or r.thesis_id in nights)
        ]
        con = open_mirror(cut.path)
        try:
            jobs = [
                j
                for r in rosters
                if (
                    j := build_job(
                        conn,
                        con,
                        r,
                        entries.get(r.thesis_id, {}),
                        end=last,
                        known_by=kb,
                        start=start,
                        nights=nights.get(r.thesis_id) if nights is not None else None,
                        body=(bodies or {}).get(r.thesis_id),
                    )
                )
                is not None
            ]
        finally:
            con.close()
    return Prepared(cut=cut, rosters=rosters, entries=entries, jobs=jobs, end=last)
