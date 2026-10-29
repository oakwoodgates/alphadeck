"""Evidence Ledger (``docs/dashboards/evidence-ledger.md``): ONE card's provenance as one dated list. Everything the
card's recorded call and its stored facts rest on, each row marked by WHO put it there. Follows the as-of dial.

PURE: the router reads the card's roster as of the as-of, the recorded call in effect from the ``calls`` log, the
Filing Stream's shared reader (``dashboards/filings.py``) for the ratified and catalyst facts, and the roster
snapshots that date a context role; ``ledger_base(...)`` builds the heavy part ONCE per (card, clock, as-of) (the
shared dashboard cache, ``dashboards/cache.py``) and ``evidence_ledger(...)`` adds the card's own entries, which
load live, and sorts and tallies per request. Display only: nothing here feeds a call, judges the evidence or
writes anything (rule 1).

The rules, in one place. This is our own simple listing, stated here and on the page.
- **The rows.** Four sources. (1) The recorded card's fired triggers and risk signals, one row per ``TriggerRef``,
  each ``Provenance`` a source of it. (2) The ratified and stored facts the shared filing reader holds (kinds
  ``ratified`` and ``catalyst``). (3) The card's own entries: catalysts, kill criteria, evidence links.
  (4) Each context role set on the roster, dated by the roster snapshot that first carried it.
- **The authority class** is a label of WHO put the row there, never a judgment of it. ``system`` = a computation
  on public data (a trigger, a risk signal); ``operator`` = a person ratified or set it (a ratified fact, a
  catalyst fact a person ratified, a context role); ``automatic`` = an ingest leg stored it, nobody ratified it;
  ``card`` = an entry the operator maintains on the card. One rule per source, in one place: ``fact_authority``.
- **The recorded card** is the row of the ``calls`` log in effect at the as-of (the greatest ``(asof, seq)`` with
  ``asof <=`` the as-of; the What Changed dashboard's rule). A call recorded for a later night is never read. A
  reconstructed night is marked on its rows, "reported, never scored".
- **Dates.** A trigger counts under its event date, a fact under the public date the shared reader gives it, a
  dated catalyst under its date, a role under the day of the first roster snapshot that carried it. A row with no
  date (a kill criterion, an undated catalyst, an evidence link, a role no snapshot dates) sits in the standing
  section, above the dated list; a date is never guessed.
- **Card entries load live.** Catalysts, kill criteria and evidence links are not snapshotted: they are the card's
  today, whatever the as-of. The triggers, facts and roster are as of the as-of.
- **Nothing is hidden from the counts.** The tallies cover every row; the page's filters are client side. A
  roster row with no resolved security can carry no fact: counted and listed (invariant #9).
- **Not evidence:** display signals, thesis-fit prose (a drafted description) and located passages (not stored).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal, NamedTuple
from urllib.parse import urlparse
from uuid import UUID

from dashboards.baskets import Membership
from dashboards.filings import FilingRow, accession_of, et_date, source_url
from domain.call import CallCard, TriggerRef
from domain.thesis import SILENT_LABELS, Catalyst, Evidence, KillCriterion, Thesis

# the facts the shared reader is asked for: the last year (its longest window)
READ_DAYS = 365

Authority = Literal["operator", "system", "automatic", "card"]
# the order of the classes in a tally: a person's rows first
AUTHORITY_ORDER: tuple[Authority, ...] = ("operator", "system", "automatic", "card")

LedgerKind = Literal[
    "trigger",
    "risk_signal",
    "ratified",
    "catalyst_fact",
    "card_catalyst",
    "kill",
    "evidence",
    "role",
]
# the order of kinds within a day: the computation first, then the stored facts, then the card's entries
KIND_ORDER: tuple[LedgerKind, ...] = (
    "trigger",
    "risk_signal",
    "ratified",
    "catalyst_fact",
    "card_catalyst",
    "kill",
    "evidence",
    "role",
)
# the order of the standing section (the rows with no date): the card's own entries, then the roles
STANDING_ORDER: tuple[LedgerKind, ...] = (
    "kill",
    "card_catalyst",
    "evidence",
    "role",
    "trigger",
    "risk_signal",
    "ratified",
    "catalyst_fact",
)
_RANK = {k: i for i, k in enumerate(KIND_ORDER)}
_STANDING_RANK = {k: i for i, k in enumerate(STANDING_ORDER)}

# the roles' words in a row's line (the page's tag and hover come from the frontend's own copy, keyed by role)
ROLE_WORDS: dict[str, str] = {
    "payer": "payer",
    "counter_case": "counter-case",
    "direct_read": "direct read",
    "participant": "participant",
    "benchmark": "benchmark",
    "financier": "financier",
}

Trace = Literal["accession", "price", "other", "none"]


# --- the rows -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One place a row points at: ``source`` = the kind of source as stored ("form4", "price", "xbrl", a form),
    ``ref`` = its reference (an accession, a price reference, a free-text reference), ``url`` = a link to it
    (``None`` = none known), ``detail`` = the computation's inputs, one ``key: value`` line each, as stored.
    """

    source: str
    ref: str | None
    url: str | None
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerRow:
    """One piece of evidence. ``date`` = the day it counts under (``None`` = standing). ``form`` = what kind of
    thing it is in the row's own words (a signal's kind, a filing's form, a catalyst's kind). ``silent`` =
    "Context" / "Bench" when the company sits there (it never counts toward the call). ``by`` / ``on`` = who
    ratified or set it and the day, on an operator row. ``edgar`` = a source of the row links to EDGAR.
    ``reconstructed`` = the row comes from a night ``pipeline.backfill`` wrote. ``key`` is unique within one
    response."""

    authority: Authority
    kind: LedgerKind
    label: str
    key: str = ""
    date: date | None = None
    security_id: UUID | None = None
    ticker: str | None = None
    form: str | None = None
    silent: str | None = None
    role: str | None = None
    by: str | None = None
    on: date | None = None
    sources: tuple[Source, ...] = ()
    detail: tuple[str, ...] = ()
    edgar: bool = False
    reconstructed: bool = False


