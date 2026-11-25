"""W3-1 — the SUGGESTED date of a held insider row (``domain.insider_holds.suggest``): the dates the filing itself
writes (a footnote the row cites, a stored sibling row of the same filing, the period of report with a ``00YY``
year read as ``20YY``), the filing's balance chain (``shares_owned_after``), and the month/day arithmetic, which
is unchanged. And THE PLACEMENT RULE IS NOT TOUCHED: a ``00YY`` period of report still fails it.

THE SHAPES are the 16 rows owed a steward decision on 2026-11-25 (the steward review's ``insider_owed.json`` and
the filings it read), under neutral labels (the fixture convention: a filing already named in tracked code keeps its
accession; every other one gets a made-up accession with its real filing year). The dates, shares, balances,
ownership forms and footnote texts are the filings' own, copied; the RECONSTRUCTED fields are marked:

- ``accepted`` — SEC's acceptance instant is not in the evidence, its market date is (``market_date``): 20:00 UTC
  on that day (``_accepted``), which is on the same New York date. Only the placement checks read it.
- footnote text that names a person, a trust or a company has that name replaced (``[name replaced]``).
- three filings (``late``, the leading-zero-year filing named in tracked code, the two-security filing) are read
  from their committed ownership documents with the ingest's own parser (``parse_form4``, ``hold_row``).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from domain.config import DEFAULT_CONFIG
from domain.insider_holds import (
    placement_night,
    placement_problem,
    suggest,
    suggested_date,
    suggestion_period_of_report,
)
from ingest.edgar.form4 import filing_held_rows, hold_evidence, hold_row, parse_form4
from tests import held_fixtures as H
from tests.domain.test_insider_holds_queue import _real_holds

_UTC = timezone.utc
_ON_TIME = DEFAULT_CONFIG.insider_hold_on_time_days


def _accepted(market: date) -> datetime:
    """RECONSTRUCTED: an acceptance on the recorded market date (see the module docstring)."""
    return datetime(market.year, market.month, market.day, 20, tzinfo=_UTC)


def _hold(
    acc: str,
    seq: int,
    written: str,
    por: str,
    *,
    market: date,
    filed: date | None,
    code: str,
    shares: float,
    after: float | None,
    di: str,
    ad: str,
    footnotes: tuple[str, ...] = (),
    sid: str = "sec-1",
) -> dict[str, Any]:
    return {
        "id": f"hold-{acc}-{seq}-{sid}",
        "tenant_id": "data",
        "security_id": sid,
        "accession": acc,
        "txn_seq": seq,
        "form": "4",
        "recorded_at": datetime(2026, 11, 1, tzinfo=_UTC),
        "row": {
            "txn_date_as_filed": written,
            "txn_code": code,
            "shares": shares,
            "shares_owned_after": after,
            "direct_indirect": di,
            "acquired_disposed": ad,
            "txn_footnotes": list(footnotes),
        },
        "period_of_report_as_filed": por,
        "filed": filed,
        "accepted": _accepted(market),
    }


def _sib(seq: int, day: str, shares: float, after: float | None, di: str) -> dict[str, Any]:
    return {
        "txn_seq": seq,
        "valid_from": date.fromisoformat(day),
        "shares": shares,
        "shares_owned_after": after,
        "direct_indirect": di,
    }


def _from_xml(
    xml: str, accession: str, *, accepted: datetime | None, filed: date | None, sid: str = "sec-1"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A committed ownership document read the ingest's way: its rows the year rule or the after-filing rule
    refuses become holds (``hold_row``), the others the stored sibling rows."""
    rows = parse_form4(xml)
    held = filing_held_rows(rows, accession, xml, accepted)
    ev = hold_evidence(xml)
    holds, sibs = [], []
    for i, t in enumerate(rows):
        if i in held:
            holds.append(
                {
                    "id": f"hold-{accession}-{i}-{sid}",
                    "tenant_id": "data",
                    "security_id": sid,
                    "accession": accession,
                    "txn_seq": i,
                    "form": "4",
                    "recorded_at": datetime(2026, 11, 1, tzinfo=_UTC),
                    "row": hold_row(t, ev.txn_dates[i]),
                    "period_of_report_as_filed": ev.period_of_report,
                    "filed": filed,
                    "accepted": accepted,
                }
            )
        else:
            sibs.append(
                {
                    "txn_seq": i,
                    "valid_from": t["txn_date"],
                    "shares": t["shares"],
                    "shares_owned_after": t["shares_owned_after"],
                    "direct_indirect": t["direct_indirect"],
                }
            )
    return holds, sibs


