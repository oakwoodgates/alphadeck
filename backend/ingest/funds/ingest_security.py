"""The per-security fund-shares leg (ETF net flow, F2) — ONE implementation, called from the per-thesis
back-half loop (and so the daily cron), the ``ingest.prices.ingest_security`` idiom exactly:

- **Gated on what the instrument IS** — only an ``instrument_kind == 'etf'`` member with a ticker is
  sampled; every other member contributes no shares sample and never touches the source (the form4
  leg's "a name with no CIK contributes nothing" mirror). The gate reads master IDENTITY, never a fact.
- **Incremental** — a re-sample of an already-stored ``(d, shares_out)`` appends NOTHING (the append-only
  table never silently grows; COUNT-the-table guarded). A snapshot for a NEW ``d`` appends one row.
- **RE-VERSION on restatement** — the SAME ``d`` with a CHANGED count (the aggregator's ~10k-rounded
  sample corrected by the issuer's exact one, or an issuer restating its page) appends a NEW version
  (same ``d``, a later ``recorded_at``) — the bitemporal store's native move; the as-of read's
  DISTINCT ON picks the latest. A replay pinned before the correction still sees the old count (#1).
- **No-lookahead** — ``valid_from = d`` = the page's OWN stated as-of date; ``recorded_at`` stays the DB
  default ``now()`` (never backdated), so a replay pinned before the first sample honestly sees nothing.
- **Fail-visible** — a fund with no samplable source RAISES (``FundSharesUnavailable`` from the composite,
  or the explicit guard here if a bare adapter returns ``None``); the pipeline captures it into the
  name's ``NameResult`` — a visible "no source" state, never a silent omission (#7/#9).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from domain.enums import InstrumentKind
from domain.security import Security
from ingest.funds.source import FundSharesSource, FundSharesUnavailable, default_fund_source


@dataclass(frozen=True)
class FundSharesResult:
    """The fund-shares leg's receipt: ``appended`` = a first sample for its ``d``; ``reversioned`` = a
    changed count re-stored for an already-sampled ``d`` (a restatement) — the exceptional path,
    surfaced loudly only when nonzero. At most one of the two is 1 per run (one snapshot per pull).
    """

    appended: int
    reversioned: int

    @property
    def total(self) -> int:
        return self.appended + self.reversioned


def stored_shares_for_day(
    conn: psycopg.Connection, security_id: UUID, d, *, tenant_id: UUID
) -> float | None:
    """The latest stored VERSION of the (security, d) sample — the compare basis for the incremental
    skip / re-version decision. Newest ``recorded_at`` wins (id the deterministic tiebreak): the SAME
    dedup the bitemporal as-of read applies, so the compare sees what a reader would."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT shares_out FROM fact_fund_shares "
            "WHERE tenant_id = %s AND security_id = %s AND d = %s "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (tenant_id, security_id, d),
        )
        row = cur.fetchone()
    return float(row["shares_out"]) if row else None


def ingest_fund_shares_for_security(
    conn: psycopg.Connection,
    sec: Security,
    *,
    tenant_id: UUID,
    allow_live: bool = True,
    force_refresh: bool = False,
    source: FundSharesSource | None = None,
) -> FundSharesResult:
    """Sample this security's fund shares outstanding and store it (see the module docstring for the
    rules). A non-ETF or ticker-less member is a no-op that never constructs or calls the source.
    Reads the snapshot through the injected ``FundSharesSource`` (the seam); ``force_refresh`` makes
    the recurring path bypass a same-day cache hit. The caller owns the transaction."""
    if sec.instrument_kind != InstrumentKind.ETF or not sec.ticker:
        return FundSharesResult(0, 0)
    src = source or default_fund_source()
    snap = src.get_snapshot(sec.ticker, allow_live=allow_live, force_refresh=force_refresh)
    if snap is None:
        # the composite raises with both stories; a bare adapter returning None lands here — the same
        # visible "no source" condition either way, never a quiet skip (#7/#9)
        raise FundSharesUnavailable(f"no samplable fund-shares source for {sec.ticker}")
    prior = stored_shares_for_day(conn, sec.id, snap["d"], tenant_id=tenant_id)
    # counts are integral; abs_tol 0.5 makes an integer-equal re-sample a skip while any real change
    # (creations/redemptions move whole creation units, >=~10k shares) re-versions
    if prior is not None and math.isclose(prior, float(snap["shares_out"]), abs_tol=0.5):
        return FundSharesResult(0, 0)  # same (d, count) — an idempotent re-sample appends nothing
    append_fact(
        conn,
        "fact_fund_shares",
        {
            "tenant_id": tenant_id,
            "security_id": sec.id,
            "d": snap["d"],
            "shares_out": snap["shares_out"],
            "source": snap["source"],
            "source_ref": snap["source_ref"],
            "valid_from": snap["d"],
        },
    )
    return FundSharesResult(1, 0) if prior is None else FundSharesResult(0, 1)
