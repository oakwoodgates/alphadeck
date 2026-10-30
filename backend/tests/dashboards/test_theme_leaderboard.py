"""Theme Leaderboard (``docs/dashboards/theme-leaderboard.md``): the pure rules on hand-built inputs (the percentile and
the score against the fixtures the page's ``score.ts`` also asserts, the median with its floor, the breadth share, the
keys of every state, the adoption slope with the partial month left out, the buyers sum, Context and Bench never
counted, a flagged price history left out and counted), the router (the heavy read stubbed, and the real ``read_board``
with its DB reads stubbed), and DB-backed reads through the real ``calls`` log, the real price tape and the real
term-mention table: a card created after the as-of is not a row, a call recorded after the as-of is not the card in
effect, and a term month not yet closed by the as-of is not in the slope.

Every card, company, ticker and term below is a neutral fixture name.
"""

from __future__ import annotations

import calendar
import json
import uuid
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import theme_leaderboard as router_mod
from dashboards.baskets import CardsAsOf, FlaggedCompany, Membership, counted_rows
from dashboards.cache import CACHE
from dashboards.peer_table import CapCell
from dashboards.theme_leaderboard import (
    MIN_PRICED,
    NO_TAPE,
    PARTS,
    Adoption,
    BreadthPart,
    CardRead,
    CardRow,
    CompanyTape,
    Counts,
    InsidersPart,
    Keys,
    Recorded,
    ScoreInput,
    TapePart,
    adoption_slope,
    assemble,
    breadth_part,
    cap_mix,
    card_row,
    closed_months,
    company_tape,
    default_order,
    insiders_part,
    keys_of,
    percentiles,
    recorded_of,
    score,
    tape_part,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall
from domain.enums import Grade, State, TermTier, Verdict
from domain.market_cap import CapTier
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, TermSetEntry, Thesis
from repositories import calls_repo, thesis_repo
from signals.display import theme_breadth
from signals.display.base import DisplayBasis, DisplayHeadline, DisplayMetric, DisplaySignal

FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "dashboards"
    / "theme-leaderboard"
    / "__tests__"
    / "score-fixtures.json"
)

ASOF = date(2026, 10, 20)  # a Tuesday
T1, T2, T3 = (uuid.uuid4() for _ in range(3))
S = [uuid.uuid4() for _ in range(12)]
MIXED = "mixed-scale price history"


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def ms(
    tid: UUID, card: str, ticker: str, sid: UUID | None, segment: str | None = "Core"
) -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name=card,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
    )


def tape(r30: float | None = None, r7: float | None = None, **kw) -> CompanyTape:
    """A company's readings; priced when it has any return."""
    return CompanyTape(
        priced=kw.pop("priced", r30 is not None or r7 is not None),
        ret_30d=r30,
        ret_7d=r7,
        **kw,
    )


def read(tid: UUID = T1, name: str = "Card A", members=(), **kw) -> CardRead:
    return CardRead(thesis_id=tid, name=name, members=tuple(members), **kw)


def rec(
    state: State = State.WARMING,
    conviction: bool = False,
    confirmation: bool = False,
    armed: int = 0,
    night: date = ASOF,
    reconstructed: bool = False,
) -> Recorded:
    return Recorded(night, reconstructed, state, conviction, confirmation, armed)


def sig(kind: str, metrics=(), headline=None) -> DisplaySignal:
    return DisplaySignal(
        kind=kind,
        label=kind,
        metrics=list(metrics),
        headline=headline,
        basis=DisplayBasis(source="fact_price_eod"),
    )


def metric(key: str, value: float | None, unit: str = "pct") -> DisplayMetric:
    return DisplayMetric(key=key, label=key, value=value, unit=unit)


def mrow(term: str, year: int, month: int, documents: int) -> dict:
    """A term-mention row as ``pit.term_mentions`` returns it (only the columns the slope reads)."""
    return {"term": term, "month": date(year, month, 1), "documents": documents}


def run_of(term: str, spec: dict[tuple[int, int], int]) -> list[dict]:
    return [mrow(term, y, m, n) for (y, m), n in spec.items()]


# ---------------------------------------------------------------------------------------------------------
# 1. the percentile and the score
# ---------------------------------------------------------------------------------------------------------


def test_the_percentile_is_the_mid_rank_over_n_minus_one_and_a_blank_stays_blank():
    assert percentiles([10, 30, 20, None]) == [0, 1, 0.5, None]
    assert percentiles([1, 1, 3]) == [0.25, 0.25, 1]
    assert percentiles([5]) == [0.5]  # a lone value has nothing to compare with
    assert percentiles([None, None]) == [None, None]
    assert percentiles([]) == []
    assert percentiles([0, -0.0, 1]) == [0.25, 0.25, 1]  # zero and negative zero are equal


def _cases() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_score_matches_the_fixtures_the_page_also_asserts(case):
    """The fixture file is shared with ``frontend/src/dashboards/theme-leaderboard/__tests__/score.test.ts``: the two
    implementations of the rule are held to the same hand-derived numbers."""
    rows = [ScoreInput(**r) for r in case["rows"]]
    got = score(rows)
    assert len(got) == len(rows)
    for r, g in zip(rows, got):
        want = case["expected"][r.key]
        for part in (*PARTS, "score"):
            actual = getattr(g, part)
            if want[part] is None:
                assert actual is None, f"{r.key} {part}"
            else:
                assert actual == pytest.approx(want[part], abs=1e-9), f"{r.key} {part}"
    assert score([]) == []


def test_the_score_is_recomputed_among_the_cards_given():
    a = ScoreInput("A", 8.0, 80.0, 2, 50.0, 5)
    b = ScoreInput("B", 3.0, 40.0, 1, 10.0, 1)
    c = ScoreInput("C", -2.0, 60.0, 0, 90.0, 3)
    # tape A 1, breadth A 1, keys A 1, adoption A .5 (C is above it), insiders A 1: (1 + 1 + 1 + .5 + 1) / 5
    assert score([a, b, c])[0].score == pytest.approx(90.0, abs=1e-9)
    # among A and B alone, A leads on every part
    assert score([a, b])[0].score == pytest.approx(100.0, abs=1e-9)
    assert score([a, b])[1].score == pytest.approx(0.0, abs=1e-9)


def test_every_part_ranks_a_higher_value_higher():
    """The direction is the same for all five parts (a bigger tape, breadth, key count, adoption slope and buyer
    count ranks higher)."""
    lo = ScoreInput("lo", 0.0, 10.0, 0, -5.0, 0)
    hi = ScoreInput("hi", 1.0, 11.0, 1, 5.0, 1)
    got = score([lo, hi])
    assert [getattr(got[0], p) for p in PARTS] == [0.0] * 5
    assert [getattr(got[1], p) for p in PARTS] == [1.0] * 5


