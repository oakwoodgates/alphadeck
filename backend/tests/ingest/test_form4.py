from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ingest.edgar.form4 import (
    _norm_cik,
    _txn_date,
    accession_filing_year,
    existing_accessions,
    implausible_txn_date,
    ingest_form4,
    parse_form4,
)

_FIX = Path(__file__).resolve().parent.parent / "fixtures" / "edgar"
_XML = (_FIX / "form4_sample.xml").read_text(encoding="utf-8")
# REAL dual-listed Form 4 fixtures (Part 0 of the S2c ADR mis-attribution fix — committed verbatim):
#   form4_tsm_mixed.xml   — TSM 0001046179-26-000461: ONE filing carrying BOTH 2 ADR txns ("American
#                           Depositary Shares (TSM)") and 1 ordinary txn ("Common Shares (2330.TW)"),
#                           foreign symbol 2330.TW — the per-transaction discrimination, on real data.
#   form4_tsm_ordinary.xml— TSM 0001046179-26-000445: a pure home-market ESPP filing (Common Shares
#                           (2330.TW), code P) — the mis-attributed shape.
#   form4_pbr_ordinary.xml— PBR 0001292814-26-002254: Petrobras titles the row with the BARE foreign
#                           symbol "PETR4" (no parenthetical) — a different real title shape.
_TSM_MIXED = (_FIX / "form4_tsm_mixed.xml").read_text(encoding="utf-8")
_TSM_ORDINARY = (_FIX / "form4_tsm_ordinary.xml").read_text(encoding="utf-8")
_PBR_ORDINARY = (_FIX / "form4_pbr_ordinary.xml").read_text(encoding="utf-8")


def test_parse_form4_extracts_transactions():
    txns = parse_form4(_XML)
    assert len(txns) == 2

    buy = next(t for t in txns if t["txn_code"] == "P")  # open-market purchase
    assert buy["shares"] == 10000
    assert buy["price"] == 21.0
    assert buy["usd"] == 210000.0
    assert buy["txn_date"] == date(2026, 6, 1)
    assert buy["insider_name"] == "Doe Jane"
    assert "Chief Executive Officer" in (buy["insider_role"] or "")

    assert any(t["txn_code"] == "S" for t in txns)  # the sale is parsed too; the detector filters


# --- the Rule 10b5-1 checkbox (CAPTURE-ONLY — no detector reads it) ---


def _with_aff(value: str) -> str:
    """The sample filing with an `<aff10b5One>` element injected at DOCUMENT level (where the SEC puts it:
    right after </reportingOwner>, not on a transaction)."""
    return _XML.replace(
        "</reportingOwner>", f"</reportingOwner>\n  <aff10b5One>{value}</aff10b5One>"
    )


def test_aff10b5one_absent_is_UNKNOWN_never_false():
    """THE LOAD-BEARING NULL: the sample has no checkbox — the shape of every filing before the SEC's
    Dec-2022 amendments. Absent must parse to None (unknown), NEVER False: False would assert "this sale was
    discretionary" about a filing that never said so — inventing a fact (#3)."""
    for t in parse_form4(_XML):
        assert t["aff_10b5_1"] is None


def test_aff10b5one_checked_and_clear_parse_to_true_and_false():
    """1/true = a PRE-PLANNED trade (autopilot, ~no information); 0/false = discretionary (a real decision)."""
    for checked in ("1", "true"):
        assert all(t["aff_10b5_1"] is True for t in parse_form4(_with_aff(checked)))
    for clear in ("0", "false"):
        assert all(t["aff_10b5_1"] is False for t in parse_form4(_with_aff(clear)))


def test_aff10b5one_is_filing_level_stamped_on_every_row():
    """The element is on the ownership DOCUMENT, so it applies to every transaction the filing reports —
    including the sale AND the purchase in this multi-txn sample."""
    txns = parse_form4(_with_aff("1"))
    assert len(txns) == 2 and {t["txn_code"] for t in txns} == {"P", "S"}
    assert all(t["aff_10b5_1"] is True for t in txns)


def test_aff10b5one_garbage_value_is_unknown_not_a_guess():
    assert all(t["aff_10b5_1"] is None for t in parse_form4(_with_aff("maybe")))


