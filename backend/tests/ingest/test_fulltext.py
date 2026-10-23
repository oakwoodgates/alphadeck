"""The EDGAR full-text discovery enumerator (Slice 1) — parse / paginate / union / filter / determinism, with
a fake EFTS client. No network, no DB. The live measurements (Check 1 dropped-set scan, Check 2 CIK-population,
the pagination cap) are gate-2, run against real EFTS + the master."""

from __future__ import annotations

import json
import urllib.parse
import uuid

import pytest

from ingest.edgar.client import EdgarClient, InvalidBody
from ingest.edgar.fulltext import (
    DiscoveryDegraded,
    EftsErrorBody,
    Filer,
    _check_efts_page,
    _parse_display,
    ciks_for_keyword,
    classify,
    discover,
    precision_filter,
)


class _FakeEfts:
    """Returns canned EFTS JSON by cache_key; an unknown key -> an empty page (terminates pagination). A
    cache_key in ``fail`` RAISES on every call (a persistent outage, failed even after ``polite_get``'s
    retries); one in ``fail_once`` raises on its FIRST call only (a transient blip the retry pass recovers).
    """

    def __init__(
        self,
        pages: dict[str, dict],
        *,
        fail: set[str] | None = None,
        fail_once: set[str] | None = None,
    ) -> None:
        self.pages = pages
        self.fail = set(fail or ())
        self.fail_once = set(fail_once or ())
        self.calls: list[str] = []

    def get_json(self, url, cache_key, *, validate=None):
        # ``validate`` is accepted (the client protocol) but not applied: ``_fetch_page`` re-checks the
        # page itself, so a fake that hands back an error body still exercises the real rejection.
        self.calls.append(cache_key)
        if cache_key in self.fail:
            raise RuntimeError(f"EFTS {cache_key} unreachable")
        if cache_key in self.fail_once:
            self.fail_once.discard(cache_key)
            raise RuntimeError(f"EFTS {cache_key} transient")
        return self.pages.get(cache_key, {"hits": {"total": {"value": 0}, "hits": []}})


def _page(total: int, *rows: tuple[str, str]) -> dict:
    """An EFTS page: each row is (cik, display_name)."""
    return {
        "hits": {
            "total": {"value": total},
            "hits": [{"_source": {"ciks": [cik], "display_names": [dn]}} for cik, dn in rows],
        }
    }


