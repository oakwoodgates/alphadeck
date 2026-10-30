"""Forming Quietly (``docs/dashboards/forming-quietly.md``): the pure rules on hand-built rows (the run walk, the row
set, the recorded keys / missing lines / names carried verbatim, the quiet signals, the adoption slope, the default
order), the router's reads (the heavy read stubbed, and the real ``read_forming`` with its DB reads stubbed), and
DB-backed reads through the real ``calls`` log, the roster, the price tape, the Form 4 table and the term-mention
months: a card that turned Warming after the as-of is not a Warming row then, a term month dated after the as-of is not
in the slope, and an insider buy accepted after the as-of is not read.

Every card, company and ticker below is a neutral fixture name.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import forming_quietly as router_mod
from dashboards.baskets import CardsAsOf, FlaggedCompany
from dashboards.cache import CACHE
from dashboards.filings import source_url
from dashboards.forming_quietly import (
    MEMBERS,
    Adoption,
    FormingRow,
    LogRow,
    Quiet,
    Reading,
    Recorded,
    adoption_slope,
    breadth_of,
    build_row,
    card_state_counts,
    closed_months,
    default_order,
    forming,
    month_last_day,
    quiet_of,
    reading_of,
    row_cards,
    run_start,
    winning_nights,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Grade, Kind, State, TermTier, Verdict
from domain.signal import Provenance
from domain.thesis import BasketMember, Segment, TermSetEntry, Thesis
from repositories import calls_repo, thesis_repo
from signals.display import insider_flow, relative_strength, theme_breadth, vcp
from signals.display.base import DisplayBasis, DisplayHeadline, DisplayMetric, DisplaySignal

ASOF = date(2026, 10, 28)  # a Wednesday
A, B, C, D = (uuid.uuid4() for _ in range(4))
T1, T2, T3, T4 = (uuid.uuid4() for _ in range(4))
TICKERS = {A: "AAA", B: "BBB", C: "CCC", D: "DDD"}


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


def utc(y: int, m: int, day: int, hour: int = 12) -> datetime:
    return datetime(y, m, day, hour, tzinfo=timezone.utc)


def ticker_of(sid: UUID) -> str:
    return TICKERS.get(sid, str(sid))


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def key(label: str, turned: bool = False, detail: str | None = None) -> KeyState:
    return KeyState(turned=turned, label=label, detail=detail)


def trig(
    sid: UUID,
    label: str = "8-day breakout",
    kind: Kind = Kind.TECHNICAL_BREAKOUT,
    on: date | None = date(2026, 10, 20),
    **kw,
) -> TriggerRef:
    return TriggerRef(label=label, kind=kind, security_id=sid, event_date=on, **kw)


def cc(state: State, *, tid: UUID = T1, asof: date = ASOF, **kw) -> CallCard:
    kw.setdefault("key_conviction", key("Conviction"))
    kw.setdefault("key_confirmation", key("Confirmation"))
    return CallCard(
        thesis_id=tid, asof=asof, state=state, verdict=Verdict.NOT_YET, expression="", **kw
    )


def rec(card: CallCard, *, seq: int = 1, reconstructed: bool = False) -> Recorded:
    return Recorded(asof=card.asof, seq=seq, reconstructed=reconstructed, card=card)


def log(
    asof: date, state: State = State.WARMING, *, seq: int = 1, reconstructed: bool = False
) -> LogRow:
    return LogRow(asof, seq, reconstructed, state)


def _sig(kind, metrics=(), events=(), headline=None, bars_used=None):
    return DisplaySignal(
        kind=kind,
        label=kind,
        metrics=list(metrics),
        events=list(events),
        headline=headline,
        basis=DisplayBasis(source="fact_price_eod", bars_used=bars_used),
    )


def _metric(k, v=None):
    return DisplayMetric(key=k, label=k, value=v)


def bars(n: int, close=lambda i: 10.0 + i * 0.1) -> list[dict]:
    return [{"d": ASOF - timedelta(days=n - 1 - i), "close": close(i)} for i in range(n)]


def coil_bars() -> list[dict]:
    """70 bars of rising tape, then a 60-bar base in three 20-bar segments whose depths contract (12% -> 8% -> 4%
    of their own peak) with the close holding above a rising 50-day average: the vcp member's coil.
    """
    out = []
    for i in range(70):
        c = 80.0 + i * 0.15
        out.append({"high": c * 1.01, "low": c * 0.99, "close": c})
    for k, depth in enumerate((0.12, 0.08, 0.04)):
        peak = 100.0 + k * 3
        for j in range(20):
            lo = peak * (1 - depth)
            c = lo + (peak - lo) * (0.6 + 0.4 * (j / 19))
            out.append(
                {
                    "high": peak if j == 10 else c * 1.0001,
                    "low": lo if j == 3 else c * 0.9999,
                    "close": c,
                }
            )
    for i, b in enumerate(out):
        b["d"] = ASOF - timedelta(days=len(out) - 1 - i)
    return out


# ---------------------------------------------------------------------------------------------------------
# 1. the run walk
# ---------------------------------------------------------------------------------------------------------


def test_the_run_start_is_the_first_night_of_the_current_unbroken_run():
    rows = [
        log(d(19), State.INCUBATING),
        log(d(20), State.INCUBATING),
        log(d(21)),
        log(d(22)),
        log(d(23)),
    ]
    got = run_start(rows, State.WARMING)
    assert (got.night, got.reconstructed, got.nights) == (d(21), False, 3)
    # the input order does not matter: the walk sorts the nights itself
    assert run_start(list(reversed(rows)), State.WARMING).night == d(21)
    # the Incubating run before it is a different run
    assert run_start(rows[:2], State.INCUBATING).night == d(19)


def test_a_run_broken_by_another_state_starts_over():
    rows = [log(d(19)), log(d(20), State.INCUBATING), log(d(21)), log(d(22))]
    assert run_start(rows, State.WARMING).night == d(21)
    armed_between = [log(d(19)), log(d(20), State.ARMED), log(d(21)), log(d(22))]
    assert run_start(armed_between, State.WARMING).night == d(21)


def test_a_same_night_re_run_with_a_higher_seq_is_the_night_that_stood():
    # the 22nd's first row was Warming, its re-run (seq 2) Incubating: the night reads Incubating, so the Warming
    # run ends on the 21st and the card is Incubating from the 22nd
    broken = [log(d(21)), log(d(22), seq=1), log(d(22), State.INCUBATING, seq=2)]
    assert run_start(broken, State.WARMING) is None
    assert run_start(broken, State.INCUBATING).night == d(22)
    # the other way round: Incubating first, the re-run Warming, so the 22nd is the Warming run's first night
    healed = [log(d(21), State.INCUBATING), log(d(22), State.INCUBATING, seq=1), log(d(22), seq=2)]
    assert run_start(healed, State.WARMING).night == d(22)
    assert [(r.asof, r.seq) for r in winning_nights(broken)] == [(d(22), 2), (d(21), 1)]


def test_a_reconstructed_first_night_is_marked_and_a_later_one_does_not_mark_it():
    assert run_start([log(d(21), reconstructed=True), log(d(22))], State.WARMING).reconstructed
    assert not run_start([log(d(21)), log(d(22), reconstructed=True)], State.WARMING).reconstructed


def test_no_rows_or_a_newest_night_in_another_state_has_no_run():
    assert run_start([], State.WARMING) is None
    assert run_start([log(d(21)), log(d(22), State.ARMED)], State.WARMING) is None


# ---------------------------------------------------------------------------------------------------------
# 2. who is a row
# ---------------------------------------------------------------------------------------------------------

NAMES = {T1: "Card A", T2: "Card B", T3: "Card C", T4: "Card D"}


def test_warming_and_incubating_cards_are_rows_armed_and_managing_are_counted_never_rows():
    recorded = {
        T1: rec(cc(State.WARMING, tid=T1)),
        T2: rec(cc(State.INCUBATING, tid=T2)),
        T3: rec(cc(State.ARMED, tid=T3)),
    }
    assert row_cards(NAMES, recorded) == [T1, T2]
    more = {**recorded, T4: rec(cc(State.MANAGING, tid=T4))}
    assert row_cards(NAMES, more) == [T1, T2]
    assert card_state_counts(NAMES, more) == {
        "armed": 1,
        "warming": 1,
        "managing": 1,
        "incubating": 1,
        "no_call": 0,
    }


def test_a_card_with_no_recorded_call_is_counted_as_no_call_yet_and_never_a_row():
    recorded = {T1: rec(cc(State.WARMING, tid=T1))}
    assert row_cards(NAMES, recorded) == [T1]
    counts = card_state_counts(NAMES, recorded)
    assert (counts["warming"], counts["no_call"]) == (1, 3)


def test_the_rows_are_by_card_name_before_the_default_order_is_applied():
    names = {T1: "Zed", T2: "alpha"}
    recorded = {T1: rec(cc(State.WARMING, tid=T1)), T2: rec(cc(State.WARMING, tid=T2))}
    assert row_cards(names, recorded) == [T2, T1]


# ---------------------------------------------------------------------------------------------------------
# 3. the recorded keys, missing lines and names, carried as recorded
# ---------------------------------------------------------------------------------------------------------

NO_QUIET = quiet_of([], {}, None)
NO_ADOPTION = adoption_slope([], [], ASOF)


def _url(ref: str, cik: str | None) -> str | None:
    return f"https://example.test/{cik}/{ref}" if cik else None


def _build(card: CallCard, runs=(), **kw) -> FormingRow:
    return build_row(
        card.thesis_id,
        "Card A",
        rec(card),
        list(runs),
        kw.pop("quiet", NO_QUIET),
        kw.pop("adoption", NO_ADOPTION),
        asof=ASOF,
        ticker_of=ticker_of,
        ciks=kw.pop("ciks", {A: "0000111111", B: None}),
        url_of=_url,
    )


def test_the_keys_and_the_missing_lines_are_carried_verbatim():
    detail = "A conviction entry trigger fired — the 'why now is real'."
    missing = [
        "Volume-confirmed breakout (the confirmation key)",
        "Risk must clear before arming: dilution overhang",
    ]
    card = cc(
        State.WARMING,
        key_conviction=key("Conviction", True, detail),
        key_confirmation=key("Confirmation", False, "No confirmation yet."),
        missing=missing,
    )
    row = _build(card)
    assert row.missing == tuple(missing)
    assert (row.key_conviction.turned, row.key_conviction.label, row.key_conviction.detail) == (
        True,
        "Conviction",
        detail,
    )
    assert (row.key_confirmation.turned, row.key_confirmation.detail) == (
        False,
        "No confirmation yet.",
    )
    assert row.state == "warming" and row.recorded_on == ASOF


def test_the_names_behind_the_keys_group_each_securitys_triggers_with_their_source_links():
    prov = Provenance(source="form4", ref="0001234567-26-000001")
    card = cc(
        State.WARMING,
        triggers_fired=[
            trig(B, "insider cluster", Kind.INSIDER, grade=Grade.CORE, sources=[prov]),
            trig(
                A,
                "8-day breakout",
                sources=[Provenance(source="price", ref="price:AAA:2026-10-20")],
            ),
            trig(A, "insider buy", Kind.INSIDER, sources=[prov]),
        ],
    )
    chips = _build(card).names
    assert [(c.ticker, len(c.triggers)) for c in chips] == [("AAA", 2), ("BBB", 1)]
    aaa = chips[0]
    assert {t.kind for t in aaa.triggers} == {Kind.TECHNICAL_BREAKOUT, Kind.INSIDER}
    by_label = {t.label: t for t in aaa.triggers}
    # a source links through the trigger's OWN issuer cik; a price reference or a name with no cik has no link
    assert (
        by_label["insider buy"].sources[0].url
        == "https://example.test/0000111111/0001234567-26-000001"
    )
    assert (
        by_label["8-day breakout"].sources[0].url
        == "https://example.test/0000111111/price:AAA:2026-10-20"
    )
    assert chips[1].triggers[0].sources[0].url is None
    assert chips[1].triggers[0].grade == Grade.CORE
    assert aaa.triggers[0].event_date == date(2026, 10, 20)


def test_a_source_links_by_the_evidence_ledgers_rule():
    prov = Provenance(source="form4", ref="0001234567-26-000001")
    row = build_row(
        T1,
        "Card A",
        rec(cc(State.WARMING, triggers_fired=[trig(A, "x", Kind.INSIDER, sources=[prov])])),
        [],
        NO_QUIET,
        NO_ADOPTION,
        asof=ASOF,
        ticker_of=ticker_of,
        ciks={A: "0000320193"},
        url_of=source_url,
    )
    assert row.names[0].triggers[0].sources[0].url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000123456726000001/"
    )


def test_the_watch_names_and_the_risk_signals_ride_along():
    card = cc(
        State.WARMING,
        watch_members=[
            MemberCall(security_id=C),
            MemberCall(security_id=C),
            MemberCall(security_id=B),
        ],
        risk_signals=[trig(D, "convertible overhang", Kind.DILUTION_RISK)],
    )
    row = _build(card)
    assert [w.ticker for w in row.watch] == ["BBB", "CCC"]  # each once, by ticker
    (risk,) = row.risks
    assert (risk.label, risk.kind, risk.ticker) == (
        "convertible overhang",
        Kind.DILUTION_RISK,
        "DDD",
    )


def test_days_in_state_counts_calendar_days_from_the_runs_first_night_to_the_asof():
    runs = [log(d(20), State.INCUBATING), log(d(24), reconstructed=True), log(d(27)), log(d(28))]
    row = _build(cc(State.WARMING), runs)
    assert (row.run_start, row.days_in_state, row.run_nights) == (d(24), 4, 3)
    assert row.run_reconstructed is True
    assert _build(cc(State.WARMING), [log(d(28))]).days_in_state == 0
    bare = _build(cc(State.WARMING), [])
    assert (bare.run_start, bare.days_in_state, bare.run_nights) == (None, None, 0)


# ---------------------------------------------------------------------------------------------------------
# 4. the quiet signals
# ---------------------------------------------------------------------------------------------------------


def _ins(buys, buyers):
    return {
        insider_flow.MEMBER_NAME: _sig(
            "ins",
            [_metric("buy_count_30d", float(buys)), _metric("distinct_buyers_30d", float(buyers))],
        )
    }


def test_insider_buying_lights_at_one_buy_and_a_cluster_word_comes_at_two_distinct_buyers():
    names = [(A, "AAA"), (B, "BBB"), (C, "CCC")]
    readings = {
        A: reading_of(_ins(3, 2)),
        B: reading_of(_ins(1, 1)),
        C: reading_of(_ins(0, 0)),
    }
    q = quiet_of(names, readings, None)
    assert [(n.ticker, n.buys_30d, n.buyers_30d, n.cluster) for n in q.insider] == [
        ("AAA", 3, 2, True),
        ("BBB", 1, 1, False),
    ]
    assert (q.names, q.read, q.lit) == (3, 3, True)


def test_a_quiet_insider_tape_is_a_stated_zero_and_no_form_4_at_all_is_counted_not_dropped():
    quiet = insider_flow.compute(
        [{"valid_from": ASOF - timedelta(days=60), "txn_code": "P", "insider_name": "X"}], ASOF
    )
    r_quiet = reading_of({insider_flow.MEMBER_NAME: quiet})
    assert (r_quiet.buys_30d, r_quiet.buyers_30d) == (0, 0)
    r_none = reading_of({insider_flow.MEMBER_NAME: insider_flow.compute([], ASOF)})
    assert (r_none.buys_30d, r_none.buyers_30d) == (None, None)
    q = quiet_of([(A, "AAA"), (B, "BBB")], {A: r_quiet, B: r_none}, None)
    assert q.insider == () and q.no_form4 == 1 and q.read == 2 and q.lit is False


def test_coiling_and_leading_read_the_members_own_headlines_and_a_missing_member_is_counted():
    coil = _sig(
        "vcp",
        [_metric("contraction", 0.4)],
        headline=DisplayHeadline(key="coiling", label="x"),
    )
    not_coil = _sig("vcp", [_metric("contraction", 0.9)])
    lead = _sig(
        "rs",
        events=[],
        headline=DisplayHeadline(key="leading", label="x"),
        bars_used=relative_strength.MIN_BARS,
    )
    thin = _sig("rs", headline=DisplayHeadline(key="leading", label="x"), bars_used=3)
    readings = {
        A: reading_of({vcp.MEMBER_NAME: coil, relative_strength.MEMBER_NAME: lead}),
        B: reading_of({vcp.MEMBER_NAME: not_coil, relative_strength.MEMBER_NAME: thin}),
        C: reading_of({}),
    }
    q = quiet_of([(A, "AAA"), (B, "BBB"), (C, "CCC")], readings, None)
    assert [(n.ticker, n.contraction) for n in q.coiling] == [("AAA", 0.4)]
    assert [n.ticker for n in q.leading] == ["AAA"]
    # BBB's thin relative strength gives no 13-week verdict (never "not leading"); CCC has no reading from either
    assert (q.coiling_no_reading, q.leading_no_reading) == (1, 2)
    assert q.lit is True


def test_the_real_members_keys_parse_so_a_renamed_key_fails_here():
    """Off the three members' own pure computes over synthetic tapes (no stub of a key name)."""
    climb = bars(260)
    flat = bars(260, close=lambda i: 200.0)
    buys = [
        {"valid_from": ASOF - timedelta(days=3), "txn_code": "P", "insider_name": "X"},
        {"valid_from": ASOF - timedelta(days=5), "txn_code": "P", "insider_name": "Y"},
    ]
    got = reading_of(
        {
            insider_flow.MEMBER_NAME: insider_flow.compute(buys, ASOF),
            vcp.MEMBER_NAME: vcp.compute(coil_bars(), ASOF),
            relative_strength.MEMBER_NAME: relative_strength.compute(
                climb, {"SPY": flat, "IWM": flat}, ASOF
            ),
        }
    )
    assert (got.buys_30d, got.buyers_30d) == (2, 2)
    assert got.coiling is True and got.contraction == pytest.approx(0.334, abs=0.001)
    assert got.rs_leading is True and got.rs_vs == ("SPY", "IWM")
    # a flat tape is neither coiling nor leading, but it is READ (a verdict, not a gap)
    quiet = reading_of(
        {
            vcp.MEMBER_NAME: vcp.compute(flat, ASOF),
            relative_strength.MEMBER_NAME: relative_strength.compute(
                flat, {"SPY": flat, "IWM": flat}, ASOF
            ),
        }
    )
    assert quiet.coiling is False and quiet.rs_leading is False


