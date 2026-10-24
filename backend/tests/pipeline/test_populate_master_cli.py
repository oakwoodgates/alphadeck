"""The company-list CLI (``python -m pipeline.populate_master``) — dry run by default, ``--apply`` in one
transaction, its own lock, its yield to a running pass, the boot catch-up's age gate, the artifact + Admin line,
and the sidecar's weekly schedule. Tables are COUNTED before and after (a dry run must write nothing at all).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from pipeline import populate_master
from pipeline.run_lock import LOCK_ID_MASTER_REFRESH, acquire_job_lock, acquire_pass_lock
from securities import sec_tickers

_BACKEND = Path(__file__).resolve().parents[2]
_CIK = "0001234567"
_NOW = datetime(2026, 10, 24, 13, 0, tzinfo=timezone.utc)  # Saturday 09:00 ET


def _file(*rows) -> dict:
    return {"fields": ["cik", "name", "ticker", "exchange"], "data": [list(r) for r in rows]}


_SEC = _file(
    (1234567, "General Fusion Inc.", "GFUZ", "Nasdaq"),
    (2222222, "New Co", "NEWC", "NYSE"),
)


def _seed(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, exchange, is_primary, valid_from) "
            "VALUES (%s, %s, %s, 'SVAC', 'Starboard', 'Nasdaq', TRUE, %s)",
            (sid, DEFAULT_TENANT_ID, _CIK, date(2026, 8, 16)),
        )
    db.commit()
    return sid


def _counts(db) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for t in ("security_master", "security_ticker_history", "price_symbol_change"):
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — test SQL
            out[t] = cur.fetchone()["n"]
    return out


@pytest.fixture
def dirs(tmp_path):
    cache = tmp_path / "sec_cache"
    sec_tickers.store_raw(_SEC, cache)
    return {"cache_dir": cache, "runs_dir": tmp_path / "runs", "fund_cache_dir": tmp_path / "fund"}


def _run(dirs, **kw):
    args = dict(
        live=False,
        apply=False,
        tenant_id=DEFAULT_TENANT_ID,
        now=lambda: _NOW,
        fund_refresh=lambda: 7,
        **dirs,
    )
    args.update(kw)
    return populate_master.run(**args)


def test_a_DRY_RUN_writes_nothing(db, dirs, capsys):
    _seed(db)
    before = _counts(db)
    assert _run(dirs) == 0
    out = capsys.readouterr().out
    assert "SVAC -> GFUZ" in out and "DRY RUN — nothing written" in out
    assert "1 new companies" in out and "1 renames" in out
    assert _counts(db) == before  # not one row
    assert not dirs["runs_dir"].exists()  # no artifact either
    with db.cursor() as cur:
        cur.execute("SELECT ticker FROM security_master")
        assert [r["ticker"] for r in cur.fetchall()] == ["SVAC"]


def test_APPLY_writes_once_caches_the_file_and_leaves_the_admin_line(db, dirs, tmp_path, capsys):
    sid = _seed(db)
    fresh = tmp_path / "fresh_cache"
    rc = _run(
        {**dirs, "cache_dir": fresh},
        live=True,
        apply=True,
        fetch=lambda: _SEC,
    )
    assert rc == 0
    assert _counts(db) == {
        "security_master": 2,
        "security_ticker_history": 1,
        "price_symbol_change": 0,  # no stored bars: nothing to pin
    }
    with db.cursor() as cur:
        cur.execute("SELECT ticker FROM security_master WHERE id = %s", (sid,))
        assert cur.fetchone()["ticker"] == "GFUZ"
    assert sec_tickers.read_cached_raw(fresh) == _SEC  # the file the master was refreshed FROM
    doc = populate_master.latest_master_run(runs_dir=dirs["runs_dir"])
    assert doc["status"] == "finished" and doc["mode"] == "apply" and doc["attention"] is False
    assert doc["admin_line"] == "Company list · refreshed Oct 24 · 1 new · 1 renames · 0 conflicts"
    assert doc["funds"] == {"refreshed": True, "rows": 7}
    assert doc["counts"]["renames"] == 1 and doc["plan"]["renames"][0]["old_ticker"] == "SVAC"
    assert "company-list: Company list · refreshed Oct 24" in capsys.readouterr().out

    from app.routers.admin import _master_refresh_out

    line = _master_refresh_out(doc)
    assert line.line == doc["admin_line"] and line.attention is False

    # IDEMPOTENT: a second --apply changes nothing (counted)
    before = _counts(db)
    assert _run({**dirs, "cache_dir": fresh}, live=True, apply=True, fetch=lambda: _SEC) == 0
    assert _counts(db) == before


def test_a_failed_fund_refresh_never_costs_the_company_refresh(db, dirs):
    _seed(db)

    def boom():
        raise RuntimeError("SEC 503")

    assert _run(dirs, live=True, apply=True, fetch=lambda: _SEC, fund_refresh=boom) == 0
    doc = populate_master.latest_master_run(runs_dir=dirs["runs_dir"])
    assert doc["status"] == "finished" and doc["funds"]["refreshed"] is False
    assert _counts(db)["security_ticker_history"] == 1


def test_a_failed_fetch_writes_nothing_and_records_a_loud_line(db, dirs):
    _seed(db)
    before = _counts(db)

    def boom():
        raise RuntimeError("timed out")

    assert _run(dirs, live=True, apply=True, fetch=boom) == 1
    assert _counts(db) == before
    doc = populate_master.latest_master_run(runs_dir=dirs["runs_dir"])
    assert doc["status"] == "failed" and doc["attention"] is True
    assert "refresh FAILED" in doc["admin_line"]


def test_the_job_holds_its_OWN_lock_a_second_refresh_is_refused(db, dirs, capsys):
    _seed(db)
    before = _counts(db)
    held = acquire_job_lock(lock_id=LOCK_ID_MASTER_REFRESH, holder="other", what="x")
    try:
        assert _run(dirs, apply=True) == 75
    finally:
        held.release()
    assert "REFUSED" in capsys.readouterr().out
    assert _counts(db) == before and not dirs["runs_dir"].exists()
    assert _run(dirs, apply=True) == 0  # released: it runs


def test_the_job_YIELDS_to_a_running_pass(db, dirs, capsys):
    _seed(db)
    before = _counts(db)
    lock = acquire_pass_lock(holder="cron")
    try:
        assert _run(dirs, apply=True) == 75
    finally:
        lock.release()
    assert "yields to it" in capsys.readouterr().out
    assert _counts(db) == before


def test_the_job_REFUSES_inside_the_nightly_window(db, dirs, monkeypatch):
    _seed(db)
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_BEFORE_MIN", "60")
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_AFTER_MIN", "60")
    get_settings.cache_clear()
    try:
        late = datetime(2026, 10, 23, 2, 15, tzinfo=timezone.utc)  # 22:15 ET
        assert _run(dirs, apply=True, now=lambda: late) == 75
    finally:
        get_settings.cache_clear()
    assert _counts(db)["security_ticker_history"] == 0


def test_the_boot_catch_up_runs_only_for_a_missed_week(db, dirs, capsys):
    _seed(db)
    # no applied refresh on record: the boot catch-up never makes the FIRST refresh (a deploy writes nothing)
    assert _run(dirs, apply=True, if_stale_days=7) == 0
    assert _counts(db)["security_ticker_history"] == 0
    runs = dirs["runs_dir"]
    runs.mkdir(parents=True)
    doc = {
        "kind": populate_master.PASS_KIND,
        "mode": "apply",
        "started_at": (_NOW - timedelta(days=2)).isoformat(),
        "finished_at": (_NOW - timedelta(days=2)).isoformat(),
        "admin_line": "x",
    }
    (runs / "20261022T130000Z.json").write_text(json.dumps(doc), encoding="utf-8")
    assert _run(dirs, apply=True, if_stale_days=7) == 0  # two days old: no catch-up
    assert _counts(db)["security_ticker_history"] == 0
    doc["finished_at"] = doc["started_at"] = (_NOW - timedelta(days=9)).isoformat()
    (runs / "20261022T130000Z.json").write_text(json.dumps(doc), encoding="utf-8")
    assert _run(dirs, apply=True, if_stale_days=7) == 0  # nine days old: the missed week runs
    assert _counts(db)["security_ticker_history"] == 1


# --- the sidecar's weekly schedule ------------------------------------------------------------------------------


def _script() -> str:
    return (_BACKEND / "scripts" / "daily_cron.sh").read_text(encoding="utf-8")


def test_the_SIDECAR_runs_the_refresh_weekly_bounded_after_the_boot_block():
    code = [ln.strip() for ln in _script().splitlines() if not ln.strip().startswith("#")]
    runs = [ln for ln in code if "python -m pipeline.populate_master" in ln]
    assert runs == [
        'timeout -k 60 "${MASTER_REFRESH_TIMEOUT_S}" python -m pipeline.populate_master --live --apply '
        '--run-kind cron "$@"'
    ]
    start = code.index("master_refresh_loop &")
    assert start > code.index('if [ "${CRON_EXIT_AFTER_BOOT:-}" = 1 ]; then')
    assert start < code.index("while :; do", start)  # ... and before the nightly loop
    assert "master_refresh_run --if-stale-days 7" in code
    compose = (_BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    for var, default in (("MASTER_REFRESH_DOW", "6"), ("MASTER_REFRESH_AT", "09:00")):
        c = re.search(rf"{var}: \$\{{ALPHADECK_{var}:-([0-9:]+)\}}", compose)
        s = re.search(rf'{var}="\$\{{{var}:-([0-9:]+)\}}"', _script())
        assert c and s and c.group(1) == s.group(1) == default


_BASH = shutil.which("bash")


@pytest.mark.skipif(_BASH is None, reason="needs bash + GNU date")
@pytest.mark.parametrize(
    "now, expected",
    [
        ("2026-10-21 12:00", "2026-10-24 09:00"),  # a Wednesday -> that Saturday
        ("2026-10-24 08:59", "2026-10-24 09:00"),  # Saturday before 09:00 -> today
        ("2026-10-24 09:00", "2026-10-31 09:00"),  # AT 09:00 -> strictly after: next week
        ("2026-10-25 10:00", "2026-10-31 09:00"),  # Sunday -> the next Saturday
    ],
)
def test_next_weekly_epoch_is_the_next_saturday_0900(now, expected):
    src = _script()
    fn = src[
        src.index("next_weekly_epoch() {") : src.index("\n}\n", src.index("next_weekly_epoch() {"))
        + 3
    ]
    prog = (
        f"MASTER_REFRESH_DOW=6\nMASTER_REFRESH_AT=09:00\n{fn}\n"
        f'e=$(date -d "{now}" +%s)\ndate -d "@$(next_weekly_epoch "$e")" "+%Y-%m-%d %H:%M"\n'
    )
    out = subprocess.run(
        [_BASH, "-c", prog],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "TZ": "America/New_York"},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected
