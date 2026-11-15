"""V2 W3a: THE RUNNER (``platform_values.extraction``): the work list, the per-company step and the run loop.

From real rows in two workspaces and the data tenant. The extraction itself is monkeypatched at the extraction
module (no SEC request anywhere); a FAKE client serves the submissions index and records every read, so "no fetch"
is counted, never assumed. Each test COUNTS THE TABLES (facts, candidates, attempts) before and after.

What is pinned:
- the work list: one entry per company on any live card (two workspaces = one entry with a card count of 2), an
  archived card does not count, a pen-only company sorts last, an ETF is left out, and never-extracted companies
  wait until a backfill attempt exists (approved B4 (a));
- the per-company step FETCHES with the company free, then calls the machine writer, then leaves EXACTLY ONE
  attempt row per outcome (done, error, busy, skipped); a failure writes nothing else and never hides a value
  awaiting the steward; its reason is one bounded line with no URL;
- idempotency: the same filings twice write no second fact or candidate, and the gate then says not due;
- the run loop: the count cap, the time budget, ``should_stop``, the kill switch, fail-open per company, and the
  summary (counts, ``to_json``, ``admin_line``).

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from db.session import connect
from db.write_lock import CompanyLockBusy, acquire_company_lock
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from domain.platform_values import ValueState
from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from ingest import CacheMiss
from platform_values import extraction as ex
from platform_values import machine
from platform_values.state import read_state
from repositories import platform_extraction_repo as repo
from repositories import platform_value_repo as pv_repo
from tests.workspaces import new_workspace

TABLES = ("fact_shares_outstanding", "fact_cash_burn", "extraction_candidate")
NOW = datetime.now(timezone.utc)
OLD = NOW - timedelta(days=60)
PERIOD = date.today() - timedelta(days=75)
NO_CACHE = (
    Path(__file__).resolve().parent / "no-such-edgar-cache"
)  # the writer reads no cached filing


# --- the world ---------------------------------------------------------------------------------------------------


def _company(db, ticker: str, cik: str | None, kind: str = "equity") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", cik, kind, date(2026, 1, 1)),
        )
    return sid


def _card(db, workspace: UUID, rows: list[tuple[UUID, str | None]], *, archived: bool = False):
    tid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) "
            "VALUES (%s, %s, %s, 'n', CASE WHEN %s THEN now() END)",
            (tid, workspace, f"card {tid.hex[:6]}", archived),
        )
        for i, (sid, segment) in enumerate(rows):
            cur.execute(
                "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id, segment) "
                "VALUES (%s, %s, %s, 'T', 'the name', %s, %s)",
                (workspace, tid, i, sid, segment),
            )
    return tid


def _filing(db, sid: UUID, form: str, acc: str, accepted: datetime) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_filing_index (tenant_id, security_id, form, accession, filed, primary_doc, "
            "accepted, source_ref, valid_from) VALUES (%s, %s, %s, %s, %s, 'd.htm', %s, %s, %s)",
            (
                DATA_TENANT_ID,
                sid,
                form,
                acc,
                accepted.date(),
                accepted,
                f"https://www.sec.gov/Archives/edgar/data/1/{acc}-index.htm",
                accepted.date(),
            ),
        )


def _subs(rows: list[tuple[str, str, datetime]]) -> dict:
    rows = sorted(rows, key=lambda r: r[2], reverse=True)
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "primaryDocument": ["d.htm" for _ in rows],
                "filingDate": [r[2].date().isoformat() for r in rows],
                "reportDate": ["" for _ in rows],
                "acceptanceDateTime": [r[2].strftime("%Y-%m-%dT%H:%M:%S.000Z") for r in rows],
            }
        }
    }


class FakeClient:
    """Serves each CIK's submissions index; records every read; counts a 'live fetch' per read."""

    def __init__(self, cache_dir: Path, subs: dict[int, dict] | None = None) -> None:
        self.cache_dir = cache_dir
        self.subs = subs or {}
        self.reads: list[str] = []
        self.live_fetches = 0

    def get_json(self, url: str, cache_key: str, **_: Any) -> dict:
        self.reads.append(cache_key)
        self.live_fetches += 1
        cik = int(cache_key.split("CIK")[1].split(".")[0])
        return self.subs.get(cik, {"filings": {"recent": {}}})

    def get_text(
        self, url: str, cache_key: str, **_: Any
    ) -> str:  # pragma: no cover - never read here
        raise AssertionError(f"unexpected document read {cache_key}")


