"""The SEC split extractor (split plan S1) on REAL filings — the diagnosis §2 table is the key: every ratio
and first session below is what the company's own filing states (``tests/fixtures/splits/README.md``).

Plus the contract on direct inputs where the saved filings have no instance: two different ratios abstain, a
word that disagrees with its digits abstains, no direction abstains, a clock time is never a ratio, two
different first sessions give no date."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from ingest.edgar import split_filing
from ingest.edgar.split_filing import (
    FilingDocument,
    KnownSplit,
    extract_split,
    extract_split_screened,
    relevant_documents,
    submission_documents,
)
from pipeline.backfill_splits import (
    SCREEN_CATCH_ALL,
    SCREEN_NONE,
    SCREEN_RESTATED,
    FilingRef,
    extract_for,
    screen_of,
)
from tests.split_fixtures import FX, documents, filing, primary_document

# (fixture, old shares, new shares, first session / effective date, date kind, a phrase of the passage)
REAL = [
    (
        "0001640334-26-001421.txt",
        "9",
        "1",
        "2026-08-31",
        "trading",
        "one-for-nine (1-for-9) reverse stock split",
    ),
    (
        "0001213900-26-108468.txt",
        "16",
        "1",
        "2026-10-15",
        "trading",
        "Beginning with the opening of trading on October 15, 2026",
    ),
    (
        "0001193125-26-379008.txt",
        "40",
        "1",
        "2026-09-02",
        "trading",
        "1-for-40 reverse stock split",
    ),
    (
        "0001213900-26-098435.txt",
        "15",
        "1",
        "2026-09-14",
        "trading",
        "15 for 1 Share Consolidation",
    ),
    (
        "0001213900-26-024093.txt",
        "25",
        "1",
        "2026-03-06",
        "trading",
        "twenty five (25) -for- one (1)",
    ),
    (
        "0001493152-26-043847.txt",
        "8",
        "1",
        "2026-09-28",
        "trading",
        "one-for-eight (1-for-8) reverse stock split",
    ),
    ("0001213900-26-097860.txt", "6", "1", "2026-09-08", "trading", "1-for-6 reverse stock split"),
    (
        "0001213900-26-098825.txt",
        "20",
        "1",
        "2026-09-16",
        "trading",
        "every 20 pre-split ordinary shares",
    ),
    (
        "0001193125-26-263723.txt",
        "10",
        "1",
        "2026-06-09",
        "trading",
        "approved a 1-for-10 reverse stock split",
    ),
    ("0001493152-26-042282.txt", "30", "1", "2026-09-14", "trading", "ratio at 1-for-30"),
    ("0001104659-26-100333.txt", "10", "1", "2026-08-25", "trading", "on a 10-for-1 basis"),
    (
        "0001493152-26-035388.txt",
        "7",
        "1",
        "2026-08-03",
        "trading",
        "a 7 for 1 reverse stock split",
    ),
    ("0001493152-25-023264.txt", "8", "1", "2025-11-18", "trading", "split ratio at 8:1"),
    (
        "0001493152-26-021807.txt",
        "20",
        "1",
        "2026-05-11",
        "trading",
        "fixed the split ratio at 20:1",
    ),
    (
        "0001214659-26-011966.txt",
        "10",
        "1",
        "2026-09-29",
        "trading",
        "at a ratio of 1-for-10 shares",
    ),
    (
        "0001731122-26-001258.txt",
        "20",
        "1",
        "2026-09-22",
        "trading",
        "1-for-20 reverse stock split",
    ),
    ("0001493152-26-039258.txt", "20", "1", "2026-08-17", "trading", "at a ratio of 1-for-20"),
    ("0001213900-26-009093.txt", "6.25", "1", "2026-02-02", "trading", "1-for-6.25"),
    # --- found by the dev backfill (2026-10-20) and fixed: each was "needs review" before ---
    # ELBM: "four (4) pre-Reverse Split shares for every one (1) post-Reverse Split share" (old for every new)
    (
        "0001171843-24-007098.txt",
        "4",
        "1",
        "2025-01-02",
        "trading",
        "four (4) pre-Reverse Split shares for every one (1) post-Reverse Split share",
    ),
    # CMND: "the first date when … shares will begin trading … after implementation of the reverse split"
    (
        "0001213900-26-105269.txt",
        "8",
        "1",
        "2026-10-05",
        "trading",
        "after implementation of the reverse split will be Monday, October 5, 2026",
    ),
    # ERNA: "Effective June 12, 2025, … to effect a reverse stock split … at a ratio of 1-for-15"
    ("0001641172-25-014877.txt", "15", "1", "2025-06-12", "legal", "Effective June 12, 2025"),
    # SMX 2025: "every twenty-eight and one half ordinary shares … combined into one"
    (
        "0001493152-25-001874.txt",
        "28.5",
        "1",
        "2025-01-15",
        "trading",
        "every twenty-eight and one half ordinary shares",
    ),
    # NIPG: an ADS RATIO CHANGE — "the same effect as a one-for-thirty reverse ADS split"
    (
        "0001493152-26-031419.txt",
        "30",
        "1",
        "2026-07-06",
        "legal",
        "the same effect as a one-for-thirty reverse ADS split",
    ),
    # ASNS: the authorization's ceiling ("… in the aggregate, exceeds 1-for-12") is not the ratio set
    (
        "0001213900-25-110404.txt",
        "10",
        "1",
        "2025-11-18",
        "trading",
        "the Board approved a 1-for-10 reverse stock split",
    ),
    # MI: the ratio sits in an interim-statements note, beside the AUTHORIZED range ("1-for-5 up to 1-for-200")
    (
        "MI_ex991_0930.htm",
        "80",
        "1",
        "2026-05-18",
        "legal",
        "effected a 1-for-80 share consolidation on May 18, 2026",
    ),
    # --- Item 8.01 / 7.01 8-Ks (2026-10-23): the split list's 8-K reader skipped these items until then ---
    # ENHA (8.01): states a LEGAL time ("effective at 12:01 a.m. Central Time on October 29, 2026") AND the
    # first TRADING session — the trading one is read
    (
        "0001628280-26-066196.txt",
        "10",
        "1",
        "2026-10-29",
        "trading",
        "begin trading on a split-adjusted basis on the New York Stock Exchange at the opening of trading on "
        "October 29, 2026",
    ),
    # SNEX (8.01): a three-for-two paid as a stock dividend — "one additional share … for every two"
    (
        "0000913760-26-000034.txt",
        "2",
        "3",
        "2026-07-20",
        "trading",
        "has approved a three-for-two split of its common stock",
    ),
    # SNEX (2.02 + 8.01): the same, announced inside an earnings release
    (
        "0000913760-25-000021.txt",
        "2",
        "3",
        "2025-03-24",
        "trading",
        "Trading is expected to begin on a stock split-adjusted basis at market open on March 24, 2025",
    ),
    # ZCSH (8.01): a trust's 3-for-1 forward share split — "two additional shares for every one pre-split share"
    (
        "0001193125-26-408382.txt",
        "1",
        "3",
        "2026-09-30",
        "trading",
        "effected a 3-for-1 forward share split",
    ),
    # ETH (8.01): a trust's 1-for-10, a LEGAL time ("effective as of 5:00 p.m. Eastern Time") on 11-19
    ("0000950170-24-129219.txt", "10", "1", "2024-11-19", "legal", "at a ratio of 1-for-10"),
]


@pytest.mark.parametrize(
    ("name", "old", "new", "when", "kind", "phrase"), REAL, ids=[r[0] for r in REAL]
)
def test_the_real_filing_states_this_split(name, old, new, when, kind, phrase):
    x = extract_split(documents(name))
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(old), Decimal(new))
    assert x.effective_date == date.fromisoformat(when)
    assert x.date_kind == kind
    assert phrase in x.passage  # the located passage is the filing's own words


def test_wnrs_reg_a_states_the_ratio_but_no_market_date():
    """WNRS's 1-A/A: "completed a 1:300 reverse stock split in the State of Nevada … pending a completed FINRA
    reverse split corporate action" — a ratio, and NO first session (the diagnosis's market date is Massive.com's
    alone), so the matcher can only send it to review."""
    x = extract_split(documents("WNRS_1aa.htm"))
    assert (x.ratio_from, x.ratio_to) == (Decimal(300), Decimal(1))
    assert x.effective_date is None and x.date_note
    assert "completed a 1:300 reverse stock split in the State of Nevada" in x.passage
    assert x.document and x.document.startswith("PART II AND III")


def test_mi_proxy_authorizing_a_range_abstains():
    """MI's 6-K proxy authorizes "up to one-for-two hundred (1:200), but in any case … not less than
    one-for-five (1:5)" and an illustration "assuming an Approved Consolidation Ratio of 1:200" — no ratio is
    SET, so the extractor abstains (the real-text abstain case)."""
    x = extract_split(documents("0001213900-26-046153.txt"))
    assert not x.has_ratio
    assert x.abstain_reason.startswith("only an authorized range of ratios")
    assert "one-for-two hundred (1:200)" in x.passage
    assert x.about_a_split


@pytest.mark.parametrize("name", ["0001213900-26-053864.txt", "0001213900-26-055196.txt"])
def test_a_6k_about_something_else_finds_no_ratio(name):
    x = extract_split(documents(name))
    assert x.abstain_reason == "no split ratio found"
    assert not x.about_a_split


def test_pbm_reads_its_own_ratio_not_the_past_one_or_the_ceiling():
    """PBM's 6-K restates a PAST split ("a 1-for-7.97 consolidation ratio which was implemented Monday, May 5,
    2025") and the authorization ceiling ("up to 1-for-50") beside its own 1-for-6.25 — neither counts, so the
    ratio is read rather than abstained on. Its first plan (01-20) is not a trading sentence; 02-02 is.
    """
    x = extract_split(documents("0001213900-26-009093.txt"))
    assert (x.ratio_from, x.ratio_to) == (Decimal("6.25"), Decimal(1))
    assert "historical:1-for-7.97" in x.mentions
    assert "range:1-for-50" in x.mentions
    assert x.effective_date == date(2026, 2, 2)
    assert "at the opening of the market on February 2, 2026" in x.passage


def test_enha_reads_its_first_trading_session_not_its_legal_time():
    """ENHA's Item 8.01 8-K states the split's LEGAL moment ("effective at 12:01 a.m. Central Time on October
    29, 2026") and its first TRADING session ("expected to begin trading on a split-adjusted basis … at the
    opening of trading on October 29, 2026"). The trading sentence wins (kind ``trading``), so the matcher
    holds it to Massive.com's first session EXACTLY — and Massive.com lists 10-28, one session early.
    """
    x = extract_split(documents("0001628280-26-066196.txt"))
    assert (x.ratio_from, x.ratio_to, x.effective_date, x.date_kind) == (
        Decimal(10),
        Decimal(1),
        date(2026, 10, 29),
        "trading",
    )
    assert "effective at 12:01 a.m. Central Time on October 29, 2026" in x.passage
    assert x.document == "8-K apadu-20261019.htm"  # the filing's own file name


@pytest.mark.parametrize(
    "name", ["0000913760-26-000034.txt", "0000913760-25-000021.txt", "0001193125-26-408382.txt"]
)
def test_a_split_paid_as_extra_shares_reads_old_to_old_plus_extra(name):
    """ "one additional share … for every two shares owned" (SNEX) and "two additional shares for every one
    pre-split share" (ZCSH) state the shares a holder RECEIVES on top of the ones held: 2 -> 3 and 1 -> 3. Read
    the old way they were 1 -> 2 (SNEX, beside its own "three-for-two": two ratios, abstained) and 1 -> 2 (ZCSH)
    — and ZCSH's "September 28, 2026 received two additional shares for every one" read the YEAR as the first
    number (1 -> 2026): the words between a ratio's two numbers are never a number themselves."""
    x = extract_split(documents(name))
    assert x.abstain_reason is None, x.abstain_reason
    additional = [m for m in x.mentions if "additional" in m]
    assert additional and all(m.startswith("operative:") for m in additional)
    assert not any("2026 received" in m for m in x.mentions)


def test_an_item_8_01_that_only_authorizes_a_range_abstains():
    """JUNS's 8-K (Items 5.07 + 8.01, filed 2026-07-27): the stockholders approved "a reverse stock split at a
    ratio not less than 1:10 and not more than 1:100 … to be determined in the discretion of our Board" — no
    ratio is SET, so nothing is read (with the catch-all filing-date screen or without it)."""
    for filed in (None, date(2026, 7, 27)):
        x = extract_split(documents("0001493152-26-034724.txt"), filed=filed)
        assert not x.has_ratio and x.effective_date is None
        assert x.abstain_reason.startswith("only an authorized range of ratios")
        assert "not less than 1:10 and not more than 1:100" in x.passage


def test_an_item_8_01_restating_a_past_split_is_read_as_past():
    """ASST's Item 8.01 8-K (filed 2026-10-05) restates its 1-for-20 "effected on February 6, 2026" (241 days
    back). Read bare, that is a 1-for-20 with a legal date of 02-06 — a split this filing never announced. With
    its filing date (the catch-all screen), the date is a PAST split's, and so is the ratio stated beside it.
    """
    bare = extract_split(documents("0001628280-26-064753.txt"))
    assert (bare.ratio_from, bare.effective_date) == (Decimal(20), date(2026, 2, 6))
    x = extract_split(documents("0001628280-26-064753.txt"), filed=date(2026, 10, 5))
    assert not x.has_ratio and x.effective_date is None
    assert x.abstain_reason == "only a past split's ratio is stated"
    assert (
        x.date_note == "only a past date stated (2026-02-06), more than 90 days before the filing"
    )
    assert (
        "retroactively adjusted to reflect the 1-for-20 reverse stock split effected on February 6, 2026"
        in (x.passage)
    )


def test_a_past_date_never_dates_a_catch_all_reading():
    """AIRE's Item 7.01 8-K/A (filed 2026-09-23) states its 1-for-25 only "as adjusted for" it, and the one
    date a legal cue reached was 2025-12-31 — in a tax table ("Effective Tax Rate"). Bare, that read as a
    1-for-25 effective 2025-12-31 (which the nightly would have LISTED as a new split, ``tests/pipeline/
    test_detect_splits.py``); with its filing date the ratio stood but the past date dated nothing. Since
    2026-11-26 the tax table dates nothing even bare: "the combined entity's deferred tax assets" is no sentence
    about a split. The catch-all rule, on its own sentences: a past date never dates the reading.
    """
    bare = extract_split(documents("0001213900-26-102384.txt"))
    assert (bare.ratio_from, bare.effective_date, bare.date_kind) == (Decimal(25), None, None)
    x = extract_split(documents("0001213900-26-102384.txt"), filed=date(2026, 9, 23))
    assert (x.ratio_from, x.ratio_to) == (Decimal(25), Decimal(1))
    assert x.effective_date is None and x.date_kind is None
    assert x.date_note == "no first-session or effective date stated"
    text = (
        "Share amounts are as adjusted for the Company's 1-for-25 reverse stock split. The reverse stock split "
        "became effective on December 31, 2025."
    )
    doc = [FilingDocument(type="8-K", filename="t.htm", raw=text)]
    assert extract_split(doc).effective_date == date(2025, 12, 31)
    y = extract_split(doc, filed=date(2026, 9, 23))
    assert (y.ratio_from, y.ratio_to, y.effective_date) == (Decimal(25), Decimal(1), None)
    assert (
        y.date_note == "only a past date stated (2025-12-31), more than 90 days before the filing"
    )


def test_a_business_combination_is_not_shares_combined():
    """ENHA's Item 8.01 8-K of 2026-08-26: "SAFE investors will receive one warrant for every two shares
    acquired, … if the business combination is consummated" — a merger's warrant terms, not a split. It used to
    read as "a ratio … not whether reverse or forward" (a split filing the nightly could not place).
    """
    x = extract_split(documents("0001628280-26-058985.txt"), filed=date(2026, 8, 26))
    assert x.abstain_reason == "no split ratio found"
    assert not x.about_a_split and x.mentions == ()


def test_a_full_submission_splits_into_typed_documents():
    docs = submission_documents(
        "<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>8-K\n<SEQUENCE>1\n<FILENAME>a.htm\n<TEXT>\n<p>x</p>\n</TEXT>\n"
        "</DOCUMENT>\n<DOCUMENT>\n<TYPE>EX-3.1\n<SEQUENCE>2\n<FILENAME>b.htm\n<TEXT>\ny\n</TEXT>\n"
        "</DOCUMENT>\n<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>3\n<FILENAME>c.htm\n<TEXT>\nz\n</TEXT>\n"
        "</DOCUMENT>\n<DOCUMENT>\n<TYPE>GRAPHIC\n<FILENAME>d.jpg\n<TEXT>\nbegin 644\n</TEXT>\n</DOCUMENT>\n"
    )
    assert [(d.type, d.filename) for d in docs] == [
        ("8-K", "a.htm"),
        ("EX-3.1", "b.htm"),
        ("EX-99.1", "c.htm"),
        ("GRAPHIC", "d.jpg"),
    ]
    # the charter exhibit and the graphic are never read
    assert [d.type for d in relevant_documents(docs)] == ["8-K", "EX-99.1"]


# --- the contract, on direct inputs ----------------------------------------------------------------------------


def _x(text: str):
    return extract_split([FilingDocument(type="8-K", filename="t.htm", raw=text)])


def test_two_different_ratios_abstain_and_never_pick_one():
    x = _x(
        "The Company effected a 1-for-10 reverse stock split. The Company effected a 1-for-20 reverse "
        "stock split. The Common Stock will begin trading on a split-adjusted basis on March 2, 2026."
    )
    assert not x.has_ratio
    assert x.abstain_reason.startswith("more than one ratio stated")


def test_words_that_disagree_with_their_digits_abstain():
    x = _x("The Board approved a one (1) for ten (20) reverse stock split.")
    assert not x.has_ratio and "disagree" in x.abstain_reason
    # a word ratio beside a DIFFERENT digit ratio is two ratios — abstained on too, never resolved
    y = _x("The Board approved a one-for-ten (1-for-20) reverse stock split.")
    assert not y.has_ratio and y.abstain_reason.startswith("more than one ratio stated")


def test_a_ratio_with_no_direction_abstains():
    x = _x("The Board approved a 1-for-10 split, effective as of March 2, 2026.")
    assert x.abstain_reason == "a ratio is stated but not whether the split is reverse or forward"


def test_a_forward_split_reads_old_to_new():
    x = _x(
        "The Board approved a 2-for-1 forward stock split. Shares will begin trading on a split-adjusted "
        "basis on March 2, 2026."
    )
    assert (x.ratio_from, x.ratio_to) == (Decimal(1), Decimal(2))
    assert x.effective_date == date(2026, 3, 2)


def test_a_clock_time_is_never_a_ratio_and_one_for_one_is_no_split():
    x = _x(
        "The reverse stock split will be effective as of 5:00 p.m. Eastern Time on March 2, 2026, at a "
        "ratio of 1-for-12, and the shares will be exchanged on a one-for-one basis."
    )
    assert (x.ratio_from, x.ratio_to) == (Decimal(12), Decimal(1))
    assert x.effective_date == date(2026, 3, 2) and x.date_kind == "legal"


def test_two_different_first_sessions_give_no_date_but_keep_the_ratio():
    x = _x(
        "The 1-for-5 reverse stock split will begin trading on a split-adjusted basis on March 2, 2026. "
        "The warrants will begin trading on a split-adjusted basis on March 9, 2026."
    )
    assert (x.ratio_from, x.ratio_to) == (Decimal(5), Decimal(1))
    assert x.effective_date is None
    assert x.date_note == "2 different trading dates stated: 2026-03-02, 2026-03-09"


def test_a_past_ads_ratio_change_is_read_with_its_own_old_date():
    """LGHL's interim-results 6-K (filed 2024-12-20) restates a 2023 ADS ratio change ("had the same effect as
    a one-for-fifty reverse ADS split … was effective on July 13, 2023") — read with ITS date, so it can never
    pass for a 2025 split (the matcher sends that pairing to review, ``tests/domain/test_splits.py``).
    """
    x = extract_split(documents("0001213900-24-111258.txt"))
    assert (x.ratio_from, x.ratio_to) == (Decimal(50), Decimal(1))
    assert (x.effective_date, x.date_kind) == (date(2023, 7, 13), "legal")


def test_a_run_on_cover_sentence_is_cut_to_a_verbatim_window():
    """NIPG's 6-K cover page runs on with no full stop; the passage shows a marked window around the date, not
    the whole cover."""
    x = extract_split(documents("0001493152-26-031419.txt"))
    date_part = x.passage.split(" … ")[-1]
    assert date_part.startswith("[…] ") and "Effective on July 6, 2026" in date_part
    assert len(date_part) < 560


# --- 2026-10-24: the readings the split list got wrong, the restatement screen, the wide forms ----------------
#
# Every filing below is REAL (``tests/fixtures/splits/README.md``), trimmed so that it extracts identically to
# the full submission under every reading — this branch's AND the 2026-10-23 reader's — checked when trimmed.


def filed_of(name: str) -> date:
    """The filing's own date (its SGML header's FILED AS OF DATE)."""
    m = re.search(r"FILED AS OF DATE:\s*(\d{4})(\d\d)(\d\d)", filing(name))
    assert m, name
    return date(int(m[1]), int(m[2]), int(m[3]))


def test_znb_reads_with_effect_from_never_the_meeting_date():
    """ZNB's 6-K of 2026-08-25 (an EGM notice) ratifies a 1-for-8 consolidation "with effect from 27 July 2026"
    and proposes a second. Its charter exhibit (EX-99.4) opens "Adopted by special resolution passed at
    extraordinary general meeting of shareholders of the Company held on 10 September 2026 … being the opening of
    trading on the Market Effective Date" — read as the first TRADING session until now. A meeting date never
    dates a split, and "with effect from" is a legal cue: 1-for-8, effective 2026-07-27 (Massive.com's date).
    """
    docs = documents("0001213900-26-093515.txt")
    x = extract_split(docs)
    assert (x.ratio_from, x.ratio_to) == (Decimal(8), Decimal(1))
    assert (x.effective_date, x.date_kind) == (date(2026, 7, 27), "legal")
    assert "with effect from 27 July 2026" in x.passage
    # the restatement screen leaves it (the date is 29 days before the filing — current)
    assert extract_split_screened(docs, filed=filed_of("0001213900-26-093515.txt")) == x


def test_dfns_reads_the_ratio_its_8k_revises_to():
    """DFNS's 8-K of 2026-07-16 restates the 1-for-50 "as disclosed on the Current Report on Form 8-K filed …
    on July 13, 2026" and then "has determined to increase the amount of the reverse stock split … at a ratio of
    1-for-125": the revised ratio is the split's (it abstained on "two ratios" until now); the superseded 8-K
    of 07-13 still reads its own 1-for-50."""
    x = extract_split(documents("0001213900-26-078551.txt"))
    assert (x.ratio_from, x.ratio_to) == (Decimal(125), Decimal(1))
    assert (x.effective_date, x.date_kind) == (date(2026, 7, 20), "trading")
    assert "superseded:1-for-50" in x.mentions
    assert "increase the amount of the reverse stock split" in x.passage
    old = extract_split(documents("0001213900-26-077658.txt"))
    assert (old.ratio_from, old.ratio_to, old.effective_date) == (
        Decimal(50),
        Decimal(1),
        date(2026, 7, 20),
    )


def test_a_ratio_restated_beside_a_new_one_without_a_revision_still_abstains():
    """The supersession rule needs BOTH words: a revision AND an earlier disclosure cited for every other ratio.
    Two ratios with neither stay two ratios (never a pick)."""
    x = _x(
        "As previously disclosed, the Board approved a 1-for-10 reverse stock split. The Company effected a "
        "1-for-20 reverse stock split. The Common Stock will begin trading on a split-adjusted basis on March 2, "
        "2026."
    )
    assert x.abstain_reason.startswith("more than one ratio stated")


def test_meds_mistyped_date_is_never_a_confident_date():
    """MEDS's 8-K of 2026-05-21: "The Reverse Stock Split will become effective … on May 26, 2025, and the
    Company's Common Stock will begin trading … on a split-adjusted basis when the market opens on May 26, 2025" —
    the filer's typo for 2026 (its press release: "legally take effect … on May 26, 2026"). Read bare it was the
    first session 2025-05-26, a year before the filing, and through the restatement screen (a charter 8-K) the
    date was dropped with nothing replacing it. Since 2026-11-26 the typo reads as no session at all (May 26, 2025
    was Memorial Day: a legal date), so even bare the filing states two legal dates and takes neither — the ratio
    stands, the date goes to review beside Massive.com's."""
    docs = documents("0001493152-26-024723.txt")
    note = "2 different legal dates stated: 2025-05-26, 2026-05-26"
    bare = extract_split(docs)
    assert (bare.ratio_from, bare.effective_date, bare.date_kind) == (Decimal(50), None, None)
    assert bare.date_note == note
    x = extract_split_screened(docs, filed=filed_of("0001493152-26-024723.txt"))
    assert (x.ratio_from, x.ratio_to) == (Decimal(50), Decimal(1))
    assert x.effective_date is None and x.date_kind is None
    assert x.date_note == note


@pytest.mark.parametrize(
    ("name", "old"),
    [("0001493152-26-039876.txt", "4"), ("0001213900-24-111258.txt", "50")],
    ids=["WSHP", "LGHL"],
)
def test_a_6k_restating_an_old_split_never_attaches_its_ratio(name, old):
    """WSHP's interim results (filed 2026-08-24) restate the pre-IPO 1-for-4 of 2025-10-22 (306 days back) — its
    own sentence dates it. LGHL's (filed 2024-12-20) state "… the same effect as a one-for-fifty reverse ADS
    split" with NO date, and the NEXT sentence: "The change in the ADS ratio was effective on July 13, 2023."
    Read bare each is a split with an old date; through the restatement screen neither date nor ratio stands
    (the filing-date screen alone kept LGHL's 1-for-50, undated, to attach as "related" to a 2025 split).
    """
    docs = documents(name)
    bare = extract_split(docs)
    assert bare.ratio_from == Decimal(old) and bare.effective_date is not None
    x = extract_split_screened(docs, filed=filed_of(name))
    assert not x.has_ratio and x.effective_date is None
    assert x.abstain_reason == "only a past split's ratio is stated"
    assert x.about_a_split  # shown as a filing that talks about a (past) split
    assert bare.effective_date.isoformat() in x.date_note


# MEASURED over all 1,139 cached basket 6-Ks (2026-10-24): applying the catch-all 8-K screen to 6-Ks unchanged
# gave these five a wrong or stale date / ratio. The restatement screen never makes a reading more confident.
REGRESSED_BY_THE_CATCH_ALL_SCREEN = [
    # CMND (filed 2026-03-12): restates its 1-for-40 of 2025-12-15 and an investee's 1-for-6 — the catch-all
    # screen read 40 -> 1. Its one legal date was an accounting-standards boilerplate's "not yet effective for the
    # three months ended January 31, 2026", which dates nothing since 2026-11-25 (W2-5)
    ("0001213900-26-026965.txt", ("40", "1", None), (None, None)),
    # CPOP (2026-06-10): a 2023 consolidation and a proxy dated 2026-04-24 — the catch-all screen read 10 -> 1 on
    # the proxy date (the split's first session is 07-13)
    ("0001213900-26-067098.txt", ("10", "1", "2026-04-24"), ("10->1", None)),
    # PBM (2026-01-08): a past 1-for-7.97 and the "250:1 is not exceeded" ceiling — it read the ceiling
    ("0001213900-26-002516.txt", ("250", "1", None), (None, None)),
    # KXIN (2026-09-16): restates 60 -> 1 (2024-10-25, also undated) and 15 -> 1 (2026-03-13) — it read 60 -> 1
    ("0001104659-26-108260.txt", ("60", "1", None), (None, None)),
    # RDY (2024-11-05): a 1:5 sub-division "effective October 28, 2024" (the record date) beside two fiscal
    # dates — the catch-all screen read 10-28 (Massive.com's first session is 11-05). Since 2026-11-25 (W2-5) a
    # fiscal period end and a standard not yet effective date nothing, so every reading states the filing's own
    # LEGAL 10-28 — six sessions from Massive.com's, outside the matcher's tolerance: still the operator's
    ("0001575872-24-001124.txt", ("1", "5", "2024-10-28"), ("1->5", "2024-10-28")),
]


def _ratio(x) -> str | None:
    return f"{x.ratio_from}->{x.ratio_to}" if x.has_ratio else None


@pytest.mark.parametrize(
    ("name", "catch_all", "screened"),
    REGRESSED_BY_THE_CATCH_ALL_SCREEN,
    ids=["CMND", "CPOP", "PBM", "KXIN", "RDY"],
)
def test_the_restatement_screen_never_resolves_what_the_bare_reading_left_open(
    name, catch_all, screened
):
    docs = documents(name)
    filed = filed_of(name)
    bare = extract_split(docs)
    c = extract_split(docs, filed=filed)  # the catch-all screen, applied to a 6-K: the regression
    assert (
        None if c.ratio_from is None else str(c.ratio_from),
        None if c.ratio_to is None else str(c.ratio_to),
        c.effective_date.isoformat() if c.effective_date else None,
    ) == catch_all
    x = extract_split_screened(docs, filed=filed)
    assert (_ratio(x), x.effective_date.isoformat() if x.effective_date else None) == screened
    # never MORE confident than the bare reading: no ratio it lacked, no date it lacked
    assert not (x.has_ratio and not bare.has_ratio)
    assert x.effective_date is None or x.effective_date == bare.effective_date


def test_cangs_open_card_reads_as_it_did():
    """CANG's 6-K of 2026-09-03 — the open review card — states "a 10-for-1 share consolidation …, which became
    effective on July 20, 2026": 45 days before the filing, inside the 90-day line, so the restatement screen
    leaves the reading exactly as it was (no Massive.com row lists it; the operator decides)."""
    docs = documents("0001493152-26-041303.txt")
    x = extract_split_screened(docs, filed=filed_of("0001493152-26-041303.txt"))
    assert x == extract_split(docs)
    assert (x.ratio_from, x.ratio_to, x.effective_date, x.date_kind) == (
        Decimal(10),
        Decimal(1),
        date(2026, 7, 20),
        "legal",
    )


def test_every_6k_with_a_current_date_reads_exactly_as_before():
    """The screen's guarantee, on every 6-K in the fixtures: a reading whose date is at most 90 days before the
    filing is never touched (every split auto-confirmed from a 6-K has one — MEASURED 2026-10-24: 39 of 39 cached
    unchanged)."""
    checked = 0
    for gz in sorted((FX / "sec").glob("*.txt.gz")):
        name = gz.name[: -len(".gz")]
        docs = documents(name)
        if not docs or not docs[0].type.startswith("6-K"):
            continue
        bare = extract_split(docs)
        filed = filed_of(name)
        if bare.effective_date is None or (filed - bare.effective_date).days > 90:
            continue
        assert extract_split_screened(docs, filed=filed) == bare, name
        checked += 1
    assert checked >= 10


# --- the wide forms (the targeted reader only) ----------------------------------------------------------------


def test_a_wide_forms_own_document_is_read_only_on_the_targeted_path():
    doc = primary_document("0001185185-26-003604")
    assert relevant_documents([doc]) == []
    assert relevant_documents([doc], wide=True) == [doc]
    assert extract_split([doc]).abstain_reason == "no readable document"


def test_dfns_10q_states_its_split_and_its_first_session():
    """DFNS's 10-Q (filed 2026-08-18, 29 days after): 1-for-125 and the first session 2026-07-20, which agree
    with Massive.com exactly — the second source the 8-K reader missed."""
    doc = primary_document("0001185185-26-003604")
    x = extract_split_screened([doc], filed=date(2026, 8, 18), wide=True)
    assert (x.ratio_from, x.ratio_to) == (Decimal(125), Decimal(1))
    assert (x.effective_date, x.date_kind) == (date(2026, 7, 20), "trading")
    assert "1-for-125 Reverse Stock Split" in x.passage


@pytest.mark.parametrize(
    ("acc", "filed", "bare", "screened"),
    [
        # MI's 20-F (filed 2026-05-01) restates its 2024 one-for-fifty: read bare, a split effective 2024-04-12
        ("0001213900-26-050939", date(2026, 5, 1), ("50->1", "2024-04-12"), (None, None)),
        # OFAL's 10-K (filed 2026-07-14): the 1-for-10 its shareholders approved (no date) beside a 2024
        # reorganization "Effective on August 29, 2024" (read bare as a split on 2024-08-29 until 2026-11-26:
        # "to consolidate its business operations" is no consolidation of shares)
        ("0001493152-26-033093", date(2026, 7, 14), ("10->1", None), ("10->1", None)),
    ],
    ids=["MI-20F", "OFAL-10K"],
)
def test_a_periodic_report_always_goes_through_the_restatement_screen(acc, filed, bare, screened):
    doc = primary_document(acc)
    b = extract_split([doc], wide=True)
    assert (_ratio(b), b.effective_date and b.effective_date.isoformat()) == bare
    x = extract_split_screened([doc], filed=filed, wide=True)
    assert (_ratio(x), x.effective_date) == screened


# --- the meeting date, on a direct input -----------------------------------------------------------------------


def test_a_meeting_date_never_makes_its_ratio_a_past_one():
    """ "approved … at the annual meeting held on January 5, 2026" dates the MEETING: the ratio set there is this
    filing's split, read with its own first session (it read as a past split's — the meeting is 147 days before
    the split)."""
    x = _x(
        "The 1-for-20 reverse stock split was approved by stockholders at the annual meeting held on January 5, "
        "2026. The Common Stock will begin trading on a split-adjusted basis at the market open on June 1, 2026."
    )
    assert (x.ratio_from, x.ratio_to, x.effective_date) == (
        Decimal(20),
        Decimal(1),
        date(2026, 6, 1),
    )


# --- 2026-11-25: the date sentences the split list misread (W2-5) ----------------------------------------------
#
# Each filing below is a REAL full submission (``tests/fixtures/splits/README.md``), read the way the nightly reads
# it (``pipeline/backfill_splits.extract_for``): a catch-all 8-K (Item 8.01 only) with its filing date, a 6-K or a
# charter 8-K through the restatement screen. Until 2026-11-25 each read as its comment says (prod's stored
# reading).


def _as_the_nightly_reads(name: str, catch_all: bool):
    docs = documents(name)
    if catch_all:
        return extract_split(docs, filed=filed_of(name))
    return extract_split_screened(docs, filed=filed_of(name))


DATE_SENTENCES = [
    # COPR (8.01 8-K): "effective on the OTC Markets at the open of business on December 15, 2025" names the
    # session without the word "trade" (read as a LEGAL date) beside the release's dateline (12-16): "2 different
    # legal dates stated: 2025-12-15, 2025-12-16"
    (
        "0001493152-25-028222.txt",
        True,
        ("20", "1"),
        "2025-12-15",
        "trading",
        "effective on the OTC Markets at the open of business on December 15, 2025",
    ),
    # NVA (6-K): "The first day of trading under the new ratio is expected to be October 29, 2025" carried no
    # cue: "no first-session or effective date stated"
    (
        "0001493152-25-018165.txt",
        False,
        ("1", "5"),
        "2025-10-29",
        "trading",
        "The first day of trading under the new ratio is expected to be October 29, 2025",
    ),
    # QGEN (6-K): "… January 28, 2025 (the “Effective Date” and the last trading day of the prior ISIN …)" read as
    # a first session beside the real one: "2 different trading dates stated: 2025-01-28, 2025-01-29"
    (
        "0001015820-25-000012.txt",
        False,
        ("36", "35"),
        "2025-01-29",
        "trading",
        "Beginning on Wednesday, January 29, 2025",
    ),
    # IMCC (6-K): the risk factors' "annual report for the year ended December 31, 2025" read as a first session:
    # "2 different trading dates stated: 2025-12-31, 2026-08-27"
    (
        "0001178913-26-004186.txt",
        False,
        ("30", "1"),
        "2026-08-27",
        "trading",
        "on a post-Consolidation basis at the opening of trading on August 27, 2026",
    ),
    # SCAG (6-K): the dateline "Nanjing, China, October 22, 2026 /PRNewswire/" in the headline sentence naming the
    # "Effective Date": "2 different legal dates stated: 2026-10-22, 2026-10-27"
    (
        "0001213900-26-113338.txt",
        False,
        ("15", "1"),
        "2026-10-27",
        "legal",
        "The ADS Ratio Change will be effective on October 27, 2026",
    ),
    # EZRA (charter 8-K): its press release's forward-looking statements name "the June 10, 2026 compliance
    # deadline" beside "post-split trading price": "2 different trading dates stated: 2026-05-18, 2026-06-10"
    (
        "0001493152-26-022840.txt",
        False,
        ("40", "1"),
        "2026-05-18",
        "trading",
        "begin trading on Nasdaq on a Reverse Stock Split-adjusted basis on May 18, 2026 at market open",
    ),
    # VEEA (charter 8-K): its press release writes "at the market open on August 31 , 2026" (a space before the
    # comma: the date went unread) after "effective at 4:30 p.m. … on August 28, 2026", so the sentence's one date
    # read was the legal one: "2 different trading dates stated: 2026-08-28, 2026-08-31"
    (
        "0001213900-26-093996.txt",
        False,
        ("20", "1"),
        "2026-08-31",
        "trading",
        "will begin trading on a split-adjusted basis when the market opens on August 31, 2026",
    ),
]


@pytest.mark.parametrize(
    ("name", "catch_all", "ratio", "when", "kind", "phrase"),
    DATE_SENTENCES,
    ids=["COPR", "NVA", "QGEN", "IMCC", "SCAG", "EZRA", "VEEA"],
)
def test_the_filing_states_its_first_session(name, catch_all, ratio, when, kind, phrase):
    x = _as_the_nightly_reads(name, catch_all)
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind, x.date_note) == (date.fromisoformat(when), kind, None)
    assert phrase in x.passage


