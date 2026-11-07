"""The fund launch radar's scan (docs/FUND_RADAR.md) — find new fund filings on the EDGAR daily index the night
they go public, read each one's header, and store FACTS ONLY (the filing, one row per fund series, the symbols
the two symbol files show). Stages and card matches are derived when read (``fund_stage`` / ``fund_match``).

Deterministic end-to-end (#3): forms, header tags, two symbol files — no LLM. Fail-visible (#9): an unreadable
header goes on the result's ``errors`` and nothing is stored for it, so the next night's catch-up window
retries it. Never imports ``calls/`` — a fund filing is a tape and a display, not a trigger.

Dates (#1, the public clock): a private-to-public filing counts from the day SEC RELEASED it (the header's
first line), never from its old acceptance; otherwise from the night of its ``ACCEPTANCE-DATETIME`` (an 18:00
acceptance counts that evening, though SEC dates it the next business day); with no acceptance, the filing
date. Neither symbol file carries a date, so each symbol counts from the night WE first saw it, and a backfill
can only mark tonight.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import psycopg

from domain.market_time import edgar_night, market_today
from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.edgar.dailyindex import IndexFiling, fetch_daily_index
from ingest.funds.snapshot_loader import _UA as _GENERIC_UA
from ingest.http import polite_get
from radar import fund_repo
from radar.fund_header import (
    KIND_EXISTING,
    KIND_NEW,
    KIND_NEW_CLASSES,
    FundHeader,
    HeaderParseError,
    parse_header,
)

FUND_FORMS = frozenset({"485APOS", "485BPOS", "N-1A", "N-1A/A"})
_KIND_RANK = {KIND_NEW: 0, KIND_NEW_CLASSES: 1, KIND_EXISTING: 2}
S1_FORMS = frozenset(
    {"S-1", "S-1/A"}
)  # crypto / commodity funds register this way (name-only rows)
_FUND_NAME = re.compile(r"\b(?:trust|etf|fund)\b", re.IGNORECASE)

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
NASDAQ_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKER_CACHE_KEY = (
    "fund-tickers/company_tickers_mf.json"  # a mutable key class: the client's TTL applies
)


@dataclass
class FundRadarResult:
    dates_scanned: list[str] = field(default_factory=list)
    dates_skipped: list[str] = field(default_factory=list)  # weekends/holidays (no index posted)
    filings_seen: int = 0  # watch-form accessions considered (deduped)
    filings_appended: int = 0
    filings_unchanged: int = 0
    series_appended: int = 0
    series_unchanged: int = 0
    symbols_appended: int = 0
    symbols_skipped: list[str] = field(
        default_factory=list
    )  # a symbol file not read (visible, not an error)
    errors: list[str] = field(default_factory=list)
    edgar_fetches: int = 0

    @property
    def summary(self) -> str:
        parts = [
            f"{len(self.dates_scanned)} days",
            f"{self.filings_seen} fund filings",
            f"+{self.filings_appended} filings ({self.filings_unchanged} unchanged)",
            f"+{self.series_appended} series ({self.series_unchanged} unchanged)",
            f"+{self.symbols_appended} symbols",
            f"{self.edgar_fetches} EDGAR fetches",
        ]
        if self.symbols_skipped:
            parts.append(f"{len(self.symbols_skipped)} symbol files skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} ERRORS")
        return " · ".join(parts)


def _is_no_index_day(e: Exception) -> bool:
    """A missing daily index (weekend/holiday): EDGAR answers 403 (not 404) — the quiet skip, as the SPAC radar's."""
    return getattr(getattr(e, "response", None), "status_code", None) in (403, 404)


def keep_filing(f: IndexFiling) -> bool:
    """The forms the radar reads: the four fund-registration forms, and an S-1 only from a filer named like a fund."""
    if f.form in FUND_FORMS:
        return True
    return f.form in S1_FORMS and _FUND_NAME.search(f.company) is not None