def is_edgar(url: str | None) -> bool:
    """A link on sec.gov (a filing index the system built, or a stored SEC URL)."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return host == "sec.gov" or host.endswith(".sec.gov")


def _scalar_words(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def _value_words(v: Any) -> str:
    if isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) for x in v):
        return ", ".join(_scalar_words(x) for x in v)
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, sort_keys=True, default=str)
    return _scalar_words(v)


def detail_lines(detail: Mapping[str, Any]) -> tuple[str, ...]:
    """A computation's inputs as ``key: value`` lines, keys as stored, sorted by key (the stored JSON has no
    order of its own). A value the detector did not state (``None``) is left out: a missing field asserts nothing.
    """
    return tuple(f"{k}: {_value_words(v)}" for k, v in sorted(detail.items()) if v is not None)


def _is_price(s: Source) -> bool:
    return s.source.casefold() == "price" or (s.ref or "").startswith("price:")


def trace_of(row: LedgerRow) -> Trace:
    """What a signal row traces to: an EDGAR accession (any source's reference is accession-shaped), else a
    price reference (the bars), else another source, else nothing stated. A count, not a judgment.
    """
    if not row.sources:
        return "none"
    if any(accession_of(s.ref) for s in row.sources):
        return "accession"
    if any(_is_price(s) for s in row.sources):
        return "price"
    return "other"


# --- the authority class ------------------------------------------------------------------------------------


def fact_authority(row: FilingRow) -> Authority:
    """WHO put a stored fact there: ``operator`` when a person ratified it (the reader's ``ratifier``), else
    ``automatic`` (an ingest leg or the machine's auto-confirm stored it: nobody ratified it)."""
    return "operator" if row.ratifier else "automatic"


# --- the recorded card's triggers ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedNight:
    """The ``calls`` log row in effect at the as-of: the night it was recorded for, whether ``pipeline.backfill``
    reconstructed it, and its card."""

    asof: date
    reconstructed: bool
    card: CallCard


def _silent_label(m: Membership) -> str | None:
    return m.segment if m.silent and m.segment in SILENT_LABELS else None


def _trigger_row(
    ref: TriggerRef,
    kind: Literal["trigger", "risk_signal"],
    night: RecordedNight,
    *,
    tickers: Mapping[UUID, str],
    ciks: Mapping[UUID, str | None],
    silent: Mapping[UUID, str | None],
) -> LedgerRow:
    cik = ciks.get(ref.security_id)
    sources = tuple(
        Source(p.source, p.ref, source_url(p.ref, cik), detail_lines(p.detail)) for p in ref.sources
    )
    what = (
        "Risk signal (feeds the counter-case, never turns a key)" if kind == "risk_signal" else None
    )
    detail = [
        *([what] if what else []),
        f"Signal kind: {ref.kind.value}",
        *([f"Grade: {ref.grade.value}"] if ref.grade else []),
        f"Event date: {ref.event_date}" if ref.event_date else "No event date stated",
        f"Recorded in the call for {night.asof}",
        *(["From a reconstructed night: reported, never scored"] if night.reconstructed else []),
        *([] if sources else ["No source stated"]),
    ]
    return LedgerRow(
        authority="system",
        kind=kind,
        label=ref.label,
        date=ref.event_date,
        security_id=ref.security_id,
        ticker=tickers.get(ref.security_id),
        form=ref.kind.value.replace("_", " "),
        silent=silent.get(ref.security_id),
        sources=sources,
        detail=tuple(detail),
        edgar=any(is_edgar(s.url) for s in sources),
        reconstructed=night.reconstructed,
    )


def trigger_rows(
    night: RecordedNight,
    *,
    tickers: Mapping[UUID, str],
    ciks: Mapping[UUID, str | None],
    silent: Mapping[UUID, str | None],
) -> tuple[LedgerRow, ...]:
    """One row per fired trigger, then one per fired risk signal, of the recorded card."""
    card = night.card
    return (
        *(
            _trigger_row(t, "trigger", night, tickers=tickers, ciks=ciks, silent=silent)
            for t in card.triggers_fired
        ),
        *(
            _trigger_row(t, "risk_signal", night, tickers=tickers, ciks=ciks, silent=silent)
            for t in card.risk_signals
        ),
    )


# --- the ratified and stored facts --------------------------------------------------------------------------


def fact_rows(
    rows_by_sid: Mapping[UUID, Sequence[FilingRow]], silent: Mapping[UUID, str | None]
) -> tuple[LedgerRow, ...]:
    """The shared reader's ``ratified`` rows (a ratified or stored cash, share or revenue-mix fact) and
    ``catalyst`` rows (a stored catalyst fact), one ledger row each; every other kind is the Filing Stream's.
    """
    out = []
    for sid, rows in rows_by_sid.items():
        for r in rows:
            if r.kind not in ("ratified", "catalyst"):
                continue
            source = (
                (Source(r.form, r.accession, r.url),) if r.url is not None or r.accession else ()
            )
            out.append(
                LedgerRow(
                    authority=fact_authority(r),
                    kind="ratified" if r.kind == "ratified" else "catalyst_fact",
                    label=r.headline,
                    date=r.date,
                    security_id=sid,
                    ticker=r.ticker,
                    form=r.form,
                    silent=silent.get(sid),
                    by=r.ratifier,
                    on=r.ratified_on,
                    sources=source,
                    detail=r.detail,
                    edgar=any(is_edgar(s.url) for s in source),
                )
            )
    return tuple(out)


# --- the roster, and the roles ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterCompany:
    """One company on the card's roster, each once. ``key`` = its ``security_id`` (``ticker:<T>`` for a row with
    no resolved security); ``role`` = its context role (identical on every row of a company); ``silent`` =
    "Context" / "Bench" when any of its rows sits there."""

    key: str
    ticker: str
    security_id: UUID | None
    role: str | None
    silent: str | None


def _key(m: Membership) -> str:
    return str(m.security_id) if m.security_id is not None else f"ticker:{m.ticker.strip().upper()}"


def roster_companies(memberships: Iterable[Membership]) -> tuple[RosterCompany, ...]:
    """Each company once, in roster order: its first row's ticker, the first role set on any of its rows, silent
    when any of its rows is (a company is silent or voting, never both)."""
    by_key: dict[str, list[Membership]] = {}
    for m in memberships:
        by_key.setdefault(_key(m), []).append(m)
    return tuple(
        RosterCompany(
            key=key,
            ticker=rows[0].ticker,
            security_id=rows[0].security_id,
            role=next((m.context_role for m in rows if m.context_role), None),
            silent=next((s for s in map(_silent_label, rows) if s), None),
        )
        for key, rows in by_key.items()
    )


class RosterSnapshot(NamedTuple):
    """One ``basket_snapshot`` row, narrowed to what dates a role: when it was taken and the role-tagged members
    it held (``security_id`` + ``context_role``; an untagged member is not listed)."""

    taken_at: datetime
    members: Sequence[Mapping[str, Any]]


def role_starts(snapshots: Iterable[RosterSnapshot]) -> dict[str, tuple[str, date]]:
    """``{security_id: (role, day)}`` for each company whose role is carried by an unbroken run of snapshots
    ending at the LAST one given: the market-time day of the first snapshot of that run. ``snapshots`` come
    oldest first and were taken by the as-of (the router's SELECT); a role dropped or changed in between starts
    its run again, so the date is when the CURRENT role was first carried, never an earlier different one.
    """
    runs: dict[str, tuple[str, datetime]] = {}
    for snap in snapshots:
        seen: dict[str, str] = {}
        for m in snap.members:
            sid, role = m.get("security_id"), m.get("context_role")
            if sid and role and sid not in seen:
                seen[sid] = role
        runs = {sid: run for sid, run in runs.items() if seen.get(sid) == run[0]}
        for sid, role in seen.items():
            runs.setdefault(sid, (role, snap.taken_at))
    return {sid: (role, et_date(taken)) for sid, (role, taken) in runs.items()}


def role_rows(
    companies: Iterable[RosterCompany], starts: Mapping[str, tuple[str, date]]
) -> tuple[LedgerRow, ...]:
    """One row per role-tagged company: dated by the first roster snapshot that carried its current role, else
    undated (standing) and saying so."""
    out = []
    for c in companies:
        if not c.role:
            continue
        start = starts.get(str(c.security_id)) if c.security_id is not None else None
        day = start[1] if start is not None and start[0] == c.role else None
        out.append(
            LedgerRow(
                authority="operator",
                kind="role",
                label=f"{c.ticker} is tagged {ROLE_WORDS.get(c.role, c.role)} on this card",
                date=day,
                security_id=c.security_id,
                ticker=c.ticker,
                form="context role",
                silent=c.silent,
                role=c.role,
                by="operator",
                on=day,
                detail=(
                    (f"First carried by the roster snapshot of {day}",)
                    if day
                    else ("No roster snapshot dates this role",)
                ),
            )
        )
    return tuple(out)


# --- the card's own entries ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardEntries:
    """The card's catalysts, kill criteria and evidence links, as loaded NOW (they are not snapshotted)."""

    catalysts: tuple[Catalyst, ...] = ()
    kills: tuple[KillCriterion, ...] = ()
    evidence: tuple[Evidence, ...] = ()


def card_entries(thesis: Thesis) -> CardEntries:
    return CardEntries(tuple(thesis.catalysts), tuple(thesis.kill_criteria), tuple(thesis.evidence))


def entry_rows(entries: CardEntries, asof: date) -> tuple[LedgerRow, ...]:
    """One row per card entry (class ``card``). A catalyst counts under its date when it has one (a date after
    the as-of says so); an undated catalyst, a kill criterion and an evidence link are standing."""
    out: list[LedgerRow] = []
    for c in entries.catalysts:
        if c.when_date is not None:
            when = (
                f"Dated {c.when_date}, after the as-of"
                if c.when_date > asof
                else f"Dated {c.when_date}"
            )
        else:
            when = "No date stated"
        detail = [when, *([f"When: {c.when_label}"] if c.when_label else [])]
        out.append(
            LedgerRow(
                authority="card",
                kind="card_catalyst",
                label=c.label,
                date=c.when_date,
                form=c.kind,
                detail=tuple(detail),
            )
        )
    for k in entries.kills:
        out.append(LedgerRow(authority="card", kind="kill", label=k.text, form="kill criterion"))
    for e in entries.evidence:
        url = source_url(
            e.ref, None
        )  # a stored URL as is; an accession alone carries no company: text only
        out.append(
            LedgerRow(
                authority="card",
                kind="evidence",
                label=e.label,
                form=e.kind,
                sources=(Source(e.kind, e.ref, url),),
                detail=(f"Dated: {e.date_label}",) if e.date_label else (),
                edgar=is_edgar(url),
            )
        )
    return tuple(out)


# --- the cached read, and one view --------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerBase:
    """Everything the heavy read produces for one card: cached per (card, clock, as-of). The card's own entries
    are NOT here (they load live, per request)."""

    thesis_id: UUID
    thesis_name: str
    roster_fallback: bool  # no roster snapshot by the as-of: today's roster stands in
    night: (
        RecordedNight | None
    )  # the recorded call in effect (``None`` = none recorded by the as-of)
    night_unreadable: bool  # a recorded row was found but its card did not read
    basket_rows: int  # every roster row
    silent_rows: int  # of those, in Context or Bench
    unresolved: tuple[
        str, ...
    ]  # tickers of the rows with no resolved security: counted, never a fact
    companies: tuple[RosterCompany, ...]
    rows: tuple[LedgerRow, ...]  # triggers, facts and roles


def ledger_base(
    *,
    thesis_id: UUID,
    thesis_name: str,
    memberships: Sequence[Membership],
    night: RecordedNight | None,
    night_unreadable: bool = False,
    tickers: Mapping[UUID, str],
    ciks: Mapping[UUID, str | None],
    rows_by_sid: Mapping[UUID, Sequence[FilingRow]],
    snapshots: Iterable[RosterSnapshot],
    roster_fallback: bool = False,
) -> LedgerBase:
    """One card's cached read. ``memberships`` = its roster rows at the as-of (``baskets.memberships_of``);
    ``night`` = the recorded call in effect; ``tickers`` / ``ciks`` = the display ticker and the issuer CIK per
    security (the roster's and the triggers'); ``rows_by_sid`` = ``filings.read_filing_rows`` over the roster's
    resolved companies; ``snapshots`` = the roster snapshots taken by the as-of, oldest first."""
    rows_in = list(memberships)
    companies = roster_companies(rows_in)
    silent = {c.security_id: c.silent for c in companies if c.security_id is not None}
    rows = [
        *(trigger_rows(night, tickers=tickers, ciks=ciks, silent=silent) if night else ()),
        *fact_rows(rows_by_sid, silent),
        *role_rows(companies, role_starts(snapshots)),
    ]
    return LedgerBase(
        thesis_id=thesis_id,
        thesis_name=thesis_name,
        roster_fallback=roster_fallback,
        night=night,
        night_unreadable=night_unreadable,
        basket_rows=len(rows_in),
        silent_rows=sum(1 for m in rows_in if m.silent),
        unresolved=tuple(dict.fromkeys(m.ticker for m in rows_in if m.security_id is None)),
        companies=companies,
        rows=tuple(rows),
    )


def sort_key(r: LedgerRow) -> tuple:
    """A DATED row: newest day first, then by kind, ticker and label (a stable sort keeps the rest in build
    order)."""
    day = -r.date.toordinal() if r.date is not None else 0
    return (day, _RANK[r.kind], (r.ticker or "").casefold(), r.label, r.form or "")


def standing_key(r: LedgerRow) -> int:
    """A STANDING row: by its kind alone. A stable sort keeps a kind's rows in the order they were built: the
    card's entries as the operator authored them, the roles as the roster lists them."""
    return _STANDING_RANK[r.kind]


@dataclass(frozen=True)
class Day:
    """One day of the ledger: its rows in order."""

    date: date
    rows: tuple[LedgerRow, ...]


@dataclass(frozen=True)
class CountBy:
    key: str
    rows: int


@dataclass(frozen=True)
class Tallies:
    """What the ledger holds, every row (the page's filters hide nothing from these). ``trace_*`` = how the
    fired signals (triggers and risk signals) trace: an EDGAR accession, a price reference only, another source,
    or no source stated. ``facts_ratified`` / ``facts_stored`` = the fact rows a person ratified / nobody did.
    """

    rows: int
    by_authority: tuple[CountBy, ...]
    by_kind: tuple[CountBy, ...]
    signals: int
    trace_accession: int
    trace_price: int
    trace_other: int
    trace_none: int
    facts_ratified: int
    facts_stored: int
    catalysts_dated: int
    catalysts_undated: int
    roles_dated: int
    roles_undated: int


def tallies_of(rows: Sequence[LedgerRow]) -> Tallies:
    traces = [trace_of(r) for r in rows if r.kind in ("trigger", "risk_signal")]
    facts = [r for r in rows if r.kind in ("ratified", "catalyst_fact")]

    def n(kind: str, dated: bool) -> int:
        return sum(1 for r in rows if r.kind == kind and (r.date is not None) == dated)

    return Tallies(
        rows=len(rows),
        by_authority=tuple(
            CountBy(a, sum(1 for r in rows if r.authority == a)) for a in AUTHORITY_ORDER
        ),
        by_kind=tuple(CountBy(k, sum(1 for r in rows if r.kind == k)) for k in KIND_ORDER),
        signals=len(traces),
        trace_accession=traces.count("accession"),
        trace_price=traces.count("price"),
        trace_other=traces.count("other"),
        trace_none=traces.count("none"),
        facts_ratified=sum(1 for r in facts if r.authority == "operator"),
        facts_stored=sum(1 for r in facts if r.authority == "automatic"),
        catalysts_dated=n("card_catalyst", True),
        catalysts_undated=n("card_catalyst", False),
        roles_dated=n("role", True),
        roles_undated=n("role", False),
    )


@dataclass(frozen=True)
class NameView:
    """A roster company with how many ledger rows it holds (a name with none is still listed)."""

    company: RosterCompany
    rows: int


@dataclass(frozen=True)
class EvidenceLedger:
    asof: date
    base: LedgerBase
    standing: tuple[LedgerRow, ...]
    groups: tuple[Day, ...]
    tallies: Tallies
    names: tuple[NameView, ...]


def evidence_ledger(base: LedgerBase, entries: CardEntries, *, asof: date) -> EvidenceLedger:
    """The page: the cached rows plus the card's own entries, the standing rows apart from the dated ones, the
    dated ones under day headers (newest first), every row keyed, and the tallies over all of them.
    """
    rows = [*base.rows, *entry_rows(entries, asof)]
    dated = sorted((r for r in rows if r.date is not None), key=sort_key)
    standing = sorted((r for r in rows if r.date is None), key=standing_key)
    keyed_dated = [replace(r, key=f"d{i}") for i, r in enumerate(dated)]
    keyed_standing = [replace(r, key=f"s{i}") for i, r in enumerate(standing)]

    groups: list[tuple[date, list[LedgerRow]]] = []
    for r in keyed_dated:
        assert r.date is not None
        if groups and groups[-1][0] == r.date:
            groups[-1][1].append(r)
        else:
            groups.append((r.date, [r]))

    held: dict[UUID, int] = {}
    for r in rows:
        if r.security_id is not None:
            held[r.security_id] = held.get(r.security_id, 0) + 1
    return EvidenceLedger(
        asof=asof,
        base=base,
        standing=tuple(keyed_standing),
        groups=tuple(Day(d, tuple(rs)) for d, rs in groups),
        tallies=tallies_of(rows),
        names=tuple(
            NameView(c, held.get(c.security_id, 0) if c.security_id is not None else 0)
            for c in base.companies
        ),
    )
