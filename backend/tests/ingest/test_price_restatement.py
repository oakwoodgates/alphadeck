"""Q11 (operator decision 2026-10-17) — the price leg's old-bar TOLERANCE (T6) and revert HYSTERESIS (T5).

The shapes are REAL, read-only from prod's ``fact_price_eod`` version history (2026-10-17):

- **BTOC** did a 1-for-16 reverse split effective 2026-10-14. The vendor served the adjusted history (A) on
  10-14 — the store's first-time re-base — then the unadjusted one (U) again on 10-16, the night T4 flagged
  a x13 cliff. ``U`` / ``A`` / ``BAR14_FIRST`` .. ``BAR16`` below are those stored values, exactly.
- **BTOC's late settling**: its 10-14 bar was first stored on 12,592 shares and re-served on 13,069 the
  next night (+3.79%), and its 10-13 close was stored 0.1804 then 0.18 (-0.22%): the real sizes of the
  wiggles T6 stops re-versioning on OLDER bars (and keeps re-versioning inside the trailing window).
- **MPU**'s 2026-10-14 flip back to the adjusted basis: two of the bars matched the adjusted values stored
  on 09-15, two did not because the vendor re-rounded them (12.934 -> 13.0 = +0.51%; 3.94 -> 4.0) — the
  measured MIXED set.

Every DB test COUNTS THE TABLE before and after (the read dedups; a duplicate append hides behind it).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.prices import restatement as R
from ingest.prices.eod_loader import ingest_prices, stored_bars
from ingest.prices.ingest_security import (
    _moved,
    _restated,
    ingest_bars_for_security,
    load_hold,
    plan_overlap,
)
from signals.base import PointInTimeData

ET = ZoneInfo("America/New_York")


def _at(d: date, hh: int, mm: int) -> datetime:
    """A market-time wall clock (aware) — the pass's ``now`` (the settle guard + the T5 pull date)."""
    return datetime.combine(d, time(hh, mm), tzinfo=ET)


def _bar(d: date, close: float, volume: float) -> dict:
    return {"d": d, "open": close, "high": close, "low": close, "close": close, "volume": volume}


def _bars(series: dict[date, tuple[float, float]]) -> list[dict]:
    return [_bar(d, c, v) for d, (c, v) in sorted(series.items())]


# --- the real BTOC shapes ---------------------------------------------------------------------------------
D07, D08, D09, D12, D13 = (date(2026, 10, d) for d in (7, 8, 9, 12, 13))
D14, D15, D16 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)
D17 = date(2026, 10, 17)
U = {  # the unadjusted basis, as first stored
    D07: (0.216000005602837, 124200),
    D08: (0.216999992728233, 126600),
    D09: (0.215000003576279, 38800),
    D12: (0.199000000953674, 126400),
    D13: (0.180000007152557, 77800),
}
A = {  # the adjusted basis the vendor served on 10-14 (x16 price, /16 volume)
    D07: (3.44799995422363, 7765),
    D08: (3.46560001373291, 7913),
    D09: (3.44176006317139, 2922),
    D12: (3.18560004234314, 7898),
    D13: (2.88639998435974, 5915),
}
BAR14_FIRST = (2.91680002212524, 12592)  # 10-14 as first served (on 10-14)
BAR14 = (2.89919996261597, 13069)  # ... and as re-served on 10-15 (late settling, +3.79% volume)
BAR15 = (3.04999995231628, 22492)
BAR16 = (2.82999992370605, 25300)

PULL_1014 = {**A, D14: BAR14_FIRST}
PULL_1015 = {**A, D14: BAR14, D15: BAR15}
PULL_1016 = {**U, D14: BAR14, D15: BAR15, D16: BAR16}  # the flip back to unadjusted
PULL_BOUNCE = {**A, D14: BAR14, D15: BAR15, D16: BAR16}  # ...and back to adjusted again


class _Source:
    """A canned PriceSource — the seam makes the pass testable without a network."""

    def __init__(self, series: dict[date, tuple[float, float]]):
        self.bars = _bars(series)

    def get_bars(self, ticker: str, *, allow_live: bool = True, force_refresh: bool = False):
        return self.bars