def _row(name: str, **parts) -> CardRow:
    """A card row with only the five parts' values set (the rest is the empty default)."""
    base = card_row(read(uuid.uuid4(), name), {}, None, ASOF)
    return replace(
        base,
        tape=TapePart(value=parts.get("tape")),
        breadth=BreadthPart(pct=parts.get("breadth")),
        keys=Keys(turned=parts.get("keys")),
        adoption=Adoption(slope_pct=parts.get("adoption")),
        insiders=InsidersPart(buyers=parts.get("insiders")),
    )


def test_the_default_order_is_the_score_with_a_card_missing_a_part_last_and_ties_by_name():
    full = dict(tape=3.0, breadth=60.0, keys=1, adoption=10.0, insiders=2)
    top = _row("Top", tape=9.0, breadth=90.0, keys=2, adoption=40.0, insiders=6)
    mid = _row("mid", **full)
    low = _row("Low", tape=-4.0, breadth=10.0, keys=0, adoption=-9.0, insiders=0)
    gap_a = _row("Gap A", **{**full, "adoption": None})
    gap_b = _row("gap b", **{**full, "keys": None})
    twin_1, twin_2 = _row("beta twin", **full), _row("Alpha twin", **full)
    got = default_order([gap_b, low, twin_1, top, gap_a, twin_2, mid])
    assert [r.thesis_name for r in got] == [
        "Top",
        "Alpha twin",  # the three identical cards tie on the score: by name, case-folded
        "beta twin",
        "mid",
        "Low",
        "Gap A",  # no score: after every scored card, by name
        "gap b",
    ]


# ---------------------------------------------------------------------------------------------------------
# 2. the tape part: the median of the priced counting names with its floor, minus IWM
# ---------------------------------------------------------------------------------------------------------


def test_the_tape_is_the_median_30_day_return_minus_iwms():
    t = tape_part([tape(4.0), tape(-2.0), tape(10.0)], iwm=1.5)
    assert (t.median, t.value, t.k, t.gap) == (4.0, 2.5, 3, None)
    even = tape_part([tape(1.0), tape(2.0), tape(3.0), tape(10.0)], iwm=0.25)
    assert (even.median, even.value, even.k) == (
        2.5,
        2.25,
        4,
    )  # the median of an even set is the mean of two


def test_fewer_than_the_floor_priced_names_is_no_tape_and_says_so():
    assert MIN_PRICED == 3
    t = tape_part([tape(4.0), tape(9.0)], iwm=1.0)
    assert (t.value, t.median, t.k, t.gap) == (None, None, 2, "thin")
    # a name with no 30-day return (a short tape) is not one of the priced names
    t = tape_part([tape(4.0), tape(9.0), tape(None, priced=True)], iwm=1.0)
    assert (t.value, t.k, t.gap) == (None, 2, "thin")
    assert tape_part([], iwm=1.0) == TapePart(k=0, gap="thin")


def test_without_iwm_the_median_is_stated_and_the_tape_is_not():
    t = tape_part([tape(4.0), tape(-2.0), tape(10.0)], iwm=None)
    assert (t.median, t.value, t.k, t.gap) == (4.0, None, 3, "no_iwm")


def test_a_flagged_name_is_never_in_the_median():
    wild = tape(8000.0, flag=MIXED)  # a scale break read as a return
    t = tape_part([tape(1.0), tape(2.0), tape(3.0), wild], iwm=0.0)
    assert (t.median, t.k) == (2.0, 3)
    # and it can not rescue a thin set
    assert tape_part([tape(1.0), tape(2.0), wild], iwm=0.0).gap == "thin"


# ---------------------------------------------------------------------------------------------------------
# 3. the breadth part: theme_breadth's own reading, plus the 200-day share per name
# ---------------------------------------------------------------------------------------------------------


def _line(n: int, step: float) -> list[float]:
    return [100.0 + step * i for i in range(n)]


def test_breadth_is_the_share_above_the_50_day_with_x_of_n_and_the_thin_names_counted():
    # one name rising (above its average), one falling (below), one with too little tape (shown, not counted)
    sig_ = theme_breadth.compute([_line(80, 1.0), _line(80, -0.5), _line(30, 1.0)])
    tapes = [tape(above_200d=True), tape(above_200d=False), tape(above_200d=None)]
    b = breadth_part(sig_, tapes)
    assert (b.pct, b.above, b.counted, b.thin) == (50.0, 1, 2, 1)
    assert (b.above_200, b.n_200) == (1, 2)  # the third name has no 200-day reading: not in n


def test_breadth_without_a_counted_name_is_no_share_and_a_flagged_name_is_not_in_the_200_day_one():
    thin_only = theme_breadth.compute([_line(30, 1.0)])
    b = breadth_part(thin_only, [tape(above_200d=True), tape(above_200d=True, flag=MIXED)])
    assert (b.pct, b.above, b.counted, b.thin) == (None, 0, 0, 1)
    assert (b.above_200, b.n_200) == (1, 1)
    assert breadth_part(None, []) == BreadthPart()


# ---------------------------------------------------------------------------------------------------------
# 4. the keys part: the recorded card in effect, every state
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recorded, want",
    [
        (None, Keys(None, None, None)),  # no recorded call by the as-of
        (rec(State.INCUBATING), Keys(0, False, False)),
        (rec(State.WARMING, conviction=True), Keys(1, True, False)),
        (rec(State.WARMING, confirmation=True), Keys(1, False, True)),
        (rec(State.ARMED, True, True, armed=2), Keys(2, True, True)),
        (rec(State.MANAGING, True, False), Keys(2, True, True)),  # Managing counts as 2
        (rec(State.MANAGING), Keys(2, True, True)),
    ],
)
def test_keys_for_every_state(recorded, want):
    assert keys_of(recorded) == want


def test_a_recorded_card_is_narrowed_to_what_the_page_reads():
    card = CallCard(
        thesis_id=T1,
        asof=ASOF,
        state=State.ARMED,
        verdict=Verdict.CORE_ENTRY,
        expression="",
        key_conviction=KeyState(turned=True, label="Conviction"),
        key_confirmation=KeyState(turned=False, label="Confirmation"),
        armed_members=[
            MemberCall(security_id=S[0]),
            MemberCall(security_id=S[1]),
            MemberCall(security_id=S[0]),  # a repeat is one name
        ],
    )
    assert recorded_of(date(2026, 10, 19), True, card) == Recorded(
        date(2026, 10, 19), True, State.ARMED, True, False, 2
    )


# ---------------------------------------------------------------------------------------------------------
# 5. the adoption slope: the last three closed months against the three before
# ---------------------------------------------------------------------------------------------------------

