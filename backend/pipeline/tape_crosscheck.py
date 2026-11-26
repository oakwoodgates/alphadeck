"""THE TAPE CROSS-CHECK (W6-3a) — tonight's stored closes against Massive.com's raw grouped day.

THE GAP THIS CLOSES. The price leg stores only what the vendor (Yahoo) serves, and Yahoo nulls sessions around a
split (MEASURED 2026-11-25: every post-split session of SNDK's 8-for-1 of 11-20, SCNI's 1-for-4 of 11-19 and SOS;
the first post-split session of DFSC, EMBJ, SGRX, COSM, KALA, PARA). A null close is dropped, nothing else prices
the name, and the tape stops: the missing-close line lists it, the stale-tape monitor then advises "check for a
ticker rename" — wrong for every one of them (the symbol is right; the vendor has no close). One raw grouped read
a night (``ingest/massive_bars.py``, ~17,000 rows, one request) answers, for the same companies the missing-close
check judges: does Massive.com have a trade for the session we have no close for, and do our stored closes agree
with its raw closes. For each open stale price row: did its symbol trade tonight ("a vendor gap, not a rename").

WHAT THIS MODULE IS, AND IS NOT (the missing-close discipline, ``pipeline/missing_closes.py``):

- **A MONITOR, not a signal and not a writer.** No tape write (the fill is W6-3b's, held), never on the CallCard,
  never paged: the run result, the run artifact (``tape_crosscheck``), the cron log and the Admin page only. It
  imports nothing from ``calls/`` or ``signals/``.
- **The judgment is PURE** (``judge``): no I/O, no clock. The FACTS are the pass's own per-name results (the
  judged set ``missing_closes.candidates_of`` builds, the stale inventory, the vendor price symbol), the grouped
  day fetched ONCE before the per-thesis loop (``fetch_grouped``, so W6-3b's fill can share it) and one
  read-only DB read (the session's newest stored version per company, the split list). ``run_tape_crosscheck``
  is the fail-open nightly unit.
- **Off is a status, never an error.** No ``MASSIVE_API_KEY`` (the off switch), ``ALPHADECK_TAPE_CROSSCHECK=
  false``, a session Massive.com has not served yet (HTTP 403 "before end of day"), a failed read: each is the
  block's ``status`` with a ``note``; nothing is judged and the pass goes on.

THE CLASSES (per judged company; ``tolerance`` = ``Settings.tape_crosscheck_tolerance``, 0.02):

- ``disagree``: both have a bar for the session, |ln(ours / massive)| > tolerance AND |ours - massive| > 0.01 (a
  sub-penny OTC print never disagrees: HCMC 0.0001 vs 0.000001). MEASURED: 192 of 195 (name, session) pairs agree
  within 0.001%; MARA 5.09 vs 5.085 (Yahoo's official close vs Massive.com's last trade, 0.098%) agrees.
  ``split_shaped`` = the ratio within ``domain.splits.TAPE_TOLERANCE`` (in log space) of a listed split's factor
  (either way) whose factor is at least ``TAPE_K_MIN`` either way — a tape on the wrong split basis.
- ``vendor_gap``: Massive.com traded it (volume > 0) and the store has no bar for the session — the SNDK / DFSC
  shape, and the OTC late closes (15 of 35 missing on 11-24 had a Massive.com bar).
- ``no_trade``: we have a bar and Massive.com has no trade — a vendor placeholder on a halt (GNS 11-10..11-19:
  Yahoo 0.101 on volume 0) or a symbol Massive.com writes differently. Never a disagreement.
- ``unmapped``: no symbol to look up (no ticker, no vendor price symbol).

Massive.com's bar is AS TRADED; the store is on the split list's scale. The judge puts the raw close on the
store's scale (``domain.splits.scale_after(listed, session, through=asof)``). The session is tonight's, so no
split in force has a first session after it and the factor is 1 by construction; the step is kept so a test
pins it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from fractions import Fraction
from time import perf_counter
from typing import Any
from uuid import UUID

import psycopg

from domain.feed_kinds import PRICE, feed_kind
from domain.settings import get_settings
from domain.splits import (
    LIVE_STATUSES,
    STATUS_NEEDS_REVIEW,
    TAPE_K_MIN,
    TAPE_TOLERANCE,
    ListedSplit,
    listed_from_row,
    scale_after,
)
from ingest import CacheMiss
from ingest.massive_bars import (
    GroupedDay,
    MassiveBarsError,
    NotServedYet,
    default_bar_source,
    massive_symbol,
)
from pipeline.missing_closes import candidates_of, fmt_day

STATUS_ON = "on"
STATUS_OFF = "off"
STATUS_NOT_SERVED = "not_served"
STATUS_UNCACHED = "uncached"
STATUS_FAILED = "failed"
STATUS_NOT_RUN = "not_run"

# A disagreement must also be worth a cent: a sub-penny OTC print (HCMC 0.0001 vs 0.000001) is not one.
PRICE_FLOOR = 0.01
# The splits that can explain a step: the live list (a set-aside split is not a split).
_SHAPED_STATUSES = (*LIVE_STATUSES, STATUS_NEEDS_REVIEW)
_SHOWN = 8  # names on one line before "+N more"
_ERROR_CLIP = 200


# --- the inputs -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Name:
    """One company as this check sees it: the master ticker, the vendor price symbol (``None`` = the ticker) and,
    for a stale row, its tape edge."""

    security_id: UUID
    ticker: str | None
    symbol: str | None = None
    edge: date | None = None

    @property
    def massive(self) -> str | None:
        """The symbol Massive.com lists it under (the vendor symbol, a class share's dash a dot)."""
        return massive_symbol(self.symbol or self.ticker)

    @property
    def label(self) -> str:
        return self.ticker or str(self.security_id)


@dataclass(frozen=True)
class GroupedFetch:
    """The pass's ONE grouped read (``fetch_grouped``): the day, or why there is none (``status`` / ``note``)."""

    session: date
    status: str
    note: str | None = None
    day: GroupedDay | None = None
    duration_s: float = 0.0


# --- the outcome ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossRow:
    """One judged company the check names: ``ours`` = the session's newest stored close, ``massive`` =
    Massive.com's raw close on the store's scale, ``ratio`` = ours / massive, ``volume`` = Massive.com's.
    """

    security_id: UUID
    ticker: str | None
    symbol: str | None
    ours: float | None = None
    massive: float | None = None
    volume: float | None = None
    ratio: float | None = None
    split_shaped: bool = False
    split: str | None = None
    otc: bool = False

    @property
    def label(self) -> str:
        return self.ticker or str(self.security_id)

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "symbol": self.symbol,
            "ours": self.ours,
            "massive": self.massive,
            "volume": self.volume,
            "ratio": self.ratio,
            "split_shaped": self.split_shaped,
            "split": self.split,
            "otc": self.otc,
        }


