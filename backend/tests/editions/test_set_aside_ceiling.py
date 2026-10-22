"""The freeze ceiling x the 0062 SET-ASIDE (editions merged onto #442): a set-aside, or a revoke, recorded AFTER
an edition's cut never changes that cut's mirror — on either export clock — while the plain (no-ceiling) read is
exactly 0062's. The store is append-only, so a cut reproduces: the judgments it honors are the ones recorded by
then."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

import pyarrow.parquet as pq
import pytest

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from db.set_aside import active_ids_sql, exclusion_sql
from replay.export import export_snapshot
from repositories import fundamentals_set_aside_repo as repo


def _fact(db, sid: UUID, period_end: date, value: float) -> UUID:
    append_fact(
        db,
        "fact_fundamentals",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "metric_key": "revenue",
            "period_end": period_end,
            "fiscal_period": "Q1",
            "fiscal_year": period_end.year,
            "value": value,
            "basis": "native",
            "accession": f"0000000000-26-{period_end:%m%d}00",
            "valid_from": date(2026, 5, 1),
            "recorded_at": datetime(2026, 5, 2, tzinfo=timezone.utc),
        },
    )
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM fact_fundamentals WHERE security_id = %s AND period_end = %s",
            (sid, period_end),
        )
        fid = cur.fetchone()["id"]
    db.commit()
    return fid


def _cut(db) -> datetime:
    """An instant after everything committed so far, before anything written next."""
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        t = cur.fetchone()["t"]
    db.commit()
    return t


def _mirror_ids(db, tmp_path, name: str, clock: str, known_by: datetime | None):
    out = tmp_path / name
    manifest = export_snapshot(db, out, clock=clock, known_by=known_by)
    db.commit()
    ids = {UUID(str(x)) for x in pq.read_table(out / "fact_fundamentals.parquet")["id"].to_pylist()}
    return ids, manifest["tables"]["fact_fundamentals"]["rows_set_aside"]


@pytest.mark.parametrize("clock", ["record", "public"])
def test_a_set_aside_or_revoke_after_the_cut_is_invisible_to_that_cut(
    db, security_id, tmp_path, clock
):
    f1 = _fact(db, security_id, date(2026, 3, 31), 100.0)
    f2 = _fact(db, security_id, date(2025, 12, 31), 90.0)
    cut0 = _cut(db)  # before any judgment
    s1 = repo.append_set_aside(db, f1, reason="t", rule="t@x", author="t")
    db.commit()
    cut1 = _cut(db)  # f1 set aside
    repo.revoke(db, s1, reason="undo", rule="t@x", author="t")
    repo.append_set_aside(db, f2, reason="t", rule="t@x", author="t")
    db.commit()
    cut2 = _cut(db)  # f1 restored, f2 set aside

    now, n_now = _mirror_ids(db, tmp_path, "now", clock, None)
    at0, n0 = _mirror_ids(db, tmp_path, "c0", clock, cut0)
    at1, n1 = _mirror_ids(db, tmp_path, "c1", clock, cut1)
    at2, n2 = _mirror_ids(db, tmp_path, "c2", clock, cut2)
    assert (f1 in now, f2 in now, n_now) == (True, False, 1)  # no ceiling: 0062's reading today
    assert (f1 in at0, f2 in at0, n0) == (True, True, 0)  # both judgments came after cut0
    assert (f1 in at1, f2 in at1, n1) == (
        False,
        True,
        1,
    )  # f1's revoke and f2's set-aside came later
    assert (f1 in at2, f2 in at2, n2) == (True, False, 1)  # the cut after both = today's reading
    # re-exporting an old cut after more judgments reproduces it exactly
    assert _mirror_ids(db, tmp_path, "c1-again", clock, cut1) == (at1, n1)


def test_the_no_ceiling_exclusion_is_byte_for_byte_0062s(db):
    """The merge kept the plain read's SQL exactly as #442 wrote it: `` AND <id> NOT IN (<active ids>)``."""
    for alias, col in ((None, '"id"'), ("t", '"t"."id"')):
        got = exclusion_sql("fact_fundamentals", alias=alias).as_string(db)
        assert got == f" AND {col} NOT IN ({active_ids_sql('fact_fundamentals')})"
    assert exclusion_sql("fact_price_eod").as_string(db) == ""
