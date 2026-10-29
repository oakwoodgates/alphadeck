"""What Changed (``docs/dashboards/what-changed.md``): the pure diff on hand-built ``CallCard``s, the "since"
rules, the router's reads (pure, with the reads stubbed), and DB-backed reads through the real ``calls`` log:
the last-row-per-night rule (it lives in the router's SQL), a later night never leaking into the baseline, and
an archived card staying off the page.

A "now" view (``asofAware: false``), so the checklist's past-as-of case does not apply as such: the endpoint
takes no as-of. Its analog on the log's own clock is ``test_db_a_later_row_never_reaches_the_baseline``.
"""

from __future__ import annotations

import uuid
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import what_changed as router_mod
from dashboards.what_changed import (
    ENTRY_WINDOW_DAYS,
    RecordedCall,
    Since,
    SinceOutOfRange,
    WhatChanged,
    baseline_date,
    parse_since,
    security_ids,
    what_changed,
)
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Grade, Kind, State, Verdict
from domain.signal import Provenance
from domain.thesis import BasketMember, Thesis
from repositories import calls_repo, thesis_repo

T = date(2026, 10, 28)
PREV = date(2026, 10, 27)
A, B, C = (uuid.uuid4() for _ in range(3))
TID = uuid.uuid4()

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def key(label: str, turned: bool = False) -> KeyState:
    return KeyState(turned=turned, label=label)


def trig(
    sid: UUID,
    label: str = "8-day breakout",
    kind: Kind = Kind.TECHNICAL_BREAKOUT,
    on: date | None = date(2026, 10, 20),
    **kw,
) -> TriggerRef:
    return TriggerRef(label=label, kind=kind, security_id=sid, event_date=on, **kw)


def mem(sid: UUID, **kw) -> MemberCall:
    return MemberCall(security_id=sid, **kw)


def cc(
    state: State = State.WARMING,
    *,
    tid: UUID = TID,
    asof: date = T,
    conviction: bool = False,
    confirmation: bool = False,
    **kw,
) -> CallCard:
    return CallCard(
        thesis_id=tid,
        asof=asof,
        state=state,
        verdict=Verdict.NOT_YET,
        expression="",
        key_conviction=key("Conviction", conviction),
        key_confirmation=key("Confirmation", confirmation),
        **kw,
    )


def rec(card: CallCard, *, seq: int = 1, reconstructed: bool = False) -> RecordedCall:
    return RecordedCall(asof=card.asof, seq=seq, reconstructed=reconstructed, card=card)


TICKERS = {A: "AAA", B: "BBB", C: "CCC"}


def diff(
    now: CallCard, base: CallCard | None, *, baseline_on: date | None = PREV, **kw
) -> WhatChanged:
    """One card, named "Grid", from ``base`` (at ``baseline_on``) to ``now`` (at T)."""
    return what_changed(
        {TID: "Grid"},
        {TID: rec(now, **kw)},
        {TID: rec(base)} if base is not None else {},
        TICKERS,
        latest=T,
        baseline_on=baseline_on,
    )


def lines(result: WhatChanged) -> list[tuple]:
    """``(change, ticker or None, extra)`` per item: the readable shape most tests assert."""
    out = []
    for i in result.items:
        extra = {
            "state": (i.from_state, i.to_state),
            "key_on": i.label,
            "key_off": i.label,
            "trigger_new": (i.label, i.signal_kind),
            "trigger_gone": (i.label, i.signal_kind),
            "risk_new": (i.label, i.signal_kind),
            "trigger_redated": (i.label, i.signal_kind, i.previous_on, i.on),
            "entry_window": (i.on, i.days),
            "exit_by": i.on,
            "new_card": i.to_state,
        }.get(i.change)
        out.append((i.change, i.ticker, extra))
    return out


# ---------------------------------------------------------------------------------------------------------
# 1. column moves
# ---------------------------------------------------------------------------------------------------------


def test_a_state_move_up_is_one_column_line():
    result = diff(cc(State.ARMED), cc(State.WARMING, asof=PREV))
    assert lines(result) == [("state", None, (State.WARMING, State.ARMED))]
    item = result.items[0]
    assert (item.section, item.thesis_id, item.thesis_name) == ("column_moves", TID, "Grid")
    assert item.security_id is None  # a card-level line


def test_a_state_move_down_is_a_column_line_too():
    result = diff(cc(State.WARMING), cc(State.ARMED, asof=PREV))
    assert lines(result) == [("state", None, (State.ARMED, State.WARMING))]