# --- the 16 owed rows --------------------------------------------------------------------------------------------

_FIX = H.HELD_DIR.parent


def _shapes() -> dict[str, tuple[dict[str, Any], list[dict[str, Any]], int]]:
    """``label -> (hold, siblings, row_count)``."""
    out: dict[str, tuple[dict[str, Any], list[dict[str, Any]], int]] = {}

    # a leading-zero year (0023) on a grant whose footnote writes the date; the filing named in tracked code
    # (``form4_bbai_leading_zero_year.xml``); its filing date 2024-03-08 is the recorded one (no acceptance used)
    bbai = (_FIX / "form4_bbai_leading_zero_year.xml").read_text(encoding="utf-8")
    (h,), sibs = _from_xml(bbai, "0001628280-24-010030", accepted=None, filed=date(2024, 3, 8))
    out["leading_zero_year_footnote"] = (h, sibs, 3)

    # a leading-zero year (0024) on a tax-withholding row; footnote and the paired distribution row write the day
    out["leading_zero_year_withholding"] = (
        _hold(
            "0000000000-24-000002",
            1,
            "0024-01-01",
            "0024-01-01",
            market=date(2024, 2, 2),
            filed=date(2024, 2, 2),
            code="F",
            shares=261.0,
            after=36066.0,
            di="D",
            ad="D",
            footnotes=(
                "[name replaced] withheld shares of [name replaced] common stock to satisfy the tax withholding "
                "obligation for the Reporting Person's deferred compensation Units that were distributed on "
                "January 1, 2024.",
                "Includes Restricted Stock Units that have not yet vested.",
            ),
        ),
        [_sib(0, "2024-01-01", 1036.0, 36327.0, "D")],
        2,
    )

    # a year typo (2018 for 2017) in a 21-row run of daily sales; the price footnote writes 11/21/2017
    run = [
        (0, "2017-11-03", 22759, 4812221),
        (1, "2017-11-06", 79327, 4732894),
        (2, "2017-11-07", 23000, 4709894),
        (3, "2017-11-08", 16875, 4693019),
        (4, "2017-11-09", 20000, 4673019),
        (5, "2017-11-10", 10000, 4663019),
        (6, "2017-11-13", 65093, 4597926),
        (7, "2017-11-14", 14907, 4583019),
        (8, "2017-11-15", 15000, 4568019),
        (9, "2017-11-16", 105000, 4463019),
        (10, "2017-11-17", 51282, 4411737),
        (11, "2017-11-20", 158718, 4253019),
        (13, "2017-11-22", 256000, 3917019),
        (14, "2017-11-24", 26010, 3891009),
        (15, "2017-11-27", 27990, 3863019),
        (16, "2017-11-28", 5080, 3857939),
        (17, "2017-11-29", 4263, 3853676),
        (18, "2017-11-30", 12666, 3841010),
        (19, "2017-12-01", 87991, 3753019),
        (20, "2017-12-04", 30485, 3722534),
    ]
    out["year_typo_in_a_run_of_sales"] = (
        _hold(
            "0000000000-17-000003",
            12,
            "2018-11-21",
            "2017-11-03",
            market=date(2017, 12, 7),
            filed=date(2017, 12, 7),
            code="S",
            shares=80000.0,
            after=4173019.0,
            di="I",
            ad="D",
            # the row's price footnote (its two other footnotes name the holder and write no date: left out)
            footnotes=(
                "The price reported in Column 4 is a weighted average price. These shares were sold in multiple "
                "transactions at prices ranging from: $17.86 to $18.57 on 11/16/2017; $18.30 to $18.65 on "
                "11/17/2017; $18.60 to $19.26 on 11/20/2017; $19.02 to $19.46 on 11/21/2017; $19.20 to $20.01 on "
                "11/22/2017; $19.84 to $20.27 on 11/24/2017; $19.41 to $20.20 on 11/27/2017; $19.00 to $19.76 on "
                "11/28/2017; $19.20 to $19.39 on 11/29/2017; $19.00 to $19.20 on 11/30/2017; $19.02 to $19.22 on "
                "12/1/2017; and $19.20 to 19.64 on 12/4/2017, inclusive. The reporting person undertakes to "
                "provide any security holder of the Issuer or the staff of the Securities Exchange Commission, "
                "upon request, full information regarding the number of shares sold at each separate price "
                "within the ranges set forth in this footnote (4) to this Form 4.",
            ),
        ),
        [_sib(s, d, float(n), float(a), "I") for s, d, n, a in run],
        21,
    )

    # a buy dated after its own filing, beside one row; nothing in the filing writes its date
    out["after_filing_no_written_date"] = (
        _hold(
            "0000000000-15-000004",
            1,
            "2015-09-14",
            "2015-08-27",
            market=date(2015, 9, 11),
            filed=date(2015, 9, 11),
            code="P",
            shares=2000.0,
            after=4000.0,
            di="I",
            ad="A",
            footnotes=(
                "The reporting person's spouse is trustee and beneficiary of the [name replaced] Family Trust.",
            ),
        ),
        [_sib(0, "2015-08-27", 2000.0, 2000.0, "I")],
        2,
    )

    # a grant dated after its filing whose balance follows a row the filing dates later than the arithmetic
    out["after_filing_balance_chain"] = (
        _hold(
            "0000000000-09-000005",
            2,
            "2009-07-17",
            "2007-12-01",
            market=date(2009, 7, 13),
            filed=date(2009, 7, 13),
            code="A",
            shares=140000.0,
            after=250000.0,
            di="D",
            ad="A",
            footnotes=("Restricted stock grant for services.",),
        ),
        [
            _sib(0, "2007-12-01", 10000.0, 10000.0, "D"),
            _sib(1, "2009-07-09", 100000.0, 110000.0, "D"),
        ],
        3,
    )

    # a leading-zero year (0025) on one sale of four; the filing is held under TWO securities of its issuer (the
    # committed ``form4_impossible_year_no_stored_acceptance.xml``, SEC's acceptance as ``tests/held_fixtures.py``
    # records it). Each copy is its own hold.
    two = (H.HELD_DIR / H.NO_ACCEPTANCE_FILE).read_text(encoding="utf-8")
    for label, sid in (
        ("leading_zero_year_two_securities_a", "sec-a"),
        ("leading_zero_year_two_securities_b", "sec-b"),
    ):
        (h,), sibs = _from_xml(
            two, H.NO_ACCEPTANCE_ACCESSION, accepted=H.NO_ACCEPTANCE_ACCEPTED, filed=None, sid=sid
        )
        out[label] = (h, sibs, 4)

    # a late filing of three buys, every row held (``tests/held_fixtures.py``, group ``late``)
    late = H.of_group("late", "4")[0]
    f = H.FILINGS[late]
    holds, sibs = _from_xml(H.xml(late), late, accepted=f.accepted, filed=f.filed)
    assert sibs == []
    for h in holds:
        out[f"late_row_{h['txn_seq']}"] = (h, [], 3)

    # a one-row late filing; its period of report is the row's own month and day
    out["one_row_late"] = (
        _hold(
            "0000000000-22-000008",
            0,
            "2022-11-30",
            "2021-11-30",
            market=date(2022, 7, 1),
            filed=date(2022, 7, 1),
            code="J",
            shares=488256.0,
            after=1806690.0,
            di="I",
            ad="D",
            footnotes=(
                "Represents a pro rata distributions for no consideration to a member of [name replaced] LLC.",
            ),
        ),
        [],
        1,
    )

    # a dividend reinvestment dated after its filing, between two stored rows of the filing's balances
    out["dividend_reinvestment_between_rows"] = (
        _hold(
            "0000000000-18-000009",
            1,
            "2018-09-13",
            "2017-06-13",
            market=date(2018, 2, 21),
            filed=date(2018, 2, 21),
            code="P",
            shares=3.0,
            after=15446.0,
            di="D",
            ad="A",
            footnotes=(
                "The reporting person reinvested dividends paid on shares held in a brokerage account pursuant to "
                "an election previously made with the brokerage firm. The dividend reinvestments did not come to "
                "the attention of the reporting person until shortly before the date of this filing.",
            ),
        ),
        [_sib(0, "2017-06-13", 3.0, 15443.0, "D"), _sib(2, "2018-02-19", 1802.0, 13644.0, "D")],
        3,
    )

    # a leading-zero year (0024) on an option exercise; the same-day sale of the same shares writes the day
    out["leading_zero_year_exercise_and_sale"] = (
        _hold(
            "0000000000-24-000010",
            0,
            "0024-02-12",
            "0024-02-12",
            market=date(2024, 2, 14),
            filed=date(2024, 2, 14),
            code="M",
            shares=3659.0,
            after=65216.0,
            di="D",
            ad="A",
        ),
        [
            _sib(1, "2024-02-12", 3659.0, 61557.0, "D"),
            _sib(2, "2024-02-13", 15552.0, 77109.0, "D"),
            _sib(3, "2024-02-13", 15552.0, 61557.0, "D"),
        ],
        4,
    )

    # an RSU vest and its tax withholding, both held, INDIRECT rows listed after the filing's direct ones (a
    # table-order trap: the direct rows dated 2022-02-28 come first, the held rows are truly 2021-08-31)
    vest = (
        'Represents the settlement upon vesting of restricted stock units ("RSUs") into [name replaced] common '
        "stock.",
        "The RSUs were granted on May 25, 2021 and vest in four installments on August 31, 2021, February 28, "
        "2022, August 31, 2022 and February 28, 2023. See also footnote 12 regarding the vesting of these awards.",
        "By spouse of the reporting person.",
        "Includes 6,765 shares held in a revocable trust in the name of the spouse of the reporting person.",
    )
    withheld = (
        "This transaction represents the withholding of shares to pay tax withholding obligations associated with "
        "the vesting of the RSU awards referenced in footnotes 2-4 above with respect to the reporting person or, "
        "as applicable, the spouse of the reporting person. See footnote 8.",
        "By spouse of the reporting person.",
        "Includes 6,765 shares held in a revocable trust in the name of the spouse of the reporting person.",
    )
    rsu_sibs = [
        _sib(0, "2022-02-28", 3282.0, 53350.0, "D"),
        _sib(1, "2022-02-28", 5608.0, 58958.0, "D"),
        _sib(2, "2022-02-28", 5606.0, 64564.0, "D"),
        _sib(3, "2022-02-28", 6413.0, 58151.0, "D"),
        _sib(6, "2022-02-28", 819.0, 27084.0, "I"),
        _sib(7, "2022-02-28", 2841.0, 29925.0, "I"),
        _sib(8, "2022-02-28", 1809.0, 31734.0, "I"),
        _sib(9, "2022-02-28", 2172.0, 29562.0, "I"),
    ]
    common = dict(market=date(2022, 3, 2), filed=date(2022, 3, 2), di="I")
    out["rsu_vest_indirect_after_direct"] = (
        _hold(
            "0000000000-22-000011",
            4,
            "2022-08-31",
            "2021-05-25",
            code="M",
            shares=286.0,
            after=26392.0,
            ad="A",
            footnotes=vest,
            **common,
        ),
        rsu_sibs,
        10,
    )
    out["rsu_withholding_indirect_after_direct"] = (
        _hold(
            "0000000000-22-000011",
            5,
            "2022-08-31",
            "2021-05-25",
            code="F",
            shares=127.0,
            after=26265.0,
            ad="D",
            footnotes=withheld,
            **common,
        ),
        rsu_sibs,
        10,
    )

    # a one-row filing whose only written date besides the row's is its period of report, a month earlier
    out["one_row_month_typo"] = (
        _hold(
            "0000000000-22-000013",
            0,
            "2022-07-30",
            "2022-06-30",
            market=date(2022, 7, 15),
            filed=None,
            code="A",
            shares=5000.0,
            after=100111.0,
            di="D",
            ad="A",
            footnotes=(
                'Shares purchased under the [name replaced] 2019 Employee Stock Purchase Plan ("ESPP").',
                "Amount includes shares held by the Reporting Person as of the date of this Form 4, but "
                "inadvertently excluded from previously filed reports.",
            ),
        ),
        [],
        1,
    )
    return out


