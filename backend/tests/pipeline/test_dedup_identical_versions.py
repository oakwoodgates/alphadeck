"""B2 repair CLI — dedup byte-identical bitemporal re-versions (the seed pile-up).

The scenario mirrors the real finding: a fixture fact re-appended at every boot produces a run of
byte-identical versions of the same logical fact. These tests prove the exact rule — within a
partition (tenant + scope + natural key), ordered by (recorded_at, id), the EARLIEST version of an
identical run survives and every LATER identical copy is deleted; any version that differs from its
immediate predecessor (a genuine restatement, e.g. the 0037/0040 insider backfills) is untouched. Every
count is asserted against the raw TABLE COUNT, never the as-of read (the CLAUDE.md convention — the
as-of read already dedups, so a duplicate append/leftover hides behind a correct-looking read).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg import sql

from db.bitemporal import append_fact, as_of
from db.session import DEFAULT_TENANT_ID, connect
from pipeline import dedup_identical_versions as dedup

_D = date(2026, 1, 5)  # the one price bar under test throughout
_ASOF = date(2027, 1, 1)  # far enough forward that valid_from <= asof always holds


def _append_price(conn, security_id, *, close, recorded_at, d=_D):
    return append_fact(
        conn,
        "fact_price_eod",
        dict(
            tenant_id=DEFAULT_TENANT_ID,
            security_id=security_id,
            d=d,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1000,
            valid_from=d,
            recorded_at=recorded_at,
        ),
    )


def _append_insider(
    conn,
    security_id,
    *,
    accession,
    aff_10b5_1,
    recorded_at,
    insider_name="Jane Doe",
    supersedes=None,
):
    return append_fact(
        conn,
        "fact_insider_txn",
        dict(
            tenant_id=DEFAULT_TENANT_ID,
            security_id=security_id,
            insider_name=insider_name,
            insider_role="director",
            txn_code="P",
            shares=100,
            price=10,
            usd=1000,
            accession=accession,
            valid_from=date(2026, 6, 1),
            recorded_at=recorded_at,
            aff_10b5_1=aff_10b5_1,
            supersedes=supersedes,
        ),
    )


def _append_catalyst(conn, security_id, *, source_ref, label, recorded_at, supersedes=None):
    return append_fact(
        conn,
        "fact_catalyst",
        dict(
            tenant_id=DEFAULT_TENANT_ID,
            security_id=security_id,
            catalyst_type="regulatory",
            grade="flip",
            label=label,
            source="ratified",
            source_ref=source_ref,
            valid_from=date(2026, 6, 1),
            recorded_at=recorded_at,
            supersedes=supersedes,
        ),
    )


def _thesis(conn, name="dedup-test-thesis") -> uuid.UUID:
    tid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "narrative"),
        )
    return tid


def _append_theme(conn, thesis_id, *, source_ref, label, recorded_at):
    return append_fact(
        conn,
        "fact_theme_conviction",
        dict(
            tenant_id=DEFAULT_TENANT_ID,
            thesis_id=thesis_id,
            grade="flip",
            label=label,
            source="ratified",
            source_ref=source_ref,
            valid_from=date(2026, 6, 1),
            recorded_at=recorded_at,
        ),
    )


def _count(conn, table) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table}"
        )  # noqa: S608 — test-only, fixed table names
        return cur.fetchone()["n"]


# --------------------------------------------------------------------------------------------------
# 1. Fixture + dry-run: v1(A) v2(A dup) v3(B restated) v4(B dup) v5(A, but predecessor is v4/B) — dry
# run reports exactly {v2, v4} and writes nothing. Mirrors the shape on fact_insider_txn and the
# thesis-scoped fact_theme_conviction.
# --------------------------------------------------------------------------------------------------


def test_dry_run_flags_exactly_the_later_identical_copies(db, security_id):
    t0, t1, t2, t3, t4 = (datetime(2026, 6, i, tzinfo=timezone.utc) for i in range(1, 6))
    v1 = _append_price(db, security_id, close=10.0, recorded_at=t0)
    v2 = _append_price(db, security_id, close=10.0, recorded_at=t1)  # dup of v1
    v3 = _append_price(db, security_id, close=12.0, recorded_at=t2)  # restated
    v4 = _append_price(db, security_id, close=12.0, recorded_at=t3)  # dup of v3
    v5 = _append_price(
        db, security_id, close=10.0, recorded_at=t4
    )  # back to A's VALUE, but predecessor is v4 (B)
    # a SECOND, unrelated bar (different date -> a different partition) with its own single version —
    # proves distinct_facts counts PARTITIONS, not rows, and this bar is untouched either way.
    other_bar = _append_price(db, security_id, close=99.0, recorded_at=t0, d=date(2026, 1, 6))
    db.commit()
    before = _count(db, "fact_price_eod")

    ids = dedup.duplicate_ids(db, "fact_price_eod")

    assert set(ids) == {v2, v4}
    for survivor in (v1, v3, v5, other_bar):  # v5 survives: its predecessor (v4) differs, not a dup
        assert survivor not in ids
    assert _count(db, "fact_price_eod") == before  # dry-run/report path writes nothing

    report = dedup.build_report(db, "fact_price_eod")
    assert report.rows == 6
    # 2 logical facts: the (tenant, security, d=_D) bar (5 recorded versions — the seed-pile-up shape
    # this tool targets) + the (tenant, security, 2026-01-06) bar (1 version).
    assert report.distinct_facts == 2
    assert report.identical_copies == 2
    assert _count(db, "fact_price_eod") == before  # build_report is read-only too


def test_dry_run_shape_on_insider_txn_and_thesis_scoped_theme_conviction(db, security_id):
    # THREE distinct recorded_at instants, strictly ascending — a tie on recorded_at would leave the
    # id (a random uuid) to break the order, making "which row is whose predecessor" nondeterministic.
    t0, t1, t2 = (datetime(2026, 6, i, tzinfo=timezone.utc) for i in (1, 2, 3))
    dup_id = _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t0)
    dup_copy_id = _append_insider(
        db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t1
    )
    # a genuine correction (the 0037/0040 shape): same natural key, ONE payload column changed —
    # never a duplicate, regardless of how many identical copies precede it.
    corrected_id = _append_insider(
        db, security_id, accession="acc-1", aff_10b5_1=True, recorded_at=t2
    )
    db.commit()

    ids = dedup.duplicate_ids(db, "fact_insider_txn")
    assert ids == [dup_copy_id]
    assert corrected_id not in ids
    assert dup_id not in ids  # the earliest version always survives

    thesis_id = _thesis(db)
    theme_v1 = _append_theme(db, thesis_id, source_ref="ref-1", label="l", recorded_at=t0)
    theme_v2 = _append_theme(db, thesis_id, source_ref="ref-1", label="l", recorded_at=t1)  # dup
    theme_v3 = _append_theme(db, thesis_id, source_ref="ref-1", label="DIFFERENT", recorded_at=t2)
    db.commit()

    theme_ids = dedup.duplicate_ids(db, "fact_theme_conviction")
    assert theme_ids == [theme_v2]
    assert theme_v1 not in theme_ids and theme_v3 not in theme_ids


# --------------------------------------------------------------------------------------------------
# 2. Apply: exactly v2/v4 deleted (count before/after); as-of reads pinned at 5 known_at points return
# IDENTICAL values before vs after the delete.
# --------------------------------------------------------------------------------------------------


def test_apply_deletes_exactly_the_flagged_copies_and_asof_reads_stay_identical(db, security_id):
    t0, t1, t2, t3, t4 = (datetime(2026, 6, i, tzinfo=timezone.utc) for i in range(1, 6))
    v1 = _append_price(db, security_id, close=10.0, recorded_at=t0)
    v2 = _append_price(db, security_id, close=10.0, recorded_at=t1)
    v3 = _append_price(db, security_id, close=12.0, recorded_at=t2)
    v4 = _append_price(db, security_id, close=12.0, recorded_at=t3)
    v5 = _append_price(db, security_id, close=10.0, recorded_at=t4)
    db.commit()
    before_rows = _count(db, "fact_price_eod")

    pins = {
        "before_v1": t0 - timedelta(hours=1),
        "between_v1_v2": t0 + timedelta(minutes=30),
        "between_v2_v3": t1 + timedelta(minutes=30),
        "between_v3_v4": t2 + timedelta(minutes=30),
        "after_v5": t4 + timedelta(minutes=30),
    }
    read_kwargs = dict(security_id=security_id, asof=_ASOF, tenant_id=DEFAULT_TENANT_ID)
    before_reads = {
        name: as_of(db, "fact_price_eod", known_at=k, **read_kwargs) for name, k in pins.items()
    }

    candidates = dedup.apply_table(db, "fact_price_eod")
    db.commit()

    assert set(candidates.deletable_ids) == {v2, v4}
    assert candidates.referenced_ids == []  # fact_price_eod has no self-referencing FK
    after_rows = _count(db, "fact_price_eod")
    assert after_rows == before_rows - 2
    with db.cursor() as cur:
        cur.execute("SELECT id FROM fact_price_eod ORDER BY id")
        assert {r["id"] for r in cur.fetchall()} == {v1, v3, v5}  # earliest-of-each-run survives

    after_reads = {
        name: as_of(db, "fact_price_eod", known_at=k, **read_kwargs) for name, k in pins.items()
    }
    for name in pins:
        before_vals = [
            {k: v for k, v in r.items() if k not in ("id", "recorded_at")}
            for r in before_reads[name]
        ]
        after_vals = [
            {k: v for k, v in r.items() if k not in ("id", "recorded_at")}
            for r in after_reads[name]
        ]
        assert before_vals == after_vals, f"as-of read changed at pin {name!r}"


def test_apply_with_real_verify_asof_end_to_end(db, security_id):
    """The NON-mocked --verify-asof path: a real persisted thesis, a real call_for_thesis /
    calls_repo._canonical round trip, before AND after a real delete — proves the safety net actually
    runs (not just its failure branch, covered by the monkeypatched test above) and lets a harmless
    delete through."""
    from domain.thesis import Thesis
    from repositories import thesis_repo

    thesis_repo.upsert(
        db,
        Thesis(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            name="dedup-verify-thesis",
            narrative="narrative",
            basket=[],
        ),
    )
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 2, tzinfo=timezone.utc)
    _append_price(db, security_id, close=10.0, recorded_at=t0)
    _append_price(db, security_id, close=10.0, recorded_at=t1)  # a real, deletable dup
    db.commit()

    dedup.main(["--apply", "--tables", "fact_price_eod", "--verify-asof", "2026-09-01"])

    fresh = connect()
    try:
        assert _count(fresh, "fact_price_eod") == 1  # the dup was actually deleted — verify passed
    finally:
        fresh.rollback()
        fresh.close()


# --------------------------------------------------------------------------------------------------
# 2.5. The dev abort, reproduced: a row that IS an identical-copy candidate but is also referenced by
# another row's self-referencing `supersedes` FK must be KEPT (deleting it raises a real
# ForeignKeyViolation on the real schema), while an unreferenced identical copy is still deleted, with
# no error. Runs against the REAL migrated schema (the `db` fixture applies every migration, so the
# fact_insider_txn_supersedes_fkey / fact_catalyst_supersedes_fkey constraints are live).
# --------------------------------------------------------------------------------------------------


def test_self_referencing_fk_columns_finds_supersedes_and_nothing_external(db):
    assert dedup.self_referencing_fk_columns(db, "fact_insider_txn") == ["supersedes"]
    assert dedup.self_referencing_fk_columns(db, "fact_catalyst") == ["supersedes"]
    assert (
        dedup.self_referencing_fk_columns(db, "fact_price_eod") == []
    )  # no supersedes column at all


def test_candidate_query_plan_has_no_subplan_for_the_referenced_check(db, security_id):
    """The regression guard for the dev hang (2026-09-04): the is_referenced check must compile to a
    JOIN, never an ``id IN (SELECT ...)`` — Postgres falls back to re-scanning a materialized subplan
    PER ROW once the referenced set is too big for a hashed subplan under work_mem, which is exactly
    the O(rows x references) plan that hung on dev for 10+ minutes moving 605k fact_insider_txn rows
    against 278,585 non-null supersedes values (planned cost ~7.8 BILLION, terminated before it wrote
    anything). EXPLAIN's plan text for the real candidates query (fetch_candidates's exact SQL) must
    never contain a SubPlan node again."""
    t0, t1 = datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 2, tzinfo=timezone.utc)
    v1 = _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t0)
    _append_insider(
        db, security_id, accession="acc-1", aff_10b5_1=True, recorded_at=t1, supersedes=v1
    )
    db.commit()

    select, params, _ = dedup._candidates_select(db, "fact_insider_txn", None)
    query = sql.SQL("EXPLAIN SELECT id, is_referenced FROM ({s}) z WHERE is_dup").format(s=select)
    with db.cursor() as cur:
        cur.execute(query, params)
        plan_lines = [next(iter(row.values())) for row in cur.fetchall()]
    plan_text = "\n".join(plan_lines)

    assert "SubPlan" not in plan_text, f"regression: the IN-subplan shape is back:\n{plan_text}"
    assert any(
        keyword in plan_text for keyword in ("Join", "Hash")
    ), f"expected a join-shaped plan for the referenced check:\n{plan_text}"


def test_apply_keeps_a_row_referenced_by_supersedes_and_deletes_the_rest_on_insider_txn(
    db, security_id
):
    """The exact dev shape: THREE plain seed re-appends (v1, v2, v3 — all supersedes=NULL, byte-
    identical) followed by a SEPARATE 0037/0040-style correction whose ``supersedes`` points at the
    MIDDLE copy (v2), not the first or last — exactly how the real backfill's "latest version at the
    time" landed on one of the piled-up seed copies. v1 is the earliest survivor (never a candidate);
    v2 IS a candidate (dup of v1) but is REFERENCED by the correction, so it must be KEPT; v3 is a dup
    of v2 and is unreferenced, so it IS deleted. apply_table must not raise ForeignKeyViolation."""
    t0, t1, t2, t3 = (datetime(2026, 6, i, tzinfo=timezone.utc) for i in (1, 2, 3, 4))
    v1 = _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t0)
    v2 = _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t1)
    v3 = _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t2)
    correction = _append_insider(
        db, security_id, accession="acc-1", aff_10b5_1=True, recorded_at=t3, supersedes=v2
    )
    db.commit()
    before = _count(db, "fact_insider_txn")

    assert dedup.referenced_duplicate_ids(db, "fact_insider_txn") == [v2]
    assert dedup.duplicate_ids(db, "fact_insider_txn") == [v3]

    candidates = dedup.apply_table(
        db, "fact_insider_txn"
    )  # would raise ForeignKeyViolation pre-fix
    db.commit()

    assert candidates.deletable_ids == [v3]
    assert candidates.referenced_ids == [v2]
    assert _count(db, "fact_insider_txn") == before - 1
    with db.cursor() as cur:
        cur.execute("SELECT id FROM fact_insider_txn ORDER BY id")
        # v2 survives — referenced by the correction, not a dud; v1 survives as the earliest version;
        # the correction itself was never a candidate (its payload differs from v3's)
        assert {r["id"] for r in cur.fetchall()} == {v1, v2, correction}


def test_apply_keeps_a_referenced_row_on_a_small_supersedes_table_fact_catalyst(db, security_id):
    """The same shape on a SECOND, small self-referencing table — proves the FK discovery + exclusion
    isn't insider_txn-specific."""
    t0, t1, t2, t3 = (datetime(2026, 6, i, tzinfo=timezone.utc) for i in (1, 2, 3, 4))
    v1 = _append_catalyst(db, security_id, source_ref="ref-1", label="l", recorded_at=t0)
    v2 = _append_catalyst(db, security_id, source_ref="ref-1", label="l", recorded_at=t1)
    v3 = _append_catalyst(db, security_id, source_ref="ref-1", label="l", recorded_at=t2)
    correction = _append_catalyst(
        db, security_id, source_ref="ref-1", label="RESTATED", recorded_at=t3, supersedes=v2
    )
    db.commit()
    before = _count(db, "fact_catalyst")

    assert dedup.referenced_duplicate_ids(db, "fact_catalyst") == [v2]
    assert dedup.duplicate_ids(db, "fact_catalyst") == [v3]

    candidates = dedup.apply_table(db, "fact_catalyst")
    db.commit()

    assert candidates.deletable_ids == [v3]
    assert candidates.referenced_ids == [v2]
    assert _count(db, "fact_catalyst") == before - 1
    with db.cursor() as cur:
        cur.execute("SELECT id FROM fact_catalyst ORDER BY id")
        assert {r["id"] for r in cur.fetchall()} == {v1, v2, correction}


