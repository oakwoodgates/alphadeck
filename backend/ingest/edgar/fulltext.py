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

import logging
import re
import urllib.parse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Scoped to the discovery path (the codebase otherwise avoids logging); WARNINGs propagate to uvicorn's root
# handler, so a skipped page / a degraded run is VISIBLE in `docker compose logs` — never a silent gap.
_log = logging.getLogger("alphadeck.discovery")


class DiscoveryUnavailable(RuntimeError):
    """Discovery could not produce a trustworthy universe — the draft must FAIL VISIBLY (the endpoint maps this
    to HTTP 503) rather than silently fall back to model recall. The base of the discovery failure modes.
    """


class DiscoveryDegraded(DiscoveryUnavailable):
    """Too large a fraction of EFTS pages failed to fetch (AFTER ``polite_get``'s retries): the run could not
    enumerate the universe, so it must NOT be returned as if complete. Completeness-or-fail."""

    def __init__(self, failed: int, attempted: int) -> None:
        self.failed, self.attempted = failed, attempted
        self.ratio = failed / attempted if attempted else 1.0
        super().__init__(
            f"discovery degraded: {failed}/{attempted} EFTS pages failed ({self.ratio:.0%}) after retries"
        )


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


@dataclass(frozen=True)
class DiscoveryCoverage:
    """How much of the universe one ``discover`` run actually enumerated — the #9 rule-2/3 instrument. Every
    run carries it (a clean run reads ``pages_ok == pages_attempted``, ``retried == 0``), and the draft
    surfaces it to the operator, so a sub-threshold gap is VISIBLE on the surface, not just in a log line.
    """

    pages_ok: int  # pages fetched = attempted − still-failed (post-retry)
    pages_attempted: int  # phase A + phase B + the late offsets a retry-recovered page-0 owed
    failed_terms: list[
        str
    ]  # distinct keywords with >=1 page still failed after the retry pass (input order)
    retried: int  # pages given the second sweep (0 on a clean run)
    recovered: int  # of those, how many succeeded


