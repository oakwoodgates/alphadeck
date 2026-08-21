"""S2c — the ADR/dual-listed insider mis-attribution repair (DB, count-the-table, KEEP-NOT-DELETE).

The scenario mirrors prod's real gap: rows ingested BEFORE migration 0041 are frozen with a NULL
``security_title`` / ``issuer_foreign_symbol`` (the incremental ingest never re-parses a stored
accession), so the ADR screen cannot see them. The repair re-parses the CACHED filing
(``forms/<accession>/<doc>`` — the immutable class) and append-only RE-VERSIONS each row to SET the two
columns — it NEVER deletes (the home-market ordinary shares have no other feed).

Real fixtures (test-honesty — no fabricated instances):
  - form4_tsm_mixed.xml = TSM 0001046179-26-000461: 3 transactions — txn_seq 0,1 "American Depositary
    Shares (TSM)" (KEPT) and txn_seq 2 "Common Shares (2330.TW)" (screened), foreign symbol 2330.TW.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import append_fact, as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import parse_form4
from pipeline.repair_adr_insider_misattribution import run_repair
from signals.insider_conviction import _is_foreign_ordinary

_FIX = Path(__file__).resolve().parent.parent / "fixtures" / "edgar"
_TSM_MIXED = (_FIX / "form4_tsm_mixed.xml").read_text(encoding="utf-8")
_US_SAMPLE = (_FIX / "form4_sample.xml").read_text(encoding="utf-8")
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _master(db, cik, ticker) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _ingest_prefix(db, sid, xml, accession, *, recorded_at=None) -> int:
    """Insert rows the way the PRE-0041 ingest did — WITHOUT security_title / issuer_foreign_symbol
    (they default NULL), at the SAME txn_seq the current ingest assigns (enumerate over parse_form4).
    Reproduces the frozen-NULL prod history the repair targets."""
    n = 0
    for i, t in enumerate(parse_form4(xml)):
        if t["txn_date"] is None:
            continue
        values = {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": t["insider_name"],
            "insider_role": t["insider_role"],
            "txn_code": t["txn_code"],
            "shares": t["shares"],
            "price": t["price"],
            "usd": t["usd"],
            "accession": accession,
            "valid_from": t["txn_date"],
            "txn_seq": i,
            # deliberately NO security_title / issuer_foreign_symbol -> NULL (the pre-0041 state)
        }
        if recorded_at is not None:
            values["recorded_at"] = recorded_at
        append_fact(db, "fact_insider_txn", values)
        n += 1
    return n


def _cache(tmp_path: Path, accession: str, text: str) -> Path:
    """Write ``text`` at the client's real cache location for ``accession``; returns the cache ROOT."""
    d = tmp_path / "forms" / accession
    d.mkdir(parents=True, exist_ok=True)
    (d / "doc.xml").write_text(text, encoding="utf-8")
    return tmp_path


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_insider_txn WHERE tenant_id = %s", (DEFAULT_TENANT_ID,)
        )
        return cur.fetchone()["n"]


def _latest(db, sid):
    return as_of(
        db,
        "fact_insider_txn",
        security_id=sid,
        asof=date(2026, 12, 31),
        known_at=_KNOWN,
        tenant_id=DEFAULT_TENANT_ID,
    )


ACC = "0001046179-26-000461"


def test_reversions_set_title_per_txn_seq_and_never_delete(db, tmp_path):
    """The core: each NULL-title row is re-versioned with the title matched by txn_seq + the foreign
    symbol — APPEND-ONLY (the table grows by the corrected rows; nothing is deleted)."""
    tsm = _master(db, "0001046179", "TSM")
    n = _ingest_prefix(db, tsm, _TSM_MIXED, ACC)
    db.commit()
    assert n == 3
    before = _count(db)

    res = run_repair(
        db,
        cache_dir=_cache(tmp_path, ACC, _TSM_MIXED),
        security_ids=[tsm],
        apply=True,
        tickers_by_id={tsm: "TSM"},
        log=lambda *_: None,
    )

    assert res.rows_reversioned == 3 and res.accessions_repaired == 1
    assert _count(db) == before + 3 == res.table_rows_after  # append-only, nothing deleted
    rows = sorted(_latest(db, tsm), key=lambda r: r["txn_seq"])
    assert [r["security_title"] for r in rows] == [
        "American Depositary Shares (TSM)",
        "American Depositary Shares (TSM)",
        "Common Shares (2330.TW)",
    ]
    assert {r["issuer_foreign_symbol"] for r in rows} == {"2330.TW"}
    # provenance: each correction links the version it supersedes (no orphan re-version)
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS sup FROM fact_insider_txn WHERE accession=%s AND supersedes IS NOT NULL",
            (ACC,),
        )
        assert cur.fetchone()["sup"] == 3


