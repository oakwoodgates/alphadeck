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


def discover(
    conn: psycopg.Connection,
    tickers: list[str],
    asof: date,
    *,
    cfg: CallConfig = DEFAULT_CONFIG,
    user_agent: str | None = None,
) -> list[Candidate]:
    """LIVE discovery (network-bound): resolve + ingest each ticker's Form 4s and prices, then rank.

    The operational tool for picking the M3 target. Requires ALPHADECK_USER_AGENT (SEC etiquette)
    and caches aggressively. Not exercised by the test suite (offline tests cover rank_candidates).
    """
    from ingest.edgar.client import EdgarClient
    from ingest.edgar.form4 import ingest_form4
    from ingest.edgar.submissions import fetch_submissions, form4_doc_url, form4_filings
    from ingest.prices.eod_loader import fetch_eod, ingest_prices
    from securities import master

    client = EdgarClient(allow_live=True, user_agent=user_agent)
    securities: list[tuple[UUID, str | None]] = []
    for ticker in tickers:
        sec = master.resolve(conn, ticker, allow_live=True)
        securities.append((sec.id, sec.ticker))
        if sec.cik:
            for f in form4_filings(fetch_submissions(client, sec.cik)):
                url = form4_doc_url(sec.cik, f["accession"], f["primary_doc"])
                doc = f["primary_doc"].rsplit("/", 1)[-1]
                xml = client.get_text(url, f"forms/{f['accession']}/{doc}")
                ingest_form4(conn, sec.id, xml, f["accession"])
        try:
            ingest_prices(conn, sec.id, fetch_eod(ticker, allow_live=True))
        except Exception:
            # a missing/failed price series for one name shouldn't abort the whole scan
            pass
        conn.commit()  # persist this name's facts (ingest defers the transaction to the caller)
    return rank_candidates(conn, securities, asof, cfg=cfg)
