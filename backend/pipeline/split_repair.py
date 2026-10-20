"""The split-basis CLASSIFIER and repair plan (split plan S2) — pure: no I/O, no clock.

``pipeline.repair_split_basis`` (the operator's tool) reads a security's STORED latest-version tape and the
split list, and asks this module one question per bar dated before a split in force: which scale is the
stored bar on, relative to the TARGET scale — the bar as traded times the cumulative factor of the splits in
force after it? The answer is an OFFSET per bar (``log(stored / target)``); the repair multiplies the bar's
prices by ``exp(-offset)`` and divides its volume by the same (recipe B — the stored bar rescaled: the
vendor's own history is gone for some names, so a fresh pull cannot repair them).

THE LABELS, relative to the target:

- **ADJUSTED** — offset 0: the bar carries every split after it. Nothing to do.
- **RAW** — the bar lacks one or more splits after it (offset -log of their product): the vendor never
  applied them (a cliff), applied them on other days only (an island), or re-based only inside its own
  two-year window on some past night (a stale tail). Multiply by the missing product.
- **OVER-ADJUSTED** — the bar carries a split twice (offset +log k): PBM's double adjustment (the vendor
  applied 1-for-6.25 at the first announced date, 01-20, and again when it moved to 02-02). Divide by k.
- **MIS-FACTORED** — the vendor applied a different factor, consistently (ATRO's 20% stock distribution:
  0.7981 applied where the SEC filing and Polygon give 0.8333). Correct by the measured ratio.

The last two are NEVER read off the tape alone: going forward, a doubled split is a lone DOWN step on rising
volume — exactly what a genuine crash on heavy volume looks like (T4's named blind spot; MEASURED here: YYAI's
real 2025-10-07 crash, x0.072 on x285 volume, read as a doubled split when it was allowed) — and a few-percent
mis-factor is invisible. Both enter only from INDEPENDENT raw prints (``spot``, Polygon's unadjusted bars),
which pin the bars they fall on.

WHERE A VENDOR CHANGES SCALE (the model, from every measured case in the split-tape diagnosis):

1. **At a split's first session** — the natural move (the target's factor drops; a RAW run meets the new scale
   with the split's step, an ADJUSTED run meets it continuously).
2. **A few sessions off it** — a misdated boundary (OPAD's sits 2 sessions early; PBM's 9). Allowed within
   ``MISDATE_SESSIONS`` of a first session.
3. **On odd days** — an ISLAND: up to ``ISLAND_MAX_BARS`` consecutive bars on another scale between two flanks
   on one scale (the 07-29 / 08-04 / 08-07 / 08-11 / 09-14 days on six names, one nightly pass writing them
   from the vendor's other source). Found first, by their two edges (pass 1, ``find_islands``).
4. **At the vendor's own window edge** — bars older than the vendor's two-year range on some past night kept
   that night's scale (BTOC's 29-bar tail, MPU's 27, VRME's 23). Only bars older than ``today -
   VENDOR_RANGE_DAYS`` can carry such an edge.

Anywhere else a scale change is not a vendor's behavior, and the path may not make one: a genuine move that
happens to be the size of a split product (SMX 2025-12-31, x0.318 on x4.3 volume ~ 20/4.88) stays a move.

HOW (pass 2, a min-cost path). The bars — islands flattened onto their flanks' scale — are walked as a chain.
Each bar takes a candidate state (which splits after it are missing; doubled / mis-factored only where a raw
print pins one); every adjacent pair pays the GENUINE move the states leave unexplained, ``|price step -
offset change|``, plus, when the states move the basis between the two bars, ``W_VOLUME x |volume step +
offset change|`` (a split moves the price by k and the share volume by 1/k, so a basis change owes the inverse
volume move; a genuine move's own volume is no evidence either way), plus ``CHANGE_COST`` for a vendor change
(cases 2 and 4 only — case 1 is free, case 3 was pass 1). Bars on or after the last split in force are the
ANCHOR: on the target scale by definition. The cheapest path is the labeling; a bar whose cheapest DIFFERENT
correction costs less than ``MIN_MARGIN`` more is AMBIGUOUS, and one ambiguous bar refuses the whole company
(no partial repair — the operator's rule). A listed split NOT in force (needs review, or announced with its
date ahead) is TRANSPARENT: its date may carry its step or not, and nothing here applies or undoes it.

THE GATES (``plan_gates``) run on the REPAIRED tape before ``--apply`` may write: the T4 detector
(``pipeline.split_basis.tape_findings``) clean over the monitor's window; no step the size of a split left at
a first session or at an edge the repair drew; every raw print within 1% (or one cent) of the repaired close
divided by its factor.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import combinations
from statistics import median

from domain.splits import ListedSplit, session_distance
from pipeline.split_basis import K_MIN, MIN_CLOSE, TapeBar, tape_findings, window_days

# --- the labels (wire strings: they ride the dry-run report and the provenance JSON) ---------------------
ADJUSTED = "adjusted"
RAW = "raw"
OVER_ADJUSTED = "over_adjusted"
MIS_FACTORED = "mis_factored"
INVALID = "invalid"  # internal: an island pass 2 could put on no vendor scale (dropped and re-run)

# --- the model ---------------------------------------------------------------------------------------------
# W_VOLUME — what a basis change's VOLUME residual costs relative to a price residual (log units). A genuine
# day's volume swings far more than its price (x3..x10 is routine on these names; an offering prints x100+),
# so it weighs a quarter: enough that the inverse volume move corroborates a basis change, never enough that
# one noisy volume day decides a label. MEASURED on the dev tapes: charging the volume to every pair (and at
# 0.5) read genuine crashes on offering volume as basis changes — MI 2026-03-10 (x0.17 on x2,827 volume),
# SMX 2024-10-28 (x0.345 on x26), YYAI 2025-10-07 (x0.072 on x285); charged only to the hypothesis that moves
# the basis, each stays the genuine move the Polygon raw prints say it was.
W_VOLUME = 0.25
# CHANGE_COST — a vendor changing scale at a misdated boundary or its window edge (cases 2 and 4). 1.0 is a
# x2.7 genuine move: a real edge (step within a few percent of k, the volume inverse) costs ~1 against the
# split-sized move it otherwise leaves unexplained; a coincidental genuine move costs more than it saves.
CHANGE_COST = 1.0
# MIN_MARGIN — the least extra cost the cheapest DIFFERENT correction for a bar may carry before the bar is
# AMBIGUOUS: half a change.
MIN_MARGIN = 0.5
# MISDATE_SESSIONS — how far from a split's first session a vendor's boundary may sit (case 2). MEASURED: OPAD
# 2 sessions early, PBM 9 (the split was postponed from 01-20 to 02-02). Two weeks of sessions.
MISDATE_SESSIONS = 10
# VENDOR_RANGE_DAYS / STALE_SLACK_DAYS — the vendor's served window (the price leg pulls ``range=2y``): a
# re-base on night P re-bases only bars from P - 2y on, and P is never after today, so a window edge (case 4)
# can only sit before ``today - VENDOR_RANGE_DAYS``, give or take two weeks of calendar arithmetic.
VENDOR_RANGE_DAYS = 730
STALE_SLACK_DAYS = 14
# ISLAND_MAX_BARS / ISLAND_BAND / ISLAND_FLANK_TOL / ISLAND_BETA_MIN — pass 1. An island is 1..5 bars
# (MEASURED: every one in the diagnosis is 1 or 2) whose entry step is within x1.25 of a split factor (or a
# product of adjacent ones) and whose exit undoes it within x1.25, with no basis-sized step inside, between
# flanks that agree within x1.5 (the T4 monitor's PAIR_TOL), and whose volume level moved the OTHER way from
# its flanks' by at least a quarter of the factor in log terms (the vendor's other source scales the volume
# by exactly 1/k; a genuine spike-and-fade does not — MEASURED: SMX 2025-12-05, x2.35 then x0.41 on a flat
# volume level, beta 0.01, is a move).
ISLAND_MAX_BARS = 5
ISLAND_BAND = 1.25
ISLAND_FLANK_TOL = 1.5
ISLAND_BETA_MIN = 0.25
# STEP_BAND / STEP_MIN_VOLUME_SHARE — "a step the size of a split" for the residual-step gate: within x1.25 of
# a factor, the volume moving the other way by at least half of it in log terms.
STEP_BAND = 1.25
STEP_MIN_VOLUME_SHARE = 0.5
# SPOT_TOL / the tick — a raw print AGREES with a repaired close within 1% or one tick of the vendor's own
# rounding: a cent at a dime and up (OPTT 2026-09-11 stored 0.11 vs a raw 0.1065, +3.3%), a tenth of a cent
# from a cent (BURU 2026-07-29: the vendor's 0.037 vs Polygon's 0.0365), 0.0001 below (WNRS trades at 0.001).
SPOT_TOL = 0.01
# SPOT_NOISE_TOL — two vendors' closes for one day differ by up to a few percent on thin names (the data audit,
# MEASURED: 127 of 26,963 listed bars 1-3% apart; SMX 2025-07-01 / 07-22 here, 1.5% / 1.3%). A print that
# misses every scale by more than 1% but sits within 3% of exactly one is that scale, reported as NEAR; the
# nearest other scale is a split factor away. Beyond it, the print refuses the company.
SPOT_NOISE_TOL = 0.03
# A mis-factor is only ever set from raw prints: at least this many in one segment, matching no scale, all
# agreeing on one vendor factor within half a percent (ATRO: 30 window prints at 0.957701 +/- 1e-7).
MIN_SPOT_FOR_MISFACTOR = 5
MISFACTOR_AGREE_TOL = 0.005


@dataclass(frozen=True)
class Bar:
    """One latest-version stored bar (floats; ``None`` where the store has none)."""

    d: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


@dataclass(frozen=True)
class Island:
    """Pass 1's find: ``bars`` dates on a scale ``factor`` x their flanks' (an adjusted day in a raw history is
    x k; a raw day in an adjusted history is x 1/k)."""

    dates: tuple[date, ...]
    factor: float


@dataclass(frozen=True)
class _State:
    """A candidate state: indices (into the splits in force) the bar LACKS (``missing``) or carries TWICE
    (``doubled``), or a measured mis-factor offset for one segment (``mf``)."""

    missing: frozenset[int] = frozenset()
    doubled: frozenset[int] = frozenset()
    mf: tuple[int, float] | None = None  # (segment index, log offset)


@dataclass
class BarLabel:
    """The verdict for one stored bar before the last split in force."""

    d: date
    label: str
    offset: float  # log(stored / target)
    factor: (
        Fraction | None
    )  # what the repair MULTIPLIES prices by (an exact product), None for a mis-factor
    margin: float  # extra cost of the cheapest different correction (inf = none possible)
    missing: tuple[date, ...] = ()  # first sessions of the splits the bar lacks
    doubled: tuple[date, ...] = ()
    mis_factor: float | None = (
        None  # the vendor's measured factor relative to the target (MIS_FACTORED)
    )
    island: float | None = None  # pass 1: this bar sat on an island x factor its flanks' scale
    alternative: float | None = (
        None  # how the cheapest different correction differs (x), for the report
    )


@dataclass
class Classification:
    """One security's verdict: the labels for every stored bar before the last split in force, the islands,
    and every reason it cannot be repaired (empty = labeled)."""

    labels: list[BarLabel] = field(default_factory=list)
    islands: list[Island] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    spot_near: list[date] = field(
        default_factory=list
    )  # raw prints read within vendor noise (1%..3%)

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def to_change(self) -> list[BarLabel]:
        return [b for b in self.labels if b.label != ADJUSTED]

    def runs(self) -> list[tuple[date, date, str, int, str]]:
        """Consecutive bars sharing a label AND a correction: ``(first, last, label, n, correction)``."""
        out: list[list] = []
        for b in self.labels:
            key = (b.label, b.factor, None if b.mis_factor is None else round(b.mis_factor, 9))
            if out and out[-1][5] == key:
                out[-1][1] = b.d
                out[-1][3] += 1
            else:
                out.append([b.d, b.d, b.label, 1, correction_label(b), key])
        return [(a, z, lab, n, c) for a, z, lab, n, c, _ in out]


def correction_label(b: BarLabel) -> str:
    """``x16`` / ``x4/25`` / ``x1.0441`` — what the repair multiplies the bar's prices by."""
    if b.factor is not None:
        f = b.factor
        return f"x{f.numerator}" if f.denominator == 1 else f"x{f.numerator}/{f.denominator}"
    return f"x{1.0 / float(b.mis_factor):.6g}"  # type: ignore[arg-type]


