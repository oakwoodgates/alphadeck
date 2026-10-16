"""Q3 — the targeted, append-only repair of the benchmark bars a market-hours pass froze.

REAL data throughout: ``tests/fixtures/prices/benchmark_tape_2026_08.json`` holds the vendor's own chart
payloads (``final``, the nightly pull of 2026-10-15) and the rows prod ACTUALLY stored for SPY and IWM
2026-08-20..08-28 (``stored_prod_2026_08`` — one version each, backdated to the bar date, the 08-25/08-27
closes frozen intraday). The store is seeded with exactly those rows; a fake ``PriceSource`` serves the
vendor's payload (offline). Every write assertion COUNTS THE TABLE — the as-of read dedups, so a duplicate
append would hide behind a correct read.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from db.bitemporal import as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from ingest.prices.eod_loader import ingest_prices_backfill, parse_yahoo_chart
from pipeline import repair_benchmark_bars as RB
from pipeline.ingest_benchmarks import DEFAULT_RANGE
from pipeline.repair_benchmark_bars import (
    APPEND,
    MATCHES,
    NO_STORED_BAR,
    NO_VENDOR_BAR,
    ScopeRefused,
    plan_repairs,
    repair_benchmark_bars,
    scope_refusals,
)
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from securities.benchmarks import benchmark_ids, seed_benchmarks
from signals.base import PointInTimeData

ET = ZoneInfo("America/New_York")
_FIX = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "benchmark_tape_2026_08.json"
    ).read_text(encoding="utf-8")
)
D20, D21, D22, D25, D27, D28 = (date(2026, 8, d) for d in (20, 21, 22, 25, 27, 28))
THANKSGIVING_2025 = date(2025, 11, 27)  # a real market holiday: the vendor has no bar for it
NOW = datetime(
    2026, 10, 16, 12, 0, tzinfo=ET
)  # the repair's pinned instant (the guard + the stamp)

# MEASURED (the prod read + the vendor payload): (stored close, vendor close, stored vol, vendor vol)
FROZEN = {
    ("SPY", D25): (765.270020, 765.909973, 13587691, 27422300),
    ("SPY", D27): (770.820129, 771.099976, 18633946, 34557100),
    ("IWM", D25): (298.665009, 299.230011, 8425444, 14618000),
    ("IWM", D27): (299.559998, 299.809998, 9550950, 13658700),
}


def _final(ticker: str) -> list[dict]:
    return parse_yahoo_chart(_FIX[ticker]["final"])


def _stored(ticker: str) -> list[dict]:
    return [{**r, "d": date.fromisoformat(r["d"])} for r in _FIX[ticker]["stored_prod_2026_08"]]


class _Source:
    """A PriceSource stub serving the vendor's REAL payload per ticker; records each call's flags."""

    def __init__(self, bars: dict[str, list[dict]] | None = None, *, fail: tuple[str, ...] = ()):
        self.bars = bars if bars is not None else {"SPY": _final("SPY"), "IWM": _final("IWM")}
        self.fail = fail
        self.calls: list[tuple[str, bool, bool]] = []

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        self.calls.append((ticker, allow_live, force_refresh))
        if ticker in self.fail:
            raise RuntimeError("yahoo 502")
        return list(self.bars.get(ticker, []))


def _seed_prod_tape(db) -> dict:
    """Store SPY + IWM 2026-08-20..08-28 EXACTLY as prod held them (one version each, recorded_at = the bar
    date 00:00 UTC — the old nightly leg's backdated stamp)."""
    ids = seed_benchmarks(db)
    for ticker, sid in ids.items():
        ingest_prices_backfill(db, sid, _stored(ticker))
    db.commit()
    return ids


def _px_count(db) -> int:
    db.rollback()  # a fresh snapshot: rows another session (the CLI's own connection) committed are visible
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE tenant_id = %s", (DEFAULT_TENANT_ID,)
        )
        return cur.fetchone()["n"]


