"""S2 — market cap after a split with a pre-split share count, on REAL data: BTOC's 10-Q cover reports
45,443,079 shares as of 2026-05-12; its 1-for-16 took effect 2026-10-15, so every close since is on the
post-split scale while the count is still the pre-split one. Latest close x latest count read 16x too high.
"""

from __future__ import annotations

from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.thesis import BasketMember
from ingest.prices.eod_loader import ingest_prices
from ingest.shares import ingest_shares_outstanding
from pipeline import repair_split_basis as tool
from signals.base import PointInTimeData
from tests import split_s2_fixtures as fx
from workbench.scoring import score_member

ASOF = date(2026, 10, 19)


def _seed(db, *, with_list: bool):
    sid = fx.seed_security(db, "BTOC")
    ingest_prices(db, sid, fx.rows("BTOC"), tenant_id=DEFAULT_TENANT_ID)
    (sh,) = fx.shares_rows("BTOC")
    ingest_shares_outstanding(
        db,
        sid,
        shares=float(sh["shares"]),
        source=sh["source"],
        source_ref=sh["source_ref"],
        event_date=date.fromisoformat(sh["valid_from"]),
        ratified_by="operator",
    )
    if with_list:
        fx.seed_splits(db, sid, "BTOC")
    db.commit()
    if with_list:
        tool.apply_plans(db, tool.plan_all(db, tickers=["BTOC"], today=fx.TODAY))
    return sid


def _cap(db, sid):
    pit = PointInTimeData(db, asof=ASOF, tenant_id=DEFAULT_TENANT_ID)
    member = BasketMember(ticker="BTOC", role="test", security_id=sid)
    return score_member(pit, member, DEFAULT_CONFIG).market_cap


def test_the_pre_split_count_is_put_on_the_post_split_scale(db):
    sid = _seed(db, with_list=True)
    close = float(next(r["close"] for r in fx.rows("BTOC") if r["d"] == ASOF))
    cap = _cap(db, sid)
    assert cap.value == round(45_443_079 / 16 * close)
    note = next(p for p in cap.provenance if p.ref == "market-cap:split-adjusted-shares")
    assert note.detail["share_factor"] == pytest.approx(1 / 16)
    assert "2026-05-12" in note.detail["note"] and "1-for-16" in note.detail["note"]


def test_without_the_list_the_cap_reads_sixteen_times_too_high(db):
    sid = _seed(db, with_list=False)
    close = float(next(r["close"] for r in fx.rows("BTOC") if r["d"] == ASOF))
    cap = _cap(db, sid)
    assert cap.value == round(45_443_079 * close)
    assert all(p.ref != "market-cap:split-adjusted-shares" for p in cap.provenance)
