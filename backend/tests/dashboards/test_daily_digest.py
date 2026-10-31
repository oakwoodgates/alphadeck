"""Daily Digest (``docs/dashboards/daily-digest.md``): the pure sections on hand-built rows (the night pair, What
Changed's lines regrouped by card in the Armed-first order with the page's one loud rule, the Managing cards'
positions with their two return bases, the Calendar's this-week bucket with the "today" mark, Insider Buying's rows cut
to the filing dates in (P, N], the tape's five largest rises and falls, the forming counts and every empty section),
the router off one cached read, and DB-backed reads through the real ``calls`` log, Form 4 tape, price tape and
decision log: a card armed after the as-of is not in section 1 then, a Form 4 filed after the as-of is not in section 4
then, a fill logged after the as-of is not a position then, a Monday reads Friday against Thursday and a single recorded
night has no earlier one.

Every card, company and ticker below is a neutral fixture name.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import daily_digest as router_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.daily_digest import (
    TOP_N,
    DailyDigest,
    DigestInputs,
    HeldPosition,
    InEffect,
    NameTape,
    daily_digest,
    hold_clock,
    last_night,
    last_session,
    night_pair,
    since_fill,
    split_since_fill,
    tone_of,
)
from dashboards.filings import Who, rows_from_insider_txns, source_url
from dashboards.hot_board import LogRow
from dashboards.trigger_log import CardNights, Night, Scope, Series
from dashboards.what_changed import ChangeItem
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Kind, State, Verdict
from domain.signal import Provenance
from domain.splits import ListedSplit
from domain.thesis import BasketMember, Catalyst, KillCriterion, Segment, Thesis
from repositories import calls_repo, thesis_repo

UTC = timezone.utc
NIGHT = date(2026, 9, 17)  # N: a Thursday
PREV = date(2026, 9, 16)  # P: the Wednesday before
T1, T2, T3, T4, T5 = (uuid.UUID(int=n) for n in (0x101, 0x102, 0x103, 0x104, 0x105))
A, B, C, H = (uuid.UUID(int=n) for n in (0xA1, 0xB2, 0xC3, 0x4A))
TICKERS = {A: "AAA", B: "BBB", C: "CCC", H: "HHH"}
CIK = "0000876543"


def d(day: int, month: int = 9) -> date:
    return date(2026, month, day)


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def key(label: str, turned: bool = False) -> KeyState:
    return KeyState(turned=turned, label=label)


def trig(
    sid: UUID,
    label: str = "8-day breakout",
    kind: Kind = Kind.TECHNICAL_BREAKOUT,
    on: date | None = d(10),
    **kw,
) -> TriggerRef:
    return TriggerRef(label=label, kind=kind, security_id=sid, event_date=on, **kw)


def mem(sid: UUID, **kw) -> MemberCall:
    return MemberCall(security_id=sid, **kw)


def cc(
    state: State = State.WARMING,
    *,
    tid: UUID = T1,
    asof: date = NIGHT,
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


def eff(card: CallCard, *, seq: int = 1, reconstructed=False, ingest_fresh=True) -> InEffect:
    return InEffect(card.asof, seq, reconstructed, ingest_fresh, card)


def thesis(tid: UUID, name: str, *, catalysts=(), kills=()) -> Thesis:
    return Thesis(
        id=tid,
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        catalysts=list(catalysts),
        kill_criteria=[KillCriterion(id=uuid.uuid4(), text=t) for t in kills],
    )


def cat(label: str, when: date | None) -> Catalyst:
    return Catalyst(id=uuid.uuid4(), label=label, when_date=when)


def weekdays(start: date, end: date) -> list[date]:
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def bars(pairs: dict[date, float]) -> tuple[tuple[date, float], ...]:
    return tuple(sorted(pairs.items()))


# IWM: 200 on Sep 10, +2 a session through the 17th (210)
IWM = bars({d(10): 200.0, d(11): 202.0, d(14): 204.0, d(15): 206.0, d(16): 208.0, d(17): 210.0})


def inputs(**over) -> DigestInputs:
    base = dict(
        asof=NIGHT,
        clock="public",
        scope=Scope(0, 0, 0),
        theses=(),
        latest=NIGHT,
        previous=PREV,
        now={},
        baseline={},
        tickers=dict(TICKERS),
        positions={},
        roster_tickers={},
        memberships=(),
        silent_rows_left_out=0,
        unresolved_rows=0,
        filings={},
        nights=(),
        runs={},
        tapes={},
        iwm_bars=IWM,
        flagged={},
        window_start=None,
    )
    base.update(over)
    return DigestInputs(**base)


def run(**over) -> DailyDigest:
    return daily_digest(inputs(**over))


# ---------------------------------------------------------------------------------------------------------
# 0. the night pair
# ---------------------------------------------------------------------------------------------------------


def test_the_night_pair_counts_the_sessions_between_and_how_far_past_the_night_the_asof_is():
    # the DEFAULT view (F1): the app's as-of is the current night, the night the pass last recorded — so on the
    # default view the as-of IS the newest night and nothing is "past" it, the morning after or on a weekend alike
    on_the_night = night_pair(NIGHT, NIGHT, PREV)
    assert (on_the_night.latest, on_the_night.previous) == (NIGHT, PREV)
    assert (on_the_night.sessions_between, on_the_night.days_after_night) == (1, 0)
    # a hand-picked as-of a day after the night (or one whose pass has not recorded yet) says how far past it is
    assert night_pair(d(18), NIGHT, PREV).days_after_night == 1
    # a Monday as-of picked by hand reads Friday against Thursday: one session between them, three days past
    monday = night_pair(d(21), d(18), d(17))
    assert (monday.sessions_between, monday.days_after_night) == (1, 3)
    # Thursday to the next Monday: Friday and Monday are two sessions
    assert night_pair(d(21), d(21), d(17)).sessions_between == 2


def test_a_record_with_no_night_or_a_single_night_has_no_pair():
    none = night_pair(NIGHT, None, None)
    assert (none.latest, none.previous, none.sessions_between, none.days_after_night) == (
        None,
        None,
        None,
        None,
    )
    one = night_pair(NIGHT, NIGHT, None)
    assert (one.latest, one.previous, one.sessions_between) == (NIGHT, None, None)


# ---------------------------------------------------------------------------------------------------------
# 1. last night
# ---------------------------------------------------------------------------------------------------------


def _item(change: str, **kw) -> ChangeItem:
    return ChangeItem(
        section="names", change=change, thesis_id=T1, thesis_name="Card A", **kw  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "item, tone",
    [
        (_item("state", from_state=State.WARMING, to_state=State.ARMED), "accent"),
        (_item("state", from_state=State.ARMED, to_state=State.WARMING), "warm"),
        (_item("state", from_state=State.ARMED, to_state=State.MANAGING), None),
        (_item("state", from_state=State.WARMING, to_state=State.INCUBATING), None),
        (_item("new_card", to_state=State.ARMED), "accent"),
        (_item("new_card", to_state=State.WARMING), None),
        (_item("armed"), "accent"),
        (_item("entry_window", days=3), "accent"),
        (_item("exit_by"), "warm"),
        (_item("no_longer_armed"), None),
        (_item("new_watch"), None),
        (_item("key_on"), None),
        (_item("key_off"), None),
        (_item("trigger_new"), None),
        (_item("trigger_gone"), None),
        (_item("trigger_redated"), None),
        (_item("risk_new"), None),
    ],
)
def test_the_one_accent_is_on_three_lines_and_the_warm_tone_on_two(item, tone):
    assert tone_of(item) == tone


def _mixed_night():
    """Five cards. A: Warming to Incubating. B: Warming, the confirmation key turned. C: Warming to Armed, AAA newly
    armed, its entry window closing in 2 days. D: Armed to Warming. E: Managing both nights, a new risk signal and its
    exit-by reached. The names sort A..E, the order below must NOT."""
    theses = [
        thesis(t, f"Card {n}") for t, n in ((T1, "A"), (T2, "B"), (T3, "C"), (T4, "D"), (T5, "E"))
    ]
    base = {
        T1: cc(State.WARMING, tid=T1, asof=PREV),
        T2: cc(State.WARMING, tid=T2, asof=PREV),
        T3: cc(State.WARMING, tid=T3, asof=PREV),
        T4: cc(State.ARMED, tid=T4, asof=PREV, armed_members=[mem(B)]),
        T5: cc(State.MANAGING, tid=T5, asof=PREV),
    }
    now = {
        T1: cc(State.INCUBATING, tid=T1),
        T2: cc(State.WARMING, tid=T2, confirmation=True),
        T3: cc(State.ARMED, tid=T3, armed_members=[mem(A)], arm_until=NIGHT + timedelta(days=2)),
        T4: cc(State.WARMING, tid=T4),
        T5: cc(
            State.MANAGING,
            tid=T5,
            exit_by=NIGHT,
            risk_signals=[trig(C, "going-concern note", Kind.DILUTION_RISK)],
        ),
    }
    return theses, {t: eff(c) for t, c in now.items()}, {t: eff(c) for t, c in base.items()}


def test_section_one_groups_by_card_with_armed_first_warming_next_and_incubating_last():
    theses, now, base = _mixed_night()
    out = last_night(
        {t.id: t.name for t in theses}, now, base, TICKERS, latest=NIGHT, previous=PREV
    )
    assert not out.no_baseline and out.cards_with_change == 5
    assert [(c.thesis_name, c.tier) for c in out.cards] == [
        ("Card C", "armed"),  # entered Armed
        ("Card D", "armed"),  # left Armed
        ("Card E", "armed"),  # Managing: a position is held
        ("Card B", "warming"),
        ("Card A", "incubating"),  # last, whatever its name
    ]
    by = {c.thesis_name: c for c in out.cards}
    # the card's state move is its headline; the other lines follow in What Changed's own order
    assert (by["Card C"].headline.from_state, by["Card C"].headline.to_state) == (
        State.WARMING,
        State.ARMED,
    )
    assert [(ln.change, ln.ticker, ln.tone) for ln in by["Card C"].lines] == [
        ("armed", "AAA", "accent"),
        ("entry_window", None, "accent"),
    ]
    assert by["Card C"].headline.tone == "accent"
    assert by["Card D"].headline.tone == "warm" and by["Card D"].previous_state == State.ARMED
    assert [(ln.change, ln.tone) for ln in by["Card D"].lines] == [("no_longer_armed", None)]
    # a Managing card with no state move has no headline; an exit-by reached is warm, a new risk signal plain
    assert by["Card E"].headline is None
    assert [(ln.change, ln.ticker, ln.tone) for ln in by["Card E"].lines] == [
        ("risk_new", "CCC", None),
        ("exit_by", None, "warm"),
    ]
    # Warming and Incubating cards carry no tone at all
    assert [(ln.change, ln.label, ln.tone) for ln in by["Card B"].lines] == [
        ("key_on", "Confirmation", None)
    ]
    assert by["Card A"].headline.tone is None and by["Card A"].lines == ()


def test_only_the_three_accent_lines_and_the_two_warm_lines_carry_a_tone():
    theses, now, base = _mixed_night()
    out = last_night(
        {t.id: t.name for t in theses}, now, base, TICKERS, latest=NIGHT, previous=PREV
    )
    toned = sorted(
        (c.thesis_name, ln.change, ln.tone)
        for c in out.cards
        for ln in (c.headline, *c.lines)
        if ln is not None and ln.tone is not None
    )
    assert toned == [
        ("Card C", "armed", "accent"),
        ("Card C", "entry_window", "accent"),
        ("Card C", "state", "accent"),
        ("Card D", "state", "warm"),
        ("Card E", "exit_by", "warm"),
    ]


def test_a_card_with_no_baseline_row_is_one_new_card_line_and_one_with_no_call_is_not_a_card_with_a_change():
    theses = [thesis(T1, "Card A"), thesis(T2, "Card B")]
    out = last_night(
        {t.id: t.name for t in theses},
        {T1: eff(cc(State.ARMED, tid=T1))},
        {},
        TICKERS,
        latest=NIGHT,
        previous=PREV,
    )
    (only,) = out.cards
    assert (only.thesis_name, only.tier, only.previous_state) == ("Card A", "armed", None)
    assert (
        only.headline.change == "new_card" and only.headline.tone == "accent" and only.lines == ()
    )
    assert out.cards_with_change == 1


def test_without_an_earlier_night_there_is_no_baseline_and_no_line():
    theses = [thesis(T1, "Card A")]
    for latest, previous in ((NIGHT, None), (None, None)):
        out = last_night(
            {T1: "Card A"},
            {T1: eff(cc(State.ARMED))},
            {},
            TICKERS,
            latest=latest,
            previous=previous,
        )
        assert out.no_baseline and out.cards == () and out.lines == 0
    assert theses


def test_a_trigger_line_carries_its_first_source_by_the_evidence_ledgers_link_rule():
    filing = Provenance(source="form4", ref="0000876543-26-000001")
    price = Provenance(source="price", ref="price:AAA:2026-09-10")
    old = cc(
        State.WARMING,
        asof=PREV,
        triggers_fired=[trig(B, "open-market buy", Kind.INSIDER, sources=[filing])],
    )
    new = cc(
        State.WARMING,
        triggers_fired=[
            trig(A, "insider buy", Kind.INSIDER, sources=[filing, price]),  # new: a filing first
            trig(C, "breakout", sources=[price]),  # new: a price reference has no link
            trig(H, "unsourced", Kind.LAGGARD),  # new: states no source at all
        ],
        risk_signals=[trig(A, "going-concern note", Kind.DILUTION_RISK, sources=[filing])],
    )
    ciks = {A: CIK, B: CIK, C: CIK, H: CIK}
    out = last_night(
        {T1: "Card A"},
        {T1: eff(new)},
        {T1: eff(old)},
        TICKERS,
        latest=NIGHT,
        previous=PREV,
        ciks=ciks,
    )
    (card,) = out.cards
    by = {(ln.change, ln.ticker): ln.source for ln in card.lines}
    link = by[("trigger_new", "AAA")]
    assert (link.source, link.ref) == ("form4", "0000876543-26-000001")
    assert link.url == source_url("0000876543-26-000001", CIK) and link.url.startswith("https://")
    assert by[("risk_new", "AAA")].ref == "0000876543-26-000001"
    assert by[("trigger_new", "CCC")].url is None and by[("trigger_new", "CCC")].source == "price"
    assert by[("trigger_new", "HHH")] is None
    # a trigger no longer firing is read from the BASELINE card it was on
    assert by[("trigger_gone", "BBB")].ref == "0000876543-26-000001"
    # a line that is not a trigger carries no source
    assert all(
        ln.source is None
        for ln in card.lines
        if ln.change not in ("trigger_new", "trigger_gone", "risk_new")
    )


def test_a_trigger_is_the_same_trigger_under_a_new_label_and_a_moved_label_makes_no_line():
    old = cc(State.WARMING, asof=PREV, triggers_fired=[trig(A, "breakout, close 10.10")])
    new = cc(State.WARMING, triggers_fired=[trig(A, "breakout, close 10.55")])
    out = last_night(
        {T1: "Card A"}, {T1: eff(new)}, {T1: eff(old)}, TICKERS, latest=NIGHT, previous=PREV
    )
    assert out.cards == ()  # identity = kind, security, event date: never the label


def test_a_card_marks_its_ingest_and_its_reconstruction_from_the_row_in_effect():
    old = cc(State.WARMING, asof=PREV)
    new = cc(State.ARMED)
    out = last_night(
        {T1: "Card A"},
        {T1: eff(new, reconstructed=True, ingest_fresh=False)},
        {T1: eff(old)},
        TICKERS,
        latest=NIGHT,
        previous=PREV,
    )
    (card,) = out.cards
    assert (card.reconstructed, card.ingest_fresh) == (True, False)


# ---------------------------------------------------------------------------------------------------------
# 2. positions
# ---------------------------------------------------------------------------------------------------------

# the held name: 50 on Sep 10 ... 66 on Sep 17
HELD = bars({d(10): 50.0, d(11): 52.0, d(14): 54.0, d(15): 55.0, d(16): 60.0, d(17): 66.0})


def _managing(*, exit_by=None, risks=(), kills=("the license is lost",), position="held", **pos):
    card = cc(State.MANAGING, tid=T1, exit_by=exit_by, risk_signals=list(risks))
    held = HeldPosition(
        security_id=pos.get("security_id", H),
        opened_on=pos.get("opened_on", d(11)),
        entry_price=pos.get("entry_price", 48.0),
        shares=pos.get("shares"),
    )
    return dict(
        theses=[thesis(T1, "Card M", kills=kills)],
        now={T1: eff(card)},
        positions={T1: held} if position == "held" else {},
        tapes={H: NameTape(bars=HELD)},
        roster_tickers={(T1, H): "HHH"},
    )


def test_a_managing_card_returns_from_the_logged_price_and_says_so():
    out = run(**_managing(shares=100.0))
    (row,) = out.positions
    assert (row.thesis_name, row.ticker, row.has_position) == ("Card M", "HHH", True)
    assert (row.opened_on, row.logged_price, row.shares) == (d(11), 48.0, 100.0)
    f = row.since_fill
    assert (f.status, f.basis, f.basis_price, f.basis_date) == (
        "priced",
        "logged_price",
        48.0,
        d(11),
    )
    assert (f.last_date, f.last_close, f.ret_pct) == (d(17), 66.0, 37.5)
    # IWM over the same dates: the first close on or after the fill date to the same last date
    assert f.bench_pct == round((210.0 / 202.0 - 1) * 100, 2)
    assert row.fill_usd == 4800.0  # both logged: shares times price


def test_without_a_logged_price_the_return_starts_at_the_first_close_on_or_after_the_fill_and_says_so():
    out = run(**_managing(entry_price=None))
    (row,) = out.positions
    f = row.since_fill
    assert (f.status, f.basis, f.basis_price, f.basis_date) == (
        "priced",
        "first_close",
        52.0,
        d(11),
    )
    assert f.ret_pct == round((66.0 / 52.0 - 1) * 100, 2) and f.bench_pct == round(
        (210.0 / 202.0 - 1) * 100, 2
    )
    assert row.logged_price is None and row.fill_usd is None
    # a fill on a weekend starts at the next session's close
    weekend = run(**_managing(entry_price=None, opened_on=d(12)))
    assert weekend.positions[0].since_fill.basis_date == d(14)


def test_shares_times_price_shows_only_when_both_were_logged():
    assert run(**_managing(shares=None)).positions[0].fill_usd is None
    assert run(**_managing(shares=10.0, entry_price=None)).positions[0].fill_usd is None
    assert run(**_managing(shares=10.0)).positions[0].fill_usd == 480.0


def test_the_last_session_is_the_calendars_last_two_sessions():
    (row,) = run(**_managing()).positions
    ls = row.last_session
    assert (ls.status, ls.session, ls.previous_session) == ("priced", d(17), d(16))
    assert ls.ret_pct == 10.0 and ls.bench_pct == round((210.0 / 208.0 - 1) * 100, 2)


def test_a_position_that_names_no_company_keeps_its_line_with_the_cards_clocks_and_risks():
    risks = [trig(C, "going-concern note", Kind.DILUTION_RISK)]
    out = run(**_managing(security_id=None, exit_by=d(22), risks=risks))
    (row,) = out.positions
    assert (row.has_position, row.security_id, row.ticker) == (True, None, None)
    assert (row.since_fill.status, row.last_session.status) == ("no_name", "unpriced")
    assert (row.opened_on, row.logged_price) == (d(11), 48.0)  # the fill itself is still shown
    assert row.hold.sessions_left == 3 and row.risk_signals == 1


def test_a_managing_card_with_no_position_as_of_the_view_keeps_its_line_and_says_so():
    (row,) = run(**_managing(position="none")).positions
    assert row.has_position is False
    assert (row.security_id, row.opened_on, row.logged_price, row.shares) == (
        None,
        None,
        None,
        None,
    )
    assert row.since_fill.status == "no_fill" and "no position logged" in row.since_fill.reason
    assert row.kill_criteria == ("the license is lost",)


def test_a_flagged_price_history_keeps_the_line_and_reads_the_reason_in_its_price_cells():
    out = run(**_managing(), flagged={H: "mixed-scale price history"})
    (row,) = out.positions
    assert (row.since_fill.status, row.since_fill.reason) == (
        "flagged",
        "mixed-scale price history",
    )
    assert (row.last_session.status, row.last_session.reason) == (
        "flagged",
        "mixed-scale price history",
    )
    assert [(f.ticker, f.reason) for f in out.flagged] == [("HHH", "mixed-scale price history")]


def test_a_split_between_the_fill_and_the_asof_puts_the_return_on_the_views_scale():
    split = ListedSplit(
        key_date=d(14),
        effective_date=d(14),
        ratio_from=Decimal(1),
        ratio_to=Decimal(2),
        status="confirmed",
    )
    held = HeldPosition(H, d(11), 48.0, None)
    tape = NameTape(bars=HELD, splits=(split,))
    f = since_fill(held, tape, Series.of(dict(IWM)), NIGHT, flag=None, window_start=None)
    assert f.basis == "first_close" and f.basis_price == 52.0 and "split" in f.note
    # a split BEFORE the fill, or one the list has not settled, leaves the logged price in use
    early = replace(split, effective_date=d(10), key_date=d(10))
    unsettled = replace(split, status="needs_review")
    for splits in ((early,), (unsettled,)):
        g = since_fill(
            held,
            NameTape(bars=HELD, splits=splits),
            Series.of(dict(IWM)),
            NIGHT,
            flag=None,
            window_start=None,
        )
        assert g.basis == "logged_price" and g.note is None
    assert split_since_fill((split,), d(11), NIGHT) and not split_since_fill((split,), d(14), NIGHT)


def test_a_fill_after_the_last_close_or_before_the_price_window_reads_unpriced_with_its_reason():
    held = HeldPosition(H, d(17), 48.0, None)
    ends_16 = NameTape(bars=tuple(b for b in HELD if b[0] <= d(16)))
    f = since_fill(held, ends_16, Series.of(dict(IWM)), NIGHT, flag=None, window_start=None)
    assert f.status == "unpriced" and "fill date" in f.reason
    old = HeldPosition(H, d(10), None, None)
    g = since_fill(
        old, NameTape(bars=HELD), Series.of(dict(IWM)), NIGHT, flag=None, window_start=d(11)
    )
    assert g.status == "unpriced" and "before the price window" in g.reason
    nothing = HeldPosition(H, None, None, None)
    assert (
        since_fill(
            nothing, NameTape(bars=HELD), Series.of(dict(IWM)), NIGHT, flag=None, window_start=None
        ).status
        == "no_fill"
    )


def test_the_hold_clock_counts_sessions_left_by_the_hot_boards_rule_or_the_sessions_since_it_passed():
    calendar = [d(14), d(15), d(16), d(17)]
    assert hold_clock(d(22), NIGHT, calendar).sessions_left == 3  # Fri, Mon, Tue
    assert hold_clock(NIGHT, NIGHT, calendar).sessions_left == 0  # its last day
    passed = hold_clock(d(15), NIGHT, calendar)
    assert (passed.sessions_left, passed.passed_sessions) == (None, 2)  # the 16th and the 17th
    assert hold_clock(None, NIGHT, calendar).exit_by is None


def test_risk_signals_count_distinct_identities_and_mark_the_ones_section_one_calls_new():
    same_signal = [
        trig(C, "dilution, 10% of float", Kind.DILUTION_RISK, on=d(15)),
        trig(C, "dilution, 12% of float", Kind.DILUTION_RISK, on=d(15)),  # relabeled: the same
        trig(B, "going-concern note", Kind.DILUTION_RISK, on=d(16)),
    ]
    world = _managing(risks=same_signal)
    base = cc(State.MANAGING, tid=T1, asof=PREV, risk_signals=[same_signal[0]])
    out = run(**world, baseline={T1: eff(base)})
    (row,) = out.positions
    assert row.risk_signals == 2  # two identities, not three labels
    assert row.risk_new == 1  # the going-concern note is new since P


def test_kill_criteria_ride_verbatim_in_the_cards_own_order():
    kills = ("the license is lost", "a second 'going concern' note")
    (row,) = run(**_managing(kills=kills)).positions
    assert row.kill_criteria == kills


def test_only_the_managing_cards_get_a_position_line_by_name_order():
    theses = [thesis(T2, "Card B"), thesis(T1, "Card A"), thesis(T3, "Card C")]
    now = {
        T1: eff(cc(State.MANAGING, tid=T1)),
        T2: eff(cc(State.MANAGING, tid=T2)),
        T3: eff(cc(State.ARMED, tid=T3)),
    }
    out = run(theses=theses, now=now)
    assert [p.thesis_name for p in out.positions] == ["Card A", "Card B"]


def test_last_session_needs_a_close_on_both_of_the_calendars_last_two_sessions():
    calendar = [d(15), d(16), d(17)]
    bench = Series.of(dict(IWM))
    one_gap = Series.of({d(15): 50.0, d(17): 66.0})  # no close on the 16th
    got = last_session(one_gap, bench, calendar)
    assert (got.status, got.reason) == ("unpriced", "no close on 2026-09-16")
    stale = Series.of({d(15): 50.0, d(16): 60.0})  # no close on the last session
    assert last_session(stale, bench, calendar).reason == "no close on 2026-09-17"
    assert last_session(None, bench, calendar).reason == "no price bars"
    assert last_session(one_gap, bench, [d(17)]).reason == "no session calendar"
    assert (
        last_session(one_gap, bench, calendar, flag="mixed-scale price history").status == "flagged"
    )
    # IWM blank (no bar on one session) leaves the name's own return
    got = last_session(Series.of({d(16): 60.0, d(17): 66.0}), Series.of({d(17): 210.0}), calendar)
    assert got.ret_pct == 10.0 and got.bench_pct is None


# ---------------------------------------------------------------------------------------------------------
# 3. due this week
# ---------------------------------------------------------------------------------------------------------


def test_due_this_week_is_the_calendars_bucket_with_the_today_mark_and_the_deadlines_of_the_card_in_effect():
    sun, mon = d(20), d(21)
    t = thesis(
        T1,
        "Card A",
        catalysts=[cat("today's readout", NIGHT), cat("sunday vote", sun), cat("monday vote", mon)],
    )
    now = {T1: eff(cc(State.ARMED, tid=T1, arm_until=d(18), exit_by=d(30)))}
    out = run(theses=[t], now=now)
    due = out.due
    assert (due.start, due.end) == (NIGHT, sun)  # through the coming Sunday
    assert [(i.item, i.label, i.today) for i in due.items] == [
        ("catalyst", "today's readout", True),
        ("entry_window", "entry window ends", False),
        ("catalyst", "sunday vote", False),
    ]
    assert [i.days for i in due.items] == [0, 1, 3]
    assert (due.catalysts, due.deadlines) == (2, 1)
    assert all(i.state == "armed" for i in due.items if i.item != "catalyst")
    # the exit-by (Sep 30) is later than the week, monday's vote is next week: neither is here


def test_due_this_week_counts_from_the_origin_on_a_live_view_and_the_asof_on_a_past_one():
    """F2: the as-of is the night (Thursday 9-17) and a live view acts the next day, Friday 9-18. From that origin
    Friday's catalyst is 'today', Thursday's has passed, and the entry window that ended on the night is gone
    (a passed deadline is never listed); counted from the as-of, as a past view is, Thursday's reads 'today'.
    """
    fri, sat = d(18), d(19)
    t = thesis(
        T1,
        "Card A",
        catalysts=[cat("thursday readout", NIGHT), cat("friday readout", fri), cat("sat", sat)],
    )
    now = {T1: eff(cc(State.ARMED, tid=T1, arm_until=NIGHT, exit_by=d(30)))}
    live = run(theses=[t], now=now, origin=fri).due
    assert live.start == fri
    assert [(i.label, i.days, i.today) for i in live.items] == [
        ("friday readout", 0, True),
        ("sat", 1, False),
    ]
    assert live.passed_total == 1  # Thursday's, to prune
    past = run(theses=[t], now=now).due  # no origin: counted from the as-of
    assert past.start == NIGHT
    assert [(i.label, i.days, i.today) for i in past.items][:2] == [
        ("thursday readout", 0, True),
        ("entry window ends", 0, True),
    ]


def test_due_this_week_counts_the_catalysts_that_passed_since_p_and_are_not_yet_pruned():
    monday, friday, thursday = d(21), d(18), d(17)
    t = thesis(
        T1,
        "Card A",
        catalysts=[cat("saturday event", d(19)), cat("thursday event", thursday), cat("old", d(1))],
    )
    out = run(theses=[t], asof=monday, latest=friday, previous=thursday)
    # Sep 19 passed after P (Thursday) and is not pruned; Sep 17 is P itself, Sep 1 long before
    assert (out.due.passed_since, out.due.passed_total) == (1, 3)
    assert (out.due.start, out.due.end) == (monday, d(27))


def test_due_this_week_with_no_p_has_no_passed_count_and_a_card_with_no_call_is_counted():
    t = thesis(T1, "Card A", catalysts=[cat("x", d(18))])
    out = run(theses=[t], previous=None)
    assert out.due.passed_since is None and out.due.cards_without_calls == 1
    assert [i.label for i in out.due.items] == ["x"]


# ---------------------------------------------------------------------------------------------------------
# 4. insider buys filed since P
# ---------------------------------------------------------------------------------------------------------

ACCS = {n: f"0000876543-26-{n:06d}" for n in range(1, 12)}
W = Who(A, "AAA", CIK, "Alpha Corp")


def _txn(n, filed: date, code="P", name="Jane Roe", role="CEO", shares=1000, price=4.0):
    return {
        "insider_name": name,
        "insider_role": role,
        "txn_code": code,
        "shares": shares,
        "price": price,
        "usd": shares * price,
        "accession": ACCS[n],
        "valid_from": filed - timedelta(days=1),
        "accepted": datetime.combine(filed, time(21, 0), tzinfo=UTC),
        "recorded_at": datetime.combine(filed, time(23, 0), tzinfo=UTC),
        "issuer_cik": CIK,
        "issuer_name": "Alpha Corp",
        "rpt_owner_cik": "0001111111",
        "aff_10b5_1": None,
        "direct_indirect": None,
        "shares_owned_after": None,
        "ownership_nature": None,
        "txn_footnotes": None,
        "filing_natures": None,
    }


def f4(n, filed: date, **kw):
    (row,) = rows_from_insider_txns([_txn(n, filed, **kw)], W)
    return row


def _member(sid=A, ticker="AAA", tid=T1, name="Card A") -> Membership:
    return Membership(tid, name, ticker, sid, "Core", False)


def _buys_world(**over):
    base = dict(
        theses=[thesis(T1, "Card A")],
        memberships=[_member()],
        filings={
            A: (
                f4(1, NIGHT, name="Jane Roe"),  # filed in (P, N]: a row
                f4(
                    2, PREV, name="Sam Poe", role="Director"
                ),  # filed ON P: not in the span, but a neighbor
                f4(3, d(1), name="Kim Lee", role="10% owner"),  # 16 days before: the lead-in
                f4(4, NIGHT, code="S", name="Jane Roe"),  # sales only, filed in the span: routine
                f4(
                    5, d(18), name="Ola Ray"
                ),  # filed AFTER the night: never a row, never a neighbor
            )
        },
    )
    base.update(over)
    return base


def test_insider_buys_are_the_purchases_filed_after_p_through_n_and_the_cluster_sees_the_lead_in():
    out = run(**_buys_world(asof=d(18))).insider
    assert not out.no_baseline and (out.span_start, out.span_end) == (PREV, NIGHT)
    (row,) = out.rows
    assert (row.ticker, row.filed, row.filer, row.role, row.accession) == (
        "AAA",
        NIGHT,
        "Jane Roe",
        "CEO",
        ACCS[1],
    )
    assert (row.shares, row.price, row.usd) == (1000.0, 4.0, 4000.0)
    assert (row.trade_date, row.filed_days_after) == (d(16), 1)
    assert row.role_classes == ("officer",)
    # three distinct buyers within 30 days of the 17th (the 16th's, the 1st's) and nobody filed after the night
    assert row.cluster.buyers == 3
    assert sorted(o.name for o in row.cluster.others) == ["Kim Lee", "Sam Poe"]
    assert [c.thesis_name for c in row.cards] == ["Card A"]
    c = out.counts
    assert (c.filings_read, c.purchases, c.routine_folded, c.amendments_no_purchase) == (2, 1, 1, 0)
    assert (c.names_read, c.names_with_purchases, c.cluster_rows, c.trigger_rows) == (1, 1, 1, 0)


def test_a_buy_with_one_buyer_is_no_cluster_and_a_repeat_buyer_is_one_buyer():
    filings = {A: (f4(1, NIGHT, name="Jane Roe"), f4(2, d(10), name="Jane Roe"))}
    (row,) = run(**_buys_world(filings=filings)).insider.rows
    assert row.cluster is None


def test_a_row_names_the_recorded_insider_trigger_that_carries_its_accession():
    card = cc(
        State.WARMING,
        tid=T1,
        triggers_fired=[
            trig(
                A,
                "open-market buy",
                Kind.INSIDER,
                sources=[Provenance(source="form4", ref=ACCS[1])],
            )
        ],
    )
    nights = [CardNights(T1, "Card A", None, (Night(NIGHT, 1, False, card),))]
    (row,) = run(**_buys_world(nights=nights)).insider.rows
    (hit,) = row.triggers
    assert (hit.thesis_name, hit.recorded_on, hit.live) == ("Card A", NIGHT, True)
    out = run(**_buys_world(nights=nights)).insider
    assert out.counts.trigger_rows == 1 and out.counts.nights_read == 1
    # no recorded trigger carries it: "not a trigger", and the page says nothing about why
    (bare,) = run(**_buys_world()).insider.rows
    assert bare.triggers == ()


def test_with_no_p_the_insider_section_has_no_span_and_no_row():
    out = run(**_buys_world(previous=None)).insider
    assert out.no_baseline and out.rows == () and (out.span_start, out.span_end) == (None, None)


def test_the_insider_section_echoes_what_the_voting_read_left_out():
    out = run(**_buys_world(silent_rows_left_out=4, unresolved_rows=1)).insider
    assert (out.counts.silent_rows_left_out, out.counts.unresolved_rows) == (4, 1)


def test_insider_buys_order_is_newest_filing_then_dollars_then_ticker():
    filings = {
        A: (
            f4(1, NIGHT, name="Jane Roe", shares=100, price=4.0),
            f4(2, NIGHT, name="Sam Poe", shares=900, price=4.0),
        )
    }
    rows = run(**_buys_world(filings=filings)).insider.rows
    assert [r.filer for r in rows] == ["Sam Poe", "Jane Roe"]  # the larger dollar size first


# ---------------------------------------------------------------------------------------------------------
# 5. the tape
# ---------------------------------------------------------------------------------------------------------


def _tape_world(returns: dict[UUID, float], *, extra_cards=(), unpriced=(), flagged=None):
    """One Armed card T1 holding every name in ``returns`` (each priced 100 on the 16th and 100 + r on the 17th, armed
    on the 15th); ``unpriced`` names have no close on the 17th. ``extra_cards`` = (tid, name, [sids]) more Armed
    cards. Tickers are ``N00``, ``N01``, ... in the order given."""
    sids = list(returns) + list(unpriced)
    tickers = {s: f"N{i:02d}" for i, s in enumerate(sids)}
    cards = [(T1, "Card A", sids), *extra_cards]
    now, theses, roster, runs = {}, [], {}, {}
    for tid, name, members in cards:
        theses.append(thesis(tid, name))
        now[tid] = eff(cc(State.ARMED, tid=tid, armed_members=[mem(s) for s in members]))
        runs[tid] = [LogRow(d(15), 1, False, State.ARMED, frozenset(members))]
        roster.update({(tid, s): tickers[s] for s in members})
    tapes = {}
    for s, r in returns.items():
        tapes[s] = NameTape(bars=bars({d(15): 98.0, d(16): 100.0, d(17): round(100.0 + r, 6)}))
    for s in unpriced:
        tapes[s] = NameTape(bars=bars({d(15): 98.0, d(16): 100.0}))
    return dict(
        theses=theses,
        now=now,
        runs=runs,
        tapes=tapes,
        tickers=tickers,
        roster_tickers=roster,
        flagged=flagged or {},
    )


def _sids(n):
    return [uuid.UUID(int=0x5000 + i) for i in range(n)]


def test_the_tape_cuts_the_five_largest_rises_and_the_five_largest_falls_over_the_priced_names():
    s = _sids(16)
    rets = [9, 8, 7, 6, 5, 4, 3, 0, -1, -2, -3, -4, -5, -6, -7, 0.5]
    out = run(**_tape_world(dict(zip(s, rets, strict=True)))).tape
    assert (out.session, out.previous_session) == (NIGHT, PREV)
    assert out.iwm_pct == round((210.0 / 208.0 - 1) * 100, 2)
    shown = lambda cut: [r.last_session.ret_pct for r in out.rows if r.cut == cut]  # noqa: E731
    assert shown("rise") == [9.0, 8.0, 7.0, 6.0, 5.0]
    assert shown("fall") == [-3.0, -4.0, -5.0, -6.0, -7.0]
    # the rows come largest rise to largest fall: every armed row is present, the cut only flags
    assert [r.last_session.ret_pct for r in out.rows] == sorted(rets, reverse=True)
    c = out.counts
    assert (c.rows, c.names, c.names_priced) == (16, 16, 16)
    assert (c.rises_shown, c.falls_shown) == (TOP_N, TOP_N) and TOP_N == 5
    # a flat name is neither a rise nor a fall
    assert next(r for r in out.rows if r.last_session.ret_pct == 0.0).cut is None


def test_with_fewer_than_five_rises_only_the_names_that_rose_are_rises():
    s = _sids(4)
    out = run(**_tape_world(dict(zip(s, [2.0, -1.0, -2.0, -3.0], strict=True)))).tape
    assert [r.last_session.ret_pct for r in out.rows if r.cut == "rise"] == [2.0]
    assert [r.last_session.ret_pct for r in out.rows if r.cut == "fall"] == [-1.0, -2.0, -3.0]


def test_a_tie_at_the_edge_of_the_cut_goes_by_ticker():
    s = _sids(7)
    # six names up 5 and one up 9: the cut keeps the 9 and the first four 5s by ticker
    out = run(**_tape_world(dict(zip(s, [9, 5, 5, 5, 5, 5, 5], strict=True)))).tape
    rises = [r.ticker for r in out.rows if r.cut == "rise"]
    assert rises == ["N00", "N01", "N02", "N03", "N04"]
    assert [r.ticker for r in out.rows if r.cut is None] == ["N05", "N06"]


def test_a_name_on_two_cards_is_two_rows_and_one_name_in_the_cut():
    s = _sids(3)
    world = _tape_world(
        dict(zip(s, [4.0, 2.0, -3.0], strict=True)), extra_cards=[(T2, "Card B", [s[0]])]
    )
    out = run(**world).tape
    assert [r.ticker for r in out.rows].count("N00") == 2
    assert out.counts.rows == 4 and out.counts.names == 3
    assert [r.cut for r in out.rows if r.ticker == "N00"] == ["rise", "rise"]
    assert out.counts.rises_shown == 2  # names, not rows


def test_an_unpriced_or_flagged_name_keeps_its_row_after_the_priced_ones_and_is_counted():
    s = _sids(3)
    gone = _sids(5)[4]
    world = _tape_world(
        {s[0]: 3.0, s[1]: -2.0},
        unpriced=[gone],
        flagged={s[1]: "mixed-scale price history"},
    )
    out = run(**world).tape
    assert [(r.ticker, r.last_session.status) for r in out.rows] == [
        ("N00", "priced"),
        ("N01", "flagged"),
        ("N02", "unpriced"),
    ]
    assert out.rows[2].last_session.reason == "no close on 2026-09-17"
    assert out.rows[1].last_session.reason == "mixed-scale price history"
    assert out.rows[1].cut is None and out.rows[1].since_arm.gap == "flagged"
    c = out.counts
    assert (c.rows, c.names, c.names_priced, c.names_unpriced, c.names_flagged) == (3, 3, 1, 1, 1)
    # the flagged note lists the name once, with its reason
    assert [(f.ticker, f.reason) for f in run(**world).flagged] == [
        ("N01", "mixed-scale price history")
    ]


def test_the_tape_rows_carry_the_arm_night_the_two_clocks_and_the_move_since_the_arm_night():
    s = _sids(1)
    world = _tape_world({s[0]: 4.0})
    world["now"][T1] = eff(
        cc(State.ARMED, tid=T1, armed_members=[mem(s[0])], arm_until=d(22), exit_by=d(30))
    )
    (row,) = run(**world).tape.rows
    assert (row.arm_night, row.arm_reconstructed, row.sessions_since) == (d(15), False, 2)
    assert (row.arm_until, row.sessions_left, row.closed_ago) == (d(22), 3, None)
    sa = row.since_arm
    assert (sa.entry_date, sa.entry_close, sa.last_date, sa.last_close) == (
        d(15),
        98.0,
        d(17),
        104.0,
    )
    assert sa.ret_pct == round((104.0 / 98.0 - 1) * 100, 2)
    assert sa.iwm_pct == round((210.0 / 206.0 - 1) * 100, 2)


def test_only_the_armed_rows_are_on_the_tape_not_watch_warming_or_managing_names():
    s = _sids(3)
    world = _tape_world({s[0]: 1.0})
    world["theses"] += [thesis(T2, "Card B"), thesis(T3, "Card C")]
    world["now"][T2] = eff(cc(State.WARMING, tid=T2, triggers_fired=[trig(s[1])]))
    world["now"][T3] = eff(cc(State.MANAGING, tid=T3, armed_members=[mem(s[2])]))
    world["now"][T1] = eff(
        cc(State.ARMED, tid=T1, armed_members=[mem(s[0])], watch_members=[mem(s[1])])
    )
    out = run(**world).tape
    assert [r.security_id for r in out.rows] == [s[0]]


# ---------------------------------------------------------------------------------------------------------
# 6. forming, the counts, the empty page
# ---------------------------------------------------------------------------------------------------------


def test_forming_is_counts_with_the_warming_cards_whose_key_turned_last_night_by_name():
    theses, now, base = _mixed_night()
    out = run(theses=theses, now=now, baseline=base).forming
    assert (out.warming, out.incubating) == (2, 1)  # B and D are Warming, A is Incubating
    assert [(k.thesis_name, k.labels) for k in out.key_turned] == [("Card B", ("Confirmation",))]


def test_the_counts_say_what_the_page_rests_on():
    theses, now, base = _mixed_night()
    theses.append(thesis(uuid.UUID(int=0x106), "Card F"))  # no call yet
    now = {
        **now,
        T2: eff(cc(State.WARMING, tid=T2, asof=d(14), confirmation=True), ingest_fresh=False),
    }
    out = run(
        theses=theses,
        now=now,
        baseline=base,
        scope=Scope(live_cards=7, not_yet_created=1, roster_fallback=2),
    ).counts
    assert (out.cards_counted, out.live_cards, out.not_yet_created, out.roster_fallback) == (
        6,
        7,
        1,
        2,
    )
    assert (out.cards_recorded_on_night, out.cards_carried, out.cards_no_call) == (4, 1, 1)
    assert out.cards_recorded_on_night + out.cards_carried + out.cards_no_call == out.cards_counted
    assert (out.cards_armed, out.cards_warming, out.cards_managing, out.cards_incubating) == (
        1,
        2,
        1,
        1,
    )
    assert (out.cards_ingest_not_fresh, out.cards_reconstructed) == (1, 0)


def test_every_section_is_present_and_says_nothing_when_there_is_nothing():
    out = run(latest=None, previous=None)
    assert out.last_night.no_baseline and out.last_night.cards == ()
    assert out.positions == ()
    assert out.due.items == () and out.due.catalysts == 0 and out.due.passed_since is None
    assert out.insider.rows == () and out.insider.no_baseline
    assert out.tape.rows == () and out.tape.counts.names == 0
    assert (out.forming.warming, out.forming.incubating, out.forming.key_turned) == (0, 0, ())
    assert out.counts.cards_counted == 0 and out.nights.latest is None
    assert out.flagged == ()


def test_a_page_with_a_night_pair_but_no_change_has_every_section_empty_and_present():
    theses = [thesis(T1, "Card A")]
    same = cc(State.WARMING, tid=T1)
    out = run(
        theses=theses,
        now={T1: eff(same)},
        baseline={T1: eff(same.model_copy(update={"asof": PREV}))},
    )
    assert not out.last_night.no_baseline and out.last_night.cards == ()
    assert out.positions == () and out.due.items == () and out.tape.rows == ()
    assert out.forming.warming == 1


# ---------------------------------------------------------------------------------------------------------
# the router off one cached read
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    calls: list[tuple] = []

    def fake_read(conn, asof, known_at, clock, origin=None):
        calls.append((asof, clock))
        return daily_digest(
            inputs(asof=asof, clock=clock, theses=[thesis(T1, "Card A")], origin=origin)
        )

    monkeypatch.setattr(router_mod, "read_digest", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    CACHE.clear()
    try:
        yield TestClient(app), calls
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()


def test_the_router_serves_every_section_from_one_cached_read(client):
    http, calls = client
    first = http.get("/dashboards/daily-digest", params={"asof": "2026-09-17"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) >= {
        "asof",
        "clock",
        "nights",
        "last_night",
        "positions",
        "due",
        "insider",
        "tape",
        "forming",
        "counts",
        "flagged",
        "cache",
        "compute_ms",
    }
    assert (body["cache"], body["clock"], body["asof"]) == ("computed", "public", "2026-09-17")
    again = http.get("/dashboards/daily-digest", params={"asof": "2026-09-17"}).json()
    assert again["cache"] == "hit" and len(calls) == 1  # one read for both requests
    http.get("/dashboards/daily-digest", params={"asof": "2026-09-18"})
    assert len(calls) == 2  # another as-of is another key


def test_the_router_counts_a_live_view_from_the_action_day_and_a_past_one_from_its_asof(
    client, pin_market_clock
):
    """F2: Tuesday 09:00 is Monday's night. The default view of that night is live, so what is due this week counts
    from Tuesday, the action day (an item dated Tuesday is 'today'); a past view counts from itself.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    pin_market_clock(datetime(2026, 10, 27, 9, 0, tzinfo=ZoneInfo("America/New_York")))
    http, _ = client
    live = http.get("/dashboards/daily-digest", params={"asof": "2026-10-26"}).json()
    assert (live["clock"], live["due"]["start"]) == ("record", "2026-10-27")
    past = http.get("/dashboards/daily-digest", params={"asof": "2026-10-23"}).json()
    assert (past["clock"], past["due"]["start"]) == ("public", "2026-10-23")
    keys = {k for k in CACHE._entries}
    assert ("daily-digest", ("record", date(2026, 10, 27)), date(2026, 10, 26)) in keys
    assert ("daily-digest", ("public", date(2026, 10, 23)), date(2026, 10, 23)) in keys


