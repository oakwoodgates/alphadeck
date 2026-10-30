"""Theme Leaderboard (``docs/dashboards/theme-leaderboard.md``): every counted card ranked by a STATED composite of
its tape, its breadth, its keys, its adoption curve and its insiders, with the five parts beside the score. Follows
the as-of dial.

PURE: the router reads the cards counted at the as-of (``dashboards/baskets.py``), the ``calls`` log (the recorded
card in effect per card), each counting name's display-member readings through the point-in-time view
(``baskets.per_company``), one theme-breadth read per card and the card's term-mention months, and hands them in as
``CardRead`` + ``CompanyTape``; ``assemble(...)`` builds the payload. Display only: nothing here feeds a call, judges
a thesis or writes anything (``docs/DASHBOARDS.md`` rule 1); the ranking is a formula the page prints (rule 3), and
what it ranks is what the data shows about each card's names and record, never whether the idea is good (#4).

The rules, in one place:
- **Who is a row.** Every card counted at the as-of (``baskets.cards_as_of``), whatever its state; a card with no
  recorded call keeps its row with the record part blank and is counted. Only the roster's COUNTING names feed the
  tape parts (Context and Bench never: ``baskets.counted_rows`` with ``include_silent=False``); a name on the
  flagged-price list (``baskets.price_flags``, rule 5) is left out of every tape reading (tape, 7D, breadth, RS, cap)
  and counted per card. Its Form 4 count is not a price reading and stays, as the Hot Board and the Peer Table keep it.
- **The five parts** (higher is better for every one; each is the card's percentile among the cards shown):
  1. ``tape``: the median 30-day return of the card's priced counting names minus IWM's 30-day return (percentage
     points). The Rotation Map's median rule: fewer than ``MIN_PRICED`` priced names = no median.
  2. ``breadth``: the share of the counting names above their 50-day SMA (``theme_breadth.breadth_for``: a name with
     fewer than its ``MIN_BARS`` is shown-not-counted). The share above the 200-day SMA rides along (``sma_position``).
  3. ``keys``: the keys the RECORDED card in effect has turned, 0, 1 or 2 (Managing counts as 2); a card with no
     recorded call has none.
  4. ``adoption``: the slope of the card's EDGAR term-mention documents (``adoption_slope``): the last three CLOSED
     calendar months against the three before, summed over the card's current SIGNAL terms. The current partial
     month never counts; a card whose six closed months are not all stored is "not counted yet" (the part is
     missing); an earlier three months with no documents has no base to state a percent against (also missing).
  5. ``insiders``: the distinct open-market buyers in 30 days summed across the counting names
     (``insider_flow_90d``.``distinct_buyers_30d``); missing only when no name has a Form 4 reading.
- **Score** (``score``): the mean of the five percentiles x 100. The percentile is the Hot Board's rule (the mid-rank
  over ``n - 1``: the best of n cards is 1, the worst 0, a tie shares the average of its ranks, a lone card is 0.5),
  taken among the cards SHOWN that have the part. A card missing any part has no score, sorts after every scored card
  and is counted. Equal weights (PROPOSED). The page recomputes it for the cards it shows
  (``frontend/src/dashboards/theme-leaderboard/score.ts`` mirrors this and is tested against the same fixtures); the
  payload here is in the page's default order (the score among every card counted).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Literal
from uuid import UUID

from dashboards.baskets import FlaggedCompany, Membership
from dashboards.peer_table import CapCell, metric_of
from domain.call import CallCard
from domain.enums import State
from signals.display import insider_flow, relative_strength, sma, trailing_returns
from signals.display.base import DisplaySignal

SLUG = "theme-leaderboard"
BENCHMARK = "IWM"

# fewer priced names than this = no median (the Cockpit link header's AGG_MIN_PRICED, the Rotation Map's floor)
MIN_PRICED = 3

# the display members the per-name read runs (by registered name; a renamed member fails loud in ``display_members``)
MEMBERS: tuple[str, ...] = (
    trailing_returns.MEMBER_NAME,
    sma.MEMBER_NAME,
    relative_strength.MEMBER_NAME,
    insider_flow.MEMBER_NAME,
)

# the adoption slope: the last RECENT_MONTHS closed months against the PRIOR_MONTHS before them
RECENT_MONTHS = 3
PRIOR_MONTHS = 3

# the five parts, in the order the page words them
PARTS: tuple[str, ...] = ("tape", "breadth", "keys", "adoption", "insiders")

TapeGap = Literal["thin", "no_iwm"]
AdoptionGap = Literal["no_terms", "not_counted_yet", "no_base"]


# ---------------------------------------------------------------------------------------------------------
# one company's readings
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyTape:
    """One company's readings at the as-of. ``None`` = no reading (an honest gap). ``flag`` = the data flags' labels
    when they reach its price readings: every price reading is then blank and only the Form 4 count stays.
    """

    flag: str | None = None
    priced: bool = False  # trailing_returns gave a reading (the company has a close)
    ret_7d: float | None = None
    ret_30d: float | None = None
    above_200d: bool | None = (
        None  # sma_position's close against its 200-day average; None = no reading
    )
    rs_leading: bool | None = (
        None  # relative_strength's headline is "leading"; None = no RS reading
    )
    buyers_30d: int | None = (
        None  # distinct open-market buyers in 30 days; None = no Form 4 ingested
    )
    cap: CapCell = field(default_factory=CapCell)


NO_TAPE = CompanyTape()


def company_tape(
    signals: Mapping[str, DisplaySignal | None], cap: CapCell, flag: str | None = None
) -> CompanyTape:
    """One company's tape off the display members' signals (keyed by member name). A flagged price history reads no
    price member (``signals`` then holds the insider member alone, or nothing): every price reading is blank.
    """
    ins = signals.get(insider_flow.MEMBER_NAME)
    m = metric_of(ins, "distinct_buyers_30d")
    buyers = None if ins is None or m is None or m.value is None else round(m.value)
    if flag is not None:
        return CompanyTape(flag=flag, buyers_30d=buyers, cap=cap)
    trailing = signals.get(trailing_returns.MEMBER_NAME)
    slow = metric_of(signals.get(sma.MEMBER_NAME), "pct_vs_slow")
    rs = signals.get(relative_strength.MEMBER_NAME)
    ret7, ret30 = metric_of(trailing, "ret_7d"), metric_of(trailing, "ret_30d")
    return CompanyTape(
        priced=trailing is not None,
        ret_7d=None if ret7 is None else ret7.value,
        ret_30d=None if ret30 is None else ret30.value,
        above_200d=None if slow is None or slow.value is None else slow.value > 0,
        rs_leading=(
            None if rs is None else (rs.headline is not None and rs.headline.key == "leading")
        ),
        buyers_30d=buyers,
        cap=cap,
    )


# ---------------------------------------------------------------------------------------------------------
# the recorded card in effect (the keys, the state, the armed names)
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recorded:
    """The ``calls`` row in effect on the as-of (the greatest ``(asof, seq)`` with ``asof <=`` the as-of: the What
    Changed read), narrowed to what this page reads: its night, whether ``pipeline.backfill`` wrote it, the state,
    which keys are turned and how many armed members it lists."""

    asof: date
    reconstructed: bool
    state: State
    conviction: bool
    confirmation: bool
    armed_names: int


def recorded_of(asof: date, reconstructed: bool, card: CallCard) -> Recorded:
    return Recorded(
        asof=asof,
        reconstructed=reconstructed,
        state=card.state,
        conviction=card.key_conviction.turned,
        confirmation=card.key_confirmation.turned,
        armed_names=len({m.security_id for m in card.armed_members}),
    )


@dataclass(frozen=True)
class Keys:
    """The keys the card's recorded call has turned. ``turned`` = 0, 1 or 2 (Managing counts as 2: the position was
    entered on both); ``None`` = the card has no recorded call at the as-of."""

    turned: int | None = None
    conviction: bool | None = None
    confirmation: bool | None = None


def keys_of(recorded: Recorded | None) -> Keys:
    if recorded is None:
        return Keys()
    if recorded.state == State.MANAGING:
        return Keys(2, True, True)
    return Keys(
        int(recorded.conviction) + int(recorded.confirmation),
        recorded.conviction,
        recorded.confirmation,
    )


# ---------------------------------------------------------------------------------------------------------
# the adoption slope
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Adoption:
    """The card's term-mention slope. ``slope_pct`` = (recent / prior - 1) x 100, over the documents of the terms
    whose six closed months are all stored; ``None`` with ``gap`` saying why. ``terms`` = the card's current SIGNAL
    terms, ``terms_counted`` = those the slope rests on. The spans are the months' first days."""

    slope_pct: float | None = None
    recent_docs: int | None = None
    prior_docs: int | None = None
    recent_from: date | None = None
    recent_to: date | None = None
    prior_from: date | None = None
    prior_to: date | None = None
    terms: int = 0
    terms_counted: int = 0
    gap: AdoptionGap | None = None


