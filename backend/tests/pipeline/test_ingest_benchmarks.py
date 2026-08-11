"""Signals §2.1 — the benchmark backfill (SPY/IWM). Offline: a fake PriceSource stands in for Yahoo,
the DB is real (the ``db`` fixture, against alphadeck_test). This is the first-in-family ingest tax:
the KNOWABILITY stamp (``valid_from = recorded_at = the bar date``, so a past-as-of replay sees each bar
only on its own date) + the count-the-table idempotency gate (the store dedups on read, so a duplicate
append would hide behind a correct read while the table silently grows)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from pipeline.ingest_benchmarks import ingest_benchmarks
from securities.benchmarks import BENCHMARKS, seed_benchmarks

_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)


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


def test_benchmark_bars_have_no_lookahead_and_recorded_at_is_the_bar_date(db):
    """The knowability fixture (R17/A.2). A bar dated ``d`` is invisible to an as-of read at ``T < d`` and
    visible at ``T >= d`` (valid-time); AND ``recorded_at`` is stamped at ``d`` (NOT ``now()``), so a read
    pinned in the past — the day OF the bar, well before "today" — still sees it (transaction-time).
    """
    d = date(2026, 3, 2)
    src = _FakeSource({"SPY": _bars([d]), "IWM": []})
    spy = {r.ticker: r for r in ingest_benchmarks(db, allow_live=False, source=src)}["SPY"]
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
