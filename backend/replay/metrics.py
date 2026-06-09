from __future__ import annotations

from statistics import mean, median
from uuid import UUID

from pydantic import BaseModel

from domain.enums import State
from replay.schema import CallSnapshot, Outcome
from replay.scoring import RealizedPrices

# Aggregate the scored outcomes into the metric set — each TIED TO THE SYSTEM'S CLAIM (opinionated on
# timing, deferential on thesis; preserve the edge = early narrative; patch the flaw = timing +
# name-selection), NOT a generic hit-rate. Every metric carries its ``n`` + ``insufficient_n``: on the
# seed only UNH is a long arc, so this produces the instrument + UNH as the worked example, NOT a
# calibration claim (that is step 2, which sweeps cfg and runs against real history at scale).

# Below this many observations a metric reports `insufficient_n=True` and its summary must not be read as
# a claim. RECALIBRATION dial; deliberately conservative for a solo instrument.
MIN_N = 5


class MetricResult(BaseModel):
    name: str
    claim: str  # which system claim this tests (so it's never read as generic hit-rate)
    n: int
    insufficient_n: bool
    summary: dict[str, float | None] = {}
    detail: list[dict] = []
    note: str = ""


class ReplayMetrics(BaseModel):
    n_episodes: int
    n_theses: int
    banner: str
    metrics: list[MetricResult]


def _ok(outcomes: list[Outcome]) -> list[Outcome]:
    return [o for o in outcomes if not o.insufficient_prices and o.forward_return is not None]