def public_date_of(h: FundHeader, *, index_filed: date | None = None) -> date:
    """The date a filing counts from (#1). Raises ``HeaderParseError`` for a private-to-public header with no
    release date (nothing honest to date it by)."""
    if h.private_to_public:
        if h.released_on is None:
            raise HeaderParseError("private-to-public header carries no release date")
        return h.released_on
    if h.accepted_at is not None:
        return edgar_night(h.accepted_at)
    d = h.filing_date or index_filed
    if d is None:
        raise HeaderParseError("header carries no acceptance time or filing date")
    return d


def header_url(cik: str, accession: str) -> str:
    return (
        f"{get_settings().sec_archives_base}/{int(cik)}/{accession.replace('-', '')}/"
        f"{accession}.hdr.sgml"
    )


def header_cache_key(accession: str) -> str:
    """Under ``forms/<accession>/`` — the IMMUTABLE key class: a filing's header never changes."""
    return f"forms/{accession}/hdr.sgml"


def _index_url(cik10: str, accession: str) -> str:
    return (
        f"{get_settings().sec_archives_base}/{int(cik10)}/{accession.replace('-', '')}/"
        f"{accession}-index.htm"
    )


def rows_from_header(
    h: FundHeader, f: IndexFiling
) -> tuple[fund_repo.FundFilingRow, list[fund_repo.FundSeriesRow]]:
    pub = public_date_of(h, index_filed=f.filed)
    ciks = [x.cik for x in h.filers] or [f.cik.zfill(10)]
    name = h.filers[0].name if h.filers and h.filers[0].name else f.company
    filing = fund_repo.FundFilingRow(
        accession=h.accession,
        form=h.form,
        filer_ciks=ciks,
        filer_name=name,
        accepted_at=h.accepted_at,
        filing_date=h.filing_date,
        effectiveness_date=h.effectiveness_date,
        private_to_public=h.private_to_public,
        released_on=h.released_on,
        public_date=pub,
        source_ref=_index_url(ciks[0], h.accession),
    )
    series: list[fund_repo.FundSeriesRow] = []
    seen: set[str] = set()
    # A header may list one series more than once (a series with new classes ALSO appears under the existing
    # contracts): one row per series ID, the strongest kind (new > new_classes > existing), classes unioned.
    merged: dict[str, dict[str, Any]] = {}
    for s in h.series:
        m = merged.setdefault(s.series_id, {"name": s.name, "kind": s.kind, "classes": {}})
        if _KIND_RANK[s.kind] < _KIND_RANK[m["kind"]]:
            m["kind"] = s.kind
        if s.name and not m["name"]:
            m["name"] = s.name
        for c in s.classes:
            m["classes"].setdefault(
                c.class_id, {"class_id": c.class_id, "name": c.name, "ticker": c.ticker}
            )
    for sid, m in merged.items():
        seen.add(sid)
        series.append(
            fund_repo.FundSeriesRow(
                accession=h.accession,
                series_key=sid,
                series_id=sid,
                series_name=m["name"],
                kind=m["kind"],
                classes=list(m["classes"].values()),
                public_date=pub,
            )
        )
    if not h.series and h.form in S1_FORMS:
        # an S-1 header names no fund: the filer's name IS the fund name (a name-only row)
        for filer in h.filers or ():
            key = f"cik:{filer.cik}"
            if key in seen:
                continue
            seen.add(key)
            series.append(
                fund_repo.FundSeriesRow(
                    accession=h.accession,
                    series_key=key,
                    series_id=None,
                    series_name=filer.name or f.company,
                    kind=KIND_NEW,
                    classes=[],
                    public_date=pub,
                )
            )
    return filing, series