def test_the_router_requires_an_asof_and_takes_no_other_parameter(client):
    http, _ = client
    assert http.get("/dashboards/daily-digest").status_code == 422
    route = next(r for r in app.routes if getattr(r, "path", "") == "/dashboards/daily-digest")
    assert {p.name for p in route.dependant.query_params} == {"asof"}
    assert route.methods == {"GET"}


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real calls log, Form 4 tape, price tape and decision log (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

INGESTED = datetime(
    2026, 11, 1, 18, 13, tzinfo=UTC
)  # our own ingest instant, after every as-of below
THU, FRI, MON = d(17), d(18), d(21)


def _security(db, ticker: str, *, etf: bool = False, cik: str | None = CIK) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01", "etf" if etf else "equity"),
        )
    return sid


def _bar(db, sid: UUID, day: date, close: float) -> None:
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
            "recorded_at": datetime.combine(day, time.min, tzinfo=UTC),
        },
    )


def _tape(db, sid: UUID, base: float) -> None:
    """A close of ``base + days since 2026-09-01`` on every weekday through 2026-09-25 (past every as-of below)."""
    for day in weekdays(d(1), d(25)):
        _bar(db, sid, day, base + (day - d(1)).days)


def _card(db, name: str, members: list[tuple[str, UUID]], *, kills=(), catalysts=()) -> UUID:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker=tk, role="the name", security_id=s, segment="Core")
            for tk, s in members
        ],
    )
    thesis_repo.upsert(db, t)
    thesis_repo.set_kill_criteria(
        db,
        t.id,
        [KillCriterion(id=uuid.uuid4(), text=k) for k in kills],
        tenant_id=DEFAULT_TENANT_ID,
    )
    thesis_repo.set_catalysts(db, t.id, list(catalysts), tenant_id=DEFAULT_TENANT_ID)
    with (
        db.cursor() as cur
    ):  # the card existed on every as-of below (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", t.id))
    return t.id


