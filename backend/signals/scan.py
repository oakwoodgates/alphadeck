from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.signal import SignalEvent
from signals import insider_conviction, volume_breakout
from signals.base import PointInTimeData


@dataclass
class Candidate:
    security_id: UUID
    ticker: str | None
    conviction: SignalEvent | None  # Key 1 (insider)
    confirmation: SignalEvent | None  # Key 2 (breakout)

    @property
    def both_keys(self) -> bool:
        return self.conviction is not None and self.confirmation is not None

    @property
    def conviction_score(self) -> float:
        return self.conviction.score if self.conviction else 0.0


def rank_candidates(
    conn: psycopg.Connection,
    securities: list[tuple[UUID, str | None]],
    asof: date,
    *,
    cfg: CallConfig = DEFAULT_CONFIG,
    known_at: datetime | None = None,
) -> list[Candidate]:
    """Run both detectors per security as-of, ranking by conviction and flagging where BOTH keys fire.

    The M3 target is a name where both keys fire (conviction warms, confirmation arms) — those sort
    first. Reads only what was knowable at (asof, known_at); no lookahead.
    """
    pit = PointInTimeData(conn, asof=asof, known_at=known_at)
    candidates = [
        Candidate(
            security_id=sid,
            ticker=ticker,
            conviction=insider_conviction.detect(pit, sid, asof, cfg),
            confirmation=volume_breakout.detect(pit, sid, asof, cfg),
        )
        for sid, ticker in securities
    ]
    candidates.sort(key=lambda c: (c.both_keys, c.conviction_score), reverse=True)
    return candidates
