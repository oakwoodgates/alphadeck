"""Loaders over ``tests/fixtures/edgar/successor_cik_2026_10.json`` — REAL data (see its ``_provenance``):
the prod master rows of UROY / REAX (the two successor-issuer cases) and HLLK / UCLE (the negatives), the
cached EDGAR submissions of each STORED CIK, the live submissions of each SUCCESSOR CIK, and the rows of
SEC's ticker map the tests use. Values are used exactly as extracted — a helper only reshapes them into
the forms the code under test consumes (a NameResult, an EDGAR-client stand-in, a master row)."""

from __future__ import annotations

import json
import uuid
from functools import cache
from pathlib import Path
from typing import Any

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.submissions import edgar_tickers
from ingest.edgar.succession import TICKER_MAP_CACHE_KEY
from pipeline.ingest_thesis import NameResult

_PATH = Path(__file__).resolve().parent / "fixtures" / "edgar" / "successor_cik_2026_10.json"

# the identity columns the fixture carries for each master row (the prod values)
_IDENTITY = (
    "sector",
    "exchange",
    "status",
    "category",
    "incorporation",
    "business_city",
    "business_country",
    "files_foreign_forms",
    "files_domestic_forms",
    "recent_foreign_form",
    "enriched_source",
    "enriched_at",
)


@cache
def data() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def case(ticker: str) -> dict:
    return data()[ticker]


def stored_doc(ticker: str) -> dict:
    return case(ticker)["stored_submissions"]


def successor_doc(ticker: str) -> dict:
    return case(ticker)["successor_submissions"]


def successor_cik(ticker: str) -> str:
    return case(ticker)["successor_cik"]


def ticker_map_raw() -> dict:
    return data()["ticker_map"]


def name_result(ticker: str, security_id: uuid.UUID) -> NameResult:
    """The NameResult the nightly ingest builds for this member: ``edgar_tickers`` read from its STORED
    CIK's submissions (the only field the detector takes from it)."""
    c = case(ticker)["master"]
    return NameResult(
        c["ticker"],
        security_id,
        0,
        0,
        edgar_tickers=edgar_tickers(stored_doc(ticker)),
        price_symbol=c["price_symbol"],
    )


def _submissions_key(cik: str) -> str:
    return f"submissions/CIK{int(cik):010d}.json"


class FixtureEdgar:
    """An EDGAR-client stand-in serving the fixture's documents by CACHE KEY — the same keys the real
    ``EdgarClient`` uses (``submissions/CIK<10>.json``, the ticker map's key) — and counting every read in
    ``live_fetches`` (so a test sees exactly how many requests a pass would make). ``docs`` limits what it
    serves (an absent key raises ``LookupError`` — a failed read); ``fail`` keys raise ``OSError``.
    """

    def __init__(self, docs: dict[str, Any], *, fail: frozenset[str] = frozenset()) -> None:
        self.docs = docs
        self.fail = fail
        self.live_fetches = 0
        self.keys: list[str] = []

    def get_json(self, url: str, cache_key: str) -> dict:
        self.live_fetches += 1
        self.keys.append(cache_key)
        if cache_key in self.fail:
            raise OSError(f"simulated fetch failure for {cache_key}")
        if cache_key not in self.docs:
            raise LookupError(f"{cache_key} not served")
        return json.loads(json.dumps(self.docs[cache_key]))  # a fresh copy each read


def network_docs(*tickers: str) -> dict[str, Any]:
    """What the scan's NETWORK client serves: SEC's ticker map + each listed case's successor doc."""
    out: dict[str, Any] = {TICKER_MAP_CACHE_KEY: ticker_map_raw()}
    for t in tickers:
        out[_submissions_key(successor_cik(t))] = successor_doc(t)
    return out


def cache_docs(*tickers: str) -> dict[str, Any]:
    """What the scan's CACHE-ONLY client serves: each listed case's STORED-CIK doc (the ingest's copy)."""
    return {_submissions_key(case(t)["master"]["cik"]): stored_doc(t) for t in tickers}


def insert_security(conn, ticker: str) -> uuid.UUID:
    """One master row for the case with its prod identity values (a fresh id — the prod id is recorded in
    the fixture only for provenance). Commits."""
    c = case(ticker)["master"]
    sid = uuid.uuid4()
    cols = ["id", "tenant_id", "ticker", "cik", "name", "price_symbol", "valid_from", *_IDENTITY]
    vals = [
        sid,
        DEFAULT_TENANT_ID,
        c["ticker"],
        c["cik"],
        c["name"],
        c["price_symbol"],
        "2026-01-01",
    ]
    vals += [c[k] for k in _IDENTITY]
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO security_master ({', '.join(cols)}) "  # noqa: S608 — fixed column names
            f"VALUES ({', '.join(['%s'] * len(cols))})",
            vals,
        )
    conn.commit()
    return sid