def closed_months(asof: date, count: int = RECENT_MONTHS + PRIOR_MONTHS) -> list[date]:
    """The ``count`` most recent calendar months CLOSED by the as-of, as their first days, oldest first. A month is
    closed once its last day is before the as-of, so the as-of's own month never is (the current partial month
    never counts), and the as-of on the 1st closes the month before."""
    out: list[date] = []
    month = asof.replace(day=1)
    for _ in range(count):
        month = (month - timedelta(days=1)).replace(day=1)
        out.append(month)
    return out[::-1]


def adoption_slope(rows: Iterable[Mapping[str, Any]], terms: Sequence[str], asof: date) -> Adoption:
    """The adoption part from the card's term-mention rows (``pit.term_mentions``: the newest version per (term,
    month), as-of) and its current SIGNAL ``terms``. Documents are summed over the terms (a document matching two
    terms counts twice) for the last ``RECENT_MONTHS`` closed months and the ``PRIOR_MONTHS`` before them. A term
    counts only when all of those months are stored for it (a term added since the last weekly run is "not counted
    yet", never read as zero); a removed term's stored months are ignored. No such term: the part is missing, as it
    is when the earlier months total no documents (nothing to state a percent against)."""
    wanted = closed_months(asof)
    prior_months, recent_months = wanted[:PRIOR_MONTHS], wanted[PRIOR_MONTHS:]
    spans = {
        "recent_from": recent_months[0],
        "recent_to": recent_months[-1],
        "prior_from": prior_months[0],
        "prior_to": prior_months[-1],
    }
    names = {t.strip().casefold() for t in terms if t.strip()}
    if not names:
        return Adoption(gap="no_terms", **spans)
    stored: dict[str, dict[date, int]] = {}
    for r in rows:
        term = str(r["term"]).strip().casefold()
        if term in names and r["month"] in wanted:
            stored.setdefault(term, {})[r["month"]] = int(r["documents"])
    complete = [by_month for by_month in stored.values() if len(by_month) == len(wanted)]
    base = {"terms": len(names), "terms_counted": len(complete), **spans}
    if not complete:
        return Adoption(gap="not_counted_yet", **base)
    recent = sum(by_month[m] for by_month in complete for m in recent_months)
    prior = sum(by_month[m] for by_month in complete for m in prior_months)
    if prior == 0:
        return Adoption(gap="no_base", recent_docs=recent, prior_docs=prior, **base)
    return Adoption(
        slope_pct=round((recent / prior - 1.0) * 100.0, 2),
        recent_docs=recent,
        prior_docs=prior,
        **base,
    )


