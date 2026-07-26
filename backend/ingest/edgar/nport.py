"""N-PORT holdings for one fund SERIES — locate, fetch, parse (ETF Sleeve, Slice 2a).

A fund trust files N-PORT PER SERIES under the TRUST's CIK (Global X Funds, CIK 1432353, files ~100/yr
across all its series — LIT is exactly one series of it). The trust-level submissions JSON mixes every
series together, so the locator uses the ONE EDGAR surface that filters by series id:
``browse-edgar?action=getcompany&CIK=<seriesId S000…>&type=NPORT-P&output=atom`` — validated live
(2026-07-26, LIT S000029441): one fetch returns that series' filings only. ``type=NPORT-P``
prefix-matches ``NPORT-P/A`` amendments too; the observed real shape is an /A filed alongside (same
day as) its original, amending the SAME report period with corrected holdings — so "latest" is
newest-FILED first, an amendment winning a same-day tie (it is the correction).

Cache classes (the key-classed freshness model, ``EdgarClient``):
- the series→filings ATOM is MUTABLE (a new quarter's filing appends) → a non-``forms/`` key, default
  12h TTL;
- an accession's ``primary_doc.xml`` is IMMUTABLE (a filed document never changes) → the ``forms/``
  cache-forever prefix. Every electronic N-PORT's primary document is literally named
  ``primary_doc.xml`` (verified across filing agents + years: Global X 2024–2026, ARK 2026).

No-lookahead (#1): ``asof`` keeps only filings with ``filed <= asof`` — the filing must have EXISTED at
the simulated time. (The ATOM carries no period-of-report; ``filed <= asof`` is the stronger condition
and implies the report period predates ``asof`` too, since a period ends before its filing. The parsed
doc's ``repPdDate`` is the vintage LABEL the caller shows.)

The parser is PURE (no I/O), namespaced-XML aware (local-name matching — the doc's default namespace is
``http://www.sec.gov/edgar/nport``), and defensive: a missing/``N/A`` field is ``None``, never a crash.
Real identifier coverage varies by FILING AGENT, measured live: Global X's LIT carries CUSIP/ISIN but
ZERO equity tickers; ARK's ARKK carries tickers on 44/46 holdings — so a ticker-keyed overlap match is
honest-but-sparse for some funds until the CUSIP→ticker upgrade (Slice 2b), and every unmatched holding
must stay VISIBLE (#9), never dropped.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

from domain.coerce import to_float
from domain.settings import get_settings
from ingest.edgar.client import EdgarClient

# The archives path key is the TRUST's CIK — parsed from the entry's filing-href (browse-edgar builds it
# from the series' parent trust; N-PORT accessions are prefixed with the filing AGENT's CIK, which must
# never be used for the URL — the same trap `ciks_for` documents).
_HREF_CIK = re.compile(r"/Archives/edgar/data/(\d+)/")

# The ATOM window: newest-first filings fetched per series. ~4 N-PORTs/yr (quarterlies + amendments) →
# 40 covers ~10 years; an ``asof`` older than the window honestly resolves to None (no filing knowable).
_ATOM_COUNT = 40


@dataclass(frozen=True)
class NportFilingRef:
    """One located N-PORT filing for a series: the accession + when it was FILED (the knowability date,
    #1), the TRUST's CIK (the archives path key), whether it is an /A amendment, and the human filing
    index URL (the ``source_ref`` provenance link, #6)."""

    accession: str
    filed: date
    trust_cik: str
    is_amendment: bool
    index_url: str


@dataclass(frozen=True)
class Holding:
    """One ``invstOrSec`` position, identifiers normalized (``N/A``/empty → ``None`` — the SEC's explicit
    not-applicable must never be matched as a real identifier)."""

    name: str | None
    cusip: str | None
    isin: str | None
    ticker: str | None
    val_usd: float | None
    pct_val: float | None


@dataclass(frozen=True)
class NportReport:
    """A parsed N-PORT: whose holdings these are (``series_id`` — verified against the requested series
    by the caller), the report-period vintage (``report_date`` — the as-of LABEL, ~quarter-end and ~60
    days lagged, fine for a discovery seed), and the positions."""

    series_id: str | None
    report_date: date | None
    series_name: str | None
    holdings: list[Holding] = field(default_factory=list)


def browse_nport_url(series_id: str) -> str:
    """The series-filtered browse-edgar ATOM URL (no ``dateb`` — the cache key must not vary by asof;
    ``asof`` filters client-side over ONE cached window)."""
    return (
        f"{get_settings().sec_browse_edgar_url}?action=getcompany&CIK={series_id}"
        f"&type=NPORT-P&dateb=&owner=include&count={_ATOM_COUNT}&output=atom"
    )


def latest_nport_accession(
    client: EdgarClient, series_id: str, *, asof: date | None = None
) -> NportFilingRef | None:
    """Locate the series' latest N-PORT filing (one ATOM fetch, mutable-cached ``browse-nport/`` key).

    ``asof`` (#1): only filings with ``filed <= asof`` are candidates — what was knowable then. Pick =
    newest ``filed``; a same-day NPORT-P/A beats its original (the correction); accession string breaks
    any remaining tie deterministically. ``None`` when the series has no (knowable) N-PORT in the window.
    """
    atom = client.get_text(browse_nport_url(series_id), f"browse-nport/{series_id}.atom")
    candidates: list[tuple[date, bool, str, NportFilingRef]] = []
    for entry in ET.fromstring(atom).findall("{*}entry"):
        content = entry.find("{*}content")
        if content is None:
            continue
        accession = (content.findtext("{*}accession-number") or "").strip()
        filed_raw = (content.findtext("{*}filing-date") or "").strip()
        ftype = (content.findtext("{*}filing-type") or "").strip()
        href = (content.findtext("{*}filing-href") or "").strip()
        if not accession or not filed_raw or not ftype.startswith("NPORT-P"):
            continue  # defensive: a malformed entry is skipped, never a crash
        try:
            filed = date.fromisoformat(filed_raw)
        except ValueError:
            continue
        if asof is not None and filed > asof:
            continue  # not knowable at asof (#1)
        cik_m = _HREF_CIK.search(href)
        if cik_m is None:
            continue  # no trust CIK -> no archives URL to fetch; skip visibly-nothing, never guess
        ref = NportFilingRef(
            accession=accession,
            filed=filed,
            trust_cik=f"{int(cik_m.group(1)):010d}",
            is_amendment=ftype.endswith("/A"),
            index_url=href,
        )
        candidates.append((filed, ref.is_amendment, accession, ref))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    return candidates[0][3]


def nport_doc_url(trust_cik: str | int, accession: str) -> str:
    """The EDGAR Archives URL for an N-PORT's raw primary XML (every electronic N-PORT names it
    ``primary_doc.xml``). Built from the TRUST's CIK — never the accession's filing-agent prefix."""
    return (
        f"{get_settings().sec_archives_base}/{int(trust_cik)}/"
        f"{accession.replace('-', '')}/primary_doc.xml"
    )


def fetch_nport(client: EdgarClient, trust_cik: str | int, accession: str) -> str:
    """Fetch one N-PORT primary doc — IMMUTABLE ``forms/`` cache class (an accession's document never
    changes; paid for once, ever)."""
    return client.get_text(
        nport_doc_url(trust_cik, accession), f"forms/{accession}/primary_doc.xml"
    )


def _clean(value: str | None) -> str | None:
    """Normalize an identifier: strip; ``N/A``/empty → ``None`` (the SEC's explicit not-applicable —
    matching it as a real identifier would cross-join every N/A row)."""
    if value is None:
        return None
    v = value.strip()
    return None if not v or v.upper() == "N/A" else v


def parse_nport_holdings(xml: str) -> NportReport:
    """Parse an N-PORT primary doc into its series identity + holdings. PURE (no I/O).

    Local-name matching throughout (the doc rides the ``edgar/nport`` default namespace). Defensive to
    the ``parse_identity`` standard: a sparse/odd doc yields ``None`` fields and whatever holdings ARE
    readable — never a raise mid-walk that would silently drop the rest (#9). ``valUSD``/``pctVal``
    coerce via ``to_float`` (malformed → ``None``).
    """
    root = ET.fromstring(xml)
    gen = root.find(".//{*}genInfo")
    series_id = _clean(gen.findtext("{*}seriesId")) if gen is not None else None
    if series_id is None:
        # fall back to the header's seriesClassInfo (both carry it on a well-formed doc)
        series_id = _clean(root.findtext(".//{*}seriesClassInfo/{*}seriesId"))
    series_name = _clean(gen.findtext("{*}seriesName")) if gen is not None else None
    report_date: date | None = None
    rep_raw = _clean(gen.findtext("{*}repPdDate")) if gen is not None else None
    if rep_raw is not None:
        try:
            report_date = date.fromisoformat(rep_raw)
        except ValueError:
            report_date = None
    holdings: list[Holding] = []
    for sec in root.findall(".//{*}invstOrSec"):
        isin_el = sec.find("{*}identifiers/{*}isin")
        ticker_el = sec.find("{*}identifiers/{*}ticker")
        try:
            val_usd = to_float(_clean(sec.findtext("{*}valUSD")))
        except ValueError:
            val_usd = None
        try:
            pct_val = to_float(_clean(sec.findtext("{*}pctVal")))
        except ValueError:
            pct_val = None
        holdings.append(
            Holding(
                name=_clean(sec.findtext("{*}name")),
                cusip=_clean(sec.findtext("{*}cusip")),
                isin=_clean(isin_el.get("value")) if isin_el is not None else None,
                ticker=_clean(ticker_el.get("value")) if ticker_el is not None else None,
                val_usd=val_usd,
                pct_val=pct_val,
            )
        )
    return NportReport(
        series_id=series_id,
        report_date=report_date,
        series_name=series_name,
        holdings=holdings,
    )
