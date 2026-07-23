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


def test_market_cap_shares_without_price_stays_visible(db, security_id):
    """The gate-3 "no save?" fix: a RATIFIED shares fact with no price bars must not vanish into a bare
    "—" — the figure stays value-None (no cap without both inputs, no fake number) but carries the fact's
    provenance + a note naming the missing half, so the confirm is visibly ON FILE."""
    ingest_shares_outstanding(
        db,
        security_id,
        shares=1_129_393_151,
        source="10-q-cover",
        source_ref="10-Q",
        event_date=date(2026, 6, 17),
        ratified_by="operator",
    )
    db.commit()
    pit = PointInTimeData(db, asof=date(2026, 7, 1), tenant_id=DEFAULT_TENANT_ID)
    m = score_member(pit, _member(security_id), DEFAULT_CONFIG)
    assert m.market_cap.value is None  # still no cap — price is genuinely missing
    assert len(m.market_cap.provenance) == 2  # the ratified fact + the awaiting-price note
    assert m.market_cap.provenance[0].source == "10-q-cover"  # the fact is VISIBLE
    notes = " ".join(str(p.detail.get("note", "")) for p in m.market_cap.provenance)
    assert "No price bars" in notes and "on file" in notes  # the missing half is NAMED
    # Part B (ENDV): the share count's cover AS-OF date rides the provenance so the FE can age it (a stale
    # count -> a plausible-but-wrong cap). Display-only; the value/pips are untouched.
    assert m.market_cap.provenance[0].detail.get("shares_asof") == "2026-06-17"


def test_market_cap_carries_shares_asof_when_priced(db, security_id):
    """The computed-cap path threads shares_asof too (not just the awaiting-price path)."""
    ingest_shares_outstanding(
        db,
        security_id,
        shares=1_000_000,
        source="10-q-cover",
        source_ref="10-Q",
        event_date=date(2023, 12, 28),  # the ENDV shape: a years-old cover
        ratified_by="auto",
    )
    _price(db, security_id, date(2026, 7, 1), 10.0)
    db.commit()
    pit = PointInTimeData(db, asof=date(2026, 7, 1), tenant_id=DEFAULT_TENANT_ID)
    m = score_member(pit, _member(security_id), DEFAULT_CONFIG)
    assert m.market_cap.value == 10_000_000  # the cap computes (1M x $10)
    sh = next(p for p in m.market_cap.provenance if p.source == "10-q-cover")
    assert (
        sh.detail.get("shares_asof") == "2023-12-28"
    )  # ...and the stale as-of is visible to the FE


def test_market_cap_price_without_shares_stays_visible(db, security_id):
    """The symmetric half: price bars with no ratified shares — value None, the price provenance rides
    with a note pointing at the extract → ratify step (never a silent blank)."""
    _price(db, security_id, date(2026, 6, 20), 100.0)
    db.commit()
    pit = PointInTimeData(db, asof=date(2026, 7, 1), tenant_id=DEFAULT_TENANT_ID)
    m = score_member(pit, _member(security_id), DEFAULT_CONFIG)
    assert m.market_cap.value is None
    assert [p.source for p in m.market_cap.provenance] == ["price"]
    assert any(
        "NO ratified shares" in str(p.detail.get("note", "")) for p in m.market_cap.provenance
    )


# ---------------------------------------------------------------------------------------------------------
# the ADS ratio in the cap derivation (spec §10) — apply where read, SUPPRESS where not, NULL = 1:1
# ---------------------------------------------------------------------------------------------------------


def _annual_shares(db, sid, shares, ratio, status, event=date(2025, 12, 31)):
    ingest_shares_outstanding(
        db,
        sid,
        shares=shares,
        source="annual-cover",
        source_ref="https://sec.gov/20f",
        event_date=event,
        ratified_by="operator",
        ads_ratio=ratio,
        ads_ratio_status=status,
    )


def _mcap(db, sid):
    pit = PointInTimeData(db, asof=date(2026, 7, 1), tenant_id=DEFAULT_TENANT_ID)
    return score_member(pit, _member(sid), DEFAULT_CONFIG).market_cap


