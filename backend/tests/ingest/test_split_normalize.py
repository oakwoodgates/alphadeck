"""The price leg's split normalization (S2, ``ingest.prices.split_normalize``) on REAL data: the stored tapes, the
vendor's real 2026-10-20 Yahoo pulls (the split-tape diagnosis's ``fetch_eod`` mirror), the real version
histories of the split nights. Pure — the DB-backed nightly passes live in ``test_ingest_split_list.py``.

Two cases are stated as CONTRACTS on real inputs with one stated edit each: "an adjusted pull" is the real pull
put on the list's scale (what a vendor re-base serves), and "a genuine correction" moves ONE real bar's close
by 2%. Every other value is used as extracted.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from domain.splits import applicable, pending
from ingest.prices.ingest_security import plan_overlap
from ingest.prices.restatement import REVERT, classify, decide
from ingest.prices.split_normalize import (
    REASON_FIRST_NIGHT,
    REASON_MIS_FACTORED,
    REASON_VENDOR_DOUBLED,
    REASON_VENDOR_RAW,
    normalize,
)
from pipeline import split_repair as sr
from pipeline.split_basis import TapeBar, tape_findings
from tests import split_s2_fixtures as fx

T = fx.TODAY


def _stored(bars) -> dict[date, dict]:
    return {b.d: {"close": b.close, "volume": b.volume} for b in bars}


def _repaired(tk: str, *, spot: bool = False, confirmed=()) -> list[sr.Bar]:
    sp = fx.splits(tk, confirmed=confirmed)
    c = sr.classify(
        fx.bars(tk),
        applicable(sp, on=T),
        today=T,
        transparent=pending(sp, on=T),
        spot=fx.spots(tk) if spot else None,
    )
    assert c.ok, c.refusals
    return sr.apply_labels(fx.bars(tk), c.labels)


SPOT = {"PBM": True}


@pytest.mark.parametrize("tk", ["BTOC", "OPTT", "BURU", "PBM", "VWAV"])
def test_before_the_repair_the_list_changes_nothing(tk):
    """The broken tapes are stored exactly as the vendor serves them, so deploying the loader BEFORE the repair
    is applied writes nothing new on them."""
    fresh = fx.yahoo(tk)
    stored = _stored(fx.bars(tk))
    norm = normalize(fresh, stored, applicable(fx.splits(tk), on=T))
    assert norm.total == 0
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert plan.old == []


@pytest.mark.parametrize(
    "tk,reason",
    [
        ("BTOC", REASON_VENDOR_RAW),
        ("OPTT", REASON_VENDOR_RAW),
        ("BURU", REASON_VENDOR_RAW),
        ("VWAV", REASON_VENDOR_RAW),
        ("PBM", REASON_VENDOR_DOUBLED),
    ],
)
def test_after_the_repair_the_vendor_pull_writes_nothing(tk, reason):
    fresh = fx.yahoo(tk)
    stored = _stored(_repaired(tk, spot=SPOT.get(tk, False)))
    norm = normalize(fresh, stored, applicable(fx.splits(tk), on=T))
    assert set(norm.scaled) == {reason} and norm.total > 0
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert (plan.holes, plan.recent, plan.old) == ([], [], [])


@pytest.mark.parametrize("tk", ["BTOC", "OPTT"])
def test_without_the_list_the_repair_is_undone_on_night_two(tk):
    """The regression the list fixes (diagnosis section 6): the vendor's raw values go back to versions the
    store held, so T5 reads a REVERT, holds it one night and applies it on the second."""
    fresh = fx.yahoo(tk)
    before = {b.d: b for b in fx.bars(tk)}
    stored = _stored(_repaired(tk))
    plan = plan_overlap(fresh, stored, last=max(stored))
    assert len(plan.old) > 400
    superseded = {
        r["d"]: [{"close": before[r["d"]].close, "volume": before[r["d"]].volume}] for r in plan.old
    }
    kind, _ = classify(plan.old, superseded)
    assert kind == REVERT
    covered = {r["d"] for r in fresh}
    n1 = decide(kind, old=plan.old, pending=None, covered=covered, pull_date=T, refreshed=True)
    assert not n1.apply
    n2 = decide(
        kind,
        old=plan.old,
        pending=n1.state,
        covered=covered,
        pull_date=T + timedelta(days=1),
        refreshed=True,
    )
    assert n2.apply  # undone


def test_an_adjusted_pull_passes_as_is():
    """CONTRACT (the real pull on the list's scale — what a vendor re-base serves): the same scale as the
    repaired store, so nothing is rescaled and nothing restated."""
    tk = "BTOC"
    repaired = {b.d: b for b in _repaired(tk)}
    raw = {b.d: b for b in fx.bars(tk)}
    fresh = []
    for r in fx.yahoo(tk):
        k = repaired[r["d"]].close / raw[r["d"]].close if r["d"] in raw else 1.0
        fresh.append(
            {
                **r,
                **{f: r[f] * k for f in ("open", "high", "low", "close") if r[f] is not None},
                "volume": None if r["volume"] is None else r["volume"] / k,
            }
        )
    stored = _stored(repaired.values())
    norm = normalize(fresh, stored, applicable(fx.splits(tk), on=T))
    assert norm.total == 0
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert (plan.recent, plan.old) == ([], [])


def test_a_genuine_one_bar_correction_still_lands_on_the_lists_scale():
    """CONTRACT (one real bar's close moved +2%): read on the store's scale, then T6 sees a 2% move and the
    bar re-versions — on the adjusted scale, never back to the raw one."""
    tk = "OPTT"
    fresh = fx.yahoo(tk)
    target = date(2026, 3, 2)
    edited = [{**r, "close": r["close"] * 1.02} if r["d"] == target else dict(r) for r in fresh]
    stored = _stored(_repaired(tk))
    norm = normalize(edited, stored, applicable(fx.splits(tk), on=T))
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert [r["d"] for r in plan.old] == [target]
    assert plan.old[0]["close"] == pytest.approx(stored[target]["close"] * 1.02, rel=1e-9)


def test_a_consistent_vendor_mis_factor_is_divided_back():
    """ATRO after its repair: the vendor serves exactly what it served before (its 0.7981 adjustment — the
    stored pre-repair tape IS the vendor's series, MEASURED identical on every name the diagnosis pulled), so
    every pre-06-15 bar is off the repaired store by the same x0.957701: divided back, nothing restated.
    """
    tk = "ATRO"
    vendor = [
        {
            "d": b.d,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in fx.bars(tk)
        if b.d >= T - timedelta(days=730)
    ]
    stored = _stored(_repaired(tk, spot=True, confirmed=("ATRO",)))
    norm = normalize(vendor, stored, applicable(fx.splits(tk, confirmed=("ATRO",)), on=T))
    assert set(norm.scaled) == {REASON_MIS_FACTORED}
    assert norm.scaled[REASON_MIS_FACTORED] == sum(1 for r in vendor if r["d"] < date(2026, 6, 15))
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert (plan.recent, plan.old) == ([], [])


# --- the first night of a split, replayed from the real version history --------------------------------------


def _night(tk: str, first_session: date):
    """The store just before the pass that stored ``tk``'s first post-split bar, and the vendor's series that
    pass served (the store mirrored the vendor bar for bar before T6/T5, 2026-10-17)."""
    vs = fx.versions(tk)
    pass_at = min(rec for r, rec in vs if r["d"] >= first_session)
    before: dict[date, dict] = {}
    served: dict[date, dict] = {}
    for r, rec in sorted(vs, key=lambda x: x[1]):
        if rec < pass_at:
            before[r["d"]] = r
        if rec <= pass_at + timedelta(hours=1):
            served[r["d"]] = r
    since = pass_at.date() - timedelta(days=730)
    fresh = [
        {k: (float(v) if k != "d" and v is not None else v) for k, v in served[d].items()}
        for d in sorted(served)
        if d >= since
    ]
    stored = {
        d: {"close": float(r["close"]), "volume": float(r["volume"] or 0)}
        for d, r in before.items()
    }
    return pass_at, stored, fresh


@pytest.mark.parametrize(
    "tk,first_session,k",
    [
        ("VRME", date(2026, 9, 29), 10),
        ("YYAI", date(2026, 8, 17), 20),
        ("LRHC", date(2026, 9, 8), 6),
    ],
)
def test_first_night_the_raw_history_is_put_on_the_new_scale(tk, first_session, k):
    """The real split night: the vendor served the raw history plus the first post-split bar (VRME's became a
    +1,276% fake breakout arm). With the split in force that night, the pre-split bars are scaled by k, so the
    first session is no longer a split-sized step."""
    pass_at, stored, fresh = _night(tk, first_session)
    in_force = [
        s for s in applicable(fx.splits(tk), on=pass_at.date()) if s.effective_date <= first_session
    ]
    assert any(s.effective_date == first_session for s in in_force)
    norm = normalize(fresh, stored, in_force)
    assert norm.first_night and set(norm.scaled) == {REASON_FIRST_NIGHT}
    pre = [b for b in norm.bars if b["d"] < first_session]
    post = [b for b in norm.bars if b["d"] >= first_session]
    step = post[0]["close"] / pre[-1]["close"]
    assert 1 / 3 < step < 3  # the split no longer reads as a breakout
    raw_step = post[0]["close"] / stored[max(d for d in stored if d < first_session)]["close"]
    assert raw_step > 0.6 * k  # what the store held: the x6..x15 "breakout"
    # and the night after: the store is now on the new scale and the same raw history is absorbed
    after = {b["d"]: {"close": b["close"], "volume": b["volume"]} for b in norm.bars}
    nxt = normalize(fresh, after, in_force)
    plan = plan_overlap(nxt.bars, after, last=max(after))
    assert (plan.recent, plan.old) == ([], []) and set(nxt.scaled) == {REASON_VENDOR_RAW}


def test_first_night_with_the_vendor_adjusted_passes_and_the_flip_back_is_absorbed():
    """BTOC's real passes: the vendor re-based the history the night BEFORE the 10-15 first session (not yet in
    force — it lands as the vendor's honest re-base), served it adjusted on the first night (nothing to do), and
    flipped it back to raw on 10-16 — 499 bars that landed raw. With the list, that flip is read as unadjusted
    and put back on the store's scale. MEASURED: the vendor's raw and adjusted series also differ BEYOND the
    factor on 11 of those bars (different volume sources: 02-26 adjusted 71,079 vs raw 7,612,900 / 16); those
    re-version through T6 as ordinary corrections — on the adjusted scale, never back to raw."""
    vs = fx.versions("BTOC")
    passes = sorted({rec.replace(minute=0, second=0, microsecond=0) for _, rec in vs})
    flip = next(p for p in passes if p.date() == date(2026, 10, 17))
    store: dict[date, dict] = {}
    served: dict[date, dict] = {}
    for r, rec in sorted(vs, key=lambda x: x[1]):
        if rec < flip:
            store[r["d"]] = {"close": float(r["close"]), "volume": float(r["volume"])}
        if rec < flip + timedelta(hours=1):
            served[r["d"]] = r
    fresh = [
        {k: (float(v) if k != "d" and v is not None else v) for k, v in served[d].items()}
        for d in sorted(served)
        if d >= date(2024, 10, 17)
    ]
    on = datetime(2026, 10, 16, tzinfo=timezone.utc).date()
    without = plan_overlap(fresh, store, last=max(store))
    assert len(without.old) + len(without.recent) >= 400  # what happened: the flip landed
    norm = normalize(fresh, store, applicable(fx.splits("BTOC"), on=on))
    assert set(norm.scaled) == {REASON_VENDOR_RAW} and norm.total >= 490
    plan = plan_overlap(norm.bars, store, last=max(store))
    assert plan.recent == [] and len(plan.old) <= 11
    for r in plan.old:  # no scale change: each is within x1.25 of the stored adjusted close
        assert 0.8 < r["close"] / store[r["d"]]["close"] < 1.25


def test_the_split_night_tape_is_t4_clean_after_the_first_night():
    pass_at, stored, fresh = _night("VRME", date(2026, 9, 29))
    in_force = [s for s in applicable(fx.splits("VRME"), on=pass_at.date())]
    norm = normalize(fresh, stored, in_force)
    findings = tape_findings([TapeBar(b["d"], b["close"], b["volume"]) for b in norm.bars])
    assert not [f for f in findings if f.kind == "cliff"]
