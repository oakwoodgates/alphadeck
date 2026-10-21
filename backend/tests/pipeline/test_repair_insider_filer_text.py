"""``pipeline.repair_insider_filer_text`` on REAL filings stored the way the pre-0055 ingest stored them (no filer
text): a bare row is never screened by name; the dry run writes nothing; ``--apply`` appends exactly the planned
versions (COUNT THE TABLES) and the read then screens AMKR's internal transfer and FTHM's buy carries its note;
a re-run appends nothing; a version is stamped now (#1); an uncached filing stays bare; ``--apply`` refuses while
the call-of-record lock is held.
"""

from __future__ import annotations

import shutil
from datetime import date, datetime, timezone

import pytest

import ingest.edgar.form4 as form4
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.insider_internal_transfers import is_internal_transfer
from pipeline import repair_insider_filer_text as repair
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from scoreboard.overlays import episode_insider_buys
from signals.base import PointInTimeData
from tests.transfer_fixtures import ACCEPTED, forms_of, seed, xml

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
TEXT = ("ownership_nature", "txn_footnotes", "filing_natures")
CASES = ("AMKR", "FTHM", "ESI")


@pytest.fixture
def bare(db, monkeypatch):
    """AMKR 2025, FTHM and Pershing Square's ESI filings stored the pre-0055 way: no filer text (and, for FTHM, no
    0054 ownership evidence either — the rows the 0054 repair never targeted)."""
    real = form4.parse_form4
    with monkeypatch.context() as m:
        m.setattr(
            form4, "parse_form4", lambda doc: [{**r, **dict.fromkeys(TEXT)} for r in real(doc)]
        )
        sids = {c: seed(db, c) for c in ("AMKR", "ESI")}
        m.setattr(
            form4,
            "parse_form4",
            lambda doc: [
                {**r, **dict.fromkeys(TEXT), "direct_indirect": None, "shares_owned_after": None}
                for r in real(doc)
            ],
        )
        sids["FTHM"] = seed(db, "FTHM")
    return sids


@pytest.fixture
def cache(tmp_path):
    """An EDGAR cache root holding the fixtures the way the app caches them: ``forms/<accession>/<doc>``."""
    for case in CASES:
        for acc in forms_of(case):
            d = tmp_path / "forms" / acc
            d.mkdir(parents=True)
            (d / "form4.xml").write_text(xml(acc), encoding="utf-8")
    return tmp_path


def _counts(db) -> tuple[int, int]:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        facts = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM insider_amendment")
        return facts, cur.fetchone()["n"]


def _amkr_legs(db, sid, known_at=FAR) -> list[dict]:
    rows = PointInTimeData(db, asof=date(2025, 3, 3), known_at=known_at).insider_txns(sid)
    return [r for r in rows if r["valid_from"] == date(2025, 2, 24) and r["txn_code"] in ("P", "S")]


def _fthm_buy(db, sid) -> dict:
    d = date(2024, 9, 12)
    (buy,) = episode_insider_buys(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        start=d,
        end=d,
        asof=d,
        known_at=FAR,
        day_lows={},
    )
    return buy


def test_a_bare_row_is_never_screened_by_name(db, bare):
    assert len(_amkr_legs(db, bare["AMKR"])) == 3
    assert not any(is_internal_transfer(r) for r in _amkr_legs(db, bare["AMKR"]))
    assert _fthm_buy(db, bare["FTHM"])["private_passage"] is None
    # one filer's own buy and sale need no text: Pershing Square pairs before the repair too
    esi = PointInTimeData(db, asof=date(2015, 7, 1), known_at=FAR).insider_txns(bare["ESI"])
    assert {r["txn_code"] for r in esi if is_internal_transfer(r)} == {"P", "S"}


