"""Chain Map (``docs/dashboards/chain-map.md``): ONE card as a picture. Its real links in chain order are the
columns, the companies on the roster at the as-of are the nodes inside them, and the role-tagged companies stand
on two rails beside the chain, each with one edge to the chain's bracket. Follows the as-of dial.

PURE: the router reads the card's roster as of the as-of (``thesis_repo.get_asof``, today's roster standing in
before its first snapshot), each company's identity, its 50-day side (the ``sma_position`` display member), its
newest filing-derived fact (the shared reader ``dashboards/filings.py``) and the price flags, and hands them to
``chain_map(...)``, which places every company and counts the work. The whole page is cached per (card, clock,
as-of) (``dashboards/cache.py``); the Context/Bench toggle is client side. Display only: nothing here feeds a
call, scores an idea or writes anything (rule 1). The 50-day side and the last-filing age are display context,
never a call input (``docs/DISPLAY_SIGNALS.md``); a role is display metadata (#4).

The rules, in one place. This is a layout, not a model: no edge weights, no clustering, no inferred relationship.
- **The columns.** The card's real links in its chain order (``thesis.segments`` as loaded: today's chain,
  Discovered wherever it sits; Context and Bench are not links here), then a roster link that is not on the card
  today ("not a link today", in roster order), then "No link", then the quiet Context and Bench columns. A real
  link is always a column, even with nobody in it. The other columns exist only when somebody sits in them.
- **The nodes.** Every roster company, each ONCE per column (by ``security_id``; a row with no resolved security by
  its ticker), in roster order, with its glyphs: business type, 50-day side, last filing age. A company in two
  links is a node in both columns. A row with no resolved security is a plain node with no glyphs, counted.
- **The rails.** A company with a context role that has NO row in a real link stands on a rail (once, never in
  a column): the left rail "what pays and funds" (payer, then financier), the right rail "what reads across"
  (direct read, counter-case, participant, benchmark), roster order inside a role. A role-tagged company that
  does sit in a real link stays in its link column with the role as a tag and has no rail copy. A silent
  (Context/Bench) company with a role is on a rail; one without a role is in the Context or Bench column.
- **The edges.** One per rail node, from the node to the chain's bracket: a role names a part, not a target, so
  an edge never lands on a link or a name. The page draws the counter-case edge dashed.
- **The glyphs.** The business type is the master's leaf (the operator's re-tag winning). The 50-day side is the
  sign of ``sma_position``'s ``pct_vs_fast`` (rounded to two places: ``at`` when it rounds to zero). The last
  filing is the newest ``FilingRow`` of the 365 days ending at the as-of, whatever its kind. A company whose data
  flags reach its price readings has no 50-day side (its bars are never read) and is named with its reason; it
  stays on the map.
- **Counts.** Nothing is dropped: every roster company is a node on the chain, on a rail, or in a silent column,
  and the counts say how many of each.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from dashboards.baskets import FlaggedCompany, Membership
from dashboards.filings import KIND_ORDER, FilingRow
from domain.thesis import SILENT_LABELS, BasketMember, Segment
from signals.display.base import DisplaySignal

# the filing reader's window: a year back from the as-of
FILING_DAYS = 365
NO_LINK = "No link"
# the sma_position metric the 50-day side reads (price vs its 50-day average, in percent)
SMA_METRIC = "pct_vs_fast"

Side = Literal["left", "right"]
ColumnKind = Literal["link", "old_link", "no_link", "silent"]
Side50 = Literal["above", "below", "at"]

# the left rail, "what pays and funds", then the right rail, "what reads across": every ``ContextRole`` value, in
# the order the roles stand on their rail
LEFT_ROLES: tuple[str, ...] = ("payer", "financier")
RIGHT_ROLES: tuple[str, ...] = ("direct_read", "counter_case", "participant", "benchmark")
ROLE_ORDER: tuple[str, ...] = (*LEFT_ROLES, *RIGHT_ROLES)
ROLE_SIDE: dict[str, Side] = {
    **{r: "left" for r in LEFT_ROLES},
    **{r: "right" for r in RIGHT_ROLES},
}
RAIL_LABEL: dict[Side, str] = {"left": "what pays and funds", "right": "what reads across"}


# --- the inputs ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterRow:
    """One basket row of the card at the as-of, with the prose the readout shows. ``authored_by`` = who wrote the
    description (``thesis_fit``); ``surfaced_terms`` = the discovery terms that surfaced the name.
    """

    ticker: str
    security_id: UUID | None
    segment: str | None
    silent: bool
    context_role: str | None
    thesis_fit: str | None
    authored_by: str
    surfaced_terms: tuple[str, ...]


def roster_rows(
    memberships: Sequence[Membership], basket: Sequence[BasketMember]
) -> tuple[RosterRow, ...]:
    """The card's roster rows: ``baskets.memberships_of([card])`` (the link, the silent flag, the role) beside the
    same card's basket (the prose). The two walk the basket in the same order, so they pair row for row.
    """
    return tuple(
        RosterRow(
            ticker=m.ticker,
            security_id=m.security_id,
            segment=m.segment,
            silent=m.silent,
            context_role=m.context_role,
            thesis_fit=b.thesis_fit,
            authored_by=b.authored_by.value,
            surfaced_terms=tuple(b.surfaced_terms),
        )
        for m, b in zip(memberships, basket, strict=True)
    )


@dataclass(frozen=True)
class LastFiling:
    """A company's newest filing-derived fact in the 365 days: its public ``date`` (``age_days`` before the
    as-of), the ``form`` as filed (or the row's own label) and its one-line ``headline``."""

    date: date
    age_days: int
    form: str
    headline: str


@dataclass(frozen=True)
class CompanyFacts:
    """What the router reads about one resolved company: its master ``name`` and business-type leaf
    (``business_type``, the operator's re-tag winning), ``pct_vs_50d`` (``None`` = no reading) and its
    filing-derived rows (the shared reader's, a year of them)."""

    name: str | None = None
    business_type: str | None = None
    pct_vs_50d: float | None = None
    filings: tuple[FilingRow, ...] = ()


@dataclass(frozen=True)
class MapInputs:
    """Everything the heavy read produces for one card: cached (as the built page) per (card, clock, as-of)."""

    thesis_id: UUID
    thesis_name: str
    segments: tuple[
        Segment, ...
    ]  # the chain as loaded: today's links, in order, Context and Bench last
    roster: tuple[RosterRow, ...]  # the card's rows at the as-of
    facts: Mapping[UUID, CompanyFacts]  # each resolved company's reads
    flagged: Mapping[
        UUID, str
    ]  # companies whose data flags reach their price readings, with the reason
    roster_fallback: bool = False  # no roster snapshot by the as-of: today's roster stands in


# --- the glyphs ------------------------------------------------------------------------------------------------


def pct_vs_50d(signal: DisplaySignal | None) -> float | None:
    """The ``sma_position`` member's price-vs-50-day-average percent, ``None`` without a reading (no tape, or
    fewer than 50 bars)."""
    if signal is None:
        return None
    return next((m.value for m in signal.metrics if m.key == SMA_METRIC), None)


def side_of(pct: float | None) -> Side50 | None:
    """Above, below or at the 50-day average (``at`` = the member's two-place percent is zero)."""
    if pct is None:
        return None
    return "above" if pct > 0 else "below" if pct < 0 else "at"


def last_filing(rows: Sequence[FilingRow], asof: date) -> LastFiling | None:
    """The newest of a company's rows by date (a row dated after the as-of cannot be read and is skipped);
    among rows of that day the first by kind order, form, headline and accession, so the pick never flips.
    """
    dated = [r for r in rows if r.date <= asof]
    if not dated:
        return None
    newest = max(r.date for r in dated)
    pick = min(
        (r for r in dated if r.date == newest),
        key=lambda r: (KIND_ORDER.index(r.kind), r.form, r.headline, r.accession or ""),
    )
    return LastFiling(pick.date, (asof - pick.date).days, pick.form, pick.headline)


# --- the picture -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MapNode:
    """One company in one place. ``key`` is unique on the page (its column or rail, then the company);
    ``company_key`` is the company's (``security_id``, or ``ticker:<T>`` with none). ``links`` = every link the
    company sits in on the card (``None`` = no link). ``role`` = its context role (a tag on a chain node, the
    reason it stands on a rail). ``thesis_fit`` / ``authored_by`` / ``matched_terms`` come from the row this node
    stands for. ``side_50d`` and ``last_filing`` are display context, never a call input."""

    key: str
    company_key: str
    ticker: str
    name: str | None
    security_id: UUID | None
    links: tuple[str | None, ...]
    role: str | None
    business_type: str | None
    side_50d: Side50 | None
    pct_vs_50d: float | None
    last_filing: LastFiling | None
    flag: str | None
    thesis_fit: str | None
    authored_by: str | None
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class MapColumn:
    """A column: a real link (``link``), a roster link that is not on the card today (``old_link``), the rows
    with no link (``no_link``) or the untagged Context/Bench names (``silent``, label Context or Bench).
    """

    key: str
    kind: ColumnKind
    label: str
    descriptor: str | None
    nodes: tuple[MapNode, ...]


@dataclass(frozen=True)
class MapRail:
    side: Side
    label: str
    nodes: tuple[MapNode, ...]


@dataclass(frozen=True)
class MapEdge:
    """One rail node's edge to the chain's bracket: the node, its role and the rail it stands on."""

    node_key: str
    role: str
    side: Side


@dataclass(frozen=True)
class RoleCount:
    role: str
    names: int


@dataclass(frozen=True)
class MapCounts:
    """The work shown on the page's quiet line. ``companies`` = distinct roster companies; ``on_rails`` and
    ``untagged_silent`` = of those, the ones on a rail and the ones in a Context/Bench column (``untagged_silent``
    is the toggle's count). ``roles`` = companies per role, rail order, only roles that are set. ``with_side`` /
    ``without_side`` and ``with_filing`` / ``without_filing`` split the companies (an unresolved or flagged one
    has no side; one with no filing-derived fact in the year has no filing)."""

    links_today: int
    old_links: int
    roster_rows: int
    resolved_rows: int
    unresolved_rows: int
    silent_rows: int
    companies: int
    on_rails: int
    untagged_silent: int
    roles: tuple[RoleCount, ...]
    with_side: int
    without_side: int
    with_filing: int
    without_filing: int


@dataclass(frozen=True)
class ChainMap:
    asof: date
    thesis_id: UUID
    thesis_name: str
    roster_fallback: bool
    columns: tuple[MapColumn, ...]
    rails: tuple[MapRail, ...]  # the left rail, then the right (either may be empty)
    edges: tuple[MapEdge, ...]  # one per rail node, left rail first
    flagged: tuple[FlaggedCompany, ...]
    counts: MapCounts


def _company_key(r: RosterRow) -> str:
    return str(r.security_id) if r.security_id is not None else f"ticker:{r.ticker.strip().upper()}"


def chain_map(inputs: MapInputs, *, asof: date) -> ChainMap:
    """Place every roster company: the columns, the rails, the edges, the glyphs and the counts."""
    rows = list(inputs.roster)
    real = list(dict.fromkeys(s.label for s in inputs.segments if s.label not in SILENT_LABELS))
    real_set = set(real)
    descriptor = {s.label: s.descriptor for s in inputs.segments}
    flags = dict(inputs.flagged)

    by_company: dict[str, list[RosterRow]] = {}
    for r in rows:
        by_company.setdefault(_company_key(r), []).append(r)
    # a company has ONE role (the write refuses two): the first row that carries one
    role_of = {
        ck: next((r.context_role for r in rs if r.context_role), None)
        for ck, rs in by_company.items()
    }
    in_real = {ck: any(r.segment in real_set for r in rs) for ck, rs in by_company.items()}
    links_of = {ck: tuple(dict.fromkeys(r.segment for r in rs)) for ck, rs in by_company.items()}

    def node(key: str, ck: str, r: RosterRow) -> MapNode:
        sid = r.security_id
        facts = inputs.facts.get(sid) if sid is not None else None
        flag = flags.get(sid) if sid is not None else None
        # a flagged company's price bars are never read: no side, whatever the facts carry
        pct = None if facts is None or flag is not None else facts.pct_vs_50d
        has_fit = bool(r.thesis_fit and r.thesis_fit.strip())
        return MapNode(
            key=key,
            company_key=ck,
            ticker=r.ticker,
            name=facts.name if facts else None,
            security_id=sid,
            links=links_of[ck],
            role=role_of[ck],
            business_type=facts.business_type if facts else None,
            side_50d=side_of(pct),
            pct_vs_50d=pct,
            last_filing=last_filing(facts.filings, asof) if facts else None,
            flag=flag,
            thesis_fit=r.thesis_fit if has_fit else None,
            authored_by=r.authored_by if has_fit else None,
            matched_terms=r.surfaced_terms,
        )

    def column_of(segment: str | None) -> tuple[str, ColumnKind, str]:
        if segment is None:
            return "no_link", "no_link", NO_LINK
        if segment in SILENT_LABELS:
            return f"silent:{segment}", "silent", segment
        if segment in real_set:
            return f"link:{segment}", "link", segment
        return f"old:{segment}", "old_link", segment

    # a real link is always a column; the others exist once somebody sits in them
    cols: dict[str, tuple[ColumnKind, str, list[MapNode]]] = {
        f"link:{s}": ("link", s, []) for s in real
    }
    rail_rows: dict[Side, list[tuple[int, MapNode, str]]] = {"left": [], "right": []}
    seen_in_column: set[tuple[str, str]] = set()
    railed: set[str] = set()
    for r in rows:  # roster order, so a column's nodes and a role's rail nodes keep it
        ck = _company_key(r)
        role = role_of[ck]
        if role is not None and not in_real[ck]:
            # a role-tagged company with no row in a real link stands on its rail, once
            if ck not in railed:
                railed.add(ck)
                side = ROLE_SIDE[role]
                n = node(f"rail:{side}|{ck}", ck, r)
                rail_rows[side].append((ROLE_ORDER.index(role), n, role))
            continue
        if role is not None and r.segment in SILENT_LABELS:
            continue  # the Context and Bench columns hold the names with no role
        ckey, kind, label = column_of(r.segment)
        if (ckey, ck) in seen_in_column:
            continue  # a company is one node per column
        seen_in_column.add((ckey, ck))
        cols.setdefault(ckey, (kind, label, []))[2].append(node(f"{ckey}|{ck}", ck, r))

    def rank(kind: ColumnKind, label: str) -> tuple[int, int]:
        """Real links in chain order, then old links (roster order: the sort is stable), No link, Context, Bench."""
        if kind == "link":
            return 0, real.index(label)
        if kind == "old_link":
            return 1, 0
        if kind == "no_link":
            return 2, 0
        return 3, SILENT_LABELS.index(label)

    columns = tuple(
        MapColumn(key, kind, label, descriptor.get(label) if kind == "link" else None, tuple(ns))
        for key, (kind, label, ns) in sorted(cols.items(), key=lambda kv: rank(kv[1][0], kv[1][1]))
    )
    # by role, then roster order (the sort is stable)
    ordered = {side: sorted(rail_rows[side], key=lambda t: t[0]) for side in rail_rows}
    rails = tuple(
        MapRail(side, RAIL_LABEL[side], tuple(n for _, n, _ in ordered[side]))
        for side in ("left", "right")
    )
    edges = tuple(
        MapEdge(n.key, role, side) for side in ("left", "right") for _, n, role in ordered[side]
    )

    # --- the counts: every company is somewhere (a node on the chain, on a rail, or in a silent column) --------
    placed: dict[str, MapNode] = {}
    for n in (*(n for rail in rails for n in rail.nodes), *(n for c in columns for n in c.nodes)):
        placed.setdefault(n.company_key, n)
    with_side = sum(1 for n in placed.values() if n.side_50d is not None)
    with_filing = sum(1 for n in placed.values() if n.last_filing is not None)
    role_names = {r: sum(1 for ck in by_company if role_of[ck] == r) for r in ROLE_ORDER}
    flagged = tuple(
        sorted(
            (
                FlaggedCompany(rs[0].security_id, rs[0].ticker, flags[rs[0].security_id])
                for rs in by_company.values()
                if rs[0].security_id is not None and rs[0].security_id in flags
            ),
            key=lambda f: (f.ticker.casefold(), str(f.security_id)),
        )
    )
    counts = MapCounts(
        links_today=len(real),
        old_links=sum(1 for c in columns if c.kind == "old_link"),
        roster_rows=len(rows),
        resolved_rows=sum(1 for r in rows if r.security_id is not None),
        unresolved_rows=sum(1 for r in rows if r.security_id is None),
        silent_rows=sum(1 for r in rows if r.silent),
        companies=len(by_company),
        on_rails=len(railed),
        untagged_silent=len(
            {n.company_key for c in columns if c.kind == "silent" for n in c.nodes}
        ),
        roles=tuple(RoleCount(r, role_names[r]) for r in ROLE_ORDER if role_names[r] > 0),
        with_side=with_side,
        without_side=len(by_company) - with_side,
        with_filing=with_filing,
        without_filing=len(by_company) - with_filing,
    )
    return ChainMap(
        asof=asof,
        thesis_id=inputs.thesis_id,
        thesis_name=inputs.thesis_name,
        roster_fallback=inputs.roster_fallback,
        columns=columns,
        rails=rails,
        edges=edges,
        flagged=flagged,
        counts=counts,
    )
