"""INTERNAL TRANSFERS + PRIVATE PURCHASES on REAL filings — the verification table of the operator's 2026-10-21
decisions (``domain/insider_internal_transfers.py``, ``domain/insider_private_purchases.py``). Fixtures +
provenance: ``tests/transfer_fixtures.py``.

Each case stores the real filings exactly the way the nightly leg does (``ingest_form4``) and reads them back
through the ONE as-of seam every reader uses, then through the readers themselves: the two insider detectors, the
Cockpit's insider-flow display member and the Scoreboard ledger overlays. "Before" is the same read with the
screen switched off — what the system served until now — so every case shows the defect AND the fix.

  AMKR 2025-02-24 / 2025-08-01  James Kim -> Sujoda: the buy, his sale and Agnes Kim's "By Spouse" re-report
                                 screened (named holdings); the CORE arm and "2 insiders sold $38.0M" gone
  AMKR 2021-12-30               NOT screened — the day's own filings name no counterparty holding (pinned)
  SLSN 2012 x2                  Whitmore's buy + Grace Brothers' sale in ONE filing: screened (same filer)
  ESI (Pershing) x2, BHC (ValueAct)  one filer's buy and sale: screened
  MRLN (Goldman)                matched fills screened; the unmatched 63-share buy stays counted
  FTHM, RDNW, ASTH x3, FRMM     private sales between separate people: COUNTED, the ledger note
  MGM (IAC / Corvex)            counted, no footnote -> no note
  ALAB, TSM, INFQ, OKLO         untouched (no matched buy and sale)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import domain.insider_amendments as insider_amendments
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade
from domain.insider_internal_transfers import (
    INTERNAL_TRANSFER,
    NAMED_HOLDING,
    SAME_FILER,
    is_internal_transfer,
)
from ingest.edgar.form4 import parse_form4
from scoreboard.overlays import episode_insider_buys, episode_insider_sells
from signals import insider_conviction, insider_sell
from signals.base import PointInTimeData
from signals.display import insider_flow
from tests import joint_report_fixtures
from tests.transfer_fixtures import seed, xml

FAR = datetime(
    2100, 1, 1, tzinfo=timezone.utc
)  # "everything recorded so far" — the valid-time axis only
JAMES, AGNES, SUJODA = "KIM JAMES J", "KIM AGNES C", "SUJODA Investments, LP"


@pytest.fixture
def unscreened(monkeypatch):
    """The read with the internal-transfer screen switched OFF — what the system served before (the phantom
    buy and the transfer sales counted)."""

    def _off():
        monkeypatch.setattr(
            insider_amendments, "screen_internal_transfers", lambda rows, ev=None: rows
        )

    return _off


def _read(db, sid, asof: date) -> list[dict]:
    return PointInTimeData(db, asof=asof, known_at=FAR).insider_txns(sid)


def _on(rows, d: date, codes: tuple[str, ...] = ("P", "S")) -> list[dict]:
    """The day's buys and sales (the 2025-08-01 filings also report the family's GRAT gifts, code G)."""
    return [r for r in rows if r["valid_from"] == d and r["txn_code"] in codes]


def _ledger(fn, db, sid, start: date, asof: date) -> list[dict]:
    return fn(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        start=start,
        end=asof,
        asof=asof,
        known_at=FAR,
        day_lows={},
    )


# --- the parser: the filer's own words, verbatim (migration 0055) ---------------------------------------------


def test_the_parser_captures_the_filers_words_verbatim():
    (james,) = parse_form4(xml("0001158839-25-000004"))
    assert james["direct_indirect"] == "D" and james["ownership_nature"] is None
    assert james["txn_footnotes"] == [
        "On February 24, 2025, the Reporting Person distributed 869,565 shares of the Common Stock of Amkor "
        'Technology, Inc. (the "Issuer") to Sujoda Investments, LP pursuant to the terms of a transaction '
        "between members of the Kim family group."
    ]
    # every INDIRECT nature the filing states — its holdings too: James Kim holds his son's trusts
    assert james["filing_natures"] == [
        "By Spouse",
        "By John T. Kim Family Trust U/A dtd. 12/11/12",
        "By John T. Kim G-S Trust dtd. 12/11/12",
        "By self as Trustee of Trust U/A dtd. 12/11/12",
    ]
    (agnes,) = parse_form4(xml("0001158840-25-000004"))
    assert (agnes["direct_indirect"], agnes["ownership_nature"]) == ("I", "By Spouse")
    assert agnes["txn_footnotes"][1].startswith(
        "James J. Kim is the spouse of the Reporting Person."
    )
    (susan,) = parse_form4(xml("0001158922-25-000006"))
    assert "By James J. Kim 2024 GRAT dtd. 8/5/24" in susan["filing_natures"]
    assert "By Agnes C. Kim 2024 GRAT dtd. 8/5/24" in susan["filing_natures"]
    # a filing that states none captures none (an empty list — "captured, none", never NULL)
    (iac,) = parse_form4(xml("0001104659-22-024271"))
    assert (iac["txn_footnotes"], iac["filing_natures"], iac["ownership_nature"]) == ([], [], None)


def test_the_stored_rows_carry_the_capture(db):
    sid = seed(db, "AMKR")
    with db.cursor() as cur:
        cur.execute(
            "SELECT ownership_nature, txn_footnotes, filing_natures FROM fact_insider_txn "
            "WHERE security_id = %s AND accession = '0001158840-25-000004'",
            (sid,),
        )
        (row,) = cur.fetchall()
    assert row["ownership_nature"] == "By Spouse"
    assert row["filing_natures"] == ["By Spouse"] and len(row["txn_footnotes"]) == 2


# --- AMKR 2025 — the reported symptom ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asof", "d", "usd"),
    [
        (date(2025, 3, 3), date(2025, 2, 24), 18_999_995.25),  # 869,565 sh @ $21.85
        (date(2025, 8, 8), date(2025, 8, 1), 9_648_719.65),  # 441,589 sh @ $21.85
    ],
)
def test_amkr_james_kims_transfer_to_sujoda_is_screened(db, unscreened, asof, d, usd):
    sid = seed(db, "AMKR")
    after = _on(_read(db, sid, asof), d)
    unscreened()
    before = _on(_read(db, sid, asof), d)

    # every leg STAYS on the read (keep-and-screen) — only tagged
    assert (
        sorted((r["insider_name"], r["txn_code"]) for r in after)
        == sorted((r["insider_name"], r["txn_code"]) for r in before)
        == sorted([(SUJODA, "P"), (JAMES, "S"), (AGNES, "S")])
    )
    by = {r["insider_name"]: r for r in after}
    assert all(is_internal_transfer(r) for r in after)
    assert by[SUJODA][INTERNAL_TRANSFER]["evidence"] == (NAMED_HOLDING,)
    assert (
        SUJODA in by[JAMES][INTERNAL_TRANSFER]["with"]
    )  # the purchase's filers, the survivor first
    assert by[SUJODA][INTERNAL_TRANSFER]["with"] == (JAMES,)
    assert by[AGNES][INTERNAL_TRANSFER]["co_report"] is True  # her "By Spouse" copy of James's sale

    # BEFORE: the phantom CORE buy (a director co-reported it — Susan Kim — so it is senior)
    buy = insider_conviction.score(before, sid, asof, DEFAULT_CONFIG)
    assert buy is not None and buy.grade == Grade.CORE
    assert buy.label.startswith(f"1 insider incl. senior officer bought ${usd:,.0f}")
    # ... and the two copies of ONE sale counted as two sellers ($2x for one transfer) — on these filings alone
    # no senior seller, so no sell cluster fires either way (the label with a real senior seller beside the
    # transfer is pinned on direct inputs: tests/signals/test_insider_internal_transfer_screens.py)
    sales = [r for r in before if r["txn_code"] == "S"]
    assert len({r["insider_name"] for r in sales}) == 2
    assert sum(float(r["usd"]) for r in sales) == pytest.approx(2 * usd)
    # AFTER: no buy at all, no sale at all
    assert insider_conviction.score(after, sid, asof, DEFAULT_CONFIG) is None
    assert insider_sell.score(after, sid, asof, DEFAULT_CONFIG) is None


def test_amkr_every_reader_sees_the_transfer_set_aside(db):
    sid = seed(db, "AMKR")
    asof = date(2025, 3, 3)
    flow = insider_flow.display(PointInTimeData(db, asof=asof, known_at=FAR), sid, asof)
    metrics = {m.key: m.value for m in flow.metrics}
    assert (metrics["buy_count"], metrics["sell_count"]) == (0, 0)
    assert "set aside 3 internal-transfer txns (~$57M)" in flow.basis.note
    buys = _ledger(episode_insider_buys, db, sid, date(2025, 2, 1), asof)
    (b,) = buys
    assert b["character"] == "internal_transfer" and JAMES in b["transfer_with"]
    assert b["private_passage"] is None  # a transfer's own label says more than a note
    sells = _ledger(episode_insider_sells, db, sid, date(2025, 2, 1), asof)
    assert {s["character"] for s in sells} == {"internal_transfer"} and len(sells) == 2
    assert all(SUJODA in s["transfer_with"] for s in sells)


def test_amkr_2021_note_payment_is_not_linked_by_its_own_filings(db):
    """PINNED — a deviation the report names: Sujoda's 2021-12-30 note-payment transfers to James Kim are NOT
    screened. The day's own filings state only generic natures ("By GRATs", "By trusts", "By limited
    partnerships", "By spouse"), and the screen reads the legs' own filings only — the natures naming the other
    side first appear in filings of 2022+ (never this read's evidence). Both sides name the counterparty in a
    FOOTNOTE ("transferred ... to James J. Kim in payment of the principal of a Note"), which is the class-b
    (private-sale) evidence the operator kept counted."""
    sid = seed(db, "AMKR21")
    rows = _on(_read(db, sid, date(2022, 1, 10)), date(2021, 12, 30))
    assert {r["txn_code"] for r in rows} == {"P", "S"}
    assert not any(is_internal_transfer(r) for r in rows)


# --- SLSN / Pershing Square / ValueAct / Goldman — one filer, a buy and a sale ----------------------------------


@pytest.mark.parametrize(
    ("d", "shares"), [(date(2012, 3, 14), 1_152_002.0), (date(2012, 9, 12), 2_421_918.0)]
)
def test_slsn_whitmores_purchase_from_grace_brothers_is_screened(db, d, shares):
    sid = seed(db, "SLSN")
    rows = [r for r in _on(_read(db, sid, date(2012, 12, 31)), d) if float(r["shares"]) == shares]
    by = {(r["insider_name"], r["txn_code"]): r for r in rows}
    assert set(by) == {
        ("WHITMORE BRADFORD T", "P"),
        ("WHITMORE BRADFORD T", "S"),
        ("SPURGEON CORP", "S"),
    }
    assert is_internal_transfer(by[("WHITMORE BRADFORD T", "P")])
    assert is_internal_transfer(by[("WHITMORE BRADFORD T", "S")])
    assert by[("WHITMORE BRADFORD T", "P")][INTERNAL_TRANSFER]["evidence"][0] == SAME_FILER
    # Spurgeon Corp's re-report of Grace Brothers' sale states a different holding and names no one: counted
    assert not is_internal_transfer(by[("SPURGEON CORP", "S")])


@pytest.mark.parametrize(
    ("case", "d", "filer"),
    [
        ("ESI", date(2015, 7, 1), "Pershing Square Capital Management, L.P."),
        ("ESI", date(2018, 8, 6), "Pershing Square Capital Management, L.P."),
        ("BHC", date(2020, 6, 5), "ValueAct Holdings, L.P."),
    ],
)
def test_a_fund_familys_internal_rebalancing_is_screened(db, case, d, filer):
    sid = seed(db, case)
    rows = _on(_read(db, sid, d), d)
    assert {(r["insider_name"], r["txn_code"]) for r in rows} == {(filer, "P"), (filer, "S")}
    assert all(r[INTERNAL_TRANSFER]["evidence"] == (SAME_FILER,) for r in rows)
    assert insider_conviction.score(rows, sid, d, DEFAULT_CONFIG) is None


def test_goldmans_matched_fills_screened_the_unmatched_buy_counted(db):
    sid = seed(db, "MRLN")
    d = date(2025, 12, 8)
    rows = [
        r
        for r in _on(_read(db, sid, d), d)
        if float(r["shares"]) == 63.0 and float(r["price"]) == 10.98
    ]
    assert sorted((r["txn_code"], is_internal_transfer(r)) for r in rows) == [
        ("P", False),
        ("P", True),
        ("S", True),
    ]


# --- decision 3 — private purchases between separate people: counted, with the ledger note -------------------------


@pytest.mark.parametrize(
    ("case", "d", "counterparty"),
    [
        ("FTHM", date(2024, 9, 12), "Harley Joshua"),
        ("RDNW", date(2021, 12, 30), "Coulter William"),
        ("ASTH", date(2021, 8, 17), "Schmidt David"),
        (
            "ASTH",
            date(2022, 6, 17),
            None,
        ),  # "purchased from Alliance Apex, LLC." — a non-filer, never guessed
        ("FRMM", date(2023, 12, 15), "Feldmann Marc"),
    ],
)
def test_a_private_purchase_counts_and_carries_its_note(db, case, d, counterparty):
    sid = seed(db, case)
    rows = _on(_read(db, sid, d), d)
    assert {r["txn_code"] for r in rows} >= {"P", "S"}
    assert not any(is_internal_transfer(r) for r in rows)  # real conviction (decision 3)
    (buy,) = [
        b
        for b in _ledger(episode_insider_buys, db, sid, d, d)
        if b["d"] == d and b["character"] != "internal_transfer"
    ]
    assert buy["character"] == "open_market"
    assert buy["private_passage"] is not None
    assert buy["private_counterparty"] == counterparty
    # the buy is in the conviction total (whatever the floors make of its size)
    assert any(r["txn_code"] == "P" and not is_internal_transfer(r) for r in rows)


def test_asth_2021_04_28_has_no_footnote_so_no_note(db):
    sid = seed(db, "ASTH")
    d = date(2021, 4, 28)
    (buy,) = [b for b in _ledger(episode_insider_buys, db, sid, d, d) if b["d"] == d]
    assert buy["private_passage"] is None and buy["character"] == "open_market"


def test_mgm_iac_buying_from_corvex_counts_without_a_note(db):
    sid = seed(db, "MGM")
    d = date(2022, 2, 14)
    rows = _on(_read(db, sid, d), d)
    assert {(r["insider_name"], r["txn_code"]) for r in rows} >= {
        ("IAC/InterActiveCorp", "P"),
        ("Meister Keith A.", "S"),
    }
    assert not any(is_internal_transfer(r) for r in rows)
    (buy,) = _ledger(episode_insider_buys, db, sid, d, d)
    assert buy["private_passage"] is None and buy["transfer_with"] is None
    fired = insider_conviction.score(rows, sid, d, DEFAULT_CONFIG)
    assert (
        fired is not None
        and "$202,500,000" in fired.label
        and "internal-transfer" not in fired.label
    )


# --- the negatives of the joint-report table: untouched -------------------------------------------------------------


@pytest.mark.parametrize(
    ("ticker", "asof"),
    [
        ("ALAB", date(2025, 8, 20)),
        ("TSM", date(2026, 10, 9)),
        ("INFQ", date(2026, 6, 5)),
        ("OKLO", date(2026, 10, 2)),
    ],
)
def test_the_joint_report_cases_are_unchanged(db, unscreened, ticker, asof):
    sid = joint_report_fixtures.seed(db, ticker)
    after = _read(db, sid, asof)
    unscreened()
    assert after == _read(db, sid, asof)
    assert not any(is_internal_transfer(r) for r in after)


# --- safe on missing evidence: a row stored before 0055 links nothing by name ---------------------------------------


def test_rows_without_the_capture_are_never_screened_by_name(db, monkeypatch):
    import ingest.edgar.form4 as form4

    real = form4.parse_form4

    def pre_0055(doc: str) -> list[dict]:
        return [
            {**r, "ownership_nature": None, "txn_footnotes": None, "filing_natures": None}
            for r in real(doc)
        ]

    monkeypatch.setattr(form4, "parse_form4", pre_0055)
    sid = seed(db, "AMKR")
    rows = _on(_read(db, sid, date(2025, 3, 3)), date(2025, 2, 24))
    assert len(rows) == 3 and not any(is_internal_transfer(r) for r in rows)
