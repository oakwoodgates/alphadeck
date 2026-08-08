"""The PURE price-symbol decider (``securities/price_symbol.py``) — the four selection gates + the tiers.

Fixtures are shaped like Yahoo ``/v1/finance/search`` payloads (``{"quotes": [...]}``). No I/O, no DB.
The load-bearing traps: the Curaleaf foreign-outranks-US case (never take the top score), the mutualfund
filter, a genuinely-uncovered name (PMBHF → NONE, never dropped), and two-plausible → FLAG.
"""

from __future__ import annotations

from securities.price_symbol import (
    normalize_company_name,
    propose_price_symbol,
    us_equity_name_matches,
)


def _q(symbol, name, quote_type="EQUITY", exchange="PNK"):
    return {"symbol": symbol, "shortname": name, "quoteType": quote_type, "exchange": exchange}


# --- normalization ---------------------------------------------------------------------------------


def test_normalize_peels_legal_suffix_keeps_descriptive_words():
    assert normalize_company_name("Curaleaf Holdings, Inc.") == "CURALEAF HOLDINGS"
    assert normalize_company_name("Curaleaf Holdings Inc") == "CURALEAF HOLDINGS"
    assert normalize_company_name("The Vireo Growth Inc.") == "VIREO GROWTH"
    # descriptive words are KEPT, so two different companies never collapse to a false match
    assert normalize_company_name("ABC Holdings Inc") != normalize_company_name("ABC Group Inc")
    assert normalize_company_name("") == "" and normalize_company_name(None) == ""


# --- the US + name + equity filter -----------------------------------------------------------------


def test_filter_rejects_foreign_venue_and_nonequity_keeps_us_equity():
    search = {
        "quotes": [
            _q(
                "CURA.TO", "Curaleaf Holdings, Inc.", exchange="TOR"
            ),  # foreign venue — dotted suffix
            _q("CURLF", "Curaleaf Holdings, Inc."),  # the US listing
            _q("FDCTX", "First Digital Fund", quote_type="MUTUALFUND"),  # not an equity
        ]
    }
    assert us_equity_name_matches(search, "Curaleaf Holdings Inc") == ["CURLF"]


# --- AUTO ------------------------------------------------------------------------------------------


def test_fdct_to_fdctd_is_auto_with_longer_history():
    """The headline case: FDCT's ticker search finds FDCTD (US equity, exact name), and FDCTD carries
    251 bars vs FDCT's 16 — longer history confirms → AUTO."""
    search = {"quotes": [_q("FDCTD", "First Digital Corp")]}
    p = propose_price_symbol(
        ticker="FDCT",
        name="First Digital Corp",
        ticker_search=search,
        canonical_bars=16,
        candidate_bars={"FDCTD": 251},
    )
    assert (p.tier, p.proposed_symbol) == ("AUTO", "FDCTD")
    assert "251" in p.why and "16" in p.why  # the confirming basis rides the why


def test_curaleaf_foreign_outranks_us_but_us_is_still_picked():
    """THE TRAP: Yahoo ranks CURA.TO (Toronto) ABOVE CURLF for a "Curaleaf" search — never take the top
    score. The ticker (CURLD) searches empty, the NAME search returns both, the filter keeps only the US
    equity, and longer history makes it AUTO CURLF (not the higher-scored foreign line)."""
    p = propose_price_symbol(
        ticker="CURLD",
        name="Curaleaf Holdings, Inc.",
        ticker_search={"quotes": []},  # ticker search empty → name fallback
        name_search={
            "quotes": [
                _q("CURA.TO", "Curaleaf Holdings, Inc.", exchange="TOR"),  # scores higher, foreign
                _q("CURLF", "Curaleaf Holdings, Inc."),
            ]
        },
        canonical_bars=12,
        candidate_bars={"CURLF": 251},
    )
    assert (p.tier, p.proposed_symbol) == ("AUTO", "CURLF")


# --- FLAG / NONE -----------------------------------------------------------------------------------


def test_mutualfund_only_is_none():
    """A stem-sharing mutualfund (FDCTX) is the ONLY quote → filtered → NONE (kept under the canonical
    ticker, never dropped)."""
    p = propose_price_symbol(
        ticker="FDCT",
        name="First Digital Corp",
        ticker_search={"quotes": [_q("FDCTX", "First Digital Corp", quote_type="MUTUALFUND")]},
        canonical_bars=16,
        candidate_bars={},
    )
    assert p.tier == "NONE" and p.proposed_symbol is None


def test_uncovered_name_is_none_and_kept():
    """PMBHF searches to nothing (genuinely uncovered) → NONE — the name is KEPT (#9), the thin-history
    flag marks it starved separately."""
    p = propose_price_symbol(
        ticker="PMBHF",
        name="Photomab Biotech",
        ticker_search={"quotes": []},
        name_search={"quotes": []},
        canonical_bars=0,
        candidate_bars={},
    )
    assert p.tier == "NONE" and p.proposed_symbol is None


def test_two_plausible_us_equity_matches_is_flag():
    """Two US-equity exact-name matches → ambiguous → FLAG (the operator picks; nothing auto-written)."""
    p = propose_price_symbol(
        ticker="ACME",
        name="Acme Corp",
        ticker_search={"quotes": [_q("ACMEA", "Acme Corp"), _q("ACMEB", "Acme Corp")]},
        canonical_bars=10,
        candidate_bars={"ACMEA": 250, "ACMEB": 250},
    )
    assert p.tier == "FLAG"
    assert set(p.candidates) == {"ACMEA", "ACMEB"}


def test_single_match_without_longer_history_is_flag_unverified():
    """A lone US-equity match whose history is NOT materially longer → FLAG (unverified) — AUTO requires
    the confirming longer history, so a same-length alias never auto-adopts."""
    p = propose_price_symbol(
        ticker="FDCT",
        name="First Digital Corp",
        ticker_search={"quotes": [_q("FDCTD", "First Digital Corp")]},
        canonical_bars=16,
        candidate_bars={"FDCTD": 18},  # only +2, under the material-gain floor
    )
    assert (p.tier, p.proposed_symbol) == ("FLAG", "FDCTD")


def test_self_match_is_not_a_resolution():
    """A search that returns only the canonical ticker itself is no resolution → NONE (a self-match can't
    be the vendor's different symbol)."""
    p = propose_price_symbol(
        ticker="HIMS",
        name="Hims & Hers Health, Inc.",
        ticker_search={"quotes": [_q("HIMS", "Hims & Hers Health, Inc.", exchange="NYQ")]},
        canonical_bars=250,
        candidate_bars={"HIMS": 250},
    )
    assert p.tier == "NONE"
