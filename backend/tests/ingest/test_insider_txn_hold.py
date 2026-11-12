"""Migration 0082 — a Form 4 / Form 4/A row whose transaction date cannot be true is HELD
(``insider_txn_hold``), not dropped: the stored facts are exactly what they were, the bad row is durable with
the filing's own words around it, and it is printed once.

The two filings are the REAL documents committed as fixtures (``form4_bbai_leading_zero_year.xml`` /
``form4_lscc_future_year.xml``, fetched from EDGAR 2026-09-16 — see ``test_form4.py``). What the ingest stores
for them is compared against ``form4_hold_stored_golden.json``: the ``fact_insider_txn`` rows the ingest wrote
for the same two documents at the commit BEFORE this change (ad9a83c9), dumped by running that commit's
``ingest_form4`` — so "the stored facts did not move" is checked against the old code's output, not against
this code's own parse.

A filing date is not in an ownership document (the enumeration supplies it), so the ``filed`` / ``accepted``
values below are this test's INPUTS to the ingest, not facts read from the fixtures.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import (
    HOLD_FORM_4,
    HOLD_FORM_4A,
    HOLD_YEAR_AFTER_FILING_YEAR,
    HOLD_YEAR_BEFORE_1934,
    HoldEvidence,
    existing_accessions,
    held_rows,
    hold_evidence,
    implausible_txn_date,
    implausible_txn_date_code,
    ingest_form4,
    parse_form4,
    write_hold,
)
from ingest.edgar.form4a import ingest_form4a
from tests import form4a_fixtures as fx

_FIX = Path(__file__).resolve().parent.parent / "fixtures" / "edgar"
_GOLDEN = json.loads((_FIX / "form4_hold_stored_golden.json").read_text(encoding="utf-8"))
_SAMPLE = (_FIX / "form4_sample.xml").read_text(encoding="utf-8")

# (fixture, accession, the held row's position, its reason, its date text, the filing's periodOfReport and
#  signatureDate as filed, the good rows stored, the filing date this test hands the ingest)
_BBAI = (
    "form4_bbai_leading_zero_year.xml",
    "0001628280-24-010030",
    0,
    HOLD_YEAR_BEFORE_1934,
    "0023-03-23",
    "0023-03-23",
    "2024-03-08",
    2,
    date(2024, 3, 8),
)
_LSCC = (
    "form4_lscc_future_year.xml",
    "0001437749-24-004874",
    1,
    HOLD_YEAR_AFTER_FILING_YEAR,
    "2027-02-17",
    "2024-02-17",
    "2024-02-20",
    3,
    date(2024, 2, 20),
)
_DROP = {"id", "recorded_at", "supersedes", "tenant_id", "security_id"}


def _facts(db) -> list[dict]:
    """Every stored fact row, as the golden holds them (every column but the ids and our own stamp)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM fact_insider_txn ORDER BY accession, txn_seq, valid_from, insider_name"
        )
        rows = [{k: v for k, v in r.items() if k not in _DROP} for r in cur.fetchall()]
    return json.loads(json.dumps(rows, default=str, sort_keys=True))


def _holds(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            'SELECT accession, txn_seq, form, reason, "row" AS held_row, period_of_report_as_filed, '
            "signature_date_as_filed, filed, accepted, recorded_at FROM insider_txn_hold "
            "ORDER BY accession, txn_seq, form"
        )
        return cur.fetchall()


def _counts(db) -> tuple[int, int]:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        facts = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM insider_txn_hold")
        return facts, cur.fetchone()["n"]


# --- the rule, as a code: one rule, two readings ------------------------------------------------------


@pytest.mark.parametrize(
    "txn_date, accession, code",
    [
        (date(23, 3, 23), "0001628280-24-010030", HOLD_YEAR_BEFORE_1934),
        (date(2027, 2, 17), "0001437749-24-004874", HOLD_YEAR_AFTER_FILING_YEAR),
        (date(2024, 10, 24), "0001628280-23-035012", HOLD_YEAR_AFTER_FILING_YEAR),
        (date(2024, 3, 5), "0001628280-24-010030", None),
        (date(1993, 5, 11), "0001181431-06-033946", None),
        (date(2026, 6, 1), "acc-planned", None),  # no filing year: the upper bound abstains
    ],
)
def test_the_code_and_the_words_are_one_rule(txn_date, accession, code):
    assert implausible_txn_date_code(txn_date, accession) == code
    assert (implausible_txn_date(txn_date, accession) is None) == (code is None)