# the six closed months at ASOF (2026-10-20): Apr May Jun (the prior three) and Jul Aug Sep (the recent three)
SIX = {(2026, 4): 10, (2026, 5): 10, (2026, 6): 10, (2026, 7): 20, (2026, 8): 20, (2026, 9): 20}


def test_the_closed_months_are_the_six_before_the_asofs_own_month():
    assert closed_months(date(2026, 10, 20)) == [date(2026, m, 1) for m in range(4, 10)]
    # the last day of a month is still that month: it has not closed
    assert closed_months(date(2026, 10, 31)) == [date(2026, m, 1) for m in range(4, 10)]
    # the first of the next month closes it
    assert closed_months(date(2026, 11, 1)) == [date(2026, m, 1) for m in range(5, 11)]
    # across a year end
    assert closed_months(date(2026, 2, 15)) == [
        date(2025, 8, 1),
        date(2025, 9, 1),
        date(2025, 10, 1),
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_the_slope_is_recent_documents_over_prior_documents_as_a_signed_percent():
    a = adoption_slope(run_of("alpha", SIX), ("alpha",), ASOF)
    assert (a.slope_pct, a.recent_docs, a.prior_docs, a.gap) == (100.0, 60, 30, None)
    assert (a.recent_from, a.recent_to) == (date(2026, 7, 1), date(2026, 9, 1))
    assert (a.prior_from, a.prior_to) == (date(2026, 4, 1), date(2026, 6, 1))
    assert (a.terms, a.terms_counted) == (1, 1)
    falling = {**SIX, (2026, 7): 5, (2026, 8): 5, (2026, 9): 5}
    assert adoption_slope(run_of("alpha", falling), ("alpha",), ASOF).slope_pct == -50.0
    third = {(2026, m): n for m, n in ((4, 1), (5, 1), (6, 1), (7, 1), (8, 1), (9, 2))}
    assert adoption_slope(run_of("alpha", third), ("alpha",), ASOF).slope_pct == 33.33


def test_the_current_partial_month_and_the_months_outside_the_window_never_count():
    rows = run_of("alpha", SIX) + [
        mrow("alpha", 2026, 10, 9999),  # the as-of's own month, read but partial
        mrow("alpha", 2026, 3, 5000),  # older than the six
    ]
    a = adoption_slope(rows, ("alpha",), ASOF)
    assert (a.slope_pct, a.recent_docs, a.prior_docs) == (100.0, 60, 30)
    # on the last day of October the month is still partial; from the 1st of November it is closed and the
    # oldest month falls out of the window
    oct_rows = run_of("alpha", {(2026, m): 10 for m in range(5, 11)})
    assert adoption_slope(oct_rows, ("alpha",), date(2026, 10, 31)).gap == "not_counted_yet"
    closed = adoption_slope(oct_rows, ("alpha",), date(2026, 11, 1))
    assert (closed.slope_pct, closed.recent_docs, closed.prior_docs) == (0.0, 30, 30)


def test_fewer_than_six_closed_months_is_not_counted_yet_never_zero():
    five = {k: v for k, v in SIX.items() if k != (2026, 4)}
    a = adoption_slope(run_of("alpha", five), ("alpha",), ASOF)
    assert (a.slope_pct, a.recent_docs, a.prior_docs, a.gap) == (
        None,
        None,
        None,
        "not_counted_yet",
    )
    assert (a.terms, a.terms_counted) == (1, 0)
    assert adoption_slope([], ("alpha",), ASOF).gap == "not_counted_yet"


def test_no_documents_in_the_earlier_three_months_has_no_base_to_state_a_percent_against():
    some_then_none = {**SIX, (2026, 4): 0, (2026, 5): 0, (2026, 6): 0}
    a = adoption_slope(run_of("alpha", some_then_none), ("alpha",), ASOF)
    assert (a.slope_pct, a.gap, a.recent_docs, a.prior_docs) == (None, "no_base", 60, 0)
    assert a.terms_counted == 1
    none_at_all = {k: 0 for k in SIX}
    assert adoption_slope(run_of("alpha", none_at_all), ("alpha",), ASOF).gap == "no_base"


def test_documents_are_summed_over_the_current_signal_terms_only():
    rows = (
        run_of("Alpha", SIX)  # matched case-insensitively
        + run_of("beta", {k: 2 * v for k, v in SIX.items()})  # a second term: its documents add
        + run_of(
            "gone", {k: 1000 for k in SIX}
        )  # a term the card no longer holds: stored, never counted
    )
    a = adoption_slope(rows, ("alpha", "beta"), ASOF)
    assert (a.recent_docs, a.prior_docs, a.slope_pct) == (180, 90, 100.0)
    assert (a.terms, a.terms_counted) == (2, 2)


def test_a_term_without_all_six_months_is_left_out_and_counted_apart():
    partial = run_of("beta", {(2026, 7): 500, (2026, 8): 500, (2026, 9): 500})
    added_since_the_last_run = []  # a third term: no stored months yet
    a = adoption_slope(
        run_of("alpha", SIX) + partial + added_since_the_last_run, ("alpha", "beta", "gamma"), ASOF
    )
    assert (a.recent_docs, a.prior_docs, a.slope_pct) == (60, 30, 100.0)  # alpha alone
    assert (a.terms, a.terms_counted) == (3, 1)


def test_a_card_with_no_signal_terms_has_no_adoption():
    a = adoption_slope(run_of("alpha", SIX), (), ASOF)
    assert (a.gap, a.slope_pct, a.terms, a.terms_counted) == ("no_terms", None, 0, 0)
    assert adoption_slope(run_of("alpha", SIX), ("  ",), ASOF).gap == "no_terms"


# ---------------------------------------------------------------------------------------------------------
# 6. the insiders part, the cap mix, the per-name tape
# ---------------------------------------------------------------------------------------------------------


def test_the_buyers_are_summed_with_the_names_that_have_any():
    got = insiders_part(
        [
            ("AAA", tape(buyers_30d=2)),
            ("BBB", tape(buyers_30d=0)),
            ("CCC", tape(buyers_30d=3)),
            ("DDD", tape(buyers_30d=None)),  # no Form 4 ingested: not zero, not read
        ]
    )
    assert (got.buyers, got.read) == (5, 3)
    assert [(n.name, n.buyers) for n in got.names] == [("CCC", 3), ("AAA", 2)]  # most buyers first


def test_no_name_with_a_form_4_reading_is_no_buyer_count_not_zero():
    assert insiders_part([("AAA", tape(buyers_30d=None))]) == InsidersPart()
    assert insiders_part([]) == InsidersPart()
    # a stated zero is a reading
    zero = insiders_part([("AAA", tape(buyers_30d=0))])
    assert (zero.buyers, zero.read, zero.names) == (0, 1, ())


def test_the_cap_mix_counts_each_tier_and_the_blanks():
    def capped(tier: CapTier | None) -> CompanyTape:
        return CompanyTape(cap=CapCell(value=1.0, tier=tier) if tier else CapCell(gap="no_price"))

    got = cap_mix(
        [capped(CapTier.NANO), capped(CapTier.SMALL), capped(CapTier.SMALL), capped(None), NO_TAPE]
    )
    assert (got.nano, got.micro, got.small, got.mid, got.large, got.blank) == (1, 0, 2, 0, 0, 2)


def test_a_companys_tape_is_read_off_the_members_own_output():
    t = company_tape(
        {
            "trailing_returns": sig(
                "trailing_returns", [metric("ret_7d", -1.25), metric("ret_30d", 4.0)]
            ),
            "sma_position": sig("sma_position", [metric("pct_vs_slow", -2.0)]),
            "relative_strength": sig(
                "relative_strength", headline=DisplayHeadline(key="leading", label="Leading")
            ),
            "insider_flow_90d": sig(
                "insider_flow_90d", [metric("distinct_buyers_30d", 2.0, "count")]
            ),
        },
        CapCell(),
    )
    assert (t.priced, t.ret_7d, t.ret_30d, t.above_200d, t.rs_leading, t.buyers_30d) == (
        True,
        -1.25,
        4.0,
        False,
        True,
        2,
    )
    assert t.flag is None
    # no verdict yet is "not leading", no member is no reading, a member with no value is no reading
    not_leading = company_tape(
        {
            "relative_strength": sig(
                "relative_strength", headline=DisplayHeadline(key="inline", label="x")
            )
        },
        CapCell(),
    )
    assert not_leading.rs_leading is False and not_leading.priced is False
    assert company_tape({}, CapCell()) == NO_TAPE
    short = company_tape(
        {"sma_position": sig("sma_position", [metric("pct_vs_slow", None)])}, CapCell()
    )
    assert short.above_200d is None
    # a stated zero buyers is a reading, a missing insider member is not
    zero = company_tape(
        {
            "insider_flow_90d": sig(
                "insider_flow_90d", [metric("distinct_buyers_30d", 0.0, "count")]
            )
        },
        CapCell(),
    )
    assert zero.buyers_30d == 0 and NO_TAPE.buyers_30d is None


def test_a_flagged_price_history_reads_no_price_member_and_keeps_its_form_4_count():
    cap = CapCell(gap="flagged", note=MIXED)
    t = company_tape(
        {
            "trailing_returns": sig(
                "trailing_returns", [metric("ret_30d", 8000.0)]
            ),  # never handed in
            "insider_flow_90d": sig(
                "insider_flow_90d", [metric("distinct_buyers_30d", 3.0, "count")]
            ),
        },
        cap,
        MIXED,
    )
    assert t.flag == MIXED and t.priced is False
    assert (t.ret_7d, t.ret_30d, t.above_200d, t.rs_leading) == (None, None, None, None)
    assert t.buyers_30d == 3 and t.cap is cap


# ---------------------------------------------------------------------------------------------------------
# 7. one card's row
# ---------------------------------------------------------------------------------------------------------


def test_context_and_bench_names_are_never_counted_and_an_unresolved_row_is_counted_apart():
    memberships = [
        ms(T1, "Card A", "AAA", S[0]),
        ms(T1, "Card A", "BBB", S[1], "Edge"),
        ms(T1, "Card A", "CCC", S[2]),
        ms(T1, "Card A", "CTX", S[3], CONTEXT_LABEL),
        ms(T1, "Card A", "BNC", S[4], BENCH_LABEL),
        ms(T1, "Card A", "NOSEC", None),
    ]
    rows = counted_rows(memberships, include_silent=False)
    assert (rows.silent_rows_left_out, rows.unresolved_rows) == (2, 1)
    tapes = {
        S[0]: tape(1.0, buyers_30d=1),
        S[1]: tape(2.0),
        S[2]: tape(3.0),
        S[3]: tape(900.0, buyers_30d=40),  # Context: read nowhere
        S[4]: tape(-900.0, buyers_30d=40),
    }
    row = card_row(read(members=rows.rows), tapes, iwm=0.0, asof=ASOF)
    assert (row.counting, row.priced, row.flagged) == (3, 3, 0)
    assert (row.tape.median, row.tape.k) == (2.0, 3)
    assert row.insiders.buyers == 1 and [n.name for n in row.insiders.names] == ["AAA"]


def test_a_company_in_two_links_of_a_card_counts_once():
    rows = counted_rows(
        [ms(T1, "Card A", "AAA", S[0], "One"), ms(T1, "Card A", "AAA", S[0], "Two")],
        include_silent=False,
    )
    row = card_row(read(members=rows.rows), {S[0]: tape(1.0)}, iwm=0.0, asof=ASOF)
    assert row.counting == 1


def test_a_flagged_name_is_left_out_of_every_price_reading_and_counted():
    members = [
        ms(T1, "Card A", t, s)
        for t, s in (("AAA", S[0]), ("BBB", S[1]), ("CCC", S[2]), ("FLG", S[3]))
    ]
    tapes = {
        S[0]: tape(1.0, 0.5, rs_leading=False, above_200d=True, buyers_30d=0),
        S[1]: tape(2.0, 1.5, rs_leading=True, above_200d=False, buyers_30d=1),
        S[2]: tape(3.0, 2.5, rs_leading=False, above_200d=True, buyers_30d=0),
        # a +8,000% scale break: it would lead every price reading, so every one of them leaves it out
        S[3]: CompanyTape(flag=MIXED, buyers_30d=4, cap=CapCell(gap="flagged", note=MIXED)),
    }
    row = card_row(read(members=members), tapes, iwm=1.0, asof=ASOF)
    assert (row.counting, row.priced, row.flagged) == (4, 3, 1)
    assert (row.tape.median, row.tape.value, row.tape.k) == (2.0, 1.0, 3)
    assert (row.ret_7d.median, row.ret_7d.k) == (1.5, 3)
    assert (row.rs_leaders, row.rs_n) == (1, 3)
    assert row.cap_mix.blank == 4  # none has a cap tier here; the flagged one is among the blanks
    # the Form 4 count is not a price reading: it stays (the Hot Board and the Peer Table keep it too)
    assert row.insiders.buyers == 5 and row.insiders.read == 4


def test_the_7_day_median_has_the_same_floor():
    members = [ms(T1, "Card A", f"N{i}", S[i]) for i in range(3)]
    two = card_row(
        read(members=members), {S[0]: tape(r7=1.0), S[1]: tape(r7=2.0), S[2]: tape()}, None, ASOF
    )
    assert (two.ret_7d.median, two.ret_7d.k) == (None, 2)
    three = card_row(
        read(members=members),
        {S[0]: tape(r7=1.0), S[1]: tape(r7=2.0), S[2]: tape(r7=6.0)},
        None,
        ASOF,
    )
    assert (three.ret_7d.median, three.ret_7d.k) == (2.0, 3)


def test_the_state_the_clock_and_the_armed_names_come_from_the_recorded_card():
    members = [ms(T1, "Card A", "AAA", S[0])]
    armed = card_row(
        read(members=members, recorded=rec(State.ARMED, True, True, 3, date(2026, 10, 19), True)),
        {},
        None,
        ASOF,
    )
    assert (armed.state, armed.recorded_asof, armed.reconstructed, armed.armed_names) == (
        State.ARMED,
        date(2026, 10, 19),
        True,
        3,
    )
    assert armed.keys.turned == 2
    for state in (State.INCUBATING, State.WARMING, State.MANAGING):
        other = card_row(read(members=members, recorded=rec(state, armed=2)), {}, None, ASOF)
        assert other.state == state and other.armed_names is None  # a count only when Armed
    none = card_row(read(members=members), {}, None, ASOF)
    assert (none.state, none.recorded_asof, none.keys.turned, none.armed_names) == (
        None,
        None,
        None,
        None,
    )
    assert none.reconstructed is False


def test_a_card_with_no_counting_name_keeps_its_row_with_every_part_blank():
    row = card_row(read(recorded=rec(State.WARMING, True)), {}, 1.0, ASOF)
    assert row.counting == 0
    assert row.tape.value is None and row.tape.gap == "thin"
    assert row.breadth.pct is None and row.insiders.buyers is None
    assert row.keys.turned == 1  # the record part is still read
    assert row.adoption.gap == "no_terms"


def test_assemble_orders_the_cards_and_carries_the_counts():
    rich = [ms(T1, "Card A", f"A{i}", S[i]) for i in range(3)]
    poor = [ms(T2, "Card B", f"B{i}", S[3 + i]) for i in range(3)]
    tapes = {
        **{S[i]: tape(5.0 + i, buyers_30d=1) for i in range(3)},
        **{S[3 + i]: tape(-5.0 - i, buyers_30d=0) for i in range(3)},
        S[6]: CompanyTape(flag=MIXED),
    }
    breadth_up = theme_breadth.compute([_line(80, 1.0)] * 3)
    breadth_down = theme_breadth.compute([_line(80, -1.0)] * 3)
    mentions = tuple(run_of("alpha", SIX))
    flat = tuple(run_of("alpha", {k: 10 for k in SIX}))
    reads = [
        read(
            T2,
            "Card B",
            poor,
            terms=("alpha",),
            recorded=rec(State.INCUBATING),
            breadth=breadth_down,
            mentions=flat,
        ),
        read(
            T1,
            "Card A",
            rich + [ms(T1, "Card A", "FLG", S[6])],
            terms=("alpha",),
            recorded=rec(State.ARMED, True, True, 1),
            breadth=breadth_up,
            mentions=mentions,
        ),
        read(
            T3, "Card C", [ms(T3, "Card C", "A0", S[0])]
        ),  # a name on two cards; no call, no terms
    ]
    got = assemble(
        asof=ASOF,
        clock="public",
        reads=reads,
        tapes=tapes,
        flagged=[FlaggedCompany(S[6], "FLG", MIXED)],
        iwm_ret_30d=1.0,
        counts=Counts(5, 2, 1, 12, 3, 1),
    )
    assert [c.thesis_name for c in got.cards] == ["Card A", "Card B", "Card C"]  # C has no score
    assert (got.cards_counted, got.live_cards, got.cards_not_yet_created) == (3, 5, 2)
    assert (got.roster_fallback_cards, got.basket_rows, got.silent_rows_left_out) == (1, 12, 3)
    assert got.unresolved_rows == 1
    assert (got.names, got.names_flagged) == (
        7,
        1,
    )  # seven distinct companies across the three cards
    assert (got.benchmark, got.iwm_ret_30d, got.clock) == ("IWM", 1.0, "public")
    assert [f.ticker for f in got.flagged] == ["FLG"]
    a, b, c = got.cards
    assert a.adoption.slope_pct == 100.0 and b.adoption.slope_pct == 0.0
    assert c.adoption.gap == "no_terms" and c.state is None


# ---------------------------------------------------------------------------------------------------------
# 8. the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


def _board():
    """Two cards (A leads B on every part) and a third with no recorded call, as ``assemble`` builds them."""
    tapes = {
        S[0]: tape(8.0, 2.0, priced=True, above_200d=True, rs_leading=True, buyers_30d=2),
        S[1]: tape(6.0, 1.0, above_200d=True, buyers_30d=1),
        S[2]: tape(7.0, 3.0, buyers_30d=0),
        S[3]: tape(-1.0, -2.0, buyers_30d=0),
        S[4]: tape(-3.0, -1.0, buyers_30d=0),
        S[5]: tape(-2.0, 0.0, buyers_30d=0),
    }
    a = [ms(T1, "Card A", t, s) for t, s in (("AAA", S[0]), ("BBB", S[1]), ("CCC", S[2]))]
    b = [ms(T2, "Card B", t, s) for t, s in (("DDD", S[3]), ("EEE", S[4]), ("FFF", S[5]))]
    up = theme_breadth.compute([_line(80, 1.0)] * 3)
    down = theme_breadth.compute([_line(80, -1.0)] * 3)
    reads = [
        read(
            T1,
            "Card A",
            a,
            terms=("alpha",),
            recorded=rec(State.ARMED, True, True, 2),
            breadth=up,
            mentions=tuple(run_of("alpha", SIX)),
        ),
        read(
            T2,
            "Card B",
            b,
            terms=("alpha",),
            recorded=rec(State.WARMING),
            breadth=down,
            mentions=tuple(run_of("alpha", {k: 10 for k in SIX})),
        ),
        read(T3, "Card C"),
    ]
    return assemble(
        asof=ASOF,
        clock="public",
        reads=reads,
        tapes=tapes,
        flagged=[],
        iwm_ret_30d=1.0,
        counts=Counts(4, 1, 0, 6, 0, 0),
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}

    def fake_read(conn, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        return _board()

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_board", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_view_and_serves_one_cached_read(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/theme-leaderboard", params={"asof": "2026-10-20"}).json()
    assert body["cache"] == "computed"
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 10, 20) and clock == "public" and known_at is not None
    again = client.get("/dashboards/theme-leaderboard", params={"asof": "2026-10-20"}).json()
    assert again["cache"] == "hit" and seen["reads"] == 1
    # another as-of is another read
    client.get("/dashboards/theme-leaderboard", params={"asof": "2026-10-19"})
    assert seen["reads"] == 2

    client.get("/dashboards/theme-leaderboard", params={"asof": market_today().isoformat()})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 3


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/theme-leaderboard", params={"asof": "2026-10-20"}).json()
    assert [c["thesis_name"] for c in body["cards"]] == ["Card A", "Card B", "Card C"]
    assert (body["min_priced"], body["recent_months"], body["prior_months"]) == (3, 3, 3)
    assert (body["benchmark"], body["iwm_ret_30d"], body["clock"]) == ("IWM", 1.0, "public")
    assert (body["live_cards"], body["cards_counted"], body["cards_not_yet_created"]) == (4, 3, 1)
    a = body["cards"][0]
    assert (
        a["state"] == "armed"
        and a["armed_names"] == 2
        and a["keys"]
        == {
            "turned": 2,
            "conviction": True,
            "confirmation": True,
        }
    )
    assert a["tape"] == {"value": 6.0, "median": 7.0, "k": 3, "gap": None}
    assert a["ret_7d"] == {"median": 2.0, "k": 3}
    assert a["breadth"]["pct"] == 100.0 and (a["breadth"]["above"], a["breadth"]["counted"]) == (
        3,
        3,
    )
    assert (a["breadth"]["above_200"], a["breadth"]["n_200"]) == (2, 2)
    assert a["adoption"]["slope_pct"] == 100.0 and a["adoption"]["recent_from"] == "2026-07-01"
    assert a["insiders"]["buyers"] == 3 and a["insiders"]["names"] == [
        {"name": "AAA", "buyers": 2},
        {"name": "BBB", "buyers": 1},
    ]
    assert (a["counting"], a["priced"], a["flagged"], a["rs_leaders"], a["rs_n"]) == (3, 3, 0, 1, 1)
    assert a["cap_mix"] == {"nano": 0, "micro": 0, "small": 0, "mid": 0, "large": 0, "blank": 3}
    c = body["cards"][2]
    assert c["state"] is None and c["keys"]["turned"] is None and c["recorded_asof"] is None
    assert c["adoption"]["gap"] == "no_terms" and c["tape"]["gap"] == "thin"


