from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Archetype, CatalystType, Grade
from domain.thesis import BasketMember
from domain.workbench import ScoredFigure
from ingest.cash_burn import ingest_cash_burn
from ingest.catalyst import ingest_catalyst
from ingest.edgar.converts import ConvertTerms, ingest_convert_terms
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from signals.base import PointInTimeData
from workbench import scoring
from workbench.scoring import _archetype_hint, score_member

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_ASOF = date(2026, 6, 2)


def _member(security_id, segment=None) -> BasketMember:
    return BasketMember(
        ticker="X", role="r", archetype=Archetype.LEADER, security_id=security_id, segment=segment
    )


def _price(db, security_id, d: date, close: float) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "d": d,
            "close": close,
            "valid_from": d,
        },
    )


def _hims_converts() -> ConvertTerms:
    return ConvertTerms(
        principal_total_usd=402_500_000.0,
        coupon_pct=0.0,
        maturity_date=date(2032, 6, 1),
        conversion_rate=33.8590,
        conversion_price_usd=29.53,
        cap_price_usd=50.15,
        capped_call_cost_usd=36_700_000.0,
        issued_date=date(2026, 5, 21),
    )


def test_score_member_golden(db, security_id):
    """Fixed facts -> exact pips / values / provenance (deterministic; the magic-number behavioral guard
    below proves the cutoffs are config-driven, not these specific numbers)."""
    # purity: 77% on a revenue-segment basis -> 3 pips (>=50, <80)
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="reactors",
        mix_pct=77,
        source="10-k-segment",
        source_ref="10-K-seg",
        event_date=date(2025, 12, 31),
    )
    # runway: $1.0B cash, $50M/qtr burn -> 1000 / (50/3) = 60 months -> 4 pips (>=24)
    ingest_cash_burn(
        db,
        security_id,
        cash_usd=1_000_000_000,
        quarterly_burn_usd=50_000_000,
        source="10-q",
        source_ref="10-Q",
        event_date=date(2026, 3, 31),
    )
    # catalysts: one LIVE core catalyst -> 2 pips
    ingest_catalyst(
        db,
        security_id,
        catalyst_type=CatalystType.CONTRACT,
        grade=Grade.CORE,
        label="a contract",
        source="ratified",
        source_ref="CAT-1",
        event_date=date(2026, 5, 1),
    )
    # dilution: the HIMS converts (~6.0% overhang) -> 1 pip (>=2, <8)
    ingest_convert_terms(
        db,
        security_id,
        _hims_converts(),
        accession="CONV-1",
        shares_outstanding=228_357_303,
        shares_outstanding_ref="10-Q",
    )
    # market cap: 100M shares x $25 close = $2.5B (a figure, pips stays None)
    ingest_shares_outstanding(
        db,
        security_id,
        shares=100_000_000,
        source="10-q-cover",
        source_ref="SH-1",
        event_date=date(2026, 5, 1),
    )
    _price(db, security_id, date(2026, 6, 1), 25.0)
    db.commit()

    sm = score_member(PointInTimeData(db, asof=_ASOF, known_at=_KNOWN), _member(security_id))

    assert sm.purity.pips == 3 and sm.purity.value == 77.0
    assert (
        sm.purity.provenance[0].source == "10-k-segment"
        and sm.purity.provenance[0].ref == "10-K-seg"
    )
    assert sm.runway.pips == 4 and sm.runway.value == 60.0
    assert sm.catalysts.pips == 2 and sm.catalysts.value == 1.0
    assert sm.dilution.pips == 1 and sm.dilution.value == 6.0  # ~5.97% overhang, rounded
    assert sm.dilution.provenance[0].source == "8-k" and sm.dilution.provenance[0].ref == "CONV-1"
    assert sm.market_cap.pips is None and sm.market_cap.value == 2_500_000_000
    assert (
        sm.fit == "core exposure"
    )  # purity 3; runway 4 (no funding risk); dilution 1 (no dilution risk)
    # archetype recommendation (#10): $2.5B (>= $500M, < $10B) + purity 3 (not off-thesis) -> high_beta
    assert sm.archetype_hint is Archetype.HIGH_BETA


def test_no_data_reads_dash_not_zero(db, security_id):
    """A bare security: the meters that have no fact read "—" (pips None); catalysts is a real 0 (the
    operator's rule — no fake zeros for dilution/purity/runway/market-cap, but 0 catalysts is a 0).
    """
    sm = score_member(PointInTimeData(db, asof=_ASOF, known_at=_KNOWN), _member(security_id))
    assert sm.purity.pips is None and sm.runway.pips is None
    assert sm.dilution.pips is None and sm.market_cap.pips is None
    assert sm.catalysts.pips == 0
    assert sm.fit == "unrated"  # no purity data
    assert (
        sm.archetype_hint is None
    )  # no market cap -> the recommendation abstains (the operator's default stands)