def test_tsm_five_to_one_ratio_divides_cap(db, security_id):
    """THE DEFECT, fixed: TSM's fact is 25.93B ORDINARY shares while the price feed carries the ADS
    (5 ordinary each). The cap divides by the read ratio — ~$2.2T, never the raw ~$10.9T product a
    1:1 multiply displayed. The fact itself stays the true ordinary count (provenance shows both).
    """
    _annual_shares(db, security_id, 25_932_524_521, 5, "known")
    _price(db, security_id, date(2026, 6, 20), 420.0)  # the ADS price
    db.commit()
    cap = _mcap(db, security_id)
    assert cap.value == round(25_932_524_521 / 5 * 420.0)  # ≈ $2.18T
    assert cap.value < 3e12  # sanity: the $10.9T-shaped raw product must be impossible
    sh = cap.provenance[0]
    assert sh.detail["shares"] == 25_932_524_521  # the fact is NEVER pre-divided
    assert sh.detail["ads_ratio"] == 5 and sh.detail["ads_ratio_status"] == "known"


def test_imos_twenty_to_one_divides_cap(db, security_id):
    """IMOS at 20:1 — the ratio whose omission showed $44.1B for a ~$2.2B company."""
    _annual_shares(db, security_id, 699_983_126, 20, "known")
    _price(db, security_id, date(2026, 6, 20), 63.0)
    db.commit()
    assert _mcap(db, security_id).value == round(699_983_126 / 20 * 63.0)  # ≈ $2.2B


def test_one_to_one_ratio_leaves_cap_unchanged(db, security_id):
    """A READ 1:1 (NVS / ARM / KYOCY) divides by one — same number as the plain product, with the read
    ratio visible in provenance (a 1:1 read and a no-evidence assumption are different provenance).
    """
    _annual_shares(db, security_id, 1_908_151_679, 1, "known")
    _price(db, security_id, date(2026, 6, 20), 110.0)
    db.commit()
    cap = _mcap(db, security_id)
    assert cap.value == round(1_908_151_679 * 110.0)
    assert cap.provenance[0].detail["ads_ratio"] == 1


def test_adr_without_readable_ratio_suppresses_cap(db, security_id):
    """`unread` (ADR evidence, no defensible ratio — SPRC/EVO/XTLB): the cap is WITHHELD, never guessed
    at 1:1 — a wrong ratio is a multiplicative, silent, permanent error. The §10.5 shape is the
    awaiting-price idiom reused: value None, pips None, the ratified shares fact still VISIBLE, and a
    note naming the missing half (the ratio). Better detection later moves a name suppressed→correct,
    never wrong→right."""
    _annual_shares(db, security_id, 365_444, None, "unread")
    _price(db, security_id, date(2026, 6, 20), 4.2)
    db.commit()
    cap = _mcap(db, security_id)
    assert cap.value is None and cap.pips is None  # withheld — NOT a computed guess
    sh = cap.provenance[0]
    assert sh.source == "annual-cover"  # the shares provenance is still present (visible, on file)
    assert sh.detail["shares"] == 365_444
    assert sh.detail["ads_ratio_status"] == "unread"
    withheld = next(p for p in cap.provenance if p.ref == "market-cap:ads-ratio-unread")
    assert "WITHHELD" in str(withheld.detail["note"])  # the missing half is NAMED


def test_no_adr_evidence_computes_at_one_to_one(db, security_id):
    """Not applicable (ASML / CAMT / NVMI — no ADR evidence): NULL/NULL computes at 1:1; the 1:1
    assumption travels in the FACT's note (written at extract), which rides provenance detail (#6).
    """
    ingest_shares_outstanding(
        db,
        security_id,
        shares=385_417_665,
        source="annual-cover",
        source_ref="https://sec.gov/asml-20f",
        event_date=date(2025, 12, 31),
        note="20-F cover count … No ADS/ADR evidence on the cover — the count prices 1:1.",
        ratified_by="operator",
    )
    _price(db, security_id, date(2026, 6, 20), 700.0)
    db.commit()
    cap = _mcap(db, security_id)
    assert cap.value == round(385_417_665 * 700.0)
    assert "No ADS/ADR evidence" in str(cap.provenance[0].detail.get("note", ""))