def _log(x: float) -> float:
    return math.log(x)


def _segment(d: date, dates: Sequence[date]) -> int:
    """The segment of ``d``: how many first sessions in force are on or before it (0 = before the first)."""
    return sum(1 for s in dates if s <= d)


def _candidate_states(n: int) -> list[_State]:
    """The states a bar may take from the TAPE: nothing missing; any one or two splits missing; every
    prefix / suffix run of splits missing (a vendor that never re-based from some split on). Doubled and
    mis-factored states enter only from a raw print (module docstring)."""
    idx = range(n)
    missing: set[frozenset[int]] = {frozenset()}
    for r in (1, 2):
        missing.update(frozenset(c) for c in combinations(idx, r))
    for j in idx:
        missing.add(frozenset(range(j, n)))
        missing.add(frozenset(range(0, j + 1)))
    return [_State(missing=m) for m in sorted(missing, key=lambda m: (len(m), sorted(m)))]


def _factor_candidates(splits: Sequence[ListedSplit]) -> list[float]:
    """Every split factor and every product of ADJACENT ones (a vendor that missed a run of them), both ways,
    of at least the T4 floor (``K_MIN``) — a smaller step is an ordinary day on these names."""
    fs = [float(s.factor) for s in sorted(splits, key=lambda s: s.effective_date)]
    out: set[float] = set()
    for i in range(len(fs)):
        prod = 1.0
        for f in fs[i:]:
            prod *= f
            out.add(prod)
    return sorted({x for f in out for x in (f, 1.0 / f) if abs(_log(f)) >= _log(K_MIN)})