def test_the_router_requires_an_asof(stub_client):
    client, _ = stub_client
    assert client.get("/dashboards/theme-leaderboard").status_code == 422


# ---------------------------------------------------------------------------------------------------------
# 9. the real read_board with its DB reads stubbed
# ---------------------------------------------------------------------------------------------------------


def test_read_board_reads_only_the_counting_names_on_the_requests_view(monkeypatch):
    """Context and Bench names are never read; a flagged name is read for its Form 4 count only, left out of the
    card's breadth view and named; every view carries the request's one ``serve_view`` pair."""
    card_a = Thesis(
        id=T1,
        name="Card A",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker="AAA", role="r", security_id=S[0], segment="Core"),
            BasketMember(ticker="BBB", role="r", security_id=S[1], segment="Core"),  # flagged
            BasketMember(ticker="CTX", role="r", security_id=S[2], segment=CONTEXT_LABEL),
            BasketMember(ticker="BNC", role="r", security_id=S[3], segment=BENCH_LABEL),
            BasketMember(ticker="NOSEC", role="r", security_id=None, segment="Core"),
        ],
        term_set=[
            TermSetEntry(term=" Alpha term ", tier=TermTier.SIGNAL),
            TermSetEntry(term="alpha TERM", tier=TermTier.SIGNAL),  # a duplicate, case-folded
            TermSetEntry(term="beta term", tier=TermTier.BROAD),  # never counted
            TermSetEntry(term="  ", tier=TermTier.SIGNAL),
        ],
    )
    card_b = Thesis(
        id=T2,
        name="Card B",
        narrative="x",
        basket=[BasketMember(ticker="CTX", role="r", security_id=S[2], segment=CONTEXT_LABEL)],
    )
    seen: dict = {}
    known_at = datetime(2026, 10, 20, 23, 59, tzinfo=timezone.utc)
    flagged_tape = CompanyTape(flag=MIXED, buyers_30d=2, cap=CapCell(gap="flagged", note=MIXED))

    def fake_cards(conn, asof, known):
        seen["roster"] = (asof, known)
        return CardsAsOf(4, [card_a, card_b], 1, 1)

    def fake_flags(conn, ids, asof, *, known_at, clock):
        seen["flags"] = (set(ids), asof, known_at, clock)
        return {S[1]: MIXED}

    def fake_per_company(conn, sids, asof, known, clock, read_one):
        seen["tapes"] = (list(sids), asof, known, clock)
        return {S[0]: tape(1.0, buyers_30d=0), S[1]: flagged_tape}

    def fake_breadths(conn, asof, known, clock, card_names):
        seen["breadth"] = (asof, known, clock, card_names)
        return {}

    def fake_mentions(tid):
        seen.setdefault("mention_reads", []).append(tid)
        return [mrow("alpha term", 2026, 9, 3)]

    def fake_view(conn, asof, known, clock, basket=None):
        seen["mentions_view"] = (asof, known, clock)
        return SimpleNamespace(term_mentions=fake_mentions)

    monkeypatch.setattr(router_mod, "cards_as_of", fake_cards)
    monkeypatch.setattr(router_mod, "price_flags", fake_flags)
    monkeypatch.setattr(router_mod, "per_company", fake_per_company)
    monkeypatch.setattr(router_mod, "read_breadths", fake_breadths)
    monkeypatch.setattr(
        router_mod, "read_recorded", lambda conn, ids, asof: {T1: rec(State.ARMED, True, True, 1)}
    )
    monkeypatch.setattr(router_mod, "read_iwm", lambda pit, asof: 2.5)
    monkeypatch.setattr(router_mod, "_view", fake_view)

    board = router_mod.read_board(object(), ASOF, known_at, "public")

    view = (ASOF, known_at, "public")
    assert seen["roster"] == (ASOF, known_at)
    assert seen["flags"] == (
        {S[0], S[1]},
        *view,
    )  # Context and Bench names are not even asked about
    assert seen["tapes"] == ([*sorted([S[0], S[1]], key=str)], *view)
    assert seen["breadth"][:3] == view
    assert seen["breadth"][3] == {T1: [S[0]], T2: []}  # the flagged name is out of the breadth view
    assert seen["mentions_view"] == view
    assert seen["mention_reads"] == [T1]  # Card B holds no SIGNAL term: nothing to read

    a, b = (next(c for c in board.cards if c.thesis_id == t) for t in (T1, T2))
    assert (a.counting, a.priced, a.flagged) == (2, 1, 1)
    assert a.state == State.ARMED and a.armed_names == 1
    assert (
        a.adoption.terms == 1
    )  # the SIGNAL term once (stripped, case-folded); BROAD and blank never
    assert (b.counting, b.state) == (
        0,
        None,
    )  # a card of Context names only: a row, nothing counted
    assert a.insiders.buyers == 2  # the flagged name's Form 4 count stays
    assert (board.names, board.names_flagged) == (2, 1)
    assert (board.live_cards, board.cards_not_yet_created, board.roster_fallback_cards) == (4, 1, 1)
    assert (board.basket_rows, board.silent_rows_left_out, board.unresolved_rows) == (6, 3, 1)
    assert board.iwm_ret_30d == 2.5
    assert [(f.ticker, f.reason) for f in board.flagged] == [("BBB", MIXED)]