def _sec(security_id) -> Security:
    return Security(id=security_id, tenant_id=DEFAULT_TENANT_ID, ticker="BTOC", name="BTOC")


def _count(db, security_id) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (security_id,)
        )
        return cur.fetchone()["n"]


def _holds(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM price_restatement_hold")
        return cur.fetchone()["n"]


def _pass(db, security_id, series, now: datetime, *, refreshed: bool = True):
    """One pass of the price leg over a canned pull at ``now``; ``refreshed`` = the recurring path's
    ``force_refresh`` + ``allow_live`` (a cache-first read when False)."""
    res = ingest_bars_for_security(
        db,
        _sec(security_id),
        tenant_id=DEFAULT_TENANT_ID,
        allow_live=True,
        force_refresh=refreshed,
        source=_Source(series),
        now=now,
    )
    db.commit()
    return res


def _seed_unadjusted(db, security_id) -> None:
    """The store before the split: U, each bar first stored the night of its session."""
    for d, (c, v) in sorted(U.items()):
        ingest_prices(db, security_id, [_bar(d, c, v)], recorded_at=_at(d, 22, 40))
    db.commit()


def _latest(db, security_id, d: date) -> tuple[float, float]:
    bar = stored_bars(db, security_id)[d]
    return float(bar["close"]), float(bar["volume"])


def _on_basis(db, security_id, basis: dict) -> bool:
    return all(_latest(db, security_id, d) == basis[d] for d in basis)


# --- the pure rules (direct contract inputs) ----------------------------------------------------------------


def test_the_trailing_window_is_the_pulls_newest_three_session_dates():
    """D1's count, reused: the vendor's own session dates, so a weekend (10-10/10-11) is simply not a date."""
    assert R.trailing_window_start(_bars(PULL_1016)) == D14
    assert R.trailing_window_start(_bars({D09: U[D09], D12: U[D12], D13: U[D13]})) == D09
    assert R.trailing_window_start(_bars({D13: U[D13]})) == D13
    assert R.trailing_window_start([]) is None


@pytest.mark.parametrize(
    ("close", "volume", "moved"),
    [
        (200.98, 1_000_000, False),  # close +0.49% — below the 0.5% tolerance
        (201.0, 1_000_000, True),  # +0.5% — AT the tolerance re-versions
        (199.0, 1_000_000, True),  # -0.5% either way
        (200.0, 1_049_000, False),  # volume +4.9%
        (200.0, 1_050_000, True),  # volume +5%
        (200.0, 950_000, True),  # volume -5%
    ],
)
def test_the_old_bar_tolerance_boundaries(close, volume, moved):
    stored = {"close": 200.0, "volume": 1_000_000}
    assert R.restated_old({"close": close, "volume": volume}, stored) is moved


def test_zero_and_absent_values_keep_the_exact_rule():
    """A stored zero has no relative scale: it moves only on a nonzero value (``_moved``'s handling)."""
    assert not R.moved_beyond({"volume": 0}, {"volume": 0}, "volume", R.VOLUME_TOL)
    assert R.moved_beyond({"volume": 100}, {"volume": 0}, "volume", R.VOLUME_TOL)
    assert not R.moved_beyond({"volume": None}, {"volume": None}, "volume", R.VOLUME_TOL)
    assert R.moved_beyond({"volume": None}, {"volume": 5}, "volume", R.VOLUME_TOL)
    assert R.moved_beyond({"volume": 5}, {"volume": None}, "volume", R.VOLUME_TOL)


def test_the_shared_exact_compare_is_untouched():
    """``_moved`` / ``_restated`` stay EXACT: the Q3 benchmark repair compares explicit dates with them
    and the SPY/IWM leg's window uses ``_restated``. The tolerance lives only in ``restated_old``.
    """
    stored = {"close": 0.1804, "volume": 77745}
    fresh = {"close": 0.18, "volume": 77800}  # BTOC 10-13's real settle wiggle (-0.22%, +0.07%)
    assert _moved(fresh, stored, "close") and _restated(fresh, stored)
    assert not R.restated_old(fresh, stored)


def test_classify_a_set_is_a_revert_only_when_every_bar_goes_back():
    old = [_bar(d, *U[d]) for d in (D07, D08)]
    superseded = {D07: [{"close": U[D07][0], "volume": U[D07][1]}], D08: []}
    assert R.classify(old, superseded) == (R.FIRST_TIME, 1)  # D08 was never held
    superseded[D08] = [{"close": U[D08][0], "volume": U[D08][1]}]
    assert R.classify(old, superseded) == (R.REVERT, 2)
    assert R.classify([], superseded) == (None, 0)


def test_agreement_ignores_a_date_the_vendors_window_dropped_but_not_a_changed_mind():
    held = R.HeldRevert(D16, {d: {"close": c, "volume": v} for d, (c, v) in U.items()})
    old = [_bar(d, *U[d]) for d in U]
    assert R.agrees(held, old, covered=set(U))
    # the 2y window's oldest bar fell off overnight: compared on what tonight's pull still covers
    assert R.agrees(held, old[1:], covered=set(U) - {D07})
    # a bar the held pull restated and tonight's pull does not: the vendor is still changing its mind
    assert not R.agrees(held, old[1:], covered=set(U))
    # a value off by more than the tolerance
    moved = [_bar(D07, U[D07][0] * 1.01, U[D07][1])] + old[1:]
    assert not R.agrees(held, moved, covered=set(U))


# --- T5 + T6 through the price leg (DB) ------------------------------------------------------------------


def test_a_first_time_split_rebase_applies_in_the_same_pass(db, security_id):
    """BTOC 10-14: the window bars (10-12, 10-13) re-version under the exact compare and the OLDER ones
    (10-07..10-09) carry values the store never held — first-time, applied in the SAME pass, so the tape
    is whole on the new basis the night the post-split bar lands. A re-run appends zero."""
    _seed_unadjusted(db, security_id)
    before = _count(db, security_id)
    res = _pass(db, security_id, PULL_1014, _at(D14, 22, 49))
    assert (res.appended, res.reversioned, res.held) == (1, 5, 0)
    assert _count(db, security_id) == before + 6
    assert _on_basis(db, security_id, A) and _holds(db) == 0

    again = _pass(db, security_id, PULL_1014, _at(D14, 23, 30))
    assert (again.appended, again.reversioned, again.held) == (0, 0, 0)
    assert _count(db, security_id) == before + 6  # COUNT the table


def test_a_flip_back_is_held_until_a_later_nights_pull_agrees(db, security_id):
    """BTOC 10-16: the vendor re-serves the unadjusted history. Every older restated bar goes back to a
    value the store held (U) — a REVERT — so the whole set is held: the tape stays on A (no x16 cliff) and
    only the tail lands. A second pull the SAME night never confirms; the next night's agreeing pull
    stores all five at once. COUNT the table at every step."""
    _seed_unadjusted(db, security_id)
    _pass(db, security_id, PULL_1014, _at(D14, 22, 49))
    late = _pass(db, security_id, PULL_1015, _at(D15, 22, 39))
    assert (late.appended, late.reversioned) == (1, 1)  # 10-14's late volume, inside the window
    n = _count(db, security_id)

    flip = _pass(db, security_id, PULL_1016, _at(D16, 22, 43))
    assert (flip.appended, flip.reversioned, flip.held) == (1, 0, 5)
    assert _count(db, security_id) == n + 1  # the 10-16 tail only
    assert _on_basis(db, security_id, A)  # never partial: every older bar still on A
    hold = load_hold(db, security_id, tenant_id=DEFAULT_TENANT_ID)
    assert hold is not None and hold.pull_date == D16 and set(hold.bars) == set(U)

    same_night = _pass(db, security_id, PULL_1016, _at(D16, 23, 30))
    assert (same_night.appended, same_night.reversioned, same_night.held) == (0, 0, 5)
    assert _count(db, security_id) == n + 1
    assert load_hold(db, security_id, tenant_id=DEFAULT_TENANT_ID).pull_date == D16

    with db.cursor() as cur:  # the pin comes from the clock that stamps recorded_at (Postgres)
        cur.execute("SELECT clock_timestamp() AS t")
        before_confirm = cur.fetchone()["t"]
    db.commit()
    agreed = _pass(db, security_id, PULL_1016, _at(D17, 22, 45))
    assert (agreed.appended, agreed.reversioned, agreed.held) == (0, 5, 0)
    assert _count(db, security_id) == n + 6
    assert _on_basis(db, security_id, U) and _holds(db) == 0

    # no-lookahead: the five confirmed versions carry the DB's now() of the confirming pass — never a
    # backdated stamp — so a replay pinned before the confirmation still sees the adjusted basis
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n, count(DISTINCT recorded_at) AS stamps FROM fact_price_eod "
            "WHERE security_id = %s AND d <= %s AND recorded_at > %s",
            (security_id, D13, before_confirm),
        )
        stamped = cur.fetchone()
    assert (stamped["n"], stamped["stamps"]) == (5, 1)
    then = PointInTimeData(db, asof=D16, known_at=before_confirm, tenant_id=DEFAULT_TENANT_ID)
    now = PointInTimeData(db, asof=D16, tenant_id=DEFAULT_TENANT_ID)
    assert float(then.price_history(security_id)[0]["close"]) == A[D07][0]
    assert float(now.price_history(security_id)[0]["close"]) == U[D07][0]

    rerun = _pass(db, security_id, PULL_1016, _at(D17, 23, 30))
    assert (rerun.appended, rerun.reversioned, rerun.held) == (0, 0, 0)
    assert _count(db, security_id) == n + 6  # idempotent


