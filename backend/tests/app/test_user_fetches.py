"""Bounded user-triggered fetches (security hardening PR 1b, docs/AUTH.md §User-triggered fetches).

A plain user's click may still pull server-sourced data (SEC, the price vendor), but only:

- for a company on one of their OWN cards: extract, auto-confirm, etf-holdings and ingest-prices answer 404 with a
  fixed message for any other security, before any fetch;
- within a per-user DAILY ALLOWANCE (``app/allowance.py``, table ``user_action_count``): over it is 429 with a
  fixed message naming the reset; it resets on the next market day; a steward is not limited; 0 = no limit; the
  count is one atomic upsert, so racing requests cannot pass the limit;
- never changing the shared security master: the OTC price-symbol resolve and the draft's identity enrichment run
  for a steward only (a plain user's draft still SEES the identity, computed for the response);
- never holding the company write lock across the vendor fetch (that test lives in
  ``test_workbench_write_lock.py``).

Built WITHOUT the suite's signed-in override: a real plain user and a real steward, each on their own workspace,
signed in through ``POST /auth/login`` (the PR 1a pattern). No test reaches SEC, OpenFIGI or a price vendor: every
fetch seam is faked, and the vendor entry points raise if called.
"""

from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import allowance, auth
from app.deps import get_conn
from app.main import app
from db.session import connect
from domain.extraction import ExtractionResult
from domain.market_time import market_now
from domain.settings import get_settings
from repositories import thesis_repo
from tests.app.test_workbench_api import (
    _auto_shares_candidate,
    _decomp,
    _efts_page,
    _FakeEfts,
    _FakeLLM,
    _subs,
)
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
NOT_ON_YOUR_CARDS = "This company is not on any of your cards. Add it to a card first."
DATA = "00000000-0000-0000-0000-000000000001"


def _workspace(db, label: str) -> uuid.UUID:
    wid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace')", (wid, label)
        )
    return wid


def _client(email: str) -> TestClient:
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


def _security(db, ticker: str, *, cik: str | None, exchange: str | None = None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, exchange, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, '2026-01-01')",
            (sid, DATA, ticker, f"{ticker} Inc", cik, exchange),
        )
    db.commit()
    return sid


def _master_rows(db) -> list[tuple]:
    """Every master row, every column, in a stable order: the before/after proof that nothing changed."""
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM security_master ORDER BY id")
        rows = [tuple(sorted((k, str(v)) for k, v in r.items())) for r in cur.fetchall()]
    db.rollback()
    return rows


