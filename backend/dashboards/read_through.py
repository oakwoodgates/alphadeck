"""Read-Throughs (``docs/dashboards/read-through.md``): every company the operator tagged with a context role (payer,
counter-case, direct read, participant, financier, benchmark) on any card counted at the as-of, one row per
(company, role), with what it last filed, its next dated catalyst and its tape against SPY. Follows the as-of dial.

PURE: the router reads the counted cards and their rosters as of the as-of (``dashboards/baskets.py``), the role
companies' filing rows through the shared reader (``dashboards/filings.py``), their trailing returns and SPY's
through the point-in-time view, their identity and their price flags, and hands them in as ``ReadThroughInputs``;
``read_through(...)`` builds the whole table ONCE per as-of (the shared dashboard cache, ``dashboards/cache.py``).
Tabs, sort and filters are client side. Display only: nothing here feeds a call, scores an idea or writes anything
(rule 1). A role is display metadata (#4) and is never written by the model (#3); this page only shows it.

The rules, in one place. This is our own simple listing, stated here and on the page.
- **The rows.** One row per (company, role), companies matched by ``security_id``. A company tagged ``payer`` on two
  cards is ONE row with two card chips; tagged ``payer`` on one card and ``counter_case`` on another it is TWO rows
  (the promote keeps a role identical across a company's rows ON ONE CARD, not across cards). Every basket row with a
  role counts, Context and Bench included (that is where most roles live), a role on a counting row too. A row with
  a role and no resolved security can carry nothing here: counted and listed, never a row (invariant #9).
- **The chip.** One per card the company holds the role on: the card, and the links its rows with that role sit in
  (Context / Bench by their labels when silent; ``None`` = no link).
- **Last filing.** The newest SEC filing in the last ``FILING_DAYS`` (365) days through the as-of: only a
  ``FilingRow`` whose kind is an 8-K, a 13D/G, a Form 4, a 4/A or a 10-Q / 10-K revenue print
  (``filings.SEC_FILING_KINDS``). A split, a dilution fact, a catalyst and a ratified or stored fact are rows the
  system holds, not filings, and are never picked. Ties by the reader's kind order (``filings.KIND_ORDER``: 8-K,
  13D/G, Form 4, 4/A, revenue), then accession / form / headline.
- **Next dated catalyst.** The nearest card-level catalyst with ``when_date >= asof`` across the row's cards (the
  as-of day counts); ties by card name then label. A dated catalyst in the past and an undated one are ignored.
  Catalysts load live with the card (they are not snapshotted), so a past as-of shows them as entered today.
- **Tape against SPY.** The company's trailing return over the last 7 and the last 30 TRADING days (the
  ``trailing_returns`` display member's own ``ret_7d`` / ``ret_30d``) minus SPY's over the same windows (the
  member's own pure ``compute`` over SPY's bars), in percentage points. A cell is blank (never a made-up number)
  when the company has no tape, the window is short, SPY has none, or the company's last close is not SPY's last
  close (the two returns would end on different days: ``ends_differ``). A company whose data flags reach its price
  readings at the as-of (``dashboards.baskets.price_flags``) shows no tape at all: the cells name the reason, the
  row stays, and the page lists it.
- **Order.** Newest last filing first; rows with no filing last, A to Z by ticker (then role order). The page
  re-sorts any column.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal
from uuid import UUID

from dashboards.baskets import (
    FlaggedCompany,
    Membership,
    NameCard,
    display_ticker,
    flagged_in_view,
)
from dashboards.filings import KIND_ORDER, SEC_FILING_KINDS, FilingKind, FilingRow
from domain.thesis import Thesis
from signals.display.base import DisplaySignal

# the span of filings the last-filing pick reads, in days
FILING_DAYS = 365
# the context roles in the Chain Wave's order (payers first): every ``domain.enums.ContextRole`` value
ROLE_ORDER: tuple[str, ...] = (
    "payer",
    "counter_case",
    "direct_read",
    "participant",
    "financier",
    "benchmark",
)
BENCHMARK = "SPY"
# the tape windows are the display member's own, in TRADING days (``trailing_returns.WINDOWS``)
TAPE_WINDOWS: tuple[int, int] = (7, 30)

Gap = Literal["flagged", "no_tape", "no_spy", "ends_differ"]

_KIND_RANK = {k: i for i, k in enumerate(KIND_ORDER)}
_ROLE_RANK = {r: i for i, r in enumerate(ROLE_ORDER)}


# --- the inputs, as read -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardRef:
    thesis_id: UUID
    thesis_name: str


@dataclass(frozen=True)
class CatalystRef:
    """A card-level catalyst with a date, as the card holds it today (``when_date`` None never gets here)."""

    thesis_id: UUID
    thesis_name: str
    catalyst_id: UUID
    label: str
    kind: str | None
    when_date: date
    when_label: str | None


@dataclass(frozen=True)
class Identity:
    """A company's master identity: its name and its leaf business type (the operator's re-tag already folded
    in, ``securities.business_type.resolve_business_type``); ``None`` = unknown."""

    name: str | None
    business_type: str | None


@dataclass(frozen=True)
class Tape:
    """A tape's trailing returns at the as-of, in percent over the last 7 / 30 trading days (``None`` = the window
    is short); ``end`` = the date of its last close (the staleness tell)."""

    ret_7d: float | None = None
    ret_30d: float | None = None
    end: date | None = None


def _metric(sig: DisplaySignal, key: str) -> float | None:
    return next((m.value for m in sig.metrics if m.key == key), None)


def tape_of(sig: DisplaySignal | None) -> Tape | None:
    """The ``trailing_returns`` member's output as a ``Tape``; ``None`` when the member gave no reading (no bars)."""
    if sig is None:
        return None
    return Tape(_metric(sig, "ret_7d"), _metric(sig, "ret_30d"), sig.basis.window_end)