def test_a_flagged_name_is_left_out_of_every_reading_and_counted_and_listed():
    names = [(A, "AAA"), (B, "BBB")]
    readings = {
        A: reading_of(_ins(2, 2)),
        B: reading_of(
            {}, "mixed-scale price history"
        ),  # never read: even its insider buying stays out
    }
    q = quiet_of(names, readings, None, unresolved=1)
    assert (q.names, q.read, q.unresolved) == (2, 1, 1)
    assert q.flagged == (FlaggedCompany(B, "BBB", "mixed-scale price history"),)
    assert [n.ticker for n in q.insider] == ["AAA"]
    assert readings[B] == Reading(flag="mixed-scale price history")


def test_breadth_reads_the_members_own_keys_and_only_a_thrust_lights_the_card():
    rising = [50.0 + i * 0.5 for i in range(90)]
    falling = [200.0 - i for i in range(90)]
    plain = theme_breadth.compute([rising, rising, falling])
    b = breadth_of(plain)
    assert (b.above, b.counted, b.thin, b.thrust) == (2, 3, 0, False)
    assert b.pct == pytest.approx(66.67) and b.delta_pts == 0.0
    # plain breadth is shown but does not light a card (no threshold for it is specified)
    assert quiet_of([(A, "AAA")], {A: Reading()}, plain).lit is False
    surge = [100.0 - i for i in range(70)] + [30.0 + 4 * i for i in range(20)]
    thrust = theme_breadth.compute([surge, surge])
    tb = breadth_of(thrust)
    assert (tb.above, tb.counted, tb.thrust) == (2, 2, True)
    assert quiet_of([(A, "AAA")], {A: Reading()}, thrust).lit is True
    # a name with too little history is counted as thin, never as below the line
    thin = breadth_of(theme_breadth.compute([rising, [1.0, 2.0]]))
    assert (thin.above, thin.counted, thin.thin) == (1, 1, 1)
    assert breadth_of(None) is None


