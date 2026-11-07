"""ETFs (``docs/dashboards/etfs.md``): panel 1's fund line and companies' line on hand-computed numbers, the three
windows sliced from one cached read, a fund found by its kind in a voting link and kept out of the companies' line,
a fund on two cards (one row in panels 2 to 4, two in panel 1), a flagged fund listed and left out of panels 1 to
4, flat shares with a moving price reading zero flow, the quiet empty states, the coverage panel, the router
(pure, the heavy read stubbed), the no-network import scan, and, DB-backed, a past as-of where a shares sample and
a bar dated after it stay invisible."""

from __future__ import annotations

import ast
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import etfs as router_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.etfs import (
    WINDOWS,
    FundBundle,
    FundReadings,
    Reading,
    etf_inputs,
    etfs,
    fund_readings,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.thesis import SILENT_LABELS, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.display import etf_flow
from tests.session_days import bar_days

ASOF = date(2026, 6, 1)
T1, T2, T3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
F = [uuid.uuid4() for _ in range(4)]  # funds
C = [uuid.uuid4() for _ in range(6)]  # companies

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def days(n: int, end: date = ASOF) -> list[date]:
    """``n`` consecutive calendar days ending at ``end`` (the tests' sessions)."""
    return [end - timedelta(days=n - 1 - i) for i in range(n)]


def tape(ds: list[date], closes: list[float | None]) -> dict[date, float]:
    return {d: c for d, c in zip(ds, closes) if c is not None}


def ms(tid: UUID, card: str, ticker: str, sid: UUID | None, segment: str | None = "Core"):
    return Membership(
        thesis_id=tid,
        thesis_name=card,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in SILENT_LABELS,
    )


def bundle(ds: list[date], closes: list[float | None], **readings) -> FundBundle:
    return FundBundle(tape(ds, closes), FundReadings(**readings))


def build(
    memberships,
    funds,
    companies=None,
    iwm=None,
    *,
    cards=None,
    flagged=None,
    names=None,
    asof=ASOF,
):
    cards = [(T1, "Card Alpha")] if cards is None else cards
    return etf_inputs(
        cards=cards,
        memberships=memberships,
        fund_ids=set(funds),
        funds=funds,
        names=names or {},
        company_closes=companies or {},
        iwm=iwm or {},
        flagged=flagged or {},
        asof=asof,
        live_cards=len(cards),
        cards_not_yet_created=0,
        roster_fallback_cards=0,
    )


def page(inputs, window="1m", asof=ASOF):
    return etfs(inputs, asof=asof, window=window)


def geometric(n: int, step: float, start: float = 100.0) -> list[float]:
    return [start * (1.0 + step) ** i for i in range(n)]


# ---------------------------------------------------------------------------------------------------------
# panel 1: the two lines
# ---------------------------------------------------------------------------------------------------------


def test_fund_and_companies_lines_on_hand_computed_numbers():
    ds = days(6)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"),
            ms(T1, "Card Alpha", "COA1", C[0]),
            ms(T1, "Card Alpha", "COA2", C[1]),
        ],
        {F[0]: bundle(ds, [100, 110, 121, 133.1, 146.41, 161.051])},
        {
            C[0]: tape(ds, [10, 10, 10, 11, 11, 12.1]),
            C[1]: tape(ds, [20, 22, 22, 22, 24.2, 24.2]),
        },
        {ds_: 100.0 + i for i, ds_ in enumerate(ds)},
    )
    p = page(inputs, "1w")
    (row,) = p.compare
    assert (row.ticker, row.thesis_id) == ("FNDA", T1)
    # the fund steps +10% a session; the companies step by the mean of their members' daily returns
    # (5%, 0, 5%, 5%, 5%): 1.05^4 - 1 = 21.55%
    assert row.ret == pytest.approx(61.05)
    assert row.companies_ret == pytest.approx(21.55)
    assert row.diff == pytest.approx(39.5)
    assert (row.companies, row.priced, row.sessions_flat) == (2, 2, 0)
    assert row.fund_points[0] == 100.0 and len(row.fund_points) == 6
    assert row.company_points[0] == 100.0 and row.company_points[-1] == pytest.approx(
        121.55, abs=0.01
    )
    assert p.iwm.available and p.iwm.ret == pytest.approx((105 / 100 - 1) * 100, abs=0.01)


