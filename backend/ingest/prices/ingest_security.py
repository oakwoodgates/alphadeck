"""The per-security price leg — ONE implementation, two callers (the decoupling the finalize screen needed).

Moved verbatim out of ``pipeline.ingest_thesis._price_leg`` so the price ingest is callable OUTSIDE the
per-thesis back-half loop: the Workbench's finalize screen pulls bars per name / per section BEFORE the
operator promotes (real market caps + live archetype hints inform the finalize decisions), while
``pipeline.ingest_thesis`` and the daily cron keep calling the same function inside their loops. One
implementation means the invariants can't fork:

- **Incremental** — only bars newer than the latest stored one are appended; a re-run of a current name
  appends ZERO rows (the append-only table never silently grows; COUNT-the-table guarded).
- **Cache-first** — ``force_refresh=False`` (the interactive default) serves a same-day re-click from the
  cache; a FIRST pull on a fresh name is a cache miss and fetches live. The recurring/daily path passes
  ``force_refresh=True`` (the #72 stale-cache rule) — that dial stays the caller's.
- **No-lookahead** — ``recorded_at`` stays the DB default ``now()``; nothing is backdated.

Price bars are FEED data (like Form 4s), not operator-ratified facts — the recommend→confirm seam does
not apply to them; what they feed (market cap, the archetype hint) stays display/recommendation until the
operator acts.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from domain.security import Security
from ingest.prices.eod_loader import ingest_prices, latest_bar_date
from ingest.prices.source import PriceSource, YahooPriceSource


def ingest_bars_for_security(
    conn: psycopg.Connection,
    sec: Security,
    *,
    tenant_id: UUID,
    allow_live: bool = True,
    force_refresh: bool = False,
    source: PriceSource | None = None,
) -> int:
    """Ingest only EOD bars newer than the latest stored bar for this security (incremental). Returns the
    count appended. Reads bars through the injected ``PriceSource`` (the seam), so the source is swappable;
    ``force_refresh`` makes the recurring path bypass a stale cache hit (see ``eod_loader.fetch_eod``).
    A ticker-less security contributes no bars (there is no listed line to quote)."""
    if not sec.ticker:
        return 0
    src = source or YahooPriceSource()
    last = latest_bar_date(conn, sec.id, tenant_id=tenant_id)
    rows = [
        r
        for r in src.get_bars(sec.ticker, allow_live=allow_live, force_refresh=force_refresh)
        if last is None or r["d"] > last
    ]
    return ingest_prices(conn, sec.id, rows, tenant_id=tenant_id)
