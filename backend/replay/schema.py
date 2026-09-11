from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from domain.call import CallCard, MemberCall, TriggerRef
from domain.enums import Grade, State, Verdict

# The replay OUTPUT models. Deliberately reusable by the future live Scoreboard (the forward twin of
# replay): a CallSnapshot is the flattened CallCard at one as-of, and an arm Episode / Outcome (in
# episodes.py / scoring.py) is what both replay and a live Scoreboard would record + score. Plain
# analytical records — not the domain core.


class MemberRow(BaseModel):
    """One basket member's slice of a CallSnapshot — armed (actionable) or watch (confirmation-only)."""

    security_id: UUID
    tier: str  # "armed" | "watch"
    verdict: Verdict | None = None
    conviction_grade: Grade | None = None
    confirmation_grade: Grade | None = None
    entry_grade: Grade | None = None
    confidence: float | None = None
    exit_by: date | None = None
    arm_until: date | None = None
    lapsing: bool = False
    theme_armed: bool = False
    # the member's own fired evidence (additive — default empty, so hand-built test rows and every
    # pre-existing consumer are unchanged): the live Scoreboard's replay panel reads the WHY from
    # the arm-date snapshot, exactly as the forward record reads it from the arm-date card (#6).
    triggers: list[TriggerRef] = []

    @classmethod
    def from_member(cls, m: MemberCall, tier: str) -> "MemberRow":
        return cls(
            security_id=m.security_id,
            tier=tier,
            verdict=m.verdict,
            conviction_grade=m.conviction_grade,
            confirmation_grade=m.confirmation_grade,
            entry_grade=m.entry_grade,
            confidence=m.confidence,
            exit_by=m.exit_by,
            arm_until=m.arm_until,
            lapsing=m.lapsing,
            theme_armed=m.theme_armed,
            triggers=list(m.triggers),
        )


class CallSnapshot(BaseModel):
    """One thesis's call at one as-of T in a replay — the flattened CallCard the timeline records. The
    headline fields mirror ``armed_members[0]``; ``members`` carries the per-member rows (armed first, then
    watch) so name-selection and per-member arm episodes are derivable."""

    thesis_id: UUID
    asof: date
    state: State
    verdict: Verdict
    conviction_grade: Grade | None = None
    entry_grade: Grade | None = None
    confidence: float | None = None
    armed_security_id: UUID | None = None
    exit_by: date | None = None
    arm_until: date | None = None
    members: list[MemberRow] = []

    @classmethod
    def from_card(cls, card: CallCard) -> "CallSnapshot":
        members = [MemberRow.from_member(m, "armed") for m in card.armed_members]
        members += [MemberRow.from_member(m, "watch") for m in card.watch_members]
        return cls(
            thesis_id=card.thesis_id,
            asof=card.asof,
            state=card.state,
            verdict=card.verdict,
            conviction_grade=card.conviction_grade,
            entry_grade=card.entry_grade,
            confidence=card.confidence,
            armed_security_id=card.armed_security_id,
            exit_by=card.exit_by,
            arm_until=card.arm_until,
            members=members,
        )


class Episode(BaseModel):
    """One arm EPISODE — the scoring unit (Armed is sticky, so per-(thesis,asof) would multi-count). A
    contiguous run during which one basket MEMBER is in ``armed_members``, keyed
    ``(thesis_id, security_id, arm_date)``. Per-member (not just the headline) so name-selection is
    scorable. Carries the entry attributes (captured at ``arm_date``) + the hold clocks, so the scorer is a
    pure function of (episode, realized prices) — it never re-opens a pit."""

    thesis_id: UUID
    security_id: UUID
    is_headline: bool  # was this member the thesis headline (armed_security_id) at arm_date?
    arm_date: date
    last_armed_date: date
    dearm_date: date | None = (
        None  # None when the run reaches the window end (close_reason="window_end")
    )
    close_reason: (
        str  # arm_until_lapsed | conviction_aged_out | managing | window_end | dearmed_other
    )
    warm_date: date | None = (
        None  # the thesis warm (first warming-with-conviction) preceding the arm
    )
    verdict: Verdict | None = None
    entry_grade: Grade | None = None
    conviction_grade: Grade | None = None
    confidence: float | None = None
    theme_armed: bool = False
    exit_by: date | None = (
        None  # the hold horizon at arm — the honest yardstick for the realized return
    )
    arm_until: date | None = None