def catalyst_refs(theses: Iterable[Thesis]) -> tuple[CatalystRef, ...]:
    """The dated card-level catalysts of the counted cards (an undated one never matters here)."""
    return tuple(
        CatalystRef(t.id, t.name, c.id, c.label, c.kind, c.when_date, c.when_label)
        for t in theses
        for c in t.catalysts
        if c.when_date is not None
    )


def identities(raw: Mapping[UUID, Mapping[str, object]]) -> dict[UUID, Identity]:
    """``master.identity_for``'s rows as this page's ``Identity`` (the name, the derived leaf business type)."""
    out: dict[UUID, Identity] = {}
    for sid, row in raw.items():
        name, btype = row.get("name"), row.get("business_type")
        out[sid] = Identity(str(name) if name else None, str(btype) if btype else None)
    return out


@dataclass(frozen=True)
class ReadThroughInputs:
    """Everything the heavy read produces: the counted cards (``cards``, every one, so the page can list the ones
    with no role), their rosters' rows as of the as-of, their dated catalysts, and per role company its identity,
    filing rows, tape and price-flag reason. ``spy`` = SPY's tape (``None`` = no SPY bars at the as-of).
    """

    live_cards: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards: tuple[CardRef, ...]
    memberships: tuple[Membership, ...]
    catalysts: tuple[CatalystRef, ...]
    identity: Mapping[UUID, Identity]
    filings: Mapping[UUID, Sequence[FilingRow]]
    tapes: Mapping[UUID, Tape | None]
    spy: Tape | None
    flagged: Mapping[UUID, str]


# --- the picks ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LastFiling:
    """The newest SEC filing of a company in the window. ``days_ago`` = as-of minus its date."""

    kind: FilingKind
    form: str
    date: date
    days_ago: int
    headline: str
    url: str | None
    accession: str | None


def last_filing(rows: Iterable[FilingRow], asof: date) -> LastFiling | None:
    """The newest SEC filing by date (an 8-K, 13D/G, Form 4 or 4/A, or a 10-Q / 10-K revenue print:
    ``filings.SEC_FILING_KINDS``; never a split, dilution fact, catalyst, or ratified or stored fact), ties by
    the reader's kind order, then accession / form / headline. A row dated after the as-of cannot occur on a
    well-formed clock; one is never picked (#1). ``None`` = no such row."""
    best = min(
        (r for r in rows if r.kind in SEC_FILING_KINDS and r.date <= asof),
        key=lambda r: (
            -r.date.toordinal(),
            _KIND_RANK[r.kind],
            r.accession or "",
            r.form,
            r.headline,
        ),
        default=None,
    )
    if best is None:
        return None
    return LastFiling(
        kind=best.kind,
        form=best.form,
        date=best.date,
        days_ago=(asof - best.date).days,
        headline=best.headline,
        url=best.url,
        accession=best.accession,
    )