@dataclass
class DiscoveryRun:
    """``discover``'s result: the enumerated universe + the run's own honesty report. ``capped_terms`` names
    every keyword whose EFTS total exceeded the hit-cap — pages beyond the cap were NOT enumerated, so a name
    surfacing only that deep is invisible this run (#9 rule 4: the cap is a pathology backstop, and hitting
    it goes on the record, never silent)."""

    filers: dict[str, Filer]
    coverage: DiscoveryCoverage
    capped_terms: list[str] = field(default_factory=list)


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
    degraded_ratio: float = 0.05,
) -> DiscoveryRun:
    """Run EFTS over the keyword set and union the distinct CIKs, each tagged with which keywords hit it — the
    RAW universe (high recall, PRE-filter; call ``precision_filter`` / ``classify`` on ``.filers`` next) plus
    the run's own honesty report (``.coverage`` + ``.capped_terms``).

    PARALLEL but rate-bounded: the per-keyword pages fan out over a ``max_workers`` thread pool, yet every
    ``get_json`` funnels through the ONE shared ``EdgarClient`` -> the ONE ``RateLimiter`` (the SEC fair-access
    budget is GLOBAL, not per-request), so concurrency removes the per-request-latency serialization WITHOUT
    exceeding the limit. Two phases: (A) page 0 of every keyword -> read ``total`` + the real ``page_size``;
    (B) fan out all remaining offsets. ``ThreadPoolExecutor.map`` yields in INPUT order, so the merge order is
    fixed run-to-run; the CIK set + keyword tagging are order-independent -> identical to the sequential walk
    (``ciks_for_keyword``, the gate's determinism reference). ``hit_cap`` is the per-keyword backstop, and
    HITTING it is recorded (``capped_terms`` + a WARNING), never silent.

    COMPLETENESS-OR-FAIL (the reliability contract): a page that fails AFTER ``polite_get``'s retries is
    logged (keyword + offset) and skipped, then given ONE retry pass at the end — same client, same rate
    limiter, the same global politeness budget (a recovered page-0 also owes its never-enumerated deep pages;
    they are fetched inside the same pass). The retry reduces the failure FREQUENCY only; the failure MODE
    stays loud (#9 rule 5): if the still-failed fraction exceeds ``degraded_ratio`` the run raises
    ``DiscoveryDegraded`` (with post-retry counts), and a within-tolerance gap rides ``coverage`` onto the
    draft report — a run that couldn't fetch part of the universe NEVER presents itself as the whole.
    """
    kws = list(dict.fromkeys(k for k in keywords if k))  # de-dup, preserve order, drop blanks
    if not kws:
        return DiscoveryRun(filers={}, coverage=DiscoveryCoverage(0, 0, [], 0, 0))

    def _safe(keyword: str, frm: int, sink: list[tuple[str, int]]):
        try:
            return _fetch_page(client, keyword, frm)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — a page that fails AFTER polite_get's retries is persistent;
            # log + skip it (never nuke the run), and count it toward the degraded threshold below.
            _log.warning(
                "discovery: EFTS page failed after retries; keyword=%r from=%d: %s",
                keyword,
                frm,
                exc,
            )
            sink.append((keyword, frm))
            return None

    capped: set[str] = set()

    def _offsets_for(kw: str, total: int, page_size: int) -> list[tuple[str, int]]:
        # The keyword's remaining pages after a successful page-0 — the ONE site the cap applies, so the
        # capped flag is detected here (no extra fetch: EFTS already reported ``total``).
        if total > hit_cap:
            capped.add(kw)
            _log.warning(
                "discovery: keyword %r hit-capped: total=%d > hit_cap=%d — pages beyond the cap NOT enumerated",
                kw,
                total,
                hit_cap,
            )
        limit = min(total, hit_cap)
        return [(kw, frm) for frm in range(page_size, limit, page_size)]

    failed: list[tuple[str, int]] = []
    uni: dict[str, Filer] = {}
    # Phase A: page 0 of every keyword, concurrently (map preserves keyword order in its output).
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        page0 = list(ex.map(lambda kw: (kw, _safe(kw, 0, failed)), kws))
    offsets: list[tuple[str, int]] = []
    for kw, res in page0:
        if res is None:  # page-0 failed -> the keyword is unenumerable until the retry pass below
            continue
        rows, total, page_size = res
        _merge_rows(uni, kw, rows)
        if page_size == 0:
            continue
        offsets.extend(_offsets_for(kw, total, page_size))
    # Phase B: every remaining page, concurrently (map preserves offsets order -> deterministic merge).
    if offsets:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for kw, res in ex.map(lambda o: (o[0], _safe(o[0], o[1], failed)), offsets):
                if res is not None:
                    _merge_rows(uni, kw, res[0])

    base_attempted = len(kws) + len(offsets)
    retried = len(failed)
    recovered = 0
    still_failed: list[tuple[str, int]] = []
    late_offsets: list[tuple[str, int]] = []
    if failed:
        # ONE retry pass over the failed subset — the same client -> the same RateLimiter -> polite_get's own
        # per-request retries, so the pass spends the same global politeness budget (no new dial). It reduces
        # the failure FREQUENCY only; the failure MODE stays loud (the post-retry degraded raise below).
        _log.warning("discovery: retrying %d failed EFTS pages (one pass)", retried)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            retry = list(ex.map(lambda o: (o[0], o[1], _safe(o[0], o[1], still_failed)), failed))
        for kw, frm, res in retry:
            if res is None:
                continue
            recovered += 1
            rows, total, page_size = res
            _merge_rows(uni, kw, rows)
            if frm == 0 and page_size > 0:
                # A recovered page-0's deep pages were never enumerated in Phase B — the run still OWES them
                # (recovering only page 0 would be a silent partial). Fetch them inside the same pass; they
                # are first attempts (each still gets polite_get's internal retries), never a further pass.
                late_offsets.extend(_offsets_for(kw, total, page_size))
        if late_offsets:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for kw, res in ex.map(
                    lambda o: (o[0], _safe(o[0], o[1], still_failed)), late_offsets
                ):
                    if res is not None:
                        _merge_rows(uni, kw, res[0])

    attempted = base_attempted + len(late_offsets)
    if attempted and len(still_failed) / attempted > degraded_ratio:
        raise DiscoveryDegraded(len(still_failed), attempted)
    if (
        still_failed
    ):  # within tolerance, but NEVER silent — the gap rides coverage onto the draft report
        _log.warning(
            "discovery: completed with %d/%d pages skipped after the retry pass (within the %.0f%% tolerance): %s",
            len(still_failed),
            attempted,
            degraded_ratio * 100,
            still_failed[:20],
        )
    failed_kws = {kw for kw, _ in still_failed}
    return DiscoveryRun(
        filers=uni,
        coverage=DiscoveryCoverage(
            pages_ok=attempted - len(still_failed),
            pages_attempted=attempted,
            failed_terms=[k for k in kws if k in failed_kws],
            retried=retried,
            recovered=recovered,
        ),
        capped_terms=[k for k in kws if k in capped],
    )


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

    - ``placed`` — high-confidence: in-master AND hits >=1 SIGNAL (a seed — an operator-specified compound).
    - ``verify`` — LOWER-confidence (in-master, no signal, hits >=1 BROAD keyword — any count): the broad-only
      adjacents (ALKS/JUNS/PRTG-class) surfaced for the operator to promote, NEVER mixed into ``placed`` (an
      LLM-driven broad match auto-treated as on-thesis is the homonym/corroboration trap; discovery proposes,
      the operator decides — #2).

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

    PLACED = in-master AND hits >=1 SIGNAL (a seed — an operator-specified compound). VERIFY = in-master AND no
    signal AND hits >=1 BROAD keyword (ANY broad-only count, 1 or more) — surfaced lower-confidence, kept
    SEPARATE from placed so an LLM-driven broad match never auto-places (#2: discovery proposes, the operator
    decides).

    SEEDS-ONLY-PLACE (the deterministic-PLACED rule): SIGNAL is the operator's seeds alone, so PLACED means
    exactly "hit a compound the operator specified" — DETERMINISTIC run-to-run (fixed seeds x deterministic
    EFTS) and clean. The OLD ">=2 distinct keywords -> PLACED" clause was dropped: with an LLM-PROPOSED broad
    set it placed corroborated names NON-deterministically (PLACED swung 96->184 on the same seeds across runs)
    for a +3 answer-key gain those names already had in VERIFY. Broad corroboration is a real signal — it
    belongs in "show me these" (VERIFY, visible + operator-promotable), NOT "auto-trust these" (PLACED),
    precisely because it is LLM-driven. Nothing is dropped; the split moves from PLACED to VERIFY.
    """
    sig, brd = set(signal), set(broad)
    placed: dict[str, UUID] = {}
    verify: dict[str, UUID] = {}
    for cik, f in filers.items():
        sid = in_master_ids.get(cik)
        if sid is None:
            continue  # not in the master -> not placeable here (the tail-sweep covers the foreign tail)
        if f.keywords & sig:  # >=1 SEED (operator-specified compound) -> high-confidence PLACED
            placed[cik] = sid
        elif f.keywords & brd:  # broad-only (any count, LLM-driven) -> VERIFY, never auto-placed
            verify[cik] = sid
    return Discovery(placed=placed, verify=verify)