# ---------------------------------------------------------------------------------------------------------
# 2. keys turned
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["conviction", "confirmation"])
def test_each_key_turning_on_and_off_shows_its_own_label(which):
    label = which.capitalize()
    on = diff(cc(**{which: True}), cc(asof=PREV))
    assert lines(on) == [("key_on", None, label)]
    assert on.items[0].section == "keys"

    off = diff(cc(), cc(asof=PREV, **{which: True}))
    assert lines(off) == [("key_off", None, label)]


def test_the_label_is_the_cards_own():
    now = cc(conviction=True).model_copy(update={"key_conviction": key("Theme conviction", True)})
    assert lines(diff(now, cc(asof=PREV))) == [("key_on", None, "Theme conviction")]


# ---------------------------------------------------------------------------------------------------------
# 3. names
# ---------------------------------------------------------------------------------------------------------


def test_a_name_armed_and_a_name_dropped():
    base = cc(State.ARMED, asof=PREV, armed_members=[mem(A, verdict=Verdict.STARTER_ENTRY)])
    now = cc(State.ARMED, armed_members=[mem(B, verdict=Verdict.CORE_ENTRY)])
    result = diff(now, base)
    assert lines(result) == [("armed", "BBB", None), ("no_longer_armed", "AAA", None)]
    assert {i.section for i in result.items} == {"names"}
    assert [i.security_id for i in result.items] == [B, A]


def test_a_new_watch_member_and_watch_to_armed():
    base = cc(asof=PREV, watch_members=[mem(A)])
    now = cc(State.ARMED, armed_members=[mem(A)], watch_members=[mem(B), mem(C)])
    result = diff(now, base)
    # A moved from watch to armed (one "armed" line; leaving the watch list is not a line);
    # B and C are new on watch, in ticker order
    assert [x for x in lines(result) if x[0] != "state"] == [
        ("armed", "AAA", None),
        ("new_watch", "BBB", None),
        ("new_watch", "CCC", None),
    ]


def test_an_armed_member_whose_call_changed_is_not_a_name_line():
    base = cc(State.ARMED, asof=PREV, armed_members=[mem(A, entry_grade=Grade.FLIP)])
    now = cc(State.ARMED, armed_members=[mem(A, entry_grade=Grade.CORE, confidence=0.7)])
    assert diff(now, base).items == ()  # matched by security, not by the whole member call


def test_a_name_with_no_master_ticker_keeps_its_line():
    stranger = uuid.uuid4()
    result = diff(cc(watch_members=[mem(stranger)]), cc(asof=PREV))
    assert lines(result) == [("new_watch", None, None)]
    assert result.items[0].security_id == stranger


# ---------------------------------------------------------------------------------------------------------
# 4. triggers
# ---------------------------------------------------------------------------------------------------------


def test_a_trigger_newly_fired_and_one_no_longer_firing():
    old = trig(A, "Insider cluster buy", Kind.INSIDER, date(2026, 10, 1))
    new = trig(B, "8-day breakout", Kind.TECHNICAL_BREAKOUT, date(2026, 10, 28))
    result = diff(cc(triggers_fired=[new]), cc(asof=PREV, triggers_fired=[old]))
    assert lines(result) == [
        ("trigger_new", "BBB", ("8-day breakout", Kind.TECHNICAL_BREAKOUT)),
        ("trigger_gone", "AAA", ("Insider cluster buy", Kind.INSIDER)),
    ]
    assert {i.section for i in result.items} == {"triggers"}
    assert [i.on for i in result.items] == [date(2026, 10, 28), date(2026, 10, 1)]


def test_a_new_risk_signal_and_a_cleared_one_is_not_a_line():
    kept = trig(A, "Convertible overhang", Kind.DILUTION_RISK)
    new = trig(B, "Close below the 200d", Kind.BREAKDOWN)
    cleared = trig(C, "Share creep", Kind.DILUTION_RISK)
    result = diff(cc(risk_signals=[kept, new]), cc(asof=PREV, risk_signals=[kept, cleared]))
    assert lines(result) == [("risk_new", "BBB", ("Close below the 200d", Kind.BREAKDOWN))]


def test_the_same_trigger_re_recorded_is_not_a_change():
    # identity = (kind, security, event date): its grade and its sources are not part of it
    base = trig(A, grade=None, sources=[])
    now = trig(
        A, grade=Grade.CORE, sources=[Provenance(source="price", ref="price:AAA:2026-10-20")]
    )
    assert diff(cc(triggers_fired=[now, now]), cc(asof=PREV, triggers_fired=[base])).items == ()