def test_each_window_is_the_tail_of_the_one_cached_calendar():
    ds = days(80)
    sessions = 63
    inputs = build(
        [ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"), ms(T1, "Card Alpha", "COA1", C[0])],
        {F[0]: bundle(ds, geometric(80, 0.01))},
        {C[0]: tape(ds, geometric(80, 0.005))},
        {d: 100.0 for d in ds},
    )
    assert len(inputs.sessions) == sessions + 1  # one 64-date calendar serves all three windows
    for window, k in WINDOWS.items():
        p = page(inputs, window)
        (row,) = p.compare
        assert p.sessions == k and len(p.dates) == k + 1 and len(row.fund_points) == k + 1
        assert row.fund_points[0] == 100.0 and row.company_points[0] == 100.0
        assert row.ret == pytest.approx((1.01**k - 1) * 100, abs=0.006)
        assert row.companies_ret == pytest.approx((1.005**k - 1) * 100, abs=0.006)
        assert row.diff == pytest.approx(row.ret - row.companies_ret, abs=0.011)
        assert p.iwm.ret == 0.0


def test_a_fund_with_too_few_bars_for_the_window_reads_no_line_and_says_how_many():
    ds = days(30)
    # the fund only has the last 12 bars; the 1M window needs 22
    inputs = build(
        [ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"), ms(T1, "Card Alpha", "COA1", C[0])],
        {F[0]: bundle(ds, [None] * 18 + geometric(12, 0.01))},
        {C[0]: tape(ds, geometric(30, 0.01))},
        {d: 100.0 for d in ds},
    )
    (row,) = page(inputs, "1m").compare
    assert row.ret is None and row.diff is None and row.fund_points is None
    assert (row.bars, row.bars_needed, row.note) == (12, 22, "n/a: 12/22 bars")
    assert row.companies_ret is not None  # the companies' line is still drawn
    (short,) = page(inputs, "1w").compare  # the 1W window (6 bars) is covered
    assert short.ret is not None and short.note is None


def test_a_card_whose_companies_are_all_unpriced_reads_no_return_never_a_flat_zero():
    ds = days(30)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"),
            ms(T1, "Card Alpha", "COA1", C[0]),
            ms(T1, "Card Alpha", "COA2", None),
        ],
        {F[0]: bundle(ds, geometric(30, 0.01))},
        {},  # COA1 has no bars at all
        {d: 100.0 for d in ds},
    )
    (row,) = page(inputs, "1w").compare
    assert row.companies_ret is None and row.diff is None and row.company_points is None
    assert (row.companies, row.priced, row.unresolved) == (2, 0, 1)
    assert row.ret is not None  # the fund's own move still reads


def test_a_basket_row_with_no_resolved_security_counts_as_a_company_and_is_never_priced():
    ds = days(10)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"),
            ms(T1, "Card Alpha", "COA1", C[0]),
            ms(T1, "Card Alpha", "NOID", None),
        ],
        {F[0]: bundle(ds, geometric(10, 0.01))},
        {C[0]: tape(ds, geometric(10, 0.01))},
        {d: 100.0 for d in ds},
    )
    (row,) = page(inputs, "1w").compare
    assert (row.companies, row.priced, row.unresolved) == (2, 1, 1)


def test_context_and_bench_are_not_the_cards_companies():
    ds = days(10)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"),
            ms(T1, "Card Alpha", "COA1", C[0]),
            ms(T1, "Card Alpha", "COA2", C[1], "Context"),
            ms(T1, "Card Alpha", "COA3", C[2], "Bench"),
        ],
        {F[0]: bundle(ds, geometric(10, 0.01))},
        {
            C[0]: tape(ds, geometric(10, 0.01)),
            C[1]: tape(ds, [50.0] * 10),
            C[2]: tape(ds, [9.0] * 10),
        },
        {d: 100.0 for d in ds},
    )
    (row,) = page(inputs, "1w").compare
    assert row.companies == 1
    assert row.companies_ret == pytest.approx(
        (1.01**5 - 1) * 100, abs=0.006
    )  # the silent flat names never dilute it
    assert inputs.companies == 1


# ---------------------------------------------------------------------------------------------------------
# which rows are funds
# ---------------------------------------------------------------------------------------------------------


