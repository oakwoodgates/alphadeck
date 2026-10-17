"""Loaders over ``tests/fixtures/prices/price_feed_repairs_2026_10.json`` — REAL prod data (see its
``_provenance``): the stored tapes of two renamed names (HLLK, BRR) and a closed one (GTLS), the vendor bars
of their EDGAR-listed successors (SDCO; SVIA and BRR's warrants SVIAW), the never-priced WONDF, and the
EDGAR evidence for each. Values are used exactly as extracted — nothing here alters, rounds or synthesizes
one; a helper only reshapes them into the forms the code under test consumes."""

from __future__ import annotations

import json
import uuid
from datetime import date
from functools import cache
from pathlib import Path

import httpx

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.submissions import DelistingFiling, FormerName, NameChangeFiling
from ingest.prices.eod_loader import ingest_prices

_PATH = Path(__file__).resolve().parent / "fixtures" / "prices" / "price_feed_repairs_2026_10.json"


@cache
def data() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def case(ticker: str) -> dict:
    return data()[ticker]


def tape_rows(ticker: str) -> list[dict]:
    """The stored tape as ``ingest_prices`` rows (open/high/low absent — the fixture keeps close + volume,
    the two fields the splice and the detectors read)."""
    return [
        {
            "d": date.fromisoformat(d),
            "open": None,
            "high": None,
            "low": None,
            "close": c,
            "volume": v,
        }
        for d, c, v in case(ticker)["tape"]
    ]


def stored_closes(ticker: str) -> dict[date, float]:
    return {date.fromisoformat(d): c for d, c, _v in case(ticker)["tape"] if c}


def probe_bars(ticker: str, symbol: str) -> list[dict]:
    """The vendor bars fetched for ``symbol`` (a successor of ``ticker``), in the ``PriceSource`` shape."""
    return [
        {
            "d": date.fromisoformat(d),
            "open": None,
            "high": None,
            "low": None,
            "close": c,
            "volume": v,
        }
        for d, c, v in case(ticker)["probes"][symbol]["bars"]
    ]


def probe_error(ticker: str, symbol: str) -> str:
    return case(ticker)["probes"][symbol]["error"]


def not_found(symbol: str) -> httpx.HTTPStatusError:
    """The vendor's 404 as the price leg receives it (``polite_get`` -> ``raise_for_status``), for the URL
    the extracted error names — the exception class + status are what ``price_not_found`` keys on.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2y"
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"Client error '404 Not Found' for url '{url}'",
        request=req,
        response=httpx.Response(404, request=req),
    )


def delisting(ticker: str) -> tuple[DelistingFiling, ...]:
    return tuple(
        DelistingFiling(
            form=f["form"], filed=date.fromisoformat(f["filed"]), accession=f["accession"]
        )
        for f in case(ticker).get("delisting_filings") or []
    )


def former(ticker: str) -> tuple[FormerName, ...]:
    return tuple(
        FormerName(name=f["name"], since=f["from"][:10], until=f["to"][:10])
        for f in case(ticker).get("former_names") or []
    )


def name_changes(ticker: str) -> tuple[NameChangeFiling, ...]:
    return tuple(
        NameChangeFiling(filed=date.fromisoformat(f["filed"]), accession=f["accession"])
        for f in case(ticker).get("item_503_8ks") or []
    )


def last_bar(ticker: str) -> date | None:
    t = case(ticker)["tape"]
    return date.fromisoformat(t[-1][0]) if t else None


def insert_security(conn, ticker: str, *, with_tape: bool = True) -> uuid.UUID:
    """One master row for the case (a fresh id — the prod id is recorded in the fixture only for
    provenance), plus its stored tape when it has one. Commits."""
    c = case(ticker)
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, c["ticker"], c["cik"], c["name"], "2026-01-01"),
        )
    if with_tape and c["tape"]:
        ingest_prices(conn, sid, tape_rows(ticker))
    conn.commit()
    return sid
