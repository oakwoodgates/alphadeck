from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from domain.edgar_forms import CORPORATE_EVENT_FORMS, ORDINARY_8K_FORMS
from domain.security import SecurityIdentity
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient, InvalidBody

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
    (``accepted`` = the raw SEC ``acceptanceDateTime`` — the Form 4 leg stores it in the fact's ``accepted``
    column, the public instant on history views, once ``ingest.edgar.acceptance`` has checked it against
    ``filed``)."""
    return filings_of(submissions, "4")


def filed_on_or_after(filings: list[dict[str, Any]], since: date | None) -> list[dict[str, Any]]:
    """X1 — the windowed read for a name whose history is still ONBOARDING (``pipeline/onboarding.py``): keep
    the filings FILED on/after ``since`` (``None`` = every filing, the normal walk). A filing whose ``filed``
    date cannot be read is KEPT — the window may cost a fetch, never a filing (#9). Pure; the nightly's
    per-filing legs and the fetch-ahead apply it through the same callers, so they cannot drift."""
    if since is None:
        return filings
    out = []
    for f in filings:
        try:
            filed = date.fromisoformat(str(f.get("filed") or "")[:10])
        except ValueError:
            out.append(f)
            continue
        if filed >= since:
            out.append(f)
    return out


# The SEC DELISTING forms — the deterministic EVIDENCE a stopped tape may be a close (#3, never an LLM,
# never a guess). Form 25 / 25-NSE = notification of REMOVAL FROM LISTING (the exchange or issuer files it
# around the delisting); Form 15-12B (exchange-listed) / 15-12G (OTC/other) = DEREGISTRATION / suspension of
# the reporting duty, filed after. MEASURED in the 2026-09-11 stale-tape triage, and again on 2026-10-16
# across the 8 genuine closes on prod: one of these landed within [-7, +13] days of every real tape end.
#
# A form ON FILE is not, by itself, a close (T1, 2026-10-16). A company files these for ANY of its listed
# classes, at any point in its life: VerifyMe (VRME) carried a 2025-06-23 Form 25-NSE that removed its
# WARRANTS, then kept trading its common stock for 15 more months. 192 of the 675 still-trading names among
# prod's 688 basket members carry one, so "any form ever filed" would have silenced any of them the night
# its tape stopped. That is why this module only REPORTS the filings (``delisting_filings``)
# and the judgment — is one filed near the tape's last bar, and does EDGAR still list our symbol? — lives
# in ``pipeline/tape_health.py`` beside the edge it needs.
DELISTING_FORMS = frozenset({"25-NSE", "25", "15-12B", "15-12G"})


@dataclass(frozen=True)
class DelistingFiling:
    """One SEC delisting-form filing (a member of ``DELISTING_FORMS``): the form type, its FILING date
    (valid-time straight from EDGAR, #1), the accession, the primary document, and the company's CIK.

    ``accession`` + ``primary_doc`` + ``cik`` ride along so the filing stays citable (#6 — a "closed" row
    can name the exact document behind it) and so the record can open its own document: the Archives URL
    needs the company CIK (``form4_doc_url``). The Form 25 names the security CLASS being removed (common
    stock vs warrants vs a preferred series), the one thing the filing date cannot tell apart — Q6 reads it
    (``ingest/edgar/form25.py``) for the tape monitor. ``cik`` is ``""`` when the submissions doc carries
    none (the record then cannot open its document, and the class reads unknown)."""

    form: str
    filed: date
    accession: str
    primary_doc: str = ""
    cik: str = ""


def delisting_filings(submissions: dict[str, Any]) -> tuple[DelistingFiling, ...]:
    """EVERY SEC delisting-form filing in a submissions JSON, newest first — ``()`` when there are none.

    Deterministic (#3): the evidence is the SEC form itself (``DELISTING_FORMS``), never a model's reading,
    the master's ``status`` heuristic, or a guess. This function deliberately does NOT pick one: which
    filing (if any) explains a stopped tape depends on the tape's LAST BAR, which only the monitor has
    (``pipeline.tape_health.classify_stop`` keeps the one nearest the edge, inside a window). Returning a
    single "most recent ever" date is exactly how a 2025 warrant delisting came to label a common stock that
    stopped in 2026 as "closed" (VRME).

    Reads the SAME ``filings.recent`` arrays every other leg walks, so during a daily pass it costs ZERO
    extra fetches: the Form 4 leg has already cached the submissions doc. ONE walk in EDGAR's own
    (reverse-chrono) row order, then a stable sort on the filing date, so the result is newest first and a
    same-day tie keeps EDGAR's order whatever the form. Tolerates a sparse/old doc: missing keys read as no
    filings, and a row with a blank or malformed date is SKIPPED, never guessed and never a crash.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    cik = str(submissions.get("cik") or "").strip()  # Q6 — so the record can open its own document
    out: list[DelistingFiling] = []
    for i, form in enumerate(forms):
        if form not in DELISTING_FORMS:
            continue
        try:
            filed = date.fromisoformat(dates[i] if i < len(dates) else "")
        except (
            TypeError,
            ValueError,
        ):  # blank/malformed -> not evidence (never a guess, never a raise)
            continue
        out.append(
            DelistingFiling(
                form=form,
                filed=filed,
                accession=accns[i] if i < len(accns) else "",
                primary_doc=docs[i] if i < len(docs) else "",
                cik=cik,
            )
        )
    out.sort(key=lambda d: d.filed, reverse=True)
    return tuple(out)


def edgar_tickers(submissions: dict[str, Any]) -> tuple[str, ...]:
    """The company's CURRENT trading symbols as EDGAR lists them (the submissions ``tickers`` array),
    stripped and upper-cased, in EDGAR's order — ``()`` when EDGAR lists none (or the key is absent).

    Identity, never a fact (#1/#3 govern numbers). The tape monitor's RENAME guard reads it as MEMBERSHIP
    (does the list hold our ``ticker`` or our ``price_symbol``?), never ``tickers[0]``: EDGAR lists every
    current class side by side (``['KYOCF', 'KYOAY']``, ``['ABLZF', 'ABBNY']``, ``['WELPP', 'WELPM']``), so
    a first-element read mis-flags a name priced under its second symbol. ``()`` is honest absence: a
    delisted / deregistered company typically lists none (7 of the 8 genuine closes measured on prod
    2026-10-16), so an empty list is never read as a rename."""
    return tuple(t for t in (str(x).strip().upper() for x in submissions.get("tickers") or []) if t)


@dataclass(frozen=True)
class FormerName:
    """One entry of EDGAR's ``formerNames``: a name the company filed under, and the window it was used
    (``since`` / ``until`` as EDGAR's own date strings, trimmed to ``YYYY-MM-DD``; ``""`` when absent).
    """

    name: str
    since: str = ""
    until: str = ""


@dataclass(frozen=True)
class NameChangeFiling:
    """An 8-K carrying item 5.03 (amendments to the articles / bylaws — where a corporate NAME change is
    reported): its filing date and accession, so a rename recommendation can cite the filing (#6).
    """

    filed: date
    accession: str


def entity_name(submissions: dict[str, Any]) -> str | None:
    """The company's CURRENT registered name as EDGAR states it (``name``), stripped; ``None`` when absent.
    Identity, never a fact — the rename recommender shows it beside the symbol EDGAR now lists ("SDCO — SDR
    Drone, Inc."), and an operator Adopt may refresh the master's display name from it."""
    return (str(submissions.get("name") or "")).strip() or None


def former_names(submissions: dict[str, Any]) -> tuple[FormerName, ...]:
    """EDGAR's ``formerNames`` in its own order (most recent first), blank names skipped — the rename
    recommender's evidence that the company it lists under a new symbol is the SAME filer that used to be
    our name (HLLK: "HALLMARK VENTURE GROUP, INC." until 2026-06-16, now "SDR Drone, Inc."). Tolerates a
    sparse doc (no key -> ``()``)."""
    out: list[FormerName] = []
    for fn in submissions.get("formerNames") or []:
        name = str((fn or {}).get("name") or "").strip()
        if name:
            out.append(
                FormerName(
                    name=name,
                    since=str(fn.get("from") or "")[:10],
                    until=str(fn.get("to") or "")[:10],
                )
            )
    return tuple(out)


def name_change_filings(submissions: dict[str, Any]) -> tuple[NameChangeFiling, ...]:
    """Every 8-K / 8-K/A in the ``recent`` window that carries item 5.03, newest first — the filing a name
    change is reported under. EVIDENCE for a rename recommendation (display only); deterministic from the
    SAME arrays every other leg walks, so it costs nothing on a pass that already cached the doc. A row with
    a malformed date is skipped, never guessed.

    ORDINARY 8-Ks only (``ORDINARY_8K_FORMS``): ``form8k_filings`` also walks the succession reports
    (8-K12B / 8-K12G3 / 8-K15D5) since 2026-10-17, whose 5.03 is the successor's new charter, not a rename —
    so this evidence set is unchanged by that widening."""
    out: list[NameChangeFiling] = []
    for f in form8k_filings(submissions):
        if f["form"] not in ORDINARY_8K_FORMS:
            continue
        if not f["items"] or "5.03" not in f["items"]:
            continue
        try:
            filed = date.fromisoformat(f["filed"])
        except (TypeError, ValueError):
            continue
        out.append(NameChangeFiling(filed=filed, accession=str(f["accession"] or "")))
    out.sort(key=lambda x: x.filed, reverse=True)
    return tuple(out)


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
    return _walk_deep(client, submissions, _acceptance_map)


def _walk_deep(
    client: EdgarClient,
    submissions: dict[str, Any],
    mapper: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], int, int]:
    """``mapper`` over every paginated older page, then over ``recent`` (which wins any overlap — the freshest
    statement of the same value): ``(merged map, pages_read, pages_failed)``. An unreadable page is COUNTED
    and skipped (``_tolerable_page_error``); a systemic fault still raises."""
    merged: dict[str, Any] = {}
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
        merged.update(mapper(page))
    merged.update(mapper(submissions.get("filings", {}).get("recent", {})))
    return merged, pages_read, pages_failed


def _listing_map(arrays: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """``{accession: (filingDate, acceptanceDateTime)}`` from one block of EDGAR's parallel arrays (either
    shape — ``_acceptance_map``'s note); a missing entry reads ``""``."""
    accns = arrays.get("accessionNumber", [])
    dates = arrays.get("filingDate", [])
    accepts = arrays.get("acceptanceDateTime", [])
    return {
        a: (
            str(dates[i]) if i < len(dates) and dates[i] else "",
            str(accepts[i]) if i < len(accepts) and accepts[i] else "",
        )
        for i, a in enumerate(accns)
        if a
    }


def listings(submissions: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """``{accession: (filingDate, acceptanceDateTime)}`` for the ``recent`` window — what the acceptance
    clock judges a stored time by (``ingest.edgar.acceptance``; the ``backfill_accepted --drift`` repair).
    """
    return _listing_map(submissions.get("filings", {}).get("recent", {}))


def listings_deep(
    client: EdgarClient, submissions: dict[str, Any]
) -> tuple[dict[str, tuple[str, str]], int, int]:
    """``listings`` PLUS every paginated older page — ``acceptance_times_deep``'s walk, with filing dates."""
    return _walk_deep(client, submissions, _listing_map)


def _tolerable_page_error(e: Exception) -> bool:
    """Is ``e`` ONE older page's fetch/parse failure rather than a systemic one? An uncached page under
    ``--no-live`` (``CacheMiss``), a truncated/garbled document (``json.JSONDecodeError``), a fetch that
    still fails after the polite retries (``httpx.HTTPError``), or a 2xx that was SEC's error reply rather
    than the page (``InvalidBody`` — the client never caches one; it is a failed fetch, not a fault here).
    A missing User-Agent or any other systemic fault is NOT one page's fault and must still abort.

    NARROWED to ``json.JSONDecodeError`` rather than the bare ``ValueError`` its sibling
    ``pipeline.ingest_thesis._tolerable_filing_error`` tolerates — and the two SHOULD differ. That one
    parses XML documents where a malformed value inside an otherwise-readable filing genuinely raises a
    plain ``ValueError``; this path's only parse is ``client.get_json``, so the sole legitimate
    ``ValueError`` here is a JSON decode failure. The other ``ValueError`` reachable on this path is
    ``fetch_submissions_page``'s belt-and-suspenders page-name guard, which ``submissions_page_names`` has
    already made unreachable — and if it ever DID fire it would mean a programming fault, which must abort
    loudly rather than be absorbed into a per-page tally (the skip-counter lesson)."""
    if isinstance(e, (CacheMiss, json.JSONDecodeError, InvalidBody)):
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
    NULL and is counted). The type matches the timestamptz ``accepted`` column and ``recorded_at``, so the
    disclosure-lag metric's ``COALESCE(accepted, recorded_at)`` (``scoreboard/provenance.py``) needs no
    coercion.

    A PARSE, not a judgment: since the public-clock rule (2026-10-21) the stored ``accepted`` IS an insider
    fact's public instant on every history view (``db/public_clock.py``; the live read still gates on
    ``recorded_at``), and SEC's listed time is not always right (MEASURED 2026-10-24: from ~10-23 the list
    pushed 25-45% of times past EDGAR's hours, some past midnight). A leg that STORES one goes through
    ``ingest.edgar.acceptance.resolve_acceptance``, which checks it against the filing date first.
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
    """List a company's 8-K-family filings (newest first) from a submissions JSON, WITH their item
    codes: ``{accession, form, filed, items}`` (``items``: ``list[str] | None`` — None = the parallel
    ``items`` entry is absent/blank, i.e. not yet resolved).

    The forms are ``domain.edgar_forms.CORPORATE_EVENT_FORMS``: the ordinary ``8-K`` / ``8-K/A`` AND,
    since 2026-10-17, a successor issuer's 8-K12B / 8-K12G3 / 8-K15D5 (+ amendments). ``form`` rides
    verbatim, so the stored row says which kind it is: the succession reports are STORED (#9) and the
    event detectors skip them by form (``signals/corporate_events.event_facts`` — their items describe
    the reorganization itself, see ``domain/edgar_forms.py``).

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
        if f in CORPORATE_EVENT_FORMS
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