# ---------------------------------------------------------------------------------------------------------
# the percentile and the score
# ---------------------------------------------------------------------------------------------------------


def percentiles(values: Sequence[float | None]) -> list[float | None]:
    """Each present value's percentile among the present values, higher value = higher percentile, the mid-rank
    over ``n - 1``: ``(below + (equal - 1) / 2) / (n - 1)`` where ``equal`` counts the value itself. The best of
    n is 1, the worst 0, a tie shares the average of the ranks it spans, a lone value is 0.5 (nothing to compare
    it with). ``None`` stays ``None``. The Hot Board's rule, copied so each dashboard deletes cleanly.
    """
    present = [v for v in values if v is not None]
    n = len(present)
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
        elif n == 1:
            out.append(0.5)
        else:
            below = sum(1 for w in present if w < v)
            equal = sum(1 for w in present if w == v)
            out.append((below + (equal - 1) / 2) / (n - 1))
    return out


@dataclass(frozen=True)
class ScoreInput:
    """What the score reads of a card: the five parts. ``None`` = no reading (the part is blank)."""

    key: str
    tape: float | None = None  # the median 30-day return minus IWM's, percentage points
    breadth: float | None = None  # the share of counting names above their 50-day SMA, percent
    keys: float | None = None  # keys turned, 0 to 2
    adoption: float | None = None  # the term-mention slope, percent
    insiders: float | None = None  # distinct open-market buyers in 30 days, summed