def _record(db, tid: UUID, night: date, state: State, armed=(), **kw) -> None:
    card = cc(
        state,
        tid=tid,
        asof=night,
        armed_members=[mem(s, verdict=Verdict.CORE_ENTRY, confidence=0.5) for s in armed],
        **{k: v for k, v in kw.items() if k not in ("ingest_fresh", "reconstructed")},
    )
    calls_repo.append(
        db,
        card,
        DEFAULT_TENANT_ID,
        ingest_fresh=kw.get("ingest_fresh"),
        reconstructed=kw.get("reconstructed", False),
    )


def _form4(db, sid, n, name, role, code, filed: date, shares=1000, price=4.0):
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": name,
            "insider_role": role,
            "txn_code": code,
            "shares": shares,
            "price": price,
            "usd": shares * price,
            "accession": ACCS[n],
            "txn_seq": 0,
            "valid_from": filed - timedelta(days=1),
            "accepted": datetime.combine(filed, time(21, 0), tzinfo=UTC),
            "recorded_at": INGESTED,
        },
    )


def _decide(db, tid, sid, action, day: date, recorded: datetime, **kw) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_decision (tenant_id, thesis_id, security_id, action, decision_date, shares, "
            "price, voids, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                DEFAULT_TENANT_ID,
                tid,
                sid,
                action,
                day,
                kw.get("shares"),
                kw.get("price"),
                kw.get("voids"),
                recorded,
            ),
        )


