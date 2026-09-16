from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from domain.security import SecurityIdentity
from domain.settings import get_settings
from ingest import CacheMiss
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
    ``{accession, primary_doc, filed, report_date, accepted}``. ``filed`` is the FILING date;
    ``report_date`` is the PERIOD OF REPORT (the quarter/year end the filing covers) — two different
    dates ~a month apart on a 10-Q, and the distinction is load-bearing: the shares extractor's
    staleness gate compares a cover "as of" date (which falls BETWEEN period end and filing date)
    against the period end, so threading ``filed`` where the period belongs made that gate unreachable
    live (every single-class name mis-flagged "dual-class"). ``accepted`` is the raw SEC
    ``acceptanceDateTime`` string (the moment the filing became public — the real "disclosed" clock;
    ``""`` when the row lacks one) — free here (a parallel array we already have; the insider leg finally
    reads it instead of discarding it). The submissions ``recent`` arrays are parallel + reverse-chrono,
    so the first match is the latest (e.g. ``filings_of(subs, "10-Q")[0]`` = the most recent 10-Q).
    ``report_date`` is "" when the row lacks one (defensive — some form types omit it).
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    reports = recent.get("reportDate", [])
    accepts = recent.get("acceptanceDateTime", [])
    return [
        {
            "accession": accns[i],
            "primary_doc": docs[i],
            "filed": dates[i],
            "report_date": reports[i] if i < len(reports) else "",
            "accepted": accepts[i] if i < len(accepts) else "",
        }
        for i, f in enumerate(forms)
        if f == form
    ]


def form4_filings(submissions: dict[str, Any]) -> list[dict[str, str]]:
    """List Form 4 filings from a submissions JSON: ``{accession, primary_doc, filed, accepted}``
    (``accepted`` = the raw SEC ``acceptanceDateTime`` — the Form 4 leg threads it into the fact's
    ``accepted`` column, the honest disclosure clock)."""
    return filings_of(submissions, "4")


# The SEC DELISTING forms — the deterministic "this name stopped trading" tell (#3, never an LLM, never a
# guess). Form 25 / 25-NSE = notification of REMOVAL FROM LISTING (the exchange or issuer files it around
# the delisting); Form 15-12B (exchange-listed) / 15-12G (OTC/other) = DEREGISTRATION / suspension of the
# reporting duty, filed after. A name that files any of these has legitimately left the market: its price
# tape correctly ENDS, and the tape monitor must read it "closed — stopped trading", never "stale — repair"
# (a feed gap the operator fixes with security_master.price_symbol). MEASURED in the 2026-09-11 stale-tape
# triage: one of these landed within days of every one of the six real tape ends.
DELISTING_FORMS = frozenset({"25-NSE", "25", "15-12B", "15-12G"})


def delisting_date(submissions: dict[str, Any]) -> date | None:
    """The most recent SEC DELISTING-form filing date in a submissions JSON, or ``None`` when the company
    has filed none.

    Deterministic (#3): the classification comes from the SEC form itself (``DELISTING_FORMS``), never a
    model's reading, the master's ``status`` heuristic, or a guess. The date is the form's FILING date —
    valid-time straight from EDGAR (#1 no-lookahead), the honest "removed from listing / deregistered
    around here" marker. A stopped price tape WITH one of these is a name that CLOSED (delisted / acquired
    / deregistered); WITHOUT one it is a feed gap to repair.

    Reads the SAME ``filings.recent`` arrays every other leg walks (``filings_of``), so during a daily pass
    it costs ZERO extra fetches — the submissions doc is already cached by the Form 4 leg. MOST RECENT
    (max filed) on purpose: a name that de/re/de-listed carries several, and the latest is the current
    closure. ISO date strings compare lexicographically = chronologically, so ``max`` is the newest.
    Tolerates a sparse/old doc (missing keys, a malformed date) — returns ``None``, never raises.
    """
    filed = [
        f["filed"]
        for form in DELISTING_FORMS
        for f in filings_of(submissions, form)
        if f.get("filed")
    ]
    if not filed:
        return None
    try:
        return date.fromisoformat(max(filed))
    except ValueError:  # a malformed filing date -> abstain (never a guess, never a crash)
        return None


def _acceptance_map(arrays: dict[str, Any]) -> dict[str, str]:
    """``{accession: raw acceptanceDateTime}`` from one block of EDGAR's parallel submission arrays.

    ONE implementation for both shapes the API serves: ``filings.recent`` nests the arrays under two keys,
    while an OLDER page (``filings.files[].name``) carries the very same array names at its TOP level. An
    entry with a blank/absent acceptance simply doesn't appear (the caller leaves the row NULL — #9).
    """
    accns = arrays.get("accessionNumber", [])
    accepts = arrays.get("acceptanceDateTime", [])
    return {
        accns[i]: accepts[i]
        for i in range(min(len(accns), len(accepts)))
        if accns[i] and accepts[i]
    }


def acceptance_times(submissions: dict[str, Any]) -> dict[str, str]:
    """Map every accession in a submissions JSON's ``recent`` window to its raw ``acceptanceDateTime``.

    The ``backfill_accepted`` source: ``filings.recent`` carries ``acceptanceDateTime`` as a parallel
    array beside ``accessionNumber``, so ONE walk maps accession -> acceptance for the whole tape (the
    ownership *document* the forms cache holds does NOT carry it — the enumeration is the only source).
    Depth is ``recent``'s: >= 1 year / 1,000 filings. Older accessions roll into paginated
    ``filings.files[]`` — ``acceptance_times_deep`` walks those.
    """
    return _acceptance_map(submissions.get("filings", {}).get("recent", {}))


