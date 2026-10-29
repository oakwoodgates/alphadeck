"""``pipeline.repair_shares_cover_dates`` — the stored shares counts whose cover date is a filer's typo.

The DB tests use REAL data: ASLE's companyfacts, submissions and 10-Q cover (``tests/fixtures/edgar/cover_dates/``,
described in ``tests/ingest/test_cover_dates.py``), served from a temporary EDGAR cache, and the bad version
stored exactly as the dev copy of prod holds it (2026-10-29): 52,990,947 shares as of 2034-03-05, ``10-q-cover``,
``ratified_by='auto'``, recorded 2026-09-01, citing the 2026-06-30 10-Q.

The later-filing case is the same shape the dry run found on 28 dev facts (AIR, AVAV, BR, ...): a genuine count of
a later filing stored under an earlier filing's URL. Here it is ASLE's real 2026-06-30 10-Q count stored under its
real 2026-03-31 10-Q URL — the shape the old reader writes when it cites one 10-Q and takes a later filing's
cover. DB-backed tests skip without Postgres.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar import client as edgar_client
from ingest.shares import ingest_shares_outstanding
from pipeline import repair_shares_cover_dates as R
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "cover_dates"
_BASE = "https://www.sec.gov/Archives/edgar/data/1754170"
_Q2 = f"{_BASE}/000110465926092710/asle-20260630x10q.htm"  # filed 2026-08-07 — the cited filing
_Q1 = f"{_BASE}/000110465926057983/asle-20260331x10q.htm"  # filed 2026-05-08
_RECORDED = datetime(2026, 9, 1, 18, 13, 46, tzinfo=timezone.utc)
_TODAY = date(2026, 10, 29)


def _cache(tmp_path: Path) -> Path:
    cache = tmp_path / "edgar_cache"
    (cache / "submissions").mkdir(parents=True)
    (cache / "companyfacts").mkdir()
    shutil.copy(_FX / "ASLE_submissions.json", cache / "submissions" / "CIK0001754170.json")
    shutil.copy(_FX / "ASLE_companyfacts.json", cache / "companyfacts" / "CIK0001754170.json")
    doc = cache / "forms" / "0001104659-26-092710"
    doc.mkdir(parents=True)
    shutil.copy(_FX / "asle-20260630x10q_cover.txt", doc / "asle-20260630x10q.htm")
    return cache


def _asle(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "ASLE", "0001754170", "2020-01-01"),
        )
    db.commit()
    return sid


def _store(db, sid, *, shares, asof, ref=_Q2, by="auto") -> uuid.UUID:
    fid = ingest_shares_outstanding(
        db,
        sid,
        shares=shares,
        source="10-q-cover",
        source_ref=ref,
        event_date=asof,
        note=f"Cover-page shares outstanding as of {asof.isoformat()} (single class).",
        ratified_by=by,
        recorded_at=_RECORDED,
    )
    db.commit()
    return fid


def _store_the_typo(db, sid) -> uuid.UUID:
    return _store(db, sid, shares=52_990_947, asof=date(2034, 3, 5))


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_shares_outstanding")
        n = cur.fetchone()["n"]
    db.commit()
    return n


def _read(db, sid, asof: date) -> list[dict]:
    rows = as_of(
        db,
        "fact_shares_outstanding",
        security_id=sid,
        asof=asof,
        known_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    return rows


def test_the_typo_is_corrected_from_the_cited_filings_own_cover(db, tmp_path):
    sid = _asle(db)
    bad = _store_the_typo(db, sid)
    res = R.scan(db, cache_dir=_cache(tmp_path), today=_TODAY)
    [f] = res.facts
    assert f.status == R.CORRECT
    assert f.filing.filed == date(2026, 8, 7) and f.filing.accepted == "2026-08-07T20:12:46.000Z"
    assert f.origin == "10-K/A 0001558370-24-004010, filed 2024-03-27"
    assert f.cover_line.endswith("outstanding as of August 3, 2026 was 47,726,763")
    assert (f.corrected_shares, f.corrected_asof) == (47_726_763.0, date(2026, 8, 3))

    before = _count(db)
    R.apply(db, res)
    assert _count(db) == before + 1 == res.rows_after
    # the read picks the correction today AND after 2034 (same source_ref, later recorded_at)
    for asof in (_TODAY, date(2034, 6, 1)):
        [row] = _read(db, sid, asof)
        assert row["id"] == f.appended_id and float(row["shares"]) == 47_726_763
        assert row["valid_from"] == date(2026, 8, 3) and row["supersedes"] == bad
        assert row["ratified_by"] == "auto" and row["vouched"] is None
        assert row["source"] == "10-q-cover" and row["source_ref"] == _Q2
        assert (
            "52,990,947 as of 2034-03-05" in row["note"] and "0001558370-24-004010" in row["note"]
        )
        assert row["recorded_at"] > _RECORDED
    # never an UPDATE: the bad version is still there, untouched
    with db.cursor() as cur:
        cur.execute("SELECT shares, valid_from FROM fact_shares_outstanding WHERE id = %s", (bad,))
        old = cur.fetchone()
    db.commit()
    assert (float(old["shares"]), old["valid_from"]) == (52_990_947, date(2034, 3, 5))


def test_a_second_apply_appends_nothing(db, tmp_path):
    sid = _asle(db)
    _store_the_typo(db, sid)
    cache = _cache(tmp_path)
    R.apply(db, R.scan(db, cache_dir=cache, today=_TODAY))
    n = _count(db)
    again = R.scan(db, cache_dir=cache, today=_TODAY)
    assert [f.status for f in again.facts] == [R.CORRECTED]
    R.apply(db, again)
    assert _count(db) == n  # counted at the table, not the read


def test_a_later_filings_count_is_listed_never_written(db, tmp_path):
    """ASLE's real Q2 count (as of 2026-08-03, filed 2026-08-07) stored under its real Q1 URL (filed 2026-05-08):
    dated after the cited filing, but the count is right — correcting it would put an older count back.
    """
    sid = _asle(db)
    _store(db, sid, shares=47_726_763, asof=date(2026, 8, 3), ref=_Q1)
    res = R.scan(db, cache_dir=_cache(tmp_path), today=_TODAY)
    [f] = res.facts
    assert f.status == R.LATER_FILING
    assert "10-Q 0001104659-26-092710, filed 2026-08-07" in f.why
    n = _count(db)
    R.apply(db, res)
    assert _count(db) == n


def test_an_operator_ratified_typo_is_listed_never_written(db, tmp_path):
    sid = _asle(db)
    _store(db, sid, shares=52_990_947, asof=date(2034, 3, 5), by="operator")
    res = R.scan(db, cache_dir=_cache(tmp_path), today=_TODAY)
    assert [f.status for f in res.facts] == [R.OPERATOR]
    n = _count(db)
    R.apply(db, res)
    assert _count(db) == n


def test_a_fact_on_or_before_its_filing_is_not_listed(db, tmp_path):
    sid = _asle(db)
    _store(db, sid, shares=47_726_763, asof=date(2026, 8, 3))
    assert R.scan(db, cache_dir=_cache(tmp_path), today=_TODAY).facts == []


def test_an_uncached_filing_is_listed_not_judged(db, tmp_path):
    """Recall-visible: no submissions index and no companyfacts for the cited company — listed, never judged,
    never written (the dry run does not go to the network)."""
    sid = _asle(db)
    _store_the_typo(db, sid)
    res = R.scan(db, cache_dir=tmp_path / "empty", today=_TODAY)
    [f] = res.facts
    assert f.status == R.NOT_JUDGED and "not in the submissions index" in f.why


def test_parse_source_ref():
    assert R.parse_source_ref(_Q2) == (1754170, "0001104659-26-092710", "asle-20260630x10q.htm")
    assert R.parse_source_ref("https://example.com/cover") is None


def test_the_cli_dry_run_writes_nothing_and_apply_journals(db, tmp_path, monkeypatch, capsys):
    sid = _asle(db)
    _store_the_typo(db, sid)
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", _cache(tmp_path))
    monkeypatch.setattr(R, "_DEFAULT_LOG_DIR", tmp_path / "repairs")
    n = _count(db)

    R.main([])
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "[correct] ASLE" in out
    assert "-> would append: 47,726,763 shares as of 2026-08-03" in out
    assert "1 date typo(s) to correct" in out and "would append 1" in out
    assert _count(db) == n and not (tmp_path / "repairs").exists()

    R.main(["--apply"])
    out = capsys.readouterr().out
    assert "-> appended: 47,726,763 shares as of 2026-08-03" in out
    assert _count(db) == n + 1
    [prov] = list((tmp_path / "repairs").glob("*-repair-shares-cover-dates.json"))
    payload = json.loads(prov.read_text(encoding="utf-8"))
    assert payload["applied"] is True and payload["rows_after"] - payload["rows_before"] == 1
    assert payload["facts"][0]["corrected_shares"] == 47_726_763
    with db.cursor() as cur:
        cur.execute("SELECT tool, action, rows_changed, deletes, provenance FROM data_repair")
        [row] = cur.fetchall()
    db.commit()
    assert (row["tool"], row["action"], row["rows_changed"], row["deletes"]) == (
        "repair_shares_cover_dates",
        "apply",
        1,
        False,
    )
    assert row["provenance"] == str(prov)

    R.main(["--apply"])  # idempotent: a second run appends nothing (and journals its zero)
    assert "appended 0" in capsys.readouterr().out and _count(db) == n + 1


def test_the_cli_apply_exits_75_while_the_lock_is_held(db, tmp_path, monkeypatch, capsys):
    sid = _asle(db)
    _store_the_typo(db, sid)
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", _cache(tmp_path))
    monkeypatch.setattr(R, "_DEFAULT_LOG_DIR", tmp_path / "repairs")
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            R.main(["--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        assert "ALREADY RUNNING" in capsys.readouterr().out
        R.main([])  # a dry run takes no lock and still runs
    finally:
        held.release()
    assert _count(db) == 1
