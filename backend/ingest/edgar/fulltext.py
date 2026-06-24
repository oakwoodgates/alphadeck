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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

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


def _fetch_page(
    client: _JsonClient, keyword: str, frm: int
) -> tuple[list[tuple[str, str, str | None]], int, int]:
    """Fetch ONE EFTS page (``&from=frm``) for ``keyword`` -> ``(rows, total, page_size)``. Each row is
    ``(cik, name, ticker)`` (a filing's CIKs x its parsed display); ``total`` is EFTS's reported hit count;
    ``page_size`` is this page's hit count (0 = past the end). Cache-first via ``client.get_json`` with the
    SAME ``cache_key`` as the sequential walk, so the parallel fan-out reuses the same cached pages.
    """
    q = urllib.parse.quote(f'"{keyword}"')
    data = client.get_json(f"{_EFTS_URL}?q={q}&from={frm}", _cache_key(keyword, frm))
    hits = data.get("hits", {}).get("hits", [])
    rows: list[tuple[str, str, str | None]] = []
    for h in hits:
        src = h.get("_source", {})
        name, ticker = _parse_display((src.get("display_names") or [""])[0])
        for cik in src.get("ciks", []):
            rows.append((cik, name, ticker))
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    return rows, total, len(hits)


def _merge_rows(
    uni: dict[str, Filer], keyword: str, rows: list[tuple[str, str, str | None]]
) -> None:
    """Union one page's rows into the CIK->Filer map: a new CIK seeds a Filer (first-seen name/ticker — best-
    effort DISPLAY only, the CIK is the identity), an existing one just adds the keyword. Order-independent on
    the CIK SET + the keyword tagging (what the precision filter scores); name/ticker is cosmetic.
    """
    for cik, name, ticker in rows:
        f = uni.get(cik)
        if f is None:
            uni[cik] = Filer(cik=cik, name=name, ticker=ticker, keywords={keyword})
        else:
            f.keywords.add(keyword)


def ciks_for_keyword(
    client: _JsonClient, keyword: str, *, hit_cap: int = 1000
) -> dict[str, tuple[str, str | None]]:
    """``{cik: (name, ticker)}`` for the US filers whose filings mention ``keyword`` — the SEQUENTIAL per-keyword
    walk, kept as the determinism reference for the parallel ``discover``. Paginated (``&from=N``), CAPPED at
    ``hit_cap`` filing-hits — a pathological-keyword BACKSTOP, not a recall limiter (on-thesis filers file
    repeatedly and hit several keywords, so they surface early / under signal keywords; a low cap silently drops
    real names that surface deep — measured in the Slice-1 gate). The CIK is read from ``_source.ciks``.
    """
    out: dict[str, tuple[str, str | None]] = {}
    frm = 0
    while frm < hit_cap:
        rows, total, page_size = _fetch_page(client, keyword, frm)
        if page_size == 0:
            break
        for cik, name, ticker in rows:
            out.setdefault(cik, (name, ticker))
        frm += page_size
        if frm >= total:
            break
    return out