@dataclass(frozen=True)
class StaleTraded:
    """An open stale PRICE row whose symbol traded on Massive.com tonight: a vendor gap, not a rename. ``split``
    = the newest listed split after the tape's edge (``split_status`` its status), the usual reason.
    """

    security_id: UUID
    ticker: str | None
    symbol: str | None
    edge: date | None
    session: date
    close: float
    volume: float
    split: str | None = None
    split_status: str | None = None

    @property
    def line(self) -> str:
        return stale_line(self.to_json())

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "symbol": self.symbol,
            "edge": self.edge.isoformat() if self.edge else None,
            "session": self.session.isoformat(),
            "close": self.close,
            "volume": self.volume,
            "split": self.split,
            "split_status": self.split_status,
        }


@dataclass
class TapeCrossCheck:
    """One pass's cross-check — the run result's block and the artifact's ``tape_crosscheck``.

    ``evaluated`` = the judge ran (a live pass, a grouped day, no fault); ``status`` / ``note`` = the grouped
    read (``on`` / ``off`` / ``not_served`` / ``uncached`` / ``failed`` / ``not_run``); ``error`` = the
    check's own fault (the DB read, the judge). ``judged`` = the companies judged, ``compared`` = those with a
    bar on both sides."""

    evaluated: bool = False
    status: str = STATUS_NOT_RUN
    note: str | None = None
    error: str | None = None
    session: date | None = None
    massive_status: str | None = None
    massive_rows: int = 0
    massive_skipped: int = 0
    live: bool = False
    tolerance: float | None = None
    judged: int = 0
    compared: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    disagree: tuple[CrossRow, ...] = ()
    vendor_gap: tuple[CrossRow, ...] = ()
    no_trade: tuple[CrossRow, ...] = ()
    unmapped: tuple[CrossRow, ...] = ()
    stale_traded: tuple[StaleTraded, ...] = ()
    duration_s: float | None = None

    def payload(self) -> dict:
        """The artifact block (``cron_run_log.build_run_payload``). Readers use ``.get`` throughout: an artifact
        written before this key existed carries none and reads as "not checked"."""
        return {
            "tape_crosscheck": {
                "evaluated": self.evaluated,
                "status": self.status,
                "note": self.note,
                "error": self.error,
                "session": self.session.isoformat() if self.session else None,
                "massive_status": self.massive_status,
                "massive_rows": self.massive_rows,
                "massive_skipped": self.massive_skipped,
                "live": self.live,
                "tolerance": self.tolerance,
                "judged": self.judged,
                "compared": self.compared,
                "excluded": dict(self.excluded),
                "disagree": [r.to_json() for r in self.disagree],
                "vendor_gap": [r.to_json() for r in self.vendor_gap],
                "no_trade": [r.to_json() for r in self.no_trade],
                "unmapped": [r.to_json() for r in self.unmapped],
                "stale_traded": [s.to_json() for s in self.stale_traded],
                "duration_s": self.duration_s,
            }
        }


