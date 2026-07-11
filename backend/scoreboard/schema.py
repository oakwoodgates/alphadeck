from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from domain.call import TriggerRef
from replay.schema import Episode, Outcome

# The Scoreboard's analytical output records — the ``replay/schema.py`` posture: plain records, not
# the domain core. An episode/outcome pair is exactly replay's models (reused, never forked); the
# live-only fields are honesty about the RECORD itself:
#
# - ``status``          open = still armed at the record edge <= asof (replay's ``window_end`` reading);
#                       its return is a RUNNING return to the last bar, not a verdict.
# - ``matured``         the episode's own ``exit_by`` has elapsed (<= asof) — the metrics gate: an
#                       episode is judged only at its own deadline, never mid-flight.
# - ``censored_start``  the member was already armed on the thesis's FIRST recorded card, so the true
#                       arm date is unknowable from the record (the record began mid-arm). Ledger-
#                       visible, excluded from arm-anchored metrics — marked, never reconstructed.


class ScoredEpisode(BaseModel):
    """One arm episode from the record, scored — plus the live record-honesty flags."""

    episode: Episode
    outcome: Outcome
    status: Literal["open", "closed"]
    matured: bool
    censored_start: bool
    triggers_at_arm: list[TriggerRef] = []  # the WHY, from the arm-date card (invariant #6)


class ThesisRecord(BaseModel):
    """One thesis's slice of the Scoreboard: record coverage + its scored episodes. Present even at
    zero episodes (the record span and any accruing warming window are the honest launch state)."""

    thesis_id: UUID
    name: str
    ticker: str | None = None
    basket_size: int = 0
    archived: bool = False
    first_call_asof: date | None = None  # the record span — None = no call-of-record yet
    last_call_asof: date | None = None
    current_state: str | None = None  # the record's edge (<= asof), not a recompute
    current_verdict: str | None = None
    warming_since: date | None = None  # open warming-with-conviction run at the record edge
    episodes: list[ScoredEpisode] = []
    error: str | None = None  # fault isolation: an unreadable historical card, surfaced not raised


class ScoreboardResult(BaseModel):
    """The whole record scored as-of one date (the SB1 analytical result; SB2 adds metrics)."""

    asof: date
    theses: list[ThesisRecord] = []
    n_theses: int = 0
    n_with_record: int = 0
    n_episodes: int = 0
    n_open: int = 0
    n_matured: int = 0
    n_censored: int = 0
