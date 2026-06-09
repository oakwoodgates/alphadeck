from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field

from domain.base import DomainModel
from domain.enums import Grade, Kind, State, Verdict
from domain.signal import Provenance
from domain.thesis import Catalyst


class KeyState(DomainModel):
    """One of the two keys (Conviction / Confirmation) rendered on the call card."""

    turned: bool
    label: str
    detail: str | None = None


class TriggerRef(DomainModel):
    label: str
    kind: Kind
    grade: Grade | None = None
    security_id: UUID  # the security this trigger fired on — drives issuer-CIK URL resolution
    sources: list[Provenance] = Field(default_factory=list)


class MemberCall(DomainModel):
    """One basket member's own call — the unit of the per-member ranked menu (M5 Part A).

    A theme thesis no longer collapses to a single headline: `armed_members` holds each co-located member's
    own call, ranked (freshness band on liveness runway primary, grade within); `watch_members` holds the
    confirmation-only members ("moving, no conviction yet — watch"). The thesis-level CallCard fields below
    reflect the HEADLINE = `armed_members[0]`.
    """

    security_id: UUID  # the member; the API resolves it to a ticker (like TriggerRef)
    verdict: Verdict | None = None  # armed: the member's verdict; watch: None (not actionable)
    conviction_grade: Grade | None = None
    confirmation_grade: Grade | None = None
    entry_grade: Grade | None = None  # the weaker key — None for a watch member (no conviction)
    confidence: float | None = None  # armed only
    exit_by: date | None = None  # the LIVENESS horizon (hold clock) = the "runway" the ranking uses
    arm_until: date | None = None  # the entry window (confirmation clock)
    lapsing: bool = False  # armed + runway below the dial; ranks below fresh members
    theme_armed: bool = (
        False  # armed via the THEME-conviction FALLBACK (M5b), not its own conviction
    )
    triggers: list[TriggerRef] = Field(default_factory=list)  # this member's own fired evidence


class CallCard(DomainModel):
    """The opinionated, auditable call — a pure function of (thesis, events, asof), recomputed on read.

    This is the domain shape. The API response contract (CallCardResponse, M3) is kept separate so the
    wire shape can resolve provenance to URLs without coupling the frontend to the domain schema.
    """

    thesis_id: UUID
    asof: date
    state: State
    verdict: Verdict
    conviction_grade: Grade | None = None  # the thesis quality (the conviction key)
    confirmation_grade: Grade | None = None  # core=volume-backed, flip=momentum-only
    entry_grade: Grade | None = None  # the action/size grade (the weaker key) — drives the verdict
    armed_security_id: UUID | None = None  # the co-located security that armed (None unless Armed)
    expression: str
    exit_by: date | None = None  # the HOLD horizon (conviction key); drives the catalyst surface
    arm_until: date | None = None  # the ENTRY window (confirmation key); the arm lapses past this
    catalyst_surface: list[Catalyst] = Field(default_factory=list)
    # Confidence is an ARMED-state metric (§7) — the Armed card's bar. None for a not-yet card
    # (Incubating/Warming) and for Managing (which renders the position, not a confidence bar).
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    key_conviction: KeyState
    key_confirmation: KeyState
    triggers_fired: list[TriggerRef] = Field(default_factory=list)
    risk_signals: list[TriggerRef] = Field(
        default_factory=list
    )  # fired risk signals (with provenance)
    missing: list[str] = Field(default_factory=list)
    counter_case: str = ""
    safe_sleeve: str | None = None
    # M5 Part A — the per-member ranked menu. `armed_members` is ranked (freshness band on runway primary,
    # grade within); the headline above is `armed_members[0]`. `watch_members` = confirmation-only members
    # ("moving, no conviction yet"). For a single-name thesis, `armed_members` is the one armed call (or empty).
    armed_members: list[MemberCall] = Field(default_factory=list)
    watch_members: list[MemberCall] = Field(default_factory=list)