def test_ingest_form4_stores_the_flag(db, security_id):
    """It reaches the column (tri-state preserved through the append)."""
    ingest_form4(db, security_id, _with_aff("1"), "acc-planned")
    ingest_form4(db, security_id, _XML, "acc-unknown")  # no checkbox -> NULL
    with db.cursor() as cur:
        cur.execute(
            "SELECT accession, aff_10b5_1 FROM fact_insider_txn WHERE security_id=%s",
            (security_id,),
        )
        got = {(r["accession"], r["aff_10b5_1"]) for r in cur.fetchall()}
    assert ("acc-planned", True) in got
    assert ("acc-unknown", None) in got  # unknown stays NULL, never False


# --- the SEC acceptance datetime (the honest "disclosed" clock — the MRVL two-clock fix) ---


def test_ingest_form4_stores_the_accepted_datetime(db, security_id):
    """The acceptance datetime threaded from the enumeration reaches the ``accepted`` column (filing-level,
    stamped on every row); absent -> NULL, so the display/metrics fall back to recorded_at/"ingested" (#9).
    parse_form4 is UNCHANGED — the ownership XML has no acceptance datetime, so this rides as a kwarg.
    """
    from datetime import datetime, timezone

    accepted = datetime(2025, 9, 27, 18, 30, 41, tzinfo=timezone.utc)
    ingest_form4(db, security_id, _XML, "acc-accepted", accepted=accepted)
    ingest_form4(db, security_id, _XML, "acc-noaccept")  # no accepted kwarg -> NULL
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT accession, accepted FROM fact_insider_txn WHERE security_id=%s AND txn_code='P'",
            (security_id,),
        )
        got = {(r["accession"], r["accepted"]) for r in cur.fetchall()}
    assert ("acc-accepted", accepted) in got  # reaches the column, tz-aware
    assert ("acc-noaccept", None) in got  # unresolved stays NULL (#9)


def test_the_flag_changes_NO_signal_logic(security_id):
    """CAPTURE-ONLY, proved: insider_conviction reads code 'P' and nothing else, so a buy fires IDENTICALLY
    whether it was planned, discretionary, or unknown. This slice stores data; it does not touch the call.
    """
    from signals.insider_conviction import score

    def buy(aff):
        return [
            {
                "txn_code": "P",
                "valid_from": date(2026, 6, 1),
                "usd": 500_000.0,
                "insider_name": "Doe Jane",
                "insider_role": "Chief Executive Officer",
                "accession": "acc-1",
                "aff_10b5_1": aff,  # planned / discretionary / unknown — the detector never looks
            }
        ]

    events = [score(buy(a), security_id, date(2026, 6, 8)) for a in (True, False, None)]
    assert all(e is not None and e.fired for e in events)
    # identical scoring across all three states — the flag is inert on the call path
    assert len({(e.score, e.grade, e.kind, e.role) for e in events}) == 1


# --- the tz-offset transactionDate (a RECENT valid Form 4 must not be silently skipped) ---


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-05-13-05:00", date(2026, 5, 13)),  # the AEHR case: a UTC offset on a date-only field
        ("2026-05-14-05:00", date(2026, 5, 14)),  # EST offset, the second skipped AEHR filing
        ("2026-06-01-04:00", date(2026, 6, 1)),  # EDT offset
        ("2026-06-01+00:00", date(2026, 6, 1)),  # a positive offset
        ("2026-06-01Z", date(2026, 6, 1)),  # a 'Z' suffix (datetime.fromisoformat rejects this one)
        ("2026-06-01T00:00:00-05:00", date(2026, 6, 1)),  # a full datetime with offset
        ("2026-06-01", date(2026, 6, 1)),  # the plain, well-formed case still works
        ("  2026-06-01  ", date(2026, 6, 1)),  # surrounding whitespace tolerated
        (None, None),  # absent date -> None (row later dropped by ingest_form4)
        ("", None),  # empty -> None
    ],
)
def test_txn_date_strips_tz_offset(raw, expected):
    """The offset never SHIFTS the calendar trade date — '2026-05-13-05:00' is the 13th, not the 12th/14th."""
    assert _txn_date(raw) == expected


def test_txn_date_still_raises_on_genuinely_malformed():
    """A value with no leading ISO date is still a ValueError — so the ingest leg skips-and-COUNTS that one
    filing (loud), rather than the fix swallowing a real parse failure into a silent success."""
    with pytest.raises(ValueError):
        _txn_date("not-a-date")


def _with_txn_date(value: str) -> str:
    """The sample filing with the open-market BUY's transactionDate replaced by ``value`` (e.g. a
    tz-offset-suffixed date)."""
    return _XML.replace(
        "<transactionDate><value>2026-06-01</value></transactionDate>",
        f"<transactionDate><value>{value}</value></transactionDate>",
    )


