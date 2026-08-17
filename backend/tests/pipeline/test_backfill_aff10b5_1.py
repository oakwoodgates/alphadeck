"""The aff_10b5_1 backfill — NULL-only targeting, append-only re-versioning, idempotency
(count-the-table), and the honest bitemporal read after a correction.

The scenario mirrors prod's actual gap: rows ingested from a filing WITHOUT reading the checkbox are
frozen NULL (``existing_accessions`` never re-parses a stored accession); the backfill re-parses the
CACHED filing and appends a correction version. The fake cache is the real on-disk shape the client
writes: ``<cache>/forms/<accession>/<doc>``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import ingest_form4
from pipeline.backfill_aff10b5_1 import run_backfill, run_verify

_XML = (
    Path(__file__).resolve().parent.parent / "fixtures" / "edgar" / "form4_sample.xml"
).read_text(encoding="utf-8")


def _with_aff(value: str) -> str:
    """The sample filing with the document-level ``<aff10b5One>`` checkbox injected (the SEC puts it
    right after ``</reportingOwner>``)."""
    return _XML.replace(
        "</reportingOwner>", f"</reportingOwner>\n  <aff10b5One>{value}</aff10b5One>"
    )


def _cache(tmp_path: Path, accession: str, text: str) -> Path:
    """Write ``text`` at the client's real cache location for ``accession``; returns the cache ROOT."""
    d = tmp_path / "forms" / accession
    d.mkdir(parents=True, exist_ok=True)
    (d / "doc.xml").write_text(text, encoding="utf-8")
    return tmp_path


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        return cur.fetchone()["n"]


