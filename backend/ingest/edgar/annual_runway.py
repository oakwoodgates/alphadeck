"""Annual-statements cash + runway for the DARK names (Retrieval Slice A) — IFRS 20-F/40-F, FLAG-only.

A basket name with NO 10-K/10-Q (a foreign private issuer) gets an honest **runway** — cash and a
span-normalized operating-cash burn — read from the financial statements of the SAME 20-F/40-F document
the annual-cover shares path already fetches (ONE fetch, shared by ``annual_facts_for_security`` below).
Always tier FLAG, always carrying the located statement rows as passages. A cash-GENERATIVE name (the
operating-cash-flow sign is the state) is marked **"cash-generative"** — never a bogus runway number.
The rules are canon in ``docs/WORKBENCH_EXTRACTION.md`` ("The annual-statements runway path"); the
values are pinned in ``tests/ingest/test_annual_runway.py`` + its fixtures — the tests are the oracle.

THE STRUCTURAL BOUND — mirror of ``annual_shares.py``: the pre-fill tier's token appears NOWHERE in
this file (a source-scan test asserts it) and every emitted fact is ``Tier.FLAG`` — an annual statement
is a fiscal year (or more) old and its composition (scale header, column pick, span basis) is the
operator's to ratify, never confirm-and-go.

THE FOUR HAZARDS this parser exists for (each pinned by a fixture test):

- **Span varies (the core computation).** ``burn_per_day = |OCF| / span_days`` — a 3/6/9-month column
  (a 6-K interim served by companyfacts, or a transition-period statement) must NEVER be read as a
  year, or runway is off 2-4x. The span comes from (1) an exact companyfacts row match (filed dates,
  the reproducible-arithmetic class), else (2) the statement's own period phrase ("years ended" /
  "six months ended"), else (3) a bare fiscal-year column header on an annual form reads as annual.
- **Unit scale.** Statement values sit under a ``$'000`` / ``in thousands`` / ``in millions`` header —
  read the scale from the statement region and apply it, or the value is 1000x off. Undeterminable
  scale -> the value is offered AS PRINTED with flag ``unit-scale-unread`` — stated, never guessed.
- **Multi-year columns.** Statements show 2-3 fiscal years side by side (sometimes plus a convenience
  second-currency column). The current-period column is picked by matching the report year against the
  column-year headers located above the row; an ambiguous mapping (a duplicated report-year header)
  withholds the value with flag ``column-ambiguous`` — never a silent prior-year (or wrong-currency)
  read. When every column shares one sign, the generative/burning STATE is still readable.
- **Currencies.** IFRS filers report in native currency (CHF/CAD/EUR/TWD/INR...). Runway is a RATIO,
  so months are currency-independent — but only if cash and burn share a currency. companyfacts is
  compared/consulted ONLY in the unit matching the statement's own detected currency, so a mixed-
  currency ratio is unrepresentable. (The stored fields are named ``*_usd``; for a native-currency
  filer they carry statement-currency values — the note names the currency, the ratio stays honest.)

NEITHER SOURCE DOMINATES — later-as-of-wins per quantity (the shares rule): companyfacts often LAGS
the filing by a full year (annual XBRL ingests slowly — the reason the filing is read at all), and is
sometimes FRESHER (a 6-K interim landing after the last annual). The statement rows carry the located
passages either way. ``source-disagreement`` fires only on a SAME-DATE value contradiction — a cross-
date difference is a lag, stated in the note, not an alarm (deliberately quieter than the shares flag:
annual XBRL lag is the RULE here, and a flag true of every name carries no information).

FAIL CLOSED: no located statement rows -> NO fact (no passage -> no fact), with an honest, distinct
``runway_empty_reason``: ``cash-generative`` (positive operating cash flow — a state, not a gap) ·
``financials-in-exhibit`` (a burning name whose statements live outside the fetched document — the
40-F/MJDS wrapper shape; runway needs the exhibit doc, deferred) · ``statements-not-located`` (sign
unknowable — unread, not empty). Deterministic parse only (#3): no LLM anywhere on this path; ``today``
and ``report_date`` are parameters (no implicit now).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, LocatedPassage, Tier
from ingest.edgar.annual_shares import (
    _companyfacts_or_none,
    _latest_annual_filing,
    extract_annual_shares,
)
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import _days
from ingest.edgar.submissions import fetch_submissions

_DAYS_PER_YEAR = 365.25
_DAYS_PER_QUARTER = _DAYS_PER_YEAR / 4  # the meter's quarterly-burn basis (months = cash/(q/3))

# Retrieval windows (the _PURITY_WINDOW precedent: module constants, not config dials — a too-small
# window means a value honestly not read, never a wrong one).
_REGION_SPAN = 12_000  # statement heading -> how far the statement's rows may run
_HEADER_BACKSCAN = 3_400  # row -> how far back the column-year / scale / period headers may sit
_ROW_VALUE_CHARS = 170  # label -> the value area (cut at the next row's label word)
_EXCERPT_PRE = 60
_EXCERPT_POST = 210

# Zero-width characters survive ``clean_filing_text`` (they are not HTML) and sit INSIDE labels and
# numbers in real filings (measured: thousands per document on some names), invisibly breaking every
# regex. The parser works on a stripped copy; passage offsets refer to that stripped text (recorded
# for audit, never filtered on — same contract as the shares path).
_ZERO_WIDTH_RE = re.compile("[​‌‍⁠﻿]")

_BS_HEADING_RE = re.compile(
    r"statements?\s+of\s+financial\s+position|balance\s+sheets?", re.IGNORECASE
)
_CF_HEADING_RE = re.compile(
    r"statements?\s+of\s+cash\s+flows?|cash\s+flows?\s+statement", re.IGNORECASE
)

# The balance-sheet cash row. The full label covers most of the measured universe; a real filer
# labels the row bare "Cash" (HYFT: "Current assets Cash 16 11,348 10,665"), so a case-SENSITIVE
# bare-label fallback runs when the full label locates nothing (capitalized row label; "Restricted
# cash" and prose stay lowercase). The guards keep the cash-flow statement's "Cash and cash
# equivalents at end/beginning of year" rows and the "Net increase in cash and cash equivalents"
# total from masquerading as the balance-sheet instant.
_CASH_LABEL_RE = re.compile(
    r"cash\s+and\s+cash\s+equivalents\b(?![\s,]*(?:at|as|end|beginning|held))", re.IGNORECASE
)
_CASH_BARE_LABEL_RE = re.compile(r"(?<![A-Za-z\d])Cash(?=[\s:]{1,4}(?:\(?\s?Note\s*\d|\d))")
_CASH_PRECEDING_VETO_RE = re.compile(
    r"(?:increase|decrease|movement|change)\s+in\s*$", re.IGNORECASE
)

# The operating-cash-flow TOTAL row — the label wording varies widely in the wild ("Net cash used in",
# "Cash used in" with no Net, "Net cash provided by (used in)", "Cash provided by (used in)", "Net
# cash generated by/from", "Net cash flows used in/from") and MUST be followed immediately by a value:
# the section heading "Cash flows from operating activities" is followed by words ("Net loss ..."),
# the total row by its number — that adjacency is the discriminator, not the label.
_OCF_LABEL_RE = re.compile(
    r"(?P<net>net\s+)?cash\s+(?:flows?\s+)?"
    r"(?:[()/\s]*(?:used\s+(?:in|for)|provided\s+by|generated\s+(?:by|from)?|utili[sz]ed\s+in|from)){1,3}"
    r"[()/\s]*operating\s+activities",
    re.IGNORECASE,
)
# ...the immediate-value gate: only these (plus an optional "(Note N)" reference — ASX prints one
# between the label and the values) may sit between the label and its first digit. Any other letter
# (the section heading's "Net loss", a reconcile phrase's "(a)" marker) rejects the site.
_VALUE_GATE_RE = re.compile(
    r"^[\s:$€£₹]{0,6}(?:\(\s*Note\s*\d{1,3}\s*\)\s*)?[\s$€£₹(]{0,6}-?\d", re.IGNORECASE
)

# Value tokens. Comma-grouped is the dominant convention; plain short integers are accepted so small
# real values ("76" thousand) parse — a leading 1-2 digit NOTE REFERENCE is dropped by the
# count-vs-columns rule in _map_columns, never by guessing. Bare 4-digit year-range tokens are inert.
_COMMA_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,6}(?:\.\d+)?")
_SPACE_GROUPED_RE = re.compile(r"\b\d{1,3}(?: \d{3})+\b")  # the European convention (measured: NVS)
_YEAR_RE = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
# the cut: the next row's label starts at the first >=3-letter word (currency markers are shorter or
# allow-listed), so a value window never swallows the following row's numbers
_CUT_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_CURRENCY_CUT_ALLOW = {"usd", "cad", "chf", "eur", "gbp", "nis", "ils", "twd", "inr", "jpy"}

# Scale + currency read from the statement region (nearest marker ABOVE the row wins — that is the
# row's own table header). Precedence: an explicit thousands/millions marker beats a bare
# "expressed in ... dollars" (HYFT states both: "(Expressed in Canadian dollars) (in thousands)").
_SCALE_THOUSANDS_RE = re.compile(
    r"in\s+(?:[a-z$€£₹.\s]{0,14})?thousands?\b|thousands\s+of|[$€£₹]\W{0,3}000|\bk\W{0,2}€|NT\$\s*000",
    re.IGNORECASE,
)
_SCALE_MILLIONS_RE = re.compile(
    r"in\s+(?:[a-z$€£₹.\s]{0,14})?millions?\b|millions\s+of|(?:USD|US\$|[$€£₹]|Rs\.?)\s*millions?\b",
    re.IGNORECASE,
)
_SCALE_UNITS_RE = re.compile(
    r"(?:expressed\s+in|amounts?\s+in)\s+(?:[a-z.\s]{0,24})?dollars?\b"
    r"|(?:expressed\s+in|amounts?\s+in)\s+(?:US\$|USD|U\.?S\.?\s*\$)",
    re.IGNORECASE,
)
# statement currency — ordered so NT$ (which contains $) resolves before the bare-$ USD fallback
_CURRENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TWD", re.compile(r"new\s+taiwan|NT\$", re.IGNORECASE)),
    ("CAD", re.compile(r"canadian\s+dollars?", re.IGNORECASE)),
    ("EUR", re.compile(r"€|\beuros?\b", re.IGNORECASE)),
    ("INR", re.compile(r"₹|\bRs\.|indian\s+rupees?", re.IGNORECASE)),
    ("CHF", re.compile(r"\bCHF\b|swiss\s+francs?", re.IGNORECASE)),
    ("ILS", re.compile(r"\bNIS\b|₪|israeli\s+(?:new\s+)?shekels?", re.IGNORECASE)),
    ("GBP", re.compile(r"£|pounds?\s+sterling", re.IGNORECASE)),
    (
        "USD",
        re.compile(
            r"US\$|U\.?S\.?\s+dollars?|united\s+states\s+dollars?|\bUSD\b|\$", re.IGNORECASE
        ),
    ),
)
# the statement's own period phrase — the span source when no companyfacts row matches the value
_PERIOD_PHRASE_RE = re.compile(
    r"(?:(?P<months>three|six|nine|twelve)\s+months?|years?)\s+(?:then\s+)?ended",
    re.IGNORECASE,
)
_MONTH_SPANS = {"three": 91, "six": 182, "nine": 274, "twelve": 365}

# a data gap stated once, verbatim on whichever side hits it first (the flag fires at most once)
_SCALE_UNREAD_NOTE = (
    "the statement's unit scale (thousands/millions) could NOT be read — figures are offered "
    "AS PRINTED; determine the scale from the passage before ratifying"
)

_OCF_CONCEPT = "CashFlowsFromUsedInOperatingActivities"
_CASH_CONCEPT = "CashAndCashEquivalents"


# ---------------------------------------------------------------------------------------------------------
# row parsing helpers (pure text -> numbers)
# ---------------------------------------------------------------------------------------------------------


_NOTE_REF_PREFIX_RE = re.compile(r"^[\s:$]{0,4}\(\s*Note\s*\d{1,3}\s*\)", re.IGNORECASE)


def _value_window(text: str, label_end: int) -> str:
    """The row's VALUE AREA: from the label to the next row's label word (or the window cap). Cutting at
    the first >=3-letter non-currency word keeps the next row's numbers out — a fixed-width window read
    neighbouring rows into the columns on tightly-packed statements. A leading "(Note N)" reference is
    stripped first (it would otherwise BE the cut word and empty the window)."""
    raw = text[label_end : label_end + _ROW_VALUE_CHARS]
    raw = _NOTE_REF_PREFIX_RE.sub(" ", raw, count=1)
    for m in _CUT_WORD_RE.finditer(raw):
        if m.group(0).lower() not in _CURRENCY_CUT_ALLOW:
            return raw[: m.start()]
    return raw


def _signed(window: str, m: re.Match[str], strip: str) -> float:
    """The token's value, sign read from an adjacent ``(`` or ``-`` (statements print negatives in
    parentheses; a minus survives in summary tables)."""
    pre = window[max(0, m.start() - 4) : m.start()]
    sign = -1.0 if ("(" in pre or "-" in pre) else 1.0
    return sign * float(m.group(0).replace(strip, "").replace(",", ""))


def _comma_tokens(window: str) -> list[float]:
    """Comma-grouped (and plain short-integer) value tokens. Plain 1-6 digit integers are accepted so
    small real values ("76" thousand, an ungrouped "707") parse; a leading 1-2 digit NOTE REFERENCE is
    dropped by the count-vs-columns rule in ``_map_columns``, never by guessing; a bare 4-digit
    year-range token is a header artifact and stays inert."""
    out: list[float] = []
    for m in _COMMA_TOKEN_RE.finditer(window):
        tok = m.group(0)
        if "," not in tok and "." not in tok and len(tok) == 4 and _YEAR_RE.fullmatch(tok):
            continue
        out.append(_signed(window, m, ","))
    return out


def _space_grouped_tokens(window: str) -> list[float]:
    return [_signed(window, m, " ") for m in _SPACE_GROUPED_RE.finditer(window)]


def _sign_of(vals: list[float]) -> bool | None:
    """The row's sign when EVERY column agrees: True = all negative (burning), False = all
    non-negative (generative), None = mixed or empty. A uniform sign makes the generative/burning
    STATE readable even when the column mapping is ambiguous — a state is sign-only."""
    if vals and all(v < 0 for v in vals):
        return True
    if vals and all(v >= 0 for v in vals):
        return False
    return None


def _read_row_values(
    window: str, years: list[int], report_year: int
) -> tuple[float | None, str | None, list[float]]:
    """(current-column value, ambiguity flag, every value token) for one statement row. A comma-bearing
    window reads comma-grouped; otherwise the space-grouped convention (the European style: ``11 435``)
    is tried FIRST and kept only when it maps cleanly onto the column-year headers — ``76 371`` (two
    small adjacent values) fails that mapping and falls back to the plain per-token read, so both real
    shapes parse without a guess."""
    if "," in window:
        vals = _comma_tokens(window)
        picked, flag = _map_columns(vals, years, report_year)
        return picked, flag, vals
    grouped = _space_grouped_tokens(window)
    if grouped:
        picked, flag = _map_columns(grouped, years, report_year)
        if picked is not None:
            return picked, flag, grouped
    plain = _comma_tokens(window)
    picked, flag = _map_columns(plain, years, report_year)
    return picked, flag, (plain or grouped)


def _column_years(text: str, region_start: int, row_start: int) -> list[int]:
    """The column-year headers above the row: every year token between the statement heading and the
    row (bounded by the backscan window), with a TRAILING duplicate run collapsed — a convenience
    second-currency column repeats the last fiscal year ("2024 2025 2025"); collapsing only the tail
    keeps a leading duplicate (a convenience column FIRST) ambiguous, which withholds rather than
    mis-picks."""
    lo = max(region_start, row_start - _HEADER_BACKSCAN)
    years = [int(m.group(0)) for m in _YEAR_RE.finditer(text, lo, row_start)]
    while len(years) >= 2 and years[-1] == years[-2]:
        years.pop()
    return years


def _map_columns(
    values: list[float], years: list[int], report_year: int
) -> tuple[float | None, str | None]:
    """Pick the CURRENT-period column: match ``report_year`` against the column headers. More value
    tokens than year headers = note-reference/convenience-column padding -> drop a leading 1-2 digit
    integer (the note ref) if that reconciles the counts, else map front-aligned (convenience columns
    trail). An unmatched or duplicated report-year header is ``column-ambiguous`` — no value, never a
    prior-year guess."""
    if not values:
        return None, None
    if len(values) > len(years) >= 1 and 0 < values[0] < 100 and float(values[0]).is_integer():
        values = values[1:]  # the leading small integer is the row's note reference
    if len(years) >= len(values):
        window = years[-len(values) :]
        if window.count(report_year) == 1:
            return values[window.index(report_year)], None
        return None, "column-ambiguous"
    if years.count(report_year) == 1 and years.index(report_year) < len(values):
        return values[years.index(report_year)], None
    return None, "column-ambiguous"


def _nearest_scale(text: str, region_start: int, row_start: int) -> tuple[float | None, str | None]:
    """(multiplier, the matched marker text) from the NEAREST scale marker above the row — that is the
    row's own table header. Thousands/millions markers outrank a bare expressed-in-dollars statement at
    the same distance; no marker at all -> (None, None): the value goes out AS PRINTED and flagged.
    """
    seg = text[region_start:row_start]  # heading -> row: the statement's own header block
    best: tuple[tuple[int, int], float, str] | None = None
    for mult, pat, rank in (
        (1_000.0, _SCALE_THOUSANDS_RE, 1),
        (1_000_000.0, _SCALE_MILLIONS_RE, 1),
        (1.0, _SCALE_UNITS_RE, 0),
    ):
        for m in pat.finditer(seg):
            key = (
                m.start(),
                rank,
            )  # nearest-to-the-row wins; thousands/millions outrank units there
            if best is None or key > best[0]:
                best = (key, mult, re.sub(r"\s+", " ", m.group(0)).strip())
    if best is None:
        return None, None
    return best[1], best[2]


def _statement_currency(text: str, region_start: int, row_start: int) -> str | None:
    seg = text[region_start:row_start]
    for code, pat in _CURRENCY_PATTERNS:
        if pat.search(seg):
            return code
    return None


def _period_span_days(text: str, region_start: int, row_start: int) -> int | None:
    """The span of the statement's period from its own phrase — the LAST phrase above the row (nearest
    header wins). A bare fiscal-year column header with no phrase returns None; the caller treats an
    annual form's year-labeled column as annual (form semantics, stated in the note)."""
    last: re.Match[str] | None = None
    for m in _PERIOD_PHRASE_RE.finditer(text, region_start, row_start):
        last = m
    if last is None:
        return None
    if last.group("months"):
        return _MONTH_SPANS[last.group("months").lower()]
    return 365