def _get(db, asof: date) -> dict:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get("/dashboards/daily-digest", params={"asof": asof.isoformat()})
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def world(db):
    """Card A: Warming on 09-16, Armed on AAA on 09-17 and 09-18. Card B: first recorded (Armed on BBB) on 09-22, AFTER
    the Thursday and Friday as-ofs. Context and Bench are not used here. A price tape for every name and IWM runs past
    every as-of."""
    aaa, bbb = _security(db, "AAA"), _security(db, "BBB")
    iwm = _security(db, "IWM", etf=True, cik=None)
    for sid, base in ((aaa, 20.0), (bbb, 40.0), (iwm, 200.0)):
        _tape(db, sid, base)
    card_a = _card(db, "Card A", [("AAA", aaa)])
    card_b = _card(db, "Card B", [("BBB", bbb)])
    _record(db, card_a, d(16), State.WARMING)
    _record(db, card_a, d(17), State.ARMED, [aaa])
    _record(db, card_b, d(22), State.ARMED, [bbb])
    db.commit()
    return {"aaa": aaa, "bbb": bbb, "iwm": iwm, "a": card_a, "b": card_b}


def test_db_a_card_armed_after_the_asof_is_not_in_section_one_then(db, world):
    thursday = _get(db, THU)
    assert [c["thesis_name"] for c in thursday["last_night"]["cards"]] == ["Card A"]
    (card,) = thursday["last_night"]["cards"]
    assert (card["tier"], card["state"], card["previous_state"]) == ("armed", "armed", "warming")
    assert card["headline"]["tone"] == "accent"
    assert [(ln["change"], ln["ticker"]) for ln in card["lines"]] == [("armed", "AAA")]
    # Card B has no recorded call by then: counted, never dropped, and not in any section
    assert thursday["counts"]["cards_no_call"] == 1 and thursday["counts"]["cards_counted"] == 2
    assert [r["thesis_name"] for r in thursday["tape"]["rows"]] == ["Card A"]
    assert (thursday["nights"]["latest"], thursday["nights"]["previous"]) == (
        "2026-09-17",
        "2026-09-16",
    )
    # on the 22nd Card B's first night is its own: a "new card" line, armed, with its row on the tape
    later = _get(db, d(22))
    names = {c["thesis_name"]: c for c in later["last_night"]["cards"]}
    assert (
        names["Card B"]["headline"]["change"] == "new_card" and names["Card B"]["tier"] == "armed"
    )
    assert {r["thesis_name"] for r in later["tape"]["rows"]} == {"Card A", "Card B"}