# --- the names off the pass's results (pure) ------------------------------------------------------------------


def names_of(results: Iterable[Any]) -> tuple[list[Name], list[Name], dict[str, int]]:
    """``(judged, stale, excluded)`` off the per-thesis results. ``judged`` = the companies the missing-close check
    judges (``candidates_of``: live basket members walked tonight with a tape; stale, onboarding and tape-less ones
    excluded and counted); ``stale`` = each open (not closed) stale PRICE row, once per company, with its edge. The
    vendor symbol is the master's ``price_symbol`` the price leg read (``None`` = the ticker)."""
    results = list(results)
    candidates, excluded, _ = candidates_of(results)
    symbols: dict[UUID, str | None] = {}
    for r in results:
        for x in getattr(r, "ingested", ()) or ():
            sid = getattr(x, "security_id", None)
            if sid is not None and getattr(x, "price_symbol", None):
                symbols[sid] = x.price_symbol
    judged = [Name(sid, c.ticker, symbols.get(sid)) for sid, c in candidates.items()]
    stale: list[Name] = []
    seen: set[UUID] = set()
    for r in results:
        for s in getattr(r, "tape_stale", ()) or ():
            if feed_kind(getattr(s, "kind", None)).key != PRICE.key or s.closed_at is not None:
                continue
            if s.security_id in seen:
                continue
            seen.add(s.security_id)
            stale.append(Name(s.security_id, s.ticker, symbols.get(s.security_id), edge=s.edge))
    return judged, stale, dict(excluded)


# --- the judgment (pure) --------------------------------------------------------------------------------------


def _scaled(close: Decimal, k: Fraction) -> Decimal:
    return close if k == 1 else close * Decimal(k.numerator) / Decimal(k.denominator)


def _split_words(s: ListedSplit) -> str:
    return f"{s.label} of {s.effective_date.isoformat()}"