def test_pbm_period_end_boilerplate_never_dates_its_split():
    """PBM's interim-results 6-K (filed 2026-11-19) restates its 1-for-6.25 of 2026-02-02; the one date a cue
    reached was the accounting note's "Accounting Standards Issued But Not Yet Effective … not yet effective for
    the period ended September 30, 2026", read as the split's LEGAL date 2026-09-30. A period end and a standard
    not yet effective date nothing (the restated ratio itself is W2-7's: a split already on the list — and since
    2026-11-26 a past split's without the list too: "the 6.25:1 share consolidation completed on February 2, 2026",
    its own clause, is 290 days before the filing).
    """
    x = _as_the_nightly_reads("0001213900-26-122017.txt", False)
    assert (x.has_ratio, x.abstain_reason) == (False, PAST_ONLY)
    assert x.effective_date is None and x.date_kind is None
    assert x.date_note == "no first-session or effective date stated"
    assert "not yet effective" not in x.passage


def test_alps_session_cue_outside_a_sentence_about_the_split_dates_nothing():
    """ALPS's 6-K: "The Authorized Share Capital Changes will be effective at market open on November 9, 2026"
    ties the date to the split only through a defined term (the share consolidation AND a capital increase). A
    session cue counts without the word "trade" only in a sentence about the split, so it still dates nothing.
    """
    x = _as_the_nightly_reads("0001493152-26-049433.txt", False)
    assert (x.ratio_from, x.ratio_to) == (Decimal(5), Decimal(1))
    assert x.effective_date is None
    assert x.date_note == "no first-session or effective date stated"


