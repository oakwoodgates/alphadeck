from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, TriggerRef
from domain.enums import Kind, State
from domain.thesis import Thesis
from replay.episodes import derive_episodes
from replay.schema import CallSnapshot, Episode, MemberRow
from replay.scoring import score_episode
from repositories import calls_repo, decisions_repo, thesis_repo
from scoreboard import provenance
from scoreboard.decisions import attach_operator_track
from scoreboard.prices import PgRealizedPrices, market_tape_edge
from scoreboard.schema import ScoreboardResult, ScoredEpisode, ThesisRecord, Transition

# The record read + episode derivation. The scoring source is the CALLS LOG (what the platform
# actually said), never a recompute: ``latest_for_thesis`` -> ascending ``CallSnapshot``s ->
# ``derive_episodes`` (replay's, as-is) -> ``score_episode`` against asof-capped realized closes.
# A day with no row means the record last spoke on the prior row (weekends / cron gaps) — episode
# boundaries stay exact because a membership change always recorded a row that day.
#
# HONEST ROWS ONLY. That assumption holds for the NIGHTLY record; it is exactly what a reconstructed
# row (``pipeline.backfill``, ``calls.reconstructed``, migration 0042) breaks: a reconstruction ran on
# TODAY's basket (``basket_member`` is full-replace — the roster on a past night is unknowable) for a
# thesis that may not have existed yet, so it can never be shown honest. THE RULE: a reconstructed row
# never defines an episode boundary. ``thesis_timeline`` reads with ``include_reconstructed=False`` —
# the filter runs BEFORE the per-as-of dedup, so every downstream consumer (``derive_episodes``,
# ``score_episode``, the metrics, the arm-day trigger enrichment, ``_dearm_detail``, the provenance
# stamp) sees ONE honest list, and a night that carries both a nightly row and a later reconstruction
# scores the nightly row. Reconstructed rows stay in the log (reversible; the evidence the backfill
# happened) and are REPORTED — the summary names the nights it has nothing honest for (every row on
# the night reconstructed; a mixed night is scored from its honest rows) — not scored. MEASURED when the
# rule landed (dev copy, 2026-09-12): of 106 episode starts that sat on a reconstructed night, 48
# survive with a later (honest) arm date, 56 drop (armed only on reconstructed rows), 2 drop (the
# thesis did not exist). A gap left by the exclusion reads exactly like a cron gap: the record last
# spoke on the prior HONEST row.


def thesis_timeline(
    conn: psycopg.Connection, thesis_id: UUID, asof: date
) -> tuple[list[CallSnapshot], dict[date, CallCard]]:
    """The thesis's HONEST call-of-record timeline up to ``asof``, ascending, plus the cards by as-of
    (for trigger enrichment). ``latest_for_thesis`` dedups to the final card per as-of — its own
    docstring: the read a scoreboard wants — with reconstructed rows EXCLUDED before that dedup (the
    header rule: a reconstructed row never defines an episode boundary)."""
    cards = [
        c
        for c in calls_repo.latest_for_thesis(conn, thesis_id, include_reconstructed=False)
        if c.asof <= asof
    ]
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


def _dearm_detail(card: CallCard | None, security_id: UUID) -> str | None:
    """The composed WHY behind a ``dearmed_other`` close (Slice C1) — from the de-arm-day card, in
    priority order: the member's own fired risk labels (the risks that ended the run), then the
    card's first ``missing[]`` entry (the key that un-turned — deliberately BEFORE the state phrase:
    ``missing`` is non-empty exactly when a key is un-turned, so state-first would make it dead
    code), then the state fallback, then the member-only case (the thesis stayed Armed on another
    name — an armed card's ``missing`` is empty by construction), then None (no card recorded on
    the de-arm day — a record gap, said as silence, never guessed). Backend-authored copy — ONE
    authority for the "why" (the ``ingest_note`` precedent). The caller gates on
    ``close_reason == "dearmed_other"``; the other four tokens self-explain (#7)."""
    if card is None:
        return None
    risks = [r.label for r in card.risk_signals if r.security_id == security_id]
    if risks:
        return " · ".join(risks[:2])
    if card.missing:
        return f"now missing: {card.missing[0]}"
    if card.state in (State.WARMING, State.INCUBATING):
        return f"thesis fell back to {card.state.value.capitalize()}"
    if card.state is State.ARMED:
        return "left the armed set (thesis still Armed)"
    return None


