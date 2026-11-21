"""Business-type taxonomy (Business-Type M1, S1) — the pure two-level resolution.

The load-bearing test is EXHAUSTIVENESS: every live SIC description (the pinned corpus in
fixtures/live_sic_strings.json, dumped verbatim from the real master) must map to a real leaf —
a map edit that strands a live string fails HERE, not silently in the cockpit. The rest pins the
precedence ladder (DB re-tag > ticker override > sic override > map > other > unclassified), the
royalty overlay's measured cases, the SEC-spacing normalization, and the loaders' fail-loud
validation. Pure — no DB, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.enums import BusinessSupersector, BusinessType
from securities.business_type import (
    ROYALTY_SICS,
    RUNWAY_NA_SICS,
    SUB_TYPE_BY_SIC,
    SUPER_BY_SUB,
    load_overrides,
    load_royalty_patterns,
    load_runway_not_meaningful,
    load_sic_map,
    load_supersectors,
    norm_sic,
    resolve_business_type,
    runway_applies,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _live_sics() -> list[str]:
    return json.loads((FIXTURES / "live_sic_strings.json").read_text(encoding="utf-8"))


# --- exhaustiveness: the whole live corpus maps (recall-sacred, #9) -------------------------------


def test_every_live_sic_string_maps_to_a_real_leaf():
    """All 300 live SIC descriptions resolve to a leaf, and none falls to OTHER — the corpus is
    fully assigned today, so OTHER stays purely the forward-drift bucket (a NEW SEC string). If a
    map edit orphans a live string, this is the loud failure that catches it."""
    for raw in _live_sics():
        read = resolve_business_type(sector=raw)
        assert read.business_type is not None, f"unmapped live SIC: {raw!r}"
        assert read.business_type is not BusinessType.OTHER, f"live SIC fell to OTHER: {raw!r}"
        assert read.supersector is SUPER_BY_SUB[read.business_type]


def test_live_corpus_normalizes_without_collisions():
    """Two distinct live SIC strings must never normalize to the same key (a collision would let
    one map row silently claim another's companies)."""
    sics = _live_sics()
    assert len({norm_sic(s) for s in sics}) == len(sics)


# --- the resolution ladder ------------------------------------------------------------------------


def test_unknown_sector_is_visible_other_never_dropped():
    read = resolve_business_type(sector="Quantum Basket Weaving, NEC")
    assert read.business_type is BusinessType.OTHER
    assert read.supersector is BusinessSupersector.OTHER


def test_no_sector_abstains_unclassified():
    """Un-enriched (sector NULL) -> the honest None/None abstain — the origin chip's idiom."""
    for sector in (None, "", "   "):
        read = resolve_business_type(sector=sector)
        assert read == (None, None, False)


def test_normalization_bridges_the_secs_own_spacing_and_case():
    """EDGAR strings carry doubled spaces and case quirks; matching collapses whitespace and
    casefolds BOTH sides, so the SEC's raw value and a human-reflowed CSV row both hit."""
    doubled = resolve_business_type(sector="Deep Sea Foreign Transportation of  Freight")
    assert doubled.business_type is BusinessType.TRANSPORTATION
    shouted = resolve_business_type(sector="RETAIL-EATING  PLACES")
    assert shouted.business_type is BusinessType.CONSUMER_RETAIL


def test_semicap_maps_to_semiconductors_with_the_erii_ticker_exception():
    """Operator ruling: 'Special Industry Machinery, NEC' is the semicap bucket (ASML/AMAT class)
    -> semiconductors; ERII (Energy Recovery) is the documented overrides.csv exception."""
    sic = "Special Industry Machinery, NEC"
    assert (
        resolve_business_type(sector=sic, ticker="AMAT").business_type
        is BusinessType.SEMICONDUCTORS
    )
    erii = resolve_business_type(sector=sic, ticker="ERII", name="Energy Recovery, Inc.")
    assert erii.business_type is BusinessType.INDUSTRIALS_MACHINERY
    assert erii.supersector is BusinessSupersector.INDUSTRIALS
    # the ticker override holds even with no sector on file (an explicit preference is absolute)
    assert (
        resolve_business_type(sector=None, ticker="erii").business_type
        is BusinessType.INDUSTRIALS_MACHINERY
    )


def test_db_override_wins_over_every_file_layer():
    """The operator's stored per-security re-tag (0033) is the most explicit act — it beats the
    ticker override and the map."""
    read = resolve_business_type(
        sector="Special Industry Machinery, NEC", ticker="ERII", override="miner"
    )
    assert read.business_type is BusinessType.MINER
    assert read.supersector is BusinessSupersector.MATERIALS


def test_invalid_stored_override_falls_through_to_derived():
    """A stored override the enum no longer knows (manual DB edit / removed leaf) must not 500 the
    scored read — it falls through to the derived classification."""
    read = resolve_business_type(sector="Metal Mining", override="high_beta")
    assert read.business_type is BusinessType.MINER


# --- the royalty/streaming overlay (measured live: 32 hits over 8,106 names, zero FP) -------------


@pytest.mark.parametrize(
    ("name", "sector", "leaf"),
    [
        (
            "Uranium Royalty Corp.",
            "Commodity Contracts Brokers & Dealers",
            BusinessType.FINANCE_BROKERS,
        ),
        ("Royalty Pharma plc", "Pharmaceutical Preparations", BusinessType.BIOTECH_PHARMA),
        ("XOMA Royalty Corp", "Pharmaceutical Preparations", BusinessType.BIOTECH_PHARMA),
        (
            "Gulf Coast Ultra Deep Royalty Trust",
            "Crude Petroleum & Natural Gas",
            BusinessType.OIL_GAS,
        ),
        ("Elemental Royalty Corp", "Gold and Silver Ores", BusinessType.MINER),
        ("Metalla Royalty & Streaming Ltd.", "Gold and Silver Ores", BusinessType.MINER),
    ],
)
def test_royalty_overlay_coexists_with_the_industry_leaf(name, sector, leaf):
    """The live royalty houses: the NAME tell lights the overlay while the SIC leaf stands — a
    royalty co keeps its industry classification (the overlay is an AND, not a leaf)."""
    read = resolve_business_type(sector=sector, name=name)
    assert read.royalty is True
    assert read.business_type is leaf


def test_royalty_by_sic_lights_without_a_name_tell():
    """'Mineral Royalty Traders' / 'Oil Royalty Traders' are royalty-by-definition (flagged in
    sic_map.csv) — the overlay lights even for a name that says nothing."""
    read = resolve_business_type(sector="Mineral Royalty Traders", name="Sabina Resources Corp")
    assert read.royalty is True
    assert read.business_type is BusinessType.MINER
    assert norm_sic("Oil Royalty Traders") in ROYALTY_SICS


@pytest.mark.parametrize(
    "name", ["CuriosityStream Inc.", "Newmont Corp", "Streamline Health Solutions, Inc.", None]
)
def test_royalty_overlay_negatives(name):
    """\\broyalt / \\bstreaming\\b must NOT fire on stream-ish non-royalty names or a null name."""
    assert resolve_business_type(sector="Gold and Silver Ores", name=name).royalty is False


# --- the loaders fail LOUD on data-file drift -----------------------------------------------------


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_sic_map_rejects_unknown_leaf(tmp_path):
    p = _write(
        tmp_path,
        "sic_map.csv",
        'sic_description,business_type,royalty\n"Metal Mining",gold_digger,\n',
    )
    with pytest.raises(ValueError, match="unknown business_type 'gold_digger'"):
        load_sic_map(p)


def test_sic_map_rejects_duplicate_and_bad_flag(tmp_path):
    dup = _write(
        tmp_path,
        "dup.csv",
        'sic_description,business_type,royalty\n"Metal Mining",miner,\n"METAL  MINING",miner,\n',
    )
    with pytest.raises(ValueError, match="duplicate sic_description"):
        load_sic_map(dup)
    flag = _write(
        tmp_path,
        "flag.csv",
        'sic_description,business_type,royalty\n"Metal Mining",miner,x\n',
    )
    with pytest.raises(ValueError, match="royalty flag"):
        load_sic_map(flag)


def test_sic_map_rejects_a_drifted_header(tmp_path):
    p = _write(tmp_path, "sic_map.csv", 'description,leaf\n"Metal Mining",miner\n')
    with pytest.raises(ValueError, match="expected header"):
        load_sic_map(p)


def test_supersectors_must_be_total_over_the_enum(tmp_path):
    p = _write(tmp_path, "supersectors.csv", "business_type,supersector\nminer,materials\n")
    with pytest.raises(ValueError, match="map must be total"):
        load_supersectors(p)


def test_royalty_patterns_reject_a_bad_regex(tmp_path):
    p = _write(tmp_path, "royalty_patterns.txt", "# c\n\\broyalt\n(*bad\n")
    with pytest.raises(ValueError, match="bad regex"):
        load_royalty_patterns(p)


def test_overrides_reject_an_unknown_kind(tmp_path):
    p = _write(
        tmp_path,
        "overrides.csv",
        'kind,key,business_type,note\ncusip,037833100,miner,"nope"\n',
    )
    with pytest.raises(ValueError, match="kind must be 'ticker' or 'sic'"):
        load_overrides(p)


# --- the loaded module state is coherent ----------------------------------------------------------


def test_supersector_map_is_total_and_the_map_is_loaded():
    assert set(SUPER_BY_SUB) == set(BusinessType)
    assert len(SUB_TYPE_BY_SIC) >= 300  # the full live corpus (grows as new strings get mapped)


# --- "runway does not apply" (V2 C1): runway_not_meaningful.csv + runway_applies ------------------


def test_every_runway_sic_entry_is_a_live_sic_string():
    """Every SIC row of the file names a string the live master carries (a typo or a stale string would match
    nothing, quietly): used, or this test reports it."""
    live = {norm_sic(s) for s in _live_sics()}
    unused = sorted(s for s in RUNWAY_NA_SICS if s not in live)
    assert not unused, f"runway_not_meaningful.csv rows that match no live SIC string: {unused}"
    assert (
        len(RUNWAY_NA_SICS) == 21
    )  # the plan's Q6 list (banks, brokers, exchanges, insurers, lenders, trusts)


def test_the_operator_decisions_stay_out_of_the_runway_list():
    """Section 5 N3: miners and crypto-treasury companies (Finance Services) and insurance agents keep runway."""
    for kept in ("Finance Services", "Insurance Agents, Brokers & Service"):
        assert norm_sic(kept) not in RUNWAY_NA_SICS


@pytest.mark.parametrize(
    ("sector", "ticker", "kind", "applies"),
    [
        ("National Commercial Banks", "BANK", "equity", False),  # a bank
        ("Security Brokers, Dealers & Flotation Companies", "SNEX", "equity", False),  # a broker
        ("Security & Commodity Brokers, Dealers, Exchanges & Services", "CME", "equity", False),
        ("Commodity Contracts Brokers & Dealers", "FBTC", "equity", False),  # a bitcoin ETF trust
        ("Investment  advice", "BLK", "equity", False),  # the SEC's spacing and case, normalized
        ("Fire, Marine & Casualty Insurance", "AGO", "equity", False),  # an insurer
        ("Mortgage Bankers & Loan Correspondents", "RKT", "equity", False),  # a lender
        ("Investors, NEC", "X", "equity", False),
        (None, "LIT", "etf", False),  # a fund: no SIC, keyed on its kind
        ("Finance Services", "MARA", "equity", True),  # a crypto miner (SIC 6199)
        ("Finance Services", "MSTR", "equity", True),  # a crypto-treasury operating company
        ("Insurance Agents, Brokers & Service", "EHTH", "equity", True),  # an insurance agent
        ("Pharmaceutical Preparations", "BIO", "equity", True),  # a biotech
        (
            None,
            "NOSIC",
            "equity",
            True,
        ),  # no SIC on file: runway APPLIES (nothing disappears by accident)
        (None, None, None, True),  # nothing known at all
        ("Some String EDGAR Grows Tomorrow", "NEW", "equity", True),  # unmapped: applies
    ],
)
def test_runway_applies_truth_table(sector, ticker, kind, applies):
    assert runway_applies(sector=sector, ticker=ticker, instrument_kind=kind) is applies


def test_a_ticker_row_turns_runway_off_for_one_company(tmp_path):
    p = _write(
        tmp_path,
        "runway_not_meaningful.csv",
        '# a comment\nkind,key,note\nticker,abcd,"one company"\nsic,Loan  Brokers,"lenders"\n',
    )
    tickers, sics = load_runway_not_meaningful(p)
    assert tickers == frozenset({"ABCD"}) and sics == frozenset({"loan brokers"})


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ('kind,key,note\ncusip,037833100,"nope"\n', "kind must be 'ticker' or 'sic'"),
        ("kind,key,note\nsic,Loan Brokers,a\nsic,loan  brokers,b\n", "empty or duplicate"),
        ("kind,key,note\nticker,ABCD,a\nticker,abcd,b\n", "empty or duplicate"),
        ("kind,key,note\nsic,,a\n", "empty or duplicate"),
        ("kind,key\nsic,Loan Brokers\n", "expected header"),
        ("kind,key,note\nsic,Loan Brokers\n", "expected 3 columns"),
    ],
)
def test_the_runway_list_fails_loudly_on_a_bad_row(tmp_path, body, match):
    p = _write(tmp_path, "runway_not_meaningful.csv", body)
    with pytest.raises(ValueError, match=match):
        load_runway_not_meaningful(p)