def test_a_flap_that_bounces_back_leaves_the_store_untouched(db, security_id):
    """A -> B -> A -> B: the held revert (U on 10-16) is never stored when the next pull serves A again —
    the store stays on A, nothing is appended and the hold clears."""
    _seed_unadjusted(db, security_id)
    _pass(db, security_id, PULL_1014, _at(D14, 22, 49))
    _pass(db, security_id, PULL_1015, _at(D15, 22, 39))
    _pass(db, security_id, PULL_1016, _at(D16, 22, 43))
    n = _count(db, security_id)
    assert _holds(db) == 1

    back = _pass(db, security_id, PULL_BOUNCE, _at(D17, 22, 45))
    assert (back.appended, back.reversioned, back.held) == (0, 0, 0)
    assert _count(db, security_id) == n
    assert _on_basis(db, security_id, A) and _holds(db) == 0


def test_a_cache_first_read_can_neither_start_nor_confirm_a_hold(db, security_id):
    """The Workbench's pull is cache-first: it re-reads a pull that already happened. It still holds a
    revert (never stores it) but writes no hold state, and it cannot confirm one a refreshed pass started.
    """
    _seed_unadjusted(db, security_id)
    _pass(db, security_id, PULL_1014, _at(D14, 22, 49))
    _pass(db, security_id, PULL_1015, _at(D15, 22, 39))
    n = _count(db, security_id)

    cached = _pass(db, security_id, PULL_1016, _at(D16, 22, 43), refreshed=False)
    assert (cached.appended, cached.held) == (1, 5) and _holds(db) == 0
    assert _on_basis(db, security_id, A)

    _pass(db, security_id, PULL_1016, _at(D16, 23, 0))  # the refreshed pass starts the hold
    assert _holds(db) == 1
    reread = _pass(db, security_id, PULL_1016, _at(D17, 9, 30), refreshed=False)
    assert (reread.reversioned, reread.held) == (0, 5)
    assert _count(db, security_id) == n + 1 and _holds(db) == 1