def _joint_page(total: int, *hits: tuple[list[str], list[str]]) -> dict:
    """An EFTS page of MULTI-CIK hits: each hit is ``(ciks, display_names)`` — the joint-filing shape
    (merger 425/S-4, tender offer SC TO, SC 13D/G, co-registrant lists) the single-cik ``_page`` helper
    can't express. ``ciks`` and ``display_names`` are parallel arrays, one entry per entity, exactly as
    EFTS returns them (verified against 1,301 cached multi-CIK hits, all parallel)."""
    return {
        "hits": {
            "total": {"value": total},
            "hits": [{"_source": {"ciks": c, "display_names": d}} for c, d in hits],
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


def test_joint_filing_pairs_each_cik_with_its_own_display():
    """THE MISBIND REGRESSION (SIMO↔MXL, KLAC↔LRCX): a joint filing (merger 425) lists BOTH entities'
    CIKs; each must get ITS OWN display name/ticker — stamping ``display_names[0]`` across every CIK is
    how merger counterparties swapped identities. The join is by the CIK each display string embeds.
    """
    pages = {
        "efts/nand_0.json": _joint_page(
            1,
            (
                ["0001288469", "0001329394"],
                [
                    "MAXLINEAR, INC  (MXL)  (CIK 0001288469)",
                    "Silicon Motion Technology CORP  (SIMO)  (CIK 0001329394)",
                ],
            ),
        )
    }
    uni = discover(_FakeEfts(pages), ["nand"]).filers
    assert (uni["0001288469"].name, uni["0001288469"].ticker) == ("MAXLINEAR, INC", "MXL")
    assert (uni["0001329394"].name, uni["0001329394"].ticker) == (
        "Silicon Motion Technology CORP",
        "SIMO",
    )


def test_joint_filing_embedded_cik_wins_over_index_order():
    """The embedded-CIK join is ORDER-PROOF: displays arrive in the opposite order of ``ciks`` and each
    entity still gets its own label (the 2015 KLA/Lam merger shape). Index position is never trusted when
    a display names its CIK."""
    pages = {
        "efts/etch_0.json": _joint_page(
            1,
            (
                ["0000707549", "0000319201"],
                [
                    "KLA TENCOR CORP  (KLAC)  (CIK 0000319201)",
                    "LAM RESEARCH CORP  (LRCX)  (CIK 0000707549)",
                ],
            ),
        )
    }
    uni = discover(_FakeEfts(pages), ["etch"]).filers
    assert (uni["0000707549"].name, uni["0000707549"].ticker) == ("LAM RESEARCH CORP", "LRCX")
    assert (uni["0000319201"].name, uni["0000319201"].ticker) == ("KLA TENCOR CORP", "KLAC")


def test_joint_filing_person_cik_never_wears_the_company_label():
    """An SC 13D lists the company AND the reporting PERSON (a real cached case: NRX + Javitt Jonathan C).
    The person's CIK gets the person's display — name only, no ticker — never the company's ticker (the
    old cross-stamp labeled a person's CIK 'NRX Pharmaceuticals (NRXP)')."""
    pages = {
        "efts/ketamine_0.json": _joint_page(
            1,
            (
                ["0001719406", "0001303782"],
                [
                    "NRX Pharmaceuticals, Inc.  (NRXP, NRXPW)  (CIK 0001719406)",
                    "Javitt Jonathan C  (CIK 0001303782)",
                ],
            ),
        )
    }
    uni = discover(_FakeEfts(pages), ["ketamine"]).filers
    assert (uni["0001719406"].name, uni["0001719406"].ticker) == (
        "NRX Pharmaceuticals, Inc.",
        "NRXP",
    )
    assert (uni["0001303782"].name, uni["0001303782"].ticker) == ("Javitt Jonathan C", None)


def test_joint_filing_unverifiable_cik_is_enumerated_unlabeled():
    """A CIK with NO verified display (ragged arrays / a display that names a DIFFERENT cik) is still
    ENUMERATED — recall never hangs on a parse (#9) — but carries no label: never another entity's, and
    never a guess. A later page where the entity appears under its own display FILLS the missing label
    (and an already-present label is never overwritten — first-verified-seen)."""
    pages = {
        # ragged: two CIKs, one display (embedding only the first) -> the second is kept, unlabeled
        "efts/kw1_0.json": _joint_page(
            1,
            (
                ["0000000001", "0000000002"],
                ["Alpha Co  (ALFA)  (CIK 0000000001)"],
            ),
        ),
        # the second entity later surfaces under its own display -> label filled, keywords unioned
        "efts/kw2_0.json": _page(1, ("0000000002", "Beta Co  (BETA)  (CIK 0000000002)")),
        # a THIRD sighting under a different display must not overwrite the filled label
        "efts/kw3_0.json": _page(1, ("0000000002", "Beta Company Holdings  (CIK 0000000002)")),
    }
    uni = discover(_FakeEfts(pages), ["kw1", "kw2", "kw3"]).filers
    assert (uni["0000000001"].name, uni["0000000001"].ticker) == ("Alpha Co", "ALFA")
    assert uni["0000000002"].keywords == {"kw1", "kw2", "kw3"}  # the ragged kw1 hit still counted
    assert (uni["0000000002"].name, uni["0000000002"].ticker) == ("Beta Co", "BETA")


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
    uni = discover(_FakeEfts(pages), ["psilocybin", "ibogaine"]).filers
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
    a = discover(_FakeEfts(pages), ["psilocybin"]).filers
    b = discover(_FakeEfts(pages), ["psilocybin"]).filers
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
    par = discover(_FakeEfts(pages), kws, max_workers=8).filers

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
    """A single page that fails PERSISTENTLY (both the first pass and the retry pass) does NOT nuke the
    universe: it is skipped, the surrounding pages still union, and below ``degraded_ratio`` the run returns —
    with the gap on the record (coverage: 2/3 pages, the term named, retried-but-not-recovered). The failed
    page's CIKs are simply absent — never a wholesale empty."""
    fake = _FakeEfts(_pages_3(), fail={"efts/kw_2.json"})  # the middle (offset-2) page fails
    run = discover(
        fake, ["kw"], max_workers=4, degraded_ratio=0.5
    )  # 1 of 3 pages = 33% < 50% -> returns
    assert set(run.filers) == {
        "0000000001",
        "0000000002",
        "0000000005",
        "0000000006",
    }  # page-0 + page-4 survive
    assert (
        "0000000003" not in run.filers and "0000000004" not in run.filers
    )  # the failed page's CIKs, skipped not faked
    cov = run.coverage
    assert (cov.pages_ok, cov.pages_attempted) == (2, 3)  # the gap is ON THE RECORD (#9 rule 2)
    assert cov.failed_terms == ["kw"]  # ...and the term is NAMED
    assert (cov.retried, cov.recovered) == (
        1,
        0,
    )  # retried once, persistent failure -> not recovered
    assert run.capped_terms == []


def test_discover_raises_DiscoveryDegraded_past_threshold():
    """Past the threshold the run is DEGRADED -> it RAISES rather than return a partial universe as if whole
    (completeness-or-fail). Same single persistent failure, a stricter ratio; the exception carries the
    POST-RETRY counts (the operator-facing 503 message names them)."""
    fake = _FakeEfts(_pages_3(), fail={"efts/kw_2.json"})  # 1 of 3 = 33%
    with pytest.raises(DiscoveryDegraded) as exc:
        discover(fake, ["kw"], max_workers=4, degraded_ratio=0.10)  # 33% > 10% -> degraded
    assert (exc.value.failed, exc.value.attempted) == (1, 3)  # post-retry counts


def test_discover_pervasive_failure_raises_not_returns_empty():
    """A pervasive failure (SEC down / a parse bug failing EVERY page) blows the ratio and surfaces LOUDLY as
    DiscoveryDegraded — never the silent empty universe that masqueraded as 'found nothing'."""
    fake = _FakeEfts(_pages_3(), fail=set(_pages_3()))  # every page fails
    with pytest.raises(DiscoveryDegraded) as exc:
        discover(fake, ["kw"], max_workers=4, degraded_ratio=0.05)
    # only page-0 is ever attemptable (its failure hides the offsets), and the retry didn't save it
    assert (exc.value.failed, exc.value.attempted) == (1, 1)


def test_discover_clean_run_coverage():
    """A clean run's coverage is the FULL-ENUMERATION statement: every page fetched, nothing retried, no term
    capped or dead — the quiet baseline the Workbench strip renders as one muted line."""
    run = discover(_FakeEfts(_pages_3()), ["kw"], max_workers=4)
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}
    cov = run.coverage
    assert cov.pages_ok == cov.pages_attempted == 3
    assert (cov.retried, cov.recovered, cov.failed_terms) == (0, 0, [])
    assert run.capped_terms == []
    assert run.empty_terms == []  # a fully-populated run has no dead seed


def test_discover_retry_recovers_a_transient_page():
    """A TRANSIENT page failure (fails once, succeeds on the retry pass) costs NOTHING: the universe is
    complete, coverage reads full, and — the point of the pass — a blip that would have blown the 5% default
    threshold no longer 503s the whole draft. The failure MODE (the degraded raise) is untouched; only its
    frequency drops."""
    fake = _FakeEfts(_pages_3(), fail_once={"efts/kw_2.json"})
    run = discover(
        fake, ["kw"], max_workers=4
    )  # default 5% ratio: pre-retry this WOULD have raised
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}  # nothing lost
    cov = run.coverage
    assert cov.pages_ok == cov.pages_attempted == 3
    assert (cov.retried, cov.recovered) == (1, 1)
    assert cov.failed_terms == []


