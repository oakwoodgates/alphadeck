"""The price leg's split normalization (S2, ``ingest.prices.split_normalize``) on REAL data: the stored tapes, the
vendor's real 2026-10-20 Yahoo pulls (the split-tape diagnosis's ``fetch_eod`` mirror), the real version
histories of the split nights. Pure — the DB-backed nightly passes live in ``test_ingest_split_list.py``.

Some cases are stated as CONTRACTS on real inputs with stated edits, each named where it is used: "an adjusted
pull" is the real pull put on the list's scale (what a vendor re-base serves); "a genuine correction" moves ONE
real bar's close; "a raw store" puts a real tape's pre-split run back on the raw scale (close / k, volume x k —
the store a vendor-raw first night leaves when the first-night rule does not take; the B1 review's simulation);
"an unlisted split" leaves a real split off the list; "holes" drop real stored sessions; "a postponed split" lists
an announced split on a date nothing happened. Every other value is used as extracted.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from domain.splits import STATUS_ANNOUNCED, ListedSplit, applicable, pending
from ingest.prices.ingest_security import plan_overlap
from ingest.prices.restatement import REVERT, classify, decide
from ingest.prices.split_normalize import (
    REASON_FIRST_NIGHT,
    REASON_MIS_FACTORED,
    REASON_VENDOR_DOUBLED,
    REASON_VENDOR_RAW,
    STORE_CONTINUOUS,
    STORE_RAW,
    STORE_UNREADABLE,
    boundary,
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
    return _at(tk, min(rec for r, rec in vs if r["d"] >= first_session))


def _pass(tk: str, prefix: str):
    """The store just before the real pass recorded at ``prefix`` (an ISO prefix of its ``recorded_at``), and
    the vendor's series that pass served."""
    return _at(tk, min(rec for _, rec in fx.versions(tk) if rec.isoformat().startswith(prefix)))


def _at(tk: str, pass_at: datetime):
    vs = fx.versions(tk)
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
    # YYAI's never-stored 08-14 was served ALREADY adjusted (price x17.66 against the raw 08-13, volume
    # x0.046): left as served beside its rescaled neighbor, and counted (the re-review's N1)
    assert norm.as_served == (1 if tk == "YYAI" else 0)
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


# --- B1 (the invariant review, 2026-10-20): a genuine vendor re-base always lands ------------------------------


def _split(tk: str, eff: date) -> ListedSplit:
    return next(s for s in fx.splits(tk) if s.effective_date == eff)


def _served(tk: str) -> list[dict]:
    """The real stored tape as a vendor series (floats) — on the list's scale where the store is."""
    return [
        {k: (float(v) if k != "d" and v is not None else v) for k, v in r.items()}
        for r in fx.rows(tk)
    ]


def _raw_store(tk: str, eff: date, k: float, until: date | None = None) -> dict[date, dict]:
    """CONTRACT input (the stated edit): the real tape with every bar before ``until`` (default: the split
    ``eff``) put back on the raw scale — close / k, volume x k."""
    cut = until or eff
    out = {}
    for r in fx.rows(tk):
        c, v = float(r["close"]), float(r["volume"] or 0)
        out[r["d"]] = (
            {"close": c / k, "volume": v * k} if r["d"] < cut else {"close": c, "volume": v}
        )
    return out


@pytest.mark.parametrize(
    "tk,prefix,first_session",
    [
        ("VRME", "2026-10-07T02:4", date(2026, 9, 29)),  # the night Yahoo re-based VRME's 1-for-10
        ("YYAI", "2026-08-22T22:0", date(2026, 8, 17)),  # ... and YYAI's 1-for-20
    ],
)
def test_the_real_nights_the_vendor_re_based_a_raw_store_land(tk, prefix, first_session):
    """REAL nights, nothing edited: the store was raw at the split (the first nights came in unadjusted,
    before S2) and that night's pull carried the vendor's re-base. With the split in force the re-base lands
    whole — nothing is divided back as "applied twice" — and the store's boundary reads RAW (never
    CONTINUOUS), which is what the audit reports."""
    pass_at, stored, fresh = _pass(tk, prefix)
    s = _split(tk, first_session)
    assert boundary(stored, (), s)[0] == STORE_RAW
    in_force = applicable(fx.splits(tk), on=pass_at.date())
    norm = normalize(fresh, stored, in_force)
    assert REASON_VENDOR_DOUBLED not in norm.scaled and REASON_MIS_FACTORED not in norm.scaled
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    landed = [r for r in plan.old + plan.recent if r["d"] < first_session]
    assert len(landed) >= 9
    k = float(s.factor)
    for r in landed:
        assert r["close"] / stored[r["d"]]["close"] == pytest.approx(k, rel=0.05)


