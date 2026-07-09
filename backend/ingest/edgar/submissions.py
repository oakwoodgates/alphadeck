from __future__ import annotations

import re
from typing import Any

from domain.security import SecurityIdentity
from domain.settings import get_settings
from ingest.edgar.client import EdgarClient

# EDGAR joins multiple filer-category attributes with a literal "<br>" (e.g. "Accelerated filer<br>Emerging
# growth company"). Strip any HTML tag → a clean " · "-joined string so the identity chip never shows raw markup.
_HTML_TAG = re.compile(r"<[^>]+>")


def submissions_url(cik: str | int) -> str:
    return f"{get_settings().sec_data_base}/submissions/CIK{int(cik):010d}.json"


def parse_identity(submissions: dict[str, Any]) -> SecurityIdentity:
    """Parse descriptive IDENTITY from a submissions JSON: sector (``sicDescription``), exchange (the first of
    ``exchanges``), a listing-presence ``status``, the SEC filer ``category`` (a maturity/size tell, e.g. "Large
    accelerated filer" vs "Smaller reporting company"), and ``formerNames`` (parsed for the later identity bridge).

    ``status`` is a HEURISTIC, not a delisting feed: a filer with a current ticker AND a current exchange reads
    ``"active"``; otherwise ``"inactive"`` (no current listing found in EDGAR). It must never be surfaced as a
    hard "delisted" verdict — the operator-facing label stays a hedged guess. ``category`` is EDGAR's own
    filing-status string surfaced verbatim (identity, never a number #1/#3) — ``None`` when the filer omits it.

    Pure (no I/O) — feed it the dict from ``fetch_submissions``. Machine-parsed identity, never a fact (#1/#3).
    Tolerates a sparse/old submissions (missing keys) without raising.
    """
    sector = (submissions.get("sicDescription") or "").strip() or None
    exchanges = [str(e).strip() for e in (submissions.get("exchanges") or []) if e]
    tickers = [str(t).strip() for t in (submissions.get("tickers") or []) if t]
    exchange = exchanges[0] if exchanges else None
    status = "active" if (tickers and exchanges) else "inactive"
    # EDGAR uses "<br>" to join multiple category attributes — strip HTML tags to a clean " · "-joined string
    # (never surface raw markup). e.g. "Non-accelerated filer<br>Smaller reporting company".
    category = _HTML_TAG.sub(" · ", submissions.get("category") or "")
    category = re.sub(r"\s+", " ", category).strip(" ·") or None
    former_names = [
        {"name": name, "from": fn.get("from") or "", "to": fn.get("to") or ""}
        for fn in (submissions.get("formerNames") or [])
        if (name := (fn.get("name") or "").strip())
    ]
    return SecurityIdentity(
        sector=sector,
        exchange=exchange,
        status=status,
        category=category,
        former_names=former_names,
    )


def fetch_submissions(client: EdgarClient, cik: str | int) -> dict[str, Any]:
    return client.get_json(submissions_url(cik), f"submissions/CIK{int(cik):010d}.json")


def filings_of(submissions: dict[str, Any], form: str) -> list[dict[str, str]]:
    """List a company's filings of one ``form`` type (newest first) from a submissions JSON:
    ``{accession, primary_doc, filed, report_date}``. ``filed`` is the FILING date; ``report_date`` is
    the PERIOD OF REPORT (the quarter/year end the filing covers) — two different dates ~a month apart
    on a 10-Q, and the distinction is load-bearing: the shares extractor's staleness gate compares a
    cover "as of" date (which falls BETWEEN period end and filing date) against the period end, so
    threading ``filed`` where the period belongs made that gate unreachable live (every single-class
    name mis-flagged "dual-class"). The submissions ``recent`` arrays are parallel + reverse-chrono,
    so the first match is the latest (e.g. ``filings_of(subs, "10-Q")[0]`` = the most recent 10-Q).
    ``report_date`` is "" when the row lacks one (defensive — some form types omit it).
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    reports = recent.get("reportDate", [])
    return [
        {
            "accession": accns[i],
            "primary_doc": docs[i],
            "filed": dates[i],
            "report_date": reports[i] if i < len(reports) else "",
        }
        for i, f in enumerate(forms)
        if f == form
    ]


def form4_filings(submissions: dict[str, Any]) -> list[dict[str, str]]:
    """List Form 4 filings from a submissions JSON: ``{accession, primary_doc, filed}``."""
    return filings_of(submissions, "4")


def form4_doc_url(cik: str | int, accession: str, primary_doc: str) -> str:
    """The EDGAR Archives URL for a filing's RAW ownership XML.

    ``primary_doc`` from submissions is the XSL-rendered path (e.g. ``xslF345X06/wk-form4_*.xml``);
    the parseable raw XML is the same filename in the accession root, so we drop the ``xsl.../`` dir.
    """
    doc = primary_doc.rsplit("/", 1)[-1]
    return f"{get_settings().sec_archives_base}/{int(cik)}/{accession.replace('-', '')}/{doc}"
