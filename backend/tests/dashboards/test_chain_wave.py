"""Chain Wave (``docs/dashboards/chain-wave.md``): the week bucketing (a holiday week, an empty week, the partial
last week), the weekly cell math on hand-computed numbers, joins and gaps, each "—" rule, a flagged company left
out and named, the chain order (a renamed link's old rows, the No-link row, Context and Bench last behind the
toggle), the role rows (silent names included with the toggle off, payers first), each company once per row,
``thin`` under 3 priced names, the readout only with payers, the window slicing; the router off one cached read
with its 404; and, DB-backed, a price published after a past as-of never reaching a cell."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import chain_wave as router_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.chain_wave import (
    ROLE_ORDER,
    THIN_BELOW,
    WEEKS,
    WINDOWS,
    WaveInputs,
    card_choices,
    chain_wave,
    read_start,
    wave_inputs,
    week_spans,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo

ASOF = date(2026, 6, 5)  # a Friday
T1 = uuid.uuid4()
SX, SY, SP, SZ, SQ, SR = (uuid.uuid4() for _ in range(6))
MIXED = "mixed-scale price history"

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def weekdays(start: date, end: date, skip: tuple[date, ...] = ()) -> list[date]:
    """Every Monday-to-Friday date in ``[start, end]`` (the tests' sessions), less ``skip``."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = weekdays(date(2026, 5, 4), ASOF)  # five full weeks: May 4 … Jun 5


def steps(
    ds: list[date], first: float, moves: dict[date, float] | None = None
) -> dict[date, float]:
    """A close on every date in ``ds``: ``first``, then each ``moves[d]`` from ``d`` on."""
    out, level = {}, first
    for d in ds:
        level = (moves or {}).get(d, level)
        out[d] = level
    return out


def ms(
    ticker: str,
    sid: UUID | None,
    segment: str | None = "Turbines",
    role: str | None = None,
    tid: UUID = T1,
) -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name="Grid",
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
        context_role=role,
    )


SEGMENTS = ["Turbines", "Cables", CONTEXT_LABEL, BENCH_LABEL]

# X: +10% Wed May 20, -10% Mon May 25, +10% Fri Jun 5. Y: +20% Fri May 22, -10% Wed Jun 3. P (a payer, in Context):
# +10% Wed May 27. SPY: +2% Fri May 22, -2% Wed May 27, flat after.
X = steps(SESSIONS, 10.0, {date(2026, 5, 20): 11.0, date(2026, 5, 25): 9.9, ASOF: 10.89})
Y = steps(SESSIONS, 20.0, {date(2026, 5, 22): 24.0, date(2026, 6, 3): 21.6})
P = steps(SESSIONS, 50.0, {date(2026, 5, 27): 55.0})
SPY = steps(SESSIONS, 100.0, {date(2026, 5, 22): 102.0, date(2026, 5, 27): 99.96})


def build(
    rows: list[Membership],
    closes: dict[UUID, dict[date, float]],
    spy: dict[date, float] | None = None,
    *,
    segments: list[str] | None = None,
    flagged: dict[UUID, str] | None = None,
    asof: date = ASOF,
) -> WaveInputs:
    return wave_inputs(
        thesis_id=T1,
        thesis_name="Grid",
        segments=segments if segments is not None else SEGMENTS,
        memberships=rows,
        closes=closes,
        spy=SPY if spy is None else spy,
        asof=asof,
        flagged=flagged,
    )


def base_card() -> WaveInputs:
    rows = [
        ms("XXX", SX),
        ms("YYY", SY, role="counter_case"),
        ms("PAY", SP, CONTEXT_LABEL, role="payer"),
    ]
    return build(rows, {SX: X, SY: Y, SP: P})


def row(page, key: str):
    return next(r for r in page.rows if r.key == key)


def diffs(cells) -> list[float | None]:
    return [c.diff for c in cells]


# ---------------------------------------------------------------------------------------------------------
# the weeks
# ---------------------------------------------------------------------------------------------------------


