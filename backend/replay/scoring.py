from __future__ import annotations

from datetime import date
from uuid import UUID

import duckdb

from db.session import DEFAULT_TENANT_ID
from replay.schema import Episode, Outcome

# The SCORING pass. RealizedPrices is a FORWARD-windowed reader with NO as-of / known_at cap — the
# deliberate opposite of ReplayPointInTimeData (which is as-of-capped). It is the ONLY reader the scorer
# uses, and the scorer takes NO pit, so forward data can never reach an as-of call (the lookahead boundary).
# This module MUST NOT import replay.pit (an import-graph test enforces it).


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
    to the last bar and ``truncated`` is set. ``warm_return`` (from the warm date) feeds the
    edge-preservation metric; ``peak_*`` feed the exit-by-vs-rollover metric."""
    out = _base_outcome(ep)
    sid = ep.security_id
    entry = realized.first_close_on_or_after(sid, ep.arm_date)
    if entry is None or ep.exit_by is None:
        return out.model_copy(update={"insufficient_prices": True})
    _, entry_close = entry
    exit_pt = realized.last_close_through(sid, ep.exit_by)
    if exit_pt is None or entry_close == 0:
        return out.model_copy(update={"entry_close": entry_close, "insufficient_prices": True})
    exit_date, exit_close = exit_pt

    window = realized.closes_between(sid, ep.arm_date, ep.exit_by)
    peak_date, peak_close = max(window, key=lambda dc: dc[1]) if window else (None, None)

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
            "exit_vs_peak_days": (exit_date - peak_date).days if peak_date else None,
            "truncated": exit_date < ep.exit_by,  # the hold horizon ran past the available data
        }
    )


def score_episodes(episodes: list[Episode], realized: RealizedPrices) -> list[Outcome]:
    return [score_episode(ep, realized) for ep in episodes]