def test_discover_retry_recovered_page0_fetches_its_deep_pages():
    """The silent-partial trap, pinned: a keyword whose PAGE-0 failed had NO offsets enumerated, so recovering
    page-0 on the retry pass must also fetch the deep pages the run still OWES — and count them in
    ``pages_attempted``. Recovering only page 0 would masquerade a 1/3 enumeration as complete."""
    fake = _FakeEfts(_pages_3(), fail_once={"efts/kw_0.json"})  # page-0 transiently fails
    run = discover(fake, ["kw"], max_workers=4)
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}  # deep pages fetched too
    cov = run.coverage
    assert cov.pages_attempted == 3  # page-0 + the 2 LATE offsets it owed
    assert cov.pages_ok == 3
    assert (cov.retried, cov.recovered) == (1, 1)
    assert "efts/kw_4.json" in fake.calls  # the deepest page really was fetched


def test_discover_reports_capped_term():
    """A keyword whose EFTS total exceeds the hit-cap is enumerated only to the cap — and that truncation is
    ON THE RECORD (#9 rule 4): the term lands in ``capped_terms`` (the draft report + the term-chip marker),
    and pages beyond the cap are genuinely not fetched."""
    fake = _FakeEfts(_pages_3())  # total=6 across 3 pages
    run = discover(fake, ["kw"], max_workers=4, hit_cap=4)  # cap below the total
    assert run.capped_terms == ["kw"]
    assert "efts/kw_4.json" not in fake.calls  # beyond-cap page NOT fetched
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 5)}  # pages 0 + 2 only
    cov = run.coverage
    assert cov.pages_ok == cov.pages_attempted == 2  # the capped enumeration itself was clean


def test_discover_reports_empty_term():
    """A seed that matches NO EDGAR filer (page-0 comes back with zero hits) is the too-FEW counterpart to a
    capped term — and, like capping, it goes ON THE RECORD (#9): the dead term lands in ``empty_terms`` instead
    of being silently discarded, while a live term never does. ``empty_terms`` preserves input order.
    """
    fake = _FakeEfts(_pages_3())  # 'kw' has hits; any other key -> the unknown-key empty page
    run = discover(fake, ["kw", "deadterm"], max_workers=4)
    assert run.empty_terms == ["deadterm"]  # the zero-hit seed is NAMED...
    assert "kw" not in run.empty_terms  # ...and the live term is not
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}  # live term's universe intact
    assert run.capped_terms == []  # dead, not capped — the orthogonal too-few / too-many axes


