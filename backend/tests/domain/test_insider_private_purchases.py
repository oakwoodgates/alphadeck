"""The private-purchase note's CONTRACT on direct inputs (``domain/insider_private_purchases.py``): the phrase list,
what it must NOT match (the weighted-average boilerplate, the open market, public offerings), and how the
counterparty is grounded. The real filings are pinned in ``tests/ingest/test_form4_internal_transfers.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from domain.insider_internal_transfers import INTERNAL_TRANSFER
from domain.insider_private_purchases import ISSUER, private_purchase_note

D0 = date(2024, 9, 12)


def _buy(*footnotes: str, code: str = "P", **extra) -> dict:
    return {
        "txn_code": code,
        "valid_from": D0,
        "insider_name": "Flanders Scott N",
        "txn_footnotes": list(footnotes),
        **extra,
    }


def _sale(owner: str, d: date = D0) -> dict:
    return {"txn_code": "S", "valid_from": d, "insider_name": owner}


@pytest.mark.parametrize(
    ("passage", "phrase"),
    [
        # the real footnotes behind the verification table (verbatim)
        (
            'The transaction is a private purchase of shares of Fathom Holdings Inc. (the "Company") common stock '
            "between the Reporting Person and the Company's founder, Joshua Harley.",
            "private purchase",
        ),
        (
            "This transaction is in consideration for the sale of Class B Common Stock pursuant to a Stock Purchase "
            "Agreement between Reporting Person and William Coulter executed 12/30/2021.",
            "stock purchase agreement",
        ),
        ("These shares were purchased from David Schmidt.", "purchased from"),
        ("These shares were purchased from Alliance Apex, LLC.", "purchased from"),
        (
            "Effective on December 15, 2023, Dr. James N. Woody, the Chief Executive Officer of the Issuer, purchased "
            "25,000 shares of common stock of the Issuer from Prof. Sir Marc Feldmann, the Co-Executive Chairman of "
            "the Issuer, in a private transaction, for $4,720 or $0.188 per share.",
            "private transaction",
        ),
        (
            "These shares were acquired from unaffiliated stockholders in a negotiated, private transaction.",
            "private transaction",
        ),
        (
            "Acquired from the Issuer pursuant to a Securities Purchase Agreement dated May 6, 2019.",
            "purchased from",
        ),
    ],
)
def test_the_phrase_list_locates_a_private_purchase(passage, phrase):
    note = private_purchase_note(_buy(passage))
    assert note is not None and note.phrase == phrase and note.passage == passage


@pytest.mark.parametrize(
    "passage",
    [
        # the weighted-average boilerplate on ~2,400 code-P rows — "purchased ... ranging from $X"
        "The price reported in Column 4 is a weighted average price. These shares were purchased in multiple "
        "transactions at prices ranging from $7.97 to $8.34 per share.",
        "These shares were bought in multiple transactions at prices ranging from $3.84 to $3.86 inclusive.",
        "Securities acquired range from $2.12 to $2.15, with a weighted average price of $2.13.",
        # explicitly the open market (ASLE), a public offering, an IPO
        "On August 17, 2023, the Reporting Person made a private purchase in the open market of 2,500 shares.",
        "Represents shares of Common Stock purchased directly from the underwriters in an underwritten public "
        "offering conducted by the Issuer.",
        "Shares purchased from time to time.",
        # a plan's routine purchases — the measured false hits of the first cut (NWE, D, LPTH), verbatim
        "Includes shares acquired from dividend reinvestment.",
        "Balance includes 272.0742 shares acquired from dividend reinvestment since reporting person's last "
        "Form 4 filing.",
        "These shares were purchased from the Employee Stock Purchase Plan.",
        "",
    ],
)
def test_what_is_not_a_private_purchase(passage):
    assert private_purchase_note(_buy(passage)) is None


def test_the_counterparty_is_grounded_or_absent():
    fthm = (
        'The transaction is a private purchase of shares of Fathom Holdings Inc. (the "Company") common stock '
        "between the Reporting Person and the Company's founder, Joshua Harley."
    )
    tape = [
        _sale("Harley Joshua"),
        _sale("Someone Else"),
        _sale("Harley Joshua", date(2024, 9, 13)),
    ]
    assert private_purchase_note(_buy(fthm), tape).counterparty == "Harley Joshua"
    # a seller the passage does not name is no counterparty; no same-day seller at all -> no name
    assert private_purchase_note(_buy(fthm), [_sale("Someone Else")]).counterparty is None
    assert private_purchase_note(_buy(fthm)).counterparty is None
    # the issuer, when the passage says the shares came from it
    pipe = "Acquired from the Issuer pursuant to a Securities Purchase Agreement dated May 6, 2019."
    assert private_purchase_note(_buy(pipe)).counterparty == ISSUER
    # a named non-filer is left in the passage, never guessed into a name
    assert (
        private_purchase_note(
            _buy("These shares were purchased from Alliance Apex, LLC.")
        ).counterparty
        is None
    )


def test_only_a_counted_buy_carries_the_note():
    passage = "These shares were purchased from David Schmidt."
    assert private_purchase_note(_buy(passage, code="S")) is None
    assert private_purchase_note(_buy(passage, **{INTERNAL_TRANSFER: {"with": ("X",)}})) is None
    # a row stored before migration 0055 (no capture) has no note
    assert private_purchase_note({"txn_code": "P", "txn_footnotes": None}) is None
    assert private_purchase_note({"txn_code": "P"}) is None
    # the first footnote that locates a passage wins
    note = private_purchase_note(_buy("Weighted average price.", passage))
    assert note is not None and note.passage == passage
