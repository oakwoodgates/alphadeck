"""Annual-statements cash + runway for the DARK names (Retrieval Slice A) — IFRS 20-F/40-F, FLAG-only.

A basket name with NO 10-K/10-Q (a foreign private issuer) gets an honest **runway** — cash and a
span-normalized operating-cash burn — read from the financial statements of the SAME 20-F/40-F document
the annual-cover shares path already fetches (ONE fetch, shared by ``annual_facts_for_security`` below).
Always tier FLAG, always carrying the located statement rows as passages. A cash-GENERATIVE name (the
operating-cash-flow sign is the state) is marked **"cash-generative"** — never a bogus runway number.
The rules are canon in ``docs/EXTRACTOR.md`` ("The annual-statements runway path"); the
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
- **Currencies.** Foreign filers report in their own currency (CNY/JPY/CAD/EUR/TWD/SGD...), often with
  a convenience US$ column beside it. Runway is a RATIO, so months are currency-independent — but
  only if cash and burn share a currency. The currency is settled in this order (the extractor
  correctness plan's PR 4): (1) the companyfacts unit that REPRODUCES the statement's figures
  exactly (us-gaap or ifrs-full, any unit — a match settles the currency; a header that says
  otherwise is ``currency-mismatch``); (2) the statement header's markers (a home currency beside a
  US$ convenience column reads as the home currency; a bare ``$`` is NOT USD); (3) else
  ``currency-unread`` — never a blank or guessed USD. (The stored fields are named ``*_usd``; for a
  native-currency filer they carry statement-currency values — the note names the currency.)

THE TABLE AND THE LINE. A 20-F often prints a parent-only, VIE/subsidiary breakdown or segment balance
sheet BEFORE the consolidated statements. Every cash-row site is collected; the one companyfacts
reproduces wins, else the first in a table with no breakdown cue in its heading, else the first site
flagged ``table-unverified``. The operating cash flow follows the same order and comes from the same
statement set (the first qualifying cash-flow statement after the chosen balance sheet). A notes line
("Cash paid for operating leases included in cash flows from operating activities") is never the total.

NEITHER SOURCE DOMINATES — later-as-of-wins per quantity (the shares rule): companyfacts often LAGS
the filing by a full year (annual XBRL ingests slowly — the reason the filing is read at all), and is
sometimes FRESHER (a 6-K interim landing after the last annual). The statement rows carry the located
passages either way. When every figure offered comes from one later filing's companyfacts rows, the
candidate cites that filing (ARM's Q2 6-K, never its March 20-F), and a fresher interim year to date (six or
nine months) becomes its quarter when the same tag's prior year to date is on file (1,109 - 902), never
pro-rated. THE CROSS-CHECK is exact at the statement's printed precision (a 1% tolerance
passed a wrong column within 0.4%): ``figures-disagree`` fires only on a SAME-DATE contradiction and
states both numbers; no same-date row is a stated condition ("not cross-checked"), never a pass; a
cross-date difference is a lag, stated in the note, not an alarm (annual XBRL lag is the RULE here,
and a flag true of every name carries no information).

CASH MEANS CASH PLUS SHORT-TERM DEPOSITS AND INVESTMENTS (operator decision D1): a short-term
deposits/investments row of the same balance sheet is added to the cash row, each part named in the
note and carried on ``investments_usd``, and so is an own-line current "Marketable securities" row
(D6, ZENA); the generic long-term investments line is never cash (D2).
THE ADDITION IS ONLY EVER FROM THE SAME STATEMENT (the 2026-11-16 re-audit's guard): the cash row must be
a balance-sheet STATEMENT row (a title heading, no breakdown cue — never a note, MD&A table or summary),
and the deposits row its own line in the same year column with a value in it (a dash is an empty cell;
a blank cell beside a note reference is never the note number). A row seen and not counted is quoted in
the note and flagged ``investments-unconfirmed``. Cash is a BALANCE: a flow line (net increase or
decrease, an exchange-rate effect, an opening balance) is never valued; the cash-flow statement's
closing balance is a fallback when no balance-sheet row is readable. The column years come from the
table's own header; an explicit presentation-currency statement ("presented in United States Dollars",
"USD 000") settles the currency when the headers name none.

FAIL CLOSED: no located statement rows -> NO fact (no passage -> no fact), with an honest, distinct
``runway_empty_reason``: ``cash-generative`` (positive operating cash flow — a state, not a gap) ·
``financials-in-exhibit`` (a burning name whose statements live outside every document read — the
40-F/MJDS wrapper shape after the exhibit hunt too came back empty) · ``exhibit-ambiguous`` (more
than one exhibit carries the statement signature — deferred, never a guessed exhibit; stamped by the
statement-source seam, Slice A-2) · ``statements-not-located`` (sign unknowable — unread, not
empty). Deterministic parse only (#3): no LLM anywhere on this path; ``today`` and ``report_date``
are parameters (no implicit now).

WHERE THE STATEMENT TEXT COMES FROM is the statement-source seam's job (Slice A-2,
``ingest/edgar/statement_sources.py``): the primary document first (the text already fetched for the
cover — fetch-once), else the EX-99 exhibit hunt (bounded, content-signature, fail-closed). This
module consumes whatever text the seam resolves and extracts from it UNCHANGED — the passages cite
the winning document.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, LocatedPassage, Tier
from domain.links import filing_index_url
from domain.market_time import market_today
from ingest.edgar.annual_shares import (
    _companyfacts_or_none,
    _latest_annual_filing,
    extract_annual_shares,
)
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import _days, _doc_url
from ingest.edgar.submissions import fetch_submissions, filings_of

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
# A FLOW line is never a balance (the 2026-11-16 re-audit: ARQQ's "Net increase/(decrease) in cash and cash
# equivalents" slipped past the veto above as its cash). Its own row (the text after the previous row's
# figures) naming a movement, an exchange-rate effect or an opening balance marks the site a flow: still a
# located site (the statement-source signature and ``cash-unread`` keep their meaning), never a value.
_CASH_FLOW_ROW_RE = re.compile(
    r"increase|decrease|movement|change|effect|beginning|opening", re.IGNORECASE
)
# The cash-flow statement's CLOSING balance ("Cash and cash equivalents at end of period") is a balance: a
# fallback cash site when no balance-sheet cash row is readable (ARQQ's real shape).
_CASH_CLOSING_RE = re.compile(
    r"cash\s+and\s+cash\s+equivalents[\s,]*(?:at|as\s+(?:at|of))?\s*(?:the\s+)?end\s+of\s+(?:the\s+)?"
    r"(?:financial\s+|fiscal\s+|reporting\s+)?(?:year|period)",
    re.IGNORECASE,
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
_CURRENCY_CUT_ALLOW = {
    "usd",
    "cad",
    "chf",
    "eur",
    "gbp",
    "nis",
    "ils",
    "twd",
    "inr",
    "jpy",
    "rmb",
    "cny",
    "sgd",
    "hkd",
    "aud",
    "krw",
    "brl",
    "zar",
}

# Scale + currency read from the statement region (nearest marker ABOVE the row wins — that is the
# row's own table header). Precedence: an explicit thousands/millions marker beats a bare
# "expressed in ... dollars" (HYFT states both: "(Expressed in Canadian dollars) (in thousands)").
# An ISO code with an apostrophe before 000 (ASND's "(EUR’000)") is a thousands marker like "$’000".
_SCALE_THOUSANDS_RE = re.compile(
    r"in\s+(?:[a-z$€£₹.\s]{0,14})?thousands?\b|thousands\s+of|[$€£₹]\W{0,3}000|\bk\W{0,2}€|NT\$\s*000"
    r"|\b(?:USD|EUR|GBP|CHF|CAD|AUD|DKK|SEK|NOK|ZAR)\s?[’']\s?000\b",
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
# Statement currency markers (PR 4). One alternation, scanned left to right: at any position the first
# alternative wins, so "US$", "NT$", "HK$", "S$", "C$" and "A$" are consumed before the bare "$" can
# match inside them. A bare "$" (or a bare "dollars") names NO currency: it is the ambiguous marker,
# never USD (a Canadian filer prints "$" too). Codes are ISO 4217.
_CURRENCY_MARKERS: tuple[tuple[str | None, str], ...] = (
    ("TWD", r"new\s+taiwan\s+dollars?|NT\$|\bTWD\b"),
    (
        "USD",
        r"(?<![A-Za-z])US\$|\bU\.S\.\s?\$|\bU\.?\s?S\.?\s*dollars?\b|united\s+states\s+dollars?|\bUSD\b",
    ),
    ("HKD", r"(?<![A-Za-z])HK\$|hong\s+kong\s+dollars?|\bHKD\b"),
    ("SGD", r"(?<![A-Za-z])S\$|singapore\s+dollars?|\bSGD\b"),
    ("AUD", r"(?<![A-Za-z])A\$|australian\s+dollars?|\bAUD\b"),
    ("CAD", r"(?<![A-Za-z])(?:CDN|C)\$|canadian\s+dollars?|\bCAD\b"),
    ("BRL", r"(?<![A-Za-z])R\$|brazilian\s+reais|\breais\b|\bBRL\b"),
    ("CNY", r"\bRMB\b|renminbi|\byuan\b|\bCNY\b"),
    ("JPY", r"¥|\byen\b|\bJPY\b"),
    ("KRW", r"₩|korean\s+won\b|\bKRW\b"),
    ("EUR", r"€|\beuros?\b|\bEUR\b"),
    ("INR", r"₹|\bRs\.|indian\s+rupees?|\bINR\b"),
    ("CHF", r"\bCHF\b|swiss\s+francs?"),
    ("ILS", r"\bNIS\b|₪|israeli\s+(?:new\s+)?shekels?|\bILS\b"),
    ("GBP", r"£|pounds?\s+sterling|\bGBP\b"),
    ("ZAR", r"south\s+african\s+rand\b|\bSA\s+rand\b|\bZAR\b"),
    (None, r"\$|\bdollars?\b"),  # ambiguous: names no currency on its own
)
_CURRENCY_RE = re.compile(
    "|".join(f"(?P<c{i}>{pat})" for i, (_, pat) in enumerate(_CURRENCY_MARKERS)), re.IGNORECASE
)
_CURRENCY_OF_GROUP = {f"c{i}": code for i, (code, _) in enumerate(_CURRENCY_MARKERS)}

# A table that looks like a breakdown rather than the consolidated statement (PR 4): parent-only,
# condensed consolidating (VIE / subsidiaries / eliminations), Schedule I, or a segment or business
# split. Searched in the heading window only — a little before the heading, and its first characters
# (the column headers) — never in the rows. "XYZ Ltd. and (its) subsidiaries" is the consolidated
# statement's own title and is not a cue (_AND_SUBSIDIARIES_RE).
_BREAKDOWN_CUE_RE = re.compile(
    r"parent\s+company|parent[-\s]only|\bparent\b|condensed|consolidating|schedule\s+I\b|\bVIEs?\b"
    r"|variable\s+interest\s+entit|subsidiar(?:y|ies)\b|eliminations?\b|\bsegments?\b"
    r"|non-financial\s+services|financial\s+services\s+businesses",
    re.IGNORECASE,
)
_AND_SUBSIDIARIES_RE = re.compile(r"(?:\band|&)\s+(?:its\s+)?(?:consolidated\s+)?$", re.IGNORECASE)
_OF_THE_PARENT_RE = re.compile(r"of\s+the\s+$", re.IGNORECASE)
_CUE_BEFORE = 160  # heading window: characters before the statement heading
_CUE_AFTER = 400  # ...and after it (the title, units and column headers)

# A notes line that MENTIONS operating activities is never the total (QGEN: "Cash paid for operating
# leases included in cash flows from operating activities $ 31,741"): the label is rejected when one of
# these sits within 60 characters before it.
_OCF_NOTE_GUARD_RE = re.compile(r"included\s+in|paid\s+for|operating\s+leases?", re.IGNORECASE)
_OCF_NOTE_GUARD_CHARS = 60

# Short-term deposits and investments on the SAME balance sheet as the cash row (D1: cash means cash
# plus short-term investments), and an own-line "Marketable securities" row (D6: ZENA's current
# 9,093,887), never a long-term line (D2). A marketable-securities row is current only by its section:
# no assets heading may sit between it and the cash row (an IFRS balance sheet lists non-current first).
_STI_LABEL_RE = re.compile(
    r"short[-\s]?term\s+(?:bank\s+)?(?:deposits?|investments?)|\btime\s+deposits?"
    r"|(?P<securities>\bmarketable\s+securities\b)",
    re.IGNORECASE,
)
_ASSET_SECTION_RE = re.compile(
    r"(?:non[-\s]?current|long[-\s]?term|current)\s+assets", re.IGNORECASE
)
_STI_PRE_VETO_RE = re.compile(
    r"(?:restricted|pledged|long[-\s]?term|non[-\s]?current"
    r"|(?:investments?|purchases?|proceeds|withdrawals?|placements?)\s+(?:in|of|from|to)"
    r"|cash(?:\s+equivalents)?,?\s+and)\s*$",
    re.IGNORECASE,
)
_STI_BOUNDARY_RE = re.compile(
    r"total\s+(?:current\s+)?assets|statements?\s+of\s+cash\s+flows?", re.IGNORECASE
)
_STI_SPAN = 1_500  # how far from the cash row a same-balance-sheet deposits row may sit
# A deposits/investments row whose own row names a composition, a total or cash itself is a component of cash,
# a summary or a reconciliation line — never its own current asset (the 2026-11-16 re-audit: QGEN's "deposits"
# inside cash, CGNT's prior-year "Total cash, ..., time deposits" summary). Seen, quoted, NOT counted.
_STI_COMPONENT_RE = re.compile(
    r"of\s+which|includ(?:ing|es|ed)|compris(?:ing|es|ed)|consist(?:ing|s|ed)\s+of|\btotal\b|\bcash\b",
    re.IGNORECASE,
)
_STI_OWN_ROW_CHARS = (
    80  # how far back a row's own label may start (after the previous row's figures)
)
# A real balance-sheet STATEMENT heading is printed as a title ("CONSOLIDATED BALANCE SHEETS", "Statement of
# Financial Position"); a prose mention ("recognized on the balance sheet at fair value", "off-balance sheet
# arrangements", "as presented in our consolidated balance sheets") opens a note or MD&A region instead.
_PROSE_BEFORE_HEADING_RE = re.compile(
    r"(?:\b(?:our|the|in|on|see|to|within|of|its|their|and|from)\s+(?:[Cc]onsolidated\s+|[Cc]ombined\s+)?|-)$"
)
_SUMMARY_AFTER_HEADING_RE = re.compile(r"\s*(?:data|information|highlights)\b", re.IGNORECASE)
# The data that ends a table's column header: the first comma-grouped figure after the first year token.
_DATA_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
_NOTE_COLUMN_RE = re.compile(r"\bNotes?\b")
# A dash cell: a standalone dash in the value area (an empty column, not a sign).
_CELL_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,6}(?:\.\d+)?)|(?P<dash>(?:(?<=\s)|^)[-–—](?=\s|$))"
)
# The filing's own presentation-currency statement ("presented in United States Dollars", "expressed in U.S.
# dollars", "USD 000", "(in thousands of US dollars)", "Figures in million - SA rand"): read anywhere in the
# document, used only when the statement headers name no currency. "denominated in" is deliberately NOT read
# (QGEN: "most of our leases are denominated in euros").
_PRESENTATION_LEAD_RE = re.compile(
    r"(?:presented|expressed)\s+in\s+(?:the\s+)?(?:(?:thousands?|millions?)\s+of\s+)?"
    r"|presentation\s+currency\s+(?:of\s+the\s+[\w\s]{1,30}?\s+)?is\s+(?:the\s+)?"
    r"|\bin\s+(?:thousands?|millions?)\s+of\s+"
    r"|figures\s+in\s+(?:thousands?|millions?)\s*[-–—:]?\s*",
    re.IGNORECASE,
)
_CODE_THOUSANDS_RE = re.compile(r"\b(?P<code>USD|EUR|GBP|CAD|CHF|ZAR)\s*[’']?\s*000\b")
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


_PAREN_NOTE_REF_PREFIX_RE = re.compile(r"^[\s:]{0,4}\(\s*\d{1,2}(?:\s*[,&]\s*\d{1,2})*\s*\)")

# A note-reference LIST before the figures (ASND: "Cash and cash equivalents 17, 18 616,041 559,543" cites
# notes 17 and 18): one- or two-digit numbers joined by a comma and a space, "&" or "and", then a figure. A
# grouped figure never has a space after its comma, so the list is the row's note references, stripped like
# "(Note N)"; the count rule in ``_map_columns`` drops only ONE leading note number and read ASND's 18 as its
# cash.
_NOTE_REF_LIST_PREFIX_RE = re.compile(
    r"^[\s:]{0,4}\d{1,2}(?:(?:\s*,\s+|\s*&\s*|\s+and\s+)\d{1,2})+(?=\s+[-–—($€£₹]?\s?\d)"
)


def _value_window(text: str, label_end: int, note_col: bool = False) -> str:
    """The row's VALUE AREA: from the label to the next row's label word (or the window cap). Cutting at
    the first >=3-letter non-currency word keeps the next row's numbers out — a fixed-width window read
    neighboring rows into the columns on tightly-packed statements. A leading "(Note N)" reference is
    stripped first (it would otherwise BE the cut word and empty the window); in a table whose header
    prints a Note column, a bare parenthesized "(3)" right after the label is that note reference too
    (QGEN's shape — read as a figure it was a negative first column). A leading note-reference list
    ("17, 18") is stripped the same way."""
    raw = text[label_end : label_end + _ROW_VALUE_CHARS]
    raw = _NOTE_REF_PREFIX_RE.sub(" ", raw, count=1)
    raw = _NOTE_REF_LIST_PREFIX_RE.sub(" ", raw, count=1)
    if note_col:
        raw = _PAREN_NOTE_REF_PREFIX_RE.sub(" ", raw, count=1)
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


def _cells(window: str) -> list[float | None]:
    """The row's cells in order: each value token, and None for a standalone dash (an empty column). Used
    where an empty current-year cell must not shift the columns (a deposits row: "5 — 1,254" is note 5, an
    empty 2025 cell and 1,254 for 2024 — never 5 for 2025)."""
    out: list[float | None] = []
    for m in _CELL_RE.finditer(window):
        if m.group("dash"):
            out.append(None)
            continue
        tok = m.group("num")
        if "," not in tok and "." not in tok and len(tok) == 4 and _YEAR_RE.fullmatch(tok):
            continue
        pre = window[max(0, m.start() - 4) : m.start()]
        sign = -1.0 if ("(" in pre or re.search(r"(?<![-–—])-\s*$", pre)) else 1.0
        out.append(sign * float(tok.replace(",", "")))
    return out


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


def _collapse_tail(years: list[int]) -> list[int]:
    years = list(years)
    while len(years) >= 2 and years[-1] == years[-2]:
        years.pop()
    return years


def _header_block(text: str, region_start: int, row_start: int) -> tuple[int, int]:
    """The table's COLUMN HEADER above the row: from the backscan start to the first comma-grouped figure
    after the first year token (the first row's data). Year tokens printed later, inside row labels
    ("Share-based payment (2024 and 2025 plans)", a second summary table's header), are not the row's
    column headers (the 2026-11-16 re-audit: WAVE's operating cash flow came from the 2023 column).
    """
    lo = max(region_start, row_start - _HEADER_BACKSCAN)
    first_year = _YEAR_RE.search(text, lo, row_start)
    if first_year is None:
        return lo, lo
    data = _DATA_TOKEN_RE.search(text, first_year.end(), row_start)
    return lo, (data.start() if data else row_start)


def _header_years(text: str, region_start: int, row_start: int) -> list[int]:
    """The column-year headers read from the table's own header block (``_header_block``), with a trailing
    duplicate run collapsed (a convenience second-currency column repeats the last fiscal year)."""
    lo, hi = _header_block(text, region_start, row_start)
    return _collapse_tail([int(m.group(0)) for m in _YEAR_RE.finditer(text, lo, hi)])


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


@dataclass(frozen=True)
class _CurrencyRead:
    """One statement header's currency, read from its markers: the ISO code (None = not determinable),
    how it was read (for the note), and whether the header is a home-currency layout with a US$
    convenience column beside it."""

    code: str | None
    basis: str
    convenience: bool = False


def _header_currency(
    text: str, region_start: int, row_start: int, *, extra_columns: bool
) -> _CurrencyRead:
    """The currency the statement header names above the row (PR 4). Explicit markers only: a bare "$"
    or "dollars" names no currency. One explicit currency -> it. A home currency plus US$ on a row that
    carries more value columns than fiscal years (or a repeated last year) is the convenience-
    translation layout: the home currency, whose column the year mapping picks. Several otherwise ->
    the nearest marker above the row."""
    explicit: list[tuple[int, str, str]] = []
    bare: str | None = None
    for m in _CURRENCY_RE.finditer(text, region_start, row_start):
        code = _CURRENCY_OF_GROUP[m.lastgroup or ""]
        if code is None:
            bare = bare or m.group(0)
            continue
        explicit.append((m.start(), code, re.sub(r"\s+", " ", m.group(0))))
    codes = list(dict.fromkeys(code for _, code, _ in explicit))
    if not codes:
        if bare is not None:
            return _CurrencyRead(
                None, f"the header prints a bare “{bare}” with no US$, USD or U.S. dollars marker"
            )
        return _CurrencyRead(None, "the header names no currency")
    if len(codes) == 1:
        marker = next(mk for _, code, mk in explicit if code == codes[0])
        return _CurrencyRead(codes[0], f"the header's “{marker}”")
    home = [c for c in codes if c != "USD"]
    if "USD" in codes and len(home) == 1 and extra_columns:
        return _CurrencyRead(
            home[0],
            f"the header names {home[0]} beside a US$ convenience column; the figure is read from "
            f"the {home[0]} column",
            convenience=True,
        )
    _, code, marker = max(explicit)
    return _CurrencyRead(
        code,
        f"several currencies in the header ({', '.join(codes)}); the nearest above the row "
        f"(“{marker}”) was taken",
    )


def _presentation_currency(text: str) -> _CurrencyRead | None:
    """The filing's own presentation-currency statement, read anywhere in the document (D, the 2026-11-16
    guard: ARQQ says it is "presented in United States Dollars (USD 000)" outside its statement headers).
    Only an EXPLICIT marker right after the phrase counts (a bare "$" or "dollars" never does); several
    statements naming different currencies settle nothing."""
    found: dict[str, str] = {}
    for lead in _PRESENTATION_LEAD_RE.finditer(text):
        m = _CURRENCY_RE.match(text, lead.end())
        code = _CURRENCY_OF_GROUP[m.lastgroup or ""] if m else None
        if m is not None and code is not None:
            found.setdefault(code, re.sub(r"\s+", " ", text[lead.start() : m.end()]).strip())
    for m in _CODE_THOUSANDS_RE.finditer(text):
        found.setdefault(m.group("code").upper(), re.sub(r"\s+", " ", m.group(0)))
    if len(found) != 1:
        return None
    code, phrase = next(iter(found.items()))
    return _CurrencyRead(code, f"the filing states “{phrase}”")


def _breakdown_cue(text: str, region_start: int, row_start: int) -> str | None:
    """The first breakdown cue (parent-only, consolidating, VIE, subsidiaries, segment...) in the
    table's heading window, or None for a table that reads as the consolidated statement."""
    lo = max(0, region_start - _CUE_BEFORE)
    hi = max(lo, min(row_start, region_start + _CUE_AFTER))
    for m in _BREAKDOWN_CUE_RE.finditer(text, lo, hi):
        word = m.group(0).lower()
        pre = text[max(0, m.start() - 30) : m.start()]
        if word.startswith("subsidiar") and _AND_SUBSIDIARIES_RE.search(pre):
            continue  # "XYZ Ltd. and its subsidiaries" titles the consolidated statement
        if word == "parent" and _OF_THE_PARENT_RE.search(pre):
            continue  # "owners of the parent" is an IFRS equity line, not a parent-only table
        return re.sub(r"\s+", " ", m.group(0))
    return None


def _printed_decimals(window: str) -> int:
    """How many decimals the row prints (a statement row prints every column at one precision)."""
    best = 0
    for m in _COMMA_TOKEN_RE.finditer(window):
        if "." in m.group(0):
            best = max(best, len(m.group(0).split(".", 1)[1]))
    return best


def _raw_years(text: str, region_start: int, row_start: int) -> list[int]:
    lo = max(region_start, row_start - _HEADER_BACKSCAN)
    return [int(m.group(0)) for m in _YEAR_RE.finditer(text, lo, row_start)]


@dataclass(frozen=True)
class _Row:
    """One located statement row and everything read from it: the current-column value AS PRINTED
    (``raw``), the printed precision, the table's own scale marker and breakdown cue."""

    start: int
    label_end: int
    region: int
    label: str
    raw: float | None
    col_flag: str | None
    vals: list[float]
    years: list[int]
    decimals: int
    scale: float | None
    marker: str | None
    cue: str | None
    extra_columns: bool
    # the 2026-11-16 guard: what KIND of cash site this is ("balance" — a balance-sheet or note row,
    # "closing" — the cash-flow statement's end-of-period balance, "flow" — a movement line, never valued),
    # whether its table is a balance-sheet STATEMENT (a title heading, not a prose mention), whether the
    # header prints a Note column, and the row's cells with dashes kept as empty columns
    kind: str = "balance"
    statement: bool = False
    note_col: bool = False
    cells: tuple[float | None, ...] = ()


def _is_statement_heading(text: str, region: int) -> bool:
    """True when the balance-sheet heading at ``region`` is a STATEMENT title ("CONSOLIDATED BALANCE
    SHEETS", "Statement of Financial Position"), False for a prose mention that opens a note or MD&A
    region ("recognized on the balance sheet at fair value", "off-balance sheet arrangements", "as
    presented in our consolidated balance sheets") or a selected-data summary ("Balance Sheet Data").
    """
    m = _BS_HEADING_RE.match(text, region)
    if m is None:
        return False
    if any(w[0].islower() for w in re.findall(r"[A-Za-z]+", m.group(0)) if len(w) > 2):
        return False
    if _PROSE_BEFORE_HEADING_RE.search(text[max(0, region - 30) : region]):
        return False
    return _SUMMARY_AFTER_HEADING_RE.match(text, m.end()) is None


def _read_row(
    text: str, start: int, label_end: int, region: int, report_year: int, kind: str = "balance"
) -> _Row:
    lo, hi = _header_block(text, region, start)
    note_col = _NOTE_COLUMN_RE.search(text, lo, hi) is not None
    window = _value_window(text, label_end, note_col)
    # The column years come from the table's OWN header (C, the 2026-11-16 guard); the older read (every
    # year token above the row) is the fallback only when the header alone cannot map the current column.
    years = _header_years(text, region, start)
    raw, col_flag, vals = _read_row_values(window, years, report_year)
    if raw is None:
        old_years = _column_years(text, region, start)
        if old_years != years:
            o_raw, o_flag, o_vals = _read_row_values(window, old_years, report_year)
            if o_raw is not None or not years:
                years, raw, col_flag, vals = old_years, o_raw, o_flag, o_vals
    count = len(vals)
    if count > len(years) >= 1 and 0 < vals[0] < 100 and float(vals[0]).is_integer():
        count -= 1  # the leading note reference
    raw_years = _raw_years(text, region, start)
    repeated_last = len(raw_years) >= 2 and raw_years[-1] == raw_years[-2]
    scale, marker = _nearest_scale(text, region, start)
    return _Row(
        start=start,
        label_end=label_end,
        region=region,
        label=text[start:label_end],
        raw=raw,
        col_flag=col_flag,
        vals=vals,
        years=years,
        decimals=_printed_decimals(window),
        scale=scale,
        marker=marker,
        cue=_breakdown_cue(text, region, start),
        extra_columns=repeated_last or count > len(years) >= 1,
        kind=kind,
        statement=_is_statement_heading(text, region),
        note_col=note_col,
        cells=tuple(_cells(window)),
    )


def _heading_snippet(text: str, region: int) -> str:
    snippet = re.sub(r"\s+", " ", text[region : region + 70]).strip()
    return snippet.rsplit(" ", 1)[0] if len(snippet) >= 70 else snippet


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


def _own_row(text: str, label_start: int, chars: int = _STI_OWN_ROW_CHARS) -> str:
    """The label's OWN row text before it: what follows the previous row's last figure."""
    pre = text[max(0, label_start - chars) : label_start]
    return re.split(r"\d", pre)[-1]


def _cash_sites(text: str) -> list[tuple[int, int, int, str]]:
    """Every (row_start, label_end, region_start, kind) cash-row site, in document order: a cash-label
    site that (a) sits within a statement region after a balance-sheet heading, (b) is not an
    increase/decrease total, and (c) is immediately followed by a value. When the full label matches
    nowhere in any region, the bare capitalized "Cash" row label is tried (HYFT's real shape) — a
    fallback, so the full-label names are untouched. PR 4 collects them ALL (a parent-only or segment
    table often comes first); ``_choose_cash`` picks. KIND (the 2026-11-16 guard): a site whose own row
    names a movement, an exchange-rate effect or an opening balance is a ``flow`` (located, never
    valued); the cash-flow statement's end-of-period rows are added as ``closing`` balances (a fallback
    when no balance-sheet row is readable)."""
    headings = [m.start() for m in _BS_HEADING_RE.finditer(text)]
    sites: list[tuple[int, int, int, str]] = []
    if headings:
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
                own = _own_row(text, m.start(), 60)
                kind = "flow" if _CASH_FLOW_ROW_RE.search(own) else "balance"
                sites.append((m.start(), m.end(), region, kind))
            if sites:
                break
    any_headings = sorted(headings + [m.start() for m in _CF_HEADING_RE.finditer(text)])
    for m in _CASH_CLOSING_RE.finditer(text):
        region = next(
            (h for h in reversed(any_headings) if h < m.start() <= h + _REGION_SPAN), None
        )
        if region is None or not _VALUE_GATE_RE.search(text[m.end() : m.end() + 18]):
            continue
        if _CASH_FLOW_ROW_RE.search(_own_row(text, m.start(), 60)):
            continue
        sites.append((m.start(), m.end(), region, "closing"))
    return sorted(sites)


def _locate_cash_row(text: str) -> tuple[int, int, int] | None:
    """(row_start, label_end, region_start) of the FIRST balance-sheet cash-row site — the content
    signature the statement-source seam tests for (a document carries a balance-sheet cash row). The
    cash-flow statement's closing rows are not part of the signature (it is unchanged by them)."""
    sites = [s for s in _cash_sites(text) if s[3] != "closing"]
    return sites[0][:3] if sites else None


def _is_notes_line(text: str, label_start: int) -> bool:
    """True when the operating-activities label sits inside a notes line that merely MENTIONS it ("Cash
    paid for operating leases included in cash flows from operating activities"). Only the label's own
    row counts: the text after the last digit before it (the previous row's figures end there), so a
    real total under an "Operating lease liabilities (1,234)" row is untouched."""
    pre = text[max(0, label_start - _OCF_NOTE_GUARD_CHARS) : label_start]
    own_row = re.split(r"\d", pre)[-1]
    return _OCF_NOTE_GUARD_RE.search(own_row) is not None


def _ocf_sites(text: str) -> list[tuple[int, int, int]]:
    """Every operating-activities TOTAL site, one per cash-flow-statement region, in document order
    (a site reached from several overlapping regions keeps the nearest heading). Within a region a
    ``Net``-prefixed label outranks a bare subtotal ("Cash flows used in operating activities" can be a
    pre-finance-items subtotal on the same statement), then the first wins. A label preceded within 60
    characters by "included in", "paid for" or "operating leases" is a notes line, never the total.
    """
    by_start: dict[int, tuple[int, int, int]] = {}
    for h in _CF_HEADING_RE.finditer(text):
        lo, hi = h.start(), h.start() + _REGION_SPAN
        sites: list[tuple[bool, int, int]] = []
        for m in _OCF_LABEL_RE.finditer(text, lo, hi):
            if not _VALUE_GATE_RE.search(text[m.end() : m.end() + 14]):
                continue
            if _is_notes_line(text, m.start()):
                continue
            sites.append((bool(m.group("net")), m.start(), m.end()))
        if not sites:
            continue
        netted = [s for s in sites if s[0]] or sites
        _, start, end = netted[0]
        by_start[start] = (start, end, lo)
    return [by_start[k] for k in sorted(by_start)]


def _locate_ocf_row(text: str) -> tuple[int, int, int] | None:
    """(row_start, label_end, region_start) of the LAST operating-activities total — the content
    signature the statement-source seam tests for (MD&A liquidity tables repeat the statement earlier
    in the document; the F-pages statement comes last)."""
    sites = _ocf_sites(text)
    return sites[-1] if sites else None


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


# The cross-check's concepts (PR 4): BOTH taxonomies — a 20-F filer may report under US GAAP (NTES, EH,
# ZEPP, QGEN, JCSE) — read in EVERY unit. ``_PRIMARY`` concepts also feed the fresher-than-the-statement
# reading and the lag note (the restricted-cash total and the continuing-operations figure only ever
# confirm a statement value, never replace one).
_CF_CASH_PRIMARY = (
    ("ifrs-full", _CASH_CONCEPT),
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
)
_CF_CASH_CONCEPTS = _CF_CASH_PRIMARY + (
    ("ifrs-full", "Cash"),
    ("us-gaap", "Cash"),
    ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
)
_CF_OCF_PRIMARY = (
    ("ifrs-full", _OCF_CONCEPT),
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
)
_CF_OCF_CONCEPTS = _CF_OCF_PRIMARY + (
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
)
# A DEPOSITS row is compared only with deposit and generic short-term-investment concepts, never with a
# securities concept (E, the 2026-11-16 guard: a mismatch counts only within the same concept family).
_CF_SECURITY_CONCEPTS = frozenset(
    {
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent",
        "HeldToMaturitySecuritiesCurrent",
    }
)
_CF_DEPOSIT_CONCEPTS = frozenset(
    {
        "ShorttermDepositsNotClassifiedAsCashEquivalents",
        "TimeDepositsAtCarryingValue",
        "CertificatesOfDepositAtCarryingValue",
    }
)
_CF_STI_CONCEPTS = (
    ("ifrs-full", "ShorttermDepositsNotClassifiedAsCashEquivalents"),
    ("ifrs-full", "CurrentInvestments"),
    ("us-gaap", "ShortTermInvestments"),
    ("us-gaap", "MarketableSecuritiesCurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesCurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"),
    ("us-gaap", "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent"),
    ("us-gaap", "HeldToMaturitySecuritiesCurrent"),
    ("us-gaap", "OtherShortTermInvestments"),
    ("us-gaap", "CertificatesOfDepositAtCarryingValue"),
    ("us-gaap", "TimeDepositsAtCarryingValue"),
)


@dataclass(frozen=True)
class _CfRow:
    """One companyfacts row, with where it came from (named in the note) and the filing that carries it."""

    taxonomy: str
    concept: str
    unit: str
    val: float
    start: date | None
    end: date
    filed: str = ""
    accn: str = ""
    form: str = ""

    @property
    def where(self) -> str:
        return f"{self.taxonomy} {self.concept}, {self.unit}"


def _cf_rows(
    companyfacts: dict[str, Any] | None,
    concepts: tuple[tuple[str, str], ...],
    *,
    duration: bool,
    end: date | None = None,
    unit: str | None = None,
) -> list[_CfRow]:
    """Every companyfacts row of ``concepts`` in every unit (or only ``unit``), instants or durations,
    optionally only those ending on ``end``."""
    facts = (companyfacts or {}).get("facts", {}) or {}
    out: list[_CfRow] = []
    for taxonomy, concept in concepts:
        units = facts.get(taxonomy, {}).get(concept, {}).get("units", {}) or {}
        for u, rows in units.items():
            if unit is not None and u != unit:
                continue
            for r in rows:
                if bool(r.get("start")) != duration or "end" not in r or "val" not in r:
                    continue
                row_end = date.fromisoformat(r["end"])
                if end is not None and row_end != end:
                    continue
                start = date.fromisoformat(r["start"]) if r.get("start") else None
                out.append(
                    _CfRow(
                        taxonomy,
                        concept,
                        u,
                        float(r["val"]),
                        start,
                        row_end,
                        r.get("filed", ""),
                        r.get("accn", ""),
                        r.get("form", ""),
                    )
                )
    return out


def _latest_instant(rows: list[_CfRow]) -> _CfRow | None:
    """The newest instant row; at the same date the latest filed."""
    return max(rows, key=lambda r: (r.end, r.filed)) if rows else None


def _latest_duration(rows: list[_CfRow]) -> _CfRow | None:
    """The latest-ending duration row; at the same end the LONGEST span wins (an annual column over a
    same-ended quarter — the smoother burn basis, stated in the note), then the latest filed."""
    dur = [r for r in rows if r.start is not None]
    if not dur:
        return None
    return max(dur, key=lambda r: (r.end, (r.end - (r.start or r.end)).days, r.filed))


def _derived_quarter(
    companyfacts: dict[str, Any] | None, ytd: _CfRow, cfg: ExtractorConfig
) -> tuple[_CfRow, float, int] | None:
    """The quarter inside a fresher interim year to date (ARM: six months to 2026-09-30, 1,109), when
    companyfacts carries the same tag's prior year to date (three months to 2026-06-30, 902): (the prior row,
    ``ytd - prior``, the quarter's days). Only for an interim year to date, longer than one quarter and at
    most three (``quarterly_span_max_days``), whose prior shares its tag, unit and start and ends
    ``quarterly_span_min_days`` to ``quarterly_span_max_days`` before it (the latest such prior, then the
    latest filed). A fiscal year keeps its smoother annual basis. None otherwise: the year to date is
    span-normalized as before (PBM), never halved by guess.
    """
    if ytd.start is None:
        return None
    span = (ytd.end - ytd.start).days
    if not cfg.quarterly_span_max_days < span <= 3 * cfg.quarterly_span_max_days:
        return None
    priors = [
        r
        for r in _cf_rows(
            companyfacts, ((ytd.taxonomy, ytd.concept),), duration=True, unit=ytd.unit
        )
        if r.start == ytd.start
        and r.end < ytd.end
        and cfg.quarterly_span_min_days <= (ytd.end - r.end).days <= cfg.quarterly_span_max_days
    ]
    if not priors:
        return None
    prior = max(priors, key=lambda r: (r.end, r.filed))
    return prior, ytd.val - prior.val, (ytd.end - prior.end).days


def _filed_as(row: _CfRow) -> str:
    """How the note names the filing behind a companyfacts row: "the 6-K filed 2026-11-04 (accession ...)"."""
    form = row.form or "filing"
    filed = f" filed {row.filed}" if row.filed else ""
    return f"the {form}{filed} (accession {row.accn})"


def _reproduces(raw: float, decimals: int, scale: float, val: float) -> bool:
    """EXACT at the statement's printed precision: companyfacts' value, brought to the printed scale and
    rounded to the printed decimals, equals the printed figure. (A 1% tolerance passed EH's wrong column,
    0.4% off.)"""
    return abs(val / scale - raw) <= 0.5 * 10.0 ** (-decimals) + 1e-9


def _matching_rows(
    raw: float | None, decimals: int, scales: list[float], rows: list[_CfRow]
) -> list[_CfRow]:
    if raw is None:
        return []
    return [r for r in rows if any(_reproduces(raw, decimals, s, r.val) for s in scales)]


def _candidate_scales(row: _Row, fallback: float | None) -> list[float]:
    """The scales a row's printed figure may be compared at: its own marker, else the companion's, else
    each plausible one (a match then identifies the table, never the scale the note reports)."""
    if row.scale is not None:
        return [row.scale]
    if fallback is not None:
        return [fallback]
    return [1.0, 1_000.0, 1_000_000.0]


@dataclass
class _Check:
    """The cross-check of one statement figure against companyfacts on the statement's own date:
    ``agrees`` (a row reproduces it), ``disagrees`` (same-date rows exist, none reproduces it),
    ``no-row`` (companyfacts has no row on that date in either taxonomy, any unit) or ``unread`` (the
    statement figure itself could not be read)."""

    quantity: str
    status: str
    figure: float | None
    matched: list[_CfRow]
    rows: list[_CfRow]
    # set when the only reproducing rows are in another unit than the statement currency (the third
    # re-audit: VNET's CNY investment part was "agreed" by a USD row): the statement currency, and the
    # check is ``disagrees``
    foreign_of: str | None = None

    def units(self) -> set[str]:
        return {r.unit for r in self.matched}


def _cross_check(
    quantity: str,
    raw: float | None,
    decimals: int,
    scale: float,
    figure: float | None,
    rows: list[_CfRow],
) -> _Check:
    if raw is None:
        return _Check(quantity, "unread", None, [], rows)
    if not rows:
        return _Check(quantity, "no-row", figure, [], rows)
    matched = _matching_rows(raw, decimals, [scale], rows)
    return _Check(quantity, "agrees" if matched else "disagrees", figure, matched, rows)


def _in_statement_currency(check: _Check, currency: str | None) -> _Check:
    """A deposits/investments figure is AGREED only by a companyfacts row in the statement's own currency
    unit. A match only in another unit (VNET: a USD row reproducing the US$ convenience column's 54,225
    inside a CNY candidate) is a mismatch: ``disagrees``, both numbers and units stated."""
    if check.status != "agrees" or currency is None:
        return check
    same = [r for r in check.matched if r.unit == currency]
    if same:
        return _Check(check.quantity, "agrees", check.figure, same, check.rows)
    return _Check(
        check.quantity, "disagrees", check.figure, check.matched, check.rows, foreign_of=currency
    )


def _sti_family(label: str, rows: list[_CfRow]) -> list[_CfRow]:
    """The companyfacts rows a deposits/investments row may be compared with: a "deposits" label never
    meets a securities concept; an "investments" label never meets a deposit concept."""
    if re.search(r"deposit", label, re.IGNORECASE):
        return [r for r in rows if r.concept not in _CF_SECURITY_CONCEPTS]
    return [r for r in rows if r.concept not in _CF_DEPOSIT_CONCEPTS]


def _check_sentence(check: _Check, asof: date) -> str:
    if check.status == "agrees":
        where = "; ".join(dict.fromkeys(r.where for r in check.matched))
        return (
            f"companyfacts agrees with the statement's {check.quantity} ({where}; exact at the "
            "printed precision)"
        )
    if check.status == "disagrees" and check.foreign_of is not None:
        seen = "; ".join(dict.fromkeys(f"{_fmt(r.val)} ({r.where})" for r in check.matched))
        units = ", ".join(sorted(check.units()))
        return (
            f"FIGURES DISAGREE on {check.quantity}: statement {_fmt(check.figure or 0.0)} "
            f"{check.foreign_of} vs companyfacts {seen} on {asof} — the only companyfacts row that "
            f"reproduces the printed figure is in {units}, not the statement's {check.foreign_of} (a "
            "translated or other-currency figure is not agreement); ratify against the passage"
        )
    if check.status == "disagrees":
        seen = "; ".join(dict.fromkeys(f"{_fmt(r.val)} ({r.where})" for r in check.rows[:4]))
        return (
            f"FIGURES DISAGREE on {check.quantity}: statement {_fmt(check.figure or 0.0)} vs "
            f"companyfacts {seen} on {asof} — no row reproduces the statement at its printed "
            "precision; ratify against the passage"
        )
    if check.status == "no-row":
        return (
            f"companyfacts has no row on {asof} for {check.quantity} (us-gaap or ifrs-full, any "
            "currency): not cross-checked"
        )
    return f"the statement's {check.quantity} could not be read, so it was not cross-checked"


# ---------------------------------------------------------------------------------------------------------
# table and row choice (PR 4) — the consolidated statement over parent-only, subsidiary or segment tables
# ---------------------------------------------------------------------------------------------------------

_CHOSEN_BECAUSE = {
    "companyfacts": "companyfacts reproduces its figure",
    "heading": "the first with no parent-only, subsidiary or segment cue in its heading",
    "unverified": "every candidate table looks like a breakdown: TABLE UNVERIFIED",
}


def _readable_cash(row: _Row) -> bool:
    # a negative "cash" is a note's subtraction line; a flow line is never a balance
    return row.kind != "flow" and row.raw is not None and row.raw >= 0


def _on_statement(row: _Row) -> bool:
    """A balance-sheet STATEMENT row: a balance row in a table with a title heading and no breakdown cue."""
    return row.kind == "balance" and row.statement and row.cue is None


def _choose_cash(rows: list[_Row], cf_rows: list[_CfRow]) -> tuple[_Row, str]:
    """The cash row: (1) among the rows whose current-column figure companyfacts reproduces on the
    statement's date (any concept, taxonomy or unit), a balance-sheet STATEMENT row first, then any
    balance row, then a closing balance; else (2) among the tables whose heading window carries no
    breakdown cue, a readable balance-sheet statement row, then any readable balance row, then a
    readable closing balance (the cash-flow statement's end-of-period line), then the first located
    site (unreadable: ``cash-unread``); else (3) the first site at all, which the caller flags
    ``table-unverified``. A flow line is never valued (the 2026-11-16 guard)."""
    matched = [
        r
        for r in rows
        if _readable_cash(r)
        and _matching_rows(r.raw, r.decimals, _candidate_scales(r, None), cf_rows)
    ]
    if matched:
        for want in (_on_statement, lambda r: r.kind == "balance", lambda r: True):
            hit = next((r for r in matched if want(r)), None)
            if hit is not None:
                return hit, "companyfacts"
    clean = [r for r in rows if r.cue is None]
    if clean:
        readable = [r for r in clean if _readable_cash(r)]
        for want in (_on_statement, lambda r: r.kind == "balance", lambda r: r.kind == "closing"):
            hit = next((r for r in readable if want(r)), None)
            if hit is not None:
                return hit, "heading"
        return next((r for r in clean if r.kind != "closing"), clean[0]), "heading"
    return next((r for r in rows if r.kind != "closing"), rows[0]), "unverified"


def _choose_ocf(rows: list[_Row], cf_rows: list[_CfRow], *, after: int) -> tuple[_Row, str]:
    """The operating-cash-flow total, by the cash row's order, from the SAME statement set: among the
    qualifying sites the first after the chosen balance sheet (the statements run balance sheet ->
    cash flows), else the last (the old rule: the F-pages statement comes last)."""

    def same_set(cands: list[_Row]) -> _Row:
        readable = [r for r in cands if r.raw is not None] or cands
        return next((r for r in readable if r.start > after), readable[-1])

    hits = [
        r
        for r in rows
        if r.raw is not None
        and _matching_rows(r.raw, r.decimals, _candidate_scales(r, None), cf_rows)
    ]
    if hits:
        return same_set(hits), "companyfacts"
    clean = [r for r in rows if r.cue is None]
    if clean:
        return same_set(clean), "heading"
    return rows[-1], "unverified"


def _sti_rows(text: str, cash: _Row, report_year: int) -> list[_Row]:
    """Short-term deposits / investments rows NEAR the chosen cash row (D1): within ``_STI_SPAN`` of it,
    in its region, with no "Total current assets" / statement boundary between them, not restricted,
    long-term or a combined "cash ... and short-term ..." line, and immediately followed by a value. A
    marketable-securities row (D6) also needs no assets heading between it and the cash row.
    Whether such a row may be COUNTED is ``_sti_judgement``'s call (the 2026-11-16 guard)."""
    lo = max(cash.region, cash.start - _STI_SPAN)
    hi = min(cash.region + _REGION_SPAN, cash.start + _STI_SPAN)
    out: list[_Row] = []
    for m in _STI_LABEL_RE.finditer(text, lo, hi):
        a, b = sorted((m.start(), cash.start))
        if _STI_BOUNDARY_RE.search(text, a, b):
            continue
        if m.group("securities") and _ASSET_SECTION_RE.search(text, a, b):
            continue  # D6: marketable securities count only from the cash row's own (current) section
        if _STI_PRE_VETO_RE.search(text[max(0, m.start() - 40) : m.start()]):
            continue
        if not _VALUE_GATE_RE.search(text[m.end() : m.end() + 18]):
            continue
        out.append(_read_row(text, m.start(), m.end(), cash.region, report_year))
    return out


@dataclass(frozen=True)
class _StiJudgement:
    """Whether one deposits/investments row is counted: its current-year value (None = not counted), why
    not (None = counted, or a dash: nothing to count), and whether the current cell is a printed dash.
    """

    value: float | None
    reason: str | None
    dash: bool = False


def _sti_judgement(text: str, cash: _Row, row: _Row, report_year: int) -> _StiJudgement:
    """THE DEPOSITS / INVESTMENTS ADDITION IS ONLY EVER FROM THE SAME STATEMENT (the 2026-11-16 re-audit:
    QGEN, CGNT, WAVE). A row is counted only when (1) the cash row is a balance-sheet STATEMENT row (a
    title heading, no breakdown cue — never a note, an MD&A table or a summary, whose deposits may be a
    component already inside cash, a total or another year); (2) the row is its own line (not "of
    which", "including", "comprising", "consisting of", a total, or a line naming cash); (3) it reads
    the same column headers as the cash row; (4) its current-year cell holds a value — a dash is an
    empty cell (nothing to add), and a blank cell beside a note reference is never the note number.
    """
    if not _on_statement(cash):
        where = "a note or summary" if cash.kind == "balance" else "the cash-flow statement"
        if cash.cue is not None:
            where = "a table that looks like a breakdown"
        return _StiJudgement(
            None,
            f"the cash row was read from {where} (“{_heading_snippet(text, cash.region)}”), not the "
            "balance-sheet statement, so a deposits or investments row beside it may be a component "
            "already inside cash, a total or another year",
        )
    own = re.sub(r"\s+", " ", _own_row(text, row.start)).strip()
    if _STI_COMPONENT_RE.search(own):
        return _StiJudgement(
            None,
            f"it reads as a component, total or summary line (“{own} …”), not its own asset line",
        )
    if row.years != cash.years:
        return _StiJudgement(
            None,
            f"its column headers ({', '.join(map(str, row.years)) or 'none'}) do not match the cash "
            f"row's ({', '.join(map(str, cash.years))})",
        )
    cells = list(row.cells)
    years = row.years
    # the cash row's own figures (after its note reference): the table's real column grid, which the year
    # tokens may over-count (a heading's dates: ALC's "at December 31, 2025 and 2024 ... Note 2025 2024")
    cash_cells = list(cash.cells)
    if (
        cash_cells
        and cash_cells[0] is not None
        and 0 < cash_cells[0] < 100
        and cash_cells[0].is_integer()
        and len(cash_cells) > len(years) >= 1
    ):
        cash_cells = cash_cells[1:]  # the cash row's own note reference
    lead_ref = bool(cells) and cells[0] is not None and 0 < cells[0] < 100 and cells[0].is_integer()
    if lead_ref and len(cells) > len(years) >= 1:
        cells = cells[1:]  # the leading small integer is the row's note reference
    elif (
        lead_ref
        and row.note_col
        and len(cells) == len(cash_cells) + 1
        and cash.raw is not None
        and cash_cells.count(cash.raw) == 1
    ):
        # the third re-audit (ALC: "Time deposits 17 80 153"): the note reference, then one figure per
        # column of the cash row — read in the cash value's own column (a dash there is an empty cell)
        cell = cells[1:][cash_cells.index(cash.raw)]
        if cell is None:
            return _StiJudgement(None, None, dash=True)
        if cell >= 0:
            return _StiJudgement(cell, None)
        return _StiJudgement(None, "its current value could not be read")
    elif lead_ref and row.note_col and len(cells) <= len(years):
        return _StiJudgement(
            None,
            f"its current-year cell is blank: the leading “{_fmt(cells[0] or 0.0)}” is the row's note "
            "reference, not a value",
        )
    # the SAME currency column as the cash value (the third re-audit: VNET's row printed 379,198 and 54,225
    # under a header of two RMB years and a US$ convenience column, and the two figures were mapped onto
    # the two years, so the US$ 54,225 was counted as CNY). When the cash row prints more figures than
    # year headers (a convenience-translation column), a row that prints fewer than the cash row cannot be
    # placed on that grid: never counted.
    if len(cash_cells) > len(years) >= 1 and len(cells) < len(cash_cells):
        return _StiJudgement(
            None,
            f"it prints {len(cells)} figure(s) where the cash row prints {len(cash_cells)} (the "
            f"{', '.join(map(str, years))} columns and a convenience-translation column), so which figure "
            f"is the {report_year} column in the cash row's currency cannot be told; a convenience "
            "translation is never counted",
        )
    if years.count(report_year) == 1 and len(cells) == len(years):
        cell = cells[years.index(report_year)]
        if cell is None:
            return _StiJudgement(None, None, dash=True)
        if cell >= 0:
            return _StiJudgement(cell, None)
    if row.raw is None or row.raw < 0:
        return _StiJudgement(None, "its current value could not be read")
    return _StiJudgement(row.raw, None)


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
    filing_refs: Mapping[str, str] | None = None,
) -> tuple[list[ExtractedFact], str | None]:
    """Pure + deterministic: (companyfacts-or-None + the CLEANED annual filing text) -> the one
    cash_burn FLAG candidate, or ``([], runway_empty_reason)`` when no statement row can be located
    (FAIL CLOSED — no passage, no fact). ``report_date`` is the filing's PERIOD OF REPORT (what the
    statements are dated to); ``today`` only ages the staleness flag (time is a parameter).
    ``filing_refs`` (accession -> document URL, from the submissions index the caller already read)
    names the document of a later filing the candidate cites; without it, that filing's EDGAR index.
    """
    text = _ZERO_WIDTH_RE.sub("", annual_text)
    report_year = report_date.year

    ocf_site = _locate_ocf_row(text)
    all_cash_sites = _cash_sites(text)
    cash_site = all_cash_sites[0] if all_cash_sites else None

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

    def flag(name: str) -> None:
        if name not in flags:
            flags.append(name)

    # ---- every located row, read; then the table choice (PR 4) ---------------------------------------
    cf_cash_rows = _cf_rows(companyfacts, _CF_CASH_CONCEPTS, duration=False, end=report_date)
    cf_ocf_rows = _cf_rows(companyfacts, _CF_OCF_CONCEPTS, duration=True, end=report_date)
    cash_rows = [_read_row(text, s, e, r, report_year, k) for s, e, r, k in all_cash_sites]
    ocf_rows = [_read_row(text, s, e, r, report_year) for s, e, r in _ocf_sites(text)]
    cash, cash_how = _choose_cash(cash_rows, cf_cash_rows)
    # a closing balance sits AFTER its own statement's operating total: the statement set starts at its
    # heading
    ocf, ocf_how = _choose_ocf(
        ocf_rows, cf_ocf_rows, after=cash.start if cash.kind != "closing" else cash.region - 1
    )
    if "unverified" in (cash_how, ocf_how):
        flag("table-unverified")
    passed_over = [r for r in cash_rows + ocf_rows if r.cue and r is not cash and r is not ocf]
    table_note = (
        f"Table: cash from “{_heading_snippet(text, cash.region)}” ({_CHOSEN_BECAUSE[cash_how]}); "
        f"operating cash flow from “{_heading_snippet(text, ocf.region)}” "
        f"({_CHOSEN_BECAUSE[ocf_how]})"
    )
    if passed_over:
        table_note += (
            f"; passed over {len(passed_over)} row(s) in tables that look like a breakdown "
            f"(“{passed_over[0].cue}”)"
        )

    # ---- both statements' headers (scale / currency), with companion inheritance -------------------
    passages.append(_passage(text, "cash-flow", annual_ref, ocf.start, ocf.label))
    scale, scale_marker = ocf.scale, ocf.marker
    c_scale, c_marker = cash.scale, cash.marker
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

    years = ocf.years
    doc_ocf_raw, vals = ocf.raw, ocf.vals
    uniform_sign = _sign_of(vals)  # True = every column negative (burning), False = positive
    if ocf.col_flag:
        flag(ocf.col_flag)

    doc_ocf: float | None = None
    if doc_ocf_raw is not None:
        if scale is None:
            flag("unit-scale-unread")
            notes.append(_SCALE_UNREAD_NOTE)
            doc_ocf = doc_ocf_raw
        else:
            doc_ocf = doc_ocf_raw * scale

    # ---- the cash row's own figure (before any composition) -----------------------------------------
    passages.append(_passage(text, "balance-sheet", annual_ref, cash.start, cash.label))
    if cash.col_flag:
        flag(cash.col_flag)
    c_raw = cash.raw if _readable_cash(cash) else None
    cash_component: float | None = None
    if c_raw is not None:
        if c_scale is None:
            if "unit-scale-unread" not in flags:
                flag("unit-scale-unread")
                notes.append(_SCALE_UNREAD_NOTE)
            cash_component = c_raw
        else:
            cash_component = c_raw * c_scale

    # ---- the cross-check: exact at the printed precision, us-gaap + ifrs-full, every unit -------------
    ocf_check = _cross_check(
        "operating cash flow", doc_ocf_raw, ocf.decimals, scale or 1.0, doc_ocf, cf_ocf_rows
    )
    cash_check = _cross_check(
        "cash balance", c_raw, cash.decimals, c_scale or 1.0, cash_component, cf_cash_rows
    )

    # ---- the currency: companyfacts' reproducing unit, else the headers, else UNREAD -----------------
    ocf_cur = _header_currency(text, ocf.region, ocf.start, extra_columns=ocf.extra_columns)
    cash_cur = _header_currency(text, cash.region, cash.start, extra_columns=cash.extra_columns)
    if ocf_cur.code and cash_cur.code and ocf_cur.code != cash_cur.code:
        text_code: str | None = None
        text_basis = (
            f"the statements' headers disagree (cash flows {ocf_cur.code}: {ocf_cur.basis}; "
            f"balance sheet {cash_cur.code}: {cash_cur.basis})"
        )
    else:
        side = ocf_cur if ocf_cur.code else cash_cur
        if side.code is None and "bare" in cash_cur.basis and "bare" not in ocf_cur.basis:
            side = cash_cur
        elif side.code is None:
            side = ocf_cur
        text_code, text_basis = side.code, side.basis
        if text_code is None:
            # D (the 2026-11-16 guard): the filing's own presentation-currency statement, when the
            # headers name none — "presented in United States Dollars", "USD 000"; never a bare "$"
            stated = _presentation_currency(text)
            if stated is not None:
                text_code, text_basis = stated.code, f"{stated.basis}; {text_basis}"
    convenience = ocf_cur.convenience or cash_cur.convenience
    u_ocf, u_cash = ocf_check.units(), cash_check.units()
    cf_code: str | None = None
    cf_conflict = ""
    if u_ocf and u_cash:
        common = u_ocf & u_cash
        if len(common) == 1:
            cf_code = next(iter(common))
        elif not common:
            cf_conflict = (
                f"companyfacts reproduces the cash in {', '.join(sorted(u_cash))} but the operating "
                f"cash flow in {', '.join(sorted(u_ocf))}"
            )
    elif len(u_ocf | u_cash) == 1:
        cf_code = next(iter(u_ocf | u_cash))
    currency: str | None
    if cf_code is not None:
        currency = cf_code
        which = " and ".join(
            q for q, units in (("cash", u_cash), ("operating cash flow", u_ocf)) if cf_code in units
        )
        currency_note = (
            f"Currency: {cf_code}, established by companyfacts (its {cf_code} unit reproduces the "
            f"statement's {which} exactly)"
        )
        if text_code is not None and text_code != cf_code:
            flag("currency-mismatch")
            currency_note += (
                f"; CURRENCY MISMATCH: the header reads {text_code} ({text_basis}) — confirm the "
                "statements' currency from the passage"
            )
        elif text_code == cf_code:
            currency_note += f"; the header agrees ({text_basis})"
        else:
            currency_note += f"; the header alone could not settle it ({text_basis})"
    else:
        currency = text_code
        unsettled = cf_conflict or "companyfacts did not settle it"
        if currency is None:
            flag("currency-unread")
            currency_note = (
                f"Currency: UNREAD — {text_basis}, and {unsettled}; confirm the currency from the "
                "passage before ratifying"
            )
        else:
            currency_note = f"Currency: {currency} ({text_basis}); {unsettled}"
        if convenience:
            flag("convenience-column")
            currency_note += (
                " (CONVENIENCE COLUMN: a translated US$ column is printed beside the figure — "
                "confirm the figure is the home-currency column)"
            )

    # span: an exact companyfacts row match (filed dates) > the statement's own period phrase > a
    # bare fiscal-year column on an annual form (annual by form semantics)
    doc_span: int | None = None
    span_basis = ""
    if doc_ocf is not None:
        spans = [
            r
            for r in ocf_check.matched
            if r.start is not None and (currency is None or r.unit == currency)
        ]
        if spans:
            best = max(spans, key=lambda r: (r.end - (r.start or r.end)).days)
            assert best.start is not None
            doc_span = max((best.end - best.start).days, 1)
            span_basis = f"companyfacts-matched period {best.start} → {best.end}"
        else:
            phrase = _period_span_days(text, ocf.region, ocf.start)
            if phrase is not None:
                doc_span = phrase
                span_basis = f"the statement's period phrase (~{phrase} days)"
            elif years:
                doc_span = 365
                span_basis = "a fiscal-year column on an annual form (annual by form)"

    # ---- later-as-of between the statement value and companyfacts (per quantity) -------------------
    # A fresher companyfacts reading is used only in the statement's established currency (a mixed-
    # currency ratio is unrepresentable); with the currency unread it is named, never used.
    cf_ocf_any = _latest_duration(
        _cf_rows(companyfacts, _CF_OCF_PRIMARY, duration=True, unit=currency)
    )
    cf_ocf = cf_ocf_any if currency is not None else None
    ocf_value: float | None = None
    ocf_span: int | None = None
    ocf_asof: date | None = None
    ocf_src = ""
    ocf_row: _CfRow | None = None  # the companyfacts row behind the offered burn, if any
    if doc_ocf is not None and doc_span is not None:
        ocf_value, ocf_span, ocf_asof, ocf_src = doc_ocf, doc_span, report_date, "statement"
    if (
        cf_ocf is not None
        and cf_ocf.start is not None
        and (ocf_asof is None or cf_ocf.end > ocf_asof)
    ):
        ocf_row = cf_ocf
        derived = _derived_quarter(companyfacts, cf_ocf, cfg)
        if derived is not None:
            prior, quarter, days = derived
            ocf_value, ocf_span, ocf_asof = quarter, max(days, 1), cf_ocf.end
            ocf_src = (
                f"companyfacts ({cf_ocf.unit}, a quarter derived from its year-to-date rows: "
                f"{_fmt(cf_ocf.val)} - {_fmt(prior.val)} ({cf_ocf.start} → {cf_ocf.end} less "
                f"{prior.start} → {prior.end}, from {_filed_as(prior)}) — fresher than the statement)"
            )
        else:
            ocf_value, ocf_span, ocf_asof = (
                cf_ocf.val,
                max((cf_ocf.end - cf_ocf.start).days, 1),
                cf_ocf.end,
            )
            ocf_src = (
                f"companyfacts ({cf_ocf.unit}, {cf_ocf.start} → {cf_ocf.end} — fresher than the "
                "statement)"
            )
        if doc_ocf is not None:
            notes.append(
                f"the statement's own figure {_fmt(doc_ocf)} (as of {report_date}) is OLDER and was "
                "not used"
            )
    elif cf_ocf_any is not None and doc_ocf is not None and cf_ocf_any.end < report_date:
        notes.append(
            f"companyfacts still serves {_fmt(cf_ocf_any.val)} ({cf_ocf_any.unit}) for "
            f"{cf_ocf_any.start} → {cf_ocf_any.end} — behind the filing (annual XBRL lags); the "
            "statement wins"
        )
    elif cf_ocf_any is not None and currency is None and cf_ocf_any.end > report_date:
        notes.append(
            f"companyfacts carries a newer operating cash flow ({_fmt(cf_ocf_any.val)} "
            f"{cf_ocf_any.unit}, ending {cf_ocf_any.end}) but the statement's currency is unread, so "
            "it was not used"
        )
    if ocf_value is None and doc_ocf_raw is None and uniform_sign is None:
        # a located row whose value AND sign are unreadable — unread, not empty
        return [], "statements-not-located"

    # ---- the cash side: the cash row plus short-term deposits and investments (D1) -----------------
    cash_value: float | None = None
    cash_asof: date | None = None
    cash_src = ""
    investments: float | None = None
    composition: list[str] = []
    sti_checks: list[_Check] = []
    sti_passages: list[LocatedPassage] = []
    cf_sti_rows = _cf_rows(companyfacts, _CF_STI_CONCEPTS, duration=False, end=report_date)
    if cash_component is not None:
        cash_value, cash_asof, cash_src = cash_component, report_date, "statement"
        if c_marker and c_marker != scale_marker:
            notes.append(f"balance-sheet scale marker: “{c_marker}”")
        if cash.kind == "closing":
            closing_label = re.sub(r"\s+", " ", cash.label)
            notes.append(
                f"cash is the cash-flow statement's closing balance (“{closing_label}”): no readable "
                "balance-sheet cash row was located"
            )
        parts_sti: list[float] = []
        quoted_sti = False  # a deposits/investments row was seen and quoted, not counted
        counted: list[tuple[_Row, str, float, float]] = (
            []
        )  # (row, label, printed value, scaled part)
        for row in _sti_rows(text, cash, report_year):
            label = re.sub(r"\s+", " ", row.label)
            verdict = _sti_judgement(text, cash, row, report_year)
            if verdict.dash:
                notes.append(
                    f"a “{label}” row reads a dash in the {report_year} column: nothing is added for it"
                )
                continue
            if verdict.value is None:
                quoted_sti = True
                flag("investments-unconfirmed")
                quote = re.sub(r"\s+", " ", text[row.start : row.label_end + 40]).strip()
                notes.append(
                    f"INVESTMENTS UNCONFIRMED: a “{label}” row was seen (“{quote} …”) but NOT counted — "
                    f"{verdict.reason}; author it from the filing if it is a separate current asset"
                )
                continue
            part = verdict.value * (c_scale or 1.0)
            parts_sti.append(part)
            composition.append(
                f"{label.lower()} {_fmt(part)} (from the balance sheet's “{label}” row)"
            )
            sti_passages.append(_passage(text, "balance-sheet", annual_ref, row.start, row.label))
            counted.append((row, label, verdict.value, part))
        for row, label, printed, part in counted:
            # E (the 2026-11-16 guard): compare within the row's concept family, and never with a concept
            # that reproduces ANOTHER counted row of the same statement (NTES: its "Short-term
            # investments" row owns ShortTermInvestments; the time deposits are not that line)
            claimed: dict[tuple[str, str], str] = {
                (r.taxonomy, r.concept): o_label
                for other, o_label, o_printed, _ in counted
                if other is not row
                for r in _matching_rows(o_printed, other.decimals, [c_scale or 1.0], cf_sti_rows)
            }
            rows_for = [
                r for r in _sti_family(label, cf_sti_rows) if (r.taxonomy, r.concept) not in claimed
            ]
            for (taxonomy, concept), o_label in claimed.items():
                notes.append(
                    f"companyfacts' {taxonomy} {concept} reproduces the statement's “{o_label}” row, so "
                    f"it was not compared with “{label}”"
                )
            sti_checks.append(
                _in_statement_currency(
                    _cross_check(
                        f"“{label}”", printed, row.decimals, c_scale or 1.0, part, rows_for
                    ),
                    currency,
                )
            )
        if parts_sti:
            investments = sum(parts_sti)
            cash_value = cash_component + investments
        elif cf_sti_rows:
            seen = "; ".join(dict.fromkeys(f"{_fmt(r.val)} ({r.where})" for r in cf_sti_rows[:3]))
            where = (
                "the balance-sheet row quoted above was not counted"
                if quoted_sti
                else "no balance-sheet row for them was located"
            )
            notes.append(
                f"companyfacts carries short-term deposits or investments on {report_date} ({seen}) "
                f"but {where}; they are NOT counted — check the balance sheet"
            )
    cf_cash = (
        _latest_instant(_cf_rows(companyfacts, _CF_CASH_PRIMARY, duration=False, unit=currency))
        if currency is not None
        else None
    )
    # companyfacts may WIN the cash because the statement row IS located (the passage the value rides
    # with — the shares CMND precedent); the note states the losing side.
    cash_row: _CfRow | None = None  # the companyfacts row behind the offered cash, if any
    if cf_cash is not None and (cash_asof is None or cf_cash.end > cash_asof):
        cash_row = cf_cash
        if cash_value is not None:
            notes.append(
                f"cash: the statement's {_fmt(cash_value)} (as of {cash_asof}) is OLDER and was not used"
            )
        cash_value, cash_asof = cf_cash.val, cf_cash.end
        cash_src = f"companyfacts ({cf_cash.unit} — fresher than the statement)"
        investments, composition = None, []
        sti_at = _cf_rows(
            companyfacts, _CF_STI_CONCEPTS, duration=False, end=cf_cash.end, unit=currency
        )
        if sti_at:
            # several investment tags often restate one another: the largest same-dated one, never a sum
            biggest = max(sti_at, key=lambda r: r.val)
            investments = biggest.val
            cash_value += biggest.val
            composition = [f"short-term investments {_fmt(biggest.val)} ({biggest.where})"]
    if cash_src == "statement":
        passages.extend(sti_passages)  # each counted deposits row rides as its own passage
    if cash_value is None and not cash.col_flag:
        flag("cash-unread")
        if cash.kind == "flow":
            flow_row = re.sub(r"\s+", " ", _own_row(text, cash.start, 60) + cash.label).strip()
            notes.append(
                f"CASH UNREAD: the only cash row located is a flow line (“{flow_row}”), never a "
                "balance; author the cash from the balance sheet"
            )
        else:
            notes.append(
                "CASH UNREAD: the balance-sheet cash row was located but its current-column value could "
                "not be read (missing or negative); author the cash from the passage"
            )

    # THE CITATION (W1-3, ARM): when every figure offered comes from companyfacts rows of ONE other filing
    # than the annual document (a later 6-K interim), the candidate cites that filing, never the annual
    # document whose statements are older; the passages stay the annual statement rows located (what the
    # filing itself prints).
    source_ref = annual_ref
    rows_used = [r for r in (ocf_row, cash_row) if r is not None]
    offered_from_cf = (ocf_value is None or ocf_row is not None) and (
        cash_value is None or cash_row is not None
    )
    accessions = {r.accn.replace("-", "") for r in rows_used}
    own = re.search(r"/data/\d+/(\d{18})/", annual_ref)
    if (
        rows_used
        and offered_from_cf
        and len(accessions) == 1
        and rows_used[0].accn
        and (own is None or own.group(1) not in accessions)
    ):
        accn = rows_used[0].accn
        cited = (filing_refs or {}).get(accn) or filing_index_url(
            str((companyfacts or {}).get("cik") or ""), accn
        )
        if cited:
            source_ref = cited
            notes.append(
                f"Cited filing: {_filed_as(rows_used[0])}, which carries these companyfacts figures; the "
                f"passages are the {annual_form}'s older statement rows"
            )

    checks = [ocf_check, cash_check, *sti_checks]
    if any(c.status == "disagrees" for c in checks):
        flag("figures-disagree")
    cross_note = "Cross-check: " + "; ".join(_check_sentence(c, report_date) for c in checks)

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
        flag("stale-runway")

    parts: list[str] = [f"{annual_form} financial statements ({currency or 'currency unread'}"]
    if scale_marker:
        parts[-1] += f", “{scale_marker}”"
    parts[-1] += ")."
    parts.append(currency_note + ".")
    parts.append(table_note + ".")
    if ocf_value is not None:
        parts.append(
            f"Operating cash flow {_fmt(ocf_value)} over {ocf_span} days "
            f"(ending {ocf_asof}; {ocf_src or 'statement'}"
            + (f"; span: {span_basis}" if ocf_src == "statement" and span_basis else "")
            + ")."
        )
    if cash_value is not None:
        cash_sentence = f"Cash {_fmt(cash_value)} as of {cash_asof} ({cash_src or 'statement'})"
        if composition and investments is not None:
            base = cash_value - investments
            cash_sentence += (
                f" = cash and cash equivalents {_fmt(base)} + "
                + " + ".join(composition)
                + " (short-term deposits and investments on the same balance sheet, in the same year "
                "column, count as cash)"
            )
        parts.append(cash_sentence + ".")
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
    parts.append(cross_note + ".")
    parts.extend(f"{n}." if not n.endswith(".") else n for n in notes)
    if len(flags) > 1:
        parts.append("FLAGS: " + ", ".join(flags[1:]) + " — confirm against the passages.")

    return [
        ExtractedFact(
            fact_type="cash_burn",
            tier=Tier.FLAG,  # ALWAYS — an annual statement's composition is the operator's to ratify
            cash_usd=cash_value,
            quarterly_burn_usd=burn,
            # the statement currency as established above (companyfacts' reproducing unit, else the
            # headers), carried DISPLAY-ONLY so the FE labels cash/burn in the filer's own currency —
            # never converted, never a scoring input; None = unread (flagged ``currency-unread``)
            statement_currency=currency,
            # the short-term deposits / investments part already inside cash_usd (D1)
            investments_usd=investments,
            source="annual-statements",
            source_ref=source_ref,
            event_date=age_ref,
            flags=flags,
            located_passages=passages,
            note=" ".join(parts),
        )
    ], None


# ---------------------------------------------------------------------------------------------------------
# the live wrapper — ONE document fetch shared by shares + runway
# ---------------------------------------------------------------------------------------------------------


def _filing_refs(subs: dict[str, Any], cik: int) -> dict[str, str]:
    """accession -> document URL for every recent filing in the submissions index already read (no request):
    the citation of a later filing whose companyfacts rows a candidate offers (ARM's 6-K)."""
    recent = subs.get("filings", {}).get("recent", {})
    return {
        accn: _doc_url(cik, accn, doc)
        for accn, doc in zip(recent.get("accessionNumber", []), recent.get("primaryDocument", []))
        if accn and doc
    }


def annual_facts_for_security(
    client: EdgarClient,
    cik: str | int,
    *,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    today: date | None = None,
) -> ExtractionResult:
    """Live (cache-first) wrapper for a DARK name: CIK -> the latest 20-F/40-F, fetched ONCE -> the
    annual-cover shares candidate (``annual_shares.extract_annual_shares``, given the raw text too for
    its inline cover — cover shares ALWAYS reads the primary document, never routed through the seam) PLUS the
    annual-statements cash/runway candidate, or their honest, DISTINCT empty reasons.

    The statements TEXT is resolved through the statement-source seam (Slice A-2): the primary
    document first (the SAME text the cover just read — no second fetch), else the EX-99 exhibit
    hunt (bounded, content-signature, fail-closed). The extraction then runs UNCHANGED on whatever
    text won, with ``annual_ref`` = the winning document's URL, so a CRDL runway passage cites the
    statements exhibit, not the 40-F wrapper. When NOTHING resolves, the extractor still reads the
    primary text for the honest tri-state (the sign logic above); an ambiguous exhibit scan defers
    with its own distinct reason.

    ``empty_reason`` keeps its Slice-1 semantics (set only when NO facts at all):
    ``no-annual-filing`` · ``cover-not-located``. ``runway_empty_reason`` is the runway leg's own
    state whenever an annual filing exists but no cash_burn fact could be emitted:
    ``cash-generative`` · ``financials-in-exhibit`` · ``exhibit-ambiguous`` ·
    ``statements-not-located`` (see the module docstring). An EXPLICIT operator action via the
    extract endpoint, never fired on a render.
    """
    # Lazy import (the extract.py idiom): statement_sources imports this module's locators, so a
    # module-level import here would be a cycle.
    from ingest.edgar.statement_sources import (
        AnnualFiling,
        ResolvedStatements,
        StatementsDeferred,
        resolve_statements,
    )

    cik = int(cik)
    subs = fetch_submissions(client, cik)
    filing = _latest_annual_filing(client, cik, subs=subs)
    if filing is None:
        return ExtractionResult(facts=[], empty_reason="no-annual-filing")
    url, raw, text, report_dt, form = filing
    forms = subs.get("filings", {}).get("recent", {}).get("form", [])
    has_f6 = any(str(f).startswith("F-6") for f in forms)
    cf = _companyfacts_or_none(client, cik)
    when = today or market_today()
    shares = extract_annual_shares(
        cf,
        text,
        annual_ref=url,
        annual_form=form,
        report_date=report_dt,
        today=when,
        has_f6_filing=has_f6,
        cfg=cfg,
        raw=raw,
    )
    # the accession of the row _latest_annual_filing selected: the newest filing of the WINNING form
    # (same submissions list, same first-row pick — no second selection logic to drift)
    accession = filings_of(subs, form)[0]["accession"]
    resolved = resolve_statements(
        client,
        AnnualFiling(
            cik=cik,
            accession=accession,
            primary_url=url,
            primary_text=text,
            report_date=report_dt,
            form=form,
        ),
        cfg=cfg,
    )
    if isinstance(resolved, StatementsDeferred):
        runway: list[ExtractedFact] = []
        runway_reason: str | None = resolved.reason
    else:
        # a ResolvedStatements extracts from the WINNING text (primary or exhibit — the ref follows);
        # None falls back to the primary text, whose rowless extraction yields the honest tri-state
        # (cash-generative / financials-in-exhibit / statements-not-located).
        stmt_text = resolved.text if isinstance(resolved, ResolvedStatements) else text
        stmt_ref = resolved.source_ref if isinstance(resolved, ResolvedStatements) else url
        runway, runway_reason = extract_annual_runway(
            cf,
            stmt_text,
            annual_ref=stmt_ref,
            annual_form=form,
            report_date=report_dt,
            today=when,
            cfg=cfg,
            filing_refs=_filing_refs(subs, cik),
        )
    facts = shares + runway
    if not facts:
        return ExtractionResult(
            facts=[], empty_reason="cover-not-located", runway_empty_reason=runway_reason
        )
    return ExtractionResult(facts=facts, runway_empty_reason=runway_reason)


__all__ = ["extract_annual_runway", "annual_facts_for_security"]
