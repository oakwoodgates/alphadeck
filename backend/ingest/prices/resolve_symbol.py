"""The price-symbol resolver's RUNTIME — the I/O orchestration around the pure decider.

Ties the two live legs (Yahoo symbol search + the history probe) to the pure ``propose_price_symbol``:

1. Search by the SEC ticker; on no US-equity name match, fall back to a NAME search (VREOD's ticker-search
   is empty; "Vireo Growth" → VREOF).
2. Probe history depth via ``fetch_eod`` — the CANONICAL ticker's bar count and each candidate's — so the
   pure decider can confirm the resolved symbol carries materially longer history. Every probe is GUARDED
   (a ``CacheMiss`` / network error → 0 bars = unverified), so a probe failure downgrades AUTO→FLAG, never
   crashes the resolution.
3. Hand the raw payloads + bar counts to ``propose_price_symbol`` and return its proposal.

Read-only: this NEVER writes the master — the caller (the finalize price seam #2, or the backfill CLI)
decides whether to ``master.set_price_symbol`` on an AUTO. Operator-triggered only (never the cron): cost is
the operator's to spend, never ambient.
"""

from __future__ import annotations

from pathlib import Path

from domain.security import Security
from ingest.prices.eod_loader import fetch_eod
from ingest.prices.symbol_search import search_quotes
from securities.price_symbol import (
    PriceSymbolProposal,
    propose_price_symbol,
    shortened_name_queries,
    us_equity_name_matches,
)


def _bar_count(
    symbol: str,
    *,
    price_cache_dir: Path | None,
    allow_live: bool,
    force_refresh: bool,
) -> int:
    """The history depth for a symbol (``fetch_eod`` bar count), GUARDED: an uncached-offline miss or a
    live fetch error yields 0 (unverified), never an exception — a probe never crashes the resolution.
    """
    try:
        return len(
            fetch_eod(
                symbol,
                cache_dir=price_cache_dir,
                allow_live=allow_live,
                force_refresh=force_refresh,
            )
        )
    except (
        Exception
    ):  # noqa: BLE001 — any probe failure (CacheMiss / network) is just "unverified" (0 bars)
        return 0


def resolve_price_symbol(
    sec: Security,
    *,
    allow_live: bool,
    symbol_cache_dir: Path | None = None,
    price_cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> PriceSymbolProposal:
    """Resolve ``sec``'s vendor price symbol: search (ticker → full name → shortened-name fallbacks), probe
    history, decide. A ticker-less security cannot be resolved (NONE). Search failures propagate (the
    caller's fail-open wraps them); history probes are guarded internally. Returns a proposal — never writes.

    The shortened-name fallback is a RECALL aid ONLY: when both the ticker and the FULL legal name search
    miss the US listing (Yahoo returns nothing usable for "Curaleaf Holdings, Inc." but does for "Curaleaf"),
    it issues progressively shorter name queries. Every result runs the SAME strict ``us_equity_name_matches``
    gate against the FULL master name — a shorter query only widens what Yahoo returns, it never loosens what
    counts as a match, so precision is unchanged (a wrong company still can't pass; two genuine matches still
    FLAG)."""
    if not sec.ticker:
        return PriceSymbolProposal(
            tier="NONE", proposed_symbol=None, why="no listed ticker — nothing to resolve"
        )

    tkr = sec.ticker.upper()

    def _matches(search) -> list[str]:
        return [m for m in us_equity_name_matches(search, sec.name) if m != tkr]

    ticker_search = search_quotes(
        sec.ticker, cache_dir=symbol_cache_dir, allow_live=allow_live, force_refresh=force_refresh
    )
    matches = _matches(ticker_search)
    name_search = None
    extra_searches: list = []
    if not matches and sec.name:
        name_search = search_quotes(
            sec.name, cache_dir=symbol_cache_dir, allow_live=allow_live, force_refresh=force_refresh
        )
        matches = _matches(name_search)
    if not matches and sec.name:
        # both the ticker and the FULL name missed — try progressively SHORTER name queries (recall only);
        # skip any query already searched (equal to the ticker or the full name).
        already = {tkr, sec.name.strip().upper()}
        for query in shortened_name_queries(sec.name):
            if query.strip().upper() in already:
                continue
            already.add(query.strip().upper())
            shortened = search_quotes(
                query,
                cache_dir=symbol_cache_dir,
                allow_live=allow_live,
                force_refresh=force_refresh,
            )
            extra_searches.append(shortened)
            matches = _matches(shortened)
            if matches:
                break

    # probe history for the CANONICAL ticker + each winning candidate, so the decider can confirm longer history
    canonical_bars = _bar_count(
        tkr, price_cache_dir=price_cache_dir, allow_live=allow_live, force_refresh=force_refresh
    )
    candidate_bars = {
        m: _bar_count(
            m, price_cache_dir=price_cache_dir, allow_live=allow_live, force_refresh=force_refresh
        )
        for m in matches
    }

    return propose_price_symbol(
        ticker=sec.ticker,
        name=sec.name,
        ticker_search=ticker_search,
        name_search=name_search,
        extra_searches=extra_searches,
        canonical_bars=canonical_bars,
        candidate_bars=candidate_bars,
    )
