"""Trigger Log (``docs/dashboards/trigger-log.md``): the pure compute on hand-built ``CallCard``s (one identity
across nights is one row dated the first night, a member-level and a card-level copy merge, the fire-date note,
role and key per kind with the revenue exception, the state pair with "new card" and the row before the window,
``live`` against the card in effect, a trigger first recorded before the window counted but not listed, the
since-return and its IWM span, a flagged name keeping its row, the sources, the order, no aggregate), the router
off one cached read, and DB-backed reads through the real ``calls`` log: a night recorded after a past as-of is
never read, the window ends at the as-of, a bar published after it never prices a row, the highest ``seq`` of a
night stands.
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import trigger_log as router_mod
from dashboards.cache import CACHE
from dashboards.trigger_log import (
    DEFAULT_WINDOW,
    REVENUE_METRIC,
    REVENUE_SOURCE,
    WINDOWS,
    CardNights,
    Counts,
    Night,
    Scope,
    Series,
    TriggerLog,
    card_events,
    collect,
    identity_of,
    is_revenue_acceleration,
    key_of,
    sessions_since,
    since_move,
    source_of,
    trigger_log,
    week_start,
    window_start,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.config import CallConfig
from domain.enums import Grade, Kind, State, Verdict
from domain.market_time import market_today
from domain.signal import Provenance
from domain.thesis import BasketMember, Segment, Thesis
from repositories import thesis_repo

UTC = timezone.utc
T = date(2026, 10, 28)  # the as-of (a Wednesday)
A, B, C = (uuid.UUID(int=n) for n in (0xA1, 0xB2, 0xC3))
TID = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
CIK = "0000876543"
ACC = "0000876543-26-000010"
MIXED = "mixed-scale price history"
CARD = "Card A"

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def trig(
    sid: UUID,
    label: str = "buy",
    kind: Kind = Kind.INSIDER,
    on: date | None = date(2026, 10, 20),
    grade: Grade | None = None,
    sources: list[Provenance] | None = None,
) -> TriggerRef:
    return TriggerRef(
        label=label,
        kind=kind,
        security_id=sid,
        event_date=on,
        grade=grade,
        sources=sources or [],
    )


def mem(sid: UUID, *triggers: TriggerRef) -> MemberCall:
    return MemberCall(security_id=sid, triggers=list(triggers))


def cc(
    state: State = State.WARMING,
    *,
    asof: date,
    triggers: tuple[TriggerRef, ...] = (),
    risks: tuple[TriggerRef, ...] = (),
    armed: tuple[MemberCall, ...] = (),
    watch: tuple[MemberCall, ...] = (),
    tid: UUID = TID,
) -> CallCard:
    return CallCard(
        thesis_id=tid,
        asof=asof,
        state=state,
        verdict=Verdict.NOT_YET,
        expression="",
        key_conviction=KeyState(turned=False, label="Conviction"),
        key_confirmation=KeyState(turned=False, label="Confirmation"),
        triggers_fired=list(triggers),
        risk_signals=list(risks),
        armed_members=list(armed),
        watch_members=list(watch),
    )


def night(card: CallCard, seq: int = 1, reconstructed: bool = False) -> Night:
    return Night(card.asof, seq, reconstructed, card)


def unreadable(asof: date, seq: int = 1) -> Night:
    return Night(asof, seq, False, None)


def nights_of(before: Night | None, *nights: Night) -> CardNights:
    return CardNights(TID, CARD, before, tuple(nights))


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


def weekdays(start: date, end: date) -> list[date]:
    """Every Monday-to-Friday date in ``[start, end]`` (the tests' sessions)."""
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def events(cn: CardNights, **kw):
    return card_events(cn, **kw)[0]


def assemble(
    cards: list[CardNights],
    *,
    asof: date = T,
    window_days: int = 90,
    closes: dict | None = None,
    bench: dict | None = None,
    flagged: dict | None = None,
    roster: dict | None = None,
    master: dict | None = None,
    ciks: dict | None = None,
    scope: Scope = Scope(1, 0, 0),
) -> TriggerLog:
    return trigger_log(
        collect(cards),
        asof=asof,
        window_days=window_days,
        scope=scope,
        roster_tickers=roster if roster is not None else {TID: {A: "AAA", B: "BBB"}},
        master_tickers=master or {},
        ciks=ciks if ciks is not None else {A: CIK, B: CIK},
        closes=closes or {},
        bench=bench or {},
        flagged=flagged or {},
    )


# ---------------------------------------------------------------------------------------------------------
# 1. the identity
# ---------------------------------------------------------------------------------------------------------


def test_one_identity_across_several_nights_is_one_row_dated_the_first_night():
    buy = trig(A, "buy")
    cn = nights_of(None, *(night(cc(asof=day, triggers=(buy,))) for day in (d(5), d(6), d(7))))
    got = events(cn)
    assert [(e.recorded_on, e.security_id, e.label) for e in got] == [(d(5), A, "buy")]


def test_the_label_is_part_of_the_identity_so_a_changed_label_is_another_row():
    """The plan's identity is (security, kind, fire date, label): the same fire date under a new label is a
    new row (What Changed leaves the label out; this page keeps it, so the row says what the record said).
    """
    cn = nights_of(
        None,
        night(cc(asof=d(5), triggers=(trig(A, "buy, 1,000 sh"),))),
        night(cc(asof=d(6), triggers=(trig(A, "buy, 2,000 sh"),))),
    )
    assert [(e.recorded_on, e.label) for e in events(cn)] == [
        (d(5), "buy, 1,000 sh"),
        (d(6), "buy, 2,000 sh"),
    ]


@pytest.mark.parametrize(
    "other",
    [
        trig(B, "buy"),  # another security
        trig(A, "buy", on=d(21)),  # another fire date
        trig(A, "buy", on=None),  # undated is its own identity
        trig(A, "buy", Kind.CATALYST),  # another kind
    ],
)
def test_any_other_field_is_another_identity(other):
    cn = nights_of(
        None,
        night(cc(asof=d(5), triggers=(trig(A, "buy"),))),
        night(cc(asof=d(6), triggers=(trig(A, "buy"), other))),
    )
    assert [e.recorded_on for e in events(cn)] == [d(5), d(6)]


def test_a_member_level_and_a_card_level_copy_merge_and_the_first_copy_stands():
    card_level = trig(A, "buy", grade=Grade.CORE)
    member_copy = trig(
        A, "buy", grade=Grade.FLIP
    )  # the same identity, a different grade on the copy
    cn = nights_of(
        None,
        night(
            cc(
                State.ARMED,
                asof=d(5),
                triggers=(card_level,),
                armed=(mem(A, member_copy),),
                watch=(mem(A, member_copy),),
            )
        ),
    )
    got = events(cn)
    assert len(got) == 1
    assert got[0].role == "entry_trigger" and got[0].grade is Grade.CORE


def test_a_member_level_trigger_alone_is_an_entry_trigger_row():
    only_on_member = trig(B, "bo", Kind.TECHNICAL_BREAKOUT)
    cn = nights_of(
        None,
        night(
            cc(State.ARMED, asof=d(5), armed=(mem(A, trig(A)),), watch=(mem(B, only_on_member),))
        ),
    )
    assert sorted((e.security_id, e.role) for e in events(cn)) == [
        (A, "entry_trigger"),
        (B, "entry_trigger"),
    ]


def test_a_risk_signal_is_its_own_row_and_never_merges_with_an_entry_trigger():
    shared = trig(
        A, "x", Kind.INSIDER
    )  # the same four fields in both lists (kinds never share a role today)
    cn = nights_of(None, night(cc(asof=d(5), triggers=(shared,), risks=(shared,))))
    assert sorted(e.role for e in events(cn)) == ["entry_trigger", "risk_signal"]
    assert identity_of("entry_trigger", shared) != identity_of("risk_signal", shared)


def test_the_same_trigger_on_two_cards_is_a_row_on_each():
    other = uuid.UUID(int=0xE2)
    buy = trig(A, "buy")
    log = assemble(
        [
            CardNights(TID, CARD, None, (night(cc(asof=d(5), triggers=(buy,))),)),
            CardNights(other, "Card B", None, (night(cc(asof=d(6), triggers=(buy,), tid=other)),)),
        ]
    )
    assert sorted((r.thesis_name, r.recorded_on) for r in log.rows) == [
        (CARD, d(5)),
        ("Card B", d(6)),
    ]


def test_the_row_before_the_window_marks_an_identity_as_carried_in_and_never_a_row():
    old = trig(A, "old buy", on=d(1, 9))
    fresh = trig(B, "fresh buy", on=d(20))
    got, carried = card_events(
        nights_of(
            night(cc(asof=d(15, 9), triggers=(old,))),
            night(cc(asof=d(22), triggers=(old, fresh))),
        )
    )
    assert [e.label for e in got] == ["fresh buy"]
    assert carried == 1  # the old buy is still on the card in effect


def test_a_carried_in_trigger_no_longer_on_the_card_is_neither_a_row_nor_counted():
    old = trig(A, "old buy", on=d(1, 9))
    got, carried = card_events(
        nights_of(night(cc(asof=d(15, 9), triggers=(old,))), night(cc(asof=d(22))))
    )
    assert got == [] and carried == 0


def test_a_carried_in_identity_that_lapses_and_returns_is_still_not_a_row():
    """The row before the window is all the log reads of the past, so an identity it shows was recorded before
    the window whatever the window then did with it."""
    old = trig(A, "old buy", on=d(1, 9))
    got, carried = card_events(
        nights_of(
            night(cc(asof=d(15, 9), triggers=(old,))),
            night(cc(asof=d(20))),
            night(cc(asof=d(22), triggers=(old,))),
        )
    )
    assert got == [] and carried == 1


def test_a_card_with_no_row_before_the_window_lists_everything_its_first_night_shows():
    got, carried = card_events(nights_of(None, night(cc(asof=d(5), triggers=(trig(A), trig(B))))))
    assert len(got) == 2 and carried == 0


def test_a_trigger_that_lapses_and_returns_inside_the_window_is_one_row_dated_the_first_night():
    buy = trig(A, "buy")
    cn = nights_of(
        None,
        night(cc(asof=d(5), triggers=(buy,))),
        night(cc(asof=d(6))),
        night(cc(asof=d(7), triggers=(buy,))),
    )
    assert [e.recorded_on for e in events(cn)] == [d(5)]


# ---------------------------------------------------------------------------------------------------------
# 2. the fire date
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "on, recorded, lag",
    [
        (d(20), d(24), 4),  # a Form 4 filed days after its trade
        (d(24), d(24), None),  # the same night: nothing to note
        (None, d(24), None),  # undated: nothing to note
        (d(26), d(24), None),  # a fire date after the night is never "after" it
    ],
)
def test_the_fire_date_note(on, recorded, lag):
    log = assemble([nights_of(None, night(cc(asof=recorded, triggers=(trig(A, on=on),))))])
    (row,) = log.rows
    assert row.event_date == on and row.recorded_on == recorded
    assert row.recorded_days_after == lag


