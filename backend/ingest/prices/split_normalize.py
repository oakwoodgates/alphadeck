"""The price leg reads the SPLIT LIST (split plan S2) — pure: no I/O, no clock.

``ingest_security.ingest_bars_for_security`` hands every fresh, settled vendor pull through ``normalize`` BEFORE
the restatement compare (``plan_overlap`` -> ``classify`` -> ``decide``), so the stored tape stays on ONE scale
whatever the vendor serves. Alpha Deck owns the split adjustment for every split on its list (this reverses
``source.py``'s old "trust the vendor to re-base" stance for listed splits; an unlisted split still lands as
the vendor serves it, and the T4 monitor flags it).

**The ruler is the stored bar.** For a fresh bar dated before a split in force, the ratio fresh / stored says
which scale the vendor served it on, RELATIVE to the store:

- **the same scale** (ratio ~1) — passes as-is; a genuine correction lands through T6/T5 unchanged.
- **lacking listed splits the store carries** (ratio ~1/K for a product K of splits after the bar) — the vendor
  is still (or again) unadjusted: the bar is scaled by K (price x K, volume / K). It then equals the stored bar,
  and nothing is written. This is what keeps a REPAIRED tape repaired: the T5 revert guard alone holds a flip
  back for one night and re-applies it on the second (MEASURED, split-tape diagnosis section 6).
- **carrying listed splits the store LACKS** (ratio ~K) — two readings, told apart by the store's own tape at
  the split's first session (``boundary``):
  - the store is RAW there (a k-sized step with the inverse volume move at the first session): the vendor
    re-based and the store has not — the bar passes, and lands as the honest first-time restatement it is;
  - the store is CONTINUOUS there (already on the new scale): the vendor applied the split TWICE (PBM's
    double adjustment) — the bar is divided by K.
- **a consistent different factor** (every overlap bar of a segment off by the same ratio m, beyond vendor
  noise, the volume inverse) — the vendor applied a different factor (ATRO: 0.7981 for a 0.8333 distribution):
  the bars are divided by m, when the store is on the list's scale there.
- **anything else** — a genuine correction: passes, and lands through T6/T5 unchanged. A split-sized one is
  the T4 monitor's to flag.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from statistics import median

from domain.splits import ListedSplit

# A fresh bar's scale relative to the stored one is read within x1.25 of a candidate ratio (the band the
# split-tape diagnosis used) — narrowed to half the log-distance to the nearest OTHER candidate, so two
# candidates never share a bar.
SCALE_BAND = 1.25
# Below this factor a split's step at its first session is not readable on the tape (a 20% day is ordinary on
# these names): the store is taken to be on the list's scale there — the post-repair state, and harmless
# before it (it can only keep a stored scale, never invent one).
BOUNDARY_MIN_FACTOR = 1.8
# The levels either side of a first session: medians of up to this many bars.
BOUNDARY_BARS = 5
# The first-night application needs a split of at least x3 either way (a genuine split-day move ran +26% to
# -37% across 96 measured split days — split plan section 16 — so x3 is never one).
FIRST_NIGHT_MIN_FACTOR = 3.0
# A mis-factor: at least this many overlap bars in one segment, 90% of them agreeing within half a percent on
# one residual ratio that is beyond vendor noise (3%: two vendors' closes, the vendor's own factor rounding —
# MEASURED: Yahoo's SMX factors sit up to 1.7% off the exact ratios, identically on every bar). The 10% slack
# lets a genuine one-bar correction in the segment pass through T6 instead of costing the whole repair.
MISFACTOR_MIN_BARS = 5
MISFACTOR_AGREE = 0.005
MISFACTOR_SHARE = 0.9
MISFACTOR_NOISE = 0.03

STORE_RAW = "raw"  # the store's pre-split run lacks the split at its first session
STORE_ON_SCALE = (
    "on_scale"  # the store is continuous across the first session (or it cannot be read)
)

REASON_VENDOR_RAW = "vendor_raw"
REASON_VENDOR_DOUBLED = "vendor_doubled"
REASON_MIS_FACTORED = "mis_factored"
REASON_FIRST_NIGHT = "first_night"


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


def boundary(
    stored: Mapping[date, Mapping],
    tail: Sequence[Mapping],
    split: ListedSplit,
) -> tuple[str, bool]:
    """``(state, first_night)`` of the STORED tape at ``split``'s first session.

    The level before is the median close of up to ``BOUNDARY_BARS`` stored bars before the first session; the
    level after, of up to as many stored bars on or after it — or, when the store holds none yet
    (``first_night``), of the fresh ``tail`` bars on or after it (the vendor's post-split bars are on the new
    scale by definition). ``STORE_RAW`` when the step between them is within half of k (in log terms) of k and
    the volume level moved the other way; ``STORE_ON_SCALE`` otherwise — including a step too small to read
    (``BOUNDARY_MIN_FACTOR``) and an unreadable one: the stored scale is then kept, never invented.
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
        return STORE_ON_SCALE, first_night
    cb = [c for b in before if (c := _num(b.get("close")))]
    ca = [c for b in after if (c := _num(b.get("close")))]
    if not cb or not ca:
        return STORE_ON_SCALE, first_night
    step = _log(median(ca) / median(cb))
    if abs(step - lk) >= 0.5 * abs(lk):
        return STORE_ON_SCALE, first_night
    vb = [v for b in before if (v := _num(b.get("volume")))]
    va = [v for b in after if (v := _num(b.get("volume")))]
    if vb and va and _log(median(va) / median(vb)) * lk >= 0:
        return STORE_ON_SCALE, first_night  # the volume did not move the other way: not a basis
    return STORE_RAW, first_night


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

    def bump(reason: str) -> None:
        out.scaled[reason] = out.scaled.get(reason, 0) + 1

    result: dict[date, dict] = {}
    decided: dict[date, float] = {}  # d -> the factor the bar is multiplied by (1.0 = as served)
    reasons: dict[date, str] = {}
    # per segment, every bar with a stored reference: (bar, factor, close residual, volume residual) — the
    # residual is what is LEFT after the read (fresh x factor / stored); a mis-factor is a residual shared by
    # the whole segment
    resid: dict[int, list[tuple[Mapping, float, float, float | None]]] = {}
    neighbors: list[Mapping] = (
        []
    )  # holes and pre-split tail bars: read against a fresh neighbor below
    for b in sorted(fresh, key=lambda b: b["d"]):
        d = b["d"]
        if d >= last:
            continue
        prior = stored.get(d)
        fc = _num(b.get("close"))
        sc = _num(prior.get("close")) if prior else None
        if not fc or not sc:
            neighbors.append(b)
            continue
        ahead = [j for j, s in enumerate(splits) if s.effective_date > d]
        hit = _read(fc / sc, _candidates(ahead, factors))
        c, reason = 1.0, None
        if hit is not None:
            ratio, js, sign = hit
            if sign != 0 and all(state[j] == STORE_ON_SCALE for j in js):
                # lacking splits the store carries (still / again unadjusted), or carrying one twice
                c, reason = 1.0 / ratio, (REASON_VENDOR_RAW if sign < 0 else REASON_VENDOR_DOUBLED)
            # the first night: the store's run lacks the new split, so the vendor's bar lacks it too unless
            # the ratio says the vendor carries it — apply it
            first = [j for j in ahead if j in apply_first and not (sign > 0 and j in js)]
            if first:
                c *= math.prod(factors[j] for j in first)
                reason = REASON_FIRST_NIGHT
        decided[d] = c
        if reason is not None:
            reasons[d] = reason
        fv, sv = _num(b.get("volume")), _num(prior.get("volume"))  # type: ignore[union-attr]
        resid.setdefault(seg(d), []).append(
            (b, c, fc * c / sc, (fv / c / sv) if (fv and sv) else None)
        )

    # a mis-factor: one segment's residuals all (90%+) the same ratio m, beyond vendor noise, the volume the
    # other way, where the store is on the list's scale — the vendor applied a different factor; divide it back
    for g, rows in resid.items():
        if len(rows) < MISFACTOR_MIN_BARS:
            continue
        m = median(r for _, _, r, _ in rows)
        agree = sum(1 for _, _, r, _ in rows if abs(_log(r / m)) <= _log(1 + MISFACTOR_AGREE))
        if agree < MISFACTOR_SHARE * len(rows) or abs(_log(m)) <= _log(1 + MISFACTOR_NOISE):
            continue
        vr = [v for _, _, _, v in rows if v]
        if vr and _log(median(vr)) * _log(m) >= 0:
            continue
        if not all(state[j] == STORE_ON_SCALE for j in range(g, len(splits))):
            continue
        for b, c, _, _ in rows:
            decided[b["d"]] = c / m
            reasons[b["d"]] = REASON_MIS_FACTORED

    # a bar the store does not hold yet (a hole the vendor filled, a pre-split tail bar on a first night): its
    # scale is read against its nearest decided neighbor IN THE VENDOR'S OWN SERIES (MEASURED: YYAI's first
    # night served 08-14, never stored, already on the new scale beside a raw 08-13)
    by_d = {b["d"]: b for b in fresh}
    for b in neighbors:
        d = b["d"]
        same = [x for x in decided if seg(x) == seg(d)]
        fc = _num(b.get("close"))
        if not same or not fc:
            continue
        near = min(same, key=lambda x: (abs((x - d).days), x))
        nc = _num(by_d[near].get("close"))
        c = decided[near]
        if nc:
            ahead = [j for j, s in enumerate(splits) if s.effective_date > d]
            hit = _read(fc / nc, _candidates(ahead, factors))
            if hit is not None:
                c = c / hit[0]
        decided[d] = c
        if abs(c - 1.0) > 1e-12:
            reasons[d] = REASON_FIRST_NIGHT if d > last_stored else REASON_VENDOR_RAW

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