def test_the_three_session_boundary_and_real_settle_wiggles(db, security_id):
    """A pull through 10-13 (no new bar): the window is 10-09 / 10-12 / 10-13. The SAME real wiggles —
    BTOC's late volume settling (+3.79%) and its close settle (-0.22%) — re-version the window bars and
    NOT the older ones (10-07, 10-08)."""
    _seed_unadjusted(db, security_id)
    before = _count(db, security_id)
    vol_settle = BAR14[1] / BAR14_FIRST[1]  # +3.79%, measured
    close_settle = 0.180000007152557 / 0.180399999022484  # -0.22%, measured
    wiggled = dict(U)
    wiggled[D08] = (
        U[D08][0],
        U[D08][1] * vol_settle,
    )  # older bar: the newest one outside the window
    wiggled[D09] = (U[D09][0], U[D09][1] * vol_settle)  # window bar: the oldest one inside
    wiggled[D07] = (U[D07][0] * close_settle, U[D07][1])  # older
    wiggled[D12] = (U[D12][0] * close_settle, U[D12][1])  # window
    res = _pass(db, security_id, wiggled, _at(D13, 22, 45))
    assert (res.appended, res.reversioned, res.held) == (0, 2, 0)
    assert _count(db, security_id) == before + 2
    # numeric stores 15 significant digits of a float8, so a computed wiggle reads back to ~1e-15
    assert _latest(db, security_id, D09) == pytest.approx(wiggled[D09], rel=1e-12)
    assert _latest(db, security_id, D12) == pytest.approx(wiggled[D12], rel=1e-12)
    assert _latest(db, security_id, D08) == U[D08] and _latest(db, security_id, D07) == U[D07]


