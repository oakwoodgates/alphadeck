"""Ticker Lookup (``docs/dashboards/ticker-lookup.md``): the pure compute on hand-built cards, calls, decisions,
filings and display readings (the cards section: two links merged, the Context / Bench tag, the context role, the
place on the card in effect: armed, watch, trigger-bearing, none, not counted, no record; the archived and
excluded folds; the trigger identity merge scoped to one company: one row per identity dated its first night, a
member-level and a card-level copy merging, the winning row per night, the night before the window, ``live``; the
decisions filter and its strikes; the catalysts; the filings grouping and routine cap; the tape grid read off the
REAL display members' computes, so a renamed metric key fails here), the router off one cached read, and, DB-backed,
the exact-ticker resolve and a page at past as-ofs where a basket row added after the as-of, a filing filed after
it, a call recorded after it and a decision logged after it are none of them on the page."""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import ticker_lookup as router_mod
from dashboards.cache import CACHE
from dashboards.filings import KIND_ORDER, FilingRow
from dashboards.ticker_lookup import (
    _SPECS,
    ETF_FLOW,
    INSIDER,
    MEMBER_LABEL,
    NO_READING,
    PATH,
    RANGE,
    RETURNS,
    ROUTINE_CAP,
    RS,
    RVOL,
    SMA,
    VCP,
    VOLUME,
    CardMeta,
    DecisionRaw,
    IdentityInputs,
    RecordedNight,
    cap_of,
    card_catalysts,
    cards_section,
    decision_rows,
    detail_lines,
    edgar_company_url,
    figure,
    filings_section,
    identity_of,
    in_effect,
    lookup,
    place_of,
    refs_on,
    sparkline_of,
    tape_of,
    trigger_log,
    turns_key,
    winning_nights,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Authorship, ContextRole, Grade, Kind, State, Verdict
from domain.market_time import market_today
from domain.signal import Provenance
from domain.thesis import (
    BENCH_LABEL,
    CONTEXT_LABEL,
    BasketMember,
    Catalyst,
    ExcludedName,
    Segment,
    Thesis,
)
from repositories import calls_repo, thesis_repo
from securities import master
from signals.display import (
    etf_flow,
    insider_flow,
    price_path,
    range52w,
    registered_display_members,
    relative_strength,
    rvol,
    sma,
    trailing_returns,
    vcp,
    volume_regime,
)
from signals.display.base import DisplayBasis, DisplayEvent, DisplayHeadline, DisplaySignal

UTC = timezone.utc
ASOF = date(2026, 10, 28)  # a Wednesday
SID = UUID("00000000-0000-0000-0000-0000000000a1")  # the company the page is about
OTHER = UUID("00000000-0000-0000-0000-0000000000a2")
T1, T2, T3 = (UUID(f"00000000-0000-0000-0000-0000000000b{i}") for i in (1, 2, 3))
CIK = "0000876543"
ACC = "0000876543-26-000010"


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def key(label: str, turned: bool = False) -> KeyState:
    return KeyState(turned=turned, label=label)


def trig(
    sid: UUID = SID,
    label: str = "8-day breakout",
    kind: Kind = Kind.TECHNICAL_BREAKOUT,
    on: date | None = date(2026, 10, 20),
    **kw,
) -> TriggerRef:
    return TriggerRef(label=label, kind=kind, security_id=sid, event_date=on, **kw)


def mem(sid: UUID = SID, **kw) -> MemberCall:
    return MemberCall(security_id=sid, **kw)


def cc(
    state: State = State.WARMING,
    *,
    tid: UUID = T1,
    asof: date = ASOF,
    verdict: Verdict = Verdict.NOT_YET,
    **kw,
) -> CallCard:
    return CallCard(
        thesis_id=tid,
        asof=asof,
        state=state,
        verdict=verdict,
        expression="",
        key_conviction=key("Conviction"),
        key_confirmation=key("Confirmation"),
        **kw,
    )


def night(card: CallCard, *, seq: int = 1, reconstructed: bool = False) -> RecordedNight:
    return RecordedNight(
        thesis_id=card.thesis_id,
        asof=card.asof,
        seq=seq,
        reconstructed=reconstructed,
        card=card,
    )


def member(
    sid: UUID = SID, ticker: str = "TLA", segment: str | None = "Core", **kw
) -> BasketMember:
    return BasketMember(ticker=ticker, role="the name", security_id=sid, segment=segment, **kw)


def card(
    name: str,
    tid: UUID,
    members: list[BasketMember],
    segments: tuple[str, ...] = ("Core", "Rest"),
    **kw,
) -> Thesis:
    return Thesis(
        id=tid,
        name=name,
        narrative="x",
        segments=[Segment(label=s) for s in segments],
        basket=members,
        **kw,
    )


def metas(*cards: Thesis) -> list[CardMeta]:
    return [CardMeta(t.id, t.name, "TLA") for t in cards]


def log(nights_by_card: dict[UUID, list[RecordedNight]], prior=None, *, cards=(T1,), cik=CIK):
    return trigger_log(
        SID,
        [CardMeta(t, f"Card {i}", "TLA") for i, t in enumerate(cards, 1)],
        nights_by_card,
        prior or {},
        window_start=ASOF - timedelta(days=365),
        cik=cik,
    )


# ---------------------------------------------------------------------------------------------------------
# the cards section
# ---------------------------------------------------------------------------------------------------------


def section(counted, effect=None, archived=(), sid=SID):
    return cards_section(
        sid,
        counted,
        effect or {},
        archived,
        live_cards=len(counted) + 1,
        not_yet_created=1,
        roster_fallback=0,
    )


def test_two_links_of_one_card_are_one_row_listing_both_in_the_chain_order():
    a = card(
        "Card A",
        T1,
        [
            member(segment="Rest", thesis_fit="why rest"),
            member(segment="Core", thesis_fit="why core"),
            member(OTHER, "OTH", segment="Core"),
        ],
    )
    got = section([a])
    assert [r.thesis_name for r in got.rows] == ["Card A"]
    row = got.rows[0]
    assert [(k.link, k.thesis_fit) for k in row.links] == [
        ("Core", "why core"),
        ("Rest", "why rest"),
    ]
    assert row.ticker == "TLA" and row.silent is None
    assert (got.live_cards, got.not_yet_created, got.counted) == (2, 1, 1)


def test_a_card_without_the_company_has_no_row_and_rows_are_a_to_z():
    z = card("Zeta", T2, [member()])
    a = card("Alpha", T1, [member()])
    none = card("Beta", T3, [member(OTHER, "OTH")])
    assert [r.thesis_name for r in section([z, none, a]).rows] == ["Alpha", "Zeta"]
    assert section([none]).rows == ()


def test_a_silent_name_reads_not_counted_keeps_its_tag_and_the_role_and_is_never_armed():
    a = card(
        "Card A",
        T1,
        [member(segment=BENCH_LABEL, context_role=ContextRole.COUNTER_CASE)],
    )
    # even a card that (impossibly) lists the silent name as armed: the place is "not counted"
    armed = cc(State.ARMED, armed_members=[mem(verdict=Verdict.CORE_ENTRY)])
    row = section([a], {T1: night(armed)}).rows[0]
    assert (row.silent, row.context_role) == (BENCH_LABEL, "counter_case")
    assert row.place.kind == "not_counted"
    assert row.state == "armed"  # the card's recorded state is still the card's


def test_the_place_is_armed_with_the_members_verdict_grade_and_setup_strength():
    armed = cc(
        State.ARMED,
        verdict=Verdict.CORE_ENTRY,
        armed_members=[
            mem(OTHER, verdict=Verdict.STARTER_ENTRY),
            mem(
                verdict=Verdict.CORE_ENTRY,
                entry_grade=Grade.CORE,
                confidence=0.62,
                triggers=[trig(kind=Kind.INSIDER, label="CEO buy")],
            ),
        ],
        triggers_fired=[trig(kind=Kind.INSIDER, label="CEO buy")],
    )
    place = place_of(night(armed), SID, False)
    assert (place.kind, place.verdict, place.grade, place.strength) == (
        "armed",
        "core_entry",
        "core",
        0.62,
    )
    assert (place.live_triggers, place.turns) == (1, ("conviction",))  # one identity, merged


def test_the_place_is_watch_for_a_confirmation_only_member():
    watch = cc(watch_members=[mem(triggers=[trig()])], triggers_fired=[trig()])
    place = place_of(night(watch), SID, False)
    assert (place.kind, place.live_triggers, place.turns) == ("watch", 1, ("confirmation",))
    assert place.verdict is None and place.strength is None


def test_the_place_is_trigger_bearing_with_the_count_and_the_keys_it_turns():
    fired = cc(
        triggers_fired=[
            trig(kind=Kind.INSIDER, label="CEO buy", on=date(2026, 10, 25)),
            trig(kind=Kind.TECHNICAL_BREAKOUT, label="breakout"),
            trig(OTHER, kind=Kind.INSIDER, label="someone else"),
        ],
        risk_signals=[trig(kind=Kind.DILUTION_RISK, label="convert overhang")],
    )
    place = place_of(night(fired), SID, False)
    assert place.kind == "triggers"
    assert (place.live_triggers, place.turns, place.live_risks) == (
        2,
        ("conviction", "confirmation"),
        1,
    )


def test_a_risk_signal_alone_is_not_trigger_bearing_and_says_nothing_else():
    risky = cc(risk_signals=[trig(kind=Kind.DILUTION_RISK, label="convert overhang")])
    place = place_of(night(risky), SID, False)
    assert (place.kind, place.live_triggers, place.live_risks) == ("none", 0, 1)


def test_the_place_is_none_for_a_quiet_name_and_no_record_without_a_call():
    assert place_of(night(cc(armed_members=[mem(OTHER)])), SID, False).kind == "none"
    assert place_of(None, SID, False).kind == "no_record"
    a = card("Card A", T1, [member()])
    row = section([a], {T1: None}).rows[0]
    assert (row.state, row.verdict, row.recorded_on, row.place.kind) == (
        None,
        None,
        None,
        "no_record",
    )


def test_the_recorded_state_is_the_call_in_effect_and_marks_a_reconstructed_night():
    a = card("Card A", T1, [member()])
    got = section([a], {T1: night(cc(State.ARMED, asof=date(2026, 10, 27)), reconstructed=True)})
    row = got.rows[0]
    assert (row.state, row.verdict, row.recorded_on, row.reconstructed) == (
        "armed",
        "not_yet",
        date(2026, 10, 27),
        True,
    )


def test_operator_conviction_is_a_displayed_weight_and_never_reaches_the_place():
    base = card("Card A", T1, [member(conviction=None)])
    heavy = card("Card A", T1, [member(conviction=5)])
    call = night(cc(State.ARMED, armed_members=[mem(verdict=Verdict.CORE_ENTRY, confidence=0.5)]))
    r0 = section([base], {T1: call}).rows[0]
    r5 = section([heavy], {T1: call}).rows[0]
    assert [k.weight for k in r0.links] == [None] and [k.weight for k in r5.links] == [5]
    assert r0.place == r5.place  # #4: the weight is metadata, the call never reads it


def test_authorship_and_sign_off_are_carried_as_stored():
    a = card(
        "Card A",
        T1,
        [
            member(segment="Core", thesis_fit="drafted", signed_off=True),
            member(
                segment="Rest",
                thesis_fit="mine",
                authored_by=Authorship.OPERATOR_EDITED,
                signed_off=False,
            ),
        ],
    )
    row = section([a]).rows[0]
    assert [(k.authored_by, k.signed_off) for k in row.links] == [
        ("system_drafted", True),
        ("operator_edited", False),
    ]
    assert row.signed_off is False  # every row must be signed off for the card to read signed off


def test_the_archived_and_excluded_folds_name_only_the_cards_that_hold_or_exclude_it():
    live = card(
        "Live",
        T1,
        [member()],
        exclusions=[ExcludedName(security_id=OTHER, ticker="OTH", reason="not this one")],
    )
    excl = card(
        "Excluder",
        T2,
        [member(OTHER, "OTH")],
        exclusions=[ExcludedName(security_id=SID, ticker="TLA", reason="a rival, not a fit")],
    )
    old = card("Old card", T3, [member()])
    unrelated = card("Unrelated", uuid.uuid4(), [member(OTHER, "OTH")])
    got = section([live, excl], archived=[unrelated, old])
    assert [(a.thesis_name) for a in got.archived] == ["Old card"]
    assert [(x.thesis_name, x.reason) for x in got.excluded] == [("Excluder", "a rival, not a fit")]
    # the company is on "Live" only: an excluding card is not a row
    assert [r.thesis_name for r in got.rows] == ["Live"]


# ---------------------------------------------------------------------------------------------------------
# the call in effect
# ---------------------------------------------------------------------------------------------------------


def test_each_night_stands_at_its_highest_seq_and_the_window_ends_in_the_row_in_effect():
    first = night(cc(State.WARMING, asof=date(2026, 10, 26)), seq=1)
    late1 = night(cc(State.ARMED, asof=date(2026, 10, 27)), seq=2)
    late2 = night(cc(State.WARMING, asof=date(2026, 10, 27)), seq=3)
    won = winning_nights([late2, first, late1])
    assert [(n.asof, n.seq) for n in won] == [(date(2026, 10, 26), 1), (date(2026, 10, 27), 3)]
    assert in_effect([late2, first, late1], None) is late2
    prior = night(cc(State.ARMED, asof=date(2026, 1, 5)))
    assert in_effect([], prior) is prior  # no row in the window: the earlier row stood through it
    assert in_effect([], None) is None


# ---------------------------------------------------------------------------------------------------------
# the triggers on one company
# ---------------------------------------------------------------------------------------------------------


def nights_of(*cards: CallCard, tid: UUID = T1) -> dict[UUID, list[RecordedNight]]:
    return {tid: [night(c) for c in cards]}


def test_one_trigger_on_twelve_nights_is_one_row_dated_the_first_night():
    cards = [
        cc(asof=date(2026, 10, 10) + timedelta(days=i), triggers_fired=[trig()]) for i in range(12)
    ]
    got = log(nights_of(*cards))
    assert len(got.events) == 1
    e = got.events[0]
    assert (e.recorded_on, e.event_date, e.kind, e.label, e.live) == (
        date(2026, 10, 10),
        date(2026, 10, 20),
        "technical_breakout",
        "8-day breakout",
        True,
    )
    assert (got.nights_read, got.cards_read, got.reconstructed_nights) == (12, 1, 0)


def test_a_member_level_and_a_card_level_copy_merge_into_one_row():
    same = trig(kind=Kind.INSIDER, label="CEO buy")
    one = cc(
        asof=date(2026, 10, 25),
        armed_members=[mem(triggers=[same])],
        triggers_fired=[trig(kind=Kind.INSIDER, label="CEO buy")],
    )
    watch = cc(asof=date(2026, 10, 26), watch_members=[mem(triggers=[same])])
    got = log(nights_of(one, watch))
    assert [(e.recorded_on, e.role) for e in got.events] == [(date(2026, 10, 25), "entry_trigger")]


def test_only_this_company_is_read_and_a_different_label_or_fire_date_is_another_row():
    c = cc(
        triggers_fired=[
            trig(label="A", on=date(2026, 10, 20)),
            trig(label="A", on=date(2026, 10, 21)),  # another fire date
            trig(label="B", on=date(2026, 10, 20)),  # another label
            trig(OTHER, label="A", on=date(2026, 10, 20)),  # another company
        ]
    )
    got = log(nights_of(c))
    assert sorted((e.label, e.event_date) for e in got.events) == [
        ("A", date(2026, 10, 20)),
        ("A", date(2026, 10, 21)),
        ("B", date(2026, 10, 20)),
    ]


def test_a_night_stands_at_its_winning_row_so_a_trigger_on_a_superseded_row_is_not_a_row():
    d = date(2026, 10, 26)
    rows = {
        T1: [
            night(cc(asof=d, triggers_fired=[trig(label="superseded")]), seq=1),
            night(cc(asof=d, triggers_fired=[trig(label="stood")]), seq=2),
        ]
    }
    assert [e.label for e in log(rows).events] == ["stood"]


def test_the_fire_date_note_counts_the_days_the_record_trails_it():
    c1 = cc(asof=date(2026, 10, 23), triggers_fired=[trig(on=date(2026, 10, 20), label="late")])
    c2 = cc(asof=date(2026, 10, 24), triggers_fired=[trig(on=date(2026, 10, 24), label="same day")])
    c3 = cc(asof=date(2026, 10, 25), triggers_fired=[trig(on=None, label="undated")])
    got = {e.label: e for e in log(nights_of(c1, c2, c3)).events}
    assert got["late"].recorded_after_days == 3
    assert got["same day"].recorded_after_days is None
    assert got["undated"].recorded_after_days is None and got["undated"].event_date is None


def test_an_entry_trigger_names_the_key_its_kind_turns_and_a_risk_signal_none():
    c = cc(
        triggers_fired=[
            trig(kind=Kind.INSIDER, label="insider"),
            trig(kind=Kind.TECHNICAL_BREAKOUT, label="breakout"),
            trig(kind=Kind.CATALYST, label="revenue re-acceleration"),
            trig(kind=Kind.SQUEEZE, label="squeeze"),  # in neither key set
        ],
        risk_signals=[trig(kind=Kind.INSIDER_SELL, label="cluster of sales")],
    )
    got = {e.label: (e.role, e.turns) for e in log(nights_of(c)).events}
    assert got == {
        "insider": ("entry_trigger", "conviction"),
        "breakout": ("entry_trigger", "confirmation"),
        "revenue re-acceleration": ("entry_trigger", "conviction"),
        "squeeze": ("entry_trigger", None),
        "cluster of sales": ("risk_signal", None),
    }
    assert turns_key(Kind.LAGGARD) == "confirmation" and turns_key(Kind.ETF_LAUNCH) is None


def test_a_trigger_already_on_the_row_in_effect_before_the_window_is_not_a_row_and_is_counted_when_live():
    old = trig(label="old", on=date(2025, 9, 1))
    gone = trig(label="gone", on=date(2025, 9, 2))
    prior = {T1: night(cc(asof=date(2025, 10, 1), triggers_fired=[old, gone]))}
    inside = cc(asof=date(2026, 3, 2), triggers_fired=[old, trig(label="new", on=date(2026, 3, 1))])
    got = log(nights_of(inside), prior)
    assert [e.label for e in got.events] == ["new"]
    assert (
        got.before_window_live == 1
    )  # "old" is live and recorded before the window; "gone" lapsed


def test_with_no_row_in_the_window_the_prior_row_stands_and_its_triggers_are_live_but_not_listed():
    prior = {T1: night(cc(asof=date(2025, 10, 1), triggers_fired=[trig(label="old")]))}
    got = log({}, prior)
    assert got.events == () and got.before_window_live == 1 and got.nights_read == 0


def test_a_lapsed_trigger_is_not_live_and_one_that_comes_back_is_still_one_row_at_its_first_night():
    t = trig(label="back and forth")
    nights = nights_of(
        cc(asof=date(2026, 10, 20), triggers_fired=[t]),
        cc(asof=date(2026, 10, 21)),
        cc(asof=date(2026, 10, 22), triggers_fired=[t]),
        cc(asof=date(2026, 10, 23)),  # the card in effect no longer lists it
    )
    got = log(nights)
    assert [(e.recorded_on, e.live) for e in got.events] == [(date(2026, 10, 20), False)]


def test_a_reconstructed_night_marks_the_row_it_dated():
    rows = {T1: [night(cc(asof=date(2026, 10, 20), triggers_fired=[trig()]), reconstructed=True)]}
    got = log(rows)
    assert got.events[0].reconstructed is True and got.reconstructed_nights == 1


def test_the_same_trigger_on_two_cards_is_a_row_per_card_newest_recorded_night_first():
    rows = {
        T1: [night(cc(tid=T1, asof=date(2026, 10, 20), triggers_fired=[trig()]))],
        T2: [night(cc(tid=T2, asof=date(2026, 10, 26), triggers_fired=[trig()]))],
    }
    got = log(rows, cards=(T1, T2))
    assert [(e.thesis_name, e.recorded_on) for e in got.events] == [
        ("Card 2", date(2026, 10, 26)),
        ("Card 1", date(2026, 10, 20)),
    ]
    assert got.cards_read == 2


def test_a_source_links_through_the_issuer_cik_and_a_price_reference_has_no_link():
    t = trig(
        kind=Kind.INSIDER,
        sources=[
            Provenance(source="form4", ref=ACC, detail={"insiders": ["Jane Roe"], "usd": 12000.0}),
            Provenance(source="price", ref="price:TLA:2026-10-20", detail={"vol_ratio": None}),
            Provenance(source="doe", ref="DOE-AWARD-77"),
        ],
    )
    e = log(nights_of(cc(triggers_fired=[t]))).events[0]
    assert [(s.source, s.ref, s.url) for s in e.sources] == [
        ("form4", ACC, "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/"),
        ("price", "price:TLA:2026-10-20", None),
        ("doe", "DOE-AWARD-77", None),
    ]
    assert e.sources[0].detail == ("insiders: Jane Roe", "usd: 12000.0")
    assert e.sources[1].detail == ()  # a stated None asserts nothing
    assert log(nights_of(cc(triggers_fired=[t])), cik=None).events[0].sources[0].url is None


def test_refs_on_reads_every_place_a_trigger_can_sit_once_per_identity():
    t = trig(kind=Kind.INSIDER, label="x")
    c = cc(
        triggers_fired=[t, t],
        armed_members=[mem(triggers=[t])],
        watch_members=[mem(OTHER, triggers=[trig(OTHER)])],
        risk_signals=[trig(kind=Kind.BREAKDOWN, label="below the base")],
    )
    entry, risk = refs_on(c, SID)
    assert len(entry) == 1 and len(risk) == 1
    assert len(refs_on(c, OTHER)[0]) == 1


def test_no_cards_means_no_triggers_and_zero_counts():
    got = trigger_log(SID, [], {}, {}, window_start=ASOF - timedelta(days=365), cik=CIK)
    assert (got.events, got.cards_read, got.nights_read, got.before_window_live) == ((), 0, 0, 0)


def test_detail_lines_are_sorted_by_key_and_say_only_what_was_stated():
    assert detail_lines({"b": 2, "a": True, "c": None, "d": [1, 2], "e": {"x": 1}}) == (
        "a: yes",
        "b: 2",
        "d: 1, 2",
        "e: {'x': 1}",
    )


# ---------------------------------------------------------------------------------------------------------
# catalysts
# ---------------------------------------------------------------------------------------------------------


def _catalyst(label, when=None, kind=None, when_label=None):
    return Catalyst(id=uuid.uuid4(), label=label, kind=kind, when_date=when, when_label=when_label)


def test_card_catalysts_come_from_the_cards_the_company_counts_on_dated_first_passed_marked():
    a = card(
        "Card A",
        T1,
        [member()],
        catalysts=[
            _catalyst("Undated", when_label="~Q4"),
            _catalyst("Later", date(2026, 12, 1), "earnings"),
            _catalyst("Passed", date(2026, 10, 1)),
        ],
    )
    silent_only = card(
        "Card B", T2, [member(segment=CONTEXT_LABEL)], catalysts=[_catalyst("Never shown")]
    )
    elsewhere = card("Card C", T3, [member(OTHER, "OTH")], catalysts=[_catalyst("Not mine")])
    got = card_catalysts([silent_only, elsewhere, a], SID, ASOF)
    assert [(c.label, c.passed, c.when_label) for c in got] == [
        ("Passed", True, None),
        ("Later", False, None),
        ("Undated", False, "~Q4"),
    ]
    assert got[0].thesis_name == "Card A" and got[1].kind == "earnings"


# ---------------------------------------------------------------------------------------------------------
# the operator's decisions
# ---------------------------------------------------------------------------------------------------------


def dec(seq, action, d, *, sid=SID, tid=T1, voids=None, shares=None, price=None, reason=None):
    return DecisionRaw(
        id=uuid.UUID(int=0xD0 + seq),
        seq=seq,
        thesis_id=tid,
        security_id=sid,
        action=action,
        decision_date=d,
        shares=shares,
        price=price,
        reason=reason,
        voids=voids,
    )


def test_decisions_name_this_company_on_or_before_the_asof_newest_first_with_the_operators_fills():
    raw = [
        dec(1, "take", date(2026, 10, 1), shares=100, price=12.5, reason="starter"),
        dec(2, "close", date(2026, 10, 20), shares=100, price=14.0),
        dec(3, "take", date(2026, 10, 29)),  # dated after the as-of: not yet
        dec(4, "take", date(2026, 10, 2), sid=OTHER),  # another company
        dec(5, "pass", date(2026, 10, 3), sid=None),  # a card-level pass names no company
    ]
    got = decision_rows(raw, SID, ASOF, {T1: "Card A"})
    assert [(d.action, d.decision_date, d.shares, d.price, d.reason) for d in got] == [
        ("close", date(2026, 10, 20), 100, 14.0, None),
        ("take", date(2026, 10, 1), 100, 12.5, "starter"),
    ]
    assert got[0].thesis_name == "Card A"


def test_a_void_strikes_the_row_it_names_and_both_stay_on_the_page():
    take = dec(1, "take", date(2026, 10, 1))
    raw = [take, dec(2, "void", date(2026, 10, 2), sid=None, voids=take.id, reason="typo")]
    got = decision_rows(raw, SID, ASOF, {T1: "Card A"})
    assert [(d.action, d.voided, d.voids) for d in got] == [
        ("void", False, take.id),
        ("take", True, None),
    ]


def test_a_void_dated_after_the_asof_has_not_struck_anything_yet():
    take = dec(1, "take", date(2026, 10, 1))
    raw = [take, dec(2, "void", date(2026, 10, 29), sid=None, voids=take.id)]
    got = decision_rows(raw, SID, ASOF, {T1: "Card A"})
    assert [(d.action, d.voided) for d in got] == [("take", False)]


def test_a_void_of_another_companys_decision_is_not_listed():
    other = dec(1, "take", date(2026, 10, 1), sid=OTHER)
    raw = [other, dec(2, "void", date(2026, 10, 2), sid=None, voids=other.id)]
    assert decision_rows(raw, SID, ASOF, {T1: "Card A"}) == ()


# ---------------------------------------------------------------------------------------------------------
# filings
# ---------------------------------------------------------------------------------------------------------


def frow(kind, d, headline="h", *, routine=False, fold=None, form="8-K", **kw) -> FilingRow:
    return FilingRow(
        security_id=SID,
        ticker="TLA",
        kind=kind,
        form=form,
        date=d,
        headline=headline,
        routine=routine,
        fold=fold,
        **kw,
    )


def test_filings_group_by_day_newest_first_in_kind_order_and_the_catalyst_facts_leave_the_list():
    d1, d2 = date(2026, 10, 27), date(2026, 10, 20)
    rows = [
        frow("revenue", d2, "rev"),
        frow("filing", d1, "S-1", form="S-1"),
        frow("8k", d1, "8-K b"),
        frow("8k", d1, "8-K a"),
        frow("catalyst", d1, "catalyst fact", form="catalyst"),
        frow("form4", d1, "F4", form="4"),
    ]
    section_, facts = filings_section(rows)
    assert [d.date for d in section_.days] == [d1, d2]
    assert [r.headline for r in section_.days[0].rows] == ["8-K a", "8-K b", "F4", "S-1"]
    assert [r.headline for r in facts] == ["catalyst fact"]
    assert section_.rows == 5 and section_.routine == 0
    assert [(c.kind, c.rows) for c in section_.by_kind] == [
        ("8k", 2),
        ("form4", 1),
        ("revenue", 1),
        ("filing", 1),
    ]
    assert "catalyst" in KIND_ORDER  # a kind of the shared reader, listed under Catalysts here


def test_the_routine_cap_lists_the_newest_per_fold_group_and_counts_the_rest():
    base = date(2026, 10, 27)
    prospectus = [
        frow("filing", base - timedelta(days=i), f"424B {i}", routine=True, fold="prospectus")
        for i in range(5)
    ]
    f4 = [
        frow("form4", base - timedelta(days=i), f"F4 {i}", routine=True, fold="form4")
        for i in range(2)
    ]
    loud = [frow("8k", base - timedelta(days=30), "old 8-K")]
    section_, _ = filings_section([*prospectus, *f4, *loud], cap=3)
    headlines = [r.headline for d in section_.days for r in d.rows]
    assert "424B 0" in headlines and "424B 2" in headlines and "424B 3" not in headlines
    assert "old 8-K" in headlines  # a row that is not routine is never cut
    assert section_.routine == 7 and section_.rows == 8  # counts cover every row, listed or not
    assert [(n.fold, n.rows, n.older_than) for n in section_.not_listed] == [
        ("prospectus", 2, base - timedelta(days=2))
    ]
    assert ROUTINE_CAP == 200


def test_no_filings_is_an_empty_section():
    section_, facts = filings_section([])
    assert (section_.days, section_.rows, section_.by_kind, section_.not_listed, facts) == (
        (),
        0,
        (),
        (),
        (),
    )


# ---------------------------------------------------------------------------------------------------------
# the tape (read off the REAL display members' computes)
# ---------------------------------------------------------------------------------------------------------


def sessions(n: int, end: date = ASOF) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def bars_for(n: int = 320, base: float = 50.0, drift: float = 0.05, end: date = ASOF) -> list[dict]:
    return [
        {
            "d": d,
            "close": round(base + drift * i + 3 * math.sin(i / 9), 4),
            "high": round(base + drift * i + 3 * math.sin(i / 9) + 0.5, 4),
            "low": round(base + drift * i + 3 * math.sin(i / 9) - 0.5, 4),
            "volume": 1_000_000.0 + 1000.0 * (i % 7),
        }
        for i, d in enumerate(sessions(n, end))
    ]


BARS = bars_for()
SPY = bars_for(base=400.0, drift=0.2)
IWM = bars_for(base=200.0, drift=0.1)


def insider_rows() -> list[dict]:
    def row(days_ago: int, code: str, usd: float, who: str) -> dict:
        return {
            "valid_from": ASOF - timedelta(days=days_ago),
            "txn_code": code,
            "usd": usd,
            "insider_name": who,
        }

    return [
        row(3, "P", 40_000.0, "Jane Roe"),
        row(20, "P", 25_000.0, "John Roe"),
        row(60, "S", 10_000.0, "Ann Poe"),
    ]


def fund_samples() -> list[dict]:
    return [
        {"d": ASOF - timedelta(days=days), "shares_out": shares, "source": "fund-site"}
        for days, shares in (
            (40, 1_000_000.0),
            (20, 1_100_000.0),
            (10, 1_150_000.0),
            (3, 1_200_000.0),
        )
    ]


def real_signals(*, etf: bool = False, bars=BARS) -> dict[str, DisplaySignal | None]:
    sigs: dict[str, DisplaySignal | None] = {
        SMA: sma.compute(bars, ASOF),
        RETURNS: trailing_returns.compute(bars, ASOF),
        RANGE: range52w.compute(bars, ASOF),
        VOLUME: volume_regime.compute(bars, ASOF),
        RVOL: rvol.compute(bars, ASOF),
        RS: relative_strength.compute(bars, {"SPY": SPY, "IWM": IWM}, ASOF),
        VCP: vcp.compute(bars, ASOF),
        INSIDER: insider_flow.compute(insider_rows(), ASOF),
        PATH: price_path.compute(bars, ASOF),
    }
    if etf:
        sigs[ETF_FLOW] = etf_flow.compute(fund_samples(), bars, ASOF)
    return sigs


def asked_for(etf: bool) -> list[str]:
    return [m.name for m in registered_display_members() if etf or m.name != ETF_FLOW]


def test_the_member_names_mirror_the_registry_and_every_member_has_a_label_and_a_gap_note():
    registered = {m.name for m in registered_display_members()}
    assert registered == {SMA, RETURNS, RANGE, VOLUME, RVOL, RS, VCP, INSIDER, ETF_FLOW, PATH}
    assert registered <= set(MEMBER_LABEL) and registered <= set(NO_READING)
    assert (sma.MEMBER_NAME, etf_flow.MEMBER_NAME, price_path.MEMBER_NAME) == (SMA, ETF_FLOW, PATH)


def test_every_figure_spec_is_a_metric_its_member_really_emits():
    """A renamed metric key must fail HERE, not drop a figure off the page."""
    tape = tape_of(real_signals(etf=True), asked_for(True), bars=BARS, iwm_bars=IWM)
    have = {(f.member, f.key) for f in tape.figures}
    assert {(m, k) for m, k, *_ in _SPECS} == have
    assert tape.members_read == 10 and tape.members_without == 0 and tape.figures_blank == 0


def test_the_grid_carries_the_members_own_figures_with_the_tone_on_signed_ones_only():
    tape = tape_of(real_signals(), asked_for(False), bars=BARS, iwm_bars=IWM)
    returns = {m.key: m.value for m in real_signals()[RETURNS].metrics}
    for k in ("ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y"):
        f = figure(tape, k)
        assert f is not None and f.value == returns[k] and f.group == "returns"
        assert f.tone == ("pos" if f.value > 0 else "neg" if f.value < 0 else None)
    assert [
        figure(tape, k).label for k in ("ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y")
    ] == [
        "1D",
        "7D",
        "30D",
        "90D",
        "1Y",
    ]
    # the range figures are not signed returns: no tone, and they carry their level and the day it printed
    off = figure(tape, "pct_off_52w_high")
    assert off.tone is None and off.level is not None and isinstance(off.on, date)
    assert figure(tape, "pct_above_52w_low").on is not None
    assert [figure(tape, k).label for k in ("rvol", "rvol20")] == ["RVOL 8", "RVOL 20"]
    assert figure(tape, "adv_usd_20d").label == "$vol 20d"
    assert figure(tape, "close").value == BARS[-1]["close"]
    assert figure(tape, "net_usd").value == 55_000.0 and figure(tape, "net_usd").tone == "pos"
    assert (
        figure(tape, "buy_count").value == 2.0 and figure(tape, "distinct_buyers_30d").value == 2.0
    )
    # the ETF flow member is not read for an equity
    assert figure(tape, "flow_1w_usd") is None and ETF_FLOW not in {g.member for g in tape.gaps}


def test_the_etf_flow_figures_appear_for_an_etf():
    tape = tape_of(real_signals(etf=True), asked_for(True), bars=BARS, iwm_bars=IWM)
    f = figure(tape, "flow_1m_usd")
    assert f is not None and f.group == "flow" and f.tone == "pos" and f.value is not None
    assert figure(tape, "flow_1w_pct_of_shares").unit == "pct"


def test_a_member_with_no_reading_is_listed_with_its_note_and_counted():
    sigs = real_signals()
    sigs[INSIDER] = None  # no Form 4 ingested
    sigs[RS] = None
    tape = tape_of(sigs, asked_for(False), bars=BARS, iwm_bars=IWM)
    assert [(g.member, g.label, g.note) for g in tape.gaps] == [
        (RS, "Relative strength", NO_READING[RS]),
        (INSIDER, "Insider flow", "no Form 4 ingested for the name"),
    ]
    assert (tape.members_read, tape.members_without) == (9, 2)
    assert figure(tape, "net_usd") is None and figure(tape, "rs_spy") is None


def test_a_metric_the_member_could_not_compute_keeps_its_note_and_is_counted_blank():
    thin = bars_for(40)
    tape = tape_of(real_signals(bars=thin), asked_for(False), bars=thin, iwm_bars=IWM)
    one_year = figure(tape, "ret_1y")
    assert one_year.value is None and one_year.note.startswith("n/a: 40/253 bars")
    assert figure(tape, "pct_vs_slow").value is None and figure(tape, "pct_vs_slow").note
    assert tape.figures_blank >= 2 and tape.members_without >= 1  # the base needs more bars


def test_the_leading_mark_and_a_coiling_base_show_only_when_the_members_say_so():
    sigs0 = real_signals()
    quiet = tape_of(sigs0, asked_for(False), bars=BARS, iwm_bars=IWM)
    said = tuple(
        b for b in ("SPY", "IWM") if any(e.key == f"rs_high_{b.lower()}" for e in sigs0[RS].events)
    )
    assert quiet.leading == said  # the mark is the member's own events, never the page's judgment
    assert quiet.coiling == (
        sigs0[VCP].headline is not None and sigs0[VCP].headline.key == "coiling"
    )
    sigs = real_signals()
    sigs[RS] = DisplaySignal(
        kind=RS,
        label="Relative strength",
        headline=DisplayHeadline(key="leading", label="Leading"),
        events=[
            DisplayEvent(
                key="rs_high_iwm", label="RS vs IWM at a 13-week high", date=ASOF, direction="up"
            )
        ],
        basis=DisplayBasis(source="fact_price_eod"),
    )
    sigs[VCP] = DisplaySignal(
        kind=VCP,
        label="Base",
        headline=DisplayHeadline(
            key="coiling", label="Base coiling", detail="range narrowed to 40%"
        ),
        basis=DisplayBasis(source="fact_price_eod"),
    )
    loud = tape_of(sigs, asked_for(False), bars=BARS, iwm_bars=IWM)
    assert loud.leading == ("IWM",)
    assert (loud.coiling, loud.coiling_detail) == (True, "range narrowed to 40%")


def test_the_sparkline_puts_the_company_and_iwm_on_the_same_sessions_indexed_to_100():
    path = price_path.compute(BARS, ASOF)
    spark = sparkline_of(path, BARS, IWM)
    assert spark is not None and spark.bars == 90 and len(spark.dates) == 90
    assert spark.dates[-1] == ASOF and spark.dates == tuple(b["d"] for b in BARS[-90:])
    assert (spark.stock[0], spark.iwm[0]) == (100.0, 100.0)
    assert spark.iwm_bars == 90
    # indexed, not raw: the last point is the company's own return over the 90 sessions
    expect = round(100.0 * BARS[-1]["close"] / BARS[-90]["close"], 2)
    assert spark.stock[-1] == expect


def test_an_iwm_session_with_no_bar_breaks_the_line_and_no_iwm_tape_leaves_it_empty():
    path = price_path.compute(BARS, ASOF)
    hole = [b for b in IWM if b["d"] != BARS[-5]["d"]]
    spark = sparkline_of(path, BARS, hole)
    assert spark.iwm[-5] is None and spark.iwm[-4] is not None and spark.iwm_bars == 89
    none = sparkline_of(path, BARS, [])
    assert none.iwm == (None,) * 90 and none.iwm_bars == 0 and none.stock[0] == 100.0


def test_a_thin_tape_draws_a_shorter_sparkline_and_says_so():
    thin = bars_for(40)
    spark = sparkline_of(price_path.compute(thin, ASOF), thin, IWM)
    assert spark.bars == 40 and len(spark.stock) == 40 and spark.note == "thin: 40/90 bars"
    assert sparkline_of(None, BARS, IWM) is None


# ---------------------------------------------------------------------------------------------------------
# identity and the cap
# ---------------------------------------------------------------------------------------------------------


def test_the_cap_is_the_peer_tables_rule_and_a_flagged_price_withholds_it():
    shares = {
        "shares": 10_000_000.0,
        "valid_from": date(2026, 9, 1),
        "ads_ratio": None,
        "ads_ratio_status": None,
    }
    close = (date(2026, 10, 28), 12.5)
    cap = cap_of(None, close, shares, ())
    assert (cap.value, cap.tier, cap.gap) == (125_000_000.0, "micro", None)
    assert (cap.close, cap.shares, cap.close_date) == (12.5, 10_000_000.0, date(2026, 10, 28))
    flagged = cap_of("mixed-scale price history", close, shares, ())
    assert (flagged.value, flagged.tier, flagged.gap, flagged.note) == (
        None,
        None,
        "flagged",
        "mixed-scale price history",
    )
    assert cap_of(None, None, shares, ()).gap == "no_price"
    assert cap_of(None, close, None, ()).gap == "no_shares"


def test_the_identity_carries_the_edgar_link_the_flag_and_the_price_symbol_only_when_it_differs():
    inputs = IdentityInputs(
        security_id=SID,
        ticker="TLA",
        name="Tape A Corp",
        cik=CIK,
        exchange="NASDAQ",
        business_type="biotech_pharma",
        supersector="healthcare",
        royalty=False,
        origin="US",
        instrument_kind="equity",
        price_symbol="TLAD",
        former_tickers=("OLDA",),
    )
    tape = tape_of(real_signals(), asked_for(False), bars=BARS, iwm_bars=IWM)
    ident = identity_of(inputs, cap_of(None, None, None, ()), tape, "mixed-scale price history")
    assert (
        ident.edgar_url
        == "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000876543"
    )
    assert ident.price_symbol == "TLAD" and ident.former_tickers == ("OLDA",)
    assert ident.flag == "mixed-scale price history"
    assert ident.adv_usd_20d == figure(tape, "adv_usd_20d").value
    same = identity_of(
        IdentityInputs(**{**inputs.__dict__, "price_symbol": "TLA"}), ident.cap, tape, None
    )
    assert same.price_symbol is None and same.flag is None
    assert edgar_company_url(None) is None and edgar_company_url("") is None


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


def sample_lookup():
    a = card(
        "Card A",
        T1,
        [member(), member(segment="Rest")],
        catalysts=[_catalyst("Vote", date(2026, 12, 1))],
    )
    effect = {
        T1: night(cc(State.ARMED, armed_members=[mem(verdict=Verdict.CORE_ENTRY, confidence=0.5)]))
    }
    inputs = IdentityInputs(
        security_id=SID,
        ticker="TLA",
        name="Tape A Corp",
        cik=CIK,
        exchange=None,
        business_type=None,
        supersector=None,
        royalty=False,
        origin=None,
        instrument_kind="equity",
        price_symbol=None,
    )
    tape = tape_of(real_signals(), asked_for(False), bars=BARS, iwm_bars=IWM)
    return lookup(
        identity_of(inputs, cap_of(None, None, None, ()), tape, None),
        cards_section(SID, [a], effect, [], live_cards=2, not_yet_created=0, roster_fallback=0),
        log(nights_of(cc(triggers_fired=[trig()]))),
        tape,
        [frow("8k", date(2026, 10, 27), "an 8-K"), frow("catalyst", date(2026, 10, 1), "a fact")],
        card_catalysts([a], SID, ASOF),
        decision_rows([dec(1, "take", date(2026, 10, 1))], SID, ASOF, {T1: "Card A"}),
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}

    def fake_read(conn, security_id, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (security_id, asof, known_at, clock)
        return sample_lookup()

    known = {SID}
    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_lookup", fake_read)
    monkeypatch.setattr(
        router_mod.master, "get", lambda conn, sid, **kw: object() if sid in known else None
    )
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_one_company_off_one_cached_read_on_the_requests_view(stub_client):
    client, seen = stub_client
    url = f"/dashboards/ticker-lookup/{SID}"
    first = client.get(url, params={"asof": "2026-10-28"}).json()
    assert (first["cache"], first["clock"], first["asof"]) == ("computed", "public", "2026-10-28")
    security_id, asof, known_at, clock = seen["view"]
    assert (security_id, asof, clock) == (SID, ASOF, "public") and known_at is not None
    again = client.get(url, params={"asof": "2026-10-28"}).json()
    assert again["cache"] == "hit" and seen["reads"] == 1
    # another as-of is another read; the live as-of reads on the record clock with no cutoff
    client.get(url, params={"asof": market_today().isoformat()})
    assert seen["reads"] == 2 and seen["view"][2:] == (None, "record")


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(f"/dashboards/ticker-lookup/{SID}", params={"asof": "2026-10-28"}).json()
    assert list(body) == [
        "asof",
        "clock",
        "identity",
        "cards",
        "triggers",
        "tape",
        "filings",
        "catalyst_facts",
        "card_catalysts",
        "decisions",
        "decisions_voided",
        "cache",
        "compute_ms",
    ]
    assert body["identity"]["ticker"] == "TLA" and body["identity"]["cap"]["gap"] == "no_price"
    row = body["cards"]["rows"][0]
    assert row["thesis_name"] == "Card A" and [k["link"] for k in row["links"]] == ["Core", "Rest"]
    assert row["place"]["kind"] == "armed" and row["place"]["verdict"] == "core_entry"
    assert body["triggers"]["events"][0]["label"] == "8-day breakout"
    assert body["triggers"]["events"][0]["turns"] == "confirmation"
    assert body["tape"]["sparkline"]["bars"] == 90 and len(body["tape"]["figures"]) > 15
    assert [r["headline"] for r in body["filings"]["days"][0]["rows"]] == ["an 8-K"]
    assert [r["headline"] for r in body["catalyst_facts"]] == ["a fact"]
    assert body["card_catalysts"][0]["label"] == "Vote"
    assert body["decisions"][0]["action"] == "take" and body["decisions_voided"] == 0


def test_the_router_404s_an_unknown_company_and_requires_an_asof(stub_client):
    client, seen = stub_client
    assert (
        client.get(
            f"/dashboards/ticker-lookup/{uuid.uuid4()}", params={"asof": "2026-10-28"}
        ).status_code
        == 404
    )
    assert client.get(f"/dashboards/ticker-lookup/{SID}").status_code == 422
    assert (
        client.get("/dashboards/ticker-lookup/not-an-id", params={"asof": "2026-10-28"}).status_code
        == 422
    )
    assert seen["reads"] == 0


def test_resolve_needs_a_ticker(stub_client):
    client, _ = stub_client
    assert client.get("/dashboards/ticker-lookup/resolve").status_code == 422
    assert client.get("/dashboards/ticker-lookup/resolve", params={"t": ""}).status_code == 422


# ---------------------------------------------------------------------------------------------------------
# DB-backed (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a past as-of (a Friday)
LATER = date(2026, 6, 12)  # a later past as-of, after every "late" fact below
INGESTED = datetime(2026, 9, 1, tzinfo=UTC)  # our own ingest instant, after both as-ofs
BASE = "/dashboards/ticker-lookup"
EARLY_8K, LATE_8K = "0000876543-26-000101", "0000876543-26-000102"


def _security(
    db, ticker: str, cik: str | None = None, name=None, sector=None, kind="equity"
) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, name, sector, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, name, sector, "2026-01-01"),
        )
    return sid


