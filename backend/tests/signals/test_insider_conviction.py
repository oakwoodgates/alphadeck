from __future__ import annotations

from datetime import date
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role
from signals import insider_conviction

ASOF = date(2026, 6, 4)
SID = uuid4()


def _buy(name, role, usd, d=date(2026, 5, 20), code="P"):
    return {
        "txn_code": code,
        "usd": usd,
        "insider_name": name,
        "insider_role": role,
        "valid_from": d,
        "accession": f"acc-{name}",
    }


def test_core_when_two_senior_insiders_buy_big():
    txns = [
        _buy("Jane Doe", "Chief Executive Officer", 150_000),
        _buy("John Roe", "Chief Financial Officer", 120_000),
    ]
    ev = insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.role is Role.ENTRY_TRIGGER and ev.kind is Kind.INSIDER
    assert ev.grade is Grade.CORE
    assert ev.alpha_half_life_days == DEFAULT_CONFIG.insider_alpha_half_life_days
    assert len(ev.provenance) == 2  # one per accession


def test_flip_when_single_insider():
    ev = insider_conviction.score([_buy("Jane Doe", "Chief Executive Officer", 50_000)], SID, ASOF)
    assert ev is not None and ev.grade is Grade.FLIP  # only one distinct insider


def test_not_fired_on_sales_only():
    assert insider_conviction.score([_buy("Jane Doe", "CEO", 500_000, code="S")], SID, ASOF) is None


def test_not_fired_below_min_usd():
    assert insider_conviction.score([_buy("Jane Doe", "CEO", 5_000)], SID, ASOF) is None


def test_ignores_buys_outside_lookback():
    old = [_buy("Jane Doe", "CEO", 200_000, d=date(2026, 1, 1))]  # > 90d before asof
    assert insider_conviction.score(old, SID, ASOF) is None