# ---------------------------------------------------------------------------------------------------------
# 3. role and key
# ---------------------------------------------------------------------------------------------------------

REVENUE = trig(
    A,
    "Revenue growth re-accelerated",
    Kind.CATALYST,
    sources=[Provenance(source=REVENUE_SOURCE, ref=ACC, detail={"metric": REVENUE_METRIC})],
)
EIGHT_K = trig(A, "8-K item", Kind.CATALYST, sources=[Provenance(source="8-K", ref=ACC)])
OTHER_XBRL = trig(
    A,
    "cash",
    Kind.CATALYST,
    sources=[Provenance(source="xbrl", ref=ACC, detail={"metric": "cash"})],
)


@pytest.mark.parametrize(
    "kind, key",
    [
        (Kind.INSIDER, "conviction"),
        (Kind.CATALYST, "conviction"),
        (Kind.THEME_CONVICTION, "conviction"),
        (Kind.ACTIVIST_STAKE, "conviction"),
        (Kind.TECHNICAL_BREAKOUT, "confirmation"),
        (Kind.LAGGARD, "confirmation"),
        (Kind.SQUEEZE, None),  # in neither set: an entry trigger that turns no key
    ],
)
def test_an_entry_trigger_turns_the_key_its_kind_belongs_to(kind, key):
    assert key_of(trig(A, kind=kind), "entry_trigger") == key


