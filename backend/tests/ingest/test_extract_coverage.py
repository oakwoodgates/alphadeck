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