def discover(
    client: _JsonClient,
    keywords: Iterable[str],
    *,
    hit_cap: int = 1000,
    max_workers: int = 8,
) -> dict[str, Filer]:
    """Run EFTS over the keyword set and union the distinct CIKs, each tagged with which keywords hit it — the
    RAW universe (high recall, PRE-filter; call ``precision_filter`` / ``classify`` next).

    PARALLEL but rate-bounded: the per-keyword pages fan out over a ``max_workers`` thread pool, yet every
    ``get_json`` funnels through the ONE shared ``EdgarClient`` -> the ONE ``RateLimiter`` (the SEC fair-access
    budget is GLOBAL, not per-request), so concurrency removes the per-request-latency serialization WITHOUT
    exceeding the limit. Two phases: (A) page 0 of every keyword -> read ``total`` + the real ``page_size``;
    (B) fan out all remaining offsets. ``ThreadPoolExecutor.map`` yields in INPUT order, so the merge order is
    fixed run-to-run; the CIK set + keyword tagging are order-independent -> identical to the sequential walk
    (``ciks_for_keyword``, the gate's determinism reference). ``hit_cap`` is the per-keyword backstop.
    """
    kws = list(dict.fromkeys(k for k in keywords if k))  # de-dup, preserve order, drop blanks
    if not kws:
        return {}
    uni: dict[str, Filer] = {}
    # Phase A: page 0 of every keyword, concurrently (map preserves keyword order in its output).
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        page0 = list(ex.map(lambda kw: (kw, _fetch_page(client, kw, 0)), kws))
    offsets: list[tuple[str, int]] = []
    for kw, (rows, total, page_size) in page0:
        _merge_rows(uni, kw, rows)
        if page_size == 0:
            continue
        limit = min(total, hit_cap)
        offsets.extend((kw, frm) for frm in range(page_size, limit, page_size))
    # Phase B: every remaining page, concurrently (map preserves offsets order -> deterministic merge).
    if offsets:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for kw, (rows, _total, _ps) in ex.map(
                lambda o: (o[0], _fetch_page(client, o[0], o[1])), offsets
            ):
                _merge_rows(uni, kw, rows)
    return uni


def precision_filter(filers: dict[str, Filer], *, signal: Iterable[str]) -> dict[str, Filer]:
    """Keep a filer iff it hit ``>=2`` DISTINCT keywords OR ``>=1`` SIGNAL keyword — dropping the
    abbreviation-collision noise while keeping the on-thesis names. (Gate-2 Check 1 scans what this DROPS for
    real on-thesis names, since a false NEGATIVE is silent.) The richer ``classify`` adds the VERIFY tier so a
    real-but-low-signal adjacent isn't silently lost."""
    sig = set(signal)
    return {cik: f for cik, f in filers.items() if len(f.keywords) >= 2 or (f.keywords & sig)}


@dataclass
class Discovery:
    """The classified EFTS universe as ``security_id``s, by tier (the resolver placed each by exact CIK
    membership — INVARIANT #2, the cleanest form).

    - ``placed`` — high-confidence (>=2 keywords OR >=1 SIGNAL keyword), in-master.
    - ``verify`` — LOWER-confidence (in-master, a single BROAD keyword, no signal): the gate-2 "ketamine is
      broad" drops (ALKS/BTAI/OVID) surfaced for the operator to promote, NEVER mixed into ``placed`` (a single
      keyword auto-treated as on-thesis is the homonym trap; same discipline as AMBIGUOUS).

    A filer not in the master is omitted (foreign / no US ticker -> the LLM tail-sweep's job)."""

    placed: dict[str, UUID]  # cik -> security_id
    verify: dict[str, UUID]  # cik -> security_id


def classify(
    filers: dict[str, Filer],
    *,
    in_master_ids: dict[str, UUID],
    signal: Iterable[str],
    broad: Iterable[str],
) -> Discovery:
    """Classify discover()'d filers into the PLACED / VERIFY tiers, using the keyword tiers + the resolved
    in-master ids (``master.ids_for_ciks``). Only in-master CIKs are placeable; a not-in-master on-thesis name
    is the tail-sweep's job (Slice 3/4), omitted here.

    PLACED = in-master AND (>=2 distinct keywords OR >=1 SIGNAL). VERIFY = in-master AND not placed AND hits
    >=1 BROAD keyword (a single broad hit, no signal) — surfaced lower-confidence, kept SEPARATE from placed
    so a single match never auto-places (#2: discovery proposes, the operator decides)."""
    sig, brd = set(signal), set(broad)
    placed: dict[str, UUID] = {}
    verify: dict[str, UUID] = {}
    for cik, f in filers.items():
        sid = in_master_ids.get(cik)
        if sid is None:
            continue  # not in the master -> not placeable here (the tail-sweep covers the foreign tail)
        if len(f.keywords) >= 2 or (f.keywords & sig):
            placed[cik] = sid
        elif f.keywords & brd:
            verify[cik] = sid
    return Discovery(placed=placed, verify=verify)