# ---------------------------------------------------------------------------------------------------------
# 10. DB-backed: the real calls log, the real tape, the real term-mention table (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

_DAY0 = date(2026, 6, 1)
_TAPE_END = date(2026, 10, 28)
_STORED = datetime(
    2026, 10, 29, 12, tzinfo=timezone.utc
)  # the weekly job stored every month recently


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _security(db, ticker: str, *, etf: bool = False) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                sid,
                DEFAULT_TENANT_ID,
                ticker,
                "0009876543",
                "2026-01-01",
                "etf" if etf else "equity",
            ),
        )
    return sid


def _close(base: float, slope: float, day: date) -> float:
    return round(base + slope * (day - _DAY0).days, 4)


def _tape(db, sid: UUID, base: float, slope: float) -> None:
    """A close of ``base + slope * days since 2026-06-01`` on every weekday through 2026-10-28."""
    for day in weekdays(_DAY0, _TAPE_END):
        append_fact(
            db,
            "fact_price_eod",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                "d": day,
                "close": _close(base, slope, day),
                "volume": 1000.0,
                "valid_from": day,
                "recorded_at": datetime.combine(day, time.min, tzinfo=timezone.utc),
            },
        )


def _card(db, name: str, members: list[tuple[str, UUID, str]], *, created: str, terms=()) -> UUID:
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
    thesis_repo.set_term_set(db, thesis.id, [TermSetEntry(term=t, tier=tier) for t, tier in terms])
    with db.cursor() as cur:  # a fixture backdate, not an app write
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", (created, thesis.id))
    return thesis.id