def test_discover_retry_recovered_empty_page0_reports_empty_term():
    """The retry-recovered zero-hit path: a term whose page-0 fails TRANSIENTLY and then comes back EMPTY on the
    retry pass is still a dead seed — it lands in ``empty_terms`` exactly as the phase-A branch does, so a blip
    on a dead term never hides that it placed no names. The live term's universe is untouched."""
    fake = _FakeEfts(_pages_3(), fail_once={"efts/deadterm_0.json"})  # page-0 blips, then empty
    run = discover(fake, ["kw", "deadterm"], max_workers=4)
    assert run.empty_terms == ["deadterm"]
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}  # live term's universe intact
    cov = run.coverage
    assert (cov.retried, cov.recovered) == (1, 1)  # the transient page-0 was retried and recovered
    assert cov.failed_terms == []  # recovered -> not a failed term (empty is a separate axis)


# --- the per-tier cap: SIGNAL deep / BROAD shallow (one loop, one cap site, the cap chosen per keyword) ---


def _cik(n: int) -> str:
    return f"{n:010d}"


def _cik_rows(*ids: int) -> list[tuple[str, str]]:
    """Invented single-CIK rows: ``Co<n> (T<n>) (CIK <n>)``."""
    return [(_cik(i), f"Co{i}  (T{i})  (CIK {_cik(i)})") for i in ids]


def _tier_pages() -> dict[str, dict]:
    """A SIGNAL-tier keyword ``sig`` (CIKs 11..16) and a BROAD-tier keyword ``brd`` (CIKs 21..26), each 3
    pages of 2 hits (total 6) -> cache keys sig_0/sig_2/sig_4 and brd_0/brd_2/brd_4. The CIK ranges are
    disjoint so which keyword — and how deep — a name came from is unambiguous. Neutral, invented names.
    """
    return {
        "efts/sig_0.json": _page(6, *_cik_rows(11, 12)),
        "efts/sig_2.json": _page(6, *_cik_rows(13, 14)),
        "efts/sig_4.json": _page(6, *_cik_rows(15, 16)),
        "efts/brd_0.json": _page(6, *_cik_rows(21, 22)),
        "efts/brd_2.json": _page(6, *_cik_rows(23, 24)),
        "efts/brd_4.json": _page(6, *_cik_rows(25, 26)),
    }


def test_discover_per_tier_caps_flag_each_tier_independently():
    """SIGNAL deep / BROAD shallow in ONE run: ``sig`` (total 6) over the SIGNAL cap 4 AND ``brd`` (total 6)
    over the BROAD cap 2 -> BOTH land in ``capped_terms`` (input order, one flat list — the chip renders per
    term inside its own tier list, so each surfaces independently, no wire change), each keyword's beyond-cap
    pages are genuinely NOT fetched, and there is ONE run-wide coverage (the caps change how far each keyword
    walks, never the accounting)."""
    fake = _FakeEfts(_tier_pages())
    run = discover(fake, ["sig", "brd"], hit_cap=4, broad={"brd"}, broad_hit_cap=2, max_workers=4)
    assert run.capped_terms == ["sig", "brd"]
    assert (
        "efts/sig_2.json" in fake.calls and "efts/sig_4.json" not in fake.calls
    )  # sig: to 4, not 6
    assert "efts/brd_2.json" not in fake.calls and "efts/brd_4.json" not in fake.calls  # brd: to 2
    assert set(run.filers) == {_cik(i) for i in (11, 12, 13, 14, 21, 22)}
    cov = run.coverage
    assert (
        cov.pages_ok == cov.pages_attempted == 3
    )  # 2 page-0s + sig's one in-cap offset, one accounting
    assert (cov.retried, cov.recovered, cov.failed_terms) == (0, 0, [])


def test_discover_deep_signal_page_survives_the_shallow_broad_cap():
    """THE POINT of the split: a name found ONLY on a deep SIGNAL page (offset 4 — beyond the BROAD cap, within
    the SIGNAL cap) IS enumerated, while the BROAD keyword at the same depth is not walked — the shallow cap
    bounds the collision-prone tier only, never a seed's deep hits (#9 rule 4)."""
    fake = _FakeEfts(_tier_pages())
    run = discover(fake, ["sig", "brd"], hit_cap=6, broad={"brd"}, broad_hit_cap=2, max_workers=4)
    assert _cik(16) in run.filers and run.filers[_cik(16)].keywords == {"sig"}  # the deep seed hit
    assert "efts/sig_4.json" in fake.calls
    assert (
        _cik(26) not in run.filers and "efts/brd_4.json" not in fake.calls
    )  # brd's deep page unsearched
    assert run.capped_terms == ["brd"]  # only the shallow tier hit its cap — the seed did not


