"""The internal-transfer screen's CONTRACT on direct inputs (``domain/insider_internal_transfers.py``) — each rule
of the operator's 2026-10-21 decisions stated once, on the smallest rows that exercise it. The real filings
behind the rule are pinned in ``tests/ingest/test_form4_internal_transfers.py``."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from domain.insider_internal_transfers import (
    INTERNAL_TRANSFER,
    NAMED_HOLDING,
    SAME_FILER,
    SHARED_HOLDING,
    is_internal_transfer,
    names_owner,
    screen_internal_transfers,
    screened_label,
    transfer_with,
)
from domain.insider_joint_reports import collapse_joint_reports

D0 = date(2025, 2, 24)


def _row(
    acc: str,
    owner: str,
    code: str,
    *,
    seq: int = 0,
    shares: float = 869_565.0,
    price: float | None = 21.85,
    di: str | None = "D",
    after: float | None = None,
    natures: list[str] | None = None,
    d: date = D0,
    cik: str | None = None,
) -> dict:
    return {
        "accession": acc,
        "txn_seq": seq,
        "insider_name": owner,
        "rpt_owner_cik": cik,
        "insider_role": "10% owner",
        "txn_code": code,
        "valid_from": d,
        "shares": shares,
        "price": price,
        "usd": shares * (price or 0.0),
        "direct_indirect": di,
        "shares_owned_after": after,
        "filing_natures": natures,
        "txn_footnotes": [],
        "accepted": datetime(2025, 2, 26, tzinfo=timezone.utc),
        "recorded_at": datetime(2025, 2, 27, tzinfo=timezone.utc),
    }


def _tags(rows):
    return [(r["insider_name"], r["txn_code"], is_internal_transfer(r)) for r in rows]


# --- nothing to screen ---------------------------------------------------------------------------------------


def test_a_read_without_a_matched_buy_and_sale_is_returned_unchanged():
    rows = [_row("a1", "KIM JAMES J", "S"), _row("a2", "OTHER", "S")]
    assert screen_internal_transfers(rows) is rows
    assert screen_internal_transfers([]) == []
    # a buy and a sale of DIFFERENT size, or price, or day: no signature
    rows = [_row("a1", "A", "P"), _row("a2", "A", "S", shares=1.0)]
    assert screen_internal_transfers(rows) is rows
    rows = [_row("a1", "A", "P"), _row("a2", "A", "S", price=21.86)]
    assert screen_internal_transfers(rows) is rows
    rows = [_row("a1", "A", "P"), _row("a1", "A", "S", seq=1, d=date(2025, 2, 25))]
    assert screen_internal_transfers(rows) is rows


# --- decision 2: the same filer ------------------------------------------------------------------------------


def test_one_filers_buy_and_sale_on_the_same_terms_are_a_transfer():
    """Pershing Square's funds "purchased and sold Subject Securities among themselves" — one filing."""
    rows = [
        _row("ps", "Pershing Square", "S", di="I"),
        _row("ps", "Pershing Square", "P", seq=1, di="I"),
    ]
    out = screen_internal_transfers(rows)
    assert _tags(out) == [("Pershing Square", "S", True), ("Pershing Square", "P", True)]
    assert out[0][INTERNAL_TRANSFER]["evidence"] == (SAME_FILER,)
    assert transfer_with(out[0]) == ("Pershing Square",)
    assert rows[0].get(INTERNAL_TRANSFER) is None  # the input is never mutated


def test_the_same_filer_by_cik_across_two_filings():
    rows = [
        _row("f1", "Fund A", "P", cik="0001234"),
        _row("f2", "FUND A LP", "S", cik="1234"),
    ]
    assert all(is_internal_transfer(r) for r in screen_internal_transfers(rows))