def test_week_bucketing_with_a_holiday_week_and_a_partial_last_week():
    asof = date(2026, 6, 3)  # a Wednesday
    ds = weekdays(date(2026, 5, 11), asof, skip=(date(2026, 5, 25),))  # Memorial Day off
    spans = week_spans(ds, asof, weeks=3)
    assert [s.week.monday for s in spans] == [
        date(2026, 5, 18),
        date(2026, 5, 25),
        date(2026, 6, 1),
    ]
    full, holiday, last = spans
    assert (full.week.first_session, full.week.close, full.week.sessions) == (
        date(2026, 5, 18),
        date(2026, 5, 22),
        5,
    )
    assert ds[full.base] == date(2026, 5, 15) and ds[full.end] == date(2026, 5, 22)
    # the holiday week: four sessions, from Tuesday; its base is the Friday before
    assert (holiday.week.first_session, holiday.week.close, holiday.week.sessions) == (
        date(2026, 5, 26),
        date(2026, 5, 29),
        4,
    )
    assert ds[holiday.base] == date(2026, 5, 22)
    # the as-of's week, to date: closes at the as-of, Wednesday
    assert (last.week.close, last.week.sessions, last.week.to_date) == (asof, 3, True)
    assert not full.week.to_date and not holiday.week.to_date


@pytest.mark.parametrize(
    "asof, to_date",
    [(date(2026, 6, 5), False), (date(2026, 6, 6), False), (date(2026, 6, 4), True)],
)
def test_the_last_week_is_to_date_only_before_its_friday(asof, to_date):
    ds = weekdays(date(2026, 5, 25), asof)
    assert week_spans(ds, asof, weeks=2)[-1].week.to_date is to_date


def test_an_empty_week_has_no_close_and_the_next_week_measures_from_the_last_close_before_it():
    ds = weekdays(
        date(2026, 5, 11), ASOF, skip=tuple(weekdays(date(2026, 5, 25), date(2026, 5, 29)))
    )
    _, empty, after = week_spans(ds, ASOF, weeks=3)
    assert (empty.week.sessions, empty.week.close, empty.end) == (0, None, None)
    assert ds[after.base] == date(2026, 5, 22)  # two weeks back


def test_the_read_starts_the_monday_fifty_two_weeks_before_the_asofs_week():
    assert read_start(date(2026, 6, 3)) == date(2025, 6, 2)
    assert read_start(date(2026, 6, 3)).weekday() == 0
    inputs = base_card()
    assert len(inputs.weeks) == WEEKS and inputs.weeks[-1].monday == date(2026, 6, 1)
    assert inputs.weeks[0].monday == date(2025, 6, 9)


# ---------------------------------------------------------------------------------------------------------
# the cell
# ---------------------------------------------------------------------------------------------------------


def test_the_weekly_cell_on_hand_computed_numbers():
    page = chain_wave(base_card(), asof=ASOF, weeks=13)
    turbines = row(page, "link:Turbines")
    last3 = turbines.cells[-3:]
    # W1: Wed mean(+10%, 0) = +5%, Fri mean(0, +20%) = +10% -> 1.05 × 1.10 - 1 = +15.5%; SPY +2% -> 13.5 pp
    # W2: Mon mean(-10%, 0) = -5%; SPY -2% -> -3.0 pp
    # W3: Wed mean(0, -10%) = -5%, Fri mean(+10%, 0) = +5% -> 0.95 × 1.05 - 1 = -0.25%; SPY flat (bars) 0% -> -0.25
    assert diffs(last3) == pytest.approx([13.5, -3.0, -0.25])
    assert [c.ret for c in last3] == pytest.approx([15.5, -5.0, -0.25])
    assert [c.spy for c in last3] == pytest.approx([2.0, -2.0, 0.0])
    assert [c.names for c in last3] == [2, 2, 2] and all(c.gap is None for c in last3)
    # the week of May 11: everyone flat with bars, a real 0% week (not a gap)
    assert turbines.cells[-4].diff == pytest.approx(0.0) and turbines.cells[-4].gap is None
    # each name's own cells vs SPY
    xxx = next(m for m in turbines.members if m.ticker == "XXX")
    yyy = next(m for m in turbines.members if m.ticker == "YYY")
    assert diffs(xxx.cells[-3:]) == pytest.approx([8.0, -8.0, 10.0])
    assert diffs(yyy.cells[-3:]) == pytest.approx([18.0, 2.0, -10.0])
    # SPY's own row: its absolute weekly return
    assert [c.ret for c in page.spy[-3:]] == pytest.approx([2.0, -2.0, 0.0])
    assert all(c.diff is None for c in page.spy)