def _price(db, sid: UUID, d: date, close: float) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "close": close,
            "volume": 1000.0,
            "valid_from": d,
            "recorded_at": datetime.combine(d, time.min, tzinfo=UTC),  # public at its own session
        },
    )


def _eight_k(db, sid: UUID, accession: str, filed: date) -> None:
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "form": "8-K",
            "items": ["5.02", "9.01"],
            "accession": accession,
            "filed": filed,
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/876543/{accession.replace('-', '')}/",
            "valid_from": filed,
            "recorded_at": INGESTED,
        },
    )


def _promote(db, name: str, members: list[BasketMember], taken: datetime) -> Thesis:
    """A card that existed on both past as-ofs, its (latest) roster snapshot backdated to ``taken``."""
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=members,
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # fixture backdates, not app writes
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE id = ("
            "  SELECT id FROM basket_snapshot WHERE thesis_id = %s ORDER BY taken_at DESC, id DESC LIMIT 1)",
            (taken, thesis.id),
        )
    db.commit()
    return thesis


def _record(db, thesis_id: UUID, asof: date, **kw) -> None:
    calls_repo.append(db, cc(tid=thesis_id, asof=asof, **kw), DEFAULT_TENANT_ID)


def _decide(db, thesis_id, sid, action, d, recorded: datetime, **kw) -> UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_decision (tenant_id, thesis_id, security_id, action, decision_date, shares, "
            "price, reason, voids, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                DEFAULT_TENANT_ID,
                thesis_id,
                sid,
                action,
                d,
                kw.get("shares"),
                kw.get("price"),
                kw.get("reason"),
                kw.get("voids"),
                recorded,
            ),
        )
        return cur.fetchone()["id"]


