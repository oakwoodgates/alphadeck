"""Catalyst Calendar (``docs/dashboards/catalyst-calendar.md``): every live card's dated catalysts and its latest
call's two deadlines on one calendar, the passed catalysts to prune and the undated ones to date. A "now" view:
catalysts are edited in place with no history.

PURE: ``catalyst_calendar(theses, latest, today=...)`` takes the cards and each card's latest recorded call as
read and returns the whole payload; the router only reads and calls it. ``today`` is a parameter (the router
passes ``domain.market_time.market_today()``), never an ambient clock. Display only: nothing here feeds a call,
scores an idea or writes anything (``docs/DASHBOARDS.md`` rule 1).

The reading rules, in one place:
- Upcoming = a dated catalyst with ``when_date >= today`` (today counts), plus each card's latest call's
  ``arm_until`` ("entry window ends") and ``exit_by`` ("exit-by") when on or after today, each its own item.
- The buckets, by calendar day. A week starts on Monday: "this week" runs through the coming Sunday (today
  alone on a Sunday), "next week" is the seven days after it, "the next 30 days" runs through today + 30,
  "31–90 days" through today + 90, "later" after that.
- Within a bucket: by date, then card name, then catalysts before the entry window before exit-by, then label.
- Passed = a dated catalyst with ``when_date < today``, newest first. A passed deadline is the call's own
  clock, not an authored catalyst, so it is never listed.
- Undated = a catalyst with no ``when_date`` (its ``when_label`` is never turned into a date), grouped per card,
  most first, in the card's own order within a group.
- A card with no recorded call has state ``None`` (shown as "—") and no deadlines; its catalysts still count.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from domain.thesis import Catalyst, Thesis

ItemKind = Literal["catalyst", "entry_window", "exit_by"]
BucketKey = Literal["this_week", "next_week", "next_30_days", "days_31_90", "later"]

ENTRY_WINDOW_LABEL = "entry window ends"
EXIT_BY_LABEL = "exit-by"
NEXT_DAYS = 30
FAR_DAYS = 90

_ITEM_ORDER: dict[str, int] = {"catalyst": 0, "entry_window": 1, "exit_by": 2}

# ---------------------------------------------------------------------------------------------------------
# the rows as read (the router builds these from its SELECT)
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LatestCall:
    """A card's LATEST ``calls`` row: its state and two clocks (``card->>'arm_until'`` /
    ``card->>'exit_by'``)."""

    state: str
    arm_until: date | None
    exit_by: date | None


# ---------------------------------------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarItem:
    """One dated line: a catalyst, or one of a card's two deadlines. ``days`` = ``on - today`` in calendar
    days (0 = today, negative = passed). ``kind`` / ``when_label`` / ``catalyst_id`` are the catalyst's own
    (``None`` on a deadline). ``state`` is the card's latest recorded state (``None`` = no recorded call).
    """

    item: ItemKind
    on: date
    days: int
    label: str
    kind: str | None
    when_label: str | None
    catalyst_id: UUID | None
    thesis_id: UUID
    thesis_name: str
    state: str | None


@dataclass(frozen=True)
class Bucket:
    """One stretch of the upcoming calendar, ``start`` to ``end`` inclusive (``end`` is ``None`` for
    "later")."""

    key: BucketKey
    label: str
    start: date
    end: date | None
    items: tuple[CalendarItem, ...]


@dataclass(frozen=True)
class UndatedCatalyst:
    catalyst_id: UUID
    label: str
    kind: str | None
    when_label: str | None


@dataclass(frozen=True)
class UndatedGroup:
    thesis_id: UUID
    thesis_name: str
    state: str | None
    count: int
    catalysts: tuple[UndatedCatalyst, ...]  # the card's own order


@dataclass(frozen=True)
class CatalystCalendar:
    today: date
    live_cards: int
    cards_without_calls: int
    cards_without_catalysts: int
    catalysts: int  # every catalyst on a live card
    dated: int
    upcoming_catalysts: int  # dated, on or after today
    passed_catalysts: int  # dated, before today
    undated: int
    deadlines: int  # entry-window and exit-by items on or after today
    buckets: tuple[Bucket, ...]  # all five, in order, empty ones included
    passed: tuple[CalendarItem, ...]  # newest first
    undated_groups: tuple[UndatedGroup, ...]  # most first


# ---------------------------------------------------------------------------------------------------------
# the compute
# ---------------------------------------------------------------------------------------------------------


def bucket_bounds(today: date) -> tuple[tuple[BucketKey, str, date, date | None], ...]:
    """The five buckets' ``(key, label, start, end)``, ``end`` inclusive. A week starts on Monday, so this
    week ends on the coming Sunday (``weekday()`` 6) and next week is the seven days after it."""
    sunday = today + timedelta(days=6 - today.weekday())
    next_sunday = sunday + timedelta(days=7)
    next_end = today + timedelta(days=NEXT_DAYS)
    far_end = today + timedelta(days=FAR_DAYS)
    return (
        ("this_week", "This week", today, sunday),
        ("next_week", "Next week", sunday + timedelta(days=1), next_sunday),
        ("next_30_days", "Rest of the next 30 days", next_sunday + timedelta(days=1), next_end),
        ("days_31_90", "31–90 days", next_end + timedelta(days=1), far_end),
        ("later", "Later", far_end + timedelta(days=1), None),
    )


def _name_key(name: str, tid: UUID) -> tuple[str, str]:
    return (name.casefold(), str(tid))


def _upcoming_key(i: CalendarItem) -> tuple:
    return (i.on, *_name_key(i.thesis_name, i.thesis_id), _ITEM_ORDER[i.item], i.label.casefold())


def _catalyst_item(
    c: Catalyst, on: date, t: Thesis, state: str | None, today: date
) -> CalendarItem:
    return CalendarItem(
        item="catalyst",
        on=on,
        days=(on - today).days,
        label=c.label,
        kind=c.kind or None,
        when_label=c.when_label,
        catalyst_id=c.id,
        thesis_id=t.id,
        thesis_name=t.name,
        state=state,
    )


def _deadline_items(t: Thesis, call: LatestCall | None, today: date) -> list[CalendarItem]:
    if call is None:
        return []
    out: list[CalendarItem] = []
    for item, on, label in (
        ("entry_window", call.arm_until, ENTRY_WINDOW_LABEL),
        ("exit_by", call.exit_by, EXIT_BY_LABEL),
    ):
        if on is not None and on >= today:
            out.append(
                CalendarItem(
                    item=item,
                    on=on,
                    days=(on - today).days,
                    label=label,
                    kind=None,
                    when_label=None,
                    catalyst_id=None,
                    thesis_id=t.id,
                    thesis_name=t.name,
                    state=call.state,
                )
            )
    return out


def catalyst_calendar(
    theses: Iterable[Thesis],
    latest: Mapping[UUID, LatestCall],
    *,
    today: date,
) -> CatalystCalendar:
    """The Catalyst Calendar payload over ``theses`` (the live cards; the caller excludes archived ones).
    A latest-call row for any other thesis is ignored."""
    theses = list(theses)
    upcoming: list[CalendarItem] = []
    passed: list[CalendarItem] = []
    groups: list[UndatedGroup] = []
    deadlines = 0
    for t in theses:
        call = latest.get(t.id)
        state = call.state if call else None
        for c in t.catalysts:
            if c.when_date is None:
                continue
            item = _catalyst_item(c, c.when_date, t, state, today)
            (upcoming if item.days >= 0 else passed).append(item)
        dl = _deadline_items(t, call, today)
        deadlines += len(dl)
        upcoming += dl
        undated = [c for c in t.catalysts if c.when_date is None]
        if undated:
            groups.append(
                UndatedGroup(
                    thesis_id=t.id,
                    thesis_name=t.name,
                    state=state,
                    count=len(undated),
                    catalysts=tuple(
                        UndatedCatalyst(c.id, c.label, c.kind or None, c.when_label)
                        for c in undated
                    ),
                )
            )

    upcoming.sort(key=_upcoming_key)
    buckets = tuple(
        Bucket(
            key=key,
            label=label,
            start=start,
            end=end,
            items=tuple(i for i in upcoming if i.on >= start and (end is None or i.on <= end)),
        )
        for key, label, start, end in bucket_bounds(today)
    )
    # newest first; a same-day pair by card name, then label
    passed.sort(key=lambda i: (*_name_key(i.thesis_name, i.thesis_id), i.label.casefold()))
    passed.sort(key=lambda i: i.on, reverse=True)
    groups.sort(key=lambda g: _name_key(g.thesis_name, g.thesis_id))
    groups.sort(key=lambda g: g.count, reverse=True)

    total = sum(len(t.catalysts) for t in theses)
    undated_n = sum(g.count for g in groups)
    return CatalystCalendar(
        today=today,
        live_cards=len(theses),
        cards_without_calls=sum(1 for t in theses if t.id not in latest),
        cards_without_catalysts=sum(1 for t in theses if not t.catalysts),
        catalysts=total,
        dated=total - undated_n,
        upcoming_catalysts=len(upcoming) - deadlines,
        passed_catalysts=len(passed),
        undated=undated_n,
        deadlines=deadlines,
        buckets=buckets,
        passed=tuple(passed),
        undated_groups=tuple(groups),
    )
