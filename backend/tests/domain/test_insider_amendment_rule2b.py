"""Rule 2b (the system's pick of an ambiguous 4/A's original) and the REVIEW LINE (what is owed), operator decision
2026-10-24 — the PURE rules, on REAL rows.

Provenance of the rows below:
- CVNA, HROW, SKYT, FIS: the committed real filings (``tests/form4a_fixtures.py`` names each one's provenance).
- SEI, AAOI, ZS, BCYC, CODI: the stored rows of prod's ``insider_amendment`` (the 4/A as filed: its Table I rows
  and its footnotes) and ``fact_insider_txn`` (its candidate / original Form 4's rows), copied verbatim — the
  fields these rules read — from the read-only pull the 2026-10-24 measurement made (``docs/temp/data-hygiene-
  measurements-2026-10-24.md`` §6 in the main checkout). Nothing here is fitted: each test reproduces a MEASURED
  outcome from those rows through the production functions.
"""

from __future__ import annotations

from datetime import date

from domain.config import DEFAULT_CONFIG
from domain.insider_amendment_cards import owner_role, signal_usd, trade_groups
from domain.insider_amendments import (
    AMBIGUOUS,
    BASIS_FILER_CORRECTION,
    CLERICAL,
    CORRECTION,
    ReviewLine,
    before_after,
    changed_signal_rows,
    filer_text,
    match_rows,
    originals_rows,
    review_line,
    row_tie_break,
    sole_match,
    sole_match_coverage,
    system_pick,
)
from signals.horizons import insider_signal_codes
from tests.domain.test_insider_amendments import _facts, _link, _stored

CODES = frozenset(insider_signal_codes(DEFAULT_CONFIG))
HORIZONS = insider_signal_codes(DEFAULT_CONFIG)
ASOF = date(2026, 10, 23)  # the measurement's asof (prod's latest call night)
EDITION_NIGHT = date(2026, 6, 10)  # prod's earliest edition night (Edition 1's first_night)


def _row(day, code, shares, price, seq, name, cik, role=None, b51=False):
    """A stored Form 4 row as ``fact_insider_txn`` holds it (the fields the rules read)."""
    return {
        "valid_from": date.fromisoformat(day),
        "txn_code": code,
        "shares": shares,
        "price": price,
        "txn_seq": seq,
        "insider_name": name,
        "insider_role": role,
        "rpt_owner_cik": cik,
        "aff_10b5_1": b51,
    }


def _arow(day, code, shares, price, seq, name, cik, role=None, b51=False):
    """A 4/A row as ``insider_amendment.rows`` stores it (the filing as filed: ``txn_date`` as ISO text)."""
    r = _row(day, code, shares, price, seq, name, cik, role, b51)
    r["txn_date"] = r.pop("valid_from").isoformat()
    return r


# --- SEI 0001415889-25-007591 (prod, read-only): the A -> P recode of a 10,000-share buy ----------------------

SEI_OWNER = ("Zartler William A", "1520378", "CHAIRMAN AND CEO")
SEI_4A = [_arow("2025-03-06", "P", 10000.0, 24.07, 0, *SEI_OWNER)]
SEI_STORED = {
    "0001415889-25-007343": [_row("2025-03-06", "A", 10000.0, 24.07, 0, *SEI_OWNER)],
    "0001415889-25-007362": [_row("2025-03-07", "P", 10000.0, 23.23, 0, *SEI_OWNER)],
}
SEI_CANDIDATES = ("0001415889-25-007343", "0001415889-25-007362")


def test_rule_2b_picks_the_original_the_operator_picked_for_cvna_and_hrow():
    """MEASURED 2026-10-24: rule 2b agrees with both of the operator's hand picks — CVNA ``-000275`` -> ``-000271``
    (28/28 rows; ``-000273`` 0/28) and HROW ``0001641172-25-010338`` -> ``0001493152-25-011028`` (1/1; ``-000741``
    holds the sale at another price, 0/1) — where the row tie-break could not decide (each loser accounts for a
    row by a coincidental sum or a price-fix shape)."""
    cvna = _link("0001690820-25-000275")
    assert cvna.disposition == AMBIGUOUS and not cvna.tie_break
    assert cvna.pick is not None
    assert (cvna.pick.target, cvna.pick.rows, dict(cvna.pick.coverage)) == (
        "0001690820-25-000271",
        28,
        {"0001690820-25-000271": 28, "0001690820-25-000273": 0},
    )
    assert cvna.pick.disposition == CORRECTION  # every sale pairs 1:1 (two re-dated)
    assert cvna.pick.resolution is not None and cvna.pick.resolution.basis == BASIS_FILER_CORRECTION
    hrow = _link("0001641172-25-010338")
    assert (hrow.pick.target, dict(hrow.pick.coverage)) == (
        "0001493152-25-011028",
        {"0001493152-25-011028": 1, "0001641172-25-000741": 0},
    )
    assert hrow.pick.disposition == CLERICAL and hrow.pick.resolution is None  # nothing to apply