SHAPES = _shapes()
D = date

# label -> (suggested date, agrees, basis, window_from, window_to) — the review's reading of each filing
# (``insider/result.json``) and the plan's table (§3b)
EXPECTED: dict[str, tuple[date | None, bool, str | None, date | None, date | None]] = {
    "leading_zero_year_footnote": (
        D(2023, 3, 23),
        True,
        "footnote",
        D(2023, 3, 23),
        D(2023, 11, 13),
    ),
    "leading_zero_year_withholding": (
        D(2024, 1, 1),
        True,
        "footnote",
        D(2024, 1, 1),
        D(2024, 2, 2),
    ),
    "year_typo_in_a_run_of_sales": (
        D(2017, 11, 21),
        True,
        "footnote",
        D(2017, 11, 20),
        D(2017, 11, 22),
    ),
    "after_filing_no_written_date": (None, False, None, D(2015, 8, 27), D(2015, 9, 11)),
    "after_filing_balance_chain": (None, False, None, D(2009, 7, 9), D(2009, 7, 13)),
    "leading_zero_year_two_securities_a": (
        D(2025, 7, 25),
        True,
        "sibling_row",
        D(2025, 7, 25),
        D(2025, 7, 25),
    ),
    "leading_zero_year_two_securities_b": (
        D(2025, 7, 25),
        True,
        "sibling_row",
        D(2025, 7, 25),
        D(2025, 7, 25),
    ),
    "late_row_0": (D(2020, 11, 16), True, "period_of_report", D(2020, 11, 16), D(2021, 3, 18)),
    "late_row_1": (D(2020, 12, 3), True, "arithmetic", D(2020, 11, 16), D(2021, 3, 18)),
    "late_row_2": (D(2020, 12, 4), True, "arithmetic", D(2020, 11, 16), D(2021, 3, 18)),
    "one_row_late": (D(2021, 11, 30), True, "period_of_report", D(2021, 11, 30), D(2022, 7, 1)),
    "dividend_reinvestment_between_rows": (
        D(2017, 9, 13),
        True,
        "arithmetic",
        D(2017, 6, 13),
        D(2018, 2, 19),
    ),
    "leading_zero_year_exercise_and_sale": (
        D(2024, 2, 12),
        True,
        "sibling_row",
        D(2024, 2, 12),
        D(2024, 2, 12),
    ),
    "rsu_vest_indirect_after_direct": (
        D(2021, 8, 31),
        True,
        "footnote",
        D(2021, 5, 25),
        D(2022, 3, 2),
    ),
    "rsu_withholding_indirect_after_direct": (
        D(2021, 8, 31),
        True,
        "arithmetic",
        D(2021, 5, 25),
        D(2022, 2, 28),
    ),
    "one_row_month_typo": (
        D(2022, 6, 30),
        False,
        "period_of_report_one_row",
        D(2022, 6, 30),
        D(2022, 7, 15),
    ),
}


