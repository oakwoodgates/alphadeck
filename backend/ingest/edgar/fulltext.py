"""EDGAR full-text search (EFTS) — the deterministic discovery enumerator (Slice 1).

Queries ``efts.sec.gov`` for every US filer whose filings mention a thesis's keywords and returns distinct
**CIKs** (+ the current name / ticker). DETERMINISTIC (an index query — re-running returns the same set),
CIK-keyed (no ticker-guessing, no rebrand/DBA problem — the CIK is the stable identity), and FREE. Discovery
only PROPOSES — the resolver + EXACT CIK membership decide (INVARIANT #2); nothing here sources a number (#3).

The precision filter (``>=2`` distinct keywords OR ``>=1`` SIGNAL keyword) drops the abbreviation-collision
noise (a miner that says "DMT" once, a retailer that says "LSD" once, a utility that says "MDMA") while keeping
the on-thesis names, which mention MANY of a theme's terms. A bake-off measured the separation: real names hit
5-11 keywords, collision noise hits one. The filter is FREE + deterministic — no LLM classifier.

Reaches EFTS through the existing ``EdgarClient`` (cache-first, polite, declared User-Agent). The keyword set is
supplied by the caller (a fixed list now; the per-thesis LLM keyword-gen is Slice 2).
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"


class _JsonClient(Protocol):
    def get_json(self, url: str, cache_key: str) -> dict[str, Any]: ...


@dataclass
class Filer:
    """One US filer EFTS surfaced for a theme: a stable CIK + its current name/ticker + which keywords hit it
    (the keyword overlap the precision filter scores)."""

    cik: str
    name: str
    ticker: str | None
    keywords: set[str] = field(default_factory=set)


# "NAME  (TICKER[, TICKER2])  (CIK 000...)" — capture the ticker group that immediately precedes the CIK group.
_TICKER_RE = re.compile(r"\(([A-Z0-9]{1,6}(?:,\s?[A-Z0-9]{1,6})*)\)\s*\(CIK")


def _parse_display(display_name: str) -> tuple[str, str | None]:
    """A ``display_names[0]`` -> (company name, first ticker or None). The name is everything before the first
    ``"  ("`` parenthetical group (the SEC formats both the ticker and the CIK as ``"  (...)"`` groups). Ticker
    is best-effort display only — the CIK (read from ``_source.ciks``, never parsed) is the identity.
    """
    name = display_name.split("  (")[0].strip()
    m = _TICKER_RE.search(display_name)
    ticker = m.group(1).split(",")[0].strip() if m else None
    return name, ticker


def _cache_key(keyword: str, frm: int) -> str:
    return f"efts/{re.sub(r'[^A-Za-z0-9_-]', '_', keyword)}_{frm}.json"


def ciks_for_keyword(
    client: _JsonClient, keyword: str, *, hit_cap: int = 1000
) -> dict[str, tuple[str, str | None]]:
    """``{cik: (name, ticker)}`` for the US filers whose filings mention ``keyword`` (the exact phrase).

    Paginated (``&from=N``) and CAPPED at ``hit_cap`` filing-hits. The cap is a MEASURED choice, not an
    assumption (gate-2): on-thesis filers file repeatedly and surface early; deep pages are mostly noise. The
    many filings of one filer dedup to its single CIK. The CIK is read from ``_source.ciks`` (never parsed).
    """
    out: dict[str, tuple[str, str | None]] = {}
    frm = 0
    while frm < hit_cap:
        q = urllib.parse.quote(f'"{keyword}"')
        data = client.get_json(f"{_EFTS_URL}?q={q}&from={frm}", _cache_key(keyword, frm))
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            src = h.get("_source", {})
            name, ticker = _parse_display((src.get("display_names") or [""])[0])
            for cik in src.get("ciks", []):
                out.setdefault(cik, (name, ticker))
        frm += len(hits)
        if frm >= data.get("hits", {}).get("total", {}).get("value", 0):
            break
    return out


def discover(
    client: _JsonClient, keywords: Iterable[str], *, hit_cap: int = 1000
) -> dict[str, Filer]:
    """Run EFTS over the keyword set and union the distinct CIKs, each tagged with which keywords hit it. The
    RAW universe — high recall, PRE-filter (call ``precision_filter`` next)."""
    uni: dict[str, Filer] = {}
    for kw in keywords:
        for cik, (name, ticker) in ciks_for_keyword(client, kw, hit_cap=hit_cap).items():
            f = uni.get(cik)
            if f is None:
                uni[cik] = Filer(cik=cik, name=name, ticker=ticker, keywords={kw})
            else:
                f.keywords.add(kw)
    return uni


def precision_filter(filers: dict[str, Filer], *, signal: Iterable[str]) -> dict[str, Filer]:
    """Keep a filer iff it hit ``>=2`` DISTINCT keywords OR ``>=1`` SIGNAL keyword — dropping the
    abbreviation-collision noise while keeping the on-thesis names. (Gate-2 Check 1 scans what this DROPS for
    real on-thesis names, since a false NEGATIVE is silent.)"""
    sig = set(signal)
    return {cik: f for cik, f in filers.items() if len(f.keywords) >= 2 or (f.keywords & sig)}