def test_discover_broad_hit_cap_none_is_the_single_cap():
    """The back-compat pin: ``broad_hit_cap=None`` collapses the tiers — a keyword listed in ``broad``
    enumerates to ``hit_cap`` exactly as if ``broad`` had not been passed (same pages fetched, same universe,
    same capped list), so every existing single-cap caller is byte-identical."""
    tiered, plain = _FakeEfts(_tier_pages()), _FakeEfts(_tier_pages())
    a = discover(
        tiered, ["sig", "brd"], hit_cap=4, broad={"brd"}, broad_hit_cap=None, max_workers=4
    )
    b = discover(plain, ["sig", "brd"], hit_cap=4, max_workers=4)
    assert sorted(tiered.calls) == sorted(
        plain.calls
    )  # the fan-out order varies; the page SET must not
    assert {c: f.keywords for c, f in a.filers.items()} == {
        c: f.keywords for c, f in b.filers.items()
    }
    assert a.capped_terms == b.capped_terms == ["sig", "brd"]  # both capped at the ONE cap (4 < 6)
    assert (
        "efts/brd_2.json" in tiered.calls
    )  # the 'broad' keyword walked to hit_cap, not a shallow cap


def test_discover_per_tier_parallel_matches_the_sequential_reference():
    """The determinism gate under per-tier caps: the PARALLEL run equals the SEQUENTIAL per-keyword walk
    (``ciks_for_keyword``) given each keyword ITS OWN cap — the deep cap for the seed, the shallow cap for the
    broad keyword. Multi-page (25 hits over 3 pages of 10) so the fan-out AND the cap boundary are exercised,
    with a CIK shared across the two keywords (the union is exercised)."""
    pages = {
        "efts/sig_0.json": _page(25, *_cik_rows(*range(100, 110))),
        "efts/sig_10.json": _page(25, *_cik_rows(*range(110, 120))),
        "efts/sig_20.json": _page(25, *_cik_rows(*range(120, 125))),
        "efts/brd_0.json": _page(25, *_cik_rows(105, *range(200, 209))),  # 105 overlaps sig
        "efts/brd_10.json": _page(25, *_cik_rows(*range(210, 220))),
        "efts/brd_20.json": _page(25, *_cik_rows(*range(220, 225))),
    }
    par = discover(
        _FakeEfts(pages), ["sig", "brd"], hit_cap=25, broad={"brd"}, broad_hit_cap=10, max_workers=8
    )
    ref: dict[str, set[str]] = (
        {}
    )  # the sequential union, keyword-by-keyword, each under its own cap
    for kw, cap in (("sig", 25), ("brd", 10)):
        for cik in ciks_for_keyword(_FakeEfts(pages), kw, hit_cap=cap):
            ref.setdefault(cik, set()).add(kw)
    assert {
        c: f.keywords for c, f in par.filers.items()
    } == ref  # identical CIK set + keyword tagging
    assert par.filers[_cik(105)].keywords == {"sig", "brd"}  # the shared CIK tagged by both
    assert _cik(124) in par.filers  # the seed's deepest page is walked
    assert _cik(210) not in par.filers  # the broad keyword's beyond-cap pages — in NEITHER walk
    assert par.capped_terms == ["brd"]


def test_discover_retry_recovered_broad_page0_caps_its_late_offsets_shallow():
    """The retry pass honors the per-tier cap too: a BROAD keyword whose page-0 fails TRANSIENTLY is recovered
    on the retry pass, and the late offsets it then owes are bounded by the BROAD cap (offset 2 fetched, the
    beyond-cap offset 4 not) — the same one cap site, so the retry can never walk a shallow keyword deep. The
    seed beside it is untouched (deep cap, fully enumerated)."""
    fake = _FakeEfts(_tier_pages(), fail_once={"efts/brd_0.json"})
    run = discover(fake, ["sig", "brd"], hit_cap=6, broad={"brd"}, broad_hit_cap=4, max_workers=4)
    assert set(run.filers) == {_cik(i) for i in (11, 12, 13, 14, 15, 16, 21, 22, 23, 24)}
    assert "efts/brd_2.json" in fake.calls and "efts/brd_4.json" not in fake.calls
    assert run.capped_terms == ["brd"]
    cov = run.coverage
    assert (cov.retried, cov.recovered, cov.failed_terms) == (1, 1, [])
    assert (
        cov.pages_ok == cov.pages_attempted == 5
    )  # sig: 3 pages; brd: page-0 (retried) + 1 in-cap late


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


# --- a 2xx ERROR REPLY is a FAILED page, never a zero-hit one (#9) -------------------------------------
#
# MEASURED 2026-10-23 on prod: EFTS answered some pages with a success status and the body
# {"errorType": "ConnectionError", "errorMessage": "getaddrinfo EBUSY ...", ...} (no ``hits``). Read with
# ``.get`` defaults it was 0 rows / total 0 / page_size 0, so a live seed on page 0 went to ``empty_terms``,
# a deep page silently dropped its companies, and coverage read 81/81. It now raises ``EftsErrorBody``.