def test_db_a_form_4_filed_after_the_asof_is_not_in_section_four_then(db, world):
    aaa = world["aaa"]
    _form4(db, aaa, 1, "Jane Roe", "CEO", "P", THU)  # filed on the night: in (P, N]
    _form4(
        db, aaa, 2, "Sam Poe", "Director", "P", d(16)
    )  # filed on P: not in the span, but a neighbor
    _form4(
        db, aaa, 3, "Ola Ray", "CFO", "P", FRI
    )  # filed AFTER the Thursday as-of (we ingested it later still)
    db.commit()
    thursday = _get(db, THU)["insider"]
    assert [r["accession"] for r in thursday["rows"]] == [ACCS[1]]
    row = thursday["rows"][0]
    assert (row["filed"], row["filer"], row["usd"]) == ("2026-09-17", "Jane Roe", 4000.0)
    assert (
        row["cluster"]["buyers"] == 2
    )  # Sam Poe filed the day before; Ola Ray's filing does not exist yet
    assert (thursday["span_start"], thursday["span_end"]) == ("2026-09-16", "2026-09-17")
    # on Friday morning the page still reads the Thursday night against Wednesday: Friday's filing is after N
    friday = _get(db, FRI)["insider"]
    assert [r["accession"] for r in friday["rows"]] == [ACCS[1]]
    assert friday["rows"][0]["cluster"]["buyers"] == 2


