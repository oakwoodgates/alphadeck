"""Which cards a new fund fits — two plain word-matching reads on the FUND NAME, derived on read (no match
table, no LLM, #3). Pure: card inputs are passed in; no I/O but the editable wrapper-word file.

**Theme read** — the fund name against a card's tiered term set, through ``radar.matcher.match_term_set`` (the
same word-boundary rules the SPAC radar uses, so "IND" cannot match inside "INDEX"). A match is ANY hit, SIGNAL
or BROAD; both tuples are kept and ``tier`` says which it is ("signal" when any SIGNAL term hit, else "broad").
BROAD counts on its own because fund names are short (operator decision, reversing the signed plan's Q1): a
dev check over 24 months found SIGNAL alone matched 5 funds while BROAD added 66, nearly all real theme
launches. Display only, never a call input.

**Name read** — the fund name against the card's COMPANY members (basket rows whose master row is not a fund,
in any link, each labeled with its link):
- a ticker of 3+ characters matches alone, as a case-sensitive word token;
- a 1-2 character ticker matches only when the name also holds a wrapper word (``fund_wrapper_words.txt``);
- the company name, legal suffixes stripped, matches as a whole phrase (case-insensitive), only when the
  cleaned name is 2+ words or one word of 5+ letters.
The matched token rides along as provenance. A funds-only wrapper ("2X Long <TICKER>") filed on a card's company
is a retail-attention read on that company — display only: never a call input (#4).

Private-to-public filings (old confidential filings SEC released) are left out of matching unless asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from domain.thesis import TermSetEntry
from radar import matcher
from radar.fund_stage import FundLaunch

WRAPPER_WORDS_PATH = Path(__file__).with_name("fund_wrapper_words.txt")
STOPWORDS_PATH = Path(__file__).with_name("fund_name_stopwords.txt")

_LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "group",
        "spa",
        "ltd",
        "limited",
        "plc",
        "llc",
        "lp",
        "holding",
        "holdings",
        "nv",
        "sa",
        "se",
        "ag",
        "class",
        "a",
        "b",
        "c",
        "ordinary",
        "shares",
        "common",
        "stock",
        "adr",
        "ads",
    }
)


_MARKS = re.compile(r"\((?:TM|R|SM)\)|[™®℠]", re.IGNORECASE)


def strip_marks(fund_name: str) -> str:
    """The fund name without trademark marks: "(TM)", "(R)", "(SM)" and the symbols. "BaseballShares(TM) 2x ..."
    must not read the mark as a 2-letter ticker."""
    return _MARKS.sub("", fund_name)


def _phrase(words: str, *, ignore_case: bool = True, hyphen_joins: bool = False) -> re.Pattern[str]:
    """A whole-word phrase. ``hyphen_joins`` (the name read) treats ``-`` as part of the word, so a ticker or a
    company name never matches beside a hyphen ("Q" in "Q-ZEN", "Acme" in "Acme-500")."""
    body = r"\s+".join(re.escape(w) for w in words.split())
    edge = "A-Za-z0-9-" if hyphen_joins else "A-Za-z0-9"
    return re.compile(rf"(?<![{edge}]){body}(?![{edge}])", re.IGNORECASE if ignore_case else 0)


@lru_cache(maxsize=4)
def load_wrapper_words(path: Path | None = None) -> tuple[str, ...]:
    """The editable wrapper-word list (one per line; ``#`` comments and blanks skipped)."""
    p = path or WRAPPER_WORDS_PATH
    out: list[str] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return tuple(out)


@lru_cache(maxsize=4)
def load_stopwords(path: Path | None = None) -> frozenset[str]:
    """The editable single-word company-name stoplist (lowercased): common fund-name words that a one-word
    company name must never match on (a company called "Growth Inc" is not every "... Growth ETF").
    """
    return frozenset(w.lower() for w in load_wrapper_words(path or STOPWORDS_PATH))


def has_wrapper_word(fund_name: str, words: tuple[str, ...]) -> bool:
    return any(_phrase(w).search(fund_name) for w in words)


def clean_company_name(name: str) -> str:
    """The company name with legal suffixes stripped from its tail ("Example Holdings Inc" -> "Example")."""
    # dots go first so "N.V." -> "NV" and "S.A." -> "SA"; a leading "The" and a trailing "& Co" are legal noise
    tokens = [t for t in re.sub(r"[,/()]", " ", name.replace(".", "")).split() if t]
    if len(tokens) > 1 and tokens[0].lower() == "the":
        tokens.pop(0)
    while len(tokens) > 1 and (tokens[-1].lower() in _LEGAL_SUFFIXES or tokens[-1] == "&"):
        tokens.pop()
    return " ".join(tokens)


def name_is_distinctive(cleaned: str) -> bool:
    """The length rule: 2+ words, or one word of 5+ letters."""
    words = cleaned.split()
    return len(words) >= 2 or (len(words) == 1 and sum(c.isalpha() for c in words[0]) >= 5)


@dataclass(frozen=True)
class CompanyRef:
    """One company member of a card, as the name read sees it."""

    ticker: str
    name: str | None
    link: str | None  # the value-chain link the member sits in (labeled in the output)


@dataclass(frozen=True)
class CardInput:
    thesis_id: str
    name: str
    term_set: list[TermSetEntry]
    companies: list[CompanyRef]


@dataclass(frozen=True)
class ThemeMatch:
    signal: tuple[str, ...]  # the SIGNAL-tier terms that hit (may be empty)
    broad: tuple[
        str, ...
    ]  # the BROAD-tier terms that hit (may be empty; counts as a match on its own)

    @property
    def tier(self) -> str:
        """ "signal" when any SIGNAL term hit, else "broad" (a ThemeMatch always has at least one hit)."""
        return "signal" if self.signal else "broad"


@dataclass(frozen=True)
class NameHit:
    ticker: str
    link: str | None
    token: (
        str  # what matched: the ticker, or the cleaned company name as it appears in the fund name
    )
    via: str  # 'ticker' | 'company_name'


def match_theme(fund_name: str, term_set: list[TermSetEntry]) -> ThemeMatch | None:
    signal, broad = matcher.match_term_set(strip_marks(fund_name), term_set)
    if not signal and not broad:
        return None
    return ThemeMatch(tuple(signal), tuple(broad))


def match_names(
    fund_name: str,
    companies: list[CompanyRef],
    *,
    wrapper_words: tuple[str, ...] | None = None,
    stopwords: frozenset[str] | None = None,
) -> list[NameHit]:
    words = load_wrapper_words() if wrapper_words is None else wrapper_words
    fund_name = strip_marks(fund_name)
    wrapped = has_wrapper_word(fund_name, words)
    stop = load_stopwords() if stopwords is None else frozenset(w.lower() for w in stopwords)
    wrapper_set = {w.lower() for w in words}
    hits: list[NameHit] = []
    for c in companies:
        tick = c.ticker.strip()
        # a matched token (ticker or cleaned name, any word count) on the stoplist never counts
        if tick and tick.lower() not in stop:
            m = _phrase(tick, ignore_case=False, hyphen_joins=True).search(fund_name)
            if m and (len(tick) >= 3 or wrapped):
                hits.append(NameHit(c.ticker, c.link, m.group(0), "ticker"))
                continue
        if c.name:
            cleaned = clean_company_name(c.name)
            lowered = cleaned.lower()
            if name_is_distinctive(cleaned) and lowered not in wrapper_set and lowered not in stop:
                m = _phrase(cleaned, hyphen_joins=True).search(fund_name)
                if m:
                    hits.append(NameHit(c.ticker, c.link, m.group(0), "company_name"))
    return hits


@dataclass(frozen=True)
class CardMatch:
    launch: FundLaunch
    theme: ThemeMatch | None
    names: tuple[NameHit, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> bool:
        return self.theme is not None or bool(self.names)


def match_launches(
    launches: list[FundLaunch],
    card: CardInput,
    *,
    include_private: bool = False,
    wrapper_words: tuple[str, ...] | None = None,
    stopwords: frozenset[str] | None = None,
) -> list[CardMatch]:
    """The launches that fit one card (at least one read hit), in the launches' order. Private-to-public
    filings are skipped unless ``include_private``."""
    out: list[CardMatch] = []
    for lc in launches:
        if lc.private_to_public and not include_private:
            continue
        theme = match_theme(lc.name, card.term_set)
        names = tuple(
            match_names(lc.name, card.companies, wrapper_words=wrapper_words, stopwords=stopwords)
        )
        if theme is not None or names:
            out.append(CardMatch(lc, theme, names))
    return out