@pytest.mark.parametrize(
    "changed, changes",
    [
        ({"kind": Kind.SQUEEZE}, ["trigger_new", "trigger_gone"]),
        ({"sid": B}, ["trigger_new", "trigger_gone"]),
        # the same kind on the same security with only the date moved: one re-dated line (below)
        ({"on": date(2026, 10, 21)}, ["trigger_redated"]),
    ],
)
def test_each_part_of_the_trigger_identity_counts(changed, changes):
    base = trig(A)
    args = {"sid": A, "label": base.label, "kind": base.kind, "on": base.event_date, **changed}
    now = trig(args.pop("sid"), **args)
    result = diff(cc(triggers_fired=[now]), cc(asof=PREV, triggers_fired=[base]))
    assert [i.change for i in result.items] == changes


# the label is NOT part of the identity: labels carry numbers that move every night. The labels below are
# the shapes MEASURED on dev (prod's data) on Oct 27 and Oct 28, where every continuing breakdown read as new.
BREAKDOWN_OCT_27 = "Structural break: close 13.93 closed below the 200-day base 16.73"
BREAKDOWN_OCT_28 = "Structural break: close 14.65 closed below the 200-day base 16.74"


def test_a_continuing_risk_whose_label_numbers_moved_is_not_a_change():
    on = date(2026, 10, 9)
    base = trig(A, BREAKDOWN_OCT_27, Kind.BREAKDOWN, on)
    now = trig(A, BREAKDOWN_OCT_28, Kind.BREAKDOWN, on)
    assert diff(cc(risk_signals=[now]), cc(asof=PREV, risk_signals=[base])).items == ()


def test_a_trigger_whose_label_changed_alone_is_neither_new_nor_gone():
    on = date(2026, 10, 22)
    base = trig(A, "8-day breakout on 0.1x avg volume", Kind.TECHNICAL_BREAKOUT, on)
    now = trig(A, "8-day breakout on 0.6x avg volume", Kind.TECHNICAL_BREAKOUT, on)
    assert diff(cc(triggers_fired=[now]), cc(asof=PREV, triggers_fired=[base])).items == ()


def test_a_trigger_on_a_new_event_date_beside_another_is_gone_and_new_each_with_its_own_cards_label():
    # two of one kind on one security went and one came: not a lone re-date, so separate lines, each
    # carrying its own card's label
    kind = Kind.TECHNICAL_BREAKOUT
    base_1 = trig(A, "8-day breakout on 0.6x avg volume", kind, date(2026, 10, 20))
    base_2 = trig(A, "8-day breakout on 0.9x avg volume", kind, date(2026, 10, 23))
    now = trig(A, "8-day breakout on 1.4x avg volume", kind, date(2026, 10, 27))
    result = diff(cc(triggers_fired=[now]), cc(asof=PREV, triggers_fired=[base_1, base_2]))
    assert [(i.change, i.label, i.on, i.previous_on) for i in result.items] == [
        ("trigger_new", "8-day breakout on 1.4x avg volume", date(2026, 10, 27), None),
        ("trigger_gone", "8-day breakout on 0.6x avg volume", date(2026, 10, 20), None),
        ("trigger_gone", "8-day breakout on 0.9x avg volume", date(2026, 10, 23), None),
    ]


# a breakout re-dates to the newest bar on each new 8-day high: the label shapes MEASURED on dev (prod's
# data) on Oct 27 and Oct 28, where 38 breakouts read as both "no longer firing" and "fired"
BREAKOUT_OCT_27 = "close 10.80 cleared the 8-day high 10.15"
BREAKOUT_OCT_28 = "close 11.11 cleared the 8-day high 10.80"


def test_a_re_dated_breakout_is_one_re_dated_line_with_both_dates_and_the_now_label():
    kind = Kind.TECHNICAL_BREAKOUT
    base = trig(A, BREAKOUT_OCT_27, kind, PREV)
    now = trig(A, BREAKOUT_OCT_28, kind, T)
    result = diff(cc(triggers_fired=[now]), cc(asof=PREV, triggers_fired=[base]))
    assert lines(result) == [("trigger_redated", "AAA", (BREAKOUT_OCT_28, kind, PREV, T))]
    (item,) = result.items
    assert (item.section, item.security_id) == ("triggers", A)
    assert result.cards_with_change == 1


