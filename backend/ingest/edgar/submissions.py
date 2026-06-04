from __future__ import annotations

from typing import Any

from ingest.edgar.client import EdgarClient


def submissions_url(cik: str | int) -> str:
    return f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"


def fetch_submissions(client: EdgarClient, cik: str | int) -> dict[str, Any]:
    return client.get_json(submissions_url(cik), f"submissions/CIK{int(cik):010d}.json")


def form4_filings(submissions: dict[str, Any]) -> list[dict[str, str]]:
    """List Form 4 filings from a submissions JSON: ``{accession, primary_doc, filed}``."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    return [
        {"accession": accns[i], "primary_doc": docs[i], "filed": dates[i]}
        for i, form in enumerate(forms)
        if form == "4"
    ]


def form4_doc_url(cik: str | int, accession: str, primary_doc: str) -> str:
    """The EDGAR Archives URL for a filing's primary document."""
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{primary_doc}"
    )