@dataclass(frozen=True)
class NextCatalyst:
    """The nearest dated catalyst on or after the as-of. ``days`` = its date minus the as-of (0 = the as-of day)."""

    thesis_id: UUID
    thesis_name: str
    catalyst_id: UUID
    label: str
    kind: str | None
    when_date: date
    when_label: str | None
    days: int


def next_catalyst(
    catalysts: Iterable[CatalystRef], card_ids: Iterable[UUID], asof: date
) -> NextCatalyst | None:
    """The nearest catalyst with ``when_date >= asof`` among the cards ``card_ids``; ties by card name, label, id.
    One dated in the past never counts."""
    ids = set(card_ids)
    best = min(
        (c for c in catalysts if c.thesis_id in ids and c.when_date >= asof),
        key=lambda c: (
            c.when_date,
            c.thesis_name.casefold(),
            c.label.casefold(),
            str(c.catalyst_id),
        ),
        default=None,
    )
    if best is None:
        return None
    return NextCatalyst(
        thesis_id=best.thesis_id,
        thesis_name=best.thesis_name,
        catalyst_id=best.catalyst_id,
        label=best.label,
        kind=best.kind,
        when_date=best.when_date,
        when_label=best.when_label,
        days=(best.when_date - asof).days,
    )


@dataclass(frozen=True)
class TapeView:
    """A row's tape. ``ret_7d`` / ``ret_30d`` = the company's trailing return in percent; ``vs_spy_*`` = that minus
    SPY's over the same window, in percentage points; ``last_close`` = the date of the company's last close.
    ``gap`` = why the vs-SPY cells are blank (``None`` = shown, or only that window is short); ``reason`` = the
    flags' labels when ``gap`` is ``flagged``."""

    ret_7d: float | None = None
    ret_30d: float | None = None
    vs_spy_7d: float | None = None
    vs_spy_30d: float | None = None
    last_close: date | None = None
    gap: Gap | None = None
    reason: str | None = None


def _minus(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 2)


def tape_view(company: Tape | None, spy: Tape | None, flagged_reason: str | None) -> TapeView:
    """The row's tape against SPY. A flagged price history shows nothing but the reason (its returns would chain
    a bad bar); no company bars is ``no_tape``; no SPY bars ``no_spy`` (the company's own returns still show); a
    company whose last close is not SPY's last close ``ends_differ`` (the returns would end on different days).
    """
    if flagged_reason is not None:
        return TapeView(gap="flagged", reason=flagged_reason)
    if company is None or company.end is None:
        return TapeView(gap="no_tape")
    own = TapeView(company.ret_7d, company.ret_30d, last_close=company.end)
    if spy is None or spy.end is None:
        return replace(own, gap="no_spy")
    if company.end != spy.end:
        return replace(own, gap="ends_differ")
    return TapeView(
        company.ret_7d,
        company.ret_30d,
        _minus(company.ret_7d, spy.ret_7d),
        _minus(company.ret_30d, spy.ret_30d),
        company.end,
    )


# --- the rows -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleCard(NameCard):
    """A card a company holds a role on (a ``NameCard``: its ticker there, the Cockpit's ``?name=`` key, and its
    links there, ``None`` = no link) with whether the company is in Context or Bench there (it never counts).
    """

    silent: bool = False


@dataclass(frozen=True)
class RoleRow:
    """One (company, role). ``key`` = ``<security_id>:<role>``, unique on the page."""

    key: str
    security_id: UUID
    ticker: str
    name: str | None
    role: str
    cards: tuple[RoleCard, ...]
    business_type: str | None
    last_filing: LastFiling | None
    next_catalyst: NextCatalyst | None
    tape: TapeView


def _cards_of(by_card: Mapping[UUID, Sequence[Membership]]) -> tuple[RoleCard, ...]:
    chips = [
        RoleCard(
            thesis_id=tid,
            thesis_name=ms[0].thesis_name,
            ticker=ms[0].ticker,
            links=tuple(dict.fromkeys(m.segment for m in ms)),
            silent=any(m.silent for m in ms),
        )
        for tid, ms in by_card.items()
    ]
    chips.sort(key=lambda c: (c.thesis_name.casefold(), str(c.thesis_id)))
    return tuple(chips)