def test_joins_and_gaps():
    # Z's first bar is Wed May 27: it joins the line from Thu (two consecutive bars); Q skips Tue Jun 2, so its
    # Tue and Wed steps drop out (only its own), and its Thu step comes from Wed
    z = steps(SESSIONS[SESSIONS.index(date(2026, 5, 27)) :], 40.0, {date(2026, 5, 29): 44.0})
    q = steps(SESSIONS, 10.0, {date(2026, 6, 3): 12.0, date(2026, 6, 4): 13.2})
    del q[date(2026, 6, 2)]
    inputs = build([ms("ZZZ", SZ), ms("QQQ", SQ)], {SZ: z, SQ: q}, steps(SESSIONS, 100.0))
    page = chain_wave(inputs, asof=ASOF, weeks=13)
    t = row(page, "link:Turbines")
    w2, w3 = t.cells[-2], t.cells[-1]
    # W2: Q flat all week (0 each day), Z steps Thu (0) and Fri (+10%): Fri mean(0, +10%) = +5%
    assert w2.ret == pytest.approx(5.0) and w2.names == 2
    # W3: Q contributes Mon (0) and Thu (13.2 / 12 - 1 = +10%) only; Z flat every day: Thu mean(+10%, 0) = +5%
    assert w3.ret == pytest.approx(5.0) and w3.names == 2
    zzz = next(m for m in t.members if m.ticker == "ZZZ")
    # Z's own line starts mid-week (Wed): no return for W2 from last week's close, then W3 flat
    assert zzz.cells[-2].gap == "not_started" and zzz.cells[-1].diff == pytest.approx(0.0)
    qqq = next(m for m in t.members if m.ticker == "QQQ")
    assert qqq.cells[-1].ret == pytest.approx(10.0)


def test_each_dash_rule():
    # no_session: a week with no bar anywhere; not_started: a name whose tape starts in the last week;
    # no_return: a name with no bar all of W2 (a flat week is not 0%); no_spy: a week SPY has no bar in, while the
    # companies' bars (after SPY's last) put it on the calendar
    gap_week = tuple(weekdays(date(2026, 5, 11), date(2026, 5, 15)))
    ds = [d for d in SESSIONS if d not in gap_week]
    spy = {d: v for d, v in steps(ds, 100.0).items() if d <= date(2026, 5, 29)}
    late = steps([d for d in ds if d >= date(2026, 6, 2)], 5.0, {ASOF: 6.0})
    holey = {
        d: v for d, v in steps(ds, 30.0).items() if not date(2026, 5, 25) <= d <= date(2026, 5, 29)
    }
    full = steps(ds, 8.0)
    inputs = build(
        [ms("LATE", SX, "Turbines"), ms("HOLE", SY, "Cables"), ms("FULL", SZ, "Cables")],
        {SX: late, SY: holey, SZ: full},
        spy,
    )
    page = chain_wave(inputs, asof=ASOF, weeks=13)
    turbines, cables = row(page, "link:Turbines"), row(page, "link:Cables")
    assert inputs.sessions[-1] == ASOF  # the companies' bars after SPY's last are on the calendar
    # the week of May 11: no session at all
    assert turbines.cells[-4].gap == "no_session" and cables.cells[-4].gap == "no_session"
    # LATE started Tue Jun 2: not started by last week's close, in every week
    assert {c.gap for c in turbines.cells} == {"not_started", "no_session"}
    hole = next(m for m in cables.members if m.ticker == "HOLE")
    assert hole.cells[-2].gap == "no_return" and hole.cells[-2].diff is None
    # Cables: FULL contributes in W2, so the row has a return there; W3 has no SPY return
    assert cables.cells[-2].gap is None and cables.cells[-1].gap == "no_spy"
    assert cables.cells[-1].ret == pytest.approx(0.0) and cables.cells[-1].spy is None
    assert page.spy[-1].gap == "no_return"