def test_the_shapes_are_the_sixteen_owed_rows():
    assert len(SHAPES) == len(EXPECTED) == 16 and set(SHAPES) == set(EXPECTED)


def _suggest(label: str, **kw: Any):
    hold, sibs, n = SHAPES[label]
    return suggest(hold, sibs, row_count=n, **kw)


# --- the four filings blocked only by a 00YY period of report ----------------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "leading_zero_year_footnote",
        "leading_zero_year_withholding",
        "leading_zero_year_two_securities_a",
        "leading_zero_year_two_securities_b",
        "leading_zero_year_exercise_and_sale",
    ],
)
def test_a_00yy_period_of_report_is_read_as_its_filing_year_so_the_written_date_agrees(label):
    s = _suggest(label)
    want = EXPECTED[label]
    assert (s.date, s.agrees) == want[:2]
    assert s.basis == want[2]
    assert (s.window_from, s.window_to) == want[3:]


# --- a one-row filing whose row cannot be its period of report --------------------------------------------------


def test_a_one_row_filing_suggests_its_period_of_report_without_agreeing_by_default():
    s = _suggest("one_row_month_typo")
    assert (s.date, s.agrees, s.basis) == (D(2022, 6, 30), False, "period_of_report_one_row")
    assert "one-row filing" in s.words and "2022-07-30" in s.words
    # the setting turns the one click on (decision W3-b); nothing else changes
    on = _suggest("one_row_month_typo", one_row_por_agrees=True)
    assert (on.date, on.agrees, on.basis) == (D(2022, 6, 30), True, "period_of_report_one_row")
    # the same row with a stored sibling is not a one-row filing: withheld, with the window
    hold, _, _ = SHAPES["one_row_month_typo"]
    two = suggest(hold, [_sib(1, "2022-06-30", 10.0, None, "D")], row_count=2)
    assert (two.date, two.agrees, two.basis) == (None, False, None)
    # an unknown row count never reads as a one-row filing
    assert suggest(hold).date is None