def test_a_re_date_sits_after_the_other_trigger_lines_and_pairs_only_its_own_name():
    kind = Kind.TECHNICAL_BREAKOUT
    base = [trig(A, BREAKOUT_OCT_27, kind, PREV), trig(B, "Insider cluster buy", Kind.INSIDER)]
    now = [trig(A, BREAKOUT_OCT_28, kind, T), trig(C, "8-day breakout", kind, T)]
    result = diff(cc(triggers_fired=now), cc(asof=PREV, triggers_fired=base))
    assert [(i.change, i.ticker) for i in result.items] == [
        ("trigger_new", "CCC"),
        ("trigger_gone", "BBB"),
        ("trigger_redated", "AAA"),
    ]


@pytest.mark.parametrize("gone, new", [(2, 1), (1, 2), (2, 2)])
def test_more_than_one_gone_or_new_on_a_name_stays_separate_lines(gone, new):
    kind = Kind.TECHNICAL_BREAKOUT
    base = [trig(A, f"breakout {d}", kind, date(2026, 10, d)) for d in (1, 2)[:gone]]
    now = [trig(A, f"breakout {d}", kind, date(2026, 10, d)) for d in (27, 28)[:new]]
    result = diff(cc(triggers_fired=now), cc(asof=PREV, triggers_fired=base))
    assert [i.change for i in result.items] == ["trigger_new"] * new + ["trigger_gone"] * gone
    assert all(i.previous_on is None for i in result.items)


def test_a_risk_signal_is_never_re_dated():
    base = trig(A, BREAKDOWN_OCT_27, Kind.BREAKDOWN, date(2026, 10, 9))
    now = trig(A, BREAKDOWN_OCT_28, Kind.BREAKDOWN, date(2026, 10, 10))
    result = diff(cc(risk_signals=[now]), cc(asof=PREV, risk_signals=[base]))
    # a risk on a new date is a new risk signal (a cleared one is never a line), never a re-date
    assert lines(result) == [("risk_new", "AAA", (BREAKDOWN_OCT_28, Kind.BREAKDOWN))]
    assert result.items[0].previous_on is None


def test_two_same_identity_signals_on_one_card_are_one_with_the_first_label():
    on = date(2026, 10, 9)
    risk_1 = trig(A, BREAKDOWN_OCT_28, Kind.BREAKDOWN, on)
    risk_2 = trig(A, BREAKDOWN_OCT_27, Kind.BREAKDOWN, on)
    # a new risk listed twice: one line, the first entry's label
    new = diff(cc(risk_signals=[risk_1, risk_2]), cc(asof=PREV))
    assert lines(new) == [("risk_new", "AAA", (BREAKDOWN_OCT_28, Kind.BREAKDOWN))]
    # against a baseline holding just one of the pair (either one), nothing changed
    assert diff(cc(risk_signals=[risk_2, risk_1]), cc(asof=PREV, risk_signals=[risk_1])).items == ()

    # a trigger listed twice on the baseline card and gone tonight: one line, the first entry's label
    kind, d = Kind.TECHNICAL_BREAKOUT, date(2026, 10, 22)
    fired_1 = trig(B, "8-day breakout on 0.6x avg volume", kind, d)
    fired_2 = trig(B, "8-day breakout on 0.1x avg volume", kind, d)
    gone = diff(cc(), cc(asof=PREV, triggers_fired=[fired_1, fired_2]))
    assert lines(gone) == [("trigger_gone", "BBB", ("8-day breakout on 0.6x avg volume", kind))]


# ---------------------------------------------------------------------------------------------------------
# 5. clocks (on the now card, as of T, in calendar days)
# ---------------------------------------------------------------------------------------------------------


def test_an_entry_window_closing_at_3_days_is_in_and_at_4_days_is_out():
    assert ENTRY_WINDOW_DAYS == 3
    base = cc(asof=PREV)
    inside = diff(cc(arm_until=date(2026, 10, 31)), base)
    assert lines(inside) == [("entry_window", None, (date(2026, 10, 31), 3))]
    assert inside.items[0].section == "clocks"

    assert diff(cc(arm_until=date(2026, 11, 1)), base).items == ()


def test_an_entry_window_closing_on_T_is_in_and_one_already_closed_is_out():
    base = cc(asof=PREV)
    assert lines(diff(cc(arm_until=T), base)) == [("entry_window", None, (T, 0))]
    assert diff(cc(arm_until=PREV), base).items == ()