# ---------------------------------------------------------------------------------------------------------
# 5. the adoption slope
# ---------------------------------------------------------------------------------------------------------


def _m(y: int, mth: int) -> date:
    return date(y, mth, 1)


def trow(term: str, month: date, docs: int, fetched: datetime | None = None) -> dict:
    return {
        "term": term,
        "month": month,
        "documents": docs,
        "fetched_at": fetched
        or datetime.combine(month_last_day(month), time(23), timezone.utc) + timedelta(days=3),
    }


# as-of 2026-10-28: the closed months are Apr, May, Jun (the prior span) and Jul, Aug, Sep (the recent span)
SIX = [_m(2026, m) for m in range(4, 10)]


def test_the_closed_months_never_include_the_asofs_own_month_and_cross_a_year():
    assert closed_months(ASOF) == SIX
    assert closed_months(date(2026, 10, 31))[-1] == _m(
        2026, 9
    )  # the month's last day is not yet closed
    assert closed_months(date(2026, 11, 1))[-1] == _m(2026, 10)
    jan = closed_months(date(2026, 1, 15))
    assert (jan[0], jan[-1]) == (_m(2025, 7), _m(2025, 12))
    assert month_last_day(_m(2026, 2)) == date(2026, 2, 28) and month_last_day(
        _m(2026, 12)
    ) == date(2026, 12, 31)