# --- the date-sentence rules, on direct inputs ------------------------------------------------------------------


def test_a_session_cue_in_a_sentence_about_the_split_is_a_trading_date():
    x = _x(
        "The 1-for-20 reverse stock split became effective at the open of business on December 15, 2025. The "
        "certificate effecting the reverse split is effective as of December 12, 2025."
    )
    assert (x.effective_date, x.date_kind) == (date(2025, 12, 15), "trading")


def test_a_last_day_of_trading_is_never_the_first_session():
    x = _x(
        "The 1-for-10 reverse stock split is effective as of 5:00 p.m. on March 1, 2026. The last day of trading "
        "on a pre-split basis will be March 2, 2026, and the shares will trade on a post-split basis after it."
    )
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 1), "legal")


@pytest.mark.parametrize(
    "noise",
    [
        # a release's dateline, in a headline that names the effective date
        "Acme Announces Effective Date of Reverse Stock Split NEW YORK, March 1, 2026 (GLOBE NEWSWIRE) -- Acme "
        "Corp. today announced the effective date of its reverse stock split.",
        # the release's own date, cited by the 8-K
        "On March 1, 2026, the Company issued a press release announcing the effective date of the reverse "
        "stock split.",
        # a period end
        "Share counts for the year ended December 31, 2025 give retroactive effect to the reverse stock split "
        "as if it were effective then.",
        # an accounting standard not yet effective
        "Accounting standards on share consolidations issued but not yet effective will become effective for "
        "periods beginning on January 1, 2027.",
        # a deadline
        "The Company expects post-split trading to cure the deficiency before the June 10, 2026 compliance "
        "deadline.",
    ],
    ids=["dateline", "press-release", "period-end", "not-yet-effective", "deadline"],
)
def test_a_date_that_names_something_else_never_dates_a_split(noise):
    x = _x(f"The 1-for-10 reverse stock split will be effective on March 4, 2026. {noise}")
    assert (x.effective_date, x.date_kind, x.date_note) == (date(2026, 3, 4), "legal", None)


def test_a_date_written_with_a_space_before_its_comma_is_read():
    x = _x(
        "The 1-for-20 reverse stock split is expected to become effective at 4:30 p.m. on August 28, 2026 and the "
        "common stock is expected to begin trading on a post-split basis at the market open on August 31 , 2026."
    )
    assert (x.effective_date, x.date_kind) == (date(2026, 8, 31), "trading")
    y = _x(
        "The 1-for-20 reverse split became effective as of the market open on December 15 th , 2025."
    )
    assert (y.effective_date, y.date_kind) == (date(2025, 12, 15), "trading")


# --- 2026-11-25: the ratio phrasings the split list misread (W2-6) ---------------------------------------------
#
# Each filing below is a REAL full submission (``tests/fixtures/splits/README.md``), read the way the nightly reads
# it. Until 2026-11-25 each read as its comment says (prod's stored reading).

RATIO_PHRASINGS = [
    # NOW (charter 8-K): "a 5-for-1 stock split …, with a proportionate increase in the number of shares of
    # authorized common stock" named no direction, and "will receive four additional shares for each share" had no
    # number after "for each": "a ratio is stated but not whether the split is reverse or forward". Both read 1 -> 5
    # now
    (
        "0001373715-25-000321.txt",
        ("1", "5"),
        "2025-12-18",
        "trading",
        "to effect a 5-for-1 stock split",
    ),
    # EMBJ (6-K, an ADS ratio change): "three additional ADSs for every ADS held on the record date" — no number
    # after "for every", in a sentence with no split word: "no split ratio found"
    (
        "0001292814-26-005066.txt",
        ("1", "4"),
        "2026-11-16",
        "trading",
        "three additional ADSs for every ADS held on the record date",
    ),
    # DFSC (two 6-Ks): "a consolidation of its common shares on a 9-to-1 basis": "no split ratio found"
    (
        "0001279569-26-001064.txt",
        ("9", "1"),
        "2026-11-16",
        "trading",
        "will complete a consolidation of its common shares on a 9-to-1 basis",
    ),
    (
        "0001279569-26-001077.txt",
        ("9", "1"),
        "2026-11-16",
        "trading",
        "has completed a consolidation of its common shares on a 9-to-1 basis",
    ),
]


@pytest.mark.parametrize(
    ("name", "ratio", "when", "kind", "phrase"),
    RATIO_PHRASINGS,
    ids=["NOW", "EMBJ", "DFSC-1064", "DFSC-1077"],
)
def test_the_filing_states_its_ratio(name, ratio, when, kind, phrase):
    x = _as_the_nightly_reads(name, False)
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind, x.date_note) == (date.fromisoformat(when), kind, None)
    assert phrase in x.passage
    assert all(m.startswith("operative:") for m in x.mentions)  # every ratio stated reads the same


def test_pom_ads_termination_abstains_on_the_ordinary_share_ratio():
    """POM's 6-K terminates its ADR program: \"every ten (10) ordinary shares … consolidated into one (1)\" is the
    ORDINARY share's ratio, while \"each holder of ten (10) ADSs will receive three (3) post-Share Consolidation
    Class A ordinary shares\" — the ADS price moves x10/3, not x10. It read 10 -> 1, so the ordinary-share ratio could
    be confirmed onto the ADS's tape; the exchange's factor is the operator's, from cited values.
    """
    x = _as_the_nightly_reads("0001213900-26-110501.txt", False)
    assert not x.has_ratio
    assert x.abstain_reason == (
        "ADS termination: the exchange sets the ADS price factor — confirm it with cited values"
    )
    assert x.about_a_split  # shown as a split filing the extractor abstained on
    assert "each holder of ten (10) ADSs will receive three (3)" in x.passage
    assert (x.effective_date, x.date_kind) == (date(2026, 10, 21), "trading")


# --- the ratio rules, on direct inputs ---------------------------------------------------------------------------


def test_consolidated_financial_statements_are_no_reverse_split():
    """NOW's 10-Q (0001373715-25-000309) and 10-K (0001373715-26-000007) read its 5-for-1 as 5 -> 1: the reverse cue
    \"consolidat\" matched \"consolidated financial statements\" (and \"reclassified into\" matched \"reclassified into
    earnings\") all through the report, and the 10-K's table footnote cites the statements in the ratio's own
    sentence. Their own words, verbatim."""
    ten_q = (
        "These condensed consolidated financial statements should be read in conjunction with the audited "
        "consolidated financial statements and related notes included in our Annual Report on Form 10-K for the "
        "year ended December 31, 2024, which was filed with the SEC on January 30, 2025. Principles of "
        "Consolidation The accompanying condensed consolidated financial statements have been prepared in "
        "conformity with GAAP, and include our accounts and the accounts of our wholly-owned subsidiaries. All "
        "intercompany transactions and balances have been eliminated upon consolidation. (19) Subsequent Event On "
        "October 29, 2025, we announced that our Board of Directors approved a 5 -for-1 split of our common stock, "
        "with a proportionate increase in authorized shares of common stock."
    )
    x = extract_split(
        [FilingDocument(type="10-Q", filename="now-20250930.htm", raw=ten_q)], wide=True
    )
    assert (x.ratio_from, x.ratio_to) == (Decimal(1), Decimal(5))
    ten_k = (
        "(1) Share and per share information in this table has been adjusted to reflect the 5-for-1 common stock "
        "split effected on December 17, 2025. Refer to Note 2 “Summary of Significant Accounting Policies” in the "
        "notes to the consolidated financial statements included in Part II, Item 8 of this Annual Report on Form "
        "10-K for additional information. For contracts qualifying as cash flow hedges, the derivative’s gain or "
        "loss is initially reported as a component of accumulated other comprehensive income (loss) and "
        "subsequently reclassified into earnings in the same period the forecasted transaction affects earnings."
    )
    y = extract_split(
        [FilingDocument(type="10-K", filename="now-20251231.htm", raw=ten_k)], wide=True
    )
    assert y.abstain_reason == "a ratio is stated but not whether the split is reverse or forward"


@pytest.mark.parametrize(
    "text",
    [
        "The Company will complete a consolidation of its common shares on a 9-to-1 basis.",
        "Every nine (9) ordinary shares will be consolidated into one (1) ordinary share.",
        "The shares will begin trading on a post-consolidation basis; the 1-for-9 ratio was set by the Board.",
        "The common shares will be consolidated at a ratio of 9:1.",
        "The Board approved the 1-for-9 Share Consolidation of the consolidated group's parent.",
    ],
)
def test_a_share_consolidation_is_still_a_reverse_split(text):
    x = _x(text)
    assert (x.ratio_from, x.ratio_to) == (Decimal(9), Decimal(1))


def test_a_proportionate_increase_in_authorized_shares_is_a_forward_split():
    x = _x(
        "The Board approved a 4-for-1 split of the common stock, with a proportionate increase in the number of "
        "authorized shares. The common stock will begin trading on a split-adjusted basis on March 2, 2026."
    )
    assert (x.ratio_from, x.ratio_to) == (Decimal(1), Decimal(4))
    # …but never where the sentence itself names a reverse split, nor where it says there is no such increase
    y = _x(
        "The 1-for-4 reverse split will not cause a proportionate increase in the authorized shares."
    )
    assert (y.ratio_from, y.ratio_to) == (Decimal(4), Decimal(1))
    z = _x(
        "The Company effected a reverse stock split. The 1-for-4 split was effected without a proportionate "
        "increase in the number of authorized shares."
    )
    assert (z.ratio_from, z.ratio_to) == (Decimal(4), Decimal(1))


def test_additional_shares_elsewhere_never_turn_a_reverse_split_forward():
    """\"additional shares\" orients only the ratio's own sentence, never the document."""
    x = _x(
        "The Company effected a reverse stock split. Each holder's 1-for-20 split shares were rounded up. The "
        "Company may issue additional shares in the future."
    )
    assert (x.ratio_from, x.ratio_to) == (Decimal(20), Decimal(1))


def test_n_to_m_is_read_only_as_a_basis():
    x = _x("The Board may select a consolidation ratio from 1-to-5 to 1-to-50 at its discretion.")
    assert not x.has_ratio


def test_a_ceiling_or_a_past_split_needs_no_direction():
    """With accounting "consolidated" no longer a reverse cue, a 10-Q's covenant and a restated old split often
    name no direction: a ceiling is still a ceiling and a past split still a past one (NEE's 10-Q restates "the
    four-for-one stock split of NEE common stock effective October 26, 2020")."""
    x = _x("The Board may effect a stock split at a ratio of no more than 4.0:1.0.")
    assert x.abstain_reason.startswith("only an authorized range of ratios")
    # a covenant's "consolidated leverage ratio" is no sentence about a split at all (since 2026-11-26)
    c = _x("The credit agreement limits our consolidated leverage ratio to no more than 4.0:1.0.")
    assert (c.abstain_reason, c.about_a_split) == ("no split ratio found", False)
    y = extract_split(
        [
            FilingDocument(
                type="10-Q",
                filename="t.htm",
                raw="Share counts reflect the four-for-one stock split of the common stock effective "
                "October 26, 2020.",
            )
        ],
        filed=date(2026, 9, 30),
        wide=True,
    )
    assert y.abstain_reason == "only a past split's ratio is stated"


def test_an_ads_termination_only_supposed_is_none():
    x = _x(
        "If we terminate our ADR program, holders may receive ordinary shares. The Board approved a 1-for-10 "
        "reverse stock split effective as of March 2, 2026."
    )
    assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (Decimal(10), Decimal(1), None)


# --- 2026-11-25: a split already CONFIRMED on the list, restated (W2-7) -----------------------------------------
#
# Each filing below is a REAL full submission (``tests/fixtures/splits/README.md``), read the way the nightly reads
# it, knowing the company's splits prod's list held CONFIRMED on 2026-11-25 (``KnownSplit``: old -> new shares and
# the first session, as the list stores them; ``pipeline/backfill_splits.known_for`` passes those in effect by the
# filing's date). Without them each reads as its comment says (prod's stored reading, or W2-5 / W2-6's).

PAST_ONLY = "only a past split's ratio is stated"


def _known(*splits: tuple[str, str, str]) -> tuple[KnownSplit, ...]:
    return tuple(KnownSplit(Decimal(a), Decimal(b), date.fromisoformat(d)) for a, b, d in splits)


