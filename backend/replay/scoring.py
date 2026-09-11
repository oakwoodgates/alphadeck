from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID

from db.session import DEFAULT_TENANT_ID
from replay.schema import Episode, Outcome

if TYPE_CHECKING:  # duckdb is the optional .[replay] extra, annotation-only in this module — the
    import duckdb  # live Scoreboard imports the scorer in the lean prod image (no duckdb installed).

# The SCORING pass. RealizedPrices is a FORWARD-windowed reader with NO as-of / known_at cap — the
# deliberate opposite of ReplayPointInTimeData (which is as-of-capped). It is the ONLY reader the scorer
# uses, and the scorer takes NO pit, so forward data can never reach an as-of call (the lookahead boundary).
# This module MUST NOT import replay.pit (an import-graph test enforces it).


def _f(x: Any) -> float | None:
    """Coerce a nullable numeric OHLCV column to ``float``/``None`` (the twin of ``scoreboard.prices._f``)."""
    return float(x) if x is not None else None


class RealizedPrices:
    """Realized EOD closes from the same frozen Parquet mirror, read FORWARD (no asof/known_at bound) —
    the latest version per ``(security_id, d)`` (a price correction's final value; harmless on the seed,
    which has none). Used only by the scorer, never the replay loop."""

    def __init__(
        self, con: duckdb.DuckDBPyConnection, *, tenant_id: UUID = DEFAULT_TENANT_ID
    ) -> None:
        self.con = con
        self.tenant_id = tenant_id

    def _closes(self, security_id: UUID, where: str, params: list) -> list[tuple[date, float]]:
        rows = self.con.execute(
            f"SELECT d, close FROM fact_price_eod "
            f"WHERE tenant_id = ? AND security_id = ? {where} "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY security_id, d ORDER BY recorded_at DESC, id DESC) = 1 "
            f"ORDER BY d",
            [str(self.tenant_id), str(security_id), *params],
        ).fetchall()
        return [(r[0], float(r[1])) for r in rows if r[1] is not None]

    def first_close_on_or_after(self, security_id: UUID, d: date) -> tuple[date, float] | None:
        rows = self._closes(security_id, "AND d >= ?", [d])
        return rows[0] if rows else None

    def last_close_through(self, security_id: UUID, through: date) -> tuple[date, float] | None:
        rows = self._closes(security_id, "AND d <= ?", [through])
        return rows[-1] if rows else None

    def closes_between(self, security_id: UUID, start: date, end: date) -> list[tuple[date, float]]:
        return self._closes(security_id, "AND d >= ? AND d <= ?", [start, end])

    def _bars(self, security_id: UUID, where: str, params: list) -> list[dict]:
        # The OHLCV twin of ``_closes`` — the SAME dedup (latest version per ``(security_id, d)``) and the
        # SAME deliberate absence of any asof/known_at bound: the scoring reader is forward-unbounded by
        # design and this method copies THIS side's shape, never the Postgres twin's double cap (the two
        # readers are duck-typed peers, not a harmonised pair — see scoreboard/prices.py::_bars).
        # Null-CLOSE rows are skipped (parity with ``_closes``); the other OHLCV columns stay nullable
        # per-column — a close-only free-EOD bar surfaces ``None``, never an invented number.
        rows = self.con.execute(
            f"SELECT d, open, high, low, close, volume FROM fact_price_eod "
            f"WHERE tenant_id = ? AND security_id = ? {where} "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY security_id, d ORDER BY recorded_at DESC, id DESC) = 1 "
            f"ORDER BY d",
            [str(self.tenant_id), str(security_id), *params],
        ).fetchall()
        return [
            {
                "d": r[0],
                "open": _f(r[1]),
                "high": _f(r[2]),
                "low": _f(r[3]),
                "close": float(r[4]),
                "volume": _f(r[5]),
            }
            for r in rows
            if r[4] is not None
        ]

    def bars_between(self, security_id: UUID, start: date, end: date) -> list[dict]:
        """Full OHLCV bars over ``[start, end]`` — the mirroring twin of
        ``scoreboard.prices.PgRealizedPrices.bars_between``, and the ONE read behind the scorer's close
        pair (peak/trough), wick pair (intraday high/low) and sparkline path."""
        return self._bars(security_id, "AND d >= ? AND d <= ?", [start, end])


def _extreme(
    window: list[dict], key: str, pick: Callable[..., dict]
) -> tuple[date | None, float | None]:
    """The ``(date, price)`` of the window's extreme on ONE OHLCV column — **all-or-nothing**: a single
    bar missing that column returns ``(None, None)``, never a max over the bars that happen to carry it.

    The field answers *"what is the most extreme price this actually traded at during the window?"* If one
    bar's extremes are unknown, the true extreme could be INSIDE that bar, so an extreme over the rest is
    not conservative — it is wrong, and silently so (#6). A wick field therefore never falls back to the
    close. On ``close`` the check is vacuous (the reader skips null-close rows), so ``trough_return`` is
    available exactly whenever ``peak_return`` is. ``max``/``min`` keep the FIRST bar on a tie, as the
    close-only peak always did."""
    if not window or any(b[key] is None for b in window):
        return (None, None)
    best = pick(window, key=lambda b: b[key])
    return (best["d"], best[key])