def _level(vals: Sequence[float | None]) -> float | None:
    xs = [v for v in vals if v is not None and v > 0]
    return median(xs) if xs else None


def find_islands(
    tape: Sequence[Bar],
    splits: Sequence[ListedSplit],
    *,
    until: date,
    exclude: frozenset[date] = frozenset(),
) -> list[Island]:
    """PASS 1 (pure): the islands on ``tape`` (ascending, closes > 0) among bars dated before ``until`` —
    1..``ISLAND_MAX_BARS`` bars whose entry step is within ``ISLAND_BAND`` of a factor candidate, whose exit
    step undoes it within the band, with no basis-sized step inside, whose flanks agree within
    ``ISLAND_FLANK_TOL``, and whose volume level moved the other way from the flanks' by at least
    ``ISLAND_BETA_MIN`` (when both carry volume). Left to right, never overlapping; an island starting on a
    date in ``exclude`` (pass 2 found it on no valid scale) is skipped."""
    fs = _factor_candidates(splits)
    band = _log(ISLAND_BAND)
    out: list[Island] = []
    i = 1
    n = len(tape)
    while i < n - 1:
        if tape[i].d >= until:
            break
        s_in = _log(tape[i].close / tape[i - 1].close)  # type: ignore[operator]
        f = next((f for f in fs if abs(s_in - _log(f)) <= band), None)
        found = None
        if f is not None and tape[i].d not in exclude:
            for L in range(1, ISLAND_MAX_BARS + 1):
                j = i + L  # the first bar after the island
                if j >= n or tape[j - 1].d >= until:
                    break
                s_out = _log(tape[j].close / tape[j - 1].close)  # type: ignore[operator]
                inner = tape[i:j]
                if any(
                    abs(_log(b.close / a.close)) >= _log(K_MIN)  # type: ignore[operator]
                    for a, b in zip(inner, inner[1:])
                ):
                    break  # a basis-sized step inside: not one scale
                if abs(s_out + _log(f)) > band:
                    continue
                flank = abs(_log(tape[j].close / tape[i - 1].close))  # type: ignore[operator]
                if flank > _log(ISLAND_FLANK_TOL):
                    continue
                v_in = _level([b.volume for b in inner])
                v_out = _level([tape[i - 1].volume, tape[j].volume])
                if v_in is not None and v_out is not None:
                    beta = -_log(v_in / v_out) / _log(f)
                    if beta < ISLAND_BETA_MIN:
                        continue  # the volume did not move the other way: a genuine move, not a basis
                found = Island(tuple(b.d for b in inner), f)
                break
        if found is not None:
            out.append(found)
            i += len(found.dates) + 1
        else:
            i += 1
    return out