def test_a_fund_in_a_voting_link_is_found_by_its_kind_and_kept_out_of_the_companies_line():
    """A roster from before the ETFs link: the fund sits in "Core" next to the companies."""
    ds = days(10)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "Core"),
            ms(T1, "Card Alpha", "COA1", C[0], "Core"),
        ],
        {F[0]: bundle(ds, geometric(10, 0.03))},
        # the fund's closes also sit in the companies map (it is on the roster): it must never be priced as one
        {C[0]: tape(ds, geometric(10, 0.01)), F[0]: tape(ds, geometric(10, 0.03))},
        {d: 100.0 for d in ds},
    )
    p = page(inputs, "1w")
    (card,) = [c for c in inputs.cards if c.thesis_id == T1]
    assert card.company_ids == (C[0],) and card.fund_ids == (F[0],)
    (row,) = p.compare
    assert row.companies == 1
    assert row.companies_ret == pytest.approx((1.01**5 - 1) * 100, abs=0.006)
    assert row.ret == pytest.approx((1.03**5 - 1) * 100, abs=0.006)
    assert p.funds == 1 and p.companies == 1


def test_a_fund_on_two_cards_is_one_row_in_panels_2_to_4_and_two_in_panel_1():
    ds = days(10)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"),
            ms(T1, "Card Alpha", "COA1", C[0]),
            ms(
                T2, "Card Beta", "FNDX", F[0], "ETFs"
            ),  # the same fund, another ticker spelling on card 2
            ms(T2, "Card Beta", "COB1", C[1]),
            ms(T2, "Card Beta", "FNDB", F[1], "ETFs"),
        ],
        {F[0]: bundle(ds, geometric(10, 0.01)), F[1]: bundle(ds, geometric(10, 0.02))},
        {C[0]: tape(ds, geometric(10, 0.01)), C[1]: tape(ds, geometric(10, 0.01))},
        {d: 100.0 for d in ds},
        cards=[(T1, "Card Alpha"), (T2, "Card Beta")],
    )
    p = page(inputs, "1w")
    assert sorted((r.thesis_name, r.security_id) for r in p.compare) == sorted(
        [("Card Alpha", F[0]), ("Card Beta", F[0]), ("Card Beta", F[1])]
    )
    assert len(p.flows) == len(p.sizes) == len(p.trends) == 2
    two = next(r for r in p.flows if r.security_id == F[0])
    assert [c.thesis_name for c in two.cards] == ["Card Alpha", "Card Beta"]
    assert [m.security_id for m in p.coverage.multi] == [F[0]]
    assert p.coverage.cards_with_fund == 2 and p.coverage.funds == 2


def test_a_flagged_fund_is_listed_and_left_out_of_panels_1_to_4_but_stays_in_coverage():
    ds = days(10)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"),
            ms(T1, "Card Alpha", "FNDB", F[1], "ETFs"),
            ms(T1, "Card Alpha", "COA1", C[0]),
            ms(T1, "Card Alpha", "COA2", C[1]),
        ],
        {F[0]: bundle(ds, geometric(10, 0.01)), F[1]: bundle(ds, geometric(10, 0.01))},
        {C[0]: tape(ds, geometric(10, 0.01)), C[1]: tape(ds, geometric(10, 0.2))},
        {d: 100.0 for d in ds},
        flagged={F[1]: "mixed-scale price history", C[1]: "price feed stopped"},
    )
    p = page(inputs, "1w")
    assert [r.security_id for r in p.compare] == [F[0]]
    assert [r.security_id for r in p.flows] == [F[0]]
    assert [r.security_id for r in p.sizes] == [F[0]]
    assert [r.security_id for r in p.trends] == [F[0]]
    assert [(f.ticker, f.reason) for f in p.flagged_funds] == [
        ("FNDB", "mixed-scale price history")
    ]
    assert [(f.ticker, f.reason) for f in p.flagged_companies] == [("COA2", "price feed stopped")]
    # the flagged company is out of the line (a +20% a session name would show), still counted on the card
    (row,) = p.compare
    assert row.companies == 2 and row.priced == 1
    assert row.companies_ret == pytest.approx((1.01**5 - 1) * 100, abs=0.006)
    # the flagged fund stays in the coverage panel
    assert p.coverage.funds == 2 and p.funds == 2


# ---------------------------------------------------------------------------------------------------------
# panels 2 to 4: the readings
# ---------------------------------------------------------------------------------------------------------


def _samples(ds: list[date], shares: list[float]):
    return [
        {"d": d, "shares_out": s, "source": "fund-site", "source_ref": "x"}
        for d, s in zip(ds, shares)
    ]