def test_domestic_10q_market_cap_unchanged(db, security_id):
    """THE §10.4 REGRESSION GUARD: every legacy row and every domestic 10-Q name has NULL/NULL, which
    MUST read as not-applicable → 1:1 — byte-identical to the pre-ratio derivation. (Encoding "unread"
    as a NULL ratio would have blanked every market cap in the app.) Asserts the exact value AND that
    no ads_* keys leak into the provenance detail of a NULL/NULL row."""
    ingest_shares_outstanding(
        db,
        security_id,
        shares=1_129_393_151,
        source="10-q-cover",
        source_ref="10-Q",
        event_date=date(2026, 6, 17),
        ratified_by="operator",
    )
    _price(db, security_id, date(2026, 6, 20), 120.5)
    db.commit()
    cap = _mcap(db, security_id)
    assert cap.value == round(1_129_393_151 * 120.5)  # the pre-change number, exactly
    assert [p.source for p in cap.provenance] == ["10-q-cover", "price"]  # no extra entries
    sh = cap.provenance[0]
    assert (
        "ads_ratio" not in sh.detail and "ads_ratio_status" not in sh.detail
    )  # byte-identical shape


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
    # the ratified values ride provenance DETAIL (display-only, #2) — the ratify panel reads them so a
    # re-open shows the operator's saved values, never the stale extract candidate (the DB-free extract
    # can't know a ratify happened; the scored read is the panel's only DB-backed surface)
    assert sm.purity.provenance[0].detail["mix_pct"] == 77.0
    assert sm.purity.provenance[0].detail["segment_label"] == "reactors"
    assert sm.runway.provenance[0].detail["cash_usd"] == 1_000_000_000.0
    assert sm.runway.provenance[0].detail["quarterly_burn_usd"] == 50_000_000.0
    sh_prov = next(p for p in sm.market_cap.provenance if p.source not in ("price", "computed"))
    assert sh_prov.detail["shares"] == 100_000_000.0
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
    # all three fact-backed meters (purity / runway / market cap) are confirmed -> nothing unconfirmed
    assert sm.unconfirmed_estimates == 0


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
    # all three fact-backed meters blank -> "rests on 3 unconfirmed"
    assert sm.unconfirmed_estimates == 3


def test_vouched_is_provenance_not_a_scoring_input(db, security_id):
    """The guardrail: a vouched='overridden' fact scores EXACTLY like any other — the as-of read never branches
    on `vouched`. Together with the golden (a vouched=NULL fact scoring 4 pips), this proves NULL / confirmed /
    overridden all score identically, so no legacy NULL-vouched fact regresses."""
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="nuclear",
        mix_pct=100,
        source="10-k-segment",
        source_ref="ref",
        event_date=date(2025, 12, 31),
        ratified_by="operator",
        vouched="overridden",
    )
    db.commit()
    sm = score_member(PointInTimeData(db, asof=_ASOF, known_at=_KNOWN), _member(security_id))
    assert (
        sm.purity.pips == 4
    )  # 100% -> 4, whatever the vouched provenance; vouched never gates scoring


def test_unconfirmed_estimates_counts_blank_fact_backed_meters(db, security_id):
    """The 'rests on N unconfirmed' flag counts the fact-backed meters (purity / runway / market cap) with no
    confirmed value; confirming one drops the count. A readiness signal, never a scoring input."""
    assert (
        score_member(
            PointInTimeData(db, asof=_ASOF, known_at=_KNOWN), _member(security_id)
        ).unconfirmed_estimates
        == 3
    )
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="nuclear",
        mix_pct=100,
        source="10-k-segment",
        source_ref="ref",
        event_date=date(2025, 12, 31),
    )
    db.commit()
    # purity now confirmed -> runway + market cap still unconfirmed
    assert (
        score_member(
            PointInTimeData(db, asof=_ASOF, known_at=_KNOWN), _member(security_id)
        ).unconfirmed_estimates
        == 2
    )


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