# --- a suggestion the filing contradicts is withheld, with the filing's own window -----------------------------


def test_a_suggestion_before_the_period_of_report_is_withheld_with_the_filings_window():
    s = _suggest("after_filing_no_written_date")
    assert (s.date, s.agrees, s.basis) == (None, False, None)
    assert (s.window_from, s.window_to) == (D(2015, 8, 27), D(2015, 9, 11))
    assert s.words == (
        "2014-09-14 is before the period of report (2015-08-27); the filing puts it between 2015-08-27 and "
        "2015-09-11"
    )


def test_a_suggestion_before_the_row_its_balance_follows_is_withheld_with_the_filings_window():
    s = _suggest("after_filing_balance_chain")
    assert (s.date, s.agrees, s.basis) == (None, False, None)
    assert (s.window_from, s.window_to) == (D(2009, 7, 9), D(2009, 7, 13))
    assert s.words == (
        "2008-07-17 is before the 2009-07-09 row this one follows in the filing's balances (110,000 -> 250,000 "
        "shares); the filing puts it between 2009-07-09 and 2009-07-13"
    )


# --- written dates corroborate the arithmetic; every reviewed row ----------------------------------------------


@pytest.mark.parametrize("label", sorted(EXPECTED))
def test_each_owed_row_gets_the_reviewed_suggestion(label):
    s = _suggest(label)
    assert (s.date, s.agrees, s.basis, s.window_from, s.window_to) == EXPECTED[label]
    assert s.words  # every outcome says why, in words