def test_the_clock_counts_from_T_not_from_the_cards_own_asof():
    # an unchanged card whose newest row is days old: its clock still counts from T
    old = cc(asof=date(2026, 10, 20), arm_until=date(2026, 10, 30))
    result = what_changed(
        {TID: "Grid"},
        {TID: rec(old)},
        {TID: rec(old)},
        {},
        latest=T,
        baseline_on=PREV,
    )
    assert lines(result) == [("entry_window", None, (date(2026, 10, 30), 2))]


def test_exit_by_reached_inside_the_window():
    base = cc(asof=PREV)
    assert lines(diff(cc(exit_by=T), base)) == [("exit_by", None, T)]
    # on the baseline night or before: not inside the window; after T: not reached
    assert diff(cc(exit_by=PREV), base).items == ()
    assert diff(cc(exit_by=date(2026, 10, 29)), base).items == ()
    # a wider window takes in an earlier exit-by
    wide = diff(cc(exit_by=date(2026, 10, 24)), base, baseline_on=date(2026, 10, 21))
    assert lines(wide) == [("exit_by", None, date(2026, 10, 24))]


# ---------------------------------------------------------------------------------------------------------
# new cards, unchanged cards, unrecorded cards, the reconstructed tag
# ---------------------------------------------------------------------------------------------------------


def test_a_card_with_no_baseline_row_is_one_new_card_line():
    now = cc(
        State.ARMED,
        conviction=True,
        confirmation=True,
        armed_members=[mem(A)],
        triggers_fired=[trig(A)],
        arm_until=T,
    )
    result = diff(now, None)
    assert lines(result) == [("new_card", None, State.ARMED)]  # one line, nothing else
    assert result.items[0].section == "new_cards"
    assert result.cards_with_change == 1


def test_no_earlier_night_makes_every_recorded_card_new():
    result = diff(cc(), None, baseline_on=None)
    assert lines(result) == [("new_card", None, State.WARMING)]
    assert result.baseline is None


def test_a_card_unchanged_between_the_dates_yields_nothing():
    card = cc(
        State.ARMED,
        asof=date(2026, 10, 20),
        conviction=True,
        confirmation=True,
        armed_members=[mem(A)],
        watch_members=[mem(B)],
        triggers_fired=[trig(A)],
        risk_signals=[trig(C, "Share creep", Kind.DILUTION_RISK)],
        exit_by=date(2026, 12, 1),
        arm_until=date(2026, 11, 15),
    )
    result = what_changed(
        {TID: "Grid"}, {TID: rec(card)}, {TID: rec(card)}, TICKERS, latest=T, baseline_on=PREV
    )
    assert result.items == () and result.cards_with_change == 0
    assert (result.live_cards, result.unrecorded) == (1, ())


def test_the_quiet_night_is_an_empty_result_that_still_shows_the_work():
    t2 = uuid.uuid4()
    one, two = cc(asof=PREV), cc(State.INCUBATING, tid=t2, asof=date(2026, 9, 1))
    result = what_changed(
        {TID: "Grid", t2: "Coal"},
        {TID: rec(one), t2: rec(two)},
        {TID: rec(one), t2: rec(two)},
        {},
        latest=T,
        baseline_on=PREV,
    )
    assert result.items == ()
    assert (result.latest, result.baseline, result.live_cards, result.cards_with_change) == (
        T,
        PREV,
        2,
        0,
    )


def test_a_live_card_with_no_recorded_call_is_counted_never_dropped():
    t2 = uuid.uuid4()
    result = what_changed(
        {TID: "Grid", t2: "Brand new"},
        {TID: rec(cc(asof=PREV))},
        {TID: rec(cc(asof=PREV))},
        {},
        latest=T,
        baseline_on=PREV,
    )
    assert [(r.thesis_id, r.thesis_name) for r in result.unrecorded] == [(t2, "Brand new")]
    assert result.live_cards == 2 and result.items == ()


def test_an_empty_log():
    result = what_changed({TID: "Grid"}, {}, {}, {}, latest=None, baseline_on=None)
    assert (result.latest, result.baseline, result.items) == (None, None, ())
    assert [r.thesis_name for r in result.unrecorded] == ["Grid"]


def test_a_reconstructed_now_row_tags_every_line_of_its_card():
    result = diff(cc(State.ARMED, conviction=True), cc(asof=PREV), reconstructed=True)
    assert [i.change for i in result.items] == ["state", "key_on"]
    assert all(i.reconstructed for i in result.items)
    assert not any(i.reconstructed for i in diff(cc(State.ARMED), cc(asof=PREV)).items)


