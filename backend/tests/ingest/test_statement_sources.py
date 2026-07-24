"""The statement-source seam + the EX-99 exhibit source (Retrieval Slice A-2) — the tests are the oracle.

ANSWER KEY (pinned 2026-07-24, hand-verified from the EXHIBITS' OWN statement text — never
companyfacts-against-itself — then cross-checked against companyfacts per the later-as-of rule):

- CRDL 40-F ``0001104659-26-037844``: EIGHT EX-99 .htm exhibits (not the spec's three). The
  statements are **EX-99.2** (``crdl-20251231xex99d2.htm``): cash $21,416,684 / OCF −23,854,650
  (current column FIRST), full CAD dollars, years-ended annual. companyfacts AGREES same-date (this
  40-F's own XBRL ingested) → the span is the cf-matched 2025-01-01 → 2025-12-31 (364d) → quarterly
  burn 5,984,142.1 → ≈10.7 months. EX-99.1 is the AIF (statement HEADINGS, no rows), EX-99.3 the
  MD&A (quotes the exact numbers in prose, no rows) — both must fail the signature.
- DRUG 40-F ``0001437749-25-038729``: the filer agent names exhibits ``ex_9014xx.htm`` — NO "99" in
  any filename (the spec's filename assumption is dead; the SGML header TYPE is the real source).
  Statements = **EX-99.2** (``ex_901433.htm``): cash 82,908,589 / OCF −8,691,619 (current FIRST of
  THREE year columns), full CAD dollars, years-ended → burn 2,174,393.0 → ≈114.4 months (the
  post-raise war chest; the MD&A's FVTPL table corroborates: 82,822,339 cash + 86,250 GIC).
  companyfacts is a FULL YEAR behind (still FY2024) — the statement wins, the lag named.
- HELP 40-F ``0001833141-26-000046``: statements = **EX-99.2** (``cybn-20260331_d2.htm``, no "ex99"
  in the name): bare "Cash" row label (the HYFT shape) 157,258 / OCF −133,271, "in thousands of
  United States dollars" → ×1,000, USD → cash $157,258,000 / burn 33,340,570.4 → ≈14.2 months.
  companyfacts serves CAD ONLY (the filer changed presentation currency) → the mixed-currency rule
  keeps it out entirely. HELP's MD&A is the STRONGEST near-miss: its quarterly-summary table HITS
  the balance-sheet cash-row locator — only the missing cash-flow row rejects it (the both-rows
  signature is load-bearing, not belt-and-suspenders).

Fixtures are REAL trimmed exhibit text; every statements trim was verified to reproduce the
full-document extraction (values, flags, note) before commit, and every negative trim keeps its
mention/near-miss region live. OFFLINE — no network, no DB.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from domain.config import DEFAULT_EXTRACTOR_CONFIG
from domain.extraction import Tier
from ingest.edgar.annual_runway import extract_annual_runway
from ingest.edgar.statement_sources import (
    STATEMENT_SOURCES,
    AnnualFiling,
    ResolvedStatements,
    StatementsDeferred,
    exhibit_statements,
    has_statement_rows,
    main_doc_statements,
    resolve_statements,
)

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "annual"
_TODAY = date(2026, 7, 24)  # the A-2 measurement date

_CRDL_ACC = "0001104659-26-037844"
_CRDL_EX99 = [f"crdl-20251231xex99d{i}.htm" for i in range(1, 9)]


def _text(fname: str) -> str:
    return (_FX / fname).read_text(encoding="utf-8")


def _cf(name: str) -> dict:
    return json.loads((_FX / f"cf-runway-{name}.json").read_text(encoding="utf-8"))


def _months(f) -> float:
    assert f.cash_usd is not None and f.quarterly_burn_usd and f.quarterly_burn_usd > 0
    return f.cash_usd / (f.quarterly_burn_usd / 3.0)


def _extract(fname: str, *, cf: dict | None, report_date: date, ref: str = "https://sec.gov/ex"):
    facts, reason = extract_annual_runway(
        cf,
        _text(fname),
        annual_ref=ref,
        annual_form="40-F",
        report_date=report_date,
        today=_TODAY,
    )
    return (facts[0] if facts else None), reason


# ---------------------------------------------------------------------------------------------------------
# the three names emit from their statements exhibit (answer-key values)
# ---------------------------------------------------------------------------------------------------------


def test_crdl_runway_from_ex99_statements():
    """CRDL's EX-99.2 statement text through the UNCHANGED Slice A extractor: cash 21,416,684 /
    OCF −23,854,650, current column FIRST, ≈10.7 months at the cf-matched 364-day span. FLAG, both
    statement rows ride as passages, and every passage cites the EXHIBIT ref it was given — the
    wrapper is nowhere in the provenance."""
    ref = "https://www.sec.gov/Archives/edgar/data/1702123/000110465926037844/crdl-20251231xex99d2.htm"
    f, reason = _extract(
        "CRDL-40f-ex99-fin.txt", cf=_cf("CRDL"), report_date=date(2025, 12, 31), ref=ref
    )
    assert reason is None and f is not None
    assert f.tier is Tier.FLAG and f.source == "annual-statements"
    assert f.cash_usd == 21_416_684.0
    assert f.quarterly_burn_usd == pytest.approx(23_854_650 / 364 * (365.25 / 4))
    assert 10.4 < _months(f) < 11.1  # ≈ 10.7 months — a below-a-year runway, honestly
    assert f.statement_currency == "CAD"
    assert "-23,854,650" in f.note and "companyfacts agrees" in f.note
    assert sorted(p.kind for p in f.located_passages) == ["balance-sheet", "cash-flow"]
    assert all(p.source_ref == ref for p in f.located_passages)
    assert f.source_ref == ref
    # fixture integrity: the two-year columns + the current-first order stay pinned in the trim.
    # CRDL's exhibit is ZERO-WIDTH-LITTERED (the SHMD hazard, now in an exhibit) — the row strings
    # exist only in the stripped view the parser reads; the raw fixture keeps the hazard live.
    import re

    from ingest.edgar.annual_runway import _ZERO_WIDTH_RE

    raw = _text("CRDL-40f-ex99-fin.txt")
    assert _ZERO_WIDTH_RE.search(raw)  # the invisible hazard stays pinned
    text = re.sub(r"\s+", " ", _ZERO_WIDTH_RE.sub("", raw))
    assert "$ 21,416,684 $ 30,580,029" in text  # current FIRST, prior second
    assert "( 23,854,650 ) ( 25,060,867 )" in text


def test_drug_runway_from_exhibit():
    """DRUG's EX-99.2 (``ex_901433.htm`` — no "99" in the filename): cash 82,908,589 / OCF
    −8,691,619 over THREE year columns (current FIRST), full CAD dollars → ≈114 months. companyfacts
    is a full year behind (FY2024) — the statement wins and the lag is NAMED, never silent."""
    f, reason = _extract("DRUG-40f-ex99-fin.txt", cf=_cf("DRUG"), report_date=date(2025, 9, 30))
    assert reason is None and f is not None
    assert f.tier is Tier.FLAG
    assert f.cash_usd == 82_908_589.0  # never 5,720,092 (the prior column / lagged companyfacts)
    assert f.quarterly_burn_usd == pytest.approx(8_691_619 / 365 * (365.25 / 4))
    assert 112.0 < _months(f) < 117.0  # ≈ 114.4 months (~9.5y)
    assert f.statement_currency == "CAD"
    assert "-1,850,186" in f.note and "behind the filing" in f.note  # the lag, stated
    text = _text("DRUG-40f-ex99-fin.txt")
    assert "( 8,691,619 ) ( 1,850,186 ) ( 7,024,265 )" in text  # three columns, current first


def test_help_runway_from_exhibit():
    """HELP's EX-99.2 (``cybn-20260331_d2.htm``): the bare "Cash" row label (the HYFT shape) reads
    157,258 THOUSAND USD → $157,258,000; OCF −133,271K → ≈14.2 months. companyfacts serves CAD only
    (the filer changed presentation currency to USD) — the mixed-currency rule keeps it OUT: no
    cross-check, no lag note, the statement stands alone."""
    f, reason = _extract("HELP-40f-ex99-fin.txt", cf=_cf("HELP"), report_date=date(2026, 3, 31))
    assert reason is None and f is not None
    assert f.cash_usd == 157_258_000.0  # thousands applied; current column, not 93,922
    assert f.quarterly_burn_usd == pytest.approx(133_271_000 / 365 * (365.25 / 4))
    assert 13.7 < _months(f) < 14.7  # ≈ 14.2 months
    assert f.statement_currency == "USD"
    assert "companyfacts" not in f.note  # CAD-only cf is unrepresentable against USD statements
    text = _text("HELP-40f-ex99-fin.txt")
    assert "Cash 157,258 93,922" in text  # the bare-label row stays pinned
    assert "thousands of United States dollars" in text


# ---------------------------------------------------------------------------------------------------------
# the content signature — structure, never a mention (the negative fixtures guard something real)
# ---------------------------------------------------------------------------------------------------------


def test_aif_and_mda_exhibits_are_not_selected():
    """The discriminator: the AIF carries the statement HEADINGS and the MD&As carry the very
    NUMBERS — none carries both statement ROWS, so none may pass. HELP's MD&A is the live near-miss:
    its quarterly-summary table HITS the cash-row locator (pinned here so the fixture can't rot into
    a strawman) and only the missing operating-cash-flow row rejects it."""
    from ingest.edgar.annual_runway import (
        _BS_HEADING_RE,
        _CF_HEADING_RE,
        _locate_cash_row,
        _locate_ocf_row,
    )

    for fname in ("CRDL-40f-ex99-aif.txt", "CRDL-40f-ex99-mda.txt", "HELP-40f-ex99-mda.txt"):
        assert not has_statement_rows(_text(fname)), fname
    for fname in ("CRDL-40f-ex99-fin.txt", "DRUG-40f-ex99-fin.txt", "HELP-40f-ex99-fin.txt"):
        assert has_statement_rows(_text(fname)), fname
    # the traps are REAL in the fixtures, not trimmed away:
    aif = _text("CRDL-40f-ex99-aif.txt")  # headings present, rows absent
    assert _BS_HEADING_RE.search(aif) and _CF_HEADING_RE.search(aif)
    mda = _text("CRDL-40f-ex99-mda.txt")  # quotes the exact OCF number in prose
    assert "23,854,650" in mda
    hmda = _text("HELP-40f-ex99-mda.txt")  # the cash-row locator genuinely fires...
    assert _locate_cash_row(hmda) is not None
    assert _locate_ocf_row(hmda) is None  # ...and only the missing OCF row rejects the doc


# ---------------------------------------------------------------------------------------------------------
# the seam fakes — a cache-shaped client that records every key and raises on anything unexpected
# ---------------------------------------------------------------------------------------------------------


class _Cf404(Exception):
    def __init__(self):
        super().__init__("404 companyfacts")
        self.response = type("R", (), {"status_code": 404})()


class _SeamClient:
    """Serves submissions/companyfacts JSON, the filing index JSON, and per-document texts keyed by
    basename. RECORDS every cache key (the fetch ledger the tests assert on) and raises on any
    document not explicitly provided — an unexpected fetch fails the test by construction."""

    def __init__(self, *, subs=None, cf=None, index=None, texts=None):
        self._subs, self._cf, self._index = subs, cf, index
        self._texts = texts or {}
        self.text_keys: list[str] = []
        self.json_keys: list[str] = []

    def get_json(self, url: str, cache_key: str) -> dict:
        self.json_keys.append(cache_key)
        if cache_key.startswith("submissions/"):
            return self._subs
        if cache_key.startswith("companyfacts/"):
            if self._cf is None:
                raise _Cf404()
            return self._cf
        if cache_key.startswith("filing-index/") and self._index is not None:
            return self._index
        raise AssertionError(f"unexpected get_json: {cache_key}")

    def get_text(self, url: str, cache_key: str) -> str:
        self.text_keys.append(cache_key)
        doc = cache_key.rsplit("/", 1)[-1]
        if doc not in self._texts:
            raise AssertionError(f"unexpected document fetch: {cache_key}")
        return self._texts[doc]


def _crdl_filing(primary_text: str) -> AnnualFiling:
    return AnnualFiling(
        cik=1702123,
        accession=_CRDL_ACC,
        primary_url="https://www.sec.gov/Archives/edgar/data/1702123/000110465926037844/crdl-20251231x40f.htm",
        primary_text=primary_text,
        report_date=date(2025, 12, 31),
        form="40-F",
    )


def _crdl_exhibit_texts(*, statements_under: str = "crdl-20251231xex99d2.htm") -> dict[str, str]:
    """The CRDL exhibit corpus the fake serves: AIF under d1, the statements under
    ``statements_under``, MD&A under d3, the real consent stub under the capped tail (d4, d5, d8).
    d6/d7 are DELIBERATELY absent — the size-ordered cap must never request them."""
    consent = _text("CRDL-40f-ex99-consent.txt")
    return {
        "0001104659-26-037844-index-headers.html": _text("index-headers-CRDL.html"),
        "crdl-20251231xex99d1.htm": _text("CRDL-40f-ex99-aif.txt"),
        statements_under: _text("CRDL-40f-ex99-fin.txt"),
        "crdl-20251231xex99d3.htm": _text("CRDL-40f-ex99-mda.txt"),
        "crdl-20251231xex99d4.htm": consent,
        "crdl-20251231xex99d5.htm": consent,
        "crdl-20251231xex99d8.htm": consent,
    }


def _crdl_index() -> dict:
    return json.loads(_text("filing-index-CRDL.json"))


# ---------------------------------------------------------------------------------------------------------
# exhibit_statements — selection, bounding, fail-closed
# ---------------------------------------------------------------------------------------------------------


def test_exhibit_source_selects_the_statements_exhibit_alone(caplog):
    """The full CRDL chain (real trimmed header + index + exhibits): eight EX-99 candidates, the cap
    takes the six largest, ONE matches the signature — EX-99.2. The AIF and MD&A are fetched and
    REJECTED; the two smallest certifications are capped out and LOGGED; the R*.htm/XBRL/GRAPHIC
    army is never touched (the fake raises on any of them)."""
    client = _SeamClient(index=_crdl_index(), texts=_crdl_exhibit_texts())
    with caplog.at_level(logging.WARNING):
        r = exhibit_statements(
            client, _crdl_filing(_text("CRDL-40f.txt")), DEFAULT_EXTRACTOR_CONFIG
        )
    assert isinstance(r, ResolvedStatements)
    assert r.source_doc == "crdl-20251231xex99d2.htm"
    assert r.source_ref.endswith("/000110465926037844/crdl-20251231xex99d2.htm")
    assert has_statement_rows(r.text)
    # bounded: exactly 6 exhibit fetches (cap), size-ordered; d6/d7 never requested
    exhibit_fetches = [k for k in client.text_keys if "ex99" in k]
    assert len(exhibit_fetches) == 6
    assert not any("ex99d6" in k or "ex99d7" in k for k in client.text_keys)
    # the cap's skips are LOGGED, never silent
    assert any("skipped" in rec.message and "ex99d6" in rec.message for rec in caplog.records)
    # cache-key classes: the accession's documents are immutable forms/*; the index is MUTABLE
    assert all(k.startswith(f"forms/{_CRDL_ACC}/") for k in client.text_keys)
    assert f"filing-index/{_CRDL_ACC}.json" in client.json_keys


def test_exhibit_ambiguous_defers_not_guesses():
    """FAIL CLOSED: the statements text served under TWO exhibit names → two signature matches → a
    chain-STOPPING ``exhibit-ambiguous`` deferral, never a pick. A wrong statement source is the
    runway analog of a confident-wrong cover match."""
    texts = _crdl_exhibit_texts()
    texts["crdl-20251231xex99d1.htm"] = _text("CRDL-40f-ex99-fin.txt")  # a second "statements" doc
    client = _SeamClient(index=_crdl_index(), texts=texts)
    r = exhibit_statements(client, _crdl_filing(_text("CRDL-40f.txt")), DEFAULT_EXTRACTOR_CONFIG)
    assert isinstance(r, StatementsDeferred)
    assert r.reason == "exhibit-ambiguous"
    # and the resolver SURFACES the deferral rather than treating it as "not here"
    client2 = _SeamClient(index=_crdl_index(), texts=texts)
    out = resolve_statements(client2, _crdl_filing(_text("CRDL-40f.txt")))
    assert isinstance(out, StatementsDeferred) and out.reason == "exhibit-ambiguous"


def test_no_statement_exhibit_returns_none():
    """Zero signature matches (the statements doc replaced by the MD&A) → ``None`` — the caller
    keeps its honest ``financials-in-exhibit``; nothing is guessed from the near-misses."""
    texts = _crdl_exhibit_texts()
    texts["crdl-20251231xex99d2.htm"] = _text("CRDL-40f-ex99-mda.txt")  # no statements anywhere
    client = _SeamClient(index=_crdl_index(), texts=texts)
    r = exhibit_statements(client, _crdl_filing(_text("CRDL-40f.txt")), DEFAULT_EXTRACTOR_CONFIG)
    assert r is None


def test_headers_filename_fallback_still_finds_the_exhibits(caplog):
    """The SGML header is the TYPE source of record; if it yields nothing (an unparseable header),
    the filename ``ex99`` pattern over the index keeps recall (#9) — CRDL's filenames carry it."""
    texts = _crdl_exhibit_texts()
    texts["0001104659-26-037844-index-headers.html"] = "<html>not an sgml header</html>"
    client = _SeamClient(index=_crdl_index(), texts=texts)
    with caplog.at_level(logging.WARNING):
        r = exhibit_statements(
            client, _crdl_filing(_text("CRDL-40f.txt")), DEFAULT_EXTRACTOR_CONFIG
        )
    assert isinstance(r, ResolvedStatements)
    assert r.source_doc == "crdl-20251231xex99d2.htm"
    assert any("filename fallback" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------------------------------------
# the resolver order + the fetch-once discipline
# ---------------------------------------------------------------------------------------------------------


def test_resolver_order_main_doc_wins_when_present():
    """An in-doc name (GHRS's real statement text as the primary) resolves via the MAIN-DOC source —
    ``client=None`` proves structurally that no fetch of any kind can happen on that path, and the
    exhibit source is never consulted (the loop stops at the first hit)."""
    filing = AnnualFiling(
        cik=1234,
        accession="0000000000-26-000001",
        primary_url="https://www.sec.gov/Archives/edgar/data/1234/000000000026000001/ghrs-20f.htm",
        primary_text=_text("GHRS-20f-fin.txt"),
        report_date=date(2025, 12, 31),
        form="20-F",
    )
    r = resolve_statements(None, filing)  # None client: any fetch would raise AttributeError
    assert isinstance(r, ResolvedStatements)
    assert r.source_ref == filing.primary_url
    assert r.source_doc == "ghrs-20f.htm"
    assert r.text is filing.primary_text  # the SAME already-fetched text — no copy, no re-fetch
    assert STATEMENT_SOURCES[0] is main_doc_statements  # the priority order is part of the contract


def test_main_doc_source_reuses_primary_no_refetch():
    """The wrapper level: an in-doc 20-F name through ``annual_facts_for_security`` fetches the
    primary document EXACTLY ONCE (shared by cover + statements) and never touches the filing index
    or any exhibit — the Slice A names' fetch profile is unchanged by the seam."""
    from ingest.edgar.annual_runway import annual_facts_for_security

    subs = {
        "cik": "1234",
        "filings": {
            "recent": {
                "form": ["20-F"],
                "accessionNumber": ["0000000000-26-000001"],
                "primaryDocument": ["ghrs-20f.htm"],
                "filingDate": ["2026-03-15"],
                "reportDate": ["2025-12-31"],
            }
        },
    }
    client = _SeamClient(
        subs=subs, cf=_cf("GHRS"), texts={"ghrs-20f.htm": _text("GHRS-20f-fin.txt")}
    )
    res = annual_facts_for_security(client, 1234, today=_TODAY)
    burn = [f for f in res.facts if f.fact_type == "cash_burn"]
    assert len(burn) == 1 and burn[0].cash_usd == 246_251_000.0  # the Slice A oracle value
    assert burn[0].source_ref.endswith("/ghrs-20f.htm")  # the passage cites the PRIMARY doc
    assert res.runway_empty_reason is None
    primary_fetches = [k for k in client.text_keys if k.endswith("ghrs-20f.htm")]
    assert len(primary_fetches) == 1  # fetched ONCE — cover and statements share it
    assert not any(k.startswith("filing-index/") for k in client.json_keys)  # no exhibit machinery


# ---------------------------------------------------------------------------------------------------------
# the wrapper end-to-end — a 40-F dark name emits runway FROM ITS EXHIBIT
# ---------------------------------------------------------------------------------------------------------


def _crdl_wrapper_client(**overrides) -> _SeamClient:
    subs = json.loads((_FX / "subs-CRDL.json").read_text(encoding="utf-8"))
    texts = {"crdl-20251231x40f.htm": _text("CRDL-40f.txt"), **_crdl_exhibit_texts()}
    texts.update(overrides.pop("texts", {}))
    return _SeamClient(subs=subs, cf=_cf("CRDL"), index=_crdl_index(), texts=texts, **overrides)


def test_wrapper_40f_name_emits_runway_from_its_exhibit():
    """THE A-2 loop: CRDL through ``annual_facts_for_security`` — the wrapper primary yields the
    shares candidate (Slice 1, unchanged), the seam hunts the EX-99 exhibits, and the runway fact
    emerges from EX-99.2 with the answer-key values, every passage citing the EXHIBIT URL (never the
    wrapper). The primary is fetched once; the honest deferral is GONE."""
    from ingest.edgar.annual_runway import annual_facts_for_security

    client = _crdl_wrapper_client()
    res = annual_facts_for_security(client, 1702123, today=_TODAY)
    assert [f.fact_type for f in res.facts] == ["shares_outstanding", "cash_burn"]
    assert res.empty_reason is None and res.runway_empty_reason is None
    shares, burn = res.facts
    assert shares.tier is Tier.FLAG and shares.source_ref.endswith("/crdl-20251231x40f.htm")
    assert burn.tier is Tier.FLAG
    assert burn.cash_usd == 21_416_684.0
    assert burn.quarterly_burn_usd == pytest.approx(23_854_650 / 364 * (365.25 / 4))
    assert burn.source_ref.endswith("/crdl-20251231xex99d2.htm")  # the WINNING doc, cited
    assert all(p.source_ref.endswith("/crdl-20251231xex99d2.htm") for p in burn.located_passages)
    assert len([k for k in client.text_keys if k.endswith("crdl-20251231x40f.htm")]) == 1


def test_wrapper_no_statements_anywhere_defers_honestly():
    """Main doc a wrapper AND no exhibit carries the signature → the runway leg keeps the honest
    ``financials-in-exhibit`` (companyfacts says CRDL burns), with the shares leg still emitting —
    the A-2 chain never invents a fact it has no passage for."""
    from ingest.edgar.annual_runway import annual_facts_for_security

    client = _crdl_wrapper_client(
        texts={"crdl-20251231xex99d2.htm": _text("CRDL-40f-ex99-mda.txt")}
    )
    res = annual_facts_for_security(client, 1702123, today=_TODAY)
    assert [f.fact_type for f in res.facts] == ["shares_outstanding"]
    assert res.runway_empty_reason == "financials-in-exhibit"


def test_wrapper_ambiguous_exhibits_defer_with_their_own_reason():
    """Two statement-signature exhibits at the wrapper level → ``exhibit-ambiguous`` rides
    ``runway_empty_reason`` (a DISTINCT reason from the not-found deferral), and no cash_burn fact
    exists — deferred, never guessed."""
    from ingest.edgar.annual_runway import annual_facts_for_security

    client = _crdl_wrapper_client(
        texts={"crdl-20251231xex99d1.htm": _text("CRDL-40f-ex99-fin.txt")}
    )
    res = annual_facts_for_security(client, 1702123, today=_TODAY)
    assert [f.fact_type for f in res.facts] == ["shares_outstanding"]
    assert res.runway_empty_reason == "exhibit-ambiguous"


# ---------------------------------------------------------------------------------------------------------
# structural bounds
# ---------------------------------------------------------------------------------------------------------


def test_seam_module_carries_no_tier_machinery():
    """The seam resolves WHERE text comes from and nothing else: the pre-fill tier's token appears
    nowhere in its source (the annual-path idiom), and it never imports the extraction tiers."""
    import ingest.edgar.statement_sources as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "AUTO" not in src
    assert "Tier" not in src
