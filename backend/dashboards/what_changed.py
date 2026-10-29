"""What Changed (``docs/dashboards/what-changed.md``): what moved on the live cards between two recorded nights
(states, keys, names, triggers) and the clocks coming due on the newest one. A "now" view with its own "since"
selector.

PURE: ``what_changed(names, now, baseline, tickers, latest=..., baseline_on=...)`` takes the two recorded calls
in effect per card (the router reads them from the ``calls`` log), the live cards' names and the master's
tickers, and returns the whole payload; ``baseline_date`` turns the "since" choice into the baseline night.
Display only: nothing here feeds a call, scores an idea or writes anything (``docs/DASHBOARDS.md`` rule 1).

The rules, in one place:
- The now card is the recorded card in effect at T, the newest night in the log across the live cards; the
  baseline card is the one in effect at the baseline night. "In effect at D" is the row with the greatest
  ``(asof, seq)`` where ``asof <= D`` (the router's read): the log appends only on change, so the last row
  on or before D is what stood that night.
- The baseline night: "last night" = the previous recorded night (the greatest ``asof < T``); "7d" =
  T - 7 calendar days; a date = that date, which must lie between the first recorded night and T.
- A card with no baseline row is one "New card" line and nothing else. A card with no recorded call at all
  is counted (``unrecorded``), never silently dropped (rule 5).
- Changes, comparing the two cards: the state (both directions); each key's ``turned`` (on and off); a
  name newly armed or no longer armed, and a name new on watch (matched by ``security_id``); a trigger
  newly fired or no longer firing, and a new risk signal (identity = kind, security, event date, label).
- Clocks, on the now card only, counted from T in calendar days (the Decision Queue's "act within Nd"):
  the entry window (``arm_until``) closing within ``ENTRY_WINDOW_DAYS``, and ``exit_by`` reached inside the
  window (after the baseline night, on or before T).
- Order is neutral and computed: sections in a fixed order, cards by name inside a section, and inside a
  card the change kinds in a fixed order, names and triggers by ticker.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from domain.call import CallCard, MemberCall, TriggerRef
from domain.enums import Kind, State

# the entry window "closes in N days" while arm_until is within this many calendar days of T (0..3)
ENTRY_WINDOW_DAYS = 3
LAST_WEEK_DAYS = 7

SinceMode = Literal["last_night", "7d", "date"]
Section = Literal["column_moves", "keys", "names", "triggers", "clocks", "new_cards"]
Change = Literal[
    "state",  # column_moves
    "key_on",  # keys
    "key_off",
    "armed",  # names
    "no_longer_armed",
    "new_watch",
    "trigger_new",  # triggers
    "trigger_gone",
    "risk_new",
    "entry_window",  # clocks
    "exit_by",
    "new_card",  # new_cards
]

# the page's section order (group by kind)
SECTIONS: tuple[Section, ...] = (
    "column_moves",
    "keys",
    "names",
    "triggers",
    "clocks",
    "new_cards",
)

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------------------------------------------------
# the "since" selector
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Since:
    mode: SinceMode
    on: date | None = None  # the picked date, for mode "date"


class SinceOutOfRange(ValueError):
    """A picked date outside the record: after T, or before the first recorded night."""


def parse_since(raw: str) -> Since:
    """``last_night`` | ``7d`` | ``YYYY-MM-DD``; anything else raises ``ValueError``."""
    if raw == "last_night":
        return Since("last_night")
    if raw == "7d":
        return Since("7d")
    if _DATE.fullmatch(raw):
        return Since("date", date.fromisoformat(raw))  # an impossible date raises ValueError
    raise ValueError(f"since must be last_night, 7d or a YYYY-MM-DD date, not {raw!r}")


def baseline_date(
    since: Since, *, latest: date | None, previous: date | None, first: date | None
) -> date | None:
    """The baseline night for ``since``. ``latest`` = T and ``first`` = the first recorded night (both over
    the live cards; ``None`` = an empty log, which has no baseline), ``previous`` = the greatest recorded
    night before T (``None`` = T is the record's only night, so "last night" has no baseline). A picked date
    after T or before ``first`` raises ``SinceOutOfRange``."""
    if latest is None:
        return None
    if since.mode == "last_night":
        return previous
    if since.mode == "7d":
        return latest - timedelta(days=LAST_WEEK_DAYS)
    assert since.on is not None
    if since.on > latest:
        raise SinceOutOfRange(
            f"{since.on.isoformat()} is after the newest recorded night ({latest.isoformat()})"
        )
    if first is not None and since.on < first:
        raise SinceOutOfRange(
            f"{since.on.isoformat()} is before the first recorded night ({first.isoformat()})"
        )
    return since.on


# ---------------------------------------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedCall:
    """One ``calls`` row in effect on a night: its own night (``asof``), its insertion order (``seq``),
    whether ``pipeline.backfill`` wrote it (``reconstructed``) and the card."""

    asof: date
    seq: int
    reconstructed: bool
    card: CallCard


@dataclass(frozen=True)
class CardRef:
    thesis_id: UUID
    thesis_name: str


@dataclass(frozen=True)
class ChangeItem:
    """One line. ``security_id`` / ``ticker`` are set for a name or a trigger (``None`` = a card-level line);
    ``ticker`` is ``None`` when the master has no ticker for it. ``label`` = the key's own label, or the
    trigger's. ``on`` = the trigger's event date, or the clock's date (``arm_until`` / ``exit_by``); ``days`` =
    calendar days from T to ``arm_until``. ``reconstructed`` = the card's NOW row was written by
    ``pipeline.backfill``."""

    section: Section
    change: Change
    thesis_id: UUID
    thesis_name: str
    reconstructed: bool = False
    security_id: UUID | None = None
    ticker: str | None = None
    from_state: State | None = None
    to_state: State | None = None
    label: str | None = None
    signal_kind: Kind | None = None
    on: date | None = None
    days: int | None = None


@dataclass(frozen=True)
class WhatChanged:
    latest: date | None  # T: the newest recorded night across the live cards (None = an empty log)
    baseline: date | None  # the baseline night (None = no earlier night to compare)
    live_cards: int
    cards_with_change: int
    unrecorded: tuple[CardRef, ...]  # live cards with no recorded call yet (by name)
    items: tuple[ChangeItem, ...]


# ---------------------------------------------------------------------------------------------------------
# the diff
# ---------------------------------------------------------------------------------------------------------


def _trigger_id(t: TriggerRef) -> tuple[Kind, UUID, date | None, str]:
    return (t.kind, t.security_id, t.event_date, t.label)


def _triggers(refs: Iterable[TriggerRef]) -> dict[tuple, TriggerRef]:
    """By identity; a re-listed identity keeps its first entry (the same trigger, once)."""
    out: dict[tuple, TriggerRef] = {}
    for t in refs:
        out.setdefault(_trigger_id(t), t)
    return out


def _members(members: Iterable[MemberCall]) -> list[UUID]:
    seen: list[UUID] = []
    for m in members:
        if m.security_id not in seen:
            seen.append(m.security_id)
    return seen


def _card_changes(
    ref: CardRef,
    now: RecordedCall,
    base: RecordedCall | None,
    tickers: Mapping[UUID, str | None],
    *,
    latest: date,
    baseline_on: date | None,
) -> list[ChangeItem]:
    """One card's lines, in the fixed in-card order."""
    items: list[ChangeItem] = []

    def add(section: Section, change: Change, **kw) -> None:
        if (sid := kw.get("security_id")) is not None:
            kw["ticker"] = tickers.get(sid)
        items.append(
            ChangeItem(
                section=section,
                change=change,
                thesis_id=ref.thesis_id,
                thesis_name=ref.thesis_name,
                reconstructed=now.reconstructed,
                **kw,
            )
        )

    def by_ticker(sid: UUID) -> tuple[str, str]:
        return ((tickers.get(sid) or "").casefold(), str(sid))

    n = now.card
    if base is None or baseline_on is None:
        add("new_cards", "new_card", to_state=n.state)
        return items
    b = base.card

    # 1. column moves
    if n.state != b.state:
        add("column_moves", "state", from_state=b.state, to_state=n.state)

    # 2. keys turned (the key's own label, on and off)
    for nk, bk in ((n.key_conviction, b.key_conviction), (n.key_confirmation, b.key_confirmation)):
        if nk.turned != bk.turned:
            add("keys", "key_on" if nk.turned else "key_off", label=nk.label)

    # 3. names, matched by security
    now_armed, base_armed = _members(n.armed_members), _members(b.armed_members)
    base_watch = set(_members(b.watch_members))
    for change, sids in (
        ("armed", [s for s in now_armed if s not in base_armed]),
        ("no_longer_armed", [s for s in base_armed if s not in now_armed]),
        ("new_watch", [s for s in _members(n.watch_members) if s not in base_watch]),
    ):
        for sid in sorted(sids, key=by_ticker):
            add("names", change, security_id=sid)

    # 4. triggers (identity = kind, security, event date, label)
    now_fired, base_fired = _triggers(n.triggers_fired), _triggers(b.triggers_fired)
    base_risk = _triggers(b.risk_signals)
    for change, refs in (
        ("trigger_new", [t for k, t in now_fired.items() if k not in base_fired]),
        ("trigger_gone", [t for k, t in base_fired.items() if k not in now_fired]),
        ("risk_new", [t for k, t in _triggers(n.risk_signals).items() if k not in base_risk]),
    ):
        for t in sorted(
            refs,
            key=lambda t: (
                *by_ticker(t.security_id),
                t.kind.value,
                t.label,
                t.event_date or date.min,
            ),
        ):
            add(
                "triggers",
                change,
                security_id=t.security_id,
                label=t.label,
                signal_kind=t.kind,
                on=t.event_date,
            )

    # 5. clocks, on the now card as of T (calendar days)
    if n.arm_until is not None and 0 <= (days := (n.arm_until - latest).days) <= ENTRY_WINDOW_DAYS:
        add("clocks", "entry_window", on=n.arm_until, days=days)
    if n.exit_by is not None and baseline_on < n.exit_by <= latest:
        add("clocks", "exit_by", on=n.exit_by)
    return items


def security_ids(rows: Iterable[RecordedCall]) -> set[UUID]:
    """Every security a line can name on these cards (armed and watch members, triggers, risk signals): the
    ids the router resolves to tickers."""
    return {
        sid
        for r in rows
        for sid in (
            *(m.security_id for m in (*r.card.armed_members, *r.card.watch_members)),
            *(t.security_id for t in (*r.card.triggers_fired, *r.card.risk_signals)),
        )
    }


def _name_key(ref: CardRef) -> tuple[str, str]:
    return (ref.thesis_name.casefold(), str(ref.thesis_id))


def what_changed(
    names: Mapping[UUID, str],
    now: Mapping[UUID, RecordedCall],
    baseline: Mapping[UUID, RecordedCall],
    tickers: Mapping[UUID, str | None],
    *,
    latest: date | None,
    baseline_on: date | None,
) -> WhatChanged:
    """The What Changed payload over the live cards ``names`` (``thesis_id -> name``; the caller excludes
    archived ones). ``now`` / ``baseline`` = each card's recorded call in effect at ``latest`` (T) and at
    ``baseline_on``; a card absent from ``now`` has no recorded call yet, one absent from ``baseline`` is new
    since the baseline night. A row for a card outside ``names`` is ignored."""
    refs = sorted((CardRef(tid, nm) for tid, nm in names.items()), key=_name_key)
    items: list[ChangeItem] = []
    unrecorded: list[CardRef] = []
    for ref in refs:
        row = now.get(ref.thesis_id)
        if row is None or latest is None:
            unrecorded.append(ref)
            continue
        items += _card_changes(
            ref,
            row,
            baseline.get(ref.thesis_id),
            tickers,
            latest=latest,
            baseline_on=baseline_on,
        )
    # cards are already in name order and each card's lines in its fixed order: a stable sort by section
    items.sort(key=lambda i: SECTIONS.index(i.section))
    return WhatChanged(
        latest=latest,
        baseline=baseline_on,
        live_cards=len(refs),
        cards_with_change=len({i.thesis_id for i in items}),
        unrecorded=tuple(unrecorded),
        items=tuple(items),
    )
