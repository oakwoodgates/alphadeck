"""``pipeline.repair_filing_dates`` — stored 8-K / 13D/G / revenue rows given their public date (F7) and share counts
their filing's acceptance (F9).

The filings are direct-input contracts (a temporary EDGAR cache holding exactly the listing each case needs: an
evening acceptance with the next day's filing date, a same-day one, the 10-23 drift past midnight, an accession the
cache does not hold). The share-count case is REAL data: ASLE's companyfacts + submissions
(``tests/fixtures/edgar/cover_dates/``) — its 2026-06-30 10-Q (0001104659-26-092710, filed 2026-08-07, accepted
2026-08-07T20:12:46Z) whose cover says 47,726,763 shares as of 2026-08-03.

Headline gates: COUNT THE TABLES (a second --apply appends zero), the dry run writes nothing, one filing is never
read at two dates (the revenue twin + set-aside), and the re-dated rows read on the night they appeared.
Dates: 2026-10-26 is a Monday; EDT (UTC-4).
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar import client as edgar_client
from ingest.edgar.form8k import ingest_form8k
from ingest.edgar.schedule13 import ingest_schedule13
from ingest.fundamentals import QuarterPoint, dated_points, store_quarters
from ingest.shares import ingest_shares_outstanding
from pipeline import repair_filing_dates as R
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from repositories import fundamentals_set_aside_repo as set_aside_repo

UTC = timezone.utc
T = DEFAULT_TENANT_ID
FAR = datetime(2100, 1, 1, tzinfo=UTC)
CIK = "0001234567"
MON, TUE = date(2026, 10, 26), date(2026, 10, 27)
EVENING = "2026-10-26T22:05:00.000Z"  # 18:05 EDT Monday
DAYTIME = "2026-10-27T14:00:00.000Z"  # 10:00 EDT Tuesday (its filing date)
DRIFT = "2026-10-27T05:00:00.000Z"  # 01:00 EDT Tuesday — EDGAR closed: untrusted

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "cover_dates"
_ASLE_Q2 = (
    "https://www.sec.gov/Archives/edgar/data/1754170/000110465926092710/asle-20260630x10q.htm"
)


def _utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _cache(tmp_path: Path, rows: list[tuple[str, str, str]], cik: str = CIK) -> Path:
    """A temporary EDGAR cache with one company's submissions listing (accession, filingDate, acceptance)."""
    cache = tmp_path / "edgar_cache"
    (cache / "submissions").mkdir(parents=True, exist_ok=True)
    (cache / "submissions" / f"CIK{int(cik):010d}.json").write_text(
        json.dumps(
            {
                "filings": {
                    "recent": {
                        "accessionNumber": [r[0] for r in rows],
                        "filingDate": [r[1] for r in rows],
                        "acceptanceDateTime": [r[2] for r in rows],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return cache


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) n FROM {table}")  # noqa: S608 — test constants
        return cur.fetchone()["n"]


def _read(db, table, sid, asof, *, known_at=FAR, clock="record"):
    return as_of(db, table, security_id=sid, asof=asof, known_at=known_at, tenant_id=T, clock=clock)


def _8k(accession: str) -> dict:
    # stored before the rule: no listed time on the row, so the ingest dated it by its filing date
    return {
        "accession": accession,
        "form": "8-K",
        "filed": "2026-10-27",
        "items": ["8.01"],
        "accepted": "",
    }


def _status(result: R.RepairResult, group: str) -> dict[str, str]:
    return {r.key: r.status for r in result.rows if r.group == group}


# --- 8-K + 13D/G ----------------------------------------------------------------------------------------------


def test_an_evening_8k_is_re_dated_and_nothing_else_is_written(db, security_id, tmp_path):
    ingest_form8k(
        db, security_id, CIK, [_8k(a) for a in ("ACC-EVE", "ACC-DAY", "ACC-DRIFT", "ACC-GONE")]
    )
    db.commit()
    cache = _cache(
        tmp_path,
        [
            ("ACC-EVE", "2026-10-27", EVENING),
            ("ACC-DAY", "2026-10-27", DAYTIME),
            ("ACC-DRIFT", "2026-10-27", DRIFT),
        ],
    )
    assert "ACC-EVE" not in {
        r["accession"] for r in _read(db, "fact_corporate_event", security_id, MON)
    }

    result = R.scan(db, cache_dir=cache, groups=(R.EIGHT_K,))
    db.rollback()
    assert _status(result, R.EIGHT_K) == {
        "ACC-EVE": R.MOVE,
        "ACC-DAY": R.SAME_DAY,
        "ACC-DRIFT": R.UNTRUSTED,
        "ACC-GONE": R.NOT_LISTED,
    }
    (eve,) = [r for r in result.rows if r.key == "ACC-EVE"]
    assert (eve.new_from, eve.accepted, eve.evening) == (MON, _utc(EVENING), True)

    n = _count(db, "fact_corporate_event")
    R.apply(db, result)
    assert _count(db, "fact_corporate_event") == n + 1  # only the move (no --same-day)
    row = {r["accession"]: r for r in _read(db, "fact_corporate_event", security_id, MON)}[
        "ACC-EVE"
    ]
    assert (row["valid_from"], row["filed"], row["accepted"], row["items"]) == (
        MON,
        TUE,
        _utc(EVENING),
        ["8.01"],
    )
    # the public clock reads it on Monday's night too
    pub = _read(
        db,
        "fact_corporate_event",
        security_id,
        MON,
        known_at=datetime(2026, 10, 27, 4, tzinfo=UTC),
        clock="public",
    )
    assert [r["accession"] for r in pub] == ["ACC-EVE"]

    # idempotent: a second run plans nothing for it, writes nothing
    again = R.scan(db, cache_dir=cache, groups=(R.EIGHT_K,))
    assert _status(again, R.EIGHT_K)["ACC-EVE"] == R.DATED
    R.apply(db, again)
    assert _count(db, "fact_corporate_event") == n + 1
    # --same-day stores the acceptance on the day-time filing too, its date unchanged
    same = R.scan(db, cache_dir=cache, groups=(R.EIGHT_K,), same_day=True)
    R.apply(db, same)
    assert _count(db, "fact_corporate_event") == n + 2
    day = {r["accession"]: r for r in _read(db, "fact_corporate_event", security_id, TUE)}[
        "ACC-DAY"
    ]
    assert (day["valid_from"], day["accepted"]) == (TUE, _utc(DAYTIME))


def test_an_evening_13g_is_re_dated(db, security_id, tmp_path):
    f = {
        "accession": "ACC-G",
        "form": "SC 13G",
        "filed": "2026-10-27",
        "primary_doc": "x.htm",
        "accepted": "",
    }
    ingest_schedule13(db, security_id, CIK, [f], None)
    db.commit()
    cache = _cache(tmp_path, [("ACC-G", "2026-10-27", EVENING)])
    result = R.scan(db, cache_dir=cache, groups=(R.THIRTEEN_D,))
    assert _status(result, R.THIRTEEN_D) == {"ACC-G": R.MOVE}
    n = _count(db, "fact_activist_stake")
    R.apply(db, result)
    assert _count(db, "fact_activist_stake") == n + 1
    (row,) = _read(db, "fact_activist_stake", security_id, MON)
    assert (row["valid_from"], row["form"], row["accepted"]) == (MON, "SC 13G", _utc(EVENING))


# --- revenue: a twin at the acceptance date, the old-date versions set aside ----------------------------------


def _q(value, filed, accession, period=date(2026, 9, 30)) -> QuarterPoint:
    return QuarterPoint(
        metric_key="revenue",
        period_end=period,
        value=value,
        filed=filed,
        accession=accession,
        basis="native",
        fiscal_period="Q3",
        fiscal_year=2026,
    )


def test_a_revenue_filing_is_re_dated_once_and_read_at_one_date(db, security_id, tmp_path):
    later = date(2027, 2, 20)  # the 10-K re-reports the quarter as a comparative (same number)
    assert (
        store_quarters(
            db, security_id, [_q(100.0, TUE, "ACC-Q"), _q(100.0, later, "ACC-K")]
        ).appended
        == 2
    )
    db.commit()
    cache = _cache(tmp_path, [("ACC-Q", "2026-10-27", EVENING)])

    result = R.scan(db, cache_dir=cache, groups=(R.REVENUE,))
    assert _status(result, R.REVENUE) == {"ACC-Q": R.MOVE, "ACC-K": R.NOT_LISTED}
    n, s = _count(db, "fact_fundamentals"), _count(db, "fundamentals_set_aside")
    R.apply(db, result)
    assert (_count(db, "fact_fundamentals"), _count(db, "fundamentals_set_aside")) == (n + 1, s + 1)

    # Monday's read sees the quarter, dated Monday
    (mon,) = _read(db, "fact_fundamentals", security_id, MON)
    assert (mon["valid_from"], mon["knowable_from"], float(mon["value"])) == (MON, MON, 100.0)
    # a later read sees ONE filing for it — the twin, never the old Tuesday version beside it
    (tue,) = _read(db, "fact_fundamentals", security_id, date(2026, 11, 15))
    assert tue["valid_from"] == MON and tue["accepted"] == _utc(EVENING)
    # after the comparative the pick is the later filing, and the number dates at Monday (D3a's run)
    (k,) = _read(db, "fact_fundamentals", security_id, date(2027, 3, 1))
    assert (k["accession"], k["knowable_from"]) == ("ACC-K", MON)
    # the public clock agrees
    (pub,) = _read(
        db,
        "fact_fundamentals",
        security_id,
        MON,
        known_at=datetime(2026, 10, 27, 4, tzinfo=UTC),
        clock="public",
    )
    assert pub["valid_from"] == MON
    # the re-dating set-aside is bookkeeping: the family-less data-quality reads leave it out
    assert set_aside_repo.count_active(db) == 0
    assert len(set_aside_repo.active(db, family=set_aside_repo.REDATE_FAMILY)) == 1

    # the nightly's own parse appends nothing over it (the stored dating is carried), and a second --apply neither
    points = dated_points(
        db, security_id, CIK, [_q(100.0, TUE, "ACC-Q"), _q(100.0, later, "ACC-K")], cache_dir=cache
    )
    assert store_quarters(db, security_id, points).appended == 0
    again = R.scan(db, cache_dir=cache, groups=(R.REVENUE,))
    assert _status(again, R.REVENUE)["ACC-Q"] == R.DATED
    R.apply(db, again)
    assert (_count(db, "fact_fundamentals"), _count(db, "fundamentals_set_aside")) == (n + 1, s + 1)


def test_a_revenue_filing_wholly_set_aside_is_not_re_dated(db, security_id, tmp_path):
    store_quarters(db, security_id, [_q(100.0, TUE, "ACC-Q")])
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM fact_fundamentals WHERE accession = 'ACC-Q'")
        fid = cur.fetchone()["id"]
    set_aside_repo.append_set_aside(
        db,
        fid,
        reason="fy_total_in_quarter",
        rule="fundamentals-quality-screen@test",
        author="test",
    )
    db.commit()
    result = R.scan(
        db, cache_dir=_cache(tmp_path, [("ACC-Q", "2026-10-27", EVENING)]), groups=(R.REVENUE,)
    )
    assert _status(result, R.REVENUE) == {"ACC-Q": R.SET_ASIDE}
    assert result.planned(R.REVENUE) == []


# --- share counts (F9, real ASLE data) -----------------------------------------------------------------------


def _asle_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "asle_cache"
    (cache / "submissions").mkdir(parents=True)
    (cache / "companyfacts").mkdir()
    shutil.copy(_FX / "ASLE_submissions.json", cache / "submissions" / "CIK0001754170.json")
    shutil.copy(_FX / "ASLE_companyfacts.json", cache / "companyfacts" / "CIK0001754170.json")
    return cache


def _asle(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, T, "ASLE", "0001754170", "2020-01-01"),
        )
    db.commit()
    return sid


def test_a_share_count_gets_its_filings_acceptance(db, tmp_path):
    sid = _asle(db)
    ingest_shares_outstanding(
        db,
        sid,
        shares=47_726_763,
        source="10-q-cover",
        source_ref=_ASLE_Q2,
        event_date=date(2026, 8, 3),
        ratified_by="auto",
        recorded_at=datetime(2026, 9, 1, 18, 13, 46, tzinfo=UTC),
    )
    db.commit()
    between = datetime(2026, 8, 20, tzinfo=UTC)
    assert (
        _read(
            db, "fact_shares_outstanding", sid, date(2026, 8, 20), known_at=between, clock="public"
        )
        == []
    )

    cache = _asle_cache(tmp_path)
    result = R.scan(db, cache_dir=cache, groups=(R.SHARES,))
    (row,) = [r for r in result.rows if r.group == R.SHARES]
    assert row.status == R.PUBLISH and row.publication_accession == "0001104659-26-092710"
    assert row.accepted == datetime(2026, 8, 7, 20, 12, 46, tzinfo=UTC)
    n = _count(db, "fact_shares_outstanding")
    R.apply(db, result)
    assert _count(db, "fact_shares_outstanding") == n + 1
    (seen,) = _read(
        db, "fact_shares_outstanding", sid, date(2026, 8, 20), known_at=between, clock="public"
    )
    assert float(seen["shares"]) == 47_726_763 and seen["recorded_at"] == row.accepted
    # idempotent
    R.apply(db, R.scan(db, cache_dir=cache, groups=(R.SHARES,)))
    assert _count(db, "fact_shares_outstanding") == n + 1


def test_a_count_no_cover_row_carries_keeps_its_ratification(db, tmp_path):
    sid = _asle(db)
    ingest_shares_outstanding(
        db,
        sid,
        shares=47_000_000,  # an operator-typed count: no cover row says it
        source="ratified",
        source_ref=_ASLE_Q2,
        event_date=date(2026, 8, 3),
        ratified_by="operator",
    )
    db.commit()
    result = R.scan(db, cache_dir=_asle_cache(tmp_path), groups=(R.SHARES,))
    assert [r.status for r in result.rows if r.group == R.SHARES] == [R.NO_COVER]
    assert result.planned(R.SHARES) == []


# --- the CLI ---------------------------------------------------------------------------------------------------


def test_the_cli_dry_run_writes_nothing_and_apply_journals(
    db, security_id, tmp_path, monkeypatch, capsys
):
    ingest_form8k(db, security_id, CIK, [_8k("ACC-EVE")])
    db.commit()
    cache = _cache(tmp_path, [("ACC-EVE", "2026-10-27", EVENING)])
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", cache)
    monkeypatch.setattr(R, "_DEFAULT_LOG_DIR", tmp_path / "repairs")
    n = _count(db, "fact_corporate_event")

    R.main(["--tables", "8k"])
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "[move] DEVCO" in out and "would append" in out
    assert _count(db, "fact_corporate_event") == n and not (tmp_path / "repairs").exists()

    R.main(["--apply", "--tables", "8k"])
    assert _count(db, "fact_corporate_event") == n + 1
    [prov] = list((tmp_path / "repairs").glob("*-repair-filing-dates.json"))
    payload = json.loads(prov.read_text(encoding="utf-8"))
    assert payload["applied"] is True and payload["rule_log"] == R.RULE_ID
    assert (
        payload["counts_after"]["fact_corporate_event"]
        - payload["counts_before"]["fact_corporate_event"]
        == 1
    )
    with db.cursor() as cur:
        cur.execute("SELECT tool, action, rows_changed, provenance FROM data_repair")
        [row] = cur.fetchall()
    db.commit()
    assert (row["tool"], row["action"], row["rows_changed"], row["provenance"]) == (
        "repair_filing_dates",
        "apply",
        1,
        str(prov),
    )


def test_the_cli_apply_exits_75_while_the_lock_is_held(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", _cache(tmp_path, []))
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            R.main(["--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        assert "ALREADY RUNNING" in capsys.readouterr().out
        R.main([])  # a dry run takes no lock and still runs
    finally:
        held.release()