def classify(
    bars: Sequence[Bar],
    in_force: Sequence[ListedSplit],
    *,
    today: date,
    transparent: Sequence[ListedSplit] = (),
    spot: Mapping[date, float] | None = None,
) -> Classification:
    """Label every stored bar dated before the last split ``in_force`` (see the module docstring).

    ``bars`` are one security's latest-version stored bars (any order); ``in_force`` the splits in force
    (``domain.splits.applicable``); ``today`` the market day of the classification (it bounds the vendor-
    window edge, case 4); ``transparent`` the live splits not in force; ``spot`` optional independent RAW
    closes (as traded) by date.

    An island pass 1 found that pass 2 can put on no valid scale (its bars would carry a split twice, or a
    ratio of factors no vendor state produces) was a genuine move after all: it is dropped and the path is
    re-run without it — at most ``len(islands)`` times."""
    out = Classification()
    if not in_force:
        return out
    splits = sorted(in_force, key=lambda s: s.effective_date)
    last = splits[-1].effective_date
    tape = sorted((b for b in bars if b.close is not None and b.close > 0), key=lambda b: b.d)
    if not any(b.d < last for b in tape):
        return out  # nothing stored before the split: nothing to label
    if not any(b.d >= last for b in tape):
        out.refusals.append(
            f"no stored bar on or after the split's first session {last.isoformat()} — nothing anchors the "
            "scale yet (re-run after the nightly stores one)"
        )
        return out
    excluded: set[date] = set()
    while True:
        islands = find_islands(tape, splits, until=last, exclude=frozenset(excluded))
        result, invalid = _attempt(
            tape, splits, islands, today=today, transparent=transparent, spot=spot
        )
        if not invalid:
            return result
        excluded |= invalid


