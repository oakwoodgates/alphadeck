"""The EDGAR-first discovery orchestrator (Slice 4a; term-set-driven since T3) — read the thesis's persisted
term set → EFTS enumerate → CIK-resolve → classify, end to end with a fake EFTS client against the test master
(DB-backed: ``ids_for_ciks`` is real). Pins the PLACED/VERIFY tiers, the not-in-master omission, and the
completeness-or-fail contract (no term set / degraded / nothing-placeable all RAISE)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import TermTier
from domain.thesis import TermSetEntry
from ingest.edgar.fulltext import DiscoveryDegraded, DiscoveryUnavailable
from securities import master
from workbench.chain_draft import (
    ProposedPlacement,
    ProposedSegment,
    resolve_discovered_chain,
)
from workbench.discovery import DiscoveryEmpty, DiscoveryNoTerms, run_discovery


def _terms(signal: list[str], broad: list[str]) -> list[TermSetEntry]:
    """A stored term set: SIGNAL = operator seeds, BROAD = keyword-gen breadth (what ``run_discovery`` reads)."""
    return [TermSetEntry(term=t, tier=TermTier.SIGNAL) for t in signal] + [
        TermSetEntry(term=t, tier=TermTier.BROAD) for t in broad
    ]


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
_TERMS = _terms(["psilocybin", "ibogaine"], ["ketamine"])


def test_run_discovery_places_and_verifies(db):
    """SEEDS-ONLY-PLACE: a SIGNAL seed hit -> PLACED (Compass on psilocybin+ibogaine; Silo on psilocybin); the
    broad-only ketamine filer (Alkermes) -> VERIFY. All three resolve to their CIK's master id; the raw filer
    map is carried for the reconciler. ``run_discovery`` READS the stored term set (no LLM call)."""
    a = _insert(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    c = _insert(db, "SILO", name="Silo Pharma, Inc.", cik=_C)
    b = _insert(db, "ALKS", name="Alkermes plc", cik=_B)
    edgar = _FakeEfts(_PAGES)
    uni = run_discovery(db, edgar, _TERMS, hit_cap=1000)
    assert uni.placed == {_A: a, _C: c}
    assert uni.verify == {_B: b}
    assert set(uni.filers) == {_A, _C, _B}  # the raw enumerated set, for the layout match-back
    assert uni.signal == ["psilocybin", "ibogaine"] and uni.broad == ["ketamine"]
    assert not uni.is_empty
    # the run's honesty report threads through: full coverage (3 keywords, page-0 each), nothing capped
    assert uni.coverage is not None
    assert uni.coverage.pages_ok == uni.coverage.pages_attempted == 3
    assert uni.coverage.failed_terms == [] and uni.capped_terms == []


def test_run_discovery_omits_not_in_master(db):
    """A discovered CIK with no master row is omitted from both tiers (foreign / no US ticker -> the tail-sweep's
    job, not placeable here) — only Compass is in the master."""
    a = _insert(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    uni = run_discovery(db, _FakeEfts(_PAGES), _TERMS, hit_cap=1000)
    assert uni.placed == {_A: a}  # Silo placed-tier but not in master -> omitted
    assert uni.verify == {}  # Alkermes not in master -> omitted


def test_run_discovery_relabels_placeable_from_master(db):
    """BIND-THEN-LABEL: a placeable CIK's Filer carries the MASTER row's ticker/name — what the organizer
    context, the match-back keys, and the reconciler's fallback labels all read — never the EFTS display
    string (filing-era, and on a joint filing possibly the COUNTERPARTY's identity outright). The
    un-placeable tail keeps its EFTS label: display-only, it never binds."""
    k = _insert(db, "KLAC", name="KLA CORP", cik=_A)
    pages = {
        # EFTS shows the joint-filing counterparty's identity for _A — the misbind's raw material
        "efts/psilocybin_0.json": _page(1, (_A, "LAM RESEARCH CORP  (LRCX)  (CIK 0000707549)")),
        # _B is not in the master (the tail) — its EFTS label must survive untouched
        "efts/ketamine_0.json": _page(1, (_B, "Tail Foreign Co  (CIK 0000000002)")),
    }
    uni = run_discovery(db, _FakeEfts(pages), _TERMS, hit_cap=1000)
    assert uni.placed == {_A: k}
    f = uni.filers[_A]
    assert (f.name, f.ticker) == ("KLA CORP", "KLAC")  # the bound master identity, not the display
    assert f.keywords == {"psilocybin"}  # the #9 provenance tell carries over
    assert (uni.filers[_B].name, uni.filers[_B].ticker) == ("Tail Foreign Co", None)


def test_end_to_end_shown_identity_equals_bound_identity(db):
    """THE MISBIND REGRESSION, end to end (SIMO↔MXL): even when the EFTS display strings arrive fully
    CROSSED — each CIK wearing the other company's name+ticker, the joint-425 worst case — every placement
    the reconciler emits shows the identity of the master row it BINDS. One name travels the organizer
    match-back path, the other the reconciler-appended 'Discovered' path; shown ≡ bound on both."""
    mxl = _insert(db, "MXL", name="MAXLINEAR, INC", cik="0001288469")
    simo = _insert(db, "SIMO", name="Silicon Motion Technology CORP", cik="0001329394")
    pages = {
        "efts/psilocybin_0.json": _page(
            2,
            ("0001288469", "Silicon Motion Technology CORP  (SIMO)  (CIK 0001329394)"),
            ("0001329394", "MAXLINEAR, INC  (MXL)  (CIK 0001288469)"),
        )
    }
    uni = run_discovery(db, _FakeEfts(pages), _terms(["psilocybin"], []), hit_cap=1000)
    segs = [
        ProposedSegment(
            label="Controllers",
            placements=[ProposedPlacement(name="MaxLinear", ticker="MXL", prose="x")],
        )
    ]
    chain = resolve_discovered_chain(db, segs, uni, tenant_id=DEFAULT_TENANT_ID)
    assert {p.security_id for p in chain.placements} == {mxl, simo}  # both names, none dropped
    for p in chain.placements:
        bound = master.get(db, p.security_id, tenant_id=DEFAULT_TENANT_ID)
        assert p.ticker == bound.ticker  # shown ≡ bound — the crossed display never rides a row


# --- the per-tier hit cap at the orchestration layer: SIGNAL deep / BROAD shallow ---

_DEEP_SIG = (
    "0000000116"  # on the SIGNAL term's DEEP page (beyond the BROAD cap, within the SIGNAL cap)
)
_DEEP_BRD = "0000000126"  # on the BROAD term's deep page (beyond the BROAD cap — unsearched)
_SHALLOW_BRD = "0000000121"  # on the BROAD term's page-0 (within the BROAD cap)


def _tier_pages() -> dict[str, dict]:
    """A SIGNAL term ``alphaterm`` (CIKs 111..116) and a BROAD term ``betaterm`` (CIKs 121..126), each 3
    pages of 2 hits (total 6) — the deep names sit on each term's offset-4 page. Invented names."""

    def _rows(*ids: int) -> list[tuple[str, str]]:
        return [(f"{i:010d}", f"Invented Co {i}  (IC{i})  (CIK {i:010d})") for i in ids]

    return {
        "efts/alphaterm_0.json": _page(6, *_rows(111, 112)),
        "efts/alphaterm_2.json": _page(6, *_rows(113, 114)),
        "efts/alphaterm_4.json": _page(6, *_rows(115, 116)),
        "efts/betaterm_0.json": _page(6, *_rows(121, 122)),
        "efts/betaterm_2.json": _page(6, *_rows(123, 124)),
        "efts/betaterm_4.json": _page(6, *_rows(125, 126)),
    }


def test_run_discovery_per_tier_caps(db):
    """SIGNAL deep / BROAD shallow end to end: under ``hit_cap=6, broad_hit_cap=2`` the name on the seed's
    DEEP page is PLACED, the name on the broad term's page-0 is VERIFY, the name on the broad term's deep page
    is ABSENT (its page was never searched) — and that truncation is ON THE RECORD: the BROAD term alone lands
    in ``capped_terms`` (#9 rule 4)."""
    deep_sig = _insert(db, "IC116", name="Invented Co 116", cik=_DEEP_SIG)
    shallow_brd = _insert(db, "IC121", name="Invented Co 121", cik=_SHALLOW_BRD)
    _insert(db, "IC126", name="Invented Co 126", cik=_DEEP_BRD)
    edgar = _FakeEfts(_tier_pages())
    uni = run_discovery(db, edgar, _terms(["alphaterm"], ["betaterm"]), hit_cap=6, broad_hit_cap=2)
    assert (
        uni.placed[_DEEP_SIG] == deep_sig
    )  # the deep SIGNAL name — walked to the deep cap, placed
    assert uni.verify[_SHALLOW_BRD] == shallow_brd  # the in-cap broad name — surfaced as VERIFY
    assert _DEEP_BRD not in uni.verify and _DEEP_BRD not in uni.filers  # beyond the shallow cap
    assert "efts/alphaterm_4.json" in edgar.calls and "efts/betaterm_4.json" not in edgar.calls
    assert uni.capped_terms == ["betaterm"]  # the BROAD term flagged; the seed was not capped
    assert uni.coverage is not None and uni.coverage.failed_terms == []


def test_run_discovery_a_term_in_both_tiers_enumerates_deep(db):
    """Recall-first: a term stored in BOTH tiers is a seed too, so it enumerates to the DEEP cap (the shallow
    set is ``broad − signal``) — its deep page IS searched, the name there PLACES, and nothing is flagged
    capped. The shallow cap never touches a seed."""
    deep = _insert(db, "IC126", name="Invented Co 126", cik=_DEEP_BRD)
    edgar = _FakeEfts(_tier_pages())
    terms = _terms(["alphaterm", "betaterm"], ["betaterm"])  # betaterm in BOTH tiers
    uni = run_discovery(db, edgar, terms, hit_cap=6, broad_hit_cap=2)
    assert uni.placed[_DEEP_BRD] == deep  # a SIGNAL hit on the deep page -> PLACED
    assert "efts/betaterm_4.json" in edgar.calls  # walked deep, not shallow
    assert uni.capped_terms == []  # neither term hit its (deep) cap


def test_run_discovery_env_caps_reach_discover(db, monkeypatch):
    """The Settings dials drive the run when the caller passes no caps: ``ALPHADECK_DISCOVERY_HIT_CAP`` bounds
    the SIGNAL tier and ``ALPHADECK_DISCOVERY_BROAD_HIT_CAP`` the BROAD tier — the same deep-placed /
    shallow-capped outcome as the explicit-argument run above."""
    from domain.settings import get_settings

    deep_sig = _insert(db, "IC116", name="Invented Co 116", cik=_DEEP_SIG)
    _insert(db, "IC126", name="Invented Co 126", cik=_DEEP_BRD)
    edgar = _FakeEfts(_tier_pages())
    monkeypatch.setenv("ALPHADECK_DISCOVERY_HIT_CAP", "6")
    monkeypatch.setenv("ALPHADECK_DISCOVERY_BROAD_HIT_CAP", "2")
    get_settings.cache_clear()  # re-read the env (the singleton may have been built at the defaults)
    try:
        uni = run_discovery(db, edgar, _terms(["alphaterm"], ["betaterm"]))
    finally:
        get_settings.cache_clear()  # drop the env-driven singleton; monkeypatch restores the env after
    assert uni.placed[_DEEP_SIG] == deep_sig
    assert _DEEP_BRD not in uni.filers and "efts/betaterm_4.json" not in edgar.calls
    assert uni.capped_terms == ["betaterm"]


def test_run_discovery_raises_when_no_term_set(db):
    """No term set produced yet (empty list) -> DiscoveryNoTerms (the operator must run .../terms first); EFTS is
    never queried. The not-ready state FAILS VISIBLY (503), never a silent recall fallback."""
    edgar = _FakeEfts(_PAGES)
    with pytest.raises(DiscoveryNoTerms):
        run_discovery(db, edgar, [], hit_cap=1000)
    assert edgar.calls == []  # never reached the enumerator
    assert issubclass(
        DiscoveryNoTerms, DiscoveryUnavailable
    )  # the endpoint catches the base -> 503


def test_run_discovery_raises_when_efts_degraded(db):
    """COMPLETENESS-OR-FAIL: an EFTS fault is NO LONGER swallowed to an empty universe (the silent-degradation
    bug that quietly fell back to recall). Every page fails -> discover() raises DiscoveryDegraded ->
    run_discovery lets it PROPAGATE so the draft can fail VISIBLY (503)."""
    _insert(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    with pytest.raises(DiscoveryDegraded):
        run_discovery(db, _FakeEfts(_PAGES, raises=True), _TERMS, hit_cap=1000)


def test_run_discovery_raises_empty_despite_terms(db):
    """The term set enumerated terms but NOTHING placeable came back (here: none of the discovered CIKs are in
    the master) -> against a populated master that is a BROKEN discovery, not an empty theme. run_discovery
    raises DiscoveryEmpty (a DiscoveryUnavailable) rather than return an empty universe the draft would silently
    fill from model recall."""
    # master left empty of the discovered CIKs
    with pytest.raises(DiscoveryEmpty):
        run_discovery(db, _FakeEfts(_PAGES), _TERMS, hit_cap=1000)
    assert issubclass(DiscoveryEmpty, DiscoveryUnavailable)  # the endpoint catches the base -> 503