def test_no_spy_tape_measures_nothing_and_says_so():
    inputs = build([ms("XXX", SX)], {SX: X}, {})
    assert inputs.spy_available is False and inputs.session_source == "companies"
    t = row(chain_wave(inputs, asof=ASOF), "link:Turbines")
    assert all(c.diff is None for c in t.cells)
    assert {c.gap for c in t.cells[-3:]} == {"no_spy"}


def test_a_row_with_no_priced_name_is_all_dashes():
    inputs = build([ms("NOBAR", SX), ms("NEWCO", None)], {})
    t = row(chain_wave(inputs, asof=ASOF), "link:Turbines")
    assert all(c.diff is None for c in t.cells) and (t.priced, t.names, t.thin) == (0, 2, True)
    newco = next(m for m in t.members if m.ticker == "NEWCO")
    assert newco.security_id is None and newco.cells == () and newco.key == "ticker:NEWCO"


def test_a_flagged_company_is_left_out_and_named():
    # FLAG's bars include a Saturday after SPY's last bar: never read, so it never reaches the calendar
    flag_tape = {**steps(SESSIONS, 7.0, {date(2026, 5, 20): 70.0}), date(2026, 6, 6): 7.0}
    inputs = build(
        [ms("XXX", SX), ms("YYY", SY), ms("FLAG", SR)],
        {SX: X, SY: Y, SR: flag_tape},
        flagged={SR: MIXED},
        asof=date(2026, 6, 6),
    )
    assert inputs.sessions[-1] == ASOF
    assert [(f.ticker, f.reason) for f in inputs.flagged] == [("FLAG", MIXED)]
    page = chain_wave(inputs, asof=date(2026, 6, 6), weeks=13)
    t = row(page, "link:Turbines")
    assert (t.names, t.priced) == (3, 2)
    assert diffs(t.cells[-3:]) == pytest.approx([13.5, -3.0, -0.25])  # the 10x jump never chained
    flag = t.members[-1]
    assert (flag.ticker, flag.flag, flag.priced, flag.cells) == ("FLAG", MIXED, False, ())
    counting = row(page, "counting")
    assert (counting.names, counting.priced) == (3, 2)


# ---------------------------------------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------------------------------------


def test_chain_order_old_links_no_link_and_the_toggle():
    rows = [
        ms("AAA", SX, "Cables"),
        ms("BBB", SY, "Old wires"),  # a link renamed since: not on the card today
        ms("CCC", SZ, None),
        ms("DDD", SQ, "Discovered"),
        ms("CTX", SP, CONTEXT_LABEL),
        ms("BEN", SR, BENCH_LABEL),
    ]
    segments = ["Turbines", "Discovered", "Cables", CONTEXT_LABEL, BENCH_LABEL]
    inputs = build(rows, {}, segments=segments)
    assert inputs.links_today == 3
    off = chain_wave(inputs, asof=ASOF)
    assert [(r.key, r.kind) for r in off.rows] == [
        ("link:Turbines", "link"),  # empty today at this roster: shown, 0 names
        ("link:Discovered", "link"),
        ("link:Cables", "link"),
        ("old:Old wires", "old_link"),
        ("no_link", "no_link"),
        ("counting", "counting"),
    ]
    assert row(off, "link:Turbines").names == 0 and row(off, "no_link").label == "No link"
    on = chain_wave(inputs, asof=ASOF, include_silent=True)
    assert [r.key for r in on.rows] == [
        "link:Turbines",
        "link:Discovered",
        "link:Cables",
        "old:Old wires",
        "no_link",
        f"silent:{CONTEXT_LABEL}",
        f"silent:{BENCH_LABEL}",
        "counting",
    ]
    # counting names: every row not in Context or Bench, whatever the toggle
    assert [m.ticker for m in row(on, "counting").members] == ["AAA", "BBB", "CCC", "DDD"]


