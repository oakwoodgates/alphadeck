"""Forming Quietly (``docs/dashboards/forming-quietly.md``): the cards whose RECORD shows a key turned (Warming) or
that are Incubating with a quiet signal on their roster, at the lowest energy the app has, with what each still
needs. Follows the as-of dial. Invariant #7 (inverse loudness) is this page's design brief: nothing on it is Armed,
it ranks nothing by promise (#4) and says nothing about timing.

PURE: the router reads the cards counted at the as-of (``dashboards/baskets.py``), the ``calls`` log (the recorded
card in effect per card, and the rows the run walk reads), the master's tickers and issuer CIKs, then for the names on
a row the display members' readings (``baskets.per_company``), one ``breadth_for`` read per card and the card's
EDGAR term-mention months; ``forming(...)`` builds the payload from them. Display only: nothing here feeds a call,
judges an idea or writes anything (``docs/DASHBOARDS.md`` rule 1).

The rules, in one place:
- **Who is a row.** From each counted card's RECORDED call in effect at the as-of (the greatest ``(asof, seq)`` with
  ``asof <=`` the as-of: the What Changed read). Every WARMING card is a row; every INCUBATING card is a row with a
  ``lit`` flag (the page shows it by default only when it is lit, or when the operator asks for every Incubating
  card). An ARMED or MANAGING card is never a row (the Hot Board and Entry Windows have them) and is counted; a card
  with no recorded call at the as-of is counted ("no call yet").
- **A quiet signal is lit** by one of four readings of the card's counting roster (Context and Bench never count):
  insider buying (``insider_flow_90d``: at least ``INSIDER_MIN_BUYS`` open-market buys in 30 days; a name is a CLUSTER
  at ``CLUSTER_MIN_BUYERS`` distinct buyers, the Leaders rule), coiling (``vcp``'s headline key ``coiling``), leading
  (``relative_strength``'s headline key ``leading``) and the theme-breadth member's own ``thrust`` headline. The
  breadth count itself and the adoption slope are shown wherever they exist and never light a card alone (no
  threshold for either is specified).
- **A flagged price history is left out of every reading** (``baskets.price_flags``): the name is counted and listed,
  never read (Leaders' rule, insider buying included: the insider screen reads the price tape too).
- **The run walk** (``run_start``): the first night of the card's CURRENT recorded run. Walk the log rows back from the
  night in effect, one winning row per night (the highest ``seq``, the Idea Timeline's rule), while the night's state
  is the card's; the earliest night of that unbroken streak. A reconstructed first night is marked.
- **The adoption slope** (``adoption_slope``): documents summed over the card's SIGNAL terms for the last three CLOSED
  calendar months against the three before. A month is closed when its last day is before the as-of, so the as-of's
  own month never counts. A term counts only with all six closed months stored (like for like); a card with no such
  term reads "not counted yet". A zero prior span has no percent (the two counts are shown).
- **Default order** (``default_order``): Warming rows by the run's first night, newest first; then Incubating rows with
  a slope percent, largest first; then the rest by card name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from dashboards.baskets import FlaggedCompany
from domain.call import CallCard, TriggerRef
from domain.enums import Grade, Kind, State
from domain.market_time import market_tz
from signals.display import insider_flow, relative_strength, vcp
from signals.display.base import DisplaySignal

SLUG = "forming-quietly"

# the three display members run once per name (by registered name; a renamed member fails loud in
# ``baskets.display_members``); ``theme_breadth`` is a thesis-level reader, read once per card
MEMBERS: tuple[str, ...] = (
    insider_flow.MEMBER_NAME,
    vcp.MEMBER_NAME,
    relative_strength.MEMBER_NAME,
)

# insider buying is lit by at least this many open-market buys in the last 30 days (the Leaders rule)
INSIDER_MIN_BUYS = 1
# a name's insider CLUSTER word: at least this many distinct buyers in the last 30 days (the Cockpit's Ins 30d)
CLUSTER_MIN_BUYERS = 2
# the adoption slope: months per span (the last SPAN closed months against the SPAN before)
ADOPTION_SPAN = 3

RowState = Literal["warming", "incubating"]
ROW_STATES: frozenset[State] = frozenset({State.WARMING, State.INCUBATING})


# ---------------------------------------------------------------------------------------------------------
# the rows as read
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recorded:
    """One ``calls`` row in effect on a night: its own night (``asof``), its insertion order (``seq``), whether
    ``pipeline.backfill`` wrote it (``reconstructed``) and the card."""

    asof: date
    seq: int
    reconstructed: bool
    card: CallCard


@dataclass(frozen=True)
class LogRow:
    """One ``calls`` row as the run walk reads it: its night, its order, its state (the card blob is not read)."""

    asof: date
    seq: int
    reconstructed: bool
    state: State


@dataclass(frozen=True)
class RunStart:
    """The first night of a card's current recorded run, whether that night's row was rebuilt by the backfill and
    how many recorded nights the run holds."""

    night: date
    reconstructed: bool
    nights: int


def winning_nights(rows: Iterable[LogRow]) -> list[LogRow]:
    """One row per night, the one with the highest ``seq``, newest night first."""
    best: dict[date, LogRow] = {}
    for r in rows:
        cur = best.get(r.asof)
        if cur is None or r.seq > cur.seq:
            best[r.asof] = r
    return sorted(best.values(), key=lambda r: r.asof, reverse=True)


def run_start(rows: Iterable[LogRow], state: State) -> RunStart | None:
    """The first night of the current run of ``state``: walk the nights back from the newest (the night in
    effect) while its winning row's state is ``state``; the earliest night of that unbroken streak. ``None`` when
    the newest night is not in ``state`` (or there are no rows)."""
    first: LogRow | None = None
    count = 0
    for night in winning_nights(rows):
        if night.state != state:
            break
        first = night
        count += 1
    return None if first is None else RunStart(first.asof, first.reconstructed, count)


# ---------------------------------------------------------------------------------------------------------
# the card's recorded call
# ---------------------------------------------------------------------------------------------------------


def card_state_counts(
    names: Mapping[UUID, str], recorded: Mapping[UUID, Recorded]
) -> dict[str, int]:
    """The counted cards by their recorded state at the as-of, plus ``no_call`` (nothing recorded by then)."""
    out = {"armed": 0, "warming": 0, "managing": 0, "incubating": 0, "no_call": 0}
    for tid in names:
        rec = recorded.get(tid)
        if rec is None:
            out["no_call"] += 1
        else:
            out[rec.card.state.value] += 1
    return out


def row_cards(names: Mapping[UUID, str], recorded: Mapping[UUID, Recorded]) -> list[UUID]:
    """The cards that are rows: recorded Warming or Incubating at the as-of, by card name."""
    ids = [t for t in names if t in recorded and recorded[t].card.state in ROW_STATES]
    return sorted(ids, key=lambda t: (names[t].casefold(), str(t)))


@dataclass(frozen=True)
class SourceLink:
    """One place a trigger points at: the kind of source as stored, its reference and a link (``None`` = none
    known: a price reference, a free-text one), by the Evidence Ledger's rule (``filings.source_url``).
    """

    source: str
    ref: str
    url: str | None


@dataclass(frozen=True)
class TriggerOut:
    label: str
    kind: Kind
    grade: Grade | None
    event_date: (
        date | None
    )  # the trigger's FIRE date (the market event), not the night it was recorded
    sources: tuple[SourceLink, ...]


@dataclass(frozen=True)
class NameChip:
    """A name behind the key that turned: its ticker on the card and every trigger that fired on it."""

    security_id: UUID
    ticker: str
    triggers: tuple[TriggerOut, ...]


@dataclass(frozen=True)
class WatchChip:
    """A confirmation-only member of the recorded call (``watch_members``)."""

    security_id: UUID
    ticker: str


@dataclass(frozen=True)
class RiskOut:
    """One risk signal on the recorded card, with the name it fired on."""

    label: str
    kind: Kind
    security_id: UUID
    ticker: str


@dataclass(frozen=True)
class KeyOut:
    """A key as the call recorded it: ``label`` and ``detail`` are the assembler's own words."""

    turned: bool
    label: str
    detail: str | None