def test_parse_form4_keeps_the_buy_when_the_date_carries_a_tz_offset():
    """THE REGRESSION: a tz-suffixed transactionDate ('YYYY-MM-DD-05:00') used to raise inside parse_form4,
    which the ingest leg tolerated by skipping the ENTIRE filing — dropping the open-market buy before it
    could reach the Key-1 insider detector. The buy must survive with its date intact, and so must the sale.
    """
    txns = parse_form4(_with_txn_date("2026-05-13-05:00"))
    assert len(txns) == 2  # nothing dropped — the whole filing still parses
    buy = next(t for t in txns if t["txn_code"] == "P")
    assert buy["txn_date"] == date(2026, 5, 13)  # the calendar date, NOT shifted by the offset
    assert buy["shares"] == 10000 and buy["usd"] == 210000.0  # the buy the detector needs, intact


def test_ingest_form4_stores_a_tz_suffixed_buy(db, security_id):
    """End-to-end: a filing whose buy carries a tz-offset date now reaches ``fact_insider_txn`` (before the
    fix the whole accession was skipped-and-counted, so the row never landed)."""
    n = ingest_form4(db, security_id, _with_txn_date("2026-05-13-05:00"), "acc-tzoffset")
    db.commit()
    assert n == 2  # both rows stored
    with db.cursor() as cur:
        cur.execute(
            "SELECT valid_from FROM fact_insider_txn WHERE accession=%s AND txn_code='P'",
            ("acc-tzoffset",),
        )
        row = cur.fetchone()
    assert row is not None and row["valid_from"] == date(2026, 5, 13)


# --- issuer + reporting-owner IDENTITY capture (migration 0024) — the insider-detector self-filing screen ---


def test_norm_cik_strips_padding_and_whitespace():
    assert _norm_cik("0001773751") == "1773751"
    assert _norm_cik("  0000054321 ") == "54321"
    assert _norm_cik(None) is None and _norm_cik("") is None and _norm_cik("0000") is None


def test_parse_form4_captures_issuer_and_owner_identity():
    buy = next(t for t in parse_form4(_XML) if t["txn_code"] == "P")
    # from <issuer> and <reportingOwnerId> — CIKs normalized (leading zeros stripped)
    assert buy["issuer_cik"] == "1234567"
    assert buy["issuer_name"] == "Devco Inc"
    assert buy["rpt_owner_cik"] == "7654321"
    # the sample is NOT a self-filing (owner 7654321 != issuer 1234567)
    assert buy["rpt_owner_cik"] != buy["issuer_cik"]


def _as_self_filing(xml: str) -> str:
    """The sample filing rewritten as a SELF-filing: the reporting owner IS the issuer (same CIK + name) —
    the KYOCERA-on-KYOCERA / Roivant-on-Roivant shape."""
    return xml.replace("0007654321", "0001234567").replace("Doe Jane", "Devco Inc")


def test_parse_form4_self_filing_has_matching_owner_and_issuer_cik():
    buy = next(t for t in parse_form4(_as_self_filing(_XML)) if t["txn_code"] == "P")
    assert buy["rpt_owner_cik"] == buy["issuer_cik"] == "1234567"
    assert buy["insider_name"] == buy["issuer_name"] == "Devco Inc"


