"""§2.5 the structural de-arm, proven IN REPLAY (R10/R12): an arm episode whose close reason is the
STRUCTURAL BREAK, not the short clock.

Sweeps the REAL pipeline (``assemble_from_pit`` -> every registered detector, incl. the new breakdown
detectors -> ``assemble_call`` with the grade-aware veto) day by day over a crafted point-in-time price
path, exactly as the replay harness does, then derives the arm EPISODE (``replay.episodes``) and asserts
the core hold ARMS, survives a hold, then DE-ARMS on the 200-day break — with the de-arm landing INSIDE
both the arm_until (entry) and exit_by (conviction) windows, so the episode closes on the break, not on a
lapsed clock. Uses an in-memory point-in-time view (no DB/DuckDB), so it is deterministic and self-contained
while still exercising the true detector -> assembler -> episode path.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, State
from domain.thesis import BasketMember, Thesis
from pipeline.core import assemble_from_pit
from replay.episodes import derive_episodes
from replay.schema import CallSnapshot
from signals.base import window_prices
from tests.calls.factories import SID

_END = date(2026, 6, 2)
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
# the de-arm runs through the master switch; this replay is the exit-half proof, so it runs the pipeline
# with the flag ON explicitly (the gate-OFF case is the last test). v1 now DEFAULTS the switch ON.
_ON = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_enabled": True})
_OFF = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_enabled": False})

# The crafted arc: a long ~50 base -> a fresh 52-week high on volume (a CORE confirmation) -> a hold that
# keeps the 200d -> a close well below the 200d SMA (the structural break). A core catalyst supplies the
# co-located conviction, so the arm is a CORE hold whose ONLY structural exit is the 200d break.
_CLOSES = [50.0] * 260 + [65.0] + [60.0] * 18 + [45.0] * 3
_VOLS = [1000.0] * 260 + [3000.0] + [1200.0] * 18 + [1200.0] * 3
_BREAKOUT_I = 260
_DEARM_I = 279  # the first sub-200d close
_START_I = 250  # sweep from here (the core catalyst is already live -> WARMING before the arm)


def _bar_date(i: int) -> date:
    return _END - timedelta(days=(len(_CLOSES) - 1 - i))


def _bars() -> list[dict[str, Any]]:
    return [
        {"d": _bar_date(i), "close": c, "high": c, "low": c, "volume": v}
        for i, (c, v) in enumerate(zip(_CLOSES, _VOLS))
    ]


_ALL_BARS = _bars()
# a CORE catalyst conviction, live from before the breakout (365d horizon) -> a durable core hold
_CATALYST = {
    "valid_from": _bar_date(_BREAKOUT_I - 10),
    "grade": "core",
    "label": "Binding offtake agreement (core catalyst)",
    "source": "ratified",
    "source_ref": "https://usaspending.gov/award/TESTCORE",
    "catalyst_type": None,
}


class _FakePIT:
    """An in-memory point-in-time view satisfying the detector ``SignalPointInTimeData`` protocol —
    price bars + one core catalyst, as-of capped at ``asof`` (no lookahead), everything else empty.
    """

    def __init__(self, asof: date) -> None:
        self.asof = asof
        self.known_at = _KNOWN
        self.tenant_id = DEFAULT_TENANT_ID

    def price_history(self, security_id: UUID, lookback_days: int | None = None) -> list[dict]:
        rows = [dict(b) for b in _ALL_BARS if b["d"] <= self.asof]
        return window_prices(rows, self.asof, lookback_days)

    def catalyst_facts(self, security_id: UUID) -> list[dict]:
        return [dict(_CATALYST)] if _CATALYST["valid_from"] <= self.asof else []

    def insider_txns(self, security_id: UUID) -> list[dict]:
        return []

    def dilution_facts(self, security_id: UUID) -> list[dict]:
        return []

    def fundamentals_facts(self, security_id: UUID) -> list[dict]:
        return []

    def corporate_event_facts(self, security_id: UUID) -> list[dict]:
        # Band 03 S3 protocol accessor — completes this fake to SignalPointInTimeData now that
        # corporate_risk is live-default (it reads this); these de-arm scenarios carry no 8-K events.
        return []

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict]:
        return []

    def security_name(self, security_id: UUID) -> str | None:
        return None


def _thesis() -> Thesis:
    return Thesis(
        id=UUID(int=0x2222),
        name="Structural de-arm arc",
        narrative="A core catalyst + a 52-week breakout arm a core hold; the 200d break de-arms it.",
        ticker="DEVCO",
        basket=[BasketMember(ticker="DEVCO", role="the name", security_id=SID)],
    )


def _sweep() -> list[CallSnapshot]:
    thesis = _thesis()
    snaps: list[CallSnapshot] = []
    for i in range(_START_I, len(_CLOSES)):
        t = _bar_date(i)
        card = assemble_from_pit(_FakePIT(t), thesis, t, _ON)
        snaps.append(CallSnapshot.from_card(card))
    return snaps


def test_core_hold_arms_survives_the_hold_then_dearms_on_the_200d_break_in_replay():
    snaps = _sweep()

    armed = [s for s in snaps if s.state is State.ARMED]
    assert armed, "the crafted core hold should ARM at the 52-week breakout"
    assert all(s.armed_security_id == SID for s in armed)
    # it armed as a CORE hold (core catalyst + core 52-week confirmation), and held for several sessions
    assert any(s.conviction_grade is Grade.CORE and s.entry_grade is Grade.CORE for s in armed)
    assert len(armed) >= 5, "the hold should survive multiple sessions before the break"

    # the arm ARMED then fell back to WARMING (a de-arm, conviction still live) — never aged silently out
    assert snaps[-1].state is State.WARMING


def test_the_episode_closes_on_the_structural_break_not_the_short_clock():
    episodes = derive_episodes(_sweep())
    mine = [e for e in episodes if e.security_id == SID]
    assert len(mine) == 1, "one clean arm episode"
    ep = mine[0]

    # it de-armed (a real close-out, not a window_end run) ON the structural break, INSIDE both clocks —
    # so the reason is NOT a lapsed entry window or an aged-out conviction (the whole point of R10/§2.5).
    assert ep.dearm_date is not None
    assert ep.close_reason == "dearmed_other"
    assert ep.close_reason not in ("arm_until_lapsed", "conviction_aged_out", "window_end")
    assert ep.arm_until is not None and ep.dearm_date < ep.arm_until  # arm window had NOT lapsed
    assert ep.exit_by is not None and ep.dearm_date <= ep.exit_by  # conviction still live
    assert ep.conviction_grade is Grade.CORE and ep.entry_grade is Grade.CORE
    assert ep.dearm_date == _bar_date(_DEARM_I)  # exactly the first sub-200d close


def test_the_dearm_snapshot_carries_the_breakdown_risk_and_the_signal_validity_expression():
    """At the break as-of, the served card directly shows: state WARMING, a BREAKDOWN risk on the card,
    and the de-arm expression (a signal-validity event, never a sell — #4/#5)."""
    t = _bar_date(_DEARM_I)
    card = assemble_from_pit(_FakePIT(t), _thesis(), t, _ON)
    assert card.state is State.WARMING and card.armed_security_id is None
    breakdowns = [r for r in card.risk_signals if r.kind is Kind.BREAKDOWN]
    assert breakdowns, "the 200d break surfaces as a breakdown risk on the card"
    assert "de-arm" in card.expression.lower() and "not a sell" in card.expression.lower()

    # the session BEFORE the break was still armed (proves the break — not a clock — flipped the state)
    prev = _bar_date(_DEARM_I - 1)
    before = assemble_from_pit(_FakePIT(prev), _thesis(), prev, _ON)
    assert before.state is State.ARMED and before.entry_grade is Grade.CORE


def test_with_the_flag_off_the_same_arc_never_dearms():
    """The MASTER SWITCH gate: with the flag OFF (explicit — v1 now defaults it ON), the identical price arc
    emits NO breakdown at all, so the core hold stays ARMED at the break bar. The capability is fully gated
    (the flag-ON tests above prove it fires)."""
    t = _bar_date(_DEARM_I)
    card = assemble_from_pit(_FakePIT(t), _thesis(), t, _OFF)  # flag explicitly OFF
    assert card.state is State.ARMED and card.armed_security_id == SID
    assert not [r for r in card.risk_signals if r.kind is Kind.BREAKDOWN]  # nothing emitted
