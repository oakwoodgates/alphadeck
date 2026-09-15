"""The per-name feed RECENCY rule (G5a price tapes · F1 fund shares) — "has this feed silently ended?"

TWO FEEDS, ONE RULE. The judgment (``is_tape_stale``) and the dedup (``_stale``) are shared; what differs
per feed is its THRESHOLD (its own ``Settings`` field, because the cadences differ) and its PROSE (its
noun and its repair advice, which live once in ``domain/feed_kinds``). Adding a third feed is a kind, a
threshold and a field adapter — never a second definition of "stale".

THE GAP THIS CLOSES. The price leg appends bars after the latest stored one and hole-fills inside the
vendor's window (``ingest/prices/ingest_security.py``). A name whose vendor series simply STOPS — the SEC
ticker stays canonical but the vendor prices the name under a new symbol after a rename, or the name
delisted — returns a series that ends at the stop: **no error, zero bars appended.** That is byte-identical
to what a market holiday looks like, so nothing in the nightly path, the health pager, the run log or the
Admin surface could tell them apart. For a rename-starved name every price-driven detector then goes dark
from the rename date: no breakout, no SMA flip, no RVOL, no laggard — and no price-based de-arm either,
while the CIK-keyed filing legs keep flowing, so the name can still WARM on a filing and never confirm on
price.

WHAT THIS MODULE IS, AND IS NOT:

- **A MONITOR, not a signal.** It imports nothing from ``calls/`` or ``signals/`` and is imported by
  neither. It cannot fire, arm, veto or grade anything (#3/#4). Its output rides on the run RESULT and the
  run-of-record artifact — deliberately **never on the CallCard**: a day-varying field in the card would
  flap ``record_if_changed``'s substance compare and break the cron's idempotency.
- **PURE.** No I/O, no DB, no clock: ``asof`` and ``stale_days`` are parameters (the signal-purity
  discipline applied to a monitor — time is never ambient). The FACTS it judges (a name's latest stored bar
  date, and an ETF sleeve's latest stored shares sample) are read in ``pipeline/ingest_thesis.py`` and
  carried on ``NameResult.tape_edge`` / ``NameResult.fund_shares_edge``; the JUDGMENT is
  made in ``pipeline/daily.py``, which is the layer that has the run's ``asof``.
- **CALENDAR days, on purpose.** ``domain/market_time.py`` deliberately has no trading calendar (no weekend
  skip, no holidays — see its docstring), so a trading-day count is not available here and teaching it one
  is out of scope. What the threshold actually buys: a LIVE tape gets that session's bar appended by the
  nightly pass, so its edge sits at 0-1 days and the threshold never comes near it — the number only starts
  to matter once bars STOP arriving. ``Settings.tape_stale_days`` = 5 therefore means **a tape may lag up to
  FOUR calendar days before it reads stale** (room for a vendor delay stretched across a weekend), while a
  genuinely dead tape surfaces within a week. Worked through: a Friday close is fresh through Tuesday's pass
  (4 days) and reads stale on WEDNESDAY's (5), so an ordinary weekend — or a weekend plus a Monday or Friday
  holiday — sits comfortably inside the window. The ACCEPTED EDGE: a rare TWO-session closure adjacent to a
  weekend (a Thursday+Friday shutdown leaves a Wednesday edge and a Monday pass = 5 days) trips it for ONE
  night and clears on the next session. Raise ``ALPHADECK_TAPE_STALE_DAYS`` if that night ever matters more
  than catching a dead tape a day sooner.

THE FUND-SHARES FEED (F1) — the same silent-end class, one layer over. An ETF sleeve's shares-outstanding
sample feeds the flow read (``signals/display/etf_flow.py`` — DISPLAY context, never a call input), and its
source is a fallback chain (Polygon when keyed → the issuer page → the aggregator) whose legs miss
independently. Two ways it stops without an error: every leg misses (a closed/renamed fund, a redesigned
page), or — the subtler one — a page's own STATED as-of date freezes while it keeps serving the same count,
which the incremental compare correctly skips as an unchanged sample. Either way the leg reports success and
the series stands still. It gets its OWN threshold (``Settings.fund_shares_stale_days``) because the
cadences differ: MEASURED on dev, the primary source states the pull date exactly (lag 0) while the
aggregator fallback stated a two-day-old date, so a healthy sleeve's edge sits 0-2 days behind the pass.

The REPAIRS are the operator's and are not code, and they DIFFER per feed (which is why each kind carries
its own advice in ``domain/feed_kinds``): for a price tape, ``security_master.price_symbol`` (the OTC symbol
override, #252) points the price leg at the vendor's current symbol and the next nightly pass re-pulls the
full year, appends the missing tail and hole-fills the overlap; for a fund-shares tape it is a fund/ticker/
source question (does it still trade under that ticker, did a source page change, is the key set). Extending
the symbol resolver to renames automatically is a separate, unbuilt decision: a wrong auto-resolve would
file ANOTHER company's tape under this member, which is worse than a visible gap (#4/#6). See
``docs/ADMIN.md`` + ``docs/DATA_SOURCES.md``.

DELISTED vs FEED-GAP (G5b — the "closed" classification). Not every stopped price tape is a feed gap to
repair: a name that DELISTED / was acquired / deregistered legitimately stopped trading, and its tape
correctly ends. Nagging the operator to "set the vendor price symbol" for such a name is wrong — there is
nothing to repair. The DETERMINISTIC tell (#3, never an LLM, never the master's ``status`` heuristic) is a
SEC delisting form — Form 25 / 25-NSE (removal from listing) or 15-12B / 15-12G (deregistration) — read
from the name's submissions (``ingest.edgar.submissions.delisting_date``) and carried as
``NameResult.delisted_at``. A stale tape WITH one is classified CLOSED (``StaleTape.closed_at`` set to the
delisting form's filing date, #1 valid-time); WITHOUT one it stays a plain stale-repair row, exactly as
before. A closed name is NEVER dropped from the roster, the monitor, or the panel (#9) — the marker only
RECLASSIFIES the stopped tape, and it is EXPECTED so it renders quietly and does not page as newly-stale
(#7/WB#3). This is a DERIVE-ON-READ classification (nothing persisted, reversible by construction): every
pass re-reads the cached submissions, so if a detection is ever wrong the fix is code, never a stored row
to clear.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from domain.feed_kinds import FUND_SHARES, PRICE, FeedKind, feed_kind

if TYPE_CHECKING:  # avoid importing the ingest module at import time (keeps the layering one-way)
    from pipeline.ingest_thesis import NameResult


def stale_label(ticker: str | None, security_id: object) -> str:
    """The ONE display rule for a stale tape, shared by the page, the Admin panel and the artifact reader:
    the ticker when there is one, else the security id.

    A ticker-less member is never dropped from a list or silently collapsed (#9 — recall is sacred applies
    to a monitor's output too): an unresolved/ticker-less name with a dead tape is exactly the case an
    operator most needs to see, so it renders by id rather than vanishing."""
    return ticker or str(security_id)


@dataclass(frozen=True)
class StaleTape:
    """One name whose monitored feed has gone stale: its ``edge`` (the latest stored date for that feed,
    ``None`` when it has NO data at all) plus the identity needed to render and to diff, and ``kind`` —
    WHICH feed stopped (``domain/feed_kinds``: ``price`` | ``fund_shares``).

    ``security_id`` is the diff KEY — never the ticker: a ticker-less name must still be able to page, and a
    ticker can change under a name (which is half of why this monitor exists). With two feeds the key is
    ``(kind, security_id)``: one name can be stale on both at once and each is its own repair.

    ``kind`` defaults to ``price`` because that is what it means everywhere it is absent — the rows written
    before this field existed are price rows (see ``feed_kind``'s fail-soft note).

    ``closed_at`` (G5b) is the DELISTING classification: the filing date of the name's SEC delisting form
    (25 / 25-NSE / 15-12B / 15-12G) when this stopped tape belongs to a name that CLOSED — delisted,
    acquired, deregistered — else ``None``. ``None`` = a feed gap to REPAIR (a rename the vendor priced
    under a new symbol; the fix is ``security_master.price_symbol``); a date = the tape correctly ENDED
    and the name stopped trading around then. A closed tape is EXPECTED, so it renders quietly and does NOT
    page as newly-stale (#7/WB#3), but it is NEVER dropped from the inventory or the monitor (#9) — the
    marker only RECLASSIFIES the row. Only ever set on a PRICE row (delisting is a listing event); the
    fund-shares feed never carries it."""

    ticker: str | None
    security_id: UUID
    edge: date | None
    kind: str = PRICE.key
    closed_at: date | None = None

    @property
    def label(self) -> str:
        return stale_label(self.ticker, self.security_id)

    @property
    def feed(self) -> FeedKind:
        """The vocabulary entry for this row's kind (noun + repair advice), fail-soft to price."""
        return feed_kind(self.kind)

    @property
    def closed(self) -> bool:
        """Did this stopped tape stop because the name CLOSED (a delisting form on file), rather than a
        feed gap to repair? A closed tape renders quietly and never pages as newly-stale (#7/WB#3); a
        non-closed one stays the loud "stale — repair" row."""
        return self.closed_at is not None


def is_tape_stale(edge: date | None, *, asof: date, stale_days: int) -> bool:
    """Has this tape stopped? ``True`` when it has NO bars at all (``edge is None``) or its latest bar is
    ``stale_days`` or more CALENDAR days before ``asof``.

    The ``>=`` boundary is deliberate: with ``stale_days=5`` an edge of ``asof - 5`` days reads stale and
    ``asof - 4`` does not. Counted out from a Friday close: Monday's pass is 3 days (fresh), Tuesday's is 4
    (fresh), and WEDNESDAY's is 5 — stale. So the window tolerates a weekend, and a weekend plus a Monday or
    Friday holiday, but not a full week without a bar. ``stale_days <= 0`` disables the judgment entirely
    (the monitor's off-switch): nothing is stale.
    """
    if stale_days <= 0:
        return False
    if edge is None:
        return True
    return (asof - edge).days >= stale_days


def _stale(
    rows: Iterable[tuple[UUID, str | None, date | None, date | None]],
    *,
    kind: FeedKind,
    asof: date,
    stale_days: int,
) -> tuple[StaleTape, ...]:
    """The shared judgment + dedup, over ``(security_id, ticker, edge, closed_at)`` tuples. ONE
    implementation for every feed kind — the per-kind public functions below are just the field adapters,
    so a second feed can never acquire a second definition of "stale" or a second dedup rule.

    ``closed_at`` (G5b) is the delisting classification for a PRICE row (the price adapter passes the name's
    delisting-form date; the fund-shares adapter always passes ``None`` — delisting is a listing event). It
    rides onto the emitted ``StaleTape`` so a stopped tape can render "closed — stopped trading <date>"
    instead of the loud "stale — repair". It is only ever meaningful on an emitted (stale) row: a name with
    a delisting form but a still-FRESH tape is not emitted at all (nothing to mark until the tape stops).

    The dedup is load-bearing, not tidiness: a name the draft placed in N value-chain links is N
    ``basket_member`` rows with the SAME ``security_id``, so ``ingest_thesis`` returns N ``NameResult``s for
    it (the later walks append nothing — incremental) and a naive count would report one dead feed N times,
    on the page and in the panel. Same rule as the on-promote ingest summary's ``members`` count. Dedup is
    WITHIN a kind, so a name stale on both feeds yields one row per feed — two different repairs.

    FIRST-SEEN order preserved."""
    out: list[StaleTape] = []
    seen: set[UUID] = set()
    for security_id, ticker, edge, closed_at in rows:
        if security_id in seen:
            continue
        seen.add(security_id)
        if is_tape_stale(edge, asof=asof, stale_days=stale_days):
            out.append(
                StaleTape(
                    ticker=ticker,
                    security_id=security_id,
                    edge=edge,
                    kind=kind.key,
                    closed_at=closed_at,
                )
            )
    return tuple(out)


def stale_tapes(
    results: Iterable[NameResult], *, asof: date, stale_days: int
) -> tuple[StaleTape, ...]:
    """The stale PRICE tapes among one thesis's per-name ingest results (see ``_stale`` for the dedup).

    Every resolved member is judged: a price tape is expected for all of them. A name whose price leg
    ERRORED is still judged — the edge read is independent of the leg's outcome, so a name that is both
    failing and dead shows up as both (the error on its own result, the stale tape here), never silently
    one or the other.

    ``r.delisted_at`` (G5b) rides onto each row as ``closed_at``: a stopped tape whose name has a SEC
    delisting form on file is a name that CLOSED, so it renders "closed — stopped trading <date>" and does
    not page, rather than the loud "stale — repair". A ``None`` delisting date (the healthy common case)
    keeps the row a plain stale-repair one."""
    return _stale(
        ((r.security_id, r.ticker, r.tape_edge, r.delisted_at) for r in results),
        kind=PRICE,
        asof=asof,
        stale_days=stale_days,
    )


def stale_fund_shares(
    results: Iterable[NameResult], *, asof: date, stale_days: int
) -> tuple[StaleTape, ...]:
    """The stale FUND-SHARES tapes among one thesis's per-name results — **only the members the leg
    applies to** (``r.fund_shares_tracked``: an ETF sleeve with a ticker).

    The filter is the difference between this and its price sibling, and it is not an optimization: an
    equity member has no samples, so its edge is ``None``, and ``None`` means STALE to the rule. Judging
    ungated would report every equity in every basket as a dead fund tape. Conversely a TRACKED sleeve with
    a ``None`` edge is genuinely news — its sampler has never produced anything — so the rule's ``None``
    handling is exactly right once the gate is applied.

    Its own threshold (``Settings.fund_shares_stale_days``), because the feeds have different cadences:
    a price tape gets a bar every session, while a sleeve's sample carries the source page's own stated
    as-of date, which measurably lags the pull by up to two days on the fallback leg.

    ``closed_at`` is always ``None`` here (the fourth tuple element): delisting is a LISTING event and the
    "closed" classification is a price-tape concept, so a stopped fund-shares sample is never marked closed
    by this mechanism — it stays a stale-repair row (its own repair: a fund/ticker/source check)."""
    return _stale(
        (
            (r.security_id, r.ticker, r.fund_shares_edge, None)
            for r in results
            if r.fund_shares_tracked
        ),
        kind=FUND_SHARES,
        asof=asof,
        stale_days=stale_days,
    )