def test_ingest_form4_stores_issuer_owner_identity(db, security_id):
    ingest_form4(db, security_id, _XML, "acc-identity")
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT issuer_cik, issuer_name, rpt_owner_cik FROM fact_insider_txn "
            "WHERE accession=%s AND txn_code='P'",
            ("acc-identity",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["issuer_cik"] == "1234567"
    assert row["issuer_name"] == "Devco Inc"
    assert row["rpt_owner_cik"] == "7654321"


def test_existing_accessions_is_distinct_set(db, security_id):
    assert existing_accessions(db, security_id) == set()  # nothing stored yet
    # two filings (4 rows total) — the helper returns the DISTINCT accessions, not the row count
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    ingest_form4(db, security_id, _XML, "0000000000-26-000002")
    db.commit()
    assert existing_accessions(db, security_id) == {
        "0000000000-26-000001",
        "0000000000-26-000002",
    }


# --- security_title + issuer_foreign_symbol capture (migration 0041) — the ADR mis-attribution screen inputs ---


def test_parse_captures_per_txn_security_title_and_filing_foreign_symbol():
    """REAL TSM filing: the per-transaction ``<securityTitle>`` separates the ADR rows from the ordinary
    row (both in ONE filing), and the filing-level ``<issuerForeignTradingSymbol>`` (2330.TW) rides on
    every row. ``parse_form4`` reads only nonDerivativeTRANSACTIONs (holdings are ignored)."""
    txns = parse_form4(_TSM_MIXED)
    assert [t["security_title"] for t in txns] == [
        "American Depositary Shares (TSM)",
        "American Depositary Shares (TSM)",
        "Common Shares (2330.TW)",
    ]
    assert all(t["issuer_foreign_symbol"] == "2330.TW" for t in txns)  # filing-level, on every row


def test_parse_us_filing_has_title_but_no_foreign_symbol():
    """A US issuer declares no foreign symbol — the title is captured, the foreign symbol is None (so the
    screen never fires on a US name; keep-when-ambiguous #9)."""
    for t in parse_form4(_XML):
        assert t["security_title"] == "Common Stock"
        assert t["issuer_foreign_symbol"] is None


def test_parse_pbr_bare_foreign_symbol_title():
    """Petrobras titles the row with the BARE foreign symbol (no parenthetical) — a different real shape
    the containment predicate still catches (the title IS the foreign symbol)."""
    txns = parse_form4(_PBR_ORDINARY)
    assert {t["security_title"] for t in txns} == {"PETR4"}
    assert all(t["issuer_foreign_symbol"] == "PETR4" for t in txns)


def test_absent_security_title_is_none_never_empty_string():
    """A transaction with no ``<securityTitle>`` parses to None (kept — a NULL title is never screened)."""
    stripped = _XML.replace("<securityTitle><value>Common Stock</value></securityTitle>", "")
    assert all(t["security_title"] is None for t in parse_form4(stripped))


def test_ingest_stores_security_title_and_foreign_symbol(db, security_id):
    """Both columns reach ``fact_insider_txn`` — per-txn title distinct per row, foreign symbol on every
    row of the filing."""
    ingest_form4(db, security_id, _TSM_MIXED, "acc-tsm")
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT security_title, issuer_foreign_symbol FROM fact_insider_txn "
            "WHERE accession=%s ORDER BY txn_seq",
            ("acc-tsm",),
        )
        rows = cur.fetchall()
    assert [r["security_title"] for r in rows] == [
        "American Depositary Shares (TSM)",
        "American Depositary Shares (TSM)",
        "Common Shares (2330.TW)",
    ]
    assert {r["issuer_foreign_symbol"] for r in rows} == {"2330.TW"}


# --- P1: the transaction-date sanity bound ------------------------------------------------------------
#
# ROOT CAUSE, settled against the SEC documents themselves before any of this was written (the fix differs
# completely depending on the answer): the impossible dates are FILER GARBAGE, not a parser defect. Fetched
# live from EDGAR 2026-09-15 —
#   0001628280-24-010030 (BBAI)  <transactionDate><value>0023-03-23</value></transactionDate>
#   0001437749-24-004874 (LSCC)  <transactionDate><value>2027-02-17</value></transactionDate>
# — verbatim, in the raw ownership XML. ``parse_form4`` reproduces the filer's value faithfully, so there is
# nothing to re-derive and no corrected date may be invented (#3). The row is rejected, loudly.


@pytest.mark.parametrize(
    "accession, expected",
    [
        ("0001628280-24-010030", 2024),  # the real BBAI leading-zero filing
        ("0001437749-24-004874", 2024),  # the real LSCC future-dated filing
        ("0001181431-06-033946", 2006),  # a 2006 filing reporting genuine 1990s transactions
        ("0000912057-99-012345", 1999),  # the 1990s pivot: 93-99 is the 20th century
        ("0000912057-93-000001", 1993),  # EDGAR's first electronic year
        ("0000912057-00-000001", 2000),  # ...and 00 is the 21st
        ("acc-planned", None),  # a seed/test label: no filing year -> abstain, no upper bound
        ("DEMO-F4", None),
        ("0001628280-24-01003", None),  # malformed (5-digit sequence) -> abstain, never a guess
    ],
)
def test_accession_filing_year(accession, expected):
    assert accession_filing_year(accession) == expected


