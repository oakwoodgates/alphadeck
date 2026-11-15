"""V2 W4d: "Extract now", the steward's per-company extraction (``POST /admin/platform-values/{id}/extract``).

Proven with REAL sign-ins (the ``world`` pattern of ``tests/app/test_admin_platform_values_api.py``): plain users A
(W_A) and B (W_B) each hold the shared DATA company on a card; S, the steward, sits on W_S. None of them is the data
tenant, so a write aimed at the caller's workspace cannot hide.

No SEC request anywhere: the route's SEC client is a dependency, overridden with a FAKE client that serves the
company's submissions index and counts every read, and the extraction itself is monkeypatched at
``platform_values.extraction`` (the way ``tests/platform_values/test_extraction_run.py`` does). Every test COUNTS
THE TABLES before and after.

What is pinned:
- a plain user gets 403 and nothing is written in any table, and nothing is fetched;
- the steward's click runs ``extract_one`` with ``run_kind='steward'``: exactly one attempt row in the data tenant,
  and the answer is the company's full values plus a typed summary per value type (applied, flagged, nothing found,
  already on file, pinned, failed with its one-line reason);
- an extraction failure is NOT a 5xx: the attempt row is the record and the answer says it failed and why;
- a busy company answers 409 with the existing sentence (one ``busy`` attempt row, no value written);
- an unknown company 404, a company with no CIK 404 and a fund 422, each with nothing written and nothing fetched;
- the company lock is free while the extraction fetches;
- the company read carries the recent attempts as an ADDITIVE field;
- the route's default client is the RECURRING one (the five-minute dial).

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn, get_platform_sec_client
from app.errors import (
    PLATFORM_VALUE_FUND,
    PLATFORM_VALUE_NO_CIK,
    PLATFORM_VALUE_NOT_FOUND,
)
from app.main import app
from app.routers.workbench import COMPANY_BUSY
from dashboards.cache import CACHE
from db.session import connect
from db.write_lock import CompanyLockBusy, acquire_company_lock
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from domain.market_time import market_now, market_today
from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from platform_values import extraction as ex
from repositories import platform_value_repo as repo
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
BASE = "/admin/platform-values"
CIK = 1234567  # the conftest ``security_id``'s CIK
NO_CACHE = Path(__file__).resolve().parent / "no-such-edgar-cache"
TABLES = (
    "fact_shares_outstanding",
    "fact_cash_burn",
    "extraction_candidate",
    "platform_value_decision",
    "steward_pin",
    "platform_extraction_attempt",
)


def _now() -> datetime:
    """The clock, read INSIDE a test (never at import)."""
    return market_now().astimezone(timezone.utc)


def _period() -> date:
    return market_today() - timedelta(days=75)


def _acc() -> str:
    return "0001234567-26-000001"


def _ref() -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{CIK}/{_acc()}/d.htm"


# --- fakes (no network) ------------------------------------------------------------------------------------------


class FakeClient:
    """Serves the company's submissions index (one 10-Q); records every read; counts a live fetch per read."""

    def __init__(self) -> None:
        self.cache_dir = NO_CACHE
        self.reads: list[str] = []
        self.live_fetches = 0

    def get_json(self, url: str, cache_key: str, **_: Any) -> dict:
        self.reads.append(cache_key)
        self.live_fetches += 1
        when = _now() - timedelta(days=60)
        return {
            "filings": {
                "recent": {
                    "form": ["10-Q"],
                    "accessionNumber": [_acc()],
                    "primaryDocument": ["d.htm"],
                    "filingDate": [when.date().isoformat()],
                    "reportDate": [""],
                    "acceptanceDateTime": [when.strftime("%Y-%m-%dT%H:%M:%S.000Z")],
                }
            }
        }

    def get_text(self, url: str, cache_key: str, **_: Any) -> str:  # pragma: no cover
        raise AssertionError(f"unexpected document read {cache_key}")


