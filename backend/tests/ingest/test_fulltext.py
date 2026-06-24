"""The EDGAR full-text discovery enumerator (Slice 1) — parse / paginate / union / filter / determinism, with
a fake EFTS client. No network, no DB. The live measurements (Check 1 dropped-set scan, Check 2 CIK-population,
the pagination cap) are gate-2, run against real EFTS + the master."""

from __future__ import annotations

from ingest.edgar.fulltext import (
    Filer,
    _parse_display,
    ciks_for_keyword,
    discover,
    precision_filter,
)


class _FakeEfts:
    """Returns canned EFTS JSON by cache_key; an unknown key -> an empty page (terminates pagination)."""

    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get_json(self, url, cache_key):
        self.calls.append(cache_key)
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
