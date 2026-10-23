"""The SEC split extractor (split plan S1) — a filing's split RATIO, its first session at the new price, and
the LOCATED passage that states them. Pure and deterministic: fixed patterns over the filing's own words, no
model anywhere near it (invariant #3). The extractor LOCATES; the matcher (``domain/splits.py``) and the
operator decide what the split list holds.

**Input.** A filing as EDGAR serves it: the full-submission ``.txt`` (``submission_documents`` splits it on
``<DOCUMENT>`` and reads each ``<TYPE>``), or a single HTML document. Only the documents that announce a
split are read (``relevant_documents``): the form's own document (8-K / 6-K / 1-A — a 1-A's offering circular
is typed ``PART II AND III``) and its press-release exhibits (``EX-99.x``). Charter exhibits (EX-3.x) and the
graphics / XBRL blobs are skipped — a certificate of amendment restates the ratio at best, and at worst
carries an EARLIER amendment's.

**Which filings** is the caller's (``pipeline/backfill_splits.py``): 8-Ks filed under Item 3.03 / 5.03 (the
charter amendment) and — since 2026-10-23 — Item 8.01 / 7.01 (a split announced with no charter amendment: one
paid as a stock dividend, a trust's share split, a board-set ratio — ENHA, SNEX, ZCSH, ETH, BTC, OFAL), 6-Ks
and Reg A forms. An 8-K read only for its 8.01 / 7.01 is a CATCH-ALL filing — MEASURED on the basket 2026-10-23,
47 of 852 mention a split ratio at all — so it is read with its filing date (``extract_split(filed=...)``,
below).

**The ratio** is read from five phrasings, every one MEASURED on the real filings in
``tests/fixtures/splits``:

- ``N-for-M`` / ``N for M`` in digits or words, with an optional parenthesized digit restatement —
  "1-for-16", "one-for-sixteen (1-for-16)", "one (1) for nine (9)", "twenty five (25) -for- one (1)",
  "15 for 1 Share Consolidation", "1-for-6.25";
- ``N:M`` when it sits next to the word "ratio" or a split word — "fixed the split ratio at 8:1", "a 1:300
  reverse stock split" (a clock time such as "5:00 p.m." never qualifies);
- ``N share(s) for every M`` — "one (1) share for every sixteen (16)", "1 post-split ordinary share for every
  20 pre-split ordinary shares", and the other way round, "four (4) pre-Reverse Split shares for every one (1)
  post-Reverse Split share" (ELBM): the words beside each number (pre / old / outstanding vs post / new) say
  which side is old. The words between the two numbers are never a number themselves ("… September 28, 2026
  received two additional shares for every one" reads "two … for every one", ZCSH);
- ``A additional share(s) … for every B`` — a split paid as extra shares, B old shares becoming A + B: "one
  additional share of common stock for every two shares owned" (SNEX's three-for-two, 2 -> 3), "two additional
  shares for every one pre-split share" (ZCSH's 3-for-1), "nine (9) additional shares … for every" one (KLA);
- ``every/each M … combined/consolidated/converted … into N`` — "every fifteen (15) … consolidated into one
  (1)", "each nine (9) pre-split shares … combine into one (1) new share", "every twenty-eight and one half …
  combined into one" (SMX; a word number may end "and one half").

The last two state the direction themselves. The ``N-for-M`` and ``N:M`` forms do not —
filings write a 1-for-16 reverse split as "1-for-16" and a 25-for-1 consolidation as "25-for-1" — so they (and a
"for every" with no pre/post words) are oriented by the filing's own words: "reverse … split" / "consolidation"
/ "combined" make it a reverse split (old = the larger number), "forward … split" / "stock dividend" /
"subdivision" a forward one. A ratio is only ever read inside a sentence that talks about a split (a "business
combination" is a merger, not shares combined), and "1-for-1" (a share exchange) is not a split. An ADS RATIO
CHANGE counts as a split of the ADS — "the same effect
as a one-for-thirty reverse ADS split" (NIPG) — because it moves the ADS price exactly like one, and Polygon
lists it as one.

**What does NOT count as the split's ratio** — the refinement of "more than one distinct ratio abstains" that
the real filings forced (named so the rule stays checkable):

- an AUTHORIZED RANGE, ceiling or illustration: a ratio within ``_RANGE_LOOKBACK`` characters after
  "range", "between", "up to", "not less than", "exceeds", "assuming" … — the proxy language ("at a ratio of
  up to one-for-two hundred (1:200)", "select a ratio of between 1-for-5 and 1-for-50", "shall not effect …
  splits that, in the aggregate, exceeds 1-for-12"), and an "every N TO M … into one" span ("every two (2) to
  two hundred and fifty (250) Ordinary Shares shall be converted into one (1)", MTEK). A filing that states
  ONLY a range abstains: no ratio was set.
- a PAST split: a ratio whose sentence's every date is more than ``HISTORICAL_GAP_DAYS`` before the filing's
  own effective date (PBM's 6-K restates "a 1-for-7.97 consolidation ratio which was implemented Monday, May 5,
  2025" beside its 1-for-6.25) — or, for a CATCH-ALL filing, before its FILING date: ASST's 8.01 8-K of
  2026-10-05 restates "the 1-for-20 reverse stock split effected on February 6, 2026", and NAKA's and CURLD's
  restate theirs (MEASURED 2026-10-23: four such 8.01 / 7.01 readings on the basket, 123 to 266 days back).

After those, **exactly one** distinct ratio must remain, or the extractor ABSTAINS and says why: none found,
only a range, only a past split, two different ratios, a word and its digit restatement disagreeing, or no
direction stated. It never picks between two values.

**The date** is the first session at the new price. A TRADING sentence (one about trading, carrying a cue
such as "split-adjusted basis", "post-consolidation basis", "market open", "opening of trading",
"commencement of trading" — or "will begin trading" in a sentence about the split, "the first date when the
… shares will begin trading … after implementation of the reverse split will be Monday, October 5, 2026",
CMND) states it directly — kind ``trading``. Only when no trading sentence carries a date does a LEGAL sentence
about the split ("effective as of", "Effective June 12, 2025", "will take effect on") supply one — kind
``legal``, which may be the session before the first one (a 5:00 p.m. effective time). Two different dates of
the winning kind → no date (named in ``date_note``); the ratio still stands. A filing that states both reads
its trading date: ENHA's 8.01 8-K says "effective at 12:01 a.m. Central Time on October 29, 2026" AND "expected
to begin trading on a split-adjusted basis … at the opening of trading on October 29, 2026" — ``trading``. For a
CATCH-ALL filing a date more than ``HISTORICAL_GAP_DAYS`` before its filing date dates nothing (AIRE's 7.01
8-K/A of 2026-09-23 reaches only 2025-12-31, in a tax table): no date, named in ``date_note``.

**The passage** is the filing's own sentence(s), verbatim after the HTML is stripped (tags removed, entities
decoded, whitespace collapsed): the sentence that states the ratio and, when it is another one, the sentence
that states the date. A run-on sentence (a 6-K cover page has no full stop) is cut to a verbatim window around
what it states, marked "[…]".
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction

# a ratio's sentence must have this much of a split in it, or the "N for M" is something else
# (an ADS RATIO CHANGE is a split of the ADS — "the same effect as a one-for-thirty reverse ADS split" (NIPG),
# and Polygon lists it as one). A "business combination" is a merger, not shares combined: "SAFE investors will
# receive one warrant for every two shares acquired, … if the business combination is consummated" (ENHA's
# Item 8.01 8-K of 2026-08-26) states no split.
_SPLIT_CUE = re.compile(
    r"revers|split|consolidat|subdivi|(?<!business\s)combin|reclassif|ratio\s+change|ADS\s+ratio",
    re.I,
)
_REVERSE_CUE = re.compile(
    r"reverse\s+(?:[\w-]+\s+)?split|reverse-split|share\s+consolidation|consolidat|combined\s+into|"
    r"combine\s+into|reclassified\s+into",
    re.I,
)
_FORWARD_CUE = re.compile(
    r"forward\s+(?:[\w-]+\s+)?split|stock\s+dividend|share\s+subdivision|subdivi", re.I
)

# a ratio this close after one of these words is an authorized RANGE / ceiling ("shall not effect … splits
# that, in the aggregate, exceeds 1-for-12", ASNS) — or a HYPOTHETICAL ("assuming an Approved Consolidation
# Ratio of 1:200", MI's proxy) — not the ratio set
_RANGE_LOOKBACK = 80
_RANGE_CUE = re.compile(
    r"\b(?:range|ranging|between|up\s+to|not\s+(?:less|more|greater|fewer)\s+than|"
    r"no\s+(?:less|more|greater|fewer)\s+than|maximum|minimum|any\s+whole\s+number|at\s+least|"
    r"as\s+(?:low|high)\s+as|exceed(?:s|ed|ing)?|assum(?:e|es|ed|ing)|hypothetical(?:ly)?|illustrat\w*|"
    r"for\s+example)\b",
    re.I,
)

# a ratio whose sentence's every date is this far before the filing's effective date is a PAST split
HISTORICAL_GAP_DAYS = 90

_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"thousand)"
)
# "and" joins only after hundred/thousand ("two hundred and fifty") — so "one-for-five and one-for-fifty"
# never reads "five and one" as a single number
_WORDNUM = rf"{_WORD}(?:[\s-]+{_WORD}|(?:(?<=hundred)|(?<=thousand))\s+and\s+{_WORD})*"
_DIGNUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"


# "twenty-eight and one-half (28.5)" — SMX's 2025 consolidation
_HALF = r"\s+and\s+(?:one|a)[\s-]+half"


def _num(name: str) -> str:
    """One number: words (with an optional "and one half") and an optional ``(digits)`` restatement, or
    digits."""
    return (
        rf"(?:(?P<{name}w>{_WORDNUM}(?:{_HALF})?)(?:\s*\(\s*(?P<{name}p>{_DIGNUM})\s*\))?|"
        rf"(?P<{name}d>{_DIGNUM}))"
    )


# "1-for-16", "one-for-sixteen", "one (1) for nine (9)", "twenty five (25) -for- one (1)", "15 for 1"
_FOR = re.compile(
    rf"(?<![\w$.,]){_num('a')}\s*(?P<sep>-\s*for\s*-|-\s*for\s+|\s+for\s*-|\s+for\s+)\s*{_num('b')}"
    r"(?![\w]|[.,]\d)",
    re.I,
)
# "8:1", "1:300" — only beside "ratio" or a split word (never a clock time)
_COLON = re.compile(
    r"(?<![\w$.:])(?P<ad>\d+(?:\.\d+)?)\s*:\s*(?P<bd>\d+(?:\.\d+)?)(?![\w:]|\.\d)(?!\s*[ap]\.?\s*m\b)",
    re.I,
)
_COLON_AFTER = re.compile(
    r"\s*\)?\s*(?:reverse|forward|stock\s+split|share\s+split|split|share\s+consolidation|consolidation|"
    r"basis)",
    re.I,
)
# "one (1) share for every sixteen (16)" — NEW shares first, then OLD. The words between the two numbers are
# never a number themselves: "… as of market close on September 28, 2026 received two additional shares for
# every one pre-split share" (ZCSH) reads "two … for every one", never "2026 … for every one".
_FOR_EVERY = re.compile(
    rf"(?<![\w$.,]){_num('a')}\s+(?:(?!{_WORD}\b)[A-Za-z][\w'-]*\s+){{0,6}}?for\s+(?:each|every)\s+"
    rf"{_num('b')}(?![\w]|[.,]\d)",
    re.I,
)
# "one additional share of common stock for every two shares owned" (SNEX's three-for-two, paid as a stock
# dividend), "two additional shares for every one pre-split share" (ZCSH's 3-for-1): the shares a holder
# RECEIVES on top of the ones held — B old shares become A + B
_ADDITIONAL = re.compile(r"\badditional\b", re.I)
# "every fifteen (15) ... consolidated into one (1)" — OLD shares first, then NEW
_EVERY_INTO = re.compile(
    rf"\b(?:each|every)\s+{_num('a')}(?![\w]|[.,]\d)(?P<fill>[^;]{{0,250}}?)\b(?:into|become|becomes)\s+"
    rf"(?:and\s+(?:\w+\s+)?become\s+)?{_num('b')}(?![\w]|[.,]\d)",
    re.I,
)
_COMBINE_VERB = re.compile(r"combin|consolidat|reclassif|convert|chang|exchang|become", re.I)
# "every two (2) TO two hundred and fifty (250) … into one (1)": the "every" number opens a RANGE
_EVERY_RANGE = re.compile(rf"\s*(?:to|through|-)\s+(?:{_DIGNUM}|{_WORD})", re.I)

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)"
)
_DATE = re.compile(
    rf"\b(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y1>(?:19|20)\d\d)\b|"
    rf"\b(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m2>{_MONTH}),?\s+(?P<y2>(?:19|20)\d\d)\b|"
    r"\b(?P<y3>(?:19|20)\d\d)-(?P<m3>\d\d)-(?P<d3>\d\d)\b",
    re.I,
)

_TRADING_WORD = re.compile(r"\btrad(?:e|es|ed|ing)\b", re.I)
_TRADING_CUE = re.compile(
    r"split[\s-]*adjusted|adjusted\s+basis|post[\s-]+(?:reverse[\s-]+)?(?:stock[\s-]+)?split|"
    r"post[\s-]+consolidat\w*|consolidated\s+basis|split\s+basis|market\s+opens?\b|markets\s+open|"
    r"market\s+opening|opening\s+of\s+(?:the\s+)?(?:market|trading|business)|"
    r"open\s+of\s+(?:the\s+)?(?:market|business|trading)|commencement\s+of\s+trading",
    re.I,
)
# "the first date when the Company's ordinary shares will begin trading … after implementation of the reverse
# split will be Monday, October 5, 2026" (CMND, PLRZ) — a start-of-trading phrase, only in a sentence about a split
_TRADING_START = re.compile(r"\b(?:begin|commence|start)s?\s+(?:to\s+)?trad(?:e|ing)\b", re.I)
# "effective as of 5:00 p.m. … on …", "will take effect on …", "Effective June 12, 2025, … filed …" (ERNA)
_LEGAL_CUE = re.compile(
    r"\beffective\b|takes?\s+effect|took\s+effect|effected\b[^.;]{0,120}?\bon\b",
    re.I,
)

# sentence ends that are NOT sentence ends: clock times, corporate suffixes, initials, the usual abbreviations
_ABBREV = re.compile(
    r"(?:\b[ap]\.m|\bInc|\bCorp|\bLtd|\bCo|\bNo|\bNos|\bU\.S|\bU\.K|\bMr|\bMs|\bMrs|\bDr|\bSt|\bJr|\bSr|\bvs|"
    r"\be\.g|\bi\.e|\bapprox|\bExh|\bInt|\bPlc|\bS\.A|\bN\.V|\bL\.P|\bL\.L\.C|\b[A-Z])$"
)
_SENTENCE_END = re.compile(r"[.;!?](?=\s+[\"“(A-Z0-9])")

# a sentence longer than this (a 6-K cover page runs on with no full stop) is shown as a verbatim WINDOW
# around what it states, marked […]
_LONG_SENTENCE = 500
_WINDOW_BEFORE = 220
_WINDOW_AFTER = 280

# the document types a split is announced in (the form's own document, a 1-A's offering circular, press
# releases); everything else in a submission is skipped
_KEEP_TYPES = ("8-K", "6-K", "1-A", "1-U", "PART II", "EX-99")

# 1:1 character map for MATCHING only (same length, so every position maps back to the verbatim text)
_MATCH_MAP = str.maketrans(
    {
        0x2010: "-",  # hyphen
        0x2011: "-",  # non-breaking hyphen
        0x2012: "-",  # figure dash
        0x2013: "-",  # en dash
        0x2014: "-",  # em dash
        0x2212: "-",  # minus sign
        0x00A0: " ",  # no-break space
    }
)


@dataclass(frozen=True)
class FilingDocument:
    """One document of a filing: its EDGAR ``<TYPE>``, file name and raw (usually HTML) text."""

    type: str
    filename: str
    raw: str


@dataclass(frozen=True)
class SplitExtraction:
    """What one filing states about a split.

    ``ratio_from`` -> ``ratio_to`` is OLD shares -> NEW shares (16 -> 1 for a 1-for-16 reverse split; 1 -> 2
    for a 2-for-1). ``None`` with ``abstain_reason`` when no single ratio is stated. ``effective_date`` is the
    first session at the new price (``date_kind`` = ``trading``) or a legal effective date (``legal``);
    ``None`` with ``date_note`` when none, or more than one, is stated. ``passage`` is verbatim; ``document``
    names the document it came from."""

    ratio_from: Decimal | None = None
    ratio_to: Decimal | None = None
    effective_date: date | None = None
    date_kind: str | None = None
    passage: str = ""
    document: str | None = None
    abstain_reason: str | None = None
    date_note: str | None = None
    mentions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_ratio(self) -> bool:
        return self.ratio_from is not None and self.ratio_to is not None

    @property
    def about_a_split(self) -> bool:
        """Did the filing talk about a split at all (any ratio mention, operative or not)?"""
        return bool(self.mentions) or self.has_ratio


# --- documents ----------------------------------------------------------------------------------------------


def submission_documents(raw: str) -> list[FilingDocument]:
    """Split an EDGAR full-submission ``.txt`` into its documents. A text with no ``<DOCUMENT>`` block is
    returned as ONE document of type ``HTML`` (a single primary document fetched on its own)."""
    parts = re.split(r"<DOCUMENT>", raw)
    if len(parts) == 1:
        return [FilingDocument(type="HTML", filename="", raw=raw)]
    docs: list[FilingDocument] = []
    for part in parts[1:]:
        t = re.search(r"<TYPE>([^\n<]+)", part)
        f = re.search(r"<FILENAME>([^\n<]+)", part)
        body = re.search(r"<TEXT>(.*?)(?:</TEXT>|\Z)", part, re.S)
        docs.append(
            FilingDocument(
                type=(t.group(1).strip() if t else ""),
                filename=(f.group(1).strip() if f else ""),
                raw=body.group(1) if body else part,
            )
        )
    return docs


def relevant_documents(docs: list[FilingDocument]) -> list[FilingDocument]:
    """The documents a split is announced in: the form's own (8-K / 6-K / 1-A / 1-U, a 1-A's ``PART II AND
    III``), the ``EX-99.x`` press releases, and a lone untyped document."""
    keep = [d for d in docs if d.type.upper().startswith(_KEEP_TYPES) or d.type == "HTML"]
    return keep


def document_text(raw: str) -> str:
    """The document's words as a reader sees them: tags removed, entities decoded, whitespace collapsed. The
    passage is a slice of THIS text (verbatim)."""
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


# --- numbers / dates ----------------------------------------------------------------------------------------


def _words_value(words: str) -> Decimal | None:
    half = re.search(_HALF + r"$", words, re.I)
    if half:
        words = words[: half.start()]
    total = 0
    current = 0
    seen = False
    for w in re.split(r"[\s-]+", words.lower()):
        if not w or w == "and":
            continue
        if w in _UNITS:
            current += _UNITS[w]
            seen = True
        elif w == "hundred":
            current = (current or 1) * 100
            seen = True
        elif w == "thousand":
            total += (current or 1) * 1000
            current = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return Decimal(total + current) + (Decimal("0.5") if half else Decimal(0))


def _digits(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _side(m: re.Match, prefix: str) -> tuple[Decimal | None, bool]:
    """``(value, conflict)`` for one side of a ratio match: the digit restatement wins over the words and a
    disagreement between them is a CONFLICT (never resolved by picking one)."""
    d = m.groupdict().get(f"{prefix}d")
    if d:
        return _digits(d), False
    w = m.groupdict().get(f"{prefix}w")
    p = m.groupdict().get(f"{prefix}p")
    wv = _words_value(w) if w else None
    if p:
        pv = _digits(p)
        if wv is not None and pv is not None and Decimal(wv) != pv:
            return pv, True
        return pv, False
    return (Decimal(wv) if wv is not None else None), False


def _parse_date(m: re.Match) -> date | None:
    g = m.groupdict()
    try:
        if g.get("y3"):
            return date(int(g["y3"]), int(g["m3"]), int(g["d3"]))
        mon = g.get("m1") or g.get("m2")
        day = g.get("d1") or g.get("d2")
        year = g.get("y1") or g.get("y2")
        if mon is None or day is None or year is None:
            return None
        if mon == "may":  # the verb, not the month (filings capitalize the month)
            return None
        return date(int(year), _MONTHS[mon[:3].lower()], int(day))
    except (ValueError, KeyError):
        return None


def _dates(text: str) -> list[tuple[int, date]]:
    out = []
    for m in _DATE.finditer(text):
        d = _parse_date(m)
        if d is not None:
            out.append((m.start(), d))
    return out


# --- sentences ----------------------------------------------------------------------------------------------


def _sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans over the normalized text; an abbreviation's period ("5:00 p.m.", "Inc.") never ends
    one."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        end = m.end()
        if text[m.start()] == "." and _ABBREV.search(text[max(start, m.start() - 12) : m.start()]):
            continue
        if end - start > 1:
            spans.append((start, end))
        start = end
        while start < len(text) and text[start] == " ":
            start += 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


# --- the extraction -----------------------------------------------------------------------------------------


@dataclass
class _Mention:
    old: Decimal | None
    new: Decimal | None
    kind: str  # operative | range | historical | conflict | undirected
    doc: int
    sentence: tuple[int, int]
    raw: str
    pos: int = 0  # where the ratio starts in the document (absolute)

    def key(self) -> Fraction | None:
        if self.old is None or self.new is None or self.new == 0:
            return None
        return Fraction(self.old) / Fraction(self.new)


@dataclass
class _Doc:
    label: str
    text: str  # verbatim (passage source)
    match: str  # same length, for matching
    spans: list[tuple[int, int]]


def _direction(sentence: str, doc_direction: str | None) -> str | None:
    rev = bool(_REVERSE_CUE.search(sentence))
    fwd = bool(_FORWARD_CUE.search(sentence))
    if rev and not fwd:
        return "reverse"
    if fwd and not rev:
        return "forward"
    return doc_direction


def _doc_direction(texts: list[str]) -> str | None:
    rev = any(_REVERSE_CUE.search(t) for t in texts)
    fwd = any(_FORWARD_CUE.search(t) for t in texts)
    if rev and not fwd:
        return "reverse"
    if fwd and not rev:
        return "forward"
    return None


def _oriented(a: Decimal, b: Decimal, direction: str | None) -> tuple[Decimal, Decimal] | None:
    if direction == "reverse":
        return (max(a, b), min(a, b))
    if direction == "forward":
        return (min(a, b), max(a, b))
    return None


_OLD_SIDE = re.compile(r"\bpre[\s-]|\bold\b|\bexisting\b|\bcurrent\b|\boutstanding\b", re.I)
_NEW_SIDE = re.compile(r"\bpost[\s-]|\bnew\b", re.I)


def _for_every_oriented(
    m: re.Match, sent: str, a: Decimal, b: Decimal, direction: str | None
) -> tuple[Decimal, Decimal] | None:
    """``(old, new)`` for "A … for every B": filings write it BOTH ways — "one (1) share for every sixteen
    (16)" (new for every old) and "four (4) pre-Reverse Split shares for every one (1) post-Reverse Split
    share" (ELBM: old for every new) — so the words around each number decide (pre / old / outstanding vs
    post / new), and only without them the split's direction."""
    whole = m.group(0)
    lead = whole[: whole.lower().rfind("for")]  # A and the words after it
    tail = sent[m.end() : m.end() + 40]  # the words after B
    if _OLD_SIDE.search(lead) and not _NEW_SIDE.search(lead):
        return (a, b)
    if _NEW_SIDE.search(lead):
        return (b, a)
    if _NEW_SIDE.search(tail) and not _OLD_SIDE.search(tail):
        return (a, b)
    if _OLD_SIDE.search(tail):
        return (b, a)
    return _oriented(a, b, direction)