def _shares(ref: str, d: date = PERIOD, tier=Tier.AUTO) -> ExtractedFact:
    return ExtractedFact(
        fact_type="shares_outstanding",
        tier=tier,
        source="10-q-cover",
        source_ref=ref,
        event_date=d,
        value=30_000_000.0,
        note="Cover-page shares outstanding (single class).",
    )


def _cash(ref: str, d: date = PERIOD) -> ExtractedFact:
    return ExtractedFact(
        fact_type="cash_burn",
        tier=Tier.AUTO,
        source="10-q",
        source_ref=ref,
        event_date=d,
        cash_usd=30_000_000.0,
        quarterly_burn_usd=3_000_000.0,
        note="Cash; operating cash flow.",
    )


class FakeExtractor:
    """Stands in for ``extract_with_annual_fallback``: a result (or an exception) per CIK; records each call and
    whether the company lock was free while it 'fetched'."""

    def __init__(self, results: dict[int, ExtractionResult | BaseException]) -> None:
        self.results = results
        self.calls: list[int] = []
        self.values_only: list[bool] = []
        self.probe: tuple[Any, UUID] | None = None
        self.lock_free: list[bool] = []

    def __call__(self, client, cik, *, values_only: bool = False, **_: Any) -> ExtractionResult:
        self.calls.append(int(cik))
        self.values_only.append(values_only)
        if self.probe is not None:
            other, sid = self.probe
            try:
                acquire_company_lock(other, sid, wait_s=0).release()
                self.lock_free.append(True)
            except CompanyLockBusy:
                self.lock_free.append(False)
        out = self.results[int(cik)]
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture
def fake_extract(monkeypatch):
    def install(results: dict[int, ExtractionResult | BaseException]) -> FakeExtractor:
        fake = FakeExtractor(results)
        monkeypatch.setattr(ex, "extract_with_annual_fallback", fake)
        return fake

    return install


def _counts(db, sid: UUID | None = None) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for table in (*TABLES, "platform_extraction_attempt"):
            if sid is None:
                cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed names
            else:
                cur.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE security_id = %s",  # noqa: S608
                    (sid,),
                )
            out[table] = cur.fetchone()["n"]
    db.rollback()
    return out


def _attempts(db, sid: UUID) -> list[dict]:
    db.rollback()
    rows = repo.latest_attempts(db, [sid], per_company=50).get(sid, [])
    db.rollback()
    return rows


def _item(db, sid: UUID, *, include_never: bool = True) -> ex.WorkItem:
    (item,) = [
        i for i in ex.work_list(db, include_never=include_never).items if i.security_id == sid
    ]
    return item


def _one_company(db, ticker: str = "ACME", cik: str = "1001") -> tuple[UUID, str, FakeClient]:
    """A company on one live card, with a 10-Q in its filing index and in its submissions."""
    w = new_workspace(db, "W")
    sid = _company(db, ticker, cik)
    _card(db, w, [(sid, "Reactors")])
    acc = f"000{cik}-26-000001"
    _filing(db, sid, "10-Q", acc, OLD)
    db.commit()
    return sid, acc, FakeClient(NO_CACHE, {int(cik): _subs([("10-Q", acc, OLD)])})


# --- the work list -----------------------------------------------------------------------------------------------


def test_the_work_list_is_one_entry_per_on_card_company_most_cards_first_pen_only_last(db):
    w_a, w_b = new_workspace(db, "A"), new_workspace(db, "B")
    both = _company(db, "BOTH", "1")
    one = _company(db, "ONE", "2")
    pen = _company(db, "PEN", "3")
    archived = _company(db, "ARCH", "4")
    fund = _company(db, "FUND", "5", kind="etf")
    _card(db, w_a, [(both, "Fuel"), (one, "Fuel"), (fund, "ETFs")])
    _card(db, w_b, [(both, "Discovered"), (pen, "Discovered")])
    _card(db, w_b, [(archived, "Fuel"), (both, "Fuel")], archived=True)
    db.commit()

    wl = ex.work_list(db, include_never=True)

    assert [(i.ticker, i.cards, i.pen_only, i.reason) for i in wl.items] == [
        ("BOTH", 2, False, "never"),  # on cards in two workspaces: ONE entry, card count 2
        ("ONE", 1, False, "never"),
        ("PEN", 1, True, "never"),  # only in a Discovered pen: last
    ]
    # an archived card does not count, and an ETF is not extracted (``extractable``, one line to flip)
    assert archived not in {i.security_id for i in wl.items}
    assert fund not in {i.security_id for i in wl.items}
    assert ex.extractable({"instrument_kind": "etf"}) is False