def _attempt(
    tape: Sequence[Bar],
    splits: Sequence[ListedSplit],
    islands: Sequence[Island],
    *,
    today: date,
    transparent: Sequence[ListedSplit],
    spot: Mapping[date, float] | None,
) -> tuple[Classification, set[date]]:
    """One pass-2 run over ``tape`` with ``islands`` flattened: ``(classification, invalid island starts)``."""
    out = Classification(islands=list(islands))
    sdates = [s.effective_date for s in splits]
    logk = [_log(float(s.factor)) for s in splits]
    last = sdates[-1]
    rel: dict[date, float] = {d: isl.factor for isl in islands for d in isl.dates}
    start_of = {d: isl.dates[0] for isl in islands for d in isl.dates}
    flat = [
        Bar(
            b.d,
            None,
            None,
            None,
            b.close / rel[b.d] if b.d in rel else b.close,  # type: ignore[operator]
            (b.volume * rel[b.d]) if (b.d in rel and b.volume is not None) else b.volume,
        )
        for b in tape
    ]

    def ahead(d: date) -> frozenset[int]:
        return frozenset(j for j, s in enumerate(sdates) if s > d)

    # --- the raw prints: constrain their bars; a doubled or mis-factored state enters only here ------------
    states = _candidate_states(len(splits))
    evidence = [_State(doubled=frozenset({j})) for j in range(len(splits))]
    allowed: dict[date, set[_State]] = {}
    misfactor_by_seg: dict[int, list[tuple[date, float]]] = {}
    by_d = {b.d: b for b in tape}
    near: dict[date, tuple[float, set[_State]]] = {}
    for d, raw in sorted((spot or {}).items()):
        b = by_d.get(d)
        if b is None or d >= last or raw <= 0:
            continue
        k_target = math.prod(float(splits[j].factor) for j in ahead(d))
        o_spot = _log(b.close / (raw * k_target)) - (_log(rel[d]) if d in rel else 0.0)  # type: ignore[operator]
        tol = _spot_log_tol(raw)
        fits = {st for st in states if abs(_offset(st, ahead(d), logk) - o_spot) <= tol}
        if not fits:
            fits = {st for st in evidence if abs(_offset(st, ahead(d), logk) - o_spot) <= tol}
            states.extend(st for st in fits if st not in states)
        if fits:
            allowed[d] = fits
            continue
        misfactor_by_seg.setdefault(_segment(d, sdates), []).append((d, o_spot))
        noise = {
            st
            for st in [*states, *evidence]
            if abs(_offset(st, ahead(d), logk) - o_spot) <= _log(1 + SPOT_NOISE_TOL)
        }
        if noise and len({round(_offset(st, ahead(d), logk), 9) for st in noise}) == 1:
            near[d] = (o_spot, noise)
    for seg, pts in sorted(misfactor_by_seg.items()):
        # a print within vendor noise of one scale IS that scale (two vendors' closes, or the vendor's own
        # cumulative factor convention — MEASURED: Yahoo's SMX factors sit 0.15..0.8% off each exact ratio,
        # 1.6% cumulative, identically on every print); only what is left can be a mis-factor
        rest = []
        for d, o in pts:
            if d in near:
                states.extend(st for st in near[d][1] if st not in states)
                allowed[d] = near[d][1]
                out.spot_near.append(d)
            else:
                rest.append((d, o))
        if not rest:
            continue
        offs = [o for _, o in rest]
        mid = sorted(offs)[len(offs) // 2]
        if len(rest) >= MIN_SPOT_FOR_MISFACTOR and all(
            abs(o - mid) <= _log(1 + MISFACTOR_AGREE_TOL) for o in offs
        ):
            st = _State(mf=(seg, mid))
            states.append(st)
            for d, _ in rest:
                allowed[d] = {st}
            continue
        for d, o in rest:
            out.refusals.append(
                f"raw print {d.isoformat()} (implied vendor offset x{math.exp(o):.6g}) matches no listed "
                f"scale within {SPOT_NOISE_TOL:.0%}, and the segment's prints do not agree on one vendor "
                f"factor (a mis-factor needs >= {MIN_SPOT_FOR_MISFACTOR} agreeing within "
                f"{MISFACTOR_AGREE_TOL:.1%})"
            )
    if out.refusals:
        return out, set()

    # --- pass 2: the min-sum path over each bar's distinct effective states ---------------------------------
    def eff(st: _State, d: date) -> tuple | None:
        if st.mf is not None:
            return ("mf", st.mf) if _segment(d, sdates) == st.mf[0] else None
        a = ahead(d)
        return (st.missing & a, st.doubled & a)

    per_bar: list[list[tuple]] = []
    offsets: list[dict[tuple, float]] = []
    for b in flat:
        keys: dict[tuple, float] = {}
        if not ahead(b.d):
            keys = {
                (frozenset(), frozenset()): 0.0
            }  # the anchor: on the target scale by definition
        else:
            for st in allowed.get(b.d, states):
                k = eff(st, b.d)
                if k is None or k in keys:
                    continue
                keys[k] = k[1][1] if k[0] == "mf" else _offset_key(k, logk)
        per_bar.append(list(keys))
        offsets.append(keys)

    na = sorted(transparent, key=lambda s: s.effective_date)
    stale_before = today - timedelta(days=VENDOR_RANGE_DAYS - STALE_SLACK_DAYS)

    def project(k: tuple, d: date) -> tuple:
        """A state carried forward to ``d``: splits whose first session passed drop out (the natural move); a
        mis-factor belongs to its segment, and leaving it is the natural move to the scale beyond.
        """
        a = ahead(d)
        if k[0] == "mf":
            return k if _segment(d, sdates) == k[1][0] else (frozenset(), frozenset())
        return (k[0] & a, k[1] & a)

    def change_ok(p: Bar, q: Bar) -> bool:
        if q.d <= stale_before:
            return True  # case 4: the vendor's window edge
        return any(
            min(abs(session_distance(s, p.d)), abs(session_distance(s, q.d))) <= MISDATE_SESSIONS
            for s in sdates
        )  # case 2: a misdated boundary

    allow_change = [False] + [change_ok(flat[i - 1], flat[i]) for i in range(1, len(flat))]

    def step_cost(i: int, ka: tuple, kb: tuple) -> float:
        natural = project(ka, flat[i].d) == kb
        if not natural and not allow_change[i]:
            return math.inf
        p, q = flat[i - 1], flat[i]
        do = offsets[i][kb] - offsets[i - 1][ka]
        step = _log(q.close / p.close)  # type: ignore[operator]
        vstep = _log(q.volume / p.volume) if (p.volume and q.volume) else None
        deltas = [0.0]
        for s in na:
            if p.d < s.effective_date <= q.d:
                lk = _log(float(s.factor))
                deltas += [lk, -lk]
        best = math.inf
        for delta in deltas:
            g = abs(step - do - delta)
            if vstep is not None and abs(do + delta) > 1e-9:
                g += W_VOLUME * abs(vstep + do + delta)
            best = min(best, g)
        return best if natural else best + CHANGE_COST

    n = len(flat)
    fwd: list[dict[tuple, float]] = [{k: 0.0 for k in per_bar[0]}]
    for i in range(1, n):
        fwd.append(
            {
                kb: min(fwd[i - 1][ka] + step_cost(i, ka, kb) for ka in per_bar[i - 1])
                for kb in per_bar[i]
            }
        )
    bwd: list[dict[tuple, float]] = [dict() for _ in range(n)]
    bwd[n - 1] = {k: 0.0 for k in per_bar[n - 1]}
    for i in range(n - 2, -1, -1):
        bwd[i] = {
            ka: min(step_cost(i + 1, ka, kb) + bwd[i + 1][kb] for kb in per_bar[i + 1])
            for ka in per_bar[i]
        }

    invalid: set[date] = set()
    ambiguous = 0
    for i, b in enumerate(tape):
        if b.d >= last:
            continue
        marg = {k: fwd[i][k] + bwd[i][k] for k in per_bar[i]}
        best = min(marg, key=lambda k: (marg[k], _key_order(k)))
        if not math.isfinite(marg[best]):
            out.refusals.append(f"{b.d.isoformat()}: no consistent scale path reaches this bar")
            continue
        o_best = offsets[i][best]
        alts = [k for k in marg if abs(offsets[i][k] - o_best) > 1e-9 and math.isfinite(marg[k])]
        alt = min(alts, key=lambda k: (marg[k], _key_order(k))) if alts else None
        margin = (marg[alt] - marg[best]) if alt is not None else math.inf
        lab = _label_of(best, splits, logk, b.d, island=rel.get(b.d), ahead=ahead(b.d))
        lab.margin = margin
        if alt is not None:
            lab.alternative = math.exp(o_best - offsets[i][alt])
        if lab.label == INVALID:
            invalid.add(start_of[b.d])
        out.labels.append(lab)
        if margin < MIN_MARGIN:
            ambiguous += 1
            if ambiguous <= 12:
                out.refusals.append(
                    f"{b.d.isoformat()}: ambiguous — labeled {lab.label} ({correction_label(lab)}) but a "
                    f"correction x{lab.alternative:.6g} different costs only {margin:.2f} more "
                    f"(need {MIN_MARGIN})"
                )
    if ambiguous > 12:
        out.refusals.append(f"... and {ambiguous - 12} more ambiguous bar(s)")
    return out, invalid


def _offset(st: _State, a: frozenset[int], logk: Sequence[float]) -> float:
    return -sum(logk[j] for j in st.missing & a) + sum(logk[j] for j in st.doubled & a)


def _offset_key(k: tuple, logk: Sequence[float]) -> float:
    return -sum(logk[j] for j in k[0]) + sum(logk[j] for j in k[1])


def _key_order(k: tuple) -> tuple:
    """A deterministic tie-break between equal-cost states: fewer missing / doubled first."""
    if k[0] == "mf":
        return (9, 0, ())
    return (len(k[0]) + len(k[1]), len(k[1]), tuple(sorted(k[0])) + tuple(sorted(k[1])))


def _spot_log_tol(raw: float) -> float:
    """The log tolerance for one raw print: 1%, or one cent of it when that is wider."""
    return max(_log(1 + SPOT_TOL), _log((raw + _tick(raw)) / raw))


def _tick(raw: float) -> float:
    """One tick of the vendor's rounding for a raw print (see ``SPOT_TOL``)."""
    if raw >= 0.10:
        return 0.01
    return 0.001 if raw >= 0.01 else 0.0001


def _label_of(
    k: tuple,
    splits: Sequence[ListedSplit],
    logk: Sequence[float],
    d: date,
    *,
    island: float | None,
    ahead: frozenset[int],
) -> BarLabel:
    """The label for state ``k`` of the (flattened) bar on ``d``. An island bar adds its pass-1 factor back
    (its stored value sat ``island`` x its flanks' scale), and the result must be a scale a vendor state
    produces — nothing missing, or a set of the splits after the bar missing — else the bar is ``INVALID``
    (the island was a genuine move; ``classify`` drops it and re-runs)."""
    if k[0] == "mf":
        if island is not None:
            return BarLabel(
                d=d, label=INVALID, offset=math.nan, factor=None, margin=math.inf, island=island
            )
        off = k[1][1]
        return BarLabel(
            d=d,
            label=MIS_FACTORED,
            offset=off,
            factor=None,
            margin=math.inf,
            mis_factor=math.exp(off),
        )
    missing, doubled = k
    factor = math.prod((splits[j].factor for j in missing), start=Fraction(1))
    factor /= math.prod((splits[j].factor for j in doubled), start=Fraction(1))
    if island is not None:
        target = float(factor) / island
        valid = {
            math.prod((splits[j].factor for j in c), start=Fraction(1))
            for r in range(len(ahead) + 1)
            for c in combinations(sorted(ahead), r)
        }
        snapped = next((v for v in valid if abs(_log(float(v)) - _log(target)) < 1e-6), None)
        if snapped is None:
            return BarLabel(
                d=d, label=INVALID, offset=math.nan, factor=None, margin=math.inf, island=island
            )
        factor = snapped
        missing = frozenset(
            next(
                set(c)
                for r in range(len(ahead) + 1)
                for c in combinations(sorted(ahead), r)
                if math.prod((splits[j].factor for j in c), start=Fraction(1)) == snapped
            )
        )
        doubled = frozenset()
    off = -_log(float(factor))
    if abs(off) < 1e-9:
        lab = ADJUSTED
    elif off > 0:
        lab = OVER_ADJUSTED
    else:
        lab = RAW
    return BarLabel(
        d=d,
        label=lab,
        offset=off,
        factor=factor,
        margin=math.inf,
        missing=tuple(sorted(splits[j].effective_date for j in missing)),
        doubled=tuple(sorted(splits[j].effective_date for j in doubled)),
        island=island,
    )


# --- the repaired values -------------------------------------------------------------------------------------


def correction(lab: BarLabel) -> Decimal:
    """What the repair multiplies the bar's prices by (volumes are divided by it): exact for a listed scale (a
    product of split factors), the measured inverse of the vendor's factor for a mis-factored bar.
    """
    with localcontext() as ctx:
        ctx.prec = 34
        if lab.factor is not None:
            return Decimal(lab.factor.numerator) / Decimal(lab.factor.denominator)
        return Decimal(1) / Decimal(repr(float(lab.mis_factor)))  # type: ignore[arg-type]


def repaired_bar(bar: Mapping, c: Decimal) -> dict:
    """``bar`` (Decimal or float OHLCV) on the corrected scale: prices x ``c``, volume / ``c``; a missing field
    stays missing. Decimal arithmetic at 34 digits — the stored numeric keeps every digit the vendor gave.
    """
    with localcontext() as ctx:
        ctx.prec = 34
        out: dict = {"d": bar["d"]}
        for f in ("open", "high", "low", "close"):
            v = bar.get(f)
            out[f] = None if v is None else Decimal(str(v)) * c
        v = bar.get("volume")
        out["volume"] = None if v is None else Decimal(str(v)) / c
        return out


def apply_labels(tape: Sequence[Bar], labels: Sequence[BarLabel]) -> list[Bar]:
    """``tape`` with every non-adjusted label's correction applied (floats) — the repaired tape the gates and
    the dry run's Cockpit columns read."""
    by = {b.d: b for b in labels if b.label != ADJUSTED}
    out = []
    for b in tape:
        lab = by.get(b.d)
        if lab is None:
            out.append(b)
            continue
        r = repaired_bar(b.__dict__, correction(lab))
        out.append(Bar(b.d, *(None if r[f] is None else float(r[f]) for f in _OHLCV)))
    return out


_OHLCV = ("open", "high", "low", "close", "volume")


# --- the gates (run on the REPAIRED tape) ------------------------------------------------------------------


@dataclass
class GateResult:
    t4_before: list[str]
    t4_after: list[str]
    k_steps_after: list[str]
    spot_fail: list[str]
    spot_checked: int = 0
    spot_near: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.t4_after or self.k_steps_after or self.spot_fail)

    def reasons(self) -> list[str]:
        out = []
        if self.t4_after:
            out.append("T4 still flags the repaired tape: " + "; ".join(self.t4_after))
        if self.k_steps_after:
            out.append("a step the size of a split is left: " + "; ".join(self.k_steps_after))
        if self.spot_fail:
            out.append("raw prints disagree with the repaired tape: " + "; ".join(self.spot_fail))
        return out


