from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import psycopg

from domain.call import CallCard, TriggerRef
from domain.enums import State
from domain.thesis import Thesis
from replay.episodes import derive_episodes
from replay.schema import CallSnapshot
from replay.scoring import score_episode
from repositories import calls_repo, thesis_repo
from scoreboard.prices import PgRealizedPrices
from scoreboard.schema import ScoreboardResult, ScoredEpisode, ThesisRecord

# The record read + episode derivation. The scoring source is the CALLS LOG (what the platform
# actually said), never a recompute: ``latest_for_thesis`` -> ascending ``CallSnapshot``s ->
# ``derive_episodes`` (replay's, as-is) -> ``score_episode`` against asof-capped realized closes.
# A day with no row means the record last spoke on the prior row (weekends / cron gaps) — episode
# boundaries stay exact because a membership change always recorded a row that day.


def thesis_timeline(
    conn: psycopg.Connection, thesis_id: UUID, asof: date
) -> tuple[list[CallSnapshot], dict[date, CallCard]]:
    """The thesis's call-of-record timeline up to ``asof``, ascending, plus the cards by as-of
    (for trigger enrichment). ``latest_for_thesis`` dedups to the final card per as-of — its own
    docstring: the read a scoreboard wants."""
    cards = [c for c in calls_repo.latest_for_thesis(conn, thesis_id) if c.asof <= asof]
    cards.reverse()  # newest-first -> ascending
    return [CallSnapshot.from_card(c) for c in cards], {c.asof: c for c in cards}


def _triggers_at_arm(card: CallCard | None, security_id: UUID) -> list[TriggerRef]:
    """The member's own fired evidence on the arm-date card (the WHY behind the arm). Falls back to
    the headline ``triggers_fired`` filtered by name (pre-M5 cards had no per-member triggers)."""
    if card is None:
        return []
    for m in card.armed_members:
        if m.security_id == security_id:
            return m.triggers
    return [t for t in card.triggers_fired if t.security_id == security_id]


def _warming_since(snaps: list[CallSnapshot]) -> date | None:
    """The start of the OPEN warming-with-conviction run at the record edge — the withheld window
    accruing right now (the honest launch-state signal for a thesis with zero episodes)."""
    since: date | None = None
    for s in reversed(snaps):
        if s.state is State.WARMING and s.conviction_grade is not None:
            since = s.asof
        else:
            break
    return since


def derive_thesis_record(
    conn: psycopg.Connection,
    thesis: Thesis,
    asof: date,
    *,
    known_at: datetime | None = None,
) -> tuple[ThesisRecord, list[CallSnapshot]]:
    """One thesis's record scored as-of: episodes from the log, outcomes against asof-capped prices,
    plus the record-honesty flags. Returns the snapshots too (SB2 feeds them to the metric set).

    - ``status``: open iff the run reached the record edge un-dearmed (``dearm_date is None``).
    - ``matured``: the episode's own ``exit_by`` elapsed (<= asof) — judged only at its deadline.
    - ``censored_start``: armed already on the thesis's FIRST recorded card — the record began
      mid-arm, the true arm date is unknowable; marked, never reconstructed (no backfill).
    """
    snaps, cards_by_asof = thesis_timeline(conn, thesis.id, asof)
    record = ThesisRecord(
        thesis_id=thesis.id,
        tenant_id=thesis.tenant_id,
        name=thesis.name,
        ticker=thesis.ticker,
        basket_size=len(thesis.basket),
        archived=thesis.archived_at is not None,
        first_call_asof=snaps[0].asof if snaps else None,
        last_call_asof=snaps[-1].asof if snaps else None,
        current_state=snaps[-1].state.value if snaps else None,
        current_verdict=snaps[-1].verdict.value if snaps else None,
        warming_since=_warming_since(snaps),
    )
    if not snaps:
        return record, snaps

    # tenant threading: the thesis's own tenant scopes every price read (never the default here)
    prices = PgRealizedPrices(conn, tenant_id=thesis.tenant_id, cap=asof, known_at=known_at)
    first_recorded = snaps[0].asof
    for ep in derive_episodes(snaps):
        record.episodes.append(
            ScoredEpisode(
                episode=ep,
                outcome=score_episode(ep, prices),
                status="open" if ep.dearm_date is None else "closed",
                matured=ep.exit_by is not None and ep.exit_by <= asof,
                censored_start=ep.arm_date == first_recorded,
                triggers_at_arm=_triggers_at_arm(cards_by_asof.get(ep.arm_date), ep.security_id),
            )
        )
    return record, snaps


def scoreboard_records(
    conn: psycopg.Connection,
    asof: date,
    *,
    include_archived: bool = True,
    known_at: datetime | None = None,
) -> tuple[ScoreboardResult, dict[UUID, list[CallSnapshot]], dict[UUID, UUID]]:
    """Every thesis's record scored as-of (archived INCLUDED by default — the record is not erased
    by archiving; it just stops accruing). Per-thesis fault isolation: an unreadable historical card
    (the log outlives schema changes; ``DomainModel`` is ``extra="forbid"``) becomes a visible
    ``ThesisRecord.error``, never a raised 500 — siblings score unaffected.

    Returns ``(result, timelines, single_name_security)`` — the last is thesis_id -> its sole
    resolved member's security_id (replay's ``_single_name_security`` shape, computed here where the
    loaded theses are in scope), the unit the withheld-arm metric can price."""
    result = ScoreboardResult(asof=asof)
    timelines: dict[UUID, list[CallSnapshot]] = {}
    single_name: dict[UUID, UUID] = {}
    for thesis in thesis_repo.list_all(conn, include_archived=include_archived):
        sids = [m.security_id for m in thesis.basket if m.security_id is not None]
        if len(sids) == 1:
            single_name[thesis.id] = sids[0]
        try:
            record, snaps = derive_thesis_record(conn, thesis, asof, known_at=known_at)
            timelines[thesis.id] = snaps
        except Exception as e:  # noqa: BLE001 — one thesis's bad card never blanks the Scoreboard
            record = ThesisRecord(
                thesis_id=thesis.id,
                tenant_id=thesis.tenant_id,
                name=thesis.name,
                ticker=thesis.ticker,
                basket_size=len(thesis.basket),
                archived=thesis.archived_at is not None,
                error=f"{type(e).__name__}: {e}",
            )
        result.theses.append(record)
    result.n_theses = len(result.theses)
    result.n_with_record = sum(1 for t in result.theses if t.first_call_asof is not None)
    result.n_episodes = sum(len(t.episodes) for t in result.theses)
    result.n_open = sum(1 for t in result.theses for e in t.episodes if e.status == "open")
    result.n_matured = sum(1 for t in result.theses for e in t.episodes if e.matured)
    result.n_censored = sum(1 for t in result.theses for e in t.episodes if e.censored_start)
    return result, timelines, single_name