def test_no_lookahead(db, security_id):
    """A fact whose event date is after `asof` does not enter a score (point-in-time honesty)."""
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="reactors",
        mix_pct=100,
        source="10-k-segment",
        source_ref="FUTURE",
        event_date=date(2026, 9, 1),  # AFTER asof
    )
    db.commit()
    sm = score_member(PointInTimeData(db, asof=_ASOF, known_at=_KNOWN), _member(security_id))
    assert sm.purity.pips is None  # the 2026-09-01 fact is in the future as of 2026-06-02


def test_pip_cutoffs_are_config_driven(db, security_id):
    """The magic-number BEHAVIORAL guard: a changed CallConfig cutoff changes a pip (the cutoffs are read
    from cfg, never hardcoded)."""
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="reactors",
        mix_pct=77,
        source="10-k-segment",
        source_ref="10-K-seg",
        event_date=date(2025, 12, 31),
    )
    db.commit()
    pit = PointInTimeData(db, asof=_ASOF, known_at=_KNOWN)
    assert score_member(pit, _member(security_id)).purity.pips == 3  # default 80-bar: 77 -> 3
    loosened = DEFAULT_CONFIG.model_copy(update={"purity_pip_pct": (10.0, 25.0, 50.0, 75.0)})
    assert score_member(pit, _member(security_id), loosened).purity.pips == 4  # 75-bar: 77 -> 4


# --- the archetype recommendation (Slice 4, #10) — a pure function of the figures; every branch + abstention ---


def _cap(value: float) -> ScoredFigure:
    return ScoredFigure(value=value)  # a market-cap figure (pips stay None)


def _purity(pips: int | None) -> ScoredFigure:
    return ScoredFigure(pips=pips, value=None if pips is None else float(pips * 25))


def test_archetype_hint_abstains_without_market_cap():
    """No market cap (no facts yet) -> None: the recommendation abstains, the operator's default stands."""
    assert _archetype_hint(ScoredFigure(), _purity(4), DEFAULT_CONFIG) is None


def test_archetype_hint_reads_cap_tiers():
    """The size read: large-cap -> leader, mid -> high_beta, micro -> lotto (default $10B / $500M bars)."""
    assert _archetype_hint(_cap(12e9), _purity(4), DEFAULT_CONFIG) is Archetype.LEADER
    assert _archetype_hint(_cap(2.5e9), _purity(4), DEFAULT_CONFIG) is Archetype.HIGH_BETA
    assert _archetype_hint(_cap(2e8), _purity(4), DEFAULT_CONFIG) is Archetype.LOTTO


def test_archetype_hint_adjacent_for_low_purity_regardless_of_size():
    """An off-thesis (low-purity) name reads adjacent even at large-cap size — purity gates before cap."""
    assert _archetype_hint(_cap(12e9), _purity(0), DEFAULT_CONFIG) is Archetype.ADJACENT
    assert _archetype_hint(_cap(12e9), _purity(1), DEFAULT_CONFIG) is Archetype.ADJACENT


def test_archetype_hint_cap_tier_when_purity_unknown():
    """Purity with no data (pips None) never blocks a cap-tier read — a mid-cap still recommends high_beta."""
    assert _archetype_hint(_cap(2.5e9), ScoredFigure(), DEFAULT_CONFIG) is Archetype.HIGH_BETA


def test_archetype_hint_cutoffs_are_config_driven():
    """The magic-number guard for the recommendation: a changed CallConfig cap bar changes the hint."""
    cap = _cap(2.5e9)  # mid-cap -> high_beta by default
    assert _archetype_hint(cap, _purity(4), DEFAULT_CONFIG) is Archetype.HIGH_BETA
    lowered = DEFAULT_CONFIG.model_copy(update={"archetype_leader_min_cap_usd": 2e9})
    assert (
        _archetype_hint(cap, _purity(4), lowered) is Archetype.LEADER
    )  # now $2.5B clears the leader bar


def test_scorer_has_no_magic_number_thresholds():
    """The magic-number LEXICAL guard (mirrors the assembler's): no float literals in the scorer — every
    cutoff comes from CallConfig."""
    src = Path(scoring.__file__).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    floats = re.findall(r"\b\d+\.\d+\b", code)
    assert floats == [], f"cutoffs must come from CallConfig; found float literals: {floats}"