def test_no_link_row_only_when_a_row_has_no_link():
    page = chain_wave(build([ms("AAA", SX, "Cables")], {}), asof=ASOF, include_silent=True)
    assert "no_link" not in [r.key for r in page.rows]
    assert [r.key for r in page.rows if r.kind == "silent"] == [
        f"silent:{CONTEXT_LABEL}",
        f"silent:{BENCH_LABEL}",
    ]


def test_role_rows_include_silent_names_with_the_toggle_off_payers_first():
    rows = [
        ms("FIN", SX, "Cables", role="financier"),
        ms("PAY", SP, CONTEXT_LABEL, role="payer"),
        ms("CC", SY, BENCH_LABEL, role="counter_case"),
        ms("PAY2", SZ, "Turbines", role="payer"),
    ]
    page = chain_wave(build(rows, {}), asof=ASOF)
    assert [r.key for r in page.rows if r.kind == "role"] == [
        "role:payer",
        "role:counter_case",
        "role:financier",
    ]
    assert sorted(m.ticker for m in row(page, "role:payer").members) == ["PAY", "PAY2"]
    assert [m.ticker for m in row(page, "role:counter_case").members] == ["CC"]
    assert not [r for r in page.rows if r.kind == "silent"] and page.roles_set == 3


def test_a_card_with_no_roles_has_no_role_rows_and_no_readout():
    inputs = build([ms("XXX", SX), ms("YYY", SY)], {SX: X, SY: Y})
    page = chain_wave(inputs, asof=ASOF)
    assert not [r for r in page.rows if r.kind == "role"] and page.roles_set == 0
    assert page.readout is None


def test_the_role_order_covers_every_context_role():
    assert sorted(ROLE_ORDER) == sorted(r.value for r in ContextRole) and ROLE_ORDER[0] == "payer"


def test_each_company_once_per_row():
    rows = [
        ms("AAA", SX, "Turbines", role="payer"),
        ms(
            "AAA", SX, "Cables", role="payer"
        ),  # one company in two links: in both links, once elsewhere
        ms("AAA", SX, "Cables", role="payer"),  # a stray duplicate row in one link
        ms("NEWCO", None, "Cables"),
        ms("newco", None, "Cables"),  # an unresolved row, by its ticker
    ]
    page = chain_wave(build(rows, {SX: X}), asof=ASOF)
    assert row(page, "link:Turbines").names == 1
    assert [m.ticker for m in row(page, "link:Cables").members] == ["AAA", "NEWCO"]
    assert row(page, "counting").names == 2 and row(page, "role:payer").names == 1
    assert row(page, "link:Cables").cells[-3:] == row(page, "link:Turbines").cells[-3:]


def test_thin_under_three_priced_names():
    three = build([ms("A", SX), ms("B", SY), ms("C", SZ)], {SX: X, SY: Y, SZ: X})
    two = build([ms("A", SX), ms("B", SY), ms("C", SZ)], {SX: X, SY: Y})
    assert THIN_BELOW == 3
    assert row(chain_wave(three, asof=ASOF), "link:Turbines").thin is False
    t = row(chain_wave(two, asof=ASOF), "link:Turbines")
    assert (t.thin, t.priced, t.names) == (True, 2, 3)
    assert t.cells[-1].diff is not None  # thin is drawn quieter, never hidden or blanked


def test_the_readout_only_with_payers_on_hand_computed_numbers():
    page = chain_wave(base_card(), asof=ASOF)
    r = page.readout
    assert r is not None and r.weeks == 4
    assert (r.start, r.end) == (date(2026, 5, 8), ASOF)
    # from May 8 to Jun 5: SPY 1.02 × 0.98 = 0.9996 (-0.04%); counting names (X, Y; the payer sits in Context)
    # 1.155 × 0.95 × 0.9975 = 1.094506875 (+9.4506875%); the payer +10%
    assert r.counting == pytest.approx(9.4506875 + 0.04, abs=1e-3)
    assert r.payers == pytest.approx(10.0 + 0.04, abs=1e-3)


