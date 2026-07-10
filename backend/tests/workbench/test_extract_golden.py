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


def _extract(
    name: str,
    cfg: ExtractorConfig = ExtractorConfig(),
    tenq_date: date = date(2026, 3, 31),
    companyfacts: dict | None = None,
):
    """``tenq_date`` is the 10-Q's PERIOD OF REPORT (what the live wrapper threads from submissions
    ``reportDate``) — the shares staleness gate + event_date stamps are period semantics."""
    s = SEED[name]
    cf = companyfacts or json.loads((_FX / f"CIK{s['cik']:010d}.json").read_text(encoding="utf-8"))
    q = (_FX / f"{name}-10q.txt").read_text(encoding="utf-8")
    k = (_FX / f"{name}-10k.txt").read_text(encoding="utf-8")
    facts = extract_facts(
        cf,
        q,
        k,
        tenq_ref=s["tenq"],
        tenk_ref="10-K",
        tenq_date=tenq_date,
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
    # LEU/SMR carry NO dei cover rows at all (dual-class filers report DEI per class with dimension
    # members that companyfacts DROPS) — the dual-class label comes from the >=2 per-class counts
    # OBSERVED on the cover text, which is the honest evidence for the flag.
    for name in ("LEU", "SMR"):
        sh = _extract(name)["shares_outstanding"]
        assert sh.tier is Tier.FLAG and sh.flags == ["dual-class"], name
        assert (
            sh.value == SEED[name]["shares"]
        ), name  # the cover A+B sum (the total economic count)


def test_shares_auto_survives_the_live_cover_date_shape():
    """THE REGRESSION for the every-name "dual-class" mis-flag: a 10-Q cover is dated AFTER the period
    end and BEFORE the filing date, so the currency gate must compare against the PERIOD OF REPORT.
    OKLO's cover (2026-05-07) vs its Q1 period (2026-03-31) -> AUTO; threading a FILING-like date
    (after the cover, what the live wrapper used to do) must NOT resurrect a class claim — it reads
    stale-cover, the condition actually observed."""
    sh = _extract("OKLO")["shares_outstanding"]
    assert sh.tier is Tier.AUTO and not sh.flags
    counterfactual = _extract("OKLO", tenq_date=date(2026, 5, 13))["shares_outstanding"]
    assert counterfactual.tier is Tier.FLAG
    assert counterfactual.flags == ["stale-cover"]  # NEVER "dual-class" for a single-class name


def test_shares_stale_cover_offers_the_value_dated_by_its_own_as_of():
    """A lagging companyfacts is a STALENESS condition, not a class structure: the single-class count is
    offered honestly (the operator confirms currency), and its event_date is the count's OWN as-of date
    (valid-time honesty), never the period it failed to reach."""
    sh = _extract("OKLO", tenq_date=date(2026, 6, 1))["shares_outstanding"]
    assert sh.tier is Tier.FLAG and sh.flags == ["stale-cover"]
    assert sh.value == SEED["OKLO"]["shares"]  # the stale count, offered — not a guess, not None
    assert sh.event_date == date(2026, 5, 7)  # OKLO's cover as-of date, not the requested period
    assert "OLDER than the filing period end" in sh.note


def test_shares_no_companyfacts_and_classless_cover_is_located_only():
    """Nothing observed anywhere — no dei concept, no per-class counts on the cover — is its OWN label
    (never a class claim), with no value (the operator authors from the located cover)."""
    s = SEED["OKLO"]
    cf = json.loads((_FX / f"CIK{s['cik']:010d}.json").read_text(encoding="utf-8"))
    del cf["facts"]["dei"]["EntityCommonStockSharesOutstanding"]
    sh = _extract("OKLO", companyfacts=cf)["shares_outstanding"]
    assert sh.tier is Tier.FLAG and sh.flags == ["no-companyfacts"]
    assert sh.value is None
    assert sh.located_passages  # the cover is still located — evidence, not a guess


# ---------------------------------------------------------------------------------------------------------
# cash+burn honest labels (the runway audit) — one OBSERVED condition, one label; no fake zeros.
# Unit-level on the pure _cash_burn with synthetic companyfacts (every path pinned).
# ---------------------------------------------------------------------------------------------------------
from ingest.edgar.extract import _cash_burn  # noqa: E402  (unit target — the pure composer)

_PERIOD = date(2026, 5, 28)
_TEXT = (
    "cash and cash equivalents 104,272 total current assets ... "
    "cash flows from operating activities (5,452) year-to-date ..."
)


def _dur(start, end, val):
    return {"start": start, "end": end, "val": val}


def _inst(end, val):
    return {"end": end, "val": val, "filed": end}


def _facts(ocf=None, cash=None, sti=None, extra=None):
    g = {}
    if ocf is not None:
        g["NetCashProvidedByUsedInOperatingActivities"] = {"units": {"USD": ocf}}
    if cash is not None:
        g["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": cash}}
    if sti is not None:
        g["ShortTermInvestments"] = {"units": {"USD": sti}}
    for concept, rows in (extra or {}).items():
        g[concept] = {"units": {"USD": rows}}
    return {"us-gaap": g}