@pytest.mark.parametrize("kind", [Kind.DILUTION_RISK, Kind.BREAKDOWN, Kind.INSIDER_SELL])
def test_a_risk_signal_turns_no_key(kind):
    assert key_of(trig(A, kind=kind), "risk_signal") is None
    # even a kind that is a conviction kind turns nothing in the risk list
    assert key_of(trig(A, kind=Kind.INSIDER), "risk_signal") is None


def test_the_revenue_acceleration_exception_follows_the_assemblers_dial():
    flipped = CallConfig(revenue_accel_key="confirmation")
    default = CallConfig(revenue_accel_key="conviction")
    assert is_revenue_acceleration(REVENUE)
    assert key_of(REVENUE, "entry_trigger", default) == "conviction"
    assert key_of(REVENUE, "entry_trigger", flipped) == "confirmation"
    # only the screen: another catalyst keeps its kind's key whatever the dial says
    for other in (EIGHT_K, OTHER_XBRL):
        assert not is_revenue_acceleration(other)
        assert key_of(other, "entry_trigger", flipped) == "conviction"


def test_the_revenue_tell_matches_the_detectors_own_provenance():
    """The assembler keys the exception on the detector, which never reaches the wire; this page recognizes the
    screen by what it stamps on its provenance, so a rename on either side must fail here."""
    from signals import revenue_acceleration
    from signals.conviction_source import COMPUTED_CONVICTION_SOURCES

    assert revenue_acceleration._REVENUE_METRIC == REVENUE_METRIC
    assert REVENUE_SOURCE in COMPUTED_CONVICTION_SOURCES


def test_the_row_carries_its_role_and_the_key_it_turns():
    cn = nights_of(
        None,
        night(
            cc(
                asof=d(5),
                triggers=(trig(A, "buy"), trig(B, "bo", Kind.TECHNICAL_BREAKOUT)),
                risks=(trig(A, "dilution", Kind.DILUTION_RISK, on=d(4)),),
            )
        ),
    )
    by_kind = {r.kind: (r.role, r.turns) for r in assemble([cn]).rows}
    assert by_kind == {
        Kind.INSIDER: ("entry_trigger", "conviction"),
        Kind.TECHNICAL_BREAKOUT: ("entry_trigger", "confirmation"),
        Kind.DILUTION_RISK: ("risk_signal", None),
    }


# ---------------------------------------------------------------------------------------------------------
# 4. the state pair
# ---------------------------------------------------------------------------------------------------------


def test_the_first_night_reads_the_row_before_the_window_as_the_night_before():
    buy = trig(A)
    (e,) = events(
        nights_of(
            night(cc(State.WARMING, asof=d(1, 9))),  # outside the window, in effect before it
            night(cc(State.ARMED, asof=d(5), triggers=(buy,))),
        )
    )
    assert (e.from_state, e.to_state, e.new_card) == (State.WARMING, State.ARMED, False)


def test_a_later_night_reads_the_previous_recorded_night_in_the_window():
    buy = trig(A)
    (e,) = events(
        nights_of(
            night(cc(State.INCUBATING, asof=d(1, 9))),
            night(cc(State.WARMING, asof=d(3))),
            night(cc(State.ARMED, asof=d(5), triggers=(buy,))),
        )
    )
    assert (e.from_state, e.to_state) == (State.WARMING, State.ARMED)


def test_an_unchanged_state_reads_the_one_state_both_sides():
    (e,) = events(
        nights_of(
            night(cc(State.WARMING, asof=d(3))),
            night(cc(State.WARMING, asof=d(5), triggers=(trig(A),))),
        )
    )
    assert e.from_state == e.to_state == State.WARMING and e.new_card is False


def test_the_cards_first_row_is_a_new_card():
    (e,) = events(nights_of(None, night(cc(State.WARMING, asof=d(5), triggers=(trig(A),)))))
    assert (e.from_state, e.to_state, e.new_card) == (None, State.WARMING, True)


def test_a_row_before_the_window_that_cannot_be_read_is_not_a_new_card():
    (e,) = events(
        nights_of(unreadable(d(1, 9)), night(cc(State.ARMED, asof=d(5), triggers=(trig(A),))))
    )
    assert (e.from_state, e.new_card) == (None, False)


# ---------------------------------------------------------------------------------------------------------
# 5. live, unreadable nights, the counts
# ---------------------------------------------------------------------------------------------------------


def test_live_reads_the_card_in_effect_at_the_asof():
    gone, stays = trig(A, "gone", on=d(2)), trig(B, "stays", on=d(3))
    got = {
        e.label: e.live
        for e in events(
            nights_of(
                None,
                night(cc(asof=d(5), triggers=(gone, stays))),
                night(cc(asof=d(8), triggers=(stays,))),  # the newest night: "gone" is off the card
            )
        )
    }
    assert got == {"gone": False, "stays": True}


def test_an_unreadable_night_is_counted_and_never_read():
    buy = trig(A)
    cn = nights_of(
        night(cc(State.WARMING, asof=d(1, 9))),
        unreadable(d(3)),
        night(cc(State.ARMED, asof=d(5), triggers=(buy,))),
    )
    (e,) = events(cn)
    assert (
        e.recorded_on == d(5) and e.from_state is None and e.new_card is False
    )  # the night before: unreadable
    assert collect([cn]).nights_unreadable == 1


def test_when_the_card_in_effect_cannot_be_read_live_is_unknown():
    (e,) = events(nights_of(None, night(cc(asof=d(5), triggers=(trig(A),))), unreadable(d(8))))
    assert e.live is None


