from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from pathlib import Path
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from ingest import CacheMiss

# Free EOD source (no API key). Swappable behind this loader (e.g. Tiingo-free) if a key is preferred.
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "price_cache"


def _to_float(s: str | None) -> float | None:
    return float(s) if s not in (None, "") else None


def stooq_url(ticker: str) -> str:
    return f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"


def parse_stooq_csv(text: str) -> list[dict]:
    """Parse a Stooq daily CSV (Date,Open,High,Low,Close,Volume) into EOD bar rows."""
    rows: list[dict] = []
    for r in csv.DictReader(StringIO(text)):
        if not r.get("Date"):
            continue
        rows.append(
            {
                "d": date.fromisoformat(r["Date"]),
                "open": _to_float(r.get("Open")),
                "high": _to_float(r.get("High")),
                "low": _to_float(r.get("Low")),
                "close": _to_float(r.get("Close")),
                "volume": _to_float(r.get("Volume")),
            }
        )
    return rows


def fetch_csv(ticker: str, *, cache_dir: Path | None = None, allow_live: bool = False) -> str:
    """Cache-first Stooq CSV text for a ticker; live only behind ``allow_live``."""
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / f"{ticker.upper()}.csv"
    if path.exists():
        return path.read_text(encoding="utf-8")
    if not allow_live:
        raise CacheMiss(f"no cached price CSV for {ticker!r} (live pulls disabled)")
    import httpx

    resp = httpx.get(stooq_url(ticker), timeout=30)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def ingest_prices(
    conn: psycopg.Connection,
    security_id: UUID,
    rows: list[dict],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> int:
    """Append EOD bars to ``fact_price_eod`` (append-only). Returns count."""
    count = 0
    for r in rows:
        values = {
            "tenant_id": tenant_id,
            "security_id": security_id,
            "d": r["d"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
            "valid_from": r["d"],
        }
        if recorded_at is not None:
            values["recorded_at"] = recorded_at
        append_fact(conn, "fact_price_eod", values)
        count += 1
    conn.commit()
    return count
