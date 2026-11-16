"""The burn PACE CHECK (V2 extractor correctness PR 3; operator decision D3, 2026-11-15).

The runway meter divides cash by the LATEST quarter's burn. An audit of 460 auto-applied cash-and-burn values found
that for 39 of the 200 cash-burning companies one unusual quarter drives the runway: UP's third quarter burned 17.1M
against 191.3M in its first half, so its runway read about 9 months where its nine-month pace gives about 2. The
arithmetic is right; the reading rests on one quarter.

D3: a quarter far off the year-to-date pace is a VISIBLE CAVEAT on the applied value, never a queued flag. The value
keeps its tier (AUTO stays AUTO and is applied; a FLAG stays a FLAG); the caveat travels with it and is shown wherever
the runway is shown, beside the year-to-date pace and the runway that pace implies.

The rule (``pace_divergence``), pure, its thresholds dials in ``ExtractorConfig``:

- the YEAR-TO-DATE PACE is the year-to-date operating cash flow that ends on the burn's end date, over its span in
  days, scaled to a quarter (``days / DAYS_PER_QUARTER``). It exists only when the year to date covers MORE than the
  one quarter (span above ``quarterly_span_max_days``): a first fiscal quarter has no separate pace and no caveat;
- ``"opposite"`` when the quarter and the pace have opposite signs (one burns, the other generates cash);
- ``"divergent"`` when the quarter's burn is under ``pace_ratio_low`` or over ``pace_ratio_high`` times the pace's;
- nothing when neither burns: a cash-generating company has no runway, so no runway caveat (honest loudness).

STORAGE (migration 0209). The extractor carries the pace on ``ExtractedFact``; the machine writer stores it on the
fact and the candidate as figures (``ytd_quarterly_burn_usd``, ``ytd_burn_days``), and the note still states it in one
fixed machine-written segment (``pace_segment``). ONE reader, ``stored_pace``, reads a stored row's pace: the columns,
else (a row written before 0209, columns NULL) the note's segment (``pace_from_note``), and re-applies the rule to the
stored burn. A correction that does not restate the pace stores NULL columns and its note loses the segment
(``drop_pace``), so no reader re-applies an old pace to a corrected burn. A value with no pace (a first quarter, an
annual filer, a hand-entered or V1 value) shows only the runway at last quarter's pace. The caveat sentence is never
stored: it is computed on read. Display only: never a call input.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig

# A year is 365.25 days on average, a quarter a quarter of that: the calendar fact the pace is scaled by (the plan's
# ``ytd_value / (span_days / 91.3125)``), not a calibration dial.
DAYS_PER_QUARTER = 365.25 / 4
_MONTHS_PER_QUARTER = 3  # quarterly burn -> monthly (workbench/scoring.py's calendar fact)

PaceKind = Literal["divergent", "opposite"]


@dataclass(frozen=True)
class YtdPace:
    """The year-to-date operating cash use behind a quarter's burn. ``burn_usd`` keeps the burn sign: POSITIVE when
    burning, negative when the year to date generated cash (``QUARTERLY_BURN_SIGN``)."""

    burn_usd: float
    start: str
    end: str

    @property
    def days(self) -> int:
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days

    @property
    def quarterly_burn_usd(self) -> float:
        """The year to date scaled to one quarter: ``burn / (days / DAYS_PER_QUARTER)``."""
        return self.burn_usd / (self.days / DAYS_PER_QUARTER)


def pace_divergence(
    quarter_burn: float, ytd_quarterly_burn: float, cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG
) -> PaceKind | None:
    """THE PACE RULE: is the latest quarter far off the year-to-date pace in a way that changes the runway reading?

    Both arguments use the burn sign (positive = burning). ``"opposite"`` when one burns and the other generates
    cash; ``"divergent"`` when the quarter's burn is under ``cfg.pace_ratio_low`` or over ``cfg.pace_ratio_high``
    times the pace's; ``None`` otherwise, and always ``None`` when neither burns (no runway either way).
    """
    if quarter_burn * ytd_quarterly_burn < 0:
        return "opposite"
    if quarter_burn <= 0 and ytd_quarterly_burn <= 0:
        return None  # cash-generating (or flat) on both readings: no runway, so no runway caveat
    if ytd_quarterly_burn == 0:
        return "divergent"  # a burning quarter against a flat year to date: infinitely far apart
    ratio = abs(quarter_burn) / abs(ytd_quarterly_burn)
    if ratio < cfg.pace_ratio_low or ratio > cfg.pace_ratio_high:
        return "divergent"
    return None


def _months(cash: float | None, quarterly_burn: float) -> float | None:
    """Months of runway at a quarterly burn (``workbench.scoring._runway``'s formula); None when not burning."""
    if cash is None or quarterly_burn <= 0:
        return None
    return round(cash / (quarterly_burn / _MONTHS_PER_QUARTER), 1)


def money(v: float, symbol: str = "$") -> str:
    """An amount in short words, sign dropped: "$17.1M", "$1.5B", "$710.0K"; a figure that rounds up to the next unit
    moves to it ("$1.0M", never "$1000.0K"). ``symbol`` is the value's own currency label
    (``domain.cash_currency.currency_symbol``), never converted."""
    a = abs(v)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if round(a / size, 1) >= 1:
            return f"{symbol}{a / size:,.1f}{suffix}"
    return f"{symbol}{a:,.0f}"


def pace_caveat(
    cash: float | None,
    quarter_burn: float,
    ytd_quarterly_burn: float,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    symbol: str = "$",
) -> str | None:
    """The caveat a user reads, one plain sentence with both numbers in the value's currency (``symbol``), or ``None``
    when the pace rule does not fire."""
    kind = pace_divergence(quarter_burn, ytd_quarterly_burn, cfg)
    if kind is None:
        return None
    q_months = _months(cash, quarter_burn)
    y_months = _months(cash, ytd_quarterly_burn)
    pace = f"{money(ytd_quarterly_burn, symbol)} a quarter"
    if quarter_burn <= 0:
        # cash-positive last quarter (the meter reads cash-generative), burning over the year to date
        tail = f": about {y_months} months of runway at that pace" if y_months is not None else ""
        return (
            f"Last quarter was cash-positive ({money(quarter_burn, symbol)}), but the year to date burned {pace}"
            f"{tail}."
        )
    if kind == "opposite":
        tail = (
            f", so the {q_months}-month runway rests on that one quarter"
            if q_months is not None
            else ""
        )
        return f"Last quarter burned {money(quarter_burn, symbol)}, but the year to date generated {pace}{tail}."
    direction = "well below" if abs(quarter_burn) < abs(ytd_quarterly_burn) else "well above"
    out = f"Last quarter's burn ({money(quarter_burn, symbol)}) is {direction} the year-to-date pace ({pace})"
    if q_months is not None and y_months is not None:
        out += (
            f": runway reads {q_months} months at last quarter's pace and {y_months} months at the "
            "year-to-date pace"
        )
    return out + "."


# --- the note segment: written by the extractor, read back by every runway screen ----------------------------

_SEGMENT_RE = re.compile(
    r"year-to-date pace: burn (-?[\d,]+) over (\d{4}-\d{2}-\d{2}) → (\d{4}-\d{2}-\d{2}) \(\d+ days\)"
)


def pace_segment(ytd: YtdPace) -> str:
    """The note's machine-written pace segment (the pace is also stored as figures since 0209):
    "year-to-date pace: burn 208,345,000 over 2026-01-01 → 2026-09-30 (272 days) = 69,943,000 a quarter".
    """
    return (
        f"year-to-date pace: burn {round(ytd.burn_usd):,} over {ytd.start} → {ytd.end} ({ytd.days} days)"
        f" = {round(ytd.quarterly_burn_usd):,} a quarter (a negative burn is cash generated)"
    )


_DROP_RE = re.compile(
    r"(?:; )?year-to-date pace: burn -?[\d,]+ over \d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2} \(\d+ days\)"
    r"(?: = -?[\d,]+ a quarter \(a negative burn is cash generated\))?"
    r"|(?:; )?pace caveat: [^\]]*"
)


def drop_pace(note: str | None) -> str | None:
    """The note without its machine-written pace (the ``pace_segment`` and the ``pace caveat: ...`` sentence). A
    steward's correction that does not restate the pace keeps the rest of its note but not an old pace, which no
    longer describes the corrected burn."""
    if note is None:
        return None
    return _DROP_RE.sub("", note).replace("[; ", "[")


def pace_from_note(note: str | None) -> YtdPace | None:
    """The year-to-date pace recovered from a stored note's ``pace_segment``; None when the note has none."""
    m = _SEGMENT_RE.search(note or "")
    if m is None:
        return None
    return YtdPace(burn_usd=float(m.group(1).replace(",", "")), start=m.group(2), end=m.group(3))


@dataclass(frozen=True)
class RunwayPace:
    """What a runway screen shows beside the runway at last quarter's pace: the year-to-date pace, the runway it
    implies (None when the year to date generated cash or no cash is on file), and the caveat sentence when the pace
    rule fires (None on the common, steady reading)."""

    ytd_quarterly_burn_usd: float
    ytd_days: int
    ytd_runway_months: float | None
    caveat: str | None


def runway_pace(
    cash: float | None,
    quarter_burn: float | None,
    note: str | None,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    symbol: str = "$",
) -> RunwayPace | None:
    """A cash-and-burn value's pace reading, computed on read from its NOTE; None when the note carries no
    year-to-date pace or there is no burn. A stored row is read through ``stored_pace`` (the columns first).
    """
    ytd = pace_from_note(note)
    if ytd is None or ytd.days <= 0:
        return None
    return _reading(cash, quarter_burn, ytd.quarterly_burn_usd, ytd.days, cfg, symbol)


def _reading(
    cash: float | None,
    quarter_burn: float | None,
    pace: float,
    days: int,
    cfg: ExtractorConfig,
    symbol: str,
) -> RunwayPace | None:
    if quarter_burn is None or days <= 0 or not math.isfinite(pace):
        return None
    return RunwayPace(
        ytd_quarterly_burn_usd=round(pace),
        ytd_days=days,
        ytd_runway_months=_months(cash, pace),
        caveat=pace_caveat(cash, quarter_burn, pace, cfg, symbol),
    )


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def stored_pace(
    row: Mapping[str, Any],
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    symbol: str = "$",
) -> RunwayPace | None:
    """THE ONE READER of a stored cash-and-burn row's pace (a fact version or a candidate): the ``ytd_quarterly_burn_
    usd`` / ``ytd_burn_days`` columns (0209); when both are NULL (a row written before 0209) the note's segment
    (``runway_pace``). The caveat is re-computed from the stored numbers by the pure rule, in the value's currency
    (``symbol``). None when no pace is recorded or there is no burn."""
    cash, burn = _num(row.get("cash_usd")), _num(row.get("quarterly_burn_usd"))
    pace, days = row.get("ytd_quarterly_burn_usd"), row.get("ytd_burn_days")
    if pace is not None and days is not None:
        return _reading(cash, burn, float(pace), int(days), cfg, symbol)
    return runway_pace(cash, burn, row.get("note"), cfg, symbol)
