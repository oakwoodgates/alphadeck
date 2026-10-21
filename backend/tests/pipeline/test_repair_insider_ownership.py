"""``pipeline.repair_insider_ownership`` on REAL filings stored the way the pre-0054 ingest stored them (no
ownership evidence): a bare row never merges; the dry run writes nothing; ``--apply`` appends exactly the planned
versions (COUNT THE TABLES) and the read then counts the joint report once; a re-run appends nothing; an uncached
filing stays bare and its partner cannot merge with it; ``--apply`` refuses while the call-of-record lock is held.
"""

from __future__ import annotations

import shutil
from datetime import date, datetime, timezone

import pytest

import ingest.edgar.form4a as form4a
from db.bitemporal import append_fact
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.insider_joint_reports import JOINT_REPORTERS
from ingest.edgar.form4 import parse_form4
from pipeline import repair_insider_ownership as repair
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from signals.base import PointInTimeData
from tests.joint_report_fixtures import FILINGS, FIXTURES, forms_of, ingest_amendment, make_security

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
EVIDENCE = ("direct_indirect", "shares_owned_after")
SINGER = "SINGER DAVID B"


def _bare(rows):
    return [{k: v for k, v in r.items() if k not in EVIDENCE} for r in rows]


def _store_bare_form4(db, sid, acc: str) -> int:
    """Append one filing's rows exactly as ``ingest_form4`` did before migration 0054 (no evidence columns)."""
    n = 0
    for i, t in enumerate(parse_form4((FIXTURES / f"{acc}.xml").read_text(encoding="utf-8"))):
        if t["txn_date"] is None:
            continue
        values = {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "accession": acc,
            "valid_from": t["txn_date"],
            "txn_seq": i,
            "accepted": FILINGS[acc].accepted,
            **{
                k: t[k]
                for k in (
                    "insider_name",
                    "insider_role",
                    "txn_code",
                    "shares",
                    "price",
                    "usd",
                    "aff_10b5_1",
                    "issuer_cik",
                    "issuer_name",
                    "rpt_owner_cik",
                    "security_title",
                    "issuer_foreign_symbol",
                )
            },
        }
        append_fact(db, "fact_insider_txn", values)
        n += 1
    db.commit()
    return n


@pytest.fixture
def infq_bare(db, monkeypatch):
    """INFQ's six real filings stored the pre-0054 way: Form 4 rows without the columns, 4/A records whose rows
    lack the keys (the 4/A ingest then linked + resolved them as it does)."""
    sid = make_security(db, "INFQ")
    for acc in forms_of("INFQ"):
        _store_bare_form4(db, sid, acc)
    real = form4a.parse_form4
    monkeypatch.setattr(form4a, "parse_form4", lambda x: _bare(real(x)))
    for acc in forms_of("INFQ", "4/A"):
        ingest_amendment(db, sid, acc)
    monkeypatch.setattr(form4a, "parse_form4", real)
    return sid


@pytest.fixture
def cache(tmp_path):
    """An EDGAR cache root holding the fixtures the way the app caches them: ``forms/<accession>/<doc>``."""
    for acc in FILINGS:
        d = tmp_path / "forms" / acc
        d.mkdir(parents=True)
        shutil.copy(FIXTURES / f"{acc}.xml", d / "form4.xml")
    return tmp_path


def _counts(db) -> tuple[int, int]:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        facts = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM insider_amendment")
        return facts, cur.fetchone()["n"]


def _sales(db, sid, d: date | None = None) -> list[dict]:
    rows = PointInTimeData(db, asof=date(2026, 6, 5), known_at=FAR).insider_txns(sid)
    return [r for r in rows if r["txn_code"] == "S" and (d is None or r["valid_from"] == d)]


def _usd(rows) -> float:
    return sum(float(r["usd"]) for r in rows)


def test_a_bare_row_never_merges(db, infq_bare):
    """Before the repair the double count STAYS (never a wrong collapse): Singer's sales beside Maverick's, the
    4/A pair's 2026-05-27 sales twice."""
    sales = _sales(db, infq_bare)
    assert SINGER in {r["insider_name"] for r in sales}
    assert not any(r.get(JOINT_REPORTERS) for r in sales)
    assert round(_usd(_sales(db, infq_bare, date(2026, 5, 27)))) == 95_204_806


def test_the_dry_run_plans_the_candidate_set_and_writes_nothing(db, infq_bare, cache):
    before = _counts(db)
    res = repair.run_repair(db, cache_dir=cache, apply=False, log=lambda *_: None)
    db.rollback()
    assert _counts(db) == before  # COUNT THE TABLES
    # the candidate set: rows sharing (date, code, shares, price) with ANOTHER owner's row in ANOTHER filing —
    # Maverick's 12 of 17 sales Singer re-reported + Singer's 12, the three 05-27 transfers both originals
    # report (2 x 3), and the two 4/A records; Maverick's 5 unshared sales and each original's unshared
    # transfer are NOT versioned (their evidence can never be read)
    assert res.fact_rows_versioned == 12 + 12 + 6
    assert res.amendment_records_versioned == 2
    assert res.by_code == {"S": 24, "J": 6}
    assert res.not_cached == [] and res.parse_errors == [] and res.seq_mismatch == []