_ERROR_BODY = {
    "errorType": "ConnectionError",
    "errorMessage": "getaddrinfo EBUSY vpc-efts-prod-opensearchservice.example",
    "trace": ["ConnectionError: getaddrinfo EBUSY ..."],
}
_ZERO_HITS = {"hits": {"total": {"value": 0}, "hits": []}}  # what SEC really sends for no matches


class _ErrorBodyEfts(_FakeEfts):
    """Like ``_FakeEfts``, but a cache_key in ``error`` answers the 2xx ERROR BODY on every call (returned,
    not raised: exactly what the client handed back before the fix), and one in ``error_once`` answers it
    on its first call only."""

    def __init__(self, pages, *, error=None, error_once=None):
        super().__init__(pages)
        self.error = set(error or ())
        self.error_once = set(error_once or ())

    def get_json(self, url, cache_key, *, validate=None):
        if cache_key in self.error:
            self.calls.append(cache_key)
            return dict(_ERROR_BODY)
        if cache_key in self.error_once:
            self.error_once.discard(cache_key)
            self.calls.append(cache_key)
            return dict(_ERROR_BODY)
        return super().get_json(url, cache_key, validate=validate)


def _fillers(n: int) -> tuple[list[str], dict[str, dict]]:
    """``n`` clean one-page keywords (one invented CIK each, 900+), to set the degraded ratio's denominator."""
    kws = [f"filler{i}" for i in range(n)]
    pages = {f"efts/filler{i}_0.json": _page(1, *_cik_rows(900 + i)) for i in range(n)}
    return kws, pages


def _deep_pages() -> dict[str, dict]:
    """'kw' with 250 hits at EFTS's real page size of 100 -> pages from=0 / from=100 / from=200 (CIKs
    1..100, 101..200, 201..250)."""
    return {
        "efts/kw_0.json": _page(250, *_cik_rows(*range(1, 101))),
        "efts/kw_100.json": _page(250, *_cik_rows(*range(101, 201))),
        "efts/kw_200.json": _page(250, *_cik_rows(*range(201, 251))),
    }


@pytest.mark.parametrize(
    "body",
    [
        _ERROR_BODY,
        {},
        {"hits": []},  # hits is not an object
        {"hits": {"total": {"value": 3}}},  # no hits list
        {"hits": {"hits": None}},
        {"hits": {"hits": []}},  # no total
        {"hits": {"total": 3, "hits": []}},  # total not an object
        {"hits": {"total": {}, "hits": []}},  # no value
        {"hits": {"total": {"value": "3"}, "hits": []}},  # value not an int
        {"hits": {"total": {"value": True}, "hits": []}},  # a bool is not a count
        ["not", "an", "object"],
        "a string",
    ],
)
def test_check_efts_page_rejects_anything_but_a_search_results_page(body):
    with pytest.raises(EftsErrorBody):
        _check_efts_page(body)


def test_check_efts_page_accepts_a_real_page_and_a_real_zero_hit_page():
    _check_efts_page(_page(1, ("0000000001", "A  (CIK 0000000001)")))
    _check_efts_page(_ZERO_HITS)


def test_EftsErrorBody_is_an_InvalidBody_so_the_client_never_caches_it():
    assert issubclass(EftsErrorBody, InvalidBody)


def test_an_error_body_on_page0_is_RETRIED_and_recovered_never_reported_empty():
    """THE BUG, reproduced: a live seed whose page 0 came back as the error reply. Before the fix it read as
    zero hits -> ``empty_terms``, its deep pages never fetched. Now page 0 FAILS, the retry pass recovers
    it, and the deep pages it owed are fetched: nothing lost, nothing mislabeled."""
    fake = _ErrorBodyEfts(_pages_3(), error_once={"efts/kw_0.json"})
    run = discover(fake, ["kw"], max_workers=4)
    assert run.empty_terms == []
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}  # deep pages too
    cov = run.coverage
    assert cov.pages_ok == cov.pages_attempted == 3
    assert (cov.retried, cov.recovered, cov.failed_terms) == (1, 1, [])


def test_an_error_body_on_page0_EVERY_attempt_is_a_FAILED_term_not_an_empty_one():
    """Still bad after the retry: within tolerance the term is NAMED in ``failed_terms`` (the draft's
    honesty strip), never filed as a dead seed."""
    fill, pages = _fillers(20)
    fake = _ErrorBodyEfts({**_pages_3(), **pages}, error={"efts/kw_0.json"})
    run = discover(fake, ["kw", *fill], max_workers=4, degraded_ratio=0.10)  # 1/21 < 10%
    assert run.empty_terms == []
    cov = run.coverage
    assert cov.failed_terms == ["kw"]
    assert (cov.pages_ok, cov.pages_attempted) == (20, 21)
    assert (cov.retried, cov.recovered) == (1, 0)
    assert fake.calls.count("efts/kw_0.json") == 2  # the first pass + the one retry


