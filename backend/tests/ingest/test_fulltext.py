"""The EDGAR full-text discovery enumerator (Slice 1) — parse / paginate / union / filter / determinism, with
a fake EFTS client. No network, no DB. The live measurements (Check 1 dropped-set scan, Check 2 CIK-population,
the pagination cap) are gate-2, run against real EFTS + the master."""

from __future__ import annotations

import uuid

import pytest

from ingest.edgar.fulltext import (
    DiscoveryDegraded,
    Filer,
    _parse_display,
    ciks_for_keyword,
    classify,
    discover,
    precision_filter,
)


class _FakeEfts:
    """Returns canned EFTS JSON by cache_key; an unknown key -> an empty page (terminates pagination). A
    cache_key in ``fail`` RAISES (simulating a page that failed even after ``polite_get``'s retries).
    """

    def __init__(self, pages: dict[str, dict], *, fail: set[str] | None = None) -> None:
        self.pages = pages
        self.fail = set(fail or ())
        self.calls: list[str] = []

    def get_json(self, url, cache_key):
        self.calls.append(cache_key)
        if cache_key in self.fail:
            raise RuntimeError(f"EFTS {cache_key} unreachable")
        return self.pages.get(cache_key, {"hits": {"total": {"value": 0}, "hits": []}})


def _page(total: int, *rows: tuple[str, str]) -> dict:
    """An EFTS page: each row is (cik, display_name)."""
    return {
        "hits": {
            "total": {"value": total},
            "hits": [{"_source": {"ciks": [cik], "display_names": [dn]}} for cik, dn in rows],
        }
    }


def test_parse_display_name_and_ticker():
    assert _parse_display("COMPASS Pathways plc  (CMPS)  (CIK 0001816590)") == (
        "COMPASS Pathways plc",
        "CMPS",
    )
    assert _parse_display("First Person Ltd.  (CIK 0001900035)") == ("First Person Ltd.", None)
    assert _parse_display("Optimi Health Corp.  (OPTH, OPTHF)  (CIK 0002027329)") == (
        "Optimi Health Corp.",
        "OPTH",  # first of several tickers
    )


def test_ciks_for_keyword_paginates_and_dedups():
    pages = {
        # page 0: 2 hits (one CIK repeated across filings) of a total of 3
        "efts/psilocybin_0.json": _page(
            3,
            ("0001816590", "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)"),
            ("0001816590", "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)"),
        ),
        # page from=2: the 3rd hit -> a second distinct CIK
        "efts/psilocybin_2.json": _page(
            3, ("0001514183", "Silo Pharma, Inc.  (SILO)  (CIK 0001514183)")
        ),
    }
    fake = _FakeEfts(pages)
    out = ciks_for_keyword(fake, "psilocybin")
    assert set(out) == {"0001816590", "0001514183"}  # deduped + paginated to total
    assert out["0001816590"] == ("COMPASS Pathways plc", "CMPS")
    assert fake.calls == ["efts/psilocybin_0.json", "efts/psilocybin_2.json"]  # stopped at total


def test_discover_unions_keyword_hits():
    pages = {
        "efts/psilocybin_0.json": _page(
            1, ("0001816590", "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)")
        ),
        "efts/ibogaine_0.json": _page(
            2,
            ("0001816590", "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)"),
            ("0001999999", "NoiseCo  (NOIS)  (CIK 0001999999)"),
        ),
    }
    uni = discover(_FakeEfts(pages), ["psilocybin", "ibogaine"])
    assert uni["0001816590"].keywords == {"psilocybin", "ibogaine"}  # union across keywords
    assert uni["0001999999"].keywords == {"ibogaine"}
    assert uni["0001816590"].ticker == "CMPS"