def test_the_written_basis_is_named_in_words():
    assert "footnote" in _suggest("leading_zero_year_footnote").words
    assert "rows 0, 2 and 3" in _suggest("leading_zero_year_two_securities_a").words
    assert "row 1" in _suggest("leading_zero_year_exercise_and_sale").words
    assert "period of report" in _suggest("one_row_late").words
    assert "month and day" in _suggest("dividend_reinvestment_between_rows").words


def test_indirect_rows_listed_after_direct_ones_keep_their_own_balance_chain():
    """The table-order trap: the direct rows (2022-02-28) come first; the held indirect rows follow only the
    indirect balances, so both keep the reviewed 2021-08-31 and agree."""
    vest = _suggest("rsu_vest_indirect_after_direct")
    tax = _suggest("rsu_withholding_indirect_after_direct")
    assert (vest.date, vest.agrees, tax.date, tax.agrees) == (
        D(2021, 8, 31),
        True,
        D(2021, 8, 31),
        True,
    )
    # the vest footnote's other 08-31 (2022) is after the filing, so it is not a written candidate
    assert "2022-08-31" not in vest.words


def test_two_distinct_written_dates_do_not_agree_and_the_words_name_both():
    """DIRECT INPUT (not a real filing): the vest shape, filed after both written August 31s."""
    hold, sibs, n = SHAPES["rsu_vest_indirect_after_direct"]
    late = {**hold, "accepted": _accepted(D(2022, 9, 15)), "filed": D(2022, 9, 15)}
    s = suggest(late, sibs, row_count=n)
    assert (s.date, s.agrees) == (D(2022, 8, 31), False)
    assert "2021-08-31" in s.words and "2022-08-31" in s.words


