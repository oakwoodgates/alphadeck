from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from domain.config import short_hash
from replay.metrics import MIN_N, compute_metrics
from replay.schema import CallSnapshot, Episode, Outcome
from scoreboard.assemble import _censor_leading_warming
from scoreboard.schema import ReplaySnapshot, ReplayThesisHistory, ScoredEpisode

# The replay snapshot builder: a replay run, flattened into the Scoreboard's own vocabulary (replayed,
# scored arm episodes with the record-honesty flags). ``build_snapshot()`` is PURE and duckdb-free (testable
# without the .[replay] extra), and this module must keep importing in the lean prod image (the lean-import
# test pins it).
#
# Its one caller today is the BACKTEST ledger (``backtest/ledger.py`` / ``backtest/run.py``): `/backtest`
# renders its drill-down through the Scoreboard's components, so its rows are built here. The Scoreboard's
# own replay-alongside panel — and the CLI that wrote its artifact, ``python -m scoreboard.replay_snapshot``
# — were retired by editions S8 (2026-10-23): a stored edition (a frozen public-clock re-run of today's rules)
# replaced it. The window rule the panel used (end at record_began - 1, an overlap marked loud) lives on in
# ``window_overlaps_record`` for whoever builds a snapshot.


class _Realized(Protocol):  # what compute_metrics' withheld leg needs — duck-typed like the scorer
    def first_close_on_or_after(self, security_id: UUID, d: date) -> tuple[date, float] | None: ...
    def last_close_through(self, security_id: UUID, through: date) -> tuple[date, float] | None: ...


@dataclass(frozen=True)
class ThesisMeta:
    """The display metadata the artifact carries per thesis (resolved at snapshot time)."""

    tenant_id: UUID | None
    name: str
    ticker: str | None
    basket_size: int


def _triggers_at_arm(snaps: list[CallSnapshot], ep: Episode):
    """The member's fired evidence on the ARM-DATE snapshot (MemberRow now carries triggers) —
    the same WHY the forward record reads from the arm-date card (invariant #6)."""
    for s in snaps:
        if s.asof == ep.arm_date:
            for m in s.members:
                if m.security_id == ep.security_id and m.tier == "armed":
                    return list(m.triggers)
    return []


