"""``pipeline.reingest_fundamentals`` — the label-fix re-ingest CLI: dry-run by default (read-only, no network),
``--apply`` in ONE counted transaction under the call-of-record lock, idempotent (count the table).

The store starts as prod held it before the fix (each company's first-present ``Revenues`` quarters, through
the real writer); the documents are REAL companyfacts trimmed to their revenue nodes
(``tests/fundamentals_fixtures.py``), laid out as an EDGAR cache so the real load path runs offline.
DB-backed (skips without Postgres).
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID, connect
from ingest.edgar import client as edgar_client
from ingest.edgar.client import EdgarClient
from ingest.fundamentals import _quarters_from_rows, _stampable_usd_duration_rows, store_quarters
from pipeline import reingest_fundamentals as RF
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from tests.fundamentals_fixtures import COMPANYFACTS_CIK, companyfacts_cache, companyfacts_revenue

RFC_EX = "RevenueFromContractWithCustomerExcludingAssessedTax"


def _add(db, ticker: str, cik: str | None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2020-01-01", "equity"),
        )
    db.commit()
    return sid


def _thesis(db, members: list[tuple[str, uuid.UUID]], *, archived: bool = False) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, f"T {tid.hex[:6]}", "n", "2026-01-01" if archived else None),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _prefix_store(db, sid: uuid.UUID, ticker: str) -> int:
    """What prod held before the fix: the first-present label's quarters, via the real writer."""
    cf = companyfacts_revenue(ticker)
    rows = _stampable_usd_duration_rows(cf["facts"]["us-gaap"]["Revenues"])
    n = store_quarters(db, sid, _quarters_from_rows(rows)).appended
    db.commit()
    return n


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_fundamentals")
        return cur.fetchone()["n"]


@pytest.fixture
def world(db, tmp_path):
    """AMD + VICR in an active basket (cached), ZZNC (not cached), ZZ404 (a cached 404 marker), and an
    archived thesis's member that the run must not touch."""
    amd = _add(db, "AMD", COMPANYFACTS_CIK["AMD"])
    vicr = _add(db, "VICR", COMPANYFACTS_CIK["VICR"])
    nc = _add(db, "ZZNC", "0009999991")
    gone = _add(db, "ZZ404", "0009999992")
    hon = _add(db, "HON", COMPANYFACTS_CIK["HON"])
    _thesis(db, [("AMD", amd), ("VICR", vicr), ("ZZNC", nc), ("ZZ404", gone)])
    _thesis(db, [("HON", hon)], archived=True)
    held = _prefix_store(db, amd, "AMD") + _prefix_store(db, vicr, "VICR")
    cache = companyfacts_cache(tmp_path / "edgar", "AMD", "VICR", "HON")
    (cache / "companyfacts" / "CIK0009999992.json.404").write_text("x", encoding="utf-8")
    return {"amd": amd, "vicr": vicr, "hon": hon, "cache": cache, "held": held}


def _client(world) -> EdgarClient:
    return EdgarClient(cache_dir=world["cache"], allow_live=False, cache_ttl_s=math.inf)


def test_the_dry_run_plans_on_a_read_only_connection_and_writes_nothing(db, world):
    before = _count(db)
    conn = connect()
    try:
        conn.read_only = True  # what main() does: a dry run PHYSICALLY cannot write
        res = RF.reingest_fundamentals(conn, client=_client(world))
    finally:
        conn.close()
    assert (
        _count(db) == before and res.appended == 0 and res.rows_before == res.rows_after == before
    )
    by = {c.ticker: c for c in res.companies}
    assert set(by) == {"AMD", "VICR", "ZZNC", "ZZ404"}  # the archived thesis's HON is out of scope
    amd = by["AMD"]
    assert (amd.old_label, amd.label) == ("Revenues", RFC_EX)
    assert (amd.old_end, amd.new_end) == (date(2017, 9, 30), date(2026, 6, 27))
    # 86 contract-tag quarters, 2 of them already held (the 2018 10-Qs tagged two 2017 quarters both ways,
    # with the same number) — the +84 the dev dry run printed for AMD
    assert amd.count(revenue=True, supersedes=False) == 84 and amd.count(revenue=False) == 0
    assert by["VICR"].count(revenue=True, supersedes=True) == 1  # the 2025-08-01 Q2-2025 conflict
    # the FY2025 10-K's Q3-2025 "quarter" carrying the year: screened, reported, not planned
    [scr] = by["VICR"].screened
    assert (scr.reason, scr.period_end, scr.value) == (
        "fy_total_in_quarter",
        date(2025, 9, 30),
        407_701_000,
    )
    assert not [v for v in by["VICR"].planned if v.point.value == 407_701_000]
    assert by["ZZNC"].source == RF.NOT_CACHED and by["ZZ404"].source == RF.KNOWN_404


