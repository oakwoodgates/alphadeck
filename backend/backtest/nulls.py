"""THE TWO NULL MODELS — the only honest way to read a backtest on a hindsight universe.

Every basket in this system was authored in 2026 over names that had, in part, already moved. Absolute
forward returns of armed windows on that universe are biased upward by selection, and no clock fixes that:
`basket_snapshot` history begins 2026-09-15, so a 2025 window has no honest roster to read. Quoting a raw
hit rate off that tape would be measuring the operator's hindsight and calling it the algorithm's edge.

What CAN be asked is RELATIVE, where both sides carry the same bias:

  TIMING NULL          same name, K random entry dates in the window, same horizon, same scorer.
                       Did the machine pick the right TIME on this name?
  NAME-SELECTION NULL  same entry date, K random OTHER members of that thesis's roster, same horizon.
                       Did the machine pick the right NAME when the theme moved?

They map one-to-one onto the two flaws the platform exists to patch — timing, and narrative-to-name
selection — which is why these two and not a generic benchmark.

**They run on the SCORED layer.** A null costs K draws per episode, and at K=50 over ~200 episodes that is
10,000 priced windows; re-running the harness for them would multiply a multi-hour sweep by fifty. Nothing
here re-opens a point-in-time view: the draws read `RealizedPrices`, the deliberately forward-unbounded
reader the scorer already uses. An import-graph test pins that `replay.pit` is unreachable from here, the
same structural rule `tests/replay/test_scoring.py` already holds for the scorer itself.

**Reproducibility is not optional.** A null is evidence, so it has to be re-derivable from the manifest: K
and the seed ride there, and each episode draws from its OWN sub-seed derived from `(seed, thesis,
security, arm_date)`. Per-episode rather than one global stream so that adding an episode -- a re-run over
a wider window, say -- does not reshuffle every other episode's draws and silently move the result.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from random import Random
from statistics import median
from uuid import UUID

from replay.schema import Episode
from replay.scoring import RealizedPrices, score_window

DEFAULT_DRAWS = 50

# A thesis's roster AS OF a date. The caller supplies it (the run has the connection and the same
# `thesis_repo.get_asof` the harness uses), which keeps this module free of the database entirely.
#
# HONEST CAVEAT, and it belongs on the surface rather than only in a manifest field: `basket_snapshot`
# history begins 2026-09-15, so for any backtest window before that this resolves to TODAY's basket via
# the harness's own documented fallback. The name-selection null and the real arm therefore draw from the
# SAME counterfactual roster -- which is the symmetric, honest choice, and is not the same thing as a
# point-in-time roster.
RosterAt = Callable[[UUID, date], Sequence[UUID]]


@dataclass(frozen=True)
class NullDraw:
    """One counterfactual decision, priced through the same scorer as the real one."""

    kind: str  # "timing" | "name"
    thesis_id: UUID
    episode_security_id: UUID
    episode_arm_date: date
    security_id: (
        UUID  # the name actually priced (differs from the episode's only for the name null)
    )
    entry_date: date
    horizon_days: int
    forward_return: float | None
    #: against the basket's equal-weight MEAN move — what an equal-weight basket position earned
    excess_return: float | None
    #: against the basket's MEDIAN move — what the TYPICAL member earned (B)
    excess_return_vs_median: float | None = None


@dataclass(frozen=True)
class EpisodeNulls:
    """One episode's real result beside its two null distributions."""

    thesis_id: UUID
    security_id: UUID
    arm_date: date
    horizon_days: int
    forward_return: float | None
    excess_return: float | None
    excess_return_vs_median: float | None
    timing: list[NullDraw]
    name: list[NullDraw]


def episode_seed(seed: str, ep: Episode) -> int:
    """This episode's own RNG seed. Derived from the run seed plus the episode's identity, so a draw is a
    pure function of (run, episode) and adding episodes never reshuffles the existing ones."""
    key = f"{seed}|{ep.thesis_id}|{ep.security_id}|{ep.arm_date.isoformat()}"
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def horizon_days(ep: Episode) -> int | None:
    """The episode's OWN hold horizon. Every null draw uses it, so a null is never scored over a window
    the real decision did not claim -- the comparison would otherwise be between two horizons rather than
    between two decisions."""
    if ep.exit_by is None or ep.arm_date is None:
        return None
    days = (ep.exit_by - ep.arm_date).days
    return days if days > 0 else None


@dataclass(frozen=True)
class BasketMove:
    """What a thesis's basket did over one window, on the SAME priced population, two ways.

    TWO STATISTICS, ONE POPULATION — the members that actually priced. They answer different questions and
    a skewed basket separates them hard:

    - ``mean`` — **what an equal-weight basket position earned.** A real portfolio answer, and the figure
      every existing `excess_return` is measured against; it is kept exactly as it was.
    - ``median`` — **what the TYPICAL member did.** One moonshot in a twenty-name basket lifts the mean by
      a twentieth of its move and leaves the median where it was, so "the algorithm beat the basket" read
      off the mean can mean "the algorithm missed the one name that carried it" — and read off the median
      it means "the algorithm beat the typical name", which is the question the name-selection null asks.

    Neither is the truth on its own, which is why both ride every draw. The headline is
    `pooled.HEADLINE_EXCESS` — one place.
    """

    mean: float | None
    median: float | None
    #: how many members PRICED — the denominator both statistics were taken over
    n: int


_NO_MOVE = BasketMove(mean=None, median=None, n=0)