def _cb(facts):
    return _cash_burn(facts, _TEXT, "10-Q", _PERIOD, ExtractorConfig())


def test_cash_ytd_raw_is_never_claimed_derived():
    """LIE A pinned: a long-span OCF column with NO same-start prior CANNOT be derived — the raw YTD goes
    out as ``ytd-raw`` with a passage saying it IS the YTD, never the old ``ytd-derived`` label + a
    passage claiming a derivation that didn't happen (runway would have read ~3x too short, believed).
    """
    f = _facts(
        ocf=[_dur("2025-08-29", "2026-05-28", -90e6)],  # 272 days, no prior
        cash=[_inst("2026-05-28", 100e6)],
    )
    cb = _cb(f)
    assert cb.tier is Tier.FLAG
    assert "ytd-raw" in cb.flags and "ytd-derived" not in cb.flags
    assert (
        cb.quarterly_burn_usd == 90e6
    )  # the RAW YTD, honestly labeled — a real figure, not a guess
    assert any("NOT a quarter" in p.excerpt for p in cb.located_passages)


def test_cash_clean_derived_quarter_is_auto_with_the_derivation_in_the_note():
    """THE RE-TIER pinned: a cleanly-derived quarter (both YTD columns on file) is reproducible
    arithmetic — AUTO, the derivation stated in the note, never a flag. (As a FLAG, ``ytd-derived``
    fired on ~3 of 4 filings — GAAP 10-Qs report cash flow YTD — so it marked the RULE, not the
    exception, and AUTO was structurally empty.) The one-time detector still runs on the derived
    quarter — see ``test_one_time_inside_a_derived_quarter_still_flags``."""
    f = _facts(
        ocf=[_dur("2025-08-29", "2026-05-28", -90e6), _dur("2025-08-29", "2026-02-26", -60e6)],
        cash=[_inst("2026-05-28", 100e6)],
    )
    cb = _cb(f)
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.quarterly_burn_usd == 30e6  # YTD − prior YTD = the actual quarter
    assert "derived (YTD − prior YTD)" in cb.note  # composition = provenance, stated where ratified
    assert cb.event_date == date(2026, 5, 28)  # dated by the burn period's own end


def test_one_time_inside_a_derived_quarter_still_flags():
    """The re-tier's counterfactual: AUTO-derived must NOT bypass the one-time detector — a derived
    quarter carrying an anomalous accrued line still FLAGs (the exception outranks the clean basis).
    """
    f = _facts(
        ocf=[_dur("2025-08-29", "2026-05-28", -90e6), _dur("2025-08-29", "2026-02-26", -60e6)],
        cash=[_inst("2026-05-28", 100e6)],
        extra={"IncreaseDecreaseInAccruedLiabilities": [_dur("2026-02-26", "2026-05-28", -25e6)]},
    )
    cb = _cb(f)
    assert cb.tier is Tier.FLAG and cb.flags == ["possible-one-time"]
    assert "derived (YTD − prior YTD)" in cb.note  # the basis still stated alongside the flag


def test_cash_no_companyfacts_is_a_located_only_flag_never_auto_zero():
    """LIE B pinned: a filer with NO cash instant and NO OCF column used to go out AUTO / $0 / $0 /
    "Clean quarter" — a confirmable fake zero. Now: FLAG, values None, located-only."""
    cb = _cb(_facts())
    assert cb.tier is Tier.FLAG and cb.flags == ["no-companyfacts"]
    assert cb.cash_usd is None and cb.quarterly_burn_usd is None
    assert cb.located_passages  # the statements are still located — evidence, not a guess
    assert "Clean quarter" not in cb.note


def test_cash_without_ocf_never_fakes_cash_generative():
    """The half case: cash on file, no operating-cash-flow column — burn stays None (its own flag),
    because burn=0 ratified straight into a fake top-pip "cash-generative" on zero evidence."""
    cb = _cb(_facts(cash=[_inst("2026-05-28", 104_272_000)]))
    assert cb.tier is Tier.FLAG and "no-cashflow-column" in cb.flags
    assert cb.cash_usd == 104_272_000 and cb.quarterly_burn_usd is None
    assert "NOT FOUND (no operating-cash-flow column)" in cb.note


def test_cash_ocf_without_cash_instant_names_the_miss():
    cb = _cb(_facts(ocf=[_dur("2026-03-01", "2026-05-28", -5e6)]))  # native 88-day quarter
    assert "no-cash-instant" in cb.flags
    assert cb.cash_usd is None and cb.quarterly_burn_usd == 5e6
    assert "cash: NOT FOUND" in cb.note


