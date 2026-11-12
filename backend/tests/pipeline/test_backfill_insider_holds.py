"""``pipeline.backfill_insider_holds`` — hold the rows an earlier ingest dropped.

The state it repairs is built the honest way: the REAL filings (the committed fixtures) are ingested, then the
hold rows the new ingest wrote are deleted — leaving exactly what the ingest before migration 0082 left (the
good rows stored, the refused row nowhere; ``tests/ingest/test_insider_txn_hold.py`` pins that those stored
rows equal the old ingest's). The tool then reads the same documents from a cache directory on disk. The
CLI's real ``main`` runs, with its real locks and connections, on the test DB.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import existing_accessions, ingest_form4
from pipeline import backfill_insider_holds as B
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
_SAMPLE = (_FIX / "form4_sample.xml").read_text(encoding="utf-8")
# (ticker, the issuer CIK in the document, accession, fixture)
_BBAI = ("BBAI", "0001836981", "0001628280-24-010030", "form4_bbai_leading_zero_year.xml")
_LSCC = ("LSCC", "0000855658", "0001437749-24-004874", "form4_lscc_future_year.xml")
_ACCEPTED = datetime(2024, 3, 8, 21, 58, 57, tzinfo=timezone.utc)  # this test's input, see _store
# the committed sample (issuer CIK 0001234567) with BOTH dates changed to leading-zero years: a filing whose
# every row the rule refuses (no real one is committed as a fixture)
_ALL_HELD_ACC = "0000000000-26-000009"
_ALL_HELD_XML = _SAMPLE.replace("<value>2026-06-01</value>", "<value>0026-06-01</value>").replace(
    "<value>2026-05-15</value>", "<value>0026-05-15</value>"
)


def _master(db, ticker: str, cik: str | None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2020-01-01"),
        )
    db.commit()
    return sid


def _cache(tmp_path: Path, *docs: tuple[str, str]) -> Path:
    """An EDGAR cache root holding ``forms/<accession>/doc.xml`` for each ``(accession, xml)``."""
    root = tmp_path / "edgar_cache"
    (root / "forms").mkdir(parents=True)
    for accession, xml in docs:
        d = root / "forms" / accession
        d.mkdir()
        (d / "doc.xml").write_text(xml, encoding="utf-8")
    return root


def _xml(case) -> str:
    return (_FIX / case[3]).read_text(encoding="utf-8")


def _holds(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            'SELECT security_id, accession, txn_seq, form, reason, "row" AS held_row, '
            "period_of_report_as_filed, signature_date_as_filed, filed, accepted "
            "FROM insider_txn_hold ORDER BY accession, txn_seq"
        )
        out = cur.fetchall()
    db.commit()
    return out


def _count(db, table: str = "insider_txn_hold") -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — a fixed test table name
        n = cur.fetchone()["n"]
    db.commit()
    return n


def _journal(db) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute("SELECT tool, action, rows_changed, deletes FROM data_repair ORDER BY seq")
        out = [(r["tool"], r["action"], r["rows_changed"], r["deletes"]) for r in cur.fetchall()]
    db.commit()
    return out


def _store(db, case, *, cik: str | None = None, accepted=_ACCEPTED) -> tuple[uuid.UUID, list[dict]]:
    """Ingest one real filing, then put the table back to what the pre-0082 ingest left: its good rows
    stored, its refused row nowhere. Returns the security and the hold rows the ingest itself wrote (what the
    backfill must reproduce)."""
    sid = _master(db, case[0], cik if cik is not None else case[1])
    ingest_form4(db, sid, _xml(case), case[2], accepted=accepted)
    db.commit()
    written = [h for h in _holds(db) if h["accession"] == case[2]]
    with db.cursor() as cur:
        cur.execute("DELETE FROM insider_txn_hold WHERE accession = %s", (case[2],))
    db.commit()
    return sid, written


@pytest.fixture
def two_filings(db, tmp_path):
    """Both real filings stored the pre-0082 way, and their documents in a cache directory."""
    _, bbai = _store(db, _BBAI)
    _, lscc = _store(db, _LSCC)
    assert _count(db) == 0 and _count(db, "fact_insider_txn") == 5
    return _cache(tmp_path, (_BBAI[2], _xml(_BBAI)), (_LSCC[2], _xml(_LSCC))), bbai + lscc


# --- 6. dry-run / apply / idempotent ------------------------------------------------------------------


def test_the_dry_run_writes_nothing_and_prints_every_planned_hold(db, two_filings, capsys):
    cache, _ = two_filings
    capsys.readouterr()

    B.main(["--cache-dir", str(cache)])

    assert _count(db) == 0 and _journal(db) == []  # nothing written, nothing journaled
    out = capsys.readouterr().out
    assert "DRY-RUN (nothing written)" in out
    # one line per planned hold: ticker, accession, row, person, code, shares, price, the date as filed, the
    # filing's period of report and signature date, the reason
    (bbai,) = [ln for ln in out.splitlines() if _BBAI[2] in ln and " row " in ln]
    for part in (
        "BBAI",
        "row 0",
        "Peffer Julie",
        "(A)",
        "shares 87827",
        "price 0",
        "date as filed 0023-03-23",
        "period of report 0023-03-23",
        "signed 2024-03-08",
        "year_before_1934",
    ):
        assert part in bbai, part
    (lscc,) = [ln for ln in out.splitlines() if _LSCC[2] in ln and " row " in ln]
    for part in (
        "LSCC",
        "row 1",
        "date as filed 2027-02-17",
        "period of report 2024-02-17",
        "signed 2024-02-20",
        "year_after_filing_year",
    ):
        assert part in lscc, part
    assert "2 row(s) to hold in 2 filing(s)" in out
    assert "  year_before_1934: 1" in out and "  year_after_filing_year: 1" in out  # by reason


def test_apply_writes_the_planned_holds_and_a_second_apply_writes_none(db, two_filings, capsys):
    cache, by_ingest = two_filings

    B.main(["--cache-dir", str(cache), "--apply"])

    # exactly the rows the ingest itself would have held (COUNT the table, then every value)
    assert _count(db) == 2
    assert _holds(db) == sorted(by_ingest, key=lambda h: (h["accession"], h["txn_seq"]))
    assert _count(db, "fact_insider_txn") == 5  # no fact moved
    assert _journal(db) == [("backfill_insider_holds", "apply", 2, False)]
    assert "2 row(s) held in 2 filing(s)" in capsys.readouterr().out

    B.main(["--cache-dir", str(cache), "--apply"])  # idempotent

    assert _count(db) == 2
    out = capsys.readouterr().out
    assert "0 row(s) held in 0 filing(s) · 2 already held" in out
    assert _journal(db)[1] == ("backfill_insider_holds", "apply", 0, False)


def test_the_filing_date_comes_from_the_filing_index_when_it_holds_one(db, tmp_path):
    """``filed`` is not in an ownership document; the backfill takes it from the stored filing index (the
    date below is this test's input to that table) and never guesses it otherwise."""
    sid, _ = _store(db, _LSCC)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_filing_index (tenant_id, security_id, form, accession, filed, source_ref, "
            "valid_from) VALUES (%s, %s, '4', %s, %s, 'test', %s)",
            (DEFAULT_TENANT_ID, sid, _LSCC[2], date(2024, 2, 20), date(2024, 2, 20)),
        )
    db.commit()
    B.main(["--cache-dir", str(_cache(tmp_path, (_LSCC[2], _xml(_LSCC)))), "--apply"])
    (h,) = _holds(db)
    assert (h["filed"], h["accepted"]) == (date(2024, 2, 20), _ACCEPTED)


def test_a_filing_with_no_cached_document_is_listed_never_guessed(db, tmp_path, capsys):
    _store(db, _BBAI)
    _store(db, _LSCC)
    cache = _cache(tmp_path, (_LSCC[2], _xml(_LSCC)))  # the BBAI document is NOT in the cache

    B.main(["--cache-dir", str(cache), "--apply"])

    out = capsys.readouterr().out
    assert f"not cached (nothing planned, never guessed): 1 — {_BBAI[2]}" in out
    assert [h["accession"] for h in _holds(db)] == [_LSCC[2]]  # only what it could read


def test_a_count_mismatch_rolls_back_and_exits_non_zero(db, two_filings, monkeypatch, capsys):
    """The write is verified at the table: if the rows the writes report are not the rows the table gained,
    everything rolls back. Forced here by a writer that reports a write it did not make."""
    cache, _ = two_filings
    real = B.write_hold
    calls = {"n": 0}

    def _lying(conn, **kw):
        calls["n"] += 1
        return True if calls["n"] == 1 else real(conn, **kw)  # first row: claimed, never inserted

    monkeypatch.setattr(B, "write_hold", _lying)
    with pytest.raises(SystemExit) as exc:
        B.main(["--cache-dir", str(cache), "--apply"])

    assert exc.value.code == 1
    assert "ROLLED BACK" in capsys.readouterr().out
    assert _count(db) == 0  # the row that WAS inserted went with the rollback
    assert _journal(db) == []


def test_apply_refuses_while_a_pass_holds_the_lock(db, two_filings):
    cache, _ = two_filings
    lock = acquire_pass_lock(holder="test")
    try:
        with pytest.raises(SystemExit) as exc:
            B.main(["--cache-dir", str(cache), "--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING
    finally:
        lock.release()
    assert _count(db) == 0
    B.main(["--cache-dir", str(cache)])  # the dry run takes no lock


# --- the ingest's own reading: the other-issuer screen -------------------------------------------------


def test_a_stored_filing_about_another_issuer_is_not_held(db, tmp_path, capsys):
    """A row stored before the other-issuer screen existed sits on a company that is not the filing's issuer.
    The ingest stores nothing for such a filing today, so the backfill holds nothing for it either.
    """
    _store(db, _BBAI, cik="0000000042")  # the master row is NOT the filing's issuer
    B.main(["--cache-dir", str(_cache(tmp_path, (_BBAI[2], _xml(_BBAI)))), "--apply"])
    assert _count(db) == 0
    assert f"about another issuer (the ingest stores nothing for these): 1 — {_BBAI[2]}" in (
        capsys.readouterr().out
    )


# --- --accessions: named filings, including ones with no stored row ------------------------------------


def test_accessions_limits_the_run_to_the_named_filings(db, two_filings):
    cache, _ = two_filings
    B.main(["--cache-dir", str(cache), "--accessions", _LSCC[2], "--apply"])
    assert [h["accession"] for h in _holds(db)] == [_LSCC[2]]


def test_an_unstored_fully_refused_filing_is_placed_by_its_issuer_cik(db, tmp_path, capsys):
    sid = _master(db, "DEVCO", "0001234567")  # the sample's issuer
    cache = _cache(tmp_path, (_ALL_HELD_ACC, _ALL_HELD_XML))
    assert existing_accessions(db, sid) == set()
    db.commit()

    B.main(["--cache-dir", str(cache), "--accessions", _ALL_HELD_ACC, "--apply"])

    holds = _holds(db)
    assert [(h["security_id"], h["accession"], h["txn_seq"]) for h in holds] == [
        (sid, _ALL_HELD_ACC, 0),
        (sid, _ALL_HELD_ACC, 1),
    ]
    assert all(
        h["accepted"] is None and h["filed"] is None for h in holds
    )  # none stored: never guessed
    assert existing_accessions(db, sid) == {_ALL_HELD_ACC}  # the nightly will not re-read it
    db.commit()
    assert "1 with no stored row" in capsys.readouterr().out


def test_an_unstored_filing_it_cannot_place_is_listed_and_skipped(db, tmp_path, capsys):
    cache = _cache(
        tmp_path, (_ALL_HELD_ACC, _ALL_HELD_XML)
    )  # no master row carries its issuer's CIK
    B.main(["--cache-dir", str(cache), "--accessions", _ALL_HELD_ACC, "--apply"])
    assert _count(db) == 0
    assert f"could not be placed on a company (skipped; try --ticker): 1 — {_ALL_HELD_ACC}" in (
        capsys.readouterr().out
    )


def test_ticker_places_an_unstored_filing(db, tmp_path):
    sid = _master(db, "NOCIK", None)  # a master row with no CIK: the issuer match cannot find it
    cache = _cache(tmp_path, (_ALL_HELD_ACC, _ALL_HELD_XML))
    B.main(
        ["--cache-dir", str(cache), "--accessions", _ALL_HELD_ACC, "--ticker", "nocik", "--apply"]
    )
    assert {h["security_id"] for h in _holds(db)} == {sid} and _count(db) == 2


def test_an_unstored_filing_with_rows_the_ingest_would_store_is_left_to_the_ingest(
    db, tmp_path, capsys
):
    """RECALL (#9). Holding the bad row of a filing whose good rows are NOT stored would mark the filing
    "already handled", and the nightly would never store those good rows. The tool leaves it alone; the
    nightly reads it and holds the bad row in the same transaction as the good ones."""
    sid = _master(db, _BBAI[0], _BBAI[1])  # the company is known, the filing was never ingested
    cache = _cache(tmp_path, (_BBAI[2], _xml(_BBAI)))

    B.main(["--cache-dir", str(cache), "--accessions", _BBAI[2], "--apply"])

    assert _count(db) == 0
    assert existing_accessions(db, sid) == set()  # still unread: the ingest will take it
    db.commit()
    assert f"left to the ingest, which holds the bad row itself): 1 — {_BBAI[2]}" in (
        capsys.readouterr().out
    )


def test_the_wrong_cache_dir_is_refused(db, tmp_path):
    with pytest.raises(SystemExit):
        B.main(["--cache-dir", str(tmp_path / "nope")])


# --- rule 2026-11-insider-rows-dated-after-filing: STORED rows dated after their own filing ---------------


def _store_the_old_way(db, monkeypatch, group: str) -> uuid.UUID:
    """Ingest a real filing the way the ingest did BEFORE the after-filing condition (the year conditions only,
    the acceptance stored on the row): the after-filing row lands in ``fact_insider_txn`` as a fact.
    """
    from ingest.edgar import form4 as F4
    from tests import held_fixtures as H

    old = F4.held_rows
    monkeypatch.setattr(F4, "filing_held_rows", lambda rows, acc, xml, accepted: old(rows, acc))
    sid = H.make_security(db, group)
    for acc in H.of_group(group, "4"):
        H.ingest(db, sid, acc)
    monkeypatch.undo()
    return sid


def _stamps(db, sid) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_change WHERE security_id = %s", (sid,))
        n = cur.fetchone()["n"]
    db.commit()
    return n


def test_a_stored_row_dated_after_its_filing_is_held_beside_the_fact_and_stamped(
    db, tmp_path, monkeypatch, capsys
):
    from domain.market_time import known_at_for_asof
    from signals.base import PointInTimeData
    from tests import held_fixtures as H

    late_acc = "0001179110-10-012412"  # one buy dated two months after its own filing
    tz_acc = (
        "0001019687-14-002802"  # dated after the acceptance's market date, not after its signature
    )
    sid = _store_the_old_way(db, monkeypatch, "on_time_b")
    tz_sid = _store_the_old_way(db, monkeypatch, "signed_ahead")
    assert _count(db) == 0 and _count(db, "fact_insider_txn") == 2
    asof = date(2010, 10, 11)

    def read():
        return [
            (r["valid_from"], r.get("date_basis"))
            for r in PointInTimeData(
                db,
                asof=asof,
                known_at=known_at_for_asof(asof, H.FAR),
                clock="public",
                workspace=DEFAULT_TENANT_ID,
            ).insider_txns(sid)
        ]

    assert read() == [(date(2010, 10, 9), None)]  # the old reading: the written date
    cache = _cache(tmp_path, (late_acc, H.xml(late_acc)), (tz_acc, H.xml(tz_acc)))
    stamps = _stamps(db, sid)
    capsys.readouterr()

    B.main(["--cache-dir", str(cache)])  # dry-run

    out = capsys.readouterr().out
    assert _count(db) == 0 and _journal(db) == [] and _stamps(db, sid) == stamps
    assert "1 row(s) to hold in 1 filing(s)" in out and "  after_filing: 1" in out
    assert tz_acc not in out.split("SUMMARY")[0]  # the time-zone filing is not planned

    B.main(["--cache-dir", str(cache), "--apply"])

    (held,) = _holds(db)
    assert (held["security_id"], held["accession"], held["txn_seq"], held["reason"]) == (
        sid,
        late_acc,
        0,
        "after_filing",
    )
    assert held["accepted"] == H.FILINGS[late_acc].accepted
    assert _count(db, "fact_insider_txn") == 2  # no fact deleted or changed
    assert (
        _stamps(db, sid) == stamps + 1 and _stamps(db, tz_sid) == 0
    )  # only the company written for
    assert read() == [(date(2010, 8, 10), "filing_night")]  # the read now counts the held version

    B.main(["--cache-dir", str(cache), "--apply"])  # idempotent

    assert _count(db) == 1 and _stamps(db, sid) == stamps + 1
    assert _journal(db)[-1] == ("backfill_insider_holds", "apply", 0, False)


# --- the rule's third condition (pure) ----------------------------------------------------------------


def test_the_after_filing_condition_needs_an_acceptance_and_honors_the_signature_date():
    from ingest.edgar.form4 import (
        HOLD_AFTER_FILING,
        filing_held_rows,
        implausible_txn_date_code,
        parse_form4,
    )
    from tests import held_fixtures as H

    acc = "0001179110-10-012412"
    accepted = H.FILINGS[acc].accepted
    d = date(2010, 10, 9)
    assert implausible_txn_date_code(d, acc) is None  # no acceptance: the two year conditions only
    assert implausible_txn_date_code(d, acc, accepted=accepted) == HOLD_AFTER_FILING
    assert (
        implausible_txn_date_code(d, acc, accepted=accepted, signature_date=date(2010, 10, 9))
        is None
    )
    assert implausible_txn_date_code(date(2010, 8, 10), acc, accepted=accepted) is None
    tz = "0001019687-14-002802"
    rows = parse_form4(H.xml(tz))
    assert filing_held_rows(rows, tz, H.xml(tz), H.FILINGS[tz].accepted) == {}
    assert filing_held_rows(rows, tz, H.xml(tz), None) == {}