GCTK_KNOWN = _known(("20", "1", "2025-02-04"), ("60", "1", "2025-06-16"), ("15", "1", "2026-08-31"))
PBM_KNOWN = _known(
    ("75", "1", "2024-11-26"), ("7.97", "1", "2025-05-05"), ("6.25", "1", "2026-02-02")
)
GPUS_KNOWN = _known(("35", "1", "2024-11-25"))
TRT_KNOWN = _known(("1", "2", "2026-01-05"))
CMND_KNOWN = _known(("40", "1", "2025-12-15"))


def _nightly(name: str, *, catch_all: bool = False, wide: bool = False, known=()):
    docs = documents(name)
    if catch_all:
        return extract_split(docs, filed=filed_of(name), known=known)
    return extract_split_screened(docs, filed=filed_of(name), wide=wide, known=known)


def test_gctk_reads_its_new_split_beside_the_one_already_listed():
    """GCTK's 8-K of 2026-10-22 (Items 1.01 / 2.03 / 3.02 / 7.01: a catch-all) announces a 1-for-2, first session
    10-26, and restates "On August 28, 2026, the Company implemented a 1-for-15 reverse stock split" — 55 days
    back, inside the 90-day gap, so it abstained on the two and the 1-for-2 waited for review. The 1-for-15 is on
    the list (first session 08-31; August 28 its legal date): read knowing it, the 8-K states the 1-for-2.
    """
    name = "0001493152-26-048139.txt"
    before = _nightly(name, catch_all=True)
    assert before.abstain_reason == "more than one ratio stated: 2->1, 15->1"
    assert _nightly(name, catch_all=True, known=()) == before  # no known split: exactly as before
    x = _nightly(name, catch_all=True, known=GCTK_KNOWN)
    assert x.abstain_reason is None
    assert (x.ratio_from, x.ratio_to) == (Decimal(2), Decimal(1))
    assert (x.effective_date, x.date_kind, x.date_note) == (date(2026, 10, 26), "trading", None)
    assert "announced a 1-for-2 reverse stock split" in x.passage
    assert "historical:1-for-15" in x.mentions
    # through the restatement screen too (a charter 8-K would be read so): the same reading
    assert _nightly(name, known=GCTK_KNOWN) == x


RESTATED = [
    # PBM's interim 6-K of 2026-11-19: "Comparative loss per share has been restated to reflect the 6.25:1 share
    # consolidation completed on February 2, 2026", in a run-on table sentence whose other dates are current: read
    # as a 6.25 -> 1 with no date (since W2-5; until then dated 2026-09-30 by a period end; since 2026-11-26 a past
    # split's without the list too: its own clause dates it 290 days before the filing)
    (
        "0001213900-26-122017.txt",
        PBM_KNOWN,
        {},
        PAST_ONLY,
        "restated to reflect the 6.25:1 share consolidation completed on February 2, 2026",
    ),
    # GPUS's 8-K of 2024-11-26 (Items 7.01 / 9.01): "The foregoing figures reflect the implementation of the
    # one-for-thirty-five reserve stock split that was effectuated on November 25, 2024" — the day before, and with
    # no direction word ("reserve"): an abstain that kept its 2024-12-13 distribution from being set aside
    (
        "0001214659-24-019590.txt",
        GPUS_KNOWN,
        {"catch_all": True},
        "a ratio is stated but not whether the split is reverse or forward",
        "that was effectuated on November 25, 2024",
    ),
    # Trio-Tech's 10-Q of 2026-11-12 (a wide form: the targeted reader): "On January 5, 2026, the Company effected a
    # two-for-one forward stock split", in footnotes under its tables, beside current dates: 1 -> 2, no date (until
    # 2026-11-26; since then a filing that dates nothing currently takes a past-dated ratio's undated mentions along
    # even when no date cue reaches the past date, so it is a past split's without the list too)
    (
        "0001437749-26-036465.txt",
        TRT_KNOWN,
        {"wide": True},
        PAST_ONLY,
        "On January 5, 2026, the Company effected a two-for-one forward stock split",
    ),
]


@pytest.mark.parametrize(
    ("name", "known", "how", "was", "phrase"), RESTATED, ids=["PBM", "GPUS", "TRT"]
)
def test_a_split_already_listed_restated_is_a_past_splits(name, known, how, was, phrase):
    before = _nightly(name, **how)
    if isinstance(was, tuple):
        assert (before.ratio_from, before.ratio_to) == (Decimal(was[0]), Decimal(was[1]))
        assert before.effective_date is None
    else:
        assert not before.has_ratio and before.abstain_reason == was
    x = _nightly(name, known=known, **how)
    assert not x.has_ratio
    assert (x.abstain_reason, x.effective_date) == (PAST_ONLY, None)
    assert x.about_a_split  # still a filing about a split: shown, never news
    assert phrase in x.passage
    assert all(m.startswith(("historical:", "range:")) for m in x.mentions)


def test_cmnd_a_restated_split_takes_its_other_mentions_along():
    """CMND's interim 6-K of 2026-03-12 restates its 1-for-40 of 2025-12-15 twice: \"the Company effected a
    1-for-40 reverse split\" and \"holders of the Company's common shares received 0.025 of a common share for every
    one common share held\" (no done word). A filing that dates nothing current of its own only restates, so the
    second goes with the first: read as the catch-all screen reads it, 40 -> 1 before, a past split's now.
    """
    name = "0001213900-26-026965.txt"
    before = _nightly(name, catch_all=True)
    assert (before.ratio_from, before.ratio_to) == (Decimal(40), Decimal(1))
    x = _nightly(name, catch_all=True, known=CMND_KNOWN)
    assert (x.has_ratio, x.abstain_reason) == (False, PAST_ONLY)


# --- the known-split rule, on direct inputs -----------------------------------------------------------------------


def _xk(text: str, known, *, filed: date | None = None):
    return extract_split(
        [FilingDocument(type="8-K", filename="t.htm", raw=text)], filed=filed, known=known
    )


K15 = _known(("15", "1", "2026-08-31"))


@pytest.mark.parametrize(
    "text",
    [
        "On August 28, 2026, the Company implemented a 1-for-15 reverse stock split.",
        "As previously disclosed, the Company effected a 1-for-15 reverse stock split.",
        "Share amounts have been retroactively adjusted to reflect the 1-for-15 reverse stock split.",
        "Per-share figures give retroactive effect to the 1-for-15 reverse stock split.",
    ],
)
def test_a_known_ratio_stated_as_done_is_a_past_splits(text):
    assert _xk(text, ()).ratio_from == Decimal(15)  # without the list: read as a split
    x = _xk(text, K15, filed=date(2026, 10, 22))
    assert (x.has_ratio, x.abstain_reason) == (False, PAST_ONLY)


@pytest.mark.parametrize(
    "text",
    [
        # still to come
        "The 1-for-15 reverse stock split is to be effected through an amendment to the charter.",
        "The 1-for-15 reverse stock split will be implemented as previously disclosed.",
        # no done word at all
        "The Board approved a 1-for-15 reverse stock split of the common stock.",
        # done, but dated away from the known split: another split of the same ratio
        "On October 20, 2026, the Company effected a 1-for-15 reverse stock split.",
    ],
)
def test_a_known_ratio_not_stated_as_done_stays(text):
    x = _xk(text, K15, filed=date(2026, 10, 22))
    assert (x.ratio_from, x.ratio_to) == (Decimal(15), Decimal(1))


def test_the_known_splits_own_evidence_reads_as_before():
    """A filing whose own date is the known split's is that split's evidence (the one that confirmed it, re-read):
    knowing the split changes nothing."""
    text = (
        "The 1-for-15 reverse stock split became effective on August 28, 2026, and the common stock began trading "
        "on a split-adjusted basis on August 31, 2026."
    )
    before = _xk(text, (), filed=date(2026, 9, 2))
    assert (before.ratio_from, before.effective_date, before.date_kind) == (
        Decimal(15),
        date(2026, 8, 31),
        "trading",
    )
    assert _xk(text, K15, filed=date(2026, 9, 2)) == before


def test_a_new_split_of_the_same_ratio_still_reads():
    """A company splitting by the same ratio again (44 days apart: inside the 90-day gap): the old split restated as
    done is set aside, the new one — announced, not done — stands, whether its own sentence carries its date or
    not."""
    k = _known(("10", "1", "2026-09-14"))
    for text in (
        "On September 14, 2026, the Company effected a 1-for-10 reverse stock split. The Board has approved a "
        "second 1-for-10 reverse stock split, and the common stock will begin trading on a split-adjusted basis "
        "on November 2, 2026.",
        "On September 14, 2026, the Company effected a 1-for-10 reverse stock split. The Board has approved a "
        "second 1-for-10 reverse stock split. The common stock will begin trading on a split-adjusted basis on "
        "November 2, 2026.",
    ):
        x = _xk(text, k, filed=date(2026, 10, 28))
        assert (x.ratio_from, x.ratio_to) == (Decimal(10), Decimal(1)), text
        assert (x.effective_date, x.date_kind) == (date(2026, 11, 2), "trading")
        assert "historical:1-for-10" in x.mentions


# --- 2026-11-26: the date sentences the long-window check misread (split-reader misreads, PR-A) --------------------
#
# The 800-day split check of 2026-11-26 planned rows dated by a sentence about something else: a trading SUSPENSION,
# a ticker change at "the opening of trading", "consolidating entities", a resolution's date, a share table's
# "outstanding as of", and a Sunday (the classification in ``docs/temp/split-reader-misreads-2026-11-26/``, R3 / R7).
# Each is read the way the pipeline reads it (``pipeline.backfill_splits.screen_of`` / ``extract_for``): its form,
# the 8-K items the nightly stores and its filing date. The fixtures are REAL full submissions
# (``tests/fixtures/splits/README.md``); the multi-MB ones are tested on their verbatim sentences, typed as the
# document they sit in. Until 2026-11-26 each read as its comment says (prod's stored reading).


def _ref(acc: str, form: str, filed: str, items: tuple[str, ...] | None = None) -> FilingRef:
    return FilingRef(accession=acc, form=form, filed=date.fromisoformat(filed), items=items)


def _as_prod_reads(name: str, form: str, items: tuple[str, ...] | None, known=()):
    ref = _ref(name.removesuffix(".txt"), form, filed_of(name).isoformat(), items)
    return screen_of(ref), extract_for(documents(name), ref, known=known)


CYCU_KNOWN = _known(("30", "1", "2025-10-27"))
QGEN_KNOWN = _known(("36", "35", "2025-01-29"))

SUSPENDED = [
    # CYCU's 8-K of 2026-07-16 (Items 3.01 / 7.01 / 9.01: a catch-all), a delisting notice, read knowing its listed
    # 1-for-30: "Because the Company effected a 1-for-30 reverse stock split on October 27, 2025, … trading in the
    # Company's securities is expected to be suspended at the opening of business on July 21, 2026" — read as a
    # 1-for-30 with first session 2026-07-21 (since 2026-11-26 the 1-for-30 is a past split's: ratio None)
    ("0001628280-26-048496.txt", ("3.01", "7.01", "9.01"), CYCU_KNOWN, None, "2026-07-21"),
    # VRM's 8-K of 2025-01-02 (Item 7.01): its 1-for-5 takes effect "upon the filing of the Charter Amendment at the
    # conclusion of the Prepackaged Chapter 11 Case" (no date); "with trading of the Common Stock formally suspended
    # at the opening of business on December 2, 2024" (the delisting) was read as its first session
    ("0001193125-25-000195.txt", ("7.01",), (), ("5", "1"), "2024-12-02"),
]


@pytest.mark.parametrize(
    ("name", "items", "known", "ratio", "suspended"), SUSPENDED, ids=["CYCU", "VRM"]
)
def test_a_trading_suspension_never_dates_a_split(name, items, known, ratio, suspended):
    screen, x = _as_prod_reads(name, "8-K", items, known)
    assert screen == SCREEN_CATCH_ALL
    if ratio is None:  # a past split's ratio only
        assert (x.has_ratio, x.abstain_reason) == (False, PAST_ONLY)
    else:
        assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind) == (None, None)
    assert suspended not in (x.date_note or "")


def test_a_first_session_that_is_no_session_is_a_legal_date():
    """SKYA's charter 8-K of 2025-04-30: "The Reverse Stock Split was effective at 11:59 p.m., Eastern Time, on April
    27, 2025 and began trading on a post-split basis under CUSIP number 82003F 309" carries a trading cue, but April
    27, 2025 is a Sunday: no session (``domain.trading_days``). Its first session was Monday 04-28; the date read is
    the legal one."""
    screen, x = _as_prod_reads("0001641172-25-007892.txt", "8-K", ("5.03", "9.01"))
    assert screen == SCREEN_RESTATED
    assert (x.ratio_from, x.ratio_to) == (Decimal(300), Decimal(1))
    assert (x.effective_date, x.date_kind, x.date_note) == (date(2025, 4, 27), "legal", None)


def test_a_no_session_trading_date_never_outranks_the_first_session():
    x = _x(
        "The 1-for-10 reverse stock split became effective at 11:59 p.m. on March 1, 2026 and began trading on a "
        "post-split basis. The common stock will begin trading on a split-adjusted basis at the market open on "
        "March 2, 2026."
    )
    assert (x.effective_date, x.date_kind, x.date_note) == (date(2026, 3, 2), "trading", None)


# CD's interim 6-K of 2025-12-10 (EX-99.1): its ratio sentence restates the February 2023 consolidation; the date
# was the ticker change's, in a sentence with no split in it
CD_RATIO = (
    "The Board approved the proposal on the share consolidation to the authorized share "
    "capital (the “Share Consolidation”) at a ratio of four hundred ( 400 )-for-one (1) with "
    "the par value of each ordinary share changed to $ 0.004 per ordinary share."
)

CD_TICKER = (
    "The Company changed its corporate name and ticker symbol On November 13, 2025, the "
    "Company officially rebranded as Chaince Digital Holdings Inc. from Mercurity Fintech "
    "Holding Inc. The new corporate name, ticker symbol “CD,” and website "
    "www.chaincedigital.com all went live on November 13, 2025, at the opening of trading on "
    "the Nasdaq Global Market."
)


def test_a_ticker_change_at_the_opening_of_trading_never_dates_a_split():
    """A trading date comes only from a sentence about the split (or one with a split-specific cue: split-adjusted,
    post-split, post-consolidation, consolidated basis). CD's "went live on November 13, 2025, at the opening of
    trading" names a new ticker: read as its 1-for-400's first session until now."""
    ref = _ref("0001493152-25-027066", "6-K", "2025-12-10")
    docs = [FilingDocument(type="EX-99.1", filename="ex99-1.htm", raw=f"{CD_RATIO} {CD_TICKER}")]
    assert screen_of(ref) == SCREEN_RESTATED
    x = extract_for(docs, ref)
    assert (x.ratio_from, x.ratio_to) == (Decimal(400), Decimal(1))
    assert (x.effective_date, x.date_kind) == (None, None)
    assert "went live" not in x.passage


# STKE's interim 6-K of 2026-08-17: the share table restating its 1-for-8 (EX-99.2), and an ACCOUNTING consolidation
# (EX-99.1) whose "effective June 1, 2026" was read as the split's legal date
STKE_ENTITIES = (
    "Accordingly, effective June 1, 2026 the Company commenced consolidating entities that "
    "it controls."
)

STKE_SHARES = (
    "Other Financial and Capital Management Outstanding Share Data At June 30, 2026 (1) "
    "Common shares outstanding: 39,241,299 Options to purchase common shares: 2,915,325 "
    "Restricted share units 88,603 Warrants: 6,479,463 At August 14, 2026 (1) Common shares "
    "outstanding: 39,486,879 Options to purchase common shares: 2,970,412 Restricted share "
    "units 55,089 Warrants: 6,479,463 (1) Reflects the 1 for 8 share consolidation that "
    "occurred on August 5, 2025."
)


def test_consolidating_entities_is_no_share_consolidation():
    ref = _ref("0001104659-26-097779", "6-K", "2026-08-17")
    docs = [
        FilingDocument(type="EX-99.1", filename="tm2622851d1_ex99-1.htm", raw=STKE_ENTITIES),
        FilingDocument(type="EX-99.2", filename="tm2622851d1_ex99-2.htm", raw=STKE_SHARES),
    ]
    x = extract_for(docs, ref)
    # 8 -> 1 until 2026-11-26's own-clause rule: "the 1 for 8 share consolidation that occurred on August 5, 2025"
    assert (x.has_ratio, x.abstain_reason) == (False, PAST_ONLY)
    assert (x.effective_date, x.date_kind) == (None, None)
    assert "consolidating entities" not in x.passage


@pytest.mark.parametrize(
    "other",
    [
        # an accounting consolidation (STKE)
        "Accordingly, effective June 1, 2026 the Company commenced consolidating entities that it controls.",
        # a merger's combined company starting to trade (MRLN's de-SPAC listing)
        "On March 17, 2026, the combined company will begin trading on Nasdaq under the ticker symbol “ACME.”",
        # reverse osmosis (NMG's technical report)
        "The reverse osmosis plant was placed in service effective May 4, 2026.",
        # a subdivision that is no share's
        "The tectonic subdivisions of the property were mapped effective May 4, 2026.",
    ],
    ids=["consolidating-entities", "combined-company", "reverse-osmosis", "subdivisions"],
)
def test_a_sentence_about_something_else_never_dates_a_split(other):
    x = _x(f"The Board approved a 1-for-10 reverse stock split. {other}")
    assert (x.ratio_from, x.ratio_to) == (Decimal(10), Decimal(1))
    assert (x.effective_date, x.date_note) == (None, "no first-session or effective date stated")