def _versions(db, sid, d) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT d, open, high, low, close, volume, valid_from, recorded_at FROM fact_price_eod "
            "WHERE security_id = %s AND d = %s ORDER BY recorded_at",
            (sid, d),
        )
        return cur.fetchall()


def _by_key(result) -> dict:
    return {(b.ticker, b.d): b for b in result.bars}


# --- the dry run: shows the four frozen bars, writes nothing -----------------------------------------------


def test_the_dry_run_shows_the_four_frozen_bars_and_appends_nothing(db):
    _seed_prod_tape(db)
    before = _px_count(db)

    result = repair_benchmark_bars(db, [D25, D27], source=_Source(), now=NOW)

    assert _px_count(db) == before  # COUNT the table: nothing written
    assert result.appended == 0 and result.recorded_at is None and not result.applied
    plan = _by_key(result)
    assert set(plan) == set(FROZEN)
    for key, (s_close, v_close, s_vol, v_vol) in FROZEN.items():
        b = plan[key]
        assert b.action == APPEND and {"close", "volume"} <= set(b.changed)
        assert float(b.stored["close"]) == pytest.approx(s_close, abs=1e-6)
        assert float(b.vendor["close"]) == pytest.approx(v_close, abs=1e-6)
        assert int(b.stored["volume"]) == s_vol and int(b.vendor["volume"]) == v_vol
        assert b.stored["versions"] == 1
        assert b.stored["recorded_at"] == datetime(2026, 8, b.d.day, tzinfo=timezone.utc)


# --- --apply: exactly four new versions, stamped now, originals untouched -----------------------------------


def test_apply_appends_exactly_the_four_corrected_versions_stamped_now(db):
    ids = _seed_prod_tape(db)
    before = _px_count(db)

    result = repair_benchmark_bars(db, [D25, D27], apply=True, source=_Source(), now=NOW)

    assert result.appended == 4 and result.recorded_at == NOW
    assert _px_count(db) == before + 4  # COUNT the table: exactly the four
    vendor = {t: {r["d"]: r for r in _final(t)} for t in ("SPY", "IWM")}
    for (ticker, d), (s_close, _, s_vol, _) in FROZEN.items():
        old, new = _versions(db, ids[ticker], d)
        # the original is untouched (append-only): still the frozen value, still its old stamp
        assert (
            float(old["close"]) == pytest.approx(s_close, abs=1e-6) and int(old["volume"]) == s_vol
        )
        assert old["recorded_at"] == datetime(2026, 8, d.day, tzinfo=timezone.utc)
        # the new version: the vendor's WHOLE bar (OHLC corrected with close + volume), valid_from = d
        assert new["recorded_at"] == NOW and new["valid_from"] == d
        for f in ("open", "high", "low", "close", "volume"):
            assert float(new[f]) == pytest.approx(vendor[ticker][d][f], rel=1e-9)
    # an OHL field the frozen bar had wrong is corrected too (SPY 08-27's high moved later in the session)
    assert float(_versions(db, ids["SPY"], D27)[-1]["high"]) == pytest.approx(772.359985, abs=1e-6)


def test_a_reapply_appends_zero(db):
    _seed_prod_tape(db)
    repair_benchmark_bars(db, [D25, D27], apply=True, source=_Source(), now=NOW)
    after_first = _px_count(db)

    again = repair_benchmark_bars(
        db, [D25, D27], apply=True, source=_Source(), now=NOW + timedelta(hours=1)
    )

    assert _px_count(db) == after_first  # COUNT the table: idempotent
    assert again.appended == 0 and again.recorded_at is None
    assert {b.action for b in again.bars} == {MATCHES}


def test_the_stamp_defaults_to_the_database_clock(db):
    """No ``now`` injected (production): the appended versions carry the DB clock read after the pull —
    bracketed by ``db_now`` before and after (the one-clock rule)."""
    ids = _seed_prod_tape(db)
    t0 = db_now(db)
    db.commit()

    result = repair_benchmark_bars(db, [D25], apply=True, source=_Source())

    t1 = db_now(db)
    db.commit()
    assert result.appended == 2 and t0 <= result.recorded_at <= t1
    assert _versions(db, ids["SPY"], D25)[-1]["recorded_at"] == result.recorded_at