def _call(
    tid: UUID, night: date, state: State, *, conviction=False, confirmation=False, armed=()
) -> CallCard:
    return CallCard(
        thesis_id=tid,
        asof=night,
        state=state,
        verdict=Verdict.NOT_YET,
        expression="",
        key_conviction=KeyState(turned=conviction, label="Conviction"),
        key_confirmation=KeyState(turned=confirmation, label="Confirmation"),
        armed_members=[
            MemberCall(
                security_id=s, verdict=Verdict.CORE_ENTRY, entry_grade=Grade.CORE, confidence=0.5
            )
            for s in armed
        ],
    )


def _mention(tid: UUID, term: str, month: date, documents: int) -> dict:
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "thesis_id": tid,
        "term": term,
        "month": month,
        "documents": documents,
        "reports": documents,
        "events": 0,
        "issuance": 0,
        "other": 0,
        "filers": 1,
        "filers_sampled": False,
        "filer_ciks": ["0000000001"],
        "new_filers": None,
        "source_ref": "https://efts.sec.gov/LATEST/search-index?fixture",
        "fetched_at": _STORED,
        "valid_from": date(
            month.year, month.month, calendar.monthrange(month.year, month.month)[1]
        ),
        "recorded_at": _STORED,
    }


# documents per month for the card's SIGNAL term (2026): the months after the as-of exist too, stored later
_MONTHLY = {1: 10, 2: 10, 3: 10, 4: 20, 5: 20, 6: 20, 7: 30, 8: 30, 9: 70, 10: 100}