@pytest.mark.parametrize(
    ("sentence", "kind"),
    [
        (
            "Every ten shares will be combined into one share at the market open on March 2, 2026.",
            "trading",
        ),
        ("The share combination will take effect at the market open on March 2, 2026.", "trading"),
        (
            "The consolidated Acme shares are expected to begin trading on the NYSE on March 2, 2026.",
            "trading",
        ),
        (
            "The shares will begin trading on a post-consolidation basis on March 2, 2026.",
            "trading",
        ),
        (
            "The post-reverse shares will begin trading at the market open on March 2, 2026.",
            "trading",
        ),
        ("The subdivision of the common shares becomes effective on March 2, 2026.", "legal"),
    ],
    ids=[
        "combined-into",
        "share-combination",
        "consolidated-shares",
        "post-consolidation",
        "post-reverse",
        "subdivision",
    ],
)
def test_a_sentence_about_the_shares_still_dates_the_split(sentence, kind):
    x = _x(f"The Board approved a 1-for-10 reverse stock split. {sentence}")
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 2), kind)


# HLYK's Reg A offering circular of 2025-04-30: an approved 100:1, and a share table's "outstanding as of April 18,
# 2025 Prior to Reverse Split" in a run-on sentence whose "the Reverse Split is effective" made it a legal date
HLYK_RATIO = (
    "We recently approved a Reverse Split of our common stock at a ratio of 100:1 on "
    "February 25, 2025."
)

HLYK_TABLE = (
    "Prior to this offering, the Company had the following securities issued and outstanding "
    "as of April 18, 2025 Prior to Reverse Split 281,947,151 shares of common stock "
    "2,750,000 shares of Series B preferred stock 6,157,422 Options 2021 Equity Incentive "
    "Plan 80,300,130 Warrants 3,324,412 consultant and director shares of common stock "
    "earned but not issued 80,811,637 shares of common stock underlying convertible notes "
    "payable to related party Dr. Michael Dent ($3,621,500 in face value) $939,178 in notes "
    "payable After the Reverse Split (assuming it is completed) 2,819,472 shares of common "
    "stock 2,750,000 shares of Series B preferred stock 61,575 Options 2021 Equity Incentive "
    "Plan 803,002 Warrants 33,245 consultant and director shares of common stock earned but "
    "not issued 808,117 shares of common stock underlying convertible notes payable to "
    "related party Dr. Michael Dent ($3,621,500 in face value) $939,178 in notes payable 2 "
    "Following this offering, assuming all offered Shares are sold, no other securities of "
    "the Company are issued, and the Reverse Split is effective, the Company will have the "
    "following securities issued and outstanding (assuming the maximum number of Shares are "
    "issued): 5,821,348 Shares of common stock (if no Bonus Shares issued); or 6,121,536 "
    "Shares of common stock (if all Bonus Shares are issued) 2,750,000 shares of preferred "
    "stock;"
)


def test_a_share_counts_outstanding_as_of_never_dates_a_split():
    ref = _ref("0001213900-25-037664", "1-A", "2025-04-30")
    docs = [
        FilingDocument(
            type="PART II AND III",
            filename="ea0237909-1a_health.htm",
            raw=f"{HLYK_RATIO} {HLYK_TABLE}",
        )
    ]
    assert screen_of(ref) == SCREEN_NONE
    x = extract_for(docs, ref)
    assert (x.ratio_from, x.ratio_to) == (Decimal(100), Decimal(1))
    assert (x.effective_date, x.date_kind) == (None, None)


# PMEC's proxy 6-K of 2025-09-02 (EX-99.1): a proposal to EXTEND an unused authority, "granted by the special
# resolution passed on 19 June 2025" — read as the 1-for-6's legal date (and 19 June 2025 was an NYSE holiday)
PMEC_PROPOSAL = (
    "3 Proposal Nine By a special resolution, to resolve that: (a) the authority granted by "
    "the special resolution passed on 19 June 2025 be hereby extended and amended such that: "
    "(i) every six (6) issued ordinary shares in the capital of the Company be consolidated "
    "into one (1) ordinary share of the Company (each a “ Consolidated Ordinary Share ” and "
    "collectively, the “ Consolidated Ordinary Shares ”); and (ii) and subject to the "
    "passing of Proposal Eleven in relation to the approval to issue Class B Preference "
    "Shares and Proposal Twelve in relation to the approval to adopt the Second Amended and "
    "Restated Constitution, every six (6) issued Class B Preference Shares in the capital of "
    "the Company be consolidated into one (1) Class B Preference Share of the Company (each "
    "a “ Consolidated Preference Share ” and collectively, the “ Consolidated Preference "
    "Shares ”), as at a time and date to be determined by the Directors, at and on which the "
    "register of members and the transfer books of the Company will be closed for the "
    "purpose of determining the entitlements of the shareholders of the Company following "
    "the proposed share consolidation (“ Share Consolidation Record Date ”) (the “ Share "
    "Consolidation ”), with such authority to continue in force until (i) the conclusion of "
    "the next annual general meeting of the Company or (ii) the date by which the next "
    "annual general meeting of the Company is required by law to be held, whichever is "
    "earlier, as determined by the Directors and such date shall be announced by the Company "
    "(the “ Effective Date ”);"
)


def test_a_resolutions_date_never_dates_a_split():
    ref = _ref("0001213900-25-082874", "6-K", "2025-09-02")
    docs = [
        FilingDocument(type="EX-99.1", filename="ea025451201ex99-1_primech.htm", raw=PMEC_PROPOSAL)
    ]
    x = extract_for(docs, ref)
    assert (x.ratio_from, x.ratio_to) == (Decimal(6), Decimal(1))
    assert (x.effective_date, x.date_kind) == (None, None)


@pytest.mark.parametrize(
    "noise",
    [
        # a resolution's date
        "The authority to effect the reverse stock split was granted by the special resolution passed on February "
        "20, 2026, and it remains effective.",
        # a share count's date
        "The Company had 12,000,000 shares of common stock issued and outstanding as of February 27, 2026, before "
        "the reverse stock split becomes effective.",
        # a trading suspension's date
        "Absent the reverse stock split, trading of the common stock would have been suspended at the opening of "
        "business on March 9, 2026.",
    ],
    ids=["resolution", "outstanding-as-of", "suspension"],
)
def test_a_date_that_names_another_event_never_dates_a_split(noise):
    x = _x(f"The 1-for-10 reverse stock split will be effective on March 4, 2026. {noise}")
    assert (x.effective_date, x.date_kind, x.date_note) == (date(2026, 3, 4), "legal", None)


def test_a_halt_does_not_take_the_resumed_first_session_with_it():
    x = _x(
        "The 1-for-10 reverse stock split became effective on March 3, 2026. Trading in the common stock was "
        "halted on March 2, 2026 and resumed on a split-adjusted basis at the market open on March 4, 2026."
    )
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 4), "trading")


# Strive's (ASST) 10-Q 0001628280-26-071332, for the quarter ended September 30, 2026 (a wide form: the targeted
# reader; its primary document, in the steward review's cache, carries no filing date), restates its
# 1-for-20 of February 6, 2026 twice — dated but with no date CUE, and undated. A junk date ("… Restatement Effective
# Date … June 16, 2026", a preferred stock's dividend terms) used to be the filing's one cued date, a past one, and
# set both aside; with it gone, the undated restatement read as a new 1-for-20.
ASST_DATED = (
    "8 Reverse stock split On February 6, 2026, the Company amended its articles of "
    'incorporation in order to effect a 1:20 reverse stock split (the "Reverse Stock '
    'Split") of its authorized shares of Class A and Class B common stock.'
)
ASST_UNDATED = (
    "Concurrently with the Reverse Stock Split of such authorized shares, every 20 shares of "
    "the Company’s Class A and Class B common stock issued and outstanding before such split "
    "were reclassified into one share of Class A or Class B common stock, respectively, "
    "without any action on the part of the holders."
)


def test_a_split_dated_only_in_the_past_takes_its_undated_restatement_along():
    """A filing that dates nothing currently, with no date cue at all, restates a past split: its undated mention
    is that past split's too (the restatement screen's rule, no longer only when a past date carries a cue). Any
    filing date after the quarter gives this reading."""
    ref = _ref("0001628280-26-071332", "10-Q", "2026-11-09")
    docs = [
        FilingDocument(
            type="10-Q", filename="asst-20260930.htm", raw=f"{ASST_DATED} {ASST_UNDATED}"
        )
    ]
    x = extract_for(docs, ref)
    assert (x.has_ratio, x.abstain_reason, x.effective_date) == (False, PAST_ONLY, None)
    assert "were reclassified into one share" in x.passage


def test_a_consolidation_of_the_authorised_issued_shares_is_about_a_split():
    """A proxy's "consolidation of the Company's authorised, issued and outstanding shares … within a range of not
    less than 2:1 and not greater than 100:1" is a share consolidation (commas between its qualifiers): a filing
    about a split that sets no ratio."""
    x = _x(
        "At the extraordinary general meeting of shareholders of the Company held on "
        "February 27, 2026, shareholders passed a resolution approving a consolidation of "
        "the Company’s authorised, issued and outstanding shares of par value US$0.0001 at a "
        "ratio within a range of not less than 2:1 and not greater than 100:1, with the "
        "exact ratio and effective date to be determined by the directors of the Company."
    )
    assert x.abstain_reason.startswith("only an authorized range of ratios") and x.about_a_split


# Each must still read exactly as it did: the filing's own ratio and date sentences, verbatim, read as the pipeline
# reads the filing (the whole filings are in the replay proof: docs/temp/split-reader-misreads-evidence/pr-a/)
STILL_READ = [
    # ABTC: its first session in a sentence about the "Combined Company" (not about a split) that gives effect to the "Reverse Stock Split" (one)
    (
        "0001213900-25-083726",
        "8-K",
        "2025-09-03",
        (
            "1.01",
            "1.02",
            "2.01",
            "3.02",
            "3.03",
            "5.01",
            "5.02",
            "5.03",
            "5.05",
            "7.01",
            "8.01",
            "9.01",
        ),
        (),
        ("8-K", "ea0255440-8k_american.htm"),
        (
            "Prior to the consummation of the Mergers (the “Closing”), on September 2, 2025, "
            "Gryphon effected a five-for-one (5:1) reverse stock split of its common stock, "
            "par value $0.0001 per share (“Gryphon Common Stock”), by filing a certificate "
            "of amendment (the “Reverse Stock Split Amendment ”) to Gryphon’s amended and "
            "restated certificate of incorporation with the Secretary of State of the State "
            "of Delaware, as described in more detail in this Report (the “Reverse Stock "
            "Split”).",
            "Prior to the Split Effective Time, the Gryphon Common Stock traded on the "
            "Nasdaq Capital Market under the ticker symbol “GRYP.” The Combined Company "
            "Class A Common Stock (giving effect to the Reverse Stock Split, the "
            "Reclassification, the Closing and the Name Change Amendment) will commence "
            "trading on the Nasdaq Capital Market on September 3, 2025, with the new CUSIP "
            "number 02462A104 under the ticker symbol “ABTC.” Second A&R Charter;",
        ),
        ("5", "1"),
        "2025-09-03",
        "trading",
    ),
    # QGEN: "the consolidated QIAGEN shares … are expected to begin trading": shares consolidated, read knowing its listed 36-for-35
    (
        "0001015820-25-000077",
        "6-K",
        "2025-12-22",
        None,
        QGEN_KNOWN,
        ("EX-99.1", "exhibit991announcementssbb.htm"),
        (
            "The terms of the synthetic share repurchase are as follows: • Every 20 issued "
            "QIAGEN shares will be consolidated into 19 QIAGEN shares, leading to a "
            "reduction of approximately 10.9 million shares from the level of 217.7 million "
            "shares as of December 18, 2025. • Following the implementation of the "
            "consolidation, QIAGEN will repay capital to shareholders of record $2.29 per "
            "pre-consolidation share.",
            "1 Beginning on Thursday, January 8, 2026, the consolidated QIAGEN shares, "
            "excluding the entitlement to the capital repayment, are expected to begin "
            "trading on the Frankfurt Stock Exchange (QIA) and on the NYSE (QGEN) under the "
            "Company’s current ticker symbols.",
        ),
        ("20", "19"),
        "2026-01-08",
        "trading",
    ),
    # NEXM: "began trading on a post-Consolidation adjusted basis"
    (
        "0001641172-25-016471",
        "8-K",
        "2025-06-25",
        ("3.03", "7.01", "9.01"),
        (),
        ("8-K", "form8-k.htm"),
        (
            "On June 10, 2025, the Board approved a final Consolidation ratio of 20:1.",
            "The Company’s Common Shares began trading on a post-Consolidation adjusted "
            "basis on June 20, 2025 when markets opened under the new CUSIP number "
            "(65346E204) and ISIN (CA65346E2042).",
        ),
        ("20", "1"),
        "2025-06-20",
        "trading",
    ),
    # HELP: "The Consolidation is effective as of September 19, 2024" (the defined term)
    (
        "0001833141-24-000017",
        "6-K",
        "2024-09-20",
        None,
        (),
        ("EX-99.1", "materialchangereport-conso.htm"),
        (
            "Item 4 Summary of Material Change On September 19, 2024, the Company announced "
            "that it had filed articles of amendment to consolidate the Company’s issued and "
            "outstanding common shares (the “ Common Shares ”) on the basis one new Common "
            "Share for every 38 existing Common Shares (the “ Consolidation ”).",
            "Item 5 Full Description of Material Change The Consolidation is effective as of "
            "September 19, 2024.",
        ),
        ("38", "1"),
        "2024-09-19",
        "legal",
    ),
    # RYM: "will be effective as of 12:01 a.m. Eastern Time on October 8, 2024"
    (
        "0001213900-24-085199",
        "8-K",
        "2024-10-04",
        ("3.03", "5.03", "9.01"),
        (),
        ("8-K", "ea0216705-8k_agrify.htm"),
        (
            "On October 3, 2024, Agrify Corporation (the “Company”) filed Articles of "
            "Amendment (the “Charter Amendment”) to its Articles of Incorporation with the "
            "Secretary of State of the State of Nevada to effect a 1-for-15 reverse stock "
            "split of the Company’s common stock, par value $0.001 per share (the “Common "
            "Stock”), in which each fifteen (15) shares of Common Stock issued and "
            "outstanding will be combined and converted into one share of Common Stock (the "
            "“Reverse Stock Split”).",
            "The Reverse Stock Split will be effective as of 12:01 a.m. Eastern Time on "
            "October 8, 2024 (the “Effective Date”).",
        ),
        ("15", "1"),
        "2024-10-08",
        "legal",
    ),
    # ZSTK: "will begin to trade on a post-Share Consolidation basis as of the opening of the Nasdaq Capital Market" (a session date; the ratio sentence is a cover-page run-on, cut where its own text starts)
    (
        "0001062993-25-013470",
        "8-K",
        "2025-08-04",
        ("3.03", "5.03", "9.01"),
        (),
        ("8-K", "form8k.htm"),
        (
            "On August 4, 2025, Flora Growth Corp., a corporation organized under the laws "
            'of the Province of Ontario (the " Company "), effected a 1-for-39 share '
            'consolidation (the "Share Consolidation") of the Company\'s issued and '
            'outstanding common shares, no par value (the "Common Shares") by the filing '
            "of Articles of Amendment to the Company's amended and restated Articles of "
            'Incorporation (the "Articles of Amendment") with the Ontario Ministry of '
            "Public and Business Service Delivery and Procurement.",
            "The Company anticipates that the Common Shares will begin to trade on a "
            "post-Share Consolidation basis as of the opening of the Nasdaq Capital Market "
            "on August 4, 2025.",
        ),
        ("39", "1"),
        "2025-08-04",
        "trading",
    ),
]


@pytest.mark.parametrize(
    ("acc", "form", "filed", "items", "known", "doc", "sentences", "ratio", "when", "kind"),
    STILL_READ,
    ids=["ABTC", "QGEN", "NEXM", "HELP", "RYM", "ZSTK"],
)
def test_a_split_and_its_date_sentence_still_read(
    acc, form, filed, items, known, doc, sentences, ratio, when, kind
):
    ref = _ref(acc, form, filed, items)
    docs = [FilingDocument(type=doc[0], filename=doc[1], raw=s) for s in sentences]
    x = extract_for(docs, ref, known=known)
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind, x.date_note) == (date.fromisoformat(when), kind, None)


# --- 2026-11-26: ratios that are not this split (split-reader misreads, PR-B) -------------------------------------
#
# The same 800-day check planned rows from a ratio that is not this split: a PAST split restated in a sentence that
# also carries a current date, or dated only by month and year (R2), and numbers that state no split at all (R4: a
# warrant's extra shares, a list of figures' "Figure 9-2: 2013"). Each is read the way the pipeline reads it
# (``screen_of`` / ``extract_for``); the multi-MB documents on their verbatim sentences (a run-on sentence on a
# verbatim slice of it), typed as the document they sit in. Until 2026-11-26 each read as its comment says (the
# reading after PR-A, the date sentences).