def _risk_events(cards_by_asof: dict[date, CallCard], ep: Episode) -> list[TriggerRef]:
    """The member's risk-signal tape over the run (Slice C2): walk the recorded cards over the
    CLOSED interval ``[arm_date, dearm_date or last_armed_date]`` (arm day included — a risk live
    AT arm haircut the arm's own setup strength, and omitting it would show a clean tape for an arm
    the record itself discounted; de-arm day included — its risk is what ended the run, exactly what
    ``_dearm_detail`` reads; an open episode's ``last_armed_date`` IS the record edge), collect the
    risks attributed to the member, and DEDUPE by the raw ``(kind, event_date)`` — a live risk
    re-fires on every daily card under a stable fact-anchored event date. Different kinds on the
    same day are distinct (#9). A ``None`` event_date keys as ``(kind, None)`` — its re-fires are
    the same live risk — and is stamped with the FIRST card-asof it appeared on (a recorded fact:
    when the record first said it). Known, accepted limit: two DISTINCT same-kind risks sharing an
    event date on one member would collapse to one row — in practice the assembler emits one risk
    event per (kind, member) per card. Order: chronological by first appearance, wire order within
    a card."""
    walk_end = ep.dearm_date or ep.last_armed_date
    seen: set[tuple[Kind, date | None]] = set()
    out: list[TriggerRef] = []
    for asof in sorted(d for d in cards_by_asof if ep.arm_date <= d <= walk_end):
        for r in cards_by_asof[asof].risk_signals:
            if r.security_id != ep.security_id:
                continue
            key = (r.kind, r.event_date)
            if key in seen:
                continue
            seen.add(key)
            out.append(r if r.event_date is not None else r.model_copy(update={"event_date": asof}))
    return out


# The three per-member fields the record trail diffs (Slice C3) — the CALL-read of the member, and
# nothing that wobbles daily (confidence is deliberately out: a per-day float would spam a trail
# whose whole point is the rare, meaningful shift).
_TRANSITION_FIELDS = ("verdict", "entry_grade", "conviction_grade")