def _get(db, asof: date) -> dict:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get("/dashboards/theme-leaderboard", params={"asof": asof.isoformat()})
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _card_named(body: dict, name: str) -> dict:
    return next(c for c in body["cards"] if c["thesis_name"] == name)


@pytest.fixture
def seeded(db):
    """Card A: three counting names (two rising, one falling) and a Context name, a SIGNAL term with ten months of
    documents, a Warming call on 09-20 and an Armed one on 10-15 (a Managing row on 10-25). Card B: created 10-10,
    one name. An IWM tape. Every tape runs to 2026-10-28."""
    n1, n2, n3 = _security(db, "AAA"), _security(db, "BBB"), _security(db, "CCC")
    ctx, solo = _security(db, "CTX"), _security(db, "DDD")
    iwm = _security(db, "IWM", etf=True)
    for sid, base, slope in (
        (n1, 20.0, 0.5),
        (n2, 40.0, 0.2),
        (n3, 100.0, -0.3),
        (ctx, 10.0, 1.0),
        (solo, 50.0, 0.1),
        (iwm, 200.0, 0.1),
    ):
        _tape(db, sid, base, slope)
    card_a = _card(
        db,
        "Card A",
        [
            ("AAA", n1, "Core"),
            ("BBB", n2, "Core"),
            ("CCC", n3, "Core"),
            ("CTX", ctx, CONTEXT_LABEL),
        ],
        created="2026-01-02",
        terms=[("alpha term", TermTier.SIGNAL), ("beta term", TermTier.BROAD)],
    )
    card_b = _card(db, "Card B", [("DDD", solo, "Core")], created="2026-10-10")
    calls_repo.append(db, _call(card_a, date(2026, 9, 20), State.WARMING, conviction=True))
    calls_repo.append(
        db,
        _call(
            card_a, date(2026, 10, 15), State.ARMED, conviction=True, confirmation=True, armed=[n1]
        ),
    )
    calls_repo.append(db, _call(card_a, date(2026, 10, 25), State.MANAGING, armed=[n1]))
    for m, n in _MONTHLY.items():
        append_fact(db, "fact_term_mentions", _mention(card_a, "alpha term", date(2026, m, 1), n))
        # a BROAD term is not counted; a term the card no longer holds stays stored and is ignored
        append_fact(db, "fact_term_mentions", _mention(card_a, "beta term", date(2026, m, 1), 5000))
        append_fact(db, "fact_term_mentions", _mention(card_a, "gone term", date(2026, m, 1), 7000))
    db.commit()
    return {"a": card_a, "b": card_b, "n1": n1}


def _ret30(base: float, slope: float, asof: date) -> float:
    """The 30-trading-day return of a fixture tape at ``asof``, from the fixture's own formula."""
    closes = [_close(base, slope, d) for d in weekdays(_DAY0, asof)]
    return round((closes[-1] / closes[-31] - 1.0) * 100.0, 2)