# ---------------------------------------------------------------------------------------------------------
# row location (deterministic retrieval — heading-anchored, value-gated)
# ---------------------------------------------------------------------------------------------------------


def _locate_cash_row(text: str) -> tuple[int, int, int] | None:
    """(row_start, label_end, region_start) of the balance-sheet cash row: the FIRST cash-label site
    that (a) sits within a statement region after a balance-sheet heading, (b) is not an
    increase/decrease total, and (c) is immediately followed by a value. When the full label matches
    nowhere in any region, the bare capitalized "Cash" row label is tried (HYFT's real shape) — a
    fallback, so the full-label names are untouched."""
    headings = [m.start() for m in _BS_HEADING_RE.finditer(text)]
    if not headings:
        return None
    for label_re in (_CASH_LABEL_RE, _CASH_BARE_LABEL_RE):
        for m in label_re.finditer(text):
            region = next(
                (h for h in reversed(headings) if h < m.start() <= h + _REGION_SPAN), None
            )
            if region is None:
                continue
            if _CASH_PRECEDING_VETO_RE.search(text[max(0, m.start() - 16) : m.start()]):
                continue
            if not _VALUE_GATE_RE.search(text[m.end() : m.end() + 18]):
                continue
            return m.start(), m.end(), region
    return None


def _locate_ocf_row(text: str) -> tuple[int, int, int] | None:
    """(row_start, label_end, region_start) of the operating-activities TOTAL row, from the LAST
    cash-flow-statement region that contains one — MD&A liquidity tables repeat the statement earlier
    in the document; the F-pages statement comes last and is the audited source. Within a region a
    ``Net``-prefixed label outranks a bare subtotal ("Cash flows used in operating activities" can be
    a pre-finance-items subtotal on the same statement), then the first wins."""
    best: tuple[int, int, int] | None = None
    for h in _CF_HEADING_RE.finditer(text):
        lo, hi = h.start(), h.start() + _REGION_SPAN
        sites: list[tuple[bool, int, int]] = []
        for m in _OCF_LABEL_RE.finditer(text, lo, hi):
            if not _VALUE_GATE_RE.search(text[m.end() : m.end() + 14]):
                continue
            sites.append((bool(m.group("net")), m.start(), m.end()))
        if not sites:
            continue
        netted = [s for s in sites if s[0]] or sites
        _, start, end = netted[0]
        best = (start, end, lo)
    return best