def test_six_closed_months_give_the_slope_as_a_signed_percent_with_both_spans():
    rows = [trow("alpha", m, n) for m, n in zip(SIX, (10, 10, 10, 20, 20, 20))]
    got = adoption_slope(rows, ["alpha"], ASOF)
    assert got.counted is True and got.percent == 100.0
    assert (got.prior, got.recent) == (30, 60)
    assert (got.prior_from, got.prior_to) == (_m(2026, 4), _m(2026, 6))
    assert (got.recent_from, got.recent_to) == (_m(2026, 7), _m(2026, 9))
    assert (got.terms_signal, got.terms_counted, got.terms_partial, got.terms_unstored) == (
        1,
        1,
        0,
        0,
    )
    assert got.months_stored == 6 and got.latest_partial is False
    falling = adoption_slope(
        [trow("alpha", m, n) for m, n in zip(SIX, (30, 30, 30, 20, 20, 20))], ["alpha"], ASOF
    )
    assert falling.percent == pytest.approx(-33.3)


def test_documents_sum_over_the_signal_terms_and_a_term_matches_without_regard_to_case():
    rows = [trow("Alpha Term", m, 10) for m in SIX] + [trow("beta", m, 5) for m in SIX[3:]]
    rows += [trow("beta", m, 5) for m in SIX[:3]]
    got = adoption_slope(rows, ["alpha term", "BETA"], ASOF)
    assert got.counted and (got.prior, got.recent, got.percent) == (45, 45, 0.0)
    assert got.terms_counted == 2


def test_five_closed_months_are_not_counted_yet_and_the_card_says_how_many_are_stored():
    rows = [trow("alpha", m, 10) for m in SIX[1:]]  # May through Sep: five months
    got = adoption_slope(rows, ["alpha"], ASOF)
    assert got.counted is False and got.percent is None and got.recent is None
    assert (got.months_stored, got.terms_partial, got.terms_counted) == (5, 1, 0)
    empty = adoption_slope([], ["alpha"], ASOF)
    assert (empty.counted, empty.months_stored, empty.terms_unstored) == (False, 0, 1)
    no_terms = adoption_slope([trow("alpha", m, 1) for m in SIX], [], ASOF)
    assert no_terms.counted is False and no_terms.terms_signal == 0


def test_the_partial_month_never_counts_nor_does_a_month_before_the_six():
    rows = [trow("alpha", m, 10) for m in SIX]
    rows += [trow("alpha", _m(2026, 10), 9999)]  # the as-of's own month: partial
    rows += [trow("alpha", _m(2026, 3), 9999)]  # older than the prior span
    got = adoption_slope(rows, ["alpha"], ASOF)
    assert (got.prior, got.recent, got.percent) == (30, 30, 0.0)
    assert got.months_stored == 6


def test_a_zero_prior_span_has_no_percent_and_the_counts_are_shown():
    rows = [trow("alpha", m, n) for m, n in zip(SIX, (0, 0, 0, 4, 4, 4))]
    got = adoption_slope(rows, ["alpha"], ASOF)
    assert got.counted is True and got.percent is None and (got.prior, got.recent) == (0, 12)
    both = adoption_slope([trow("alpha", m, 0) for m in SIX], ["alpha"], ASOF)
    assert both.counted and both.percent is None and (both.prior, both.recent) == (0, 0)


def test_only_terms_with_all_six_months_are_summed_so_the_slope_compares_like_for_like():
    rows = [trow("alpha", m, 10) for m in SIX]
    rows += [
        trow("beta", m, 100) for m in SIX[3:]
    ]  # beta has only the recent span: partial, left out
    got = adoption_slope(rows, ["alpha", "beta", "gamma"], ASOF)
    assert (got.prior, got.recent) == (30, 30)
    assert (got.terms_signal, got.terms_counted, got.terms_partial, got.terms_unstored) == (
        3,
        1,
        1,
        1,
    )


def test_a_term_that_is_not_a_signal_term_of_the_card_is_not_counted():
    rows = [trow("alpha", m, 10) for m in SIX] + [trow("broad", m, 500) for m in SIX]
    got = adoption_slope(rows, ["alpha"], ASOF)
    assert (got.prior, got.recent, got.terms_signal) == (30, 30, 1)


def test_a_latest_month_read_before_it_closed_is_flagged():
    sep = _m(2026, 9)
    rows = [trow("alpha", m, 10) for m in SIX[:-1]]
    rows.append(
        trow("alpha", sep, 10, fetched=utc(2026, 9, 27))
    )  # read three days before September ended
    assert adoption_slope(rows, ["alpha"], ASOF).latest_partial is True
    rows[-1] = trow("alpha", sep, 10, fetched=utc(2026, 10, 3))
    assert adoption_slope(rows, ["alpha"], ASOF).latest_partial is False


# ---------------------------------------------------------------------------------------------------------
# 6. the default order and the payload
# ---------------------------------------------------------------------------------------------------------


