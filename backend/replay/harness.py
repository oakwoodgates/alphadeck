from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import duckdb
import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.market_time import known_at_for_asof
from domain.thesis import Thesis
from pipeline.core import assemble_from_pit
from replay.pit import ReplayPointInTimeData
from replay.schema import CallSnapshot
from repositories import thesis_repo


def trading_sessions(
    con: duckdb.DuckDBPyConnection,
    security_ids: list[UUID],
    start: date,
    end: date,
    tenant_id: UUID,
) -> list[date]:
    """The real EOD sessions to sweep — the union of ``fact_price_eod.d`` over the given securities within
    ``[start, end]`` (so we step on actual trading days, not weekends/holidays with a stale tape).
    """
    if not security_ids:
        return []
    placeholders = ", ".join("?" for _ in security_ids)
    rows = con.execute(
        f"SELECT DISTINCT d FROM fact_price_eod "
        f"WHERE tenant_id = ? AND security_id IN ({placeholders}) AND d BETWEEN ? AND ? "
        f"ORDER BY d",
        [str(tenant_id), *[str(s) for s in security_ids], start, end],
    ).fetchall()
    return [r[0] for r in rows]


# --- the roster clock (F4) -----------------------------------------------------------------------------
# WHICH NAMES WERE IN THE BASKET AT T. Until now the harness replayed every T on the LIVE roster: it was
# handed a Thesis loaded by `thesis_repo.list_all` (a plain `SELECT * FROM basket_member`, no time axis) and
# reused it for the whole sweep. Harmless while baskets are static, and a real lookahead vector the moment
# one changes over the window — a member added after T was still replayed at T, which is exactly the shape
# the record's own F12 fix closed for serve / cron / backfill. `basket_snapshot` (migration 0043) and
# `thesis_repo.get_asof` already existed; this was a WIRING gap, not an absent capability.
#
# THE CLOCK IS PER-T, NOT PER-RUN, and that is the whole decision (operator, reading B). The harness pins
# ONE `known_at` for the sweep — the determinism pin — so the obvious wiring ("resolve the roster at the
# pin") would resolve it ONCE and, for the `scoreboard.replay_snapshot` path which pins `now`, return
# today's roster: byte-identical to the bug. The roster is therefore resolved at
#
#     known_at = min(pin, known_at_for_asof(T))      == known_at_for_asof(T, now=pin)
#
# the SAME market-day cap the serve path adopted for its scrub-back (`domain.market_time`, INVARIANTS #4):
# the end of T's MARKET day, never later than the run's own pin. One definition of "what was knowable by
# the end of that day", shared with `serve_known_at` rather than re-derived here.
#
# THE ASYMMETRY IS DELIBERATE AND CONSERVATIVE: the ROSTER is capped at T while the FACTS stay capped at the
# run's single pin (the determinism pin, usually "everything we know now"). So the replay can know a fact
# sooner than it knows a membership. That is the safe direction — it can never invent a member the thesis
# did not have — and it is temporary: the backtest's public-clock mode will move the FACT axis onto the same
# per-T clock, at which point the two axes are lockstep and this comment's caveat disappears. F4 lays that
# rail; it does not change the fact axis today.
#
# THE HONEST RESIDUAL, which no wiring can fix: `basket_snapshot` history begins 2026-09-15, so every T
# before that has no qualifying snapshot and falls back to the live roster. The fallback is the right
# behavior (#9 — never replay a thesis with an empty basket because we lack its history) but it MUST be
# said out loud, which is what `RosterSource` carries to the run output and the panel banner.


@dataclass(frozen=True)
class RosterSource:
    """Where one thesis's roster came from across the sweep — the label behind every replayed episode.

    ``source`` is ``"snapshot"`` only when EVERY replayed T resolved a real point-in-time roster; one
    fallback day makes the whole thesis ``"live_fallback"``, because a timeline stitched from both is a
    counterfactual on membership and rounding that to "snapshot" would be the silent half-truth this field
    exists to prevent. The day counts keep it quantitative rather than binary: a thesis whose window is 2
    fallback days out of 300 is a very different artifact from one that is 300 out of 300, and the banner
    can say which."""

    source: str  # "snapshot" | "live_fallback"
    fallback_days: int  # replayed sessions with NO qualifying snapshot (the live roster stood in)
    total_days: int  # replayed sessions in the window (0 = the thesis never replayed)


@dataclass(frozen=True)
class ReplayResult:
    """A whole replay: the per-thesis call timeline PLUS where each thesis's roster came from.

    A named pair rather than a bare tuple so a caller cannot silently unpack it the wrong way round, and so
    the roster provenance is impossible to drop on the floor — every consumer that wants the timelines has
    the label in the same object."""

    timelines: dict[UUID, list[CallSnapshot]]
    roster_sources: dict[UUID, RosterSource]

    @property
    def fallback_theses(self) -> int:
        """How many theses replayed on the live roster for at least one session."""
        return sum(1 for r in self.roster_sources.values() if r.source == "live_fallback")

    def note(self) -> str | None:
        """The one-line roster caption for a run report / the panel banner, or ``None`` when every thesis
        replayed on a real point-in-time roster. Backend-authored copy, one authority (the ``ingest_note``
        precedent), in the F11 voice: this says the recompute is a recompute, never that it is the record.
        """
        n = self.fallback_theses
        if not n:
            return None
        days = sum(
            r.fallback_days for r in self.roster_sources.values() if r.source == "live_fallback"
        )
        return (
            f"{n} of {len(self.roster_sources)} theses recomputed on TODAY's basket for "
            f"{days} session(s) — no roster snapshot existed that far back, so their membership is a "
            "labeled counterfactual, not the roster of record"
        )