def test_screen_then_works_on_repaired_history(db, tmp_path):
    """The point of the repair: AFTER it, the as-of read carries the titles, so the buy screen sets the
    home-market ordinary row aside while keeping the ADR rows — the screen now works on HISTORY."""
    tsm = _master(db, "0001046179", "TSM")
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC)
    db.commit()
    # BEFORE: NULL titles -> the screen keeps every row (recall-safe)
    assert not any(_is_foreign_ordinary(r) for r in _latest(db, tsm))
    run_repair(
        db,
        cache_dir=_cache(tmp_path, ACC, _TSM_MIXED),
        security_ids=[tsm],
        apply=True,
        tickers_by_id={tsm: "TSM"},
        log=lambda *_: None,
    )
    # AFTER: exactly the ordinary row screens; the two ADR rows are kept
    screened = [r for r in _latest(db, tsm) if _is_foreign_ordinary(r)]
    assert len(screened) == 1 and screened[0]["security_title"] == "Common Shares (2330.TW)"


def test_idempotent_count_the_table(db, tmp_path):
    """A re-run appends ZERO — the repaired rows' latest version is non-NULL, so nothing is targeted
    (assert the TABLE COUNT, not the read, which dedups)."""
    tsm = _master(db, "0001046179", "TSM")
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC)
    db.commit()
    cache = _cache(tmp_path, ACC, _TSM_MIXED)
    run_repair(db, cache_dir=cache, security_ids=[tsm], apply=True, log=lambda *_: None)
    after_first = _count(db)
    second = run_repair(db, cache_dir=cache, security_ids=[tsm], apply=True, log=lambda *_: None)
    assert second.rows_reversioned == 0 and second.accessions_targeted == 0
    assert _count(db) == after_first == second.table_rows_after  # the table did not grow


def test_dry_run_reports_but_writes_nothing(db, tmp_path):
    tsm = _master(db, "0001046179", "TSM")
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC)
    db.commit()
    before = _count(db)
    res = run_repair(
        db,
        cache_dir=_cache(tmp_path, ACC, _TSM_MIXED),
        security_ids=[tsm],
        apply=False,
        log=lambda *_: None,
    )
    assert res.rows_reversioned == 3  # the full report...
    assert _count(db) == before == res.table_rows_after  # ...but not one row written
    assert all(r["security_title"] is None for r in _latest(db, tsm))


def test_scope_isolates_the_targeted_security(db, tmp_path):
    """``security_ids`` scopes the repair: a US name's NULL-title rows are left untouched when the run
    is scoped to TSM (TSM-first, the whole tape only on --all)."""
    tsm = _master(db, "0001046179", "TSM")
    us = _master(db, "0001234567", "DEVCO")
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC)
    _ingest_prefix(db, us, _US_SAMPLE, "acc-us")
    db.commit()
    run_repair(
        db,
        cache_dir=_cache(_cache(tmp_path, ACC, _TSM_MIXED), "acc-us", _US_SAMPLE),
        security_ids=[tsm],
        apply=True,
        tickers_by_id={tsm: "TSM"},
        log=lambda *_: None,
    )
    assert all(r["security_title"] is not None for r in _latest(db, tsm))  # TSM repaired
    assert all(r["security_title"] is None for r in _latest(db, us))  # DEVCO untouched


def test_all_scope_repairs_every_security(db, tmp_path):
    """``security_ids=None`` (--all) widens to the whole tape — both securities repaired."""
    tsm = _master(db, "0001046179", "TSM")
    us = _master(db, "0001234567", "DEVCO")
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC)
    _ingest_prefix(db, us, _US_SAMPLE, "acc-us")
    db.commit()
    run_repair(
        db,
        cache_dir=_cache(_cache(tmp_path, ACC, _TSM_MIXED), "acc-us", _US_SAMPLE),
        security_ids=None,
        apply=True,
        log=lambda *_: None,
    )
    assert all(r["security_title"] is not None for r in _latest(db, tsm))
    # DEVCO gets its title too (US "Common Stock") — foreign symbol stays NULL, never screened
    us_rows = _latest(db, us)
    assert all(r["security_title"] == "Common Stock" for r in us_rows)
    assert all(r["issuer_foreign_symbol"] is None for r in us_rows)
    assert not any(_is_foreign_ordinary(r) for r in us_rows)  # a US name is never screened