def test_a_row_for_a_card_outside_the_live_names_is_ignored():
    archived = uuid.uuid4()
    result = what_changed(
        {TID: "Grid"},
        {TID: rec(cc(asof=PREV)), archived: rec(cc(State.ARMED, tid=archived))},
        {TID: rec(cc(asof=PREV))},
        {},
        latest=T,
        baseline_on=PREV,
    )
    assert result.items == () and result.live_cards == 1


# ---------------------------------------------------------------------------------------------------------
# order: sections in a fixed order, cards by name inside one, a card's lines in their fixed order
# ---------------------------------------------------------------------------------------------------------


def test_items_come_by_section_then_card_name():
    t_grid, t_coal, t_new = TID, uuid.uuid4(), uuid.uuid4()
    grid_base = cc(asof=PREV, armed_members=[mem(B)])
    grid_now = cc(State.ARMED, conviction=True, armed_members=[mem(A)], arm_until=T)
    coal_base = cc(tid=t_coal, asof=PREV)
    coal_now = cc(State.ARMED, tid=t_coal, triggers_fired=[trig(C)])
    result = what_changed(
        {t_grid: "grid", t_coal: "Coal", t_new: "Aardvark"},
        {
            t_grid: rec(grid_now),
            t_coal: rec(coal_now),
            t_new: rec(cc(tid=t_new)),
        },
        {t_grid: rec(grid_base), t_coal: rec(coal_base)},
        TICKERS,
        latest=T,
        baseline_on=PREV,
    )
    assert [(i.section, i.thesis_name, i.change) for i in result.items] == [
        ("column_moves", "Coal", "state"),
        ("column_moves", "grid", "state"),  # card names compare ignoring case
        ("keys", "grid", "key_on"),
        ("names", "grid", "armed"),
        ("names", "grid", "no_longer_armed"),
        ("triggers", "Coal", "trigger_new"),
        ("clocks", "grid", "entry_window"),
        ("new_cards", "Aardvark", "new_card"),
    ]
    assert result.cards_with_change == 3


def test_security_ids_cover_every_list_a_line_can_name():
    card = cc(
        armed_members=[mem(A)],
        watch_members=[mem(B)],
        triggers_fired=[trig(C)],
        risk_signals=[trig(TID)],
    )
    assert security_ids([rec(card)]) == {A, B, C, TID}


# ---------------------------------------------------------------------------------------------------------
# the "since" selector
# ---------------------------------------------------------------------------------------------------------


def test_parse_since():
    assert parse_since("last_night") == Since("last_night")
    assert parse_since("7d") == Since("7d")
    assert parse_since("2026-10-21") == Since("date", date(2026, 10, 21))
    for bad in ("yesterday", "2026-13-01", "2026-10-1", "8d"):
        with pytest.raises(ValueError):
            parse_since(bad)


def test_baseline_dates():
    span = {"latest": T, "previous": date(2026, 10, 23), "first": date(2026, 9, 1)}
    assert baseline_date(Since("last_night"), **span) == date(2026, 10, 23)  # the previous night
    assert baseline_date(Since("7d"), **span) == date(2026, 10, 21)  # T - 7 calendar days
    assert baseline_date(Since("date", date(2026, 10, 2)), **span) == date(2026, 10, 2)
    assert baseline_date(Since("date", T), **span) == T  # the bounds are inclusive
    assert baseline_date(Since("date", date(2026, 9, 1)), **span) == date(2026, 9, 1)
    # 7 days can reach back past the first night: an honest baseline (every card then is new)
    assert baseline_date(Since("7d"), **{**span, "first": PREV}) == date(2026, 10, 21)


def test_a_picked_date_outside_the_record_is_refused():
    span = {"latest": T, "previous": PREV, "first": date(2026, 9, 1)}
    with pytest.raises(SinceOutOfRange, match="after the newest recorded night"):
        baseline_date(Since("date", date(2026, 10, 29)), **span)
    with pytest.raises(SinceOutOfRange, match="before the first recorded night"):
        baseline_date(Since("date", date(2026, 8, 31)), **span)


def test_no_baseline_on_an_empty_log_or_a_single_night():
    assert baseline_date(Since("7d"), latest=None, previous=None, first=None) is None
    assert baseline_date(Since("last_night"), latest=T, previous=None, first=T) is None


# ---------------------------------------------------------------------------------------------------------
# the router (pure: every read stubbed; it only reads and calls the compute)
# ---------------------------------------------------------------------------------------------------------