def t4_labels(tape: Sequence[Bar], *, asof: date) -> list[str]:
    """The T4 monitor's island / cliff findings on ``tape`` over its own window ending ``asof``."""
    since = asof - timedelta(days=window_days())
    fs = tape_findings(
        [TapeBar(b.d, b.close, b.volume) for b in tape if since <= b.d <= asof and b.close]
    )
    return [
        f"{f.kind} x{f.factor:.2f} ({f.dates[0].isoformat()}..{f.dates[-1].isoformat()})"
        for f in fs
    ]


def k_sized_steps(
    before: Sequence[Bar], after: Sequence[Bar], in_force: Sequence[ListedSplit]
) -> list[str]:
    """The steps on the REPAIRED tape where a split basis could still hide: every first session (the last bar
    before it to the first on or after it) and every EDGE the repair drew (adjacent bars it corrected by
    different factors). A step there "looks like a split" when the close moved within ``STEP_BAND`` of a factor
    candidate (``_factor_candidates`` — a factor below the T4 floor is not tested: a 20% step is an ordinary
    day on these names, so only raw prints can judge it) AND the volume moved the other way by at least
    ``STEP_MIN_VOLUME_SHARE`` of it in log terms. Genuine moves elsewhere are T4's to judge: the repair did not
    draw them."""
    b0 = {b.d: b for b in before}
    bars = [b for b in sorted(after, key=lambda b: b.d) if b.close and b.close > 0]
    cands = _factor_candidates(in_force)
    firsts = {s.effective_date for s in in_force}

    def corr(b: Bar) -> float:
        old = b0.get(b.d)
        return b.close / old.close if old and old.close else 1.0  # type: ignore[operator]

    out = []
    for p, q in zip(bars, bars[1:]):
        at_first = any(p.d < s <= q.d for s in firsts)
        edge = abs(_log(corr(q)) - _log(corr(p))) > 1e-9
        if not (at_first or edge):
            continue
        if p.close < MIN_CLOSE or q.close < MIN_CLOSE or not (p.volume and q.volume):  # type: ignore[operator]
            continue
        step = _log(q.close / p.close)  # type: ignore[operator]
        vstep = _log(q.volume / p.volume)
        for f in cands:
            target = _log(f)
            if abs(step - target) > _log(STEP_BAND):
                continue
            if vstep * target < 0 and abs(vstep) >= STEP_MIN_VOLUME_SHARE * abs(target):
                out.append(
                    f"{q.d.isoformat()} x{math.exp(step):.3f} on volume x{math.exp(vstep):.3f}"
                )
    return sorted(set(out))