# --- bitemporal honesty -----------------------------------------------------------------------------------


def test_a_read_pinned_before_the_repair_still_sees_the_frozen_close(db):
    """Transaction time: a read whose known_at predates the repair sees what the record held (765.27); a
    read at/after it sees the vendor's final (765.91). Checked through the raw as-of read AND the one
    consumer of the benchmark tape, ``PointInTimeData.benchmark_prices`` (the RS display column)."""
    ids = _seed_prod_tape(db)
    repair_benchmark_bars(db, [D25, D27], apply=True, source=_Source(), now=NOW)

    def close(ticker, d, known_at):
        (row,) = [
            r
            for r in as_of(
                db,
                "fact_price_eod",
                security_id=ids[ticker],
                asof=D28,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
            )
            if r["d"] == d
        ]
        return float(row["close"])

    def rs_close(ticker, d, known_at):
        bars = PointInTimeData(db, asof=D28, known_at=known_at).benchmark_prices(ticker)
        return float(next(b["close"] for b in bars if b["d"] == d))

    before, after = NOW - timedelta(minutes=1), NOW
    for (ticker, d), (s_close, v_close, _, _) in FROZEN.items():
        assert close(ticker, d, before) == pytest.approx(s_close, abs=1e-6)
        assert close(ticker, d, after) == pytest.approx(v_close, abs=1e-6)
        assert rs_close(ticker, d, before) == pytest.approx(s_close, abs=1e-6)
        assert rs_close(ticker, d, after) == pytest.approx(v_close, abs=1e-6)


# --- the scope guard --------------------------------------------------------------------------------------


def test_the_guard_refuses_an_empty_list():
    assert scope_refusals([], now=NOW) and "no dates" in scope_refusals([], now=NOW)[0]


def test_the_guard_refuses_a_weekend_date():
    (why,) = scope_refusals([D22], now=NOW)  # 2026-08-22 is a Saturday
    assert why.startswith("2026-08-22") and "weekend" in why


def test_the_guard_refuses_today_even_after_it_settles():
    (why,) = scope_refusals([D27], now=datetime(2026, 8, 27, 19, 0, tzinfo=ET))
    assert "today or later" in why and "not settled" not in why


def test_the_guard_refuses_an_unsettled_session():
    (why,) = scope_refusals([D27], now=datetime(2026, 8, 27, 12, 7, tzinfo=ET))
    assert "not settled" in why and "18:00:00-04:00" in why


def test_the_guard_refuses_a_future_date():
    (why,) = scope_refusals([D28], now=datetime(2026, 8, 27, 19, 0, tzinfo=ET))
    assert "today or later" in why and "not settled" in why


def test_the_guard_allows_settled_past_weekdays():
    assert scope_refusals([D25, D27], now=NOW) == []


@pytest.mark.parametrize(
    ("dates", "now"),
    [
        ([], NOW),
        ([D25, D22], NOW),  # one bad date refuses the WHOLE request
        ([D27], datetime(2026, 8, 27, 12, 7, tzinfo=ET)),
        ([D28], datetime(2026, 8, 27, 19, 0, tzinfo=ET)),
    ],
)
def test_a_refused_request_pulls_nothing_and_writes_nothing(db, dates, now):
    _seed_prod_tape(db)
    before = _px_count(db)
    src = _Source()

    with pytest.raises(ScopeRefused):
        repair_benchmark_bars(db, dates, apply=True, source=src, now=now)

    assert _px_count(db) == before and src.calls == []


# --- what is skipped ----------------------------------------------------------------------------------------


