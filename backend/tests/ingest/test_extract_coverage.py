"""extract_for_security COVERAGE — the both-forms relaxation + the honest empty signal.

Unit-level (no network, no fixtures): a fake client serves a submissions index and one stand-in text per filing,
and ``extract_facts`` is captured, so the role wiring (which filing feeds shares/cash and which feeds purity), the
fallback and the "no domestic filing -> []" rule are proven directly, without the golden fixtures (which exercise
``extract_facts`` with both forms already present). The fixture-backed newest-filing tests (decision B1) are
``test_extract_newest_filing.py``."""

from __future__ import annotations

import types
from datetime import date

from ingest.edgar import extract as ex


def _client():
    # extract_with_annual_fallback's wiring tests monkeypatch fetch_submissions and extract_for_security; a
    # minimal stub is enough (no network).
    return types.SimpleNamespace(get_json=lambda url, cache: {"facts": {}})


class _PeriodicClient:
    """companyfacts (ignored by the captured extract_facts) + a submissions index of ``rows`` (newest first:
    (form, accession, filed, report date)); each document's text is ``<accession>-TEXT``. Records document reads.
    """

    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows
        self.docs: list[str] = []

    def get_json(self, url: str, cache: str) -> dict:
        if cache.startswith("submissions/"):
            return {
                "filings": {
                    "recent": {
                        "form": [r[0] for r in self.rows],
                        "accessionNumber": [r[1] for r in self.rows],
                        "primaryDocument": [f"{r[1]}.htm" for r in self.rows],
                        "filingDate": [r[2] for r in self.rows],
                        "reportDate": [r[3] for r in self.rows],
                        "acceptanceDateTime": [f"{r[2]}T21:05:00.000Z" for r in self.rows],
                    }
                }
            }
        return {"facts": {}}

    def get_text(self, url: str, cache: str) -> str:
        self.docs.append(cache)
        return f"{cache.split('/')[1]}-TEXT"


def _capture_extract_facts(monkeypatch):
    calls: dict = {}

    def fake(
        cf, tenq_text, tenk_text, *, tenq_ref, tenk_ref, tenq_date, tenk_date, cfg, values_form
    ):
        calls.update(
            values_form=values_form,
            tenq_text=tenq_text,
            tenk_text=tenk_text,
            tenq_ref=tenq_ref,
            tenk_ref=tenk_ref,
            tenq_date=tenq_date,
            tenk_date=tenk_date,
        )
        return ["shares", "burn", "purity"]  # extract_facts always yields the three candidates

    monkeypatch.setattr(ex, "extract_facts", fake)
    return calls


def test_both_forms_present_is_unchanged(monkeypatch):
    # the 10-Q is the newer filing: q=10-Q for the shares/burn role, k=10-K for the purity role
    client = _PeriodicClient(
        [("10-Q", "Q1", "2026-05-12", "2026-03-31"), ("10-K", "K1", "2026-03-17", "2025-12-31")]
    )
    calls = _capture_extract_facts(monkeypatch)
    out = ex.extract_for_security(client, 111)
    assert out == ["shares", "burn", "purity"]
    assert calls["tenq_text"] == "Q1-TEXT" and calls["tenk_text"] == "K1-TEXT"
    assert calls["tenq_date"] == date(2026, 3, 31) and calls["tenk_date"] == date(2025, 12, 31)
    assert (
        calls["values_form"] == "10-Q"
    )  # the cash candidate's provenance kind follows the cited form


def test_only_10k_falls_back_to_it_for_all_roles(monkeypatch):
    # a domestic filer with a 10-K but NO recent 10-Q must still yield candidates (the relaxation)
    client = _PeriodicClient([("10-K", "K1", "2026-03-17", "2025-12-31")])
    calls = _capture_extract_facts(monkeypatch)
    out = ex.extract_for_security(client, 222)
    assert out == ["shares", "burn", "purity"]  # NOT [] — the 10-K alone is enough now
    assert calls["tenq_text"] == "K1-TEXT"  # shares/burn role fell back to the 10-K
    assert calls["values_form"] == "10-K"
    assert calls["tenk_text"] == "K1-TEXT"
    assert client.docs == ["forms/K1/K1.htm"]  # one document, read once


def test_only_10q_falls_back_to_it_for_all_roles(monkeypatch):
    client = _PeriodicClient([("10-Q", "Q1", "2026-05-12", "2026-03-31")])
    calls = _capture_extract_facts(monkeypatch)
    out = ex.extract_for_security(client, 333)
    assert out == ["shares", "burn", "purity"]
    assert (
        calls["tenq_text"] == "Q1-TEXT" and calls["tenk_text"] == "Q1-TEXT"
    )  # purity fell back to the 10-Q
    assert client.docs == ["forms/Q1/Q1.htm"]


def test_no_periodic_filing_returns_empty(monkeypatch):
    # a foreign private issuer (20-F/6-K only) has neither form -> honest [] (the "not covered" signal)
    client = _PeriodicClient(
        [("20-F", "F1", "2026-04-01", "2025-12-31"), ("6-K", "S1", "2026-03-01", "")]
    )
    # extract_facts must NOT be called when there's nothing to parse
    monkeypatch.setattr(
        ex, "extract_facts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run"))
    )
    assert ex.extract_for_security(client, 444) == []
    assert client.docs == []  # no document read


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
    monkeypatch.setattr(
        ex, "extract_for_security", lambda client, cik, cfg, values_only=False: fake
    )
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


def test_fallback_hands_values_only_to_the_periodic_path(monkeypatch):
    """A values-only run (the platform-value runs) reaches ``extract_for_security`` as values-only."""
    seen: dict = {}

    def periodic(client, cik, cfg, values_only=False):
        seen["values_only"] = values_only
        return []

    monkeypatch.setattr(ex, "fetch_submissions", lambda client, cik: _subs_with(["10-K"]))
    monkeypatch.setattr(ex, "extract_for_security", periodic)
    ex.extract_with_annual_fallback(_client(), 111, values_only=True)
    assert seen == {"values_only": True}
    ex.extract_with_annual_fallback(_client(), 111)
    assert seen == {"values_only": False}