def test_never_extracted_companies_wait_until_a_backfill_attempt_exists(db, fake_extract):
    """Approved B4 (a): before the first backfill, the default list (the nightly's and onboarding's) carries no
    never-extracted company; it counts them instead."""
    sid, acc, client = _one_company(db)
    assert ex.backfill_applied(db) is False
    wl = ex.work_list(db)
    assert wl.items == [] and wl.withheld_never == 1

    item = _item(db, sid)
    fake_extract({1001: ExtractionResult(facts=[])})
    ex.extract_one(db, item, client=client, wait_s=0, run_kind="nightly")
    assert ex.backfill_applied(db) is False  # a nightly attempt is not a backfill

    other = _company(db, "OTHER", "1002")
    _card(db, new_workspace(db, "W2"), [(other, "Fuel")])
    db.commit()
    fake_extract({1001: ExtractionResult(facts=[]), 1002: ExtractionResult(facts=[])})
    ex.extract_one(db, item, client=client, wait_s=0, run_kind="backfill")
    assert ex.backfill_applied(db) is True
    assert [i.ticker for i in ex.work_list(db).items] == ["OTHER"]


def test_due_reasons_order_new_filing_then_retry_then_never(db, fake_extract):
    w = new_workspace(db, "W")
    newf, retry, never = (
        _company(db, "NEWF", "11"),
        _company(db, "RETRY", "12"),
        _company(db, "NEVER", "13"),
    )
    _card(db, w, [(never, "Fuel")])
    _card(db, new_workspace(db, "W2"), [(never, "Fuel"), (newf, "Fuel"), (retry, "Fuel")])
    _card(db, new_workspace(db, "W3"), [(never, "Fuel")])
    for sid, cik in ((newf, 11), (retry, 12)):
        _filing(db, sid, "10-Q", f"{cik}-q1", OLD)
    db.commit()
    client = FakeClient(
        NO_CACHE,
        {11: _subs([("10-Q", "11-q1", OLD)]), 12: _subs([("10-Q", "12-q1", OLD)])},
    )
    fake_extract({11: ExtractionResult(facts=[]), 12: CacheMiss("forms/x not cached")})
    for sid in (newf, retry):
        ex.extract_one(db, _item(db, sid), client=client, wait_s=0, run_kind="backfill")
    _filing(db, newf, "10-K", "11-k1", NOW - timedelta(hours=30))  # public 30 h: due
    db.commit()

    wl = ex.work_list(db, include_never=True)
    # NEVER sits on three cards, but a new filing and a retry come first (Q5)
    assert [(i.ticker, i.reason) for i in wl.items] == [
        ("NEWF", "new_filing"),
        ("RETRY", "retry"),
        ("NEVER", "never"),
    ]


# --- the per-company step ----------------------------------------------------------------------------------------


def test_an_auto_result_is_one_auto_fact_in_the_data_tenant_and_one_done_attempt(db, fake_extract):
    sid, acc, client = _one_company(db)
    ref = f"https://www.sec.gov/Archives/edgar/data/1001/{acc}/d.htm"
    fake = fake_extract({1001: ExtractionResult(facts=[_shares(ref), _cash(ref)])})

    got = ex.extract_one(db, _item(db, sid), client=client, wait_s=0, run_kind="backfill")

    assert fake.values_only == [True]  # never the purity document
    assert (got.status, got.shares_reason, got.cash_reason) == ("done", "applied", "applied")
    assert _counts(db, sid) == {
        "fact_shares_outstanding": 1,
        "fact_cash_burn": 1,
        "extraction_candidate": 0,
        "platform_extraction_attempt": 1,
    }
    (row,) = _attempts(db, sid)
    assert (row["status"], row["run_kind"], row["shares_reason"], row["tenant_id"]) == (
        "done",
        "backfill",
        "applied",
        DATA_TENANT_ID,
    )
    assert row["inputs_key"] == f"1001|10-Q:{acc}|10-K:|20-F:|40-F:"
    assert row["live_fetches"] == 1  # the one submissions read (the extraction is faked)
    st = read_state(db, sid, "shares_outstanding")
    assert st.state is ValueState.FROM_FILING and st.value["tenant_id"] == DATA_TENANT_ID