def test_a_matching_bar_is_skipped_and_an_unpassed_date_is_untouched(db):
    """08-20 matches the vendor on every field for both benchmarks: nothing appended. And the out-of-scope
    08-21 SPY bar (a volume-only vendor revision: 39,030,400 stored vs 39,188,700) is never touched by a
    repair of other dates — only the --dates passed are in scope."""
    ids = _seed_prod_tape(db)
    before = _px_count(db)

    same = repair_benchmark_bars(db, [D20], apply=True, source=_Source(), now=NOW)

    assert same.appended == 0 and {b.action for b in same.bars} == {MATCHES}
    assert _px_count(db) == before

    repair_benchmark_bars(db, [D25, D27], apply=True, source=_Source(), now=NOW)

    (only,) = _versions(db, ids["SPY"], D21)
    assert int(only["volume"]) == 39030400


def test_a_date_the_vendor_has_no_bar_for_is_reported_and_skipped(db):
    """Thanksgiving 2025 (REAL payload: the vendor has no 11-27 bar) is skipped, never fabricated; the
    approved date beside it still repairs."""
    _seed_prod_tape(db)
    before = _px_count(db)
    spy = parse_yahoo_chart(_FIX["SPY"]["thanksgiving_2025"]) + _final("SPY")
    src = _Source({"SPY": spy, "IWM": _final("IWM")})

    result = repair_benchmark_bars(db, [THANKSGIVING_2025, D25], apply=True, source=src, now=NOW)

    plan = _by_key(result)
    assert plan[("SPY", THANKSGIVING_2025)].action == NO_VENDOR_BAR
    assert plan[("IWM", THANKSGIVING_2025)].action == NO_VENDOR_BAR
    assert result.appended == 2 and _px_count(db) == before + 2


def test_a_date_with_no_stored_bar_is_not_filled():
    """A hole is a backfill, not a repair: the planner skips it rather than writing a first version."""
    stored = {r["d"]: r for r in _stored("SPY") if r["d"] != D27}
    sid = uuid4()

    b25, b27 = plan_repairs("SPY", sid, [D25, D27], vendor=_final("SPY"), stored=stored)

    assert b25.action == APPEND and b27.action == NO_STORED_BAR and b27.security_id == sid


def test_a_failed_vendor_pull_writes_nothing_on_apply(db):
    _seed_prod_tape(db)
    before = _px_count(db)

    result = repair_benchmark_bars(
        db, [D25, D27], apply=True, source=_Source(fail=("IWM",)), now=NOW
    )

    assert result.fetch_errors == {"IWM": "RuntimeError: yahoo 502"}
    assert result.appended == 0 and _px_count(db) == before  # all-or-nothing


def test_the_repair_resolves_the_benchmarks_read_only_and_never_seeds_them(db):
    """No benchmark rows (an unseeded store): the repair reports them missing and inserts no master row."""
    result = repair_benchmark_bars(db, [D25], apply=True, source=_Source(), now=NOW)

    assert result.missing == ["SPY", "IWM"] and result.bars == []
    assert benchmark_ids(db) == {}


def test_the_vendor_pull_is_live_force_refreshed_into_a_throwaway_cache(db, monkeypatch):
    """The default source is the seam's Yahoo adapter at the benchmark leg's range, pulled live with
    force_refresh — and its cache dir is a temp dir that is gone afterwards (the shared cache is untouched).
    """
    _seed_prod_tape(db)
    src = _Source()
    made: list[dict] = []

    def _yahoo(**kw):
        made.append(kw)
        assert kw["cache_dir"].is_dir()
        return src

    monkeypatch.setattr(RB, "YahooPriceSource", _yahoo)

    repair_benchmark_bars(db, [D25], now=NOW)

    assert len(made) == 1 and made[0]["range_"] == DEFAULT_RANGE
    assert not made[0]["cache_dir"].exists()  # removed on return
    assert sorted(src.calls) == [("IWM", True, True), ("SPY", True, True)]