def test_the_counts_cover_every_card_night_and_unreadable_row():
    quiet = CardNights(uuid.uuid4(), "Card Q", night(cc(asof=d(1, 9))), ())
    never = CardNights(uuid.uuid4(), "Card N", None, ())
    busy = nights_of(
        night(cc(asof=d(1, 9))),
        night(cc(asof=d(5), triggers=(trig(A),))),
        Night(d(6), 1, True, cc(asof=d(6))),
        unreadable(d(7)),
    )
    got = collect([busy, quiet, never])
    assert (got.cards_counted, got.cards_with_nights, got.cards_no_call) == (3, 1, 1)
    assert (got.nights_read, got.nights_reconstructed, got.nights_unreadable) == (3, 1, 1)
    log = assemble([busy, quiet, never], scope=Scope(5, 1, 2))
    assert log.counts.cards_quiet == 1  # a call by the as-of, no night in the window
    assert (log.counts.live_cards, log.counts.not_yet_created, log.counts.roster_fallback) == (
        5,
        1,
        2,
    )


def test_a_reconstructed_night_marks_the_row_it_dates():
    buy = trig(A)
    log = assemble([nights_of(None, Night(d(5), 1, True, cc(asof=d(5), triggers=(buy,))))])
    assert log.rows[0].reconstructed is True and log.counts.nights_reconstructed == 1


# ---------------------------------------------------------------------------------------------------------
# 6. the tape: sessions since, the since-return, the week
# ---------------------------------------------------------------------------------------------------------

SESSIONS = weekdays(d(19), T)  # Mon Oct 19 .. Wed Oct 28, eight sessions


@pytest.mark.parametrize(
    "night_, asof, want",
    [
        (d(23), T, 3),  # Fri -> Wed: Mon, Tue, Wed after it
        (d(24), T, 2),  # a Saturday night: the entry bar is Monday's
        (T, T, 0),  # the as-of's own session
        (d(29), date(2026, 10, 30), None),  # no session on or after it
        (d(23), d(25), 0),  # a Sunday as-of: the last session is the entry's own
        (d(24), d(25), None),  # a weekend night and a weekend as-of: no session between them
    ],
)
def test_sessions_since_counts_the_steps_the_return_spans(night_, asof, want):
    cal = [s for s in SESSIONS if s <= asof]
    assert sessions_since(cal, night_, asof) == want


def test_sessions_since_with_no_calendar_is_none():
    assert sessions_since([], d(23), T) is None


CLOSES = Series.of({d(23): 10.0, d(26): 11.0, d(27): 12.0, d(28): 13.0})
IWM = Series.of({d(23): 100.0, d(26): 101.0, d(27): 102.0, d(28): 103.0})


def test_the_since_return_runs_from_the_first_close_on_or_after_the_night_to_the_last_before_the_asof():
    got = since_move(CLOSES, IWM, d(23), T)
    assert got.status == "priced" and (got.entry_date, got.end_date) == (d(23), T)
    assert got.ret == pytest.approx(13.0 / 10.0 - 1.0)
    assert got.bench_ret == pytest.approx(103.0 / 100.0 - 1.0)  # IWM on the same two dates

    # a night with no bar (Saturday): the entry is Monday's close
    sat = since_move(CLOSES, IWM, d(24), T)
    assert sat.entry_date == d(26) and sat.ret == pytest.approx(13.0 / 11.0 - 1.0)
    assert sat.bench_ret == pytest.approx(103.0 / 101.0 - 1.0)

    # an as-of before the newest bar: bars after it are never read
    early = since_move(CLOSES, IWM, d(23), d(27))
    assert early.end_date == d(27) and early.ret == pytest.approx(12.0 / 10.0 - 1.0)


def test_the_entry_bar_being_the_end_bar_is_a_zero_return_not_unpriced():
    got = since_move(CLOSES, IWM, T, T)
    assert (got.status, got.ret, got.bench_ret) == ("priced", 0.0, 0.0)


@pytest.mark.parametrize(
    "own, night_",
    [
        (None, d(23)),  # no tape at all
        (Series.of({}), d(23)),
        (CLOSES, d(29)),  # no bar on or after the night by the as-of
        (Series.of({d(20): 5.0}), d(23)),  # only bars before the night
    ],
)
def test_a_name_with_no_bar_since_the_night_is_unpriced(own, night_):
    assert since_move(own, IWM, night_, T).status == "unpriced"


def test_iwm_is_none_when_it_has_no_bar_on_either_date_and_never_a_shifted_span():
    no_entry = Series.of({d(26): 101.0, d(27): 102.0, d(28): 103.0})  # no bar on the entry date
    got = since_move(CLOSES, no_entry, d(23), T)
    assert got.status == "priced" and got.ret == pytest.approx(0.3) and got.bench_ret is None
    assert since_move(CLOSES, Series.of({}), d(23), T).bench_ret is None


def test_a_non_positive_close_is_not_a_bar():
    s = Series.of({d(23): 0.0, d(26): -1.0, d(27): 5.0})
    assert s.dates == (d(27),)


def test_the_week_is_the_recorded_nights_monday():
    assert week_start(d(26)) == d(26)  # a Monday
    assert week_start(d(30)) == d(26)  # a Friday
    assert week_start(d(25)) == d(19)  # a Sunday belongs to the week before
    assert week_start(date(2026, 11, 1)) == d(26)


def test_the_window_starts_the_days_back_inclusive():
    assert window_start(T, 90) == date(2026, 7, 30)
    assert window_start(T, 30) == d(28, 9)
    assert list(WINDOWS) == [30, 90, 180] and DEFAULT_WINDOW == 90


# ---------------------------------------------------------------------------------------------------------
# 7. the page: tape on the rows, flagged and unpriced names keep theirs, sources, order, no aggregate
# ---------------------------------------------------------------------------------------------------------


