"""The benchmark family — SPY + IWM as reference ``security_master`` rows (Signals §2.1).

The relative-strength column (``signals/display/relative_strength.py``) prices each basket member
AGAINST a market benchmark, so the benchmark's EOD tape has to live in ``fact_price_eod`` the same
way a member's does. A benchmark is NOT a basket member (it floats free of any thesis, invariant #2
notwithstanding — it is reference data, never a name the operator is judging), so it is seeded here
as a small, idempotent set of master rows rather than through the promote/basket path.

- ``instrument_kind='etf'`` — a benchmark IS a fund (SPY/IWM are ETFs); the RS accessor resolves the
  reference series by ``(tenant, ticker, etf)`` so it can never collide with an operating company that
  happens to share a ticker.
- No ``cik`` — an ETF is a fund-trust series, not an operating-company CIK (the same ``cik=None`` an
  ETF sleeve carries); nothing here files Form 4s, so the missing CIK strands no facts.
- Idempotent by ``(tenant, ticker)`` — a re-seed inserts nothing (count-the-table safe), so the
  ``pipeline.ingest_benchmarks`` CLI can call it on every run.

The PRICE backfill (the one-shot deep history) lives in ``pipeline.ingest_benchmarks``; this module
owns only the identity rows. The RS display module hand-keeps its OWN ``BENCHMARKS`` tuple equal to
these tickers (the seam cannot import ``securities`` cleanly) — ``test_relative_strength`` drift-guards
the two lists so they can never diverge.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import psycopg

from db.session import DEFAULT_TENANT_ID

# ticker -> registered fund name. The RS column reads these two; add a benchmark here (+ mirror the
# tuple in ``signals.display.relative_strength.BENCHMARKS``) and both the seed and ingest follow.
BENCHMARKS: dict[str, str] = {
    "SPY": "SPDR S&P 500 ETF Trust",
    "IWM": "iShares Russell 2000 ETF",
}

# the benchmark identity is knowable from inception; a fixed early valid_from keeps every historical
# bar's valid_from (= the bar date) at/after it, so the master row is never "newer" than its own tape.
_SEED_VALID_FROM = date(2000, 1, 1)


def seed_benchmarks(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[str, UUID]:
    """Ensure the benchmark ETF rows (SPY, IWM) exist in ``tenant_id``'s master — idempotent by
    ``(tenant, ticker)``. Returns ``{ticker: security_id}`` for the ingest to price against.

    Reference securities, NOT basket members (#2 governs the thesis spine; a benchmark floats free).
    Resolves an existing row before inserting, so a re-seed appends nothing — the master does not grow
    on re-run (the seeder is called on every ``ingest_benchmarks`` pass). The caller commits.
    """
    out: dict[str, UUID] = {}
    with conn.cursor() as cur:
        for ticker, name in BENCHMARKS.items():
            cur.execute(
                "SELECT id FROM security_master "
                "WHERE tenant_id = %s AND ticker = %s AND instrument_kind = 'etf' "
                "ORDER BY recorded_at DESC, id DESC LIMIT 1",
                (tenant_id, ticker),
            )
            row = cur.fetchone()
            if row is not None:
                out[ticker] = row["id"]
                continue
            sid = uuid4()
            cur.execute(
                "INSERT INTO security_master "
                "(id, tenant_id, ticker, name, instrument_kind, valid_from) "
                "VALUES (%s, %s, %s, %s, 'etf', %s)",
                (sid, tenant_id, ticker, name, _SEED_VALID_FROM),
            )
            out[ticker] = sid
    return out