UrlOf = Callable[[str, str | None], str | None]


def _trigger_out(t: TriggerRef, cik: str | None, url_of: UrlOf) -> TriggerOut:
    return TriggerOut(
        label=t.label,
        kind=t.kind,
        grade=t.grade,
        event_date=t.event_date,
        sources=tuple(SourceLink(p.source, p.ref, url_of(p.ref, cik)) for p in t.sources),
    )


def name_chips(
    card: CallCard,
    ticker_of: Callable[[UUID], str],
    ciks: Mapping[UUID, str | None],
    url_of: UrlOf,
) -> tuple[NameChip, ...]:
    """The names behind the recorded keys: each ``triggers_fired`` entry grouped by its security, by ticker."""
    by_sid: dict[UUID, list[TriggerOut]] = {}
    for t in card.triggers_fired:
        by_sid.setdefault(t.security_id, []).append(
            _trigger_out(t, ciks.get(t.security_id), url_of)
        )
    chips = [NameChip(sid, ticker_of(sid), tuple(ts)) for sid, ts in by_sid.items()]
    return tuple(sorted(chips, key=lambda c: (c.ticker.casefold(), str(c.security_id))))


def watch_chips(card: CallCard, ticker_of: Callable[[UUID], str]) -> tuple[WatchChip, ...]:
    """The recorded card's confirmation-only members, each once, by ticker."""
    seen: set[UUID] = set()
    chips: list[WatchChip] = []
    for m in card.watch_members:
        if m.security_id not in seen:
            seen.add(m.security_id)
            chips.append(WatchChip(m.security_id, ticker_of(m.security_id)))
    return tuple(sorted(chips, key=lambda c: (c.ticker.casefold(), str(c.security_id))))


