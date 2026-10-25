"""The split list's pure rules (split plan S1): the price factor, the session distance, and the AUTO-CONFIRM
rule — a split is confirmed only when two INDEPENDENT sources agree (operator decision, 2026-10-20). Pure: no
clock, no settings, no I/O. The SEC side arrives already extracted (``ingest/edgar/split_filing.py``); this
module never reads a filing and never guesses a value.

**The two sources.** Massive.com's split reference (a market-data vendor's row: execution date = the first session
at the new price, ``split_from`` -> ``split_to``) and an SEC filing (the company's own words: a ratio, and
either the first trading session — kind ``trading`` — or a legal effective date — kind ``legal``).

**Confirmed (``auto:sec+polygon``)** when, for at least one SEC filing in the window:

1. the ratios are EXACTLY equal (compared as exact fractions: 16 -> 1 equals 16 -> 1, never "close"), AND
2. the dates agree:
   - a ``trading`` date (the filing states the first session itself) must EQUAL Massive.com's execution date;
   - a ``legal`` effective date may sit up to ``SESSION_TOLERANCE`` sessions from it — a split effective at
     5:00 p.m. on a Friday first trades on the Monday — and the first session recorded is Massive.com's.

The earliest such filing is the confirming one: its filing date is when the split became public
(``valid_from``, never later than the execution date itself).

**Announced** — the same agreement, but the first session is after the backfill's as-of: listed, not yet in
effect (a split is never ``confirmed`` before its day — a later slice scales the bars BEFORE a confirmed
split's first session, and a future one would rescale the whole tape early).

**Needs review** — everything else, with both sources' evidence kept on the row: Massive.com alone, an SEC filing
that disagrees on the ratio or the date, or a filing the extractor abstained on. The row carries Massive.com's
values until the operator confirms one source's values or rejects it. Nothing here picks between two
disagreeing values.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

STATUS_ANNOUNCED = "announced"
STATUS_CONFIRMED = "confirmed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_REJECTED = "rejected"
STATUS_REVOKED = "revoked"
STATUSES = (
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_REVOKED,
)
# the statuses an operator has set aside (kept on the list, grayed — WB#2)
SET_ASIDE = (STATUS_REJECTED, STATUS_REVOKED)

# The extractor's abstain reason for a filing whose every ratio is a PAST split's (``ingest/edgar/split_filing``
# ``PAST_RATIO_ONLY`` — the same words; this module reads no filing and imports nothing from ingest/).
PAST_RATIO_ONLY = "only a past split's ratio is stated"

VIA_AUTO = "auto:sec+polygon"
VIA_OPERATOR = "operator"

SOURCE_SEC = "sec"
SOURCE_POLYGON = "polygon"
SOURCE_BOTH = "both"

# How far (in Mon-Fri sessions; market holidays are not modeled, the ``domain/trading_days`` calendar) a SEC
# LEGAL effective date may sit from Massive.com's execution date and still agree. Two covers the shapes the real
# filings use — effective 12:01 a.m. the same day (0), 5:00 p.m. the session before (1), a Friday-evening
# certificate trading on the Monday after a holiday (2). A filing that states the first TRADING session itself
# must match exactly: a one-session disagreement there is a real disagreement, and it goes to the operator.
SESSION_TOLERANCE = 2


@dataclass(frozen=True)
class PolygonEvidence:
    """One Massive.com split-reference row: the first session at the new price and ``split_from`` OLD shares ->
    ``split_to`` NEW shares (16 -> 1 for a 1-for-16 reverse split)."""

    id: str
    ticker: str
    execution_date: date
    split_from: Decimal
    split_to: Decimal


@dataclass(frozen=True)
class SecEvidence:
    """One SEC filing and what the extractor read from it. ``ratio_from``/``ratio_to`` are ``None`` when it
    abstained (``abstain_reason``); ``about_a_split`` = it mentioned a split ratio at all."""

    accession: str
    form: str
    filed: date
    url: str
    ratio_from: Decimal | None = None
    ratio_to: Decimal | None = None
    effective_date: date | None = None
    date_kind: str | None = None
    passage: str = ""
    document: str | None = None
    abstain_reason: str | None = None
    date_note: str | None = None
    about_a_split: bool = False

    @property
    def has_ratio(self) -> bool:
        return self.ratio_from is not None and self.ratio_to is not None


@dataclass(frozen=True)
class SplitDecision:
    """The matcher's verdict for one Massive.com row (or, S4, one SEC-only candidate — ``polygon`` is then
    ``None``): the values the list carries, its status, and the evidence."""

    key_date: date
    effective_date: date
    ratio_from: Decimal
    ratio_to: Decimal
    status: str
    confirmed_via: str | None
    value_source: str
    valid_from: date
    polygon: PolygonEvidence | None
    sec: SecEvidence | None
    note: str


def price_factor(ratio_from: Decimal | int, ratio_to: Decimal | int) -> Fraction:
    """k = old / new: prices before the split are multiplied by k (16 for a 1-for-16 reverse split, 1/2 for a
    2-for-1), volumes divided by it. Exact."""
    return Fraction(Decimal(ratio_from)) / Fraction(Decimal(ratio_to))


# --- READING the list (split plan S2): which splits are IN FORCE, and the tape's scale ----------------------
#
# The list is read by the price loader (one scale for the stored tape), the repair tool, the insider price
# screen and the market cap. They share ONE rule for which listed splits count, and ONE product for the scale.


@dataclass(frozen=True)
class ListedSplit:
    """One split as a reader of the list sees it: the newest version of one logical split (as-of whatever
    ``known_at`` the read used). ``factor`` = k = old / new (``price_factor``), exact."""

    key_date: date
    effective_date: date
    ratio_from: Decimal
    ratio_to: Decimal
    status: str
    sec_accession: str | None = None
    version_id: str | None = None

    @property
    def factor(self) -> Fraction:
        return price_factor(self.ratio_from, self.ratio_to)

    @property
    def label(self) -> str:
        return ratio_label(self.ratio_from, self.ratio_to)


def listed_from_row(row: Mapping[str, Any]) -> ListedSplit:
    """A ``security_split`` row (a DB dict, a replay-mirror dict, or a ``SplitVersion`` dump) as a
    ``ListedSplit``. Numerics arrive as Decimal (Postgres) or float (the Parquet mirror); a float goes through
    ``repr`` so 6.25 stays 6.25 and the factor stays exact."""

    def dec(v: Any) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(repr(v) if isinstance(v, float) else str(v))

    def day(v: Any) -> date:
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])

    vid = row.get("id")
    return ListedSplit(
        key_date=day(row["key_date"]),
        effective_date=day(row["effective_date"]),
        ratio_from=dec(row["ratio_from"]),
        ratio_to=dec(row["ratio_to"]),
        status=str(row["status"]),
        sec_accession=row.get("sec_accession"),
        version_id=None if vid is None else str(vid),
    )


def is_applicable(status: str, effective_date: date, *, on: date) -> bool:
    """Is a listed split IN FORCE on market day ``on``? ``confirmed``, or ``announced`` once its first session
    has arrived (an announced split already had two sources agree — S1's rule — so a split filed in advance is
    in force on its own first night). Never before its first session, whatever the status. ``needs_review`` /
    ``rejected`` / ``revoked`` never are: a split the operator has not settled is not applied to anything.
    """
    if effective_date > on:
        return False
    return status in (STATUS_CONFIRMED, STATUS_ANNOUNCED)


def applicable(splits: Sequence[ListedSplit], *, on: date) -> list[ListedSplit]:
    """The splits in force on ``on``, oldest first."""
    return sorted(
        (s for s in splits if is_applicable(s.status, s.effective_date, on=on)),
        key=lambda s: (s.effective_date, s.key_date),
    )


def pending(splits: Sequence[ListedSplit], *, on: date) -> list[ListedSplit]:
    """The LIVE splits that are not in force on ``on`` — ``needs_review``, or ``announced`` with a first
    session still ahead. A reader leaves their dates alone (it neither applies nor undoes them) and says it is
    waiting for them; rejected / revoked splits are not splits and are not returned."""
    return sorted(
        (
            s
            for s in splits
            if s.status in (STATUS_NEEDS_REVIEW, STATUS_ANNOUNCED)
            and not is_applicable(s.status, s.effective_date, on=on)
        ),
        key=lambda s: (s.effective_date, s.key_date),
    )


def tape_scale(rows: Sequence[Mapping[str, Any]], *, through: date) -> Callable[[date], float]:
    """``f(trade_date) -> factor``: puts a price AS TRADED on ``trade_date`` onto the scale of the tape the
    view reads — the product of k over the listed splits in force on ``through`` with a first session after
    the trade date and on or before ``through`` (``scale_after``). ``rows`` are the split list as the view
    knows it (``PointInTimeData.split_events``); ``through`` is the market day of the view's ``known_at``:
    on a HISTORY view (the public clock) that is the as-of day, and the bars are on that day's scale
    (``on_view_scale``); on the live read it is today, and the stored tape is on today's scale. So a split in
    force by the view's day is applied and none after it (#1). 1.0 when nothing applies. Dollar amounts
    (shares x price) are split-invariant: only a PER-SHARE value is scaled.
    """
    splits = applicable([listed_from_row(r) for r in rows], on=through)
    if not splits:
        return lambda d: 1.0
    memo: dict[date, float] = {}

    def factor(d: date) -> float:
        if d not in memo:
            memo[d] = float(scale_after(splits, d, through=through))
        return memo[d]

    return factor


def scale_after(splits: Sequence[ListedSplit], d: date, *, through: date) -> Fraction:
    """The cumulative price factor that puts a value AS TRADED on ``d`` onto the scale of ``through``: the
    product of k over the splits in force on ``through`` whose first session is after ``d`` and on or before
    ``through`` (chained splits multiply). 1 when none. Prices are multiplied by it, share counts and share
    volumes divided."""
    k = Fraction(1)
    for s in applicable(splits, on=through):
        if d < s.effective_date <= through:
            k *= s.factor
    return k


# --- the VIEW's scale (S2, the public clock) ----------------------------------------------------------------
#
# A history view at day D reads each price bar on D's scale: the splits in force by D applied, none after. The
# stored value is on the scale of the bar's newest version (``db.public_clock``: every public-clock price row
# carries ``split_store_through`` = that version's market date), so the view multiplies by the splits in force
# by D and divides by the ones the store carries. ONE pure function, both engines (the live PIT and the replay
# mirror), so a history view and a public-clock backtest read the same numbers.

# the column a public-clock price row carries (``db.public_clock.STORE_THROUGH_COL`` is this name)
STORE_THROUGH_COL = "split_store_through"
# the statuses a reader applies (``is_applicable``) — also what decides which bars a listed split re-scales
LIVE_STATUSES = (STATUS_CONFIRMED, STATUS_ANNOUNCED)
_PRICE_COLS = ("open", "high", "low", "close")


def _as_day(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def on_view_scale(
    rows: Sequence[Mapping[str, Any]],
    split_rows: Sequence[Mapping[str, Any]],
    *,
    through: date,
) -> list[Mapping[str, Any]]:
    """Put a security's public-clock price rows on the VIEW's scale — the splits in force on ``through`` (the
    view's market day) and none after.

    ``split_rows`` is the split list's CURRENT reading for the security (the newest version of every logical
    split — ``split_repo.current_rows`` / the mirror's split file): the factor between two days is the list's,
    whichever day a split became public, because a split in force by ``through`` was public by it and one
    that was not yet public is not in force. Per row with ``split_store_through`` = S (the scale the stored
    value is on): prices x ``scale_after(d, through) / scale_after(d, S)``, volume divided by it. A row
    without the column (a record-clock read, an older mirror) or whose factor is 1 is returned AS IS (the same
    object); a converted row is a new dict with float prices (both engines then hold the same numbers). Pure.
    """
    live = [s for s in (listed_from_row(r) for r in split_rows) if s.status in LIVE_STATUSES]
    if not live or not rows:
        return list(rows)
    out: list[Mapping[str, Any]] = []
    for r in rows:
        store = r.get(STORE_THROUGH_COL)
        if store is None:
            out.append(r)
            continue
        d = _as_day(r["d"])
        k = scale_after(live, d, through=through) / scale_after(live, d, through=_as_day(store))
        if k == 1:
            out.append(r)
            continue
        c = dict(r)
        f = float(k)
        for col in _PRICE_COLS:
            if c.get(col) is not None:
                c[col] = float(c[col]) * f
        if c.get("volume") is not None:
            c["volume"] = float(c["volume"]) / f
        out.append(c)
    return out


def same_ratio(a_from: Decimal, a_to: Decimal, b_from: Decimal, b_to: Decimal) -> bool:
    """Exactly the same split (16 -> 1 == 32 -> 2; 6.25 -> 1 != 6.2 -> 1)."""
    return price_factor(a_from, a_to) == price_factor(b_from, b_to)


def direction(ratio_from: Decimal, ratio_to: Decimal) -> str:
    return "reverse" if Decimal(ratio_from) > Decimal(ratio_to) else "forward"


def _fmt(v: Decimal) -> str:
    return format(Decimal(v).normalize(), "f")


def ratio_label(ratio_from: Decimal, ratio_to: Decimal) -> str:
    """The operator's words: "1-for-16 reverse split", "2-for-1 forward split"."""
    if direction(ratio_from, ratio_to) == "reverse":
        return f"{_fmt(ratio_to)}-for-{_fmt(ratio_from)} reverse split"
    return f"{_fmt(ratio_to)}-for-{_fmt(ratio_from)} forward split"


def session_distance(a: date, b: date) -> int:
    """Signed Mon-Fri sessions from ``a`` to ``b``: 0 on the same day, +1 from a Friday to the next Monday,
    negative when ``b`` is before ``a``. Holidays count as sessions (not modeled)."""
    if a == b:
        return 0
    lo, hi = (a, b) if a < b else (b, a)
    n = 0
    d = lo
    while d < hi:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n if b > a else -n


def date_agreement(sec: SecEvidence, polygon: PolygonEvidence) -> tuple[bool, str]:
    """Do the SEC filing's date and Massive.com's execution date agree (the rule in the module docstring)?
    ``(agree, why)`` — ``why`` names the gap either way."""
    if sec.effective_date is None:
        return False, sec.date_note or "the SEC filing states no date"
    gap = session_distance(sec.effective_date, polygon.execution_date)
    if sec.date_kind == "trading":
        if sec.effective_date == polygon.execution_date:
            return True, ""
        return False, (
            f"the SEC filing states the first session {sec.effective_date.isoformat()}, Massive.com "
            f"{polygon.execution_date.isoformat()} ({gap:+d} session(s))"
        )
    if abs(gap) <= SESSION_TOLERANCE:
        return True, (
            ""
            if gap == 0
            else f"SEC legal effective date {sec.effective_date.isoformat()}, first session "
            f"{polygon.execution_date.isoformat()} per Massive.com ({gap:+d} session(s))"
        )
    return False, (
        f"the SEC filing's effective date {sec.effective_date.isoformat()} is {gap:+d} session(s) from "
        f"Massive.com's {polygon.execution_date.isoformat()} (tolerance ±{SESSION_TOLERANCE})"
    )


def _sec_said(s: SecEvidence) -> str:
    ratio = ratio_label(s.ratio_from, s.ratio_to) if s.has_ratio else "no ratio"  # type: ignore[arg-type]
    when = (
        f"{'first session' if s.date_kind == 'trading' else 'effective'} {s.effective_date.isoformat()}"
        if s.effective_date
        else "no date"
    )
    return f"SEC {s.form} {s.accession} (filed {s.filed.isoformat()}): {ratio}, {when}"


def _poly_said(p: PolygonEvidence) -> str:
    return f"Massive.com: {ratio_label(p.split_from, p.split_to)}, first session {p.execution_date.isoformat()}"


def decide(
    polygon: PolygonEvidence,
    sec: Sequence[SecEvidence],
    *,
    asof: date,
    unread: int = 0,
) -> SplitDecision:
    """The verdict for one Massive.com row given every SEC filing read in its window (the module docstring)."""
    tail = f" {unread} SEC filing(s) in the window could not be read." if unread else ""
    readable = [s for s in sec if s.has_ratio]
    confirming = []
    for s in readable:
        if not same_ratio(s.ratio_from, s.ratio_to, polygon.split_from, polygon.split_to):  # type: ignore[arg-type]
            continue
        agree, why = date_agreement(s, polygon)
        if agree:
            confirming.append((s, why))
    if confirming:
        first, why = min(confirming, key=lambda c: (c[0].filed, c[0].accession))
        effective = polygon.execution_date
        future = effective > asof
        note = f"{_sec_said(first)} and Massive.com agree."
        if why:
            note += f" {why}."
        if len(confirming) > 1:
            note += f" {len(confirming)} SEC filings state it; the earliest is kept."
        if future:
            note += f" Takes effect {effective.isoformat()}."
        return SplitDecision(
            key_date=polygon.execution_date,
            effective_date=effective,
            ratio_from=polygon.split_from,
            ratio_to=polygon.split_to,
            status=STATUS_ANNOUNCED if future else STATUS_CONFIRMED,
            confirmed_via=VIA_AUTO,  # announced or confirmed, it is the two sources that agreed
            value_source=SOURCE_BOTH,
            valid_from=min(first.filed, polygon.execution_date),
            polygon=polygon,
            sec=first,
            note=note + tail,
        )

    def gap(s: SecEvidence) -> tuple[int, date, str]:
        anchor = s.effective_date or s.filed
        return (abs(session_distance(anchor, polygon.execution_date)), s.filed, s.accession)

    # a filing about THIS split that disagrees: the same ratio on another date, or a date that agrees with
    # another ratio
    related = [
        s
        for s in readable
        if same_ratio(s.ratio_from, s.ratio_to, polygon.split_from, polygon.split_to)  # type: ignore[arg-type]
        or (s.effective_date is not None and date_agreement(s, polygon)[0])
    ]
    # a filing that abstained because every ratio it states is a PAST split's (``PAST_RATIO_ONLY``) restates
    # older splits: in this split's window (filed at most ``WINDOW_AFTER_DAYS`` after it) it can never be about
    # this one, so it is never shown as this split's evidence (LGHL's 6-K/A of 2025-03-07 restating its 2023 ADS
    # change beside the 2025 split)
    abstained = [
        s
        for s in sec
        if not s.has_ratio and s.about_a_split and s.abstain_reason != PAST_RATIO_ONLY
    ]
    chosen: SecEvidence | None = None
    if related:
        chosen = min(related, key=gap)
        parts = []
        if not same_ratio(chosen.ratio_from, chosen.ratio_to, polygon.split_from, polygon.split_to):  # type: ignore[arg-type]
            parts.append("the ratios differ")
        agree, why = date_agreement(chosen, polygon)
        if not agree:
            parts.append(why)
        note = f"{_sec_said(chosen)}; {_poly_said(polygon)} — {'; '.join(parts)}."
        valid_from = min(chosen.filed, polygon.execution_date)
    elif abstained:
        chosen = min(abstained, key=gap)
        note = (
            f"Massive.com only to confirm: SEC {chosen.form} {chosen.accession} (filed {chosen.filed.isoformat()}) "
            f"mentions a split but the extractor abstained — {chosen.abstain_reason}."
        )
        valid_from = polygon.execution_date
    else:
        note = (
            f"Massive.com only — no SEC filing read in the window states this split "
            f"({len(sec)} filing(s) read)."
        )
        if readable:
            near = min(readable, key=gap)
            note += f" Nearest split filing: {_sec_said(near)} — a different split?"
        valid_from = polygon.execution_date
    return SplitDecision(
        key_date=polygon.execution_date,
        effective_date=polygon.execution_date,
        ratio_from=polygon.split_from,
        ratio_to=polygon.split_to,
        status=STATUS_NEEDS_REVIEW,
        confirmed_via=None,
        value_source=SOURCE_POLYGON,
        valid_from=valid_from,
        polygon=polygon,
        sec=chosen,
        note=note + tail,
    )


# --- S4 (split plan, 2026-10-22): the SEC-only candidate and the TAPE as a confirming source -------------------
#
# The nightly detector (``pipeline/detect_splits.py``) finds splits the S1 rule above cannot settle alone: a
# split an SEC filing states before (or without) a Massive.com row, and a split one documentary source states that
# the stored price tape can confirm. ``decide`` above stays THE rule for a Massive.com row; nothing below changes it.

VIA_SEC_TAPE = "auto:sec+tape"
VIA_POLYGON_TAPE = "auto:polygon+tape"
AUTO_VIAS = (VIA_AUTO, VIA_SEC_TAPE, VIA_POLYGON_TAPE)

# THE TAPE TOLERANCE — MEASURED 2026-10-22 on a read-only copy of prod (``security_split`` + ``fact_price_eod``),
# the step read on each bar's FIRST-SERVED version (what the vendor served the night it was stored):
# - REAL SPLIT DAYS: the 18 listed reverse splits whose first-served step lands near k on the listed date
#   (12 with the prior bar served before the split, 6 from a history load the vendor never adjusted) stepped
#   by ln(step / k) in [-0.464, +0.187] (SMX 2025-11-18 1-for-8: x5.03, the day's real move -37%; MI 1-for-80:
#   x96.4) — the split-day moves section 16 of the plan measured on Massive.com's closes (+26% .. -37%). The volume
#   fell on every one (beta = -ln(volume ratio) / ln(step) in [0.041 (CMND), 2.19 (DUKR)]); a single bar's
#   volume is too noisy for T4's flank-median BETA_MIN of 0.5 (CMND's real 1-for-8 day would fail it).
# - NON-SPLIT DAYS (the null): 331,022 consecutive first-served bar pairs on the basket union, more than 10
#   days from any listed split. Share that would pass at TAPE_TOLERANCE = 0.5 with the volume falling, by k:
#   k=1.8 0.448% · 2 0.071% · 3 0.0063% · 4 0.0018% · 5 0.0015% · 8..50 <= 0.0006% · 100 0. (An upper bound:
#   the few k >= 8 passes look like unlisted splits themselves — LIVG 2026-03-18 x8.5, NEXR 2024-11-20 x14.6,
#   QUCY 2024-12-03 x34.8.) The tape is only ever read on the ONE session a documentary source names.
# Hence: within x e^0.5 (x1.65) of k either way, volume down (beta in (0, 3]), reverse splits of k >= 1.8 only —
# below that a real day moves as much as the split, and a FORWARD split on an unadjusted tape is a fall on
# rising volume, which a real crash looks exactly like (T4's documented blind spot): the tape never confirms one.
TAPE_TOLERANCE = 0.5
TAPE_K_MIN = 1.8
TAPE_BETA_MAX = 3.0


@dataclass(frozen=True)
class TapeEvidence:
    """The stored price tape at a candidate first session: the bar on ``session`` and the stored bar before it
    (``prior``), each as the vendor FIRST served it — the as-traded print, before any later re-basing (the
    vendor's, or the split list's own once a split is applied). ``prior_served_before`` = the prior bar's first
    version was served before ``session`` (so it cannot already carry this split's adjustment)."""

    session: date
    prior: date
    prior_close: float
    close: float
    prior_volume: float | None
    volume: float | None
    prior_served_before: bool

    @property
    def step(self) -> float:
        return self.close / self.prior_close

    @property
    def volume_ratio(self) -> float | None:
        if not self.prior_volume or not self.volume or self.prior_volume <= 0 or self.volume <= 0:
            return None
        return self.volume / self.prior_volume

    def describe(self) -> str:
        vr = self.volume_ratio
        vol = (
            f", volume {self.prior_volume:,.0f} -> {self.volume:,.0f} (x{vr:.3g})"
            if vr is not None
            else ", no volume on one of the bars"
        )
        served = "" if self.prior_served_before else " (the prior bar was first served after it)"
        return (
            f"Tape as first served: {self.prior.isoformat()} close {self.prior_close:.4g} -> "
            f"{self.session.isoformat()} {self.close:.4g} (x{self.step:.3g}){vol}{served}"
        )


def tape_agreement(
    tape: TapeEvidence | None, ratio_from: Decimal, ratio_to: Decimal, first_session: date
) -> tuple[bool, str]:
    """Does the tape confirm a split of ``ratio_from`` -> ``ratio_to`` first trading on ``first_session``?
    ``(agree, why)`` — the rule and its MEASURED tolerance are in the constants above. Exact on the date: the
    step must land ON ``first_session`` (a vendor that pre-adjusts a session early does not confirm it).
    """
    if tape is None or tape.session != first_session:
        return False, f"no stored bar on {first_session.isoformat()} to read"
    k = float(price_factor(ratio_from, ratio_to))
    if k < TAPE_K_MIN:
        return False, (
            f"a {ratio_label(ratio_from, ratio_to)} moves the price by less than x{TAPE_K_MIN} up, which the "
            "tape cannot tell from a real day's move"
        )
    step = tape.step
    if step <= 0 or abs(math.log(step / k)) > TAPE_TOLERANCE:
        return False, f"the tape steps x{step:.3g} on {first_session.isoformat()}, not about x{k:g}"
    vr = tape.volume_ratio
    if vr is None:
        return False, "the tape has no volume on one of the two bars"
    beta = -math.log(vr) / math.log(step)
    if not 0 < beta <= TAPE_BETA_MAX:
        return False, f"the volume moved x{vr:.3g}, not the other way to the price"
    return True, ""


def with_tape(d: SplitDecision, tape: TapeEvidence | None) -> SplitDecision:
    """A needs-review decision that ONE documentary source settles alone — Massive.com's row with no SEC filing
    disagreeing, or an SEC filing that states the first TRADING session (a legal date leaves the first session
    open by a session or two, and the tape is never asked to pick it) — upgraded to ``confirmed`` when the tape
    agrees on its first session (``tape_agreement``). Any other needs-review decision comes back with the tape
    reading on its first session added to the note (the evidence, either way); an announced or confirmed one
    comes back unchanged (two sources already settled it — the S1 content stays S1's). Two sources that
    DISAGREE are never arbitrated by the tape."""
    if tape is None or tape.session != d.effective_date or d.status != STATUS_NEEDS_REVIEW:
        return d
    note = f"{d.note} {tape.describe()}."
    via: str | None = None
    if (
        d.value_source == SOURCE_POLYGON
        and d.polygon is not None
        and (d.sec is None or not d.sec.has_ratio)
    ):
        via = VIA_POLYGON_TAPE
    elif (
        d.value_source == SOURCE_SEC
        and d.polygon is None
        and d.sec is not None
        and d.sec.date_kind == "trading"
    ):
        via = VIA_SEC_TAPE
    if via is None:
        return replace(d, note=note)
    agree, why = tape_agreement(tape, d.ratio_from, d.ratio_to, d.effective_date)
    if not agree:
        return replace(d, note=f"{note} The tape does not confirm it: {why}.")
    src = "Massive.com's" if via == VIA_POLYGON_TAPE else "the SEC filing's"
    return replace(
        d,
        status=STATUS_CONFIRMED,
        confirmed_via=via,
        note=f"{note} The tape confirms {src} first session and ratio.",
    )


def related(s: SecEvidence, polygon: PolygonEvidence) -> bool:
    """Is a read SEC filing about the same split as a Massive.com row — the same ratio, or a date that agrees
    (``decide``'s own test for the filing it shows beside a disagreeing row)?"""
    if not s.has_ratio:
        return False
    return same_ratio(s.ratio_from, s.ratio_to, polygon.split_from, polygon.split_to) or (  # type: ignore[arg-type]
        s.effective_date is not None and date_agreement(s, polygon)[0]
    )


def cluster_sec(filings: Sequence[SecEvidence]) -> list[list[SecEvidence]]:
    """SEC filings that state a ratio AND a date, grouped into one candidate split each: the same exact ratio
    and dates within ``SESSION_TOLERANCE`` sessions of the group's first (an 8-K announcing it and another
    restating it). Ordered by first filing."""
    groups: list[list[SecEvidence]] = []
    for s in sorted(
        (s for s in filings if s.has_ratio and s.effective_date is not None),
        key=lambda s: (s.filed, s.accession),
    ):
        for g in groups:
            g0 = g[0]
            if same_ratio(s.ratio_from, s.ratio_to, g0.ratio_from, g0.ratio_to) and (  # type: ignore[arg-type]
                abs(session_distance(g0.effective_date, s.effective_date)) <= SESSION_TOLERANCE  # type: ignore[arg-type]
            ):
                g.append(s)
                break
        else:
            groups.append([s])
    return groups


def decide_sec_only(group: Sequence[SecEvidence], *, asof: date) -> SplitDecision:
    """The verdict for one SEC-only candidate (``cluster_sec``): ``needs_review`` — one source never confirms
    a split (S1's rule) — carrying the SEC values: the first TRADING session one of the filings states, else
    the earliest filing's legal date. ``with_tape`` may then confirm it. ``valid_from`` = the earliest filing.
    """
    trading = [s for s in group if s.date_kind == "trading"]
    pool = trading or list(group)
    chosen = min(pool, key=lambda s: (s.filed, s.accession))
    first = min(group, key=lambda s: (s.filed, s.accession))
    dates = sorted({s.effective_date for s in pool if s.effective_date is not None})
    note = f"SEC only: {_sec_said(chosen)}; no Massive.com row lists this split (tonight's pull)."
    if len(group) > 1:
        note += f" {len(group)} SEC filings state it."
    if len(dates) > 1:
        note += " They state different dates: " + ", ".join(d.isoformat() for d in dates) + "."
    if chosen.date_kind != "trading":
        note += " A legal effective date: the first session may be a session later."
    eff: date = chosen.effective_date  # type: ignore[assignment]
    if eff > asof:
        note += f" Takes effect {eff.isoformat()}."
    return SplitDecision(
        key_date=eff,
        effective_date=eff,
        ratio_from=chosen.ratio_from,  # type: ignore[arg-type]
        ratio_to=chosen.ratio_to,  # type: ignore[arg-type]
        status=STATUS_NEEDS_REVIEW,
        confirmed_via=None,
        value_source=SOURCE_SEC,
        valid_from=min(first.filed, eff),
        polygon=None,
        sec=chosen,
        note=note,
    )