def _shaped(ratio: float, splits: Sequence[ListedSplit]) -> ListedSplit | None:
    """The listed split whose factor (either way) the ratio sits within ``TAPE_TOLERANCE`` of, in log space —
    the nearest when several do; only splits of at least ``TAPE_K_MIN`` either way (a small distribution would
    make every ordinary disagreement look split-shaped)."""
    if ratio <= 0:
        return None
    best: tuple[float, ListedSplit] | None = None
    for s in splits:
        if s.status not in _SHAPED_STATUSES:
            continue
        lk = math.log(float(s.factor))
        if abs(lk) < math.log(TAPE_K_MIN):
            continue
        dist = min(abs(math.log(ratio) - lk), abs(math.log(ratio) + lk))
        if dist <= TAPE_TOLERANCE and (best is None or dist < best[0]):
            best = (dist, s)
    return best[1] if best else None


def _gap_split(
    splits: Sequence[ListedSplit], *, edge: date | None, through: date
) -> ListedSplit | None:
    """The newest live listed split whose first session falls after the tape's edge (any, for a never-priced
    tape) and on or before ``through`` — the usual reason a vendor stopped serving closes."""
    hits = [
        s
        for s in splits
        if s.status in _SHAPED_STATUSES
        and (edge is None or s.effective_date > edge)
        and s.effective_date <= through
    ]
    return max(hits, key=lambda s: (s.effective_date, s.key_date)) if hits else None


def _order(r: CrossRow | StaleTraded) -> tuple[str, str]:
    return ((r.ticker or "").upper(), str(r.security_id))


def judge(
    *,
    session: date,
    asof: date,
    judged: Sequence[Name],
    stale: Sequence[Name],
    stored: Mapping[UUID, Decimal | float],
    massive: GroupedDay,
    listed: Mapping[UUID, Sequence[ListedSplit]],
    tolerance: float,
) -> TapeCrossCheck:
    """THE RULE (module docstring), pure over its inputs. ``stored`` = the session's newest stored close per
    judged company (absent = no bar); ``massive`` = the session's grouped day; ``listed`` = each company's split
    list; ``asof`` = the pass's as-of (the scale the store is on)."""
    out = TapeCrossCheck(
        evaluated=True,
        status=STATUS_ON,
        session=session,
        massive_status=massive.status,
        massive_rows=len(massive.bars),
        massive_skipped=massive.skipped,
        live=massive.live,
        tolerance=tolerance,
    )
    if (
        not massive.bars
    ):  # a holiday (a Saturday answered 200 with 0 rows): nothing to judge against
        return out
    disagree: list[CrossRow] = []
    gaps: list[CrossRow] = []
    no_trade: list[CrossRow] = []
    unmapped: list[CrossRow] = []
    compared = 0
    for n in judged:
        splits = list(listed.get(n.security_id, ()))
        sym = n.massive
        ours_d = stored.get(n.security_id)
        ours = float(ours_d) if ours_d is not None else None
        if sym is None:
            unmapped.append(CrossRow(n.security_id, n.ticker, None, ours=ours))
            continue
        bar = massive.get(sym)
        traded = bar is not None and bar.volume > 0
        if not traded:
            if ours is not None:
                no_trade.append(CrossRow(n.security_id, n.ticker, sym, ours=ours))
            continue
        assert bar is not None
        theirs = float(_scaled(bar.close, scale_after(splits, session, through=asof)))
        row = CrossRow(
            n.security_id, n.ticker, sym, massive=theirs, volume=float(bar.volume), otc=bar.otc
        )
        if ours is None:
            gaps.append(row)
            continue
        compared += 1
        ratio = ours / theirs if ours > 0 else None
        off = ratio is None or abs(math.log(ratio)) > tolerance
        if off and abs(ours - theirs) > PRICE_FLOOR:
            hit = _shaped(ratio, splits) if ratio is not None else None
            disagree.append(
                CrossRow(
                    n.security_id,
                    n.ticker,
                    sym,
                    ours=ours,
                    massive=theirs,
                    volume=float(bar.volume),
                    ratio=ratio,
                    split_shaped=hit is not None,
                    split=_split_words(hit) if hit else None,
                    otc=bar.otc,
                )
            )
    traded_stale: list[StaleTraded] = []
    for n in stale:
        bar = massive.get(n.massive)
        if bar is None or bar.volume <= 0:
            continue
        splits = list(listed.get(n.security_id, ()))
        why = _gap_split(splits, edge=n.edge, through=asof)
        traded_stale.append(
            StaleTraded(
                n.security_id,
                n.ticker,
                n.massive,
                n.edge,
                session,
                close=float(_scaled(bar.close, scale_after(splits, session, through=asof))),
                volume=float(bar.volume),
                split=_split_words(why) if why else None,
                split_status=why.status if why else None,
            )
        )
    out.judged = len(judged)
    out.compared = compared
    out.disagree = tuple(sorted(disagree, key=_order))
    out.vendor_gap = tuple(sorted(gaps, key=_order))
    out.no_trade = tuple(sorted(no_trade, key=_order))
    out.unmapped = tuple(sorted(unmapped, key=_order))
    out.stale_traded = tuple(sorted(traded_stale, key=_order))
    return out


