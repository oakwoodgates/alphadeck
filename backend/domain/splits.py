"""The split list's pure rules (split plan S1): the price factor, the session distance, and the AUTO-CONFIRM
rule — a split is confirmed only when two INDEPENDENT sources agree (operator decision, 2026-10-20). Pure: no
clock, no settings, no I/O. The SEC side arrives already extracted (``ingest/edgar/split_filing.py``); this
module never reads a filing and never guesses a value.

**The two sources.** Polygon's split reference (a market-data vendor's row: execution date = the first session
at the new price, ``split_from`` -> ``split_to``) and an SEC filing (the company's own words: a ratio, and
either the first trading session — kind ``trading`` — or a legal effective date — kind ``legal``).

**Confirmed (``auto:sec+polygon``)** when, for at least one SEC filing in the window:

1. the ratios are EXACTLY equal (compared as exact fractions: 16 -> 1 equals 16 -> 1, never "close"), AND
2. the dates agree:
   - a ``trading`` date (the filing states the first session itself) must EQUAL Polygon's execution date;
   - a ``legal`` effective date may sit up to ``SESSION_TOLERANCE`` sessions from it — a split effective at
     5:00 p.m. on a Friday first trades on the Monday — and the first session recorded is Polygon's.

The earliest such filing is the confirming one: its filing date is when the split became public
(``valid_from``, never later than the execution date itself).

**Announced** — the same agreement, but the first session is after the backfill's as-of: listed, not yet in
effect (a split is never ``confirmed`` before its day — a later slice scales the bars BEFORE a confirmed
split's first session, and a future one would rescale the whole tape early).

**Needs review** — everything else, with both sources' evidence kept on the row: Polygon alone, an SEC filing
that disagrees on the ratio or the date, or a filing the extractor abstained on. The row carries Polygon's
values until the operator confirms one source's values or rejects it. Nothing here picks between two
disagreeing values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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

VIA_AUTO = "auto:sec+polygon"
VIA_OPERATOR = "operator"

SOURCE_SEC = "sec"
SOURCE_POLYGON = "polygon"
SOURCE_BOTH = "both"

# How far (in Mon-Fri sessions; market holidays are not modeled, the ``domain/trading_days`` calendar) a SEC
# LEGAL effective date may sit from Polygon's execution date and still agree. Two covers the shapes the real
# filings use — effective 12:01 a.m. the same day (0), 5:00 p.m. the session before (1), a Friday-evening
# certificate trading on the Monday after a holiday (2). A filing that states the first TRADING session itself
# must match exactly: a one-session disagreement there is a real disagreement, and it goes to the operator.
SESSION_TOLERANCE = 2


@dataclass(frozen=True)
class PolygonEvidence:
    """One Polygon split-reference row: the first session at the new price and ``split_from`` OLD shares ->
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
    """The matcher's verdict for one Polygon row: the values the list carries, its status, and the evidence."""

    key_date: date
    effective_date: date
    ratio_from: Decimal
    ratio_to: Decimal
    status: str
    confirmed_via: str | None
    value_source: str
    valid_from: date
    polygon: PolygonEvidence
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
    """``f(trade_date) -> factor``: puts a price AS TRADED on ``trade_date`` onto the stored tape's scale —
    the product of k over the listed splits in force on ``through`` with a first session after the trade date
    and on or before ``through`` (``scale_after``). ``rows`` are the split list as the reader's view knows it
    (``PointInTimeData.split_events``: recorded by its ``known_at``); ``through`` is the market day of that
    ``known_at`` — the bars the view reads carry every split the system had applied by then, so the factor
    must carry them too. A split the view did not know is not in ``rows`` and is never applied (#1). 1.0 when
    nothing applies. Dollar amounts (shares x price) are split-invariant: only a PER-SHARE value is scaled.
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
    """Do the SEC filing's date and Polygon's execution date agree (the rule in the module docstring)?
    ``(agree, why)`` — ``why`` names the gap either way."""
    if sec.effective_date is None:
        return False, sec.date_note or "the SEC filing states no date"
    gap = session_distance(sec.effective_date, polygon.execution_date)
    if sec.date_kind == "trading":
        if sec.effective_date == polygon.execution_date:
            return True, ""
        return False, (
            f"the SEC filing states the first session {sec.effective_date.isoformat()}, Polygon "
            f"{polygon.execution_date.isoformat()} ({gap:+d} session(s))"
        )
    if abs(gap) <= SESSION_TOLERANCE:
        return True, (
            ""
            if gap == 0
            else f"SEC legal effective date {sec.effective_date.isoformat()}, first session "
            f"{polygon.execution_date.isoformat()} per Polygon ({gap:+d} session(s))"
        )
    return False, (
        f"the SEC filing's effective date {sec.effective_date.isoformat()} is {gap:+d} session(s) from "
        f"Polygon's {polygon.execution_date.isoformat()} (tolerance ±{SESSION_TOLERANCE})"
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
    return f"Polygon: {ratio_label(p.split_from, p.split_to)}, first session {p.execution_date.isoformat()}"


def decide(
    polygon: PolygonEvidence,
    sec: Sequence[SecEvidence],
    *,
    asof: date,
    unread: int = 0,
) -> SplitDecision:
    """The verdict for one Polygon row given every SEC filing read in its window (the module docstring)."""
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
        note = f"{_sec_said(first)} and Polygon agree."
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
    abstained = [s for s in sec if not s.has_ratio and s.about_a_split]
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
            f"Polygon only to confirm: SEC {chosen.form} {chosen.accession} (filed {chosen.filed.isoformat()}) "
            f"mentions a split but the extractor abstained — {chosen.abstain_reason}."
        )
        valid_from = polygon.execution_date
    else:
        note = (
            f"Polygon only — no SEC filing read in the window states this split "
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
