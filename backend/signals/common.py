from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.config import CallConfig
from domain.enums import CatalystType, Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from domain.trading_days import next_scheduled_day, nth_scheduled_day_after


def window_end(fire_date: date, alpha_liveness_days: int, *, sessions: int | None = None) -> date:
    """THE last day of a trigger's window — the ONE place it is computed (F4).

    Calendar (``sessions is None``, every window today): ``fire_date + alpha_liveness_days`` calendar days.
    Sessions (a bar-driven family whose dormant F4 dial says so): the ``sessions``-th market session after
    ``fire_date`` on the one session calendar (``domain.trading_days.nth_scheduled_day_after``), so the window
    gives the same number of sessions whatever weekday it fired on and whatever holiday falls inside.

    Every reader goes through here: ``entry_signal_is_live`` (F3's next-session test, shared by the detectors
    and the assembler's ``_live``), the assembler's ``_clock`` (``exit_by`` / ``arm_until``) and ``BarWindow``
    (which hands a session window to the fired event as its calendar span). Pure: no clock.
    """
    if sessions is None:
        return fire_date + timedelta(days=alpha_liveness_days)
    return nth_scheduled_day_after(fire_date, sessions)


def entry_signal_is_live(fire_date: date, alpha_liveness_days: int, asof: date) -> bool:
    """Is a trigger live on night ``asof``? Shared by every detector and the call assembler (CALL_LOGIC §2/§6).

    F3 (operator rule 2026-10-30, rule log ``2026-11-entry-window-next-session``): a trigger counts on night N
    only while the FIRST SESSION AFTER N (``domain.trading_days.next_scheduled_day``) is on or before its
    window end. The call is recorded after N's close, so the operator can act no earlier than that session; a
    night whose next session falls outside the window would show an arm (or a warm-up, or a risk) nobody can
    act on. Before F3 the test was ``asof <= window end``, which kept a trigger live through the window's last
    night with no session of it left. No window is stretched: the end date is unchanged, only the last
    unusable night (or, for a window ending on a weekend or holiday, the nights after its last session) drops.
    """
    return next_scheduled_day(asof) <= window_end(fire_date, alpha_liveness_days)


@dataclass(frozen=True)
class BarWindow:
    """A BAR-DRIVEN family's entry window (F4): its calendar dial, plus its session count when the family's
    dormant unit dial says ``"sessions"``.

    **How a session window reaches every reader unchanged.** An event carries its window as
    ``alpha_liveness_days`` and every reader adds that to the fire date (F3's ``entry_signal_is_live``, the
    assembler's ``_clock``). So a session window is handed to the event as its CALENDAR SPAN — the days from
    the fire date to the ``sessions``-th session after it (``span_days``) — and nothing downstream needs to
    know which unit produced it: ``arm_until``, the next-session test and "sessions left" all read the same
    end. Under ``"calendar"`` the span IS the dial, byte-identically.

    **Why a detector asks per bar.** A session window's span depends on the fire date (7 sessions span 9
    days from a Monday fire and 11 from a Friday one, more across a holiday), so a detector scanning for its
    latest live bar asks ``is_live`` of each candidate's own date, never of one span computed once.
    """

    days: int
    sessions: int | None = None

    def end(self, fire_date: date) -> date:
        return window_end(fire_date, self.days, sessions=self.sessions)

    def span_days(self, fire_date: date) -> int:
        """The window as calendar days from ``fire_date`` — what the fired event's ``alpha_liveness_days``
        carries (>= 1 by construction: a session window ends on a session strictly after the fire date).
        """
        return (self.end(fire_date) - fire_date).days

    def is_live(self, fire_date: date, asof: date) -> bool:
        """F3's rule, unchanged: live while the first session after ``asof`` is on or before the end."""
        return entry_signal_is_live(fire_date, self.span_days(fire_date), asof)


def _bar_window(days: int, unit: str, sessions: int | None, family: str) -> BarWindow:
    # ``CallConfig._bar_window_units`` already refuses a lone half; this guard is for a config built with
    # ``model_copy(update=...)``, which skips validation — a "sessions" unit with no count must fail loud,
    # never quietly fall back to calendar days.
    if unit == "sessions":
        if sessions is None:
            raise ValueError(f"{family}_alpha_liveness_unit='sessions' with no session count")
        return BarWindow(days, sessions)
    return BarWindow(days)


def breakout_window(cfg: CallConfig) -> BarWindow:
    """The 8-day volume breakout's window, and the FLIP BREAKDOWN's: it searches the same breakouts the arm
    cleared (``signals/breakdown._recent_flip_base``), so it counts on the same clock."""
    return _bar_window(
        cfg.breakout_alpha_liveness_days,
        cfg.breakout_alpha_liveness_unit,
        cfg.breakout_alpha_liveness_sessions,
        "breakout",
    )


def breakout_52w_window(cfg: CallConfig) -> BarWindow:
    """The structural 52-week breakout's window."""
    return _bar_window(
        cfg.breakout_52w_alpha_liveness_days,
        cfg.breakout_52w_alpha_liveness_unit,
        cfg.breakout_52w_alpha_liveness_sessions,
        "breakout_52w",
    )


def laggard_window(cfg: CallConfig) -> BarWindow:
    """The laggard rotation's window, anchored on the leader's breakout date."""
    return _bar_window(
        cfg.laggard_alpha_liveness_days,
        cfg.laggard_alpha_liveness_unit,
        cfg.laggard_alpha_liveness_sessions,
        "laggard",
    )


def source_provenance(
    source: str,
    ref: str,
    *,
    detail: dict[str, Any] | None = None,
) -> Provenance:
    """Stamp one source/computation pointer in the common signal provenance shape."""
    return Provenance(source=source, ref=ref, detail={} if detail is None else detail)


def fired_signal(
    *,
    detector: str,
    security_id: UUID,
    role: Role,
    kind: Kind,
    score: float,
    label: str,
    asof: date,
    provenance: Iterable[Provenance],
    grade: Grade | None = None,
    dearm_grade: Grade | None = None,
    catalyst_type: CatalystType | None = None,
    alpha_liveness_days: int | None = None,
) -> SignalEvent:
    """Construct the one output shape used by every current fired signal producer.

    ``dearm_grade`` is the de-arm TARGET a ``breakdown`` risk signal carries (the grade of armed entry it
    de-arms) — left ``None`` by every entry trigger and every other risk (see ``SignalEvent``).
    """
    return SignalEvent(
        detector=detector,
        security_id=security_id,
        role=role,
        kind=kind,
        type=catalyst_type,
        grade=grade,
        dearm_grade=dearm_grade,
        score=score,
        fired=True,
        label=label,
        alpha_liveness_days=alpha_liveness_days,
        provenance=list(provenance),
        asof=asof,
    )