def _adoption(percent: float | None, counted: bool = True) -> Adoption:
    return Adoption(
        counted=counted,
        percent=percent,
        recent=10 if counted else None,
        prior=5 if counted else None,
        recent_from=None,
        recent_to=None,
        prior_from=None,
        prior_to=None,
        terms_signal=1,
        terms_counted=1 if counted else 0,
        terms_partial=0,
        terms_unstored=0,
        months_stored=6 if counted else 0,
        latest_partial=False,
    )


def _row(
    name: str, state: str, start: date | None = None, percent: float | None = None, lit=False
) -> FormingRow:
    tid = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    quiet = Quiet(1, 1, 0, (), (), 0, (), 0, (), 0, None, lit)
    return FormingRow(
        key=str(tid),
        thesis_id=tid,
        thesis_name=name,
        state=state,  # type: ignore[arg-type]
        recorded_on=ASOF,
        run_start=start,
        run_nights=1 if start else 0,
        days_in_state=0 if start else None,
        run_reconstructed=False,
        key_conviction=NO_ROW_KEY,
        key_confirmation=NO_ROW_KEY,
        names=(),
        missing=(),
        watch=(),
        risks=(),
        quiet=quiet,
        adoption=_adoption(percent, counted=percent is not None),
    )


NO_ROW_KEY = build_row(
    T1,
    "x",
    rec(cc(State.WARMING)),
    [],
    NO_QUIET,
    NO_ADOPTION,
    asof=ASOF,
    ticker_of=ticker_of,
    ciks={},
    url_of=_url,
).key_conviction


def test_the_default_order_is_warming_by_the_runs_first_night_then_incubating_by_slope_then_the_rest_by_name():
    rows = [
        _row("Card Z", "incubating"),
        _row("Card B", "warming", d(20)),
        _row("Card Y", "incubating", d(10), percent=12.5),
        _row("Card D", "warming", d(26)),
        _row("Card X", "incubating", d(10), percent=40.0),
        _row("Card A", "incubating"),
        _row("Card C", "warming", d(26)),
        _row("Card N", "warming", None),
    ]
    got = [r.thesis_name for r in default_order(rows)]
    # newest run first (a tie by name), a Warming row with no run start last of the Warming rows
    assert got == ["Card C", "Card D", "Card B", "Card N", "Card X", "Card Y", "Card A", "Card Z"]


def test_the_payload_counts_what_it_lists_and_what_it_does_not():
    rows = [
        _row("Card A", "warming", d(26), percent=10.0),
        _row("Card B", "incubating", lit=True),
        _row("Card C", "incubating"),
    ]
    page = forming(
        rows,
        asof=ASOF,
        clock="public",
        counts={"armed": 2, "warming": 1, "managing": 1, "incubating": 2, "no_call": 3},
        live_cards=10,
        cards_counted=9,
        cards_not_yet_created=1,
        roster_fallback_cards=0,
        names_read=7,
        flagged=[FlaggedCompany(A, "AAA", "x")],
    )
    assert (page.cards_armed, page.cards_managing, page.cards_no_call) == (2, 1, 3)
    assert (page.cards_warming, page.cards_incubating, page.cards_incubating_lit) == (1, 2, 1)
    assert (page.cards_with_term_months, page.cards_without_term_months) == (1, 2)
    assert (page.names_read, page.names_flagged) == (7, 1)
    assert page.members == MEMBERS == ("insider_flow_90d", "vcp", "relative_strength")
    assert [r.thesis_name for r in page.rows][0] == "Card A"


# ---------------------------------------------------------------------------------------------------------
# 7. the router (the heavy read stubbed; then the real read_forming with its DB reads stubbed)
# ---------------------------------------------------------------------------------------------------------


def _hand_page():
    card = cc(
        State.WARMING,
        key_conviction=key("Conviction", True, "A conviction entry trigger fired."),
        missing=["Volume-confirmed breakout (the confirmation key)"],
        triggers_fired=[trig(A, "insider cluster", Kind.INSIDER)],
    )
    quiet = quiet_of(
        [(A, "AAA")],
        {A: Reading(buys_30d=2, buyers_30d=2)},
        theme_breadth.compute([[float(i) for i in range(1, 99)]]),
    )
    row = build_row(
        T1,
        "Card A",
        rec(card),
        [log(d(26)), log(d(27)), log(d(28))],
        quiet,
        adoption_slope([trow("alpha", m, 10) for m in SIX], ["alpha"], ASOF),
        asof=ASOF,
        ticker_of=ticker_of,
        ciks={},
        url_of=_url,
    )
    return forming(
        [row],
        asof=ASOF,
        clock="public",
        counts={"armed": 1, "warming": 1, "managing": 0, "incubating": 0, "no_call": 2},
        live_cards=4,
        cards_counted=4,
        cards_not_yet_created=0,
        roster_fallback_cards=0,
        names_read=1,
        flagged=[],
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}

    def fake_read(conn, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        return _hand_page()

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_forming", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_serve_view_pair_and_serves_a_second_request_from_the_cache(
    stub_client,
):
    client, seen = stub_client
    first = client.get("/dashboards/forming-quietly", params={"asof": "2026-06-01"}).json()
    again = client.get("/dashboards/forming-quietly", params={"asof": "2026-06-01"}).json()
    assert (first["cache"], again["cache"]) == ("computed", "hit") and seen["reads"] == 1
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 6, 1) and clock == "public" and known_at is not None

    from domain.market_time import market_today

    client.get("/dashboards/forming-quietly", params={"asof": market_today().isoformat()})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2
    # a different as-of is its own read
    client.get("/dashboards/forming-quietly", params={"asof": "2026-06-02"})
    assert seen["reads"] == 3


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/forming-quietly", params={"asof": "2026-06-01"}).json()
    assert body["clock"] == "public" and body["members"] == list(MEMBERS)
    assert (body["cards_armed"], body["cards_warming"], body["cards_no_call"]) == (1, 1, 2)
    (row,) = body["rows"]
    assert row["thesis_name"] == "Card A" and row["state"] == "warming"
    assert row["run_start"] == "2026-10-26" and row["days_in_state"] == 2
    assert row["key_conviction"] == {
        "turned": True,
        "label": "Conviction",
        "detail": "A conviction entry trigger fired.",
    }
    assert row["missing"] == ["Volume-confirmed breakout (the confirmation key)"]
    assert (
        row["names"][0]["ticker"] == "AAA" and row["names"][0]["triggers"][0]["kind"] == "insider"
    )
    assert row["quiet"]["insider"] == [
        {"security_id": str(A), "ticker": "AAA", "buys_30d": 2, "buyers_30d": 2, "cluster": True}
    ]
    assert row["quiet"]["breadth"]["counted"] == 1 and row["quiet"]["lit"] is True
    assert row["adoption"]["counted"] is True and row["adoption"]["percent"] == 0.0
    assert body["flagged"] == [] and isinstance(body["compute_ms"], int)