def test_it_fetches_with_the_company_free_and_only_then_takes_the_lock(
    db, fake_extract, monkeypatch
):
    sid, acc, client = _one_company(db)
    fake = fake_extract({1001: ExtractionResult(facts=[])})
    order: list[str] = []
    real_record = machine.record_extraction

    def record(*a, **kw):
        order.append("record")
        return real_record(*a, **kw)

    real_call = fake.__call__

    class Wrapped:
        def __call__(self, *a, **kw):
            order.append("fetch")
            return real_call(*a, **kw)

    monkeypatch.setattr(ex, "record_extraction", record)
    monkeypatch.setattr(ex, "extract_with_annual_fallback", Wrapped())
    other = connect()
    try:
        fake.probe = (other, sid)
        ex.extract_one(db, _item(db, sid), client=client, wait_s=0, run_kind="nightly")
    finally:
        other.close()
    assert order == ["fetch", "record"]
    assert fake.lock_free == [True]  # nobody held the company while it fetched


def test_a_raised_extraction_writes_one_error_attempt_and_nothing_else(db, fake_extract):
    sid, acc, client = _one_company(db)
    secret_url = "https://www.sec.gov/Archives/edgar/data/1001/x.htm?token=abc"
    fake_extract({1001: RuntimeError(f"boom at {secret_url}\nTraceback: line 2")})
    before = _counts(db, sid)

    got = ex.extract_one(db, _item(db, sid), client=client, wait_s=0, run_kind="nightly")

    assert got.status == "error"
    after = _counts(db, sid)
    assert {t: after[t] - before[t] for t in after} == {
        "fact_shares_outstanding": 0,
        "fact_cash_burn": 0,
        "extraction_candidate": 0,
        "platform_extraction_attempt": 1,
    }
    (row,) = _attempts(db, sid)
    assert row["status"] == "error" and row["error"] == "extraction failed (RuntimeError)"
    assert "http" not in row["error"] and "\n" not in row["error"]


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (CacheMiss("forms/0001/x.htm not cached (live pulls disabled)"), "not cached"),
        (RuntimeError("set ALPHADECK_USER_AGENT (SEC requires ...)"), "no SEC User-Agent set"),
        (ValueError("x" * 5000), "extraction failed (ValueError)"),
    ],
)
def test_the_error_reason_is_one_bounded_line(exc, reason):
    got = ex.error_reason(exc)
    assert got == reason and len(got) <= 200 and "\n" not in got


def test_a_failure_never_hides_a_value_awaiting_the_steward(db, fake_extract):
    sid, acc, client = _one_company(db)
    pv_repo.append_candidate(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        outcome="flag",
        flags=["dual-class"],
        source_ref="https://www.sec.gov/Archives/edgar/data/1001/q.htm",
        event_date=PERIOD,
        shares=1.0,
    )
    db.commit()
    assert read_state(db, sid, "shares_outstanding").state is ValueState.AWAITING_STEWARD
    fake_extract({1001: ConnectionError("SEC down")})
    assert (
        ex.extract_one(db, _item(db, sid), client=client, wait_s=0, run_kind="nightly").status
        == "error"
    )
    assert read_state(db, sid, "shares_outstanding").state is ValueState.AWAITING_STEWARD


def test_a_held_company_is_one_busy_attempt_and_nothing_else(db, fake_extract):
    sid, acc, client = _one_company(db)
    ref = f"https://www.sec.gov/Archives/edgar/data/1001/{acc}/d.htm"
    fake_extract({1001: ExtractionResult(facts=[_shares(ref)])})
    other = connect()
    try:
        hold = acquire_company_lock(other, sid, wait_s=0)
        got = ex.extract_one(db, _item(db, sid), client=client, wait_s=0.1, run_kind="nightly")
        hold.release()
    finally:
        other.close()
    assert got.status == "busy"
    assert _counts(db, sid) == {
        "fact_shares_outstanding": 0,
        "fact_cash_burn": 0,
        "extraction_candidate": 0,
        "platform_extraction_attempt": 1,
    }
    assert _attempts(db, sid)[0]["status"] == "busy"
    assert _item(db, sid).reason == "retry"  # due again: busy is not a failure