def _get(db, path: str, **params):
    app.dependency_overrides[get_conn] = lambda: db
    try:
        return TestClient(app).get(f"{BASE}/{path}", params=params)
    finally:
        app.dependency_overrides.clear()


def test_db_resolve_takes_the_exact_ticker_over_a_substring_match_and_a_former_one_only_on_request(
    db,
):
    abc = _security(db, "ABC", name="Abc Corp")
    _security(db, "ABCD", name="Abcd Holdings")
    _security(db, "XYZ", name="ABC Holdings Group")  # a NAME that contains the typed text
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_ticker_history (tenant_id, security_id, old_ticker, new_ticker, source) "
            "VALUES (%s, %s, 'OLDT', 'ABC', 'sec:company_tickers_exchange')",
            (DEFAULT_TENANT_ID, abc),
        )
    db.commit()

    # the suggestion net lists all three; Enter's exact resolve picks the ticker
    assert {s.ticker for s in master.search(db, "ABC", include_former=True)} == {
        "ABC",
        "ABCD",
        "XYZ",
    }
    for typed in ("ABC", "abc", "  Abc "):
        r = _get(db, "resolve", t=typed)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "security_id": str(abc),
            "ticker": "ABC",
            "name": "Abc Corp",
            "matched": "ticker",
        }
    # a fragment is no exact ticker; neither is a former one unless asked for
    assert _get(db, "resolve", t="AB").status_code == 404
    assert _get(db, "resolve", t="OLDT").status_code == 404
    former = _get(db, "resolve", t="oldt", former=True)
    assert former.status_code == 200 and former.json()["matched"] == "former"
    assert former.json()["security_id"] == str(abc) and former.json()["ticker"] == "ABC"
    assert _get(db, "resolve", t="NOPE", former=True).status_code == 404