def test_a_split_its_own_clause_dates_long_ago_is_past_whatever_else_the_sentence_dates():
    """CYCU's delisting notice (8-K of 2026-07-16, Items 3.01 / 7.01 / 9.01: a catch-all): "Because the Company
    effected a 1-for-30 reverse stock split on October 27, 2025, … trading … is expected to be suspended at the
    opening of business on July 21, 2026". The ratio's own clause states it DONE and dates it 262 days back; the
    later suspension date kept the sentence from the past test, and "expected to" kept it from the known-split
    one: read as 30 -> 1 with no date, knowing its listed 1-for-30 or not. A past split's either way now.
    """
    name = "0001628280-26-048496.txt"
    for known in ((), CYCU_KNOWN):
        screen, x = _as_prod_reads(name, "8-K", ("3.01", "7.01", "9.01"), known)
        assert screen == SCREEN_CATCH_ALL
        assert (x.has_ratio, x.abstain_reason, x.effective_date) == (False, PAST_ONLY, None)
        assert "effected a 1-for-30 reverse stock split on October 27, 2025" in x.passage


def test_a_split_that_occurred_long_ago_is_past_beside_current_dates():
    """STKE's interim 6-K of 2026-08-17 (MD&A, EX-99.2): a share table's run-on sentence dated June 30 and August
    14, 2026 ends "(1) Reflects the 1 for 8 share consolidation that occurred on August 5, 2025" (377 days back).
    The current dates kept the sentence from the past test, and "occurred" was no done word: read alone, 8 -> 1
    with no date (the filing as a whole reaches past only since PR-A, through the dated restatement beside it).
    """
    ref = _ref("0001104659-26-097779", "6-K", "2026-08-17")
    docs = [FilingDocument(type="EX-99.2", filename="tm2622851d1_ex99-2.htm", raw=STKE_SHARES)]
    assert screen_of(ref) == SCREEN_RESTATED
    x = extract_for(docs, ref)
    assert (x.has_ratio, x.abstain_reason, x.effective_date) == (False, PAST_ONLY, None)


EMAT_SPLIT = (
    "In June 2022, the Company implemented a 1-for-10 stock split, which increased the "
    "number of issued shares from 1,000 to 10,000."
)

# the filing's one direction cue: the target's preferred stock terms (EX-99.5), "such as IPOs, stock dividend"
EMAT_PREFERRED = (
    "The details of the Company’s redeemable convertible preferred stock are as follows: "
    "Category Details Issuance Date May 22, 2015 Outstanding shares 37,500 shares Par Value"
    " KRW5,000 (equivalent to $ 3.7) Issuance Amount KRW750,000,000 (equivalent to $ "
    "552,934) Conversion Price KRW20,000 (equivalent to $ 14.7) Conversion Period From the "
    "day following the issuance date until 10 years later (Subsequently automatically "
    "converted to common stock) Conversion Ratio 1 preferred share to 1 common share "
    "(certain adjustments may apply based on the IPO offering price) Redemption Guaranteed "
    "Yield Annual 5.8% Redemption Claimable Period After 42 months from the issuance date, "
    "until the conversion Dividends participating cumulative, annual 1% The conversion "
    "ratio of the redeemable convertible preferred stock is subject to adjustment upon the "
    "occurrence of certain events specified in the 2015 agreement such as IPOs, stock "
    "dividend, etc., and the additional issuance, by refixing at 70 percent of the initial "
    "convertible price as a minimum level."
)

EMAT_ITEMS = (
    "1.01",
    "2.01",
    "2.03",
    "3.02",
    "3.03",
    "5.01",
    "5.02",
    "5.03",
    "5.05",
    "5.06",
    "7.01",
    "9.01",
)


def test_a_split_dated_by_month_and_year_long_ago_is_past():
    """EMAT's de-SPAC 8-K of 2026-01-09 (a charter 8-K: the restatement screen) files the target's statements
    (EX-99.4): "In June 2022, the Company implemented a 1-for-10 stock split" — a private company's split, dated by
    month and year only, which no date pattern read: 1 -> 10 with no date (forward by the filing's one
    direction cue, a preferred stock's "stock dividend" adjustment term in EX-99.5)."""
    ref = _ref("0001213900-26-002600", "8-K", "2026-01-09", EMAT_ITEMS)
    docs = [
        FilingDocument(type="EX-99.4", filename="ea026711701ex99-4_evolution.htm", raw=EMAT_SPLIT),
        FilingDocument(
            type="EX-99.5", filename="ea026711701ex99-5_evolution.htm", raw=EMAT_PREFERRED
        ),
    ]
    assert screen_of(ref) == SCREEN_RESTATED
    x = extract_for(docs, ref)
    assert (x.has_ratio, x.abstain_reason, x.effective_date) == (False, PAST_ONLY, None)
    assert "In June 2022" in x.passage


CD_RESTATED = (
    "We have revised the number of ordinary shares amounts in the revised consolidated "
    "statements of Changes in Shareholders’ Equity for the six months ended June 30, 2022, "
    "to retroactively present our 1-for-400 share consolidation in February 2023 back to "
    "the earliest period presented as stipulated in SAB 4C."
)


def test_cds_restated_consolidation_stays_a_past_splits():
    """CD's interim 6-K of 2025-12-10 (EX-99.1) restates its February 2023 1-for-400 ("to retroactively present
    our 1-for-400 share consolidation in February 2023"). PINNED, not a failing test: since PR-A it reads past ratio
    only — its restatements also carry full past dates ("June 30, 2022"; "On February 28, 2023, when the Share
    Consolidation was effective"), and the filing dates nothing currently."""
    ref = _ref("0001493152-25-027066", "6-K", "2025-12-10")
    docs = [FilingDocument(type="EX-99.1", filename="ex99-1.htm", raw=f"{CD_RATIO} {CD_RESTATED}")]
    x = extract_for(docs, ref)
    assert (x.has_ratio, x.abstain_reason, x.effective_date) == (False, PAST_ONLY, None)


NMG_FIGURES = (
    "Figure 7-1: Tectonic subdivisions of the Grenville Province 7-1 Figure 7-2: Grenville "
    "orogeny thrusting 7-2 Figure 7-3: Principal divisions of the Grenville Province and "
    "location of the Tony Block 7-3 Figure 7-4: Cross-section of the Grenville Province "
    "centered over the Morin Terrane 7-4 Figure 7-5: Terranes adjacent to the Tony Block "
    "7-5 Figure 7-6: Simplified geological model of the West Zone and proposed open pit "
    "7-11 Figure 7-7: Geology and major mineral deposits of the Grenville Province 7-13 "
    "Figure 9-1: Significant 2013-2022 exploration results 9-4 Figure 9-2: 2013 and 2015 "
    "Airborne TDEM survey results 9-6 Figure 9-3: 2014-2019 PhiSpy survey results 9-8 "
    "Figure 9-4: Trench TO-16-TR-11, looking to the east 9-12 Figure 10-1: 2015-2019 "
    "Channel sampling and delineation drilling programs, West Zone 10-4 Figure 10-2: 2015 "
    "Trenching and drilling program, South-East and South-West zones 10-5 Figure 10-3: "
    "2014-2015 Trenching and 2015 drilling program, North, North-East, and East zones 10-6 "
    "Figure 10-4: Drilling and trenching programs other than for Resource Estimate use, "
    "West Zone 10-7 Figure 10-5: West Zone drill hole section W+0200 10-12 Figure 10-6: "
    "West Zone drill hole section W+0900 10-12 Figure 10-7: West Zone drill hole section "
    "W+1700 10-13 Figure 10-8: Drill hole section S2900 10-17 Figure 10-9: Drill hole "
    "section S1500 10-18 Figure 11-1: Comparison between quarter-core and half-core C(g) "
    "results 11-2 Figure 11-2: Core box picture after core splitting and sampling 11-3"
)


def test_a_list_of_figures_is_no_colon_ratio():
    """NMG's 6-K of 2025-11-18 files an NI 43-101 technical report (EX-99.1) whose list of figures runs on with no
    full stop (here a verbatim slice of that sentence): "Figure 9-2: 2013 and 2015 Airborne TDEM survey results"
    read as a 2:2013 ratio — the colon's cue "ratio" matched inside "exploration", "core splitting" put a split
    in the sentence and "Tectonic subdivisions" made it a forward one: 2 -> 2013 with no date."""
    ref = _ref("0001104659-25-113538", "6-K", "2025-11-18")
    docs = [FilingDocument(type="EX-99.1", filename="tm2530895d2_ex99-1.htm", raw=NMG_FIGURES)]
    x = extract_for(docs, ref)
    assert (x.has_ratio, x.abstain_reason, x.mentions) == (False, "no split ratio found", ())


def test_a_year_is_never_a_side_of_a_colon_ratio():
    """A note heading "Note 12: 2024 Reverse Stock Split" puts a year beside a colon and a split word: read as a
    2024 -> 12 reverse split. A year (1900-2100) is never a side of a colon ratio."""
    x = _x("Note 12: 2024 Reverse Stock Split. The Company has no other share split.")
    assert (x.has_ratio, x.abstain_reason) == (False, "no split ratio found")


CRML_WARRANTS = (
    "The Empery Warrants provide the PIPE Investor with 3 Additional Shares for each "
    "Ordinary Share that the PIPE Investor purchases upon exercise of the PIPE Investors’ "
    "Warrants."
)


def test_extra_shares_for_each_share_purchased_are_no_split():
    """CRML's 6-K of 2025-03-19 (EX-99.2): "The Empery Warrants provide the PIPE Investor with 3 Additional Shares
    for each Ordinary Share that the PIPE Investor purchases" — a warrant term, with no split word in the sentence,
    read as a 4-for-1 forward split: 1 -> 4 with no date. With no split word, "A additional shares for each share"
    states a split only for shares HELD."""
    ref = _ref("0001213900-25-024970", "6-K", "2025-03-19")
    docs = [
        FilingDocument(type="EX-99.2", filename="ea023442601ex99-2_critical.htm", raw=CRML_WARRANTS)
    ]
    x = extract_for(docs, ref)
    assert (x.has_ratio, x.abstain_reason, x.mentions) == (False, "no split ratio found", ())


@pytest.mark.parametrize(
    ("text", "new"),
    [
        # VSTD's words without the sentence's split word
        (
            "Each holder will receive three additional shares of common stock for each then-held share.",
            4,
        ),
        # EMBJ's ADS ratio change (the module docstring)
        ("Holders will receive three additional ADSs for every ADS held on the record date.", 4),
        # NOW's 5-for-1
        ("Holders will receive four additional shares for each share of Common Stock held.", 5),
        ("Holders will receive one additional share for each share owned.", 2),
        ("Holders will receive one additional share for each share of record.", 2),
    ],
    ids=["then-held", "held-after", "held-later", "owned", "of-record"],
)
def test_extra_shares_for_each_share_held_still_read_with_no_split_word(text, new):
    """PINNED (each reads so before and after): the shares a holder receives on top of the ones it holds."""
    x = _x(text)
    assert (x.ratio_from, x.ratio_to) == (Decimal(1), Decimal(new))


MRLN_CONVERSION = (
    "AND SUBSIDIARIES NOTES TO CONSOLIDATED FINANCIAL STATEMENTS (in thousands, except "
    "share and per share amounts) To effectuate the conversions contemplated by the "
    "Exchange Agreements, the Company amended and restated its Certificate of Incorporation"
    " to add a contingent mandatory conversion feature to its redeemable convertible "
    "preferred stock (“Special Mandatory Conversion”) whereby if a holder of redeemable "
    "convertible preferred stock failed to purchase a minimum investment amount of Pre-PIPE"
    " Bridge Notes and Warrants, such redeemable convertible preferred stock held by the "
    "non-participating investor would automatically convert, on a ten-to-one basis, into "
    "shares of the Company’s common stock; otherwise, if the holder of redeemable "
    "convertible preferred stock satisfied its applicable minimum investment threshold "
    "contemplated by the Pre-PIPE Bridge Agreement, shares of redeemable convertible "
    "preferred stock held by such holder would be exchanged for shares of Prime Preferred "
    "Stock, as described below."
)


def test_mrlns_preferred_conversion_stays_no_split():
    """MRLN's de-SPAC 8-K of 2026-03-20 (EX-99.1, the target's notes): preferred stock "would automatically convert,
    on a ten-to-one basis, into shares of the Company's common stock". PINNED, not a failing test: since PR-A the
    sentence is about no split (its "CONSOLIDATED FINANCIAL STATEMENTS" is no consolidation of shares), so no
    ratio is read in it."""
    ref = _ref(
        "0001213900-26-032329",
        "8-K",
        "2026-03-20",
        ("1.01", "2.01", "3.02", "3.03", "4.01", "5.01", "5.02", "5.03", "5.06", "8.01", "9.01"),
    )
    docs = [FilingDocument(type="EX-99.1", filename="ea028257401ex99-1.htm", raw=MRLN_CONVERSION)]
    x = extract_for(docs, ref)
    assert (x.has_ratio, x.abstain_reason, x.mentions) == (False, "no split ratio found", ())


DCX_RATIO = (
    "(b) Reverse Stock Split On June 28, 2024, the Company announced a 1-for-30 reverse "
    "stock split of its outstanding common shares (“Reverse Stock Split”) that became "
    "effective on July 8, 2024."
)
DCX_RESTATED = (
    "Share data as of June 30, 2024 and December 31, 2023 have been retroactively restated "
    "to give effect to the 1-for-30 reverse stock split that is discussed in note 1(b)."
)
DCX_TRADING = (
    "Beginning on July 8, 2024, the Company’s ordinary shares began trading on the Nasdaq "
    "Global Market on a split adjusted basis."
)
VSTD_RATIO = (
    "On July 18, 2025, Yoshiharu Global Co., a Delaware corporation (the “Company announced"
    " a 4 for 1 forward stock split of the Company’s Class A and Class B Common Stock, "
    "whereby for each share of Common Stock held, the holder will receive three additional "
    "shares in the respective class."
)
VSTD_HELD = (
    "At the effective time of the forward stock split, each holder of one share of Class A "
    "Common Stock outstanding on July 28, 2025, the record date for the stock split, will "
    "receive a dividend of three additional shares of common stock for each then-held "
    "share."
)
VSTD_TRADING = (
    "The Common Stock will continue to trade on The Nasdaq Capital Market under the symbol "
    "“YOSH” and will begin trading on a post-split basis when the market opens on July 31, "
    "2025."
)
RJET_RATIO = (
    "The Board of Directors of Mesa Air Group, Inc. (the “Company” or Mesa”) has approved a"
    " reverse stock split of the Company’s issued and outstanding shares of common stock, "
    "no par value per share (the “Common Stock”), at a ratio of 15:1, accompanied by a "
    "corresponding decrease in the Company’s authorized shares of Common Stock (the "
    "“Reverse Stock Split”), such that, following the consummation of the Reverse Stock "
    "Split, the number of authorized shares of Common Stock shall be reduced from "
    "125,000,000 to 8,333,333."
)
RJET_EVERY = (
    "At the effective time of the Reverse Stock Split, every 15 shares of the Common Stock "
    "will be automatically reclassified and combined into one share of Common Stock."
)
RJET_TRADING = (
    "The Company expects to file the Certificate of Change following the completion of "
    "certain related administrative processes and anticipates that the Effective Time of "
    "the Reverse Stock Split will occur on or about 5:00 p.m. Eastern Time on November 24, "
    "2025, with the Common Stock trading on a post-split basis under the Company’s expected"
    " new Nasdaq trading symbol, “RJET,” at the market open on November 25, 2025 under the "
    "new CUSIP number of 590479408 (assuming the pre-market consummation of the previously "
    "announced merger with Republic Airways Holdings Inc. (“Republic Airways”))."
)
MSTR_RATIO = (
    "BTC Yield and Basic and Assumed Diluted Shares Outstanding 12/31/2023 6/30/2024 "
    "9/19/2024 Total Bitcoin Holdings 189,150 226,331 252,220 Shares Outstanding (in ‘000s)"
    " (1) Class A 149,041 171,030 182,995 Class B 19,640 19,640 19,640 Basic Shares "
    "Outstanding (2) 168,681 190,670 202,635 2025 Convertible Shares @$39.80 16,332 3,659 —"
    " 2027 Convertible Shares @$143.25 7,330 7,330 7,330 2028 Convertible Shares @$183.19 —"
    " — 5,513 2030 Convertible Shares @$149.77 — 5,342 5,342 2031 Convertible Shares "
    "@$232.72 — 2,594 2,594 2032 Convertible Shares @$204.33 — 3,915 3,915 Options "
    "Outstanding 12,936 5,916 5,704 RSU/PSU Unvested 2,359 2,244 2,085 Assumed Diluted "
    "Shares Outstanding (3) 207,638 221,670 235,118 BTC Yield % (Quarter to Date) 5.1 % BTC"
    " Yield % (Year to Date) 17.8 % (1) On July 11, 2024, the Company announced a 10-for-1 "
    "stock split of the Company’s class A common stock and class B common stock."
)
MSTR_DIVIDEND = (
    "The stock split was effected by means of a stock dividend to the holders of record of "
    "the Company’s class A common stock and class B common stock as of the close of business "
    "on August 1, 2024, the record date for the dividend."
)
MSTR_TRADING = (
    "The dividend was distributed after the close of trading on August 7, 2024 and trading "
    "commenced on a split-adjusted basis at market open on August 8, 2024."
)