def test_second_apply_is_a_no_op_across_every_table_including_referenced_rows(db, security_id):
    """Idempotency after the fix: dev landed 7 tables and aborted on insider_txn mid-run, so a rerun
    MUST be a no-op on the already-finished tables and finish cleanly on the rest — a referenced row
    stays referenced (never re-flagged as newly deletable) on every subsequent run."""
    t0, t1, t2, t3 = (datetime(2026, 6, i, tzinfo=timezone.utc) for i in (1, 2, 3, 4))
    _append_price(db, security_id, close=10.0, recorded_at=t0)
    _append_price(db, security_id, close=10.0, recorded_at=t1)  # a plain dup, no FK involved
    _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t0)
    v2 = _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t1)
    _append_insider(db, security_id, accession="acc-1", aff_10b5_1=None, recorded_at=t2)
    _append_insider(
        db, security_id, accession="acc-1", aff_10b5_1=True, recorded_at=t3, supersedes=v2
    )
    db.commit()

    for table in ("fact_price_eod", "fact_insider_txn"):
        first = dedup.apply_table(db, table)
        db.commit()
        assert first.deletable_ids, f"expected the first apply on {table} to delete something"
        before_second = _count(db, table)

        second = dedup.apply_table(db, table)
        db.commit()

        # count the TABLE, not the read — the convention this whole tool follows
        assert second.deletable_ids == []
        assert _count(db, table) == before_second