def _thesis(name: str, tid: UUID) -> Thesis:
    return Thesis(id=tid, name=name, narrative="x", basket=[BasketMember(ticker="AAA", role="x")])


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"in_effect": []}
    first, prev = date(2026, 9, 1), PREV

    def fake_list_all(conn, **kwargs):
        seen["list_all_kwargs"] = kwargs
        return [_thesis("Grid", TID)]

    def fake_span(conn, ids):
        return first, T

    def fake_previous(conn, ids, latest):
        seen["previous_for"] = latest
        return prev

    def fake_in_effect(conn, ids, on):
        seen["in_effect"].append(on)
        card = cc(State.ARMED, armed_members=[mem(A)]) if on == T else cc(asof=on)
        return {TID: rec(card)}

    def fake_tickers_for(conn, ids):
        seen["tickers_for"] = set(ids)
        return {A: "AAA"}

    monkeypatch.setattr(router_mod.thesis_repo, "list_all", fake_list_all)
    monkeypatch.setattr(router_mod, "_span", fake_span)
    monkeypatch.setattr(router_mod, "_previous_night", fake_previous)
    monkeypatch.setattr(router_mod, "_in_effect", fake_in_effect)
    monkeypatch.setattr(router_mod.master, "tickers_for", fake_tickers_for)
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()


def test_the_router_reads_the_default_live_list_and_serves_the_compute(stub_client):
    client, seen = stub_client
    r = client.get("/dashboards/what-changed")
    assert r.status_code == 200
    body = r.json()

    # list_all's DEFAULT excludes archived cards; the router must never widen it
    assert seen["list_all_kwargs"] == {}
    assert seen["in_effect"] == [T, PREV] and seen["previous_for"] == T
    assert seen["tickers_for"] == {A}
    assert (body["since"], body["since_date"]) == ("last_night", None)
    assert (body["first_recorded"], body["latest"], body["baseline"]) == (
        "2026-09-01",
        "2026-10-28",
        "2026-10-27",
    )
    assert (body["live_cards"], body["cards_with_change"], body["unrecorded"]) == (1, 1, [])
    assert [(i["section"], i["change"], i["ticker"]) for i in body["items"]] == [
        ("column_moves", "state", None),
        ("names", "armed", "AAA"),
    ]
    move = body["items"][0]
    assert (move["from_state"], move["to_state"], move["thesis_id"]) == (
        "warming",
        "armed",
        str(TID),
    )


