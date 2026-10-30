"""Insider Buying (``docs/dashboards/insider-tracker.md``): every open-market insider purchase (Form 4 code P) across
the counted cards' names in a window, one row per Form 4, newest first: who bought, how much, whether others bought
too, whether it became a trigger, and what the tape did since. Follows the as-of dial.

PURE: the router reads the counted cards and their rosters at the as-of (``dashboards/baskets.py``), every counted
name's filings through the shared reader (``dashboards/filings.py``: the point-in-time view plus the Form 4/A SELECT,
both on the request's one ``serve_view(asof)`` pair), the ``calls`` log's nights for the window, the bars and the data
flags of the names with a purchase, and hands them in as ``TrackerInputs``; ``insider_tracker(...)`` builds the page.
Display only: nothing here feeds a call, scores an idea or writes anything (``docs/DASHBOARDS.md`` rule 1), and
NOTHING here aggregates the rows' returns (rule 6: a median move "after insider buys" is an event-outcome statistic,
sliced by market-cap tier on the Track Record page; this page lists, and its only numbers across rows are counts).

The rules, in one place:
- **A row is a Form 4 (an accession) holding a purchase.** The reader's own rows (``FilingRow``, kind ``form4`` or
  ``form4a``): a Form 4 the reader did not fold as routine holds a code-P purchase (``routine`` is false exactly
  then); a 4/A is a purchase when the rows an applied 4/A put on the tape include a code-P line. A filing with sales
  only is no row (counted as routine). The page NEVER re-parses a Form 4 and never re-derives the insider detector's
  screens (the 13D control screen, the issuer-self and internal-transfer rules): it reads back the reader's own
  words (``purchase_of``: the headline names the filer and the role, each detail line names one transaction's trade
  date, code, shares, price and dollars), and the tags the reader put on the row ride along as they are. The round
  trip from the reader's words is pinned by a test, so a change to the reader's wording fails loud here.
- **Dating.** The row's date is the FILING date (``FilingRow.date``: the acceptance in market time, else the day the
  4/A was filed), the public clock; the trade date (the earliest code-P line's) shows beside it with "filed N days
  after the trade" when they differ.
- **The buyer** is the filer the headline names (the first reporting owner; "and N more" co-filers are shown, never
  counted as further buyers: their names are not on the row). A filing whose filer is not named has no buyer: it
  keeps its row and joins no cluster (counted).
- **A cluster** is two or more DISTINCT buyers on one name whose filing dates lie within ``CLUSTER_WINDOW_DAYS`` (30)
  of this row's (``leaders.CLUSTER_MIN_BUYERS`` = 2, the Leaders page's rule), among the purchases read. The read
  reaches ``CLUSTER_WINDOW_DAYS`` further back than the window, so a row at the window's edge counts its neighbors;
  only the window's own rows are listed. A repeat buy by one person is not a cluster. The row names the other buyers.
- **Became a trigger?** A trigger is read off the RECORDED cards: every night of the ``calls`` log in the window, each
  counted card's winning row (the highest ``seq``), and an insider entry trigger (``triggers_fired`` or a member's
  ``triggers``, kind ``insider``) whose ``sources`` carry this filing's accession on this name. The row reads one chip
  per card, with the first night the record shows it and whether it is still on the card in effect at the as-of. A
  filing no recorded trigger carries is "not a trigger", and the page says nothing about why (the trigger path owns that
  judgment).
- **Since** is the name's as-traded return from the first close on or after the FILING date to the last close on or
  before the as-of, with IWM over the same two dates (``trigger_log.since_move``). A name with no bar since reads
  "unpriced"; a flagged price history (``baskets.price_flags``) is never priced, and either keeps its row.
- **Size and cap.** The purchase's shares, volume-weighted price and dollars are the code-P lines' (the reader's
  ``_code_summary`` rule); the dollars are the PURCHASES' (a filing that also holds sales does not net them). The
  company's approximate cap and its tier come from the Peer Table's ``cap_cell`` at the as-of; blank with its reason.
  A tier is a filter dimension only (invariant #4).
- **Order** is neutral and computed: the filing date newest first, then the dollar size, then ticker.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from dashboards.baskets import Membership, companies_in, display_ticker
from dashboards.filings import FilingRow
from dashboards.leaders import CLUSTER_MIN_BUYERS
from dashboards.peer_table import CapCell
from dashboards.trigger_log import (
    CardNights,
    Scope,
    Series,
    SinceMove,
    copies,
    since_move,
    week_start,
)
from domain.enums import Kind
from domain.thesis import SILENT_LABELS

WINDOWS: tuple[int, ...] = (30, 90)  # the window choices, in days
DEFAULT_WINDOW = 30
CLUSTER_WINDOW_DAYS = 30  # two buyers within this many days of each other (inclusive) are a cluster

SinceStatus = Literal["priced", "unpriced", "flagged"]
RoleClass = Literal["officer", "director", "ten_percent_owner", "other"]
ROLE_CLASSES: tuple[RoleClass, ...] = ("officer", "director", "ten_percent_owner", "other")


# ---------------------------------------------------------------------------------------------------------
# reading the reader's words
# ---------------------------------------------------------------------------------------------------------

_P_MARK = (
    ": P purchase"  # the code-P summary opens the headline's summaries (``filings._CODE_ORDER``)
)
_AMENDMENT = re.compile(
    r"^4/A(?: by (?P<by>.+?))? amends (?:the Form 4 filed \S+|an earlier Form 4)$"
)
_MORE = re.compile(r" and (?P<more>\d+) more$")
_SHARES = re.compile(r"^(?P<sh>[\d,]+(?:\.\d+)?) sh(?: @ \$(?P<px>[\d,]+(?:\.\d+)?))?$")
_USD = re.compile(r"^(?P<neg>-)?\$(?P<v>[\d,]+(?:\.\d+)?)$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NO_OWNER = (
    "reporting owner not named"  # ``filings._form4_group``'s words for a row with no insider name
)


@dataclass(frozen=True)
class Purchase:
    """What the page reads back off a Form 4 row's words. ``filer`` = the first reporting owner (``None`` = not
    named), ``role`` = the filing's own role words (``None`` = not stated), ``co_filers`` = the "and N more" count,
    ``trade_date`` = the earliest code-P line's trade date, ``shares`` / ``price`` / ``usd`` = the code-P lines'
    total, shares-weighted mean price and dollars (each ``None`` when no line states it)."""

    filer: str | None
    role: str | None
    co_filers: int
    trade_date: date | None
    shares: float | None
    price: float | None
    usd: float | None


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _p_line(line: str) -> tuple[date | None, float | None, float | None, float | None] | None:
    """``(trade date, shares, price, dollars)`` of one reader detail line that states a code-P transaction
    ("2026-10-12 · P · 12,000 sh @ $4.10 · $49,200 · direct · ..."), else ``None``."""
    parts = line.split(" · ")
    if len(parts) < 2 or parts[1] != "P" or not (_DAY.match(parts[0]) or parts[0] == "undated"):
        return None
    day = date.fromisoformat(parts[0]) if parts[0] != "undated" else None
    shares = price = usd = None
    for part in parts[2:]:
        if shares is None and (m := _SHARES.match(part)):
            shares = _num(m["sh"])
            price = _num(m["px"]) if m["px"] else None
        elif usd is None and (m := _USD.match(part)):
            usd = _num(m["v"]) * (-1 if m["neg"] else 1)
    return day, shares, price, usd


def _split_filer(text: str) -> tuple[str, str | None, int]:
    """``(name, role, co-filers)`` of the reader's "Name (Role) and N more". The role is the LAST balanced
    parenthetical (a name like "A & B Capital (GP) LP" keeps its own, a role like "CFO (Principal)" its nested
    one); a filing that states no role reads whole as the name, and a name ending in a parenthetical with no role
    behind it cannot be told from a role (the text is all there is)."""
    text = text.strip()
    more = 0
    if (m := _MORE.search(text)) is not None:
        more, text = int(m["more"]), text[: m.start()].rstrip()
    if text.endswith(")"):
        depth = 0
        for i in range(len(text) - 1, -1, -1):
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
                if depth == 0:
                    if i > 0 and text[i - 1] == " ":
                        return text[: i - 1].strip(), text[i + 1 : -1].strip() or None, more
                    break
    return text, None, more


def _filer_of(row: FilingRow) -> tuple[str | None, str | None, int]:
    """``(filer, role, co-filers)`` from the headline. A 4/A record row names its owners ("4/A by X, Y amends ...")
    and states no role; every other row is "Name (Role) and N more: P purchase ...". An unparseable headline gives
    nothing (the row keeps its place, the filer blank)."""
    if (m := _AMENDMENT.match(row.headline)) is not None:
        by = m["by"]
        return (by.strip() if by else None), None, 0
    head, found, _ = row.headline.partition(_P_MARK)
    if not found:
        return None, None, 0
    name, role, more = _split_filer(head)
    if not name or name == NO_OWNER:
        return None, role, more
    return name, role, more


def purchase_of(row: FilingRow) -> Purchase | None:
    """The purchase a Form 4 / 4/A row holds, or ``None`` when it holds none. A Form 4 the reader did not fold as
    routine IS a purchase (the reader's own flag: no code-P line, ``routine``); its fields are read back from the
    words and left ``None`` where they do not parse, never dropped. A 4/A is a purchase only when a detail line
    states a code-P transaction."""
    if row.kind not in ("form4", "form4a"):
        return None
    if row.kind == "form4" and row.routine:
        return None
    lines = [p for ln in row.detail if (p := _p_line(ln)) is not None]
    if row.kind == "form4a" and not lines:
        return None
    filer, role, co = _filer_of(row)
    days = [d for d, _, _, _ in lines if d is not None]
    shares = [s for _, s, _, _ in lines if s is not None]
    priced = [(s, p) for _, s, p, _ in lines if s is not None and p is not None]
    weight = sum(s for s, _ in priced if s > 0)
    stated = [u for _, _, _, u in lines if u is not None]
    # the reader's ``_code_summary`` rule: the stated dollars, else shares x price; a mean price needs a weight
    usd = sum(stated) if stated else (sum(s * p for s, p in priced) if priced else None)
    return Purchase(
        filer=filer,
        role=role,
        co_filers=co,
        trade_date=min(days) if days else None,
        shares=sum(shares) if shares else None,
        price=sum(s * p for s, p in priced if s > 0) / weight if weight > 0 else None,
        usd=usd,
    )


_TEN_PCT = re.compile(r"\b10\s*%|\bten percent\b|\b10 percent\b", re.I)
_DIRECTOR = re.compile(r"\bdirector\b|\bchair(?:man|woman|person)?\b", re.I)
_OFFICER = re.compile(
    r"\bofficer\b|\bchief\b|\b(?:ceo|cfo|coo|cto|cio|cmo|cro|clo|cao)\b|\bpresident\b|\bvp\b|\b[esv]vp\b"
    r"|\bgeneral counsel\b|\bsecretary\b|\btreasurer\b|\bcontroller\b|\bcomptroller\b",
    re.I,
)


def role_classes(role: str | None) -> tuple[RoleClass, ...]:
    """The classes of the filing's own role words, for the page's role filter: ``officer`` (an officer's title, or
    the word), ``director``, ``ten_percent_owner``; a role can be several ("Director, Officer"). A role that is
    not stated, or whose words match none, is ``other``. It reads WORDS only (the Form 4's flags are not on the
    row), so a title like "Director of Sales" reads as a director."""
    if not role:
        return ("other",)
    out: list[RoleClass] = []
    if _OFFICER.search(role):
        out.append("officer")
    if _DIRECTOR.search(role):
        out.append("director")
    if _TEN_PCT.search(role):
        out.append("ten_percent_owner")
    return tuple(out) or ("other",)


def buyer_key(filer: str | None) -> str | None:
    """A buyer's identity: the filer's name casefolded, whitespace collapsed (``None`` = not named)."""
    if not filer:
        return None
    return " ".join(filer.split()).casefold() or None


def accession_key(accession: str | None) -> str | None:
    """An accession as compared across the record and the reader: dashes and whitespace removed (both carry
    EDGAR's dashed form; the key does not depend on it)."""
    if not accession:
        return None
    key = re.sub(r"[\s-]", "", str(accession))
    return key or None


# ---------------------------------------------------------------------------------------------------------
# the clusters
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OtherBuyer:
    """Another buyer in a row's cluster: the name and the filing date nearest this row's."""

    name: str
    filed: date


@dataclass(frozen=True)
class Cluster:
    """``buyers`` = distinct buyers on this name within ``CLUSTER_WINDOW_DAYS`` of the row's filing date, the row's
    own included; ``others`` = the other buyers, by name."""

    buyers: int
    others: tuple[OtherBuyer, ...]


def clusters(
    buys: Sequence[tuple[UUID, date, str | None, str | None]],
) -> list[Cluster | None]:
    """A cluster for each purchase, in the order given: ``buys`` = ``(security, filed, buyer key, buyer name)``. A
    row is in a cluster when at least ``CLUSTER_MIN_BUYERS`` distinct buyers on its security filed within
    ``CLUSTER_WINDOW_DAYS`` days of it (inclusive: 30 days apart is a cluster, 31 is not); the same person twice is
    one buyer. A row with no named buyer has none."""
    by_sid: dict[UUID, list[int]] = {}
    for i, (sid, _, key, _) in enumerate(buys):
        if key is not None:
            by_sid.setdefault(sid, []).append(i)
    out: list[Cluster | None] = [None] * len(buys)
    for idxs in by_sid.values():
        for i in idxs:
            _, filed, key, _ = buys[i]
            near: dict[str, tuple[int, str, date]] = {}  # buyer key -> (days apart, name, filed)
            for j in idxs:
                _, f2, k2, n2 = buys[j]
                gap = abs((f2 - filed).days)
                if gap <= CLUSTER_WINDOW_DAYS and k2 is not None and k2 != key:
                    if k2 not in near or gap < near[k2][0]:
                        near[k2] = (gap, n2 or k2, f2)
            if len(near) + 1 >= CLUSTER_MIN_BUYERS:
                others = sorted(near.values(), key=lambda t: (t[0], t[1].casefold()))
                out[i] = Cluster(len(near) + 1, tuple(OtherBuyer(n, f) for _, n, f in others))
    return out


# ---------------------------------------------------------------------------------------------------------
# the triggers
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerHit:
    """A recorded insider trigger that carries a filing's accession, on one card: ``recorded_on`` = the first night
    in the window the record shows it; ``live`` = it is on the card in effect at the as-of (``None`` = that card
    could not be read)."""

    thesis_id: UUID
    thesis_name: str
    recorded_on: date
    live: bool | None


def _insider_keys(card: Any) -> set[tuple[UUID, str]]:
    """The ``(security, accession)`` pairs the card's insider ENTRY triggers carry in their sources."""
    out: set[tuple[UUID, str]] = set()
    for (role, sid, kind, _), refs in copies(card).items():
        if role != "entry_trigger" or kind != Kind.INSIDER:
            continue
        for ref in refs:
            for p in ref.sources:
                if (key := accession_key(p.ref)) is not None:
                    out.add((sid, key))
    return out


def trigger_hits(cards: Sequence[CardNights]) -> dict[tuple[UUID, str], tuple[TriggerHit, ...]]:
    """``(security, accession key) -> its hits``, one per card whose recorded nights in the window show an insider
    entry trigger carrying it. The first night wins; ``live`` reads the newest night in the window (the card in
    effect at the as-of: the log appends only on change, so a quiet card's newest night IS what stood). A night
    that does not validate is skipped (the router counts it)."""
    out: dict[tuple[UUID, str], list[TriggerHit]] = {}
    for cn in cards:
        newest = cn.nights[-1] if cn.nights else None
        standing = _insider_keys(newest.card) if newest is not None and newest.card else None
        first: dict[tuple[UUID, str], date] = {}
        for night in cn.nights:
            if night.card is None:
                continue
            for key in _insider_keys(night.card):
                first.setdefault(key, night.asof)
        for key, day in first.items():
            live = None if standing is None else key in standing
            out.setdefault(key, []).append(TriggerHit(cn.thesis_id, cn.thesis_name, day, live))
    return {
        k: tuple(sorted(v, key=lambda h: (h.thesis_name.casefold(), str(h.thesis_id))))
        for k, v in out.items()
    }


# ---------------------------------------------------------------------------------------------------------
# the inputs and the page
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackerInputs:
    """Everything the router read. ``memberships`` = the counted rows (Context and Bench only when included);
    ``filings`` = each name's reader rows dated from ``window_start - CLUSTER_WINDOW_DAYS`` through the as-of;
    ``nights`` = each counted card's recorded nights in the window; ``closes`` = each unflagged name's
    ``{day: close}`` (names with a purchase only) and ``bench`` IWM's; ``flagged`` = ``security -> reason``;
    ``caps`` = each name's approximate cap at the as-of."""

    asof: date
    window_days: int
    include_silent: bool
    scope: Scope
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    memberships: Sequence[Membership]
    filings: Mapping[UUID, Sequence[FilingRow]]
    nights: Sequence[CardNights]
    closes: Mapping[UUID, Mapping[date, float]]
    bench: Mapping[date, float]
    flagged: Mapping[UUID, str]
    caps: Mapping[UUID, CapCell]


@dataclass(frozen=True)
class CardChip:
    """A card a name sits on: the card, the ticker the Cockpit's ``?name=`` key wants there, and ``silent`` = the
    label ("Context" / "Bench") when the name sits there only silently (it never counts toward the call).
    """

    thesis_id: UUID
    thesis_name: str
    ticker: str
    silent: str | None


@dataclass(frozen=True)
class Cap:
    """The approximate market cap at the as-of and its tier (the Peer Table's rule); ``gap`` is why it is blank."""

    value: float | None
    tier: str | None
    gap: str | None
    note: str | None


@dataclass(frozen=True)
class PurchaseRow:
    """One Form 4 holding a purchase, ready to show. ``filed`` = the filing date (the public clock); ``week`` = the
    Monday of its week; ``filed_days_after`` = the days from the earliest purchase's trade date to the filing when the
    filing is later; ``headline`` / ``detail`` / ``tags`` = the reader's own words and field predicates, unchanged;
    ``cluster`` = see ``clusters``; ``triggers`` = the recorded insider triggers carrying this accession (empty =
    "not a trigger"); ``silent_only`` = every card the name sits on holds it in Context or Bench."""

    key: str
    security_id: UUID
    ticker: str
    accession: str | None
    form: str
    url: str | None
    filed: date
    week: date
    trade_date: date | None
    filed_days_after: int | None
    cards: tuple[CardChip, ...]
    silent_only: bool
    filer: str | None
    role: str | None
    role_classes: tuple[RoleClass, ...]
    co_filers: int
    shares: float | None
    price: float | None
    usd: float | None
    tags: tuple[str, ...]
    headline: str
    detail: tuple[str, ...]
    cluster: Cluster | None
    triggers: tuple[TriggerHit, ...]
    since: SinceMove
    cap: Cap


@dataclass(frozen=True)
class NoteName:
    """A name set aside from the tape, listed on the quiet line: ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str | None


@dataclass(frozen=True)
class Counts:
    """The work the page shows on its quiet line. Counts of what was read and listed, never a statistic over the
    rows' returns (rule 6). ``filings_read`` = the Form 4s and 4/As filed in the window = ``purchases`` +
    ``routine_folded`` + ``amendments_no_purchase``."""

    cards_counted: int
    live_cards: int
    not_yet_created: int
    roster_fallback: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    names_read: int
    filings_read: int
    purchases: int
    routine_folded: int
    amendments_no_purchase: int
    names_with_purchases: int
    filer_unresolved: int
    cluster_rows: int
    cluster_names: int
    trigger_rows: int
    nights_read: int
    nights_unreadable: int
    names_unpriced: int
    names_flagged: int
    cap_blank: int


@dataclass(frozen=True)
class InsiderTracker:
    asof: date
    window_days: int
    window_start: date
    include_silent: bool
    rows: tuple[PurchaseRow, ...]
    counts: Counts
    flagged: tuple[NoteName, ...]


def window_start(asof: date, window_days: int) -> date:
    """The first filing date the window lists: ``window_days`` calendar days back from the as-of, inclusive."""
    return asof - timedelta(days=window_days)


def read_start(asof: date, window_days: int) -> date:
    """The first filing date the router reads: the window's start less the cluster window, so a row at the
    window's edge counts the buyers just before it."""
    return window_start(asof, window_days) - timedelta(days=CLUSTER_WINDOW_DAYS)


def purchases_in(
    filings: Mapping[UUID, Sequence[FilingRow]],
) -> list[tuple[UUID, FilingRow, Purchase]]:
    """Every purchase the reader's rows hold, as ``(security, row, purchase)``, in a stable order (security, then
    filing date, then accession)."""
    out = [
        (sid, row, p)
        for sid, rows in filings.items()
        for row in rows
        if (p := purchase_of(row)) is not None
    ]
    out.sort(key=lambda t: (str(t[0]), t[1].date, t[1].accession or ""))
    return out


def purchase_security_ids(
    filings: Mapping[UUID, Sequence[FilingRow]], asof: date, window_days: int
) -> list[UUID]:
    """The names with a purchase filed in the window (the ones the router prices), in a stable order."""
    start = window_start(asof, window_days)
    return sorted(
        {sid for sid, row, _ in purchases_in(filings) if start <= row.date <= asof}, key=str
    )


def row_key(sid: UUID, accession: str | None, filed: date) -> str:
    """A short stable id for one purchase row (unique within a response)."""
    return hashlib.sha1(f"{sid}|{accession or filed}".encode()).hexdigest()[:16]


def cap_of(c: CapCell) -> Cap:
    return Cap(c.value, c.tier.value if c.tier is not None else None, c.gap, c.note)


def _chips(rows: Sequence[Membership]) -> tuple[dict[UUID, tuple[CardChip, ...]], dict[UUID, str]]:
    """Each counted name's card chips and the ticker it shows under (``baskets.display_ticker``)."""
    chips: dict[UUID, tuple[CardChip, ...]] = {}
    tickers: dict[UUID, str] = {}
    for sid, cards in companies_in(rows):
        tickers[sid] = display_ticker(cards)
        chips[sid] = tuple(
            CardChip(
                c.thesis_id,
                c.thesis_name,
                c.ticker,
                c.links[0] if c.links and all(link in SILENT_LABELS for link in c.links) else None,
            )
            for c in cards
        )
    return chips, tickers


def _order(r: PurchaseRow) -> tuple:
    return (
        -r.filed.toordinal(),
        -(r.usd if r.usd is not None else float("-inf")),
        r.ticker.casefold(),
        r.accession or "",
    )


def insider_tracker(inp: TrackerInputs) -> InsiderTracker:
    """The page's payload: one row per Form 4 holding a purchase filed in the window, with its cluster, its
    recorded triggers, the tape since and the cap, and the counts of what was read."""
    start = window_start(inp.asof, inp.window_days)
    chips, tickers = _chips(inp.memberships)
    bench = Series.of(inp.bench)
    series = {sid: Series.of(c) for sid, c in inp.closes.items()}
    hits = trigger_hits(inp.nights)

    found = purchases_in(
        inp.filings
    )  # the lead-in included: the clusters see the buyers just before the window
    cl = clusters(
        [(sid, row.date, buyer_key(p.filer), p.filer) for sid, row, p in found],
    )

    rows: list[PurchaseRow] = []
    flagged_names: dict[UUID, NoteName] = {}
    unpriced: set[UUID] = set()
    for (sid, fr, p), cluster in zip(found, cl, strict=True):
        if not start <= fr.date <= inp.asof:
            continue
        ticker = tickers.get(sid) or fr.ticker
        if sid in inp.flagged:
            move = SinceMove("flagged", reason=inp.flagged[sid])
            flagged_names.setdefault(sid, NoteName(sid, ticker, inp.flagged[sid]))
        else:
            move = since_move(series.get(sid), bench, fr.date, inp.asof)
            if move.status == "unpriced":
                unpriced.add(sid)
        card_chips = chips.get(sid, ())
        rows.append(
            PurchaseRow(
                key=row_key(sid, fr.accession, fr.date),
                security_id=sid,
                ticker=ticker,
                accession=fr.accession,
                form=fr.form,
                url=fr.url,
                filed=fr.date,
                week=week_start(fr.date),
                trade_date=p.trade_date,
                filed_days_after=(
                    (fr.date - p.trade_date).days
                    if p.trade_date is not None and p.trade_date < fr.date
                    else None
                ),
                cards=card_chips,
                silent_only=bool(card_chips) and all(c.silent is not None for c in card_chips),
                filer=p.filer,
                role=p.role,
                role_classes=role_classes(p.role),
                co_filers=p.co_filers,
                shares=p.shares,
                price=p.price,
                usd=p.usd,
                tags=fr.tags,
                headline=fr.headline,
                detail=fr.detail,
                cluster=cluster,
                triggers=hits.get((sid, accession_key(fr.accession) or ""), ()),
                since=move,
                cap=cap_of(inp.caps[sid]) if sid in inp.caps else Cap(None, None, "no_price", None),
            )
        )
    rows.sort(key=_order)

    window_filings = [
        fr
        for rs in inp.filings.values()
        for fr in rs
        if fr.kind in ("form4", "form4a") and start <= fr.date <= inp.asof
    ]
    listed = {(r.security_id, r.accession) for r in rows}
    with_purchase = sum(1 for fr in window_filings if (fr.security_id, fr.accession) in listed)
    routine = sum(1 for fr in window_filings if fr.kind == "form4" and fr.routine)
    names = {r.security_id for r in rows}
    cluster_rows = [r for r in rows if r.cluster is not None]
    window_nights = [n for cn in inp.nights for n in cn.nights]

    def by_ticker(n: NoteName) -> tuple[str, str]:
        return (n.ticker.casefold(), str(n.security_id))

    return InsiderTracker(
        asof=inp.asof,
        window_days=inp.window_days,
        window_start=start,
        include_silent=inp.include_silent,
        rows=tuple(rows),
        counts=Counts(
            cards_counted=len(inp.nights),
            live_cards=inp.scope.live_cards,
            not_yet_created=inp.scope.not_yet_created,
            roster_fallback=inp.scope.roster_fallback,
            basket_rows=inp.basket_rows,
            silent_rows_left_out=inp.silent_rows_left_out,
            unresolved_rows=inp.unresolved_rows,
            names_read=len(inp.filings),
            filings_read=len(window_filings),
            purchases=len(rows),
            routine_folded=routine,
            amendments_no_purchase=len(window_filings) - with_purchase - routine,
            names_with_purchases=len(names),
            filer_unresolved=sum(1 for r in rows if r.filer is None),
            cluster_rows=len(cluster_rows),
            cluster_names=len({r.security_id for r in cluster_rows}),
            trigger_rows=sum(1 for r in rows if r.triggers),
            nights_read=len(window_nights),
            nights_unreadable=sum(1 for n in window_nights if n.card is None),
            names_unpriced=len(unpriced),
            names_flagged=len(flagged_names),
            cap_blank=sum(1 for r in rows if r.cap.value is None),
        ),
        flagged=tuple(sorted(flagged_names.values(), key=by_ticker)),
    )