def _base_outcome(ep: Episode) -> Outcome:
    return Outcome(
        thesis_id=ep.thesis_id,
        security_id=ep.security_id,
        is_headline=ep.is_headline,
        verdict=ep.verdict,
        entry_grade=ep.entry_grade,
        conviction_grade=ep.conviction_grade,
        confidence=ep.confidence,
        theme_armed=ep.theme_armed,
        close_reason=ep.close_reason,
        arm_date=ep.arm_date,
        exit_by=ep.exit_by,
    )


def score_episode(ep: Episode, realized: RealizedPrices) -> Outcome:
    """Score one arm episode over its OWN hold horizon ``[arm_date, exit_by]`` on realized closes. The exit
    is the system's own ``exit_by`` (the honest yardstick); if it runs past the data, the return is measured
    to the last bar IN THAT WINDOW and ``truncated`` is set. ``warm_return`` (from the warm date) feeds the
    edge-preservation metric; ``peak_*`` feed the exit-by-vs-rollover metric.

    The window is read ONCE as full OHLCV bars, which serves five things off one query: the close-based
    excursion pair (``peak_*`` MFE / ``trough_*`` MAE), the wick-based pair (``intraday_high_*`` /
    ``intraday_low_*``) and the ``path`` the ledger's sparkline draws. Excursions and path are DESCRIPTIVE
    — nothing in ``replay.metrics`` reads them, so adding them moves no metric."""
    out = _base_outcome(ep)
    sid = ep.security_id
    entry = realized.first_close_on_or_after(sid, ep.arm_date)
    if entry is None or ep.exit_by is None:
        return out.model_copy(update={"insufficient_prices": True})
    _, entry_close = entry

    # ONE OHLC read over the scored window, replacing the close-only ``closes_between``: the same rows,
    # the same query count, four more columns. The window list IS the sparkline's path — the two are the
    # same read, not two reads that happen to agree.
    #
    # It is ALSO the EXIT. The exit is the last bar OF THE SCORED WINDOW, never (as it was) the last bar
    # anywhere <= exit_by — a read unbounded BELOW. The two are identical whenever the window holds a bar;
    # they diverge only when the last bar <= exit_by PRECEDES arm_date, and there the old read paired a
    # LATER entry with an EARLIER exit and measured the return backwards in time. That is not theoretical:
    # two episodes on the record arm on a Sunday with exit_by the SAME Sunday (market_today() does no
    # weekend skip by design, so a Sunday backfill records a Sunday as-of), so the entry read Monday's
    # close and the exit read the preceding Friday's — -4.33% and +3.32%, the negation of a real move,
    # one of them inside the live metrics. Taking the exit from the window makes that impossible by
    # construction, and an empty window then has no exit to report: insufficient_prices is the honest
    # answer, not a signed number (the render sites label a MATURED empty window for what it is).
    window = realized.bars_between(sid, ep.arm_date, ep.exit_by)
    if not window or entry_close == 0:
        return out.model_copy(update={"entry_close": entry_close, "insufficient_prices": True})
    exit_date, exit_close = window[-1]["d"], window[-1]["close"]
    peak_date, peak_close = _extreme(window, "close", max)
    trough_date, trough_close = _extreme(window, "close", min)
    high_date, high_px = _extreme(window, "high", max)
    low_date, low_px = _extreme(window, "low", min)

    # The de-arm's place on the path, composed HERE so the FE never re-derives it from a date list it
    # does not receive (the ``ingest_note`` / ``dearm_detail`` precedent: one authority for the answer).
    # ``None`` when the de-arm fell OUTSIDE the scored window — the run outlived its own horizon — which
    # reads as "no marker", never as "never de-armed"; the copy at the render site says which.
    dearm_index: int | None = None
    if ep.dearm_date is not None and window and ep.dearm_date <= window[-1]["d"]:
        on_path = [i for i, b in enumerate(window) if b["d"] <= ep.dearm_date]
        dearm_index = on_path[-1] if on_path else None

    warm_close = None
    if ep.warm_date is not None:
        warm = realized.first_close_on_or_after(sid, ep.warm_date)
        warm_close = warm[1] if warm else None

    arm_until_close = None
    if ep.arm_until is not None:
        au = realized.last_close_through(sid, ep.arm_until)
        arm_until_close = au[1] if au else None

    return out.model_copy(
        update={
            "entry_close": entry_close,
            "exit_close": exit_close,
            "exit_date": exit_date,
            "forward_return": exit_close / entry_close - 1,
            "arm_until_return": (arm_until_close / entry_close - 1) if arm_until_close else None,
            "warm_return": (exit_close / warm_close - 1) if warm_close else None,
            "peak_return": (peak_close / entry_close - 1) if peak_close else None,
            "peak_date": peak_date,
            "trough_return": (trough_close / entry_close - 1) if trough_close else None,
            "trough_date": trough_date,
            "intraday_high_return": (high_px / entry_close - 1) if high_px else None,
            "intraday_high_date": high_date,
            "intraday_low_return": (low_px / entry_close - 1) if low_px else None,
            "intraday_low_date": low_date,
            "path": [b["close"] for b in window],
            "dearm_index": dearm_index,
            "exit_vs_peak_days": (exit_date - peak_date).days if peak_date else None,
            "truncated": exit_date < ep.exit_by,  # the hold horizon ran past the available data
        }
    )


def score_episodes(episodes: list[Episode], realized: RealizedPrices) -> list[Outcome]:
    return [score_episode(ep, realized) for ep in episodes]