def risks_of(card: CallCard, ticker_of: Callable[[UUID], str]) -> tuple[RiskOut, ...]:
    """The recorded card's risk signals, in the recorded order."""
    return tuple(
        RiskOut(t.label, t.kind, t.security_id, ticker_of(t.security_id)) for t in card.risk_signals
    )


# ---------------------------------------------------------------------------------------------------------
# the quiet signals
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One company's readings at the as-of: the few facts this page shows. ``None`` = no reading. A flagged price
    history carries its reason in ``flag`` and every other field blank: it is not read at all."""

    flag: str | None = None
    buys_30d: int | None = None  # None = no Form 4 ingested
    buyers_30d: int | None = None
    coiling: bool | None = None  # None = too little tape to read a base
    contraction: float | None = None  # the newest segment's depth over the oldest's
    rs_leading: bool | None = None  # None = no 13-week verdict
    rs_vs: tuple[str, ...] = ()  # the benchmarks the relative strength is at a 13-week high against


def _metric(sig: DisplaySignal | None, key: str) -> float | None:
    if sig is None:
        return None
    return next((m.value for m in sig.metrics if m.key == key), None)


def _count(sig: DisplaySignal | None, key: str) -> int | None:
    v = _metric(sig, key)
    return None if v is None else round(v)


def reading_of(signals: Mapping[str, DisplaySignal | None], flag: str | None = None) -> Reading:
    """One company's reading off the three display members' signals (keyed by member name). A flagged price history
    is not read: the reading is blank and carries the flag's reason."""
    if flag is not None:
        return Reading(flag=flag)
    ins = signals.get(insider_flow.MEMBER_NAME)
    buys = _count(ins, "buy_count_30d")
    buyers = _count(ins, "distinct_buyers_30d")
    coil = signals.get(vcp.MEMBER_NAME)
    rs = signals.get(relative_strength.MEMBER_NAME)
    rs_leading: bool | None = None
    rs_vs: tuple[str, ...] = ()
    if rs is not None and (rs.basis.bars_used or 0) >= relative_strength.MIN_BARS:
        events = {e.key for e in rs.events}
        rs_vs = tuple(s for s in relative_strength.BENCHMARKS if f"rs_high_{s.lower()}" in events)
        rs_leading = rs.headline is not None and rs.headline.key == "leading"
    return Reading(
        buys_30d=buys if ins is not None else None,
        buyers_30d=buyers,
        coiling=(
            None if coil is None else (coil.headline is not None and coil.headline.key == "coiling")
        ),
        contraction=_metric(coil, "contraction"),
        rs_leading=rs_leading,
        rs_vs=rs_vs,
    )


@dataclass(frozen=True)
class InsiderName:
    security_id: UUID
    ticker: str
    buys_30d: int
    buyers_30d: int
    cluster: bool


@dataclass(frozen=True)
class CoilingName:
    security_id: UUID
    ticker: str
    contraction: float | None


@dataclass(frozen=True)
class LeadingName:
    security_id: UUID
    ticker: str
    vs: tuple[str, ...]


@dataclass(frozen=True)
class Breadth:
    """The card's theme-breadth reading (``theme_breadth.breadth_for``): ``above`` of ``counted`` names at or above
    their 50-day average; ``thin`` names had too little history and are not counted. ``thrust`` is the member's own
    headline (a majority above the line and a surge against 20 bars ago)."""

    above: int | None
    counted: int
    thin: int
    pct: float | None
    delta_pts: float | None
    thrust: bool