def test_rule_2b_would_pick_sei_where_the_tie_break_cannot():
    """MEASURED 2026-10-24 (today owed): SEI's 4/A recodes a 10,000-share buy from ``A`` to ``P``. Both candidates
    hold a row the 4/A accounts for (``-007343`` a recode, ``-007362`` a same-code + shares date fix), so the
    tie-break leaves it ambiguous; only ``-007343`` holds the buy at its shares AND price -> rule 2b picks it. The
    pick is a 1:1 CORRECTION the system applies (the buy reads as the open-market purchase it was).
    """
    assert row_tie_break(SEI_4A, SEI_CANDIDATES, SEI_STORED, CODES) is None
    assert sole_match(SEI_4A, SEI_CANDIDATES, SEI_STORED) == "0001415889-25-007343"
    pick = system_pick(SEI_4A, SEI_CANDIDATES, SEI_STORED, CODES)
    assert pick is not None
    assert (pick.target, pick.disposition, dict(pick.coverage)) == (
        "0001415889-25-007343",
        CORRECTION,
        {"0001415889-25-007343": 1, "0001415889-25-007362": 0},
    )
    assert pick.resolution is not None and pick.resolution.basis == BASIS_FILER_CORRECTION


def test_rule_2b_keeps_the_tie_breaks_outcomes_and_picks_nothing_it_cannot_prove():
    """Where the row tie-break already settled a set the link is definite, and 2b never runs (SKYT: the restated
    sale's share count matches neither candidate — 2b alone would pick nothing; FIS: 2b agrees with the
    tie-break). With direct inputs: no pick for a zero-row 4/A, for two candidates that both cover every row, for
    a row of another owner, or for a price off by more than 1e-4."""
    skyt = _link("0001856680-26-000016")
    assert skyt.tie_break and skyt.pick is None
    assert (
        sole_match(
            list(_facts("0001856680-26-000016").rows), skyt.candidate_accessions, _stored("SKYT")
        )
        is None
    )
    fis = _link("0001193125-26-131811")
    assert fis.tie_break and fis.pick is None
    assert (
        sole_match(
            list(_facts("0001193125-26-131811").rows), fis.candidate_accessions, _stored("FIS")
        )
        == fis.original_accessions[0]
    )
    one = [_arow("2026-01-05", "P", 100.0, 10.0, 0, "Doe Jane", "9")]
    a = [_row("2026-01-05", "P", 100.0, 10.0, 0, "Doe Jane", "9")]
    assert sole_match([], ("A", "B"), {"A": a, "B": a}) is None
    assert sole_match(one, ("A", "B"), {"A": a, "B": a}) is None  # both cover it: no sole match
    other = [_row("2026-01-05", "P", 100.0, 10.0, 0, "Roe Rick", "8")]
    assert sole_match(one, ("A", "B"), {"A": a, "B": other}) == "A"
    assert sole_match_coverage(one, other) == 0  # another owner never matches
    near = [_row("2026-01-05", "P", 100.0, 10.0001, 0, "Doe Jane", "9")]
    far = [_row("2026-01-05", "P", 100.0, 10.0002, 0, "Doe Jane", "9")]
    assert sole_match_coverage(one, near) == 1 and sole_match_coverage(one, far) == 0
    # one-to-one: a candidate row is used once (two amendment rows need two candidate rows)
    twice = [one[0], {**one[0], "txn_seq": 1}]
    assert sole_match_coverage(twice, a) == 1


# --- the REVIEW LINE on the five 4/As prod owed under the old 940-day horizon (MEASURED 2026-10-24) ----------

