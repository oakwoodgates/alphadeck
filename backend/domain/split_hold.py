"""The split-night HOLD's pure rules (split plan S5, operator-approved 2026-10-22; ``docs/CALL_LOGIC.md`` §2b).

THE PROBLEM. A vendor's first post-split bar can arrive on the new price scale while the stored history is
still on the old one (or the reverse: a session early). Read across that step, a 1-for-16 reverse split is a
+1,500% "breakout" and a forward split is a crash. MEASURED (Q10, 2026-10-18): 17 honest armed rows rested on
such fake breakouts (GAME 10, NXL 5, MDCX 1, VRME 1), and the T4 monitor can judge a lone cliff only from bar 3.

THE RULE (this module is the WHOLE of it; ``signals/split_hold.py`` reads the point-in-time view and applies
it, one implementation for the nightly, the serve path and every history view):

1. **A split-shaped step** — two consecutive STORED bars whose close moves by at least ``STEP_MIN`` (x3) up
   or down (to 1/3 or less), with the volume moving the OTHER way: ``beta = -ln(volume ratio) / ln(price
   step)`` in ``(0, BETA_MAX]``. A split multiplies the price by k and divides the share volume by k (the
   dollar volume carries straight through); a genuine big move comes on a volume surge in the SAME direction
   (JAGX 2026-09-22: x12.9 on x383 volume -> beta < 0). Both closes at or above the tick floor
   (``MIN_CLOSE``, T4's). The volume is read bar to bar, not as T4's flank-median level: MEASURED by S4
   (``domain.splits`` TAPE_* constants), a single bar's volume is too noisy for T4's BETA_MIN of 0.5 (CMND's
   real 1-for-8 day read beta 0.041) — so any move against the price counts, up to S4's physical bound.
2. **Held** when the step's first new-scale bar falls in the last ``HOLD_SESSIONS`` (3) sessions through the
   call's asof (the step day = session 1) and no split IN FORCE on the asof (``domain.splits.is_applicable``:
   confirmed, or announced once its first session arrived) has its first session in the step's window
   (``covers``). BOTH directions (operator-approved): an unadjusted forward split looks like a crash.
3. **Released** after those sessions. A step no list row ever named then goes to the operator (Admin ->
   Splits, "is this real?", ``unlisted``) — never a page (#7).

Pure: no clock, no I/O, no settings. Rows arrive as plain mappings (a Postgres dict, a replay-mirror dict).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from domain.splits import (
    SESSION_TOLERANCE,
    SET_ASIDE,
    STATUS_ANNOUNCED,
    STATUS_NEEDS_REVIEW,
    is_applicable,
    listed_from_row,
    ratio_label,
    session_distance,
)

# --- the dials (operator-approved 2026-10-22; each pinned by ``tests/domain/test_split_hold.py``) ---------
# STEP_MIN — the price step a hold needs: x3 up or to 1/3 down. MEASURED (plan section 16): no real split-day
# move across 96 measurable split days was split-sized (+26% .. -37%), so a x3 step on split night was always
# the artifact; and the null (S4): 0.0063% of non-split bar pairs step x3-ish with the volume falling.
STEP_MIN = 3.0
# BETA_MAX — S4's ``TAPE_BETA_MAX``: a volume move wildly out of scale with the price step (a flash crash on a
# x100 volume spike) is not a split's inverse volume.
BETA_MAX = 3.0
# HOLD_SESSIONS — how long a step holds: its first new-scale bar is on the asof's session or one of the two
# before it (market sessions, ``domain.splits.session_distance``; a holiday is not one, F5). Three, not "the newest bar": the vendor
# serves the re-scaled bar late (MDCX's 10-08 close arrived re-scaled on the 10-09 night) or early (NXL's
# 08-28 bar arrived on the 1-for-30 scale a session before the 08-31 split, so its 09-01 card read the step on
# the third-newest bar). Sessions, not bar positions: a missing bar (GAME's 08-24) or a stopped feed never
# stretches a hold. After it, an unlisted step goes to the operator.
HOLD_SESSIONS = 3
# MIN_CLOSE — the tick floor (``pipeline.split_basis.MIN_CLOSE``, T4's; pinned equal by test): below two cents
# one quote increment is itself a x1.5..x2 move. Compared with T4's float32 allowance.
MIN_CLOSE = 0.02
# COVER_SESSIONS — how far a listed split's first session may sit outside the step's (prior bar, step bar]
# window and still be the split that caused it: S1's ``SESSION_TOLERANCE`` (a legal effective date vs the first
# session; a vendor that re-scales a session or two early or late — NXL 08-28 vs 08-31, MDCX 10-08 vs 10-12).
COVER_SESSIONS = SESSION_TOLERANCE


@dataclass(frozen=True)
class SplitStep:
    """One split-shaped step between two consecutive stored bars: ``prior_d`` (the last bar on the old scale)
    and ``d`` (the first on the new). ``step`` = close / prior close (x16 for an unadjusted 1-for-16 reverse
    split, about x0.33 for a 3-for-1 forward one)."""

    prior_d: date
    d: date
    prior_close: float
    close: float
    prior_volume: float
    volume: float

    @property
    def step(self) -> float:
        return self.close / self.prior_close

    @property
    def up(self) -> bool:
        return self.step > 1

    @property
    def beta(self) -> float:
        return -math.log(self.volume / self.prior_volume) / math.log(self.step)

    def describe(self) -> str:
        """ "stepped x15.8 on 10-15" / "stepped down to 1/3.1 on 09-29" — the operator's words."""
        how = f"x{_fmt(self.step)}" if self.up else f"down to 1/{_fmt(1 / self.step)}"
        return f"stepped {how} on {_md(self.d)}"


