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

    ORIGIN ingredients (migration 0028) — four raw locators for the derive-on-read origin chip
    (``securities/origin.py``): ``incorporation`` (``stateOfIncorporationDescription`` — "Cayman Islands", or
    a US state abbrev like "CA"), ``business_city`` / ``business_country`` (``addresses.business`` — the SEC
    quirk: for US entities ``stateOrCountryDescription`` holds the US STATE abbreviation, not "United States",
    and for the China-ADR class it is often null while ``city`` ("SHANGHAI") is the only populated locator),
    and ``files_foreign_forms`` (a 20-F or 40-F in ``filings.recent.form``). Blank/missing → None.

    FILER-FORM ingredients (migration 0031) — for the derive-on-read foreign-filer explainability tell
    (``securities/filer_coverage.py``): ``recent_foreign_form`` (the newer of a 20-F vs 40-F in
    ``filings.recent.form`` — "20-F" FPI · "40-F" Canadian-MJDS; the two regimes are mutually exclusive, so
    single-form is the common case, and ``filed`` breaks the rare both-present tie) and
    ``files_domestic_forms`` (a 10-K or 10-Q present — the domestic veto that kills the legacy-foreign-form
    false positive, e.g. Energy Fuels' stale 40-F). Neither present → None/False (the tell abstains).

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
    # Origin ingredients — defensive over sparse/old docs (missing/None keys tolerated throughout).
    business = (submissions.get("addresses") or {}).get("business") or {}
    incorporation = (submissions.get("stateOfIncorporationDescription") or "").strip() or None
    business_city = (business.get("city") or "").strip() or None
    business_country = (business.get("stateOrCountryDescription") or "").strip() or None
    # Foreign-form filings (each list newest-first): reused for both the 0028 bool and the 0031 form string.
    f20 = filings_of(submissions, "20-F")
    f40 = filings_of(submissions, "40-F")
    files_foreign_forms = bool(f20 or f40)
    # recent_foreign_form — the newer of the two by FILING date. Single-form is the common case (the FPI vs
    # MJDS regimes are mutually exclusive); compare ``filed`` only when both are present (an ISO date string,
    # so a lexicographic compare is chronological — 20-F wins an exact tie, deterministically).
    if f20 and f40:
        recent_foreign_form: str | None = "20-F" if f20[0]["filed"] >= f40[0]["filed"] else "40-F"
    elif f20:
        recent_foreign_form = "20-F"
    elif f40:
        recent_foreign_form = "40-F"
    else:
        recent_foreign_form = None
    # files_domestic_forms — the domestic veto: a recent 10-K/10-Q means the issuer DOES file Form 4, so the
    # foreign-filer tell must abstain even with a stale foreign form on file (the Energy-Fuels/UUUU case).
    files_domestic_forms = bool(filings_of(submissions, "10-K") or filings_of(submissions, "10-Q"))
    return SecurityIdentity(
        sector=sector,
        exchange=exchange,
        status=status,
        category=category,
        former_names=former_names,
        incorporation=incorporation,
        business_city=business_city,
        business_country=business_country,
        files_foreign_forms=files_foreign_forms,
        files_domestic_forms=files_domestic_forms,
        recent_foreign_form=recent_foreign_form,
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


def parse_item_codes(raw: str | None) -> list[str] | None:
    """Parse EDGAR's comma-joined 8-K ``items`` string ("1.01,9.01") into item codes, or ``None``.

    ``None`` = UNKNOWN — the submissions JSON has not resolved the filing's items yet (or the entry
    is blank); an empty string parses to ``None`` too, never ``[]``, so "unresolved" and "no items"
    can't silently conflate. The ONE items parser — the SPAC radar (``radar/spac.py::_items_for``)
    and the 8-K corporate-event ingest (``ingest/edgar/form8k.py``) both call this.
    """
    return [s.strip() for s in (raw or "").split(",") if s.strip()] or None


def form8k_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    """List a company's 8-K filings (8-K + 8-K/A, newest first) from a submissions JSON, WITH their
    item codes: ``{accession, form, filed, items}`` (``items``: ``list[str] | None`` — None = the
    parallel ``items`` entry is absent/blank, i.e. not yet resolved).

    The submissions ``recent`` arrays are parallel + reverse-chrono; the ``items`` array parallels
    ``accessionNumber``, so ONE walk captures the whole tape — no per-filing document fetch. Same
    accepted depth as ``form4_filings``: ``recent`` covers >= 1 year / 1,000 filings (the deferred
    cadence-baseline slice may need the paginated older pages; this slice does not). Tolerates a
    submissions doc with no ``items`` array (every row reads unresolved), so an old/sparse doc
    degrades honestly rather than raising.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    return [
        {
            "accession": accns[i],
            "form": f,
            "filed": dates[i],
            "items": parse_item_codes(items[i] if i < len(items) else None),
        }
        for i, f in enumerate(forms)
        if f in ("8-K", "8-K/A")
    ]


# The 13D/G form-type universe, BOTH naming eras (the S5 rename trap): EDGAR renamed the form type
# from the classic "SC 13D" strings to "SCHEDULE 13D" when the structured-XML requirement landed
# (cutover ~2024-12-18 — measured on real subjects: the same issuer's tape flips SC 13G ->
# SCHEDULE 13G between 2024-11-14 and 2025-02-05). An SC-only match silently drops every 2025+
# filing (#9); the D/G split is the detector's fire boundary (13D = intent, 13G = passive).
SCHEDULE13_D_FORMS = frozenset({"SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"})
SCHEDULE13_G_FORMS = frozenset({"SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"})
SCHEDULE13_FORMS = SCHEDULE13_D_FORMS | SCHEDULE13_G_FORMS


def schedule13_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    """List the 13D/G-family filings a company is the SUBJECT of (newest first) from ITS OWN
    submissions JSON: ``{accession, form, filed, primary_doc}``.

    The S5-verified enumeration path (a): EDGAR indexes an ownership schedule under BOTH the filer
    and the subject, so the SUBJECT's submissions JSON lists every 13D/G filed about it — the same
    document the Form 4 / 8-K legs already fetch, zero extra enumeration fetches. Matches ALL EIGHT
    form strings across both naming eras (``SCHEDULE13_FORMS`` above). ``primary_doc`` rides along
    because the structured era's raw XML (filer identity + %-owned evidence) is addressed from it.
    Same accepted depth as ``form4_filings``: ``recent`` covers >= 1 year / 1,000 filings — full
    history for the measured microcap subjects, and the detector's months-scale liveness never
    needs deeper.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    return [
        {
            "accession": accns[i],
            "form": f,
            "filed": dates[i],
            "primary_doc": docs[i] if i < len(docs) else "",
        }
        for i, f in enumerate(forms)
        if f in SCHEDULE13_FORMS
    ]


def form4_doc_url(cik: str | int, accession: str, primary_doc: str) -> str:
    """The EDGAR Archives URL for a filing's RAW primary document.

    ``primary_doc`` from submissions is the XSL-rendered path (e.g. ``xslF345X06/wk-form4_*.xml``,
    ``xslSCHEDULE_13D_X01/primary_doc.xml``); the parseable raw document is the same filename in the
    accession root, so we drop the ``xsl.../`` dir. The logic is form-agnostic — the Form 4 leg and
    the S5 13D/G identity fetch (``ingest/edgar/schedule13.py``) share this one implementation.
    """
    doc = primary_doc.rsplit("/", 1)[-1]
    return f"{get_settings().sec_archives_base}/{int(cik)}/{accession.replace('-', '')}/{doc}"