def build_snapshot(
    timeline: dict[UUID, list[CallSnapshot]],
    scored: list[tuple[Episode, Outcome]],
    *,
    thesis_meta: dict[UUID, ThesisMeta],
    window_start: date,
    window_end: date,
    pin: datetime,
    generated_at: datetime,
    matured_asof: date,
    record_began: date | None,
    realized: _Realized | None = None,
    single_name_security: dict[UUID, UUID] | None = None,
    roster_fallback_theses: int = 0,
    roster_source_note: str | None = None,
    config_hash: str | None = None,
    code_sha: str | None = None,
) -> ReplaySnapshot:
    """Flatten a replay run into the artifact — pure (no DB, no duckdb, no clock).

    The honesty flags mirror the forward record exactly: ``censored_start`` = already armed on the
    window's FIRST replayed day (true arm date unknowable); ``matured`` = the episode's own exit_by
    elapsed by ``matured_asof`` (the snapshot's generation date — replay scores with unbounded
    forward reads, so maturity is against the data edge, not the window end); metrics judge only
    matured + non-censored episodes (the SAME eligibility rule as the live summary, so the two
    strips are comparable); the withheld metric's timeline is censor-trimmed the same way."""
    by_thesis: dict[UUID, list[tuple[Episode, Outcome]]] = {}
    for ep, out in scored:
        by_thesis.setdefault(ep.thesis_id, []).append((ep, out))

    theses: list[ReplayThesisHistory] = []
    eligible: list[Outcome] = []
    n_censored = 0
    for tid, meta in thesis_meta.items():
        snaps = timeline.get(tid, [])
        first_replayed = snaps[0].asof if snaps else None
        episodes: list[ScoredEpisode] = []
        for ep, out in by_thesis.get(tid, []):
            censored = first_replayed is not None and ep.arm_date == first_replayed
            matured = ep.exit_by is not None and ep.exit_by <= matured_asof
            if censored:
                n_censored += 1
            elif matured:
                eligible.append(out)
            episodes.append(
                ScoredEpisode(
                    episode=ep,
                    outcome=out,
                    status="open" if ep.dearm_date is None else "closed",
                    matured=matured,
                    censored_start=censored,
                    triggers_at_arm=_triggers_at_arm(snaps, ep),
                )
            )
        theses.append(
            ReplayThesisHistory(
                thesis_id=tid,
                tenant_id=meta.tenant_id,
                name=meta.name,
                ticker=meta.ticker,
                basket_size=meta.basket_size,
                episodes=episodes,
            )
        )

    metrics = compute_metrics(
        eligible,
        timeline={tid: _censor_leading_warming(s) for tid, s in timeline.items()},
        realized=realized,
        single_name_security=single_name_security,
    )
    overlaps = record_began is not None and window_end >= record_began
    # THE BANNER SAYS WHAT THIS PANEL IS, in two clauses that answer two different questions.
    #
    # F2 — WHICH DIALS it reflects. The snapshot CLI used to take DEFAULT_CONFIG implicitly, so "today's
    # code + dials" was a claim the artifact could not back. Shortened HERE, server-side, through
    # `domain.config.short_hash` — the one slicing point in the codebase — so the frontend renders the
    # sentence and computes nothing.
    #
    # F4 — WHICH ROSTER it ran on, and this clause REPLACED the blanket "Baskets are not versioned
    # (REPLAY.md known limitation)". They ARE versioned now (`basket_snapshot`, read per session), so the
    # permanent caveat had become false; what remains true is per-run and quantitative — which theses fell
    # back to today's basket, and for how many sessions. Silence means every thesis replayed on a real
    # point-in-time roster, which the blanket sentence could never say. `roster_source_note` is
    # backend-authored (one authority, the `ingest_note` precedent) and speaks in the F11 voice: a
    # recompute, never the recorded call.
    #
    # ORDER IS POLICY THEN ROSTER: the dials are the stronger claim about what the numbers mean, and the
    # roster qualifies whose numbers they are.
    #
    # BOTH clauses are sentence-cased, because both follow a full stop. The roster note is composed
    # lowercase (it also rides a run-report line mid-sentence) and is cased on its FIRST CHARACTER ONLY —
    # NOT `str.capitalize()`, which lowercases the rest and would turn "TODAY's basket" into "today's
    # basket", destroying the emphasis that is the whole point of the word.
    policy_label = f"policy {short_hash(config_hash)}" if config_hash else None
    policy = f" {policy_label.capitalize()}." if policy_label else ""
    roster_clause = (
        f" {roster_source_note[0].upper()}{roster_source_note[1:]}."
        if roster_source_note
        else " Rosters are point-in-time."
    )
    banner = (
        f"REPLAYED — today's code + dials over historical facts (window {window_start} → "
        f"{window_end}, pinned {pin.date()}); NOT the record."
        + policy
        + roster_clause
        + f" {len(eligible)} episodes eligible for metrics "
        f"(matured + non-censored; gate n<{MIN_N})."
        + (
            " WARNING: the window overlaps the forward record — arms may appear in both sections."
            if overlaps
            else ""
        )
    )
    n_episodes = sum(len(t.episodes) for t in theses)
    return ReplaySnapshot(
        generated_at=generated_at.isoformat(),
        window_start=window_start,
        window_end=window_end,
        known_at_pin=pin.isoformat(),
        record_began=record_began,
        window_overlaps_record=overlaps,
        roster_fallback_theses=roster_fallback_theses,
        roster_source_note=roster_source_note,
        config_hash=config_hash,
        code_sha=code_sha,
        policy_label=policy_label,
        banner=banner,
        min_n=MIN_N,
        n_theses=len(theses),
        n_episodes=n_episodes,
        n_censored=n_censored,
        n_eligible=len(eligible),
        metrics=metrics.metrics,
        theses=theses,
    )
