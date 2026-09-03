"""Shared DB fixtures for the point-in-time memo / prefetch / bounds proofs (Board/Cockpit perf PR-1b).

Three ingredients the equality tests need beyond the seed theses:

- ``add_mid_versions`` — re-versions ONE HIMS price bar and the Wells Form 4 at two later transaction
  times (``T1`` < ``T2``), so a read pinned at ``MID`` (between them) sits BETWEEN two recorded versions
  of each fact: the version-pick under a past ``known_at`` is exactly where a batched ``DISTINCT ON``
  could diverge from the per-security read, so every equality test runs at ``PIN`` (sees all) AND ``MID``.
- ``seed_benchmark_bars`` — SPY bars, so the relative-strength members exercise the out-of-basket
  fallback path (a benchmark is never a basket member).
- ``seed_oldco`` — a name whose ONLY Form 4 is ~700 d old: the ``insider_flow_90d`` "rows, none in the
  window" (``0/0``) shape that a bounded insider read would collapse into "no rows" (``—``).

Everything runs in the test's transaction (the shared ``db`` fixture rolls back).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices
from pipeline.seed import (
    _SEED_DATA,
    _WELLS_ACCESSION,
    HIMS_SECURITY_ID,
    HIMS_THESIS_ID,
    LEU_ID,
    NNE_ID,
    NUCLEAR_THESIS_ID,
    OKLO_ID,
    SMR_ID,
    UNH_SECURITY_ID,
    UNH_THESIS_ID,
    seed_hims,
    seed_leu_catalyst,
    seed_nuclear,
    seed_nuclear_catalyst,
    seed_nuclear_theme_conviction,
    seed_unh,
)
from securities.benchmarks import seed_benchmarks

SECS: list[UUID] = [HIMS_SECURITY_ID, UNH_SECURITY_ID, SMR_ID, OKLO_ID, NNE_ID, LEU_ID]
THESES: list[UUID] = [HIMS_THESIS_ID, UNH_THESIS_ID, NUCLEAR_THESIS_ID]

# Transaction-time pins. PIN sees every version; MID sits strictly between the two re-versions below
# (both later than the seed's ``recorded_at = now()`` as long as the suite runs before T1).
PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 1, tzinfo=timezone.utc)
MID = datetime(2026, 10, 15, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 1, tzinfo=timezone.utc)
# the re-versioned Form 4's distinguishing marker per version (a display-only column, never a gate)
ACCEPTED_V1 = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)
ACCEPTED_V2 = datetime(2026, 5, 21, 12, tzinfo=timezone.utc)
# the HIMS bar to re-version: the latest seed bar on/before this date (inside the 2026-06-01 windows)
_REVERSION_TARGET = date(2026, 5, 29)

OLDCO_ID = UUID("01dc0000-0000-0000-0000-000000000001")
OLD_FORM4_AGE_DAYS = 700


def seed_all(db) -> None:
    """The three seed theses + their facts + the SPY/IWM benchmark master rows (no bars yet)."""
    seed_hims(db)
    seed_unh(db)
    seed_nuclear(db)
    seed_nuclear_catalyst(db)
    seed_leu_catalyst(db)
    seed_nuclear_theme_conviction(db)
    seed_benchmarks(db)
    db.commit()


def add_mid_versions(db) -> tuple[date, Decimal, Decimal]:
    """Re-version one HIMS bar (close +0.01 at T2) and the Wells Form 4 (a distinct ``accepted`` per
    version) at T1 and T2. Returns ``(bar date, close at T1, close at T2)``."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT d, open, high, low, close, volume FROM fact_price_eod "
            "WHERE tenant_id = %s AND security_id = %s AND d <= %s "
            "ORDER BY d DESC, recorded_at DESC LIMIT 1",
            (DEFAULT_TENANT_ID, HIMS_SECURITY_ID, _REVERSION_TARGET),
        )
        bar = cur.fetchone()
    assert bar is not None, "the HIMS seed must hold a bar on/before the re-version target"
    v1 = {k: bar[k] for k in ("d", "open", "high", "low", "close", "volume")}
    v2_close = bar["close"] + Decimal("0.01")
    v2 = {**v1, "close": v2_close, "high": max(bar["high"], v2_close)}
    ingest_prices(db, HIMS_SECURITY_ID, [v1], recorded_at=T1)
    ingest_prices(db, HIMS_SECURITY_ID, [v2], recorded_at=T2)
    xml = (_SEED_DATA / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8")
    ingest_form4(db, HIMS_SECURITY_ID, xml, _WELLS_ACCESSION, recorded_at=T1, accepted=ACCEPTED_V1)
    ingest_form4(db, HIMS_SECURITY_ID, xml, _WELLS_ACCESSION, recorded_at=T2, accepted=ACCEPTED_V2)
    db.commit()
    return bar["d"], bar["close"], v2_close


def _synthetic_bars(asof: date, n: int, *, start_price: float, step: float) -> list[dict]:
    """``n`` weekday bars ending at ``asof`` with a gently rising close (so SMA/RS/range all compute)."""
    bars: list[dict] = []
    d = asof
    while len(bars) < n:
        if d.weekday() < 5:
            bars.append(d)
        d -= timedelta(days=1)
    bars.reverse()
    out = []
    for i, day in enumerate(bars):
        close = start_price + step * i
        out.append(
            {
                "d": day,
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1_000_000 + 1_000 * i,
            }
        )
    return out


def benchmark_id(db, symbol: str) -> UUID:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM security_master "
            "WHERE tenant_id = %s AND ticker = %s AND instrument_kind = 'etf'",
            (DEFAULT_TENANT_ID, symbol),
        )
        row = cur.fetchone()
    assert row is not None, f"benchmark {symbol} not seeded"
    return row["id"]


def seed_benchmark_bars(db, asof: date, n: int = 320) -> UUID:
    """SPY bars ending at ``asof`` (the RS members' reference tape). Returns SPY's master id."""
    spy = benchmark_id(db, "SPY")
    ingest_prices(db, spy, _synthetic_bars(asof, n, start_price=400.0, step=0.1))
    db.commit()
    return spy


def seed_oldco(db, asof: date) -> UUID:
    """A resolved name with ~a year of bars whose ONLY Form 4 is ``OLD_FORM4_AGE_DAYS`` old."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (OLDCO_ID, DEFAULT_TENANT_ID, "0009999999", "OLDCO", "Old Filer Co", date(2024, 1, 1)),
        )
    ingest_prices(db, OLDCO_ID, _synthetic_bars(asof, 300, start_price=20.0, step=0.01))
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": OLDCO_ID,
            "insider_name": "Old Insider",
            "insider_role": "Director",
            "txn_code": "P",
            "shares": 1_000,
            "price": 15.0,
            "usd": 15_000.0,
            "accession": "0009999999-24-000001",
            "valid_from": asof - timedelta(days=OLD_FORM4_AGE_DAYS),
            "txn_seq": 0,
        },
    )
    db.commit()
    return OLDCO_ID