def _plausible(v: Decimal | None, *, plain_for: bool) -> bool:
    if v is None or v <= 0 or v > 100000:
        return False
    # a bare "2025 for 2026" is years, not a ratio
    return not (plain_for and v == v.to_integral_value() and 1900 <= v <= 2100)


def _mentions(docs: list[_Doc], doc_direction: str | None) -> list[_Mention]:
    out: list[_Mention] = []
    for di, d in enumerate(docs):
        for s, e in d.spans:
            sent = d.match[s:e]
            if not _SPLIT_CUE.search(sent):
                continue
            direction = _direction(sent, doc_direction)
            found: list[tuple[int, int, Decimal | None, Decimal | None, str, str]] = []
            for m in _EVERY_INTO.finditer(sent):
                if not _COMBINE_VERB.search(m.group("fill")):
                    continue
                (a, ca), (b, cb) = _side(m, "a"), _side(m, "b")
                if not (_plausible(a, plain_for=False) and _plausible(b, plain_for=False)):
                    continue
                kind = "conflict" if ca or cb else "explicit"
                if kind == "explicit" and _EVERY_RANGE.match(m.group("fill")):
                    # "every two (2) to two hundred and fifty (250) Ordinary Shares shall be converted into
                    # one (1)" (MTEK's proxy 6-K) — the low end of an authorized range, not the ratio set
                    kind = "range"
                found.append((m.start(), m.end(), a, b, kind, m.group(0)))
            for m in _FOR_EVERY.finditer(sent):
                (a, ca), (b, cb) = _side(m, "a"), _side(m, "b")
                if not (_plausible(a, plain_for=False) and _plausible(b, plain_for=False)):
                    continue
                if ca or cb:
                    found.append((m.start(), m.end(), a, b, "conflict", m.group(0)))
                    continue
                whole = m.group(0)
                if _ADDITIONAL.search(whole[: whole.lower().rfind("for")]):
                    # "A additional shares for every B": B old shares -> A + B new (a forward split)
                    found.append((m.start(), m.end(), b, a + b, "explicit", whole))  # type: ignore[operator]
                    continue
                o = _for_every_oriented(m, sent, a, b, direction)  # type: ignore[arg-type]
                if o is None:
                    found.append((m.start(), m.end(), a, b, "undirected", m.group(0)))
                else:
                    found.append((m.start(), m.end(), o[0], o[1], "explicit", m.group(0)))
            for m in _FOR.finditer(sent):
                if any(fs <= m.start() < fe for fs, fe, *_ in found):
                    continue  # already read as "N share for every M"
                plain = m.group("sep").strip().lower() == "for"
                (a, ca), (b, cb) = _side(m, "a"), _side(m, "b")
                if not (_plausible(a, plain_for=plain) and _plausible(b, plain_for=plain)):
                    continue
                if ca or cb:
                    found.append((m.start(), m.end(), a, b, "conflict", m.group(0)))
                    continue
                o = _oriented(a, b, direction)  # type: ignore[arg-type]
                if o is None:
                    found.append((m.start(), m.end(), a, b, "undirected", m.group(0)))
                else:
                    found.append((m.start(), m.end(), o[0], o[1], "explicit", m.group(0)))
            for m in _COLON.finditer(sent):
                before = sent[max(0, m.start() - 40) : m.start()]
                after = sent[m.end() : m.end() + 30]
                if not (re.search(r"ratio", before, re.I) or _COLON_AFTER.match(after)):
                    continue
                a, b = _digits(m.group("ad")), _digits(m.group("bd"))
                if not (_plausible(a, plain_for=False) and _plausible(b, plain_for=False)):
                    continue
                o = _oriented(a, b, direction)  # type: ignore[arg-type]
                if o is None:
                    found.append((m.start(), m.end(), a, b, "undirected", m.group(0)))
                else:
                    found.append((m.start(), m.end(), o[0], o[1], "explicit", m.group(0)))
            for start, _end, old, new, kind, raw in sorted(found, key=lambda f: f[0]):
                if old is None or new is None or old == new:
                    continue  # unreadable, or "one-for-one" (an exchange, not a split)
                look = sent[max(0, start - _RANGE_LOOKBACK) : start]
                if kind == "explicit" and _RANGE_CUE.search(look):
                    kind = "range"
                elif kind == "explicit":
                    kind = "operative"
                out.append(
                    _Mention(
                        old=old,
                        new=new,
                        kind=kind,
                        doc=di,
                        sentence=(s, e),
                        raw=raw.strip(),
                        pos=s + start,
                    )
                )
    return out