def test_a_company_with_no_cik_is_one_skipped_attempt_and_no_fetch(db, fake_extract):
    w = new_workspace(db, "W")
    sid = _company(db, "NOCIK", None)
    _card(db, w, [(sid, "Fuel")])
    db.commit()
    fake = fake_extract({})
    client = FakeClient(NO_CACHE)
    got = ex.extract_one(db, _item(db, sid), client=client, wait_s=0, run_kind="backfill")
    assert got.status == "skipped" and client.reads == [] and fake.calls == []
    assert [r["detail"] for r in _attempts(db, sid)] == ["no CIK"]
    assert all(i.security_id != sid for i in ex.work_list(db, include_never=True).items)


def test_the_same_filings_twice_write_no_second_fact_or_candidate_and_are_then_not_due(
    db, fake_extract
):
    sid, acc, client = _one_company(db)
    ref = f"https://www.sec.gov/Archives/edgar/data/1001/{acc}/d.htm"
    flagged = _shares(ref, tier=Tier.FLAG)
    fake_extract({1001: ExtractionResult(facts=[flagged, _cash(ref)])})

    item = _item(db, sid)
    ex.extract_one(db, item, client=client, wait_s=0, run_kind="backfill")
    first = _counts(db, sid)
    assert first == {
        "fact_shares_outstanding": 0,
        "fact_cash_burn": 1,
        "extraction_candidate": 1,
        "platform_extraction_attempt": 1,
    }
    second = ex.extract_one(db, item, client=client, wait_s=0, run_kind="backfill")
    assert (second.shares_reason, second.cash_reason) == ("flagged", "already_on_file")
    after = _counts(db, sid)
    assert after == {**first, "platform_extraction_attempt": 2}  # only the attempt row is new

    # the gate: same filings, not due — so a run makes ZERO requests for it
    assert all(i.security_id != sid for i in ex.work_list(db, include_never=True).items)
    counting = FakeClient(NO_CACHE, client.subs)
    run = ex.run_extraction(
        connect, ex.work_list(db, include_never=True).items, client=counting, run_kind="backfill"
    )
    assert counting.reads == [] and run.attempted == 0


def test_a_nothing_found_company_is_not_fetched_again(db, fake_extract):
    sid, acc, client = _one_company(db)
    fake = fake_extract({1001: ExtractionResult(facts=[], empty_reason="no-annual-filing")})
    ex.extract_one(db, _item(db, sid), client=client, wait_s=0, run_kind="backfill")
    assert _attempts(db, sid)[0]["shares_reason"] == "nothing_found"
    counting = FakeClient(NO_CACHE, client.subs)
    run = ex.run_extraction(
        connect, ex.work_list(db, include_never=True).items, client=counting, run_kind="backfill"
    )
    assert counting.reads == [] and fake.calls == [1001] and run.attempted == 0


# --- the run loop ------------------------------------------------------------------------------------------------


def _three(db) -> tuple[list[UUID], FakeClient]:
    w = new_workspace(db, "W")
    sids = [_company(db, t, c) for t, c in (("AAA", "21"), ("BBB", "22"), ("CCC", "23"))]
    _card(db, w, [(s, "Fuel") for s in sids])
    for s, c in zip(sids, (21, 22, 23), strict=True):
        _filing(db, s, "10-Q", f"{c}-q", OLD)
    db.commit()
    return sids, FakeClient(NO_CACHE, {c: _subs([("10-Q", f"{c}-q", OLD)]) for c in (21, 22, 23)})


def test_the_run_is_fail_open_per_company_and_counts_every_outcome(db, fake_extract):
    sids, client = _three(db)
    ref = "https://www.sec.gov/Archives/edgar/data/21/21-q/d.htm"
    fake_extract(
        {
            21: ExtractionResult(facts=[_shares(ref), _cash(ref)]),
            22: TimeoutError("read timed out"),
            23: ExtractionResult(facts=[], empty_reason="no-annual-filing"),
        }
    )
    work = ex.work_list(db, include_never=True).items

    run = ex.run_extraction(connect, work, client=client, run_kind="backfill")

    assert (run.status, run.attempted, run.failed, run.deferred) == ("ran", 3, 1, 0)
    assert run.companies == {"applied": 1, "to_steward": 0, "nothing_found": 1, "unchanged": 0}
    assert run.outcomes["shares_outstanding"] == {"applied": 1, "nothing_found": 1}
    assert run.failed_labels == ["BBB: extraction failed (TimeoutError)"]
    assert run.due_by_reason == {"never": 3}
    assert run.live_fetches == 3
    assert _counts(db)["platform_extraction_attempt"] == 3
    doc = run.to_json()
    json.dumps(doc)  # serializable as-is: a later PR writes it into a run artifact
    assert doc["admin_line"] == run.admin_line() and run.attention is True
    line = run.admin_line()
    assert "2 extracted: 1 applied, 0 to the steward, 1 nothing found ·" in line
    assert "1 failed (BBB: extraction failed (TimeoutError))" in line