def _fmt(x: float) -> str:
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}"


def _md(d: date) -> str:
    return d.strftime("%m-%d")


def _day(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _above_floor(close: float) -> bool:
    return close >= MIN_CLOSE * (1 - 1e-6)


def step_between(a: Mapping[str, Any], b: Mapping[str, Any]) -> SplitStep | None:
    """The split-shaped step between consecutive bars ``a`` -> ``b`` (rule 1), or None."""
    ca, cb = _num(a.get("close")), _num(b.get("close"))
    va, vb = _num(a.get("volume")), _num(b.get("volume"))
    if not (ca and cb and va and vb) or min(ca, cb, va, vb) <= 0:
        return None  # no inverse-volume evidence without both closes and both volumes
    if not (_above_floor(ca) and _above_floor(cb)):
        return None
    log_step = math.log(cb / ca)
    if abs(log_step) < math.log(STEP_MIN):
        return None
    beta = -math.log(vb / va) / log_step
    if not 0 < beta <= BETA_MAX:
        return None  # the volume moved WITH the price (a genuine move), or out of physical scale
    return SplitStep(_day(a["d"]), _day(b["d"]), ca, cb, va, vb)


def find_steps(bars: Sequence[Mapping[str, Any]]) -> list[SplitStep]:
    """Every split-shaped step on a tape (ascending by ``d``; bars without a positive close are skipped, so a
    step is always between two PRICED bars). Pure."""
    priced = [b for b in bars if (c := _num(b.get("close"))) is not None and c > 0]
    out: list[SplitStep] = []
    for a, b in zip(priced, priced[1:]):
        s = step_between(a, b)
        if s is not None:
            out.append(s)
    return out


def sessions_since(step: SplitStep, asof: date) -> int:
    """Sessions from the step's first new-scale bar to ``asof``: 0 on the step day itself."""
    return session_distance(step.d, asof)


def in_hold_window(step: SplitStep, asof: date) -> bool:
    """Is the step inside its hold (rule 2's window): on the asof's session or one of the two before it?"""
    return 0 <= sessions_since(step, asof) < HOLD_SESSIONS


def _near(step: SplitStep, effective: date) -> bool:
    """Is a listed split with first session ``effective`` the split that caused ``step``? Its first session
    falls between the two bars (``prior_d < effective <= d``), or within ``COVER_SESSIONS`` of the step bar.
    """
    if step.prior_d < effective <= step.d:
        return True
    return abs(session_distance(effective, step.d)) <= COVER_SESSIONS


def nearby(step: SplitStep, split_rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The split-list rows (newest versions, any status) whose first session is near the step."""
    return [r for r in split_rows if _near(step, _day(r["effective_date"]))]


def covers(step: SplitStep, split_rows: Iterable[Mapping[str, Any]], *, on: date) -> bool:
    """Is the step explained by a split IN FORCE on ``on`` (``domain.splits.is_applicable``: confirmed, or
    announced once its first session arrived)? Then it is not held: the price leg (S2) applies that split, and a
    history view puts the bars on one scale (``domain.splits.on_view_scale``)."""
    for r in nearby(step, split_rows):
        s = listed_from_row(r)
        if is_applicable(s.status, s.effective_date, on=on):
            return True
    return False


@dataclass(frozen=True)
class Hold:
    """One company's split-night hold: the step its price-path triggers are held at (``cut`` = the first
    new-scale bar — the held view reads only the bars BEFORE it) and the reason, in the operator's words.
    """

    step: SplitStep
    reason: str

    @property
    def cut(self) -> date:
        return self.step.d


def hold_for(
    bars: Sequence[Mapping[str, Any]],
    split_rows: Sequence[Mapping[str, Any]],
    *,
    asof: date,
) -> Hold | None:
    """Rule 2 for one company: the OLDEST split-shaped step inside the hold window that no split in force on
    ``asof`` covers, with its reason — or None. The oldest, so a two-bar island (a step and its reversal) is
    cut before both. ``bars`` = the view's tape through ``asof``; ``split_rows`` = the split list as the view
    knows it (``PointInTimeData.split_events``). Pure."""
    for step in find_steps(bars):
        if in_hold_window(step, asof) and not covers(step, split_rows, on=asof):
            return Hold(step=step, reason=reason(step, split_rows))
    return None


def reason(step: SplitStep, split_rows: Sequence[Mapping[str, Any]]) -> str:
    """Why the triggers are held, naming the list's own row when one sits near the step (a needs-review or a
    not-yet-in-force announced split, else a set-aside one), else the step itself:

    - "possible unconfirmed 1-for-16 reverse split (SEC 8-K filed 10-09; waiting for a second source)"
    - "possible unconfirmed split (the price stepped x15.8 on 10-15 against the volume; no split filing found
      yet)"
    """
    rows = nearby(step, split_rows)
    live = [r for r in rows if r.get("status") in (STATUS_NEEDS_REVIEW, STATUS_ANNOUNCED)]
    aside = [r for r in rows if r.get("status") in SET_ASIDE]
    if live:
        r = min(live, key=lambda r: abs(session_distance(_day(r["effective_date"]), step.d)))
        if r.get("status") == STATUS_ANNOUNCED:
            # two sources agreed, but its first session is still ahead of the step: not in force yet
            return (
                f"the {_label(r)} is announced for {_md(_day(r['effective_date']))}, but the price "
                f"{step.describe()}, before its first session"
            )
        return f"possible unconfirmed {_label(r)} ({_sources(r)})"
    if aside:
        r = aside[0]
        return (
            f"possible unconfirmed split (the price {step.describe()} against the volume; the "
            f"{_label(r)} listed for {_md(_day(r['effective_date']))} was {r['status']})"
        )
    return (
        f"possible unconfirmed split (the price {step.describe()} against the volume; no split filing "
        "found yet)"
    )


def _label(r: Mapping[str, Any]) -> str:
    s = listed_from_row(r)
    return ratio_label(s.ratio_from, s.ratio_to)


def _sources(r: Mapping[str, Any]) -> str:
    """What a needs-review row stands on, in the operator's words.

    "Polygon" below is the vendor's former name (now Massive.com). It stays on purpose: this text is written onto
    the recorded CallCard's ``missing[]``, so rewording it moves the editions fingerprint and the stored calls —
    the operator's call to take, with a declared rule-log entry (docs/CALL_LOGIC.md, the hold-line table).
    """
    sec = None
    if r.get("sec_accession") or r.get("sec_filed"):
        form = r.get("sec_form") or "filing"
        filed = r.get("sec_filed")
        sec = f"SEC {form}" + (f" filed {_md(_day(filed))}" if filed else "")
    poly = bool(r.get("polygon_id"))
    if sec and poly:
        return f"{sec} and Polygon disagree; waiting for your review"
    if sec:
        return f"{sec}; waiting for a second source"
    if poly:
        return "listed by Polygon; waiting for a second source"
    return "waiting for a second source"


def join(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C" — de-duplicated, first-seen order."""
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def note(triggers: Sequence[str], ticker: str, why: str) -> str:
    """The ``missing[]`` line: "Breakout on BTOC held: possible unconfirmed 1-for-16 reverse split (...)"."""
    return f"{join(triggers)} on {ticker} held: {why}"


def unlisted(step: SplitStep, split_rows: Iterable[Mapping[str, Any]], *, asof: date) -> bool:
    """The escalation test (rule 3): the hold has ended (``HOLD_SESSIONS`` sessions passed) and NO list row
    of any status sits near the step — no source ever named a split there. A row the operator rejected or
    revoked is a decision already taken, and a needs-review row is already on the operator's list.
    """
    return sessions_since(step, asof) >= HOLD_SESSIONS and not nearby(step, split_rows)