@dataclass(frozen=True)
class Score:
    """A card's five percentiles (0 to 1; ``None`` = that part has no reading) and its score (0 to 100; ``None``
    when any part is missing)."""

    tape: float | None
    breadth: float | None
    keys: float | None
    adoption: float | None
    insiders: float | None
    score: float | None


def score(rows: Sequence[ScoreInput]) -> list[Score]:
    """The score of each card among ``rows`` (the cards shown), in order. Each part's percentile is taken among the
    cards with that reading (a higher value ranks higher for all five); the score is the mean of the five times 100,
    and ``None`` for a card missing any part. Equal weights (PROPOSED)."""
    cols = [percentiles([getattr(r, part) for r in rows]) for part in PARTS]
    out: list[Score] = []
    for parts in zip(*cols):
        total = None if any(p is None for p in parts) else sum(parts) / len(PARTS) * 100.0  # type: ignore[arg-type]
        out.append(Score(*parts, total))
    return out


# ---------------------------------------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardRead:
    """What the router read for one counted card: its COUNTING rows (resolved, Context and Bench out), its current
    SIGNAL terms, its recorded call in effect (``None`` = none by the as-of), its theme-breadth reading over the
    counting names whose price history is not flagged (``None`` = none) and its term-mention rows.
    """

    thesis_id: UUID
    name: str
    members: tuple[Membership, ...]
    terms: tuple[str, ...] = ()
    recorded: Recorded | None = None
    breadth: DisplaySignal | None = None
    mentions: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class TapePart:
    """The card's median 30-day return of its priced counting names (``median``, over ``k`` names) and that minus
    IWM's (``value``, percentage points). ``gap``: ``thin`` = fewer than ``MIN_PRICED`` priced names, ``no_iwm`` =
    IWM has no 30-day return at the as-of."""

    value: float | None = None
    median: float | None = None
    k: int = 0
    gap: TapeGap | None = None


@dataclass(frozen=True)
class MedianPart:
    """A median over the priced names (``None`` below ``MIN_PRICED``), with the ``k`` it rests on."""

    median: float | None = None
    k: int = 0


@dataclass(frozen=True)
class BreadthPart:
    """The share of the counting names above their 50-day SMA: ``pct`` = ``above`` of ``counted`` (``thin`` =
    shown-not-counted for too little history); ``None`` = no member counted. ``above_200`` of ``n_200`` is the 200-day
    share, by the same names' ``sma_position`` reading."""

    pct: float | None = None
    above: int = 0
    counted: int = 0
    thin: int = 0
    above_200: int = 0
    n_200: int = 0


@dataclass(frozen=True)
class InsiderBuyers:
    name: str  # the card's ticker for the company
    buyers: int


@dataclass(frozen=True)
class InsidersPart:
    """Distinct open-market buyers in 30 days summed across the counting names with a Form 4 reading (``read``
    names); ``buyers`` is ``None`` when no name has one. ``names`` = the names with at least one buyer.
    """

    buyers: int | None = None
    read: int = 0
    names: tuple[InsiderBuyers, ...] = ()