AAOI_OWNER = ("Chang Hung-Lun (Fred)", "1587462", "*** See Remarks")
AAOI_4A = [
    _arow("2024-11-08", "S", 8195.0, 25.0417, 0, *AAOI_OWNER),
    _arow("2024-11-20", "S", 20323.0, 30.3514, 1, *AAOI_OWNER),
]
AAOI_ORIGINAL = {
    "0001683168-24-007917": [
        _row("2024-11-08", "S", 10000.0, 22.1067, 0, *AAOI_OWNER),
        _row("2024-11-08", "S", 8915.0, 25.0417, 1, *AAOI_OWNER),
    ]
}
AAOI_TEXT = filer_text(
    "*** Senior Vice President and North America General Manager",
    [
        "This amendment is being filed to address an error in reporting the number of shares sold in a "
        "transaction on November 8, 2024 and total number of shares beneficially owned following the "
        "transaction.",
        "This amendment is being filed to address an error in reporting the total number of shares beneficially "
        "owned on November 20, 2024.",
    ],
)
ZS_OWNER = ("Schlossman Robert", "1435941", "Chief Legal Officer")
ZS_4A = [
    _arow("2024-06-20", "S", 3911.0, 178.6282, 0, *ZS_OWNER, True),
    _arow("2024-06-20", "S", 372.0, 179.1973, 1, *ZS_OWNER, True),
]
ZS_ORIGINAL = {
    "0001713683-24-000087": [
        _row("2024-06-17", "G", 1000.0, 0.0, 0, *ZS_OWNER, True),
        _row("2024-06-18", "S", 3594.0, 180.93, 1, *ZS_OWNER, True),
        _row("2024-06-20", "S", 1990.0, 176.97, 2, *ZS_OWNER, True),
        _row("2024-06-20", "S", 200.0, 176.19, 3, *ZS_OWNER, True),
        _row("2024-06-20", "S", 376.0, 177.7207, 4, *ZS_OWNER, True),
        _row("2024-06-20", "S", 3698.0, 178.6355, 5, *ZS_OWNER, True),
        _row("2024-06-20", "S", 372.0, 179.1973, 6, *ZS_OWNER, True),
    ]
}
ZS_TEXT = filer_text(
    None,
    [
        "Includes 213 shares sold on September 23, 2024 pursuant to 10b5-1 trading plan adopted on June 20, 2023 "
        '(the "Trading Plan"). Such shares should have been included in the original order effected under the '
        "Trading Plan on June 20, 2024.",
        "No change to Form 4 filed June 20, 2024.",
        "Reflects ending balance after the 213 shares were sold.",
    ],
)
BCYC_OWNER = ("Perry Jennifer Scott", "2119908", "Chief Operating Officer")
BCYC_4A = [
    _arow("2026-10-05", "S", 30.0, 3.57, 0, *BCYC_OWNER, True),
    _arow("2026-10-05", "S", 326.0, 3.78, 1, *BCYC_OWNER, True),
]
BCYC_ORIGINAL = {
    "0002119908-26-000011": [
        _row("2026-10-02", "S", 1094.0, 3.47, 0, *BCYC_OWNER, True),
        _row("2026-10-05", "S", 80.0, 3.56, 1, *BCYC_OWNER, True),
        _row("2026-10-05", "S", 326.0, 3.78, 2, *BCYC_OWNER, True),
    ]
}
BCYC_TEXT = filer_text(
    None,
    [
        "Represents the number of shares required to be sold to cover the statutory tax withholding obligations "
        "in connection with the vesting and settlement of the RSUs. This sale is mandated by the Reporting "
        "Person's award agreement that requires the satisfaction of minimum statutory tax withholding "
        'obligations to be funded by a "sell to cover" transaction and does not represent a discretionary sale '
        "by the Reporting Person.",
        "The original Form 4 inadvertently overstated the number of shares sold, weighted average price and the "
        "number of shares beneficially owned following the reported transactions. This amendment correctly "
        "states the number of shares sold, weighted average price and the number of shares beneficially owned "
        "following the reported transactions.",
        "The price reported in Column 4 is a weighted average price. These shares were sold in multiple "
        "transactions at prices ranging from $3.56 to $3.57 inclusive. The Reporting Person undertakes to "
        "provide to the Issuer, any security holder of the Issuer, or the staff of the Securities and Exchange "
        "Commission, upon request, full information regarding the number of shares sold at each separate price "
        "within the range set forth in this footnote.",
        "This transaction was made pursuant to a Rule 10b5-1 trading plan adopted on March 27, 2025.",
    ],
)
CODI_OWNER = ("Sawtelle Zachary T.", "2140248", None)
CODI_4A = [_arow("2026-08-13", "P", 17000.0, 12.4204, 0, *CODI_OWNER)]
CODI_ORIGINAL = {
    "0001193125-26-349762": [
        _row("2026-08-12", "P", 25000.0, 11.9863, 0, *CODI_OWNER),
        _row("2026-08-13", "P", 7000.0, 12.45, 1, *CODI_OWNER),
    ]
}
CODI_TEXT = filer_text(
    "(a) Mr. Sawtelle is the Chief Operating Officer of Compass Group Diversified Holdings LLC, Sponsor of the "
    "Trust.",
    [
        "Each Common Share represents one undivided beneficial interest in Compass Diversified Holdings (the "
        '"Trust") property and corresponds to one underlying trust common interest of Compass Group Diversified '
        "Holdings LLC held by the Trust.",
        "On 08/13/2026, the Reporting Person filed a Form 4 that inadvertently reported the purchase of 7,000 "
        "Common Shares, when in fact, 17,000 Common Shares of the Issuer were purchased.",
        "The price reported in Column 4 is a weighted average price. These shares were purchased in multiple "
        "transactions at prices ranging from $12.39 to $12.45, inclusive. Full information regarding the number "
        "of shares purchased at each separate price will be provided upon request by the SEC staff, the issuer, "
        "or any security holder of the issuer.",
    ],
)