def _cued_dates(
    docs: list[_Doc], cue: re.Pattern, *, need_trading: bool
) -> list[tuple[date, int, tuple[int, int], int]]:
    """``(date, doc, sentence, where)`` for every sentence carrying ``cue`` (and, for a trading sentence, a word
    about trading): its only date, or the first date after the cue when it has several."""
    out = []
    for di, d in enumerate(docs):
        for s, e in d.spans:
            sent = d.match[s:e]
            cm = cue.search(sent)
            if cm is None and need_trading and _SPLIT_CUE.search(sent):
                cm = _TRADING_START.search(sent)  # "begin trading … after … the reverse split"
            if cm is None:
                continue
            if need_trading and not _TRADING_WORD.search(sent):
                continue
            if not need_trading and not _SPLIT_CUE.search(sent):
                continue
            ds = _dates(sent)
            if not ds:
                continue
            if len({x for _, x in ds}) == 1:
                out.append((ds[0][1], di, (s, e), s + ds[0][0]))
                continue
            after = [(pos, x) for pos, x in ds if pos >= cm.start()]
            if after:
                out.append((after[0][1], di, (s, e), s + after[0][0]))
    return out


def _past(d: date, filed: date) -> bool:
    """Is ``d`` a PAST split's date for a filing filed on ``filed`` (more than ``HISTORICAL_GAP_DAYS``
    before it)?"""
    return (filed - d).days > HISTORICAL_GAP_DAYS