# --- the CLI ----------------------------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """The CLI against the test DB, the vendor stubbed with the REAL payload, provenance into tmp."""
    src = _Source()
    monkeypatch.setattr(RB, "YahooPriceSource", lambda **kw: src)
    log_dir = tmp_path / "repairs"
    monkeypatch.setattr(RB, "_DEFAULT_LOG_DIR", log_dir)
    seen: dict = {}
    real = RB.repair_benchmark_bars

    def _spy(conn, dates, **kw):
        seen["read_only"] = conn.read_only
        return real(conn, dates, **kw)

    monkeypatch.setattr(RB, "repair_benchmark_bars", _spy)
    return log_dir, seen


def test_the_cli_dry_run_is_read_only_and_writes_no_provenance(db, cli, capsys):
    log_dir, seen = cli
    _seed_prod_tape(db)
    before = _px_count(db)

    RB.main(["--dates", "2026-08-25,2026-08-27"])

    assert seen["read_only"] is True  # the dry run's connection physically cannot write
    assert _px_count(db) == before and not log_dir.exists()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "SUMMARY: would append 4" in out
    assert "765.270020" in out and "765.909973" in out


def test_the_cli_apply_appends_four_and_writes_the_provenance(db, cli, capsys):
    log_dir, seen = cli
    ids = _seed_prod_tape(db)
    before = _px_count(db)

    RB.main(["--dates", "2026-08-25,2026-08-27", "--apply"])

    assert seen["read_only"] is False
    assert _px_count(db) == before + 4
    (path,) = list(log_dir.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["applied"] is True and payload["summary"]["appended"] == 4
    got = {(a["ticker"], a["d"]): a for a in payload["appended"]}
    assert set(got) == {(t, d.isoformat()) for t, d in FROZEN}
    spy25 = got[("SPY", "2026-08-25")]
    assert spy25["old"]["close"] == pytest.approx(765.270020, abs=1e-6)
    assert spy25["new"]["close"] == pytest.approx(765.909973, abs=1e-6)
    assert spy25["old"]["recorded_at"] == "2026-08-25T00:00:00+00:00"
    assert spy25["security_id"] == str(ids["SPY"]) and spy25["valid_from"] == "2026-08-25"
    stamp = datetime.fromisoformat(spy25["recorded_at"])
    assert _versions(db, ids["SPY"], D25)[-1]["recorded_at"] == stamp
    assert "SUMMARY: appended 4" in capsys.readouterr().out

    RB.main(["--dates", "2026-08-25,2026-08-27", "--apply"])  # a re-apply

    assert _px_count(db) == before + 4  # COUNT the table: zero more
    assert "SUMMARY: appended 0" in capsys.readouterr().out


def test_the_cli_apply_exits_75_while_the_lock_is_held(db, cli, capsys):
    log_dir, _ = cli
    _seed_prod_tape(db)
    before = _px_count(db)
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            RB.main(["--dates", "2026-08-25,2026-08-27", "--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        assert "ALREADY RUNNING" in capsys.readouterr().out
        assert _px_count(db) == before and not log_dir.exists()
        # a dry run writes nothing, so it takes no lock and still runs
        RB.main(["--dates", "2026-08-25,2026-08-27"])
        assert _px_count(db) == before
    finally:
        held.release()


@pytest.mark.parametrize("raw", ["", "2026-08-22", "2026-08-25,2026-08-23"])
def test_the_cli_refuses_out_of_scope_dates_with_exit_2(db, cli, capsys, raw):
    log_dir, _ = cli
    _seed_prod_tape(db)
    before = _px_count(db)

    with pytest.raises(SystemExit) as exc:
        RB.main(["--dates", raw, "--apply"])

    assert exc.value.code == RB.EXIT_REFUSED == 2
    assert "REFUSED" in capsys.readouterr().out
    assert _px_count(db) == before and not log_dir.exists()


def test_the_cli_refuses_today(db, cli, capsys):
    """Today in market time (from the DB clock) is the nightly leg's, never a repair's."""
    log_dir, _ = cli
    today = db_now(db).astimezone(ET).date()
    db.rollback()

    with pytest.raises(SystemExit) as exc:
        RB.main(["--dates", today.isoformat(), "--apply"])

    assert exc.value.code == 2 and "today or later" in capsys.readouterr().out