def test_shares_flat_with_a_moving_price_reads_zero_flow():
    """The three traps of ``etf_flow``: price, volume and size can all move; the fund's own share count did
    not, so nothing was created or redeemed."""
    ds = days(45)
    bars = [{"d": d, "close": 10.0 + i, "volume": 1000.0 * (i + 1)} for i, d in enumerate(ds)]
    sig = etf_flow.compute(_samples(ds, [50_000.0] * 45), bars, ASOF)
    r = fund_readings({"etf_flow": sig}, _samples(ds, [50_000.0] * 45), bars, ASOF)
    assert r.flow_1w_usd.value == 0.0 and r.flow_1m_usd.value == 0.0
    assert r.flow_1m_pct.value == 0.0
    assert r.filling_1w is False and r.filling_1m is False
    assert r.size_usd == pytest.approx(
        50_000.0 * (10.0 + 44)
    )  # approx size: the latest sample x its close


def test_a_window_still_filling_keeps_the_members_note_and_the_first_sample_date():
    ds = days(9)
    bars = [{"d": d, "close": 10.0, "volume": 1.0} for d in ds]
    samples = _samples(ds, [1000.0 + i for i in range(9)])
    r = fund_readings({"etf_flow": etf_flow.compute(samples, bars, ASOF)}, samples, bars, ASOF)
    assert r.flow_1m_usd.value is None and r.flow_1m_usd.note == "n/a: 9/30 sampled days"
    # nine daily samples: the 7-day window has its baseline, the 30-day one does not
    assert r.filling_1m is True and r.filling_1w is False
    assert r.first_sample == ds[0] and r.latest_sample == ASOF and r.source == "fund-site"
    inputs = build(
        [ms(T1, "Card Alpha", "FNDA", F[0], "ETFs")],
        {
            F[0]: bundle(
                ds, [10.0] * 9, flow_1m_usd=r.flow_1m_usd, filling_1m=True, first_sample=ds[0]
            )
        },
    )
    (row,) = page(inputs).flows
    assert row.filling_1m and row.first_sample == ds[0] and row.flow_1m_usd.value is None


def test_a_sample_dated_after_the_as_of_is_never_read():
    ds = days(5)
    bars = [{"d": d, "close": 10.0} for d in ds]
    later = [{"d": ASOF + timedelta(days=2), "shares_out": 9e9, "source": "x", "source_ref": "x"}]
    r = fund_readings({}, _samples(ds, [100.0] * 5) + later, bars, ASOF)
    assert r.samples == 5 and r.latest_sample == ASOF and r.shares == 100.0


def test_a_fund_with_no_shares_sample_says_so_and_sorts_last_in_size_and_flow():
    ds = days(10)
    big = dict(
        size_usd=5e9,
        shares=1e8,
        latest_sample=ASOF,
        adv_usd_20d=Reading(2e7),
        flow_1m_usd=Reading(3e6),
    )
    small = dict(
        size_usd=1e6,
        shares=1e5,
        latest_sample=ASOF,
        adv_usd_20d=Reading(1e3),
        flow_1m_usd=Reading(-4e6),
    )
    none = dict(size_note="no shares sample yet", adv_usd_20d=Reading(9e9))
    inputs = build(
        [
            ms(T1, "Card Alpha", t, f, "ETFs")
            for t, f in (("FNDA", F[0]), ("FNDB", F[1]), ("FNDC", F[2]))
        ],
        {
            F[0]: bundle(ds, [10.0] * 10, **small),
            F[1]: bundle(ds, [10.0] * 10, **big),
            F[2]: bundle(ds, [10.0] * 10, **none),
        },
    )
    p = page(inputs)
    assert [r.ticker for r in p.sizes] == ["FNDB", "FNDA", "FNDC"]  # largest first; no sample last
    assert p.sizes[-1].size_note == "no shares sample yet"
    assert [r.ticker for r in p.flows] == [
        "FNDB",
        "FNDA",
        "FNDC",
    ]  # 1M dollars high to low, none last
    assert [r.ticker for r in p.trends] == ["FNDA", "FNDB", "FNDC"]  # A to Z


def test_range_position_reads_zero_at_the_low_and_one_hundred_at_the_high():
    from signals.display import range52w

    up = [{"d": d, "close": 10.0 + i} for i, d in enumerate(days(300))]
    at_high = fund_readings({"range_52w": range52w.compute(up, ASOF)}, [], up, ASOF)
    assert at_high.range_pos.value == pytest.approx(100.0)
    down = [{"d": d, "close": 400.0 - i} for i, d in enumerate(days(300))]
    at_low = fund_readings({"range_52w": range52w.compute(down, ASOF)}, [], down, ASOF)
    assert at_low.range_pos.value == pytest.approx(0.0)
    none = fund_readings({}, [], [], ASOF)
    assert none.range_pos.value is None and none.range_pos.note


# ---------------------------------------------------------------------------------------------------------
# panel 5 and the empty states
# ---------------------------------------------------------------------------------------------------------


def test_coverage_counts_cards_with_a_fund_and_lists_the_rest_a_to_z():
    ds = days(10)
    inputs = build(
        [
            ms(T1, "Zeta", "FNDA", F[0], "ETFs"),
            ms(T2, "Mu", "COB1", C[0]),
            ms(T3, "alpha", "COC1", C[1]),
        ],
        {F[0]: bundle(ds, geometric(10, 0.01))},
        cards=[(T1, "Zeta"), (T2, "Mu"), (T3, "alpha")],
    )
    cov = page(inputs).coverage
    assert (cov.cards_counted, cov.cards_with_fund, cov.funds) == (3, 1, 1)
    assert [c.thesis_name for c in cov.without] == ["alpha", "Mu"]
    assert cov.multi == ()


def test_no_fund_anywhere_is_a_quiet_empty_page_never_an_error():
    ds = days(10)
    inputs = build([ms(T1, "Card Alpha", "COA1", C[0])], {}, {C[0]: tape(ds, geometric(10, 0.01))})
    p = page(inputs)
    assert p.compare == p.flows == p.sizes == p.trends == ()
    assert p.coverage.cards_with_fund == 0 and len(p.coverage.without) == 1
    assert p.funds == 0 and p.sessions == 0
    empty = page(build([], {}, cards=[]))
    assert empty.coverage.cards_counted == 0 and not empty.iwm.available


def test_a_missing_iwm_tape_is_absent_with_its_reason_and_the_sessions_come_from_the_bars():
    ds = days(10)
    inputs = build(
        [ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"), ms(T1, "Card Alpha", "COA1", C[0])],
        {F[0]: bundle(ds, geometric(10, 0.01))},
        {C[0]: tape(ds, geometric(10, 0.01))},
    )
    p = page(inputs, "1w")
    assert p.iwm.available is False and "IWM" in (p.iwm.reason or "") and p.iwm.ret is None
    assert p.session_source == "companies" and p.compare[0].ret is not None


def test_panel_1_sorts_by_fund_minus_companies_highest_first_none_last():
    ds = days(10)
    inputs = build(
        [
            ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"),
            ms(T1, "Card Alpha", "FNDB", F[1], "ETFs"),
            ms(T1, "Card Alpha", "FNDC", F[2], "ETFs"),
            ms(T1, "Card Alpha", "COA1", C[0]),
        ],
        {
            F[0]: bundle(ds, geometric(10, 0.02)),
            F[1]: bundle(ds, geometric(10, 0.04)),
            F[2]: bundle(ds, [None] * 10),
        },
        {C[0]: tape(ds, geometric(10, 0.01))},
        {d: 100.0 for d in ds},
    )
    assert [r.ticker for r in page(inputs, "1w").compare] == ["FNDB", "FNDA", "FNDC"]


# ---------------------------------------------------------------------------------------------------------
# the page reads nothing off the network
# ---------------------------------------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_MODULES = [_ROOT / "dashboards" / "etfs.py", _ROOT / "app" / "routers" / "dashboards" / "etfs.py"]
# an ingest client, the EDGAR client, a fund source or the OpenFIGI client, or any HTTP/socket library
_BANNED_PARTS = ("ingest", "edgar", "figi", "fund_source", "funds")
_BANNED_ROOTS = ("requests", "httpx", "urllib", "urllib3", "http", "socket", "aiohttp", "anthropic")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    return names


@pytest.mark.parametrize("path", _MODULES, ids=lambda p: p.parent.name + "/" + p.name)
def test_the_page_imports_no_network_client(path):
    offenders = []
    for name in sorted(_imports(path)):
        parts = name.split(".")
        if parts[0] in _BANNED_ROOTS or any(p in _BANNED_PARTS for p in parts):
            offenders.append(name)
    assert not offenders, f"{path.name} imports a network-reaching module: {offenders}"


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}
    ds = days(30)

    def fake_read(conn, asof, known_at, clock, **_):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        return build(
            [ms(T1, "Card Alpha", "FNDA", F[0], "ETFs"), ms(T1, "Card Alpha", "COA1", C[0])],
            {F[0]: bundle(ds, geometric(30, 0.01))},
            {C[0]: tape(ds, geometric(30, 0.005))},
            {d: 100.0 for d in ds},
            names={F[0]: "Synthetic Fund A"},
        )

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_view_and_serves_every_window_off_one_cached_read(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/etfs", params={"asof": "2026-06-01"}).json()
    assert body["cache"] == "computed" and body["window"] == "1m"
    assert body["window_sessions"] == {"1w": 5, "1m": 21, "3m": 63}
    asof, known_at, clock = seen["view"]
    assert asof == ASOF and clock == "public" and known_at is not None
    (row,) = body["compare"]
    assert row["ticker"] == "FNDA" and len(row["fund_points"]) == body["sessions"] + 1
    assert set(body) >= {
        "flows",
        "sizes",
        "trends",
        "coverage",
        "flagged_funds",
        "flagged_companies",
    }

    for window in ("1w", "3m"):
        again = client.get(
            "/dashboards/etfs", params={"asof": "2026-06-01", "window": window}
        ).json()
        assert again["cache"] == "hit" and again["window"] == window
    assert seen["reads"] == 1  # one read for every window

    client.get("/dashboards/etfs", params={"asof": market_today().isoformat()})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2


def test_the_router_rejects_an_unknown_window(stub_client):
    client, _ = stub_client
    assert (
        client.get("/dashboards/etfs", params={"asof": "2026-06-01", "window": "1y"}).status_code
        == 422
    )


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a past as-of reads only what was public by then (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------


def _bar(db, sid, d: date, close: float, volume: float = 1000.0) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "close": close,
            "volume": volume,
            "valid_from": d,
            "recorded_at": datetime.combine(d, time.min, tzinfo=timezone.utc),
        },
    )