def two_name_log(**kw) -> TriggerLog:
    cn = nights_of(
        night(cc(State.WARMING, asof=d(1, 9))),
        night(
            cc(
                State.ARMED,
                asof=d(23),
                triggers=(trig(A, "buy a", on=d(21)), trig(B, "buy b", on=d(22))),
            )
        ),
    )
    return assemble([cn], **kw)


def test_the_rows_carry_the_since_return_and_the_sessions_on_iwms_calendar():
    log = two_name_log(
        closes={
            A: dict(zip(CLOSES.dates, CLOSES.closes)),
            B: dict(zip(CLOSES.dates, CLOSES.closes)),
        },
        bench=dict(zip(IWM.dates, IWM.closes)),
    )
    row = log.rows[0]
    assert (row.sessions_since, row.since.status) == (3, "priced")
    assert row.since.ret == pytest.approx(0.3) and row.since.bench_ret == pytest.approx(0.03)
    assert log.sessions.source == "benchmarks" and log.sessions.count == 4
    assert (log.sessions.first, log.sessions.last) == (d(23), T)


def test_a_flagged_name_keeps_its_row_unpriced_by_its_own_flag():
    log = two_name_log(
        closes={
            A: dict(zip(CLOSES.dates, CLOSES.closes))
        },  # B's bars are never read: it is flagged
        bench=dict(zip(IWM.dates, IWM.closes)),
        flagged={B: MIXED},
    )
    by = {r.ticker: r for r in log.rows}
    assert set(by) == {"AAA", "BBB"}  # both rows stay
    assert (by["BBB"].since.status, by["BBB"].since.reason) == ("flagged", MIXED)
    assert by["BBB"].since.ret is None and by["AAA"].since.status == "priced"
    assert [(n.ticker, n.reason) for n in log.flagged] == [("BBB", MIXED)]
    assert log.counts.names_flagged == 1 and log.counts.names_unpriced == 0


def test_a_name_with_no_bars_is_unpriced_and_listed_not_dropped():
    log = two_name_log(
        closes={A: dict(zip(CLOSES.dates, CLOSES.closes))}, bench=dict(zip(IWM.dates, IWM.closes))
    )
    assert len(log.rows) == 2
    by = {r.ticker: r.since.status for r in log.rows}
    assert by == {"AAA": "priced", "BBB": "unpriced"}
    assert [n.ticker for n in log.unpriced] == ["BBB"] and log.counts.names_unpriced == 1


def test_with_no_tape_at_all_every_row_stays_and_the_calendar_says_none():
    log = two_name_log()
    assert len(log.rows) == 2 and all(r.since.status == "unpriced" for r in log.rows)
    assert (log.sessions.source, log.sessions.count) == ("none", 0)
    assert all(r.sessions_since is None for r in log.rows)


def test_the_one_accent_is_a_row_recorded_on_the_asofs_own_night():
    cn = nights_of(
        None,
        night(cc(asof=d(27), triggers=(trig(A, "earlier"),))),
        night(cc(asof=T, triggers=(trig(A, "earlier"), trig(B, "tonight")))),
    )
    assert {r.label: r.newest for r in assemble([cn]).rows} == {"earlier": False, "tonight": True}


def test_the_ticker_is_the_rosters_else_the_masters_else_none_and_counted():
    cn = nights_of(None, night(cc(asof=d(5), triggers=(trig(A), trig(B), trig(C, "c")))))
    log = assemble([cn], roster={TID: {A: "AAA"}}, master={B: "BBB-M"})
    assert {r.security_id: r.ticker for r in log.rows} == {A: "AAA", B: "BBB-M", C: None}
    assert log.counts.rows_no_ticker == 1


def test_the_rows_are_newest_night_first_then_ticker():
    cn = nights_of(
        None,
        night(cc(asof=d(5), triggers=(trig(B, "early b"),))),
        night(cc(asof=d(6), triggers=(trig(B, "late b"), trig(A, "late a")))),
    )
    assert [r.label for r in assemble([cn]).rows] == ["late a", "late b", "early b"]


def test_every_row_has_its_own_key_and_a_week():
    cn = nights_of(None, night(cc(asof=d(5), triggers=(trig(A), trig(A, "other")))))
    rows = assemble([cn]).rows
    assert len({r.key for r in rows}) == 2
    assert all(r.week == week_start(d(5)) for r in rows)


def test_the_counts_say_what_was_listed_by_role():
    cn = nights_of(
        None,
        night(cc(asof=d(5), triggers=(trig(A),), risks=(trig(A, "dil", Kind.DILUTION_RISK),))),
    )
    counts = assemble([cn]).counts
    assert (counts.entry_rows, counts.risk_rows, counts.carried_live) == (1, 1, 0)


def test_the_sources_follow_the_evidence_ledgers_rule():
    form4 = source_of(
        Provenance(source="form4", ref=ACC, detail={"shares": 1000, "code": "P", "note": None}), CIK
    )
    assert form4.url == "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/"
    assert form4.detail == ("code: P", "shares: 1000")  # sorted, a missing field left out
    assert form4.bar_date is None

    price = source_of(Provenance(source="price", ref=f"price:{A}:2026-10-27"), CIK)
    assert price.url is None and price.bar_date == date(2026, 10, 27)

    stored = source_of(Provenance(source="doc", ref="https://example.test/award"), CIK)
    assert stored.url == "https://example.test/award"
    assert source_of(Provenance(source="doc", ref="award 123"), CIK).url is None
    assert (
        source_of(Provenance(source="form4", ref=ACC), None).url is None
    )  # no issuer CIK: never a guessed link


def test_the_page_states_no_aggregate():
    """Rule 6: a median move "after insider triggers" is an event-outcome statistic, sliced by market-cap tier on
    the Track Record page. This page lists: its payload holds counts of what was read and never a statistic over
    the rows' returns."""
    banned = (
        "median",
        "mean",
        "avg",
        "average",
        "hit_rate",
        "win_rate",
        "pooled",
        "excess",
        "stdev",
    )
    names = [f.name for cls in (Counts, TriggerLog) for f in fields(cls)]
    for model in (
        router_mod.TriggerLogOut,
        router_mod.TriggerLogCountsOut,
        router_mod.TriggerLogRowOut,
        router_mod.TriggerLogSinceOut,
    ):
        names += list(model.model_fields)
    assert not [n for n in names if any(b in n for b in banned)]
    import dashboards.trigger_log as compute

    source = open(compute.__file__, encoding="utf-8").read()
    for word in ("statistics", "fmean", "median(", "numpy", "polars"):
        assert word not in source