def _latest_flags(db, accession: str) -> set[bool | None]:
    """The LATEST version's flag per natural key under ``accession`` — the value the as-of read serves."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (tenant_id, security_id, accession, insider_name, valid_from, "
            "txn_seq) aff_10b5_1 FROM fact_insider_txn WHERE accession = %s "
            "ORDER BY tenant_id, security_id, accession, insider_name, valid_from, txn_seq, "
            "recorded_at DESC, id DESC",
            (accession,),
        )
        return {r["aff_10b5_1"] for r in cur.fetchall()}


def test_backfill_corrects_null_only_and_never_rewrites_a_captured_value(db, security_id, tmp_path):
    # acc-null: ingested pre-capture (flag never read -> NULL); the filing SAYS true.
    ingest_form4(db, security_id, _XML, "acc-null")
    # acc-captured: ingested post-capture with a REAL stored False; the cache deliberately says the
    # OPPOSITE (true) — the backfill must not touch it (a disagreement is --verify's to report).
    ingest_form4(db, security_id, _with_aff("0"), "acc-captured")
    db.commit()
    cache = _cache(tmp_path, "acc-null", _with_aff("1"))
    _cache(tmp_path, "acc-captured", _with_aff("1"))
    before = _count(db)  # 4 rows: two per filing (P + S)

    res = run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)

    # NULL keys corrected via NEW VERSIONS (append-only: originals still present, table grew by
    # exactly the corrected rows), captured keys untouched.
    assert res.accessions_targeted == 1 and res.accessions_corrected == 1
    assert res.rows_to_true == 2 and res.rows_to_false == 0 and res.rows_residual_null == 0
    assert _count(db) == before + 2 == res.table_rows_after
    assert _latest_flags(db, "acc-null") == {True}
    assert _latest_flags(db, "acc-captured") == {False}  # never rewritten, no new versions
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n, count(supersedes) AS sup FROM fact_insider_txn "
            "WHERE accession='acc-captured'"
        )
        row = cur.fetchone()
    assert row["n"] == 2 and row["sup"] == 0  # exactly the two original rows, no corrections
    with db.cursor() as cur:  # provenance: each correction links the version it supersedes
        cur.execute(
            "SELECT count(*) AS sup FROM fact_insider_txn "
            "WHERE accession='acc-null' AND supersedes IS NOT NULL"
        )
        assert cur.fetchone()["sup"] == 2


def test_backfill_is_idempotent_count_the_table(db, security_id, tmp_path):
    """The convention test: a re-run appends ZERO rows — assert the TABLE COUNT, not the read (the
    as-of read dedups, so a duplicate append would hide behind a correct-looking read)."""
    ingest_form4(db, security_id, _XML, "acc-null")
    db.commit()
    cache = _cache(tmp_path, "acc-null", _with_aff("true"))

    first = run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)
    count_after_first = _count(db)
    second = run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)

    assert first.rows_to_true == 2
    assert second.accessions_targeted == 0  # nothing left with a latest-version NULL
    assert second.rows_to_true == second.rows_to_false == 0
    assert _count(db) == count_after_first == second.table_rows_after  # the table did not grow


def test_unknown_stays_unknown_and_one_bad_filing_never_aborts(db, security_id, tmp_path):
    """Recall-safe residuals: checkbox-absent parses to None -> stays NULL; an uncached or unparseable
    filing stays NULL and is counted — and the run CONTINUES to correct the correctable one."""
    ingest_form4(db, security_id, _XML, "acc-absent")  # cached WITHOUT a checkbox
    ingest_form4(db, security_id, _XML, "acc-uncached")  # not in the cache at all
    ingest_form4(db, security_id, _XML, "acc-bad")  # cached garbage
    ingest_form4(db, security_id, _XML, "acc-ok")  # cached with the checkbox
    db.commit()
    cache = _cache(tmp_path, "acc-absent", _XML)
    _cache(tmp_path, "acc-bad", "<this is not xml")
    _cache(tmp_path, "acc-ok", _with_aff("0"))
    before = _count(db)

    res = run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)

    assert res.accessions_targeted == 4
    assert res.accessions_checkbox_absent == 1
    assert res.accessions_not_cached == 1
    assert res.accessions_parse_error == 1
    assert res.accessions_corrected == 1  # the run survived the bad ones and still corrected
    assert res.rows_to_false == 2 and res.rows_residual_null == 6
    assert _count(db) == before + 2
    assert _latest_flags(db, "acc-absent") == {None}  # unknown stays unknown, never coerced (#3)
    assert _latest_flags(db, "acc-uncached") == {None}
    assert _latest_flags(db, "acc-bad") == {None}
    assert _latest_flags(db, "acc-ok") == {False}


def test_dry_run_reports_but_writes_nothing(db, security_id, tmp_path):
    ingest_form4(db, security_id, _XML, "acc-null")
    db.commit()
    cache = _cache(tmp_path, "acc-null", _with_aff("1"))
    before = _count(db)

    res = run_backfill(db, cache_dir=cache, execute=False, log=lambda *_: None)

    assert res.rows_to_true == 2 and res.accessions_corrected == 1  # the full report...
    assert _count(db) == before == res.table_rows_after  # ...but not one row written
    assert _latest_flags(db, "acc-null") == {None}


def test_asof_read_serves_the_correction_and_replay_stays_honest(db, security_id, tmp_path):
    """The point of the mechanism: after the backfill the shared as-of read returns the corrected flag
    (known_at >= the backfill), while a replay pinned BEFORE it still sees NULL — and the key count
    never changes (a correction is a new VERSION, never a new fact)."""
    ingested_at = datetime(2026, 6, 5, tzinfo=timezone.utc)
    ingest_form4(db, security_id, _XML, "acc-null", recorded_at=ingested_at)
    db.commit()
    cache = _cache(tmp_path, "acc-null", _with_aff("1"))
    run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)

    read = dict(security_id=security_id, asof=date(2026, 8, 1), tenant_id=DEFAULT_TENANT_ID)
    now_rows = as_of(db, "fact_insider_txn", known_at=datetime.now(timezone.utc), **read)
    then_rows = as_of(
        db, "fact_insider_txn", known_at=datetime(2026, 6, 10, tzinfo=timezone.utc), **read
    )
    assert len(now_rows) == len(then_rows) == 2  # same logical facts, no double-count
    assert {r["aff_10b5_1"] for r in now_rows} == {True}  # the correction is what reads serve now
    assert {r["aff_10b5_1"] for r in then_rows} == {None}  # what the system knew then — unchanged


def test_verify_cross_checks_stored_values_against_the_cache(db, security_id, tmp_path):
    """--verify is the independent gate: 0 mismatches on an honest table; a stored value the filing
    disagrees with IS a mismatch; a remaining correctable NULL is counted (0 expected post-run)."""
    ingest_form4(db, security_id, _with_aff("1"), "acc-true")  # stored True, cache True
    ingest_form4(db, security_id, _with_aff("0"), "acc-wrong")  # stored False, cache says True
    ingest_form4(db, security_id, _XML, "acc-null")  # stored NULL, cache has the checkbox
    db.commit()
    cache = _cache(tmp_path, "acc-true", _with_aff("1"))
    _cache(tmp_path, "acc-wrong", _with_aff("1"))
    _cache(tmp_path, "acc-null", _with_aff("1"))

    res = run_verify(db, cache_dir=cache, log=lambda *_: None)

    assert res.keys_total == 6 and res.keys_stored_nonnull == 4 and res.keys_compared == 4
    assert res.keys_null_but_correctable == 2
    assert [(m[0], m[2], m[3]) for m in res.mismatches] == [
        ("acc-wrong", False, True),
        ("acc-wrong", False, True),
    ]

    # after the backfill corrects acc-null, verify goes clean on it: correctable NULLs -> 0 for it
    run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)
    res2 = run_verify(db, cache_dir=cache, log=lambda *_: None)
    assert res2.keys_null_but_correctable == 0
    assert (
        len(res2.mismatches) == 2
    )  # the pre-existing stored-vs-filing disagreement still reported


def _second_security(db) -> uuid.UUID:
    """A SECOND master row for the SAME issuer (same CIK — another listing/share class) — the prod
    shape behind the 2026-08-17 abort. The ingest skip (``existing_accessions``) is scoped per
    (tenant, security), so BOTH rows legitimately carry the same Form 4, once per security scope."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "DEVCO2", "0001234567", "2026-01-01"),
        )
    db.commit()
    return sid


