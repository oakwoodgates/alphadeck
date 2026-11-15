"""V2 W2b: the get-data auto-confirm calls the machine writer (``platform_values.machine``; approved D2 (a)).

- TWO CLICKS AT ONCE write ONE value. Before V2 the route checked "is a count on file?" BEFORE its SEC call and
  never again, so two simultaneous clicks both passed the check and both appended. The writer now takes the
  company write lock (``db.write_lock``) and re-reads what is on file under it.
- Real logins (the ``world`` pattern of ``tests/app/test_workspace_isolation.py``): plain users A (W_A) and B
  (W_B) and a steward S (W_S), none on the data tenant. B's get-data on B's own card applies the share count AND
  the cash/burn value under the DATA tenant, and A's scored read shows both; B's second click writes nothing; a
  flagged company records a candidate (never a fact); one allowance unit per click; a company on none of B's
  cards is a 404 with the fixed sentence.

No test reaches SEC: the extraction seam is faked. DB-backed (the shared ``db`` fixture).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import date, timedelta

import psycopg
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from db.session import connect
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from domain.scopes import DATA_TENANT_ID
from tests.app.test_workbench_api import _auto_shares_candidate, _insert_security
from tests.app.test_workspace_isolation import _promote, world  # noqa: F401 — the fixture, by name

NOT_ON_YOUR_CARDS = "This company is not on any of your cards. Add it to a card first."


def _rows(table: str, sid) -> int:
    with connect() as c, c.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table} WHERE security_id = %s", (sid,)
        )  # noqa: S608
        return cur.fetchone()["n"]


def _own_connection() -> Iterator[psycopg.Connection]:
    """One connection PER REQUEST (the production shape, ``app.db_conn.get_conn``), so two requests really run
    side by side instead of sharing the test's one connection."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def test_two_simultaneous_get_data_clicks_write_one_share_count(db, monkeypatch):
    """Both requests finish their extraction before either writes (a barrier inside the faked SEC call). Before
    V2 both had already passed the "nothing on file" check, so both appended: TWO rows. Under the company lock
    the second re-reads, finds the first one's filing on file, and writes nothing."""
    from app.routers import workbench as wb

    sid = _insert_security(db, "NNE", name="Nano Nuclear Energy", cik="0001923891")
    barrier = threading.Barrier(2, timeout=20)

    def slow_periodic(client, cik):
        barrier.wait()  # both requests are past every check made before the SEC call
        return [_auto_shares_candidate()]

    def slow_fallback(client, cik):
        barrier.wait()
        return ExtractionResult(facts=[_auto_shares_candidate()])

    monkeypatch.setattr(wb, "extract_for_security", slow_periodic, raising=False)
    monkeypatch.setattr(wb, "extract_with_annual_fallback", slow_fallback)
    app.dependency_overrides[get_conn] = _own_connection
    answers: list = []

    def click() -> None:
        r = TestClient(app).post(
            "/workbench/facts/auto-confirm",
            json={"security_id": str(sid), "fact_type": "shares_outstanding"},
        )
        answers.append(r)

    try:
        threads = [threading.Thread(target=click) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
    finally:
        app.dependency_overrides.pop(get_conn, None)

    assert [r.status_code for r in answers] == [200, 200], [r.text for r in answers]
    assert _rows("fact_shares_outstanding", sid) == 1  # count the table, not the read
    assert sorted(r.json()["reason"] for r in answers) == ["already_on_file", "applied"]


# --- real logins: plain users A (W_A) and B (W_B), a steward S (W_S); none on the data tenant -----------------


def _b_card(world) -> None:  # noqa: F811
    """B puts the shared company on B's own card (get-data is for a company on one of your cards)."""
    r = _promote(world["b"], "Card of B", world["sid"])
    assert r.status_code == 200, r.text


def _fake_extraction(monkeypatch, *facts, **reasons) -> list:
    from app.routers import workbench as wb

    calls: list = []

    def fake(client, cik):
        calls.append(cik)
        return ExtractionResult(facts=list(facts), **reasons)

    def periodic_must_not_run(client, cik):
        raise AssertionError("the route reads annual filers too: extract_with_annual_fallback")

    monkeypatch.setattr(wb, "extract_with_annual_fallback", fake)
    monkeypatch.setattr(wb, "extract_for_security", periodic_must_not_run, raising=False)
    return calls


def _clean(asof: date) -> tuple[ExtractedFact, ExtractedFact]:
    ref = "https://www.sec.gov/Archives/edgar/data/1234567/devco-10q.htm"
    shares = ExtractedFact(
        fact_type="shares_outstanding",
        tier=Tier.AUTO,
        source="10-q-cover",
        source_ref=ref,
        event_date=asof - timedelta(days=30),
        value=50_000_000.0,
    )
    cash = ExtractedFact(
        fact_type="cash_burn",
        tier=Tier.AUTO,
        source="10-q",
        source_ref=ref,
        event_date=asof - timedelta(days=45),
        cash_usd=30_000_000.0,
        quarterly_burn_usd=3_000_000.0,
    )
    return shares, cash


def _get_data(c: TestClient, sid):
    return c.post(
        "/workbench/facts/auto-confirm",
        json={"security_id": str(sid), "fact_type": "shares_outstanding"},
    )


def _table_counts(db) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for t in ("fact_shares_outstanding", "fact_cash_burn", "extraction_candidate"):
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — fixed names
            out[t] = cur.fetchone()["n"]
    db.rollback()
    return out


def _user_id(db, email: str):
    with db.cursor() as cur:
        cur.execute("SELECT id FROM app_user WHERE lower(email) = lower(%s)", (email,))
        uid = cur.fetchone()["id"]
    db.rollback()
    return uid


def test_bs_get_data_applies_the_count_and_the_cash_in_the_data_tenant_and_a_reads_both(
    world, db, monkeypatch  # noqa: F811
):
    _b_card(world)
    _fake_extraction(monkeypatch, *_clean(world["asof"]))
    r = _get_data(world["b"], world["sid"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["applied"], body["reason"]) == (True, "applied")
    assert (body["cash_burn"]["applied"], body["cash_burn"]["reason"]) == (True, "applied")
    db.rollback()
    with db.cursor() as cur:
        for table in ("fact_shares_outstanding", "fact_cash_burn"):
            cur.execute(
                f"SELECT tenant_id, ratified_by FROM {table} WHERE security_id = %s",  # noqa: S608
                (world["sid"],),
            )
            assert [(x["tenant_id"], x["ratified_by"]) for x in cur.fetchall()] == [
                (DATA_TENANT_ID, "auto")
            ], table
    db.rollback()
    (a_m,) = (
        world["a"]
        .get(
            f"/workbench/theses/{world['a_id']}/scored",
            params={"asof": world["asof"].isoformat()},
        )
        .json()["members"]
    )
    assert a_m["market_cap"]["value"] is not None  # A reads the count B's click applied
    assert a_m["runway"]["value"] == 30.0  # 30M cash / (3M a quarter / 3) = 30 months


def test_bs_second_click_writes_nothing(world, db, monkeypatch):  # noqa: F811
    _b_card(world)
    _fake_extraction(monkeypatch, *_clean(world["asof"]))
    assert _get_data(world["b"], world["sid"]).json()["applied"] is True
    before = _table_counts(db)
    body = _get_data(world["b"], world["sid"]).json()
    assert (body["reason"], body["cash_burn"]["reason"]) == ("already_on_file", "already_on_file")
    assert _table_counts(db) == before


def test_a_flagged_company_records_a_candidate_never_a_fact(world, db, monkeypatch):  # noqa: F811
    _b_card(world)
    shares, _ = _clean(world["asof"])
    flagged = shares.model_copy(update={"tier": Tier.FLAG, "flags": ["dual-class"]})
    _fake_extraction(monkeypatch, flagged, runway_empty_reason="cash-generative")
    before = _table_counts(db)
    body = _get_data(world["b"], world["sid"]).json()
    assert (body["applied"], body["reason"], body["fact_id"]) == (False, "not_auto", None)
    assert body["candidate_id"] is not None
    assert (body["cash_burn"]["reason"], body["cash_burn"]["applied"]) == ("nothing_found", False)
    after = _table_counts(db)
    assert after["extraction_candidate"] == before["extraction_candidate"] + 2
    assert after["fact_shares_outstanding"] == before["fact_shares_outstanding"]
    assert after["fact_cash_burn"] == before["fact_cash_burn"]
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT DISTINCT tenant_id FROM extraction_candidate")
        assert [x["tenant_id"] for x in cur.fetchall()] == [DATA_TENANT_ID]
    db.rollback()


def test_one_allowance_unit_per_click(world, db, monkeypatch):  # noqa: F811
    from app import allowance

    _b_card(world)
    _fake_extraction(monkeypatch, *_clean(world["asof"]))
    b_id = _user_id(db, "b@iso.test")
    for n in (1, 2):
        assert _get_data(world["b"], world["sid"]).status_code == 200
        assert allowance.used(db, b_id, "auto_confirm") == n
        db.rollback()


def test_a_company_on_none_of_bs_cards_is_404_and_nothing_is_fetched_or_written(
    world, db, monkeypatch  # noqa: F811
):
    calls = _fake_extraction(monkeypatch, *_clean(world["asof"]))
    before = _table_counts(db)
    r = _get_data(world["b"], world["sid"])  # only A's card holds it
    assert r.status_code == 404
    assert r.json()["detail"] == NOT_ON_YOUR_CARDS
    assert calls == [] and _table_counts(db) == before


def test_a_busy_company_is_409_and_nothing_is_written(world, db, monkeypatch):  # noqa: F811
    from db.write_lock import acquire_company_lock
    from domain.settings import get_settings

    monkeypatch.setenv("ALPHADECK_INGEST_LOCK_REQUEST_WAIT_S", "0.3")
    get_settings.cache_clear()
    _b_card(world)
    _fake_extraction(monkeypatch, *_clean(world["asof"]))
    before = _table_counts(db)
    other = connect()
    try:
        hold = acquire_company_lock(other, world["sid"], wait_s=0)
        r = _get_data(world["b"], world["sid"])
        assert r.status_code == 409, r.text
        assert "Another ingest is writing this company's facts right now" in r.json()["detail"]
        assert _table_counts(db) == before
        hold.release()
        assert _get_data(world["b"], world["sid"]).json()["applied"] is True
    finally:
        other.close()


def test_an_extraction_failure_is_502_and_nothing_is_written(world, db, monkeypatch):  # noqa: F811
    from app.routers import workbench as wb

    _b_card(world)

    def down(client, cik):
        raise ConnectionError("SEC is unreachable")

    monkeypatch.setattr(wb, "extract_with_annual_fallback", down)
    monkeypatch.setattr(wb, "extract_for_security", down, raising=False)
    before = _table_counts(db)
    assert _get_data(world["b"], world["sid"]).status_code == 502
    assert _table_counts(db) == before  # a failure is never recorded as "nothing found"