class FakeExtractor:
    """Stands in for ``extract_with_annual_fallback``: one result (or exception); records each call and whether the
    company lock was free while it 'fetched'."""

    def __init__(self, result: ExtractionResult | BaseException, sid: uuid.UUID) -> None:
        self.result = result
        self.sid = sid
        self.calls: list[int] = []
        self.lock_free: list[bool] = []
        self.probe = (
            False  # True: check from a second connection that the company is free while it fetches
        )

    def __call__(self, client, cik, *, values_only: bool = False, **_: Any) -> ExtractionResult:
        self.calls.append(int(cik))
        if self.probe:
            other = connect()
            try:
                acquire_company_lock(other, self.sid, wait_s=0).release()
                self.lock_free.append(True)
            except CompanyLockBusy:
                self.lock_free.append(False)
            finally:
                other.close()
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _shares(tier: Tier = Tier.AUTO) -> ExtractedFact:
    return ExtractedFact(
        fact_type="shares_outstanding",
        tier=tier,
        source="10-q-cover",
        source_ref=_ref(),
        event_date=_period(),
        value=30_000_000.0,
        note="Cover-page shares outstanding (single class).",
    )


def _cash() -> ExtractedFact:
    return ExtractedFact(
        fact_type="cash_burn",
        tier=Tier.AUTO,
        source="10-q",
        source_ref=_ref(),
        event_date=_period(),
        cash_usd=30_000_000.0,
        quarterly_burn_usd=3_000_000.0,
        note="Cash; operating cash flow.",
    )


# --- the world ---------------------------------------------------------------------------------------------------


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