def test_apply_appends_in_one_counted_transaction_and_a_rerun_appends_zero(db, world):
    before = _count(db)
    res = RF.reingest_fundamentals(db, apply=True, client=_client(world))
    assert res.appended == res.planned > 0
    assert res.rows_before == before and res.rows_after == before + res.planned
    assert _count(db) == before + res.planned  # COUNT the table

    again = RF.reingest_fundamentals(db, apply=True, client=_client(world))
    assert again.planned == 0 and again.appended == 0
    assert _count(db) == before + res.planned  # zero more


def test_a_fetch_is_never_made_for_a_cached_document_or_a_known_404(db, world):
    """With fetching allowed, the cache still wins (whatever its age) and a cached 404 is left to the
    nightly's gate; only the uncached ZZNC goes to the client — here a no-network client, so it is reported
    as not cached rather than fetched."""
    calls: list[str] = []

    class _Spy(EdgarClient):
        def get_json(self, url, cache_key):
            calls.append(cache_key)
            return super().get_json(url, cache_key)

    spy = _Spy(cache_dir=world["cache"], allow_live=False, cache_ttl_s=math.inf)
    res = RF.reingest_fundamentals(db, fetch=True, client=spy)
    assert calls == ["companyfacts/CIK0009999991.json"]
    by = {c.ticker: c for c in res.companies}
    assert by["AMD"].source == RF.CACHED and by["ZZ404"].source == RF.KNOWN_404
    assert by["ZZNC"].source == RF.NOT_CACHED and "not cached" in (by["ZZNC"].error or "")


def test_ticker_filter_limits_the_run(db, world):
    res = RF.reingest_fundamentals(db, client=_client(world), tickers=["vicr"])
    assert [c.ticker for c in res.companies] == ["VICR"]


def test_the_cli_dry_run_then_apply_then_rerun(db, world, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", world["cache"])
    monkeypatch.setattr(RF, "_DEFAULT_LOG_DIR", tmp_path / "repairs")
    before = _count(db)

    RF.main([])
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "would append" in out and "AMD: label Revenues -> " in out
    assert "series end 2017-09-30 -> 2026-06-27" in out
    # every screened row is printed in the dry run (VICR's year-in-a-quarter row), and counted
    assert (
        "screened fy_total_in_quarter: quarter ending 2025-09-30 filed 2026-03-02 value 407,701,000"
        in out
    )
    assert "screened out 1 row(s) in 1 companies" in out
    assert _count(db) == before and not (tmp_path / "repairs").exists()

    RF.main(["--apply", "--no-live"])
    out = capsys.readouterr().out
    assert "SUMMARY:" in out and "appended" in out
    added = _count(db) - before
    assert added > 0
    [prov] = list((tmp_path / "repairs").glob("*-reingest-fundamentals.json"))
    payload = json.loads(prov.read_text(encoding="utf-8"))
    assert payload["applied"] is True and payload["appended"] == added
    assert payload["rows_after"] - payload["rows_before"] == added

    RF.main(["--apply", "--no-live"])
    assert "appended 0" in capsys.readouterr().out
    assert _count(db) - before == added  # COUNT the table: zero more


def test_the_cli_apply_exits_75_while_the_lock_is_held(db, world, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", world["cache"])
    monkeypatch.setattr(RF, "_DEFAULT_LOG_DIR", tmp_path / "repairs")
    before = _count(db)
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            RF.main(["--apply", "--no-live"])
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        assert "ALREADY RUNNING" in capsys.readouterr().out
        assert _count(db) == before and not (tmp_path / "repairs").exists()
        RF.main([])  # a dry run takes no lock and still runs
        assert _count(db) == before
    finally:
        held.release()
