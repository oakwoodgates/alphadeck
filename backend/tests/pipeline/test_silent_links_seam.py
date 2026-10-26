"""Context and Bench through the REAL call seam (``pipeline.core.assemble_from_pit``) — no DB.

A company the operator puts in Context or Bench is on the card but never counts toward the call. These drive the
real detectors, the theme broadcast, the rotation read, the split-night hold and ``assemble_call`` over an
in-memory point-in-time view (the ``_FakePIT`` idiom of ``tests/replay/test_breakdown_dearm_replay.py``), and
pin the contract the product rests on: a silent name's card is byte-identical to the card for the same thesis
without it. Each case has a NON-VACUITY twin — the same name in a real link DOES change the card — so a green
equality cannot be a fixture that never moves anything.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind, State
from domain.thesis import BasketMember, Segment, Thesis
from pipeline.core import assemble_from_pit
from signals.base import window_prices

_END = date(2026, 6, 2)
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_N = 261  # 260 base sessions + the asof bar


def _d(i: int, n: int = _N) -> date:
    return _END - timedelta(days=(n - 1 - i))


def _bars(closes: list[float], vols: list[float]) -> list[dict[str, Any]]:
    n = len(closes)
    return [
        {"d": _d(i, n), "close": c, "high": c, "low": c, "volume": v}
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


def _ramp(parts: list[tuple[int, float, float]]) -> list[float]:
    out: list[float] = []
    for n, a, b in parts:
        out += [a + (b - a) * (i / max(n - 1, 1)) for i in range(n)]
    return out


# A CORE breakout on the asof bar: a flat 50 base, then a fresh closing high (+30%) on 3x volume.
_BREAKOUT = _bars([50.0] * 260 + [65.0], [1000.0] * 260 + [3000.0])
# Nothing happens: flat, flat volume.
_QUIET = _bars([50.0] * _N, [1000.0] * _N)
# A lagging uptrend: 60 -> 160 over 231 sessions (well above its 200d SMA), then a -5% pullback over 30.
_LAGGING = _bars(_ramp([(231, 60.0, 160.0), (30, 160.0, 152.0)]), [1000.0] * _N)
# An extreme mover: flat 50, then +100% over the last 30 sessions on flat volume.
_RUNNER = _bars(_ramp([(231, 50.0, 50.0), (30, 50.0, 100.0)]), [1000.0] * _N)
# A split-shaped step on the asof bar: x16 on 1/16 of the volume (an unadjusted 1-for-16 reverse split).
_STEPPED = _bars([50.0] * 260 + [800.0], [1000.0] * 260 + [62.5])

# A CORE catalyst conviction, live before the breakout (the dearm replay's proven conviction supply).
_CORE_CATALYST = {
    "valid_from": _d(250),
    "grade": "core",
    "label": "Binding offtake agreement (core catalyst)",
    "source": "ratified",
    "source_ref": "https://usaspending.gov/award/TESTCORE",
    "catalyst_type": None,
}

X, Y, L, V, S = (UUID(int=i) for i in (0xA1, 0xA2, 0xA3, 0xA4, 0xA5))
_TICKER = {X: "XXXX", Y: "YYYY", L: "LEAD", V: "LAGG", S: "SILE"}


class _Pit:
    """An in-memory point-in-time view satisfying ``SignalPointInTimeData``: per-security bars and catalysts,
    as-of capped at ``asof``; every other fact table empty."""

    def __init__(
        self,
        asof: date,
        bars: dict[UUID, list[dict[str, Any]]],
        catalysts: dict[UUID, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.asof = asof
        self.known_at = _KNOWN
        self.tenant_id = DEFAULT_TENANT_ID
        self._bars = bars
        self._catalysts = catalysts or {}

    def price_history(self, security_id: UUID, lookback_days: int | None = None) -> list[dict]:
        rows = [dict(b) for b in self._bars.get(security_id, []) if b["d"] <= self.asof]
        return window_prices(rows, self.asof, lookback_days)

    def catalyst_facts(self, security_id: UUID) -> list[dict]:
        return [
            dict(c) for c in self._catalysts.get(security_id, []) if c["valid_from"] <= self.asof
        ]

    def insider_txns(self, security_id: UUID) -> list[dict]:
        return []

    def dilution_facts(self, security_id: UUID) -> list[dict]:
        return []

    def fundamentals_facts(self, security_id: UUID) -> list[dict]:
        return []

    def corporate_event_facts(self, security_id: UUID) -> list[dict]:
        return []

    def activist_stake_facts(self, security_id: UUID) -> list[dict]:
        return []

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict]:
        return []

    def security_name(self, security_id: UUID) -> str | None:
        return None

    def security_cik(self, security_id: UUID) -> str | None:
        return None

    def split_events(self, security_id: UUID) -> list[dict]:
        return []


_BARS = {X: _BREAKOUT, Y: _QUIET, L: _BREAKOUT, V: _LAGGING, S: _RUNNER}
_CATALYSTS = {X: [_CORE_CATALYST], L: [_CORE_CATALYST]}


def _member(sid: UUID, segment: str | None) -> BasketMember:
    return BasketMember(ticker=_TICKER[sid], role="r", security_id=sid, segment=segment)


def _thesis(*members: tuple[UUID, str | None]) -> Thesis:
    return Thesis(
        id=uuid.UUID(int=0x5151),
        name="Silent links seam",
        narrative="A name in Context or Bench is on the card but never counts toward the call.",
        segments=[Segment(label="Chain"), Segment(label="Discovered")],
        basket=[_member(sid, seg) for sid, seg in members],
    )


def _card(
    *members: tuple[UUID, str | None],
    bars: dict[UUID, list[dict[str, Any]]] | None = None,
) -> CallCard:
    pit = _Pit(_END, bars or _BARS, _CATALYSTS)
    return assemble_from_pit(pit, _thesis(*members), _END, DEFAULT_CONFIG)


def _sids_on(card: CallCard) -> set[UUID]:
    """Every security the card names anywhere a security can appear."""
    out = {m.security_id for m in card.armed_members} | {m.security_id for m in card.watch_members}
    out |= {t.security_id for t in card.triggers_fired} | {t.security_id for t in card.risk_signals}
    for m in (*card.armed_members, *card.watch_members):
        out |= {t.security_id for t in m.triggers}
    if card.armed_security_id is not None:
        out.add(card.armed_security_id)
    return out


SILENT = pytest.mark.parametrize("silent", ["Context", "Bench"])


# --- non-vacuity: the fixtures DO move a card when the name votes ---------------------------------------


def test_non_vacuity_a_breakout_with_conviction_in_a_real_link_arms_the_card_on_it():
    card = _card((X, "Chain"), (Y, "Chain"))
    assert card.state is State.ARMED
    assert card.armed_security_id == X
    assert [m.security_id for m in card.armed_members] == [X]
    assert X in _sids_on(card)
    assert any(
        t.kind is Kind.TECHNICAL_BREAKOUT and t.security_id == X for t in card.triggers_fired
    )


@pytest.mark.parametrize("link", ["Discovered", None])
def test_discovered_and_unplaced_names_still_count_toward_the_call(link):
    """Only Context and Bench are silent. A name in Discovered (or in no link) votes exactly as it did."""
    card = _card((X, link), (Y, "Chain"))
    assert card.state is State.ARMED and card.armed_security_id == X


# --- the equality: silent == absent ----------------------------------------------------------------------


@SILENT
def test_a_silent_name_leaves_the_card_byte_identical_to_the_card_without_it(silent):
    with_x = _card((X, silent), (Y, "Chain"))
    without_x = _card((Y, "Chain"))
    assert with_x.model_dump() == without_x.model_dump()
    assert with_x.model_dump_json() == without_x.model_dump_json()
    # and the card did not arm, warm, or name X anywhere
    assert with_x.state is not State.ARMED and with_x.armed_security_id is None
    assert X not in _sids_on(with_x)
    assert not with_x.armed_members and not with_x.watch_members
    assert not with_x.triggers_fired and not with_x.risk_signals
    assert not any(_TICKER[X] in line for line in with_x.missing)


@SILENT
def test_a_silent_name_beside_a_voting_one_leaves_the_voting_names_call_untouched(silent):
    """X (breakout + conviction) silent beside L (breakout + conviction) voting: the card arms on L exactly as
    if X were not there."""
    with_x = _card((X, silent), (L, "Chain"))
    without_x = _card((L, "Chain"))
    assert with_x.model_dump() == without_x.model_dump()
    assert with_x.state is State.ARMED and with_x.armed_security_id == L
    assert X not in _sids_on(with_x)


def test_a_thesis_of_only_silent_names_is_an_empty_call():
    card = _card((X, "Context"), (L, "Bench"))
    empty = _card()
    assert card.model_dump() == empty.model_dump()
    assert card.state is State.INCUBATING and not _sids_on(card)


def test_silent_and_voting_names_share_a_roster_order_independently():
    a = _card((X, "Context"), (Y, "Chain"), (L, "Bench"))
    b = _card((L, "Bench"), (Y, "Chain"), (X, "Context"))
    assert a.model_dump() == b.model_dump() == _card((Y, "Chain")).model_dump()


# --- rotation: a silent name neither leads nor moves the median -------------------------------------------


def _laggard_labels(card: CallCard) -> list[str]:
    return [t.label for t in card.triggers_fired if t.kind is Kind.LAGGARD and t.security_id == V]


def test_non_vacuity_a_voting_leader_gives_the_voting_laggard_a_laggard_confirmation():
    card = _card((L, "Chain"), (V, "Chain"))
    assert len(_laggard_labels(card)) == 1
    assert V in {
        m.security_id for m in card.watch_members
    }  # confirmation-only: moving, no conviction


@SILENT
def test_a_silent_leader_gives_nobody_a_laggard_confirmation(silent):
    """L has a live breakout, but L is silent: it is not a leader the rotation reads, so V gets no sympathy
    confirmation and the card is the card for V alone."""
    card = _card((L, silent), (V, "Chain"))
    assert _laggard_labels(card) == []
    assert card.model_dump() == _card((V, "Chain")).model_dump()
    assert L not in _sids_on(card)


def test_non_vacuity_a_voting_extreme_mover_moves_the_median_printed_in_the_laggards_label():
    with_s = _card((L, "Chain"), (V, "Chain"), (S, "Chain"))
    without_s = _card((L, "Chain"), (V, "Chain"))
    (a,), (b,) = _laggard_labels(with_s), _laggard_labels(without_s)
    assert a != b  # the basket median in the label moved: S's return is in it


@SILENT
def test_a_silent_extreme_mover_does_not_move_the_median_a_voting_laggard_is_measured_against(
    silent,
):
    with_s = _card((L, "Chain"), (V, "Chain"), (S, silent))
    without_s = _card((L, "Chain"), (V, "Chain"))
    assert _laggard_labels(with_s) == _laggard_labels(without_s) != []
    assert with_s.model_dump() == without_s.model_dump()
    assert S not in _sids_on(with_s)


# --- the split-night hold: a silent name's step is not the card's business --------------------------------

_STEPPED_BARS = {**_BARS, S: _STEPPED}


def test_non_vacuity_a_voting_name_with_a_split_shaped_step_puts_a_held_line_on_missing():
    card = _card((Y, "Chain"), (S, "Chain"), bars=_STEPPED_BARS)
    assert any(_TICKER[S] in line and "held" in line for line in card.missing)


@SILENT
def test_a_silent_name_with_a_split_shaped_step_puts_no_held_line_on_missing(silent):
    card = _card((Y, "Chain"), (S, silent), bars=_STEPPED_BARS)
    assert not any(_TICKER[S] in line for line in card.missing)
    assert card.model_dump() == _card((Y, "Chain"), bars=_STEPPED_BARS).model_dump()


# --- the seam does not mutate what it is given ------------------------------------------------------------


def test_the_seam_leaves_the_callers_thesis_untouched():
    thesis = _thesis((X, "Context"), (Y, "Chain"))
    before = thesis.model_dump()
    assemble_from_pit(_Pit(_END, _BARS, _CATALYSTS), thesis, _END, DEFAULT_CONFIG)
    assert thesis.model_dump() == before
    assert [m.segment for m in thesis.basket] == [
        "Context",
        "Chain",
    ]  # the silent row is still on the card
