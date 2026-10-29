"""Idea Timeline (``docs/dashboards/idea-timeline.md``): each live card's RECORDED state over time (the ``calls``
log as runs of one state on a shared date axis), with its dated catalysts, the operator's logged decisions and
the latest call's two clocks. A "now" view: the time axis IS the history.

PURE: ``idea_timeline(theses, calls, annotations, decisions, clocks, today=...)`` takes the rows as read and
returns the whole payload; the router only reads and calls it. ``today`` is a parameter (the router passes
``domain.market_time.market_today()``), never an ambient clock. Display only: nothing here feeds a call,
scores an idea or writes anything (``docs/DASHBOARDS.md`` rule 1).

The reading rules, in one place:
- Each completed nightly pass writes a card's first row at a new night, and a re-run that night appends only
  when the card changed (``calls_repo.record_if_changed``). A night with several rows reads its LAST one
  (highest ``seq``); the night keeps its row count for the hover.
- A state HOLDS from its night until the card's next recorded night; the current run holds until ``today``.
  A night with no row carries the previous state through it.
- A segment is one run of the same recorded state. Its days are calendar days, ``end - start``, with ``end``
  the next run's first night or ``today`` for the current run.
- ``reconstructed`` (a missed night rebuilt by ``pipeline.backfill``) and an ACTIVE ``exclude`` annotation
  (no ``revoke`` row names it: the rule ``call_annotations_repo.ACTIVE_SQL`` states) are read off each night's
  winning row and drawn as hatches over the span that night holds.
- Times armed = the number of armed runs, each one an entry into armed (the first recorded night included).
- Catalysts: a dated one is a marker (past = before ``today``); undated ones are counted. Decisions: take /
  close / pass at their ``decision_date``; a row a later ``void`` names stays, marked voided; a void row
  draws nothing of its own.
- The axis: the earliest recorded night to ``today``, then on to the latest future marker it shows, capped at
  ``today + FUTURE_CAP_DAYS`` (a marker past the cap never stretches it; the page counts it on its row).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import groupby
from typing import Literal
from uuid import UUID

from domain.enums import State
from domain.thesis import Thesis

FUTURE_CAP_DAYS = 90
DECISION_MARKS = ("take", "close", "pass")  # a 'void' row draws nothing; it grays the row it names

HatchKind = Literal["reconstructed", "excluded"]


# ---------------------------------------------------------------------------------------------------------
# the rows as read (the router builds these from its SELECTs)
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CallRow:
    """One ``calls`` row, without its card blob."""

    id: UUID
    seq: int
    thesis_id: UUID
    asof: date
    state: str
    verdict: str
    reconstructed: bool


@dataclass(frozen=True)
class AnnotationRow:
    """One ``call_annotation`` row of action ``exclude`` or ``revoke`` (``revokes`` names the one it
    withdraws)."""

    id: UUID
    call_id: UUID
    action: str
    reason: str
    revokes: UUID | None


@dataclass(frozen=True)
class DecisionRow:
    """One ``operator_decision`` row."""

    id: UUID
    seq: int
    thesis_id: UUID
    security_id: UUID | None
    action: str
    decision_date: date
    reason: str | None
    voids: UUID | None


@dataclass(frozen=True)
class Clocks:
    """The LATEST calls row's two clocks (``card->>'exit_by'`` / ``card->>'arm_until'``)."""

    exit_by: date | None
    arm_until: date | None


# ---------------------------------------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Hatch:
    """A stretch of a segment whose nights' winning rows carry a flag. ``end`` is exclusive, like a
    segment's. ``reason`` = the active exclude annotation's reason(s); ``None`` for ``reconstructed``.
    """

    kind: HatchKind
    start: date
    end: date
    nights: int
    reason: str | None


@dataclass(frozen=True)
class MultiRowNight:
    asof: date
    rows: int


@dataclass(frozen=True)
class Segment:
    """One run of the same recorded state. ``verdict`` is the run's first night's; ``end`` is exclusive
    (the next run's first night, or ``today`` for the current run)."""

    state: str
    verdict: str
    start: date
    end: date
    days: int
    current: bool
    nights: int
    hatches: tuple[Hatch, ...]
    multi_row_nights: tuple[MultiRowNight, ...]


@dataclass(frozen=True)
class CatalystMark:
    label: str
    kind: str | None
    when_date: date
    when_label: str | None
    past: bool


@dataclass(frozen=True)
class DecisionMark:
    """A take / close / pass. ``ticker`` is read from the card's basket by ``security_id`` (``None`` for a
    card-level decision, or a name no longer on the basket); ``voided`` = a later void row names it.
    """

    id: UUID
    action: str
    decision_date: date
    security_id: UUID | None
    ticker: str | None
    reason: str | None
    voided: bool