def test_each_buy_pairs_with_one_sale_the_extra_leg_stays_counted():
    """Goldman's two 63-share buys against ONE 63-share sale: one transfer; the second buy is a real trade."""
    rows = [
        _row("gs", "GOLDMAN", "P", seq=21, shares=63.0, price=10.98, di=None),
        _row("gs", "GOLDMAN", "P", seq=22, shares=63.0, price=10.98, di=None),
        _row("gs", "GOLDMAN", "S", seq=26, shares=63.0, price=10.98, di=None),
    ]
    out = screen_internal_transfers(rows)
    assert [is_internal_transfer(r) for r in out] == [True, False, True]
    # an INDIRECT extra leg of the same filer is still the filer's own trade — same-filer never makes a co-report
    rows[1] = {**rows[1], "direct_indirect": "I"}
    assert [is_internal_transfer(r) for r in screen_internal_transfers(rows)] == [True, False, True]


# --- decision 1: linked filers --------------------------------------------------------------------------------


def test_a_named_holding_links_two_filers():
    """Susan Kim's purchase filing reports shares "By James J. Kim 2024 GRAT": she holds her father's shares —
    his sale and her family partnership's purchase are one transfer."""
    rows = [
        _row(
            "sujoda",
            "SUJODA Investments, LP",
            "P",
            natures=["By James J. Kim 2024 GRAT dtd. 8/5/24"],
        ),
        _row("james", "KIM JAMES J", "S", natures=[]),
    ]
    out = screen_internal_transfers(rows)
    assert _tags(out) == [("SUJODA Investments, LP", "P", True), ("KIM JAMES J", "S", True)]
    assert out[0][INTERNAL_TRANSFER]["evidence"] == (NAMED_HOLDING,)
    assert transfer_with(out[0]) == ("KIM JAMES J",)
    assert transfer_with(out[1]) == ("SUJODA Investments, LP",)
    # either side's filing may state it
    rows = [
        _row("sujoda", "SUJODA Investments, LP", "P", natures=[]),
        _row("james", "KIM JAMES J", "S", natures=["By Sujoda Investments, LP"]),
    ]
    assert all(is_internal_transfer(r) for r in screen_internal_transfers(rows))


def test_a_joint_survivors_co_reporters_link_too():
    """The purchase survives the joint collapse under Sujoda (direct); the GRAT natures ride on SUSAN's row (a
    co-reporter, gone from the read) — the evidence rows carry them."""
    sujoda = _row("sujoda", "SUJODA Investments, LP", "P", di="D", after=3_347_890.0, natures=[])
    susan = _row(
        "susan",
        "KIM SUSAN Y",
        "P",
        di="I",
        after=3_347_890.0,
        natures=["By James J. Kim 2024 GRAT"],
    )
    james = _row("james", "KIM JAMES J", "S", di="D", after=71_828.0, natures=[])
    resolved = [sujoda, susan, james]
    collapsed = collapse_joint_reports(resolved)
    assert len(collapsed) == 2
    out = screen_internal_transfers(collapsed, resolved)
    assert all(is_internal_transfer(r) for r in out)
    # without the co-reporter's evidence the survivor's own filing names no one: nothing links
    assert screen_internal_transfers(collapsed) is collapsed


def test_a_shared_holding_links_two_filings():
    """A joint-report fingerprint (``filing_links``) proves the buy's filing and the sale's filing report one
    holding: here a second trade both filings report, with the same non-trivial holding left (one indirect).
    """
    rows = [
        _row("f-buy", "BUYER FUND", "P", natures=None),
        _row("f-sell", "SELLER PARTNER", "S", natures=None),
        # the fingerprint: one gift both filings report, the same 123,457 left afterwards, one held indirectly
        _row("f-buy", "BUYER FUND", "G", seq=1, shares=5.0, price=0.0, di="D", after=123_457.0),
        _row(
            "f-sell", "SELLER PARTNER", "G", seq=1, shares=5.0, price=0.0, di="I", after=123_457.0
        ),
    ]
    out = screen_internal_transfers(rows)
    assert [is_internal_transfer(r) for r in out] == [True, True, False, False]
    assert out[0][INTERNAL_TRANSFER]["evidence"] == (SHARED_HOLDING,)


