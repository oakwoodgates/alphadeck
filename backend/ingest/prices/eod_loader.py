from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from ingest import CacheMiss
from ingest.http import polite_get

# Free EOD source. Stooq's free CSV is now apikey/captcha-gated, so the live default is Yahoo Finance
# (free, no key); the loader stays swappable (DATA_SOURCES: Stooq / Tiingo-free / equivalent).
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


def fetch_csv(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    force_refresh: bool = False,
) -> str:
    """Cache-first Stooq CSV text for a ticker; live only behind ``allow_live``. ``force_refresh`` (live
    only) bypasses a cache hit to re-pull + overwrite — see ``fetch_eod`` for the why."""
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / f"{ticker.upper()}.csv"
    if path.exists() and not (force_refresh and allow_live):
        return path.read_text(encoding="utf-8")
    if not allow_live:
        raise CacheMiss(f"no cached price CSV for {ticker!r} (live pulls disabled)")
    resp = polite_get(stooq_url(ticker), timeout=30)  # 429/5xx backoff (D6 politeness)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def yahoo_chart_url(ticker: str, range_: str = "1y") -> str:
    return (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"
        f"?interval=1d&range={range_}"
    )


def parse_yahoo_chart(payload: dict) -> list[dict]:
    """Parse a Yahoo Finance chart JSON payload into ascending EOD bar rows."""
    results = payload.get("chart", {}).get("result") or []
    if not results:
        return []
    result = results[0]
    ts = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    rows: list[dict] = []
    for i, t in enumerate(ts):
        close = closes[i] if i < len(closes) else None
        if close is None:  # Yahoo emits nulls for half-days / gaps
            continue
        rows.append(
            {
                "d": datetime.fromtimestamp(t, tz=timezone.utc).date(),
                "open": opens[i] if i < len(opens) else None,
                "high": highs[i] if i < len(highs) else None,
                "low": lows[i] if i < len(lows) else None,
                "close": close,
                "volume": vols[i] if i < len(vols) else None,
            }
        )
    return rows


def fetch_eod(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    range_: str = "1y",
    force_refresh: bool = False,
) -> list[dict]:
    """Cache-first EOD bars from Yahoo Finance (free, no key). Live only behind ``allow_live``.

    ``force_refresh`` (meaningful ONLY together with ``allow_live``) bypasses a cache hit to re-pull live and
    OVERWRITE the cache. The recurring/daily ingest sets it so the cron gets NEW bars instead of a frozen
    cache hit; the dev / ``--no-live`` path leaves it ``False`` and stays cache-first (and is honored even if
    ``force_refresh`` is set — no network without ``allow_live``). A cache MISS always fetches, so a new
    name's first ingest is fresh regardless."""
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / f"{ticker.upper()}.yahoo.json"
    if path.exists() and not (force_refresh and allow_live):
        return parse_yahoo_chart(json.loads(path.read_text(encoding="utf-8")))
    if not allow_live:
        raise CacheMiss(f"no cached EOD for {ticker!r} (live pulls disabled)")
    resp = polite_get(  # 429/5xx backoff (D6 politeness)
        yahoo_chart_url(ticker, range_),
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (Alpha Deck research)"},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return parse_yahoo_chart(resp.json())


def ingest_prices(
    conn: psycopg.Connection,
    security_id: UUID,
    rows: list[dict],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> int:
    """Append EOD bars to ``fact_price_eod`` (append-only); the caller owns the transaction (no
    commit here). Returns the count appended."""
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
    return count


def latest_bar_date(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> date | None:
    """The most-recent EOD bar date already stored for (tenant, security), or ``None`` if there are none.

    The per-thesis ingest appends ONLY bars newer than this, so a re-run of an already-current name writes
    NOTHING — the append-only table never silently grows on re-ingest (the read dedups; this stops the
    write). A plain ``MAX`` is unaffected by duplicate versions of a date (they share ``d``)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(d) AS d FROM fact_price_eod WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        return cur.fetchone()["d"]