def test_the_router_requires_an_asof(stub_client):
    client, _ = stub_client
    assert client.get("/dashboards/forming-quietly").status_code == 422


def test_no_user_facing_text_of_the_wire_says_a_timing_or_a_ranking_word():
    text = str(router_mod.FormingQuietlyOut.model_json_schema())
    assert not re.search(r"early|setting up|about to|best|strongest", text, re.I)


def test_read_forming_threads_one_view_into_every_read_and_runs_only_three_members(monkeypatch):
    """``read_forming`` itself (the DB reads stubbed): the roster, the flags, every company's view, every card's
    breadth view and the term-month view all get the request's one ``(known_at, clock)``; the log is read once for
    the cards and once for the Warming and Incubating ones' runs; Context and Bench never count; the Armed card is
    counted, never a row."""
    cards = {
        T1: cc(State.WARMING, tid=T1, missing=["Volume-confirmed breakout (the confirmation key)"]),
        T2: cc(State.INCUBATING, tid=T2),
        T3: cc(State.ARMED, tid=T3),
    }
    theses = [
        Thesis(
            id=T1,
            name="Card A",
            narrative="x",
            segments=[Segment(label="Core")],
            basket=[
                BasketMember(ticker="AAA", role="x", security_id=A, segment="Core"),
                BasketMember(ticker="BBB", role="x", security_id=B, segment="Core"),
                BasketMember(ticker="CCC", role="x", security_id=C, segment="Context"),
            ],
            term_set=[
                TermSetEntry(term="alpha", tier=TermTier.SIGNAL),
                TermSetEntry(term="broad", tier=TermTier.BROAD),
            ],
        ),
        Thesis(
            id=T2,
            name="Card B",
            narrative="x",
            segments=[Segment(label="Core")],
            basket=[BasketMember(ticker="DDD", role="x", security_id=D, segment="Core")],
        ),
        Thesis(id=T3, name="Card C", narrative="x"),
        Thesis(id=T4, name="Card D", narrative="x"),
    ]
    seen: dict = {
        "roster": [],
        "recorded": [],
        "runs": [],
        "flags": [],
        "company": [],
        "pits": [],
        "breadth": [],
    }

    def fake_cards(conn, asof, known_at):
        seen["roster"].append((asof, known_at))
        return CardsAsOf(5, theses, 1, 1)

    def fake_recorded(conn, ids, asof):
        seen["recorded"].append((list(ids), asof))
        return {tid: rec(card) for tid, card in cards.items()}

    def fake_runs(conn, ids, asof):
        seen["runs"].append((list(ids), asof))
        return {T1: [log(d(27)), log(d(28))], T2: [log(d(28), State.INCUBATING)]}

    def fake_flags(conn, ids, asof, *, known_at, clock):
        seen["flags"].append((set(ids), asof, known_at, clock))
        return {B: "mixed-scale price history"}

    class FakePit:
        def __init__(self, basket):
            self.basket = basket

        def term_mentions(self, thesis_id):
            return [trow("alpha", m, 10) for m in SIX] if thesis_id == T1 else []

    def fake_pit(conn, asof, known_at, clock, basket=None):
        seen["pits"].append((asof, known_at, clock, basket))
        return FakePit(basket)

    def fake_breadth(pit, sids):
        seen["breadth"].append((pit.basket, list(sids)))
        return theme_breadth.compute([[float(i) for i in range(1, 99)] for _ in sids])

    def fake_per_company(conn, sids, asof, known_at, clock, read):
        seen["company"].append((list(sids), asof, known_at, clock))
        return {
            sid: (
                Reading(buys_30d=3, buyers_30d=2)
                if sid == A
                else Reading(coiling=True, contraction=0.3)
            )
            for sid in sids
        }

    CACHE.clear()
    monkeypatch.setattr(router_mod, "cards_as_of", fake_cards)
    monkeypatch.setattr(router_mod, "read_recorded", fake_recorded)
    monkeypatch.setattr(router_mod, "read_runs", fake_runs)
    monkeypatch.setattr(router_mod, "_pit", fake_pit)
    monkeypatch.setattr(router_mod, "price_flags", fake_flags)
    monkeypatch.setattr(router_mod, "per_company", fake_per_company)
    monkeypatch.setattr(router_mod.theme_breadth, "breadth_for", fake_breadth)

    def fake_members(names, who):
        seen["members"] = tuple(names)
        return {}

    monkeypatch.setattr(router_mod, "display_members", fake_members)
    monkeypatch.setattr(router_mod.master, "tickers_for", lambda conn, ids, **kw: dict(TICKERS))
    monkeypatch.setattr(router_mod.master, "ciks_for", lambda conn, ids, **kw: {})
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    try:
        body = (
            TestClient(app).get("/dashboards/forming-quietly", params={"asof": "2026-10-28"}).json()
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    asof, known_at = seen["roster"][0]
    assert asof == ASOF and known_at is not None
    # Context (CCC) never counts: the flags and the members are asked about AAA, BBB and DDD only
    assert seen["flags"][0] == ({A, B, D}, ASOF, known_at, "public")
    # the flagged BBB is never read; the members run on AAA and DDD only, once per name
    assert seen["company"][0] == (sorted([A, D], key=str), ASOF, known_at, "public")
    assert seen["members"] == MEMBERS
    assert seen["recorded"] == [([T1, T2, T3, T4], ASOF)] and seen["runs"] == [([T1, T2], ASOF)]
    # one breadth read per row card, over its unflagged counting names; one term-month view for them all
    assert seen["breadth"] == [([A], [A]), ([D], [D])]
    assert all(p[:3] == (ASOF, known_at, "public") for p in seen["pits"]) and len(seen["pits"]) == 3
    assert [p[3] for p in seen["pits"]].count(None) == 1

    rows = {r["thesis_name"]: r for r in body["rows"]}
    assert set(rows) == {"Card A", "Card B"}
    a = rows["Card A"]
    assert (a["quiet"]["names"], a["quiet"]["read"]) == (2, 1)
    assert a["quiet"]["flagged"] == [
        {"security_id": str(B), "ticker": "BBB", "reason": "mixed-scale price history"}
    ]
    assert [n["ticker"] for n in a["quiet"]["insider"]] == ["AAA"] and a["quiet"]["insider"][0][
        "cluster"
    ]
    assert a["adoption"]["counted"] is True and a["adoption"]["terms_signal"] == 1
    assert rows["Card B"]["quiet"]["coiling"][0]["ticker"] == "DDD"
    assert rows["Card B"]["adoption"]["counted"] is False
    # Warming first, then Incubating; the Armed card is counted and has no row; the card with no call is counted
    assert [r["thesis_name"] for r in body["rows"]] == ["Card A", "Card B"]
    assert (
        body["cards_armed"],
        body["cards_warming"],
        body["cards_incubating"],
        body["cards_no_call"],
    ) == (1, 1, 1, 1)
    assert (body["live_cards"], body["cards_counted"], body["cards_not_yet_created"]) == (5, 4, 1)
    assert body["roster_fallback_cards"] == 1 and body["cards_incubating_lit"] == 1
    assert (body["names_read"], body["names_flagged"]) == (2, 1)
    assert body["flagged"] == [
        {"security_id": str(B), "ticker": "BBB", "reason": "mixed-scale price history"}
    ]
    assert (body["cards_with_term_months"], body["cards_without_term_months"]) == (1, 1)


# ---------------------------------------------------------------------------------------------------------
# 8. DB-backed: the real calls log, roster, tape, Form 4 and term months (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

_DB_DAY0 = date(2026, 9, 1)


def weekdays(start: date, end: date) -> list[date]:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _security(db, ticker: str) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "0009876543", "2026-01-01", "equity"),
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
            "recorded_at": datetime.combine(day, time.min, tzinfo=timezone.utc),
        },
    )