def _promote(c: TestClient, db, *security_ids: uuid.UUID) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, ticker FROM security_master WHERE id = ANY(%s)", (list(security_ids),)
        )
        ticker = {r["id"]: r["ticker"] for r in cur.fetchall()}
    db.rollback()
    r = c.post(
        "/workbench/theses",
        json={
            "name": f"Fetch card {uuid.uuid4().hex[:8]}",
            "narrative": "a synthetic narrative for the bounded-fetch proof",
            "basket": [
                {"ticker": ticker[sid], "role": "the name", "security_id": str(sid)}
                for sid in security_ids
            ],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


def _bar(d: date, close: float) -> dict:
    return {"d": d, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


class _FakeSource:
    requested: list[str] = []

    def get_bars(self, ticker, *, allow_live=True, force_refresh=False):
        _FakeSource.requested.append(ticker)
        return [_bar(date(2026, 7, 1), 10.0)]


@pytest.fixture
def fakes(monkeypatch):
    """Every fetch seam faked; the vendor entry points raise. Returns the call log."""
    from app.routers import workbench as wb
    from ingest.prices import ingest_security as price_leg

    calls: dict[str, list] = {"extract": [], "auto": [], "resolve": [], "fund": []}

    def boom(*a, **kw):
        raise AssertionError("a vendor was called")

    monkeypatch.setattr("securities.figi._fetch_live", boom)
    monkeypatch.setattr("securities.sec_tickers.cik_for", boom)

    def fake_extract(client, cik):
        calls["extract"].append(cik)
        return ExtractionResult(facts=[])

    def fake_auto(client, cik):
        calls["auto"].append(cik)
        return [_auto_shares_candidate()]

    def fake_fund(ticker, **kw):
        calls["fund"].append(ticker)
        return None  # "not in the SEC fund file": a 404 after the checks, with no SEC traffic

    monkeypatch.setattr(wb, "extract_with_annual_fallback", fake_extract)
    monkeypatch.setattr(wb, "extract_for_security", fake_auto)
    monkeypatch.setattr(wb.fund_tickers, "resolve", fake_fund)
    _FakeSource.requested = []
    monkeypatch.setattr(price_leg, "YahooPriceSource", _FakeSource)
    return calls


@pytest.fixture
def people(db, security_id, monkeypatch, fakes):
    """A plain user P and a steward S, each on their own workspace. P's card holds DEVCO (``security_id``) and
    an OTC name; ``other`` is on nobody's card."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    w_p, w_s = (_workspace(db, f"fetches {x}") for x in ("P", "S"))
    auth.create_user(db, email="p@uf.test", display_name="P", password=PASSWORD, workspace_id=w_p)
    auth.create_user(
        db,
        email="s@uf.test",
        display_name="S",
        password=PASSWORD,
        role="super_admin",
        workspace_id=w_s,
    )
    db.commit()
    otc = _security(db, "OTCX", cik="0007770001", exchange="OTC")
    other = _security(db, "OTHR", cik="0009990001")
    p, s = _client("p@uf.test"), _client("s@uf.test")
    p_card = _promote(p, db, security_id, otc)
    with (
        db.cursor() as cur
    ):  # the card's own promote may have spent fast-lane price pulls: start each test at 0
        cur.execute("DELETE FROM user_action_count")
    db.commit()
    yield {
        "p": p,
        "s": s,
        "p_card": p_card,
        "own": security_id,
        "otc": otc,
        "other": other,
        "p_id": auth_user_id(db, "p@uf.test"),
        "calls": fakes,
    }
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()


def auth_user_id(db, email: str) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute("SELECT id FROM app_user WHERE lower(email) = lower(%s)", (email,))
        return cur.fetchone()["id"]


@pytest.fixture
def limits(monkeypatch):
    """Set allowance limits for one test: ``limits(extract=2, price_pull=1)``."""

    def set_(**per_action: int) -> None:
        for action, n in per_action.items():
            monkeypatch.setenv(f"ALPHADECK_ALLOWANCE_{action.upper()}_PER_DAY", str(n))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


# --- the four fetch routes, by name ------------------------------------------------------------------------------


def _extract(c, sid):
    return c.get(f"/workbench/securities/{sid}/extract")


def _pull(c, sid):
    return c.post(f"/workbench/securities/{sid}/ingest-prices")


def _auto(c, sid):
    return c.post(
        "/workbench/facts/auto-confirm",
        json={"security_id": str(sid), "fact_type": "shares_outstanding"},
    )


def _holdings(c, sid):
    return c.get(f"/workbench/securities/{sid}/etf-holdings")


FETCHES = {"extract": _extract, "price_pull": _pull, "auto_confirm": _auto, "holdings": _holdings}


# --- own cards only ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", list(FETCHES))
def test_a_plain_users_fetch_of_a_security_on_none_of_their_cards_is_404(people, db, action):
    before = _master_rows(db)
    r = FETCHES[action](people["p"], people["other"])
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == NOT_ON_YOUR_CARDS
    assert people["calls"] == {"extract": [], "auto": [], "resolve": [], "fund": []}
    assert _FakeSource.requested == []  # nothing fetched
    assert _master_rows(db) == before
    # a refused request spends nothing; auto-confirm names its security in the BODY, so it is counted before the
    # card check (the route dependency cannot read the body)
    assert allowance.used(db, people["p_id"], action) == (1 if action == "auto_confirm" else 0)


def test_a_security_on_another_users_card_is_404_to_a_plain_user(people, db):
    s_card_member = _security(db, "STEW", cik="0008880001")
    _promote(people["s"], db, s_card_member)
    assert _extract(people["p"], s_card_member).status_code == 404


@pytest.mark.parametrize("action", list(FETCHES))
def test_a_plain_users_fetch_on_their_own_card_passes_the_check(people, action):
    r = FETCHES[action](people["p"], people["own"])
    # holdings: the faked fund file has no such fund (404 with ITS message, after the checks)
    if action == "holdings":
        assert r.status_code == 404 and r.json()["detail"] != NOT_ON_YOUR_CARDS
        assert people["calls"]["fund"] == ["DEVCO"]
    else:
        assert r.status_code == 200, r.text


def test_a_steward_may_fetch_any_security(people):
    assert _extract(people["s"], people["other"]).status_code == 200
    assert _pull(people["s"], people["other"]).status_code == 200


# --- no shared-master write on a plain user's click ---------------------------------------------------------------


def _auto_resolver(monkeypatch, calls):
    from app.routers import workbench as wb
    from securities.price_symbol import PriceSymbolProposal

    def fake(sec, **kw):
        calls.append(sec.ticker)
        return PriceSymbolProposal(tier="AUTO", proposed_symbol="OTCXD", why="251 vs 16")

    monkeypatch.setattr(wb, "resolve_price_symbol", fake)


def test_a_plain_users_otc_pull_never_resolves_or_writes_the_price_symbol(people, db, monkeypatch):
    calls: list[str] = []
    _auto_resolver(monkeypatch, calls)
    before = _master_rows(db)
    r = _pull(people["p"], people["otc"])
    assert r.status_code == 200, r.text
    assert calls == []  # the resolver never ran for a plain user
    assert _FakeSource.requested == [
        "OTCX"
    ]  # priced under the canonical ticker (the fail-open path)
    assert _master_rows(db) == before


def test_a_stewards_otc_pull_still_resolves_and_writes_the_price_symbol(people, db, monkeypatch):
    calls: list[str] = []
    _auto_resolver(monkeypatch, calls)
    r = _pull(people["s"], people["otc"])
    assert r.status_code == 200, r.text
    assert calls == ["OTCX"] and _FakeSource.requested == ["OTCXD"]
    with db.cursor() as cur:
        cur.execute("SELECT price_symbol FROM security_master WHERE id = %s", (people["otc"],))
        assert cur.fetchone()["price_symbol"] == "OTCXD"


@pytest.fixture
def inline_drafts(monkeypatch):
    from workbench import draft_jobs

    draft_jobs.reset_state()
    monkeypatch.setattr(
        draft_jobs, "_DEFAULT_EXECUTOR", lambda job, run: draft_jobs._run_job(job, run)
    )
    yield
    draft_jobs.reset_state()


def _draft_as(c: TestClient, db, card: uuid.UUID) -> dict:
    from app.deps import get_decompose_client, get_edgar_client, get_research_client
    from domain.enums import TermTier
    from domain.thesis import TermSetEntry

    thesis_repo.set_term_set(db, card, [TermSetEntry(term="nuclear", tier=TermTier.SIGNAL)])
    db.commit()
    edgar = _FakeEfts(
        {
            "efts/nuclear_0.json": _efts_page(
                ("0001849056", "Oklo Inc.  (OKLO)  (CIK 0001849056)"),
                ("0001849057", "Gone Corp  (GONE)  (CIK 0001849057)"),
            ),
            "submissions/CIK0001849056.json": _subs("1849056"),
            # no current listing -> 'inactive' -> the status gate downgrades it
            "submissions/CIK0001849057.json": _subs("1849057", exchanges=(), tickers=()),
        }
    )
    app.dependency_overrides[get_edgar_client] = lambda: edgar
    app.dependency_overrides[get_research_client] = lambda: _FakeLLM(research_returns=None)
    app.dependency_overrides[get_decompose_client] = lambda: _FakeLLM(
        returns=_decomp(("Oklo Inc.", "OKLO"), ("Gone Corp", "GONE"))
    )
    started = c.post(f"/workbench/theses/{card}/draft-chain")
    assert started.status_code == 202, started.text
    polled = c.get(f"/workbench/theses/{card}/draft-chain/jobs/{started.json()['job_id']}")
    assert polled.status_code == 200 and polled.json()["status"] == "done", polled.text
    return {p["name"]: p for p in polled.json()["result"]["placements"]}


def test_a_plain_users_draft_writes_no_master_row_and_still_carries_identity(
    people, db, inline_drafts
):
    oklo = _security(db, "OKLO", cik="0001849056")
    gone = _security(db, "GONE", cik="0001849057")
    before = _master_rows(db)
    by_name = _draft_as(people["p"], db, people["p_card"])
    assert _master_rows(db) == before  # the shared master is unchanged
    # ...and the draft reads exactly as an enriched one would (computed for the response, never stored)
    p = by_name["OKLO Inc"]
    assert p["status"] == "placed" and p["security_id"] == str(oklo)
    assert (p["sector"], p["exchange"], p["listing_status"]) == (
        "Electric Services",
        "Nasdaq",
        "active",
    )
    assert p["business_type"] == "utilities"
    g = by_name["GONE Inc"]
    assert (
        g["status"] == "ambiguous" and g["listing_status"] == "inactive"
    )  # the status gate still works
    assert [c["security_id"] for c in g["candidates"]] == [str(gone)]


def test_a_stewards_draft_still_stores_the_identity(people, db, inline_drafts):
    oklo = _security(db, "OKLO", cik="0001849056")
    _security(db, "GONE", cik="0001849057")
    s_card = _promote(people["s"], db, people["own"])
    _draft_as(people["s"], db, s_card)
    with db.cursor() as cur:
        cur.execute("SELECT sector, status FROM security_master WHERE id = %s", (oklo,))
        row = cur.fetchone()
    assert (row["sector"], row["status"]) == ("Electric Services", "active")


# --- the daily allowance ------------------------------------------------------------------------------------------

LIMIT_MSG_START = "Daily allowance reached:"


def test_the_allowance_answers_429_at_the_limit_with_a_fixed_message(people, db, limits):
    limits(extract=2)
    assert _extract(people["p"], people["own"]).status_code == 200
    assert _extract(people["p"], people["own"]).status_code == 200
    r = _extract(people["p"], people["own"])
    assert r.status_code == 429
    assert r.json()["detail"] == (
        "Daily allowance reached: 2 extracts a day. It resets at midnight America/New_York time."
    )
    assert len(people["calls"]["extract"]) == 2  # the refused request fetched nothing
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM user_action_count")
        assert cur.fetchone()["n"] == 1  # one row per (user, action, market day)
    assert allowance.used(db, people["p_id"], "extract") == 2  # a refusal is not counted


def test_each_action_has_its_own_allowance(people, limits):
    limits(extract=1, price_pull=1, auto_confirm=1)
    assert _extract(people["p"], people["own"]).status_code == 200
    assert _extract(people["p"], people["own"]).status_code == 429
    assert _pull(people["p"], people["own"]).status_code == 200
    assert _pull(people["p"], people["own"]).status_code == 429
    assert _auto(people["p"], people["own"]).status_code == 200
    assert _auto(people["p"], people["own"]).status_code == 429


def test_the_holdings_allowance(people, limits):
    limits(holdings=1)
    assert (
        _holdings(people["p"], people["own"]).status_code == 404
    )  # the faked fund file, after the count
    r = _holdings(people["p"], people["own"])
    assert r.status_code == 429 and r.json()["detail"].startswith(LIMIT_MSG_START)


def test_the_allowance_resets_on_the_next_market_day(people, limits, pin_market_clock):
    limits(extract=1)
    today = market_now()
    pin_market_clock(today)
    assert _extract(people["p"], people["own"]).status_code == 200
    assert _extract(people["p"], people["own"]).status_code == 429
    pin_market_clock(today + timedelta(days=1))
    assert _extract(people["p"], people["own"]).status_code == 200


def test_a_steward_is_not_limited(people, db, limits):
    limits(extract=1, price_pull=1)
    for _ in range(3):
        assert _extract(people["s"], people["other"]).status_code == 200
        assert _pull(people["s"], people["other"]).status_code == 200
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM user_action_count")
        assert cur.fetchone()["n"] == 0  # a steward is never counted


def test_zero_means_no_limit(people, db, limits):
    limits(extract=0)
    for _ in range(3):
        assert _extract(people["p"], people["own"]).status_code == 200


def test_the_draft_and_term_allowances(people, limits, inline_drafts):
    limits(chain_draft=1, term_run=1)
    card = people["p_card"]
    from app.deps import (
        get_decompose_client,
        get_edgar_client,
        get_keyword_client,
        get_research_client,
        get_tier_rec_client,
    )

    app.dependency_overrides[get_keyword_client] = lambda: _FakeLLM(returns=None)
    app.dependency_overrides[get_tier_rec_client] = lambda: _FakeLLM(returns=None)
    app.dependency_overrides[get_edgar_client] = lambda: _FakeEfts({})
    app.dependency_overrides[get_research_client] = lambda: _FakeLLM(research_returns=None)
    app.dependency_overrides[get_decompose_client] = lambda: _FakeLLM(returns=None)
    # the first term run passes the allowance (whatever the fake model answers), the second is refused
    assert people["p"].post(f"/workbench/theses/{card}/recommend-tiers").status_code != 429
    r = people["p"].post(f"/workbench/theses/{card}/terms", json={})
    assert r.status_code == 429 and "term or tier runs" in r.json()["detail"]
    assert people["p"].post(f"/workbench/theses/{card}/draft-chain").status_code != 429
    r = people["p"].post(f"/workbench/theses/{card}/draft-chain")
    assert r.status_code == 429 and "chain drafts" in r.json()["detail"]


def test_the_count_is_atomic_under_concurrent_requests(people, limits):
    """Twenty racing consumers on their own connections, a limit of five: exactly five pass."""
    limits(price_pull=5)
    uid = people["p_id"]
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker():
        conn = connect()
        try:
            barrier.wait()
            ok = allowance.consume(conn, uid, "price_pull")
            with lock:
                results.append(ok)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 5
    conn = connect()
    try:
        assert allowance.used(conn, uid, "price_pull") == 5
    finally:
        conn.close()


def test_consume_refuses_a_batch_larger_than_what_is_left(people, db, limits):
    limits(price_pull=3)
    assert allowance.consume(db, people["p_id"], "price_pull", n=2) is True
    assert (
        allowance.consume(db, people["p_id"], "price_pull", n=2) is False
    )  # 2 + 2 > 3: nothing counted
    assert allowance.used(db, people["p_id"], "price_pull") == 2
    assert allowance.consume(db, people["p_id"], "price_pull", n=1) is True
    assert allowance.consume(db, people["p_id"], "price_pull", n=5) is False


def test_the_settings_defaults():
    s = get_settings()
    assert (
        s.allowance_extract_per_day,
        s.allowance_price_pull_per_day,
        s.allowance_auto_confirm_per_day,
        s.allowance_holdings_per_day,
        s.allowance_chain_draft_per_day,
        s.allowance_term_run_per_day,
    ) == (5000, 5000, 2000, 1000, 100, 200)


# --- the promote's fast lane spends price pulls -------------------------------------------------------------------


@pytest.fixture
def lane(monkeypatch):
    """The fast lane taken for any promote (outside the window, few cold names); record the kicks."""
    from app.routers import workbench as wb
    from pipeline import onboarding

    kicks: list[set] = []
    monkeypatch.setattr(
        wb,
        "_plan_lane",
        lambda conn, tid, tenant, ids: onboarding.PromotePlan(frozenset(ids), True),
    )
    monkeypatch.setattr(
        wb, "_kick_member_ingest", lambda tid, ids, tenant_id=None: kicks.append(set(ids))
    )
    return kicks


def test_a_plain_users_fast_lane_counts_its_new_names_as_price_pulls(people, db, limits, lane):
    limits(price_pull=10)
    a, b = _security(db, "AAAA", cik="0005550001"), _security(db, "BBBB", cik="0005550002")
    before = allowance.used(db, people["p_id"], "price_pull")
    r = people["p"].post(
        "/workbench/theses",
        json={
            "name": "lane card",
            "narrative": "the fast lane spends price pulls",
            "basket": [
                {"ticker": "AAAA", "role": "x", "security_id": str(a)},
                {"ticker": "BBBB", "role": "x", "security_id": str(b)},
            ],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    assert lane[-1] == {a, b}
    assert allowance.used(db, people["p_id"], "price_pull") == before + 2  # one per new name


def test_a_spent_allowance_sends_the_fast_lane_to_the_queue(people, db, limits, lane):
    limits(price_pull=1)
    lane.clear()
    a, b = _security(db, "AAAA", cik="0005550001"), _security(db, "BBBB", cik="0005550002")
    r = people["p"].post(
        "/workbench/theses",
        json={
            "name": "lane card",
            "narrative": "the fast lane spends price pulls",
            "basket": [
                {"ticker": "AAAA", "role": "x", "security_id": str(a)},
                {"ticker": "BBBB", "role": "x", "security_id": str(b)},
            ],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text  # the promote itself never fails over the allowance
    assert lane == []  # no fast-lane ingest
    body = r.json()
    assert body["ingest"] is None
    assert body["onboarding"]["new_members"] == 2
    assert "price pull allowance" in body["onboarding"]["reason"]


def test_a_stewards_fast_lane_is_not_counted(people, db, limits, lane):
    limits(price_pull=1)
    a = _security(db, "AAAA", cik="0005550001")
    _promote(people["s"], db, a, people["own"])
    assert lane[-1] == {a, people["own"]}


# --- every fetch route carries its allowance ----------------------------------------------------------------------

_CHARGED = {
    ("GET", "/workbench/securities/{security_id}/extract"): "extract",
    ("POST", "/workbench/securities/{security_id}/ingest-prices"): "price_pull",
    ("POST", "/workbench/facts/auto-confirm"): "auto_confirm",
    ("GET", "/workbench/securities/{security_id}/etf-holdings"): "holdings",
    ("POST", "/workbench/theses/{thesis_id}/draft-chain"): "chain_draft",
    ("POST", "/workbench/theses/{thesis_id}/terms"): "term_run",
    ("POST", "/workbench/theses/{thesis_id}/recommend-tiers"): "term_run",
}


def test_each_fetch_route_carries_its_allowance_dependency():
    seen = {}
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for method in r.methods:
            for dep in r.dependant.dependencies:
                action = getattr(dep.call, "allowance_action", None)
                if action is not None:
                    seen[(method, r.path)] = action
    assert seen == _CHARGED


def test_the_day_is_the_market_day(monkeypatch):
    from domain import market_time

    instant = datetime(2026, 11, 12, 23, 30, tzinfo=market_time.market_tz())
    monkeypatch.setattr(market_time, "market_now", lambda tz=None: instant)
    assert allowance.market_day() == date(2026, 11, 12)