def test_db_a_context_or_bench_name_is_never_a_row_in_the_insider_or_tape_sections(db):
    aaa, ctx = _security(db, "AAA"), _security(db, "CTX")
    iwm = _security(db, "IWM", etf=True, cik=None)
    for sid, base in ((aaa, 20.0), (ctx, 30.0), (iwm, 200.0)):
        _tape(db, sid, base)
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Card A",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker="AAA", role="x", security_id=aaa, segment="Core"),
            BasketMember(ticker="CTX", role="x", security_id=ctx, segment="Context"),
        ],
    )
    thesis_repo.upsert(db, t)
    with db.cursor() as cur:
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", t.id))
    _record(db, t.id, d(16), State.WARMING)
    _record(db, t.id, d(17), State.ARMED, [aaa])
    _form4(db, aaa, 1, "Jane Roe", "CEO", "P", THU)
    _form4(db, ctx, 2, "Sam Poe", "Director", "P", THU)
    db.commit()
    body = _get(db, THU)
    assert [r["ticker"] for r in body["insider"]["rows"]] == ["AAA"]
    assert body["insider"]["counts"]["silent_rows_left_out"] == 1
    assert [r["ticker"] for r in body["tape"]["rows"]] == ["AAA"]


def _managing_world(db):
    """Two Managing cards on one held name HHH: M1 with a fill logged before every as-of below (100 shares at 48),
    M2 with a fill dated the 15th but LOGGED on the 20th."""
    hhh = _security(db, "HHH")
    iwm = _security(db, "IWM", etf=True, cik=None)
    for sid, base in ((hhh, 40.0), (iwm, 200.0)):
        _tape(db, sid, base)
    m1 = _card(db, "Card M1", [("HHH", hhh)], kills=("the license is lost",))
    m2 = _card(db, "Card M2", [("HHH", hhh)])
    for tid in (m1, m2):
        _record(db, tid, d(16), State.ARMED, [hhh])
        _record(db, tid, d(17), State.MANAGING, [hhh], exit_by=d(24))
    _decide(
        db, m1, hhh, "take", d(15), datetime(2026, 9, 15, 20, 0, tzinfo=UTC), shares=100, price=48
    )
    _decide(
        db, m2, hhh, "take", d(15), datetime(2026, 9, 20, 15, 0, tzinfo=UTC), shares=50, price=47
    )
    db.commit()
    return {"hhh": hhh, "m1": m1, "m2": m2}