# --------------------------------------------------------------------------------------------------
# 3. Cross-partition negative: a row in a DIFFERENT partition (another security) sitting adjacent in
# global (recorded_at, id) order is never flagged, even though it superficially resembles the other
# partition's bar.
# --------------------------------------------------------------------------------------------------


def test_cross_partition_row_never_flagged_even_when_adjacent_in_global_order(db, security_id):
    sid_b = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid_b, DEFAULT_TENANT_ID, "DEVCO2", "0009999999", "2026-01-01"),
        )
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    t_mid = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 2, tzinfo=timezone.utc)
    v1 = _append_price(db, security_id, close=10.0, recorded_at=t0)
    # security B's bar sits BETWEEN A's v1 and v2 in global insertion/recorded_at order, and shares the
    # same OHLCV — if PARTITION BY were dropped, a naive global lag() would flag it (or v2) as a dup.
    foreign = _append_price(db, sid_b, close=10.0, recorded_at=t_mid)
    v2 = _append_price(db, security_id, close=10.0, recorded_at=t1)  # genuine dup of v1
    db.commit()

    ids = dedup.duplicate_ids(db, "fact_price_eod")

    assert ids == [v2]  # only A's real dup — B's single-row partition is never touched
    assert foreign not in ids
    assert v1 not in ids