# --- the fetch (once a pass, before the per-thesis loop) and the nightly unit ----------------------------------


def _fault(e: BaseException) -> str:
    msg = " ".join(str(e).split())[:_ERROR_CLIP]
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def fetch_grouped(
    *,
    asof: date,
    allow_live: bool,
    enabled: bool | None = None,
    source: Any = None,
    clock: Callable[[], float] = perf_counter,
) -> GroupedFetch:
    """The pass's ONE grouped read for session ``asof`` (one request; a cache hit makes none). FAIL-OPEN: off
    (``enabled`` False — ``Settings.tape_crosscheck`` when None — or no key), not served yet, uncached on a
    ``--no-live`` pass, or failed is a ``status`` with a ``note``, never an exception into the pass.
    """
    t0 = clock()
    try:
        if enabled is None:
            enabled = get_settings().tape_crosscheck
    except Exception as e:  # noqa: BLE001 — a settings fault never costs the pass
        return GroupedFetch(asof, STATUS_FAILED, _fault(e))
    if not enabled:
        return GroupedFetch(asof, STATUS_OFF, "ALPHADECK_TAPE_CROSSCHECK=false")
    try:
        src = source if source is not None else default_bar_source()
        if src is None:
            return GroupedFetch(asof, STATUS_OFF, "no MASSIVE_API_KEY")
        day = src.day(asof, allow_live=allow_live)
    except NotServedYet as e:
        status, note, day = STATUS_NOT_SERVED, str(e)[:_ERROR_CLIP], None
    except CacheMiss:
        status, note, day = STATUS_UNCACHED, "not cached (a --no-live pass)", None
    except MassiveBarsError as e:
        status, note, day = STATUS_FAILED, str(e)[:_ERROR_CLIP], None
    except Exception as e:  # noqa: BLE001 — a source fault never costs the pass
        status, note, day = STATUS_FAILED, _fault(e), None
    else:
        status, note = STATUS_ON, None
    return GroupedFetch(asof, status, note, day, duration_s=round(clock() - t0, 3))


# the session's NEWEST stored version per company (the ``stored_bars`` pick: newest ``recorded_at`` wins)
_STORED_SQL = """
    SELECT DISTINCT ON (security_id) security_id, close
      FROM fact_price_eod
     WHERE tenant_id = %(tenant_id)s AND security_id = ANY(%(ids)s) AND d = %(d)s
     ORDER BY security_id, recorded_at DESC
"""


def read_facts(
    conn: psycopg.Connection,
    *,
    session: date,
    tenant_id: UUID,
    judged: Sequence[UUID],
    listed_for: Sequence[UUID],
) -> tuple[dict[UUID, Decimal], dict[UUID, list[ListedSplit]]]:
    """``(stored, listed)`` — SELECTs only (transaction control is the caller's): the session's newest stored
    close per judged company, and the split list (the newest version of each split) of every named company,
    in one batched read."""
    from repositories import split_repo  # lazy: keep the monitor's import graph narrow

    stored: dict[UUID, Decimal] = {}
    if judged:
        with conn.cursor() as cur:
            cur.execute(_STORED_SQL, {"tenant_id": tenant_id, "ids": list(judged), "d": session})
            for r in cur.fetchall():
                if r["close"] is not None:
                    stored[r["security_id"]] = Decimal(str(r["close"]))
    listed: dict[UUID, list[ListedSplit]] = {}
    if listed_for:
        for v in split_repo.latest_versions(
            conn, tenant_id=tenant_id, security_ids=list(listed_for)
        ):
            listed.setdefault(v.security_id, []).append(listed_from_row(v.model_dump()))
    return stored, listed


