"""The display-signal seam: read-only computed indicators, structurally OFF the call path.

A display signal is context the operator reads (price vs its 50/200-day SMA, the 52-week range, …);
it is NEVER a ``SignalEvent`` — it has no ``role``, cannot fire, arm, veto, or grade, and nothing in
``pipeline/`` or ``calls/`` consumes this package. The bound is structural (the explain-seam idiom):
this package never imports the detector seam (``signals.base``/``signals.registry``), the call
domain (``domain.signal``), a DB driver, or a repository — a member is a pure function of the
point-in-time view it is handed. Display output is also never persisted: the daily cron's
``record_if_changed`` canonicalizes the whole domain CallCard, so a day-varying display field there
would append a call-of-record row every night. See ``docs/DISPLAY_SIGNALS.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import Field

from domain.base import DomainModel


class DisplayPointInTimeData(Protocol):
    """The accessors a display member may read — its OWN narrow contract, deliberately not the
    detectors' ``SignalPointInTimeData`` (which stays "no future plugin surface"). The concrete
    ``PointInTimeData`` satisfies both structurally; nothing inherits or registers across the seam.
    """

    asof: date
    known_at: datetime
    tenant_id: UUID

    def price_history(
        self, security_id: UUID, lookback_days: int | None = None
    ) -> list[dict[str, Any]]: ...

    def insider_txns(self, security_id: UUID) -> list[dict[str, Any]]: ...


class DisplayMetric(DomainModel):
    """One labeled reading. ``value=None`` is an HONEST gap — the ``note`` says why ("n/a: 140/200
    bars"), never a fake number (#6 show the work, #7 quiet degrade)."""

    key: str
    label: str
    value: float | None = None
    unit: Literal["pct", "usd", "price", "count", "ratio"] | None = None
    note: str | None = None


class DisplayEvent(DomainModel):
    """A dated flip/cross the tape actually printed — display context, never a trigger."""

    key: str
    label: str
    date: date
    direction: Literal["up", "down"] | None = None


class DisplayBasis(DomainModel):
    """Show-the-work for a computed indicator (#6): the fact table it read, the parameters, and the
    exact bar window used — plus a staleness note when the tape lags the asof."""

    source: str  # "fact_price_eod" | "fact_insider_txn" | ...
    params: dict[str, Any] = Field(default_factory=dict)
    bars_used: int | None = None
    window_start: date | None = None
    window_end: date | None = None  # last bar actually used — the staleness tell
    note: str | None = None


class DisplayHeadline(DomainModel):
    """A member's one-glance state chip, rendered at the top of its block.

    ``key`` is a STABLE machine state (a future Board column / basket cell can consume the
    categorical directly); ``label`` is the literal statement, always derived from the member's
    params (never a hardcoded window or MA type — change fast/slow/SMA→EMA and the words follow);
    ``glyph`` is a token the FE maps to a subtly-tinted arrow; ``detail`` is the muted secondary
    read. A headline states the tape, it never forecasts (#4)."""

    key: str
    label: str
    glyph: Literal["up", "down", "turn_up", "turn_down", "flat"] | None = None
    detail: str | None = None


class DisplaySignal(DomainModel):
    """A display member's output: f(point_in_time_data, security_id, asof) -> DisplaySignal | None.

    Structurally NOT a ``SignalEvent``: no role/fired/grade/score, so it physically cannot turn a
    key, veto a window, or ride the recorded CallCard. ``kind`` is the registered member name.
    """

    kind: str
    label: str
    headline: DisplayHeadline | None = None  # the optional at-the-top state chip (any member)
    metrics: list[DisplayMetric] = Field(default_factory=list)
    events: list[DisplayEvent] = Field(default_factory=list)
    basis: DisplayBasis


# No CallConfig in the signature — deliberate: display dials are named module constants surfaced in
# ``basis.params``, never the call engine's trust-validated dial set.
DisplayFn = Callable[[DisplayPointInTimeData, UUID, date], DisplaySignal | None]


@dataclass(frozen=True, slots=True)
class DisplayMember:
    """One registered display member with the read-only contract."""

    name: str
    compute: DisplayFn

    def __call__(
        self, pit: DisplayPointInTimeData, security_id: UUID, asof: date
    ) -> DisplaySignal | None:
        sig = self.compute(pit, security_id, asof)
        if sig is not None and sig.kind != self.name:
            raise ValueError(f"display member {self.name!r} emitted a signal stamped {sig.kind!r}")
        return sig