def test_the_real_filings_each_hold_exactly_one_row():
    for fname, accession, seq, reason, *_ in (_BBAI, _LSCC):
        rows = parse_form4((_FIX / fname).read_text(encoding="utf-8"))
        assert held_rows(rows, accession) == {seq: reason}


def test_hold_evidence_reads_the_filings_own_text():
    """The period of report and the signature date are read as TEXT, exactly as filed — on the BBAI filing
    the period of report is itself the impossible value, which is why none of this is ever a correction.
    """
    bbai = hold_evidence((_FIX / _BBAI[0]).read_text(encoding="utf-8"))
    assert bbai == HoldEvidence(
        "0023-03-23", "2024-03-08", ("0023-03-23", "2023-11-13", "2024-03-05")
    )
    lscc = hold_evidence((_FIX / _LSCC[0]).read_text(encoding="utf-8"))
    assert (lscc.period_of_report, lscc.signature_date) == ("2024-02-17", "2024-02-20")
    assert lscc.txn_dates == ("2024-02-17", "2027-02-17", "2024-02-18", "2024-02-18")


def test_the_LAST_signature_date_is_kept_when_a_filing_has_several():
    """A real joint filing with four ``<ownerSignature>`` blocks (the 4/A fixture set). Every committed
    multi-signature filing signs all its blocks on ONE date, so the real document cannot tell "last" from
    "first"; the second half changes the last block's date value (a stated edit of the real document) to pin
    which one is kept."""
    xml = fx.xml("0001104659-26-041960")
    signed = re.findall(r"<signatureDate>([^<]+)</signatureDate>", xml)
    assert signed == ["2026-04-10"] * 4
    assert hold_evidence(xml).signature_date == "2026-04-10"
    head, _, tail = xml.rpartition("<signatureDate>2026-04-10</signatureDate>")
    edited = head + "<signatureDate>2026-04-13</signatureDate>" + tail
    assert hold_evidence(edited).signature_date == "2026-04-13"


# --- 1. the real filings: the good rows as before, the bad row held, printed once ----------------------


@pytest.mark.parametrize("case", [_BBAI, _LSCC], ids=["leading_zero_year", "future_year"])
def test_a_real_filing_stores_its_good_rows_as_before_and_holds_the_bad_one(
    db, security_id, capsys, case
):
    fname, accession, seq, reason, as_filed, period, signed, good, filed = case
    xml = (_FIX / fname).read_text(encoding="utf-8")

    n = ingest_form4(db, security_id, xml, accession, filed=filed)
    db.commit()

    assert n == (good, 1)
    # the stored facts are what the commit before this change stored (COUNT + every value)
    assert _counts(db) == (good, 1)
    assert _facts(db) == _GOLDEN[accession]
    # the bad row is held, with the rule's code and the filing's own words
    (h,) = _holds(db)
    assert (h["accession"], h["txn_seq"], h["form"], h["reason"]) == (
        accession,
        seq,
        HOLD_FORM_4,
        reason,
    )
    assert h["period_of_report_as_filed"] == period
    assert h["signature_date_as_filed"] == signed
    assert (h["filed"], h["accepted"]) == (filed, None)
    row = dict(h["held_row"])
    assert row.pop("txn_date_as_filed") == as_filed
    parsed = parse_form4(xml)[seq]
    assert row == {**parsed, "txn_date": parsed["txn_date"].isoformat()}  # the row as parsed
    assert row["txn_date"] == as_filed  # held verbatim: no corrected date
    # printed once, itemized
    out = capsys.readouterr().out
    assert out.count("HELD insider txn") == 1
    assert accession in out and f"seq={seq}" in out and as_filed in out
    assert "not stored as a fact" in out and "no corrected date is invented" in out


