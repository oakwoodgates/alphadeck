"""Ticker Lookup (``docs/dashboards/ticker-lookup.md``): type a ticker and see what the platform holds about THAT
company at the as-of, on one screen: its identity and approximate market cap, every card it sits on and its part in
each, the triggers the recorded calls show on it, its tape, its filings, the catalysts of its cards and the
operator's logged decisions on it. Follows the as-of dial.

PURE: the router reads the master identity, the counted cards with their rosters as of the as-of
(``dashboards/baskets.py``), the ``calls`` log rows of the cards the company sits on, the company's readings through
the same point-in-time view the Cockpit builds (``baskets.per_company``, the request's one ``serve_view(asof)``
pair), its filings (the shared reader, ``dashboards/filings.py``) and the operator's decisions, and hands them in;
``lookup(...)`` builds the whole payload ONCE per (company, clock, as-of) in the shared dashboard cache
(``dashboards/cache.py``). Every figure is one another surface already computes: this page gathers, it never makes
a number (#3), never feeds a call, never ranks or judges a company (#4) and writes nothing (rule 1).

The rules, in one place:
- **Which cards.** The counted cards (the live cards that existed by the as-of, each with its roster then) that hold
  at least one basket row of the company. A company in two links of a card is ONE row listing both links; the
  Context / Bench tag marks a silent name, which reads "not counted" (it never counts toward the call). The folds
  under the table: the archived cards that hold it (names only) and the counted cards that exclude it (with the
  reason).
- **The place on the card.** Read off the recorded call in effect at the as-of (the ``calls`` row with the greatest
  ``(asof, seq)`` on or before it, the What Changed read): ``armed`` (the member's verdict, grade and setup
  strength), ``watch`` (a confirmation-only member), ``triggers`` (live entry triggers on the name, with the key
  each turns), ``none``, ``not_counted`` (a silent name) or ``no_record`` (the card has no recorded call yet).
- **A trigger's identity** (the Trigger Log's rule, ``docs/dashboards/trigger-log.md``): its role, security, kind,
  fire date and label, across the card's nights; the same trigger on twelve nights is ONE row. It is read from the
  card's ``triggers_fired`` (entry), ``risk_signals`` and every armed or watch member's own ``triggers``. Its
  recorded night is the first night in the window the record shows it, off each night's winning row (the highest
  ``seq``); a trigger already on the row in effect the night before the window opens was recorded before it and is
  not a row (the page counts the live ones). ``turns`` is the key the trigger's kind turns by
  ``DEFAULT_CONFIG.conviction_kinds`` / ``confirmation_kinds``. ``live`` = the same identity is on the card in
  effect at the as-of.
- **The tape** is the display members' own readings (``signals/display/``), picked by their metric keys into a
  fixed grid; a member with no reading shows its note and is counted. The ETF flow member is read for an ETF only.
- **Filings** are the shared reader's rows for the company (``dashboards/filings.py``), grouped by day, newest
  first, the routine rows capped per fold group like the Filing Stream's; the catalyst facts are listed under
  Catalysts instead, once.
- **Decisions** are the operator's own logged take / pass / close rows naming the company on the counted cards,
  with the void rows that strike them; a row counts only from its ``decision_date`` (and, on a past view, only
  once it was logged by the end of that day: the position stays as of the same instant, ``serve_view``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from dashboards.filings import KIND_ORDER, FilingKind, FilingRow, FoldGroup, source_url
from dashboards.peer_table import CapCell, CompanyReads, cap_cell
from dashboards.series import aligned
from domain.call import CallCard, TriggerRef
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind
from domain.splits import ListedSplit
from domain.thesis import Thesis, is_silent
from signals.display.base import DisplayMetric, DisplaySignal

SLUG = "ticker-lookup"

# the window the triggers and the filings are read for; the page's 90-day / 365-day filings toggle slices it
READ_DAYS = 365
TRIGGER_DAYS = 365
# at most this many routine filings of one fold group are listed in the read, newest first; the rest are counted
ROUTINE_CAP = 200
EDGAR_COMPANY_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="

TriggerRole = Literal["entry_trigger", "risk_signal"]
TurnsKey = Literal["conviction", "confirmation"]
PlaceKind = Literal["armed", "watch", "triggers", "none", "not_counted", "no_record"]
Unit = Literal["pct", "usd", "price", "count", "ratio"]
Tone = Literal["pos", "neg"]


# ---------------------------------------------------------------------------------------------------------
# the recorded call in effect
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedNight:
    """One ``calls`` row: its card, its own night (``asof``), its insertion order (``seq``) and whether
    ``pipeline.backfill`` wrote it (``reconstructed``)."""

    thesis_id: UUID
    asof: date
    seq: int
    reconstructed: bool
    card: CallCard


def winning_nights(rows: Iterable[RecordedNight]) -> list[RecordedNight]:
    """Each night's winning row (the highest ``seq``), oldest night first. A night with several rows stood at its
    last one."""
    best: dict[date, RecordedNight] = {}
    for r in rows:
        if r.asof not in best or r.seq > best[r.asof].seq:
            best[r.asof] = r
    return [best[d] for d in sorted(best)]


def in_effect(nights: Sequence[RecordedNight], prior: RecordedNight | None) -> RecordedNight | None:
    """The call in effect at the as-of: the newest winning night of the window, else the row in effect the night
    before the window opened (the log appends only on change, so it stood through the window)."""
    won = winning_nights(nights)
    return won[-1] if won else prior


# ---------------------------------------------------------------------------------------------------------
# the triggers on one company
# ---------------------------------------------------------------------------------------------------------

# role, kind, fire date, label: the Trigger Log's identity for one company (the security is the page's)
TriggerId = tuple[str, str, date | None, str]


def trigger_id(role: TriggerRole, ref: TriggerRef) -> TriggerId:
    return (role, ref.kind.value, ref.event_date, ref.label)


def refs_on(
    card: CallCard, sid: UUID
) -> tuple[dict[TriggerId, TriggerRef], dict[TriggerId, TriggerRef]]:
    """``(entry triggers, risk signals)`` the card lists on the company, each by identity (a repeat keeps its
    first entry): the card's ``triggers_fired`` and every armed / watch member's own ``triggers`` for the entry
    side, ``risk_signals`` for the risk side."""
    entry: dict[TriggerId, TriggerRef] = {}
    risk: dict[TriggerId, TriggerRef] = {}
    pools: list[Sequence[TriggerRef]] = [
        card.triggers_fired,
        *(m.triggers for m in card.armed_members),
        *(m.triggers for m in card.watch_members),
    ]
    for pool in pools:
        for t in pool:
            if t.security_id == sid:
                entry.setdefault(trigger_id("entry_trigger", t), t)
    for t in card.risk_signals:
        if t.security_id == sid:
            risk.setdefault(trigger_id("risk_signal", t), t)
    return entry, risk


def turns_key(kind: Kind) -> TurnsKey | None:
    """The key an entry trigger's KIND turns (the assembler's partition by ``DEFAULT_CONFIG``). The revenue
    re-acceleration screen is a ``catalyst`` kind the assembler keys by ``revenue_accel_key`` (default
    ``conviction``, the same as the kind's own membership); ``TriggerRef`` carries no detector, so the page reads
    the kind."""
    if kind in DEFAULT_CONFIG.conviction_kinds:
        return "conviction"
    if kind in DEFAULT_CONFIG.confirmation_kinds:
        return "confirmation"
    return None


@dataclass(frozen=True)
class SourceRef:
    """One source of a trigger: its kind, its stored reference, the EDGAR (or stored) link when the reference
    is accession-shaped or a URL (``filings.source_url``; a ``price:`` reference has none) and the computation's
    inputs as ``key: value`` lines."""

    source: str
    ref: str
    url: str | None
    detail: tuple[str, ...]


@dataclass(frozen=True)
class TriggerEvent:
    thesis_id: UUID
    thesis_name: str
    ticker: str  # the company's ticker on that card (the Cockpit's ?name= key)
    recorded_on: date  # the first night in the window the record shows it
    reconstructed: bool  # that night's row was written by pipeline.backfill
    event_date: (
        date | None
    )  # its fire date (an insider's trade date, a breakout's bar date); None = undated
    recorded_after_days: (
        int | None
    )  # the days the recorded night trails the fire date (None when not later)
    role: TriggerRole
    kind: str
    grade: str | None
    label: str
    turns: TurnsKey | None  # the key an entry trigger turns; None for a risk signal
    sources: tuple[SourceRef, ...]
    live: bool  # the same identity is on the card in effect at the as-of


@dataclass(frozen=True)
class TriggerLog:
    window_start: date
    events: tuple[TriggerEvent, ...]
    cards_read: int  # the cards whose calls were read (the cards the company sits on)
    nights_read: int  # (card, night) winning rows in the window
    reconstructed_nights: int
    before_window_live: int  # live identities first recorded before the window: not listed


@dataclass(frozen=True)
class CardMeta:
    thesis_id: UUID
    thesis_name: str
    ticker: str


def _scalar_words(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def detail_lines(detail: Mapping[str, Any]) -> tuple[str, ...]:
    """A computation's inputs as ``key: value`` lines, keys as stored, sorted by key. A value the detector did not
    state (``None``) is left out: a missing field asserts nothing."""
    out: list[str] = []
    for k, v in sorted(detail.items()):
        if v is None:
            continue
        if isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out.append(f"{k}: {', '.join(_scalar_words(x) for x in v)}")
        elif isinstance(v, (dict, list, tuple)):
            out.append(f"{k}: {v}")
        else:
            out.append(f"{k}: {_scalar_words(v)}")
    return tuple(out)


def _event(
    ref: TriggerRef,
    role: TriggerRole,
    meta: CardMeta,
    night: RecordedNight,
    cik: str | None,
    live: bool,
) -> TriggerEvent:
    fired, seen = ref.event_date, night.asof
    return TriggerEvent(
        thesis_id=meta.thesis_id,
        thesis_name=meta.thesis_name,
        ticker=meta.ticker,
        recorded_on=seen,
        reconstructed=night.reconstructed,
        event_date=fired,
        recorded_after_days=(seen - fired).days if fired is not None and fired < seen else None,
        role=role,
        kind=ref.kind.value,
        grade=ref.grade.value if ref.grade is not None else None,
        label=ref.label,
        turns=turns_key(ref.kind) if role == "entry_trigger" else None,
        sources=tuple(
            SourceRef(p.source, p.ref, source_url(p.ref, cik), detail_lines(p.detail))
            for p in ref.sources
        ),
        live=live,
    )


def trigger_log(
    sid: UUID,
    metas: Sequence[CardMeta],
    nights: Mapping[UUID, Sequence[RecordedNight]],
    prior: Mapping[UUID, RecordedNight],
    *,
    window_start: date,
    cik: str | None,
) -> TriggerLog:
    """The triggers the recorded calls show on ``sid`` across the cards in ``metas``, newest recorded night
    first. ``nights`` = each card's ``calls`` rows dated in [window_start, as-of]; ``prior`` = the row in effect
    the night before the window opened. See the module note for the identity, the recorded night and ``live``.
    """
    events: list[TriggerEvent] = []
    nights_read = reconstructed = before_live = 0
    for meta in metas:
        won = winning_nights(nights.get(meta.thesis_id, ()))
        nights_read += len(won)
        reconstructed += sum(1 for n in won if n.reconstructed)
        before = prior.get(meta.thesis_id)
        pre: set[TriggerId] = set()
        if before is not None:
            e0, r0 = refs_on(before.card, sid)
            pre = {*e0, *r0}
        effect = won[-1] if won else before
        live_e, live_r = refs_on(effect.card, sid) if effect is not None else ({}, {})
        live_ids = {*live_e, *live_r}
        before_live += len(pre & live_ids)
        seen: set[TriggerId] = set()
        for night in won:
            entry, risk = refs_on(night.card, sid)
            sides: list[tuple[TriggerRole, dict[TriggerId, TriggerRef]]] = [
                ("entry_trigger", entry),
                ("risk_signal", risk),
            ]
            for role, refs in sides:
                for tid, ref in refs.items():
                    if tid in pre or tid in seen:
                        continue
                    seen.add(tid)
                    events.append(_event(ref, role, meta, night, cik, tid in live_ids))
    events.sort(
        key=lambda e: (
            -e.recorded_on.toordinal(),
            -(e.event_date.toordinal() if e.event_date else 0),
            e.thesis_name.casefold(),
            e.kind,
            e.label,
        )
    )
    return TriggerLog(
        window_start=window_start,
        events=tuple(events),
        cards_read=len(metas),
        nights_read=nights_read,
        reconstructed_nights=reconstructed,
        before_window_live=before_live,
    )


# ---------------------------------------------------------------------------------------------------------
# the cards the company sits on
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkRow:
    """One basket row of the company on a card: its link (``None`` = no link), the drafted or edited thesis-fit
    prose with who wrote it (``authored_by``: ``system_drafted`` = model draft, ``operator_edited`` = your words),
    whether the operator signed the name off and the operator's 1-5 weight (display only, never a call input,
    #4)."""

    link: str | None
    thesis_fit: str | None
    authored_by: str
    signed_off: bool
    weight: int | None


@dataclass(frozen=True)
class Place:
    """The company's part in the recorded call in effect. ``armed`` carries the member's ``verdict``, ``grade``
    (the weaker key, ``entry_grade``) and ``strength`` (setup strength, the wire field ``confidence``);
    ``live_triggers`` / ``turns`` = the entry triggers listed on the name and the keys they turn; ``live_risks``
    = the risk signals listed on it."""

    kind: PlaceKind
    verdict: str | None = None
    grade: str | None = None
    strength: float | None = None
    live_triggers: int = 0
    turns: tuple[str, ...] = ()
    live_risks: int = 0


@dataclass(frozen=True)
class CardRow:
    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: tuple[LinkRow, ...]  # in the card's chain order
    silent: str | None  # "Context" / "Bench" when the company sits there
    context_role: str | None
    signed_off: bool  # every row of the company on the card is signed off
    state: str | None  # the recorded state in effect (None = no recorded call yet)
    verdict: str | None
    recorded_on: date | None  # the night that row was recorded for
    reconstructed: bool
    place: Place


@dataclass(frozen=True)
class CardName:
    thesis_id: UUID
    thesis_name: str


@dataclass(frozen=True)
class Excluded:
    thesis_id: UUID
    thesis_name: str
    reason: str | None


@dataclass(frozen=True)
class CardsSection:
    live_cards: int
    not_yet_created: int
    roster_fallback: int
    counted: int
    rows: tuple[CardRow, ...]
    archived: tuple[CardName, ...]
    excluded: tuple[Excluded, ...]


def sits_on(card: Thesis, sid: UUID) -> bool:
    return any(m.security_id == sid for m in card.basket)


def place_of(night: RecordedNight | None, sid: UUID, silent: bool) -> Place:
    """The company's part in the call in effect (see the module note)."""
    if silent:
        return Place("not_counted")
    if night is None:
        return Place("no_record")
    card = night.card
    entry, risk = refs_on(card, sid)
    turns = sorted(
        {k for t in entry.values() if (k := turns_key(t.kind)) is not None},
        key=("conviction", "confirmation").index,
    )
    common: dict[str, Any] = {
        "live_triggers": len(entry),
        "turns": tuple(turns),
        "live_risks": len(risk),
    }
    armed = next((m for m in card.armed_members if m.security_id == sid), None)
    if armed is not None:
        return Place(
            "armed",
            verdict=armed.verdict.value if armed.verdict is not None else None,
            grade=armed.entry_grade.value if armed.entry_grade is not None else None,
            strength=armed.confidence,
            **common,
        )
    if any(m.security_id == sid for m in card.watch_members):
        return Place("watch", **common)
    if entry:
        return Place("triggers", **common)
    return Place("none", **common)


def card_row(card: Thesis, sid: UUID, night: RecordedNight | None) -> CardRow:
    """One card's row for the company: its basket rows there merged (links in chain order), the Context / Bench tag,
    the context role, and its place in the call in effect."""
    order = {s.label: i for i, s in enumerate(card.segments)}
    rows = sorted(
        (m for m in card.basket if m.security_id == sid),
        key=lambda m: order.get(m.segment, len(order)) if m.segment is not None else len(order),
    )
    silent = next((m.segment for m in rows if is_silent(m)), None)
    return CardRow(
        thesis_id=card.id,
        thesis_name=card.name,
        ticker=rows[0].ticker,
        links=tuple(
            LinkRow(m.segment, m.thesis_fit, m.authored_by.value, m.signed_off, m.conviction)
            for m in rows
        ),
        silent=silent,
        context_role=next((m.context_role.value for m in rows if m.context_role is not None), None),
        signed_off=all(m.signed_off for m in rows),
        state=night.card.state.value if night is not None else None,
        verdict=night.card.verdict.value if night is not None else None,
        recorded_on=night.asof if night is not None else None,
        reconstructed=night.reconstructed if night is not None else False,
        place=place_of(night, sid, silent is not None),
    )


def cards_section(
    sid: UUID,
    counted: Sequence[Thesis],
    effect: Mapping[UUID, RecordedNight | None],
    archived: Sequence[Thesis],
    *,
    live_cards: int,
    not_yet_created: int,
    roster_fallback: int,
) -> CardsSection:
    """The cards table and its two folds. ``counted`` = the counted cards with their rosters as of the as-of;
    ``effect`` = each one's recorded call in effect; ``archived`` = the archived cards that existed by the as-of.
    """
    rows = [card_row(t, sid, effect.get(t.id)) for t in counted if sits_on(t, sid)]
    rows.sort(key=lambda r: (r.thesis_name.casefold(), str(r.thesis_id)))
    excluded = [
        Excluded(t.id, t.name, x.reason)
        for t in sorted(counted, key=lambda t: (t.name.casefold(), str(t.id)))
        for x in t.exclusions
        if x.security_id == sid
    ]
    archived_on = [
        CardName(t.id, t.name)
        for t in sorted(archived, key=lambda t: (t.name.casefold(), str(t.id)))
        if sits_on(t, sid)
    ]
    return CardsSection(
        live_cards=live_cards,
        not_yet_created=not_yet_created,
        roster_fallback=roster_fallback,
        counted=len(counted),
        rows=tuple(rows),
        archived=tuple(archived_on),
        excluded=tuple(excluded),
    )


# ---------------------------------------------------------------------------------------------------------
# catalysts of the cards the company counts on
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardCatalyst:
    thesis_id: UUID
    thesis_name: str
    label: str
    kind: str | None
    when_date: date | None  # None = undated (a fuzzy "~Q3")
    when_label: str | None
    passed: bool  # a dated catalyst before the as-of


def card_catalysts(counted: Sequence[Thesis], sid: UUID, asof: date) -> tuple[CardCatalyst, ...]:
    """The thesis-level catalysts of the cards the company COUNTS on (a non-silent row there), dated ones by date
    then the undated, each with its card. A card that holds the company only in Context or Bench is left out: its
    name never counts toward that card's call."""
    out = [
        CardCatalyst(
            t.id,
            t.name,
            c.label,
            c.kind,
            c.when_date,
            c.when_label,
            c.when_date is not None and c.when_date < asof,
        )
        for t in counted
        if any(m.security_id == sid and not is_silent(m) for m in t.basket)
        for c in t.catalysts
    ]
    out.sort(
        key=lambda c: (
            c.when_date is None,
            c.when_date or date.min,
            c.thesis_name.casefold(),
            c.label,
        )
    )
    return tuple(out)


# ---------------------------------------------------------------------------------------------------------
# the operator's decisions
# ---------------------------------------------------------------------------------------------------------

DECISION_ACTIONS = ("take", "pass", "close")


@dataclass(frozen=True)
class DecisionRaw:
    """One ``operator_decision`` row as read."""

    id: UUID
    seq: int
    thesis_id: UUID
    security_id: UUID | None
    action: str
    decision_date: date
    shares: float | None
    price: float | None
    reason: str | None
    voids: UUID | None


@dataclass(frozen=True)
class DecisionRow:
    """A take / pass / close on the company (or the void row striking one). ``voided`` = a void row names it
    (shown struck through, never hidden); ``shares`` / ``price`` are the operator's own fills, as logged.
    """

    id: UUID
    thesis_id: UUID
    thesis_name: str
    action: str
    decision_date: date
    shares: float | None
    price: float | None
    reason: str | None
    voided: bool
    voids: UUID | None  # a void row: the decision it strikes


def decision_rows(
    raw: Iterable[DecisionRaw], sid: UUID, asof: date, names: Mapping[UUID, str]
) -> tuple[DecisionRow, ...]:
    """The decisions naming ``sid`` dated on or before the as-of, newest first, with the void rows that strike
    them. A void dated after the as-of has not struck anything yet."""
    seen = [d for d in raw if d.decision_date <= asof]
    voided = {d.voids for d in seen if d.action == "void" and d.voids is not None}
    own = [d for d in seen if d.security_id == sid and d.action in DECISION_ACTIONS]
    own_ids = {d.id for d in own}
    voids = [d for d in seen if d.action == "void" and d.voids in own_ids]
    rows = sorted([*own, *voids], key=lambda d: (d.decision_date, d.seq), reverse=True)
    return tuple(
        DecisionRow(
            id=d.id,
            thesis_id=d.thesis_id,
            thesis_name=names.get(d.thesis_id, "?"),
            action=d.action,
            decision_date=d.decision_date,
            shares=d.shares,
            price=d.price,
            reason=d.reason,
            voided=d.id in voided,
            voids=d.voids,
        )
        for d in rows
    )


# ---------------------------------------------------------------------------------------------------------
# filings
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingDay:
    date: date
    rows: tuple[FilingRow, ...]  # kind order, then headline


@dataclass(frozen=True)
class KindCount:
    kind: FilingKind
    rows: int


@dataclass(frozen=True)
class NotListed:
    """Routine rows of one fold group the cap left out of the list (counted, never vanished)."""

    fold: FoldGroup | None
    rows: int
    older_than: (
        date | None
    )  # the oldest listed row of that group: the unlisted ones are on or before it


@dataclass(frozen=True)
class FilingsSection:
    read_days: int
    days: tuple[FilingDay, ...]  # newest day first
    rows: int  # every filing row in the read, listed or not (catalyst facts are under Catalysts)
    by_kind: tuple[KindCount, ...]
    routine: int
    not_listed: tuple[NotListed, ...]


_KIND_INDEX = {k: i for i, k in enumerate(KIND_ORDER)}


def filings_section(
    rows: Iterable[FilingRow], *, read_days: int = READ_DAYS, cap: int = ROUTINE_CAP
) -> tuple[FilingsSection, tuple[FilingRow, ...]]:
    """``(the filings section, the catalyst facts)``. The catalyst rows leave the filings list for the Catalysts
    section (listed once); every other row is grouped by day, newest first, in kind order inside a day. At most
    ``cap`` routine rows of one fold group are listed (newest first); the rest are counted in ``not_listed`` and
    in every count."""
    everything = list(rows)
    catalysts = tuple(sorted((r for r in everything if r.kind == "catalyst"), key=_newest_first))
    body = sorted((r for r in everything if r.kind != "catalyst"), key=_newest_first)

    listed_routine: dict[FoldGroup | None, int] = {}
    oldest: dict[FoldGroup | None, date] = {}
    cut: dict[FoldGroup | None, int] = {}
    listed: list[FilingRow] = []
    for r in body:
        if r.routine:
            n = listed_routine.get(r.fold, 0)
            if n >= cap:
                cut[r.fold] = cut.get(r.fold, 0) + 1
                continue
            listed_routine[r.fold] = n + 1
            oldest[r.fold] = r.date
        listed.append(r)

    by_day: dict[date, list[FilingRow]] = {}
    for r in listed:
        by_day.setdefault(r.date, []).append(r)
    days = tuple(
        FilingDay(d, tuple(sorted(rs, key=lambda r: (_KIND_INDEX.get(r.kind, 99), r.headline))))
        for d, rs in sorted(by_day.items(), reverse=True)
    )
    counts: dict[FilingKind, int] = {}
    for r in body:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    return (
        FilingsSection(
            read_days=read_days,
            days=days,
            rows=len(body),
            by_kind=tuple(KindCount(k, counts[k]) for k in KIND_ORDER if k in counts),
            routine=sum(1 for r in body if r.routine),
            not_listed=tuple(
                NotListed(fold, n, oldest.get(fold))
                for fold, n in sorted(cut.items(), key=lambda kv: (-kv[1], str(kv[0])))
            ),
        ),
        catalysts,
    )


def _newest_first(r: FilingRow) -> tuple[int, int, str]:
    return (-r.date.toordinal(), _KIND_INDEX.get(r.kind, 99), r.headline)


# ---------------------------------------------------------------------------------------------------------
# the tape
# ---------------------------------------------------------------------------------------------------------

# member names, mirrored from signals/display/ (the registry is checked at read time: ``tape`` reads whatever
# ``registered_display_members()`` returns; a renamed member simply reads as a member with no reading)
SMA, RETURNS, RANGE, VOLUME, RVOL, RS, VCP, INSIDER, ETF_FLOW, PATH = (
    "sma_position",
    "trailing_returns",
    "range_52w",
    "volume_regime",
    "rvol",
    "relative_strength",
    "vcp",
    "insider_flow_90d",
    "etf_flow",
    "price_path",
)

FigureGroup = Literal["price", "returns", "range", "volume", "strength", "insider", "flow"]
GROUPS: tuple[FigureGroup, ...] = (
    "price",
    "returns",
    "range",
    "volume",
    "strength",
    "insider",
    "flow",
)

# (member, metric key, group, label override or None = the metric's own label, tone on the sign)
_SPECS: tuple[tuple[str, str, FigureGroup, str | None, bool], ...] = (
    (SMA, "close", "price", "close", False),
    (SMA, "pct_vs_fast", "price", None, True),
    (SMA, "pct_vs_slow", "price", None, True),
    (RETURNS, "ret_1d", "returns", "1D", True),
    (RETURNS, "ret_7d", "returns", "7D", True),
    (RETURNS, "ret_30d", "returns", "30D", True),
    (RETURNS, "ret_90d", "returns", "90D", True),
    (RETURNS, "ret_1y", "returns", "1Y", True),
    (RANGE, "pct_off_52w_high", "range", "off 52w high", False),
    (RANGE, "pct_above_52w_low", "range", "above 52w low", False),
    (RVOL, "rvol", "volume", "RVOL 8", False),
    (RVOL, "rvol20", "volume", "RVOL 20", False),
    (VOLUME, "adv_usd_20d", "volume", None, False),
    (RS, "rs_spy", "strength", None, False),
    (RS, "rs_iwm", "strength", None, False),
    (INSIDER, "buy_count", "insider", "buys 90d", False),
    (INSIDER, "sell_count", "insider", "sells 90d", False),
    (INSIDER, "distinct_buyers", "insider", "buyers 90d", False),
    (INSIDER, "net_usd", "insider", "net $ 90d", True),
    (INSIDER, "buy_count_30d", "insider", "buys 30d", False),
    (INSIDER, "distinct_buyers_30d", "insider", "buyers 30d", False),
    (ETF_FLOW, "flow_1w_usd", "flow", "1w net flow", True),
    (ETF_FLOW, "flow_1w_pct_of_shares", "flow", "1w Δshares", True),
    (ETF_FLOW, "flow_1m_usd", "flow", "1m net flow", True),
    (ETF_FLOW, "flow_1m_pct_of_shares", "flow", "1m Δshares", True),
)

# why a member has no reading at all (its note on the page)
NO_READING: dict[str, str] = {
    SMA: "no price bars at the as-of",
    RETURNS: "no price bars at the as-of",
    RANGE: "no price bars at the as-of",
    VOLUME: "no volume bars at the as-of",
    RVOL: "no price bars at the as-of",
    RS: "no price bars, or no benchmark bars that overlap them",
    VCP: "too few price bars for a base",
    INSIDER: "no Form 4 ingested for the name",
    ETF_FLOW: "no fund share samples",
    PATH: "no price bars at the as-of",
}
MEMBER_LABEL: dict[str, str] = {
    SMA: "SMA position",
    RETURNS: "Trailing returns",
    RANGE: "52-week range",
    VOLUME: "Volume regime",
    RVOL: "Relative volume",
    RS: "Relative strength",
    VCP: "Base (VCP)",
    INSIDER: "Insider flow",
    ETF_FLOW: "Fund flow",
    PATH: "Price path",
}


@dataclass(frozen=True)
class Figure:
    """One tape figure, as its member states it. ``value`` is ``None`` with the member's own ``note`` when the
    member could not compute it (an honest gap, counted). ``level`` / ``on`` = the 52-week high or low it is
    measured from, and the day it printed."""

    member: str
    key: str
    group: FigureGroup
    label: str
    value: float | None
    unit: Unit | None
    note: str | None
    tone: Tone | None
    level: float | None = None
    on: date | None = None


@dataclass(frozen=True)
class MemberGap:
    """A member that returned no reading at all, with why."""

    member: str
    label: str
    note: str


@dataclass(frozen=True)
class Sparkline:
    """The company's last closes and IWM's on the same sessions, both indexed to 100 at the first session where
    both have a bar (``None`` = no bar that session; the line breaks, never interpolates)."""

    dates: tuple[date, ...]
    stock: tuple[float | None, ...]
    iwm: tuple[float | None, ...]
    bars: int  # the company's bars drawn
    iwm_bars: int  # the sessions IWM has a bar on
    note: str | None  # the price path member's own note ("thin: 40/90 bars")


@dataclass(frozen=True)
class Tape:
    figures: tuple[Figure, ...]
    leading: tuple[str, ...]  # the benchmarks the RS line is at a 13-week high against (SPY / IWM)
    coiling: bool  # the VCP member's headline: a base coiling
    coiling_detail: str | None
    headline_notes: tuple[
        str, ...
    ]  # a member's own basis note worth a quiet line (stale tape, thin base)
    gaps: tuple[MemberGap, ...]
    members_read: int  # members asked (the ETF flow member only for an ETF)
    members_without: int  # of those, the ones with no reading at all
    figures_blank: int  # figures whose value is None (counted)
    sparkline: Sparkline | None
    window_end: date | None  # the last bar the price readings used


def _tone(value: float | None, signed: bool) -> Tone | None:
    if not signed or value is None:
        return None
    return "pos" if value > 0 else "neg" if value < 0 else None


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _metric(sig: DisplaySignal | None, key: str) -> DisplayMetric | None:
    return next((m for m in sig.metrics if m.key == key), None) if sig is not None else None


def _printed_on(m: DisplayMetric | None) -> date | None:
    """The day a 52-week level printed: the high / low metric's note reads ``on YYYY-MM-DD``."""
    if m is None or not m.note or not m.note.startswith("on "):
        return None
    try:
        return date.fromisoformat(m.note[3:13])
    except ValueError:
        return None


def sparkline_of(
    path: DisplaySignal | None,
    bars: Sequence[Mapping[str, Any]],
    iwm_bars: Sequence[Mapping[str, Any]],
) -> Sparkline | None:
    """The price-path member's closes (its fixed 90-slot series, left-padded) on the sessions of the company's
    own last bars, with IWM aligned on the same sessions (``series.aligned``). Both are indexed to 100 at the first
    session where both have a bar, so two lines can share one axis. ``None`` = the member had no reading.
    """
    series = next((s for s in path.series if s.key == "close"), None) if path is not None else None
    if path is None or series is None:
        return None
    closes = [v for v in series.values if v is not None]
    priced = [b for b in bars if b.get("close") is not None]
    n = min(len(closes), len(priced))
    if n == 0:
        return None
    sessions = tuple(b["d"] for b in priced[-n:])
    own = dict(zip(sessions, closes[-n:]))
    bench = {b["d"]: float(b["close"]) for b in iwm_bars if b.get("close") is not None}
    own_line, iwm_line = aligned(own, sessions), aligned(bench, sessions)
    start = next((i for i, (a, b) in enumerate(zip(own_line, iwm_line)) if a and b), None)
    if start is None:
        stock = [
            100.0 * c / own_line[0] if c is not None and own_line[0] else None for c in own_line
        ]
        indexed_iwm: list[float | None] = [None] * n
    else:
        a0, b0 = own_line[start], iwm_line[start]
        assert a0 is not None and b0 is not None
        stock = [100.0 * c / a0 if c is not None else None for c in own_line]
        indexed_iwm = [100.0 * c / b0 if c is not None else None for c in iwm_line]
    return Sparkline(
        dates=sessions,
        stock=tuple(None if v is None else round(v, 2) for v in stock),
        iwm=tuple(None if v is None else round(v, 2) for v in indexed_iwm),
        bars=n,
        iwm_bars=sum(1 for v in iwm_line if v is not None),
        note=path.basis.note,
    )


def tape_of(
    signals: Mapping[str, DisplaySignal | None],
    asked: Sequence[str],
    *,
    bars: Sequence[Mapping[str, Any]] = (),
    iwm_bars: Sequence[Mapping[str, Any]] = (),
) -> Tape:
    """The tape grid off the members' readings. ``asked`` = the member names that were read, in order (so a
    member that returned ``None`` is counted as one with no reading); ``signals`` = their readings by name.
    """
    figures: list[Figure] = []
    for member, key, group, label, signed in _SPECS:
        sig = signals.get(member)
        m = _metric(sig, key)
        if sig is None or m is None:
            continue
        level = on = None
        if key in ("pct_off_52w_high", "pct_above_52w_low"):
            lvl = _metric(sig, "high_52w" if key == "pct_off_52w_high" else "low_52w")
            level, on = (_num(lvl.value) if lvl else None), _printed_on(lvl)
        value = _num(m.value)
        figures.append(
            Figure(
                member=member,
                key=key,
                group=group,
                label=label or m.label,
                value=value,
                unit=m.unit,
                note=m.note,
                tone=_tone(value, signed),
                level=level,
                on=on,
            )
        )
    rs = signals.get(RS)
    leading = tuple(
        b
        for b in ("SPY", "IWM")
        if rs is not None and any(e.key == f"rs_high_{b.lower()}" for e in rs.events)
    )
    vcp = signals.get(VCP)
    coiling = vcp is not None and vcp.headline is not None and vcp.headline.key == "coiling"
    notes: list[str] = []
    for member in (SMA, RANGE):
        sig = signals.get(member)
        if sig is not None and sig.basis.note:
            notes.append(f"{MEMBER_LABEL[member]}: {sig.basis.note}")
    gaps = tuple(
        MemberGap(m, MEMBER_LABEL.get(m, m), NO_READING.get(m, "no reading"))
        for m in asked
        if signals.get(m) is None
    )
    ends = [
        sig.basis.window_end for sig in (signals.get(SMA), signals.get(RETURNS)) if sig is not None
    ]
    return Tape(
        figures=tuple(figures),
        leading=leading,
        coiling=coiling,
        coiling_detail=vcp.headline.detail if coiling and vcp and vcp.headline else None,
        headline_notes=tuple(notes),
        gaps=gaps,
        members_read=len(asked),
        members_without=len(gaps),
        figures_blank=sum(1 for f in figures if f.value is None),
        sparkline=sparkline_of(signals.get(PATH), bars, iwm_bars),
        window_end=max((d for d in ends if d is not None), default=None),
    )


def figure(tape: Tape, key: str) -> Figure | None:
    return next((f for f in tape.figures if f.key == key), None)


# ---------------------------------------------------------------------------------------------------------
# identity and the approximate market cap
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cap:
    """The approximate market cap and its tier, with the inputs that made it (the Peer Table's rule). ``gap`` is
    why it is blank (``flagged`` = a data flag reaches the price: the reason is in ``note``)."""

    value: float | None
    tier: str | None
    gap: str | None
    note: str | None
    close: float | None
    close_date: date | None
    shares: float | None
    shares_date: date | None
    ads_ratio: int | None
    split_factor: float | None


def cap_of(
    flag: str | None,
    close: tuple[date, float] | None,
    shares: Mapping[str, Any] | None,
    splits: Sequence[ListedSplit],
) -> Cap:
    """The approximate cap by the Peer Table's rule (``peer_table.cap_cell``: the latest close times the latest
    shares fact, the ADS ratio and any split in force between the two applied; a flagged price history withholds
    it). Labeled approximate everywhere."""
    c: CapCell = cap_cell(CompanyReads(flag=flag, close=close, shares=shares, splits=tuple(splits)))
    return Cap(
        value=c.value,
        tier=c.tier.value if c.tier is not None else None,
        gap=c.gap,
        note=c.note,
        close=c.close,
        close_date=c.close_date,
        shares=c.shares,
        shares_date=c.shares_date,
        ads_ratio=c.ads_ratio,
        split_factor=c.split_factor,
    )


@dataclass(frozen=True)
class Identity:
    security_id: UUID
    ticker: str | None
    name: str | None
    cik: str | None
    edgar_url: str | None
    exchange: str | None
    business_type: str | None
    supersector: str | None
    royalty: bool
    origin: str | None
    instrument_kind: str
    price_symbol: (
        str | None
    )  # the vendor symbol it is priced under, when it differs from the ticker
    former_tickers: tuple[str, ...]
    cap: Cap
    adv_usd_20d: float | None
    adv_note: str | None
    flag: str | None  # the data flags reaching its price readings (never hidden)


def edgar_company_url(cik: str | None) -> str | None:
    """The company's EDGAR filings page, from its CIK (digits only); ``None`` when there is no CIK."""
    digits = "".join(ch for ch in (cik or "") if ch.isdigit())
    return f"{EDGAR_COMPANY_URL}{digits}" if digits else None


@dataclass(frozen=True)
class IdentityInputs:
    """The master's identity row for the company (``master.get`` + ``master.identity_for``)."""

    security_id: UUID
    ticker: str | None
    name: str | None
    cik: str | None
    exchange: str | None
    business_type: str | None
    supersector: str | None
    royalty: bool
    origin: str | None
    instrument_kind: str
    price_symbol: str | None
    former_tickers: tuple[str, ...] = ()


def identity_of(inputs: IdentityInputs, cap: Cap, tape: Tape, flag: str | None) -> Identity:
    adv = figure(tape, "adv_usd_20d")
    symbol = inputs.price_symbol
    return Identity(
        security_id=inputs.security_id,
        ticker=inputs.ticker,
        name=inputs.name,
        cik=inputs.cik,
        edgar_url=edgar_company_url(inputs.cik),
        exchange=inputs.exchange,
        business_type=inputs.business_type,
        supersector=inputs.supersector,
        royalty=inputs.royalty,
        origin=inputs.origin,
        instrument_kind=inputs.instrument_kind,
        price_symbol=symbol if symbol and symbol != inputs.ticker else None,
        former_tickers=inputs.former_tickers,
        cap=cap,
        adv_usd_20d=adv.value if adv else None,
        adv_note=adv.note if adv else None,
        flag=flag,
    )


# ---------------------------------------------------------------------------------------------------------
# the whole page
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Lookup:
    """Everything the page shows for one company at one as-of (the value the cache holds)."""

    identity: Identity
    cards: CardsSection
    triggers: TriggerLog
    tape: Tape
    filings: FilingsSection
    catalyst_facts: tuple[FilingRow, ...]
    card_catalysts: tuple[CardCatalyst, ...]
    decisions: tuple[DecisionRow, ...]
    decisions_voided: int


def lookup(
    identity: Identity,
    cards: CardsSection,
    triggers: TriggerLog,
    tape: Tape,
    filing_rows: Iterable[FilingRow],
    catalysts: Sequence[CardCatalyst],
    decisions: Sequence[DecisionRow],
) -> Lookup:
    """Assemble the page from its sections (each already built by its own pure function)."""
    section, facts = filings_section(filing_rows)
    return Lookup(
        identity=identity,
        cards=cards,
        triggers=triggers,
        tape=tape,
        filings=section,
        catalyst_facts=facts,
        card_catalysts=tuple(catalysts),
        decisions=tuple(decisions),
        decisions_voided=sum(1 for d in decisions if d.voided),
    )