def run_fund_radar(
    conn: psycopg.Connection,
    *,
    until: date | None = None,
    days: int = 3,
    allow_live: bool = True,
    user_agent: str | None = None,
    edgar_client: EdgarClient | None = None,
    tenant_id: UUID = DATA_TENANT_ID,
    symbol_files: bool = True,
    symbol_night: date | None = None,
    nasdaq_fetch: Callable[[str], str] | None = None,
) -> FundRadarResult:
    """One radar pass: scan the daily indexes for ``days`` days ending at ``until`` (default today), read each
    fund filing's header, store the facts, then (``symbol_files``) record the symbols the SEC ticker file and
    the Nasdaq directory show for the series we hold. Idempotent over a re-scan (append-only if-changed).

    ``symbol_files=False`` is the backfill's day-by-day pass: neither symbol file is dated, so a backfill marks
    them once, tonight (``run_symbol_legs``), never once per replayed day. ``nasdaq_fetch`` is the Nasdaq files'
    HTTP seam (tests inject one); by default a live run uses the polite GET and a no-live run reads nothing.
    """
    client = edgar_client or EdgarClient(
        allow_live=allow_live, user_agent=user_agent, cache_ttl_s=RECURRING_CACHE_TTL_S
    )
    until = until or market_today()
    result = FundRadarResult()
    seen_accessions: set[str] = set()

    for offset in range(days - 1, -1, -1):  # oldest → newest
        d = until - timedelta(days=offset)
        try:
            filings = fetch_daily_index(client, d)
        except CacheMiss:
            result.dates_skipped.append(f"{d} (not cached)")
            continue
        except Exception as e:  # noqa: BLE001 — 403/404 = a no-index day, anything else is an error
            if _is_no_index_day(e):
                result.dates_skipped.append(str(d))
            else:
                result.errors.append(f"{d}: index fetch: {e}")
            continue
        result.dates_scanned.append(str(d))
        for f in filings:
            # the index lists one row per co-registrant: dedupe by accession
            if not keep_filing(f) or f.accession in seen_accessions:
                continue
            seen_accessions.add(f.accession)
            result.filings_seen += 1
            try:
                text = client.get_text(
                    header_url(f.cik, f.accession), header_cache_key(f.accession)
                )
                header = parse_header(text)
                filing_row, series_rows = rows_from_header(header, f)
            except (
                Exception
            ) as e:  # noqa: BLE001 — per-filing isolation; stored nothing, retried tomorrow
                result.errors.append(f"{d} {f.form} {f.accession}: header: {e}")
                continue
            if fund_repo.record_filing_if_changed(conn, filing_row, tenant_id=tenant_id):
                result.filings_appended += 1
            else:
                result.filings_unchanged += 1
            for s in series_rows:
                if fund_repo.record_series_if_changed(conn, s, tenant_id=tenant_id):
                    result.series_appended += 1
                else:
                    result.series_unchanged += 1
        conn.commit()

    if symbol_files:
        run_symbol_legs(
            conn,
            client,
            night=symbol_night or market_today(),
            allow_live=allow_live,
            nasdaq_fetch=nasdaq_fetch,
            result=result,
            tenant_id=tenant_id,
        )

    result.edgar_fetches = client.live_fetches
    return result


# --- the two symbol files -------------------------------------------------------------------------------


def parse_sec_ticker_file(doc: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``company_tickers_mf.json`` → ``(series_id, class_id, symbol)`` rows (a row missing a key is skipped:
    exact mappings only, as ``securities.fund_tickers``)."""
    fields = doc.get("fields")
    data = doc.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ValueError("SEC company_tickers_mf.json: unexpected shape")
    idx = {f: i for i, f in enumerate(fields)}
    out: list[tuple[str, str, str]] = []
    for r in data:
        sid, cid, sym = r[idx["seriesId"]], r[idx["classId"]], r[idx["symbol"]]
        if sid and sym:
            out.append((str(sid), str(cid or ""), str(sym).upper()))
    return out


def parse_nasdaq_etf_symbols(text: str) -> set[str]:
    """The symbols a Nasdaq Trader directory file lists as an ETF (``ETF`` = Y) and not a test issue
    (``Test Issue`` = N). Both files are pipe-delimited with a header row and a trailing ``File Creation
    Time`` line; a symbol is taken under every symbol column the file has (``Symbol`` / ``ACT Symbol`` /
    ``NASDAQ Symbol`` / ``CQS Symbol``) so a class-share spelling (BRK.B / BRK B) cannot miss."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty symbol directory")
    header = [h.strip() for h in lines[0].split("|")]
    if "ETF" not in header or "Test Issue" not in header:
        raise ValueError("symbol directory: no ETF / Test Issue columns")
    i_etf, i_test = header.index("ETF"), header.index("Test Issue")
    sym_cols = [
        header.index(c)
        for c in ("Symbol", "ACT Symbol", "NASDAQ Symbol", "CQS Symbol")
        if c in header
    ]
    out: set[str] = set()
    for ln in lines[1:]:
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) != len(header):  # the trailing "File Creation Time: ..." line
            continue
        if parts[i_etf] != "Y" or parts[i_test] != "N":
            continue
        for i in sym_cols:
            if parts[i]:
                out.add(parts[i].upper())
    return out