# --------------------------------------------------------------------------------------------------
# 4. --tables whitelist rejects an unknown table; --apply with a failing --verify-asof rolls back and
# leaves counts unchanged.
# --------------------------------------------------------------------------------------------------


def test_tables_whitelist_rejects_unknown_table():
    with pytest.raises(SystemExit, match="unknown table"):
        dedup._validate_tables(["fact_price_eod", "not_a_real_table"])


def test_apply_rolls_back_on_verify_asof_mismatch(db, security_id, monkeypatch):
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 2, tzinfo=timezone.utc)
    _append_price(db, security_id, close=10.0, recorded_at=t0)
    _append_price(db, security_id, close=10.0, recorded_at=t1)  # a real, deletable dup
    db.commit()
    before = _count(db, "fact_price_eod")
    assert before == 2

    calls = {"n": 0}

    def fake_snapshot(conn, asof, known_at):
        calls["n"] += 1
        # first call = the pre-apply baseline; every call after = the post-delete recheck — made to
        # DIFFER so the tool must detect it and abort, exactly the "monkeypatch the verify to differ"
        # scenario the coordinator asked for.
        return {} if calls["n"] == 1 else {uuid.uuid4(): "changed"}

    monkeypatch.setattr(dedup, "snapshot_canonical_calls", fake_snapshot)

    with pytest.raises(SystemExit):
        dedup.main(["--apply", "--tables", "fact_price_eod", "--verify-asof", "2026-06-01"])

    # main() opened its OWN connection and rolled it back — confirm via a FRESH connection (a rollback
    # on one session is invisible to another only in that it never committed; re-reading proves nothing
    # was written system-wide).
    fresh = connect()
    try:
        assert _count(fresh, "fact_price_eod") == before
    finally:
        fresh.rollback()
        fresh.close()
    assert calls["n"] >= 2  # both the baseline and at least one post-delete recheck ran


def test_require_database_url_refuses_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        dedup.require_database_url()