# ---------------------------------------------------------------------------------------------------------
# companyfacts (the cross-check + the sometimes-fresher source) — IFRS taxonomy, native units
# ---------------------------------------------------------------------------------------------------------


def _cf_units(companyfacts: dict[str, Any] | None, concept: str) -> dict[str, list[dict]]:
    return (companyfacts or {}).get("facts", {}).get("ifrs-full", {}).get(concept, {}).get(
        "units", {}
    ) or {}


def _cf_unit_for(
    companyfacts: dict[str, Any] | None, concept: str, currency: str | None
) -> str | None:
    """The companyfacts unit this path may read: the STATEMENT's own currency when detected (so a
    cross-currency comparison is unrepresentable), else the only unit on file, else the unit with the
    latest data (sign-reads for a document with no readable statements)."""
    units = _cf_units(companyfacts, concept)
    if not units:
        return None
    if currency is not None:
        return currency if currency in units else None
    if len(units) == 1:
        return next(iter(units))
    return max(units, key=lambda u: max(r["end"] for r in units[u]))


def _cf_latest_instant(rows: list[dict]) -> tuple[float, date] | None:
    if not rows:
        return None
    end = max(r["end"] for r in rows)
    at = [r for r in rows if r["end"] == end]
    return float(max(at, key=lambda r: r.get("filed", ""))["val"]), date.fromisoformat(end)