def test_an_error_body_on_page0_every_attempt_over_the_threshold_is_DiscoveryDegraded():
    fake = _ErrorBodyEfts(_pages_3(), error={"efts/kw_0.json"})
    with pytest.raises(DiscoveryDegraded) as exc:
        discover(fake, ["kw"], max_workers=4)  # 1/1 > the 5% default
    assert (exc.value.failed, exc.value.attempted) == (1, 1)


def test_an_error_body_on_a_DEEP_page_is_a_counted_failed_page_not_a_silent_loss():
    """The silent half of the bug: page from=100 came back as the error reply. Before the fix it added zero
    rows and nothing was flagged (CIKs 101..200 vanished under a 100% coverage line). Now it is a FAILED
    page: retried, then counted and the term named; the pages around it still union."""
    fill, pages = _fillers(10)
    fake = _ErrorBodyEfts({**_deep_pages(), **pages}, error={"efts/kw_100.json"})
    run = discover(fake, ["kw", *fill], max_workers=4, degraded_ratio=0.10)  # 1/13 < 10%
    cov = run.coverage
    assert cov.failed_terms == ["kw"]
    assert (cov.pages_ok, cov.pages_attempted) == (12, 13)
    assert (cov.retried, cov.recovered) == (1, 0)
    assert {_cik(i) for i in (1, 100, 201, 250)} <= set(run.filers)  # pages 0 and 200 survive
    assert (
        _cik(150) not in run.filers
    )  # the failed page's CIKs are absent, and that is ON THE RECORD
    assert run.empty_terms == []


def test_an_error_body_on_a_deep_page_over_the_threshold_is_DiscoveryDegraded():
    fake = _ErrorBodyEfts(_deep_pages(), error={"efts/kw_100.json"})
    with pytest.raises(DiscoveryDegraded) as exc:
        discover(fake, ["kw"], max_workers=4)  # 1/3 > 5%
    assert (exc.value.failed, exc.value.attempted) == (1, 3)


def test_a_REAL_zero_hit_page_is_still_an_empty_term():
    """``empty_terms`` keeps its meaning: SEC really returned zero hits (a VALID page with no hits)."""
    fake = _ErrorBodyEfts({**_pages_3(), "efts/deadterm_0.json": _ZERO_HITS})
    run = discover(fake, ["kw", "deadterm"], max_workers=4)
    assert run.empty_terms == ["deadterm"]
    assert run.coverage.failed_terms == []
    assert run.coverage.pages_ok == run.coverage.pages_attempted == 4


class _OverrideOnceEfts(_FakeEfts):
    """``_FakeEfts`` whose first call for a key in ``once`` returns that body instead of the real page."""

    def __init__(self, pages, *, once=None, always=None):
        super().__init__(pages)
        self.once = dict(once or {})
        self.always = dict(always or {})

    def get_json(self, url, cache_key, *, validate=None):
        if cache_key in self.always:
            self.calls.append(cache_key)
            return self.always[cache_key]
        if cache_key in self.once:
            self.calls.append(cache_key)
            return self.once.pop(cache_key)
        return super().get_json(url, cache_key, validate=validate)


def test_a_page_with_hits_but_NO_TOTAL_is_a_failed_page_not_a_silent_truncation():
    """Pagination reads ``hits.total``. A page 0 with hits but no total used to read total 0: its own rows
    merged, its deeper pages were never asked for, and the term was neither empty nor failed (a silent
    truncation, the error reply's sibling). Now it is a FAILED page like any other bad body."""
    no_total = {"hits": {"hits": _pages_3()["efts/kw_0.json"]["hits"]["hits"]}}  # 2 hits, no total

    # persistent: the term is NAMED (failed_terms), never empty, and the gap is on the record
    fill, pages = _fillers(20)
    fake = _OverrideOnceEfts({**_pages_3(), **pages}, always={"efts/kw_0.json": no_total})
    run = discover(fake, ["kw", *fill], max_workers=4, degraded_ratio=0.10)  # 1/21 < 10%
    assert run.coverage.failed_terms == ["kw"] and run.empty_terms == []
    assert (run.coverage.pages_ok, run.coverage.pages_attempted) == (20, 21)
    assert "efts/kw_2.json" not in fake.calls  # not fetched, and the coverage line says so

    # transient: the retry gets the real page, so the deeper pages it owes ARE fetched
    fake = _OverrideOnceEfts(_pages_3(), once={"efts/kw_0.json": no_total})
    run = discover(fake, ["kw"], max_workers=4)
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}
    assert {"efts/kw_2.json", "efts/kw_4.json"} <= set(fake.calls)
    assert (run.coverage.retried, run.coverage.recovered, run.coverage.failed_terms) == (1, 1, [])