def test_cash_stale_instant_is_flagged_and_dated():
    """GAP C pinned: an included instant older than the filing period flags ``stale-cash`` and the note
    STATES every input's as-of (the dates were previously discarded — silent mixing)."""
    f = _facts(
        ocf=[_dur("2026-03-01", "2026-05-28", -5e6)],
        cash=[_inst("2026-02-26", 100e6)],  # a quarter older than the period
    )
    cb = _cb(f)
    assert "stale-cash" in cb.flags
    assert "cash as of 2026-02-26" in cb.note


def test_cash_offdate_marketable_is_excluded_never_summed():
    """A balance sheet is ONE date (the MU live catch): a marketable instant dated differently from cash
    is a DIFFERENT balance sheet — usually a discontinued tag (MU's AvailableForSaleSecurities* last
    reported 2018, and the old bare-value composer silently added those eight-year-old balances into
    CURRENT cash). Excluded from the sum and NAMED in the note — no flag (the re-tier): the exclusion
    errs CONSERVATIVE (understated cash reads runway SHORTER), so the alarm, when it matters, is the
    meter reading short — and the note right there says where current investments might live."""
    f = _facts(
        ocf=[_dur("2026-03-01", "2026-05-28", -5e6)],
        cash=[_inst("2026-05-28", 100e6)],
        sti=[_inst("2018-11-29", 50e6)],  # eight years off-date — a discontinued tag
    )
    cb = _cb(f)
    assert cb.cash_usd == 100e6  # cash ONLY — the 2018 balance never enters the sum
    assert cb.tier is Tier.AUTO and cb.flags == []  # composition is a note, never an alarm
    assert "EXCLUDED from the sum" in cb.note and "2018-11-29" in cb.note


def test_cash_samedate_marketable_sums_quietly_into_the_composition():
    """Same-dated STI/LTI + cash is the textbook liquidity composition — summed, stated in the note's
    as-ofs, AUTO. (``verify-marketable-securities`` fired on ~every real filer — a flag marking the
    rule, retired by the re-tier.)"""
    f = _facts(
        ocf=[_dur("2026-03-01", "2026-05-28", -5e6)],
        cash=[_inst("2026-05-28", 100e6)],
        sti=[_inst("2026-05-28", 50e6)],  # the SAME balance sheet as cash
    )
    cb = _cb(f)
    assert cb.cash_usd == 150e6
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert "marketable as of 2026-05-28" in cb.note  # the composition stated where ratified


def test_cash_clean_native_quarter_is_auto_with_asofs_in_the_note():
    cb = _cb(
        _facts(ocf=[_dur("2026-03-01", "2026-05-28", -5e6)], cash=[_inst("2026-05-28", 100e6)])
    )
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert "cash as of 2026-05-28" in cb.note and "burn over 2026-03-01" in cb.note
    assert cb.event_date == date(2026, 5, 28)


# ---------------------------------------------------------------------------------------------------------
# Tier 1/2 — cash: composition (cash-only OR + same-dated marketable) -> AUTO; flags mark exceptions
# ---------------------------------------------------------------------------------------------------------
def test_cash_auto_when_no_marketable_securities():
    leu = _extract("LEU")["cash_burn"]
    assert leu.tier is Tier.AUTO and leu.cash_usd == SEED["LEU"]["cash"]


def test_cash_marketable_composition_is_a_note_never_an_alarm():
    """THE RE-TIER on the seed fixtures. SMR still FLAGs — via the one-time detector (the real
    exception), never verify-marketable. OKLO goes AUTO; its companyfacts tag sum UNDERCOUNTS the true
    balance-sheet cash — the deliberate trade of retiring the flag: the error direction is CONSERVATIVE
    (understated cash -> runway reads SHORTER; a funding-risk gauge that over-warns, never under-warns),
    and the note states the exact composition where the operator ratifies."""
    smr = _extract("SMR")["cash_burn"]
    assert "verify-marketable-securities" not in smr.flags
    assert smr.flags == ["possible-one-time"]  # the surviving flag marks the exception
    oklo = _extract("OKLO")["cash_burn"]
    assert oklo.tier is Tier.AUTO and oklo.flags == []
    assert oklo.cash_usd != SEED["OKLO"]["cash"]  # the undercount — conservative by direction
    assert "marketable" in oklo.note  # the composition stated where the operator ratifies


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


def test_ytd_quarter_is_derived_and_auto():
    """NNE (a Q2 filer): the quarter derives from the 6-month YTD − Q1 and goes out AUTO with the
    derivation stated in the note — the ``ytd-derived`` flag is retired (composition, not an exception).
    """
    nne = _extract("NNE")["cash_burn"]
    assert nne.quarterly_burn_usd == SEED["NNE"]["qburn"]  # Q2 = the 6-month YTD - Q1 (derived)
    assert nne.tier is Tier.AUTO and nne.flags == []
    assert "derived (YTD − prior YTD)" in nne.note


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