def _cf_latest_duration(rows: list[dict]) -> tuple[float, date, date] | None:
    """(value, start, end) of the latest-ending duration row; at the same end the LONGEST span wins
    (an annual column over a same-ended quarter — the smoother burn basis, stated in the note).
    ``_days`` is the periodic extractor's own row-span helper, reused read-only."""
    dur = [r for r in rows if r.get("start")]
    if not dur:
        return None
    end = max(r["end"] for r in dur)
    at = [r for r in dur if r["end"] == end]
    row = max(at, key=lambda r: (r["end"] > r["start"], _days(r), r.get("filed", "")))
    return float(row["val"]), date.fromisoformat(row["start"]), date.fromisoformat(row["end"])


def _cf_span_for_value(
    companyfacts: dict[str, Any] | None,
    unit: str | None,
    scaled_value: float,
    report_date: date,
) -> tuple[date, date] | None:
    """Does a filed companyfacts duration row ENDING AT THE PERIOD OF REPORT reproduce the statement
    value? Then its exact start/end dates are the span — reproducible arithmetic on filed data, immune
    to a mislabeled period phrase (a real filer titled an annual statement "nine months ended"). The
    end-date pin matters: matching on value alone collided with a COINCIDENTALLY-equal old interim row
    (measured live) and mis-spanned a clean annual figure."""
    if unit is None:
        return None
    end_key = report_date.isoformat()
    for r in _cf_units(companyfacts, _OCF_CONCEPT).get(unit, []):
        if not r.get("start") or r.get("end") != end_key:
            continue
        val = float(r["val"])
        if val == 0 or scaled_value == 0:
            continue
        if (val < 0) == (scaled_value < 0) and abs(abs(val) - abs(scaled_value)) <= 0.005 * abs(
            val
        ):
            return date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
    return None


