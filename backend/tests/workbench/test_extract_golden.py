"""The extractor golden oracle (Slice hybrid-1) — OFFLINE: run the extractor on the four cached seed
filings and reproduce the hand-ratified seed matrix. AUTO cells exact; FLAG cells flagged WITH the located
passage; purity HUMAN and NEVER auto-valued; AND the AUTO cells do not spuriously flag (precision). The
#49 seed is the oracle. Pure (no DB, no network) — the cached fixtures are the inputs.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from domain.config import ExtractorConfig
from domain.extraction import Tier
from ingest.edgar.extract import extract_facts

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor"

# the #49 oracle (hand-ratified, three-pass-reconciled) — the values the extractor is graded against
SEED = {
    "LEU": dict(
        cik=1065059,
        tenk_date=date(2025, 12, 31),
        shares=19_672_794,
        cash=1_868_200_000,
        qburn=35_100_000,
        tenq="https://www.sec.gov/Archives/edgar/data/1065059/000162828026030891/leu-20260331.htm",
    ),
    "SMR": dict(
        cik=1822966,
        tenk_date=date(2025, 12, 31),
        shares=365_481_156,
        cash=1_008_763_000,
        qburn=50_483_000,
        tenq="https://www.sec.gov/Archives/edgar/data/1822966/000182296626000054/smr-20260331.htm",
    ),
    "OKLO": dict(
        cik=1849056,
        tenk_date=date(2025, 12, 31),
        shares=173_990_987,
        cash=2_536_898_000,
        qburn=17_867_000,
        tenq="https://www.sec.gov/Archives/edgar/data/1849056/000162828026034095/oklo-20260331.htm",
    ),
    "NNE": dict(
        cik=1923891,
        tenk_date=date(2025, 9, 30),
        shares=52_083_294,
        cash=568_895_558,
        qburn=5_264_361,
        tenq="https://www.sec.gov/Archives/edgar/data/1923891/000149315226023071/form10-q.htm",
    ),
}


def _extract(name: str, cfg: ExtractorConfig = ExtractorConfig()):
    s = SEED[name]
    cf = json.loads((_FX / f"CIK{s['cik']:010d}.json").read_text(encoding="utf-8"))
    q = (_FX / f"{name}-10q.txt").read_text(encoding="utf-8")
    k = (_FX / f"{name}-10k.txt").read_text(encoding="utf-8")
    facts = extract_facts(
        cf,
        q,
        k,
        tenq_ref=s["tenq"],
        tenk_ref="10-K",
        tenq_date=date(2026, 3, 31),
        tenk_date=s["tenk_date"],
        cfg=cfg,
    )
    return {f.fact_type: f for f in facts}


# ---------------------------------------------------------------------------------------------------------
# Tier 3 — purity is interpretation-bound: located, NEVER auto-valued
# ---------------------------------------------------------------------------------------------------------
def test_purity_is_uniformly_human_and_never_valued():
    for name in SEED:
        p = _extract(name)["revenue_mix"]
        assert p.tier is Tier.HUMAN, name
        assert (
            p.value is None
        ), name  # purity is the operator's edge — the extractor never proposes a number
        assert p.located_passages, name  # but the evidence IS located (segment footnote / Item-1)
    # the locate target differs by name: revenue -> segment; pre-revenue -> business-description
    assert _extract("LEU")["revenue_mix"].source == "10-k-segment"
    assert _extract("OKLO")["revenue_mix"].source == "10-k-business-description"


# ---------------------------------------------------------------------------------------------------------
# Tier 1/2 — shares (market cap): single-class current -> AUTO exact; dual-class -> FLAG with the A+B sum
# ---------------------------------------------------------------------------------------------------------
def test_shares_auto_single_class_exact():
    for name in ("OKLO", "NNE"):
        sh = _extract(name)["shares_outstanding"]
        assert sh.tier is Tier.AUTO and sh.value == SEED[name]["shares"], name


def test_shares_dual_class_flagged_with_ab_sum():
    for name in ("LEU", "SMR"):
        sh = _extract(name)["shares_outstanding"]
        assert sh.tier is Tier.FLAG and "dual-class" in sh.flags, name
        assert (
            sh.value == SEED[name]["shares"]
        ), name  # the cover A+B sum (the total economic count)


# ---------------------------------------------------------------------------------------------------------
# Tier 1/2 — cash: cash-only -> AUTO exact; marketable securities present -> FLAG (verify the basis)
# ---------------------------------------------------------------------------------------------------------
def test_cash_auto_when_no_marketable_securities():
    leu = _extract("LEU")["cash_burn"]
    assert leu.tier is Tier.AUTO and leu.cash_usd == SEED["LEU"]["cash"]


def test_cash_flagged_when_marketable_securities_present():
    smr = _extract("SMR")["cash_burn"]
    assert "verify-marketable-securities" in smr.flags
    assert (
        smr.cash_usd == SEED["SMR"]["cash"]
    )  # SMR's tag basis happens to reconcile; still flagged to verify
    # OKLO: the companyfacts tag sum UNDERCOUNTS -> the FLAG is what catches it (the operator reads the BS)
    oklo = _extract("OKLO")["cash_burn"]
    assert "verify-marketable-securities" in oklo.flags
    assert oklo.cash_usd != SEED["OKLO"]["cash"] and any(
        p.kind == "balance-sheet" for p in oklo.located_passages
    )


# ---------------------------------------------------------------------------------------------------------
# THE ACID TEST + PRECISION — the one-time (ENTRA1) detector
# ---------------------------------------------------------------------------------------------------------
def test_one_time_acid_test_smr_flagged_and_located():
    smr = _extract("SMR")["cash_burn"]
    assert "possible-one-time" in smr.flags
    # the extractor reports the RAW op-cash-use and NEVER subtracts — the operator ratifies to the recurring
    assert smr.quarterly_burn_usd == 314_678_000
    assert (
        smr.quarterly_burn_usd != SEED["SMR"]["qburn"]
    )  # raw != recurring (the 264.195M ENTRA1 gap)
    # the located passage points at the ENTRA1 line in the cash-flow statement (264,195 in thousands)
    assert any("264,195" in p.excerpt for p in smr.located_passages)


def test_one_time_precision_clean_burns_do_not_flag():
    """The clean AUTO burns must NOT spuriously trip the one-time detector — even LEU's inventory swing
    (139% of its op-cash-use) and OKLO's share-based comp (87%), which are routine, not one-time."""
    for name in ("LEU", "OKLO"):
        cb = _extract(name)["cash_burn"]
        assert "possible-one-time" not in cb.flags, name
        assert cb.quarterly_burn_usd == SEED[name]["qburn"], name
    assert (
        _extract("LEU")["cash_burn"].tier is Tier.AUTO
    )  # LEU: no flags at all (cash-only + clean burn)


def test_ytd_quarter_is_derived():
    nne = _extract("NNE")["cash_burn"]
    assert "ytd-derived" in nne.flags
    assert nne.quarterly_burn_usd == SEED["NNE"]["qburn"]  # Q2 = the 6-month YTD - Q1 (derived)


# ---------------------------------------------------------------------------------------------------------
# the magic-number guard — the detector dials are config-driven, not hardcoded
# ---------------------------------------------------------------------------------------------------------
def test_one_time_threshold_is_config_driven():
    # raising the fraction above SMR's ~84% turns the flag OFF -> proves it's the dial, not a hardcode
    smr = _extract("SMR", cfg=ExtractorConfig(one_time_line_fraction=0.90))["cash_burn"]
    assert "possible-one-time" not in smr.flags


def test_detector_dials_route_through_config():
    src = (Path(__file__).resolve().parents[2] / "ingest" / "edgar" / "extract.py").read_text(
        encoding="utf-8"
    )
    # the thresholds come from ExtractorConfig (no bare cutoff literals in the detector logic)
    assert "cfg.one_time_line_fraction" in src
    assert "cfg.quarterly_span_max_days" in src
    assert "0.70" not in src and "0.40" not in src  # the dial values live only in config.py