def _tape(db, sid: UUID, base: float) -> None:
    for day in weekdays(_DB_DAY0, ASOF):
        _bar(db, sid, day, base + (day - _DB_DAY0).days)


def _card(
    db,
    name: str,
    members: list[tuple[str, UUID, str]],
    terms: tuple[str, ...] = (),
) -> UUID:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker=t, role="the name", security_id=s, segment=seg)
            for t, s, seg in members
        ],
    )
    thesis_repo.upsert(db, thesis)
    if terms:
        thesis_repo.set_term_set(
            db, thesis.id, [TermSetEntry(term=t, tier=TermTier.SIGNAL) for t in terms]
        )
    with (
        db.cursor() as cur
    ):  # the card existed on every as-of below (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    return thesis.id


def _record(db, tid: UUID, night: date, state: State, **kw) -> None:
    flags = {k: kw.pop(k) for k in ("reconstructed",) if k in kw}
    card = cc(state, tid=tid, asof=night, **kw)
    calls_repo.append(db, card, DEFAULT_TENANT_ID, **flags)


def _buy(db, sid: UUID, day: date, accepted: datetime, who: str = "Pat Doe") -> None:
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": who,
            "insider_role": "Director",
            "txn_code": "P",
            "shares": 1000,
            "price": 10.0,
            "usd": 10000,
            "accession": f"0000876543-26-{uuid.uuid4().int % 900000 + 100000}",
            "txn_seq": 0,
            "valid_from": day,
            "accepted": accepted,
            "recorded_at": accepted,
        },
    )


def _mention(db, tid: UUID, term: str, month: date, docs: int) -> None:
    append_fact(
        db,
        "fact_term_mentions",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "thesis_id": tid,
            "term": term,
            "month": month,
            "documents": docs,
            "reports": docs,
            "events": 0,
            "issuance": 0,
            "other": 0,
            "filers": 0,
            "filers_sampled": False,
            "filer_ciks": [],
            "new_filers": None,
            "source_ref": "https://efts.example.test/q",
            "fetched_at": datetime.combine(month_last_day(month), time(12), timezone.utc)
            + timedelta(days=4),
            "valid_from": month_last_day(month),
        },
    )


def _get(db, asof: date) -> dict:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get("/dashboards/forming-quietly", params={"asof": asof.isoformat()})
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _shape(body: dict) -> list[tuple]:
    return [(r["thesis_name"], r["state"], r["run_start"]) for r in body["rows"]]


@pytest.fixture
def seeded(db):
    """Card A: Incubating on 09-14 and 09-15, Warming from 09-22 (a key turned, AAA fired). Card B: Warming since
    09-10, still Warming; Card C: Armed since 09-12. A tape for every name."""
    aaa, bbb, ccc = _security(db, "AAA"), _security(db, "BBB"), _security(db, "CCC")
    for sid, base in ((aaa, 20.0), (bbb, 40.0), (ccc, 60.0)):
        _tape(db, sid, base)
    card_a = _card(db, "Card A", [("AAA", aaa, "Core")])
    card_b = _card(db, "Card B", [("BBB", bbb, "Core")])
    card_c = _card(db, "Card C", [("CCC", ccc, "Core")])
    _record(db, card_a, date(2026, 9, 14), State.INCUBATING)
    _record(db, card_a, date(2026, 9, 15), State.INCUBATING)
    _record(
        db,
        card_a,
        date(2026, 9, 22),
        State.WARMING,
        key_conviction=key("Conviction", True, "A conviction entry trigger fired."),
        triggers_fired=[trig(aaa, "insider cluster", Kind.INSIDER, on=date(2026, 9, 21))],
        missing=["Volume-confirmed breakout (the confirmation key)"],
    )
    _record(db, card_b, date(2026, 9, 10), State.WARMING)
    _record(db, card_b, date(2026, 9, 11), State.WARMING)
    _record(db, card_c, date(2026, 9, 12), State.ARMED)
    db.commit()
    return {"aaa": aaa, "bbb": bbb, "ccc": ccc, "a": card_a, "b": card_b, "c": card_c}


def test_db_a_card_that_turned_warming_after_the_as_of_is_not_a_warming_row_then(db, seeded):
    before = _get(db, date(2026, 9, 21))
    assert before["cards_warming"] == 1 and before["cards_incubating"] == 1
    assert _shape(before) == [
        ("Card B", "warming", "2026-09-10"),
        ("Card A", "incubating", "2026-09-14"),
    ]
    after = _get(db, date(2026, 9, 25))
    assert after["cards_warming"] == 2 and after["cards_incubating"] == 0
    assert _shape(after) == [
        ("Card A", "warming", "2026-09-22"),
        ("Card B", "warming", "2026-09-10"),
    ]
    # the new Warming row carries the recorded call's own words and the night it began
    row = after["rows"][0]
    assert row["days_in_state"] == 3 and row["recorded_on"] == "2026-09-22"
    assert row["key_conviction"]["turned"] is True
    assert row["key_conviction"]["detail"] == "A conviction entry trigger fired."
    assert row["missing"] == ["Volume-confirmed breakout (the confirmation key)"]
    assert [n["ticker"] for n in row["names"]] == ["AAA"]
    assert row["names"][0]["triggers"][0]["event_date"] == "2026-09-21"


def test_db_an_armed_card_is_counted_and_never_a_row_and_a_card_with_no_call_is_counted(db, seeded):
    body = _get(db, date(2026, 9, 25))
    assert body["cards_armed"] == 1 and "Card C" not in [r["thesis_name"] for r in body["rows"]]
    # before Card C's first recorded night it has no call
    first_days = _get(db, date(2026, 9, 11))
    # Card A (first night 09-14) and Card C (09-12) have no recorded call yet
    assert first_days["cards_no_call"] == 2 and first_days["cards_armed"] == 0