def test_db_a_fill_logged_after_the_asof_is_not_a_position_then(db):
    w = _managing_world(db)
    thursday = _get(db, THU)
    rows = {p["thesis_name"]: p for p in thursday["positions"]}
    assert set(rows) == {"Card M1", "Card M2"}  # both are Managing in the record
    m1, m2 = rows["Card M1"], rows["Card M2"]
    assert (m1["has_position"], m1["ticker"], m1["opened_on"]) == (True, "HHH", "2026-09-15")
    assert (m1["logged_price"], m1["shares"], m1["fill_usd"]) == (48.0, 100.0, 4800.0)
    assert m1["since_fill"]["basis"] == "logged_price"
    # HHH closes 40 + days since the 1st: 54 on the 15th, 55 on the 16th, 56 on the 17th
    assert m1["since_fill"]["last_close"] == 56.0
    assert m1["since_fill"]["ret_pct"] == round((56.0 / 48.0 - 1) * 100, 2)
    assert m1["kill_criteria"] == ["the license is lost"]
    assert (m1["hold"]["exit_by"], m1["hold"]["sessions_left"]) == ("2026-09-24", 5)
    # M2's fill was logged on the 20th: on Thursday the 17th it is no position, and the card keeps its line
    assert m2["has_position"] is False and m2["opened_on"] is None and m2["fill_usd"] is None
    assert m2["since_fill"]["status"] == "no_fill"
    # once logged (on a Monday as-of, after the 20th) it is a position, dated the 15th
    monday = {p["thesis_name"]: p for p in _get(db, MON)["positions"]}
    assert monday["Card M2"]["has_position"] is True
    assert (monday["Card M2"]["opened_on"], monday["Card M2"]["shares"]) == ("2026-09-15", 50.0)
    assert w["hhh"]