def test_the_cap_and_should_stop_stop_between_companies(db, fake_extract):
    sids, client = _three(db)
    fake = fake_extract({c: ExtractionResult(facts=[]) for c in (21, 22, 23)})
    work = ex.work_list(db, include_never=True).items

    capped = ex.run_extraction(connect, work, client=client, run_kind="backfill", max_names=2)
    assert (capped.attempted, capped.deferred, capped.stopped_by) == (2, 1, "cap")
    assert "1 still due" in capped.admin_line()

    calls = iter([False, True])
    stopped = ex.run_extraction(
        connect,
        work,  # the full list again: the caller's list is walked as given
        client=client,
        run_kind="backfill",
        should_stop=lambda: next(calls),
    )
    assert (stopped.attempted, stopped.deferred, stopped.stopped_by) == (1, 2, "stop")
    assert len(fake.calls) == 3


def test_the_budget_never_starts_a_company_it_cannot_afford(db, fake_extract):
    sids, client = _three(db)
    fake_extract({c: ExtractionResult(facts=[]) for c in (21, 22, 23)})
    ticks = iter([0.0, 0.0, 40.0, 40.0, 80.0, 80.0, 120.0])  # each company takes 40 s
    run = ex.run_extraction(
        connect,
        ex.work_list(db, include_never=True).items,
        client=client,
        run_kind="backfill",
        budget_s=100.0,
        clock=lambda: next(ticks),
    )
    # after two (80 s), a third at 40 s would end at 120 s > 100 s: deferred
    assert (run.attempted, run.deferred, run.stopped_by) == (2, 1, "budget")


def test_the_kill_switch_turns_the_nightly_and_onboarding_off_but_not_the_backfill(
    db, fake_extract, monkeypatch
):
    sids, client = _three(db)
    fake = fake_extract({c: ExtractionResult(facts=[]) for c in (21, 22, 23)})
    monkeypatch.setenv("ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES", "0")
    get_settings.cache_clear()
    try:
        work = ex.work_list(db, include_never=True).items
        for kind in ("nightly", "onboarding"):
            run = ex.run_extraction(connect, work, client=client, run_kind=kind)
            assert (run.status, run.attempted) == ("off", 0)
            assert "off" in run.admin_line()
        assert client.reads == [] and fake.calls == []
        assert _counts(db)["platform_extraction_attempt"] == 0
        assert ex.run_extraction(connect, work, client=client, run_kind="backfill").attempted == 3
    finally:
        get_settings.cache_clear()


def test_the_nightly_takes_its_cap_and_budget_from_the_settings(db, fake_extract, monkeypatch):
    sids, client = _three(db)
    fake_extract({c: ExtractionResult(facts=[]) for c in (21, 22, 23)})
    monkeypatch.setenv("ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES", "1")
    get_settings.cache_clear()
    try:
        run = ex.run_extraction(
            connect, ex.work_list(db, include_never=True).items, client=client, run_kind="nightly"
        )
        assert (run.attempted, run.stopped_by, run.budget_s) == (1, "cap", 900.0)
    finally:
        get_settings.cache_clear()


# --- nothing here feeds the call ---------------------------------------------------------------------------------


def test_no_call_path_module_names_the_background_extraction():
    """The extraction and its attempt record are platform bookkeeping, never a call input: no module the call,
    the replay or the editions engine is built from names them."""
    backend = Path(__file__).resolve().parents[2]
    call_path = [backend / d for d in ("signals", "calls", "replay", "editions")] + [
        backend / "pipeline" / "core.py",
        backend / "db" / "bitemporal.py",
        backend / "db" / "public_clock.py",
    ]
    files = [p for root in call_path for p in ([root] if root.is_file() else root.rglob("*.py"))]
    assert len(files) > 20  # the walk found the engine
    names = (
        "platform_values.extraction",
        "platform_extraction_repo",
        "platform_extraction_attempt",
    )
    assert [p.name for p in files if any(n in p.read_text(encoding="utf-8") for n in names)] == []