@pytest.mark.parametrize(
    "txn_date, accession",
    [
        (date(23, 3, 23), "0001628280-24-010030"),  # BBAI — leading-zero year, verbatim in the XML
        (
            date(24, 2, 12),
            "0000913760-24-000032",
        ),  # SNEX — beside a correct 2024-02-12 in the same doc
        (date(24, 10, 2), "0000912282-24-000687"),  # UUUU
        (date(25, 7, 25), "0001900188-25-000010"),  # GS
        (date(2027, 2, 17), "0001437749-24-004874"),  # LSCC — three years after the filing
        (date(2027, 11, 17), "0001628280-23-039558"),  # CRDO
        (
            date(2024, 10, 24),
            "0001628280-23-035012",
        ),  # CRDO — only ONE year ahead, still impossible
        (date(2022, 1, 5), "0001209191-21-002530"),  # PENN — the same off-by-one-year shape
    ],
)
def test_implausible_txn_date_flags_every_measured_impossible_row(txn_date, accession):
    """The eight real (date, accession) pairs measured on the dev copy of prod. Not a synthetic shape —
    each one is a stored row whose date the filing states verbatim."""
    assert implausible_txn_date(txn_date, accession) is not None


@pytest.mark.parametrize(
    "txn_date, accession",
    [
        # THE FALSE-POSITIVE GUARD, and the reason the lower anchor is 1934 and not 2003: a Form 4 filed
        # years later legitimately reports an old transaction. MEASURED: 42 corpus rows carry a real
        # 1993-2002 date on accessions filed in 2006/2007. A "predates EDGAR's 2003 mandate" floor would
        # have deleted every one of them.
        (date(1993, 5, 11), "0001181431-06-033946"),  # the oldest genuine row in the corpus
        (date(1995, 9, 30), "0001144204-07-028344"),
        (date(2000, 12, 15), "0001144204-07-028340"),
        (date(2024, 3, 5), "0001628280-24-010030"),  # a correct row from the BBAI filing itself
        (date(2024, 2, 12), "0000913760-24-000032"),  # ...and from the SNEX one
        (
            date(2024, 12, 31),
            "0001628280-24-999999",
        ),  # the last day of the filing year is in bounds
        (
            date(2026, 6, 1),
            "acc-planned",
        ),  # no filing year -> the upper bound abstains, row kept (#9)
        (date(1934, 1, 1), "acc-planned"),  # the epoch itself is in bounds
    ],
)
def test_implausible_txn_date_keeps_legitimate_dates(txn_date, accession):
    assert implausible_txn_date(txn_date, accession) is None


def _with_both_txn_dates(buy: str, sale: str) -> str:
    """The sample filing with BOTH transaction dates replaced (the buy is 2026-06-01, the sale 2026-05-15)."""
    return _XML.replace("<value>2026-06-01</value>", f"<value>{buy}</value>").replace(
        "<value>2026-05-15</value>", f"<value>{sale}</value>"
    )


def test_ingest_form4_rejects_the_impossible_row_and_keeps_the_rest(db, security_id):
    """PER-TRANSACTION rejection, not per-filing. The real LSCC filing carries one 2027-02-17 row beside
    five correctly-dated ones; dropping the whole filing to punish the typo would lose real insider history
    (#9). Only the bad row is withheld, and the table proves it."""
    n = ingest_form4(
        db, security_id, _with_both_txn_dates("2027-06-01", "2026-05-15"), "0000000000-26-000001"
    )
    db.commit()
    assert n == 1  # the sale landed; the impossible buy did not
    with db.cursor() as cur:
        cur.execute("SELECT txn_code, valid_from FROM fact_insider_txn")
        rows = cur.fetchall()
    assert [(r["txn_code"], r["valid_from"]) for r in rows] == [("S", date(2026, 5, 15))]


def test_ingest_form4_never_clamps_an_impossible_date(db, security_id):
    """NOT stored, NOT clamped. A clamp (to the filing year, to ``periodOfReport``, to anything) would
    fabricate a fact the filing never stated — #3. The table must contain no row for that transaction at
    ANY date."""
    ingest_form4(
        db, security_id, _with_both_txn_dates("2026-06-01", "0025-05-15"), "0000000000-26-000002"
    )
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn WHERE txn_code = 'S'")
        assert cur.fetchone()["n"] == 0  # no clamped/"corrected" stand-in was written


def test_ingest_form4_prints_the_rejection_itemized_never_a_tally(db, security_id, capsys):
    """FAIL LOUD. The repo's Form-4 lesson is that a rising skip COUNTER masks a real bug, so a rejection
    must name the filing, the insider, the sequence and the raw date in the run's output — enough for the
    operator to open the filing and check it — not increment a tally nobody reads."""
    ingest_form4(
        db, security_id, _with_both_txn_dates("2027-06-01", "2026-05-15"), "0001437749-24-004874"
    )
    db.commit()
    out = capsys.readouterr().out
    assert "0001437749-24-004874" in out  # which filing
    assert "2027-06-01" in out  # the raw date, verbatim
    assert "seq=0" in out  # which transaction within it
    assert "REJECT" in out and "NOT stored" in out