def test_a_second_read_of_the_same_filing_holds_nothing_new_and_says_nothing(
    db, security_id, capsys
):
    """The hold's own idempotency, COUNTED AT THE TABLE: the writer is called twice on purpose (the leg's
    accession skip is tested in ``tests/pipeline/test_insider_holds_leg.py``)."""
    xml = (_FIX / _BBAI[0]).read_text(encoding="utf-8")
    first = ingest_form4(db, security_id, xml, _BBAI[1])
    db.commit()
    capsys.readouterr()
    held_before = _counts(db)[1]

    again = ingest_form4(db, security_id, xml, _BBAI[1])
    db.commit()

    assert (first.rejected, again.rejected) == (1, 0)  # counted when it was first held, never again
    assert _counts(db)[1] == held_before == 1
    assert "HELD" not in capsys.readouterr().out


def test_the_acceptance_and_the_callers_stamp_ride_the_hold(db, security_id):
    accepted = datetime(2024, 2, 20, 21, 30, tzinfo=timezone.utc)
    stamp = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    xml = (_FIX / _LSCC[0]).read_text(encoding="utf-8")
    ingest_form4(db, security_id, xml, _LSCC[1], accepted=accepted, recorded_at=stamp)
    db.commit()
    (h,) = _holds(db)
    assert (h["accepted"], h["recorded_at"], h["filed"]) == (accepted, stamp, None)
    with db.cursor() as cur:  # the facts carry the same acceptance, as before
        cur.execute("SELECT DISTINCT accepted FROM fact_insider_txn")
        assert [r["accepted"] for r in cur.fetchall()] == [accepted]


def test_the_live_path_stamps_the_hold_now(db, security_id):
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        before = cur.fetchone()["t"]
    db.commit()  # so the ingest's transaction (whose now() stamps the row) begins strictly later
    ingest_form4(db, security_id, (_FIX / _BBAI[0]).read_text(encoding="utf-8"), _BBAI[1])
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        after = cur.fetchone()["t"]
    (h,) = _holds(db)
    assert before <= h["recorded_at"] <= after


# --- 3 (the table half). a filing whose every row is held is "already handled" -------------------------


def _all_dates_impossible() -> str:
    """The committed sample filing with BOTH transaction dates replaced by leading-zero years (the BBAI
    shape). No real fully-refused filing is committed as a fixture, so this is the sample with its two date
    values changed — the same edit ``test_form4.py`` makes."""
    return _SAMPLE.replace("<value>2026-06-01</value>", "<value>0026-06-01</value>").replace(
        "<value>2026-05-15</value>", "<value>0026-05-15</value>"
    )


def test_a_fully_held_filing_is_listed_by_existing_accessions(db, security_id):
    acc = "0000000000-26-000009"
    assert existing_accessions(db, security_id) == set()
    n = ingest_form4(db, security_id, _all_dates_impossible(), acc)
    db.commit()
    assert n == (0, 2)
    assert _counts(db) == (0, 2)  # no fact at all, two holds
    assert existing_accessions(db, security_id) == {acc}  # read once, then skipped


def test_a_stored_filing_is_still_listed_and_a_4A_hold_is_not(db, security_id):
    ingest_form4(db, security_id, _SAMPLE, "acc-clean")
    write_hold(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=security_id,
        accession="0000000000-26-000010",
        txn_seq=0,
        form=HOLD_FORM_4A,
        reason=HOLD_YEAR_BEFORE_1934,
        row={"txn_date": date(26, 1, 1)},
        evidence=HoldEvidence(None, None, ()),
        filed=None,
        accepted=None,
    )
    db.commit()
    # the 4/A leg has its own skip (the amendment record), so its hold is not a Form 4 "already handled"
    assert existing_accessions(db, security_id) == {"acc-clean"}


# --- the table: append-only, and only the rule's reasons ------------------------------------------------