def run_tape_crosscheck(
    *,
    results: Sequence[Any],
    asof: date,
    allow_live: bool,
    grouped: GroupedFetch,
    tenant_id: UUID,
    connect: Callable[[], psycopg.Connection],
    clock: Callable[[], float],
    tolerance: float,
) -> TapeCrossCheck:
    """The nightly unit, after the per-thesis loop: judge tonight's session from ``grouped`` (fetched once
    before the loop) and one ``read_only`` connection. A ``--no-live`` pass does not evaluate (its tape is a
    cache read, not tonight's feed); no grouped day is the fetch's status, judged nothing; any fault returns
    ``evaluated=False`` with the ``error`` — never an exception into the cron. Nothing to judge opens no
    connection."""
    if not allow_live:
        return TapeCrossCheck(status=STATUS_NOT_RUN, note="a --no-live pass")
    base = TapeCrossCheck(
        session=asof, status=grouped.status, note=grouped.note, tolerance=tolerance
    )
    if grouped.day is None:
        base.duration_s = grouped.duration_s
        return base
    t0 = clock()
    try:
        judged, stale, excluded = names_of(results)
        ids = [n.security_id for n in judged]
        named = sorted({*ids, *(n.security_id for n in stale)})
        stored: dict[UUID, Decimal] = {}
        listed: dict[UUID, list[ListedSplit]] = {}
        if named:
            conn = connect()
            try:
                conn.read_only = True  # the monitor reads; structurally it cannot write a row
                stored, listed = read_facts(
                    conn, session=asof, tenant_id=tenant_id, judged=ids, listed_for=named
                )
                conn.rollback()  # end the read-only transaction; nothing to keep
            finally:
                conn.close()
        out = judge(
            session=asof,
            asof=asof,
            judged=judged,
            stale=stale,
            stored=stored,
            massive=grouped.day,
            listed=listed,
            tolerance=tolerance,
        )
        out.excluded = excluded
    except Exception as e:  # noqa: BLE001 — a monitor fault is reported, never a failed pass
        base.error = _fault(e)
        base.duration_s = round(grouped.duration_s + clock() - t0, 3)
        return base
    out.duration_s = round(grouped.duration_s + clock() - t0, 3)
    return out


# --- the words (the cron log + the Admin line): ONE builder each, over the outcome or its artifact block ------


def _block(cc: TapeCrossCheck | Mapping) -> Mapping:
    return cc.payload()["tape_crosscheck"] if isinstance(cc, TapeCrossCheck) else cc


