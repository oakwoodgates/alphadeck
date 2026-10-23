"""The SEC split extractor (split plan S1) on REAL filings — the diagnosis §2 table is the key: every ratio
and first session below is what the company's own filing states (``tests/fixtures/splits/README.md``).

Plus the contract on direct inputs where the saved filings have no instance: two different ratios abstain, a
word that disagrees with its digits abstains, no direction abstains, a clock time is never a ratio, two
different first sessions give no date."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ingest.edgar.split_filing import (
    FilingDocument,
    extract_split,
    relevant_documents,
    submission_documents,
)
from tests.split_fixtures import documents

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
    reverse split corporate action" — a ratio, and NO first session (the diagnosis's market date is Polygon's
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
    holds it to Polygon's first session EXACTLY — and Polygon lists 10-28, one session early."""
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
    date a legal cue reaches is 2025-12-31 — in a tax table ("Effective Tax Rate"). Bare, that reads as a
    1-for-25 effective 2025-12-31 (which the nightly would have LISTED as a new split, ``tests/pipeline/
    test_detect_splits.py``); with its filing date the ratio stands but the past date dates nothing.
    """
    bare = extract_split(documents("0001213900-26-102384.txt"))
    assert (bare.ratio_from, bare.effective_date, bare.date_kind) == (
        Decimal(25),
        date(2025, 12, 31),
        "legal",
    )
    x = extract_split(documents("0001213900-26-102384.txt"), filed=date(2026, 9, 23))
    assert (x.ratio_from, x.ratio_to) == (Decimal(25), Decimal(1))
    assert x.effective_date is None and x.date_kind is None
    assert (
        x.date_note == "only a past date stated (2025-12-31), more than 90 days before the filing"
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