@pytest.mark.parametrize(
    ("close", "volume", "reversioned"),
    [(200.98, 1_000_000, 0), (201.0, 1_000_000, 1), (200.0, 1_049_000, 0), (200.0, 1_050_000, 1)],
)
def test_the_tolerance_boundaries_through_the_price_leg(
    db, security_id, close, volume, reversioned
):
    """0.49% vs 0.5% close and 4.9% vs 5% volume on an OLDER bar (three newer sessions behind it)."""
    old = date(2026, 10, 6)
    ingest_prices(db, security_id, [_bar(old, 200.0, 1_000_000)], recorded_at=_at(old, 22, 40))
    for d in (D07, D08, D09):
        ingest_prices(db, security_id, [_bar(d, 200.0, 1_000_000)], recorded_at=_at(d, 22, 40))
    db.commit()
    before = _count(db, security_id)
    series = {old: (close, volume), **{d: (200.0, 1_000_000) for d in (D07, D08, D09)}}
    res = _pass(db, security_id, series, _at(D09, 22, 45))
    assert res.reversioned == reversioned and res.held == 0
    assert _count(db, security_id) == before + reversioned


# --- the MIXED set (MPU, 2026-10-14) -------------------------------------------------------------------

M_0306, M_0307 = date(2025, 3, 6), date(2025, 3, 7)
M_0910, M_0911 = date(2026, 9, 10), date(2026, 9, 11)
# each older bar's stored versions, oldest first: unadjusted (as first stored), adjusted (served 09-15),
# unadjusted again (the 09-17 flip, which the store applied) — and what the vendor served on 10-14
MPU_VERSIONS = {
    M_0306: [(0.649999976158142, 126400), (13.0, 6318), (0.649999976158142, 126400)],
    M_0307: [(0.649999976158142, 71200), (12.9340000152588, 3557), (0.649999976158142, 71200)],
    M_0910: [(0.189999997615814, 149500), (3.80900001525879, 7482), (0.189999997615814, 149500)],
    M_0911: [(0.200000002980232, 169200), (3.94000005722046, 8461), (0.200000002980232, 169200)],
}
MPU_1014 = {
    M_0306: (13.0, 6320),  # matches the 09-15 adjusted version (+0.03% volume) -> a revert
    M_0307: (13.0, 3560),  # 12.934 re-rounded to 13.0: +0.51% -> a value never held
    M_0910: (3.79999995231628, 7475),  # -0.24% close, -0.09% volume -> a revert
    M_0911: (4.0, 8460),  # 3.94 -> 4.0: +1.5% -> a value never held
}
MPU_POST = {  # post-split bars, unchanged by the flip
    date(2026, 9, 16): (3.24000000953674, 49100),
    date(2026, 9, 17): (3.14000010490417, 39700),
    date(2026, 10, 13): (2.70000004768372, 5300),
}
MPU_TAIL = {date(2026, 10, 14): (2.49000000953674, 3400)}
_VERSION_AT = [_at(date(2026, 9, 3), 22, 41), _at(date(2026, 9, 15), 22, 43)]
_VERSION_AT.append(_at(date(2026, 9, 17), 22, 42))