# ---------------------------------------------------------------------------------------------------------
# 8. the router (pure: the heavy read stubbed; it threads serve_view and caches one read)
# ---------------------------------------------------------------------------------------------------------


def sample_log(window_days: int = 90) -> TriggerLog:
    """Two nights, two triggers, priced on a two-bar tape, as the stubbed read returns it."""
    t1 = trig(
        A, "buy", on=d(22), sources=[Provenance(source="form4", ref=ACC, detail={"shares": 1000})]
    )
    t2 = trig(
        B,
        "8-day breakout",
        Kind.TECHNICAL_BREAKOUT,
        on=d(28),
        grade=Grade.FLIP,
        sources=[Provenance(source="price", ref=f"price:{B}:2026-10-28")],
    )
    cn = nights_of(
        night(cc(State.WARMING, asof=d(1))),
        night(cc(State.ARMED, asof=d(27), triggers=(t1,))),
        night(cc(State.ARMED, asof=T, triggers=(t1, t2))),
    )
    return assemble(
        [cn],
        window_days=window_days,
        closes={A: {d(27): 10.0, T: 11.0}, B: {T: 20.0}},
        bench={d(27): 100.0, T: 101.0},
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": []}

    def fake_read(conn, asof, window_days, known_at, clock):
        seen["reads"].append((asof, window_days, known_at, clock))
        return sample_log(window_days)

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_log", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_repeat_requests_off_one_cached_read(stub_client):
    client, seen = stub_client
    first = client.get("/dashboards/trigger-log", params={"asof": PAST.isoformat()}).json()
    assert (
        first["cache"] == "computed" and first["window_days"] == 90 and first["clock"] == "public"
    )
    assert (
        client.get("/dashboards/trigger-log", params={"asof": PAST.isoformat()}).json()["cache"]
        == "hit"
    )
    assert len(seen["reads"]) == 1
    asof, window, known_at, clock = seen["reads"][0]
    assert (asof, window, clock) == (PAST, 90, "public") and known_at is not None

    # another window is another read; the live as-of is the unchanged live view
    client.get("/dashboards/trigger-log", params={"asof": PAST.isoformat(), "days": 30})
    assert [r[1] for r in seen["reads"]] == [90, 30]
    client.get("/dashboards/trigger-log", params={"asof": market_today().isoformat()})
    assert seen["reads"][-1][2:] == (None, "record")


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/trigger-log", params={"asof": "2026-10-28"}).json()
    assert [r["label"] for r in body["rows"]] == [
        "8-day breakout",
        "buy",
    ]  # newest recorded night first
    bo, buy = body["rows"]
    assert (bo["role"], bo["kind"], bo["grade"], bo["turns"]) == (
        "entry_trigger",
        "technical_breakout",
        "flip",
        "confirmation",
    )
    assert (bo["recorded_on"], bo["week"], bo["newest"], bo["live"]) == (
        "2026-10-28",
        "2026-10-26",
        True,
        True,
    )
    assert (bo["from_state"], bo["to_state"], bo["new_card"]) == ("armed", "armed", False)
    assert bo["sources"][0]["bar_date"] == "2026-10-28" and bo["sources"][0]["url"] is None
    assert (
        bo["since"]["status"] == "priced"
        and bo["since"]["ret"] == 0.0
        and bo["sessions_since"] == 0
    )

    assert (buy["recorded_on"], buy["recorded_days_after"], buy["newest"]) == (
        "2026-10-27",
        5,
        False,
    )
    assert (buy["from_state"], buy["to_state"]) == ("warming", "armed")
    assert buy["since"]["ret"] == pytest.approx(0.1) and buy["since"]["bench_ret"] == pytest.approx(
        0.01
    )
    assert buy["sessions_since"] == 1
    assert buy["sources"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/876543/")
    assert buy["sources"][0]["detail"] == ["shares: 1000"]

    assert body["window_start"] == "2026-07-30" and body["counts"]["entry_rows"] == 2
    assert body["sessions"] == {
        "source": "benchmarks",
        "count": 2,
        "first": "2026-10-27",
        "last": "2026-10-28",
    }
    assert body["flagged"] == [] and body["unpriced"] == [] and isinstance(body["compute_ms"], int)


def test_the_router_rejects_an_unknown_window_and_a_missing_asof(stub_client):
    client, seen = stub_client
    assert (
        client.get("/dashboards/trigger-log", params={"asof": "2026-10-28", "days": 45}).status_code
        == 422
    )
    assert client.get("/dashboards/trigger-log").status_code == 422
    assert [int(w) for w in router_mod.TriggerLogDays] == list(WINDOWS)
    assert seen["reads"] == []


# ---------------------------------------------------------------------------------------------------------
# 9. DB-backed: the real calls log, the real bars, no lookahead (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a Friday: the past as-of
LATER = date(2026, 6, 19)  # a later past as-of, after every "late" row below
SESSION_DAYS = weekdays(date(2026, 3, 2), LATER)
_THAW = datetime(2026, 9, 1, 18, 13, 31, tzinfo=UTC)
F4_ACC = "0000876543-26-000020"


def _security(db, ticker: str, cik: str | None, kind: str = "equity") -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, "2026-01-01"),
        )
    return sid


def _bar(db, sid: UUID, day: date, close: float, recorded_at: datetime) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": day,
            "close": close,
            "volume": 1000.0,
            "valid_from": day,
            "recorded_at": recorded_at,
        },
    )


def _insert_call(db, tid: UUID, card: CallCard, *, reconstructed: bool = False) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, reconstructed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                DEFAULT_TENANT_ID,
                tid,
                card.asof,
                card.state.value,
                card.verdict.value,
                Json(card.model_dump(mode="json")),
                reconstructed,
            ),
        )