def _transitions(snaps: list[CallSnapshot], ep: Episode) -> list[Transition]:
    """The member's intra-run RECORD changes over ``[arm_date, last_armed_date]`` (Slice C3):
    consecutive-card diffs of verdict / entry_grade / conviction_grade on the member's own armed
    row. The first card is the baseline (no emission — the episode already carries the at-arm
    values); a change across a weekend/cron gap lands on the LATER card's asof (a recorded fact:
    when the record first said it). By ``derive_episodes`` construction the member is armed on
    every snap in the interval; a missing row is skipped defensively, never invented."""
    prev: MemberRow | None = None
    out: list[Transition] = []
    for s in snaps:
        if not (ep.arm_date <= s.asof <= ep.last_armed_date):
            continue
        row = next(
            (m for m in s.members if m.tier == "armed" and m.security_id == ep.security_id), None
        )
        if row is None:
            continue
        if prev is not None:
            for field in _TRANSITION_FIELDS:
                a, b = getattr(prev, field), getattr(row, field)
                if a != b:
                    out.append(
                        Transition(
                            asof=s.asof,
                            field=field,
                            from_value=a.value if a is not None else None,
                            to_value=b.value if b is not None else None,
                        )
                    )
        prev = row
    return out


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
    market_edge: date | None = None,
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
    # tenant threading: the thesis's own tenant scopes every price read (never the default here).
    # ``market_edge`` is the REQUEST-level market tape edge, resolved once per tenant by the caller
    # and threaded through: it is the same answer for every thesis in a tenant and it costs a scan,
    # so a reader resolving its own would put that scan on all 12 theses. None = resolve lazily
    # (the single-thesis / standalone path).
    prices = PgRealizedPrices(
        conn,
        tenant_id=thesis.tenant_id,
        cap=asof,
        known_at=known_at,
        market_edge=market_edge,
    )
    if snaps:
        first_recorded = snaps[0].asof
        episodes = list(derive_episodes(snaps))
        triggers = [
            _triggers_at_arm(cards_by_asof.get(ep.arm_date), ep.security_id) for ep in episodes
        ]
        # Record-provenance (2d) — composed AFTER scoring, from reads the scoring path never sees:
        # the winning arm-date rows' R2b stamps + ONE batched thaw-lag query over every cited form4
        # accession. The flags segment/annotate only; ``score_episode``'s inputs are untouched.
        # the SAME honest filter as thesis_timeline, so the stamp belongs to the row that was scored
        health = (
            calls_repo.ingest_health_for_thesis(conn, thesis.id, include_reconstructed=False)
            if episodes
            else {}
        )
        lags = (
            provenance.thaw_lags(
                conn,
                provenance.form4_accessions([t for ts in triggers for t in ts]),
                tenant_id=thesis.tenant_id or DEFAULT_TENANT_ID,
                known_at=known_at,
            )
            if episodes
            else {}
        )
        for ep, trigs in zip(episodes, triggers):
            prov = provenance.derive_episode_provenance(
                ep.arm_date, trigs, health=health, lags=lags
            )
            record.episodes.append(
                ScoredEpisode(
                    episode=ep,
                    outcome=score_episode(ep, prices),
                    status="open" if ep.dearm_date is None else "closed",
                    matured=ep.exit_by is not None and ep.exit_by <= asof,
                    censored_start=ep.arm_date == first_recorded,
                    arm_ingest_fresh=prov.arm_ingest_fresh,
                    freeze_era=prov.freeze_era,
                    thaw_lag_days=prov.thaw_lag_days,
                    ingest_flagged=prov.ingest_flagged,
                    ingest_note=prov.ingest_note,
                    triggers_at_arm=trigs,
                    # Slice C — composed ONLY for the one opaque token (the others self-explain, #7);
                    # dict lookups over the cards already in hand, no new queries.
                    dearm_detail=(
                        _dearm_detail(cards_by_asof.get(ep.dearm_date), ep.security_id)
                        if ep.close_reason == "dearmed_other"
                        else None
                    ),
                    risk_events=_risk_events(cards_by_asof, ep),
                    transitions=_transitions(snaps, ep),
                )
            )

    # the operator track (SB3): the decision log joined to the episodes it answered — runs even
    # with no record yet (a decision can predate the first call-of-record; it rides off-record)
    rows = decisions_repo.list_for_thesis(conn, thesis.id, tenant_id=thesis.tenant_id)
    if rows:
        record.operator_spans, counts, record.decision_anomaly = attach_operator_track(
            record.episodes, rows, prices, asof
        )
        record.n_takes = counts["takes"]
        record.n_passes = counts["passes"]
        record.n_overrides = counts["overrides"]
        record.n_voided = counts["voided"]
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
    # The market tape edge (``truncated``'s second leg) is per TENANT, not per thesis or per episode,
    # and it costs a seq scan. Resolve it at most once per tenant for the whole request and hand it
    # down; recomputing it per thesis would multiply that scan by the thesis count for an answer that
    # cannot differ between them.
    market_edges: dict[UUID, date | None] = {}
    for thesis in thesis_repo.list_all(conn, include_archived=include_archived):
        sids = [m.security_id for m in thesis.basket if m.security_id is not None]
        if len(sids) == 1:
            single_name[thesis.id] = sids[0]
        if thesis.tenant_id not in market_edges:
            market_edges[thesis.tenant_id] = market_tape_edge(
                conn, tenant_id=thesis.tenant_id, cap=asof, known_at=known_at
            )
        try:
            record, snaps = derive_thesis_record(
                conn, thesis, asof, known_at=known_at, market_edge=market_edges[thesis.tenant_id]
            )
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
    result.n_ingest_flagged = sum(1 for t in result.theses for e in t.episodes if e.ingest_flagged)
    result.n_takes = sum(t.n_takes for t in result.theses)
    result.n_passes = sum(t.n_passes for t in result.theses)
    result.n_overrides = sum(t.n_overrides for t in result.theses)
    result.n_voided = sum(t.n_voided for t in result.theses)
    return result, timelines, single_name
