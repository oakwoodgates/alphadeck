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
- **RE-VERSION on restatement (source-strategy Option A, operator pick 2026-07-11)** — Yahoo re-bases the
  WHOLE history on every split, while this ingest is incremental, so a name splitting mid-thesis used to
  accumulate mixed-basis stored bars (old-basis history + new-basis appends → a false cliff for the
  breakout detector, a mis-graded volume gate). Now the fresh pull's OVERLAP with stored history is
  compared per date: where the fresh bar differs beyond float noise, a NEW VERSION is appended (same
  ``d``, a new ``recorded_at`` — the bitemporal store's native move; the as-of read's DISTINCT ON picks
  the latest, so the series snaps to the new basis within one pass, price AND volume). A replay with a
  ``known_at`` BEFORE the re-version still sees the old basis — transaction-time honesty, exactly what
  the bitemporal store exists for. The mixed-basis window is at most one cron tick.

Price bars are FEED data (like Form 4s), not operator-ratified facts — the recommend→confirm seam does
not apply to them; what they feed (market cap, the archetype hint) stays display/recommendation until the
operator acts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

import psycopg

from domain.security import Security
from ingest.prices.eod_loader import ingest_prices, latest_bar_date, stored_bars
from ingest.prices.source import PriceSource, YahooPriceSource


@dataclass(frozen=True)
class BarsResult:
    """The price leg's receipt: ``appended`` = bars newer than the latest stored (the incremental
    tail); ``reversioned`` = overlap bars re-stored because the source RESTATED them (a split
    re-base) or backfilled a hole — the exceptional path, surfaced loudly only when nonzero."""

    appended: int
    reversioned: int

    @property
    def total(self) -> int:
        return self.appended + self.reversioned


def _num(x: object) -> float | None:
    return None if x is None else float(x)  # numeric columns arrive as Decimal; fresh bars as float


def _restated(fresh: dict, stored: dict) -> bool:
    """A restatement = close or volume moved beyond float noise. A split re-base moves both ~10×;
    repr/Decimal round-trip jitter is ~1e-12 — rel_tol 1e-9 separates them cleanly. Close + volume
    are the fields the detectors read (the breakout close, the volume-confirmation gate); OHL cannot
    be re-based without close moving, so they don't need their own compare."""
    for field in ("close", "volume"):
        a, b = _num(fresh.get(field)), _num(stored.get(field))
        if a is None and b is None:
            continue
        if a is None or b is None:
            return True
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            return True
    return False


def ingest_bars_for_security(
    conn: psycopg.Connection,
    sec: Security,
    *,
    tenant_id: UUID,
    allow_live: bool = True,
    force_refresh: bool = False,
    source: PriceSource | None = None,
) -> BarsResult:
    """Ingest this security's EOD bars: the incremental tail (newer than the latest stored bar) plus
    the RE-VERSION pass over the overlap (restated bars get a new version; see the module docstring).
    Reads bars through the injected ``PriceSource`` (the seam); ``force_refresh`` makes the recurring
    path bypass a stale cache hit. A ticker-less security contributes no bars. Idempotent by
    construction: a second run over the same fresh series appends the tail (now empty) and finds no
    overlap diffs — COUNT-the-table guarded. The caller owns the transaction."""
    if not sec.ticker:
        return BarsResult(0, 0)
    src = source or YahooPriceSource()
    fresh = src.get_bars(sec.ticker, allow_live=allow_live, force_refresh=force_refresh)
    last = latest_bar_date(conn, sec.id, tenant_id=tenant_id)

    appended = ingest_prices(
        conn,
        sec.id,
        [r for r in fresh if last is None or r["d"] > last],
        tenant_id=tenant_id,
    )

    # the re-version pass: only meaningful when the fresh pull overlaps stored history (a cache-first
    # re-click returns the very series already ingested → zero diffs → a no-op)
    reversioned = 0
    if last is not None:
        overlap = [r for r in fresh if r["d"] <= last]
        if overlap:
            stored = stored_bars(conn, sec.id, tenant_id=tenant_id)
            restated = [
                r
                for r in overlap
                # unseen d inside the overlap = a hole the source backfilled — store it (new info,
                # not a restatement, but the same honest append)
                if (prior := stored.get(r["d"])) is None or _restated(r, prior)
            ]
            reversioned = ingest_prices(conn, sec.id, restated, tenant_id=tenant_id)

    return BarsResult(appended, reversioned)