def breadth_of(sig: DisplaySignal | None) -> Breadth | None:
    """The page's breadth reading off the member's signal; ``None`` without a signal (no resolved name)."""
    if sig is None:
        return None
    counted = _count(sig, "members_counted") or 0
    thin = _count(sig, "members_thin") or 0
    pct = _metric(sig, "breadth")
    return Breadth(
        above=None if pct is None else round(pct / 100.0 * counted),
        counted=counted,
        thin=thin,
        pct=pct,
        delta_pts=_metric(sig, "breadth_delta"),
        thrust=sig.headline is not None and sig.headline.key == "thrust",
    )


@dataclass(frozen=True)
class Quiet:
    """One card's quiet signals over its counting roster. ``names`` = the counting names (Context and Bench left
    out), ``read`` = those actually read (a flagged price history is counted in ``flagged``, never read),
    ``unresolved`` = roster rows with no resolved security. ``no_form4`` / ``coiling_no_reading`` /
    ``leading_no_reading`` count the read names a member gave no reading for. ``lit`` = at least one signal that
    lights a card is on (insider buying, coiling, leading, breadth thrust)."""

    names: int
    read: int
    unresolved: int
    flagged: tuple[FlaggedCompany, ...]
    insider: tuple[InsiderName, ...]
    no_form4: int
    coiling: tuple[CoilingName, ...]
    coiling_no_reading: int
    leading: tuple[LeadingName, ...]
    leading_no_reading: int
    breadth: Breadth | None
    lit: bool


def quiet_of(
    names: Sequence[tuple[UUID, str]],
    readings: Mapping[UUID, Reading],
    breadth: DisplaySignal | None,
    *,
    unresolved: int = 0,
) -> Quiet:
    """A card's quiet signals. ``names``: its counting companies, each once, as ``(security_id, ticker)``;
    ``readings``: each company's reading (a company with none counts as read with every field blank);
    ``breadth``: the card's theme-breadth signal."""
    flagged: list[FlaggedCompany] = []
    insider: list[InsiderName] = []
    coiling: list[CoilingName] = []
    leading: list[LeadingName] = []
    read = no_form4 = coil_none = lead_none = 0
    for sid, ticker in names:
        r = readings.get(sid, Reading())
        if r.flag is not None:
            flagged.append(FlaggedCompany(sid, ticker, r.flag))
            continue
        read += 1
        if r.buys_30d is None:
            no_form4 += 1
        elif r.buys_30d >= INSIDER_MIN_BUYS:
            buyers = r.buyers_30d or 0
            insider.append(
                InsiderName(sid, ticker, r.buys_30d, buyers, buyers >= CLUSTER_MIN_BUYERS)
            )
        if r.coiling is None:
            coil_none += 1
        elif r.coiling:
            coiling.append(CoilingName(sid, ticker, r.contraction))
        if r.rs_leading is None:
            lead_none += 1
        elif r.rs_leading:
            leading.append(LeadingName(sid, ticker, r.rs_vs))
    insider.sort(
        key=lambda n: (-n.buyers_30d, -n.buys_30d, n.ticker.casefold(), str(n.security_id))
    )
    coiling.sort(key=lambda n: (n.ticker.casefold(), str(n.security_id)))
    leading.sort(key=lambda n: (n.ticker.casefold(), str(n.security_id)))
    flagged.sort(key=lambda f: (f.ticker.casefold(), str(f.security_id)))
    b = breadth_of(breadth)
    return Quiet(
        names=len(names),
        read=read,
        unresolved=unresolved,
        flagged=tuple(flagged),
        insider=tuple(insider),
        no_form4=no_form4,
        coiling=tuple(coiling),
        coiling_no_reading=coil_none,
        leading=tuple(leading),
        leading_no_reading=lead_none,
        breadth=b,
        lit=bool(insider or coiling or leading or (b is not None and b.thrust)),
    )


# ---------------------------------------------------------------------------------------------------------
# the adoption slope
# ---------------------------------------------------------------------------------------------------------


def month_last_day(month: date) -> date:
    """The last calendar day of ``month`` (any day in it)."""
    first_next = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return date.fromordinal(first_next.toordinal() - 1)


def closed_months(asof: date, n: int = ADOPTION_SPAN * 2) -> list[date]:
    """The ``n`` most recent calendar months that CLOSED before ``asof`` (the last day before the as-of), each as its
    first day, ascending. The as-of's own month is never one of them: it is partial."""
    y, m = asof.year, asof.month
    out: list[date] = []
    for _ in range(n):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        out.append(date(y, m, 1))
    return out[::-1]