# Each must still read exactly as it did (the whole filings are in the replay proof:
# docs/temp/split-reader-misreads-evidence/pr-b/)
STILL_READ_B = [
    # DCX: "became effective on July 8, 2024", 84 days before the 6-K (inside the 90-day line), beside a
    # restatement dated by a balance date more than 90 days back
    (
        "0001493152-24-038804",
        "6-K",
        "2024-09-30",
        None,
        ("EX-99.1", "ex99-1.htm"),
        (DCX_RATIO, DCX_RESTATED, DCX_TRADING),
        ("30", "1"),
        "2024-07-08",
        "trading",
    ),
    # VSTD: "three additional shares of common stock for each then-held share" (a split word in the sentence)
    (
        "0001641172-25-020290",
        "8-K",
        "2025-07-18",
        ("3.03", "7.01", "9.01"),
        ("8-K", "form8-k.htm"),
        (VSTD_RATIO, VSTD_HELD, VSTD_TRADING),
        ("1", "4"),
        "2025-07-31",
        "trading",
    ),
    # RJET: "at a ratio of 15:1" (an 8.01 catch-all)
    (
        "0001193125-25-290300",
        "8-K",
        "2025-11-21",
        ("8.01",),
        ("8-K", "d56096d8k.htm"),
        (RJET_RATIO, RJET_EVERY, RJET_TRADING),
        ("15", "1"),
        "2025-11-25",
        "trading",
    ),
    # MSTR: "On July 11, 2024, the Company announced a 10-for-1 stock split" (a catch-all filed 71 days later;
    # "announced" is no done word), forward by "effected by means of a stock dividend"
    (
        "0001193125-24-222498",
        "8-K",
        "2024-09-20",
        ("7.01", "8.01"),
        ("8-K", "d822569d8k.htm"),
        (MSTR_RATIO, MSTR_DIVIDEND, MSTR_TRADING),
        ("1", "10"),
        "2024-08-08",
        "trading",
    ),
]


@pytest.mark.parametrize(
    ("acc", "form", "filed", "items", "doc", "sentences", "ratio", "when", "kind"),
    STILL_READ_B,
    ids=["DCX", "VSTD", "RJET", "MSTR"],
)
def test_a_split_beside_the_new_ratio_rules_still_reads(
    acc, form, filed, items, doc, sentences, ratio, when, kind
):
    ref = _ref(acc, form, filed, items)
    docs = [FilingDocument(type=doc[0], filename=doc[1], raw=s) for s in sentences]
    x = extract_for(docs, ref)
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind, x.date_note) == (date.fromisoformat(when), kind, None)


# --- 2026-11-26: a stated ratio that is not the listed security's (split-reader misreads, PR-C) --------------------
#
# The same 800-day check planned rows from a ratio the filing states for something other than the listed security: the
# ordinary shares' consolidation beside an ADS RATIO CHANGE (R1: the ADS moves by its own factor, and OPRA's and MOB's
# not at all), a split made FOR THE IPO, before any public price (R6), and a listed subsidiary's earnings release filed
# as an exhibit (R5). Each is a REAL full submission cut to the documents the reader reads (``tests/fixtures/splits/
# README.md``), read the way the pipeline reads it (``screen_of`` / ``extract_for``: its form and the 8-K items of its
# SGML header, its filing date; none of the six companies has a split on prod's list). Until 2026-11-26 each read as
# its comment says (the reading after PR-B).

ADS_SPLIT = [
    # OPRA's AGM notice (6-K of 2024-11-04): "every two (2) issued and unissued ordinary shares … be consolidated into
    # one (1)", and "a change in the ADS ratio … from one (1) ADS representing two (2) ordinary shares to … one (1) ADS
    # representing one (1) ordinary share": the listed ADS keeps its price. Read 2 -> 1, legal 2024-12-06
    ("0000950170-24-120712.txt", "the ratio between shares and ADSs can then be changed"),
    # MOB's meeting notice (6-K of 2025-09-17): the unlisted ordinary shares consolidated "on a 275 to 1 basis", and
    # "each ADS will represent one Share, instead of … (275) pre-Consolidation Shares". Read 275 -> 1, legal 2025-10-10
    ("0001213900-25-088581.txt", "each ADS will represent one Share"),
    # AZUL (6-K of 2026-04-15): a 150,000-to-1 reverse split of its B3 common shares, and the ADS from 500,000 old
    # shares to two new ones, so holders "receive 0.666666 of a new ADS for every one (1) existing ADS" (the ADS's
    # own 5-for-3 forward split, as an extra 0.666666 ADS). Read 1 -> 0.666666 (a reverse), trading 2026-04-20 (the B3
    # ex-date)
    ("0001292814-26-002268.txt", "will change the ADS-to-common share ratio"),
]


@pytest.mark.parametrize(("name", "phrase"), ADS_SPLIT, ids=["OPRA", "MOB", "AZUL"])
def test_an_ads_ratio_change_beside_a_share_split_abstains(name, phrase):
    """D1: the ordinary shares' ratio is not the listed ADS's when the ADS ratio changes too — abstain, shown to the
    steward (an UNPLACED line), never a row."""
    screen, x = _as_prod_reads(name, "6-K", None)
    assert screen == SCREEN_RESTATED
    assert not x.has_ratio, (x.ratio_from, x.ratio_to)
    assert x.abstain_reason == split_filing.ADS_RATIO_CHANGE
    assert x.about_a_split
    assert phrase in x.passage


PRE_LISTING_FILINGS = [
    # BETA's charter 8-K of 2025-11-06: "On November 4, 2025, in connection with the IPO …, the following events
    # occurred: … (iv) a 6.3811681-for-1 forward split of the Company's capital stock … (collectively with …, the “IPO
    # Recapitalization”)". Read 1 -> 6.3811681, legal 2025-11-04
    (
        "0001193125-25-269718.txt",
        ("1.01", "3.02", "3.03", "5.02", "5.03", "8.01", "9.01"),
        SCREEN_RESTATED,
        "6.3811681-for-1 forward split",
    ),
    # FRVO's earnings 8-K of 2026-06-22 (Items 2.02 / 7.01 / 9.01: a catch-all): "(1) Shares for periods presented have
    # been retroactively adjusted to reflect the 0.7194 -for-1 reverse stock split effected on May 14, 2026 in
    # connection with the Company's IPO" — the day it listed on Nasdaq. Read 1 -> 0.7194, legal 2026-05-14
    (
        "0001628280-26-044474.txt",
        ("2.02", "7.01", "9.01"),
        SCREEN_CATCH_ALL,
        "0.7194 -for-1 reverse stock split effected on May 14, 2026 in connection with the Company",
    ),
]


@pytest.mark.parametrize(
    ("name", "items", "want_screen", "phrase"), PRE_LISTING_FILINGS, ids=["BETA", "FRVO"]
)
def test_a_split_made_for_the_ipo_is_pre_listing(name, items, want_screen, phrase):
    """D2: a split made for the IPO came before any public price — no listed price moves by it. An abstain, shown as
    an UNPLACED line (not set aside as no news)."""
    screen, x = _as_prod_reads(name, "8-K", items)
    assert screen == want_screen
    assert not x.has_ratio, (x.ratio_from, x.ratio_to)
    assert x.abstain_reason == split_filing.PRE_LISTING
    assert x.about_a_split
    assert phrase in x.passage


def test_a_listed_subsidiarys_release_filed_as_an_exhibit_is_not_read():
    """ANTA's 6-K of 2026-04-30 says: "On April 30, 2026, Aurelion, Inc. (Nasdaq: AURE), a publicly traded subsidiary
    of Antalpha Platform Holding Company, issued a press release …, incorporated by reference as Exhibit 99.1". The
    exhibit is Aurelion's earnings release, and its "1-for-10 share consolidation effective February 19, 2026" is
    Aurelion's: read as ANTA's 10 -> 1, legal 2026-02-19."""
    name = "0001171843-26-002879.txt"
    screen, x = _as_prod_reads(name, "6-K", None)
    assert screen == SCREEN_RESTATED
    assert (x.has_ratio, x.abstain_reason, x.mentions) == (False, "no split ratio found", ())
    assert [d.type for d in relevant_documents(documents(name))] == ["6-K"]


# --- the same rules on direct inputs -----------------------------------------------------------------------------


def _doc6k(form: str, *exhibits: str) -> list[FilingDocument]:
    return [FilingDocument(type="6-K", filename="f6k.htm", raw=form)] + [
        FilingDocument(type=f"EX-99.{i}", filename=f"ex99-{i}.htm", raw=t)
        for i, t in enumerate(exhibits, 1)
    ]


def test_a_share_consolidation_with_a_changed_ads_ratio_abstains():
    x = _x(
        "Every ten (10) ordinary shares will be consolidated into one (1) ordinary share, effective as of March 2, "
        "2026. The Company will change the ADS ratio from one (1) ADS representing ten (10) ordinary shares to one "
        "(1) ADS representing one (1) ordinary share."
    )
    assert not x.has_ratio, (x.ratio_from, x.ratio_to)
    assert x.abstain_reason == split_filing.ADS_RATIO_CHANGE
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 2), "legal")


@pytest.mark.parametrize(
    ("text", "ratio"),
    [
        # the ADS ratio kept: the ADS moves with the shares
        (
            "Every ten (10) ordinary shares will be consolidated into one (1) ordinary share, effective as of March "
            "2, 2026. The ADS ratio will remain unchanged, and each ADS will continue to represent two ordinary "
            "shares.",
            ("10", "1"),
        ),
        # an ADS ratio change alone is the ADS's own split (NIPG's, SCAG's)
        (
            "The Company will change the ADS ratio from one (1) ADS representing two (2) ordinary shares to one (1) "
            "ADS representing sixty (60) ordinary shares, effective as of March 2, 2026. For ADS holders, the ADS "
            "ratio change will have the same effect as a one-for-thirty reverse ADS split.",
            ("30", "1"),
        ),
        # … and one that says the shares are untouched, beside a share split the filing restates
        (
            "The ADS ratio change will have the same effect as a one-for-thirty reverse ADS split, effective as of "
            "March 2, 2026. The ADS ratio change will have no impact on the Company's underlying ordinary shares. "
            "The ordinary shares were consolidated in 2009.",
            ("30", "1"),
        ),
    ],
    ids=["ads-ratio-kept", "ads-change-alone", "underlying-untouched"],
)
def test_an_ads_ratio_change_with_no_share_split_still_reads(text, ratio):
    """PINNED (each reads so before and after)."""
    x = _x(text)
    assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (
        Decimal(ratio[0]),
        Decimal(ratio[1]),
        None,
    )


def test_a_split_made_in_connection_with_the_ipo_abstains():
    x = _x(
        "In connection with its initial public offering, the Company effected a 10-for-1 forward stock split, "
        "effective as of March 2, 2026."
    )
    assert not x.has_ratio, (x.ratio_from, x.ratio_to)
    assert x.abstain_reason == split_filing.PRE_LISTING


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # another split beside the IPO's: two ratios, as before
        (
            "In connection with the IPO, the Company effected a 10-for-1 forward stock split. On March 2, 2026, the "
            "Company effected a 1-for-20 reverse stock split.",
            "more than one ratio stated",
        ),
        # a merger is no listing (ORKA's)
        (
            "In connection with the Merger, the Company effected a 1-for-12 reverse stock split, which became "
            "effective on March 2, 2026.",
            None,
        ),
    ],
    ids=["another-split", "merger"],
)
def test_a_split_not_made_for_the_ipo_reads_as_before(text, want):
    """PINNED (each reads so before and after)."""
    x = _x(text)
    if want is None:
        assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (Decimal(12), Decimal(1), None)
    else:
        assert x.abstain_reason.startswith(want)


ACME_RELEASE = (
    "Acme Mining Corp. (NYSE American: ACM) today announced a 1-for-10 reverse stock split, effective as of March 4, "
    "2026."
)
OWN_RELEASE = "The Company approved a 1-for-5 reverse stock split, effective as of March 4, 2026."


@pytest.mark.parametrize(
    "form",
    [
        "On March 2, 2026, Acme Mining Corp. (NYSE American: ACM), a publicly listed affiliate of the Company, issued "
        "a press release, furnished as Exhibit 99.1 hereto.",
        # no exhibit number: the release whose lead names the subsidiary's ticker
        "Acme Mining Corp. (NYSE American: ACM), a publicly traded subsidiary of the Company, issued the press "
        "release attached to this report.",
        # a run-on cover: only the exhibit named right after it, never the whole exhibit index
        "On March 2, 2026, Acme Mining Corp. (NYSE American: ACM), a publicly listed affiliate of the Company, issued "
        "a press release, furnished as Exhibit 99.1 hereto EXHIBIT INDEX Exhibit No. Description Exhibit 99.1 Press "
        "release of Acme Mining Corp. Exhibit 99.2 Press release of the Company",
    ],
    ids=["exhibit-named", "ticker-named", "exhibit-index"],
)
def test_a_listed_affiliates_release_is_not_read_beside_the_filers_own(form):
    docs = _doc6k(form, ACME_RELEASE, OWN_RELEASE)
    assert [d.type for d in relevant_documents(docs)] == ["6-K", "EX-99.2"]
    x = extract_split(docs)
    assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (Decimal(5), Decimal(1), None)


# Each must still read exactly as it did (the whole filings are in the replay proof:
# docs/temp/split-reader-misreads-evidence/pr-c/)
ABTC_FORM = (
    *STILL_READ[0][6],
    "As a result of the Mergers, American Bitcoin Holdings LLC (“ABH”), a Delaware limited liability company and a "
    "wholly owned subsidiary of Hut 8 Corp. (“Hut 8”), holds 585,779,924 shares of Combined Company Class B Common "
    "Stock, representing approximately 80% of the voting power of the Combined Company’s capital stock.",
)
ABTC_RELEASE = (
    "Abo ut American Bitcoin American Bitcoin Corp., a majority-owned subsidiary of Hut 8 Corp., is a Bitcoin "
    "accumulation platform focused on building America’s Bitcoin infrastructure platform."
)
ORKA_FORM = (
    "Following consummation of the Merger, the Company effected a 1-for-12 reverse stock split (the “Reverse Stock "
    "Split”) of the common stock, par value $0.001 per share, of the Company (“Company Common Stock”), which became "
    "effective on September 3, 2024."
)
ORKA_NOTE = (
    "In connection with the Merger Agreement discussed above, the Company effected a one-for-twelve reverse stock "
    "split of the Company’s issued and outstanding shares of common stock on September 3, 2024 without any change "
    "in the par value per share."
)

STILL_READ_C = [
    # ABTC: "a majority-owned subsidiary of Hut 8" in its OWN release (EX-99.1); its form names Hut 8's subsidiary
    (
        "0001213900-25-083726",
        "8-K",
        "2025-09-03",
        STILL_READ[0][3],
        [("8-K", "ea0255440-8k_american.htm", s) for s in ABTC_FORM]
        + [("EX-99.1", "ea025544001ex99-1_american.htm", ABTC_RELEASE)],
        ("5", "1"),
        "2025-09-03",
        "trading",
    ),
    # ORKA (8.01 catch-all, 72 days after): "in connection with the Merger" is no listing
    (
        "0001213900-24-098530",
        "8-K",
        "2024-11-14",
        ("8.01", "9.01"),
        [
            ("8-K", "ea0221362-8k_oruka.htm", ORKA_FORM),
            ("EX-99.1", "ea022136201ex99-1_oruka.htm", ORKA_NOTE),
        ],
        ("12", "1"),
        "2024-09-03",
        "legal",
    ),
]


@pytest.mark.parametrize(
    ("acc", "form", "filed", "items", "docs", "ratio", "when", "kind"),
    STILL_READ_C,
    ids=["ABTC", "ORKA"],
)
def test_a_split_beside_the_other_security_rules_still_reads(
    acc, form, filed, items, docs, ratio, when, kind
):
    ref = _ref(acc, form, filed, items)
    fds = [FilingDocument(type=t, filename=f, raw=r) for t, f, r in docs]
    assert [d.type for d in relevant_documents(fds)] == [t for t, _, _ in docs]
    x = extract_for(fds, ref)
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind, x.date_note) == (date.fromisoformat(when), kind, None)


@pytest.mark.parametrize(
    ("name", "ratio", "when", "kind"),
    [
        ("0001493152-26-031419.txt", ("30", "1"), "2026-07-06", "legal"),  # NIPG
        ("0001292814-26-005066.txt", ("1", "4"), "2026-11-16", "trading"),  # EMBJ
        ("0001493152-25-018165.txt", ("1", "5"), "2025-10-29", "trading"),  # NVA
        ("0001213900-26-113338.txt", ("15", "1"), "2026-10-27", "legal"),  # SCAG
        # LGHL: its 2023 ADS ratio change, a past split's
        ("0001213900-24-111258.txt", None, None, None),
    ],
    ids=["NIPG", "EMBJ", "NVA", "SCAG", "LGHL"],
)
def test_an_ads_ratio_change_alone_still_reads_as_the_ads_split(name, ratio, when, kind):
    """PINNED (each reads so before and after): an ADS ratio change with no split of the shares beside it — the
    ADS's own split — read as prod reads the 6-K."""
    screen, x = _as_prod_reads(name, "6-K", None)
    assert screen == SCREEN_RESTATED
    if ratio is None:
        assert (x.has_ratio, x.abstain_reason) == (False, PAST_ONLY)
        return
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind) == (date.fromisoformat(when), kind)