@pytest.mark.parametrize("tk,eff", [("KITT", date(2026, 9, 25)), ("LRHC", date(2026, 1, 26))])
def test_a_raw_store_the_vendor_re_based_is_never_divided_back(tk, eff):
    """The B1 finding, on the reviewer's two windows (CONTRACT: the real tape with the stated edit — the run
    before the split put back raw, the store a misread first night leaves; the vendor serves the real
    re-based tape). The old boundary misread both stores as "on scale" and divided the re-base back every
    night (MEASURED by the review: 14 of 14 bars ``vendor_doubled`` on each). Now: the boundary is never
    CONTINUOUS there, nothing is divided back, and every pre-split bar re-bases by the factor."""
    s = _split(tk, eff)
    k = float(s.factor)
    stored = _raw_store(tk, eff, k)
    assert boundary(stored, (), s)[0] in (STORE_RAW, STORE_UNREADABLE)
    fresh = _served(tk)
    norm = normalize(fresh, stored, applicable(fx.splits(tk), on=T))
    assert REASON_VENDOR_DOUBLED not in norm.scaled and REASON_MIS_FACTORED not in norm.scaled
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    moved = {r["d"]: r["close"] / stored[r["d"]]["close"] for r in plan.old + plan.recent}
    pre = [d for d in stored if d < eff]
    assert sorted(moved) == sorted(pre)
    assert all(m == pytest.approx(k, rel=1e-9) for m in moved.values())


def test_an_unlisted_later_split_of_the_same_ratio_re_bases_both_sides_of_the_listed_one():
    """The B1 review's second path, on LRHC's REAL serial pair (1-for-10 on 2026-01-26, again on 04-20).
    CONTRACT, two stated edits: the list as it stood before 04-20 was listed (07-07 and 01-26 only), and the
    store as it stood before the vendor's 04-20 re-base (every bar before 04-20 back on that raw scale). The
    vendor serves the real tape. The read says "01-26 applied twice" for every bar before 01-26, but the read
    does not change AT 01-26 (the bars after it are off by the same x10) — no local edge, nothing proven:
    the re-base lands on BOTH sides of 01-26, no cliff (the old rule divided 01-26's side back)."""
    b = date(2026, 4, 20)
    listed = [s for s in fx.splits("LRHC") if s.effective_date < b]
    stored = _raw_store("LRHC", b, 10.0)
    norm = normalize(_served("LRHC"), stored, applicable(listed, on=T))
    assert norm.total == 0
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    moved = {r["d"]: r["close"] / stored[r["d"]]["close"] for r in plan.old + plan.recent}
    assert sorted(moved) == sorted(d for d in stored if d < b)
    assert all(m == pytest.approx(10.0, rel=1e-9) for m in moved.values())
    assert any(d < date(2026, 1, 26) for d in moved) and any(
        date(2026, 1, 26) <= d < b for d in moved
    )


# --- B2: a read that only APPROXIMATES a scale is a correction, not a scale -----------------------------------


@pytest.mark.parametrize("pct", [1.15, 1.25, 0.85, 1.08])
def test_a_genuine_correction_under_a_small_factor_lands_as_served(pct):
    """ATRO's 5-for-6 (k = 0.8333, the stand-in confirm): a band so wide that +15% reads closer to "the vendor
    lacks the split" (x1.2) than to "the same scale". CONTRACT: the vendor serves the repaired tape (the list's
    scale) with ONE real close moved by ``pct``. The old rule wrote +15% as -4.2%; now no run under x1.8 is ever
    proven, so the bar lands exactly as the vendor corrected it."""
    stored = _stored(_repaired("ATRO", spot=True, confirmed=("ATRO",)))
    target = date(2026, 3, 2)
    fresh = [
        {
            "d": d,
            "open": b["close"],
            "high": b["close"],
            "low": b["close"],
            "close": b["close"],
            "volume": b["volume"],
        }
        | ({"close": b["close"] * pct} if d == target else {})
        for d, b in sorted(stored.items())
    ]
    norm = normalize(fresh, stored, applicable(fx.splits("ATRO", confirmed=("ATRO",)), on=T))
    assert norm.total == 0
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert [r["d"] for r in plan.old] == [target]
    assert plan.old[0]["close"] == pytest.approx(stored[target]["close"] * pct, rel=1e-12)


def test_a_correction_inside_a_proven_raw_run_lands_on_the_lists_scale():
    """The other side of B2 (the reason the 3% match is a run's 90%, not every bar's): BTOC's real raw series
    against its repaired store, with ONE raw close moved +15% (CONTRACT). The run is proven (495 of 496 bars
    equal the store once scaled), so the corrected bar is scaled with it and re-versions at +15% ON THE
    ADJUSTED SCALE — passing it as served would land a raw-scale bar, a one-bar x1/16 island."""
    stored = _stored(_repaired("BTOC"))
    target = date(2026, 3, 2)
    fresh = [
        {**r, "close": r["close"] * 1.15} if r["d"] == target else dict(r) for r in fx.yahoo("BTOC")
    ]
    norm = normalize(fresh, stored, applicable(fx.splits("BTOC"), on=T))
    assert set(norm.scaled) == {REASON_VENDOR_RAW}
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert [r["d"] for r in plan.old] == [target]
    assert plan.old[0]["close"] == pytest.approx(stored[target]["close"] * 1.15, rel=1e-6)