@dataclass(frozen=True)
class TimelineRow:
    thesis_id: UUID
    thesis_name: str
    current_state: str | None  # None = no recorded call yet
    current_verdict: str | None
    days_in_state: int | None
    total_days_armed: int
    times_armed: int
    first_recorded: date | None
    last_recorded: date | None
    recorded_calls: int  # every calls row for the card, before the per-night pick
    nights: int  # distinct recorded nights
    segments: tuple[Segment, ...]
    catalysts: tuple[CatalystMark, ...]  # dated, by date
    undated_catalysts: int
    decisions: tuple[DecisionMark, ...]  # by decision_date, then insertion order
    exit_by: date | None
    arm_until: date | None


@dataclass(frozen=True)
class IdeaTimeline:
    today: date
    axis_start: date
    axis_end: date
    future_cap: date  # today + FUTURE_CAP_DAYS
    live_cards: int
    cards_without_calls: int
    recorded_calls: int
    nights: int  # (card, night) pairs recorded
    multi_row_nights: int
    reconstructed_nights: int
    excluded_nights: int
    first_recorded: date | None
    last_recorded: date | None
    rows: tuple[TimelineRow, ...]  # in the cards' given order (list_all: by name)


# ---------------------------------------------------------------------------------------------------------
# the compute
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Night:
    asof: date
    row: CallRow  # the night's winning (last) row
    rows: int
    excluded: str | None  # the winning row's active exclude reason(s)


def active_excludes(annotations: Iterable[AnnotationRow]) -> dict[UUID, str]:
    """``call_id -> reason(s)`` of its ACTIVE exclude annotations: an ``exclude`` no ``revoke`` row names
    (the same rule as ``call_annotations_repo.ACTIVE_SQL``). Several reasons on one row are joined.
    """
    anns = list(annotations)
    revoked = {a.revokes for a in anns if a.action == "revoke" and a.revokes is not None}
    reasons: dict[UUID, list[str]] = {}
    for a in anns:
        if a.action == "exclude" and a.id not in revoked:
            got = reasons.setdefault(a.call_id, [])
            if a.reason not in got:
                got.append(a.reason)
    return {cid: "; ".join(rs) for cid, rs in reasons.items()}


def _nights(rows: Sequence[CallRow], excludes: Mapping[UUID, str]) -> list[_Night]:
    out: list[_Night] = []
    for asof, grp in groupby(sorted(rows, key=lambda r: (r.asof, r.seq)), key=lambda r: r.asof):
        g = list(grp)
        win = g[-1]
        out.append(_Night(asof=asof, row=win, rows=len(g), excluded=excludes.get(win.id)))
    return out


def _hatches(spans: Sequence[tuple[_Night, date, date]]) -> tuple[Hatch, ...]:
    """Merge consecutive flagged nights (same kind, same reason) into one hatch each."""
    out: list[Hatch] = []
    for kind in ("reconstructed", "excluded"):
        cur: Hatch | None = None
        for night, start, end in spans:
            on = night.row.reconstructed if kind == "reconstructed" else night.excluded is not None
            reason = night.excluded if kind == "excluded" else None
            if on and cur is not None and cur.reason == reason:
                cur = Hatch(kind, cur.start, end, cur.nights + 1, reason)
                continue
            if cur is not None:
                out.append(cur)
            cur = Hatch(kind, start, end, 1, reason) if on else None
        if cur is not None:
            out.append(cur)
    return tuple(sorted(out, key=lambda h: (h.start, h.kind)))


def _segments(nights: Sequence[_Night], today: date) -> tuple[Segment, ...]:
    runs: list[list[_Night]] = []
    for n in nights:
        if runs and runs[-1][-1].row.state == n.row.state:
            runs[-1].append(n)
        else:
            runs.append([n])
    out: list[Segment] = []
    for i, run in enumerate(runs):
        current = i == len(runs) - 1
        start = run[0].asof
        # the current run holds until today (never before its own last night)
        end = max(today, run[-1].asof) if current else runs[i + 1][0].asof
        spans = [
            (n, n.asof, run[j + 1].asof if j + 1 < len(run) else end) for j, n in enumerate(run)
        ]
        out.append(
            Segment(
                state=run[0].row.state,
                verdict=run[0].row.verdict,
                start=start,
                end=end,
                days=(end - start).days,
                current=current,
                nights=len(run),
                hatches=_hatches(spans),
                multi_row_nights=tuple(MultiRowNight(n.asof, n.rows) for n in run if n.rows > 1),
            )
        )
    return tuple(out)


