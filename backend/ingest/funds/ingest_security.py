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
from datetime import date
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


def fund_shares_applies(sec: Security) -> bool:
    """Does the fund-shares leg apply to this member — i.e. is it an ETF sleeve with a ticker to sample?

    THE ONE DEFINITION, deliberately extracted so it has two readers that cannot drift: the leg's own
    early return (below) and the RECENCY MONITOR's edge read (``pipeline/ingest_thesis.py``). The monitor
    cannot infer this from the leg's outcome, and that is the whole point — an ETF with no samplable
    source RAISES ``FundSharesUnavailable``, so there is no result to read a flag off, and that is exactly
    the name whose sampler is dead and must be reported. It also cannot infer it from the stored edge: a
    name with no samples reads ``None``, which for an EQUITY means "never sampled, correctly" and for an
    ETF means "the sampler has never worked" — opposite conclusions from identical data.

    Reads master IDENTITY (what the instrument IS), never a fact — the same gate discipline as the form4
    leg's "a name with no CIK contributes nothing"."""
    return sec.instrument_kind == InstrumentKind.ETF and bool(sec.ticker)


def latest_shares_date(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID
) -> date | None:
    """The most-recent fund-shares sample date (``d``) stored for (tenant, security), or ``None`` when
    there are none — THE SAMPLE EDGE the recency monitor judges.

    The ``eod_loader.latest_bar_date`` mirror, for the other feed. A plain ``MAX`` is unaffected by
    duplicate VERSIONS of a date (a restated count re-versions the same ``d``), so this answers "how
    recent is the newest sample the store holds", which is the question. ``valid_from == d`` for this
    table by construction (migration 0027), so the as-of index covers the equivalent scan; the table is
    tiny in any case (one row per sleeve per sampled day).

    A FACT, never a judgment: whether that date is too old is decided by ``pipeline/tape_health.py``
    against the run's ``asof``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(d) AS d FROM fact_fund_shares WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        return cur.fetchone()["d"]


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
    if not fund_shares_applies(sec):  # the gate, shared with the recency monitor (above)
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