# --- 2026-11-27: prod's split corpus replayed (split-reader misreads, PR-A review) ------------------------------
#
# PR-A's first rule (a trading date comes only from a sentence about the split, or one with a cue only a split has)
# dropped first sessions prod's list holds: the sentence names the split only by what the shares trade AS — "on a
# consolidation-adjusted basis", "the new ADS to ordinary share ratio". Each is a full submission read as the nightly
# reads it (a 6-K: the restatement screen), with the confirmed splits in effect by its filing date (none).
SPLIT_ONLY_TRADING = [
    # Chijet's 6-K of 2025-10-29: "will effect a one hundred (100)-for-one (1) share consolidation … on November 3,
    # 2025" and "beginning with the opening of trading on November 3, 2025, the Company's class A ordinary shares will
    # trade on the Nasdaq Capital Market on a consolidation-adjusted basis" (prod lists 100:1, 2025-11-03)
    ("0001493152-25-020018.txt", ("100", "1"), "2025-11-03"),
    # BioLineRx's 6-K of 2025-01-17: "will have the same effect as a 1-for-40 reverse stock split of the ADSs" and
    # "The new ADS to ordinary share ratio of 1 for 600 will be effective prior to the commencement of trading on the
    # Nasdaq Capital Market on Thursday, January 30, 2025" (prod lists 40:1, 2025-01-30)
    ("0001178913-25-000145.txt", ("40", "1"), "2025-01-30"),
    # Brainsway's 6-K of 2026-02-17: "each existing ADS holder will receive one additional ADS for each ADS held" and
    # "The new 1-to-1 ADS to ordinary share ratio will take effect prior to the commencement of trading on the NASDAQ
    # on Tuesday, March 3, 2026" — beside the legal "effective March 3, 2026" (prod lists 1:2, 2026-03-03)
    ("0001171843-26-000847.txt", ("1", "2"), "2026-03-03"),
]


@pytest.mark.parametrize(
    ("name", "ratio", "first_session"), SPLIT_ONLY_TRADING, ids=["CJET", "BLRX", "BWAY"]
)
def test_a_sentence_on_what_the_shares_trade_as_dates_the_split(name, ratio, first_session):
    """Trading "on a consolidation-adjusted basis" or under "the new ADS to ordinary share ratio" is a cue only a
    split has: the sentence's date is the first session, as "split-adjusted" and "the new ADS ratio" are.
    """
    screen, x = _as_prod_reads(name, "6-K", None)
    assert screen == SCREEN_RESTATED
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind) == (date.fromisoformat(first_session), "trading")


@pytest.mark.parametrize(
    "sentence",
    [
        # a listing transfer of the ADSs: trading, ADSs, no ratio and no split
        "The Company's ADSs will begin trading on the Nasdaq Capital Market at the opening of trading on March 3, "
        "2026, under the existing ticker symbol.",
        # the ratio the ADSs already trade under, restated beside a ticker change
        "Each ADS continues to represent ten ordinary shares; the ADSs will begin trading under the new ticker "
        "symbol at the opening of trading on March 3, 2026.",
    ],
    ids=["listing", "ticker"],
)
def test_ads_trading_without_a_ratio_change_never_dates_a_split(sentence):
    """The ADS-ratio cue needs the ratio itself ("ADS ratio", "ADS to ordinary share ratio"): ADSs that merely trade
    somewhere new date nothing."""
    ref = _ref("0001493152-26-000001", "6-K", "2026-02-17")
    ratio = "The Company will effect a ratio change of its ADSs from one ADS for one share to one ADS for ten shares."
    docs = [FilingDocument(type="6-K", filename="t.htm", raw=f"{ratio} {sentence}")]
    x = extract_for(docs, ref)
    assert x.effective_date is None and x.date_kind is None


def test_a_new_split_of_a_past_splits_ratio_is_never_taken_for_the_past_one():
    """Beyond Air's charter 8-K of 2026-06-26 (Items 2.02 / 5.03 / 7.01 / 9.01: the restatement screen), read knowing
    its listed 1-for-20 of 2025-07-14: "Following stockholder approval at the Company's June 18 special meeting, the
    Board approved a 1-for-20 reverse stock split, which is expected to enable the Company to regain compliance ahead
    of the deadline" is the NEXT 1-for-20 (prod lists it, first session 2026-07-13), undated here. The filing dates
    nothing currently and restates the old one-for-twenty, but a sentence about what WILL happen is never that past
    split's — unless a dated past cue says so (the rule before 2026-11-26)."""
    screen, x = _as_prod_reads(
        "0001493152-26-030305.txt",
        "8-K",
        ("2.02", "5.03", "7.01", "9.01"),
        _known(("20", "1", "2025-07-14")),
    )
    assert screen == SCREEN_RESTATED
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to, x.effective_date) == (Decimal(20), Decimal(1), None)


def test_a_subdivision_of_issued_and_unissued_shares_is_about_a_split():
    """Hesai's 6-K of 2026-07-10: "Effective before the commencement of trading on The Stock Exchange of Hong Kong
    Limited on July 10, 2026, the Company subdivided each of its issued and unissued ordinary shares into eight (8)
    ordinary shares." is a share subdivision ("unissued" between the qualifiers, as "authorised" in a
    consolidation): a sentence about a split, so its date is read, as before 2026-11-26 (the ratio is not, either
    way)."""
    ref = _ref("0001104659-26-082432", "6-K", "2026-07-10")
    raw = (
        "Effective before the commencement of trading on The Stock Exchange of Hong Kong Limited on July 10, 2026, "
        "the Company subdivided each of its issued and unissued ordinary shares into eight (8) ordinary shares."
    )
    x = extract_for([FilingDocument(type="6-K", filename="t.htm", raw=raw)], ref)
    assert (x.effective_date, x.date_kind) == (date(2026, 7, 10), "trading")


# --- 2026-11-27: prod's split corpus replayed (split-reader misreads, PR-C review) ------------------------------
#
# PR-C's ADS rule (an ADS ratio change beside a split of the shares abstains) hit two splits prod lists: a sentence
# that EQUATES the ADS change to a split was taken for a split of the shares beside it, and a filing that describes
# ONE event twice — the ADS ratio change and "a reverse share split" of the same factor — abstained. Each is a full
# submission read as the nightly reads it (a 6-K: the restatement screen), knowing the confirmed splits in effect by
# its filing date.


def test_a_sentence_equating_the_ads_change_to_a_split_reads_the_ads_split():
    """BioLineRx's 6-K of 2025-01-21 (an investor letter, EX-99.1): "… our intent to change the ratio of our American
    Depositary Shares to ordinary shares, from 15 ordinary shares per ADS to 600 ordinary shares per ADS. This is
    equivalent to a 1-for-40 reverse stock split, and will go into effect on January 30, 2025." The second sentence
    is the ADS change itself, never a split of the shares beside it (prod lists 40:1, first session 2025-01-30).
    Until this rule: ``ADS_RATIO_CHANGE``. Since 2026-11-27 its date is read too: "will go into effect on" is a legal
    cue (until then no date cue, so no date; its sibling 6-K of 2025-01-17 dates it as a first session,
    ``test_a_sentence_on_what_the_shares_trade_as_dates_the_split``).
    """
    screen, x = _as_prod_reads("0001178913-25-000177.txt", "6-K", None)
    assert screen == SCREEN_RESTATED
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(40), Decimal(1))
    assert (x.effective_date, x.date_kind) == (date(2025, 1, 30), "legal")
    assert (
        "This is equivalent to a 1-for-40 reverse stock split, and will go into effect on January 30, 2025"
        in x.passage
    )


def test_an_ads_ratio_change_of_the_splits_own_factor_reads_the_split():
    """Trident Digital's 6-K of 2026-05-01: "… to change the ratio of its ADS to Class B ordinary shares from the
    previous ratio of one (1) ADS to eight (8) Class B ordinary shares to a new ratio of one (1) ADS to two hundred
    and forty (240) Class B ordinary shares. Effective April 24, 2026, the Company effected a 1-for-30 reverse share
    split." The ADS change's own factor (240 / 8 = 30) is the split's: one event, described twice (prod lists 30:1,
    2026-04-24, two sources agreeing). Until this rule: ``ADS_RATIO_CHANGE``."""
    screen, x = _as_prod_reads(
        "0001213900-26-051029.txt", "6-K", None, _known(("30", "1", "2026-04-24"))
    )
    assert screen == SCREEN_RESTATED
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(30), Decimal(1))
    assert (x.effective_date, x.date_kind) == (date(2026, 4, 24), "legal")


# --- the same rules on direct inputs -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "equates",
    [
        "This is equivalent to a 1-for-10 reverse stock split.",
        "This will have the same effect as a 1-for-10 reverse stock split.",
        "The change is the equivalent of a 1-for-10 reverse share split.",
        "This is analogous to a 1-for-10 reverse stock split.",
    ],
    ids=["equivalent-to", "same-effect-as", "the-equivalent-of", "analogous-to"],
)
def test_a_sentence_equating_the_ads_change_to_a_split_is_no_split_of_the_shares(equates):
    """No factor is stated here, so only the equating sentence decides: the ADS change alone, its own split."""
    x = _x(
        "The Company will change the ratio of its ADSs to ordinary shares, effective as of March 2, 2026. "
        + equates
    )
    assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (Decimal(10), Decimal(1), None)


@pytest.mark.parametrize(
    ("text", "ratio"),
    [
        # reverse, the ADS first: one (1) ADS from five (5) shares to fifty (50), a 1-for-10 reverse share split
        (
            "The Company will change the ratio of its ADSs to ordinary shares from one (1) ADS representing five (5) "
            "ordinary shares to one (1) ADS representing fifty (50) ordinary shares. Effective March 2, 2026, the "
            "Company effected a 1-for-10 reverse share split.",
            ("10", "1"),
        ),
        # forward, the shares first: from 60 shares per ADS to 12, a 5-for-1 forward share split
        (
            "The ADS ratio will change from 60 ordinary shares per ADS to 12 ordinary shares per ADS. Effective "
            "March 2, 2026, the Company effected a 5-for-1 forward share split.",
            ("1", "5"),
        ),
    ],
    ids=["reverse", "forward"],
)
def test_an_ads_ratio_change_of_the_stated_ratio_is_that_split(text, ratio):
    x = _x(text)
    assert x.abstain_reason is None, x.abstain_reason
    assert (x.ratio_from, x.ratio_to) == (Decimal(ratio[0]), Decimal(ratio[1]))
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 2), "legal")


@pytest.mark.parametrize(
    "text",
    [
        # the ADS's factor the other way round (OPRA's): ten shares per ADS to one, beside a ten-to-one consolidation
        "Every ten (10) ordinary shares will be consolidated into one (1) ordinary share, effective as of March 2, "
        "2026. The Company will change the ADS ratio from one (1) ADS representing ten (10) ordinary shares to one "
        "(1) ADS representing one (1) ordinary share.",
        # a factor that is not the split's: five shares per ADS to ten, beside a ten-to-one consolidation
        "Every ten (10) ordinary shares will be consolidated into one (1) ordinary share, effective as of March 2, "
        "2026. The Company will change the ADS ratio from one (1) ADS representing five (5) ordinary shares to one "
        "(1) ADS representing ten (10) ordinary shares.",
        # one side of the ADS change unstated (MOB's)
        "Every ten (10) ordinary shares will be consolidated into one (1) ordinary share, effective as of March 2, "
        "2026. Following the consolidation, each ADS will represent one ordinary share, instead of ten "
        "pre-consolidation shares.",
        # both sides stated, but new before old: the order is not read
        "Every ten (10) ordinary shares will be consolidated into one (1) ordinary share, effective as of March 2, "
        "2026. The Company will change the ADS ratio to one (1) ADS representing one hundred (100) ordinary shares, "
        "instead of one (1) ADS representing ten (10) ordinary shares.",
    ],
    ids=["other-way-round", "other-factor", "one-side", "new-first"],
)
def test_an_ads_ratio_change_of_another_or_no_factor_still_abstains(text):
    """PINNED (each reads so before and after): the ADS's factor differs from the shares' ratio, or is unreadable."""
    x = _x(text)
    assert not x.has_ratio, (x.ratio_from, x.ratio_to)
    assert x.abstain_reason == split_filing.ADS_RATIO_CHANGE


# --- 2026-11-27: "go into effect" is a legal cue (framework completion, F3a) ---------------------------------------
#
# BioLineRx's investor letter (above) dates its 1-for-40 only by "will go into effect on January 30, 2025", no date
# cue until 2026-11-27. The "go" family (go / goes / going / went / gone into effect) is now a legal cue, as "take(s)
# / took effect" is: like every legal cue it dates only a sentence about the split, a trading date still wins, and a
# date it reaches long before the filing is a past split's.


@pytest.mark.parametrize(
    "text",
    [
        "The 1-for-10 reverse stock split will go into effect on March 2, 2026.",
        "The 1-for-10 reverse stock split goes into effect at 12:01 a.m. Eastern Time on March 2, 2026.",
        "The 1-for-10 reverse stock split is going into effect on March 2, 2026.",
        "The 1-for-10 reverse stock split went into effect on March 2, 2026.",
        "The 1-for-10 reverse stock split has gone into effect as of March 2, 2026.",
    ],
    ids=["will-go", "goes-at-a-time", "going", "went", "gone"],
)
def test_go_into_effect_dates_the_split_as_a_legal_date(text):
    x = _x(text)
    assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (Decimal(10), Decimal(1), None)
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 2), "legal")
    assert "into effect" in x.passage


def test_a_split_that_went_into_effect_days_before_a_6k_is_dated_by_it():
    """Read as the nightly reads a 6-K (the restatement screen): a date two days before the filing is current."""
    ref = _ref("t", "6-K", "2026-03-04")
    raw = "The 1-for-10 reverse share split went into effect on March 2, 2026."
    x = extract_for([FilingDocument(type="6-K", filename="t.htm", raw=raw)], ref)
    assert screen_of(ref) == SCREEN_RESTATED
    assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (Decimal(10), Decimal(1), None)
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 2), "legal")


@pytest.mark.parametrize(
    ("form", "items", "screen"),
    [("8-K", ("8.01",), SCREEN_CATCH_ALL), ("6-K", None, SCREEN_RESTATED)],
    ids=["catch-all-8k", "6k"],
)
def test_a_split_that_went_into_effect_long_before_the_filing_is_past(form, items, screen):
    """A filing of 2026-06-01 restating "the 1-for-8 reverse stock split went into effect on May 2, 2025": a past
    split's ratio either way, and since 2026-11-27 the past date is named (until then the sentence carried no date
    cue: "no first-session or effective date stated")."""
    ref = _ref("t", form, "2026-06-01", items)
    raw = "As previously reported, the Company's 1-for-8 reverse stock split went into effect on May 2, 2025."
    x = extract_for([FilingDocument(type=form, filename="t.htm", raw=raw)], ref)
    assert screen_of(ref) == screen
    assert not x.has_ratio and x.abstain_reason == split_filing.PAST_RATIO_ONLY
    assert x.mentions == ("historical:1-for-8",)
    assert (
        x.date_note == "only a past date stated (2025-05-02), more than 90 days before the filing"
    )


def test_a_trading_sentence_still_wins_over_go_into_effect():
    """The ENHA shape (``test_enha_reads_its_first_trading_session_not_its_legal_time``): the legal moment and the
    first session at the market open; the trading date stands. PINNED (reads so before and after).
    """
    x = _x(
        "The 1-for-10 reverse stock split will go into effect at 5:00 p.m. Eastern Time on February 27, 2026. "
        "The Common Stock will begin trading on a split-adjusted basis at the market open on March 2, 2026."
    )
    assert (x.ratio_from, x.ratio_to) == (Decimal(10), Decimal(1))
    assert (x.effective_date, x.date_kind) == (date(2026, 3, 2), "trading")


@pytest.mark.parametrize(
    "other",
    [
        "The license agreement went into effect as of January 1, 2022.",
        "The amended and restated bylaws will go into effect on March 2, 2026.",
        "The 2026 Equity Incentive Plan went into effect on March 2, 2026.",
    ],
    ids=["agreement", "bylaws", "plan"],
)
def test_go_into_effect_in_a_sentence_about_something_else_dates_nothing(other):
    """The cue dates only a sentence about the split, as every legal cue: a licence agreement, the bylaws or a
    plan that goes into effect dates nothing. PINNED (reads so before and after). Galapagos' 6-K
    0001193125-25-067161 (no ratio) shows the edge: its licence agreement "went into effect as of January 1,
    2022" in a run-on sentence that also names an "equal cost split", so the date reaches its note as a past one.
    """
    x = _x("The Board approved a 1-for-10 reverse stock split. " + other)
    assert (x.ratio_from, x.ratio_to, x.abstain_reason) == (Decimal(10), Decimal(1), None)
    assert (x.effective_date, x.date_kind) == (None, None)
    assert x.date_note == "no first-session or effective date stated"