def _default_nasdaq_fetch(url: str) -> str:
    # not an SEC host: never send it the SEC contact User-Agent; use the repo's generic research UA
    return polite_get(
        url, headers={"User-Agent": _GENERIC_UA}, timeout=get_settings().http_timeout_s
    ).text


def run_symbol_legs(
    conn: psycopg.Connection,
    client: EdgarClient,
    *,
    night: date,
    allow_live: bool,
    nasdaq_fetch: Callable[[str], str] | None,
    result: FundRadarResult,
    tenant_id: UUID = DATA_TENANT_ID,
) -> None:
    """Record, for every series we hold, the symbols the SEC ticker file and the Nasdaq directory show — dated
    ``night`` (the first night we saw them). Fail-open and visible: a file that cannot be read is listed on the
    result (``symbols_skipped`` for a cache miss with live pulls off, ``errors`` for a real fault) and the other
    leg still runs."""
    # only the funds a filing announced as NEW are followed to a ticker and a listing (a 485BPOS lists hundreds of
    # long-standing mutual-fund classes; their symbols are not launches)
    tracked = {
        v.series_id
        for v in fund_repo.series_views(conn, tenant_id=tenant_id)
        if v.series_id and v.kind == KIND_NEW
    }

    # 1. the SEC fund ticker file, through the SEC client (the global budget; a mutable key class)
    try:
        doc = client.get_json(get_settings().sec_company_tickers_mf_url, SEC_TICKER_CACHE_KEY)
        for sid, cid, sym in parse_sec_ticker_file(doc):
            if sid in tracked and fund_repo.record_symbol_if_new(
                conn,
                fund_repo.SymbolRow(sid, cid, sym, fund_repo.SOURCE_SEC, night),
                tenant_id=tenant_id,
            ):
                result.symbols_appended += 1
        conn.commit()
    except CacheMiss:
        result.symbols_skipped.append("SEC fund ticker file (not cached)")
    except Exception as e:  # noqa: BLE001 — fail-open, fail-visible
        conn.rollback()
        result.errors.append(f"SEC fund ticker file: {e}")

    # 2. the Nasdaq directory, for every symbol we hold (SEC file rows + the tickers a header listed)
    # live only when BOTH the caller and the SEC client allow it (a cache-only client means a cache-only run)
    live = allow_live and getattr(client, "allow_live", True)
    fetch = nasdaq_fetch or (_default_nasdaq_fetch if live else None)
    if fetch is None:
        result.symbols_skipped.append("Nasdaq directory (live pulls off)")
        return
    held: set[tuple[str, str, str]] = set()
    for r in fund_repo.symbol_rows(conn, tenant_id=tenant_id):
        if r.source == fund_repo.SOURCE_SEC:
            held.add((r.series_id, r.class_id, r.symbol))
    for v in fund_repo.series_views(conn, tenant_id=tenant_id):
        if v.series_id in tracked:
            for c in v.classes:
                if c.get("ticker"):
                    held.add((v.series_id, c.get("class_id") or "", str(c["ticker"]).upper()))
    etf_symbols: set[str] = set()
    read_any = False
    for url in (NASDAQ_TRADED_URL, NASDAQ_OTHER_URL):
        try:
            etf_symbols |= parse_nasdaq_etf_symbols(fetch(url))
            read_any = True
        except Exception as e:  # noqa: BLE001 — one file down never costs the other
            result.errors.append(f"Nasdaq directory {url.rsplit('/', 1)[-1]}: {e}")
    if not read_any:
        return
    for sid, cid, sym in sorted(held):
        if sym in etf_symbols and fund_repo.record_symbol_if_new(
            conn,
            fund_repo.SymbolRow(sid, cid, sym, fund_repo.SOURCE_NASDAQ, night),
            tenant_id=tenant_id,
        ):
            result.symbols_appended += 1
    conn.commit()