def test_a_missing_balance_proves_no_window():
    """DIRECT INPUT: the balance-chain shape with the predecessor's balance unknown (a NULL ``shares_owned_after``
    proves nothing): the arithmetic is back, on or after the period of report, as before this rule.
    """
    hold, sibs, n = SHAPES["after_filing_balance_chain"]
    blind = [{**s, "shares_owned_after": None} if s["txn_seq"] == 1 else s for s in sibs]
    s = suggest(hold, blind, row_count=n)
    assert (s.date, s.agrees, s.basis) == (D(2008, 7, 17), True, "arithmetic")
    # ...and so is a held row with no balance of its own
    row = {**hold["row"], "shares_owned_after": None}
    assert suggest({**hold, "row": row}, sibs, row_count=n).date == D(2008, 7, 17)
    # two predecessors with the same balance prove nothing either
    twice = sibs + [_sib(3, "2009-07-10", 5.0, 110000.0, "D")]
    assert suggest(hold, twice, row_count=4).date == D(2008, 7, 17)


# --- the period of report the suggestion reads; the placement rule reads the raw text ---------------------------


def test_suggestion_period_of_report_repairs_only_a_00yy_year_near_the_filing():
    def por(text, acc="0000000000-24-000001"):
        return suggestion_period_of_report({"accession": acc, "period_of_report_as_filed": text})

    assert por("0023-03-23") == D(2023, 3, 23)  # the year before the filing's
    assert por("0024-01-01") == D(2024, 1, 1)  # the filing's own year
    assert por("0022-03-23") is None  # neither: no guess
    assert por("0023-03-23", acc="acc-planned") is None  # no filing year to anchor the repair
    assert por("2020-11-16") == D(2020, 11, 16)
    assert por("2028-05-24") is None  # after the filing's year: the year rule
    assert por("n/a") is None and por(None) is None


def test_placement_still_reads_the_raw_period_of_report():
    """Decision W3-a: the 00YY repair feeds the suggestion only. Every owed shape stays unplaced, and a 00YY period
    of report still fails the placement rule's year check, word for word."""
    for label, (hold, _, _) in SHAPES.items():
        assert placement_night(hold, _ON_TIME) is None, label
        assert placement_problem(hold, _ON_TIME) is not None, label
    for label in (
        "leading_zero_year_withholding",
        "leading_zero_year_two_securities_a",
        "leading_zero_year_exercise_and_sale",
    ):
        hold = SHAPES[label][0]
        text = hold["period_of_report_as_filed"]
        assert placement_problem(hold, _ON_TIME) == (
            f"the period of report as filed ({text}) fails the year rule (before 1934, or after the filing's "
            "year)"
        )


def test_every_real_held_filing_places_as_before():
    """The placed-by-rule filings keep their nights (``tests/held_fixtures.py``; pinned here from the rule before
    this change), and the late one stays unplaced."""
    holds = dict(_real_holds())
    nights = {acc: placement_night(h, _ON_TIME) for acc, h in holds.items()}
    assert nights == {
        "0001354488-15-000004": D(2015, 1, 2),
        "0001179110-10-012412": D(2010, 8, 10),
        "0000899243-21-012557": None,
        "0001019687-14-002802": None,  # its period of report is after the filing (signed ahead of New York)
        "0001140361-24-027755": D(2024, 5, 28),
        "0001179110-20-004028": D(2020, 3, 20),
        "0001628280-23-039558": D(2023, 11, 17),
        "0001628280-23-035012": D(2023, 10, 24),
        "0000903651-05-000023": D(2005, 11, 16),
        "0001209191-21-010430": D(2021, 2, 12),
        "0001209191-18-051954": D(2018, 9, 21),
        "0001209191-15-078036": D(2015, 11, 3),
        "0001209191-22-000235": D(2022, 1, 3),
        "0001628280-24-010030": None,
        "0001437749-24-004874": None,
    }


def test_the_wrapper_keeps_its_two_values():
    for hold, sibs, n in SHAPES.values():
        assert suggested_date(hold) == tuple(suggest(hold)[:2])
