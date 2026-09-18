"""POOLED analysis — the algorithm as the unit, never the thesis.

The operator's reframe, and it is the safer frame as well as the truer one: the question is "globally, does
the timing algorithm behave sensibly", not "does it behave on this thesis". Pooling episodes across every
thesis tests the ALGORITHM. Slicing outcomes by thesis tests the THESIS, which is the leaderboard trap and
a #4 violation — the platform is opinionated about timing and deferential about the idea, and a screen that
ranked theses by realized return would quietly stop being deferential.

So Q4 is enforced STRUCTURALLY here rather than by convention: `PooledReport` has no thesis field, the
slices are by ALGORITHM only (Key-1 source x confirmation grade x co-arm bucket x close reason), and a test
walks the serialized payload looking for any thesis identifier. A per-thesis view is reachable only as a
single-thesis drill-down on the surface, from the run's own ledger — never from this report.

Two further honesty rules carried from the spec:

- **Every metric reports its two nulls beside it.** An absolute number on a hindsight universe is not
  evidence; the same number against random timing and random names on the same universe is.
- **Every metric reports its n and an `insufficient_n` flag**, and effective n is far below the episode
  count because 82% of episodes armed alongside a co-member. The co-arm slice is what makes that visible
  rather than assumed.

FIRING DIAGNOSTICS carry no outcome at all — counts, arm density, trigger mix, close reasons. They are the
half of this report that has no hindsight risk, and they are what catches a broken detector.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, Field

from backtest.nulls import EpisodeNulls
from replay.metrics import MIN_N
from replay.schema import Episode

SLICE_KEYS: tuple[str, ...] = ("key1_source", "confirmation_grade", "co_arm_bucket", "close_reason")

#: WHICH excess-over-basket is the headline — ONE place, and the report carries the answer so the surface
#: reads it rather than deciding for itself.
#:
#: The median, since B: the mean is what an equal-weight basket POSITION earned, and one moonshot in a
#: twenty-name basket moves it by a twentieth of its own move while leaving the typical member where it
#: was. "The algorithm beat the basket" off the mean can therefore mean "the algorithm missed the one name
#: that carried the theme". The median asks what the algorithm beat the TYPICAL name by, which is the same
#: question the name-selection null asks and is the one a timing platform has to answer. Both are always
#: reported; only the order and the emphasis change.
HeadlineExcess = Literal["median", "mean"]
HEADLINE_EXCESS: HeadlineExcess = "median"


class Stat(BaseModel):
    """One number with its sample size — never a bare figure."""

    n: int = 0
    median: float | None = None
    mean: float | None = None

    @classmethod
    def of(cls, values: Sequence[float | None]) -> "Stat":
        vals = [v for v in values if v is not None]
        if not vals:
            return cls()
        return cls(
            n=len(vals),
            median=round(median(vals), 6),
            mean=round(sum(vals) / len(vals), 6),
        )


class MetricWithNulls(BaseModel):
    """A pooled metric and the two things it must be read against.

    ``actual`` alone is uninterpretable on a universe assembled with hindsight. ``vs_timing`` and
    ``vs_name`` are the same statistic over the two null distributions, and ``excess`` strips the theme's
    own drift by measuring against the basket's equal-weight move over the identical window. A reader who
    quotes ``actual`` without them is quoting the operator's stock-picking, not the algorithm's timing.
    """

    name: str
    claim: str
    actual: Stat
    #: against the basket's equal-weight MEAN move — what an equal-weight basket position earned.
    #: UNCHANGED by B: same population, same arithmetic, same number.
    excess: Stat
    #: against the basket's MEDIAN move — what the TYPICAL member did. The headline since B; see
    #: `HEADLINE_EXCESS`. Empty on a report written before B, which is honest: those reports had no
    #: median to quote.
    excess_vs_basket_median: Stat = Field(default_factory=Stat)
    vs_timing: Stat
    vs_name: Stat
    insufficient_n: bool = True
    note: str = ""


class Slice(BaseModel):
    """One ALGORITHM slice. ``key`` names only algorithm dimensions — see SLICE_KEYS."""

    key: dict[str, str] = Field(default_factory=dict)
    n: int = 0
    actual: Stat = Field(default_factory=Stat)
    excess: Stat = Field(default_factory=Stat)
    excess_vs_basket_median: Stat = Field(default_factory=Stat)
    vs_timing: Stat = Field(default_factory=Stat)
    vs_name: Stat = Field(default_factory=Stat)
    insufficient_n: bool = True


class FiringDiagnostics(BaseModel):
    """Counts and mixes — NO outcome, so no hindsight risk. This is what catches a broken detector."""

    n_episodes: int = 0
    n_scoreable: int = 0
    key1_source_mix: dict[str, int] = Field(default_factory=dict)
    confirmation_grade_mix: dict[str, int] = Field(default_factory=dict)
    co_arm_bucket_mix: dict[str, int] = Field(default_factory=dict)
    close_reason_mix: dict[str, int] = Field(default_factory=dict)
    entry_grade_mix: dict[str, int] = Field(default_factory=dict)
    # the independence fact, stated as a number rather than left to be inferred
    pct_armed_with_a_co_member: float | None = None
    widest_single_session_group: int = 0
    # How many trading sessions the TIMING null had to draw from. Reported because a short window makes
    # that null vacuous and the failure is silent otherwise: on a four-session run there is roughly one
    # alternative entry date per episode, every draw prices a near-empty forward window, and `vs_timing`
    # comes back as a median of 0.0 on a handful of draws -- which reads like "the algorithm ties with
    # chance" when it actually means "there was no chance to compare against". The name null is unaffected
    # (it varies the NAME, not the date), which is why the two can disagree sharply on a short window.
    timing_candidate_sessions: int = 0


class PooledReport(BaseModel):
    """The whole pooled view. Deliberately carries NO thesis identifier of any kind — see the module
    docstring; a test walks this payload to keep it that way."""

    n_episodes: int = 0
    n_scoreable: int = 0
    null_draws: int = 0
    null_seed: str = ""
    min_n: int = MIN_N
    #: which excess-over-basket this report is to be READ on. On the artifact rather than in the surface's
    #: head, so a stored report says how it was meant to be read; a report written before B carries no
    #: field and the surface falls back to "mean", which is what those reports were read under.
    headline_excess: HeadlineExcess = HEADLINE_EXCESS
    banner: str = ""
    metrics: list[MetricWithNulls] = Field(default_factory=list)
    slices: list[Slice] = Field(default_factory=list)
    diagnostics: FiringDiagnostics = Field(default_factory=FiringDiagnostics)


def _mix(values: Sequence[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        k = "none" if v is None else (v.value if hasattr(v, "value") else str(v))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def _slice_key(ep: Episode) -> dict[str, str]:
    def s(v: Any) -> str:
        return "none" if v is None else (v.value if hasattr(v, "value") else str(v))

    return {k: s(getattr(ep, k, None)) for k in SLICE_KEYS}


def build_report(
    episodes: list[Episode],
    nulls: list[EpisodeNulls],
    *,
    draws: int,
    seed: str,
    sessions: int = 0,
) -> PooledReport:
    """Pool the run's episodes and their nulls into the algorithm-level view."""
    by_key = {(n.thesis_id, n.security_id, n.arm_date): n for n in nulls}
    paired = [
        (ep, by_key[(ep.thesis_id, ep.security_id, ep.arm_date)])
        for ep in episodes
        if (ep.thesis_id, ep.security_id, ep.arm_date) in by_key
    ]
    scoreable = [(ep, n) for ep, n in paired if n.forward_return is not None]

    actual = [n.forward_return for _, n in scoreable]
    excess = [n.excess_return for _, n in scoreable]
    excess_median = [n.excess_return_vs_median for _, n in scoreable]
    timing = [d.forward_return for _, n in scoreable for d in n.timing]
    name = [d.forward_return for _, n in scoreable for d in n.name]

    metrics = [
        MetricWithNulls(
            name="arm_timing_forward_return",
            claim=(
                "timing (the flaw patched): the realized return over the hold window from when it armed, "
                "against the same name entered at random and against random other names on the same day"
            ),
            actual=Stat.of(actual),
            excess=Stat.of(excess),
            excess_vs_basket_median=Stat.of(excess_median),
            vs_timing=Stat.of(timing),
            vs_name=Stat.of(name),
            insufficient_n=len(actual) < MIN_N,
            note=(
                "read the four together: `actual` alone is a statement about a universe assembled with "
                "hindsight, and only the gaps are about the algorithm. The two excess figures are one "
                "population two ways -- against the basket's MEDIAN move (the typical member, the "
                "headline) and against its equal-weight MEAN (what an equal-weight basket position "
                "earned); they part company exactly when the basket is skewed, which is when the "
                "difference matters."
            ),
        )
    ]

    slices: dict[tuple, list] = {}
    for ep, n in scoreable:
        slices.setdefault(tuple(sorted(_slice_key(ep).items())), []).append(n)
    sliced = [
        Slice(
            key=dict(k),
            n=len(group),
            actual=Stat.of([g.forward_return for g in group]),
            excess=Stat.of([g.excess_return for g in group]),
            excess_vs_basket_median=Stat.of([g.excess_return_vs_median for g in group]),
            vs_timing=Stat.of([d.forward_return for g in group for d in g.timing]),
            vs_name=Stat.of([d.forward_return for g in group for d in g.name]),
            insufficient_n=len(group) < MIN_N,
        )
        for k, group in sorted(slices.items(), key=lambda kv: str(kv[0]))
    ]

    with_co = [ep for ep in episodes if ep.co_arm_count > 0]
    diagnostics = FiringDiagnostics(
        n_episodes=len(episodes),
        n_scoreable=len(scoreable),
        key1_source_mix=_mix([ep.key1_source for ep in episodes]),
        confirmation_grade_mix=_mix([ep.confirmation_grade for ep in episodes]),
        co_arm_bucket_mix=_mix([ep.co_arm_bucket for ep in episodes]),
        close_reason_mix=_mix([ep.close_reason for ep in episodes]),
        entry_grade_mix=_mix([ep.entry_grade for ep in episodes]),
        pct_armed_with_a_co_member=(round(len(with_co) / len(episodes), 4) if episodes else None),
        widest_single_session_group=max((ep.co_arm_count + 1 for ep in episodes), default=0),
        timing_candidate_sessions=sessions,
    )

    return PooledReport(
        n_episodes=len(episodes),
        n_scoreable=len(scoreable),
        null_draws=draws,
        null_seed=seed,
        metrics=metrics,
        slices=sliced,
        diagnostics=diagnostics,
        banner=(
            "POOLED ACROSS THESES — the unit is the ALGORITHM, never the thesis. Absolute returns on this "
            "universe are biased upward by selection (the baskets were authored in 2026 over names that "
            "had already moved), so read every metric against its two nulls and its excess-over-basket. "
            "Episodes are NOT independent: the co-arm slice shows how many arrived in bursts, and the "
            "effective n is far below the episode count."
        ),
    )
