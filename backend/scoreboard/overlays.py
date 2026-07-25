"""Pure + as-of overlay helpers for the drawer chart (Slice A): per-bar SMA context and the
window's code-P open-market insider buys.

Two additions ride the ``price-window`` response, both under the SAME no-lookahead discipline as the
bars themselves (``docs/CALL_LOGIC.md`` / invariant #1):

- ``annotate_sma`` — a PURE rolling mean over the realized closes, computed with a warm-up read so the
  window's LEFT edge is honest (the value at ``start`` sees the ~200 prior closes) and ``None`` where too
  little history exists (never back-padded). It reads no clock and no DB — the router hands it the
  asof-capped bars ``PgRealizedPrices.bars_between`` already returns (never a forked as-of path).
- ``episode_insider_buys`` — the window's individual open-market purchases, read via the shared
  bitemporal ``as_of`` (latest version per natural key, so a corrected row never double-counts) and
  screened by the EXACT open-market definition the NamePanel's insider-flow display signal uses
  (``_is_open_market_buy``), so the chart's dots reconcile with the panel's net-flow figure. Code-P is
  the floor; the screen only sets aside offer-price subscriptions / implausible rows (recall-safe, #9).

The transaction axis is the load-bearing bit: a buy is positioned by ``valid_from`` (the transaction
date) and GATED by ``recorded_at <= known_at`` — we only surface what we'd have KNOWN by ``known_at``.
``known_at_for_asof`` caps that at the request's as-of so a scrubbed-back Scoreboard hides not just later
bars but later-DISCLOSED buys (the IBM "ingested 166d after its event date" case), exactly the honesty a
forward reader owes.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import as_of
from signals.display.insider_flow import BUY, _is_open_market_buy

# Warm-up buffer read BEHIND the window start so the leftmost returned bar carries an honest SMA200
# (needs ~200 prior TRADING days; 300 calendar days ≈ 205-215 trading days clears it). A young security
# or a short ingested history simply yields ``None`` at the left — the gap is honest, not padded.
SMA_WARMUP_DAYS = 300
SMA_WINDOWS: tuple[int, ...] = (50, 200)

# The overlay's RELEVANCE floor (Slice A R1): events older than the thesis-existed window are off-story —
# a thesis born 2026-07 does not plot a 2020 insider buy. This is NOT a recall cut (#9): the excluded
# events are genuinely PRE-THESIS. The floor is ``max(created_at − 365d, first_bar)`` — a year of run-up
# before the thesis was created, never earlier than the first bar we hold.
UNIVERSE_LOOKBACK_DAYS = 365


def _f(x: Any) -> float | None:
    """Coerce a nullable numeric column (``Decimal``/``None``) to ``float``/``None`` (the prices twin)."""
    return float(x) if x is not None else None


def thesis_created_at(conn: psycopg.Connection, thesis_id: UUID) -> date:
    """The thesis's ``created_at`` DATE (the ``thesis`` table is single-row operational, not bitemporal).
    Read directly here — ``get_thesis_or_404`` returns the domain ``Thesis`` (which carries no ``created_at``),
    so the router pulls the one column it needs for the relevance floor without widening the domain model.
    The row exists (the caller already loaded the thesis)."""
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM thesis WHERE id = %s", [thesis_id])
        row = cur.fetchone()
    return row["created_at"].date()


def universe_floor(created_at: date, first_bar: date | None) -> date:
    """The overlay event floor (R1): ``max(created_at − 365d, first_bar)``. A thesis older than its price
    history floors at the first bar (nothing to plot before we have prices); a thesis younger than its
    history floors at ``created_at − 365d`` (a year of run-up, not the whole tape). ``first_bar`` None
    (no bars yet) → the created floor stands. Pure."""
    created_floor = created_at - timedelta(days=UNIVERSE_LOOKBACK_DAYS)
    return created_floor if first_bar is None else max(created_floor, first_bar)


def annotate_sma(bars: list[dict[str, Any]], windows: tuple[int, ...] = SMA_WINDOWS) -> list[dict]:
    """Annotate each bar with trailing simple moving averages of ``close`` — PURE, no clock, no DB.

    ``bars`` must be ascending by date, each carrying a numeric ``close`` (the shape ``bars_between``
    returns). For each window ``w`` a ``sma{w}`` field is written: the mean of the trailing ``w`` closes
    INCLUDING the bar, or ``None`` where fewer than ``w`` closes precede it (the honest LEFT-edge gap —
    never back-padded, never invented). Returns NEW dicts; the input list is untouched. The router calls
    it with the default ``(50, 200)`` (→ ``sma50``/``sma200``); tests pass small windows for readability.
    """
    closes = [b["close"] for b in bars]
    out: list[dict] = []
    for i, b in enumerate(bars):
        row = dict(b)
        for w in windows:
            row[f"sma{w}"] = sum(closes[i + 1 - w : i + 1]) / w if i + 1 >= w else None
        out.append(row)
    return out


def known_at_for_asof(asof: date, now: datetime | None = None) -> datetime:
    """The transaction-axis cap for the insider read: ``min(now, end-of-asof-day)``.

    A LIVE view (``asof`` today / future) reads at ``now`` — everything disclosed by this moment. A
    scrubbed-back ``asof`` caps ``known_at`` at that day's end, so a buy DISCLOSED (``recorded_at``) after
    the as-of is absent — the two-axis no-lookahead a forward reader owes (invariant #1). Distinct from the
    price read, which stays at ``now`` (a price bar's ``valid_from == d``, so its valid-axis cap already
    carries the honesty; an insider filing lags its transaction by days-to-months, so its transaction axis
    must cap too).
    """
    now = now or datetime.now(timezone.utc)
    asof_eod = datetime.combine(asof, time.max, tzinfo=timezone.utc)
    return min(now, asof_eod)


def episode_insider_buys(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    start: date,
    end: date,
    asof: date,
    known_at: datetime,
    day_lows: dict[date, float],
) -> list[dict[str, Any]]:
    """The window's code-P open-market insider buys, as-of-capped on both axes.

    Read via the shared bitemporal ``as_of`` (``valid_from <= asof`` on the valid axis, ``recorded_at <=
    known_at`` on the transaction axis, and the LATEST version per natural key — so a corrected/superseded
    row never double-counts). Then: keep code-P only, keep transactions inside the drawn window
    ``[start, min(end, asof)]`` (``asof`` already caps the read; ``end`` bounds a matured episode whose
    exit_by is in the past), and apply the NamePanel's open-market screen (``_is_open_market_buy``) so a
    dot on the chart is a dot in the panel's net-flow. ``day_lows`` (trade-date → EOD low, from the SAME
    asof-capped price view) drives the offer-price screen; an absent low keeps the buy (recall-safe, #9).

    Each row: ``d`` = ``valid_from`` (the chip's x, the transaction date), ``disclosed`` =
    ``recorded_at::date`` (the tooltip's honest disclosure lag), plus who / role / shares / $ / the
    10b5-1 plan flag. Sorted by transaction date (then disclosure) — the frontend numbers chronologically.
    """
    rows = as_of(
        conn,
        "fact_insider_txn",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
    )
    buys: list[dict[str, Any]] = []
    for r in rows:
        if r.get("txn_code") != BUY:  # code 'P' — open-market OR private purchase
            continue
        vf: date = r["valid_from"]
        if vf < start or vf > end:  # position window (end bounds a matured, past-exit episode)
            continue
        if not _is_open_market_buy(
            r, day_lows
        ):  # sets aside offer-price / implausible rows (#9-named)
            continue
        buys.append(
            {
                "d": vf,
                "insider_name": r.get("insider_name"),
                "insider_role": r.get("insider_role"),
                "shares": _f(r.get("shares")),
                "usd": _f(r.get("usd")),
                "aff_10b5_1": r.get("aff_10b5_1"),
                "disclosed": r["recorded_at"].date(),
            }
        )
    buys.sort(key=lambda b: (b["d"], b["disclosed"]))
    return buys
