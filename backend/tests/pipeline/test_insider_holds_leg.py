"""Migration 0082, through the nightly's own unit (``pipeline.ingest_thesis``): a filing with a held row is
read ONCE. A second pass re-reads no document, writes no row in either table (COUNT both) and prints nothing —
including the filing whose EVERY row is held, which stored nothing and was re-read and re-printed every night
before the hold existed.

Offline: the network legs are faked, the DB is real. The two real filings are the committed fixtures
(``form4_bbai_leading_zero_year.xml`` / ``form4_lscc_future_year.xml``); their master rows carry the filings'
own issuer CIKs, so the documents are ingested unedited. The enumeration rows (filing date, acceptance time)
are this test's inputs — an ownership document carries neither.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import (
    HOLD_YEAR_AFTER_FILING_YEAR,
    HOLD_YEAR_BEFORE_1934,
    existing_accessions,
)
from pipeline import ingest_thesis as IT

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
_SAMPLE = (_FIX / "form4_sample.xml").read_text(encoding="utf-8")

# the real filings: (ticker, the issuer CIK in the document, accession, fixture, filed, accepted)
_BBAI = (
    "BBAI",
    "0001836981",
    "0001628280-24-010030",
    "form4_bbai_leading_zero_year.xml",
    "2024-03-08",
    "2024-03-08T21:58:57.000Z",
)
_LSCC = (
    "LSCC",
    "0000855658",
    "0001437749-24-004874",
    "form4_lscc_future_year.xml",
    "2024-02-20",
    "2024-02-20T21:30:00.000Z",
)
# the fully-held filing: the committed sample (issuer CIK 0001234567) with BOTH dates changed to leading-zero
# years — no real fully-refused filing is committed as a fixture
_ALL_HELD = ("DEVCO", "0001234567", "0000000000-26-000009", None, "2026-05-01", "")
_ALL_HELD_XML = _SAMPLE.replace("<value>2026-06-01</value>", "<value>0026-06-01</value>").replace(
    "<value>2026-05-15</value>", "<value>0026-05-15</value>"
)


class _Client:
    """Stands in for ``EdgarClient``: serves each filing's document by accession and COUNTS every read."""

    reads: list[str] = []

    def __init__(self, **kwargs):
        pass

    def get_text(self, url: str, cache_key: str) -> str:
        _Client.reads.append(cache_key)
        for _t, _cik, acc, fname, *_ in (_BBAI, _LSCC):
            if acc in cache_key:
                return (_FIX / fname).read_text(encoding="utf-8")
        if _ALL_HELD[2] in cache_key:
            return _ALL_HELD_XML
        raise AssertionError(f"unexpected read {cache_key}")


def _subs(cases) -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["4" for _ in cases],
                "accessionNumber": [c[2] for c in cases],
                "primaryDocument": [f"xslF345X05/{c[2]}.xml" for c in cases],
                "filingDate": [c[4] for c in cases],
                "acceptanceDateTime": [c[5] for c in cases],
                "items": ["" for _ in cases],
            }
        }
    }


class _NoBars:
    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return []


def _patch(monkeypatch, cases) -> None:
    by_cik = {int(c[1]): c for c in cases}
    _Client.reads = []
    monkeypatch.setattr(IT, "EdgarClient", _Client)
    monkeypatch.setattr(IT, "fetch_submissions", lambda client, cik: _subs([by_cik[int(cik)]]))
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _NoBars())


def _thesis(db, cases) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    tid = uuid.uuid4()
    sids: dict[str, uuid.UUID] = {}
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "Test thesis", "n"),
        )
        for i, (ticker, cik, *_rest) in enumerate(cases):
            sid = uuid.uuid4()
            sids[ticker] = sid
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, ticker, cik, "2020-01-01"),
            )
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid, sids


def _counts(db) -> tuple[int, int]:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        facts = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM insider_txn_hold")
        held = cur.fetchone()["n"]
    db.commit()
    return facts, held


def _form4_reads() -> list[str]:
    return [k for k in _Client.reads if k.startswith("forms/") and k.endswith(".xml")]


def test_the_real_filings_are_held_once_and_a_second_pass_writes_nothing(db, monkeypatch, capsys):
    cases = (_BBAI, _LSCC)
    _patch(monkeypatch, cases)
    tid, sids = _thesis(db, cases)

    first = {r.ticker: r for r in IT.ingest_thesis(db, tid, allow_live=False)}

    assert all(r.error is None for r in first.values())
    assert (first["BBAI"].form4_appended, first["BBAI"].form4_txn_rejected) == (2, 1)
    assert (first["LSCC"].form4_appended, first["LSCC"].form4_txn_rejected) == (3, 1)
    assert _counts(db) == (5, 2)
    assert capsys.readouterr().out.count("HELD insider txn") == 2
    assert len(_form4_reads()) == 2
    # the leg threaded the enumeration's filing date and the acceptance it resolved onto each hold
    with db.cursor() as cur:
        cur.execute(
            "SELECT accession, txn_seq, form, reason, filed, accepted FROM insider_txn_hold "
            "ORDER BY accession"
        )
        holds = cur.fetchall()
    db.commit()
    assert [(h["accession"], h["txn_seq"], h["form"], h["reason"], h["filed"]) for h in holds] == [
        (_LSCC[2], 1, "4", HOLD_YEAR_AFTER_FILING_YEAR, date(2024, 2, 20)),
        (_BBAI[2], 0, "4", HOLD_YEAR_BEFORE_1934, date(2024, 3, 8)),
    ]
    assert [h["accepted"] for h in holds] == [
        datetime(2024, 2, 20, 21, 30, tzinfo=timezone.utc),
        datetime(2024, 3, 8, 21, 58, 57, tzinfo=timezone.utc),
    ]

    before = _counts(db)
    again = IT.ingest_thesis(db, tid, allow_live=False)  # the identical second pass

    assert _counts(db) == before  # NEITHER table grew
    assert all(r.form4_appended == 0 and r.form4_txn_rejected == 0 for r in again)
    assert "HELD" not in capsys.readouterr().out
    assert len(_form4_reads()) == 2  # no document was read a second time


def test_a_filing_whose_every_row_is_held_is_read_once_not_every_pass(db, monkeypatch, capsys):
    """THE NIGHTLY RE-READ this change stops. The filing stores no fact, so before the hold existed nothing
    marked it handled: every pass fetched it again and printed its rows again."""
    cases = (_ALL_HELD,)
    _patch(monkeypatch, cases)
    tid, sids = _thesis(db, cases)
    sid = sids["DEVCO"]

    (first,) = IT.ingest_thesis(db, tid, allow_live=False)

    assert first.error is None
    assert (first.form4_appended, first.form4_txn_rejected) == (0, 2)
    assert _counts(db) == (0, 2)  # nothing stored as a fact; both rows held
    assert existing_accessions(db, sid) == {_ALL_HELD[2]}  # ...and the filing is "already handled"
    db.commit()
    assert capsys.readouterr().out.count("HELD insider txn") == 2
    assert len(_form4_reads()) == 1

    (again,) = IT.ingest_thesis(db, tid, allow_live=False)

    assert (again.form4_appended, again.form4_txn_rejected, again.error) == (0, 0, None)
    assert _counts(db) == (0, 2)  # no new row
    assert "HELD" not in capsys.readouterr().out  # no print
    assert len(_form4_reads()) == 1  # not re-read