def test_apply_versions_exactly_the_plan_then_the_read_counts_it_once(db, infq_bare, cache):
    facts, records = _counts(db)
    res = repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    assert _counts(db) == (
        facts + res.fact_rows_versioned,
        records + res.amendment_records_versioned,
    )
    assert (res.fact_rows_versioned, res.amendment_records_versioned) == (30, 2)
    with (
        db.cursor() as cur
    ):  # append-only: every version supersedes the row it repairs, nothing updated
        cur.execute(
            "SELECT count(*) AS n FROM fact_insider_txn WHERE supersedes IS NOT NULL "
            "AND direct_indirect IS NOT NULL AND shares_owned_after IS NOT NULL"
        )
        assert cur.fetchone()["n"] == 30
    sales = _sales(db, infq_bare)
    assert SINGER not in {r["insider_name"] for r in sales}
    assert round(_usd(_sales(db, infq_bare, date(2026, 5, 27)))) == 47_602_403
    # idempotent: a re-run appends nothing
    again = repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    assert (again.fact_rows_versioned, again.amendment_records_versioned) == (0, 0)
    assert again.candidate_rows_with_evidence == again.reader_relevant_rows
    assert _counts(db) == (facts + 30, records + 2)


def test_a_pre_repair_replay_still_reads_what_the_system_held(db, infq_bare, cache):
    """#1 — the versions are stamped NOW: a read pinned before the repair keeps the double count it held."""
    pinned = db_now(db)  # the clock that stamps recorded_at
    db.commit()
    repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    held = PointInTimeData(db, asof=date(2026, 6, 5), known_at=pinned).insider_txns(infq_bare)
    assert SINGER in {r["insider_name"] for r in held if r["txn_code"] == "S"}


def test_an_uncached_filing_stays_bare_and_cannot_merge(db, infq_bare, cache):
    shutil.rmtree(cache / "forms" / "0001012975-26-000486")
    res = repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    assert res.not_cached == ["0001012975-26-000486"]
    may21_22 = [r for r in _sales(db, infq_bare) if r["valid_from"] < date(2026, 5, 27)]
    # Singer's twelve stay bare -> their groups have ONE evidenced owner -> nothing merges: double count kept
    assert sum(1 for r in may21_22 if r["insider_name"] == SINGER) == 12
    assert not any(r.get(JOINT_REPORTERS) for r in may21_22)


def test_the_cli_apply_refuses_while_the_lock_is_held(db, infq_bare, cache):
    before = _counts(db)
    lock = acquire_pass_lock(holder="test")
    try:
        with pytest.raises(SystemExit) as exc:
            repair.main(["--apply", "--cache-dir", str(cache)])
        assert exc.value.code == EXIT_ALREADY_RUNNING
    finally:
        lock.release()
    assert _counts(db) == before


def test_the_cli_dry_run_then_apply(db, infq_bare, cache, capsys):
    before = _counts(db)
    repair.main(["--cache-dir", str(cache), "--ticker", "INFQ"])
    assert _counts(db) == before
    assert "WOULD version" in capsys.readouterr().out
    repair.main(["--apply", "--cache-dir", str(cache)])
    assert _counts(db) == (before[0] + 30, before[1] + 2)


def _cand(sec: str, acc: str, code: str, d: int, shares: float = 100.0) -> dict:
    return {
        "security_id": sec,
        "accession": acc,
        "txn_code": code,
        "valid_from": date(2026, 5, d),
        "shares": shares,
        "price": 10.0,
    }


def test_only_components_a_reader_can_reach_are_versioned():
    """WHY THE SET SUFFICES (direct inputs): readers read P/S only, so a candidate group of another code is
    versioned only when its filings connect — directly or through a chain of other filings — to a P/S candidate
    group; a component of grants alone is never read and never versioned."""
    rows = [
        # A and B share a sale; B and C share a transfer; C and D share another — one reachable component
        _cand("s1", "A", "S", 1),
        _cand("s1", "B", "S", 1),
        _cand("s1", "B", "J", 2),
        _cand("s1", "C", "J", 2),
        _cand("s1", "C", "J", 3, 7.0),
        _cand("s1", "D", "J", 3, 7.0),
        # E and F share only a grant — never read by any reader
        _cand("s1", "E", "A", 4),
        _cand("s1", "F", "A", 4),
        # the same accession label on another security is another node
        _cand("s2", "A", "A", 1),
        _cand("s2", "G", "A", 1),
    ]
    kept = repair.relevant(rows, frozenset({"P", "S"}))
    assert {(r["security_id"], r["accession"]) for r in kept} == {
        ("s1", "A"),
        ("s1", "B"),
        ("s1", "C"),
        ("s1", "D"),
    }
    assert repair.reader_codes() >= {"P", "S"}