def test_the_router_threads_each_since(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/what-changed", params={"since": "7d"}).json()
    assert body["since"] == "7d" and body["baseline"] == "2026-10-21"
    assert seen["in_effect"][-1] == date(2026, 10, 21)
    assert "previous_for" not in seen  # only "last night" needs the previous night

    body = client.get("/dashboards/what-changed", params={"since": "2026-10-02"}).json()
    assert (body["since"], body["since_date"], body["baseline"]) == (
        "date",
        "2026-10-02",
        "2026-10-02",
    )


def test_the_router_serves_a_re_dated_trigger_with_both_dates(stub_client, monkeypatch):
    client, _ = stub_client
    kind = Kind.TECHNICAL_BREAKOUT

    def fake_in_effect(conn, ids, on):
        fired = (
            trig(A, BREAKOUT_OCT_28, kind, T) if on == T else trig(A, BREAKOUT_OCT_27, kind, PREV)
        )
        return {TID: rec(cc(asof=on, triggers_fired=[fired]))}

    monkeypatch.setattr(router_mod, "_in_effect", fake_in_effect)
    body = client.get("/dashboards/what-changed").json()
    assert [
        (i["change"], i["ticker"], i["label"], i["previous_on"], i["on"]) for i in body["items"]
    ] == [("trigger_redated", "AAA", BREAKOUT_OCT_28, "2026-10-27", "2026-10-28")]


@pytest.mark.parametrize(
    "since, needle",
    [
        ("2026-10-29", "after the newest recorded night (2026-10-28)"),
        ("2026-08-01", "before the first recorded night (2026-09-01)"),
        ("2026-02-30", "day is out of range"),
    ],
)
def test_a_bad_since_date_is_a_clear_422(stub_client, since, needle):
    client, _ = stub_client
    r = client.get("/dashboards/what-changed", params={"since": since})
    assert r.status_code == 422
    assert needle in r.json()["detail"]


def test_a_since_that_is_not_one_of_the_choices_is_a_422(stub_client):
    client, _ = stub_client
    assert client.get("/dashboards/what-changed", params={"since": "yesterday"}).status_code == 422


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real calls log and the real list_all (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------


def _seed_thesis(db, name: str) -> UUID:
    t = _thesis(name, uuid.uuid4()).model_copy(update={"tenant_id": DEFAULT_TENANT_ID})
    thesis_repo.upsert(db, t)
    return t.id


def _append(db, tid: UUID, asof: date, state: State, **kw) -> None:
    calls_repo.append(db, cc(state, tid=tid, asof=asof), DEFAULT_TENANT_ID, **kw)


def _get(db, **params) -> dict:
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get("/dashboards/what-changed", params=params)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _moves(body: dict) -> list[tuple]:
    return [
        (i["thesis_name"], i["change"], i["from_state"], i["to_state"], i["reconstructed"])
        for i in body["items"]
    ]


def test_db_the_last_row_per_night_rule(db):
    tid = _seed_thesis(db, "Grid")
    _append(db, tid, date(2026, 10, 26), State.INCUBATING)
    _append(db, tid, PREV, State.ARMED)  # seq 1 on the night
    _append(db, tid, PREV, State.WARMING)  # seq 2 on the same night: the one that stood
    _append(db, tid, T, State.ARMED)
    db.commit()

    body = _get(db)
    assert (body["latest"], body["baseline"]) == ("2026-10-28", "2026-10-27")
    # the baseline is the night's HIGHEST seq (warming), so the move reads warming -> armed
    assert _moves(body) == [("Grid", "state", "warming", "armed", False)]

    # a date between rows: the last row ON OR BEFORE it stood that night
    body = _get(db, since="2026-10-26")
    assert _moves(body) == [("Grid", "state", "incubating", "armed", False)]


def test_db_a_later_row_never_reaches_the_baseline(db):
    tid = _seed_thesis(db, "Grid")
    _append(db, tid, date(2026, 10, 20), State.WARMING)
    _append(db, tid, T, State.ARMED, reconstructed=True)
    db.commit()

    # last night = the previous recorded night (Oct 20, the log appends only on change)
    body = _get(db)
    assert body["baseline"] == "2026-10-20"
    assert _moves(body) == [("Grid", "state", "warming", "armed", True)]
    # since a date after Oct 20 but before T: the T row is not in effect then
    assert _moves(_get(db, since="2026-10-27")) == [("Grid", "state", "warming", "armed", True)]
    # since T itself: nothing moved
    assert _get(db, since="2026-10-28")["items"] == []


def test_db_a_card_created_inside_the_window_is_new(db):
    old, new = _seed_thesis(db, "Old"), _seed_thesis(db, "New")
    _append(db, old, date(2026, 10, 1), State.WARMING)
    _append(db, new, T, State.ARMED)
    db.commit()

    body = _get(db, since="7d")
    assert body["baseline"] == "2026-10-21"
    assert [(i["thesis_name"], i["change"], i["to_state"]) for i in body["items"]] == [
        ("New", "new_card", "armed")
    ]
    assert (body["live_cards"], body["cards_with_change"]) == (2, 1)


def test_db_an_archived_card_is_absent(db):
    live, parked = _seed_thesis(db, "Live"), _seed_thesis(db, "Parked")
    unrecorded = _seed_thesis(db, "No call yet")
    for tid in (live, parked):
        _append(db, tid, PREV, State.WARMING)
        _append(db, tid, T, State.ARMED)
    _append(db, parked, date(2026, 10, 29), State.MANAGING)  # a newer night on the archived card
    thesis_repo.set_archived(db, parked, True)
    db.commit()

    body = _get(db)
    # T and the previous night come from the live cards only: the archived card's newer night is unseen
    assert (body["latest"], body["baseline"]) == ("2026-10-28", "2026-10-27")
    assert _moves(body) == [("Live", "state", "warming", "armed", False)]
    assert body["live_cards"] == 2
    assert body["unrecorded"] == [{"thesis_id": str(unrecorded), "thesis_name": "No call yet"}]


def test_db_an_empty_log_is_an_honest_empty_page(db):
    _seed_thesis(db, "Grid")
    db.commit()
    body = _get(db, since="2026-10-01")  # no record to bound the date against
    assert (body["latest"], body["baseline"], body["first_recorded"], body["items"]) == (
        None,
        None,
        None,
        [],
    )
    assert [r["thesis_name"] for r in body["unrecorded"]] == ["Grid"]