def test_db_a_void_logged_after_the_asof_does_not_yet_close_the_position(db):
    w = _managing_world(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM operator_decision WHERE thesis_id = %s AND action = 'take'", (w["m1"],)
        )
        take = cur.fetchone()["id"]
    _decide(
        db, w["m1"], w["hhh"], "void", d(16), datetime(2026, 9, 19, 12, 0, tzinfo=UTC), voids=take
    )
    db.commit()
    assert {p["thesis_name"]: p["has_position"] for p in _get(db, THU)["positions"]}[
        "Card M1"
    ] is True
    assert {p["thesis_name"]: p["has_position"] for p in _get(db, MON)["positions"]}[
        "Card M1"
    ] is False


def test_db_a_monday_reads_fridays_night_against_thursdays_and_says_so(db, world):
    _record(db, world["a"], FRI, State.ARMED, [world["aaa"]])  # an unchanged re-record is no change
    db.commit()
    monday = _get(db, MON)
    n = monday["nights"]
    assert (n["latest"], n["previous"], n["days_after_night"], n["sessions_between"]) == (
        "2026-09-18",
        "2026-09-17",
        3,
        1,
    )
    assert monday["last_night"]["cards"] == []  # Friday's row matches Thursday's: nothing changed
    assert (
        monday["counts"]["cards_recorded_on_night"] == 1 and monday["counts"]["cards_no_call"] == 1
    )


def test_db_the_default_view_on_a_monday_morning_is_fridays_night_zero_days_past_it(
    db, world, monkeypatch
):
    """F1: the app's default as-of is the current night (``domain.market_time.current_night``, what ``GET /clock``
    serves the dial), so at 09:00 ET on Monday the page opens on Friday's night — read live — and reads it against
    Thursday's, 0 days past it (the pre-F1 Monday-date view, above, said 3)."""
    from domain import market_time
    from domain.settings import get_settings

    monkeypatch.delenv("ALPHADECK_CRON_AT", raising=False)
    get_settings.cache_clear()
    monday_morning = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)  # 09:00 EDT
    monkeypatch.setattr(
        market_time,
        "market_now",
        lambda tz=None: monday_morning.astimezone(tz or market_time.market_tz()),
    )
    _record(db, world["a"], FRI, State.ARMED, [world["aaa"]])
    db.commit()
    night = market_time.current_night()
    assert night == FRI and market_time.serve_view(night) == (None, "record")
    n = _get(db, night)["nights"]
    assert (n["latest"], n["previous"], n["days_after_night"], n["sessions_between"]) == (
        "2026-09-18",
        "2026-09-17",
        0,
        1,
    )


def test_db_a_single_recorded_night_has_no_earlier_one_and_no_section_one_or_four(db):
    aaa = _security(db, "AAA")
    iwm = _security(db, "IWM", etf=True, cik=None)
    _tape(db, aaa, 20.0)
    _tape(db, iwm, 200.0)
    card = _card(db, "Card A", [("AAA", aaa)])
    _record(db, card, d(17), State.ARMED, [aaa])
    _form4(db, aaa, 1, "Jane Roe", "CEO", "P", THU)
    db.commit()
    body = _get(db, THU)
    assert body["nights"]["previous"] is None and body["nights"]["latest"] == "2026-09-17"
    assert body["last_night"]["no_baseline"] is True and body["last_night"]["cards"] == []
    assert body["insider"]["no_baseline"] is True and body["insider"]["rows"] == []
    assert [r["ticker"] for r in body["tape"]["rows"]] == ["AAA"]  # the tape needs no baseline


def test_db_the_tape_reads_the_closes_public_by_the_asof_and_none_after(db, world):
    thursday = _get(db, THU)
    (row,) = thursday["tape"]["rows"]
    # AAA closes 20 + days since the 1st: 35 on the 16th, 36 on the 17th; the tape runs on to the 25th
    ls = row["last_session"]
    assert (ls["session"], ls["previous_session"]) == ("2026-09-17", "2026-09-16")
    assert ls["ret_pct"] == round((36.0 / 35.0 - 1) * 100, 2)
    assert thursday["tape"]["iwm_pct"] == round((216.0 / 215.0 - 1) * 100, 2)
    sa = row["since_arm"]
    assert (sa["entry_date"], sa["last_date"]) == ("2026-09-17", "2026-09-17")
    assert row["arm_night"] == "2026-09-17" and row["sessions_since"] == 0


def test_db_an_empty_log_gives_an_honest_page_with_every_section(db):
    card = _card(db, "Card A", [])
    archived = _card(db, "Card Z", [])
    thesis_repo.set_archived(db, archived, True)
    db.commit()
    body = _get(db, THU)
    assert body["nights"]["latest"] is None
    assert body["positions"] == [] and body["tape"]["rows"] == [] and body["due"]["items"] == []
    assert body["last_night"]["cards"] == [] and body["insider"]["rows"] == []
    assert (body["counts"]["cards_counted"], body["counts"]["cards_no_call"]) == (1, 1)
    assert card


def test_db_the_ingest_stamp_and_reconstruction_of_the_row_in_effect_ride_with_the_card(db):
    aaa = _security(db, "AAA")
    iwm = _security(db, "IWM", etf=True, cik=None)
    _tape(db, aaa, 20.0)
    _tape(db, iwm, 200.0)
    card = _card(db, "Card A", [("AAA", aaa)])
    _record(db, card, d(16), State.WARMING)
    _record(db, card, d(17), State.ARMED, [aaa], ingest_fresh=False, reconstructed=True)
    db.commit()
    body = _get(db, THU)
    (c,) = body["last_night"]["cards"]
    assert (c["ingest_fresh"], c["reconstructed"]) == (False, True)
    assert (body["counts"]["cards_ingest_not_fresh"], body["counts"]["cards_reconstructed"]) == (
        1,
        1,
    )