def _conflicts(amend, original, text):
    """The rows an UNRESOLVED row match owes the operator (what the review line judges for it)."""
    m = match_rows(amend, originals_rows(list(original), original), CODES, text)
    assert m.unresolved
    return m.conflicts


def _sei_rows():
    """An AMBIGUOUS link's rows: every buy / sell on every candidate and on the 4/A (its target is unknown)."""
    before, after = before_after(
        AMBIGUOUS,
        original_accessions=(),
        voided_accessions=(),
        candidate_accessions=SEI_CANDIDATES,
        stored=SEI_STORED,
        amend_rows=SEI_4A,
    )
    return changed_signal_rows(AMBIGUOUS, before, after, CODES)


def test_the_review_line_moves_aaoi_zs_and_sei_to_historical_and_keeps_bcyc_and_codi_owed():
    """MEASURED 2026-10-24 (prod, read-only, asof 2026-10-23, earliest edition night 2026-06-10): of the five
    4/As owed under the old flat 940-day horizon, three touch only rows no live call and no edition page reads —
    AAOI (2024-11 sales), ZS (2024-06 sales), SEI (2025-03 buys: the edge for a buy is 2025-11-12) -> historical —
    and two touch rows inside today's windows — BCYC (2026-10 sales), CODI (2026-08 buys) -> owed.
    """
    line = review_line(ASOF, HORIZONS, first_edition_night=EDITION_NIGHT)
    old = ReviewLine.flat(ASOF, 940)
    owed = {
        "AAOI": _conflicts(AAOI_4A, AAOI_ORIGINAL, AAOI_TEXT),
        "ZS": _conflicts(ZS_4A, ZS_ORIGINAL, ZS_TEXT),
        "BCYC": _conflicts(BCYC_4A, BCYC_ORIGINAL, BCYC_TEXT),
        "CODI": _conflicts(CODI_4A, CODI_ORIGINAL, CODI_TEXT),
        "SEI": _sei_rows(),
    }
    assert {t for t, rows in owed.items() if old.inside(rows)} == set(owed)  # all five owed before
    assert {t for t, rows in owed.items() if line.inside(rows)} == {"BCYC", "CODI"}
    # the line is the edition's read: a buy dated on the edge is inside, the day before is not
    assert line.inside([{"txn_code": "P", "valid_from": date(2025, 11, 12)}])
    assert not line.inside([{"txn_code": "P", "valid_from": date(2025, 11, 11)}])
    assert line.inside([{"txn_code": "S", "valid_from": date(2026, 2, 10)}])
    assert not line.inside([{"txn_code": "S", "valid_from": date(2026, 2, 9)}])
    # a row dated after asof, or undated, is never provably unread (over-include, #9)
    assert line.inside([{"txn_code": "S", "valid_from": date(2026, 11, 2)}])
    assert line.inside([{"txn_code": "S"}])
    assert not line.inside([])


def test_the_card_reads_the_filers_words_for_who_and_what():
    """AAOI's role points at its remarks ("*** See Remarks"): the card reads the remarks. BCYC's 4/A: two sales,
    356 shares at $3.57–$3.78 on 2026-10-05, $1,339.38 in all. A zero-row 4/A has no dollar total.
    """
    assert owner_role(AAOI_4A, "*** Senior Vice President and North America General Manager") == (
        "Senior Vice President and North America General Manager"
    )
    assert owner_role(CODI_4A, None) is None  # nothing stated, nothing inferred
    assert owner_role([], None, BCYC_ORIGINAL["0002119908-26-000011"]) == "Chief Operating Officer"
    (sales,) = trade_groups(BCYC_4A)
    assert (sales.code, sales.trades, sales.shares, sales.price_low, sales.price_high) == (
        "S",
        2,
        356.0,
        3.57,
        3.78,
    )
    assert (sales.first_date, sales.last_date) == (date(2026, 10, 5), date(2026, 10, 5))
    assert round(sales.usd, 2) == round(30 * 3.57 + 326 * 3.78, 2)
    assert signal_usd(BCYC_4A, CODES) == sales.usd and signal_usd([], CODES) is None
    groups = trade_groups(ZS_ORIGINAL["0001713683-24-000087"])
    assert [g.code for g in groups] == ["S", "G"]  # buys and sales first, then the other codes