def test_uncached_filing_stays_null_and_run_continues(db, tmp_path):
    """Recall-safe residual: an accession with no cached doc stays NULL (KEPT) and is counted — never a
    silent drop, and the run does not abort."""
    tsm = _master(db, "0001046179", "TSM")
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC)  # NOT written to the cache below
    db.commit()
    empty_cache = tmp_path / "cache"
    (empty_cache / "forms").mkdir(parents=True)
    res = run_repair(db, cache_dir=empty_cache, security_ids=[tsm], apply=True, log=lambda *_: None)
    assert res.accessions_not_cached == 1 and res.rows_reversioned == 0
    assert res.rows_residual_null == 3
    assert all(r["security_title"] is None for r in _latest(db, tsm))  # KEPT, never dropped


def test_asof_read_serves_the_repair_and_replay_stays_honest(db, tmp_path):
    """A correction is a new VERSION: the as-of read serves the title for known_at >= the repair, a
    replay pinned earlier still sees NULL, and the logical-fact count never changes."""
    tsm = _master(db, "0001046179", "TSM")
    ingested = datetime(2026, 8, 5, tzinfo=timezone.utc)
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC, recorded_at=ingested)
    db.commit()
    run_repair(
        db,
        cache_dir=_cache(tmp_path, ACC, _TSM_MIXED),
        security_ids=[tsm],
        apply=True,
        log=lambda *_: None,
    )
    read = dict(security_id=tsm, asof=date(2026, 12, 31), tenant_id=DEFAULT_TENANT_ID)
    now_rows = as_of(db, "fact_insider_txn", known_at=datetime.now(timezone.utc), **read)
    then_rows = as_of(
        db, "fact_insider_txn", known_at=datetime(2026, 8, 6, tzinfo=timezone.utc), **read
    )
    assert len(now_rows) == len(then_rows) == 3  # same logical facts, no double-count
    assert all(r["security_title"] is not None for r in now_rows)  # the repair is served now
    assert all(
        r["security_title"] is None for r in then_rows
    )  # what the system knew then — unchanged


def test_execute_refuses_loud_on_a_pre_0037_constraint(db, tmp_path):
    """The write precondition (the 0037 lesson): against the pre-0037 constraint (no security_id) a batch
    of same-instant re-versions of one filing under two securities would collide — ``apply`` refuses UP
    FRONT (SystemExit naming 0037), before any write. Teardown force-restores the 0037 shape."""
    tsm = _master(db, "0001046179", "TSM")
    _ingest_prefix(db, tsm, _TSM_MIXED, ACC)
    db.commit()
    cache = _cache(tmp_path, ACC, _TSM_MIXED)
    with db.cursor() as cur:  # regress the constraint to its pre-0037 shape, uncommitted
        cur.execute("ALTER TABLE fact_insider_txn DROP CONSTRAINT fact_insider_txn_natural_key")
        cur.execute(
            "ALTER TABLE fact_insider_txn ADD CONSTRAINT fact_insider_txn_natural_key "
            "UNIQUE (tenant_id, accession, insider_name, valid_from, txn_seq, recorded_at)"
        )
    try:
        with pytest.raises(SystemExit, match="0037"):
            run_repair(db, cache_dir=cache, security_ids=[tsm], apply=True, log=lambda *_: None)
        assert _count(db) == 3  # refused BEFORE any write
    finally:
        db.rollback()
        with db.cursor() as cur:  # force-restore the 0037 shape regardless (idempotent)
            cur.execute(
                "ALTER TABLE fact_insider_txn DROP CONSTRAINT IF EXISTS fact_insider_txn_natural_key"
            )
            cur.execute(
                "ALTER TABLE fact_insider_txn ADD CONSTRAINT fact_insider_txn_natural_key UNIQUE "
                "(tenant_id, security_id, accession, insider_name, valid_from, txn_seq, recorded_at)"
            )
        db.commit()