def test_db_a_card_created_after_the_asof_is_not_a_row(db, seeded):
    early = _get(db, date(2026, 10, 5))
    assert [c["thesis_name"] for c in early["cards"]] == ["Card A"]
    assert (early["live_cards"], early["cards_counted"], early["cards_not_yet_created"]) == (
        2,
        1,
        1,
    )
    later = _get(db, date(2026, 10, 12))
    assert sorted(c["thesis_name"] for c in later["cards"]) == ["Card A", "Card B"]
    assert (later["cards_counted"], later["cards_not_yet_created"]) == (2, 0)
    # the card with no call yet keeps its row, its record part blank
    b = _card_named(later, "Card B")
    assert b["state"] is None and b["keys"]["turned"] is None and b["counting"] == 1


def test_db_an_archived_card_is_not_a_row_and_an_empty_roster_keeps_its_row(db, seeded):
    thesis_repo.set_archived(db, seeded["b"], True)
    empty = _card(db, "Card E", [], created="2026-01-03")
    db.commit()
    body = _get(db, date(2026, 10, 12))
    assert sorted(c["thesis_name"] for c in body["cards"]) == ["Card A", "Card E"]
    assert body["live_cards"] == 2  # the archived card is not a live card
    e = _card_named(body, "Card E")
    assert (e["counting"], e["priced"], e["flagged"], e["state"]) == (0, 0, 0, None)
    assert (
        e["tape"]["gap"] == "thin"
        and e["breadth"]["pct"] is None
        and e["insiders"]["buyers"] is None
    )
    assert body["cards"][-1]["thesis_id"] == str(
        empty
    )  # neither card has a score (no Form 4 reading): by name


def test_db_every_part_is_read_at_the_asof_off_the_real_tape(db, seeded):
    body = _get(db, ASOF)
    a = _card_named(body, "Card A")
    # the counting names: three; the Context name is read nowhere
    assert (a["counting"], a["priced"], a["flagged"]) == (3, 3, 0)
    medians = sorted(_ret30(*bs, ASOF) for bs in ((20.0, 0.5), (40.0, 0.2), (100.0, -0.3)))
    iwm = _ret30(200.0, 0.1, ASOF)
    assert a["tape"]["k"] == 3 and a["tape"]["median"] == medians[1]
    assert a["tape"]["value"] == round(medians[1] - iwm, 2) and body["iwm_ret_30d"] == iwm
    # two names above their 50-day average, one below
    assert (a["breadth"]["above"], a["breadth"]["counted"], a["breadth"]["thin"]) == (2, 3, 0)
    assert a["breadth"]["pct"] == 66.67
    assert (a["breadth"]["above_200"], a["breadth"]["n_200"]) == (
        0,
        0,
    )  # under 200 bars of tape: no reading
    # the recorded call in effect on 10-20 is the Armed one of 10-15 (the 10-25 Managing row is later)
    assert (a["state"], a["recorded_asof"], a["armed_names"]) == ("armed", "2026-10-15", 1)
    assert a["keys"] == {"turned": 2, "conviction": True, "confirmation": True}
    # no Form 4 and no shares on file: not zero, not read
    assert a["insiders"] == {"buyers": None, "read": 0, "names": []}
    assert a["cap_mix"] == {"nano": 0, "micro": 0, "small": 0, "mid": 0, "large": 0, "blank": 3}
    # the slope: Jul Aug Sep (30 + 30 + 70) against Apr May Jun (20 + 20 + 20), the SIGNAL term alone
    ad = a["adoption"]
    assert (ad["recent_docs"], ad["prior_docs"], ad["slope_pct"]) == (130, 60, 116.67)
    assert (ad["terms"], ad["terms_counted"], ad["gap"]) == (1, 1, None)
    assert (ad["recent_from"], ad["recent_to"], ad["prior_from"], ad["prior_to"]) == (
        "2026-07-01",
        "2026-09-01",
        "2026-04-01",
        "2026-06-01",
    )


def test_db_a_call_recorded_after_the_asof_is_not_the_card_in_effect(db, seeded):
    before = _card_named(_get(db, date(2026, 10, 5)), "Card A")
    assert (before["state"], before["recorded_asof"]) == ("warming", "2026-09-20")
    assert before["keys"] == {"turned": 1, "conviction": True, "confirmation": False}
    assert before["armed_names"] is None
    # nothing recorded yet on 09-10: the card is counted, with no call
    none = _card_named(_get(db, date(2026, 9, 10)), "Card A")
    assert none["state"] is None and none["keys"]["turned"] is None
    # on 10-27 the Managing row of 10-25 stands: two keys, and no armed count outside Armed
    managing = _card_named(_get(db, date(2026, 10, 27)), "Card A")
    assert (managing["state"], managing["keys"]["turned"], managing["armed_names"]) == (
        "managing",
        2,
        None,
    )


def test_db_a_term_month_not_yet_closed_by_the_asof_is_not_in_the_slope(db, seeded):
    """Months Jan to Oct are stored (the weekly job runs recently). On 08-15 only Feb to Jul have closed: the
    August row is stored, and so are September and October, and none of them is read."""
    ad = _card_named(_get(db, date(2026, 8, 15)), "Card A")["adoption"]
    # recent May Jun Jul (20 + 20 + 30) against Feb Mar Apr (10 + 10 + 20)
    assert (ad["recent_docs"], ad["prior_docs"], ad["slope_pct"]) == (70, 40, 75.0)
    assert (ad["recent_to"], ad["prior_from"]) == ("2026-07-01", "2026-02-01")


def test_db_the_current_partial_month_is_read_but_never_counted(db, seeded):
    """On 07-31 the July row (valid from 07-31) is readable, and the month has not closed: the slope is Apr May
    Jun (20 + 20 + 20) against Jan Feb Mar (10 + 10 + 10)."""
    ad = _card_named(_get(db, date(2026, 7, 31)), "Card A")["adoption"]
    assert (ad["recent_docs"], ad["prior_docs"], ad["slope_pct"]) == (60, 30, 100.0)
    assert (ad["recent_to"], ad["prior_from"]) == ("2026-06-01", "2026-01-01")
    # the next day it has closed: May Jun Jul (20 + 20 + 30) against Feb Mar Apr (10 + 10 + 20)
    after = _card_named(_get(db, date(2026, 8, 1)), "Card A")["adoption"]
    assert (after["recent_docs"], after["prior_docs"], after["slope_pct"]) == (70, 40, 75.0)


def test_db_fewer_than_six_closed_months_is_not_counted_yet(db, seeded):
    # on 2026-03-15 only Sep 2025 to Feb 2026 are in the window and only January and February are stored
    ad = _card_named(_get(db, date(2026, 3, 15)), "Card A")["adoption"]
    assert (ad["slope_pct"], ad["gap"], ad["terms_counted"]) == (None, "not_counted_yet", 0)
    assert ad["terms"] == 1