class Outcome(BaseModel):
    """One arm episode scored against realized forward prices — the SECOND, independent pass. Carries the
    episode's entry attributes (so the metrics need only Outcomes) plus the realized returns over the hold
    window ``[arm_date, exit_by]``. A pure function of (episode, RealizedPrices); never re-opens a pit.
    """

    thesis_id: UUID
    security_id: UUID
    is_headline: bool
    verdict: Verdict | None = None
    entry_grade: Grade | None = None
    conviction_grade: Grade | None = None
    confidence: float | None = None
    theme_armed: bool = False
    close_reason: str = ""
    arm_date: date | None = None
    exit_by: date | None = None
    # realized (forward) — None when prices are insufficient
    entry_close: float | None = None
    exit_close: float | None = None
    exit_date: date | None = None
    forward_return: float | None = None  # arm_date -> exit_by (the hold window) — the timing metric
    arm_until_return: float | None = None  # arm_date -> the arm_until (entry-window) checkpoint
    warm_return: float | None = None  # warm_date -> exit_by (the edge-preservation comparison)
    # --- the excursion pair, on TWO bases. Descriptive only: nothing in replay.metrics reads them, so
    # they move no metric.
    #
    # CLOSE-based (peak/trough) — the maximum favourable / adverse excursion (MFE / MAE) on the SAME
    # basis `forward_return` and `exit_vs_peak_days` already use. `trough_return` reads a real 0.0 for a
    # name that never closed below entry (24% of the measured record); 0.0 is the measurement, not a
    # missing value, and it is available exactly whenever `peak_return` is.
    peak_return: float | None = None
    peak_date: date | None = None
    trough_return: float | None = None
    trough_date: date | None = None
    # WICK-based (the intraday extremes — the worst/best price that actually traded). Null unless EVERY
    # bar in the window carries the column: a partial extreme is not conservative, it is wrong (the true
    # extreme could sit inside the unknown bar), so these NEVER fall back to the close. NB the adverse
    # wick essentially never reads a clean 0 — on the measured record every name traded below its entry
    # close at some point intraday (max -0.2%), so the two adverse fields never agree on "untouched".
    intraday_high_return: float | None = None
    intraday_high_date: date | None = None
    intraday_low_return: float | None = None
    intraday_low_date: date | None = None
    # The scored window's own closes, ascending — the ledger sparkline's tape, VARIABLE length (each
    # episode spans its own [arm_date, exit_date], so these paths share no time axis and a steeper line
    # does NOT mean a faster move). Empty when the window is; the >=2-closes render floor is the
    # display layer's, so the data never pretends a shorter tape is longer.
    path: list[float] = []
    # index into `path` of the last bar <= dearm_date. None when the de-arm fell OUTSIDE the scored
    # window (the run outlived its own horizon) or the episode is still open — which means "no marker",
    # never "never de-armed"; the render site's copy is what keeps the two apart.
    dearm_index: int | None = None
    exit_vs_peak_days: int | None = (
        None  # exit_date - peak_date (>0 = held past the peak; rollover fit)
    )
    # ONE condition: the episode's horizon extends past the end of this NAME'S price tape
    # (`exit_by > tape_edge`, the tape edge read within the reader's own as-of cap). It is a fact
    # about the data, not a judgement about whether to say so: an immature episode is truncated by
    # construction (the asof caps the tape edge too) and the phrasing sites rely on that, while the
    # ledger badge gates additionally on `matured` — a running return falling short of its horizon IS
    # running; a realized one falling short is a caveat on a number presented as final.
    truncated: bool = False
    insufficient_prices: bool = False