# ---------------------------------------------------------------------------------------------------------
# the pure extractor
# ---------------------------------------------------------------------------------------------------------


def _passage(text: str, kind: str, source_ref: str, row_start: int, label: str) -> LocatedPassage:
    lo = max(0, row_start - _EXCERPT_PRE)
    excerpt = re.sub(r"\s+", " ", text[lo : row_start + len(label) + _EXCERPT_POST]).strip()
    return LocatedPassage(
        kind=kind,
        source_ref=source_ref,
        anchor=re.sub(r"\s+", " ", label)[:80],
        excerpt=f"… {excerpt} …",
        offset=row_start,
    )


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def extract_annual_runway(
    companyfacts: dict[str, Any] | None,
    annual_text: str,
    *,
    annual_ref: str,
    annual_form: str,
    report_date: date,
    today: date,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> tuple[list[ExtractedFact], str | None]:
    """Pure + deterministic: (companyfacts-or-None + the CLEANED annual filing text) -> the one
    cash_burn FLAG candidate, or ``([], runway_empty_reason)`` when no statement row can be located
    (FAIL CLOSED — no passage, no fact). ``report_date`` is the filing's PERIOD OF REPORT (what the
    statements are dated to); ``today`` only ages the staleness flag (time is a parameter)."""
    text = _ZERO_WIDTH_RE.sub("", annual_text)
    report_year = report_date.year

    ocf_site = _locate_ocf_row(text)
    cash_site = _locate_cash_row(text)

    # ---- the fact needs BOTH statement rows (its passages are the cash row AND the OCF row) --------
    if ocf_site is None or cash_site is None:
        # The STATE is still honestly readable — the located row's own sign if any, else the
        # companyfacts sign (any unit: a sign is currency-independent). A generative name is a STATE
        # ("cash-generative", no runway applies); a burning one without in-document statements is the
        # exhibit/MJDS shape — runway needs the exhibit document, deferred.
        burning: bool | None = None
        if ocf_site is not None:
            s_start, s_end, s_region = ocf_site
            burning = _sign_of(
                _read_row_values(
                    _value_window(text, s_end), _column_years(text, s_region, s_start), report_year
                )[2]
            )
        if burning is None:
            unit = _cf_unit_for(companyfacts, _OCF_CONCEPT, None)
            latest = (
                _cf_latest_duration(_cf_units(companyfacts, _OCF_CONCEPT).get(unit, []))
                if unit
                else None
            )
            if latest is not None:
                burning = latest[0] < 0
        if burning is None:
            return [], "statements-not-located"
        return [], ("financials-in-exhibit" if burning else "cash-generative")

    flags: list[str] = ["annual-statements"]
    passages: list[LocatedPassage] = []
    notes: list[str] = []

    # ---- both statements' headers (scale / currency), with companion inheritance -------------------
    ocf_start, ocf_end_pos, ocf_region = ocf_site
    c_start, c_end_pos, c_region = cash_site
    ocf_label = text[ocf_start:ocf_end_pos]
    passages.append(_passage(text, "cash-flow", annual_ref, ocf_start, ocf_label))
    scale, scale_marker = _nearest_scale(text, ocf_region, ocf_start)
    c_scale, c_marker = _nearest_scale(text, c_region, c_start)
    currency = _statement_currency(text, ocf_region, ocf_start) or _statement_currency(
        text, c_region, c_start
    )
    # ONE filing's statements share one units declaration (often printed once, on the FS title page or
    # a single statement's header) — when only one side reads a marker, the other inherits it, noted.
    if scale is None and c_scale is not None:
        scale, scale_marker = c_scale, c_marker
        notes.append(f"cash-flow scale read from the companion statement's header (“{c_marker}”)")
    elif c_scale is None and scale is not None:
        c_scale, c_marker = scale, scale_marker
        notes.append(
            f"balance-sheet scale read from the companion statement's header (“{scale_marker}”)"
        )

    years = _column_years(text, ocf_region, ocf_start)
    doc_ocf_raw, col_flag, vals = _read_row_values(
        _value_window(text, ocf_end_pos), years, report_year
    )
    uniform_sign = _sign_of(vals)  # True = every column negative (burning), False = positive
    if col_flag and col_flag not in flags:
        flags.append(col_flag)

    doc_ocf: float | None = None
    if doc_ocf_raw is not None:
        if scale is None:
            flags.append("unit-scale-unread")
            notes.append(_SCALE_UNREAD_NOTE)
            doc_ocf = doc_ocf_raw
        else:
            doc_ocf = doc_ocf_raw * scale

    # span: an exact companyfacts row match (filed dates) > the statement's own period phrase > a
    # bare fiscal-year column on an annual form (annual by form semantics)
    cf_ocf_unit = _cf_unit_for(companyfacts, _OCF_CONCEPT, currency)
    doc_span: int | None = None
    span_basis = ""
    if doc_ocf is not None:
        matched = _cf_span_for_value(companyfacts, cf_ocf_unit, doc_ocf, report_date)
        if matched is not None:
            doc_span = max((matched[1] - matched[0]).days, 1)
            span_basis = f"companyfacts-matched period {matched[0]} → {matched[1]}"
        else:
            phrase = _period_span_days(text, ocf_region, ocf_start)
            if phrase is not None:
                doc_span = phrase
                span_basis = f"the statement's period phrase (~{phrase} days)"
            elif years:
                doc_span = 365
                span_basis = "a fiscal-year column on an annual form (annual by form)"

    # ---- later-as-of between the statement value and companyfacts (per quantity) -------------------
    cf_ocf = (
        _cf_latest_duration(_cf_units(companyfacts, _OCF_CONCEPT).get(cf_ocf_unit, []))
        if cf_ocf_unit
        else None
    )
    ocf_value: float | None = None
    ocf_span: int | None = None
    ocf_asof: date | None = None
    ocf_src = ""
    if doc_ocf is not None and doc_span is not None:
        ocf_value, ocf_span, ocf_asof, ocf_src = doc_ocf, doc_span, report_date, "statement"
    if cf_ocf is not None and (ocf_asof is None or cf_ocf[2] > ocf_asof):
        val, start, end = cf_ocf
        ocf_value, ocf_span, ocf_asof = val, max((end - start).days, 1), end
        ocf_src = f"companyfacts ({cf_ocf_unit}, {start} → {end} — fresher than the statement)"
        if doc_ocf is not None:
            notes.append(
                f"the statement's own figure {_fmt(doc_ocf)} (as of {report_date}) is OLDER and was "
                "not used"
            )
    elif cf_ocf is not None and doc_ocf is not None and cf_ocf[2] == report_date:
        if abs(abs(cf_ocf[0]) - abs(doc_ocf)) <= 0.01 * max(abs(doc_ocf), 1.0):
            notes.append("companyfacts agrees with the statement's operating cash flow")
        else:
            flags.append("source-disagreement")
            notes.append(
                f"SAME-DATE DISAGREEMENT on operating cash flow: statement {_fmt(doc_ocf)} vs "
                f"companyfacts {_fmt(cf_ocf[0])} ({cf_ocf_unit}) as of {report_date} — ratify "
                "against the passage"
            )
    elif cf_ocf is not None and doc_ocf is not None:
        notes.append(
            f"companyfacts still serves {_fmt(cf_ocf[0])} ({cf_ocf_unit}) for {cf_ocf[1]} → "
            f"{cf_ocf[2]} — behind the filing (annual XBRL lags); the statement wins"
        )
    if ocf_value is None and doc_ocf_raw is None and uniform_sign is None:
        # a located row whose value AND sign are unreadable — unread, not empty
        return [], "statements-not-located"

    # ---- the cash side ------------------------------------------------------------------------------
    cash_value: float | None = None
    cash_asof: date | None = None
    cash_src = ""
    c_label = text[c_start:c_end_pos]
    passages.append(_passage(text, "balance-sheet", annual_ref, c_start, c_label))
    c_years = _column_years(text, c_region, c_start)
    c_raw, c_flag, _ = _read_row_values(_value_window(text, c_end_pos), c_years, report_year)
    if c_flag and c_flag not in flags:
        flags.append(c_flag)
    if (
        c_raw is not None and c_raw >= 0
    ):  # negative "cash" = a note's subtraction line, not a balance
        if c_scale is None:
            if "unit-scale-unread" not in flags:
                flags.append("unit-scale-unread")
                notes.append(_SCALE_UNREAD_NOTE)
            cash_value, cash_asof, cash_src = c_raw, report_date, "statement"
        else:
            cash_value, cash_asof, cash_src = c_raw * c_scale, report_date, "statement"
        if c_marker and c_marker != scale_marker:
            notes.append(f"balance-sheet scale marker: “{c_marker}”")
    cf_cash_unit = _cf_unit_for(companyfacts, _CASH_CONCEPT, currency)
    cf_cash = (
        _cf_latest_instant(_cf_units(companyfacts, _CASH_CONCEPT).get(cf_cash_unit, []))
        if cf_cash_unit
        else None
    )
    # companyfacts may WIN the cash because the statement row IS located (the passage the value rides
    # with — the shares CMND precedent); the note states the losing side.
    if cf_cash is not None and (cash_asof is None or cf_cash[1] > cash_asof):
        if cash_value is not None:
            notes.append(
                f"cash: the statement's {_fmt(cash_value)} (as of {cash_asof}) is OLDER and was not used"
            )
        cash_value, cash_asof = cf_cash
        cash_src = f"companyfacts ({cf_cash_unit} — fresher than the statement)"
    elif cf_cash is not None and cash_asof is not None and cf_cash[1] == cash_asof:
        if cash_value is not None and abs(cf_cash[0] - cash_value) <= 0.01 * max(cash_value, 1.0):
            notes.append("companyfacts agrees with the statement's cash balance")
        elif cash_value is not None:
            if "source-disagreement" not in flags:
                flags.append("source-disagreement")
            notes.append(
                f"SAME-DATE DISAGREEMENT on cash: statement {_fmt(cash_value)} vs companyfacts "
                f"{_fmt(cf_cash[0])} ({cf_cash_unit}) as of {cash_asof} — ratify against the passage"
            )

    # ---- compose: sign is the state; burn is span-normalized to the meter's quarter ----------------
    burn: float | None = None
    generative: bool | None = None
    if ocf_value is not None and ocf_span:
        per_day = ocf_value / ocf_span
        burn = -per_day * _DAYS_PER_QUARTER  # negative OCF (burning) -> a POSITIVE quarterly burn
        generative = ocf_value >= 0
    elif uniform_sign is not None:
        generative = not uniform_sign  # every column shares a sign: the state survives ambiguity

    age_ref = ocf_asof or cash_asof or report_date
    if (today - age_ref).days > cfg.annual_stale_runway_days:
        flags.append("stale-runway")

    parts: list[str] = [f"{annual_form} financial statements ({currency or 'currency unread'}"]
    if scale_marker:
        parts[-1] += f", “{scale_marker}”"
    parts[-1] += ")."
    if ocf_value is not None:
        parts.append(
            f"Operating cash flow {_fmt(ocf_value)} over {ocf_span} days "
            f"(ending {ocf_asof}; {ocf_src or 'statement'}"
            + (f"; span: {span_basis}" if ocf_src == "statement" and span_basis else "")
            + ")."
        )
    if cash_value is not None:
        parts.append(f"Cash {_fmt(cash_value)} as of {cash_asof} ({cash_src or 'statement'}).")
    if generative:
        parts.append(
            "CASH-GENERATIVE (operating cash flow is positive) — no runway applies; the meter reads "
            "top-pip on ratify, with no months figure."
        )
    elif burn is not None and cash_value is not None and burn > 0:
        months = cash_value / (burn / 3.0)
        parts.append(
            f"Quarterly burn (span-normalized) {_fmt(burn)} → implied runway ≈ {months:.1f} months "
            f"(~{months / 12:.1f}y) at this burn."
        )
    elif generative is False:
        parts.append(
            "BURNING (operating cash flow is negative); a value above could not be read — author from the passages."
        )
    if (today - age_ref).days > cfg.annual_stale_runway_days:
        parts.append(
            f"STALE: the newest reading ends {age_ref} — {(today - age_ref).days} days before "
            f"{today}; annual filers refresh yearly, this is beyond even that."
        )
    parts.extend(f"{n}." if not n.endswith(".") else n for n in notes)
    if len(flags) > 1:
        parts.append("FLAGS: " + ", ".join(flags[1:]) + " — confirm against the passages.")

    return [
        ExtractedFact(
            fact_type="cash_burn",
            tier=Tier.FLAG,  # ALWAYS — an annual statement's composition is the operator's to ratify
            cash_usd=cash_value,
            quarterly_burn_usd=burn,
            # the already-detected statement currency, carried DISPLAY-ONLY so the FE labels cash/burn
            # in the filer's native currency (cash NT$ …) — never converted, never a scoring input
            statement_currency=currency,
            source="annual-statements",
            source_ref=annual_ref,
            event_date=age_ref,
            flags=flags,
            located_passages=passages,
            note=" ".join(parts),
        )
    ], None


# ---------------------------------------------------------------------------------------------------------
# the live wrapper — ONE document fetch shared by shares + runway
# ---------------------------------------------------------------------------------------------------------


def annual_facts_for_security(
    client: EdgarClient,
    cik: str | int,
    *,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    today: date | None = None,
) -> ExtractionResult:
    """Live (cache-first) wrapper for a DARK name: CIK -> the latest 20-F/40-F, fetched ONCE -> the
    annual-cover shares candidate (``annual_shares.extract_annual_shares``, behavior unchanged) PLUS
    the annual-statements cash/runway candidate, or their honest, DISTINCT empty reasons.

    ``empty_reason`` keeps its Slice-1 semantics (set only when NO facts at all):
    ``no-annual-filing`` · ``cover-not-located``. ``runway_empty_reason`` is the runway leg's own
    state whenever an annual filing exists but no cash_burn fact could be emitted:
    ``cash-generative`` · ``financials-in-exhibit`` · ``statements-not-located`` (see the module
    docstring). An EXPLICIT operator action via the extract endpoint, never fired on a render.
    """
    cik = int(cik)
    subs = fetch_submissions(client, cik)
    filing = _latest_annual_filing(client, cik, subs=subs)
    if filing is None:
        return ExtractionResult(facts=[], empty_reason="no-annual-filing")
    url, text, report_dt, form = filing
    forms = subs.get("filings", {}).get("recent", {}).get("form", [])
    has_f6 = any(str(f).startswith("F-6") for f in forms)
    cf = _companyfacts_or_none(client, cik)
    when = today or date.today()
    shares = extract_annual_shares(
        cf,
        text,
        annual_ref=url,
        annual_form=form,
        report_date=report_dt,
        today=when,
        has_f6_filing=has_f6,
        cfg=cfg,
    )
    runway, runway_reason = extract_annual_runway(
        cf,
        text,
        annual_ref=url,
        annual_form=form,
        report_date=report_dt,
        today=when,
        cfg=cfg,
    )
    facts = shares + runway
    if not facts:
        return ExtractionResult(
            facts=[], empty_reason="cover-not-located", runway_empty_reason=runway_reason
        )
    return ExtractionResult(facts=facts, runway_empty_reason=runway_reason)


__all__ = ["extract_annual_runway", "annual_facts_for_security"]
