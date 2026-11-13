"""Fixed error text at the routes a user can reach (security hardening PR 2, item 2; docs/AUTH.md §Errors).

A route that catches an upstream, file or configuration failure answers a fixed sentence for that failure plus a
reference id, through the one helper ``app.errors.fail``; the exception is logged under that id. A plain user sees
only the sentence and the reference. A steward still sees the underlying error after them (operator decision
2026-11-12). Each test below covers both callers for one class of failure.
"""

from __future__ import annotations

import re
import uuid

import psycopg
import pytest

from app.main import app  # noqa: F401  (the client fixture's app)
from domain.settings import get_settings
from tests.app.test_errors import PLAIN, as_user
from tests.auth_fixtures import TEST_STEWARD

REF = re.compile(r"Reference [0-9a-f]{8}\.")
LEAK = "https://www.sec.gov/cgi-bin/secret?x=1 C:/srv/cache/edgar/0000.json"


def _check(r, status: int, sentence: str, *, steward: bool) -> None:
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail.startswith(sentence), detail
    assert REF.search(detail), detail
    if steward:
        assert "C:/srv/cache" in detail, detail
    else:
        assert "C:/srv/cache" not in detail and "sec.gov" not in detail, detail


def _check_text(detail: str, sentence: str, *, steward: bool) -> None:
    assert detail.startswith(sentence), detail
    assert REF.search(detail), detail
    assert ("C:/srv/cache" in detail) is steward, detail


CALLERS = [pytest.param(PLAIN, False, id="plain"), pytest.param(TEST_STEWARD, True, id="steward")]


# --- an upstream fetch (SEC) ------------------------------------------------------------------------------------------