def _fmt(v: Decimal) -> str:
    s = format(v.normalize(), "f")
    return s


def _label(old: Decimal, new: Decimal) -> str:
    return f"{_fmt(old)}->{_fmt(new)}"


def _passage(
    docs: list[_Doc], parts: list[tuple[int, tuple[int, int], int]]
) -> tuple[str, str | None]:
    """The verbatim passage: each part's sentence — or, for a run-on sentence, a window of it around what it
    states (``pos``) — joined with " … ". The label names the first part's document."""
    seen: list[tuple[int, tuple[int, int], int]] = []
    for p in parts:
        if all((p[0], p[1]) != (q[0], q[1]) for q in seen):
            seen.append(p)
    texts = []
    for di, (s, e), pos in seen:
        text = docs[di].text
        lo, hi = s, e
        if e - s > _LONG_SENTENCE:
            lo = max(s, pos - _WINDOW_BEFORE)
            hi = min(e, pos + _WINDOW_AFTER)
            if lo > s:
                cut = text.find(" ", lo, pos)
                lo = cut + 1 if cut != -1 else lo
            if hi < e:
                cut = text.rfind(" ", pos, hi)
                hi = cut if cut != -1 else hi
        t = text[lo:hi].strip()
        texts.append(("[…] " if lo > s else "") + t + (" […]" if hi < e else ""))
    label = docs[seen[0][0]].label if seen else None
    return " … ".join(texts), label