# --- B8: a bar the store never held takes its scale from a LOCAL neighbor --------------------------------------


def test_holes_in_a_proven_raw_run_take_the_runs_scale_through_local_neighbors():
    """CONTRACT (holes): BTOC's repaired store with five consecutive real sessions dropped (a vendor backfill /
    missed nights); the vendor serves its real raw series. Each hole is read against a decided neighbor at
    most two sessions away in the vendor's own series (chained), so every hole lands on the adjusted scale —
    none raw, none inferred across a long gap."""
    full = _stored(_repaired("BTOC"))
    ds = sorted(d for d in full if date(2026, 3, 2) <= d)[:5]
    stored = {d: b for d, b in full.items() if d not in ds}
    norm = normalize(fx.yahoo("BTOC"), stored, applicable(fx.splits("BTOC"), on=T))
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert sorted(r["d"] for r in plan.holes) == ds
    for r in plan.holes:
        assert r["close"] == pytest.approx(full[r["d"]]["close"], rel=1e-6)


def test_a_pump_day_in_a_hole_is_not_read_as_a_split():
    """The re-review's N1. CONTRACT (two stated edits): BURU's repaired store with 2025-06-11 never stored (a
    hole), and that day served as a genuine +420% pump — price x5.2, volume x5 — inside the vendor's raw run.
    Its ratio to the neighbor (x5.03) reads as BURU's 1-for-4.99 ahead, so the old rule stored the pump at a
    fifth of its scale. A basis change moves volume the OTHER way; a pump moves both up: the hole takes its
    neighbor's scale and lands as the pump on the store's scale."""
    full = _stored(_repaired("BURU"))
    d0 = date(2025, 6, 11)
    stored = {d: b for d, b in full.items() if d != d0}
    fresh = [
        {**r, "close": r["close"] * 5.2, "volume": r["volume"] * 5} if r["d"] == d0 else dict(r)
        for r in fx.yahoo("BURU")
    ]
    norm = normalize(fresh, stored, applicable(fx.splits("BURU"), on=T))
    assert norm.as_served == 0
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    (hole,) = [r for r in plan.holes if r["d"] == d0]
    assert hole["close"] == pytest.approx(full[d0]["close"] * 5.2, rel=1e-6)


def test_a_correction_too_big_to_read_inside_a_proven_run_takes_the_runs_scale():
    """The re-review's residual (b). CONTRACT: BTOC's real raw series against its repaired store, with ONE raw
    close moved +50% — beyond the x1.25 read band, so the bar reads nothing. Inside a proven raw run it was
    served on the run's (raw) scale like its neighbors: it takes the run's x16 and re-versions at +50% on the
    ADJUSTED scale (it used to keep x1 and land as a one-bar x1/16 island)."""
    stored = _stored(_repaired("BTOC"))
    target = date(2026, 3, 2)
    fresh = [
        {**r, "close": r["close"] * 1.5} if r["d"] == target else dict(r) for r in fx.yahoo("BTOC")
    ]
    norm = normalize(fresh, stored, applicable(fx.splits("BTOC"), on=T))
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert [r["d"] for r in plan.old] == [target]
    assert plan.old[0]["close"] == pytest.approx(stored[target]["close"] * 1.5, rel=1e-6)


# --- B7: an announced split that never happened changes nothing ------------------------------------------------


@pytest.mark.parametrize("first_night", [False, True])
def test_a_postponed_announced_split_is_never_applied(first_night):
    """CONTRACT (a postponed split): OPTT's repaired tape, the vendor on the same scale, and an ANNOUNCED
    1-for-10 listed for 2026-10-13 — a date nothing happened. The tape is flat there, so the store reads
    CONTINUOUS (never RAW): no first night, nothing scaled, nothing written. (The audit names such a split NOT
    VERIFIED — a flat tape is equally "applied" and "never happened"; ``test_repair_split_basis``.)
    """
    s = ListedSplit(
        key_date=date(2026, 10, 13),
        effective_date=date(2026, 10, 13),
        ratio_from=Decimal(10),
        ratio_to=Decimal(1),
        status=STATUS_ANNOUNCED,
    )
    full = _stored(_repaired("OPTT"))
    stored = {d: b for d, b in full.items() if not (first_night and d >= s.effective_date)}
    fresh = [
        {
            "d": d,
            "open": b["close"],
            "high": b["close"],
            "low": b["close"],
            "close": b["close"],
            "volume": b["volume"],
        }
        for d, b in sorted(full.items())
    ]
    tail = [b for b in fresh if b["d"] > max(stored)]
    assert boundary(stored, tail, s)[0] == STORE_CONTINUOUS
    norm = normalize(fresh, stored, applicable([*fx.splits("OPTT"), s], on=T))
    assert norm.total == 0 and norm.first_night == []
    plan = plan_overlap(norm.bars, stored, last=max(stored))
    assert (plan.recent, plan.old) == ([], [])
