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
    assert n == (2, 0)  # both rows stored, nothing rejected
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
    three correctly-dated NON-DERIVATIVE ones (six transactions in the document; ``parse_form4`` stores the
    non-derivative ones only) — dropping the whole filing to punish the typo would lose real insider history
    (#9). Only the bad row is withheld, and the table proves it. The real filing is committed as a fixture
    and exercised directly in ``test_the_real_lscc_filing_*`` below; this keeps the minimal synthetic case.
    """
    n = ingest_form4(
        db, security_id, _with_both_txn_dates("2027-06-01", "2026-05-15"), "0000000000-26-000001"
    )
    db.commit()
    assert n == (1, 1)  # the sale landed; the impossible buy did not, and it is TALLIED
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


# --- THE REAL SEC DOCUMENTS: parser-vs-filer, settled in the repo rather than in prose ------------------
#
# The central factual claim of this change is that the impossible dates are FILER GARBAGE and that our
# parser reproduces them faithfully — so there is nothing to re-derive and no corrected date may be
# invented (#3). Until now that claim lived only in a commit message. These two accessions were fetched
# live from EDGAR on 2026-09-16 with the declared User-Agent and are committed VERBATIM (unedited external
# content — the American-English sweep must never touch them):
#
#   form4_bbai_leading_zero_year.xml  0001628280-24-010030 (BBAI, CIK 1836981, wk-form4_1709935137.xml,
#                                    6,449 b) — <transactionDate><value>0023-03-23</value>, and its
#                                    periodOfReport is ALSO 0023-03-23, which is why periodOfReport is no
#                                    substitute. Two correctly-dated rows sit beside it.
#   form4_lscc_future_year.xml       0001437749-24-004874 (LSCC, CIK 855658, rdgdoc.xml, 12,100 b) —
#                                    <transactionDate><value>2027-02-17</value> on an accession filed
#                                    2024-02-20. SIX transactions in the document: four nonDerivative
#                                    (one bad) + two derivative. parse_form4 stores the non-derivative
#                                    ones, so THREE correctly-dated rows survive the rejection — not the
#                                    five an earlier draft of this change claimed by counting all six.
_BBAI_REAL = (_FIX / "form4_bbai_leading_zero_year.xml").read_text(encoding="utf-8")
_LSCC_REAL = (_FIX / "form4_lscc_future_year.xml").read_text(encoding="utf-8")


def test_the_real_sec_xml_really_says_the_impossible_date():
    """THE root-cause test. If our parser were corrupting these dates, the fix would be a parser fix and
    rejecting the rows would be destroying real data. It is not: the SEC document itself says
    ``0023-03-23`` and ``2027-02-17``, and ``parse_form4`` reproduces each verbatim."""
    bbai = parse_form4(_BBAI_REAL)
    assert [t["txn_date"] for t in bbai] == [date(23, 3, 23), date(2023, 11, 13), date(2024, 3, 5)]
    assert "<value>0023-03-23</value>" in _BBAI_REAL  # the raw text, not just our parse of it

    lscc = parse_form4(_LSCC_REAL)
    assert [t["txn_date"] for t in lscc] == [
        date(2024, 2, 17),
        date(2027, 2, 17),
        date(2024, 2, 18),
        date(2024, 2, 18),
    ]
    assert "<value>2027-02-17</value>" in _LSCC_REAL


def test_period_of_report_is_not_a_usable_fallback_on_the_real_filing():
    """The obvious "fix" — substitute ``periodOfReport`` for the impossible transactionDate — is not
    available: on the BBAI filing periodOfReport is ITSELF ``0023-03-23``. (It is also the filing's
    EARLIEST reportable transaction date, not this row's, so it would be the wrong value even when
    well-formed.) There is no honest date to write, which is why the row is rejected rather than repaired.
    """
    assert "<periodOfReport>0023-03-23</periodOfReport>" in _BBAI_REAL


def test_the_real_bbai_filing_loses_one_row_and_keeps_two(db, security_id, capsys):
    """End-to-end on the real document: one rejection, two survivors, COUNT THE TABLE."""
    n = ingest_form4(db, security_id, _BBAI_REAL, "0001628280-24-010030")
    db.commit()
    assert n == (2, 1)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        assert cur.fetchone()["n"] == 2
        cur.execute("SELECT valid_from FROM fact_insider_txn ORDER BY valid_from")
        assert [r["valid_from"] for r in cur.fetchall()] == [date(2023, 11, 13), date(2024, 3, 5)]
    out = capsys.readouterr().out
    assert "0023-03-23" in out and "Peffer Julie" in out  # itemized, not tallied


def test_the_real_lscc_filing_loses_one_row_and_keeps_THREE(db, security_id, capsys):
    """The count an earlier draft got wrong, now checkable: the document holds six transactions, but
    ``parse_form4`` stores the four non-derivative ones, so rejecting the ``2027-02-17`` row leaves
    THREE — not five. Rejection is per-TRANSACTION; the good rows in a filing are never punished for a
    filer's typo (#9)."""
    n = ingest_form4(db, security_id, _LSCC_REAL, "0001437749-24-004874")
    db.commit()
    assert n == (3, 1)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        assert cur.fetchone()["n"] == 3
        cur.execute("SELECT valid_from FROM fact_insider_txn ORDER BY valid_from, txn_seq")
        assert [r["valid_from"] for r in cur.fetchall()] == [
            date(2024, 2, 17),
            date(2024, 2, 18),
            date(2024, 2, 18),
        ]
    assert "2027-02-17" in capsys.readouterr().out


def test_the_live_path_leaves_recorded_at_at_now_even_when_a_row_is_REJECTED(db, security_id):
    """THE LIVE-PATH REGRESSION (invariant #1). The ingest must never backdate ``recorded_at`` — a fact
    ingested today has to be invisible to an as-of read pinned earlier — and a rejection must not disturb
    that for the rows that DO store. Asserted on the real filing, where one row is rejected mid-loop.

    ONE CLOCK, NOT TWO. Both bounds come from the clock that STAMPS the column — Postgres — never from
    ``datetime.now()`` on the host. The first draft of this test bracketed the ingest with the host clock
    and failed 27 times in 100 consecutive runs on a dev box. The cause was MEASURED, not guessed, and it
    is arithmetic rather than scheduling jitter — instrumenting 100 replays of this exact fixture shape:

      * ``recorded_at`` equals Postgres ``now()`` to the microsecond in every one of the 100 replays — the
        column default is ``now()`` (migration 0001) — and ``now()`` is the TRANSACTION-START instant, not
        the statement instant. So the stamp lands at the EARLIEST moment of the ingest, a median 0.4-1.5 ms
        after a host ``before`` captured immediately before it.
      * the host and the container's Postgres are two different clocks, offset by a measured median
        -124 microseconds (Postgres behind the host).

    A sub-millisecond margin cannot survive a sub-millisecond offset, so the lower bound went legitimately
    negative (to -0.76 ms) in 10 of those 100 replays — the ingest path was never at fault. The upper bound
    never bit: the commit puts ~5 ms between the stamp and ``after``. The shape below then ran 100/100
    clean where the host-clock shape ran 73/100.
    ``tests/ingest/test_price_reversion.py::test_replay_before_the_reversion_still_sees_the_old_basis``
    already pins its own bitemporal instant this way for the same reason; this mirrors it. Note the
    ``commit()`` after reading the lower bound: it is load-bearing, because the ingest's transaction —
    whose ``now()`` stamps ``recorded_at`` — must BEGIN strictly after the instant we read.

    The bracket is not weakened by the move. It still pins ``recorded_at`` inside a few-millisecond window
    around the ingest, so backdating by a microsecond still fails it. The second assertion then names the
    failure mode a real backdating bug would actually produce — a stamp sitting on the FILING's own dates
    instead of today's — which no clock can explain away.
    """
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        before = cur.fetchone()["t"]
    db.commit()  # so the ingest's transaction — whose now() stamps recorded_at — begins strictly later

    ingest_form4(db, security_id, _LSCC_REAL, "0001437749-24-004874")
    db.commit()

    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t, current_date AS today")
        row = cur.fetchone()
        after, today = row["t"], row["today"]
        cur.execute("SELECT recorded_at, valid_from FROM fact_insider_txn")
        rows = cur.fetchall()

    assert len(rows) == 3
    assert all(
        before <= r["recorded_at"] <= after for r in rows
    ), "recorded_at must be the DB's now(), never backdated"
    # the shape a backdating bug leaves behind: the stamp follows the FILING instead of the ingest.
    # These rows are dated 2024-02-17/18, so "today, and strictly later than the event" is the whole
    # no-lookahead claim — an as-of read pinned before today cannot see them.
    assert all(r["recorded_at"].date() >= today for r in rows), "recorded_at must be TODAY's ingest"
    assert all(
        r["recorded_at"].date() > r["valid_from"] for r in rows
    ), "recorded_at must never collapse onto the transaction's own date"


def test_a_clean_filing_rejects_nothing_and_prints_nothing(db, security_id, capsys):
    """Honest loudness on the other side: the ordinary case is SILENT and the tally is 0, so a nonzero
    count in the cron summary genuinely marks the exception rather than being background noise."""
    n = ingest_form4(db, security_id, _XML, "acc-clean")
    db.commit()
    assert n.rejected == 0
    assert "REJECT" not in capsys.readouterr().out