class _BasketBenchmark:
    """The basket's close-to-close move over a window — the denominator that strips out "everything went
    up" — as a mean AND a median over the same priced members.

    Memoized on ``(thesis, entry, exit)``: the nulls ask for it K times per episode with identical
    arguments, and it costs one priced window per basket member. **The median is free**: both statistics
    are taken over the one list of returns this already prices, so B adds no priced window to a run. Read
    from the SAME mirror as every other return here, so the benchmark and the thing it benchmarks can
    never come from different tapes.
    """

    def __init__(self, realized: RealizedPrices, roster_at: RosterAt) -> None:
        self._realized = realized
        self._roster_at = roster_at
        self._cache: dict[tuple[UUID, date, date], BasketMove] = {}

    def __call__(self, thesis_id: UUID, entry: date, exit_: date) -> BasketMove:
        key = (thesis_id, entry, exit_)
        if key not in self._cache:
            members = list(self._roster_at(thesis_id, entry))
            rets = [
                w.forward_return
                for sid in members
                if (w := score_window(self._realized, sid, entry, exit_)) is not None
            ]
            # EQUAL-weight, and the denominator is the names that actually PRICED, not the roster size:
            # dividing by names with no tape would drag the benchmark toward zero and flatter every
            # excess return against it. The median is taken over the SAME list, so the two figures never
            # describe different populations.
            self._cache[key] = (
                BasketMove(mean=sum(rets) / len(rets), median=median(rets), n=len(rets))
                if rets
                else _NO_MOVE
            )
        return self._cache[key]


def _excess(ret: float | None, bench: float | None) -> float | None:
    return None if ret is None or bench is None else ret - bench


def draw_nulls(
    episodes: list[Episode],
    realized: RealizedPrices,
    *,
    roster_at: RosterAt,
    sessions: Sequence[date],
    seed: str,
    draws: int = DEFAULT_DRAWS,
) -> list[EpisodeNulls]:
    """Both nulls for every scoreable episode.

    ``sessions`` is the run's own trading-session list — the timing null draws entry dates from the days
    the market was actually open in this window, not from a calendar, so a draw is a decision that could
    have been made rather than one that could not.
    """
    bench = _BasketBenchmark(realized, roster_at)
    out: list[EpisodeNulls] = []
    for ep in episodes:
        h = horizon_days(ep)
        if h is None or ep.arm_date is None:
            continue  # an episode with no horizon has nothing for a null to match
        real = score_window(realized, ep.security_id, ep.arm_date, ep.exit_by)
        real_ret = real.forward_return if real else None
        real_exit = real.exit_date if real else ep.exit_by
        rng = Random(episode_seed(seed, ep))

        timing: list[NullDraw] = []
        # the days this decision could have been made instead: the run's real sessions, minus the one it
        # WAS made on (a null that can draw the real answer is not a null)
        candidates = [d for d in sessions if d != ep.arm_date]
        for entry in _sample(rng, candidates, draws):
            exit_ = entry + (ep.exit_by - ep.arm_date)
            w = score_window(realized, ep.security_id, entry, exit_)
            timing.append(
                NullDraw(
                    kind="timing",
                    thesis_id=ep.thesis_id,
                    episode_security_id=ep.security_id,
                    episode_arm_date=ep.arm_date,
                    security_id=ep.security_id,
                    entry_date=entry,
                    horizon_days=h,
                    forward_return=w.forward_return if w else None,
                    excess_return=_excess(
                        w.forward_return if w else None, bench(ep.thesis_id, entry, exit_).mean
                    ),
                    excess_return_vs_median=_excess(
                        w.forward_return if w else None, bench(ep.thesis_id, entry, exit_).median
                    ),
                )
            )

        name: list[NullDraw] = []
        # ...and the names it could have been made ON, from the roster AS OF the entry date, minus itself
        peers = [s for s in roster_at(ep.thesis_id, ep.arm_date) if s != ep.security_id]
        for sid in _sample(rng, peers, draws):
            w = score_window(realized, sid, ep.arm_date, ep.exit_by)
            name.append(
                NullDraw(
                    kind="name",
                    thesis_id=ep.thesis_id,
                    episode_security_id=ep.security_id,
                    episode_arm_date=ep.arm_date,
                    security_id=sid,
                    entry_date=ep.arm_date,
                    horizon_days=h,
                    forward_return=w.forward_return if w else None,
                    excess_return=_excess(
                        w.forward_return if w else None,
                        bench(ep.thesis_id, ep.arm_date, ep.exit_by).mean,
                    ),
                    excess_return_vs_median=_excess(
                        w.forward_return if w else None,
                        bench(ep.thesis_id, ep.arm_date, ep.exit_by).median,
                    ),
                )
            )

        out.append(
            EpisodeNulls(
                thesis_id=ep.thesis_id,
                security_id=ep.security_id,
                arm_date=ep.arm_date,
                horizon_days=h,
                forward_return=real_ret,
                excess_return=_excess(real_ret, bench(ep.thesis_id, ep.arm_date, real_exit).mean),
                excess_return_vs_median=_excess(
                    real_ret, bench(ep.thesis_id, ep.arm_date, real_exit).median
                ),
                timing=timing,
                name=name,
            )
        )
    return out


def _sample(rng: Random, population: Sequence, k: int) -> list:
    """``k`` draws WITHOUT replacement, or the whole population when it is smaller.

    Without replacement because the question is "how would other choices have done", and drawing the same
    alternative twice weights it twice for no reason. Taking the whole population when it is smaller than
    ``k`` is the honest degradation: a four-name basket has three peers, and reporting three is right
    where resampling to fifty would manufacture confidence the data cannot support. The per-slice ``n``
    carries that through to the metric."""
    if not population:
        return []
    if len(population) <= k:
        return list(population)
    return rng.sample(list(population), k)
