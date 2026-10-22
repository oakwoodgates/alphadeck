"""The shared "is this real?" DATA FLAG (split plan S3, concept DECIDED 2026-10-20) — the pure model.

"The operator needs to know if he is looking at something real or not." A flag says that ONE number on a
Cockpit row (a price return, an RVOL, an insider count, a market cap) rests on data the platform knows is
wrong or cannot vouch for, says why in plain words, and says what clears it. This module is the model and
its pure rules; ``pipeline/data_flags.py`` reads the view and builds the flags.

WHAT A FLAG IS, AND IS NOT:

- **Computed on read, never stored, never on the call card.** A flag changes day to day (a stopped feed's age,
  a split-night hold's three sessions), so a field on the recorded ``CallCard`` would make the nightly
  ``record_if_changed`` re-record every card (the ``pipeline/tape_health.py`` / display-signal rule). It rides
  its own endpoints (``GET /theses/{id}/data-flags``, ``GET /data-flags``).
- **Never a call input (#3/#4).** Nothing in ``calls/`` or ``signals/`` imports this module or its reader
  (pinned by ``tests/domain/test_data_flags.py``). The one flag that names a call effect — the split-night
  hold — READS the call path's own rule (``domain/split_hold.py``, S5); it changes nothing.
- **Rare by construction (WB#3).** A flag exists only for a name with an issue, and it lists only the CELLS
  whose numbers the issue can reach (``cells_for``): a split two years back that no column reads marks
  nothing on the row. No push alert, ever (#7).

THE TWO LEVELS (plan section 9):

- ``in_doubt`` — the number is shown, grayed, with the reason on hover. Real big moves stay visible.
- ``known_wrong`` — the number is not shown: "—" with the reason (a stopped feed's "today" return, a
  market cap withheld for an unreadable ADS ratio).

(The plan's third level, ``not_final``, belongs to the later intraday numbers and is not built.)

THE KINDS AT LAUNCH (``KINDS``):

- ``possible_unconfirmed_split`` — the S5 split-night hold is open on this name: the newest bars carry a
  split-shaped step no confirmed split covers. ``call_affected``: its price triggers are held on the card.
- ``split_needs_review`` — a split on the list stands at ``needs_review`` (one source, or two that disagree)
  with its first session inside the view: it is not applied, so prices across it may sit on two scales.
- ``mixed_scale_history`` — the T4 monitor's island / cliff / flap shapes on the stored tape
  (``pipeline/split_basis.py``), with the S2 follow-up applied: a flapping bar whose newest version sits on the
  split list's scale is SETTLED and draws nothing.
- ``price_feed_stopped`` — the tape-recency rule (``pipeline/tape_health.py``): no bar for ``tape_stale_days``,
  read as renamed / closed / a plain feed gap by the nightly classifier where the view is today's.
- ``market_cap_share_count`` — the market cap multiplies a share count from BEFORE a split the list has not
  applied (or one the tape shows unlisted), or the ADS ratio could not be read (the cap is withheld).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

# --- levels ---------------------------------------------------------------------------------------------
IN_DOUBT = "in_doubt"
KNOWN_WRONG = "known_wrong"
LEVELS = (IN_DOUBT, KNOWN_WRONG)

# --- kinds (wire strings — the FE keys its copy on them; never reformat) -----------------------------------
SPLIT_HOLD = "possible_unconfirmed_split"
SPLIT_REVIEW = "split_needs_review"
MIXED_SCALE = "mixed_scale_history"
FEED_STOPPED = "price_feed_stopped"
MKTCAP_SHARES = "market_cap_share_count"
KINDS = (SPLIT_HOLD, SPLIT_REVIEW, MIXED_SCALE, FEED_STOPPED, MKTCAP_SHARES)

KIND_LABEL = {
    SPLIT_HOLD: "possible unconfirmed split",
    SPLIT_REVIEW: "split needs review",
    MIXED_SCALE: "mixed-scale price history",
    FEED_STOPPED: "price feed stopped",
    MKTCAP_SHARES: "market cap on a pre-split or unreadable share count",
}

# --- what a flag can reach: four column GROUPS, and the Cockpit CELLS in each ------------------------------
PRICE = "price"
VOLUME = "volume"
INSIDER = "insider"
MARKET_CAP = "market_cap"
GROUPS = (PRICE, VOLUME, INSIDER, MARKET_CAP)

# The cells' keys are the Cockpit basket table's (the FE's sort-column ids where one exists).
PRICE_CELLS = ("sma", "ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y", "path")
VOLUME_CELLS = ("rvol8", "rvol20")
INSIDER_CELLS = ("ins_30d", "ins_90d")
MARKET_CAP_CELLS = ("mktcap",)
CELLS_BY_GROUP = {
    PRICE: PRICE_CELLS,
    VOLUME: VOLUME_CELLS,
    INSIDER: INSIDER_CELLS,
    MARKET_CAP: MARKET_CAP_CELLS,
}

# How far back (in TRADING BARS before the as-of bar) each bar-windowed cell reads. Mirrors the display
# members (``signals/display/`` — trailing_returns WINDOWS, sma SMA_SLOW + SLOPE_BARS, price_path BARS, rvol
# BASELINE_BARS / BASELINE_BARS_20); ``tests/domain/test_data_flags.py`` pins them equal, so widening a member
# widens the cell it reaches. The insider cells read CALENDAR windows (``INSIDER_DAYS``).
BAR_WINDOWS = {
    "ret_1d": 1,
    "ret_7d": 7,
    "ret_30d": 30,
    "ret_90d": 90,
    "ret_1y": 252,
    "sma": 200 + 5,  # the 200-bar SMA, and its slope read 5 bars back
    "path": 90 - 1,  # 90 closes, the as-of bar the newest
    "rvol8": 8,
    "rvol20": 20,
}
INSIDER_DAYS = {"ins_30d": 30, "ins_90d": 90}

# --- how a date marks the tape ---------------------------------------------------------------------------
# STEP: the price SCALE changes at this bar (a split's first session, a cliff, a hold's step) — a window that
#       reads a bar BEFORE it and a bar on/after it straddles two scales.
# BAR:  this one bar's own value is in doubt (an island bar, a flapping bar) — a window that reads it.
STEP = "step"
BAR = "bar"


@dataclass(frozen=True)
class Mark:
    """One date a flag puts in doubt, and how (``STEP`` / ``BAR``)."""

    d: date
    how: str


@dataclass(frozen=True)
class Evidence:
    """One piece of evidence behind a flag (#6): a label, and a link when the source has one (an SEC filing)."""

    label: str
    url: str | None = None


@dataclass(frozen=True)
class DataFlag:
    """One data issue on one security, as of the view's day.

    ``columns`` are the CELLS the issue reaches (``cells_for``) — empty when no column on the row reads the
    affected dates (the flag is real, the row's numbers are not touched: it shows in the panel and Admin, never
    as a row mark). ``affects`` are the GROUPS the kind can reach. ``dates`` are the bar dates the Scoreboard
    chart shades (the affected bars), ``since`` when the issue began, ``clears`` what ends it."""

    kind: str
    level: str
    reason: str
    clears: str
    since: date | None = None
    affects: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    dates: tuple[date, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    call_affected: bool = False

    @property
    def label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)


@dataclass(frozen=True)
class SplitLabel:
    """One split on the list as the NamePanel and the Scoreboard chart label it (its newest version as the view
    reads it). The events the panel prints come from these fields: announced (``filed``), confirmed
    (``confirmed_via``), took effect (``effective_date`` on or before the view's day), history corrected
    (``corrected_on``, only when Alpha Deck fixed the vendor's bars — the repair tool's provenance).
    """

    key_date: date
    effective_date: date
    label: str
    factor: float
    direction: str
    status: str
    confirmed_via: str | None = None
    confirmed_by: str | None = None
    value_source: str | None = None
    sec_form: str | None = None
    sec_filed: date | None = None
    sec_url: str | None = None
    polygon: bool = False
    note: str | None = None
    took_effect: bool = False
    corrected_on: date | None = None
    corrected_note: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class MemberFlags:
    """One security's flags and split labels as of the view's day."""

    security_id: object
    ticker: str | None
    flags: tuple[DataFlag, ...] = ()
    splits: tuple[SplitLabel, ...] = field(default_factory=tuple)

    @property
    def marked(self) -> bool:
        """Does the ROW carry a mark? Only when some flag reaches a cell on it (WB#3)."""
        return any(f.columns for f in self.flags)


def _window_start(bar_dates: Sequence[date], back: int) -> date | None:
    """The oldest bar a cell reading ``back`` bars before the as-of bar reads (the whole tape when it is
    shorter — the cell then reads "—" anyway)."""
    if not bar_dates:
        return None
    return bar_dates[max(0, len(bar_dates) - 1 - back)]


def _reaches(marks: Iterable[Mark], lo: date, hi: date) -> bool:
    for m in marks:
        if m.how == STEP and lo < m.d <= hi:
            return True
        if m.how == BAR and lo <= m.d <= hi:
            return True
    return False


def cells_for(
    marks: Sequence[Mark], bar_dates: Sequence[date], asof: date, groups: Iterable[str]
) -> tuple[str, ...]:
    """The Cockpit cells in ``groups`` whose window reads a marked date (``STEP``: the window holds a bar
    before it and the bar itself; ``BAR``: the window holds the bar). ``bar_dates`` = the view's tape,
    ascending (the newest bar is the as-of bar every bar-windowed cell reads back from). Market cap is never
    reached here — only the market-cap kind sets it. Pure; the order follows ``CELLS_BY_GROUP``."""
    out: list[str] = []
    marks = list(marks)
    gs = set(groups)
    hi = bar_dates[-1] if bar_dates else None
    for g in (PRICE, VOLUME):
        if g not in gs or hi is None:
            continue
        for cell in CELLS_BY_GROUP[g]:
            lo = _window_start(bar_dates, BAR_WINDOWS[cell])
            if lo is not None and _reaches(marks, lo, hi):
                out.append(cell)
    if INSIDER in gs:
        for cell in INSIDER_CELLS:
            lo = asof - timedelta(days=INSIDER_DAYS[cell])
            # the window is (asof - N, asof]: a STEP inside it leaves the buys before it read against day
            # lows on another scale; a BAR inside it is a day whose own low may be on another scale
            if any(lo < m.d <= asof for m in marks):
                out.append(cell)
    return tuple(out)


def all_cells(groups: Iterable[str]) -> tuple[str, ...]:
    """Every cell of ``groups`` (a stopped feed reaches all of them)."""
    gs = set(groups)
    return tuple(c for g in GROUPS if g in gs for c in CELLS_BY_GROUP[g])


def md(d: date) -> str:
    """The operator's short date: "2026-10-09" (ISO — unambiguous, the codebase's convention in prose)."""
    return d.isoformat()


def sort_flags(flags: Iterable[DataFlag]) -> list[DataFlag]:
    """Known-wrong first (it hides a number), then by kind order, then the oldest issue first."""
    return sorted(
        flags,
        key=lambda f: (
            0 if f.level == KNOWN_WRONG else 1,
            KINDS.index(f.kind) if f.kind in KINDS else len(KINDS),
            f.since or date.min,
        ),
    )