@dataclass(frozen=True)
class Adoption:
    """A card's EDGAR term-mention slope: ``recent`` documents (the last three closed months, ``recent_from`` to
    ``recent_to`` as first days) against ``prior`` (the three before), summed over the SIGNAL terms that have all six
    closed months stored (``terms_counted`` of ``terms_signal``; ``terms_partial`` have some, ``terms_unstored``
    none). ``percent`` is ``None`` without a nonzero prior span. ``months_stored`` = the six closed months any
    signal term has a row for. ``latest_partial`` = a counted term's latest closed month was read on or before its
    last day (a weekly run had not yet re-read it after it closed)."""

    counted: bool
    percent: float | None
    recent: int | None
    prior: int | None
    recent_from: date | None
    recent_to: date | None
    prior_from: date | None
    prior_to: date | None
    terms_signal: int
    terms_counted: int
    terms_partial: int
    terms_unstored: int
    months_stored: int
    latest_partial: bool


def _read_date(fetched_at: object) -> date | None:
    if not isinstance(fetched_at, datetime):
        return None
    return fetched_at.astimezone(market_tz()).date()


def adoption_slope(
    rows: Iterable[Mapping[str, object]], signal_terms: Sequence[str], asof: date
) -> Adoption:
    """The adoption slope off the card's stored term-month rows (``PointInTimeData.term_mentions``: the newest
    version of each ``(term, month)``, as-of) and its current SIGNAL terms. See the module docstring.
    """
    months = closed_months(asof)
    prior_m, recent_m = months[:ADOPTION_SPAN], months[ADOPTION_SPAN:]
    want = {t.casefold() for t in signal_terms}
    docs: dict[str, dict[date, int]] = {}
    read_on: dict[str, dict[date, date | None]] = {}
    for r in rows:
        term = str(r["term"]).casefold()
        month = r["month"]
        if term in want and isinstance(month, date) and month in months:
            docs.setdefault(term, {})[month] = int(r["documents"])  # type: ignore[call-overload]
            read_on.setdefault(term, {})[month] = _read_date(r.get("fetched_at"))
    full = sorted(t for t, got in docs.items() if len(got) == len(months))
    stored = {m for got in docs.values() for m in got}

    def shape(**over: object) -> Adoption:
        base: dict[str, object] = dict(
            counted=False,
            percent=None,
            recent=None,
            prior=None,
            recent_from=None,
            recent_to=None,
            prior_from=None,
            prior_to=None,
            terms_signal=len(want),
            terms_counted=len(full),
            terms_partial=len(docs) - len(full),
            terms_unstored=len(want) - len(docs),
            months_stored=len(stored),
            latest_partial=False,
        )
        base.update(over)
        return Adoption(**base)  # type: ignore[arg-type]

    if not full:
        return shape()
    recent = sum(docs[t][m] for t in full for m in recent_m)
    prior = sum(docs[t][m] for t in full for m in prior_m)
    last = months[-1]
    latest_partial = any(
        (d := read_on[t].get(last)) is not None and d <= month_last_day(last) for t in full
    )
    return shape(
        counted=True,
        percent=None if prior == 0 else round((recent - prior) / prior * 100.0, 1),
        recent=recent,
        prior=prior,
        recent_from=recent_m[0],
        recent_to=recent_m[-1],
        prior_from=prior_m[0],
        prior_to=prior_m[-1],
        latest_partial=latest_partial,
    )


# ---------------------------------------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FormingRow:
    """One card the record shows Warming or Incubating. ``recorded_on`` = the night of the recorded call in effect;
    ``run_start`` = the first night of its current run of that state (``days_in_state`` = the calendar days from
    there to the as-of; ``run_reconstructed`` = that night was rebuilt by ``pipeline.backfill``); the keys are the
    call's own words; ``names`` = the names behind the keys that turned; ``missing`` = the call's ``missing[]`` lines
    verbatim."""

    key: str  # the thesis id
    thesis_id: UUID
    thesis_name: str
    state: RowState
    recorded_on: date
    run_start: date | None
    run_nights: int
    days_in_state: int | None
    run_reconstructed: bool
    key_conviction: KeyOut
    key_confirmation: KeyOut
    names: tuple[NameChip, ...]
    missing: tuple[str, ...]
    watch: tuple[WatchChip, ...]
    risks: tuple[RiskOut, ...]
    quiet: Quiet
    adoption: Adoption


