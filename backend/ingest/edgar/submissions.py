from __future__ import annotations

from typing import Any

from domain.settings import get_settings
from ingest.edgar.client import EdgarClient


def submissions_url(cik: str | int) -> str:
    return f"{get_settings().sec_data_base}/submissions/CIK{int(cik):010d}.json"


def fetch_submissions(client: EdgarClient, cik: str | int) -> dict[str, Any]:
    return client.get_json(submissions_url(cik), f"submissions/CIK{int(cik):010d}.json")


def filings_of(submissions: dict[str, Any], form: str) -> list[dict[str, str]]:
    """List a company's filings of one ``form`` type (newest first) from a submissions JSON:
    ``{accession, primary_doc, filed}``. The submissions ``recent`` arrays are parallel + reverse-chrono,
    so the first match is the latest (e.g. ``filings_of(subs, "10-Q")[0]`` = the most recent 10-Q).
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    return [
        {"accession": accns[i], "primary_doc": docs[i], "filed": dates[i]}
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