def _card(db, name: str, members: list[BasketMember], *, archived: bool = False) -> UUID:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=members,
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    if archived:
        thesis_repo.set_archived(db, thesis.id, True)
    db.commit()
    return thesis.id


class World:
    """One card on two names with a five-night log, bars through ``LATER`` for both and IWM, and the ids."""

    def __init__(self, db) -> None:
        self.sa = _security(db, "AAA", CIK)
        self.sb = _security(db, "BBB", CIK)
        iwm = _security(db, "IWM", None, kind="etf")
        # a bar per weekday, each public at its own session, running through LATER (so bars AFTER the past
        # as-of exist in the store and must stay invisible to it)
        self.close = {
            self.sa: {s: 10.0 + 0.1 * i for i, s in enumerate(SESSION_DAYS)},
            self.sb: {s: 20.0 + 0.2 * i for i, s in enumerate(SESSION_DAYS)},
            iwm: {s: 100.0 + 0.5 * i for i, s in enumerate(SESSION_DAYS)},
        }
        for sid, closes in self.close.items():
            for day, close in closes.items():
                _bar(db, sid, day, close, datetime.combine(day, time.min, tzinfo=UTC))
        self.iwm = iwm
        self.tid = _card(
            db,
            CARD,
            [
                BasketMember(ticker="AAA", role="x", security_id=self.sa, segment="Core"),
                BasketMember(ticker="BBB", role="x", security_id=self.sb, segment="Core"),
            ],
        )

        def t(sid, label, kind, on, sources=None):
            return trig(sid, label, kind, on, sources=sources)

        self.x = t(self.sa, "old buy", Kind.INSIDER, date(2026, 2, 17))
        self.y = t(
            self.sb,
            "8-day breakout",
            Kind.TECHNICAL_BREAKOUT,
            date(2026, 4, 13),
            [Provenance(source="price", ref=f"price:{self.sb}:2026-04-13")],
        )
        self.z = t(
            self.sb,
            "buy",
            Kind.INSIDER,
            date(2026, 5, 7),
            [Provenance(source="form4", ref=F4_ACC, detail={"shares": 1000})],
        )
        self.w = t(self.sa, "laggard", Kind.LAGGARD, date(2026, 5, 11))
        self.v = t(self.sa, "late buy", Kind.INSIDER, date(2026, 6, 9))
        ghost = trig(self.sb, "ghost", Kind.DILUTION_RISK, date(2026, 5, 11))

        def call(day, state, *triggers, risks=(), **kw):
            _insert_call(
                db,
                self.tid,
                cc(state, asof=day, triggers=triggers, risks=risks, tid=self.tid),
                **kw,
            )

        call(date(2026, 2, 20), State.WARMING, self.x)  # before every window below
        call(date(2026, 4, 14), State.WARMING, self.x, self.y, reconstructed=True)
        # two rows on one night: the HIGHER seq stood, so the ghost risk signal of the first never lists
        call(date(2026, 5, 12), State.ARMED, self.x, self.y, self.z, risks=(ghost,))
        call(date(2026, 5, 12), State.ARMED, self.x, self.y, self.z, self.w)
        call(date(2026, 6, 10), State.ARMED, self.x, self.y, self.z, self.w, self.v)  # after PAST
        db.commit()