def test_an_indirect_co_report_of_a_transfer_leg_is_screened_with_it():
    """Agnes Kim re-reports her husband's sale "By Spouse" (a different post-trade holding, so the joint collapse
    leaves it); Susan Kim's filing holds "By Agnes C. Kim 2024 GRAT" — linked by a holding, it is the SAME
    shares reported again."""
    rows = [
        _row("james", "KIM JAMES J", "S", di="D", after=71_828.0, natures=[]),
        _row("agnes", "KIM AGNES C", "S", di="I", after=10_020_870.0, natures=[]),
        _row(
            "sujoda",
            "SUJODA Investments, LP",
            "P",
            natures=[
                "By James J. Kim 2024 GRAT dtd. 8/5/24",
                "By Agnes C. Kim 2024 GRAT dtd. 8/5/24",
            ],
        ),
    ]
    out = screen_internal_transfers(rows)
    assert all(is_internal_transfer(r) for r in out)
    tag_james, tag_agnes = out[0][INTERNAL_TRANSFER], out[1][INTERNAL_TRANSFER]
    assert tag_james["co_report"] is False and tag_agnes["co_report"] is True
    assert transfer_with(out[1]) == ("SUJODA Investments, LP",)
    assert screened_label(out) == "3 internal-transfer txns ($56,999,986) screened"
    # a DIRECT leftover is a person's own sale — counted
    rows[1] = {**rows[1], "direct_indirect": "D"}
    assert [is_internal_transfer(r) for r in screen_internal_transfers(rows)] == [True, False, True]


# --- decision 3 + safety: what never links ---------------------------------------------------------------------


def test_a_private_sale_between_unrelated_people_is_not_a_transfer():
    """FTHM: a director buys the founder's block; each footnote NAMES the other — a counterparty, not a holding.
    Real conviction (decision 3)."""
    rows = [
        {
            **_row("flanders", "Flanders Scott N", "P", shares=100_000.0, price=2.85, natures=[]),
            "txn_footnotes": [
                "a private purchase ... between the Reporting Person and ... Joshua Harley."
            ],
        },
        {
            **_row(
                "harley",
                "Harley Joshua",
                "S",
                shares=100_000.0,
                price=2.85,
                natures=["See Footnote (3)"],
            ),
            "txn_footnotes": [
                "a private sale ... between the Reporting Person and a Company director, Scott Flanders."
            ],
        },
    ]
    assert screen_internal_transfers(rows) is rows


def test_missing_evidence_never_screens():
    """A row stored before migration 0055 (``filing_natures`` NULL) links nothing by name: it stays counted."""
    rows = [
        _row("sujoda", "SUJODA Investments, LP", "P", natures=None),
        _row("james", "KIM JAMES J", "S", natures=None),
    ]
    assert screen_internal_transfers(rows) is rows


def test_postgres_and_mirror_types_screen_alike():
    """``Decimal`` shares/prices (Postgres) and ``float`` (the DuckDB mirror) form the same signature."""
    pg = [
        {**_row("ps", "PS", "S"), "shares": Decimal("869565"), "price": Decimal("21.8500")},
        {**_row("ps", "PS", "P", seq=1), "shares": Decimal("869565.0"), "price": Decimal("21.85")},
    ]
    assert all(is_internal_transfer(r) for r in screen_internal_transfers(pg))


# --- naming ----------------------------------------------------------------------------------------------------


def test_names_owner_needs_every_identifying_word():
    assert names_owner("KIM JAMES J", "By James J. Kim 2024 GRAT dtd. 8/5/24")
    assert names_owner("SUJODA Investments, LP", "By Sujoda Investments, LP")
    assert not names_owner("KIM JAMES J", "By John T. Kim Family Trust")  # another Kim
    assert not names_owner("KIM JAMES J", "By Spouse")
    # a lone short word names no one ("LEE S III" -> {lee}); stop-words alone name no one
    assert not names_owner("LEE S III", "By Lee Family Trust")
    assert not names_owner("Capital Partners LP", "By Capital Partners LP")
    assert not names_owner(None, "anything") and not names_owner("KIM JAMES J", None)