def _seed_page(db):
    """TLA on two cards (one from the start, one added June 10), a tape, a cap, an early and a late 8-K, calls,
    catalysts, decisions; TLB on no card. Returns the ids."""
    tla = _security(
        db, "TLA", "0000876543", name="Tape A Corp", sector="Pharmaceutical Preparations"
    )
    tlb = _security(db, "TLB", "0000876544", name="Tape B Corp")
    other = _security(db, "OTH", "0000876545", name="Other Corp")
    days = [
        d
        for i in range(60)
        if (d := date(2026, 4, 20) + timedelta(days=i)) <= LATER + timedelta(days=10)
        and d.weekday() < 5
    ]
    closes = {d: 10.0 + 0.1 * i for i, d in enumerate(days)}
    for d in days:
        _price(db, tla, d, closes[d])
        _price(db, tlb, d, 20.0)
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": tla,
            "shares": 10_000_000,
            "source": "10-q",
            "source_ref": "tla-10q-1",
            "valid_from": date(2026, 5, 10),
            "recorded_at": datetime(2026, 5, 12, tzinfo=UTC),
        },
    )
    _eight_k(db, tla, EARLY_8K, date(2026, 6, 3))
    _eight_k(db, tla, LATE_8K, date(2026, 6, 10))
    _eight_k(db, tlb, "0000876544-26-000001", date(2026, 6, 3))

    first = _promote(
        db,
        "Lookup card one",
        [member(tla, "TLA"), member(other, "OTH")],
        datetime(2026, 6, 2, 12, tzinfo=UTC),
    )
    second = _promote(
        db, "Lookup card two", [member(other, "OTH")], datetime(2026, 6, 2, 12, tzinfo=UTC)
    )
    # card two takes TLA on in a second promote, snapshotted June 10: after PAST, before LATER
    thesis_repo.upsert(
        db,
        second.model_copy(update={"basket": [member(other, "OTH"), member(tla, "TLA")]}),
    )
    with db.cursor() as cur:
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE id = ("
            "  SELECT id FROM basket_snapshot WHERE thesis_id = %s ORDER BY taken_at DESC, id DESC LIMIT 1)",
            (datetime(2026, 6, 10, 12, tzinfo=UTC), second.id),
        )
    db.commit()

    # card one's recorded calls: the early trigger on June 3, the late one on June 10, a later night (June 20,
    # after both as-ofs) in which the early one has lapsed
    early = trig(tla, "CEO bought", Kind.INSIDER, date(2026, 6, 2))
    late = trig(tla, "8-day breakout", Kind.TECHNICAL_BREAKOUT, date(2026, 6, 9))
    _record(db, first.id, date(2026, 6, 3), triggers_fired=[early])
    _record(db, first.id, date(2026, 6, 10), triggers_fired=[early, late])
    _record(db, first.id, date(2026, 6, 20), triggers_fired=[late])
    thesis_repo.set_catalysts(
        db,
        first.id,
        [Catalyst(id=uuid.uuid4(), label="Trial readout", when_date=date(2026, 7, 1))],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()

    # the operator's log: a take on June 3 (logged then), a close on June 10, a void of the take on June 11, and a
    # pass dated June 1 but only LOGGED June 20 (after both as-ofs)
    take = _decide(
        db,
        first.id,
        tla,
        "take",
        date(2026, 6, 3),
        datetime(2026, 6, 3, 20, tzinfo=UTC),
        shares=100,
        price=10.5,
    )
    _decide(
        db,
        first.id,
        tla,
        "close",
        date(2026, 6, 10),
        datetime(2026, 6, 10, 20, tzinfo=UTC),
        shares=100,
        price=11.0,
    )
    _decide(
        db,
        first.id,
        None,
        "void",
        date(2026, 6, 11),
        datetime(2026, 6, 11, 20, tzinfo=UTC),
        voids=take,
    )
    _decide(
        db,
        first.id,
        tla,
        "pass",
        date(2026, 6, 1),
        datetime(2026, 6, 20, 20, tzinfo=UTC),
        reason="logged late",
    )
    db.commit()
    return {
        "tla": tla,
        "tlb": tlb,
        "other": other,
        "first": first.id,
        "second": second.id,
        "closes": closes,
    }


def test_db_a_page_at_a_past_asof_holds_only_what_was_public_then(db, pinned_today):
    ids = _seed_page(db)
    CACHE.clear()
    try:
        past = _get(db, str(ids["tla"]), asof=PAST.isoformat())
        later = _get(db, str(ids["tla"]), asof=LATER.isoformat())
        live = _get(db, str(ids["tla"]), asof=pinned_today.isoformat())
        past, later, live = past.json(), later.json(), live.json()
    finally:
        CACHE.clear()

    assert (past["clock"], later["clock"], live["clock"]) == ("public", "public", "record")

    # a basket row added after the as-of is not on the page: card two holds TLA only from its June 10 roster
    assert [r["thesis_name"] for r in past["cards"]["rows"]] == ["Lookup card one"]
    assert [r["thesis_name"] for r in later["cards"]["rows"]] == [
        "Lookup card one",
        "Lookup card two",
    ]
    assert past["cards"]["counted"] == 2 and past["cards"]["roster_fallback"] == 0

    # a filing filed after the as-of is not listed
    def eight_ks(body):
        return sorted(
            r["accession"] for d in body["filings"]["days"] for r in d["rows"] if r["kind"] == "8k"
        )

    assert eight_ks(past) == [EARLY_8K]
    assert eight_ks(later) == sorted([EARLY_8K, LATE_8K])
    assert all(d["date"] <= PAST.isoformat() for d in past["filings"]["days"])

    # a call recorded after the as-of is not read; "live" is judged on the card in effect at the as-of
    def triggers(body):
        return sorted((e["label"], e["recorded_on"], e["live"]) for e in body["triggers"]["events"])

    assert triggers(past) == [("CEO bought", "2026-06-03", True)]
    assert triggers(later) == [
        ("8-day breakout", "2026-06-10", True),
        ("CEO bought", "2026-06-03", True),
    ]
    assert triggers(live) == [
        ("8-day breakout", "2026-06-10", True),
        ("CEO bought", "2026-06-03", False),  # the June 20 night dropped it
    ]
    row = past["cards"]["rows"][0]
    assert (row["state"], row["recorded_on"], row["place"]["kind"]) == (
        "warming",
        "2026-06-03",
        "triggers",
    )
    assert row["place"]["turns"] == ["conviction"]

    # a decision logged after the as-of (or dated after it) is not on the page; a void strikes only once logged
    def decisions(body):
        return [(d["action"], d["decision_date"], d["voided"]) for d in body["decisions"]]

    assert decisions(past) == [("take", "2026-06-03", False)]
    assert decisions(later) == [
        ("void", "2026-06-11", False),  # dated June 11: after PAST, before LATER's June 12
        ("close", "2026-06-10", False),
        ("take", "2026-06-03", True),
    ]
    assert [d[0] for d in decisions(live)] == ["void", "close", "take", "pass"]
    assert past["decisions"][0]["shares"] == 100 and past["decisions"][0]["price"] == 10.5

    # the catalysts of the cards it counts on; the tape and cap end at the as-of
    assert [c["label"] for c in past["card_catalysts"]] == ["Trial readout"]
    assert past["tape"]["window_end"] == "2026-06-05"
    last_close = ids["closes"][PAST]
    assert past["identity"]["cap"]["close"] == pytest.approx(last_close)
    assert past["identity"]["cap"]["value"] == round(10_000_000 * last_close)
    assert past["identity"]["cap"]["tier"] == "micro" and past["identity"]["cik"] == "0000876543"
    assert past["identity"]["edgar_url"].endswith("CIK=0000876543")
    assert past["tape"]["sparkline"]["dates"][-1] == "2026-06-05"


def test_db_a_company_on_no_counted_card_still_shows_identity_tape_and_filings(db):
    ids = _seed_page(db)
    CACHE.clear()
    try:
        body = _get(db, str(ids["tlb"]), asof=LATER.isoformat()).json()
    finally:
        CACHE.clear()
    assert body["cards"]["rows"] == [] and body["cards"]["counted"] == 2
    assert (
        body["triggers"]["events"] == []
        and body["decisions"] == []
        and body["card_catalysts"] == []
    )
    assert body["identity"]["ticker"] == "TLB" and body["identity"]["cap"]["gap"] == "no_shares"
    assert len(body["tape"]["figures"]) > 5 and body["tape"]["sparkline"] is not None
    assert [r["accession"] for d in body["filings"]["days"] for r in d["rows"]] == [
        "0000876544-26-000001"
    ]


def test_db_the_archived_and_excluded_folds_and_the_silent_tag(db):
    ids = _seed_page(db)
    old = _promote(
        db, "Lookup archived", [member(ids["tla"], "TLA")], datetime(2026, 6, 2, 12, tzinfo=UTC)
    )
    thesis_repo.set_archived(db, old.id, True)
    third = _promote(
        db,
        "Lookup silent",
        [member(ids["tla"], "TLA", segment=CONTEXT_LABEL, context_role=ContextRole.PAYER)],
        datetime(2026, 6, 2, 12, tzinfo=UTC),
    )
    thesis_repo.set_exclusions(
        db,
        ids["second"],
        [ExcludedName(security_id=ids["tlb"], ticker="TLB", reason="a rival")],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    CACHE.clear()
    try:
        tla = _get(db, str(ids["tla"]), asof=LATER.isoformat()).json()
        tlb = _get(db, str(ids["tlb"]), asof=LATER.isoformat()).json()
    finally:
        CACHE.clear()
    assert [a["thesis_name"] for a in tla["cards"]["archived"]] == ["Lookup archived"]
    assert "Lookup archived" not in [r["thesis_name"] for r in tla["cards"]["rows"]]
    silent = next(r for r in tla["cards"]["rows"] if r["thesis_id"] == str(third.id))
    assert (silent["silent"], silent["context_role"], silent["place"]["kind"]) == (
        CONTEXT_LABEL,
        "payer",
        "not_counted",
    )
    assert [(x["thesis_name"], x["reason"]) for x in tlb["cards"]["excluded"]] == [
        ("Lookup card two", "a rival")
    ]


def test_db_every_page_read_is_on_the_requests_view_and_cached_once_per_company_and_clock(
    db, pinned_today
):
    ids = _seed_page(db)
    CACHE.clear()
    try:
        first = _get(db, str(ids["tla"]), asof=PAST.isoformat()).json()
        again = _get(db, str(ids["tla"]), asof=PAST.isoformat()).json()
        other = _get(db, str(ids["tlb"]), asof=PAST.isoformat()).json()
        missing = _get(db, str(uuid.uuid4()), asof=PAST.isoformat())
    finally:
        CACHE.clear()
    assert (first["cache"], again["cache"], other["cache"]) == ("computed", "hit", "computed")
    assert missing.status_code == 404
    assert BENCH_LABEL == "Bench"  # the silent labels are the domain's own
