"""Signals §2.1 — the benchmark tape (SPY/IWM). Offline: a fake PriceSource stands in for Yahoo, the DB is
real (the ``db`` fixture, against alphadeck_test). The ingest tax: the KNOWABILITY stamp — since D1 two of
them: ``recorded_at = now`` on the RECURRING path (the nightly leg; honest transaction time) and
``valid_from = recorded_at = the bar date`` only for the one-shot historical backfill CLI (so a past-as-of
replay sees deep history on its own dates) — the D2 settle guard, the trailing-window restatement
re-version, and the count-the-table idempotency gate (the store dedups on read, so a duplicate append
would hide behind a correct read while the table silently grows).

The D1/D2 tests run on REAL data at FIXED instants: ``tests/fixtures/prices/benchmark_tape_2026_08.json``
holds the vendor's own chart payloads and the rows prod actually stored for SPY 2026-08-20..08-28 (its
``_provenance`` key says how each was cut).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.bitemporal import as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof, serve_known_at
from ingest.prices.eod_loader import ingest_prices_backfill, parse_yahoo_chart, stored_bars
from pipeline import ingest_benchmarks as IB
from pipeline.ingest_benchmarks import ingest_benchmarks, plan_benchmark_bars
from securities.benchmarks import BENCHMARKS, seed_benchmarks
from signals.base import PointInTimeData

_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)
ET = ZoneInfo("America/New_York")
_FIX = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "benchmark_tape_2026_08.json"
    ).read_text(encoding="utf-8")
)
D24, D25, D26, D27, D28 = (date(2026, 8, d) for d in (24, 25, 26, 27, 28))


def _bars(dates: list[date]) -> list[dict]:
    return [
        {"d": d, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0} for d in dates
    ]


class _FakeSource:
    """A PriceSource stub returning controlled bars per ticker (or delegating to a fn that may raise)."""

    def __init__(self, bars_by_ticker: dict[str, list[dict]] | None = None, fn=None):
        self._bars = bars_by_ticker or {}
        self._fn = fn

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        if self._fn is not None:
            return self._fn(ticker)
        return list(self._bars.get(ticker, []))


def _px_count(db, *, tenant=DEFAULT_TENANT_ID) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) n FROM fact_price_eod WHERE tenant_id = %s", (tenant,))
        return cur.fetchone()["n"]


# --- the seeder: idempotent identity rows, NOT basket members ---------------------------------------


def test_seed_benchmarks_is_idempotent_and_not_a_basket_member(db):
    ids1 = seed_benchmarks(db)
    db.commit()
    ids2 = seed_benchmarks(db)  # a re-seed inserts nothing
    db.commit()

    assert ids1 == ids2  # same rows resolved, never re-inserted
    assert set(ids1) == set(BENCHMARKS)
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) n FROM security_master "
            "WHERE instrument_kind = 'etf' AND ticker = ANY(%s)",
            (list(BENCHMARKS),),
        )
        assert cur.fetchone()["n"] == len(BENCHMARKS)  # exactly one row per benchmark
        # a benchmark is reference data, never a basket member (#2 — it floats free of any thesis)
        cur.execute(
            "SELECT count(*) n FROM basket_member WHERE security_id = ANY(%s)",
            (list(ids1.values()),),
        )
        assert cur.fetchone()["n"] == 0


# --- the backfill: lands bars, stamped at the bar date ----------------------------------------------


def test_backfill_lands_bars_for_each_benchmark(db):
    d1, d2 = date(2026, 3, 2), date(2026, 3, 3)
    src = _FakeSource({"SPY": _bars([d1, d2]), "IWM": _bars([d1])})

    by = {r.ticker: r for r in ingest_benchmarks(db, allow_live=False, source=src)}

    assert by["SPY"].bars_appended == 2 and by["SPY"].error is None
    assert by["IWM"].bars_appended == 1 and by["IWM"].error is None
    assert _px_count(db) == 3


def test_the_one_shot_backfill_backdates_recorded_at_to_the_bar_date(db):
    """The knowability fixture (R17/A.2) — now the HISTORICAL-BACKFILL path only (``historical_backfill
    =True``, what the CLI passes). A bar dated ``d`` is invisible to an as-of read at ``T < d`` and visible
    at ``T >= d`` (valid-time); AND ``recorded_at`` is stamped at ``d`` (NOT ``now()``), so a read pinned in
    the past — the day OF the bar, well before "today" — still sees it (transaction-time).
    """
    d = date(2026, 3, 2)
    src = _FakeSource({"SPY": _bars([d]), "IWM": []})
    spy = {
        r.ticker: r
        for r in ingest_benchmarks(db, allow_live=False, source=src, historical_backfill=True)
    }["SPY"]
    sid = spy.security_id

    def read(*, asof, known_at):
        return as_of(
            db,
            "fact_price_eod",
            security_id=sid,
            asof=asof,
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
        )

    # axis 1 (event time / valid_from = d): invisible the day before, visible on the bar date
    assert read(asof=d - timedelta(days=1), known_at=_FAR_FUTURE) == []
    assert len(read(asof=d, known_at=_FAR_FUTURE)) == 1

    # axis 2 (transaction time / recorded_at = d 00:00 UTC, NOT now()): with the event time long past,
    # a read pinned the day BEFORE the bar cannot see it, but one pinned ON the bar date can — proof the
    # backfill backdated recorded_at rather than stamping it "today" (the no-backfill trap).
    asof = date(2026, 12, 31)
    on_d = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    assert read(asof=asof, known_at=on_d - timedelta(days=1)) == []
    assert len(read(asof=asof, known_at=on_d)) == 1
    # and a known_at well before "today" (but after the bar date) still sees it — not stamped now()
    assert len(read(asof=asof, known_at=datetime(2026, 3, 3, tzinfo=timezone.utc))) == 1


# --- idempotency + incremental ----------------------------------------------------------------------


def test_rerun_appends_zero_rows_count_the_table(db):
    """THE idempotency gate: a second identical run appends NOTHING — verified by COUNTING the table
    (the read dedups, so a duplicate append would hide behind a correct read while the table grows).
    """
    src = _FakeSource(
        {"SPY": _bars([date(2026, 3, 2), date(2026, 3, 3)]), "IWM": _bars([date(2026, 3, 2)])}
    )
    ingest_benchmarks(db, allow_live=False, source=src)
    before = _px_count(db)

    results = ingest_benchmarks(db, allow_live=False, source=src)  # identical second run

    assert _px_count(db) == before  # the TABLE did not grow
    assert all(r.bars_appended == 0 for r in results)


def test_incremental_appends_only_new_bars(db):
    src1 = _FakeSource({"SPY": _bars([date(2026, 3, 2)]), "IWM": _bars([date(2026, 3, 2)])})
    ingest_benchmarks(db, allow_live=False, source=src1)
    base = _px_count(db)

    # a later run: SPY gains one new bar, IWM is unchanged (its bar re-served)
    src2 = _FakeSource(
        {"SPY": _bars([date(2026, 3, 2), date(2026, 3, 3)]), "IWM": _bars([date(2026, 3, 2)])}
    )
    by = {r.ticker: r for r in ingest_benchmarks(db, allow_live=False, source=src2)}

    assert by["SPY"].bars_appended == 1  # only the 03-03 bar (d > latest)
    assert by["IWM"].bars_appended == 0
    assert _px_count(db) == base + 1


def test_one_bad_benchmark_does_not_abort_the_other(db):
    """Fail-visible + per-benchmark isolation: one benchmark whose pull raises is captured and skipped;
    the other still lands."""

    def fn(ticker):
        if ticker == "IWM":
            raise RuntimeError("yahoo 500")
        return _bars([date(2026, 3, 2)])

    by = {r.ticker: r for r in ingest_benchmarks(db, allow_live=False, source=_FakeSource(fn=fn))}

    assert by["SPY"].bars_appended == 1 and by["SPY"].error is None
    assert by["IWM"].bars_appended == 0 and by["IWM"].error and "yahoo 500" in by["IWM"].error
    assert _px_count(db) == 1  # only SPY's bar landed


# --- D1: the RECURRING path stamps honest transaction time ------------------------------------------


def _spy(cut: str, *, through: date | None = None) -> list[dict]:
    rows = parse_yahoo_chart(_FIX["SPY"][cut])
    return [r for r in rows if through is None or r["d"] <= through]


def _iwm(cut: str, *, through: date | None = None) -> list[dict]:
    rows = parse_yahoo_chart(_FIX["IWM"][cut])
    return [r for r in rows if through is None or r["d"] <= through]


def _rows(db, sid) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT d, close, volume, recorded_at FROM fact_price_eod WHERE security_id = %s "
            "ORDER BY d, recorded_at",
            (sid,),
        )
        return cur.fetchall()


def _by_ticker(results) -> dict:
    return {r.ticker: r for r in results}


def test_the_recurring_path_stamps_recorded_at_from_the_database_clock(db):
    """The DEFAULT (the nightly leg's call) no longer backdates: ``recorded_at`` is the instant the bar was
    learned, read from the DATABASE clock — bracketed by ``db_now`` before and after the pass — never the
    bar date's midnight (which predates its own session)."""
    d = date(2026, 3, 2)
    src = _FakeSource({"SPY": _bars([d]), "IWM": []})
    t0 = db_now(db)
    db.commit()

    spy = _by_ticker(ingest_benchmarks(db, allow_live=False, source=src))["SPY"]

    t1 = db_now(db)
    db.commit()
    (row,) = _rows(db, spy.security_id)
    assert t0 <= row["recorded_at"] <= t1
    assert row["recorded_at"] != datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def test_a_bar_ingested_at_22_35_ET_is_visible_to_that_nights_call_and_not_before(db):
    """THE PIT pin (D1). The nightly leg ingests 2026-08-24 at 22:35 ET, then 2026-08-25 at 22:35 ET (REAL
    vendor closes). The 08-25 bar is stamped exactly that instant — and it is visible to every honest
    reader of asof 08-25 (the night's own call, the serve-path scrub-back cap at the end of the market day)
    while invisible to a read of an earlier day, and to a read pinned before it was ingested."""
    night24 = datetime(2026, 8, 24, 22, 35, tzinfo=ET)
    night25 = datetime(2026, 8, 25, 22, 35, tzinfo=ET)
    ingest_benchmarks(
        db,
        allow_live=False,
        source=_FakeSource({"SPY": _spy("final", through=D24), "IWM": []}),
        now=night24,
    )
    before = _px_count(db)

    spy = _by_ticker(
        ingest_benchmarks(
            db,
            allow_live=False,
            source=_FakeSource({"SPY": _spy("final", through=D25), "IWM": []}),
            now=night25,
        )
    )["SPY"]

    assert spy.bars_appended == 1 and spy.bars_restated == 0
    assert _px_count(db) == before + 1  # COUNT the table: exactly the one new bar
    rows = [r for r in _rows(db, spy.security_id) if r["d"] == D25]
    assert len(rows) == 1 and rows[0]["recorded_at"] == night25  # honest transaction time, pinned

    def latest_d(*, asof, known_at):
        bars = PointInTimeData(db, asof=asof, known_at=known_at).benchmark_prices("SPY")
        return bars[-1]["d"] if bars else None

    # the night's own call (known_at = a moment after the leg) sees it
    assert latest_d(asof=D25, known_at=datetime(2026, 8, 25, 22, 40, tzinfo=ET)) == D25
    # the scrub-back cap (end of D's MARKET day — known_at_for_asof / serve_known_at) sees it
    assert latest_d(asof=D25, known_at=known_at_for_asof(D25, now=_FAR_FUTURE)) == D25
    assert latest_d(asof=D25, known_at=serve_known_at(D25, today=D26)) == D25
    # ...but NOT before it was ingested (transaction time) — the old D 00:00 UTC stamp leaked here
    assert latest_d(asof=D25, known_at=datetime(2026, 8, 25, 22, 34, tzinfo=ET)) == D24
    assert latest_d(asof=D25, known_at=datetime(2026, 8, 25, 12, 0, tzinfo=ET)) == D24
    # ...and never to a read of an EARLIER day, at any transaction time (valid time)
    assert latest_d(asof=D24, known_at=_FAR_FUTURE) == D24


def test_the_cli_is_the_one_caller_that_backdates(monkeypatch):
    """``python -m pipeline.ingest_benchmarks`` is the explicit one-shot historical backfill: it asks for
    the backdated stamp (the nightly leg's call site asks for none — pinned in ``test_daily``)."""
    seen: dict = {}

    class _Conn:
        def close(self):
            pass

    def _fake(conn, **k):
        seen.update(k)
        return []

    monkeypatch.setattr(IB, "connect", lambda: _Conn())
    monkeypatch.setattr(IB, "ingest_benchmarks", _fake)

    IB.main(["--no-live"])

    assert seen["historical_backfill"] is True


# --- D2: the settle guard on the benchmark leg -------------------------------------------------------


def test_a_daytime_benchmark_pass_withholds_todays_bar_and_the_night_stores_it(db):
    """The measured failure, both benchmarks: a pass at 12:07 ET on 2026-08-25 saw SPY at 765.27 and IWM at
    298.665 mid-session. Now neither is stored; the 22:35 pass appends the FINAL closes as ordinary tail
    bars (765.91 / 299.23). COUNT the table at each step."""
    day = _by_ticker(
        ingest_benchmarks(
            db,
            allow_live=False,
            source=_FakeSource(
                {"SPY": _spy("midsession_2026_08_25"), "IWM": _iwm("midsession_2026_08_25")}
            ),
            now=datetime(2026, 8, 25, 12, 7, tzinfo=ET),
        )
    )
    assert all((r.bars_appended, r.bars_withheld) == (3, 1) for r in day.values())
    assert _px_count(db) == 6
    assert all(D25 not in stored_bars(db, r.security_id) for r in day.values())

    night = _by_ticker(
        ingest_benchmarks(
            db,
            allow_live=False,
            source=_FakeSource(
                {"SPY": _spy("final", through=D25), "IWM": _iwm("final", through=D25)}
            ),
            now=datetime(2026, 8, 25, 22, 35, tzinfo=ET),
        )
    )
    assert all(
        (r.bars_appended, r.bars_restated, r.bars_withheld) == (1, 0, 0) for r in night.values()
    )
    assert _px_count(db) == 8
    assert float(stored_bars(db, night["SPY"].security_id)[D25]["close"]) == pytest.approx(
        765.909973
    )
    assert float(stored_bars(db, night["IWM"].security_id)[D25]["close"]) == pytest.approx(
        299.230011
    )


# --- D1: the restatement re-version, bounded to a trailing window ------------------------------------


def _prod_stored() -> list[dict]:
    return [{**r, "d": date.fromisoformat(r["d"])} for r in _FIX["SPY"]["stored_prod_2026_08"]]


def _seed_prod_tape(db):
    """Store SPY 2026-08-20..08-28 EXACTLY as prod held it (one version each, backdated to the bar date by
    the old nightly leg): the 08-25/08-27 closes frozen intraday, the nightly volumes pre-revision.
    """
    sid = seed_benchmarks(db)["SPY"]
    ingest_prices_backfill(db, sid, _prod_stored())
    db.commit()
    return sid


def _final_pass(db, *, now, **kw):
    return _by_ticker(
        ingest_benchmarks(
            db,
            allow_live=False,
            source=_FakeSource({"SPY": _spy("final"), "IWM": []}),
            now=now,
            **kw,
        )
    )["SPY"]


def test_restatement_reversions_inside_the_window_and_leaves_older_bars_alone(db):
    """REAL data, window = 3 sessions at the 08-28 night: the vendor's final 08-26 volume, 08-27 close and
    08-28 volume are re-versioned (each a NEW row stamped now); the frozen 08-25 close and the revised
    08-21/08-24 volumes sit OUTSIDE the window and are left exactly as stored — that is the Q3 repair,
    not a nightly side effect. COUNT the table."""
    sid = _seed_prod_tape(db)
    base = _px_count(db)
    now = datetime(2026, 8, 28, 22, 35, tzinfo=ET)

    spy = _final_pass(db, now=now, window_sessions=3)

    assert (spy.bars_appended, spy.bars_restated) == (0, 3)
    assert _px_count(db) == base + 3
    latest = stored_bars(db, sid)
    assert float(latest[D27]["close"]) == pytest.approx(771.099976)  # inside: restated
    assert float(latest[D26]["volume"]) == 28751600 and float(latest[D28]["volume"]) == 36744300
    assert float(latest[D25]["close"]) == pytest.approx(765.270020)  # outside: untouched (Q3)
    assert float(latest[date(2026, 8, 21)]["volume"]) == 39030400  # outside: untouched
    new = [r for r in _rows(db, sid) if r["recorded_at"] == now]
    assert sorted(r["d"] for r in new) == [D26, D27, D28]


def test_the_default_window_of_5_reaches_the_frozen_close_4_sessions_back(db):
    """The setting's default (5 sessions): at the 08-28 night the window is 08-24..08-28, so the frozen
    08-25 close IS inside — the same bar a 3-session window left alone. The boundary is the dial."""
    sid = _seed_prod_tape(db)
    base = _px_count(db)

    spy = _final_pass(db, now=datetime(2026, 8, 28, 22, 35, tzinfo=ET))

    # 08-24 vol, 08-25 close+vol, 08-26 vol, 08-27 close+vol, 08-28 vol — 08-20 equal, 08-21 outside
    assert spy.bars_restated == 5 and _px_count(db) == base + 5
    assert float(stored_bars(db, sid)[D25]["close"]) == pytest.approx(765.909973)
    assert float(stored_bars(db, sid)[date(2026, 8, 21)]["volume"]) == 39030400


def test_a_restatement_rerun_appends_zero_and_a_pinned_replay_still_sees_the_old_version(db):
    """Idempotent (count the table) and transaction-time honest: a read pinned BEFORE the re-version still
    sees the frozen 765.27 the record saw; a read after sees the vendor's final 765.91."""
    sid = _seed_prod_tape(db)
    first = datetime(2026, 8, 28, 22, 35, tzinfo=ET)
    _final_pass(db, now=first)
    after_first = _px_count(db)

    again = _final_pass(db, now=datetime(2026, 8, 31, 22, 35, tzinfo=ET))

    assert (again.bars_appended, again.bars_restated) == (0, 0)
    assert _px_count(db) == after_first

    def close_on_25(known_at):
        (row,) = [
            r
            for r in as_of(
                db,
                "fact_price_eod",
                security_id=sid,
                asof=D28,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
            )
            if r["d"] == D25
        ]
        return float(row["close"])

    assert close_on_25(first - timedelta(minutes=1)) == pytest.approx(765.270020)
    assert close_on_25(first) == pytest.approx(765.909973)


def test_a_restatement_on_the_backfill_path_is_stamped_now_never_backdated(db):
    """Even the one-shot CLI stamps a CORRECTION ``now``: a backdated new version would claim the record
    knew the corrected value on 08-25 (and would collide with the backdated original on the unique key).
    """
    sid = _seed_prod_tape(db)
    now = datetime(2026, 8, 28, 22, 35, tzinfo=ET)

    spy = _final_pass(db, now=now, historical_backfill=True)

    assert spy.error is None and spy.bars_restated == 5
    v25 = [r["recorded_at"] for r in _rows(db, sid) if r["d"] == D25]
    assert v25 == [datetime(2026, 8, 25, tzinfo=timezone.utc), now]


# --- the pure planner --------------------------------------------------------------------------------


def test_plan_window_zero_disables_the_compare_and_keeps_the_tail():
    stored = {r["d"]: r for r in _prod_stored() if r["d"] <= D27}

    plan = plan_benchmark_bars(
        _spy("final"), stored, now=datetime(2026, 8, 28, 22, 35, tzinfo=ET), window_sessions=0
    )

    assert [r["d"] for r in plan.tail] == [D28]
    assert plan.restated == [] and plan.holes == [] and plan.window == []


def test_plan_fills_a_hole_inside_the_window_but_not_outside_it():
    """A date the store is missing is a FIRST version, filled only inside the window (an older one is left
    for an explicit backfill, like every other out-of-window difference)."""
    stored = {r["d"]: r for r in _prod_stored() if r["d"] not in (date(2026, 8, 21), D26)}

    plan = plan_benchmark_bars(
        _spy("final"), stored, now=datetime(2026, 8, 28, 22, 35, tzinfo=ET), window_sessions=5
    )

    assert plan.window == [D24, D25, D26, D27, D28]
    assert [r["d"] for r in plan.holes] == [D26]
    assert plan.tail == []