def _seed_mpu(db, security_id) -> None:
    for d, versions in MPU_VERSIONS.items():
        for (c, v), at in zip(versions, _VERSION_AT, strict=True):
            ingest_prices(db, security_id, [_bar(d, c, v)], recorded_at=at)
    for d, (c, v) in MPU_POST.items():
        ingest_prices(db, security_id, [_bar(d, c, v)], recorded_at=_at(d, 22, 42))
    db.commit()


def test_a_mixed_set_is_first_time_and_is_stored_whole(db, security_id):
    """MPU 10-14: two older bars go back to the adjusted values stored on 09-15, two carry values the
    store never held (the vendor re-rounded them). One never-held value makes the set FIRST-TIME: all four
    are stored in this pass — never two of them."""
    _seed_mpu(db, security_id)
    before = _count(db, security_id)
    res = _pass(db, security_id, {**MPU_1014, **MPU_POST, **MPU_TAIL}, _at(D14, 22, 49))
    assert (res.appended, res.reversioned, res.held) == (1, 4, 0)
    assert _count(db, security_id) == before + 5
    assert all(_latest(db, security_id, d) == MPU_1014[d] for d in MPU_1014) and _holds(db) == 0


def test_an_all_revert_set_is_held_whole(db, security_id):
    """The same set with every bar re-served EXACTLY as stored on 09-15: all four revert, so none is
    stored this pass (never part of it) and every older bar keeps its latest stored version."""
    _seed_mpu(db, security_id)
    before = _count(db, security_id)
    reverted = {d: versions[1] for d, versions in MPU_VERSIONS.items()}
    res = _pass(db, security_id, {**reverted, **MPU_POST, **MPU_TAIL}, _at(D14, 22, 49))
    assert (res.appended, res.reversioned, res.held) == (1, 0, 4)
    assert _count(db, security_id) == before + 1
    assert all(_latest(db, security_id, d) == MPU_VERSIONS[d][-1] for d in MPU_VERSIONS)


def test_plan_overlap_splits_holes_window_and_older_bars():
    """Pure: a hole is always stored, a window bar is compared exactly, an older bar under T6."""
    stored = {
        d: {"close": c, "volume": v} for d, (c, v) in U.items() if d != D08
    }  # D08 never stored
    fresh = _bars({**U, D07: (U[D07][0] * 1.001, U[D07][1]), D13: (U[D13][0] * 1.001, U[D13][1])})
    plan = plan_overlap(fresh, stored, last=D13)
    assert [r["d"] for r in plan.holes] == [D08]
    assert [r["d"] for r in plan.recent] == [D13]  # +0.1% inside the window: exact compare
    assert plan.old == []  # +0.1% on an older bar: below the tolerance


# --- visibility: the run artifact + the one quiet line ----------------------------------------------------


def test_a_hold_rides_the_artifact_and_prints_one_quiet_line_per_name(tmp_path, capsys):
    from pipeline.cron_run_log import write_cron_run_log
    from pipeline.daily import ThesisRunResult
    from pipeline.ingest_thesis import NameResult, print_held_lines

    sid = uuid4()

    def name(held: int, sec=None) -> NameResult:
        return NameResult(
            "BTOC", sec or sid, form4_appended=0, price_bars_appended=1, price_bars_held=held
        )

    results = [
        ThesisRunResult(thesis_id=uuid4(), name="Bitcoin", ingested=[name(5), name(0, uuid4())]),
        ThesisRunResult(thesis_id=uuid4(), name="Crypto", ingested=[name(5)]),
    ]
    path = write_cron_run_log(
        results,
        asof=D16,
        allow_live=True,
        started_at=_at(D16, 22, 30).astimezone(timezone.utc),
        finished_at=_at(D16, 22, 50).astimezone(timezone.utc),
        base_dir=tmp_path,
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [t["price_bars_held"] for t in doc["theses"]] == [5, 5]

    print_held_lines(x for r in results for x in r.ingested)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln]
    assert len(lines) == 1  # deduped across the two theses holding the same name
    assert lines[0].startswith("BTOC: 5 older price bar(s) held")
