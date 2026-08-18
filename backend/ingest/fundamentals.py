"""§2.2 fundamentals ingest — the quarterly REVENUE series behind the acceleration-inflection detector.

Pulls a name's SEC ``companyfacts`` (XBRL) ONCE — which returns the FULL multi-year history, each period
carrying its own ``filed`` / ``accn`` / ``end`` — and reconstructs the honest historical knowability: every
quarter is stamped ``valid_from = recorded_at = its filing acceptance (``filed``) date`` (R17), NEVER today.
So a single pull today rebuilds "what was knowable when", and a past-as-of replay sees each quarter EXACTLY
when it was filed (invariant #1, no-lookahead). A DETERMINISTIC parse — the LLM never sources the number (#3).

REUSE (one source of truth): it reads the SAME companyfacts client the Workbench AUTO tier uses and the SAME
revenue concepts (``ingest.edgar.extract.REVENUE_CONCEPTS`` — ``us-gaap:Revenues`` then the two
``RevenueFromContractWithCustomer*`` tags, first present wins, mirroring ``_first`` in that module), so "which
XBRL tag IS revenue" can never drift between the extractor and this ingest.

The quarterly series is:
  * every NATIVE ~3-month duration fact (Q1/Q2/Q3 arrive natively, each with its own ``filed``/``accn`` —
    clean per-row knowability and clean restatement-versioning), PLUS
  * a DERIVED Q4 = the fiscal-year 12-month total − the same-fiscal-year 9-month YTD, where no native Q4
    exists — so the FY/Q4 report (the NVDA-Jan-2024 tell) is not blind. A derived Q4's knowability is the
    LATER of its two source filings (you cannot know Q4 until the 10-K is filed), so it stamps the FY 10-K's
    ``filed`` and ``accn`` (the honest later-of knowability; provenance = the 10-K).

Missing / degenerate periods are simply ABSENT — the detector DECLINES, never fabricates (#9/#3).

§2.4 DEFERRED (margin / FCF): a later gross-margin or FCF inflection is a VARIANT over this same
``fact_fundamentals`` family — it adds ROWS under a new ``metric_key`` (needing COGS / OCF / capex concepts),
never a column. The (metric_key, value, unit) shape here is built for exactly that; only revenue ships now.

Band 03 S4 (share-count creep) adds the first sibling family: the quarterly SHARES-OUTSTANDING series —
three XBRL instant concepts stored as three metric_keys (``_xbrl``-suffixed to keep them verbally distinct
from the operator-ratified ``fact_shares_outstanding`` Workbench table). ALL THREE are stored (evidence is
complete, #9 — measured on the real 475-name basket no single concept covers everyone: dei 419 present /
us-gaap outstanding 361 / issued 372); the ``share_creep`` detector walks a fixed ladder on READ and never
mixes concepts inside one computation (issued vs outstanding differ by treasury stock). Instants carry no
``start``, so there is no span/derived-Q4 machinery — every point is 'native', stamped at its OWN ``filed``
(the same XBRL knowability trap + fix as revenue: knowable at 10-Q/K acceptance, never the instant date).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import REVENUE_CONCEPTS, companyfacts_url

# The metric_key the revenue series is stored under (the detector filters on the same literal — see
# ``signals.revenue_acceleration._REVENUE_METRIC``; kept a plain string so the pure detector needs no ingest
# import). §2.4 adds sibling keys (e.g. 'gross_margin', 'fcf') under this same family — rows, not columns.
REVENUE_METRIC = "revenue"

# Band 03 S4 — the shares-outstanding metric_keys (the ``share_creep`` detector mirrors these literals as
# its concept ladder; kept plain strings for the same no-ingest-import reason). ``_xbrl``-suffixed so the
# keys can never be confused with the operator-ratified ``fact_shares_outstanding`` table (a different
# store: ratified Workbench facts vs this deterministic companyfacts parse).
SHARES_OUT_METRIC = (
    "shares_out_xbrl"  # us-gaap:CommonStockSharesOutstanding (balance-sheet instant)
)
SHARES_OUT_COVER_METRIC = "shares_out_cover_xbrl"  # dei:EntityCommonStockSharesOutstanding (cover)
SHARES_ISSUED_METRIC = "shares_issued_xbrl"  # us-gaap:CommonStockSharesIssued

# (metric_key, taxonomy, concept) — all three stored; the detector's read-side ladder decides which
# expresses a name (the evidence/policy seam: re-ordering the ladder is a code edit, zero re-ingest).
SHARE_CONCEPTS: tuple[tuple[str, str, str], ...] = (
    (SHARES_OUT_METRIC, "us-gaap", "CommonStockSharesOutstanding"),
    (SHARES_OUT_COVER_METRIC, "dei", "EntityCommonStockSharesOutstanding"),
    (SHARES_ISSUED_METRIC, "us-gaap", "CommonStockSharesIssued"),
)

# companyfacts revenue DURATION rows classified by span (end − start). A clean fiscal quarter is ~85-98d; a
# 6/9/12-month YTD/annual is longer. These windows separate a standalone quarter from a cumulative period —
# they are DATA-parse dials (like the extractor's regexes/windows), never call-engine tuning.
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100
_YTD9_MIN_DAYS = 250  # ~9 months (the Q1–Q3 cumulative on the Q3 10-Q)
_YTD9_MAX_DAYS = 295
_ANNUAL_MIN_DAYS = 350  # ~12 months (the fiscal-year total on the 10-K)
_ANNUAL_MAX_DAYS = 380


@dataclass(frozen=True)
class QuarterPoint:
    """One knowable quarterly metric point: the value, its fiscal period END, and the SINGLE filing that made
    it knowable (``filed`` = the acceptance date, ``accession`` = provenance). ``basis`` = 'native' (a
    standalone XBRL quarter) | 'derived' (FY − 9moYTD). A restatement of the same period is a DISTINCT point
    (a later ``filed``), so the store versions it rather than dropping it."""

    metric_key: str
    period_end: date
    value: float
    filed: date
    accession: str
    basis: str
    fiscal_period: str | None
    fiscal_year: int | None
    unit: str = (
        "USD"  # 'USD' (revenue) | 'shares' (the S4 share-count series) — the stored unit column
    )


@dataclass(frozen=True)
class FundamentalsResult:
    """The ingest receipt: ``appended`` = new (security, metric, period_end, filed) versions stored;
    ``skipped`` = already-stored versions (the idempotency counter — a re-run appends ZERO, count-the-table
    guarded)."""

    appended: int
    skipped: int


def _span_days(row: dict[str, Any]) -> int | None:
    s, e = row.get("start"), row.get("end")
    if not s or not e:
        return None
    return (date.fromisoformat(e) - date.fromisoformat(s)).days


def _usd_duration_rows(companyfacts: dict[str, Any]) -> list[dict[str, Any]]:
    """The USD duration rows of the FIRST present revenue concept (``_first`` semantics — one concept, no
    mixing tags). Only rows carrying every field the knowability stamp needs (start/end/val/filed/accn) are
    kept; anything sparse is dropped (honest — an unstampable row is not fabricated into one)."""
    facts = companyfacts.get("facts", {})
    for tax, concept in REVENUE_CONCEPTS:
        node = facts.get(tax, {}).get(concept)
        rows = node.get("units", {}).get("USD", []) if node else []
        rows = [
            r
            for r in rows
            if r.get("start")
            and r.get("end")
            and r.get("val") is not None
            and r.get("filed")
            and r.get("accn")
        ]
        if rows:
            return rows
    return []


def extract_revenue_quarters(companyfacts: dict[str, Any]) -> list[QuarterPoint]:
    """Pure, deterministic: a companyfacts dict -> the quarterly revenue points (native quarters + derived
    Q4), each stamped at its OWN filing. Testable offline against a hand-built companyfacts fixture (the
    ``extract_facts`` precedent — the caller owns fetching). Never raises on a sparse/odd doc — it yields
    fewer points."""
    rows = _usd_duration_rows(companyfacts)
    # dedup by (period_end, filed) — companyfacts can repeat a fact; the first wins (deterministic).
    points: dict[tuple[date, date], QuarterPoint] = {}

    # (1) native standalone quarters (Q1/Q2/Q3 — each its own filing, so knowability + versioning are per-row)
    for r in rows:
        span = _span_days(r)
        if span is None or not (_QUARTER_MIN_DAYS <= span <= _QUARTER_MAX_DAYS):
            continue
        e, f = date.fromisoformat(r["end"]), date.fromisoformat(r["filed"])
        points.setdefault(
            (e, f),
            QuarterPoint(
                metric_key=REVENUE_METRIC,
                period_end=e,
                value=float(r["val"]),
                filed=f,
                accession=r["accn"],
                basis="native",
                fiscal_period=r.get("fp"),
                fiscal_year=r.get("fy"),
            ),
        )

    # (2) derived Q4 = FY(12mo) − 9moYTD(same fiscal-year start), only where no native quarter ends at the FY
    # end. Knowability = the LATER of the two filings (always the FY 10-K); provenance = the FY accession.
    native_ends = {e for (e, _f) in points}
    annuals = [
        r for r in rows if (s := _span_days(r)) and _ANNUAL_MIN_DAYS <= s <= _ANNUAL_MAX_DAYS
    ]
    ytd9 = [r for r in rows if (s := _span_days(r)) and _YTD9_MIN_DAYS <= s <= _YTD9_MAX_DAYS]
    for a in annuals:
        a_end = date.fromisoformat(a["end"])
        if a_end in native_ends:
            continue  # a native Q4 already exists — never double it
        a_filed = date.fromisoformat(a["filed"])
        # the 9-month YTD of the SAME fiscal year (same start), knowable by the FY's filing; latest such wins
        cands = [
            y
            for y in ytd9
            if y["start"] == a["start"] and date.fromisoformat(y["filed"]) <= a_filed
        ]
        if not cands:
            continue
        y = max(cands, key=lambda r: r["filed"])
        q4_val = float(a["val"]) - float(y["val"])
        if q4_val <= 0:
            continue  # a non-positive derived quarter is a bad pair, not real revenue — decline (no fabrication)
        filed = max(a_filed, date.fromisoformat(y["filed"]))  # the later-of knowability (the 10-K)
        points.setdefault(
            (a_end, filed),
            QuarterPoint(
                metric_key=REVENUE_METRIC,
                period_end=a_end,
                value=q4_val,
                filed=filed,
                accession=a["accn"],
                basis="derived",
                fiscal_period="Q4",
                fiscal_year=a.get("fy"),
            ),
        )

    return sorted(points.values(), key=lambda p: (p.period_end, p.filed))


def extract_share_points(companyfacts: dict[str, Any]) -> list[QuarterPoint]:
    """Pure, deterministic (Band 03 S4): a companyfacts dict -> the shares-outstanding instant points for
    ALL THREE share concepts (``SHARE_CONCEPTS``), each stamped at its OWN filing. Instants carry no
    ``start`` so there is no span classification and no derived Q4 — every point is 'native':
    ``period_end`` = the instant's ``end`` (balance-sheet date / cover "as of" date), knowability =
    ``filed``. Only rows carrying every field the knowability stamp needs (end/val/filed/accn) are kept
    (the same honesty rule as revenue: an unstampable row is not fabricated into one). Degenerate values
    (a literal 0-share XBRL row) are STORED as-is — the evidence tape is honest and complete (#9); the
    ``share_creep`` detector is where garbage is screened, never the ingest (the threshold/guard is the
    detector's cut, never an ingest filter). Dedup per (metric, end, filed) — a restatement (same end,
    later filed) is a DISTINCT version the store keeps."""
    facts = companyfacts.get("facts", {})
    points: dict[tuple[str, date, date], QuarterPoint] = {}
    for metric_key, tax, concept in SHARE_CONCEPTS:
        node = facts.get(tax, {}).get(concept)
        rows = node.get("units", {}).get("shares", []) if node else []
        for r in rows:
            if not (r.get("end") and r.get("val") is not None and r.get("filed") and r.get("accn")):
                continue
            e, f = date.fromisoformat(r["end"]), date.fromisoformat(r["filed"])
            points.setdefault(
                (metric_key, e, f),
                QuarterPoint(
                    metric_key=metric_key,
                    period_end=e,
                    value=float(r["val"]),
                    filed=f,
                    accession=r["accn"],
                    basis="native",
                    fiscal_period=r.get("fp"),
                    fiscal_year=r.get("fy"),
                    unit="shares",
                ),
            )
    return sorted(points.values(), key=lambda p: (p.metric_key, p.period_end, p.filed))


def _existing_versions(
    conn: psycopg.Connection, security_id: UUID, tenant_id: UUID
) -> set[tuple[str, date, date]]:
    """The (metric_key, period_end, filed=valid_from) versions already stored for this security — the
    idempotency set. A re-ingest skips these, so the append-only table never silently grows (count-the-table).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metric_key, period_end, valid_from FROM fact_fundamentals "
            "WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        return {(r["metric_key"], r["period_end"], r["valid_from"]) for r in cur.fetchall()}


def store_quarters(
    conn: psycopg.Connection,
    security_id: UUID,
    quarters: list[QuarterPoint],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> FundamentalsResult:
    """Append each quarter as a bitemporal fact stamped at its TRUE knowability (``valid_from = recorded_at
    = filed`` — R17, the honest backfill; NOT ``now()``). Idempotent: a (metric, period_end, filed) already
    stored is skipped, so a re-run appends ZERO. Append-only; the caller owns the txn (no commit here).
    """
    existing = _existing_versions(conn, security_id, tenant_id)
    appended = skipped = 0
    for q in quarters:
        if (q.metric_key, q.period_end, q.filed) in existing:
            skipped += 1
            continue
        # recorded_at = the filing date at 00:00 UTC — the transaction-time twin of valid_from. Under the
        # canonical fixed-future pin this is trivially <= known_at; under a lockstep known_at it gates too.
        recorded_at = datetime.combine(q.filed, time.min, tzinfo=timezone.utc)
        append_fact(
            conn,
            "fact_fundamentals",
            {
                "tenant_id": tenant_id,
                "security_id": security_id,
                "metric_key": q.metric_key,
                "period_end": q.period_end,
                "fiscal_period": q.fiscal_period,
                "fiscal_year": q.fiscal_year,
                "value": q.value,
                "unit": q.unit,
                "basis": q.basis,
                "accession": q.accession,
                "source": "companyfacts",
                "valid_from": q.filed,  # KNOWABILITY: the filing acceptance date, never today
                "recorded_at": recorded_at,
            },
        )
        existing.add((q.metric_key, q.period_end, q.filed))
        appended += 1
    return FundamentalsResult(appended, skipped)


def ingest_fundamentals_for_security(
    conn: psycopg.Connection,
    sec: Security,
    *,
    client: EdgarClient,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> FundamentalsResult:
    """Fetch this security's companyfacts (cache-first via the injected client), extract the quarterly
    revenue series AND the S4 shares-outstanding series (both ride the SAME single pull — zero extra SEC
    traffic), and store them. A CIK-less security contributes nothing (like the Form-4 leg's no-CIK
    mirror). The multi-year backfill is intrinsic: companyfacts returns the full history in one pull, so a
    single call reconstructs every historical quarter at its own ``filed`` date."""
    if not sec.cik:
        return FundamentalsResult(0, 0)
    cf = client.get_json(
        companyfacts_url(sec.cik),
        f"companyfacts/CIK{int(sec.cik):010d}.json",
    )
    quarters = extract_revenue_quarters(cf) + extract_share_points(cf)
    return store_quarters(conn, sec.id, quarters, tenant_id=tenant_id)


__all__ = [
    "REVENUE_METRIC",
    "SHARES_OUT_METRIC",
    "SHARES_OUT_COVER_METRIC",
    "SHARES_ISSUED_METRIC",
    "SHARE_CONCEPTS",
    "QuarterPoint",
    "FundamentalsResult",
    "extract_revenue_quarters",
    "extract_share_points",
    "store_quarters",
    "ingest_fundamentals_for_security",
]