def _promote(c: TestClient, name: str, security_id: uuid.UUID) -> None:
    r = c.post(
        "/workbench/theses",
        json={
            "name": name,
            "narrative": f"{name}: a synthetic narrative for the extract proof",
            "basket": [{"ticker": "DEVCO", "role": "the name", "security_id": str(security_id)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def world(db, security_id, pinned_today, monkeypatch):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    fake_client = FakeClient()
    app.dependency_overrides[get_platform_sec_client] = lambda: fake_client
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("ALPHADECK_INGEST_LOCK_REQUEST_WAIT_S", "0.3")
    get_settings.cache_clear()
    CACHE.clear()
    w_a, w_b, w_s = (_workspace(db, f"extract proof {x}") for x in ("A", "B", "S"))
    auth.create_user(db, email="a@ex.test", display_name="A", password=PASSWORD, workspace_id=w_a)
    auth.create_user(db, email="b@ex.test", display_name="B", password=PASSWORD, workspace_id=w_b)
    auth.create_user(
        db,
        email="s@ex.test",
        display_name="S",
        password=PASSWORD,
        role="super_admin",
        workspace_id=w_s,
    )
    db.commit()
    a, b = _client("a@ex.test"), _client("b@ex.test")
    _promote(a, f"A card {uuid.uuid4().hex[:8]}", security_id)
    _promote(b, f"B card {uuid.uuid4().hex[:8]}", security_id)
    db.commit()

    def install(result: ExtractionResult | BaseException) -> FakeExtractor:
        fake = FakeExtractor(result, security_id)
        monkeypatch.setattr(ex, "extract_with_annual_fallback", fake)
        return fake

    yield {
        "sid": security_id,
        "a": a,
        "b": b,
        "s": _client("s@ex.test"),
        "client": fake_client,
        "install": install,
    }
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()


def _counts(db) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for t in TABLES:
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — a fixed list
            out[t] = cur.fetchone()["n"]
    db.rollback()
    return out


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {t: after[t] - before[t] for t in after if after[t] != before[t]}


def _attempts(db, sid: uuid.UUID) -> list[dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM platform_extraction_attempt WHERE security_id = %s "
            "ORDER BY recorded_at DESC, id DESC",
            (sid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return rows


# --- a plain user: 403, nothing written, nothing fetched ---------------------------------------------------------


def test_a_plain_user_gets_403_and_nothing_is_written_or_fetched(world, db):
    fake = world["install"](ExtractionResult(facts=[_shares(), _cash()]))
    before = _counts(db)
    for who in ("a", "b"):
        r = world[who].post(f"{BASE}/{world['sid']}/extract")
        assert r.status_code == 403, f"{who}: {r.status_code} {r.text}"
    assert _counts(db) == before
    assert fake.calls == [] and world["client"].reads == []


# --- the steward: one attempt, the company's values and a summary per value type --------------------------------


def test_an_auto_result_applies_both_values_and_records_one_steward_attempt(world, db):
    s, sid = world["s"], world["sid"]
    fake = world["install"](ExtractionResult(facts=[_shares(), _cash()]))
    fake.probe = True
    before = _counts(db)

    r = s.post(f"{BASE}/{sid}/extract")

    assert r.status_code == 200, r.text
    got = r.json()
    attempt = got["attempt"]
    assert (attempt["status"], attempt["error"]) == ("done", None)
    assert (attempt["shares"]["outcome"], attempt["cash_burn"]["outcome"]) == ("applied", "applied")
    assert attempt["shares"]["written"] is True and attempt["shares"]["fact_id"]
    assert attempt["live_fetches"] == 1  # the one submissions read (the extraction is faked)
    company = got["company"]
    assert (company["security_id"], company["shares"]["state"]) == (str(sid), "from_filing")
    assert company["shares"]["value"]["shares"] == 30_000_000
    assert company["cash_burn"]["state"] == "from_filing"
    assert company["attempts"][0]["id"] == attempt["attempt_id"]
    assert _delta(before, _counts(db)) == {
        "fact_shares_outstanding": 1,
        "fact_cash_burn": 1,
        "platform_extraction_attempt": 1,
    }
    (row,) = _attempts(db, sid)
    assert (row["run_kind"], row["status"], row["tenant_id"]) == ("steward", "done", DATA_TENANT_ID)
    assert (row["shares_reason"], row["cash_reason"]) == ("applied", "applied")
    assert fake.lock_free == [True]  # nobody held the company while it fetched


def test_a_flag_result_is_a_candidate_awaiting_the_steward_and_a_second_click_writes_none(
    world, db
):
    s, sid = world["s"], world["sid"]
    world["install"](ExtractionResult(facts=[_shares(Tier.FLAG), _cash()]))

    first = s.post(f"{BASE}/{sid}/extract")
    assert first.status_code == 200, first.text
    a1 = first.json()["attempt"]
    assert (a1["shares"]["outcome"], a1["shares"]["written"]) == ("flagged", True)
    assert a1["shares"]["candidate_id"]
    assert first.json()["company"]["shares"]["state"] == "awaiting_steward"
    mid = _counts(db)

    second = s.post(f"{BASE}/{sid}/extract")
    assert second.status_code == 200, second.text
    a2 = second.json()["attempt"]
    # the same flag is already waiting: no second candidate; the applied cash value is already on file
    assert (a2["shares"]["outcome"], a2["shares"]["written"]) == ("flagged", False)
    assert a2["shares"]["candidate_id"] == a1["shares"]["candidate_id"]
    assert a2["cash_burn"]["outcome"] == "already_on_file"
    assert _delta(mid, _counts(db)) == {"platform_extraction_attempt": 1}


def test_nothing_found_and_a_pinned_company(world, db):
    s, sid = world["s"], world["sid"]
    r = s.post(f"{BASE}/{sid}/pin", json={"fact_type": "shares_outstanding"})
    assert r.status_code == 200, r.text
    world["install"](ExtractionResult(facts=[_shares()], runway_empty_reason="cash-generative"))

    r = s.post(f"{BASE}/{sid}/extract")

    assert r.status_code == 200, r.text
    attempt = r.json()["attempt"]
    assert attempt["shares"]["outcome"] == "pinned"  # a clean value held for the steward
    assert attempt["cash_burn"]["outcome"] == "nothing_found"
    assert attempt["cash_burn"]["reason"] == "cash-generative"
    company = r.json()["company"]
    assert company["shares"]["state"] == "awaiting_steward"
    assert (company["cash_burn"]["state"], company["cash_burn"]["reason"]) == (
        "no_value",
        "cash-generative",
    )


# --- a failure is the attempt's record, never a 5xx --------------------------------------------------------------


def test_an_extraction_failure_answers_200_with_its_one_line_reason_and_writes_only_the_attempt(
    world, db
):
    s, sid = world["s"], world["sid"]
    _flag_waiting(db, sid)
    world["install"](RuntimeError("set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent)"))
    before = _counts(db)

    r = s.post(f"{BASE}/{sid}/extract")

    assert r.status_code == 200, r.text
    attempt = r.json()["attempt"]
    assert (attempt["status"], attempt["error"]) == ("error", "no SEC User-Agent set")
    for part in ("shares", "cash_burn"):
        assert attempt[part] == {
            "outcome": "failed",
            "reason": "no SEC User-Agent set",
            "written": False,
            "fact_id": None,
            "candidate_id": None,
        }
    # the failure hides nothing: the flag waiting before still waits
    assert r.json()["company"]["shares"]["state"] == "awaiting_steward"
    assert _delta(before, _counts(db)) == {"platform_extraction_attempt": 1}
    (row,) = _attempts(db, sid)
    assert (row["status"], row["run_kind"], row["error"]) == (
        "error",
        "steward",
        "no SEC User-Agent set",
    )
    shown = r.json()["company"]["attempts"][0]
    assert (shown["status"], shown["error"], shown["run_kind"]) == (
        "error",
        "no SEC User-Agent set",
        "steward",
    )


def _flag_waiting(db, sid: uuid.UUID) -> None:
    repo.append_candidate(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        outcome="flag",
        flags=["dual-class"],
        source="cover",
        source_ref=_ref(),
        event_date=_period(),
        shares=1.0,
    )
    db.commit()


def test_a_busy_company_answers_409_with_the_busy_sentence_and_writes_no_value(world, db):
    s, sid = world["s"], world["sid"]
    world["install"](ExtractionResult(facts=[_shares(), _cash()]))
    before = _counts(db)
    other = connect()
    try:
        hold = acquire_company_lock(other, sid, wait_s=0)
        r = s.post(f"{BASE}/{sid}/extract")
        hold.release()
    finally:
        other.close()
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith(COMPANY_BUSY)
    # the attempt row is extract_one's record that another writer held it (not a failure); no value written
    assert _delta(before, _counts(db)) == {"platform_extraction_attempt": 1}
    assert _attempts(db, sid)[0]["status"] == "busy"


# --- refused before any fetch ------------------------------------------------------------------------------------


def test_unknown_company_no_cik_and_a_fund_are_refused_with_nothing_written_or_fetched(world, db):
    s = world["s"]
    fake = world["install"](ExtractionResult(facts=[_shares()]))
    no_cik, fund = uuid.uuid4(), uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, 'NOCIK', 'No Cik Corp', NULL, 'equity', '2026-01-01'), "
            "       (%s, %s, 'FUND', 'A Fund', '0000000777', 'etf', '2026-01-01')",
            (no_cik, DATA_TENANT_ID, fund, DATA_TENANT_ID),
        )
    db.commit()
    before = _counts(db)
    for target, status, sentence in (
        (uuid.uuid4(), 404, PLATFORM_VALUE_NOT_FOUND),
        (no_cik, 404, PLATFORM_VALUE_NO_CIK),
        (fund, 422, PLATFORM_VALUE_FUND),
    ):
        r = s.post(f"{BASE}/{target}/extract")
        assert r.status_code == status, f"{target}: {r.text}"
        assert r.json()["detail"].startswith(sentence)
    r = s.post(f"{BASE}/not-a-uuid/extract")
    assert r.status_code == 422
    assert _counts(db) == before
    assert fake.calls == [] and world["client"].reads == []


# --- the company read: the recent attempts, additive -------------------------------------------------------------


def test_the_company_read_lists_recent_attempts_newest_first(world, db):
    s, sid = world["s"], world["sid"]
    got = s.get(f"{BASE}/{sid}").json()
    assert got["attempts"] == []
    for key in (
        "security_id",
        "ticker",
        "name",
        "shares",
        "cash_burn",
    ):  # nothing renamed or removed
        assert key in got
    world["install"](ExtractionResult(facts=[], empty_reason="no-annual-filing"))
    assert s.post(f"{BASE}/{sid}/extract").status_code == 200
    world["install"](TimeoutError("read timed out"))
    assert s.post(f"{BASE}/{sid}/extract").status_code == 200

    attempts = s.get(f"{BASE}/{sid}").json()["attempts"]

    assert [(x["status"], x["run_kind"]) for x in attempts] == [
        ("error", "steward"),
        ("done", "steward"),
    ]
    newest, older = attempts
    assert newest["error"] == "extraction failed (TimeoutError)"
    assert (newest["shares_reason"], newest["cash_reason"]) == (None, None)
    assert (older["shares_reason"], older["cash_reason"]) == ("nothing_found", "nothing_found")
    assert newest["recorded_at"] >= older["recorded_at"]
    assert set(newest) == {
        "id",
        "recorded_at",
        "run_kind",
        "status",
        "shares_reason",
        "cash_reason",
        "error",
        "detail",
    }


# --- the client ----------------------------------------------------------------------------------------------------


def test_the_routes_sec_client_is_the_recurring_one():
    """The default dependency builds the RECURRING client (the five-minute dial), so a click reads the cached
    companyfacts unless the company filed since (the filing-gated class), instead of re-downloading it. Building
    the client makes no request."""
    client = get_platform_sec_client()
    assert isinstance(client, EdgarClient)
    assert client.cache_ttl_s == RECURRING_CACHE_TTL_S and client.recurring is True
    assert client.allow_live is True