def _result(name, claim, values, summary, *, detail=None, note="") -> MetricResult:
    n = len(values)
    return MetricResult(
        name=name,
        claim=claim,
        n=n,
        insufficient_n=n < MIN_N,
        summary=summary,
        detail=detail or [],
        note=note,
    )


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "mean": None, "min": None, "max": None}
    return {
        "median": round(median(values), 4),
        "mean": round(mean(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def _arm_timing(outcomes: list[Outcome]) -> MetricResult:
    ok = _ok(outcomes)
    rets = [o.forward_return for o in ok]
    return _result(
        "arm_timing_forward_return",
        "timing (the flaw patched): the realized return over the hold window from when it armed",
        rets,
        _stats(rets),
        note="the distribution of arm_date->exit_by returns; pair with false_arm + withheld_arm.",
    )


def _early_vs_armed(outcomes: list[Outcome]) -> MetricResult:
    pairs = [(o.warm_return - o.forward_return) for o in _ok(outcomes) if o.warm_return is not None]
    return _result(
        "early_vs_armed_delta",
        "preserve the edge: how much of the move already visible at the WARM the arm-timing wait gave up",
        pairs,
        _stats(pairs),
        note="warm_return - arm_return, to the same exit_by; large positive => the gate clips the early edge.",
    )


def _calibration(outcomes: list[Outcome]) -> MetricResult:
    ok = _ok(outcomes)
    buckets: dict[str, list[float]] = {}
    for o in ok:
        g = o.entry_grade.value if o.entry_grade else "none"
        buckets.setdefault(g, []).append(o.forward_return)
    detail = [{"entry_grade": g, "n": len(v), **_stats(v)} for g, v in sorted(buckets.items())]
    core = median(buckets["core"]) if buckets.get("core") else None
    flip = median(buckets["flip"]) if buckets.get("flip") else None
    monotonic = core is not None and flip is not None and core >= flip
    # Calibration is a PER-BUCKET claim: it needs >= MIN_N in EACH of >= 2 graded buckets — a large TOTAL n
    # across tiny buckets (the seed: a couple core + a couple flip arms) still can't establish monotonicity.
    graded = {g: v for g, v in buckets.items() if g in ("core", "flip")}
    insufficient = len(graded) < 2 or any(len(v) < MIN_N for v in graded.values())
    return MetricResult(
        name="grade_confidence_calibration",
        claim="timing-quality discrimination: do higher-grade arms track better realized outcomes (monotonic)?",
        n=len(ok),
        insufficient_n=insufficient,
        summary={
            "core_median": round(core, 4) if core is not None else None,
            "flip_median": round(flip, 4) if flip is not None else None,
            "monotonic": 1.0 if monotonic else 0.0,
        },
        detail=detail,
        note="calibration needs >= MIN_N per grade bucket; at seed N read the per-bucket n, not the medians.",
    )


def _name_selection(outcomes: list[Outcome]) -> MetricResult:
    ok = _ok(outcomes)
    by_thesis: dict[UUID, list[Outcome]] = {}
    for o in ok:
        by_thesis.setdefault(o.thesis_id, []).append(o)
    cases = []
    for tid, outs in by_thesis.items():
        head = [o.forward_return for o in outs if o.is_headline]
        field = [o.forward_return for o in outs if not o.is_headline]
        if head and field:
            cases.append(
                {
                    "thesis_id": str(tid),
                    "headline_mean": round(mean(head), 4),
                    "field_mean": round(mean(field), 4),
                    "lift": round(mean(head) - mean(field), 4),
                }
            )
    lifts = [c["lift"] for c in cases]
    return _result(
        "name_selection_lift",
        "name-selection (the flaw patched): did the ranked headline outperform the rest of the basket?",
        lifts,
        {"median_lift": round(median(lifts), 4) if lifts else None},
        detail=cases,
        note="relative (isolates selection from theme beta); on the seed this is ~1 theme decision — a case, not a rate.",
    )


def _false_arm(outcomes: list[Outcome]) -> MetricResult:
    judged = [o for o in _ok(outcomes) if o.close_reason != "managing"]
    rets = [o.forward_return for o in judged]
    adverse = sum(1 for r in rets if r <= 0)
    return _result(
        "false_arm_rate",
        "timing precision: arms whose realized hold-window return was adverse (the gate firing wrongly)",
        rets,
        {
            "adverse": float(adverse),
            "total": float(len(rets)),
            "rate": round(adverse / len(rets), 4) if rets else None,
        },
        note="report the raw count at small N; a percentage would imply precision the data can't support.",
    )


def _withheld_arm(
    timeline: dict[UUID, list[CallSnapshot]] | None,
    realized: RealizedPrices | None,
    single_name_security: dict[UUID, UUID] | None,
) -> MetricResult:
    """The gate's OTHER error: over WARMING-with-conviction (withheld) windows of single-name theses, the
    move the operator would have caught by entering at the warm — what the timing discipline cost or saved.
    Multi-name themes are skipped (a withheld return can't be attributed to one name)."""
    runs: list[float] = []
    detail = []
    if timeline and realized and single_name_security:
        for tid, snaps in timeline.items():
            sid = single_name_security.get(tid)
            if sid is None:
                continue
            for start, end in _warming_runs(snaps):
                a = realized.first_close_on_or_after(sid, start)
                b = realized.last_close_through(sid, end)
                if a and b and a[1]:
                    r = b[1] / a[1] - 1
                    runs.append(r)
                    detail.append(
                        {
                            "thesis_id": str(tid),
                            "warm_start": start.isoformat(),
                            "warm_end": end.isoformat(),
                            "withheld_return": round(r, 4),
                        }
                    )
    return _result(
        "withheld_arm_counterfactual",
        "timing's false-negative side: the move during windows the gate WITHHELD (a gate that never fires is useless)",
        runs,
        _stats(runs),
        detail=detail,
        note="single-name theses only; positive => the withhold cost upside, negative => it dodged a drawdown.",
    )


def _exit_vs_rollover(outcomes: list[Outcome]) -> MetricResult:
    ok = [o for o in _ok(outcomes) if o.exit_vs_peak_days is not None and o.peak_return is not None]
    days = [float(o.exit_vs_peak_days) for o in ok]
    given_up = [o.peak_return - o.forward_return for o in ok]
    return _result(
        "exit_by_vs_rollover",
        "timing (the exit side): does the edge persist to exit_by, or decay earlier? (the liveness dials)",
        ok,
        {
            "median_days_exit_after_peak": round(median(days), 4) if days else None,
            "median_return_given_up": round(median(given_up), 4) if given_up else None,
        },
        note="days from the realized peak to exit_by, and the return given up by holding to exit_by past it.",
    )


def _warming_runs(snaps: list[CallSnapshot]):
    """Maximal contiguous runs of WARMING-with-conviction → (start_date, end_date)."""
    snaps = sorted(snaps, key=lambda s: s.asof)
    runs, start, last = [], None, None
    for s in snaps:
        warming = s.state is State.WARMING and s.conviction_grade is not None
        if warming and start is None:
            start = s.asof
        elif not warming and start is not None:
            runs.append((start, last))
            start = None
        if warming:
            last = s.asof
    if start is not None:
        runs.append((start, last))
    return runs


def compute_metrics(
    outcomes: list[Outcome],
    *,
    timeline: dict[UUID, list[CallSnapshot]] | None = None,
    realized: RealizedPrices | None = None,
    single_name_security: dict[UUID, UUID] | None = None,
) -> ReplayMetrics:
    """The seven claim-tied metrics over the scored outcomes (+ the snapshots for the withheld metric).
    Every metric carries n + insufficient_n; the banner states the instrument-not-claim posture."""
    n_theses = len({o.thesis_id for o in outcomes}) if outcomes else 0
    metrics = [
        _arm_timing(outcomes),
        _early_vs_armed(outcomes),
        _calibration(outcomes),
        _name_selection(outcomes),
        _false_arm(outcomes),
        _withheld_arm(timeline, realized, single_name_security),
        _exit_vs_rollover(outcomes),
    ]
    return ReplayMetrics(
        n_episodes=len(outcomes),
        n_theses=n_theses,
        banner=(
            "INSTRUMENT, NOT A CLAIM. On the seed only UNH is a long forward arc; treat metrics flagged "
            "insufficient_n as scaffold. Calibration / name-selection need step-2 history at scale."
        ),
        metrics=metrics,
    )
