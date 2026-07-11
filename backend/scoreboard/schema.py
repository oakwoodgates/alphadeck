from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from domain.call import TriggerRef
from replay.metrics import MetricResult
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


class EpisodeOperator(BaseModel):
    """The operator's answer to one arm episode (SB3): the earliest take-span inside the episode's
    window, or a pass when nobody took. Prices carry ``inferred`` flags (a logged fill wins; a
    missing one is the close, flagged, never silent); a pass carries no prices — the episode's own
    outcome sits beside it. NO delta/counterfactual fields (parked to v2)."""

    action: Literal["took", "passed"]
    decision_id: UUID
    decision_date: date
    reason: str | None = None
    thesis_level: bool = False  # logged without a name; never guessed onto one (unpriced)
    entry_price: float | None = None
    entry_inferred: bool = False
    exit_price: float | None = None
    exit_inferred: bool = False
    exit_date: date | None = None
    running: bool = False  # still open at asof: the return is running, not realized
    operator_return: float | None = None


class OperatorSpan(BaseModel):
    """A take→close span answering NO armed episode — the off-record row, carrying the stance
    FROZEN on the take at logging time (the record is attribution's source). ``override`` = the
    platform's stance was not armed/managing when the operator entered (the gate's logged
    override, now with its outcome attached)."""

    take_id: UUID
    take_date: date
    security_id: UUID | None = None
    thesis_level: bool = False
    call_state_at_take: str | None = None
    call_verdict_at_take: str | None = None
    override: bool = False
    close_id: UUID | None = None
    close_date: date | None = None
    running: bool = False
    entry_price: float | None = None
    entry_inferred: bool = False
    exit_price: float | None = None
    exit_inferred: bool = False
    exit_date: date | None = None
    operator_return: float | None = None
    reason: str | None = None


class ScoredEpisode(BaseModel):
    """One arm episode from the record, scored — plus the live record-honesty flags."""

    episode: Episode
    outcome: Outcome
    status: Literal["open", "closed"]
    matured: bool
    censored_start: bool
    triggers_at_arm: list[TriggerRef] = []  # the WHY, from the arm-date card (invariant #6)
    operator: EpisodeOperator | None = None  # None = no decision logged: the honest capture gap


class ThesisRecord(BaseModel):
    """One thesis's slice of the Scoreboard: record coverage + its scored episodes. Present even at
    zero episodes (the record span and any accruing warming window are the honest launch state)."""

    thesis_id: UUID
    tenant_id: UUID | None = None  # threads the thesis's tenant to resolution/readers (never wire)
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
    operator_spans: list[OperatorSpan] = []  # off-record take→close spans (overrides live here)
    decision_anomaly: str | None = None  # a log shape the API should prevent — surfaced, not fixed
    n_takes: int = 0
    n_passes: int = 0
    n_overrides: int = 0
    n_voided: int = 0
    error: str | None = None  # fault isolation: an unreadable historical card, surfaced not raised


class ScoreboardSummary(BaseModel):
    """The aggregate layer: replay's claim-tied metric set over ELIGIBLE outcomes only — matured
    (judged at the episode's own exit_by) AND non-censored (the record saw the arm) — plus the
    banner that keeps it honest. Metrics below ``min_n`` are an instrument, never a claim."""

    banner: str
    min_n: int
    n_eligible: int = 0
    record_began: date | None = None
    metrics: list[MetricResult] = []


class ScoreboardResult(BaseModel):
    """The whole record scored as-of one date; ``summary`` rides once the metric pass has run
    (``assemble_scoreboard``) — the bare record walk leaves it None."""

    asof: date
    theses: list[ThesisRecord] = []
    n_theses: int = 0
    n_with_record: int = 0
    n_episodes: int = 0
    n_open: int = 0
    n_matured: int = 0
    n_censored: int = 0
    n_takes: int = 0  # the operator track (SB3): non-voided decisions <= asof
    n_passes: int = 0
    n_overrides: int = 0  # off-record takes against a not-armed stance
    n_voided: int = 0  # decisions later voided (excluded from all math, still counted)
    summary: ScoreboardSummary | None = None