def _decision_marks(
    thesis: Thesis, decisions: Sequence[DecisionRow], voided: set[UUID]
) -> tuple[DecisionMark, ...]:
    tickers: dict[UUID, str] = {}
    for m in thesis.basket:
        if m.security_id is not None:
            tickers.setdefault(m.security_id, m.ticker)
    marks = sorted(
        (d for d in decisions if d.action in DECISION_MARKS),
        key=lambda d: (d.decision_date, d.seq),
    )
    return tuple(
        DecisionMark(
            id=d.id,
            action=d.action,
            decision_date=d.decision_date,
            security_id=d.security_id,
            ticker=tickers.get(d.security_id) if d.security_id is not None else None,
            reason=d.reason,
            voided=d.id in voided,
        )
        for d in marks
    )


def _row(
    thesis: Thesis,
    calls: Sequence[CallRow],
    excludes: Mapping[UUID, str],
    decisions: Sequence[DecisionRow],
    voided: set[UUID],
    clocks: Clocks | None,
    today: date,
) -> tuple[TimelineRow, list[_Night]]:
    nights = _nights(calls, excludes)
    segments = _segments(nights, today)
    last = segments[-1] if segments else None
    armed = [s for s in segments if s.state == State.ARMED]
    dated = sorted(
        (c for c in thesis.catalysts if c.when_date is not None),
        key=lambda c: (c.when_date, c.label),
    )
    row = TimelineRow(
        thesis_id=thesis.id,
        thesis_name=thesis.name,
        current_state=last.state if last else None,
        current_verdict=nights[-1].row.verdict if nights else None,
        days_in_state=last.days if last else None,
        total_days_armed=sum(s.days for s in armed),
        times_armed=len(armed),
        first_recorded=nights[0].asof if nights else None,
        last_recorded=nights[-1].asof if nights else None,
        recorded_calls=len(calls),
        nights=len(nights),
        segments=segments,
        catalysts=tuple(
            CatalystMark(
                label=c.label,
                kind=c.kind,
                when_date=c.when_date,
                when_label=c.when_label,
                past=c.when_date < today,
            )
            for c in dated
        ),
        undated_catalysts=sum(1 for c in thesis.catalysts if c.when_date is None),
        decisions=_decision_marks(thesis, decisions, voided),
        exit_by=clocks.exit_by if clocks else None,
        arm_until=clocks.arm_until if clocks else None,
    )
    return row, nights


def _marker_dates(row: TimelineRow) -> Iterable[date]:
    yield from (c.when_date for c in row.catalysts)
    yield from (d.decision_date for d in row.decisions)
    yield from (d for d in (row.exit_by, row.arm_until) if d is not None)


def idea_timeline(
    theses: Iterable[Thesis],
    calls: Iterable[CallRow],
    annotations: Iterable[AnnotationRow],
    decisions: Iterable[DecisionRow],
    clocks: Mapping[UUID, Clocks],
    *,
    today: date,
) -> IdeaTimeline:
    """The Idea Timeline payload over ``theses`` (the live cards; the caller excludes archived ones). Rows
    for any other thesis are ignored."""
    theses = list(theses)
    by_card: dict[UUID, list[CallRow]] = {t.id: [] for t in theses}
    for c in calls:
        if c.thesis_id in by_card:
            by_card[c.thesis_id].append(c)
    decisions = list(decisions)
    voided = {d.voids for d in decisions if d.action == "void" and d.voids is not None}
    decisions_by_card: dict[UUID, list[DecisionRow]] = {}
    for d in decisions:
        decisions_by_card.setdefault(d.thesis_id, []).append(d)
    excludes = active_excludes(annotations)

    rows: list[TimelineRow] = []
    all_nights: list[_Night] = []
    for t in theses:
        row, nights = _row(
            t,
            by_card[t.id],
            excludes,
            decisions_by_card.get(t.id, []),
            voided,
            clocks.get(t.id),
            today,
        )
        rows.append(row)
        all_nights += nights

    firsts = [r.first_recorded for r in rows if r.first_recorded is not None]
    lasts = [r.last_recorded for r in rows if r.last_recorded is not None]
    cap = today + timedelta(days=FUTURE_CAP_DAYS)
    # a marker past the cap is not shown on the axis (the page counts it), so it never stretches the axis
    shown = (d for r in rows for d in _marker_dates(r) if d <= cap)
    latest = max([today, *lasts, *shown])
    return IdeaTimeline(
        today=today,
        axis_start=min([today, *firsts]),
        axis_end=min(cap, latest),
        future_cap=cap,
        live_cards=len(rows),
        cards_without_calls=sum(1 for r in rows if r.nights == 0),
        recorded_calls=sum(r.recorded_calls for r in rows),
        nights=len(all_nights),
        multi_row_nights=sum(1 for n in all_nights if n.rows > 1),
        reconstructed_nights=sum(1 for n in all_nights if n.row.reconstructed),
        excluded_nights=sum(1 for n in all_nights if n.excluded is not None),
        first_recorded=min(firsts) if firsts else None,
        last_recorded=max(lasts) if lasts else None,
        rows=tuple(rows),
    )
