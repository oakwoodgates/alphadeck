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
below). A 6-K or a charter 8-K is read through the RESTATEMENT SCREEN (``extract_split_screened``, 2026-10-24),
which only ever removes from the unscreened reading. And — on the TARGETED path only, a split no filing in its
own window agrees with — the WIDE forms (``WIDE_KEEP_TYPES``: a 10-Q / 10-K / 20-F / 40-F, an F-6 / F-6 POS, a
424B3 / 424B4, a DEF / PRE 14C), always through that screen (``relevant_documents(wide=True)``).

**The ratio** is read from five phrasings, every one MEASURED on the real filings in
``tests/fixtures/splits``:

- ``N-for-M`` / ``N for M`` in digits or words, with an optional parenthesized digit restatement —
  "1-for-16", "one-for-sixteen (1-for-16)", "one (1) for nine (9)", "twenty five (25) -for- one (1)",
  "15 for 1 Share Consolidation", "1-for-6.25" — and ``N-to-M`` right before "basis" ("on a 9-to-1 basis", DFSC);
- ``N:M`` when it sits next to the word "ratio" or a split word — "fixed the split ratio at 8:1", "a 1:300
  reverse stock split" (a clock time such as "5:00 p.m." never qualifies; since 2026-11-26 the WORD "ratio", never
  the one inside "exploration", and a year is never a side: "Figure 9-2: 2013 and 2015 Airborne TDEM survey results",
  NMG's list of figures);
