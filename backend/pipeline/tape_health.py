"""The price-tape RECENCY rule (G5a) — "has this name's tape silently ended?"

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
  discipline applied to a monitor — time is never ambient). The FACT it judges (a name's latest stored bar
  date) is read in ``pipeline/ingest_thesis.py`` and carried on ``NameResult.tape_edge``; the JUDGMENT is
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

The REPAIR is the operator's and is not code: ``security_master.price_symbol`` (the OTC symbol override,
#252) points the price leg at the vendor's current symbol, and the next nightly pass re-pulls the full
year, appends the missing tail and hole-fills the overlap. Extending the symbol resolver to renames
automatically is a separate, unbuilt decision: a wrong auto-resolve would file ANOTHER company's tape under
this member, which is worse than a visible gap (#4/#6). See ``docs/ADMIN.md`` + ``docs/DATA_SOURCES.md``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

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
    """One name whose price tape has gone stale: its ``edge`` (the latest stored bar date, ``None`` when
    the tape has NO bars at all) plus the identity needed to render and to diff.

    ``security_id`` is the diff KEY — never the ticker: a ticker-less name must still be able to page, and a
    ticker can change under a name (which is half of why this monitor exists)."""

    ticker: str | None
    security_id: UUID
    edge: date | None

    @property
    def label(self) -> str:
        return stale_label(self.ticker, self.security_id)


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


def stale_tapes(
    results: Iterable[NameResult], *, asof: date, stale_days: int
) -> tuple[StaleTape, ...]:
    """The stale tapes among one thesis's per-name ingest results, FIRST-SEEN order, **deduplicated by
    ``security_id``**.

    The dedup is load-bearing, not tidiness: a name the draft placed in N value-chain links is N
    ``basket_member`` rows with the SAME ``security_id``, so ``ingest_thesis`` returns N ``NameResult``s for
    it (the later walks append nothing — incremental) and a naive count would report one dead tape N times,
    on the page and in the panel. Same rule as the on-promote ingest summary's ``members`` count.

    A name whose price leg ERRORED is still judged: the edge read is independent of the leg's outcome, so a
    name that is both failing and dead shows up as both (the error on its own result, the stale tape here) —
    never silently one or the other."""
    out: list[StaleTape] = []
    seen: set[UUID] = set()
    for r in results:
        if r.security_id in seen:
            continue
        seen.add(r.security_id)
        if is_tape_stale(r.tape_edge, asof=asof, stale_days=stale_days):
            out.append(StaleTape(ticker=r.ticker, security_id=r.security_id, edge=r.tape_edge))
    return tuple(out)
