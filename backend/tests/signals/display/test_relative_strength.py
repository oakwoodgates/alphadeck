"""Signals §2.1/§1.3 — benchmark relative strength. The pure RS math (a fresh 13-week high IS the
leadership tell; a flat or fading RS is NOT; thin/absent benchmark tape is an honest UNKNOWN, never a
fabricated ratio, #9), plus the ``PointInTimeData.benchmark_prices`` accessor (as-of capped, no
lookahead) and the §1.3 supersector rollup."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from signals.base import PointInTimeData
from signals.display import relative_strength as rs
from signals.display.relative_strength import _aligned_rs, compute, sector_rs_for

_START = date(2026, 1, 1)
_MIN = rs.MIN_BARS  # the aligned-bar floor for a 13-week-high verdict


def _bars(closes: list[float], start: date = _START) -> list[dict]:
    """Ascending EOD bars on consecutive days from ``start`` (only ``d`` + ``close``, all compute reads)."""
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


# --- the pure RS math -------------------------------------------------------------------------------


def test_aligned_rs_intersects_dates_and_drops_nonpositive_benchmark():
    member = _bars([10.0, 20.0, 30.0])  # d0, d1, d2
    bench = _bars([5.0, 0.0, 15.0])  # d1's benchmark close is 0 -> that date is dropped (no ÷0)
    out = _aligned_rs(member, bench)
    assert [(d, round(v, 4)) for d, v in out] == [(member[0]["d"], 2.0), (member[2]["d"], 2.0)]


def test_rs_at_a_fresh_13w_high_fires_the_leadership_tell():
    n = _MIN + 5
    member = _bars([100.0 + i for i in range(n)])  # strictly rising
    bench = _bars([50.0] * n)  # flat market -> RS rises to a fresh high at the last bar
    sig = compute(member, {"SPY": bench, "IWM": bench}, member[-1]["d"])

    assert sig is not None and sig.kind == "relative_strength"
    assert sig.headline.key == "leading"
    assert {e.key for e in sig.events} == {"rs_high_spy", "rs_high_iwm"}
    assert all(e.date == member[-1]["d"] and e.direction == "up" for e in sig.events)
    by = {m.key: m for m in sig.metrics}
    assert by["rs_spy"].value == round((100.0 + n - 1) / 50.0, 4) and by["rs_spy"].note is None


def test_rs_below_its_high_does_not_fire():
    up = [100.0 + i for i in range(40)]
    down = [140.0 - i for i in range(1, 26)]  # peak in the middle, fading into the as-of bar
    member = _bars(up + down)  # 65 bars
    bench = _bars([50.0] * 65)
    sig = compute(member, {"SPY": bench, "IWM": bench}, member[-1]["d"])

    assert sig.events == []  # not at a fresh high
    assert sig.headline.key == "inline"
    assert all(m.value is not None for m in sig.metrics)  # the ratio is still shown (honest)


def test_flat_rs_never_leads():
    """A name that moves EXACTLY with the market (RS constant) is not leading — the strict fresh-high
    test (not >=) is what keeps a flat RS from falsely firing."""
    bench_closes = [50.0 + i for i in range(_MIN + 5)]
    member = _bars([2.0 * c for c in bench_closes])  # member = 2 × benchmark -> RS ≡ 2.0
    bench = _bars(bench_closes)
    sig = compute(member, {"SPY": bench, "IWM": bench}, member[-1]["d"])

    assert sig.events == [] and sig.headline.key == "inline"
    assert {m.key: m.value for m in sig.metrics} == {"rs_spy": 2.0, "rs_iwm": 2.0}


def test_thin_history_is_unknown_not_a_false_high():
    n = _MIN - 30  # fewer than MIN_BARS aligned bars -> no 13-week-high verdict
    member = _bars([100.0 + i for i in range(n)])
    bench = _bars([50.0] * n)
    sig = compute(member, {"SPY": bench, "IWM": bench}, member[-1]["d"])

    assert sig.events == [] and sig.headline.key == "inline"
    by = {m.key: m for m in sig.metrics}
    assert by["rs_spy"].value is not None  # the ratio is honestly shown
    assert "13w-high n/a" in by["rs_spy"].note and f"{n}/{_MIN}" in by["rs_spy"].note


def test_no_benchmark_tape_is_absent_not_fabricated():
    member = _bars([100.0 + i for i in range(_MIN + 5)])
    assert compute(member, {"SPY": [], "IWM": []}, member[-1]["d"]) is None
    assert compute(member, {}, member[-1]["d"]) is None  # missing entirely -> still absent


def test_partial_benchmark_shows_one_and_marks_the_other():
    n = _MIN + 5
    member = _bars([100.0 + i for i in range(n)])
    bench = _bars([50.0] * n)
    sig = compute(member, {"SPY": bench, "IWM": []}, member[-1]["d"])

    assert sig is not None
    by = {m.key: m for m in sig.metrics}
    assert by["rs_spy"].value is not None
    assert by["rs_iwm"].value is None and "no aligned IWM bars" in by["rs_iwm"].note


def test_no_member_bars_is_none():
    assert compute([], {"SPY": _bars([50.0] * (_MIN + 5))}, _START) is None


def test_benchmarks_tuple_mirrors_the_seed():
    """The display seam cannot import ``securities``; its ``BENCHMARKS`` tuple is hand-kept equal to the
    seeder's — this drift-guard is the safety net (the ``rvol`` dial-mirror idiom)."""
    from securities.benchmarks import BENCHMARKS as SEEDED

    assert set(rs.BENCHMARKS) == set(SEEDED)