@dataclass(frozen=True)
class CapMix:
    """The counting names' approximate-cap tiers at the as-of (the Peer Table's ``cap_cell``): one count per band and
    ``blank`` = names with no cap (flagged, no price, no shares, an unread ADS ratio)."""

    nano: int = 0
    micro: int = 0
    small: int = 0
    mid: int = 0
    large: int = 0
    blank: int = 0


@dataclass(frozen=True)
class CardRow:
    thesis_id: UUID
    thesis_name: str
    state: State | None  # the recorded card in effect's state; None = no recorded call by the as-of
    recorded_asof: date | None
    reconstructed: bool  # the row in effect was written by ``pipeline.backfill``
    counting: int  # distinct resolved counting names
    priced: int  # of those, names with a close at the as-of (never a flagged one)
    flagged: int  # of those, names left out for a flagged price history
    tape: TapePart
    ret_7d: MedianPart
    breadth: BreadthPart
    keys: Keys
    adoption: Adoption
    insiders: InsidersPart
    rs_leaders: int
    rs_n: int
    cap_mix: CapMix
    armed_names: int | None  # the recorded card's armed members when it is Armed


@dataclass(frozen=True)
class Leaderboard:
    asof: date
    clock: Literal["record", "public"]
    benchmark: str
    iwm_ret_30d: float | None
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # counting rows with no resolved security: counted, never read
    names: int  # distinct counting companies across the counted cards
    names_flagged: int
    cards: tuple[CardRow, ...]
    flagged: tuple[FlaggedCompany, ...]


def _median(values: Sequence[float]) -> float | None:
    return round(median(values), 2) if len(values) >= MIN_PRICED else None


def names_of(members: Sequence[Membership]) -> list[tuple[UUID, str]]:
    """The card's distinct counting companies in first-seen order, each with the ticker the card writes."""
    seen: dict[UUID, str] = {}
    for m in members:
        if m.security_id is not None and m.security_id not in seen:
            seen[m.security_id] = m.ticker
    return list(seen.items())


def breadth_part(sig: DisplaySignal | None, tapes: Sequence[CompanyTape]) -> BreadthPart:
    """The 50-day share off ``theme_breadth``'s own reading, the 200-day share off the same names' tapes."""
    n200 = [t.above_200d for t in tapes if t.flag is None and t.above_200d is not None]
    base = {"above_200": sum(n200), "n_200": len(n200)}
    if sig is None:
        return BreadthPart(**base)
    pct = metric_of(sig, "breadth")
    counted = metric_of(sig, "members_counted")
    thin = metric_of(sig, "members_thin")
    n = 0 if counted is None or counted.value is None else round(counted.value)
    t = 0 if thin is None or thin.value is None else round(thin.value)
    if pct is None or pct.value is None or n == 0:
        return BreadthPart(thin=t, **base)
    return BreadthPart(pct=pct.value, above=round(pct.value / 100.0 * n), counted=n, thin=t, **base)


def tape_part(tapes: Sequence[CompanyTape], iwm: float | None) -> TapePart:
    vals = [t.ret_30d for t in tapes if t.flag is None and t.ret_30d is not None]
    med = _median(vals)
    if med is None:
        return TapePart(k=len(vals), gap="thin")
    if iwm is None:
        return TapePart(median=med, k=len(vals), gap="no_iwm")
    return TapePart(value=round(med - iwm, 2), median=med, k=len(vals))


def cap_mix(tapes: Sequence[CompanyTape]) -> CapMix:
    counts = {"nano": 0, "micro": 0, "small": 0, "mid": 0, "large": 0, "blank": 0}
    for t in tapes:
        counts[str(t.cap.tier) if t.cap.tier is not None else "blank"] += 1
    return CapMix(**counts)


def insiders_part(named: Sequence[tuple[str, CompanyTape]]) -> InsidersPart:
    read = [(name, t.buyers_30d) for name, t in named if t.buyers_30d is not None]
    if not read:
        return InsidersPart()
    buyers = sorted(((n, b) for n, b in read if b > 0), key=lambda nb: (-nb[1], nb[0].casefold()))
    return InsidersPart(
        buyers=sum(b for _, b in read),
        read=len(read),
        names=tuple(InsiderBuyers(n, b) for n, b in buyers),
    )


