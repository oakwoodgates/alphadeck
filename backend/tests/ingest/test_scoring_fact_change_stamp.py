"""The four scoring-fact writers (``ingest_revenue_mix`` / ``ingest_shares_outstanding`` / ``ingest_cash_burn`` /
``ingest_catalyst``) run outside the company write lock: the Workbench's ``POST /workbench/facts`` and
``POST /workbench/facts/auto-confirm``, the ``pipeline.ratify_*`` CLIs. So each moves the company's change stamp
in the SAME transaction as its fact (``db.change_stamp``: the dashboards' cache drops that company's pieces).
The route-level proof is ``tests/app/test_workbench_api.py``'s. DB-backed."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.change_stamp import stamp_map
from db.session import DEFAULT_TENANT_ID
from domain.enums import CatalystType, Grade
from ingest.cash_burn import ingest_cash_burn
from ingest.catalyst import ingest_catalyst
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding

_COMMON = dict(source="ratified", source_ref="10-Q-2026Q1", event_date=date(2026, 5, 1))

WRITERS = {
    "revenue_mix": lambda db, sid: ingest_revenue_mix(
        db, sid, segment_label="core", mix_pct=100, ratified_by="operator", **_COMMON
    ),
    "shares_outstanding": lambda db, sid: ingest_shares_outstanding(
        db, sid, shares=1_000_000, ratified_by="operator", **_COMMON
    ),
    "cash_burn": lambda db, sid: ingest_cash_burn(
        db,
        sid,
        cash_usd=280_000_000,
        quarterly_burn_usd=25_000_000,
        ratified_by="operator",
        **_COMMON,
    ),
    "catalyst": lambda db, sid: ingest_catalyst(
        db,
        sid,
        catalyst_type=CatalystType.REGULATORY,
        grade=Grade.CORE,
        label="a filed award",
        ratified_by="operator",
        **_COMMON,
    ),
}


def _security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, date(2025, 1, 1)),
        )
    db.commit()
    return sid


@pytest.mark.parametrize("fact", sorted(WRITERS))
def test_a_scoring_fact_moves_its_companys_change_stamp_in_its_own_transaction(db, fact):
    sid, other = _security(db, "AAA"), _security(db, "BBB")
    WRITERS[fact](db, sid)
    db.rollback()
    assert stamp_map(db) == {}  # rolled back with the fact: nothing stamped
    db.rollback()
    WRITERS[fact](db, sid)
    db.commit()
    first = stamp_map(db)
    db.rollback()
    assert set(first) == {sid} and other not in first
    WRITERS[fact](db, sid)  # a re-ratify is a new row and a new stamp
    db.commit()
    assert stamp_map(db)[sid] > first[sid]
    db.rollback()