def test_db_a_night_recorded_after_the_as_of_is_not_read_and_a_higher_seq_is_the_night_that_stood(
    db,
):
    aaa = _security(db, "AAA")
    _tape(db, aaa, 20.0)
    card = _card(db, "Card A", [("AAA", aaa, "Core")])
    _record(db, card, date(2026, 9, 14), State.INCUBATING)
    _record(db, card, date(2026, 9, 15), State.WARMING, reconstructed=True)
    _record(db, card, date(2026, 9, 15), State.INCUBATING)  # the re-run that night: seq 2 stands
    _record(db, card, date(2026, 9, 16), State.WARMING, reconstructed=True)
    _record(db, card, date(2026, 9, 17), State.WARMING)
    db.commit()
    on_the_15th = _get(db, date(2026, 9, 15))
    assert _shape(on_the_15th) == [("Card A", "incubating", "2026-09-14")]
    on_the_17th = _get(db, date(2026, 9, 17))
    (row,) = on_the_17th["rows"]
    assert (row["state"], row["run_start"], row["days_in_state"]) == ("warming", "2026-09-16", 1)
    assert row["run_reconstructed"] is True and row["run_nights"] == 2


def test_db_context_and_bench_names_never_count_and_a_buy_accepted_after_the_as_of_is_not_read(db):
    aaa, bbb, ccc = _security(db, "AAA"), _security(db, "BBB"), _security(db, "CCC")
    for sid, base in ((aaa, 20.0), (bbb, 40.0), (ccc, 60.0)):
        _tape(db, sid, base)
    card = _card(
        db, "Card A", [("AAA", aaa, "Core"), ("BBB", bbb, "Context"), ("CCC", ccc, "Bench")]
    )
    _record(db, card, date(2026, 10, 1), State.INCUBATING)
    # a buy on 10-20 on every name, accepted on the evening of 10-21 (public from then)
    for sid in (aaa, bbb, ccc):
        _buy(db, sid, date(2026, 10, 20), utc(2026, 10, 21, 21))
        _buy(db, sid, date(2026, 10, 20), utc(2026, 10, 21, 21), who="Sam Roe")
    db.commit()
    seen_before = _get(db, date(2026, 10, 20))
    (row,) = seen_before["rows"]
    assert (
        row["quiet"]["names"] == 1 and row["quiet"]["insider"] == []
    )  # not yet public on the 20th
    assert row["quiet"]["lit"] is False
    seen_after = _get(db, date(2026, 10, 22))
    (row,) = seen_after["rows"]
    # only AAA (Core) counts: Context and Bench are on the card but never counted here
    assert row["quiet"]["names"] == 1 and row["quiet"]["read"] == 1
    assert [
        (n["ticker"], n["buys_30d"], n["buyers_30d"], n["cluster"]) for n in row["quiet"]["insider"]
    ] == [("AAA", 2, 2, True)]
    assert row["quiet"]["lit"] is True and seen_after["cards_incubating_lit"] == 1
    assert seen_after["names_read"] == 1
    # breadth reads the card's counting names; AAA's tape is too short for the 50-day line, so it is thin
    assert row["quiet"]["breadth"]["counted"] == 0 and row["quiet"]["breadth"]["thin"] == 1


def test_db_a_term_month_dated_after_the_as_of_is_not_in_the_slope(db):
    aaa = _security(db, "AAA")
    _tape(db, aaa, 20.0)
    card = _card(db, "Card A", [("AAA", aaa, "Core")], terms=("alpha term",))
    _record(db, card, date(2026, 1, 10), State.INCUBATING)
    for month, docs in ((1, 10), (2, 10), (3, 10), (4, 10), (5, 20), (6, 20), (7, 20), (8, 1000)):
        _mention(db, card, "alpha term", date(2026, month, 1), docs)
    # BROAD terms are never counted: a row stored under a term the card does not list as SIGNAL is not summed
    _mention(db, card, "stray term", date(2026, 7, 1), 5000)
    db.commit()
    # as of 08-15 the closed months are Feb-Jul (August's count is dated 08-31): 30 against 60, no August in it
    july = _get(db, date(2026, 8, 15))["rows"][0]["adoption"]
    assert july["counted"] is True and (july["prior"], july["recent"], july["percent"]) == (
        30,
        60,
        100.0,
    )
    assert (july["recent_to"], july["months_stored"], july["terms_signal"]) == ("2026-07-01", 6, 1)
    # as of 09-15 August is closed and counts: Mar-May 40 against Jun-Aug 1040
    august = _get(db, date(2026, 9, 15))["rows"][0]["adoption"]
    assert (august["prior"], august["recent"], august["recent_to"]) == (40, 1040, "2026-08-01")
    # as of 07-15 only Jan-Jun are closed AND stored in full for Jan-Jun: Jan-Mar 30 against Apr-Jun 50
    june = _get(db, date(2026, 7, 15))["rows"][0]["adoption"]
    assert (june["prior"], june["recent"]) == (30, 50)
    # as of 06-15 only five closed months are stored (Jan-May): not counted yet, never a guess
    may = _get(db, date(2026, 6, 15))["rows"][0]["adoption"]
    assert may["counted"] is False and may["months_stored"] == 5


def test_db_a_live_asof_reads_on_the_record_clock_and_never_counts_the_current_month(
    db, pinned_today
):
    aaa = _security(db, "AAA")
    _tape(db, aaa, 20.0)
    card = _card(db, "Card A", [("AAA", aaa, "Core")], terms=("alpha term",))
    _record(db, card, date(2026, 9, 14), State.INCUBATING)
    months = closed_months(pinned_today)
    for m in months:
        _mention(db, card, "alpha term", m, 10)
    this_month = date(pinned_today.year, pinned_today.month, 1)
    _mention(
        db, card, "alpha term", this_month, 7777
    )  # recorded now, dated at the end of the as-of's month
    db.commit()
    body = _get(db, pinned_today)
    assert body["clock"] == "record"
    (row,) = body["rows"]
    assert row["adoption"]["counted"] is True
    assert (row["adoption"]["prior"], row["adoption"]["recent"]) == (30, 30)
    assert row["adoption"]["recent_to"] == months[-1].isoformat()


def test_db_an_archived_card_and_an_empty_log_give_an_honest_empty_page(db):
    card = _card(db, "Card A", [])
    thesis_repo.set_archived(db, card, True)
    _card(db, "Card B", [])
    db.commit()
    body = _get(db, date(2026, 9, 16))
    assert body["rows"] == [] and body["cards_counted"] == 1 and body["cards_no_call"] == 1
    assert body["names_read"] == 0 and body["flagged"] == []