def test_the_hold_table_is_append_only_and_checks_its_reason_and_form(db, security_id):
    ingest_form4(db, security_id, (_FIX / _BBAI[0]).read_text(encoding="utf-8"), _BBAI[1])
    db.commit()
    with pytest.raises(psycopg.Error):
        with db.cursor() as cur:
            cur.execute("UPDATE insider_txn_hold SET reason = 'year_after_filing_year'")
    db.rollback()

    def _insert(reason: str, form: str, seq: int) -> None:
        with db.cursor() as cur:
            cur.execute(
                'INSERT INTO insider_txn_hold (tenant_id, security_id, accession, txn_seq, form, reason, "row") '
                "VALUES (%s, %s, 'a', %s, %s, %s, '{}'::jsonb)",
                (DEFAULT_TENANT_ID, security_id, seq, form, reason),
            )

    for reason, form in (("made_up", "4"), ("year_before_1934", "5")):
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(reason, form, 0)
        db.rollback()
    _insert("after_filing", "4", 1)  # reserved for the next step: the CHECK already allows it
    db.commit()
    assert _counts(db)[1] == 2


# --- 4. Form 4/A: a refused row is held with form '4/A'; the record and the link are what they were -----

_RPAY_4A = "0001104659-26-041960"


def _rpay_4a_with_one_impossible_date() -> str:
    """The REAL clerical 4/A (three Table I rows) with ONE date value changed to a leading-zero year — no real
    4/A with an impossible date is committed as a fixture."""
    xml = fx.xml(_RPAY_4A)
    assert xml.count("<value>2026-04-08</value>") == 1
    return xml.replace("<value>2026-04-08</value>", "<value>0026-04-08</value>")


def test_a_4A_row_failing_the_rule_is_held_and_the_record_and_link_stay_as_they_were(db, capsys):
    f = fx.FILINGS[_RPAY_4A]
    sid = fx.make_security(db, f.key)
    fx.ingest_originals(db, sid, f.key)
    facts_before = _counts(db)[0]
    capsys.readouterr()

    out = ingest_form4a(
        db,
        sid,
        _rpay_4a_with_one_impossible_date(),
        _RPAY_4A,
        member_cik=f.cik,
        filed_by_accession=fx.filed_by_accession(f.key),
        accepted_by_accession=fx.accepted_by_accession(f.key),
        filed=f.filed,
        accepted=f.accepted,
    )
    db.commit()

    # the hold: form '4/A', the row's own position, the filing date and acceptance the leg resolved
    (h,) = _holds(db)
    assert (h["accession"], h["txn_seq"], h["form"], h["reason"]) == (
        _RPAY_4A,
        1,
        HOLD_FORM_4A,
        HOLD_YEAR_BEFORE_1934,
    )
    assert h["held_row"]["txn_date_as_filed"] == "0026-04-08"
    assert (h["filed"], h["accepted"]) == (f.filed, f.accepted)
    printed = capsys.readouterr().out
    assert printed.count("HELD insider 4/A txn") == 1 and _RPAY_4A in printed
    # the amendment path is what it was: the record keeps only its good rows, the count rides the link
    # detail, nothing is written into fact_insider_txn, and the 4/A is not a Form 4 "already handled".
    # (MEASURED against the commit before this change on this same input: the record, the link and the
    # system's decision are identical row for row.)
    assert (out.rejected, out.rows) == (1, 2)
    with db.cursor() as cur:
        cur.execute("SELECT rows FROM insider_amendment WHERE accession = %s", (_RPAY_4A,))
        (rec,) = cur.fetchall()
        cur.execute(
            "SELECT detail FROM insider_amendment_link WHERE amendment_accession = %s", (_RPAY_4A,)
        )
        (link,) = cur.fetchall()
    assert [r["txn_seq"] for r in rec["rows"]] == [0, 2]
    assert all(
        "txn_date_as_filed" not in r for r in rec["rows"]
    )  # nothing new rides the stored rows
    assert link["detail"]["rejected_rows"] == 1
    assert _counts(db)[0] == facts_before
    assert _RPAY_4A not in existing_accessions(db, sid)


def test_a_clean_4A_holds_nothing(db):
    f = fx.FILINGS[_RPAY_4A]
    sid = fx.seed(db, f.key)  # the real originals + the real, unedited 4/A
    assert _counts(db)[1] == 0
    assert _RPAY_4A not in existing_accessions(db, sid)