def _sample(db, sid, d: date, shares: float) -> None:
    append_fact(
        db,
        "fact_fund_shares",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "shares_out": shares,
            "source": "fund-site",
            "source_ref": "https://example.test/fund",
            "valid_from": d,
            "recorded_at": datetime.combine(d, time.min, tzinfo=timezone.utc),
        },
    )


def _master(db, ticker: str, kind: str) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "0009876545", kind, "2026-01-01"),
        )
    return sid


def test_a_sample_and_a_bar_dated_after_the_as_of_stay_invisible(db):
    """A fund (in a voting link, as a roster from before the ETFs link has it) and a company, 70 sessions each
    ending at the as-of. A shares sample dated two days AFTER the as-of and a bar dated three days after it exist
    in the store; the past view reads neither. The fund is found by its kind and kept out of the companies.
    """
    fund = _master(db, "FNDZ", "etf")
    co = _master(db, "COZ1", "equity")
    sessions = bar_days(ASOF, 70)
    for i, d in enumerate(sessions):
        _bar(db, fund, d, 20.0 + i * 0.1)
        _bar(db, co, d, 10.0 + i * 0.05)
    for d in sessions[-45:]:
        _sample(db, fund, d, 1_000_000.0)
    _sample(db, fund, ASOF + timedelta(days=2), 9_000_000.0)  # public after the as-of
    _bar(db, fund, ASOF + timedelta(days=3), 999.0)  # public after the as-of
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="ETF fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker="FNDZ", role="the fund", security_id=fund, segment="Core"),
            BasketMember(ticker="COZ1", role="the name", security_id=co, segment="Core"),
        ],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        body = (
            TestClient(app)
            .get("/dashboards/etfs", params={"asof": ASOF.isoformat(), "window": "1m"})
            .json()
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    assert body["dates"][-1] == ASOF.isoformat()
    (row,) = [r for r in body["compare"] if r["thesis_id"] == str(thesis.id)]
    assert row["ticker"] == "FNDZ" and row["companies"] == 1 and row["priced"] == 1
    # the as-of's own bar (20 + 69 x 0.1), not the 999 bar dated after it
    assert row["fund_points"][-1] / row["fund_points"][-2] == pytest.approx(26.9 / 26.8, rel=1e-4)
    (flow,) = [r for r in body["flows"] if r["security_id"] == str(fund)]
    assert flow["latest_sample"] == ASOF.isoformat() and flow["samples"] == 45
    assert flow["flow_1m_usd"]["value"] == 0.0  # the 9,000,000-share sample never counts
    (size,) = [r for r in body["sizes"] if r["security_id"] == str(fund)]
    assert size["shares"] == 1_000_000.0 and size["size_usd"] == pytest.approx(1_000_000.0 * 26.9)
    assert body["coverage"]["cards_with_fund"] >= 1
