"""Filing Stream (``docs/dashboards/filing-stream.md``): ONE card's names' filing-derived facts as a dated stream,
newest day first, as they were readable at the as-of. Follows the as-of dial.

PURE: the router reads the card's roster as of the as-of and each company's rows through the shared reader
(``dashboards/filings.py``, which threads the request's one ``serve_view(asof)`` pair into every read), builds
``stream_inputs(...)`` ONCE per (card, clock, as-of) (the shared dashboard cache, ``dashboards/cache.py``), and
``filing_stream(...)`` slices the window per request. Display only: nothing here feeds a call, ranks an idea or
writes anything (rule 1).

The rules, in one place. This is our own simple listing, stated here and on the page.
- **Which names.** Every roster row at the as-of, Context and Bench included (research is where the silent names
  earn their place): each company ONCE (by ``security_id``; a row with no resolved security by its ticker), its
  card links folded together. The page's "Counting names only" toggle (client side) leaves the silent ones out.
  A basket row with no resolved security can carry no facts: counted and listed in the roster note (invariant
  #9), never in the stream.
- **The window.** The last 30, 90 (default) or 365 days: dates from ``asof - N days`` through ``asof``,
  inclusive. The cached read holds the 365-day rows; a window is sliced per request.
- **The order.** Newest day first; within a day by kind (``filings.KIND_ORDER``: 8-K, 13D/G, Form 4, 4/A,
  revenue, split, dilution, catalyst, ratified fact), then by ticker.
- **The fold.** A day's ``routine`` rows (a Form 4 holding no code-P purchase) collapse into one quiet line on the
  page (a client toggle). The counts here are of EVERY row, folded or not (``Counts.routine`` says how many rows
  the fold covers); nothing is hidden from the numbers.
- **Links.** The card's real links in today's chain order (links load live, only the roster is snapshotted), then
  a roster link that is not on the card today, "No link", Context and Bench (the silent links, last).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from dashboards.baskets import Membership, link_count
from dashboards.filings import KIND_ORDER, FilingKind, FilingRow
from domain.enums import ContextRole
from domain.thesis import SILENT_LABELS

WINDOWS: tuple[int, ...] = (30, 90, 365)
DEFAULT_DAYS = 90
# the cached read's span: the longest window
READ_DAYS = max(WINDOWS)
# the card roster's role tags in the enum's own order, so a role added to ``ContextRole`` lists itself
ROLE_ORDER: tuple[str, ...] = tuple(r.value for r in ContextRole)
NO_LINK = "No link"

LinkKind = Literal["link", "old_link", "no_link", "silent"]


# --- the roster ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Company:
    """One company on the card's roster, each once. ``key`` = its ``security_id`` (``ticker:<T>`` for a row with no
    resolved security). ``links`` = its links on the card (``None`` = no link; Context / Bench by their labels);
    ``role`` = its context role (identical on every row of a company, the promote's rule); ``silent`` = in
    Context or Bench (never counts toward the call)."""

    key: str
    ticker: str
    security_id: UUID | None
    links: tuple[str | None, ...]
    role: str | None
    silent: bool


def _key(m: Membership) -> str:
    return str(m.security_id) if m.security_id is not None else f"ticker:{m.ticker.strip().upper()}"


def companies_of(memberships: Iterable[Membership]) -> tuple[Company, ...]:
    """Each company once, in roster order: its first row's ticker, its links folded in first-seen order, its
    role the first one set, silent when any of its rows is (a company is silent or voting, never both).
    """
    by_key: dict[str, list[Membership]] = {}
    for m in memberships:
        by_key.setdefault(_key(m), []).append(m)
    out = []
    for key, rows in by_key.items():
        out.append(
            Company(
                key=key,
                ticker=rows[0].ticker,
                security_id=rows[0].security_id,
                links=tuple(dict.fromkeys(m.segment for m in rows)),
                role=next((m.context_role for m in rows if m.context_role), None),
                silent=any(m.silent for m in rows),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class LinkOption:
    """A link a page can filter by: ``label`` (``None`` for "No link" is spelled ``NO_LINK``), its ``kind`` and
    the companies on it (each once)."""

    key: str
    label: str
    kind: LinkKind
    names: int


def link_options(segments: Sequence[str], companies: Sequence[Company]) -> tuple[LinkOption, ...]:
    """The filter's links in page order: the card's real links in today's chain order, a roster link that is not
    on the card today (in first-seen order), "No link" when a company has none, then Context and Bench.
    """
    on_roster: dict[str | None, int] = {}
    for c in companies:
        for link in c.links:
            on_roster[link] = on_roster.get(link, 0) + 1
    real = [s for s in dict.fromkeys(segments) if s not in SILENT_LABELS]
    out = [LinkOption(f"link:{s}", s, "link", on_roster.get(s, 0)) for s in real]
    for link, n in on_roster.items():
        if link is not None and link not in SILENT_LABELS and link not in real:
            out.append(LinkOption(f"old:{link}", link, "old_link", n))
    if None in on_roster:
        out.append(LinkOption("no_link", NO_LINK, "no_link", on_roster[None]))
    out.extend(
        LinkOption(f"silent:{s}", s, "silent", on_roster[s])
        for s in SILENT_LABELS
        if s in on_roster
    )
    return tuple(out)


def roles_set(companies: Iterable[Company]) -> tuple[str, ...]:
    """The context roles set on the card, in the enum's order."""
    have = {c.role for c in companies if c.role}
    return tuple(r for r in ROLE_ORDER if r in have)


# --- the rows -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamRow:
    """A filing row with its company's place on the card: ``links`` / ``role`` / ``silent`` ride with the row so
    the page filters without a second lookup. ``key`` is unique within one response."""

    key: str
    row: FilingRow
    links: tuple[str | None, ...]
    role: str | None
    silent: bool


_RANK = {k: i for i, k in enumerate(KIND_ORDER)}


def sort_key(r: FilingRow) -> tuple:
    """Newest first; within a day by kind, then ticker, then (for a stable order) accession, form, headline."""
    return (
        -r.date.toordinal(),
        _RANK[r.kind],
        r.ticker.casefold(),
        r.accession or "",
        r.form,
        r.headline,
    )


def stream_rows(
    rows_by_sid: Mapping[UUID, Sequence[FilingRow]], companies: Sequence[Company]
) -> tuple[StreamRow, ...]:
    """Every company's rows with its place on the card, in stream order. A row of a company not on the roster
    cannot occur (the reader reads the roster's ids); one is left out rather than guessed at."""
    by_sid = {c.security_id: c for c in companies if c.security_id is not None}
    flat = [(r, by_sid[sid]) for sid, rs in rows_by_sid.items() if sid in by_sid for r in rs]
    flat.sort(key=lambda rc: sort_key(rc[0]))
    return tuple(StreamRow(f"{i}", r, c.links, c.role, c.silent) for i, (r, c) in enumerate(flat))


def in_window(rows: Iterable[StreamRow], asof: date, days: int) -> tuple[StreamRow, ...]:
    """The rows dated from ``asof - days`` through ``asof``, inclusive (a row dated after the as-of is never in)."""
    start = asof - timedelta(days=days)
    return tuple(r for r in rows if start <= r.row.date <= asof)


@dataclass(frozen=True)
class Day:
    """One day of the stream: its rows in stream order; ``routine`` = how many the fold covers."""

    date: date
    rows: tuple[StreamRow, ...]
    routine: int


def group_by_day(rows: Iterable[StreamRow]) -> tuple[Day, ...]:
    """The rows (already in stream order) under day headers, newest day first."""
    out: list[tuple[date, list[StreamRow]]] = []
    for r in rows:
        if out and out[-1][0] == r.row.date:
            out[-1][1].append(r)
        else:
            out.append((r.row.date, [r]))
    return tuple(Day(d, tuple(rs), sum(1 for x in rs if x.row.routine)) for d, rs in out)


@dataclass(frozen=True)
class KindCount:
    kind: FilingKind
    rows: int


@dataclass(frozen=True)
class Counts:
    """What the window holds, folded rows included: every kind (a zero too), the Form 4s holding a purchase, and
    the rows the fold covers."""

    rows: int
    by_kind: tuple[KindCount, ...]
    form4_purchases: int
    routine: int


def counts_of(rows: Iterable[StreamRow]) -> Counts:
    got = list(rows)
    tally = {k: 0 for k in KIND_ORDER}
    for r in got:
        tally[r.row.kind] += 1
    return Counts(
        rows=len(got),
        by_kind=tuple(KindCount(k, tally[k]) for k in KIND_ORDER),
        form4_purchases=sum(1 for r in got if r.row.kind == "form4" and not r.row.routine),
        routine=sum(1 for r in got if r.row.routine),
    )


# --- the cached read, and one view --------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamInputs:
    """Everything the heavy read produces for one card: cached per (card, clock, as-of); every window derived."""

    thesis_id: UUID
    thesis_name: str
    roster_fallback: bool  # no roster snapshot by the as-of: today's roster stands in
    links_today: int  # the card's real links today (Context and Bench not counted)
    basket_rows: int  # every roster row
    silent_rows: int  # of those, in Context or Bench
    unresolved: tuple[
        str, ...
    ]  # tickers of the rows with no resolved security: counted, never in the stream
    links: tuple[LinkOption, ...]
    roles: tuple[str, ...]
    companies: tuple[Company, ...]
    rows: tuple[StreamRow, ...]  # the ``READ_DAYS`` window, in stream order


def stream_inputs(
    *,
    thesis_id: UUID,
    thesis_name: str,
    segments: Sequence[str],
    memberships: Sequence[Membership],
    rows_by_sid: Mapping[UUID, Sequence[FilingRow]],
    roster_fallback: bool = False,
) -> StreamInputs:
    """One card's cached read. ``segments`` = its chain order (link labels as loaded); ``memberships`` = its roster
    rows at the as-of (``baskets.memberships_of``); ``rows_by_sid`` = ``filings.read_filing_rows`` over the
    roster's resolved companies."""
    rows_in = list(memberships)
    companies = companies_of(rows_in)
    return StreamInputs(
        thesis_id=thesis_id,
        thesis_name=thesis_name,
        roster_fallback=roster_fallback,
        links_today=link_count(segments),
        basket_rows=len(rows_in),
        silent_rows=sum(1 for m in rows_in if m.silent),
        unresolved=tuple(dict.fromkeys(m.ticker for m in rows_in if m.security_id is None)),
        links=link_options(segments, companies),
        roles=roles_set(companies),
        companies=companies,
        rows=stream_rows(rows_by_sid, companies),
    )


@dataclass(frozen=True)
class NameView:
    """A roster company with how many rows it holds in the window (a name with none is still listed)."""

    company: Company
    rows: int


@dataclass(frozen=True)
class FilingStream:
    asof: date
    days: int
    window_start: date
    inputs: StreamInputs
    groups: tuple[Day, ...]
    counts: Counts
    names: tuple[NameView, ...]


def filing_stream(inputs: StreamInputs, *, asof: date, days: int = DEFAULT_DAYS) -> FilingStream:
    """The page for one window (the last ``days`` days)."""
    if days not in WINDOWS:
        raise ValueError(f"days must be one of {WINDOWS}, not {days}")
    rows = in_window(inputs.rows, asof, days)
    held: dict[UUID, int] = {}
    for r in rows:
        held[r.row.security_id] = held.get(r.row.security_id, 0) + 1
    return FilingStream(
        asof=asof,
        days=days,
        window_start=asof - timedelta(days=days),
        inputs=inputs,
        groups=group_by_day(rows),
        counts=counts_of(rows),
        names=tuple(
            NameView(c, held.get(c.security_id, 0) if c.security_id is not None else 0)
            for c in inputs.companies
        ),
    )