def test_precision_filter_keeps_multi_and_signal_drops_single_collision():
    """The load-bearing rule: ≥2 distinct keywords OR ≥1 SIGNAL keyword. A name on one COLLISION keyword
    (Chevron on 'DMT') is dropped; a multi-keyword name and a single-SIGNAL name are kept."""
    filers = {
        "A": Filer("A", "Real Multi", "RM", {"psilocybin", "ibogaine"}),  # ≥2 keywords -> kept
        "B": Filer("B", "Real Single-signal", "RS", {"ibogaine"}),  # ≥1 signal -> kept
        "C": Filer("C", "Chevron", "CVX", {"DMT"}),  # 1 collision keyword -> DROPPED
    }
    kept = precision_filter(filers, signal={"psilocybin", "psilocin", "ibogaine"})
    assert set(kept) == {"A", "B"}


def test_discover_is_deterministic():
    pages = {
        "efts/psilocybin_0.json": _page(
            1, ("0001816590", "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)")
        )
    }
    a = discover(_FakeEfts(pages), ["psilocybin"])
    b = discover(_FakeEfts(pages), ["psilocybin"])
    assert {k: v.keywords for k, v in a.items()} == {k: v.keywords for k, v in b.items()}


def test_discover_parallel_matches_the_sequential_reference():
    """The concurrency gate: the PARALLEL ``discover`` returns the SAME universe as the SEQUENTIAL per-keyword
    walk (``ciks_for_keyword``) — the CIK set + the keyword tagging are order-independent. Multi-page (the
    fan-out is exercised: 25 hits over 3 pages) with a CIK shared across two keywords (the union is exercised).
    """

    def _rows(*ids: int) -> list[tuple[str, str]]:
        return [(f"{i:010d}", f"Co{i}  (T{i})  (CIK {i:010d})") for i in ids]

    pages = {
        "efts/psilocybin_0.json": _page(25, *_rows(*range(0, 10))),
        "efts/psilocybin_10.json": _page(25, *_rows(*range(10, 20))),
        "efts/psilocybin_20.json": _page(
            25, *_rows(*range(20, 25))
        ),  # 5 hits — the short last page
        "efts/ibogaine_0.json": _page(2, *_rows(5, 99)),  # cik 5 overlaps psilocybin; 99 is new
    }
    kws = ["psilocybin", "ibogaine"]
    par = discover(_FakeEfts(pages), kws, max_workers=8)

    ref: dict[str, set[str]] = {}  # the sequential union, keyword-by-keyword
    for kw in kws:
        for cik in ciks_for_keyword(_FakeEfts(pages), kw):
            ref.setdefault(cik, set()).add(kw)

    assert {c: f.keywords for c, f in par.items()} == ref  # identical CIK set + keyword tagging
    assert par["0000000005"].keywords == {"psilocybin", "ibogaine"}  # the shared CIK tagged by both
    assert "0000000099" in par  # a deep/second-keyword name isn't lost by the fan-out


# --- reliability: per-page resilience + the completeness-or-fail threshold ---


def _pages_3() -> dict[str, dict]:
    """One keyword 'kw' over 3 pages of 2 hits each (total 6) -> cache keys kw_0, kw_2, kw_4."""
    return {
        "efts/kw_0.json": _page(
            6, ("0000000001", "A (A) (CIK 0000000001)"), ("0000000002", "B (B) (CIK 0000000002)")
        ),
        "efts/kw_2.json": _page(
            6, ("0000000003", "C (C) (CIK 0000000003)"), ("0000000004", "D (D) (CIK 0000000004)")
        ),
        "efts/kw_4.json": _page(
            6, ("0000000005", "E (E) (CIK 0000000005)"), ("0000000006", "F (F) (CIK 0000000006)")
        ),
    }


def test_discover_skips_a_failed_page_below_threshold_and_keeps_the_rest():
    """A single page that fails after retries does NOT nuke the universe (the silent-degradation bug): it is
    skipped, the surrounding pages still union, and below ``degraded_ratio`` the run returns (the skip is logged
    elsewhere). The failed page's CIKs are simply absent — never a wholesale empty."""
    fake = _FakeEfts(_pages_3(), fail={"efts/kw_2.json"})  # the middle (offset-2) page fails
    uni = discover(
        fake, ["kw"], max_workers=4, degraded_ratio=0.5
    )  # 1 of 3 pages = 33% < 50% -> returns
    assert set(uni) == {
        "0000000001",
        "0000000002",
        "0000000005",
        "0000000006",
    }  # page-0 + page-4 survive
    assert (
        "0000000003" not in uni and "0000000004" not in uni
    )  # the failed page's CIKs, skipped not faked