def plan_gates(
    before: Sequence[Bar],
    after: Sequence[Bar],
    in_force: Sequence[ListedSplit],
    *,
    asof: date,
    spot: Mapping[date, float] | None = None,
) -> GateResult:
    """The three gates on the repaired tape (module docstring). A raw print within 1% or one tick of the
    repaired close / its factor PASSES; within ``SPOT_NOISE_TOL`` it is NEAR (reported — two vendors' closes,
    not two scales); beyond, it FAILS the gate."""
    spot_fail, spot_near = [], []
    checked = 0
    by_d = {b.d: b for b in after}
    for d, raw in sorted((spot or {}).items()):
        b = by_d.get(d)
        if b is None or not b.close:
            continue
        checked += 1
        k = math.prod(float(s.factor) for s in in_force if d < s.effective_date)
        implied = b.close / k
        line = f"{d.isoformat()} repaired {b.close:.6g} / {k:.6g} = {implied:.6g} vs raw {raw:.6g}"
        if abs(implied - raw) <= max(SPOT_TOL * raw, _tick(raw)):
            continue
        if abs(_log(implied / raw)) <= _log(1 + SPOT_NOISE_TOL):
            spot_near.append(f"{line} ({implied / raw - 1:+.1%})")
        else:
            spot_fail.append(line)
    return GateResult(
        t4_before=t4_labels(before, asof=asof),
        t4_after=t4_labels(after, asof=asof),
        k_steps_after=k_sized_steps(before, after, in_force),
        spot_fail=spot_fail,
        spot_checked=checked,
        spot_near=spot_near,
    )
