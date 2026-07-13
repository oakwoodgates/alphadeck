from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import SignalEvent
from domain.thesis import Thesis
from signals.base import SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance

# A theme conviction is the weaker key — a modest conviction score, kept below an own catalyst's flip
# score so an own name out-tiebreaks a theme-armed one on conviction_score too (the is_own axis already
# orders own-above-theme; this just reinforces it). Confidence is capped at starter_confidence_cap anyway.
_THEME_SCORE = 0.45


def _liveness(fact: dict[str, Any], cfg: CallConfig) -> int:
    """The theme conviction's relevance HORIZON in days — its operator-set expiry, DECOUPLED from grade
    (like a catalyst). Runs to ``horizon_end`` when ratified with one, else the configured default.
    """
    horizon_end = fact.get("horizon_end")
    if horizon_end is not None:
        return max((horizon_end - fact["valid_from"]).days, 1)
    return cfg.theme_conviction_default_horizon_days


def strongest_live_fact(
    facts: list[dict[str, Any]], asof: date, cfg: CallConfig
) -> dict[str, Any] | None:
    """The most-recent LIVE theme conviction for a thesis (or None). Live = the event date is within its
    horizon as-of; a theme conviction expires unless re-ratified — no zombie narratives (rule 3)."""
    live = [f for f in facts if entry_signal_is_live(f["valid_from"], _liveness(f, cfg), asof)]
    if not live:
        return None
    return max(live, key=lambda f: f["valid_from"])  # the operator's latest live ratification


def detect_fact(
    pit: SignalPointInTimeData,
    thesis_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> dict[str, Any] | None:
    """The only DB-touching step: read the thesis's operator-ratified theme conviction as-of. Returns the
    live fact, or None when none is ratified / all have expired."""
    return strongest_live_fact(pit.theme_conviction_facts(thesis_id), asof, cfg)


def _member_kinds(events: list[SignalEvent], security_id: UUID, kinds) -> list[SignalEvent]:
    return [e for e in events if e.fired and e.security_id == security_id and e.kind in kinds]


def broadcast(
    thesis: Thesis,
    member_events: list[SignalEvent],
    theme_fact: dict[str, Any] | None,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> list[SignalEvent]:
    """Pure: broadcast the theme conviction onto each ELIGIBLE basket member as a Key-1 FALLBACK event.

    The entire M5b eligibility discipline lives here, never in the guarded assembler:
      - **rule 4** — the member has a LIVE, VOLUME-BACKED confirmation (a confirmation-kind event of
        grade CORE); momentum-only (flip) breakouts are excluded, and it can't reach outside the basket;
      - **rule 5 / 7** — the member has NO live OWN-conviction event (own wins; a *lapsed* own conviction,
        absent from the stream because its detector returned None, falls back here — the floor).
    The event is emitted at flip (**rule 2** — capped at starter; belief never mints a core); its liveness
    is the theme's horizon (**rule 3**), so it expires and drives the member's exit_by/runway. From there
    the assembler treats it as an ordinary conviction event — co-location, ranking, the starter cap, and
    the risk veto all follow with no assembler changes. ``member_events`` are the per-member detector
    outputs (each already live), so presence == live — no extra liveness re-check is needed.
    """
    if theme_fact is None:
        return []
    liveness = _liveness(theme_fact, cfg)
    provenance = [source_provenance(theme_fact["source"], theme_fact["source_ref"])]
    events: list[SignalEvent] = []
    for member in thesis.basket:
        sec = member.security_id
        if sec is None:
            continue  # unresolved member — never emit a None-keyed event (would pollute conv_secs)
        if _member_kinds(member_events, sec, cfg.own_conviction_kinds):
            continue  # own conviction wins (rule 5); a lapsed own is absent -> the member falls back (7)
        volume_backed = any(
            e.grade is Grade.CORE for e in _member_kinds(member_events, sec, cfg.confirmation_kinds)
        )
        if not volume_backed:
            continue  # rule 4: needs its OWN volume-backed (CORE) confirmation; momentum-only excluded
        events.append(
            fired_signal(
                detector="theme_conviction",
                security_id=sec,
                role=Role.ENTRY_TRIGGER,
                kind=Kind.THEME_CONVICTION,
                grade=Grade.FLIP,  # rule 2: capped at starter — a theme conviction never mints a core
                score=_THEME_SCORE,
                label=theme_fact["label"],
                alpha_liveness_days=liveness,
                provenance=provenance,
                asof=theme_fact["valid_from"],
            )
        )
    return events