def _day(v: object) -> date | None:
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def fmt_price(v: object) -> str:
    """A close as its plain decimal digits (73.61, 1.5542, 0.000001) — never rounded away, never ``1e-06``."""
    try:
        d = Decimal(repr(float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    return format(d.normalize(), "f")


def _rows(block: Mapping, key: str) -> list[Mapping]:
    return [r for r in (block.get(key) or ()) if isinstance(r, Mapping)]


def _label(r: Mapping) -> str:
    t = r.get("ticker")
    return t if isinstance(t, str) and t else str(r.get("security_id"))


def _names(rows: list[Mapping], value: Callable[[Mapping], str]) -> str:
    shown = ", ".join(f"{_label(r)} {value(r)}" for r in rows[:_SHOWN])
    more = f", +{len(rows) - _SHOWN} more" if len(rows) > _SHOWN else ""
    return shown + more


def quiet_line(cc: TapeCrossCheck | Mapping) -> str:
    """The ONE nightly line, from the outcome or its artifact block — e.g. "Massive.com cross-check · Nov 25:
    1,742 compared, 0 disagree; Massive.com traded 2 we have no close for: FNUC 5.28, AITX 0.0002".
    """
    b = _block(cc)
    session = _day(b.get("session"))
    day = fmt_day(session) if session else "the session"
    head = "Massive.com cross-check ·"
    if b.get("error"):
        return f"{head} not checked — {b['error']}"
    status = b.get("status")
    if not b.get("evaluated"):
        if status == STATUS_OFF:
            return f"{head} off ({b.get('note') or 'no MASSIVE_API_KEY'})"
        if status == STATUS_NOT_SERVED:
            return (
                f"{head} not run — Massive.com had not served {day} yet (it answers after the session's "
                "end of day)"
            )
        if status == STATUS_FAILED:
            return f"{head} not run — the Massive.com read failed: {b.get('note') or 'unknown'}"
        return f"{head} not run — {b.get('note') or status or 'not run'}"
    if not int(b.get("massive_rows") or 0):
        return f"{head} {day} not judged — Massive.com lists no bars for it"
    disagree = _rows(b, "disagree")
    shaped = sum(1 for r in disagree if r.get("split_shaped"))
    parts = [
        f"{day}: {int(b.get('compared') or 0):,} compared, {len(disagree)} disagree"
        + (f" ({shaped} split-shaped)" if shaped else "")
    ]
    gaps = _rows(b, "vendor_gap")
    if gaps:
        parts.append(
            f"Massive.com traded {len(gaps)} we have no close for: "
            + _names(gaps, lambda r: fmt_price(r.get("massive")))
        )
    if no_trade := _rows(b, "no_trade"):
        parts.append(f"{len(no_trade)} with no Massive.com trade")
    if unmapped := _rows(b, "unmapped"):
        parts.append(f"{len(unmapped)} with no Massive.com symbol")
    return f"{head} " + "; ".join(parts)


def disagree_line(cc: TapeCrossCheck | Mapping) -> str | None:
    """The disagreements named ("SNDK 633.92 stored vs 79.24 (x8, split-shaped: ...)"), or ``None``."""
    rows = _rows(_block(cc), "disagree")
    if not rows:
        return None

    def what(r: Mapping) -> str:
        ratio = r.get("ratio")
        x = f"x{float(ratio):.4g}" if isinstance(ratio, (int, float)) else "x?"
        shaped = f", split-shaped: {r.get('split')}" if r.get("split_shaped") else ""
        return f"{fmt_price(r.get('ours'))} stored vs {fmt_price(r.get('massive'))} ({x}{shaped})"

    return "Massive.com cross-check · disagrees: " + _names(rows, what)


def stale_line(row: Mapping) -> str:
    """One open stale row that traded tonight: "SNDK: no close stored since 2026-11-19, but Massive.com shows
    it traded tonight (73.61) — a vendor gap, not a rename; the 8-for-1 forward split of 2026-11-20 is on the
    list"."""
    edge = row.get("edge")
    since = f"no close stored since {edge}" if edge else "no close ever stored"
    line = (
        f"{_label(row)}: {since}, but Massive.com shows it traded tonight ({fmt_price(row.get('close'))}) "
        "— a vendor gap, not a rename"
    )
    if row.get("split"):
        review = " (needs review)" if row.get("split_status") == STATUS_NEEDS_REVIEW else ""
        line += f"; the {row['split']} is on the list{review}"
    return line


def stale_lines(cc: TapeCrossCheck | Mapping) -> list[str]:
    return [stale_line(r) for r in _rows(_block(cc), "stale_traded")]


def report_lines(cc: TapeCrossCheck) -> list[str]:
    """The cron's printed block (``pipeline.daily.main``): the quiet line, the disagreements named, one line per
    stale row that traded tonight; a failed check loud; nothing on a ``--no-live`` pass. Never a page.
    """
    if cc.error:
        return [f"TAPE CROSS-CHECK FAILED — {cc.error} (tonight's tape was not cross-checked)"]
    if cc.status == STATUS_NOT_RUN and not cc.evaluated:
        return []
    out = [quiet_line(cc)]
    if line := disagree_line(cc):
        out.append(line)
    out.extend(stale_lines(cc))
    return out
