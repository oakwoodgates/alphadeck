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
    """Resolve ``sec``'s vendor price symbol: search (ticker → name fallback), probe history, decide. A
    ticker-less security cannot be resolved (NONE). Search failures propagate (the caller's fail-open wraps
    them); history probes are guarded internally. Returns a proposal — never writes."""
    if not sec.ticker:
        return PriceSymbolProposal(
            tier="NONE", proposed_symbol=None, why="no listed ticker — nothing to resolve"
        )

    ticker_search = search_quotes(
        sec.ticker, cache_dir=symbol_cache_dir, allow_live=allow_live, force_refresh=force_refresh
    )
    matches = us_equity_name_matches(ticker_search, sec.name)
    name_search = None
    if not matches and sec.name:
        name_search = search_quotes(
            sec.name, cache_dir=symbol_cache_dir, allow_live=allow_live, force_refresh=force_refresh
        )
        matches = us_equity_name_matches(name_search, sec.name)

    # probe history for the CANONICAL ticker + each candidate (minus a self-match), so the decider can confirm
    tkr = sec.ticker.upper()
    canonical_bars = _bar_count(
        tkr, price_cache_dir=price_cache_dir, allow_live=allow_live, force_refresh=force_refresh
    )
    candidate_bars = {
        m: _bar_count(
            m, price_cache_dir=price_cache_dir, allow_live=allow_live, force_refresh=force_refresh
        )
        for m in matches
        if m != tkr
    }

    return propose_price_symbol(
        ticker=sec.ticker,
        name=sec.name,
        ticker_search=ticker_search,
        name_search=name_search,
        canonical_bars=canonical_bars,
        candidate_bars=candidate_bars,
    )
