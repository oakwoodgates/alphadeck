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

DELISTED vs FEED-GAP (G5b, tightened by T1 — the "closed" classification, ``classify_stop``). Not every
stopped price tape is a feed gap to repair: a name that DELISTED / was acquired / deregistered
legitimately stopped trading, and its tape correctly ends. Nagging the operator to "set the vendor price
symbol" for such a name is wrong — there is nothing to repair. But the reverse error is WORSE: a real feed
gap mislabeled "closed" is SILENCED — it renders quietly and never pages — so the classification has to
earn the quiet treatment, and anything short of that evidence stays the loud repair row (#9: never silence
a name; #7/WB#3: the quiet is reserved for the expected event). A stopped tape is CLOSED only when BOTH:

1. **Proximity.** A SEC delisting form — Form 25 / 25-NSE (removal from listing) or 15-12B / 15-12G
   (deregistration), deterministic (#3, never an LLM, never the master's ``status`` heuristic) — was
   filed inside ``[last bar − tape_closed_before_days, last bar + tape_closed_after_days]`` (defaults 30 /
   45, ``Settings``) and on or before the run's ``asof`` (a filing the night could not have seen is not
   evidence for it, #1). ``closed_at`` is that filing's date; with several inside the window, the one
   NEAREST the last bar wins (a tie goes to the later filing). A form on file at some OTHER time is not a
   close: VRME carried a 2025-06-23 Form 25-NSE that removed its WARRANTS, kept trading its common stock
   for 15 months, and was labeled "closed" off it the night that stock's tape stopped (it had become
   OpenWorld/OPNW). MEASURED 2026-10-16 on prod: the 8 genuine closes all have their nearest delisting
   form within [−7, +13] days of their last bar, while 192 of the 675 still-trading names among prod's 688
   basket members carry SOME delisting form — every one of which the old "any form, ever" rule would have
   silenced the night its tape stopped. 7 still would under proximity alone if their tape stopped tonight,
   each on a 25-NSE filed in the last 30 days — the residual the CLASS CHECK below closes. A name that has
   NEVER been priced (``edge`` is None) has no last bar to be near, so it can never be closed by proximity;
   it stays a stale-repair row.

   **The Form 25 CLASS CHECK (Q6).** A Form 25 / 25-NSE in the window counts only if its document removes
   THIS security — the common or ordinary stock, or the ADS for an ADR. One that names ANOTHER class
   (warrants, units, rights, notes, preferred) is not evidence, and the next-nearest form is tried. The
   document is read by ``ingest/edgar/form25.py`` (deterministic text, never a model) through the night's
   EDGAR client, ONLY for a name already judged stale, and is cached forever (``forms/*`` is immutable), so a
   name pays one fetch per Form 25 the first night it goes stale. An UNKNOWN or unreadable class (a paper
   filing, a failed fetch, a description the reader does not recognize) still COUNTS — today's reading, the
   fail-safe — and prints one line saying so. MEASURED 2026-10-17 (prod's cached submissions, each window
   Form 25 fetched): ACHR, ARBE, BKKT, IONQ, KTTA and PDYN — warrants, every one — no longer read closed;
   VSTD's 2026-10-09 25-NSE removes its "Class A Common Stock" (a Nasdaq 12d2-2(b) strike), so it still
   would, correctly; the 8 genuine closes all stay closed (each Form 25 reads "Common Stock" / "Class A
   common stock"), with one ``closed_at`` moving: XOMA's nearest form was the 2026-07-16 25-NSE removing
   its PREFERRED depositary shares, so its close now dates from the next-nearest, the 2026-07-27 15-12G.
2. **No rename.** EDGAR's current ``tickers`` list (``submissions.edgar_tickers``) is EMPTY, or holds the
   master ``ticker`` or ``price_symbol``. When EDGAR lists symbols and NEITHER of ours is among them, the
   company is still listed under a different symbol: the stopped tape is a rename-starved feed, so the row
   is a stale-REPAIR row marked ``renamed`` and carries EDGAR's list for display ("renamed — EDGAR lists
   OPNW"), never closed — even if a delisting form sits right at the edge (BTAI's Nasdaq exit to OTC as
   BTAIQ is the shape). MEMBERSHIP, never ``tickers[0]``: EDGAR lists every class side by side
   (``['KYOCF', 'KYOAY']``), so a first-element read would mis-flag a name priced under its second symbol.
   MEASURED 2026-10-16 across prod's 688 basket members (all cached): the rule flags exactly HLLK, CTGL,
   BTAI, AIXC, BRR and VRME, with 0 false positives (WELPM, PRTC/PRTCY, FDCT/FDCTD, KYOCY/KYOAY and ABBNY
   all pass); a ``tickers[0]`` read would ALSO have flagged ABBNY, KYOCY, PRTC and WELPM.

The marker is DISPLAY-only: nothing here writes ``price_symbol`` or adopts the EDGAR symbol (a wrong
adoption would file another company's tape under the member, #4/#6). The recommender that proposes an
adoption for the operator to ratify is ``pipeline/rename_probe.py`` (T2: a nightly splice probe of each
EDGAR symbol against our stored tape — a pending recommendation, never applied), and the operator's
ratifications (Adopt / Set symbol / Acknowledge, each with its Undo) are the Admin price-feed repairs
(``app/routers/admin_price_feed.py``). A closed name's price 404 — and an acknowledged unpriceable name's —
counts as EXPECTED (T3, ``pipeline/price_feed.py``). WHICH security class a Form 25 removed (Q6) is
``classify_stop``'s injected ``removes_other_class`` hook, so this module stays pure.

UNPRICEABLE (Q8) is the third reading of a stopped price tape, beside closed and renamed: a member with NO
stored bar at all that neither explains (``is_unpriceable``). It is surfaced loudly (it pages once when new,
through the same newly-stale diff) and stays on the Admin page until the operator ratifies it — sets a
price symbol, acknowledges it ("keep it, unpriced": quiet thereafter, revocable), or removes it from the
basket in the editor.

A closed name is NEVER dropped from the roster, the monitor, or the panel (#9) — the marker only
RECLASSIFIES the stopped tape, and it is EXPECTED so it renders quietly and does not page as newly-stale
(#7/WB#3). A name that moves the other way, closed → stale-repair (VRME the first night this ships), is
news: the newly-stale diff counts only NON-closed rows on both sides (``cron_run_log.previous_stale_tapes``),
so it pages exactly once. This is a DERIVE-ON-READ classification (nothing persisted, reversible by
construction): every pass re-reads the cached submissions, so if a detection is ever wrong the fix is code,
never a stored row to clear.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID

from domain.feed_kinds import FUND_SHARES, PRICE, FeedKind, feed_kind

if TYPE_CHECKING:  # avoid importing the ingest module at import time (keeps the layering one-way)
    from ingest.edgar.submissions import DelistingFiling
    from pipeline.ingest_thesis import NameResult

# The proximity window's defaults, in calendar days around the tape's last bar (``classify_stop``). The
# ``Settings`` fields ``tape_closed_before_days`` / ``tape_closed_after_days`` carry the live values and a
# test pins them equal to these. BEFORE = 30: a Form 25 is filed ahead of the removal (it takes effect ten
# days after filing, and an exchange can file well before the last session). AFTER = 45: the Form 15
# deregistration follows the delisting, and SEC processing can lag it by weeks. MEASURED 2026-10-16 on the
# 8 genuine closes on prod: nearest form within [−7, +13] days, so both windows leave real headroom while
# still excluding a stray form filed a season (or, for VRME's warrants, 15 months) away.
DEFAULT_CLOSED_BEFORE_DAYS = 30
DEFAULT_CLOSED_AFTER_DAYS = 45

# Q6 — the delisting forms whose DOCUMENT names the class removed (the removal-from-listing pair; the
# 15-12B / 15-12G deregistrations are not read). Kept equal to ``ingest.edgar.form25.FORM_25`` by a test —
# a local copy so this pure module imports nothing from ``ingest`` at run time.
FORM_25 = frozenset({"25", "25-NSE"})


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

    ``closed_at`` (G5b/T1) is the DELISTING classification: the filing date of the SEC delisting form
    (25 / 25-NSE / 15-12B / 15-12G) that explains this stopped tape — filed near its last bar, on a name
    EDGAR no longer lists under a different symbol (``classify_stop``) — else ``None``. ``None`` = a feed
    gap to REPAIR (the fix is ``security_master.price_symbol``); a date = the tape correctly ENDED and the
    name stopped trading around then. A closed tape is EXPECTED, so it renders quietly and does NOT page as
    newly-stale (#7/WB#3), but it is NEVER dropped from the inventory or the monitor (#9) — the marker only
    RECLASSIFIES the row. Only ever set on a PRICE row (delisting is a listing event); the fund-shares feed
    never carries it.

    ``renamed`` (T1) marks a REPAIR row whose company EDGAR still lists, under symbols that include neither
    the master ``ticker`` nor ``price_symbol`` — the rename-starved shape, so the advice is "point the
    price symbol at the new listing". Never set together with ``closed_at``. ``edgar_tickers`` is EDGAR's
    current list as read this pass (``()`` = EDGAR lists none, ``None`` = not read: no CIK, or the
    submissions read failed); DISPLAY only — nothing adopts it.

    ``unpriceable`` (Q8, derived — see ``is_unpriceable``) is the third reading of a stopped price tape: a
    name with NO stored bar at all that is neither closed nor renamed (WONDF — a 40-F filer EDGAR lists no
    symbol for, 404 at the vendor every night). It stays a loud repair row until the operator ratifies it.
    ``acknowledged`` marks an unpriceable row the operator ACKNOWLEDGED ("keep it, unpriced" — an
    append-only, revocable ``price_feed_ack`` row): it goes QUIET like a closed row (no newly-stale page, a
    quiet count on Admin, still listed — #9), and its vendor 404 counts as expected (T3). Only ever set on an
    unpriceable row: an acknowledgment cannot silence a tape that has bars and then stopped (that is a feed
    gap, whatever was acknowledged before)."""

    ticker: str | None
    security_id: UUID
    edge: date | None
    kind: str = PRICE.key
    closed_at: date | None = None
    renamed: bool = False
    edgar_tickers: tuple[str, ...] | None = None
    acknowledged: bool = False

    @property
    def label(self) -> str:
        return stale_label(self.ticker, self.security_id)

    @property
    def feed(self) -> FeedKind:
        """The vocabulary entry for this row's kind (noun + repair advice), fail-soft to price."""
        return feed_kind(self.kind)

    @property
    def closed(self) -> bool:
        """Did this stopped tape stop because the name CLOSED (a delisting form filed near its last bar, no
        rename), rather than a feed gap to repair? A closed tape renders quietly and never pages as
        newly-stale (#7/WB#3); a non-closed one stays the loud "stale — repair" row."""
        return self.closed_at is not None

    @property
    def unpriceable(self) -> bool:
        """Q8 — a price row with no stored bar at all that is neither closed nor renamed
        (``is_unpriceable``)."""
        return is_unpriceable(
            kind=self.kind, edge=self.edge, closed_at=self.closed_at, renamed=self.renamed
        )

    @property
    def quiet(self) -> bool:
        """Does this stopped feed stay OFF the loud newly-stale page? A CLOSED tape (an expected event) and
        an ACKNOWLEDGED unpriceable one (the operator's ratified "keep it, unpriced") — both still listed,
        never dropped (#9). Everything else is a repair the operator has not ratified yet, and pages once.
        """
        return self.closed or self.acknowledged


def is_unpriceable(
    *, kind: str | None, edge: date | None, closed_at: date | None, renamed: bool
) -> bool:
    """Q8 — the UNPRICEABLE class, the ONE definition (the nightly pass, the artifact reader and the Admin
    panel all ask this): a PRICE row whose tape has NO stored bar at all (``edge is None`` — never priced,
    or no priceable symbol: a ticker-less member lands here too) that is NOT explained by the closed
    classifier (T1 — a never-priced name has no last bar, so it can never be closed by proximity anyway)
    nor by the rename guard (T2 — EDGAR lists another symbol to probe). WONDF is the first instance: a 40-F
    filer, EDGAR ``tickers=[]``, 404 at the vendor every night. Pure, fail-soft on ``kind`` (``None`` reads
    ``price``, the artifact convention)."""
    return feed_kind(kind).key == PRICE.key and edge is None and closed_at is None and not renamed


@dataclass(frozen=True)
class StopClassification:
    """What ``classify_stop`` concluded about ONE name's stopped tape — the three fields it lends to a
    ``StaleTape``. The default (nothing set) is the plain stale-repair reading, which is also what every
    fund-shares row and every row with no EDGAR evidence gets: the LOUD reading is the default and the
    quiet one has to be earned."""

    closed_at: date | None = None
    renamed: bool = False
    edgar_tickers: tuple[str, ...] | None = None


UNCLASSIFIED = StopClassification()


def classify_stop(
    *,
    edge: date | None,
    filings: Iterable[DelistingFiling],
    edgar_tickers: tuple[str, ...] | None,
    ticker: str | None,
    price_symbol: str | None,
    asof: date,
    before_days: int = DEFAULT_CLOSED_BEFORE_DAYS,
    after_days: int = DEFAULT_CLOSED_AFTER_DAYS,
    removes_other_class: Callable[[DelistingFiling], bool] | None = None,
) -> StopClassification:
    """Is this stopped price tape a CLOSE, a RENAME, or a plain feed gap? Pure over its arguments (the one
    I/O, reading a Form 25's class, is the injected ``removes_other_class``); the caller has already
    judged the tape stale (see the module docstring for the rule and the measurements behind it).

    1. RENAME guard first: EDGAR lists one or more current symbols and NEITHER ``ticker`` nor
       ``price_symbol`` is among them (case-insensitive MEMBERSHIP, never ``tickers[0]``) → a repair row
       marked ``renamed``, never closed, whatever the forms say. An empty or unread list never fires it.
    2. PROXIMITY: among the delisting filings filed in ``[edge − before_days, edge + after_days]`` and on
       or before ``asof``, the one nearest ``edge`` (a tie goes to the later filing) closes the tape, and
       ``closed_at`` is its filing date. No last bar (``edge`` is None) → nothing to be near → not closed.
       Q6 — a FORM 25 / 25-NSE candidate counts only if ``removes_other_class(filing)`` is False: one whose
       document names ANOTHER class (warrants, units, rights, notes, preferred — VRME's warrants) is not
       evidence this security closed, and the next-nearest candidate is tried. The hook is asked
       nearest-first and only until a candidate counts, so a stale name pays at most one document read per
       Form 25 it has to look past. An UNKNOWN class reads False — it counts, exactly as before Q6 (the
       fail-safe; the hook logs it). ``None`` (no hook) = every candidate counts, the T1 rule.
    3. Otherwise a plain repair row.

    ``edgar_tickers`` rides onto the result in every case (display only). A negative window reads as 0
    (only a form filed on the edge day itself counts on that side) — never an error inside the nightly
    loop."""
    listed = edgar_tickers
    ours = {s.strip().upper() for s in (ticker, price_symbol) if s and s.strip()}
    if listed and not ours.intersection(t.strip().upper() for t in listed):
        return StopClassification(renamed=True, edgar_tickers=listed)
    if edge is None:
        return StopClassification(edgar_tickers=listed)
    before, after = max(0, before_days), max(0, after_days)
    near = [f for f in filings if f.filed <= asof and -before <= (f.filed - edge).days <= after]
    for f in sorted(near, key=lambda f: (abs((f.filed - edge).days), -f.filed.toordinal())):
        if removes_other_class is not None and f.form in FORM_25 and removes_other_class(f):
            continue  # Q6 — this Form 25 removed another class; not evidence THIS security closed
        return StopClassification(closed_at=f.filed, edgar_tickers=listed)
    return StopClassification(edgar_tickers=listed)


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
    rows: Iterable[tuple[UUID, str | None, date | None, Callable[[], StopClassification] | None]],
    *,
    kind: FeedKind,
    asof: date,
    stale_days: int,
) -> tuple[StaleTape, ...]:
    """The shared judgment + dedup, over ``(security_id, ticker, edge, classify)`` tuples. ONE
    implementation for every feed kind — the per-kind public functions below are just the field adapters,
    so a second feed can never acquire a second definition of "stale" or a second dedup rule.

    ``classify`` (G5b/T1) is a zero-argument callable returning the stopped tape's ``StopClassification``
    (closed / renamed / plain repair) for a PRICE row, or ``None`` (the fund-shares adapter — delisting is a
    listing event). It is called ONLY for a row already judged stale, so the classification can never mark
    a live tape: a name with a delisting form or a renamed EDGAR listing but a still-FRESH tape is not
    emitted at all (nothing to mark until the tape stops).

    The dedup is load-bearing, not tidiness: a name the draft placed in N value-chain links is N
    ``basket_member`` rows with the SAME ``security_id``, so ``ingest_thesis`` returns N ``NameResult``s for
    it (the later walks append nothing — incremental) and a naive count would report one dead feed N times,
    on the page and in the panel. Same rule as the on-promote ingest summary's ``members`` count. Dedup is
    WITHIN a kind, so a name stale on both feeds yields one row per feed — two different repairs.

    FIRST-SEEN order preserved."""
    out: list[StaleTape] = []
    seen: set[UUID] = set()
    for security_id, ticker, edge, classify in rows:
        if security_id in seen:
            continue
        seen.add(security_id)
        if is_tape_stale(edge, asof=asof, stale_days=stale_days):
            c = classify() if classify is not None else UNCLASSIFIED
            out.append(
                StaleTape(
                    ticker=ticker,
                    security_id=security_id,
                    edge=edge,
                    kind=kind.key,
                    closed_at=c.closed_at,
                    renamed=c.renamed,
                    edgar_tickers=c.edgar_tickers,
                )
            )
    return tuple(out)


def stale_tapes(
    results: Iterable[NameResult],
    *,
    asof: date,
    stale_days: int,
    closed_before_days: int = DEFAULT_CLOSED_BEFORE_DAYS,
    closed_after_days: int = DEFAULT_CLOSED_AFTER_DAYS,
    removes_other_class: Callable[[str, DelistingFiling], bool] | None = None,
) -> tuple[StaleTape, ...]:
    """The stale PRICE tapes among one thesis's per-name ingest results (see ``_stale`` for the dedup).

    Every resolved member is judged: a price tape is expected for all of them. A name whose price leg
    ERRORED is still judged — the edge read is independent of the leg's outcome, so a name that is both
    failing and dead shows up as both (the error on its own result, the stale tape here), never silently
    one or the other.

    Each stale row is then CLASSIFIED (``classify_stop``) from the EDGAR facts the ingest carried on the
    result — ``r.delisting_filings`` and ``r.edgar_tickers`` — against the name's own ``ticker`` /
    ``price_symbol`` and the proximity window (``closed_before_days`` / ``closed_after_days``; the nightly
    pass passes the ``Settings`` values). CLOSED renders "closed — stopped trading <date>" and does not
    page; RENAMED and plain rows stay the loud "stale — repair", the renamed one naming EDGAR's symbols. No
    EDGAR facts at all (no CIK, a failed read) → the plain repair reading, never the quiet one.

    ``removes_other_class`` (Q6) is the Form 25 class check, called as ``(label, filing)`` — the name's
    display label rides along for the hook's log line. The nightly pass binds it to its EDGAR client
    (``ingest.edgar.form25.delisted_class_is_other``); it runs only inside ``classify_stop``, which runs
    only for a row already judged STALE, so a live tape never costs a document read. ``None`` = the T1
    rule (every Form 25 in the window counts)."""
    return _stale(
        (
            (
                r.security_id,
                r.ticker,
                r.tape_edge,
                partial(
                    classify_stop,
                    edge=r.tape_edge,
                    filings=r.delisting_filings,
                    edgar_tickers=r.edgar_tickers,
                    ticker=r.ticker,
                    price_symbol=r.price_symbol,
                    asof=asof,
                    before_days=closed_before_days,
                    after_days=closed_after_days,
                    removes_other_class=(
                        partial(removes_other_class, stale_label(r.ticker, r.security_id))
                        if removes_other_class is not None
                        else None
                    ),
                ),
            )
            for r in results
        ),
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

    No classifier here (the fourth tuple element is ``None``): delisting and renames are LISTING events and
    the "closed" / "renamed" classification is a price-tape concept, so a stopped fund-shares sample is
    never marked by this mechanism — it stays a plain stale-repair row (its own repair: a fund/ticker/source
    check)."""
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
