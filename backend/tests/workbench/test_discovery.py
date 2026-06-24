"""The EDGAR-first discovery orchestrator (Slice 4a) — keyword-gen → EFTS enumerate → CIK-resolve → classify,
end to end with a fake keyword LLM + a fake EFTS client against the test master (DB-backed: ``ids_for_ciks``
is real). Pins the PLACED/VERIFY tiers, the not-in-master omission, and the fail-open contract."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.fulltext import DiscoveryDegraded, DiscoveryUnavailable
from workbench.discovery import DiscoveryEmpty, run_discovery


class _FakeKeyword:
    """A keyword-gen LLM: ``draft_structured`` returns canned ``{signal, broad}`` (or None to fail open)."""

    def __init__(self, *, returns):
        self._returns = returns
        self.calls: list[dict] = []

    def draft_structured(self, *, system, user, tool):
        self.calls.append({"user": user})
        return self._returns


class _FakeEfts:
    """Canned EFTS pages by cache_key; an unknown key -> an empty page. ``raises=True`` simulates an EFTS/network
    fault (the enumerate step must fail open to an empty universe)."""

    def __init__(self, pages: dict[str, dict], *, raises: bool = False):
        self.pages = pages
        self.raises = raises
        self.calls: list[str] = []

    def get_json(self, url, cache_key):
        self.calls.append(cache_key)
        if self.raises:
            raise RuntimeError("EFTS down")
        return self.pages.get(cache_key, {"hits": {"total": {"value": 0}, "hits": []}})


def _page(total: int, *rows: tuple[str, str]) -> dict:
    return {
        "hits": {
            "total": {"value": total},
            "hits": [{"_source": {"ciks": [cik], "display_names": [dn]}} for cik, dn in rows],
        }
    }


def _insert(db, ticker, *, name, cik) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


# CIKs in the EDGAR zero-padded 10-digit form (what the master stores + EFTS returns).
_A = "0001816590"  # Compass — two keywords -> PLACED
_C = "0001514183"  # Silo — one SIGNAL keyword -> PLACED
_B = "0000000002"  # Alkermes — one BROAD keyword (ketamine) -> VERIFY

_PAGES = {
    "efts/psilocybin_0.json": _page(
        2,
        (_A, "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)"),
        (_C, "Silo Pharma, Inc.  (SILO)  (CIK 0001514183)"),
    ),
    "efts/ibogaine_0.json": _page(1, (_A, "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)")),
    "efts/ketamine_0.json": _page(1, (_B, "Alkermes plc  (ALKS)  (CIK 0000000002)")),
}
_KEYWORDS = {"signal": ["psilocybin", "ibogaine"], "broad": ["ketamine"]}


def test_run_discovery_places_and_verifies(db):
    """Two keywords -> PLACED (Compass, >=2 keywords; Silo, 1 SIGNAL); the single-BROAD ketamine filer (Alkermes)
    -> VERIFY. All three resolve to their CIK's master id; the raw filer map is carried for the reconciler.
    """
    a = _insert(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    c = _insert(db, "SILO", name="Silo Pharma, Inc.", cik=_C)
    b = _insert(db, "ALKS", name="Alkermes plc", cik=_B)
    edgar = _FakeEfts(_PAGES)
    uni = run_discovery(
        db, edgar, _FakeKeyword(returns=_KEYWORDS), "psychedelic therapy", hit_cap=1000
    )
    assert uni.placed == {_A: a, _C: c}
    assert uni.verify == {_B: b}
    assert set(uni.filers) == {_A, _C, _B}  # the raw enumerated set, for the layout match-back
    assert uni.signal == ["psilocybin", "ibogaine"] and uni.broad == ["ketamine"]
    assert not uni.is_empty


def test_run_discovery_omits_not_in_master(db):
    """A discovered CIK with no master row is omitted from both tiers (foreign / no US ticker -> the tail-sweep's
    job, not placeable here) — only Compass is in the master."""
    a = _insert(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    uni = run_discovery(
        db, _FakeEfts(_PAGES), _FakeKeyword(returns=_KEYWORDS), "psychedelics", hit_cap=1000
    )
    assert uni.placed == {_A: a}  # Silo placed-tier but not in master -> omitted
    assert uni.verify == {}  # Alkermes not in master -> omitted


def test_run_discovery_failopen_no_keywords(db):
    """No keywords (the keyword LLM fails open to None) -> an empty universe, and EFTS is never queried."""
    edgar = _FakeEfts(_PAGES)
    uni = run_discovery(db, edgar, _FakeKeyword(returns=None), "anything", hit_cap=1000)
    assert uni.is_empty and uni.placed == {} and uni.verify == {}
    assert edgar.calls == []  # never reached the enumerator


def test_run_discovery_raises_when_efts_degraded(db):
    """COMPLETENESS-OR-FAIL: an EFTS fault is NO LONGER swallowed to an empty universe (the silent-degradation
    bug that quietly fell back to recall). Every page fails -> discover() raises DiscoveryDegraded ->
    run_discovery lets it PROPAGATE so the draft can fail VISIBLY (503)."""
    _insert(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    with pytest.raises(DiscoveryDegraded):
        run_discovery(
            db, _FakeEfts(_PAGES, raises=True), _FakeKeyword(returns=_KEYWORDS), "x", hit_cap=1000
        )


def test_run_discovery_raises_empty_despite_keywords(db):
    """Keyword-gen produced keywords but NOTHING placeable came back (here: none of the discovered CIKs are in
    the master) -> against a populated master that is a BROKEN discovery, not an empty theme. run_discovery
    raises DiscoveryEmpty (a DiscoveryUnavailable) rather than return an empty universe the draft would silently
    fill from model recall."""
    # master left empty of the discovered CIKs
    with pytest.raises(DiscoveryEmpty):
        run_discovery(db, _FakeEfts(_PAGES), _FakeKeyword(returns=_KEYWORDS), "x", hit_cap=1000)
    assert issubclass(DiscoveryEmpty, DiscoveryUnavailable)  # the endpoint catches the base -> 503
