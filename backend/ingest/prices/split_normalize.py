"""The price leg reads the SPLIT LIST (split plan S2) — pure: no I/O, no clock.

``ingest_security.ingest_bars_for_security`` hands every fresh, settled vendor pull through ``normalize`` BEFORE
the restatement compare (``plan_overlap`` -> ``classify`` -> ``decide``), so the stored tape stays on ONE scale
whatever the vendor serves. Alpha Deck owns the split adjustment for every split on its list (this reverses
``source.py``'s old "trust the vendor to re-base" stance for listed splits; an unlisted split still lands as
the vendor serves it, and the T4 monitor flags it).

**The ruler is the stored bar, and nothing is rescaled without positive evidence.** For a fresh bar dated
before a split in force, the ratio fresh / stored READS which scale the vendor served it on, relative to the
store; a read alone never rewrites a bar. The cases:

- **the same scale** (ratio ~1) — passes as-is; a genuine correction lands through T6/T5 unchanged.
- **lacking listed splits the store carries** (ratio ~1/K for a product K of splits after the bar) — the vendor
  is still (or again) unadjusted: the bar is scaled by K (price x K, volume / K). It then equals the stored bar,
  and nothing is written. This is what keeps a REPAIRED tape repaired: the T5 revert guard alone holds a flip
  back for one night and re-applies it on the second (MEASURED, split-tape diagnosis section 6).
- **carrying listed splits the store already carries** (ratio ~K) — the vendor applied a split TWICE (PBM's
  double adjustment): the bar is divided by K. The same ratio is ALSO what a genuine vendor re-base onto a RAW
  store looks like, so this branch needs the store POSITIVELY continuous at the split (below).
- **a consistent different factor** (every same-scale overlap bar of a segment off by one ratio m, beyond vendor
  noise, the volume inverse, the next segment agreeing with the store) — the vendor applied a different factor
  (ATRO: 0.7981 for a confirmed 0.8333 distribution): the bars are divided by m.
- **anything else** — passes, and lands through T6/T5 unchanged. A split-sized one is the T4 monitor's to flag.

**The evidence (the B1 review fix, 2026-10-20).** The vendor's bars group into RUNS of one read. A run is scaled
only when it is PROVEN:

1. at every LOCAL edge of the run (where the read changes, no more than ``EDGE_MAX_GAP`` bars apart), the STORE
   is continuous — its 1-bar and 5-bar steps across the edge are both under half the factor the read changes
   by — so the k-sized step sits in the VENDOR's series, not the store's; at least one edge says so and none
   says otherwise. This is the reviewer's "the vendor's own series shows the k-step": the vendor's step at an
   edge is the store's plus the read's change, so a continuous store there IS the vendor's step. It separates
   a raw store the vendor re-based (the store steps at the edge: the re-base lands), a later UNLISTED split of
   the same ratio (the read does not change at the listed split, so there is no local edge), and the double
   adjustment (the store is flat where the vendor steps — PBM's vendor step sits nine sessions early, 01-20,
   which the edge reads where it is);
2. for a "twice" read, the store reads ``STORE_CONTINUOUS`` at every split the read names (``boundary``), never
   ``STORE_RAW`` or ``STORE_UNREADABLE``; for a "lacking" read, never ``STORE_RAW`` there;
3. at least 90% of the run's bars equal the stored bar within ``VENDOR_NOISE`` once scaled (the B2 fix: a bar
   that merely READS near a candidate is a genuine correction, not a scale — a short run must match bar for
   bar; in a long proven run the remaining few are corrections ON the proven scale and land through T6 there,
   never on the raw scale as a one-bar island).

``boundary`` is three-state: ``STORE_RAW`` / ``STORE_CONTINUOUS`` / ``STORE_UNREADABLE``. Unreadable is never
read as either (it used to default to "on scale", which let a raw store's re-base be divided back every
night — the B1 finding, MEASURED on dev windows: 9 of 119 simulated raw stores misread). MEASURED on the same
119 real windows: the 1-bar step reads a continuous store CONTINUOUS in 119 and a raw store CONTINUOUS in 0;
with the 5-bar step also required, 118 and 0.

Bars dated on or after the last split in force are never touched. **The first night of a split**: when the
store holds no bar on or after a split's first session yet (the split arrives with tonight's pull — an
announced split filed in advance, AXG's 1-for-200), its boundary is read against the vendor's own post-split
bars; when that shows the store's pre-split run RAW and the vendor serves the same raw history, the pre-split
bars are scaled by k, so the tape is on one scale the night the split takes effect (plan section 7: "on
confirmation, the old bars are moved to the new scale once"). Only for a split of x3 or more either way, with
no step the size of the split anywhere in the stored run it rescales (no bar already on the new scale) —
anything less certain is left to the operator's repair tool (``pipeline.repair_split_basis``) and the T4
monitor. MEASURED on the dev history: VRME 2026-09-29 (1-for-10, the night of its fake +1,276% breakout arm),
YYAI 2026-08-17 (1-for-20) and LRHC 2026-09-08 (1-for-6) each arrived as the raw history plus the first
post-split bar. The same rule covers a FIRST PULL (an empty store) on the vendor's own series.

There is no retirement: when the vendor serves the adjusted scale the normalization is a pass-through, which
costs nothing (the plan's "stop after N adjusted nights" is not needed).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from statistics import median

from domain.splits import STATUS_CONFIRMED, ListedSplit

# A fresh bar's scale relative to the stored one is read within x1.25 of a candidate ratio (the band the
# split-tape diagnosis used) — narrowed to half the log-distance to the nearest OTHER candidate, so two
# candidates never share a bar.
SCALE_BAND = 1.25
# Below this factor a step is not readable on the tape (a 20% day is ordinary on these names): a split's first
# session, or a run edge, that small is UNREADABLE — never taken as either state.
BOUNDARY_MIN_FACTOR = 1.8
# The levels either side of a first session (or a run edge): medians of up to this many bars, beside the 1-bar
# step across it.
BOUNDARY_BARS = 5
# A run edge is read only LOCALLY: at most this many bars (in the store's or the vendor's series) between its
# two sides. A serial reverse splitter's price falls by about k between its splits (MEASURED: LRHC's 1-for-10s
# of 2026-01-26 and 04-20), so a step read across months says nothing about a scale.
EDGE_MAX_GAP = 2
# A bar the store does not hold (a hole, a pre-split tail bar on a first night) is read against a decided
# neighbor at most this many sessions away in the vendor's own series, or left as served (the B8 fix).
NEIGHBOR_MAX_SESSIONS = 2
# The first-night application needs a split of at least x3 either way (a genuine split-day move ran +26% to
# -37% across 96 measured split days — split plan section 16 — so x3 is never one).
FIRST_NIGHT_MIN_FACTOR = 3.0
# Vendor noise: two vendors' closes, the vendor's own factor rounding — MEASURED: Yahoo's SMX factors sit up to
# 1.7% off the exact ratios, identically on every bar. A scaled bar "equals" the stored one within this (B2).
VENDOR_NOISE = 0.03
# A proven run: at least this share of its bars equal the store once scaled (the rest land through T6 as
# corrections on the proven scale).
RUN_MATCH_SHARE = 0.9
# A mis-factor: at least this many same-scale overlap bars in one segment, 90% of them agreeing within half a
# percent on one residual ratio that is beyond vendor noise. The 10% slack lets a genuine one-bar correction
# in the segment pass through T6 instead of costing the whole repair.
MISFACTOR_MIN_BARS = 5
MISFACTOR_AGREE = 0.005
MISFACTOR_SHARE = 0.9
MISFACTOR_NOISE = VENDOR_NOISE

STORE_RAW = "raw"  # the store's pre-split run lacks the split at its first session
STORE_CONTINUOUS = "continuous"  # the store is flat across the first session: it carries the split
STORE_UNREADABLE = (
    "unreadable"  # neither can be told (too small a factor, a genuine move as big as the split,
)
# a split-sized price step without the inverse volume move, missing bars) — never read as either

REASON_VENDOR_RAW = "vendor_raw"
REASON_VENDOR_DOUBLED = "vendor_doubled"
REASON_MIS_FACTORED = "mis_factored"
REASON_FIRST_NIGHT = "first_night"
# the reasons that DIVIDE BACK a vendor's bar or rescale history for the first time — the exceptions the price
# leg logs at WARNING (vendor_raw, the steady absorb of a vendor still serving the unadjusted basis, is counted
# in the pass summary only)
WARN_REASONS = frozenset({REASON_VENDOR_DOUBLED, REASON_MIS_FACTORED, REASON_FIRST_NIGHT})


@dataclass
class Normalized:
    """What ``normalize`` did: the fresh bars (normalized where needed, otherwise the same dicts) and a count
    per reason. ``first_night`` names the splits applied to the stored history for the first time tonight.
    """

    bars: list[dict]
    scaled: dict[str, int] = field(default_factory=dict)
    first_night: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.scaled.values())


def _log(x: float) -> float:
    return math.log(x)


def _scale(bar: Mapping, c: float) -> dict:
    """``bar`` with prices x ``c`` and volume / ``c`` (a missing field stays missing)."""
    out = dict(bar)
    for f in ("open", "high", "low", "close"):
        if out.get(f) is not None:
            out[f] = float(out[f]) * c
    if out.get("volume") is not None:
        out["volume"] = float(out["volume"]) / c
    return out


def _num(x: object) -> float | None:
    return None if x is None else float(x)  # stored numerics arrive as Decimal


def _steps(cb: Sequence[float], ca: Sequence[float]) -> tuple[float, float]:
    """The log step across a boundary: ``(1-bar, 5-bar)`` — the last close before against the first after, and
    the medians of the closes either side (each oldest first)."""
    return _log(ca[0] / cb[-1]), _log(median(ca) / median(cb))


def _flat(cb: Sequence[float], ca: Sequence[float], lk: float) -> bool:
    """CONTINUOUS across a boundary for a factor ``exp(lk)``: the 1-bar AND the 5-bar step are both under half
    of it (in log terms). The 1-bar step is the sharp one (nano caps trend hard for days after a reverse
    split: OPTT's 5-bar median fell 57% across its 1-for-30, its 1-bar step 17%); the 5-bar one keeps a single
    odd print from deciding."""
    one, five = _steps(cb, ca)
    half = 0.5 * abs(lk)
    return abs(one) < half and abs(five) < half


def boundary(
    stored: Mapping[date, Mapping],
    tail: Sequence[Mapping],
    split: ListedSplit,
) -> tuple[str, bool]:
    """``(state, first_night)`` of the STORED tape at ``split``'s first session — three states.

    The bars before are up to ``BOUNDARY_BARS`` stored bars before the first session; the bars after, up to as
    many stored bars on or after it — or, when the store holds none yet (``first_night``), the fresh ``tail``
    bars on or after it (the vendor's post-split bars are on the new scale by definition).

    - ``STORE_RAW`` — the 5-bar step is within half of k (in log terms) of k and the volume level moved the
      other way (when both sides carry one): the store lacks the split there. (Unchanged from S2's first cut:
      the 1-bar step is NOT required here — BTOC's real raw store steps one session early, at 10-14, where the
      vendor's null 10-14 / 10-15 left an adjusted bar, and must still page.)
    - ``STORE_CONTINUOUS`` — the 1-bar AND the 5-bar step are both under half of k: the store carries the split
      there. Disjoint from RAW by the 5-bar step.
    - ``STORE_UNREADABLE`` — anything else: a factor too small to read (``BOUNDARY_MIN_FACTOR``), missing bars,
      a split-sized price step without the inverse volume move, a genuine move as big as the split. Never
      read as either: a caller that needs a state to act on leaves the vendor's bar alone.
    """
    s = split.effective_date
    lk = _log(float(split.factor))
    before = [stored[d] for d in sorted(d for d in stored if d < s)][-BOUNDARY_BARS:]
    after_dates = sorted(d for d in stored if d >= s)
    first_night = not after_dates
    after = (
        [stored[d] for d in after_dates[:BOUNDARY_BARS]]
        if after_dates
        else sorted((b for b in tail if b["d"] >= s), key=lambda b: b["d"])[:BOUNDARY_BARS]
    )
    if abs(lk) < _log(BOUNDARY_MIN_FACTOR) or not before or not after:
        return STORE_UNREADABLE, first_night
    cb = [c for b in before if (c := _num(b.get("close")))]
    ca = [c for b in after if (c := _num(b.get("close")))]
    if not cb or not ca:
        return STORE_UNREADABLE, first_night
    _, five = _steps(cb, ca)
    if abs(five - lk) < 0.5 * abs(lk):
        vb = [v for b in before if (v := _num(b.get("volume")))]
        va = [v for b in after if (v := _num(b.get("volume")))]
        if vb and va and _log(median(va) / median(vb)) * lk >= 0:
            # a split-sized price step, the volume not the other way
            return STORE_UNREADABLE, first_night
        return STORE_RAW, first_night
    if _flat(cb, ca, lk):
        return STORE_CONTINUOUS, first_night
    return STORE_UNREADABLE, first_night


def _clean_run(closes: Sequence[float], factor: float) -> bool:
    """No adjacent step the size of ``factor`` (within ``SCALE_BAND`` of it, either way) — the run carries no
    bar already on the split's scale (an island, a vendor boundary sitting early), so rescaling the whole run
    cannot double one. A genuine big move of another size does not count (MEASURED: YYAI's real 2025-10-07
    crash, x0.072, sits in the run its 1-for-20 rescales; a "no x1.8 step" rule would refuse it)."""
    lk = abs(_log(factor))
    return all(abs(abs(_log(b / a)) - lk) > _log(SCALE_BAND) for a, b in zip(closes, closes[1:]))


def _candidates(
    ahead: Sequence[int], factors: Sequence[float]
) -> list[tuple[float, tuple[int, ...], int]]:
    """``(ratio, splits, sign)``: the vendor relative to the store LACKS (sign -1, ratio 1/K) or CARRIES EXTRA
    (sign +1, ratio K) the splits ``splits`` (up to three of them), K their product; plus the same scale
    (ratio 1, sign 0)."""
    out: list[tuple[float, tuple[int, ...], int]] = [(1.0, (), 0)]
    idx = sorted(ahead)
    for r in range(1, min(len(idx), 3) + 1):
        for c in combinations(idx, r):
            k = math.prod(factors[j] for j in c)
            out.append((1.0 / k, c, -1))
            out.append((k, c, +1))
    return out


def _read(ratio: float, cands: Sequence[tuple[float, tuple[int, ...], int]]):
    """The candidate a fresh / stored ratio reads as, or None: the nearest in log terms, within ``SCALE_BAND``
    and within half the distance to the next-nearest distinct candidate."""
    lr = _log(ratio)
    ranked = sorted(cands, key=lambda c: abs(lr - _log(c[0])))
    best = ranked[0]
    dist = abs(lr - _log(best[0]))
    others = [
        abs(_log(c[0]) - _log(best[0]))
        for c in ranked[1:]
        if abs(_log(c[0]) - _log(best[0])) > 1e-9
    ]
    gap = min(others) if others else math.inf
    return best if dist <= min(_log(SCALE_BAND), gap / 2) else None


def _first_pull(fresh: Sequence[Mapping], splits: Sequence[ListedSplit]) -> Normalized:
    """The store holds nothing yet: a split in force whose pre-split history the vendor serves decisively RAW
    (``boundary`` on the vendor's own series, x3 or more, one clean run before it) is applied to it.
    """
    factors = [float(s.factor) for s in splits]
    by_d = {b["d"]: b for b in fresh}
    out = Normalized(bars=[])
    apply = []
    for j, s in enumerate(splits):
        pre = sorted(d for d in by_d if d < s.effective_date)
        closes = [c for d in pre if (c := _num(by_d[d].get("close")))]
        if (
            not pre
            or abs(_log(factors[j])) < _log(FIRST_NIGHT_MIN_FACTOR)
            or not _clean_run(closes, factors[j])
        ):
            continue
        st, _ = boundary(by_d, (), s)  # the vendor's own pre- and post-split bars
        if st == STORE_RAW:
            apply.append(j)
    result = []
    for b in sorted(fresh, key=lambda b: b["d"]):
        c = math.prod(factors[j] for j in apply if b["d"] < splits[j].effective_date)
        if abs(c - 1.0) > 1e-12:
            result.append(_scale(b, c))
            out.scaled[REASON_FIRST_NIGHT] = out.scaled.get(REASON_FIRST_NIGHT, 0) + 1
        else:
            result.append(dict(b))
    out.bars = result
    out.first_night = [f"{splits[j].label} {splits[j].effective_date.isoformat()}" for j in apply]
    return out


@dataclass
class _Run:
    """Consecutive overlap bars (skipping bars that read nothing) with ONE read: the vendor relative to the
    store lacks (``sign`` -1) or carries extra (+1) the splits ``js`` — ``ratio`` the candidate — or is on the
    same scale (0)."""

    ratio: float
    js: tuple[int, ...]
    sign: int
    dates: list[date]


_EDGE_FLAT = "flat"  # the store is continuous across the edge: the step is the vendor's
_EDGE_STEP = "step"  # the store itself steps there: the vendor may be the continuous one
_EDGE_UNREAD = "unread"  # too small a change, or not local


def normalize(
    fresh: Sequence[Mapping],
    stored: Mapping[date, Mapping],
    in_force: Sequence[ListedSplit],
) -> Normalized:
    """Normalize a fresh vendor pull onto the list's scale for every split in force (module docstring).

    ``fresh`` — the settled vendor bars (``d`` / OHLCV dicts); ``stored`` — the latest stored version per date
    (``close`` / ``volume``, as ``eod_loader.stored_bars`` returns them); ``in_force`` — the splits in force on
    the pass's market day (``domain.splits.applicable``)."""
    splits = sorted(in_force, key=lambda s: s.effective_date)
    if not splits or not fresh:
        return Normalized(bars=[dict(b) for b in fresh])
    last = splits[-1].effective_date
    if not any(b["d"] < last for b in fresh):
        return Normalized(bars=[dict(b) for b in fresh])
    if not stored:
        return _first_pull(fresh, splits)
    factors = [float(s.factor) for s in splits]
    last_stored = max(stored)
    tail = [b for b in fresh if b["d"] > last_stored]
    fresh_dates = {b["d"] for b in fresh}
    state: dict[int, str] = {}
    apply_first: set[int] = set()
    for j, s in enumerate(splits):
        st, first = boundary(stored, tail, s)
        state[j] = st
        if not (
            first and st == STORE_RAW and abs(_log(factors[j])) >= _log(FIRST_NIGHT_MIN_FACTOR)
        ):
            continue
        # the run the first night would rescale: every stored bar before the first session the pull covers
        run = [
            c
            for d in sorted(d for d in stored if d < s.effective_date and d in fresh_dates)
            if (c := _num(stored[d].get("close")))
        ]
        if run and _clean_run(run, factors[j]):
            apply_first.add(j)

    out = Normalized(bars=[])

    def seg(d: date) -> int:
        return sum(1 for s in splits if s.effective_date <= d)

    def ahead(d: date) -> list[int]:
        return [j for j, s in enumerate(splits) if s.effective_date > d]

    def bump(reason: str) -> None:
        out.scaled[reason] = out.scaled.get(reason, 0) + 1

    # --- 1. the read of every overlap bar — including those on/after the last split (same-scale candidates
    # only): the reference the runs' edges are read against
    by_d = {b["d"]: b for b in fresh}
    reads: list[tuple[date, tuple[float, tuple[int, ...], int] | None]] = []
    neighbors: list[Mapping] = (
        []
    )  # holes and pre-split tail bars: read against a fresh neighbor below
    for b in sorted(fresh, key=lambda b: b["d"]):
        d = b["d"]
        prior = stored.get(d)
        fc = _num(b.get("close"))
        sc = _num(prior.get("close")) if prior else None
        if not fc or not sc:
            if d < last:
                neighbors.append(b)
            continue
        reads.append((d, _read(fc / sc, _candidates(ahead(d), factors))))

    # --- 2. runs, and which are PROVEN (the module docstring's three conditions)
    runs: list[_Run] = []
    for d, hit in reads:
        if hit is None:
            continue
        ratio, js, sign = hit
        if runs and (runs[-1].js, runs[-1].sign) == (js, sign):
            runs[-1].dates.append(d)
        else:
            runs.append(_Run(ratio, js, sign, [d]))
    series = sorted(set(stored) | fresh_dates)
    s_close = {d: c for d in stored if (c := _num(stored[d].get("close")))}
    s_dates = sorted(s_close)

    def edge(a: _Run, b: _Run) -> str:
        lk = _log(b.ratio / a.ratio)  # the read changes by this across the edge
        if abs(lk) < _log(BOUNDARY_MIN_FACTOR):
            return _EDGE_UNREAD
        x, y = a.dates[-1], b.dates[0]
        if bisect_left(series, y) - bisect_right(series, x) > EDGE_MAX_GAP:
            return _EDGE_UNREAD
        cb = [s_close[d] for d in s_dates[: bisect_right(s_dates, x)][-BOUNDARY_BARS:]]
        ca = [s_close[d] for d in s_dates[bisect_left(s_dates, y) :][:BOUNDARY_BARS]]
        return _EDGE_FLAT if cb and ca and _flat(cb, ca, lk) else _EDGE_STEP

    proven: dict[date, tuple[float, str]] = {}
    for i, r in enumerate(runs):
        if r.sign == 0:
            continue
        if r.sign > 0 and any(state[j] != STORE_CONTINUOUS for j in r.js):
            continue  # "twice" needs the store positively carrying each split it names
        if r.sign < 0 and any(state[j] == STORE_RAW for j in r.js):
            continue  # "lacking" a split the store itself lacks reads nothing
        edges = ([edge(runs[i - 1], r)] if i else []) + (
            [edge(r, runs[i + 1])] if i + 1 < len(runs) else []
        )
        if _EDGE_FLAT not in edges or _EDGE_STEP in edges:
            continue
        c = 1.0 / r.ratio
        match = sum(
            1
            for d in r.dates
            if abs(_log(float(by_d[d]["close"]) * c / s_close[d])) <= _log(1 + VENDOR_NOISE)
        )
        if match < RUN_MATCH_SHARE * len(r.dates):
            continue
        reason = REASON_VENDOR_RAW if r.sign < 0 else REASON_VENDOR_DOUBLED
        for d in r.dates:
            proven[d] = (c, reason)

    # --- 3. per bar: the proven scale, then the first night's factor
    decided: dict[date, float] = {}  # d -> the factor the bar is multiplied by (1.0 = as served)
    reasons: dict[date, str] = {}
    for d, hit in reads:
        if d >= last:
            continue
        c, reason = proven.get(d, (1.0, None))
        if hit is not None:
            _, js, sign = hit
            # the first night: the store's run lacks the new split, so the vendor's bar lacks it too unless
            # the read says the vendor carries it — apply it
            first = [j for j in ahead(d) if j in apply_first and not (sign > 0 and j in js)]
            if first:
                c *= math.prod(factors[j] for j in first)
                reason = REASON_FIRST_NIGHT
        decided[d] = c
        if reason is not None:
            reasons[d] = reason

    # --- 4. a mis-factor: a segment's SAME-SCALE reads all (90%+) off the store by one ratio m beyond vendor
    # noise, the volume the other way, the NEXT segment agreeing with the store (the offset changes AT this
    # confirmed split, nowhere else) — the vendor applied a different factor; divide it back
    resid: dict[int, list[tuple[date, float, float | None]]] = {}
    for d, hit in reads:
        if hit is None or hit[2] != 0:
            continue
        fb, sb = by_d[d], stored[d]
        fv, sv = _num(fb.get("volume")), _num(sb.get("volume"))
        resid.setdefault(seg(d), []).append(
            (d, float(fb["close"]) / s_close[d], (fv / sv) if (fv and sv) else None)
        )
    for g, rows in resid.items():
        if g >= len(splits) or len(rows) < MISFACTOR_MIN_BARS:
            continue
        if splits[g].status != STATUS_CONFIRMED or state[g] == STORE_RAW:
            continue
        m = median(r for _, r, _ in rows)
        agree = sum(1 for _, r, _ in rows if abs(_log(r / m)) <= _log(1 + MISFACTOR_AGREE))
        if agree < MISFACTOR_SHARE * len(rows) or abs(_log(m)) <= _log(1 + MISFACTOR_NOISE):
            continue
        vr = [v for _, _, v in rows if v]
        if vr and _log(median(vr)) * _log(m) >= 0:
            continue
        nxt = resid.get(g + 1)
        if not nxt or abs(_log(median(r for _, r, _ in nxt))) > _log(1 + MISFACTOR_AGREE):
            continue
        for d, _, _ in rows:
            decided[d] = decided[d] / m
            reasons[d] = REASON_MIS_FACTORED

    # --- 5. a bar the store does not hold yet (a hole the vendor filled, a pre-split tail bar on a first
    # night): its scale is read against a decided neighbor at most NEIGHBOR_MAX_SESSIONS away IN THE VENDOR'S
    # OWN SERIES, or it is left as served (MEASURED: YYAI's first night served 08-14, never stored, already on
    # the new scale beside a raw 08-13)
    f_dates = sorted(fresh_dates)
    pos = {d: i for i, d in enumerate(f_dates)}
    for b in neighbors:
        d = b["d"]
        fc = _num(b.get("close"))
        if not fc:
            continue
        i = pos[d]
        near_by = [
            x
            for x in f_dates[max(0, i - NEIGHBOR_MAX_SESSIONS) : i + NEIGHBOR_MAX_SESSIONS + 1]
            if x != d and x in decided and seg(x) == seg(d)
        ]
        if not near_by:
            continue
        near = min(near_by, key=lambda x: (abs(pos[x] - i), x))
        nc = _num(by_d[near].get("close"))
        c = decided[near]
        if nc:
            # a ONE-session move read as a scale change only at x3 or more (no genuine split-day move came
            # near it: +26% to -37% across 96 measured split days); anything less is the same scale
            hit = _read(fc / nc, _candidates(ahead(d), factors))
            if hit is not None and abs(_log(hit[0])) >= _log(FIRST_NIGHT_MIN_FACTOR):
                c = c / hit[0]
        decided[d] = c
        if abs(c - 1.0) > 1e-12:
            reasons[d] = (
                REASON_FIRST_NIGHT if d > last_stored else reasons.get(near, REASON_VENDOR_RAW)
            )

    result: dict[date, dict] = {}
    for b in fresh:
        d = b["d"]
        c = decided.get(d, 1.0)
        if d in reasons and abs(c - 1.0) > 1e-12:
            result[d] = _scale(b, c)
            bump(reasons[d])
        else:
            result[d] = dict(b)
    out.bars = [result[b["d"]] for b in sorted(fresh, key=lambda b: b["d"])]
    out.first_night = [
        f"{splits[j].label} {splits[j].effective_date.isoformat()}" for j in sorted(apply_first)
    ]
    return out