# --- the PointInTimeData.benchmark_prices accessor (DB-backed, no-lookahead) -------------------------


def _pxrow(d: date, close: float) -> dict:
    return {"d": d, "open": None, "high": None, "low": None, "close": close, "volume": None}


def test_benchmark_prices_accessor_resolves_and_caps_as_of(db, security_id):
    from ingest.prices.eod_loader import ingest_prices
    from securities.benchmarks import seed_benchmarks

    ids = seed_benchmarks(db)
    ingest_prices(
        db, ids["SPY"], [_pxrow(date(2026, 5, 1), 100.0), _pxrow(date(2026, 6, 10), 110.0)]
    )
    db.commit()

    pit = PointInTimeData(db, asof=date(2026, 6, 1))
    bars = pit.benchmark_prices("SPY")
    assert [b["d"] for b in bars] == [
        date(2026, 5, 1)
    ]  # the 06-10 bar is post-asof -> invisible (#1)
    assert pit.benchmark_prices("NOPE") == []  # no such benchmark -> honest empty, never an error


def test_display_reads_the_benchmark_and_honors_no_lookahead(db, security_id):
    """End-to-end through the PIT: a rising member vs a flat SPY/IWM prints a fresh 13-week RS high —
    and a post-asof spike bar is invisible, so the high is the as-of bar, never a lookahead blowout.
    """
    from ingest.prices.eod_loader import ingest_prices
    from securities.benchmarks import seed_benchmarks

    ids = seed_benchmarks(db)
    asof = date(2026, 6, 1)
    start = asof - timedelta(days=_MIN + 4)
    member = [_pxrow(start + timedelta(days=i), 100.0 + i) for i in range(_MIN + 5)]
    flat = [_pxrow(start + timedelta(days=i), 50.0) for i in range(_MIN + 5)]
    ingest_prices(db, security_id, member)
    ingest_prices(db, ids["SPY"], flat)
    ingest_prices(db, ids["IWM"], flat)
    ingest_prices(db, security_id, [_pxrow(asof + timedelta(days=1), 9_999.0)])  # future spike
    db.commit()

    sig = rs.display(PointInTimeData(db, asof=asof), security_id, asof)
    assert sig is not None and sig.headline.key == "leading"
    assert {e.key for e in sig.events} == {"rs_high_spy", "rs_high_iwm"}
    assert sig.basis.window_end == asof  # the future 9,999 bar never entered the window


# --- §1.3 the supersector rollup --------------------------------------------------------------------


class _FakePit:
    """A minimal DisplayPointInTimeData: fixed benchmark bars + per-security member bars (no db)."""

    asof = date(2026, 6, 1)

    def __init__(self, bench: list[dict], member_bars: dict):
        self._bench = bench
        self._members = member_bars

    def benchmark_prices(self, symbol, lookback_days=None):
        return self._bench

    def price_history(self, security_id, lookback_days=None):
        return self._members.get(security_id, [])


def test_sector_rs_rollup_counts_leaders_by_supersector():
    n = _MIN + 5
    bench = _bars([50.0] * n)
    lead = _bars([100.0 + i for i in range(n)])  # RS rising -> a fresh high (leader)
    flat = _bars([100.0] * n)  # RS constant 2.0 -> counted but not leading
    a, b, c = uuid4(), uuid4(), uuid4()
    pit = _FakePit(bench, {a: lead, b: lead, c: flat})
    supersector = {a: "technology", b: "technology", c: "healthcare"}

    sig = sector_rs_for(pit, [a, b, c], supersector)

    assert sig is not None and sig.kind == "sector_rs"
    by = {m.key: m for m in sig.metrics}
    assert by["rs_lead_technology"].value == 2.0 and "2/2 leading" in by["rs_lead_technology"].note
    assert by["rs_lead_healthcare"].value == 0.0
    assert sig.metrics[0].key == "rs_lead_technology"  # leaders-first ordering
    assert sig.headline.key == "leading" and "technology" in sig.headline.label


def test_sector_rs_groups_unclassified_and_marks_thin():
    bench = _bars([50.0] * 70)
    thin = _bars([100.0 + i for i in range(_MIN - 30)])  # too few aligned bars -> no verdict
    sid = uuid4()
    pit = _FakePit(bench, {sid: thin})

    sig = sector_rs_for(pit, [sid], {sid: None})  # no supersector -> the "unclassified" group (#9)

    by = {m.key: m for m in sig.metrics}
    assert "rs_lead_unclassified" in by and "thin" in by["rs_lead_unclassified"].note
    assert sig.headline.key == "unknown"  # every member too thin -> no verdict at all


def test_sector_rs_none_without_members_or_benchmark():
    assert sector_rs_for(_FakePit([], {}), [], {}) is None  # no members
    assert (
        sector_rs_for(_FakePit([], {}), [uuid4()], {}) is None
    )  # no benchmark tape -> uncomputable
