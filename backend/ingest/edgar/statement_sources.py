"""The statement-source SEAM (Retrieval Slice A-2) — WHERE annual financial statements come from.

Slice A read a filer's statements from exactly one place: the annual primary document. The 40-F/MJDS
names (CRDL, DRUG, HELP) broke that assumption — their primary document is a WRAPPER and the
statements live in an ``EX-99.x`` exhibit. Rather than nest ``if main-doc else exhibit``, the source
of statement TEXT is a light ordered chain: a list of source functions tried in priority order,
first hit wins (``resolve_statements``). The seam changes WHERE the text comes from, never WHAT is
done to it — the resolved text flows unchanged into ``extract_annual_runway``, whose cash/OCF
locating, span-normalize, current-column, unit-scale and sign→state logic is untouched, and every
located passage cites the WINNING document (a CRDL runway passage cites the exhibit, not the
wrapper). Deliberately a list + a loop, NOT a registry/class framework: 3-5 sources total are ever
expected (revenue-segment passages are the next user); more machinery earns its keep then.

THE CONTENT SIGNATURE — statements are identified by STRUCTURE, strictly. The winning document is
the one whose cleaned text locates BOTH a balance-sheet cash-and-cash-equivalents ROW and an
operating-activities cash-flow ROW (``has_statement_rows`` — the same locators the extractor itself
uses, so the signature and the extraction can never disagree). A keyword MENTION fails it: the AIF
names the statements (headings, no rows) and the MD&A quotes the very numbers (values, no statement
region) — measured on every A-2 name, including an MD&A whose quarterly-summary table hits the cash
locator alone. Requiring BOTH rows is what rejects them.

FAIL CLOSED — a wrong statement source is the runway analog of a confident-wrong cover match, the
highest-severity failure. Zero signature matches → ``None`` (the caller keeps its honest
``financials-in-exhibit`` deferral); MORE than one match → ``StatementsDeferred("exhibit-ambiguous")``
— a chain-STOPPING deferral (never guess between two statement-shaped exhibits, and never let a
lower-priority source override the ambiguity).

THE COST THREAD — bounded, cache-first fetching. A 40-F carries 90-110 documents; only the EX-99
``.htm`` exhibits are candidates, identified by their SGML-header TYPE (the accession's
``-index-headers.html`` — filenames are NOT trustworthy: one measured filer names its exhibits
``ex_901433.htm`` with no "99" anywhere, another puts the statements in ``cybn-20260331_d2.htm``;
the filename pattern survives only as a fallback when the header yields nothing). Candidates are
ordered unknown-size-first then LARGEST-first (a statements exhibit is a big document; the EX-99
tail is tiny certifications) and capped at ``cfg.exhibit_scan_max``; every candidate the cap sheds
is LOGGED, never silently dropped. The filing index (``index.json``) rides a MUTABLE cache prefix
(12h TTL, safe-by-default per DATA_SOURCES); every document of the accession itself is immutable
``forms/*`` — fetched once, forever. The primary document is NEVER re-fetched here: it arrives on
the ``AnnualFiling`` already fetched (the fetch-once discipline), and ``main_doc_statements`` only
reads it.

No LLM anywhere on this path (#3); no tier decisions here at all — the extractor downstream stays
FLAG-only by its own structural bound. Deterministic; time is not consulted.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date
from typing import Any, Callable, NamedTuple

from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.settings import get_settings
from ingest.edgar.annual_runway import _ZERO_WIDTH_RE, _locate_cash_row, _locate_ocf_row
from ingest.edgar.client import EdgarClient
from ingest.edgar.converts import clean_filing_text

log = logging.getLogger(__name__)

# One <DOCUMENT> block of the accession's SGML header: TYPE, then SEQUENCE, then FILENAME (the
# filing-level <TYPE>40-F in the SEC-HEADER prologue is not followed by <SEQUENCE>, so it can't
# match). The header document serves these tags HTML-ESCAPED inside a <PRE> block — parse the
# ``html.unescape``d text, never the raw.
_SGML_DOC_RE = re.compile(r"<TYPE>([^<\s]+)\s*<SEQUENCE>[^<]*<FILENAME>([^<\s]+)", re.IGNORECASE)
# The filename FALLBACK (only when the SGML header yields no EX-99 .htm at all): "ex99" with an
# optional separator, anywhere in the name — the shape one measured filer agent actually uses.
_EX99_NAME_RE = re.compile(r"ex[-_]?99", re.IGNORECASE)
_HTM_SUFFIXES = (".htm", ".html")


class AnnualFiling(NamedTuple):
    """The filing context a statement source reads — assembled ONCE by the caller from data it
    already holds (submissions row + the primary document text it already fetched)."""

    cik: int
    accession: str  # dashed EDGAR accession ("0001104659-26-037844")
    primary_url: str
    primary_text: str  # the CLEANED primary-document text — already fetched, never re-fetched
    report_date: date
    form: str  # "20-F" | "40-F"


class ResolvedStatements(NamedTuple):
    """Located statement TEXT + the document it came from (what every passage must cite)."""

    text: str  # CLEANED text of the statement-bearing document
    source_ref: str  # the WINNING document's URL — passages/facts cite THIS
    source_doc: str  # its filename (the primary document's for the main-doc source)


class StatementsDeferred(NamedTuple):
    """A chain-STOPPING deferral: statements-shaped documents were found but no single one can be
    chosen (``reason`` = ``"exhibit-ambiguous"``). Distinct from ``None`` ("not here — try the next
    source"): a deferral is a decision the resolver surfaces, never overrides."""

    reason: str


StatementSource = Callable[
    [EdgarClient, AnnualFiling, ExtractorConfig],
    "ResolvedStatements | StatementsDeferred | None",
]


def has_statement_rows(text: str) -> bool:
    """The content signature: does this cleaned text carry BOTH statement rows — the balance-sheet
    cash row AND the operating-activities cash-flow total — located by the extractor's OWN locators
    (heading-anchored, value-gated)? Statement STRUCTURE, not a keyword mention: an AIF's heading
    references and an MD&A's quoted figures both fail it. Zero-width characters are stripped first,
    exactly as the extractor does (they litter real filings, invisibly breaking every regex)."""
    t = _ZERO_WIDTH_RE.sub("", text)
    return _locate_ocf_row(t) is not None and _locate_cash_row(t) is not None


def main_doc_statements(
    client: EdgarClient, filing: AnnualFiling, cfg: ExtractorConfig
) -> ResolvedStatements | StatementsDeferred | None:
    """The Slice A behavior in seam shape: the statements in the ALREADY-FETCHED primary document.
    Reads ``filing.primary_text`` only — it never touches ``client`` (the fetch-once discipline,
    provable by passing ``client=None``). ``None`` when the primary carries no statement rows (the
    40-F/MJDS wrapper shape) — the next source's turn."""
    if has_statement_rows(filing.primary_text):
        return ResolvedStatements(
            text=filing.primary_text,
            source_ref=filing.primary_url,
            source_doc=filing.primary_url.rsplit("/", 1)[-1],
        )
    return None


def _archives_base(cik: int, accession: str) -> str:
    return f"{get_settings().sec_archives_base}/{cik}/{accession.replace('-', '')}"


def _ex99_candidates(client: EdgarClient, filing: AnnualFiling) -> list[str]:
    """The EX-99 ``.htm`` candidate FILENAMES for this accession, by SGML-header TYPE (the
    trustworthy source), else the filename-pattern fallback over the index listing. Returns them
    UNORDERED — the caller orders and caps (the cost thread)."""
    headers_name = f"{filing.accession}-index-headers.html"
    raw = client.get_text(
        f"{_archives_base(filing.cik, filing.accession)}/{headers_name}",
        f"forms/{filing.accession}/{headers_name}",
    )
    by_type = [
        fname
        for typ, fname in _SGML_DOC_RE.findall(html.unescape(raw))
        if typ.upper().startswith("EX-99") and fname.lower().endswith(_HTM_SUFFIXES)
    ]
    if by_type:
        return by_type
    # fallback: the header parsed to nothing — a filename scan of the index keeps recall (#9)
    idx = _filing_index(client, filing)
    by_name = [
        it["name"]
        for it in idx
        if _EX99_NAME_RE.search(it.get("name", ""))
        and it.get("name", "").lower().endswith(_HTM_SUFFIXES)
    ]
    if by_name:
        log.warning(
            "exhibit_statements %s: SGML header yielded no EX-99 docs; filename fallback found %s",
            filing.accession,
            by_name,
        )
    return by_name


def _filing_index(client: EdgarClient, filing: AnnualFiling) -> list[dict[str, Any]]:
    """The accession's document listing (``index.json`` → ``directory.item[]``). Cached under the
    MUTABLE ``filing-index/`` prefix (12h TTL, default-refresh — the key-classed freshness rule), so
    a re-listed accession is seen without any caller threading a flag."""
    idx = client.get_json(
        f"{_archives_base(filing.cik, filing.accession)}/index.json",
        f"filing-index/{filing.accession}.json",
    )
    return idx.get("directory", {}).get("item", []) or []


def exhibit_statements(
    client: EdgarClient, filing: AnnualFiling, cfg: ExtractorConfig
) -> ResolvedStatements | StatementsDeferred | None:
    """The A-2 capability: hunt the financial statements among the filing's EX-99 exhibits.
    Enumerate (index.json) → identify candidates by SGML TYPE (filename fallback) → order
    unknown-size-first then largest-first → cap at ``cfg.exhibit_scan_max`` (skips LOGGED) → fetch
    each (cache-first, immutable) → the content signature picks the statements. Exactly one match
    wins; zero → ``None``; more than one → ``StatementsDeferred("exhibit-ambiguous")`` — never a
    guess."""
    candidates = _ex99_candidates(client, filing)
    if not candidates:
        return None
    sizes: dict[str, int] = {}
    for it in _filing_index(client, filing):
        if str(it.get("size", "")).isdigit():
            sizes[it["name"]] = int(it["size"])
    # unknown size first (must-check — never let a missing size push a real doc out), then largest
    # first (statements exhibits are big; the EX-99 tail is tiny certifications); ties keep the
    # header's own order (Python's stable sort).
    ordered = sorted(candidates, key=lambda n: (n in sizes, -sizes.get(n, 0)))
    scanned, skipped = ordered[: cfg.exhibit_scan_max], ordered[cfg.exhibit_scan_max :]
    if skipped:
        log.warning(
            "exhibit_statements %s: %d EX-99 candidates exceed exhibit_scan_max=%d — skipped %s",
            filing.accession,
            len(ordered),
            cfg.exhibit_scan_max,
            skipped,
        )
    matches: list[tuple[str, str]] = []  # (doc, cleaned text)
    for doc in scanned:
        url = f"{_archives_base(filing.cik, filing.accession)}/{doc}"
        text = clean_filing_text(client.get_text(url, f"forms/{filing.accession}/{doc}"))
        if has_statement_rows(text):
            matches.append((doc, text))
    if not matches:
        return None
    if len(matches) > 1:
        log.warning(
            "exhibit_statements %s: %d exhibits carry the statement signature (%s) — deferring, "
            "never guessing",
            filing.accession,
            len(matches),
            [d for d, _ in matches],
        )
        return StatementsDeferred(reason="exhibit-ambiguous")
    doc, text = matches[0]
    return ResolvedStatements(
        text=text,
        source_ref=f"{_archives_base(filing.cik, filing.accession)}/{doc}",
        source_doc=doc,
    )


# ORDER = priority: the primary document wins whenever it carries the statements (the Slice A names
# never reach the exhibit scan); the exhibit hunt runs only for the wrapper shape.
STATEMENT_SOURCES: list[StatementSource] = [main_doc_statements, exhibit_statements]


def resolve_statements(
    client: EdgarClient,
    filing: AnnualFiling,
    *,
    sources: list[StatementSource] | None = None,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> ResolvedStatements | StatementsDeferred | None:
    """Try the statement sources in priority order; first non-``None`` answer wins. A
    ``StatementsDeferred`` STOPS the chain (an ambiguity is surfaced, never out-voted by a
    lower-priority source); ``None`` from every source means no statements anywhere — the caller
    keeps its honest empty reason."""
    for src in sources if sources is not None else STATEMENT_SOURCES:
        r = src(client, filing, cfg)
        if r is not None:
            return r
    return None


__all__ = [
    "AnnualFiling",
    "ResolvedStatements",
    "StatementsDeferred",
    "StatementSource",
    "STATEMENT_SOURCES",
    "has_statement_rows",
    "main_doc_statements",
    "exhibit_statements",
    "resolve_statements",
]