def test_discover_raises_DiscoveryDegraded_past_threshold():
    """Past the threshold the run is DEGRADED -> it RAISES rather than return a partial universe as if whole
    (completeness-or-fail). Same single failure, a stricter ratio."""
    fake = _FakeEfts(_pages_3(), fail={"efts/kw_2.json"})  # 1 of 3 = 33%
    with pytest.raises(DiscoveryDegraded):
        discover(fake, ["kw"], max_workers=4, degraded_ratio=0.10)  # 33% > 10% -> degraded


def test_discover_pervasive_failure_raises_not_returns_empty():
    """A pervasive failure (SEC down / a parse bug failing EVERY page) blows the ratio and surfaces LOUDLY as
    DiscoveryDegraded — never the silent empty universe that masqueraded as 'found nothing'."""
    fake = _FakeEfts(_pages_3(), fail=set(_pages_3()))  # every page fails
    with pytest.raises(DiscoveryDegraded):
        discover(fake, ["kw"], max_workers=4, degraded_ratio=0.05)


# --- classify: the PLACED / VERIFY tiers (Slice 2b) ---


def test_classify_placed_verify_and_omits_not_in_master():
    """SEEDS-ONLY-PLACE: PLACED = in-master AND >=1 SIGNAL (a seed). VERIFY = in-master, no signal, hits >=1
    BROAD (any count) — a SEPARATE lower-confidence tier (the broad-only adjacents, made visible, never
    auto-placed). A filer not in the master is omitted entirely (the tail-sweep's job)."""
    s_a, s_b, s_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    filers = {
        "A": Filer("A", "Two signals", "M", {"psilocybin", "ibogaine"}),  # signal -> PLACED
        "B": Filer("B", "Signal-single", "S", {"ibogaine"}),  # 1 signal -> PLACED
        "C": Filer(
            "C", "Broad-single in-master (e.g. Alkermes/ketamine)", "ALKS", {"ketamine"}
        ),  # -> VERIFY
        "D": Filer("D", "Broad-single NOT in master", "D", {"MDMA"}),  # not in master -> omitted
    }
    in_master = {"A": s_a, "B": s_b, "C": s_c}  # D absent
    out = classify(
        filers,
        in_master_ids=in_master,
        signal={"psilocybin", "ibogaine"},
        broad={"ketamine", "MDMA"},
    )
    assert out.placed == {"A": s_a, "B": s_b}
    assert out.verify == {"C": s_c}  # the broad-only adjacent, surfaced not dropped
    # D is in neither tier (not placeable here)
    assert "D" not in out.placed and "D" not in out.verify


def test_classify_broad_only_is_verify_not_placed():
    """SEEDS-ONLY-PLACE: broad corroboration no longer auto-places. A name hitting MANY distinct BROAD keywords
    but NO signal -> VERIFY (visible, operator-promotable), never PLACED — because a broad set is LLM-proposed
    and non-deterministic, so >=2-broad placement made PLACED swing run-to-run. Corroboration is 'show me', not
    'auto-trust'. (Supersedes the old '>=2 keywords -> PLACED' rule; nothing is dropped, the split moves.)
    """
    sid = uuid.uuid4()
    filers = {"X": Filer("X", "Three broad, no signal", "X", {"ketamine", "MDMA", "psychedelic"})}
    out = classify(
        filers,
        in_master_ids={"X": sid},
        signal={"psilocybin"},
        broad={"ketamine", "MDMA", "psychedelic"},
    )
    assert out.placed == {} and out.verify == {"X": sid}  # broad-only (any count) -> VERIFY
