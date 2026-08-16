from __future__ import annotations

import uuid
from datetime import date

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role, State
from domain.thesis import BasketMember, Thesis
from signals import laggard
from tests.calls.factories import breakout_event, insider_event

_LEADER = uuid.UUID(int=0x1EAD)
_B = uuid.UUID(int=0x0B)  # the laggard candidate
_C = uuid.UUID(int=0x0C)  # a co-mover, not lagging
_TID = uuid.UUID(int=0x7777)
_ASOF = date(
    2026, 6, 2
)  # == the factory ASOF, so the injected breakout/insider events are live here


class _FakePit:
    """The narrow slice of the point-in-time view the laggard detector reads: ascending EOD closes per
    security. Dates are irrelevant to the detector (it works on close-series + the leader's event
    date), so bars carry only ``close``."""

    asof = _ASOF

    def __init__(self, closes_by_sid: dict[uuid.UUID, list[float]]) -> None:
        self._closes = closes_by_sid

    def price_history(self, security_id, lookback_days=None):
        return [{"close": c} for c in self._closes.get(security_id, [])]


def _ramp(segments: list[tuple[int, float, float]]) -> list[float]:
    """Concatenated linear ramps: each ``(n, start, end)`` is ``n`` bars from ``start`` to ``end``."""
    out: list[float] = []
    for n, s, e in segments:
        out += [s + (e - s) * (i / max(n - 1, 1)) for i in range(n)]
    return out


# LEADER: flat then +40% over the last 30 bars (the breakout name; 30d return ~= +0.40)
_LEADER_UP = _ramp([(180, 100.0, 100.0), (30, 100.0, 140.0)])
# C: flat then +20% over the last 30 bars (a co-mover at the basket median; not lagging)
_C_UP = _ramp([(180, 100.0, 100.0), (30, 100.0, 120.0)])
# B: a long uptrend (60 -> 160) then a mild -5% pullback -> ABOVE its 200d SMA but a low 30d return
_B_LAG = _ramp([(180, 60.0, 160.0), (30, 160.0, 152.0)])


def _thesis() -> Thesis:
    return Thesis(
        id=_TID,
        name="Rotation basket",
        narrative="a moving theme with a laggard",
        ticker=None,
        basket=[
            BasketMember(ticker="LEAD", role="leader", security_id=_LEADER),
            BasketMember(ticker="LAGB", role="laggard", security_id=_B),
            BasketMember(ticker="COMV", role="co-mover", security_id=_C),
        ],
    )


def _leader_breakout():
    # a live volume_breakout on the leader (kind TECHNICAL_BREAKOUT), fire-dated at _ASOF
    return breakout_event(grade=Grade.CORE, security_id=_LEADER)


def test_fixture_returns_are_what_the_rule_needs():
    """Anchor the tape: LEADER +40%, C +20%, B -5% over 30 bars; the basket median is +20%, so the
    threshold (median - 15pts) is +5% and B (-5%) is the sole laggard. B sits above its 200d SMA."""
    cfg = DEFAULT_CONFIG
    assert round(laggard._return(_LEADER_UP, cfg.laggard_return_days), 2) == 0.40
    assert round(laggard._return(_C_UP, cfg.laggard_return_days), 2) == 0.20
    assert round(laggard._return(_B_LAG, cfg.laggard_return_days), 2) == -0.05
    assert _B_LAG[-1] >= laggard._sma(_B_LAG, cfg.laggard_trend_sma_window)  # uptrend intact