def _sort_key(r: RoleRow) -> tuple:
    """Newest last filing first; no filing last; then A to Z by ticker, role order, then id (a stable order)."""
    f = r.last_filing
    return (
        f is None,
        -f.date.toordinal() if f else 0,
        r.ticker.casefold(),
        _ROLE_RANK.get(r.role, len(ROLE_ORDER)),
        str(r.security_id),
    )


@dataclass(frozen=True)
class RoleCount:
    role: str
    rows: int


@dataclass(frozen=True)
class ReadThrough:
    """The whole table with the counts the page's quiet line shows. ``role_tags`` = the roles the operator set
    (one per card and company); ``companies`` = distinct role companies; ``unresolved`` = tickers of the role rows
    with no resolved security (``unresolved_rows`` of them: counted, never a row)."""

    asof: date
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards_with_roles: int
    cards_without_roles: tuple[CardRef, ...]
    role_tags: int
    companies: int
    by_role: tuple[RoleCount, ...]
    rows: tuple[RoleRow, ...]
    rows_with_filing: int
    unresolved: tuple[str, ...]
    unresolved_rows: int
    flagged: tuple[FlaggedCompany, ...]
    tape_ends_differ: int
    spy: Tape | None
    filing_days: int


def read_through(inputs: ReadThroughInputs, asof: date) -> ReadThrough:
    """The page: one row per (company, role) from the role-bearing roster rows of the counted cards."""
    role_members = [m for m in inputs.memberships if m.context_role]
    resolved = [m for m in role_members if m.security_id is not None]
    unresolved = [m for m in role_members if m.security_id is None]

    grouped: dict[tuple[UUID, str], dict[UUID, list[Membership]]] = {}
    for m in resolved:
        assert m.security_id is not None and m.context_role is not None
        grouped.setdefault((m.security_id, m.context_role), {}).setdefault(m.thesis_id, []).append(
            m
        )

    rows: list[RoleRow] = []
    for (sid, role), by_card in grouped.items():
        cards = _cards_of(by_card)
        ident = inputs.identity.get(sid)
        rows.append(
            RoleRow(
                key=f"{sid}:{role}",
                security_id=sid,
                ticker=display_ticker(cards),
                name=ident.name if ident else None,
                role=role,
                cards=cards,
                business_type=ident.business_type if ident else None,
                last_filing=last_filing(inputs.filings.get(sid, ()), asof),
                next_catalyst=next_catalyst(inputs.catalysts, (c.thesis_id for c in cards), asof),
                tape=tape_view(inputs.tapes.get(sid), inputs.spy, inputs.flagged.get(sid)),
            )
        )
    rows.sort(key=_sort_key)

    seen_roles = list(dict.fromkeys(r.role for r in rows))
    extra = sorted(
        r for r in seen_roles if r not in _ROLE_RANK
    )  # a role the order does not know: listed anyway
    tally = {r: 0 for r in (*ROLE_ORDER, *extra)}
    for r in rows:
        tally[r.role] += 1

    def company_key(m: Membership) -> str:
        return (
            str(m.security_id)
            if m.security_id is not None
            else f"ticker:{m.ticker.strip().upper()}"
        )

    with_roles = {m.thesis_id for m in role_members}
    return ReadThrough(
        asof=asof,
        live_cards=inputs.live_cards,
        cards_counted=len(inputs.cards),
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        cards_with_roles=len(with_roles),
        cards_without_roles=tuple(
            sorted(
                (c for c in inputs.cards if c.thesis_id not in with_roles),
                key=lambda c: (c.thesis_name.casefold(), str(c.thesis_id)),
            )
        ),
        role_tags=len({(m.thesis_id, company_key(m), m.context_role) for m in role_members}),
        companies=len({m.security_id for m in resolved}),
        by_role=tuple(RoleCount(r, n) for r, n in tally.items()),
        rows=tuple(rows),
        rows_with_filing=sum(1 for r in rows if r.last_filing is not None),
        unresolved=tuple(dict.fromkeys(m.ticker for m in unresolved)),
        unresolved_rows=len(unresolved),
        flagged=flagged_in_view(resolved, inputs.flagged),
        tape_ends_differ=sum(1 for r in rows if r.tape.gap == "ends_differ"),
        spy=inputs.spy,
        filing_days=FILING_DAYS,
    )