def test_the_window_slices_the_cached_weeks_and_priced_follows_it():
    # OLD has bars only months back: priced in the 52-week window, not in the 13-week one
    old_days = weekdays(date(2025, 9, 1), date(2025, 10, 31))
    old = steps(old_days, 3.0, {date(2025, 9, 10): 3.3})
    spy = {**steps(weekdays(date(2025, 6, 1), date(2026, 5, 1)), 100.0), **SPY}
    inputs = build([ms("XXX", SX), ms("OLD", SY)], {SX: X, SY: old}, spy)
    full = chain_wave(inputs, asof=ASOF, weeks=52)
    short = chain_wave(inputs, asof=ASOF, weeks=13)
    assert [len(full.week_columns), len(short.week_columns)] == [52, 13]
    assert short.week_columns == full.week_columns[-13:]
    ft, st = row(full, "link:Turbines"), row(short, "link:Turbines")
    assert st.cells == ft.cells[-13:] and short.spy == full.spy[-13:]
    assert (ft.priced, st.priced) == (2, 1)
    assert [m.priced for m in st.members] == [True, False]
    assert full.readout == short.readout
    with pytest.raises(ValueError):
        chain_wave(inputs, asof=ASOF, weeks=10)
    assert WINDOWS == (13, 26, 52)