def test_laggard_fires_for_the_lagging_uptrend_member():
    pit = _FakePit({_LEADER: _LEADER_UP, _B: _B_LAG, _C: _C_UP})
    out = laggard.detect(pit, _thesis(), [_leader_breakout()], _ASOF, DEFAULT_CONFIG)
    assert [e.security_id for e in out] == [_B]  # only B — LEADER is the leader, C isn't lagging
    ev = out[0]
    assert ev.kind is Kind.LAGGARD and ev.role is Role.ENTRY_TRIGGER
    assert ev.grade is Grade.FLIP  # a flip sympathy confirmation, never a core (R4)
    assert ev.asof == _ASOF  # fire-dated at the leader's breakout bar, not the query asof
    assert ev.alpha_liveness_days == DEFAULT_CONFIG.laggard_alpha_liveness_days
    d = ev.provenance[0].detail
    assert d["median"] == 0.2 and d["threshold"] == 0.05 and d["ret"] == -0.05


def test_no_fire_without_a_leader_breakout():
    """No live volume_breakout in the stream -> no rotation cue -> nothing fires (R3)."""
    pit = _FakePit({_LEADER: _LEADER_UP, _B: _B_LAG, _C: _C_UP})
    assert laggard.detect(pit, _thesis(), [], _ASOF, DEFAULT_CONFIG) == []


def test_no_fire_when_not_in_uptrend():
    """Same lag, but B's close is BELOW its 200d SMA (a downtrend) -> the uptrend gate blocks it."""
    b_down = _ramp([(180, 160.0, 60.0), (30, 60.0, 57.0)])  # ret30 ~ -5% but close << 200d SMA
    assert b_down[-1] < laggard._sma(b_down, DEFAULT_CONFIG.laggard_trend_sma_window)
    pit = _FakePit({_LEADER: _LEADER_UP, _B: b_down, _C: _C_UP})
    assert laggard.detect(pit, _thesis(), [_leader_breakout()], _ASOF, DEFAULT_CONFIG) == []


def test_no_fire_when_not_lagging():
    """B keeps its uptrend but rallies +9% over 30 bars -> above the (median - 15pt) bar -> no fire."""
    b_keeping_up = _ramp([(180, 60.0, 160.0), (30, 160.0, 175.0)])  # ret30 ~ +9%
    pit = _FakePit({_LEADER: _LEADER_UP, _B: b_keeping_up, _C: _C_UP})
    assert laggard.detect(pit, _thesis(), [_leader_breakout()], _ASOF, DEFAULT_CONFIG) == []


def test_laggard_arms_a_co_located_conviction_name():
    """R4 end-to-end: B carries an OWN conviction (insider) but hasn't broken out; the laggard supplies
    the confirmation key, so co-location (conv ∩ conf) ARMS B. The leader (breakout, no conviction)
    stays in the watch tier; the laggard is the arm, exactly as ratified."""
    pit = _FakePit({_LEADER: _LEADER_UP, _B: _B_LAG, _C: _C_UP})
    member_events = [_leader_breakout(), insider_event(security_id=_B)]
    lag_events = laggard.detect(pit, _thesis(), member_events, _ASOF, DEFAULT_CONFIG)
    assert [e.security_id for e in lag_events] == [_B]

    card = assemble_call(_thesis(), member_events + lag_events, _ASOF, DEFAULT_CONFIG)
    assert card.state is State.ARMED
    armed = {m.security_id for m in card.armed_members}
    assert _B in armed  # the laggard armed the conviction name that hadn't run yet
    assert (
        _LEADER not in armed
    )  # breakout-only -> the watch tier, never armed on confirmation alone
    assert _LEADER in {m.security_id for m in card.watch_members}


def test_leaders_and_unresolved_members_never_fire():
    """A name that itself broke out is a leader, not a laggard (excluded); an unresolved member (no
    security_id) is skipped without a None-keyed event."""
    thesis = _thesis()
    thesis.basket.append(BasketMember(ticker="GHOST", role="unresolved", security_id=None))
    # give the leader AND B both a breakout: B is now a leader too -> B cannot be its own laggard
    pit = _FakePit({_LEADER: _LEADER_UP, _B: _B_LAG, _C: _C_UP})
    events = [_leader_breakout(), breakout_event(grade=Grade.FLIP, security_id=_B)]
    assert laggard.detect(pit, thesis, events, _ASOF, DEFAULT_CONFIG) == []