# EDGAR names an older submissions page ``CIK<10 digits>-submissions-<3 digits>.json``. The name comes out
# of a fetched document and is then used as BOTH a URL suffix and a cache-file path, so it is validated
# against that exact shape before either — a name carrying ``../`` would otherwise write outside the cache
# dir. A name that fails is skipped and COUNTED (never silently), which is also the honest signal if EDGAR
# ever changes the convention.
_PAGE_NAME = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")


def submissions_page_names(submissions: dict[str, Any]) -> list[str]:
    """The older-page filenames listed in ``filings.files[]`` (newest page first), validated.

    ``filings.files`` is EDGAR's pagination index: each entry is ``{name, filingCount, filingFrom,
    filingTo}`` and the named document holds the next 1,000-2,000 older filings. A company inside the
    ``recent`` window lists none, so this returns ``[]`` and a deep walk costs nothing extra for it.
    """
    files = submissions.get("filings", {}).get("files") or []
    out: list[str] = []
    for f in files:
        name = str((f or {}).get("name") or "").strip()
        if name and _PAGE_NAME.match(name):
            out.append(name)
    return out


def submissions_page_url(name: str) -> str:
    return f"{get_settings().sec_data_base}/submissions/{name}"


def fetch_submissions_page(client: EdgarClient, name: str) -> dict[str, Any]:
    """One older submissions page, through the SAME polite/cached client as every other EDGAR read.

    The cache key sits beside the company's own document (``submissions/<name>``), so the key-classed
    freshness policy treats it as mutable like the rest of that prefix — correct and safe-by-default: an
    older page is effectively closed, so a refresh costs only bandwidth, and nothing here threads a
    per-call freshness flag (``docs/DATA_SOURCES.md`` §cache-freshness).
    """
    if not _PAGE_NAME.match(
        name
    ):  # belt-and-suspenders: never build a path from an unvalidated name
        raise ValueError(f"not an EDGAR submissions page name: {name!r}")
    return client.get_json(submissions_page_url(name), f"submissions/{name}")


def acceptance_times_deep(
    client: EdgarClient, submissions: dict[str, Any]
) -> tuple[dict[str, str], int, int]:
    """``acceptance_times`` PLUS every paginated older page — the full accession -> acceptance map.

    Returns ``(map, pages_read, pages_failed)``. The ``recent`` window is bounded per CIK by FILING COUNT,
    so the heaviest-filing names lose acceptance coverage first; this is the walk that reaches the rest.

    Cost is one extra fetch per older page (cache-first, rate-limited, declared User-Agent — EDGAR
    etiquette is a correctness requirement, not a courtesy). A page that cannot be read is COUNTED and the
    walk continues: its accessions stay unresolved and therefore NULL and visible (#9), never dropped and
    never guessed. ``recent`` wins a key collision, since it is the freshest statement of the same value.
    """
    merged: dict[str, str] = {}
    pages_read = 0
    pages_failed = 0
    for name in submissions_page_names(submissions):
        try:
            page = fetch_submissions_page(client, name)
        except Exception as e:
            if not _tolerable_page_error(e):
                raise  # systemic (no User-Agent, a DB/config fault) — never absorbed into a page tally
            pages_failed += 1
            continue
        pages_read += 1
        merged.update(_acceptance_map(page))
    merged.update(
        acceptance_times(submissions)
    )  # the recent window is authoritative on any overlap
    return merged, pages_read, pages_failed


def _tolerable_page_error(e: Exception) -> bool:
    """Is ``e`` ONE older page's fetch/parse failure rather than a systemic one? Mirrors
    ``pipeline.ingest_thesis._tolerable_filing_error``: an uncached page under ``--no-live``
    (``CacheMiss``), a truncated/garbled document (``ValueError``, which ``json`` raises), or a fetch that
    still fails after the polite retries (``httpx.HTTPError``). A missing User-Agent or any other systemic
    fault is NOT one page's fault and must still abort."""
    if isinstance(e, (CacheMiss, ValueError)):
        return True
    try:
        import httpx  # lazy, mirroring the clients — the package imports without it
    except ImportError:  # pragma: no cover — with httpx absent, no httpx error can have been raised
        return False
    return isinstance(e, httpx.HTTPError)


def parse_acceptance(raw: str | None) -> datetime | None:
    """Parse an EDGAR ``acceptanceDateTime`` ("2025-09-27T18:30:41.000Z") into a tz-aware UTC datetime.

    ``None``/empty -> ``None`` (recall-safe: an unresolved acceptance stays NULL, never a guess — #9).
    Tolerates a trailing ``Z`` and fractional seconds; a value with no offset is assumed UTC. A genuinely
    unparseable value -> ``None`` (never raises inside the ingest / backfill loop — the row simply stays
    NULL and is counted). Stored into the timestamptz ``accepted`` column, so the type matches ``recorded_at``
    and the disclosure-lag metric's ``COALESCE(accepted, recorded_at)`` (``scoreboard/provenance.py``) needs
    no coercion — ``accepted`` is a display/metrics column, never the as-of read gate (that keys on
    ``recorded_at``).
    """
    if not raw:
        return None
    s = raw.strip()
    if s.endswith("Z"):  # EDGAR's UTC suffix — datetime.fromisoformat wants +00:00 (pre-3.11)
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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