def test_the_exact_real_zero_hit_body_through_the_real_client_is_still_an_empty_term(tmp_path):
    """The literal body SEC sends for no matches, read from the cache by a real ``EdgarClient``, passes
    the stricter page check and still files the seed under ``empty_terms`` (not failed)."""
    (tmp_path / "efts").mkdir()
    (tmp_path / "efts" / "deadterm_0.json").write_text(
        '{"hits":{"total":{"value":0},"hits":[]}}', encoding="utf-8"
    )
    for key, page in _pages_3().items():
        (tmp_path / key).write_text(json.dumps(page), encoding="utf-8")
    run = discover(
        EdgarClient(cache_dir=tmp_path, allow_live=False), ["kw", "deadterm"], max_workers=4
    )
    assert run.empty_terms == ["deadterm"]
    assert run.coverage.failed_terms == []
    assert run.coverage.pages_ok == run.coverage.pages_attempted == 4


# --- the same, end to end through the REAL EdgarClient (its cache in a tmp dir; ``_fetch`` stubbed) ----


def _edgar(tmp_path, answers: dict[str, list[dict]], *, allow_live: bool = True):
    """A real ``EdgarClient`` over ``tmp_path`` whose network answers, per cache-key stem (``kw_0``), the
    listed bodies in order (the last repeats). Returns it plus the per-stem fetch count."""
    counts: dict[str, int] = {}
    client = EdgarClient(cache_dir=tmp_path, allow_live=allow_live, user_agent="test ua")

    def _fetch(url: str) -> str:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        stem = f"{q['q'][0].strip(chr(34))}_{q['from'][0]}"
        counts[stem] = counts.get(stem, 0) + 1
        bodies = answers[stem]
        return json.dumps(bodies[min(counts[stem], len(bodies)) - 1])

    client._fetch = _fetch  # type: ignore[method-assign]
    return client, counts


def test_through_the_real_client_the_retry_REFETCHES_instead_of_rereading_a_cached_error(tmp_path):
    """Why the validator runs inside the client: had only ``_fetch_page`` raised, the error body would
    already be on disk and the retry pass would re-read it from the cache (failing for the whole 12h TTL).
    The client never wrote it, so the retry goes back to SEC and gets the real page, which IS cached.
    """
    pages = _pages_3()
    client, counts = _edgar(
        tmp_path,
        {
            "kw_0": [_ERROR_BODY, pages["efts/kw_0.json"]],
            "kw_2": [pages["efts/kw_2.json"]],
            "kw_4": [pages["efts/kw_4.json"]],
        },
    )
    run = discover(client, ["kw"], max_workers=4)
    assert counts["kw_0"] == 2  # rejected, then re-fetched by the retry (not served from the cache)
    assert run.empty_terms == [] and run.coverage.failed_terms == []
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}
    cached = json.loads((tmp_path / "efts" / "kw_0.json").read_text(encoding="utf-8"))
    assert cached == pages["efts/kw_0.json"]
    assert client.live_completed == 3  # the rejected body was never a completion


def test_through_the_real_client_a_POISONED_cache_file_is_refetched_when_live(tmp_path):
    """A page cached BEFORE this fix (the 8 prod files of 2026-10-23) is a miss, not a dead seed."""
    pages = _pages_3()
    poisoned = tmp_path / "efts" / "kw_0.json"
    poisoned.parent.mkdir(parents=True)
    poisoned.write_text(json.dumps(_ERROR_BODY), encoding="utf-8")  # fresh by the TTL
    client, counts = _edgar(
        tmp_path,
        {
            "kw_0": [pages["efts/kw_0.json"]],
            "kw_2": [pages["efts/kw_2.json"]],
            "kw_4": [pages["efts/kw_4.json"]],
        },
    )
    run = discover(client, ["kw"], max_workers=4)
    assert counts["kw_0"] == 1 and run.empty_terms == []
    assert set(run.filers) == {f"{i:010d}" for i in range(1, 7)}
    assert "errorType" not in poisoned.read_text(encoding="utf-8")  # overwritten by the real page


def test_through_the_real_client_OFFLINE_a_poisoned_page_is_a_failed_term_not_an_empty_one(
    tmp_path,
):
    (tmp_path / "efts").mkdir()
    (tmp_path / "efts" / "kw_0.json").write_text(json.dumps(_ERROR_BODY), encoding="utf-8")
    (tmp_path / "efts" / "ok_0.json").write_text(
        json.dumps(_page(1, *_cik_rows(42))), encoding="utf-8"
    )
    client = EdgarClient(cache_dir=tmp_path, allow_live=False)
    run = discover(client, ["kw", "ok"], max_workers=4, degraded_ratio=0.6)  # 1/2 < 60%
    assert run.coverage.failed_terms == ["kw"]
    assert run.empty_terms == []
    assert set(run.filers) == {_cik(42)}
