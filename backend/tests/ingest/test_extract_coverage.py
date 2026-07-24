"""extract_for_security COVERAGE — the both-forms relaxation + the honest empty signal.

Unit-level (no network, no fixtures): monkeypatch the fetch boundary (``_latest_filing``) + capture what
``extract_facts`` is handed, so the fallback wiring + the "no domestic filing -> []" rule are proven directly,
without the golden fixtures (which exercise ``extract_facts`` with both forms already present)."""

from __future__ import annotations

import types
from datetime import date

from ingest.edgar import extract as ex


def _client():
    # extract_for_security calls client.get_json(url, cache_path) for companyfacts; the fake extract_facts
    # below ignores the returned dict, so a minimal stub is enough (no network).
    return types.SimpleNamespace(get_json=lambda url, cache: {"facts": {}})


def _capture_extract_facts(monkeypatch):
    calls: dict = {}

    def fake(cf, tenq_text, tenk_text, *, tenq_ref, tenk_ref, tenq_date, tenk_date, cfg):
        calls.update(tenq_text=tenq_text, tenk_text=tenk_text, tenq_ref=tenq_ref, tenk_ref=tenk_ref)
        return ["shares", "burn", "purity"]  # extract_facts always yields the three candidates

    monkeypatch.setattr(ex, "extract_facts", fake)
    return calls


def test_both_forms_present_is_unchanged(monkeypatch):
    def latest(client, cik, form):
        return (f"http://{form}", f"{form}-TEXT", date(2026, 3, 31))

    monkeypatch.setattr(ex, "_latest_filing", latest)
    calls = _capture_extract_facts(monkeypatch)
    out = ex.extract_for_security(_client(), 111)
    assert out == ["shares", "burn", "purity"]
    # q=10-Q for the shares/burn role, k=10-K for the purity role — the existing wiring
    assert calls["tenq_text"] == "10-Q-TEXT" and calls["tenk_text"] == "10-K-TEXT"


def test_only_10k_falls_back_to_it_for_all_roles(monkeypatch):
    # a domestic filer with a 10-K but NO recent 10-Q must still yield candidates (the relaxation)
    def latest(client, cik, form):
        return ("http://k", "K-TEXT", date(2025, 12, 31)) if form == "10-K" else None

    monkeypatch.setattr(ex, "_latest_filing", latest)
    calls = _capture_extract_facts(monkeypatch)
    out = ex.extract_for_security(_client(), 222)
    assert out == ["shares", "burn", "purity"]  # NOT [] — the 10-K alone is enough now
    assert calls["tenq_text"] == "K-TEXT"  # shares/burn role fell back to the 10-K
    assert calls["tenk_text"] == "K-TEXT"


def test_only_10q_falls_back_to_it_for_all_roles(monkeypatch):
    def latest(client, cik, form):
        return ("http://q", "Q-TEXT", date(2026, 3, 31)) if form == "10-Q" else None

    monkeypatch.setattr(ex, "_latest_filing", latest)
    calls = _capture_extract_facts(monkeypatch)
    out = ex.extract_for_security(_client(), 333)
    assert out == ["shares", "burn", "purity"]
    assert (
        calls["tenq_text"] == "Q-TEXT" and calls["tenk_text"] == "Q-TEXT"
    )  # purity fell back to the 10-Q


def test_no_periodic_filing_returns_empty(monkeypatch):
    # a foreign private issuer (20-F/6-K only) has neither form -> honest [] (the "not covered" signal)
    monkeypatch.setattr(ex, "_latest_filing", lambda client, cik, form: None)
    # extract_facts must NOT be called when there's nothing to parse
    monkeypatch.setattr(
        ex, "extract_facts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run"))
    )
    assert ex.extract_for_security(_client(), 444) == []


# --- extract_with_annual_fallback (Retrieval Slice 1) — the route's entry, wiring only ---


def _subs_with(forms: list[str]) -> dict:
    n = len(forms)
    return {
        "filings": {
            "recent": {
                "form": forms,
                "accessionNumber": [f"a-{i}" for i in range(n)],
                "primaryDocument": [f"d{i}.htm" for i in range(n)],
                "filingDate": ["2026-01-01"] * n,
                "reportDate": ["2025-12-31"] * n,
            }
        }
    }


def test_fallback_domestic_filer_rides_the_periodic_path_unchanged(monkeypatch):
    """A 10-Q/10-K filer takes the EXISTING path verbatim (facts passthrough, no empty_reason) — the
    annual path must not even be consulted."""
    import ingest.edgar.annual_runway as annual
    from domain.extraction import ExtractedFact, Tier

    fake = [
        ExtractedFact(
            fact_type=ft,
            tier=Tier.HUMAN,
            source="10-q",
            source_ref="r",
            event_date=date(2026, 3, 31),
        )
        for ft in ("revenue_mix", "shares_outstanding", "cash_burn")
    ]
    monkeypatch.setattr(ex, "fetch_submissions", lambda client, cik: _subs_with(["10-Q", "8-K"]))
    monkeypatch.setattr(ex, "extract_for_security", lambda client, cik, cfg: fake)
    monkeypatch.setattr(
        annual,
        "annual_facts_for_security",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("annual path must not run")),
    )
    out = ex.extract_with_annual_fallback(_client(), 111)
    assert out.facts == fake and out.empty_reason is None
    assert out.runway_empty_reason is None  # the periodic path never sets the annual runway state


def test_fallback_dark_name_routes_to_the_annual_path(monkeypatch):
    """No 10-Q AND no 10-K -> the combined annual result (shares + runway candidates, OR their honest
    empty/deferral reasons) passes through verbatim. The periodic extractor must NOT run — it fetches
    companyfacts up front, which 404s for the no-companyfacts names the annual path serves cover-only
    (GLAS/CRLBF/TRSG)."""
    import ingest.edgar.annual_runway as annual
    from domain.extraction import ExtractionResult

    sentinel = ExtractionResult(
        facts=[], empty_reason="cover-not-located", runway_empty_reason="financials-in-exhibit"
    )
    monkeypatch.setattr(ex, "fetch_submissions", lambda client, cik: _subs_with(["20-F", "6-K"]))
    monkeypatch.setattr(
        ex,
        "extract_for_security",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("periodic path must not run")),
    )
    monkeypatch.setattr(annual, "annual_facts_for_security", lambda client, cik, cfg: sentinel)
    assert ex.extract_with_annual_fallback(_client(), 222) is sentinel