def test_the_dry_run_plans_the_target_set_and_writes_nothing(db, bare, cache):
    before = _counts(db)
    res = repair.run_repair(db, cache_dir=cache, apply=False, log=lambda *_: None)
    db.rollback()
    assert _counts(db) == before  # COUNT THE TABLES
    # the transfer candidates: AMKR's two signatures (5 purchase filers + James + Agnes, twice) and FTHM's pair;
    # Pershing Square's buy and sale share ONE owner (linked on identity alone) — not a candidate
    assert res.transfer_candidates == 7 + 7 + 2
    assert res.fact_rows_versioned == 16
    assert res.by_reason == {"transfer": 16, "private_purchase": 1}  # Flanders's buy is both
    assert res.not_cached == [] and res.parse_errors == [] and res.seq_mismatch == []
    assert res.amendment_records_versioned == 0


def test_apply_versions_exactly_the_plan_then_the_read_screens(db, bare, cache):
    facts, records = _counts(db)
    res = repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    assert _counts(db) == (facts + res.fact_rows_versioned, records)
    assert res.fact_rows_versioned == 16
    with (
        db.cursor() as cur
    ):  # append-only: every version supersedes the row it repairs, nothing updated
        cur.execute(
            "SELECT count(*) AS n FROM fact_insider_txn WHERE supersedes IS NOT NULL AND filing_natures IS NOT NULL"
        )
        assert cur.fetchone()["n"] == 16
        # the 0054 evidence FTHM's rows lacked rides in the same version
        cur.execute(
            "SELECT direct_indirect FROM fact_insider_txn WHERE security_id = %s AND supersedes IS NOT NULL",
            (bare["FTHM"],),
        )
        assert {r["direct_indirect"] for r in cur.fetchall()} == {"D"}
    legs = _amkr_legs(db, bare["AMKR"])
    assert len(legs) == 3 and all(is_internal_transfer(r) for r in legs)
    buy = _fthm_buy(db, bare["FTHM"])
    assert buy["private_counterparty"] == "Harley Joshua" and buy["character"] == "open_market"
    # idempotent: a re-run appends nothing
    again = repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    assert (again.fact_rows_versioned, again.amendment_records_versioned) == (0, 0)
    assert _counts(db) == (facts + 16, records)


def test_a_pre_repair_replay_still_reads_what_the_system_held(db, bare, cache):
    """#1 — the versions are stamped NOW: a read pinned before the repair keeps the unscreened legs it held."""
    pinned = db_now(db)
    db.commit()
    repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    assert not any(is_internal_transfer(r) for r in _amkr_legs(db, bare["AMKR"], known_at=pinned))
    assert all(is_internal_transfer(r) for r in _amkr_legs(db, bare["AMKR"]))


def test_an_uncached_filing_stays_bare(db, bare, cache):
    """Susan Kim's purchase filing is the one that names James's and Agnes's GRATs on 2025-02-24; without it the
    purchase leg's natures come only from the other four filers — James's own filing still names John T. Kim's
    trusts (a purchase co-reporter), so his sale pairs; Agnes's copy has no holding link left and stays counted.
    """
    shutil.rmtree(cache / "forms" / "0001158922-25-000006")
    res = repair.run_repair(db, cache_dir=cache, apply=True, log=lambda *_: None)
    assert res.not_cached == ["0001158922-25-000006"]
    by = {r["insider_name"]: r for r in _amkr_legs(db, bare["AMKR"])}
    assert is_internal_transfer(by["SUJODA Investments, LP"]) and is_internal_transfer(
        by["KIM JAMES J"]
    )
    assert not is_internal_transfer(by["KIM AGNES C"])


def test_the_cli_apply_refuses_while_the_lock_is_held(db, bare, cache):
    before = _counts(db)
    lock = acquire_pass_lock(holder="test")
    try:
        with pytest.raises(SystemExit) as exc:
            repair.main(["--apply", "--cache-dir", str(cache)])
        assert exc.value.code == EXIT_ALREADY_RUNNING
    finally:
        lock.release()
    assert _counts(db) == before


def test_the_cli_dry_run_then_apply(db, bare, cache, capsys):
    before = _counts(db)
    repair.main(["--cache-dir", str(cache), "--ticker", "AMKR"])
    assert _counts(db) == before
    assert "WOULD version" in capsys.readouterr().out
    repair.main(["--apply", "--cache-dir", str(cache)])
    assert _counts(db) == (before[0] + 16, before[1])
    assert set(ACCEPTED) >= set(forms_of("AMKR"))