@pytest.fixture
def carded_security(client, db, monkeypatch) -> uuid.UUID:
    """A company on a card in the test workspace (so a plain user's own-cards check passes), with the extract
    allowance off (the plain test user has no user row to count against)."""
    monkeypatch.setattr(get_settings(), "allowance_extract_per_day", 0)
    monkeypatch.setattr(get_settings(), "allowance_holdings_per_day", 0)
    monkeypatch.setattr(get_settings(), "allowance_price_pull_per_day", 0)
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) VALUES "
            "(%s, '00000000-0000-0000-0000-000000000001', 'ERRT', 'Err Test Inc', '0000999001', '2026-01-01')",
            (sid,),
        )
    db.commit()
    as_user(TEST_STEWARD)
    r = client.post(
        "/workbench/theses",
        json={
            "name": "Error text card",
            "narrative": "n",
            "basket": [{"ticker": "ERRT", "role": "r", "security_id": str(sid)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    return sid


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_an_sec_failure_on_extract_is_a_fixed_sentence(
    client, carded_security, monkeypatch, user, steward
):
    from app.routers import workbench as wb

    def boom(client, cik):
        raise RuntimeError(LEAK)

    monkeypatch.setattr(wb, "extract_with_annual_fallback", boom)
    as_user(user)
    r = client.get(f"/workbench/securities/{carded_security}/extract")
    _check(r, 502, wb.SEC_FILINGS_UNREADABLE, steward=steward)


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_a_price_source_failure_is_a_fixed_sentence(
    client, carded_security, monkeypatch, user, steward
):
    from app.routers import workbench as wb
    from ingest.prices import ingest_security as mod

    class _Boom:
        def get_bars(self, ticker, *, allow_live=True, force_refresh=False):
            raise RuntimeError(LEAK)

    monkeypatch.setattr(mod, "YahooPriceSource", _Boom)
    as_user(user)
    r = client.post(f"/workbench/securities/{carded_security}/ingest-prices")
    _check(r, 502, wb.PRICE_SOURCE_UNREACHABLE, steward=steward)


# --- a file on the server ---------------------------------------------------------------------------------------------


@pytest.fixture
def thesis_id(client) -> uuid.UUID:
    as_user(TEST_STEWARD)
    r = client.post(
        "/workbench/theses",
        json={"name": "Session card", "narrative": "n", "basket": [], "segments": []},
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


@pytest.mark.parametrize(("user", "steward"), CALLERS)
@pytest.mark.parametrize(
    ("method", "fn", "key"),
    [
        ("get", "read_session", "read"),
        ("put", "write_session", "write"),
        ("delete", "delete_session", "delete"),
    ],
)
def test_a_triage_session_file_failure_is_a_fixed_sentence(
    client, thesis_id, monkeypatch, user, steward, method, fn, key
):
    from app.routers import workbench as wb
    from workbench import triage_store

    def boom(*a, **kw):
        raise OSError(13, "Permission denied", "C:/srv/cache/triage_sessions/x/latest.json")

    monkeypatch.setattr(triage_store, fn, boom)
    as_user(user)
    url = f"/workbench/theses/{thesis_id}/triage-session"
    if method == "put":
        r = client.put(url, json={"schema_version": 1, "state": {}})
    else:
        r = getattr(client, method)(url)
    _check(r, 500, wb.TRIAGE_SESSION_FAILED[key], steward=steward)


# --- a stored artifact that no longer validates -----------------------------------------------------------------------


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_an_incompatible_saved_run_is_a_fixed_sentence(
    client, thesis_id, monkeypatch, user, steward
):
    from app.routers import workbench as wb

    monkeypatch.setattr(get_settings(), "run_loader_enabled", True)
    monkeypatch.setattr(
        wb.run_loader, "read_run", lambda tid, rid: {"thesis_id": "C:/srv/cache/not-a-uuid"}
    )
    as_user(user)
    r = client.get(f"/workbench/theses/{thesis_id}/runs/some-run")
    _check(r, 422, wb.SAVED_RUN_INCOMPATIBLE, steward=steward)


# --- a server setting -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_a_malformed_schedule_setting_is_a_fixed_sentence(client, monkeypatch, user, steward):
    from app import errors
    from app.routers import clock

    def boom():
        raise ValueError("ALPHADECK_CRON_AT='C:/srv/cache' is not HH:MM")

    monkeypatch.setattr(clock, "market_clock", boom)
    as_user(user)
    r = client.get("/clock")
    _check(r, 500, errors.SCHEDULE_SETTING_MALFORMED, steward=steward)


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_a_malformed_schedule_setting_on_the_scoreboard_is_a_fixed_sentence(
    client, monkeypatch, user, steward
):
    from app import errors

    monkeypatch.setattr(get_settings(), "cron_run_at", "C:/srv/cache")
    as_user(user)
    r = client.get("/scoreboard", params={"asof": "2026-07-01"})
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert detail.startswith(errors.SCHEDULE_SETTING_MALFORMED)
    assert ("C:/srv/cache" in detail) is steward


# --- the steward's run-daily: the database connection text ------------------------------------------------------------


def test_run_daily_unreachable_database_is_a_fixed_sentence(client, monkeypatch):
    from app.routers import admin

    def boom():
        raise psycopg.OperationalError('connection to server at "10.0.0.5", port 5432 failed')

    monkeypatch.setattr(admin, "probe_pass_lock", boom)
    as_user(TEST_STEWARD)
    r = client.post("/admin/run-daily")
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail.startswith(admin.RUN_LOCK_UNREACHABLE)
    assert REF.search(detail)
    assert "OperationalError" in detail  # a steward still sees it (the route is steward-only)


# --- a background job's failure, on its poll (security cleanup Part B item 2; docs/AUTH.md §Errors) ------------------


def _draft_failed(thesis_id: uuid.UUID) -> str:
    from workbench import draft_jobs

    def boom():
        raise RuntimeError(LEAK)

    return draft_jobs.start_draft_job(
        thesis_id, boom, executor=lambda job, run: draft_jobs._run_job(job, run)
    )


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_a_failed_draft_job_polls_as_a_fixed_sentence(client, thesis_id, user, steward):
    from app import errors

    job_id = _draft_failed(thesis_id)
    as_user(user)
    r = client.get(f"/workbench/theses/{thesis_id}/draft-chain/jobs/{job_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed"
    _check_text(body["error"], errors.SENTENCES["draft"], steward=steward)
    if steward:
        assert f"Detail: RuntimeError: {LEAK}" in body["error"]


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_a_failed_ingest_job_polls_as_a_fixed_sentence(client, thesis_id, user, steward):
    from app import errors
    from workbench import ingest_jobs

    def boom(scope):
        raise RuntimeError(LEAK)

    job = ingest_jobs.start_ingest_job(
        thesis_id, {uuid.uuid4()}, boom, executor=lambda j: ingest_jobs._run_job(j)
    )
    as_user(user)
    r = client.get(f"/workbench/theses/{thesis_id}/ingest/jobs/{job.job_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed"
    _check_text(body["error"], errors.SENTENCES["fetch"], steward=steward)
    if steward:
        assert f"Detail: RuntimeError: {LEAK}" in body["error"]


@pytest.mark.parametrize(("user", "steward"), CALLERS)
def test_a_partial_ingest_names_the_tickers_and_legs_but_not_the_raw_text(
    client, db, monkeypatch, caplog, user, steward
):
    """D10: a plain user sees the counts and each failed ticker with its legs in plain words, plus a reference;
    the raw per-leg text is logged under that reference and shown to a steward only."""
    from pipeline.ingest_thesis import NameResult
    from workbench import ingest_jobs

    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) VALUES "
            "(%s, '00000000-0000-0000-0000-000000000001', 'PART', 'Part Inc', '0000999002', '2026-01-01')",
            (sid,),
        )
    db.commit()

    def fake(conn, thesis_id, *, only_security_ids=None, **kw):
        return [
            NameResult(
                "PART",
                s,
                0,
                0,
                error=f"form4: {LEAK}; price: {LEAK}",
                leg_errors=(("form4", LEAK), ("price", LEAK)),
            )
            for s in only_security_ids or []
        ]

    monkeypatch.setattr("pipeline.ingest_thesis.ingest_thesis", fake)
    monkeypatch.setattr(ingest_jobs, "_DEFAULT_EXECUTOR", lambda j: ingest_jobs._run_job(j))
    as_user(TEST_STEWARD)
    with caplog.at_level("WARNING", logger="alphadeck.errors"):
        r = client.post(
            "/workbench/theses",
            json={
                "name": "Partial card",
                "narrative": "n",
                "basket": [{"ticker": "PART", "role": "r", "security_id": str(sid)}],
                "segments": [],
            },
        )
    assert r.status_code == 200, r.text
    ingest = r.json()["ingest"]
    as_user(user)
    polled = client.get(f"/workbench/theses/{r.json()['id']}/ingest/jobs/{ingest['job_id']}")
    assert polled.status_code == 200, polled.text
    error = polled.json()["error"]
    assert polled.json()["status"] == "failed"
    assert "PART: insider filings, prices" in error, error
    ref = REF.search(error)
    assert ref is not None, error
    assert (LEAK in error) is steward, error
    # the log line carries the same reference and the raw leg text
    logged = [rec.getMessage() for rec in caplog.records if rec.name == "alphadeck.errors"]
    assert any(ref.group(0) in m and LEAK in m for m in logged), logged


def test_a_failed_job_logs_its_reference(client, thesis_id, caplog):
    """The reference on the poll is the one on the log line (so a quoted reference finds the traceback)."""
    with caplog.at_level("ERROR", logger="alphadeck.errors"):
        job_id = _draft_failed(thesis_id)
    r = client.get(f"/workbench/theses/{thesis_id}/draft-chain/jobs/{job_id}")
    ref = REF.search(r.json()["error"])
    assert ref is not None
    logged = [rec for rec in caplog.records if rec.name == "alphadeck.errors"]
    assert any(ref.group(0) in rec.getMessage() and rec.exc_info for rec in logged)