- ``N share(s) for every M`` — "one (1) share for every sixteen (16)", "1 post-split ordinary share for every
  20 pre-split ordinary shares", and the other way round, "four (4) pre-Reverse Split shares for every one (1)
  post-Reverse Split share" (ELBM): the words beside each number (pre / old / outstanding vs post / new) say
  which side is old. The words between the two numbers are never a number themselves ("… September 28, 2026
  received two additional shares for every one" reads "two … for every one", ZCSH);
- ``A additional share(s) … for every B`` — a split paid as extra shares, B old shares becoming A + B: "one
  additional share of common stock for every two shares owned" (SNEX's three-for-two, 2 -> 3), "two additional
  shares for every one pre-split share" (ZCSH's 3-for-1), "nine (9) additional shares … for every" one (KLA) —
  and with no number after "for each / every", one old share: "four additional shares for each share" (NOW's
  5-for-1), "three additional ADSs for every ADS held" (EMBJ; read even with no split word in the sentence — but
  then, since 2026-11-26, only for shares HELD: "held", "then-held", "owned", "of record"; "3 Additional Shares for
  each Ordinary Share that the PIPE Investor purchases upon exercise", CRML's warrant term, is no split);
- ``every/each M … combined/consolidated/converted … into N`` — "every fifteen (15) … consolidated into one
  (1)", "each nine (9) pre-split shares … combine into one (1) new share", "every twenty-eight and one half …
  combined into one" (SMX; a word number may end "and one half").

The last two state the direction themselves. The ``N-for-M`` and ``N:M`` forms do not —
filings write a 1-for-16 reverse split as "1-for-16" and a 25-for-1 consolidation as "25-for-1" — so they (and a
"for every" with no pre/post words) are oriented by the filing's own words: "reverse … split" / a consolidation
OF SHARES / "combined" make it a reverse split (old = the larger number), "forward … split" / "stock dividend" /
"subdivision" a forward one — and, in the ratio's own sentence when it names neither, "with a proportionate
increase in … authorized" shares or "additional shares" (NOW). An accounting "consolidated financial statements"
is no consolidation of shares. A ratio is only ever read inside a sentence that talks about a split: "split", a
consolidation, combination or subdivision OF SHARES, a reverse split or consolidation, a reclassification, an ADS
ratio change — each word in its share sense (``_SPLIT_CUE``, since 2026-11-26), never "consolidating entities",
"the combined company", "reverse osmosis" or "tectonic subdivisions"; a "business combination" is a merger, not
shares combined. "1-for-1" (a share exchange) is not a split. An ADS RATIO CHANGE counts as a split of the ADS —
"the same effect as a one-for-thirty reverse ADS split" (NIPG) — because it moves the ADS price exactly like one,
and Massive.com lists it as one.

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
  restate theirs (MEASURED 2026-10-23: four such 8.01 / 7.01 readings on the basket, 123 to 266 days back). Where
  the filing date is given (a catch-all, the restatement screen's second reading), since 2026-11-26 also a ratio
  stated as DONE ("effected", "implemented", "occurred" …) whose OWN clause dates it more than
  ``HISTORICAL_GAP_DAYS`` before the filing, WHATEVER ELSE its sentence dates — the clause's date is the sentence's
  nearest to the ratio, full or MONTH AND YEAR ("In June 2022"), with no other ratio between: CYCU's delisting notice
  "Because the Company effected a 1-for-30 reverse stock split on October 27, 2025, … trading … is expected to be
  suspended … on July 21, 2026", STKE's share table "(1) Reflects the 1 for 8 share consolidation that occurred on
  August 5, 2025" beside its 2026 dates, EMAT's de-SPAC exhibit "In June 2022, the Company implemented a 1-for-10
  stock split".

Both hold whether or not the filing says which way the ratio points (since 2026-11-25: a 10-Q's covenant "no more
than 4.0:1.0" is a ceiling, NEE's 10-Q restating its 2020 "four-for-one stock split" a past split, with no
direction word left once "consolidated financial statements" stopped reading as a reverse split).

- a SUPERSEDED ratio (2026-10-24): when a filing states EXACTLY ONE ratio in a sentence that revises one
  ("has determined to increase the amount of the reverse stock split … at a ratio of 1-for-125") and every
  other ratio only in sentences citing an earlier disclosure ("… at a ratio of 1-for-50 as disclosed on the
  Current Report on Form 8-K filed … on July 13, 2026") — DFNS's 8-K of 2026-07-16 — the revised one stands.
- a KNOWN split (2026-11-25): the caller passes the security's splits already CONFIRMED on the list and in
  effect by the filing's date (``known``, ``KnownSplit``). A ratio equal to one of them, in a sentence that
  states it as DONE ("implemented", "effected", "completed", "restated to reflect", "as previously disclosed";
  never "to be effected"), is that past split's WHATEVER ITS AGE: GCTK's 8-K of 2026-10-22 restates "On August
  28, 2026, the Company implemented a 1-for-15 reverse stock split" (55 days back) beside its new 1-for-2. Not
  when that sentence dates it away from the known split (another split of the same ratio), nor when the filing's
  own date is the known split's (the filing is that split's evidence).

After those, **exactly one** distinct ratio must remain, or the extractor ABSTAINS and says why: none found,
only a range, only a past split, two different ratios, a word and its digit restatement disagreeing, or no
direction stated. It never picks between two values. A filing that ENDS its ADR program (``ADS_TERMINATION``)
always abstains: the ADS's price moves by the exchange's factor, not the ordinary shares' ratio it states (POM:
"every ten (10) ordinary shares … consolidated into one (1)", while "each holder of ten (10) ADSs will receive
three (3)" shares).

**The date** is the first session at the new price. A TRADING sentence (one about trading, carrying a cue
such as "split-adjusted basis", "post-consolidation basis", "market open", "opening of trading",
"commencement of trading" — or "will begin trading" in a sentence about the split, "the first date when the
… shares will begin trading … after implementation of the reverse split will be Monday, October 5, 2026",
CMND) states it directly — kind ``trading``. Since 2026-11-26 a trading sentence is one about the split, or one
with a cue only a split has (split- or consolidation-adjusted, post-split, post-consolidation, "consolidated basis",
the NEW CUSIP / ISIN, the new ratio the shares trade under or the ADS-to-share ratio the ADSs trade under): a new
ticker or a listing "at the opening of trading" dates nothing (CD's, EMAT's). A trading date that is no session (a
weekend or an NYSE holiday, ``domain.trading_days``) is read as a legal one: "effective at 11:59 p.m., Eastern Time,
on April 27, 2025 and began trading on a post-split basis" (SKYA's charter 8-K; a Sunday). Only when no trading
sentence carries a date does a LEGAL sentence
about the split ("effective as of", "Effective June 12, 2025", "will take effect on") supply one — kind
``legal``, which may be the session before the first one (a 5:00 p.m. effective time). Two different dates of
the winning kind → no date (named in ``date_note``); the ratio still stands. A filing that states both reads
its trading date: ENHA's 8.01 8-K says "effective at 12:01 a.m. Central Time on October 29, 2026" AND "expected
to begin trading on a split-adjusted basis … at the opening of trading on October 29, 2026" — ``trading``. For a
CATCH-ALL filing a date more than ``HISTORICAL_GAP_DAYS`` before its filing date dates nothing (AIRE's 7.01
8-K/A of 2026-09-23 reached only 2025-12-31, in a tax table, until 2026-11-26): no date, named in ``date_note``.
"With effect from 27 July 2026" is a legal cue (ZNB's 6-K of 2026-08-25), and a date a MEETING was held on
("extraordinary general meeting of shareholders … held on 10 September 2026", ZNB's charter exhibit) never dates a
split — read anywhere, as a trading, legal or past date.

The date sentences the split list misread until 2026-11-25 (W2-5), each MEASURED on its real filing: a SESSION
cue names the first session without the word "trade" in a sentence about the split ("effective on the OTC
Markets at the open of business on December 15, 2025", COPR); "the first day of trading … is expected to be
October 29, 2025" is a trading cue (NVA); a sentence naming the LAST session at the old price ("the last trading
day of the prior ISIN", QGEN) states no first session. A date that NAMES SOMETHING ELSE never dates a split: a
period end ("the year ended December 31, 2025", IMCC), an accounting standard "not yet effective" (PBM), the
release's own date (its dateline "Nanjing, China, October 22, 2026 /PRNewswire/", SCAG; "On <date>, the Company
issued a press release"), a deadline ("the June 10, 2026 compliance deadline", EZRA) — and, since 2026-11-26, a
trading SUSPENSION, delisting or halt ("trading … is expected to be suspended at the opening of business on July 21,
2026", CYCU's delisting notice; VRM's, SKYA's), a resolution's own date ("the authority granted by the special
resolution passed on 19 June 2025", PMEC) and a share count's ("issued and outstanding as of April 18, 2025", HLYK;
never "effective as of"). A date may carry a space before its comma or suffix ("August 31 , 2026", VEEA;
"December 15 th , 2025", COPR).

**The restatement screen** (``extract_split_screened`` — 6-Ks, charter 8-Ks, the wide forms) only ever
REMOVES: a reading whose date is current stands exactly as read; a date more than ``HISTORICAL_GAP_DAYS``
before the filing is dropped and nothing replaces it (a restated old split, or MEDS's mistyped "May 26, 2025"
in an 8-K filed 2026-05-21); a ratio the text dates in the past (its own sentence, the date sentence beside an
undated one — LGHL's "… the same effect as a one-for-fifty reverse ADS split. The change in the ADS ratio was
effective on July 13, 2023." — or, in a filing that dates nothing currently, any mention of it, whether or not
a date cue reaches the past date, though never one about what WILL happen unless a cue does) is a past split's; an
abstain is never resolved into a reading. Its docstring holds the MEASURED reasons.

**The passage** is the filing's own sentence(s), verbatim after the HTML is stripped (tags removed, entities
decoded, whitespace collapsed): the sentence that states the ratio and, when it is another one, the sentence
that states the date. A run-on sentence (a 6-K cover page has no full stop) is cut to a verbatim window around
what it states, marked "[…]".
"""

from __future__ import annotations

import calendar
import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from domain.splits import PAST_RATIO_ONLY
from domain.trading_days import is_scheduled_day

# what makes a sentence ABOUT a split (``_SPLIT_CUE``) and its DIRECTION cues (``_REVERSE_CUE``, ``_FORWARD_CUE``,
# ``_FORWARD_SENTENCE_CUE``) follow the number words

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


_REVERSE_CUE_CORE = (
    r"reverse\s+(?:[\w-]+\s+)?split|reverse-split|(?:share|stock)\s+consolidat|"
    r"combined\s+into|combine\s+into"
)
# "consolidat…" is a reverse split only in its SHARE sense — "a consolidation of its common shares", "consolidated
# into one (1)", "the shares will be consolidated", "post-consolidation", "the Consolidation" — never in the
# accounting one: "consolidated financial statements", "eliminated upon consolidation" run all through a 10-Q /
# 10-K, and so does "reclassified into earnings" (a reverse cue only before a number: "reclassified into one (1)
# share"). They made NOW's 5-for-1 read 5 -> 1 in its 10-Q and its 10-K until 2026-11-25.
_SHARE_WORD = r"(?:shares?|stock|ADSs?|ADRs?)\b"
# the words between "consolidat…" and the shares it consolidates: "a consolidation of its common shares", "the
# Corporation consolidated all of its issued and outstanding Class A Multiple Voting Shares" (QNTM's 20-F)
_SHARE_QUALIFIER = (
    r"(?:the|its|our|their|all|every|each|of|issued|and|outstanding|existing|common|ordinary|class\s+\w|\w+['’]s|"
    rf"(?-i:[A-Z][\w-]*)|[\d,.]+|\(\s*[\d,.]+\s*\)|{_WORD})"
)
_ACCOUNTING_NOUN = (
    r"(?:condensed\s+)?(?:financial|statements?|balance|results|interim|net|income|subsidiar\w*)\b"
)
_CONSOLIDATION_OF_SHARES = (
    rf"\bconsolidat\w*\s+(?:into\b|at\s+(?:a|the)\s+ratio\b|on\s+an?\s+(?:{_WORD}|\d)|"
    rf"(?!{_ACCOUNTING_NOUN})(?:{_SHARE_QUALIFIER}\s+){{0,8}}?{_SHARE_WORD})|"
    rf"\b{_SHARE_WORD}\s+"
    r"(?:(?:will|would|shall|to|be|been|being|is|are|was|were|has|have|had|then|automatically)\s+){0,4}"
    r"consolidat|\b(?:pre|post)[\s-]*consolidat|\bconsolidation\s+ratio\b|"
    # a ratio right before it: "completed a 201:1 consolidation", "a 1-for-10 consolidation" (QNTM)
    rf"(?:\d\s*:\s*\d+|\bfor[\s-]+(?:[\d.]+|{_WORD}))\s*\)?\s*(?:-\s*)?consolidat|"
    # the defined term: (the "Consolidation"), "the 2024 Consolidation" (QNTM)
    r"(?-i:\b[Tt]he\s+[“\"]?\s*(?:\d{4}\s+)?Consolidation\b)"
)
_REVERSE_CUE = re.compile(
    rf"{_REVERSE_CUE_CORE}|{_CONSOLIDATION_OF_SHARES}|reclassified\s+into\s+(?:{_WORD}|\d|a\s+single)\b",
    re.I,
)
_FORWARD_CUE = re.compile(
    r"forward\s+(?:[\w-]+\s+)?split|stock\s+dividend|share\s+subdivision|subdivi", re.I
)
# In the ratio's OWN sentence only (never the document's direction), and only when that sentence names no
# direction itself: a split "with a proportionate increase in the number of shares of authorized common stock"
# (NOW's 8-K, 10-Q and 10-K; "a proportional increase" too), or one paid as "additional shares", is a forward
# split — but not "without a proportionate increase"
_FORWARD_SENTENCE_CUE = re.compile(
    r"\bproportion(?:ate|al)(?:ly)?\s+increase[sd]?\b[^.;]{0,60}?\bauthori[sz]ed\b|"
    r"\badditional\s+(?:shares|ADSs)\b",
    re.I,
)
_NEGATED_BEFORE = re.compile(r"\b(?:without|no|not|never)\s+(?:an?\s+|any\s+)?$", re.I)

# A ratio's sentence must have this much of a split in it, or the "N for M" is something else; and only a sentence
# about the split dates it (``_cued_dates``). An ADS RATIO CHANGE is a split of the ADS — "the same effect as a
# one-for-thirty reverse ADS split" (NIPG), and Massive.com lists it as one. Each word counts in its SHARE sense only
# (2026-11-26; until then a bare stem, and the 800-day check of 2026-11-26 dated splits by "consolidating
# entities" (STKE), "the combined company will begin trading" (MRLN), "RO reverse osmosis" and "Tectonic
# subdivisions" (NMG)):
# - "consolidat…": a consolidation OF SHARES (``_CONSOLIDATION_OF_SHARES``; and, for this test only, "a consolidation
#   of the Company's authorised, issued and outstanding shares", commas between);
# - "combin…": shares combined INTO fewer ("every 300 shares … were combined into one", "will be combined and
#   converted into one share"), a "share combination", a "combination of … shares" — never a company, entity or
#   business combined ("the combined company"), nor a "business combination" ("SAFE investors will receive one
#   warrant for every two shares acquired, … if the business combination is consummated", ENHA's 8.01 8-K of
#   2026-08-26, states no split);
# - "subdivi…": a share subdivision, a subdivision of (or shares subdivided into) shares — "the Company subdivided
#   each of its issued and unissued ordinary shares into eight (8)" (Hesai's 6-K; "unissued" / "authorised" may sit
#   among the qualifiers, as in a consolidation of the authorised, issued and outstanding shares);
# - "revers…": a reverse split or consolidation, "pre-/post-reverse".
_COMBINATION_OF_SHARES = (
    r"(?<!business\s)\bcombin(?:e|es|ed|ing)\b"
    r"(?:(?!\b(?:compan\w*|entit\w*|business\w*|group|operations|results|financial)\b)[^.;]){0,60}?\binto\b|"
    r"\b(?:share|stock)\s+combinations?\b|"
    rf"(?<!business\s)\bcombination\s+of\s+(?:{_SHARE_QUALIFIER}\s+){{0,8}}?{_SHARE_WORD}"
)
_SUBDIVISION_OF_SHARES = (
    r"\b(?:share|stock)\s+subdivi\w*|"
    r"\bsubdivi(?:de|des|ded|ding|sion|sions)\b(?:\s+(?:of|into))?\s+"
    rf"(?:(?:{_SHARE_QUALIFIER}|unissued|authori[sz]ed),?\s+){{0,8}}?{_SHARE_WORD}|"
    rf"\b{_SHARE_WORD}\s+(?:(?:will|would|shall|to|be|been|being|is|are|was|were|has|have|had)\s+){{0,4}}subdivided"
)
_CONSOLIDATION_OF_ISSUED_SHARES = (
    r"\bconsolidat\w*\s+of\s+"
    rf"(?:(?:{_SHARE_QUALIFIER}|unissued|authori[sz]ed),?\s+){{0,8}}?{_SHARE_WORD}"
)
_REVERSE_SENSE = (
    r"\breverse[\s-]+(?:[\w-]+[\s-]+){0,2}?(?:split|consolidat)|\b(?:pre|post)[\s-]*reverse\b"
)
_SPLIT_CUE = re.compile(
    rf"split|{_CONSOLIDATION_OF_SHARES}|{_CONSOLIDATION_OF_ISSUED_SHARES}|{_COMBINATION_OF_SHARES}|"
    rf"{_SUBDIVISION_OF_SHARES}|{_REVERSE_SENSE}|"
    r"reclassif|ratio\s+change|ADS\s+ratio",
    re.I,
)

# "1-for-16", "one-for-sixteen", "one (1) for nine (9)", "twenty five (25) -for- one (1)", "15 for 1"
_FOR = re.compile(
    rf"(?<![\w$.,]){_num('a')}\s*(?P<sep>-\s*for\s*-|-\s*for\s+|\s+for\s*-|\s+for\s+)\s*{_num('b')}"
    r"(?![\w]|[.,]\d)",
    re.I,
)
# "8:1", "1:300" — only beside the word "ratio" or a split word (never a clock time). Since 2026-11-26 the WORD:
# "Figure 9-1: Significant 2013-2022 exploration results 9-4 Figure 9-2: 2013 and 2015 Airborne TDEM survey results"
# (NMG's technical report, a list of figures) read as a 2:2013 ratio by the "ratio" inside "exploration" — and a
# year (1900-2100) is never a side of one (``_plausible``)
_COLON_CUE_BEFORE = re.compile(r"\bratio\b", re.I)
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
# … and with NO number after "for each / every": "will receive four additional shares for each share of Common
# Stock held" (NOW's 5-for-1), "three additional ADSs for every ADS held on the record date" (EMBJ's ADS ratio
# change) — one old share becomes 1 + A. The words themselves state a split (a holder receives more of what it
# holds), so the sentence is read with no split word in it (EMBJ's has none).
_ADS_WORD = r"(?:ADSs?|ADRs?|American\s+Depositary\s+(?:Shares?|Receipts?))"
_HELD_UNIT = rf"(?:shares?|{_ADS_WORD})\b"
_ADDITIONAL_EACH = re.compile(
    rf"(?<![\w$.,]){_num('a')}\s+additional\s+(?:(?!for\b)[A-Za-z][\w'-]*\s+){{0,3}}?{_HELD_UNIT}"
    rf"(?:\s+(?!for\b)(?!{_WORD}\b)[A-Za-z][\w'-]*){{0,6}}?\s+for\s+(?:each|every)\s+"
    rf"(?:(?!{_WORD}\b)[A-Za-z][\w'-]*\s+){{0,2}}?{_HELD_UNIT}",
    re.I,
)
# … but with no split word in the sentence only for the shares a holder HOLDS (since 2026-11-26): "held" ("for each
# then-held share"), "owned" or "of record", in the phrase or right after it (``_HELD_REACH`` characters). "The
# Empery Warrants provide the PIPE Investor with 3 Additional Shares for each Ordinary Share that the PIPE Investor
# purchases upon exercise" (CRML's 6-K of 2025-03-19, a warrant term) read as a 4-for-1 split
_HELD = re.compile(r"\bheld\b|\bowned\b|\bof\s+record\b", re.I)
_HELD_REACH = 40
# "a consolidation of its common shares on a 9-to-1 basis" (DFSC's two 6-Ks of 2026-11): "N-to-M" is read only
# right before "basis" (elsewhere "1-to-5 to 1-to-50" is a range); oriented like "N-for-M"
_TO_BASIS = re.compile(
    rf"(?<![\w$.,]){_num('a')}(?P<sep>\s*-\s*to\s*-\s*|\s+to\s+){_num('b')}(?=\s+basis\b)",
    re.I,
)
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
# "August 31 , 2026" (VEEA's press release) and "December 15 th , 2025" (COPR's) are dates too: a space may
# sit before the comma or the ordinal suffix
_DATE = re.compile(
    rf"\b(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}})(?:\s?(?:st|nd|rd|th))?\s?,?\s+(?P<y1>(?:19|20)\d\d)\b|"
    rf"\b(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m2>{_MONTH}),?\s+(?P<y2>(?:19|20)\d\d)\b|"
    r"\b(?P<y3>(?:19|20)\d\d)-(?P<m3>\d\d)-(?P<d3>\d\d)\b",
    re.I,
)

# A MONTH AND YEAR ("In June 2022", "our February 2023 1-for-400 share consolidation") dates a ratio only as its
# OWN date, to say it is a PAST split's (``_own_clause``) — never a split's date: it names no day. Read as the
# month's LAST day, the latest it can be. Capitalized only (never the verb "may").
_MONTH_YEAR = re.compile(
    r"\b(?P<m>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<y>(?:19|20)\d\d)\b"
)
# a ratio's OWN date is its sentence's nearest date at most this many characters from it; its clause reaches this
# far before the two (never past another date or ratio)
_OWN_DATE_REACH = 150
_CLAUSE_LEAD = 120

_TRADING_WORD = re.compile(r"\btrad(?:e|es|ed|ing)\b", re.I)
# A SESSION cue names the first session's own moment, so in a sentence about the split it is a trading date
# without the word "trade": "the Reverse Stock Split, which was effective on the OTC Markets at the open of
# business on December 15, 2025" (COPR's 8.01 8-K of 2025-12-18, read as a LEGAL date until 2026-11-25)
_SESSION = (
    r"market\s+opens?\b|markets\s+open|market\s+opening|opening\s+of\s+(?:the\s+)?(?:market|trading|business)|"
    r"open\s+of\s+(?:the\s+)?(?:market|business|trading)|commencement\s+of\s+trading"
)
_SESSION_CUE = re.compile(_SESSION, re.I)
# … but never a plan the sentence says was changed: "… a reverse stock split at a ratio of 1-for-6.25, which was
# intended to become effective at the opening of the market on January 20, 2026, however, … the Company … elected
# to delay the effectiveness …" (PBM's 6-K of 2026-01-29; its first session is the 02-02 its next sentence states)
_PLAN_CHANGED = re.compile(
    r"\b(?:delay(?:s|ed|ing)?|postpon\w*|intended\s+to|originally\s+(?:scheduled|expected|planned|intended))\b",
    re.I,
)
# "The first day of trading under the new ratio is expected to be October 29, 2025" (NVA's 6-K of 2025-10-15)
_FIRST_SESSION = (
    r"\bfirst\s+(?:trading\s+)?(?:day|date|session)\s+of\s+trading\b(?=[^.;]{0,160}?\b(?:(?:is|are)\s+"
    r"(?:expected|anticipated|scheduled|planned)\s+to\s+be|will\s+be)\b)"
)
_TRADING_CUE = re.compile(
    r"split[\s-]*adjusted|adjusted\s+basis|post[\s-]+(?:reverse[\s-]+)?(?:stock[\s-]+)?split|"
    rf"post[\s-]+consolidat\w*|consolidated\s+basis|split\s+basis|{_SESSION}|{_FIRST_SESSION}",
    re.I,
)
# … but a trading date comes only from a sentence ABOUT the split (``_SPLIT_CUE``) or one carrying a cue only a
# split has (2026-11-26): "The new corporate name, ticker symbol "CD," and website … all went live on November 13,
# 2025, at the opening of trading on the Nasdaq Global Market" (CD's interim 6-K, a ticker change) and "On January 6,
# 2026, the WTMA Common Stock began trading on the Nasdaq Global Market as EMAT Common Stock" (a listing) name no
# split's first session. The shares trading under their NEW CUSIP / ISIN or a NEW (ADS) ratio are the split's: "…
# expected to commence trading on Nasdaq when the market opens on October 21, 2026 under the symbol "POM" with the
# new CUSIP number" (POM's 6-K), "The first day of trading under the new ratio is expected to be October 29, 2025"
# (NVA's), "… under the new CUSIP 776105108 when markets open on August 25, 2026" (REAX's). So are shares trading
# on a split- / consolidation-ADJUSTED basis — "beginning with the opening of trading on November 3, 2025, the
# Company's class A ordinary shares will trade … on a consolidation-adjusted basis" (Chijet's 6-K) — and ADSs under
# their ADS-to-share ratio: "The new ADS to ordinary share ratio of 1 for 600 will be effective prior to the
# commencement of trading … on Thursday, January 30, 2025" (BioLineRx's), "The new 1-to-1 ADS to ordinary share
# ratio will take effect prior to the commencement of trading on the NASDAQ on Tuesday, March 3, 2026" (Brainsway's)
_SPLIT_TRADING_CUE = re.compile(
    r"(?:split|consolidation|combination|subdivision)[\s-]*adjusted|"
    r"post[\s-]+(?:reverse[\s-]+)?(?:stock[\s-]+)?split|post[\s-]+consolidat\w*|"
    r"consolidated\s+basis|\bnew\s+(?:CUSIP|ISIN)\b|\bnew\s+(?:ADS\s+|ADR\s+)?ratio\b|"
    r"\bAD[SR]s?[\s-]+to[\s-]+(?:ordinary[\s-]+|common[\s-]+)?shares?[\s-]+ratio\b|"
    r"\b(?:ordinary[\s-]+|common[\s-]+)?shares?[\s-]+to[\s-]+AD[SR]s?[\s-]+ratio\b",
    re.I,
)
# The LAST session at the old price is never the first at the new one: "The last day of trading of the pre-split
# shares … is planned to be Tuesday, January 28, 2025", "… January 28, 2025 (the “Effective Date” and the last
# trading day of the prior ISIN / CUSIP / WKN)" (QGEN's 6-K of 2025-01-16) — a sentence naming it gives no trading
# date
_LAST_SESSION = re.compile(
    r"\b(?:last|final)\s+(?:trading\s+(?:day|date|session)|(?:day|date|session)\s+(?:of|for)\s+trading)\b",
    re.I,
)
# "the first date when the Company's ordinary shares will begin trading … after implementation of the reverse
# split will be Monday, October 5, 2026" (CMND, PLRZ) — a start-of-trading phrase, only in a sentence about a split
_TRADING_START = re.compile(r"\b(?:begin|commence|start)s?\s+(?:to\s+)?trad(?:e|ing)\b", re.I)
# "effective as of 5:00 p.m. … on …", "will take effect on …", "Effective June 12, 2025, … filed …" (ERNA),
# "… be consolidated into one (1) class A ordinary share …, with effect from 27 July 2026 (the “Effective Date”)"
# (ZNB's 6-K of 2026-08-25 — the British phrasing; without it the first date after "Effective Date" was read, a
# board resolution's 1 July 2026)
_LEGAL_CUE = re.compile(
    r"\beffective\b|takes?\s+effect|took\s+effect|with\s+effect\s+from|effected\b[^.;]{0,120}?\bon\b",
    re.I,
)

# A date a MEETING was held on dates the meeting, never a split: "… adopted by special resolution passed at
# extraordinary general meeting of shareholders of the Company held on 10 September 2026 … being the opening of
# trading on the Market Effective Date" (ZNB's charter exhibit, read as the first trading session until
# 2026-10-24), "Announcement of Notice Convening Extraordinary General Meeting on September 10, 2026". The words
# between "meeting" and the date never name the split's own event (an effect, trading, the split itself): "at the
# meeting, … the reverse split, which will become effective on March 2" keeps March 2.
_MEETING_BEFORE = re.compile(
    r"\b(?:meeting|EGM|AGM)\b(?:(?!\b(?:effect\w*|trad\w*|split\w*|consolidat\w*)\b)[^.;]){0,80}?"
    r"\b(?:held\s+|convened\s+)?on\s+(?:the\s+)?$",
    re.I,
)
_MEETING_LOOKBACK = 120

# A date that NAMES SOMETHING ELSE never dates a split (``_dates(dating=True)``) — each was read as one until
# 2026-11-25:
# - a period end: "… annual report for the year ended December 31, 2025" (IMCC's risk factors, beside its first
#   session), "not yet effective for the period ended September 30, 2026" (PBM's interim notes), each date of
#   "the three months ended September 30, 2026 and September 30, 2025";
_DATE_WORDS = (
    rf"(?:{_MONTH}\s+\d{{1,2}}(?:\s?(?:st|nd|rd|th))?\s?,?\s+(?:19|20)\d\d|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+(?:19|20)\d\d)"
)
_PERIOD_END_BEFORE = re.compile(
    r"\b(?:years?|quarters?|periods?|months|weeks|half[\s-]years?)\s+(?:then\s+)?(?:ended|ending)\s+(?:on\s+)?"
    rf"(?:{_DATE_WORDS}\s*,?\s*(?:and\s+)?)*$",
    re.I,
)
# - the release's own date: its dateline — a place, the date, then a wire service and/or a dash before the
#   issuer's name: "Nanjing, China, October 22, 2026 /PRNewswire/ -- Scage Future" (SCAG's 6-K, in a headline
#   naming the "Effective Date"), "Boise, Idaho, December 16, 2025 (GLOBE NEWSWIRE) — Idaho Copper" (COPR),
#   "LAKEWOOD, N.J., May 14, 2026 — Reliance" — and "On August 26, 2026, the Company issued a press release",
#   "Press Release dated December 16, 2025";
_DATELINE_BEFORE = re.compile(r"(?:,|\s[-–—]{1,2})\s*$")
_WEEKDAY_BEFORE = re.compile(r"\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s*$", re.I)
_WIRE = r"[(/]\s*(?:[A-Z][A-Za-z]*\s+)*?(?:[A-Za-z]*(?:WIRE|[Ww]ire)|Newsfile|CNW|EQS[\w-]*)(?:[-\s][A-Za-z]+)?\s*[)/]"
_DATELINE_AFTER = re.compile(
    rf"\s*(?:{_WIRE}|[-–—]{{1,2}}\s*(?:{_WIRE}\s*[-–—]{{0,2}}\s*)?(?=[A-Z]))"
)
_PRESS_RELEASE_DATED = re.compile(r"\bpress\s+release\b[^;]{0,80}?\bdated\s+(?:as\s+of\s+)?$", re.I)
_ON_BEFORE = re.compile(r"\b[Oo]n\s+$")
_ISSUED_A_RELEASE = re.compile(
    r",?\s+[^;]{0,120}?\b(?:issued|released|published|disseminated|distributed)\s+(?:a|its|the)\s+press\s+release\b",
    re.I,
)
# - a deadline: "… the ability to regain compliance … prior to the June 10, 2026 compliance deadline" (EZRA's press
#   release, in a forward-looking statement beside "post-split trading price");
_DEADLINE_AFTER = re.compile(r"\s*(?:[\w-]+\s+){0,2}?deadline\b", re.I)
_DEADLINE_BEFORE = re.compile(r"\bdeadline\s+(?:of|on|is|was|date\s+(?:of|is))\s+$", re.I)
# - an accounting standard NOT YET EFFECTIVE — the whole sentence dates nothing: "Accounting Standards Issued But
#   Not Yet Effective A number of new standards … are not yet effective for the period ended September 30, 2026"
#   (PBM's 6-K of 2026-11-19: read as its split's legal date).
_NOT_YET_EFFECTIVE = re.compile(r"\bnot\s+yet\s+effective\b", re.I)
# … and since 2026-11-26 (the 800-day check's misreads):
# - a trading SUSPENSION, delisting or halt: "… trading in the Company's securities is expected to be suspended at the
#   opening of business on July 21, 2026" (CYCU's delisting notice, beside its past 1-for-30), "with trading of the
#   Common Stock formally suspended at the opening of business on December 2, 2024" (VRM), "will be suspended at the
#   opening of business on July 18, 2024" (SKYA's offering circular) — each read as a first session. Only when the
#   words between name no split, no resumption and no other date: "halted on March 2, 2026 and resumed on a
#   split-adjusted basis at the market open on March 4, 2026" still dates March 4;
_SUSPENDED_BEFORE = re.compile(
    r"\b(?:suspen(?:d|ds|ded|ding|sion)|delist(?:s|ed|ing)?|halt(?:s|ed|ing)?)\b"
    r"(?:(?!\b(?:split\w*|consolidat\w*|resum\w*|(?:19|20)\d\d)\b)[^.;]){0,100}$",
    re.I,
)
# - a RESOLUTION's own date: "the authority granted by the special resolution passed on 19 June 2025 be hereby
#   extended" (PMEC's proxy 6-K of 2025-09-02, renewing an unused authority: read as its 1-for-6's legal date);
_RESOLUTION_BEFORE = re.compile(
    r"\bresolutions?\b(?:(?!\b(?:effect\w*|trad\w*|split\w*|consolidat\w*)\b)[^.;]){0,60}?"
    r"\b(?:passed|adopted)\b(?:(?!\b(?:effect\w*|trad\w*|split\w*|consolidat\w*)\b)[^.;]){0,60}?"
    r"\bon\s+(?:the\s+)?$",
    re.I,
)
# - a COUNT's date: "the following securities issued and outstanding as of April 18, 2025 Prior to Reverse Split
#   281,947,151 shares" (HLYK's offering circular of 2025-04-30, in a run-on sentence saying "the Reverse Split is
#   effective") — "outstanding as of", never "effective as of".
_OUTSTANDING_AS_OF = re.compile(r"\boutstanding\s+as\s+(?:of|at)\s+$", re.I)
_STANDARD = re.compile(
    r"\b(?:accounting|standards?|pronouncements?|ASUs?|IFRSs?|IAS|GAAP|ASC|interpretations?)\b",
    re.I,
)

# The SUPERSEDED ratio (DFNS's 8-K of 2026-07-16): a filing that REVISES a ratio it disclosed before restates
# the old one beside the new — "approved a reverse stock split … at a ratio of 1-for-50 as disclosed on the
# Current Report on Form 8-K filed … on July 13, 2026" then "has determined to increase the amount of the reverse
# stock split … at a ratio of 1-for-125". Only when EXACTLY ONE ratio is stated in a sentence that revises one
# (and cites no earlier disclosure), and EVERY other ratio only in sentences citing an earlier disclosure, is the
# revised ratio the split's; anything else is still two ratios (abstain).
_REVISION_CUE = re.compile(
    r"\b(?:increas|decreas|chang|amend|revis|modif)(?:e|es|ed|ing|y|ies|ied)?\s+(?:the\s+)?"
    r"(?:(?:amount|ratio|size)\s+of|(?:reverse\s+)?(?:stock\s+|share\s+)?split\s+ratio)\b",
    re.I,
)
_PRIOR_DISCLOSURE = re.compile(
    r"\b(?:as\s+(?:previously\s+|earlier\s+|initially\s+)?(?:disclosed|reported|announced)|"
    r"previously\s+(?:disclosed|reported|announced))\b",
    re.I,
)

# A sentence that says the split WILL happen cannot be a past split's, whatever date it carries: MEDS's 8-K of
# 2026-05-21 says "The Reverse Stock Split will become effective … on May 26, 2025" — the filer's typo for 2026
# (its own press release says May 26, 2026).
_FUTURE = re.compile(
    r"\b(?:will|shall)\b|\bexpect(?:s|ed)?\s+to\b|\banticipat\w*\s+to\b|\bintends?\s+to\b", re.I
)

# A KNOWN split (2026-11-25) — one the caller holds as CONFIRMED on the split list for this security, in effect by
# the filing's date (``KnownSplit``). A filing restating it is no news, however recent: GCTK's 8-K of 2026-10-22
# says "On August 28, 2026, the Company implemented a 1-for-15 reverse stock split" (55 days back, inside
# ``HISTORICAL_GAP_DAYS``) beside its new 1-for-2 and abstained on the two; GPUS's 8-K of 2024-11-26 restates
# "… the one-for-thirty-five reserve stock split that was effectuated on November 25, 2024" (the day before), and
# its abstain kept a distribution from being set aside; PBM's interim 6-K of 2026-11-19 ("Comparative loss per
# share has been restated to reflect the 6.25:1 share consolidation completed on February 2, 2026", a run-on
# table sentence whose other dates are current) and Trio-Tech's 10-Q ("On January 5, 2026, the Company effected a
# two-for-one forward stock split", in its tables) read as splits with no date. So a mention of a known ratio
# (exactly, either way round), in a sentence — a run-on one: the window around the ratio — that states it as DONE,
# is that past split's. Never when the sentence dates it more than ``KNOWN_DATE_TOLERANCE_DAYS`` from the known
# split's first session (another split of the same ratio), nor when the filing's own date is within that of it
# (the filing IS that split's evidence: re-reading it changes nothing). GCTK's "August 28" is its legal date; the
# first session was August 31.
KNOWN_DATE_TOLERANCE_DAYS = 10
_DONE = re.compile(
    r"\b(?:implemented|effected|effectuated|completed|consummated|became\s+effective|took\s+effect|"
    r"(?<!\bnot\s)(?<!\bnot\syet\s)occurred|"
    r"restated\s+to\s+reflect|(?:retroactively|retrospectively)\s+(?:adjusted|restated|applied)|"
    r"adjusted\s+to\s+reflect|as\s+adjusted\s+for|"
    r"giving\s+(?:retroactive\s+|retrospective\s+)?effect\s+to|"
    r"giv(?:e|es|en)\s+(?:retroactive|retrospective)\s+effect\s+to|"
    r"(?:as\s+)?(?:previously|earlier)\s+(?:disclosed|reported|announced))\b",
    re.I,
)
# … never what is still to come: "to be effected through the filing of an amendment" (Trio-Tech's 8-K of
# 2025-12-19, announcing the split), "will be implemented", "being effected", "will be proportionately adjusted" —
# nor any clause that says what WILL happen (``_FUTURE``: "after giving effect to the reverse split, the Company
# will have …" announces one)
_NOT_YET_BEFORE = re.compile(
    r"\b(?:(?:to|will|shall|would|may|might|can|could|must)\s+(?:not\s+)?be|being)\s+(?:\w+ly\s+)?$",
    re.I,
)

# An ADS TERMINATION (2026-11-25): the depositary cancels the ADSs and hands each holder ordinary shares — "each
# holder of ten (10) ADSs will receive three (3) post-Share Consolidation Class A ordinary shares" (POM's 6-K of
# 2026-10-19, beside its ordinary shares' own 10-for-1 consolidation). The listed price then moves by the
# EXCHANGE's factor (10/3), never the ordinary shares' ratio, so a filing that terminates its ADR program
# abstains: the operator confirms the ADS factor from cited values. A termination stated only as a possibility
# ("if we terminate our ADR program") is not one.
ADS_TERMINATION = (
    "ADS termination: the exchange sets the ADS price factor — confirm it with cited values"
)
_ADS_PROGRAM_ENDS = re.compile(
    r"\bterminat(?:e|es|ed|ing|ion)\s+(?:of\s+)?(?:the\s+|its\s+|our\s+|their\s+)?"
    rf"(?:(?:sponsored|related)\s+)?{_ADS_WORD}\s+(?:program|programme|facility)\b",
    re.I,
)
_ADS_EXCHANGE = re.compile(
    rf"\beach\s+holder\s+of\s+{_num('a')}\s+{_ADS_WORD}\s+(?:will|shall|would)\s+receive\s+"
    rf"{_num('b')}(?![\w]|[.,]\d)",
    re.I,
)
# the defined term, in a sentence about ADSs
_MANDATORY_EXCHANGE = re.compile(r"\bMandatory\s+Exchange\b")
_ADS_MENTION = re.compile(r"\bADSs?\b|\bADRs?\b|\bAmerican\s+Depositary\b", re.I)
_CONDITIONAL = re.compile(r"\b(?:if|may|might|could|whether|in\s+the\s+event)\b", re.I)

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
# THE WIDE FORMS (the targeted reader, 2026-10-24): the periodic reports, ADR registrations, prospectuses and
# information statements that state a split no 8-K / 6-K announced (LGHL's ADS ratio changes in F-6 POS
# filings, OPTH's 424B4, MVNC's DEF 14C, SDCO's 10-K, DFNS's 10-Q). Read ONLY on the targeted path
# (``relevant_documents(wide=True)``, a split with no agreeing filing) and ALWAYS through the past-date screen
# (``extract_split_screened``): a periodic report restates old splits — OFAL's 10-K and MI's 20-F read an old
# split as current without it (MEASURED 2026-10-24).
WIDE_KEEP_TYPES = (
    "10-Q",
    "10-K",
    "20-F",
    "40-F",
    "F-6",
    "424B3",
    "424B4",
    "DEF 14C",
    "PRE 14C",
)

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


@dataclass(frozen=True)
class KnownSplit:
    """A split the caller holds as CONFIRMED on the split list for the filing's security, its first session on or
    before the filing's date (``extract_split(known=...)``; the module docstring's KNOWN split). ``ratio_from`` ->
    ``ratio_to`` is old -> new shares, as the list stores it."""

    ratio_from: Decimal
    ratio_to: Decimal
    first_session: date


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


def relevant_documents(docs: list[FilingDocument], *, wide: bool = False) -> list[FilingDocument]:
    """The documents a split is announced in: the form's own (8-K / 6-K / 1-A / 1-U, a 1-A's ``PART II AND
    III``), the ``EX-99.x`` press releases, and a lone untyped document. ``wide`` (the targeted reader only)
    also keeps the WIDE forms' own documents (``WIDE_KEEP_TYPES``: a 10-Q, a 20-F, an F-6 POS, a 424B4 …).
    """
    types = _KEEP_TYPES + WIDE_KEEP_TYPES if wide else _KEEP_TYPES
    keep = [d for d in docs if d.type.upper().startswith(types) or d.type == "HTML"]
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


def _dates(text: str, *, dating: bool = False) -> list[tuple[int, date]]:
    """``(where, date)`` for every date in ``text`` — except a date a MEETING was held on (``_MEETING_BEFORE``),
    which never dates a split. ``dating`` (the dates a cued sentence may give the split) also drops a date that
    names something else (``_names_something_else``)."""
    out = []
    for m in _DATE.finditer(text):
        d = _parse_date(m)
        if d is None:
            continue
        before = text[max(0, m.start() - _MEETING_LOOKBACK) : m.start()]
        if _MEETING_BEFORE.search(before):
            continue
        if dating and _names_something_else(before, text[m.end() : m.end() + 160]):
            continue
        out.append((m.start(), d))
    return out


def _date_spans(text: str) -> list[tuple[int, int, date]]:
    """``(start, end, date)`` for every date in ``text`` that may date a split (as ``_dates(dating=True)``) and every
    MONTH-YEAR one (``_MONTH_YEAR``, read as the month's last day; never part of a full date: "28 February 2023").
    """
    out: list[tuple[int, int, date]] = []
    full: list[tuple[int, int]] = []
    for m in _DATE.finditer(text):
        full.append((m.start(), m.end()))
        d = _parse_date(m)
        before = text[max(0, m.start() - _MEETING_LOOKBACK) : m.start()]
        if d is None or _MEETING_BEFORE.search(before):
            continue
        if not _names_something_else(before, text[m.end() : m.end() + 160]):
            out.append((m.start(), m.end(), d))
    for m in _MONTH_YEAR.finditer(text):
        if any(s < m.end() and m.start() < e for s, e in full):
            continue
        before = text[max(0, m.start() - _MEETING_LOOKBACK) : m.start()]
        if _MEETING_BEFORE.search(before) or _names_something_else(
            before, text[m.end() : m.end() + 160]
        ):
            continue
        y, mo = int(m.group("y")), _MONTHS[m.group("m")[:3].lower()]
        out.append((m.start(), m.end(), date(y, mo, calendar.monthrange(y, mo)[1])))
    return sorted(out)


def _own_clause(
    sent: str, start: int, end: int, others: list[tuple[int, int]]
) -> tuple[date, str] | None:
    """The ratio at ``sent[start:end]``'s OWN date and clause: its sentence's NEAREST date (full or month-year,
    ``_date_spans``), at most ``_OWN_DATE_REACH`` characters away with no other ratio (``others``) between — else
    None; the clause runs from the earlier of the two back to the previous date or ratio (at most ``_CLAUSE_LEAD``
    characters) to the later one's end: "Because the Company effected a 1-for-30 reverse stock split on October 27,
    2025", "In June 2022, the Company implemented a 1-for-10"."""
    spans = _date_spans(sent)
    best: tuple[int, int, int, date] | None = None
    for ds, de, d in spans:
        gap = ds - end if ds >= end else start - de
        if gap < 0 or gap > _OWN_DATE_REACH:
            continue
        if best is None or gap < best[0]:
            best = (gap, ds, de, d)
    if best is None:
        return None
    _, ds, de, d = best
    lo, hi = (end, ds) if ds >= end else (de, start)
    if any(lo <= rs < hi for rs, _ in others):
        return None  # another ratio sits between: the date is that one's
    a, b = min(start, ds), max(end, de)
    bounds = [e for _, e, _ in spans if e <= a] + [re_ for _, re_ in others if re_ <= a]
    return d, sent[max([0, a - _CLAUSE_LEAD, *bounds]) : b]


def _names_something_else(before: str, after: str) -> bool:
    """Is the date between ``before`` and ``after`` a period end, the release's own date, a deadline, a trading
    suspension's, a resolution's or a share count's — never a split's date?"""
    if _PERIOD_END_BEFORE.search(before) or _PRESS_RELEASE_DATED.search(before):
        return True
    if (
        _SUSPENDED_BEFORE.search(before)
        or _RESOLUTION_BEFORE.search(before)
        or _OUTSTANDING_AS_OF.search(before)
    ):
        return True
    if _DEADLINE_BEFORE.search(before) or _DEADLINE_AFTER.match(after):
        return True
    if _ON_BEFORE.search(before) and _ISSUED_A_RELEASE.match(after):
        return True
    return bool(
        _DATELINE_BEFORE.search(before)
        and not _WEEKDAY_BEFORE.search(before)
        and _DATELINE_AFTER.match(after)
    )


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
    kind: str  # operative | range | historical | conflict | undirected | superseded
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
    if not rev and _forward_in_sentence(sentence):
        return "forward"  # "… with a proportionate increase in … authorized" (NOW)
    return doc_direction


def _forward_in_sentence(sentence: str) -> bool:
    """Does the sentence say its split is a forward one without the words "forward split"
    (``_FORWARD_SENTENCE_CUE``, never negated: "without a proportionate increase")?"""
    return any(
        not _NEGATED_BEFORE.search(sentence[max(0, m.start() - 30) : m.start()])
        for m in _FORWARD_SENTENCE_CUE.finditer(sentence)
    )


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


def _plausible(v: Decimal | None, *, no_year: bool) -> bool:
    """Can ``v`` be one side of a split ratio? ``no_year``: a year (1900-2100) is not — a bare "2025 for 2026" is
    years, and so is the "2013" of "Figure 9-2: 2013" (a colon ratio, NMG)."""
    if v is None or v <= 0 or v > 100000:
        return False
    return not (no_year and v == v.to_integral_value() and 1900 <= v <= 2100)


def _mentions(docs: list[_Doc], doc_direction: str | None) -> list[_Mention]:
    out: list[_Mention] = []
    for di, d in enumerate(docs):
        for s, e in d.spans:
            sent = d.match[s:e]
            about = _SPLIT_CUE.search(sent) is not None
            extra = [
                m
                for m in _ADDITIONAL_EACH.finditer(sent)
                if about or _HELD.search(sent, m.start(), m.end() + _HELD_REACH)
            ]
            if not about and not extra:
                continue
            direction = _direction(sent, doc_direction)
            found: list[tuple[int, int, Decimal | None, Decimal | None, str, str]] = []
            for m in extra:
                # "A additional shares for each share": one old share -> 1 + A new (a forward split)
                a, ca = _side(m, "a")
                if not _plausible(a, no_year=False):
                    continue
                kind = "conflict" if ca else "explicit"
                found.append((m.start(), m.end(), Decimal(1), a + 1, kind, m.group(0)))  # type: ignore[operator]
            scan = sent if about else ""  # a sentence with no split word states only that form
            for m in _EVERY_INTO.finditer(scan):
                if not _COMBINE_VERB.search(m.group("fill")):
                    continue
                (a, ca), (b, cb) = _side(m, "a"), _side(m, "b")
                if not (_plausible(a, no_year=False) and _plausible(b, no_year=False)):
                    continue
                kind = "conflict" if ca or cb else "explicit"
                if kind == "explicit" and _EVERY_RANGE.match(m.group("fill")):
                    # "every two (2) to two hundred and fifty (250) Ordinary Shares shall be converted into
                    # one (1)" (MTEK's proxy 6-K) — the low end of an authorized range, not the ratio set
                    kind = "range"
                found.append((m.start(), m.end(), a, b, kind, m.group(0)))
            for m in _FOR_EVERY.finditer(scan):
                (a, ca), (b, cb) = _side(m, "a"), _side(m, "b")
                if not (_plausible(a, no_year=False) and _plausible(b, no_year=False)):
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
            for m in [*_FOR.finditer(scan), *_TO_BASIS.finditer(scan)]:
                if any(fs <= m.start() < fe for fs, fe, *_ in found):
                    continue  # already read as "N share for every M"
                plain = m.group("sep").strip().lower() in ("for", "to")
                (a, ca), (b, cb) = _side(m, "a"), _side(m, "b")
                if not (_plausible(a, no_year=plain) and _plausible(b, no_year=plain)):
                    continue
                if ca or cb:
                    found.append((m.start(), m.end(), a, b, "conflict", m.group(0)))
                    continue
                o = _oriented(a, b, direction)  # type: ignore[arg-type]
                if o is None:
                    found.append((m.start(), m.end(), a, b, "undirected", m.group(0)))
                else:
                    found.append((m.start(), m.end(), o[0], o[1], "explicit", m.group(0)))
            for m in _COLON.finditer(scan):
                before = sent[max(0, m.start() - 40) : m.start()]
                after = sent[m.end() : m.end() + 30]
                if not (_COLON_CUE_BEFORE.search(before) or _COLON_AFTER.match(after)):
                    continue
                a, b = _digits(m.group("ad")), _digits(m.group("bd"))
                if not (_plausible(a, no_year=True) and _plausible(b, no_year=True)):
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
                if kind in ("explicit", "undirected") and _RANGE_CUE.search(look):
                    # a ceiling is one whichever way it points ("no more than 4.0:1.0")
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
    about trading — or, in a sentence about the split, a SESSION cue): its only date, or the first date after the
    cue when it has several. A trading sentence is one about the split, or one carrying a cue only a split has
    (``_SPLIT_TRADING_CUE``). A date that names something else is none (``_dates(dating=True)``); a sentence about
    an accounting standard not yet effective gives no date, one naming the LAST session no trading date.
    """
    out = []
    for di, d in enumerate(docs):
        for s, e in d.spans:
            sent = d.match[s:e]
            about = _SPLIT_CUE.search(sent) is not None
            cm = cue.search(sent)
            if cm is None and need_trading and about:
                cm = _TRADING_START.search(sent)  # "begin trading … after … the reverse split"
            if cm is None:
                continue
            if need_trading and not (about or _SPLIT_TRADING_CUE.search(sent)):
                continue  # a ticker change, a listing: trading, but no split's (CD, EMAT)
            if need_trading and not (
                _TRADING_WORD.search(sent)
                or (about and _SESSION_CUE.search(sent) and not _PLAN_CHANGED.search(sent))
            ):
                continue
            if need_trading and _LAST_SESSION.search(sent):
                continue  # the last session at the old price (QGEN)
            if not need_trading and not about:
                continue
            if _NOT_YET_EFFECTIVE.search(sent) and _STANDARD.search(sent):
                continue  # an accounting standard's effective date (PBM)
            ds = _dates(sent, dating=True)
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
    states (``pos``) — joined with " … ". The label names the first part's document. Two parts in the SAME
    run-on sentence each get their window when they are too far apart for one to show the other (ZNB's 6-K cover:
    the ratio and "with effect from 27 July 2026" sit ~1,000 characters apart in one sentence)."""

    def same(p: tuple[int, tuple[int, int], int], q: tuple[int, tuple[int, int], int]) -> bool:
        if (p[0], p[1]) != (q[0], q[1]):
            return False
        s, e = p[1]
        return e - s <= _LONG_SENTENCE or abs(p[2] - q[2]) <= _WINDOW_AFTER

    seen: list[tuple[int, tuple[int, int], int]] = []
    for p in parts:
        if not any(same(p, q) for q in seen):
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


# ``PAST_RATIO_ONLY`` (``domain.splits``) is the abstain reason for a filing whose every ratio is a PAST split's
# (it restates them) — no news to the nightly (``pipeline/detect_splits._NOT_NEWS``), and never a split's
# evidence (``domain.splits.decide``).
# the start of the abstain reason for a filing that states only an authorized range
RANGE_ONLY = "only an authorized range of ratios"

_Part = tuple[
    int, tuple[int, int], int
]  # (document, sentence span, where) — one piece of the passage


@dataclass
class _Read:
    """One reading with what the screen needs to combine two of them: the documents, the passage parts of
    what it states (the ratio, or what it abstained on) apart from its date's, the date's part, and how many
    distinct operative ratios were left (0 = every ratio was set aside: past, a range, unreadable).
    """

    x: SplitExtraction
    docs: list[_Doc]
    parts: list[_Part]
    date_at: _Part | None
    operative: int


def _documents(documents: list[FilingDocument], *, wide: bool) -> list[_Doc]:
    docs: list[_Doc] = []
    for fd in relevant_documents(documents, wide=wide):
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
    return docs


def _superseded(docs: list[_Doc], distinct: dict[Fraction, list[_Mention]]) -> Fraction | None:
    """The one ratio a filing REVISES, when every other ratio it states sits only in sentences citing an
    earlier disclosure (``_REVISION_CUE`` / ``_PRIOR_DISCLOSURE``) — else ``None`` (still two ratios).
    """

    def sentence(m: _Mention) -> str:
        s, e = m.sentence
        return docs[m.doc].match[s:e]

    revised = [
        k
        for k, ms in distinct.items()
        if any(
            _REVISION_CUE.search(sentence(m)) and not _PRIOR_DISCLOSURE.search(sentence(m))
            for m in ms
        )
    ]
    if len(revised) != 1:
        return None
    others = [ms for k, ms in distinct.items() if k != revised[0]]
    if all(_PRIOR_DISCLOSURE.search(sentence(m)) for ms in others for m in ms):
        return revised[0]
    return None


def _ads_termination(docs: list[_Doc]) -> _Part | None:
    """Where a filing states that its ADR program ENDS (``ADS_TERMINATION``): the exchange sentence ("each holder
    of N ADSs will receive M …") when it has one, else the first sentence terminating the program or naming the
    "Mandatory Exchange" of ADSs — never one that only supposes it ("if", "may")."""
    first: _Part | None = None
    for di, d in enumerate(docs):
        for s, e in d.spans:
            sent = d.match[s:e]
            m = _ADS_EXCHANGE.search(sent)
            if m:
                return (di, (s, e), s + m.start())
            if first is not None or _CONDITIONAL.search(sent):
                continue
            m = _ADS_PROGRAM_ENDS.search(sent) or (
                _MANDATORY_EXCHANGE.search(sent) if _ADS_MENTION.search(sent) else None
            )
            if m:
                first = (di, (s, e), s + m.start())
    return first


def _states_done(clause: str) -> bool:
    """Does the clause state a split as DONE (``_DONE``; never "to be effected", ``_NOT_YET_BEFORE``, and never in
    a clause about what WILL happen, ``_FUTURE``)?"""
    if _FUTURE.search(clause):
        return False
    return any(
        not _NOT_YET_BEFORE.search(clause[max(0, m.start() - 40) : m.start()])
        for m in _DONE.finditer(clause)
    )


def _set_known_aside(
    docs: list[_Doc],
    mentions: list[_Mention],
    known: tuple[KnownSplit, ...],
    effective: date | None,
) -> None:
    """Mark ``historical`` the mentions of a KNOWN split's ratio (the module docstring) its filing states as DONE
    (``_states_done``; in a run-on sentence, the window around the ratio) — and, in a filing that dates nothing
    current of its own (``effective`` is None: it only restates), every other mention of that ratio not about what
    WILL happen too: CMND's interim 6-K restates its 1-for-40 as "effected" in one sentence and as "holders …
    received 0.025 of a common share for every one" in another. A filing announcing a NEW split of the same ratio
    keeps every mention it does not state as done. Never a mention whose sentence dates it away from the known
    split (another split of the same ratio), nor any when the filing's own date is the known split's
    (``KNOWN_DATE_TOLERANCE_DAYS`` either way: the filing is its evidence).
    """

    def near(a: date, b: date) -> bool:
        return abs((a - b).days) <= KNOWN_DATE_TOLERANCE_DAYS

    def clause(mt: _Mention) -> str:
        s, e = mt.sentence
        if e - s > _LONG_SENTENCE:
            s, e = max(s, mt.pos - _WINDOW_BEFORE), min(e, mt.pos + _WINDOW_AFTER)
        return docs[mt.doc].match[s:e]

    for k in known:
        if Decimal(k.ratio_from) <= 0 or Decimal(k.ratio_to) <= 0 or k.ratio_from == k.ratio_to:
            continue
        if effective is not None and near(effective, k.first_session):
            continue  # the filing dates THIS split as its own event: it is its evidence, not a restatement
        factor = Fraction(k.ratio_from) / Fraction(k.ratio_to)
        same: list[tuple[_Mention, str]] = []
        for mt in mentions:
            if mt.kind not in ("operative", "undirected") or mt.key() not in (factor, 1 / factor):
                continue
            c = clause(mt)
            ds = [x for _, x in _dates(c)]
            if ds and not any(near(x, k.first_session) for x in ds):
                continue  # dated, but away from the known split: another split of the same ratio
            same.append((mt, c))
        if not any(_states_done(c) for _, c in same):
            continue  # the filing never says the known split is done: it is not restating it
        for mt, c in same:
            if _states_done(c) or (effective is None and not _FUTURE.search(c)):
                mt.kind = "historical"


def _read(
    documents: list[FilingDocument],
    *,
    filed: date | None,
    wide: bool,
    strict: bool,
    known: tuple[KnownSplit, ...] = (),
) -> _Read:
    """The extraction (``extract_split`` / ``extract_split_screened``). ``filed`` screens dates against the
    filing date; ``strict`` (the restatement screen's second reading) also dates a ratio stated in an undated
    sentence by the date sentence beside it, and never reads a sentence about what WILL happen as a past
    split's. ``known``: the KNOWN splits (``_set_known_aside``)."""
    docs = _documents(documents, wide=wide)
    if not docs:
        return _Read(SplitExtraction(abstain_reason="no readable document"), [], [], None, 0)

    # the date first — the past-split screen measures against it
    trading = _cued_dates(docs, _TRADING_CUE, need_trading=True)
    legal = _cued_dates(docs, _LEGAL_CUE, need_trading=False)
    # a trading date that is no session (a weekend, an NYSE holiday: ``domain.trading_days``) is no first session —
    # "The Reverse Stock Split was effective at 11:59 p.m., Eastern Time, on April 27, 2025 and began trading on a
    # post-split basis" (SKYA's charter 8-K: a Sunday) dates the split's legal effect, and is read with those
    legal = legal + [c for c in trading if not is_scheduled_day(c[0])]
    trading = [c for c in trading if is_scheduled_day(c[0])]
    cued_by_sentence: dict[tuple[int, tuple[int, int]], list[date]] = {}
    for c in trading + legal:
        cued_by_sentence.setdefault((c[1], c[2]), []).append(c[0])
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
    date_at: _Part | None = None
    for cand, k in ((trading, "trading"), (legal, "legal")):
        if not cand:
            continue
        distinct_dates = sorted({c[0] for c in cand})
        if len(distinct_dates) == 1:
            effective, kind = distinct_dates[0], k
            date_at = (cand[0][1], cand[0][2], cand[0][3])
        else:
            date_note = f"{len(distinct_dates)} different {k} dates stated: " + ", ".join(
                x.isoformat() for x in distinct_dates
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
    index = {(di, sp): i for di, d in enumerate(docs) for i, sp in enumerate(d.spans)}
    ratios_at: dict[tuple[int, tuple[int, int]], list[tuple[int, int]]] = {}
    for mt in mentions:
        span = (mt.pos - mt.sentence[0], mt.pos - mt.sentence[0] + len(mt.raw))
        ratios_at.setdefault((mt.doc, mt.sentence), []).append(span)
    for mt in mentions:
        if mt.kind not in ("operative", "undirected"):
            continue  # a past split's ratio is past whichever way it points (NEE's 2020 four-for-one)
        s, e = mt.sentence
        own = docs[mt.doc].match[s:e]
        if filed is not None:
            # a ratio stated as DONE whose OWN clause dates it more than ``HISTORICAL_GAP_DAYS`` before the filing is
            # a past split's, whatever else its sentence dates (since 2026-11-26): CYCU's "Because the Company
            # effected a 1-for-30 reverse stock split on October 27, 2025, … trading … is expected to be suspended …
            # on July 21, 2026", STKE's "(1) Reflects the 1 for 8 share consolidation that occurred on August 5,
            # 2025" in a share table dated 2026, EMAT's "In June 2022, the Company implemented a 1-for-10 stock split"
            at = mt.pos - s
            others = [sp for sp in ratios_at[(mt.doc, mt.sentence)] if sp[0] != at]
            oc = _own_clause(own, at, at + len(mt.raw), others)
            if oc is not None and _past(oc[0], filed) and _states_done(oc[1]):
                mt.kind = "historical"
                continue
        if strict and _FUTURE.search(own):
            continue  # what WILL happen is never a past split's
        ds = [x for _, x in _dates(own)]
        if not ds and strict:
            # "… had the same effect as a one-for-fifty reverse ADS split. The change in the ADS ratio was
            # effective on July 13, 2023." (LGHL's interim results, filed 2024-12-20): the sentence beside an
            # undated ratio dates it
            i = index[(mt.doc, mt.sentence)]
            spans = docs[mt.doc].spans
            for j in (i - 1, i + 1):
                if not 0 <= j < len(spans):
                    continue
                near = cued_by_sentence.get((mt.doc, spans[j]))
                if near and not _FUTURE.search(docs[mt.doc].match[spans[j][0] : spans[j][1]]):
                    ds.extend(near)
        if ds and any(all((a - x).days > HISTORICAL_GAP_DAYS for x in ds) for a in anchors):
            mt.kind = "historical"
    _set_known_aside(docs, mentions, known, effective)
    if strict and not trading and not legal:
        # the filing dates NOTHING currently — every split date it states is a past one — so a ratio it also
        # dates in the past somewhere is that past split's, wherever else it is restated undated: KXIN's interim
        # results of 2025-09-12 ("On October 25, 2024, the Group effected a share consolidation at a ratio of
        # one-for-sixty" beside an undated "(iii) share consolidation at a ratio of one-for-sixty"). Since
        # 2026-11-26 also when no cue dates anything at all (until then a past CUED date was needed): ASST's 10-Q
        # restates "On February 6, 2026, the Company amended its articles … to effect a 1:20 reverse stock split"
        # (dated, uncued) beside an undated "every 20 shares … were reclassified into one share", and read the
        # second as a new split once a junk date ("… Restatement Effective Date … June 16, 2026", a preferred
        # stock's dividend terms) stopped counting
        dated_past = {m.key() for m in mentions if m.kind == "historical"}
        # an undirected mention by its two numbers, either way round (NUCL's 10-Q restating its 2-for-1 of
        # 2026-07-06, dated in one sentence and undated in another, with no direction word)
        past_pairs = {frozenset((m.old, m.new)) for m in mentions if m.kind == "historical"}
        for mt in mentions:
            cs, ce = mt.sentence
            if ce - cs > _LONG_SENTENCE:
                cs, ce = max(cs, mt.pos - _WINDOW_BEFORE), min(ce, mt.pos + _WINDOW_AFTER)
            if not past and _FUTURE.search(docs[mt.doc].match[cs:ce]):
                # with no past CUED date (the widening above) a sentence about what WILL happen is a new split of
                # the same ratio, never the past one: Beyond Air's 8-K of 2026-06-26 restates its one-for-twenty of
                # 2025 beside "the Board approved a 1-for-20 reverse stock split, which is expected to enable the
                # Company to regain compliance" (its next 1-for-20, first session 2026-07-13)
                continue
            if mt.kind == "operative" and mt.key() in dated_past:
                mt.kind = "historical"
            elif mt.kind == "undirected" and frozenset((mt.old, mt.new)) in past_pairs:
                mt.kind = "historical"

    conflicts = [m for m in mentions if m.kind == "conflict"]
    operative = [m for m in mentions if m.kind == "operative"]
    undirected = [m for m in mentions if m.kind == "undirected"]
    by_ratio: dict[Fraction, list[_Mention]] = {}
    for m in operative:
        k = m.key()
        if k is not None:
            by_ratio.setdefault(k, []).append(m)
    if len(by_ratio) > 1:
        keep = _superseded(docs, by_ratio)
        if keep is not None:
            for k, ms in by_ratio.items():
                if k != keep:
                    for m in ms:
                        m.kind = "superseded"
            by_ratio = {keep: by_ratio[keep]}
    distinct: dict[Fraction, _Mention] = {k: ms[0] for k, ms in by_ratio.items()}

    seen_labels = tuple(dict.fromkeys(f"{m.kind}:{m.raw}" for m in mentions))
    base = {
        "effective_date": effective,
        "date_kind": kind,
        "date_note": date_note,
        "mentions": seen_labels,
    }

    def abstain(reason: str, at: list[_Mention], first: _Part | None = None) -> _Read:
        parts = ([first] if first is not None else []) + [
            (m.doc, m.sentence, m.pos) for m in at[: 1 if first is not None else 2]
        ]
        all_parts = parts + ([date_at] if date_at is not None else [])
        passage, label = _passage(docs, all_parts) if all_parts else ("", None)
        x = SplitExtraction(abstain_reason=reason, passage=passage, document=label, **base)
        return _Read(x, docs, parts, date_at, len(distinct))

    ended = _ads_termination(docs)
    if ended is not None:
        # the ADS's price factor is the exchange's, never the ordinary shares' ratio read here (POM)
        return abstain(ADS_TERMINATION, [*distinct.values(), *conflicts, *undirected], first=ended)
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
                f"{RANGE_ONLY} ("
                + ", ".join(dict.fromkeys(m.raw for m in ranged))
                + ") — no ratio set",
                ranged,
            )
        historical = [m for m in mentions if m.kind == "historical"]
        if historical:
            return abstain(PAST_RATIO_ONLY, historical)
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
    passage, label = _passage(docs, parts + ([date_at] if date_at is not None else []))
    x = SplitExtraction(
        ratio_from=chosen.old,
        ratio_to=chosen.new,
        passage=passage,
        document=label,
        **base,
    )
    return _Read(x, docs, parts, date_at, 1)


def extract_split(
    documents: list[FilingDocument],
    *,
    filed: date | None = None,
    wide: bool = False,
    known: Iterable[KnownSplit] = (),
) -> SplitExtraction:
    """Read one filing's split ratio, effective date and located passage (see the module docstring for every
    rule). Pure: the documents in, a ``SplitExtraction`` out — an abstain is a result, never a raise.

    ``filed`` — the filing date, given for a CATCH-ALL filing (an 8-K read only for its Item 8.01 / 7.01): a
    date more than ``HISTORICAL_GAP_DAYS`` before it is a PAST split's (the filing restates it), never this
    filing's first session, and a ratio stated only beside such dates is a past split's too. ``wide`` keeps the
    wide forms' own documents (``relevant_documents``). ``known`` — the security's splits already CONFIRMED on
    the list and in effect by the filing's date (``KnownSplit``): a ratio equal to one, stated as done, is that
    past split's whatever its age (the module docstring). Empty, the reading is exactly what it was without it.
    """
    return _read(documents, filed=filed, wide=wide, strict=False, known=tuple(known)).x


def extract_split_screened(
    documents: list[FilingDocument],
    *,
    filed: date,
    wide: bool = False,
    known: Iterable[KnownSplit] = (),
) -> SplitExtraction:
    """THE RESTATEMENT SCREEN (2026-10-24) — for a filing that may restate OLD splits: a 6-K (interim results
    restate the company's past consolidations), a charter 8-K, and every wide form (a 10-Q / 10-K / 20-F does it
    by design). It only ever REMOVES from the unscreened reading, never adds:

    - **A current date** (at most ``HISTORICAL_GAP_DAYS`` before the filing) — the reading stands exactly as
      ``extract_split`` reads it. Every split auto-confirmed from such a filing has one (its window is
      ``[first session - 120, first session + 10]`` days and its date agrees with Massive.com's), so none changes.
    - **A past date** (more than ``HISTORICAL_GAP_DAYS`` before the filing) — never the filing's split date: a
      restated old split (WSHP's 6-K of 2026-08-24 restating its pre-IPO 1-for-4 of 2025-10-22) or a mistyped
      date (MEDS's 8-K: "will become effective … on May 26, 2025", filed 2026-05-21). The date is dropped and
      NOTHING replaces it — another date the filing states (MEDS's press release says 2026-05-26) is named in
      ``date_note``, not taken: dropping a past date must never make a reading more confident (CPOP's 6-K of
      2026-06-10 and RDY's of 2024-11-05 would otherwise gain a record or proxy date).
    - **A ratio the text dates in the past** — in its own sentence, or (an undated ratio) in the date sentence
      beside it, and never in a sentence about what WILL happen — is a past split's, not the filing's: LGHL's
      6-K of 2024-12-20 ("… the same effect as a one-for-fifty reverse ADS split. The change in the ADS ratio
      was effective on July 13, 2023.") abstains instead of attaching its 2023 ratio to a 2025 split.
    - **An abstain stays an abstain**: setting a ratio aside never resolves "more than one ratio" into a single
      reading (KXIN's 6-K of 2026-09-16 restates 60 -> 1 undated beside the 15 -> 1 of 2026-03-13 — with the 15
      set aside the 60 would read as its split); the reason becomes ``PAST_RATIO_ONLY`` only when EVERY ratio
      was set aside.

    ``known`` (the KNOWN splits, as ``extract_split``) is applied to both of its readings.
    """
    known = tuple(known)
    bare = _read(documents, filed=None, wide=wide, strict=False, known=known)
    b = bare.x
    if b.effective_date is not None and not _past(b.effective_date, filed):
        return b
    scr = _read(documents, filed=filed, wide=wide, strict=True, known=known)
    s = scr.x
    docs = scr.docs
    # the date: dropped, never replaced
    if b.effective_date is not None:
        gap = (filed - b.effective_date).days
        if s.effective_date is not None:
            date_note = (
                f"the stated {'first session' if b.date_kind == 'trading' else 'effective date'} "
                f"{b.effective_date.isoformat()} is {gap} days before the filing (a past split's, or a "
                f"mistyped date); the filing also states {s.effective_date.isoformat()} — no date is taken"
            )
        else:
            date_note = s.date_note or (
                f"only a past date stated ({b.effective_date.isoformat()}), more than "
                f"{HISTORICAL_GAP_DAYS} days before the filing"
            )
    else:
        date_note = b.date_note
    shown_date = [bare.date_at] if bare.date_at is not None else []

    def out(src: _Read, *, ratio: bool, reason: str | None = None) -> SplitExtraction:
        passage, label = (
            _passage(docs, src.parts + shown_date) if src.parts or shown_date else ("", None)
        )
        return SplitExtraction(
            ratio_from=src.x.ratio_from if ratio else None,
            ratio_to=src.x.ratio_to if ratio else None,
            effective_date=None,
            date_kind=None,
            passage=passage,
            document=label,
            abstain_reason=None if ratio else (reason or src.x.abstain_reason),
            date_note=date_note,
            mentions=s.mentions,
        )

    if b.has_ratio and s.has_ratio:
        if Fraction(b.ratio_from) / Fraction(b.ratio_to) != Fraction(s.ratio_from) / Fraction(  # type: ignore[arg-type]
            s.ratio_to  # type: ignore[arg-type]
        ):
            # cannot happen by the rules above (the screen only sets ratios aside); never pick one if it does
            two = f"{_label(b.ratio_from, b.ratio_to)}, {_label(s.ratio_from, s.ratio_to)}"  # type: ignore[arg-type]
            return out(scr, ratio=False, reason=f"more than one ratio stated: {two}")
        return out(scr, ratio=True)  # the same ratio (the screen only sets ratios aside)
    if b.has_ratio:
        return out(scr, ratio=False)  # the screen set it aside: a past split's
    if (
        not s.has_ratio
        and scr.operative == 0
        and (s.abstain_reason == PAST_RATIO_ONLY or (s.abstain_reason or "").startswith(RANGE_ONLY))
    ):
        return out(scr, ratio=False)  # every ratio was set aside: past (or only a range left)
    return out(bare, ratio=False)  # the unscreened abstain stands — never resolved by the screen