def _get(db, asof: date, days: int = 90, **patch) -> dict:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get(
            "/dashboards/trigger-log", params={"asof": asof.isoformat(), "days": days}
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _labels(body: dict) -> list[tuple[str, str]]:
    return [(r["recorded_on"], r["label"]) for r in body["rows"]]


def test_db_a_night_recorded_after_the_asof_is_not_read(db):
    """The log holds a night dated June 10; the as-of June 5 never sees it (its trigger is no row, it is not in
    the nights read, it never makes anything live), and the later as-of June 19 does."""
    World(db)
    past = _get(db, PAST)
    assert ("2026-06-10", "late buy") not in _labels(past)
    assert (
        past["counts"]["nights_read"] == 2
    )  # Apr 14, and the two rows of May 12 read as ONE night
    later = _get(db, LATER)
    assert ("2026-06-10", "late buy") in _labels(later)
    late = next(r for r in later["rows"] if r["label"] == "late buy")
    assert late["live"] is True and late["from_state"] == "armed" and late["to_state"] == "armed"


def test_db_the_window_at_a_past_asof_ends_there(db):
    w = World(db)
    body = _get(db, PAST)
    assert (body["asof"], body["window_start"], body["window_days"], body["clock"]) == (
        "2026-06-05",
        "2026-03-07",
        90,
        "public",
    )
    # the 90-day window reads Apr 14 and May 12: the row of Feb 20 is the one before it
    assert _labels(body) == [
        ("2026-05-12", "laggard"),
        ("2026-05-12", "buy"),
        ("2026-04-14", "8-day breakout"),
    ]
    assert (
        body["counts"]["carried_live"] == 1
    )  # the Feb buy: first recorded before the window, still live
    assert (body["counts"]["entry_rows"], body["counts"]["risk_rows"]) == (3, 0)

    # a narrower window moves its first night and its row before it
    narrow = _get(db, PAST, days=30)
    assert narrow["window_start"] == "2026-05-06"
    assert _labels(narrow) == [("2026-05-12", "laggard"), ("2026-05-12", "buy")]
    assert narrow["counts"]["carried_live"] == 2  # the Feb buy and the Apr breakout
    z = next(r for r in narrow["rows"] if r["label"] == "buy")
    assert (z["from_state"], z["to_state"]) == (
        "warming",
        "armed",
    )  # the night before: Apr 14's row

    # an earlier past as-of: nothing recorded after it is read
    early = _get(db, date(2026, 5, 1), days=30)
    assert early["window_start"] == "2026-04-01"
    assert _labels(early) == [("2026-04-14", "8-day breakout")]
    assert early["counts"]["nights_read"] == 1
    assert w.tid is not None


def test_db_the_highest_seq_of_a_night_stands_and_the_rows_say_where_they_came_from(db):
    w = World(db)
    body = _get(db, PAST)
    assert not any(r["label"] == "ghost" for r in body["rows"])  # in the night's first row only
    by = {r["label"]: r for r in body["rows"]}

    # Y: recorded on a reconstructed night, state unchanged against the row before the window
    y = by["8-day breakout"]
    assert y["reconstructed"] is True and (y["from_state"], y["to_state"]) == ("warming", "warming")
    assert y["recorded_days_after"] == 1 and y["event_date"] == "2026-04-13"
    assert (y["role"], y["turns"], y["ticker"]) == ("entry_trigger", "confirmation", "BBB")
    assert y["sources"][0]["bar_date"] == "2026-04-13" and y["sources"][0]["url"] is None
    assert body["counts"]["nights_reconstructed"] == 1

    # Z: its Form 4 accession links to EDGAR through the issuer's CIK
    z = by["buy"]
    assert (
        z["sources"][0]["url"]
        == "https://www.sec.gov/Archives/edgar/data/876543/000087654326000020/"
    )
    assert (z["turns"], z["live"], z["recorded_days_after"]) == ("conviction", True, 5)
    assert z["thesis_id"] == str(w.tid) and z["thesis_name"] == CARD


def test_db_the_since_return_reads_the_bars_public_by_the_asof(db):
    """Bars exist through June 19 and the as-of bar is then re-versioned at double AFTER the as-of's day:
    the June 5 view prices every row on the ORIGINAL bars ending June 5, IWM on the same two dates.
    """
    w = World(db)
    _bar(db, w.sa, PAST, w.close[w.sa][PAST] * 2, _THAW + timedelta(days=7))
    db.commit()
    body = _get(db, PAST)
    by = {r["label"]: r for r in body["rows"]}

    def at(sid, day):
        return w.close[sid][day]

    z = by["buy"]  # BBB, recorded May 12
    assert z["since"]["status"] == "priced"
    assert (z["since"]["entry_date"], z["since"]["end_date"]) == ("2026-05-12", "2026-06-05")
    assert z["since"]["ret"] == pytest.approx(at(w.sb, PAST) / at(w.sb, date(2026, 5, 12)) - 1.0)
    assert z["since"]["bench_ret"] == pytest.approx(
        at(w.iwm, PAST) / at(w.iwm, date(2026, 5, 12)) - 1.0
    )
    assert z["sessions_since"] == SESSION_DAYS.index(PAST) - SESSION_DAYS.index(date(2026, 5, 12))

    laggard = by["laggard"]  # AAA, recorded May 12: the corrected bar is not public by June 5
    assert laggard["since"]["ret"] == pytest.approx(
        at(w.sa, PAST) / at(w.sa, date(2026, 5, 12)) - 1.0
    )

    # the calendar ends at the as-of whatever bars the store holds after it
    assert (body["sessions"]["source"], body["sessions"]["last"]) == ("benchmarks", "2026-06-05")


def test_db_a_flagged_name_keeps_its_row_and_its_bars_are_never_read(db, monkeypatch):
    w = World(db)
    asked: list[list[UUID]] = []
    real = router_mod._closes

    def spy(conn, sids, *a, **k):
        asked.append(list(sids))
        return real(conn, sids, *a, **k)

    monkeypatch.setattr(router_mod, "price_flags", lambda *a, **k: {w.sb: MIXED})
    monkeypatch.setattr(router_mod, "_closes", spy)
    body = _get(db, PAST)
    by = {r["label"]: r for r in body["rows"]}
    assert (by["buy"]["since"]["status"], by["buy"]["since"]["reason"]) == ("flagged", MIXED)
    assert by["laggard"]["since"]["status"] == "priced"
    assert [(n["ticker"], n["reason"]) for n in body["flagged"]] == [("BBB", MIXED)]
    assert asked == [[w.sa]]  # only the unflagged name was priced
    assert len(body["rows"]) == 3  # no row dropped


def test_db_an_archived_card_is_absent_and_a_card_with_no_call_is_counted(db):
    w = World(db)
    parked = _card(
        db,
        "Card P",
        [BasketMember(ticker="AAA", role="x", security_id=w.sa, segment="Core")],
        archived=True,
    )
    _insert_call(
        db,
        parked,
        cc(State.ARMED, asof=date(2026, 5, 12), triggers=(trig(w.sa, "parked buy"),), tid=parked),
    )
    _card(db, "Card N", [BasketMember(ticker="BBB", role="x", security_id=w.sb, segment="Core")])
    db.commit()
    body = _get(db, PAST)
    assert all(r["label"] != "parked buy" for r in body["rows"])
    counts = body["counts"]
    assert (counts["cards_counted"], counts["cards_with_nights"], counts["cards_no_call"]) == (
        2,
        1,
        1,
    )
    assert counts["live_cards"] == 2 and counts["not_yet_created"] == 0


def test_db_a_stored_card_that_no_longer_validates_is_counted_not_a_500(db):
    w = World(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                DEFAULT_TENANT_ID,
                w.tid,
                date(2026, 5, 20),
                "armed",
                "not_yet",
                Json({"legacy": True}),
            ),
        )
    db.commit()
    body = _get(db, PAST)
    assert body["counts"]["nights_unreadable"] == 1 and body["counts"]["nights_read"] == 3
    # the newest row on or before the as-of is the unreadable one: liveness is unknown, never guessed
    assert all(r["live"] is None for r in body["rows"])


def test_db_the_roster_at_the_asof_names_the_ticker(db):
    """A past as-of falls back to today's roster when no snapshot predates it (counted on the line); the
    ticker is the card's own, the master's only for a name the roster lacks."""
    w = World(db)
    body = _get(db, PAST)
    assert {r["ticker"] for r in body["rows"]} == {"AAA", "BBB"}
    assert body["counts"]["roster_fallback"] == 1
    assert {r["security_id"] for r in body["rows"]} <= {str(w.sa), str(w.sb)}