def extract_split(documents: list[FilingDocument], *, filed: date | None = None) -> SplitExtraction:
    """Read one filing's split ratio, effective date and located passage (see the module docstring for every
    rule). Pure: the documents in, a ``SplitExtraction`` out — an abstain is a result, never a raise.

    ``filed`` — the filing date, given for a CATCH-ALL filing (an 8-K read only for its Item 8.01 / 7.01): a
    date more than ``HISTORICAL_GAP_DAYS`` before it is a PAST split's (the filing restates it), never this
    filing's first session, and a ratio stated only beside such dates is a past split's too.
    """
    docs: list[_Doc] = []
    for fd in relevant_documents(documents):
        text = document_text(fd.raw)
        if not text:
            continue
        match = text.translate(_MATCH_MAP)
        docs.append(
            _Doc(
                label=" ".join(x for x in (fd.type, fd.filename) if x),
                text=text,
                match=match,
                spans=_sentences(match),
            )
        )
    if not docs:
        return SplitExtraction(abstain_reason="no readable document")

    # the date first — the past-split screen measures against it
    trading = _cued_dates(docs, _TRADING_CUE, need_trading=True)
    legal = _cued_dates(docs, _LEGAL_CUE, need_trading=False)
    past: list[date] = []
    if filed is not None:
        # a catch-all filing restating a PAST split (AIRE's 8-K/A, filed 2026-09-23: "as adjusted for the
        # Company's 1-for-25 reverse stock split", its only date 2025-12-31) dates nothing of its own there
        past = sorted({c[0] for c in trading + legal if _past(c[0], filed)})
        trading = [c for c in trading if not _past(c[0], filed)]
        legal = [c for c in legal if not _past(c[0], filed)]
    effective: date | None = None
    kind: str | None = None
    date_note: str | None = None
    date_at: tuple[int, tuple[int, int], int] | None = None
    for cand, k in ((trading, "trading"), (legal, "legal")):
        if not cand:
            continue
        distinct = sorted({c[0] for c in cand})
        if len(distinct) == 1:
            effective, kind = distinct[0], k
            date_at = (cand[0][1], cand[0][2], cand[0][3])
        else:
            date_note = f"{len(distinct)} different {k} dates stated: " + ", ".join(
                x.isoformat() for x in distinct
            )
        break
    if effective is None and date_note is None:
        date_note = "no first-session or effective date stated"
        if past:
            date_note = (
                f"only a past date stated ({', '.join(x.isoformat() for x in past)}), more than "
                f"{HISTORICAL_GAP_DAYS} days before the filing"
            )

    direction = _doc_direction([d.match for d in docs])
    mentions = _mentions(docs, direction)
    anchors = [a for a in (effective, filed) if a is not None]
    for mt in mentions:
        if mt.kind != "operative":
            continue
        s, e = mt.sentence
        ds = [x for _, x in _dates(docs[mt.doc].match[s:e])]
        if ds and any(all((a - x).days > HISTORICAL_GAP_DAYS for x in ds) for a in anchors):
            mt.kind = "historical"

    seen_labels = tuple(dict.fromkeys(f"{m.kind}:{m.raw}" for m in mentions))
    base = {
        "effective_date": effective,
        "date_kind": kind,
        "date_note": date_note,
        "mentions": seen_labels,
    }
    conflicts = [m for m in mentions if m.kind == "conflict"]
    operative = [m for m in mentions if m.kind == "operative"]
    undirected = [m for m in mentions if m.kind == "undirected"]
    distinct: dict[Fraction, _Mention] = {}
    for m in operative:
        k = m.key()
        if k is not None:
            distinct.setdefault(k, m)

    def abstain(reason: str, at: list[_Mention]) -> SplitExtraction:
        parts = [(m.doc, m.sentence, m.pos) for m in at[:2]]
        if date_at is not None:
            parts.append(date_at)
        passage, label = _passage(docs, parts) if parts else ("", None)
        return SplitExtraction(abstain_reason=reason, passage=passage, document=label, **base)

    if conflicts:
        return abstain(
            "a ratio's words and its digits disagree: " + "; ".join(m.raw for m in conflicts[:3]),
            conflicts,
        )
    if len(distinct) > 1:
        return abstain(
            "more than one ratio stated: "
            + ", ".join(_label(m.old, m.new) for m in distinct.values()),  # type: ignore[arg-type]
            list(distinct.values()),
        )
    if not distinct:
        if undirected:
            return abstain(
                "a ratio is stated but not whether the split is reverse or forward", undirected
            )
        ranged = [m for m in mentions if m.kind == "range"]
        if ranged:
            return abstain(
                "only an authorized range of ratios ("
                + ", ".join(dict.fromkeys(m.raw for m in ranged))
                + ") — no ratio set",
                ranged,
            )
        past = [m for m in mentions if m.kind == "historical"]
        if past:
            return abstain("only a past split's ratio is stated", past)
        return abstain("no split ratio found", [])
    if undirected:
        # a second, unoriented ratio — only harmless when it is the same numbers as the one we read
        only = next(iter(distinct.values()))
        pair = {only.old, only.new}
        if any({m.old, m.new} != pair for m in undirected):
            return abstain(
                "a second ratio is stated without a direction: "
                + "; ".join(m.raw for m in undirected),
                [only, *undirected],
            )
    chosen = next(iter(distinct.values()))
    parts = [(chosen.doc, chosen.sentence, chosen.pos)]
    if date_at is not None:
        parts.append(date_at)
    passage, label = _passage(docs, parts)
    return SplitExtraction(
        ratio_from=chosen.old,
        ratio_to=chosen.new,
        passage=passage,
        document=label,
        **base,
    )