def replay_thesis(
    con: duckdb.DuckDBPyConnection,
    thesis: Thesis,
    *,
    start: date,
    end: date,
    known_at: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    conn: psycopg.Connection | None = None,
) -> list[CallSnapshot]:
    """One thesis's call timeline across its real trading sessions in the window — the TIMELINE-ONLY view.

    Unchanged contract: callers that hold a thesis and want its snapshots get exactly that. Pass ``conn``
    to get the F4 per-T roster read; ``replay_thesis_with_roster`` is the same sweep when you also need to
    know WHERE the roster came from."""
    return replay_thesis_with_roster(
        con,
        thesis,
        start=start,
        end=end,
        known_at=known_at,
        cfg=cfg,
        tenant_id=tenant_id,
        conn=conn,
    )[0]


def replay_thesis_with_roster(
    con: duckdb.DuckDBPyConnection,
    thesis: Thesis,
    *,
    start: date,
    end: date,
    known_at: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    conn: psycopg.Connection | None = None,
) -> tuple[list[CallSnapshot], RosterSource]:
    """Sweep one thesis's call across its real trading sessions in the window, running the REAL pipeline
    (``assemble_from_pit``) over a replay pit capped at each ``(T, known_at)``. ZERO forward knowledge —
    the loop only records snapshots; scoring is a separate pass (``replay.scoring``).

    ``conn`` (the operational SoR) enables the F4 per-T ROSTER read: at each T the basket is re-resolved
    through ``thesis_repo.get_asof`` at ``known_at_for_asof(T, now=known_at)`` — see the block comment
    above for why the clock is per-T and why the fact axis deliberately is not (yet). ``conn=None`` keeps
    the pre-F4 behavior (the passed thesis's live roster, every day) for the callers that already hold a
    thesis and only want a timeline; it reports ``live_fallback`` for every session rather than claiming a
    point-in-time roster it never read.

    The sessions are resolved ONCE from the passed thesis's roster: which days the window contains is a
    property of the tape, and re-deriving it per T from a shifting roster would make the sweep's own step
    depend on its output. Only WHICH NAMES the call sees moves with T."""
    sids = [m.security_id for m in thesis.basket if m.security_id is not None]
    sessions = trading_sessions(con, sids, start, end, tenant_id)
    snapshots: list[CallSnapshot] = []
    fallback_days = 0
    for t in sessions:
        roster = thesis
        if conn is not None:
            roster_known_at = known_at_for_asof(t, known_at)
            if thesis_repo.snapshot_exists_asof(conn, thesis.id, roster_known_at):
                roster = thesis_repo.get_asof(conn, thesis.id, roster_known_at) or thesis
            else:
                fallback_days += 1  # no snapshot that far back — the live roster stands in, LOUDLY
        pit = ReplayPointInTimeData(con, asof=t, known_at=known_at, tenant_id=tenant_id)
        snapshots.append(CallSnapshot.from_card(assemble_from_pit(pit, roster, t, cfg)))
    source = RosterSource(
        source="snapshot" if conn is not None and fallback_days == 0 else "live_fallback",
        fallback_days=fallback_days if conn is not None else len(sessions),
        total_days=len(sessions),
    )
    return snapshots, source


def replay_all(
    conn: psycopg.Connection,
    con: duckdb.DuckDBPyConnection,
    *,
    start: date,
    end: date,
    known_at: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> ReplayResult:
    """Replay every thesis over the window, resolving each one's ROSTER point-in-time at every session
    (F4 — see the block comment above). Returns the per-thesis timelines PLUS the roster provenance.
    ``cfg`` is a parameter so the recalibration pass (step 2) can sweep the dials.

    Thesis DEFINITIONS other than the roster (narrative, catalysts, kill criteria) are still read from the
    current SoR, as is ``security_master`` — that half of ``docs/REPLAY.md``'s KNOWN LIMITATION stands.
    """
    timelines: dict[UUID, list[CallSnapshot]] = {}
    roster_sources: dict[UUID, RosterSource] = {}
    for thesis in thesis_repo.list_all(conn):
        snaps, source = replay_thesis_with_roster(
            con,
            thesis,
            start=start,
            end=end,
            known_at=known_at,
            cfg=cfg,
            tenant_id=tenant_id,
            conn=conn,
        )
        timelines[thesis.id] = snaps
        roster_sources[thesis.id] = source
    return ReplayResult(timelines=timelines, roster_sources=roster_sources)