def build_row(
    thesis_id: UUID,
    thesis_name: str,
    rec: Recorded,
    runs: Sequence[LogRow],
    quiet: Quiet,
    adoption: Adoption,
    *,
    asof: date,
    ticker_of: Callable[[UUID], str],
    ciks: Mapping[UUID, str | None],
    url_of: UrlOf,
) -> FormingRow:
    card = rec.card
    start = run_start(runs, card.state)
    return FormingRow(
        key=str(thesis_id),
        thesis_id=thesis_id,
        thesis_name=thesis_name,
        state="warming" if card.state == State.WARMING else "incubating",
        recorded_on=rec.asof,
        run_start=start.night if start else None,
        run_nights=start.nights if start else 0,
        days_in_state=(asof - start.night).days if start else None,
        run_reconstructed=bool(start and start.reconstructed),
        key_conviction=KeyOut(
            card.key_conviction.turned, card.key_conviction.label, card.key_conviction.detail
        ),
        key_confirmation=KeyOut(
            card.key_confirmation.turned, card.key_confirmation.label, card.key_confirmation.detail
        ),
        names=name_chips(card, ticker_of, ciks, url_of),
        missing=tuple(card.missing),
        watch=watch_chips(card, ticker_of),
        risks=risks_of(card, ticker_of),
        quiet=quiet,
        adoption=adoption,
    )


def default_order(rows: Iterable[FormingRow]) -> list[FormingRow]:
    """Warming rows by the run's first night, newest first (a row without one last); then Incubating rows with a
    slope percent, largest first; then the rest by card name. Ties by card name."""

    def name(r: FormingRow) -> tuple[str, str]:
        return (r.thesis_name.casefold(), str(r.thesis_id))

    items = list(rows)
    warming = [r for r in items if r.state == "warming"]
    incubating = [r for r in items if r.state != "warming"]
    sloped = [r for r in incubating if r.adoption.percent is not None]
    rest = [r for r in incubating if r.adoption.percent is None]
    warming.sort(
        key=lambda r: (
            r.run_start is None,
            -(r.run_start.toordinal()) if r.run_start else 0,
            *name(r),
        )
    )
    sloped.sort(key=lambda r: (-(r.adoption.percent or 0.0), *name(r)))
    rest.sort(key=name)
    return [*warming, *sloped, *rest]


@dataclass(frozen=True)
class FormingQuietly:
    asof: date
    clock: Literal["record", "public"]
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards_armed: int
    cards_warming: int
    cards_managing: int
    cards_incubating: int
    cards_incubating_lit: (
        int  # Incubating cards with a quiet signal lit (the page's default Incubating rows)
    )
    cards_no_call: int
    cards_with_term_months: (
        int  # row cards with at least one stored signal-term month in the six closed months
    )
    cards_without_term_months: int
    names_read: int  # distinct companies read across the rows
    names_flagged: int  # distinct companies left out for a flagged price history
    members: tuple[str, ...]
    rows: tuple[FormingRow, ...]
    flagged: tuple[FlaggedCompany, ...]


def forming(
    rows: Sequence[FormingRow],
    *,
    asof: date,
    clock: Literal["record", "public"],
    counts: Mapping[str, int],
    live_cards: int,
    cards_counted: int,
    cards_not_yet_created: int,
    roster_fallback_cards: int,
    names_read: int,
    flagged: Sequence[FlaggedCompany],
    members: Sequence[str] = MEMBERS,
) -> FormingQuietly:
    """The whole payload from the built rows and the counts read for them."""
    ordered = default_order(rows)
    return FormingQuietly(
        asof=asof,
        clock=clock,
        live_cards=live_cards,
        cards_counted=cards_counted,
        cards_not_yet_created=cards_not_yet_created,
        roster_fallback_cards=roster_fallback_cards,
        cards_armed=counts["armed"],
        cards_warming=counts["warming"],
        cards_managing=counts["managing"],
        cards_incubating=counts["incubating"],
        cards_incubating_lit=sum(1 for r in ordered if r.state == "incubating" and r.quiet.lit),
        cards_no_call=counts["no_call"],
        cards_with_term_months=sum(1 for r in ordered if r.adoption.months_stored > 0),
        cards_without_term_months=sum(1 for r in ordered if r.adoption.months_stored == 0),
        names_read=names_read,
        names_flagged=len(flagged),
        members=tuple(members),
        rows=tuple(ordered),
        flagged=tuple(flagged),
    )