def test_card_choices_are_a_to_z_with_their_real_link_count():
    def card(name: str, links: list[str]) -> Thesis:
        return Thesis(
            id=uuid.uuid4(),
            name=name,
            narrative="x",
            segments=[Segment(label=s) for s in [*links, CONTEXT_LABEL, BENCH_LABEL]],
        )

    got = card_choices([card("grid", ["A", "B"]), card("Batteries", ["A"]), card("copper", [])])
    assert [(c.name, c.links) for c in got] == [("Batteries", 1), ("copper", 0), ("grid", 2)]


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy reads stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0, "card_reads": 0}
    other = uuid.uuid4()

    def fake_cards(conn, asof):
        seen["card_reads"] += 1
        return router_mod.CardsRead(
            live_cards=3,
            not_yet_created=1,
            cards=(
                router_mod.CardChoice(T1, "Grid", 2),
                router_mod.CardChoice(other, "Batteries", 1),
            ),
        )

    def fake_read(conn, thesis_id, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (thesis_id, asof, known_at, clock)
        return base_card()

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_cards", fake_cards)
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_every_window_and_the_toggle_off_one_cached_read(stub_client):
    client, seen = stub_client
    url = f"/dashboards/chain-wave/{T1}"
    body = client.get(url, params={"asof": "2026-06-05"}).json()
    assert body["cache"] == "computed" and body["weeks"] == 26 and len(body["week_columns"]) == 26
    assert body["include_silent"] is False and body["windows"] == [13, 26, 52]
    assert (body["thin_below"], body["readout_weeks"]) == (3, 4)
    thesis_id, asof, known_at, clock = seen["view"]
    assert thesis_id == T1 and asof == ASOF and clock == "public" and known_at is not None
    for weeks, silent in ((13, False), (52, True), (26, True)):
        again = client.get(
            url, params={"asof": "2026-06-05", "weeks": weeks, "include_silent": silent}
        ).json()
        assert again["cache"] == "hit" and len(again["week_columns"]) == weeks
        kinds = [r["kind"] for r in again["rows"]]
        assert ("silent" in kinds) is silent
    assert seen["reads"] == 1 and seen["card_reads"] == 1

    client.get(url, params={"asof": market_today().isoformat()})
    assert seen["view"][2:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(f"/dashboards/chain-wave/{T1}", params={"asof": "2026-06-05"}).json()
    assert [r["key"] for r in body["rows"]] == [
        "link:Turbines",
        "link:Cables",
        "counting",
        "role:payer",
        "role:counter_case",
    ]
    turbines = body["rows"][0]
    assert turbines["names"] == 2 and turbines["priced"] == 2 and turbines["thin"] is True
    assert turbines["cells"][-3]["diff"] == pytest.approx(13.5)
    assert turbines["cells"][0]["gap"] == "no_session"
    xxx = next(m for m in turbines["members"] if m["ticker"] == "XXX")
    assert xxx["security_id"] == str(SX) and xxx["flag"] is None and len(xxx["cells"]) == 26
    assert body["rows"][3]["role"] == "payer" and body["rows"][3]["label"] == "payer"
    assert body["readout"]["payers"] == pytest.approx(10.04, abs=1e-3)
    assert body["week_columns"][-1] == {
        "monday": "2026-06-01",
        "first_session": "2026-06-01",
        "close": "2026-06-05",
        "sessions": 5,
        "to_date": False,
    }
    assert body["spy"][-3]["ret"] == pytest.approx(2.0) and body["spy"][-3]["diff"] is None


def test_the_router_404s_a_card_not_counted_at_the_asof(stub_client):
    client, seen = stub_client
    r = client.get(f"/dashboards/chain-wave/{uuid.uuid4()}", params={"asof": "2026-06-05"})
    assert r.status_code == 404 and seen["reads"] == 0


def test_the_router_rejects_an_unknown_window(stub_client):
    client, _ = stub_client
    r = client.get(f"/dashboards/chain-wave/{T1}", params={"asof": "2026-06-05", "weeks": 10})
    assert r.status_code == 422
    assert [int(w) for w in router_mod.ChainWaveWeeks] == list(WINDOWS)


def test_the_picker_list_is_cached(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/chain-wave/cards", params={"asof": "2026-06-05"}).json()
    assert [c["name"] for c in body["cards"]] == [
        "Grid",
        "Batteries",
    ]  # the stub's own order, served as read
    assert (body["live_cards"], body["not_yet_created"], body["cache"]) == (3, 1, "computed")
    again = client.get("/dashboards/chain-wave/cards", params={"asof": "2026-06-05"}).json()
    assert again["cache"] == "hit" and seen["card_reads"] == 1


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a price published after a past as-of never reaches a cell (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

_THAW = datetime(2026, 9, 1, 18, 13, 31, tzinfo=timezone.utc)


def _price(db, sid, d: date, close: float, recorded_at: datetime) -> None:
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
            "recorded_at": recorded_at,
        },
    )


def _security(db, ticker: str, cik: str | None, kind: str = "equity") -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, "2026-01-01"),
        )
    return sid