def card_row(
    read: CardRead, tapes: Mapping[UUID, CompanyTape], iwm: float | None, asof: date
) -> CardRow:
    """One card's row: the five parts and the columns beside them, off the card's counting names' tapes."""
    names = names_of(read.members)
    own = [(name, tapes.get(sid, NO_TAPE)) for sid, name in names]
    flat = [t for _, t in own]
    rec = read.recorded
    live = [t for t in flat if t.flag is None]
    rs = [t.rs_leading for t in live if t.rs_leading is not None]
    return CardRow(
        thesis_id=read.thesis_id,
        thesis_name=read.name,
        state=rec.state if rec else None,
        recorded_asof=rec.asof if rec else None,
        reconstructed=rec.reconstructed if rec else False,
        counting=len(names),
        priced=sum(1 for t in live if t.priced),
        flagged=len(flat) - len(live),
        tape=tape_part(flat, iwm),
        ret_7d=_seven_day(flat),
        breadth=breadth_part(read.breadth, flat),
        keys=keys_of(rec),
        adoption=adoption_slope(read.mentions, read.terms, asof),
        insiders=insiders_part(own),
        rs_leaders=sum(rs),
        rs_n=len(rs),
        cap_mix=cap_mix(flat),
        armed_names=rec.armed_names if rec and rec.state == State.ARMED else None,
    )


def _seven_day(tapes: Sequence[CompanyTape]) -> MedianPart:
    vals = [t.ret_7d for t in tapes if t.flag is None and t.ret_7d is not None]
    return MedianPart(_median(vals), len(vals))


def score_input(row: CardRow) -> ScoreInput:
    """The five parts of a row, as the score reads them."""
    return ScoreInput(
        key=str(row.thesis_id),
        tape=row.tape.value,
        breadth=row.breadth.pct,
        keys=None if row.keys.turned is None else float(row.keys.turned),
        adoption=row.adoption.slope_pct,
        insiders=None if row.insiders.buyers is None else float(row.insiders.buyers),
    )


def default_order(rows: Sequence[CardRow]) -> list[CardRow]:
    """The page's default order: the score among every card counted, highest first, a card with no score after every
    scored one, ties by name."""
    scores = score([score_input(r) for r in rows])
    keyed = sorted(
        zip(rows, scores),
        key=lambda rs: (
            rs[1].score is None,
            -(rs[1].score or 0.0),
            rs[0].thesis_name.casefold(),
            str(rs[0].thesis_id),
        ),
    )
    return [r for r, _ in keyed]


@dataclass(frozen=True)
class Counts:
    """The shared basket read's counts for the quiet line (``baskets.CardsAsOf`` + ``baskets.CountedRows``)."""

    live_cards: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int


def assemble(
    *,
    asof: date,
    clock: Literal["record", "public"],
    reads: Sequence[CardRead],
    tapes: Mapping[UUID, CompanyTape],
    flagged: Sequence[FlaggedCompany],
    iwm_ret_30d: float | None,
    counts: Counts,
) -> Leaderboard:
    """The payload: one row per counted card in the default order, and the counts the quiet line shows."""
    rows = default_order([card_row(r, tapes, iwm_ret_30d, asof) for r in reads])
    sids = {m.security_id for r in reads for m in r.members if m.security_id is not None}
    return Leaderboard(
        asof=asof,
        clock=clock,
        benchmark=BENCHMARK,
        iwm_ret_30d=iwm_ret_30d,
        live_cards=counts.live_cards,
        cards_counted=len(reads),
        cards_not_yet_created=counts.cards_not_yet_created,
        roster_fallback_cards=counts.roster_fallback_cards,
        basket_rows=counts.basket_rows,
        silent_rows_left_out=counts.silent_rows_left_out,
        unresolved_rows=counts.unresolved_rows,
        names=len(sids),
        names_flagged=sum(1 for sid in sids if tapes.get(sid, NO_TAPE).flag is not None),
        cards=tuple(rows),
        flagged=tuple(flagged),
    )