def test_same_filing_under_two_securities_corrects_both_without_collision(
    db, security_id, tmp_path
):
    """THE 2026-08-17 PROD ABORT, reproduced. One filing key (accession, insider_name, valid_from,
    txn_seq) sits latest-version-NULL under TWO security_ids (one issuer held as two master rows;
    the per-security ingest stores the filing once per scope). Both scopes are corrected inside ONE
    batch transaction, where ``recorded_at`` defaults to ``now()`` — transaction_timestamp, CONSTANT
    across the batch — and the pre-0037 natural-key constraint omitted ``security_id``: the second
    append carried an identical constraint tuple -> UniqueViolation, batch rolled back. With 0037
    the constraint keys the same per-security grain the as-of read does, so the fixed tool corrects
    BOTH scopes, idempotently, and each security's read serves its corrected flag."""
    sec_b = _second_security(db)
    # prod's originals landed in separate per-security ingest commits -> distinct recorded_at
    # (explicit here; a shared instant would have been unstorable under the pre-0037 constraint)
    ingest_form4(
        db, security_id, _XML, "acc-dual", recorded_at=datetime(2026, 6, 5, tzinfo=timezone.utc)
    )
    ingest_form4(db, sec_b, _XML, "acc-dual", recorded_at=datetime(2026, 6, 6, tzinfo=timezone.utc))
    db.commit()
    cache = _cache(tmp_path, "acc-dual", _with_aff("1"))
    before = _count(db)  # 4 rows: 2 txns (P + S) x 2 securities

    res = run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)  # raised pre-fix

    assert res.accessions_targeted == 1 and res.accessions_corrected == 1
    assert res.rows_to_true == 4 and res.rows_residual_null == 0
    assert _count(db) == before + 4 == res.table_rows_after  # BOTH scopes corrected, append-only

    # idempotent — count the TABLE (the dedup'd read would hide a duplicate append)
    second = run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)
    assert second.accessions_targeted == 0
    assert _count(db) == before + 4 == second.table_rows_after

    # each security's as-of read serves ITS corrected rows — 2 facts per scope, no cross-scope bleed
    for sid in (security_id, sec_b):
        rows = as_of(
            db,
            "fact_insider_txn",
            security_id=sid,
            asof=date(2026, 8, 1),
            known_at=datetime.now(timezone.utc),
            tenant_id=DEFAULT_TENANT_ID,
        )
        assert len(rows) == 2
        assert {r["aff_10b5_1"] for r in rows} == {True}
        assert all(r["security_id"] == sid for r in rows)


def test_execute_refuses_loud_on_a_pre_0037_constraint(db, security_id, tmp_path):
    """The fix's precondition is EXPLICIT: against a DB still carrying the pre-0037 constraint (no
    ``security_id``), ``--execute`` refuses UP FRONT — a SystemExit naming migration 0037, before any
    write — instead of collapsing mid-batch the way prod did. The swap below stays uncommitted (DDL is
    transactional; the guard fires before the first append), and teardown force-restores the 0037
    shape either way so the shared session schema can never be left regressed."""
    ingest_form4(db, security_id, _XML, "acc-null")
    db.commit()
    cache = _cache(tmp_path, "acc-null", _with_aff("1"))
    with db.cursor() as cur:  # regress the constraint to its pre-0037 shape, uncommitted
        cur.execute("ALTER TABLE fact_insider_txn DROP CONSTRAINT fact_insider_txn_natural_key")
        cur.execute(
            "ALTER TABLE fact_insider_txn ADD CONSTRAINT fact_insider_txn_natural_key "
            "UNIQUE (tenant_id, accession, insider_name, valid_from, txn_seq, recorded_at)"
        )
    try:
        with pytest.raises(SystemExit, match="0037"):
            run_backfill(db, cache_dir=cache, execute=True, log=lambda *_: None)
        assert _count(db) == 2  # refused BEFORE any write
    finally:
        db.rollback()  # discard the (uncommitted) regression...
        with db.cursor() as cur:  # ...and force-restore the 0037 shape regardless (idempotent)
            cur.execute(
                "ALTER TABLE fact_insider_txn "
                "DROP CONSTRAINT IF EXISTS fact_insider_txn_natural_key"
            )
            cur.execute(
                "ALTER TABLE fact_insider_txn ADD CONSTRAINT fact_insider_txn_natural_key UNIQUE "
                "(tenant_id, security_id, accession, insider_name, valid_from, txn_seq, recorded_at)"
            )
        db.commit()