def test_a_price_published_after_a_past_asof_never_reaches_a_cell(db, monkeypatch):
    """Eight weeks of weekday bars ending at the as-of for SPY and three names, each public at its own session.
    CWA's as-of bar is then re-versioned a week after the thaw at double the close: the past view's last week
    reads the ORIGINAL close (the correction was not public by the end of that day), a live view the correction.
    The data flags ride the same views through the real reader: none at the past as-of; on today's view the
    tapes (they end at the as-of) are stopped feeds, so every name is left out and named."""
    ds = weekdays(date(2026, 4, 13), ASOF)
    spy_id = _security(db, "SPY", None, kind="etf")
    shapes = {
        "CWA": [10.0 + 0.1 * i for i in range(len(ds))],
        "CWB": [20.0 + 0.05 * i + (0.4 if i % 7 == 0 else 0.0) for i in range(len(ds))],
        "CWC": [30.0 - 0.02 * i for i in range(len(ds))],
    }
    spy = [100.0 + 0.03 * i for i in range(len(ds))]
    sids = {t: _security(db, t, f"000876543{k}") for k, t in enumerate(shapes)}
    for i, d in enumerate(ds):
        at = datetime.combine(d, time.min, tzinfo=timezone.utc)
        _price(db, spy_id, d, spy[i], at)
        for t, closes in shapes.items():
            _price(db, sids[t], d, closes[i], at)
    corrected = shapes["CWA"][-1] * 2
    _price(db, sids["CWA"], ASOF, corrected, _THAW + timedelta(days=7))
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Chain Wave fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker="CWA", role="the name", security_id=sids["CWA"], segment="Core"),
            BasketMember(ticker="CWB", role="the name", security_id=sids["CWB"], segment="Core"),
            BasketMember(
                ticker="CWC",
                role="the name",
                security_id=sids["CWC"],
                segment=CONTEXT_LABEL,
                context_role=ContextRole.PAYER,
            ),
        ],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    url = f"/dashboards/chain-wave/{thesis.id}"
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        cards = client.get("/dashboards/chain-wave/cards", params={"asof": ASOF.isoformat()}).json()
        past = client.get(url, params={"asof": ASOF.isoformat(), "weeks": 13}).json()
        live_params = {"asof": market_today().isoformat(), "weeks": 52}
        live = client.get(url, params=live_params).json()
        # the clock alone: the same live read with the flag read stubbed out
        CACHE.clear()
        monkeypatch.setattr(router_mod, "price_flags", lambda *a, **k: {})
        unflagged = client.get(url, params=live_params).json()
        missing = client.get(
            f"/dashboards/chain-wave/{uuid.uuid4()}", params={"asof": "2026-06-05"}
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    def expected(closes_by_ticker):
        rows = [
            ms("CWA", sids["CWA"], "Core"),
            ms("CWB", sids["CWB"], "Core"),
            ms("CWC", sids["CWC"], CONTEXT_LABEL, role="payer"),
        ]
        inputs = build(
            rows,
            {sids[t]: dict(zip(ds, c)) for t, c in closes_by_ticker.items()},
            dict(zip(ds, spy)),
            segments=["Core", CONTEXT_LABEL, BENCH_LABEL],
        )
        return chain_wave(inputs, asof=ASOF, weeks=13)

    original = expected(shapes)
    fixed = expected({**shapes, "CWA": shapes["CWA"][:-1] + [corrected]})
    core_then, core_fixed = row(original, "link:Core"), row(fixed, "link:Core")
    assert core_then.cells[-1].diff != pytest.approx(core_fixed.cells[-1].diff)

    assert any(c["thesis_id"] == str(thesis.id) for c in cards["cards"])
    assert missing.status_code == 404
    assert past["last_session"] == ASOF.isoformat() and past["spy_available"] is True
    assert (
        past["roster_fallback"] is True
    )  # no roster snapshot by the as-of: today's roster stands in
    core = next(r for r in past["rows"] if r["key"] == "link:Core")
    assert [c["diff"] for c in core["cells"]] == pytest.approx(diffs(core_then.cells), abs=1e-3)
    assert (core["names"], core["priced"]) == (2, 2)
    assert past["flagged"] == []  # no flag at the past as-of
    assert past["readout"]["payers"] == pytest.approx(original.readout.payers, abs=1e-3)

    # today's view: no bar since the as-of is a stopped feed, a flag on every price cell: every name is left out,
    # still names, never priced, every cell "—" (never a made-up reading)
    lcore = next(r for r in live["rows"] if r["key"] == "link:Core")
    assert (lcore["names"], lcore["priced"]) == (2, 0) and all(
        c["diff"] is None for c in lcore["cells"]
    )
    assert sorted(f["ticker"] for f in live["flagged"]) == ["CWA", "CWB", "CWC"]
    assert {f["reason"] for f in live["flagged"]} == {"price feed stopped"}

    # the correction, public by today: the as-of's week reads it (52 weeks back from today still holds it)
    ucore = next(r for r in unflagged["rows"] if r["key"] == "link:Core")
    asof_week = next(
        i for i, w in enumerate(unflagged["week_columns"]) if w["monday"] == "2026-06-01"
    )
    assert ucore["cells"][asof_week]["diff"] == pytest.approx(core_fixed.cells[-1].diff, abs=1e-3)
