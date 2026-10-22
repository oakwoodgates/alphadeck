"""The split-night HOLD at the call seam (split plan S5, operator-approved 2026-10-22; ``docs/CALL_LOGIC.md`` §2b).

The rule itself is ``domain/split_hold.py`` (pure). This module is its point-in-time half, used ONLY by
``pipeline.core.assemble_from_pit`` — the one seam the nightly, the serve path, ``pipeline.backfill``, D3b's
``annotate_phantom_arms`` and the replay harness all assemble through, so the hold applies identically
everywhere (the live view on the record clock, a history view on the public clock, the Parquet mirror).

- ``find_holds`` reads each basket member's newest bars and the split list AS THE VIEW KNOWS THEM
  (``price_history`` / ``split_events``: no lookahead, #1 — a history view sees only the splits public by its
  night) and returns the members whose price-path triggers are held tonight.
- ``HeldPriceView`` is the view a held member's price-path triggers read: its bars BEFORE the step only, so a
  trigger that reads across the step cannot fire, and one wholly before it still stands (a breakout three
  sessions before the split keeps arming on its own clock). Every other read passes through untouched.
- ``notes`` renders the ``missing[]`` lines (held is never silent, #6).

Which detectors are price-path triggers is a PROPERTY each declares (``Detector.price_trigger`` — its name on
the card), never a kind branch; ``tests/signals/test_split_hold.py`` fails a registered detector that reads
the price path without declaring one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from domain.config import CallConfig
from domain.signal import SignalEvent
from domain.split_hold import Hold, hold_for, join, note
from domain.thesis import Thesis

# How far back the hold reads a member's bars: the step's prior bar must sit inside it. Three sessions plus a
# prior bar is at most a week of calendar days; a month leaves room for a missing bar or a holiday gap.
HOLD_LOOKBACK_DAYS = 30

# the laggard rotation's name on the card when its reading is held (a thesis-level reader, not a Detector)
LAGGARD_TRIGGER = "Laggard confirmation"
# the line's subject when the hold withheld no trigger (every open hold still has its line)
PRICE_TRIGGERS = "Price triggers"

# The registered detectors that read ``fact_price_eod`` WITHOUT being price-path triggers: the two insider
# detectors read a Form 4's day low to screen an off-market price (on the split list's scale since S2) — they
# fire on the FILING. Every other price reader must declare ``Detector.price_trigger`` (the registry guard in
# ``tests/signals/test_split_hold.py``), so a new price-path detector cannot read across a step unheld.
NOT_PRICE_PATH = frozenset({"insider_conviction", "insider_sell"})


def HORIZONS(
    cfg: CallConfig,
) -> dict[str, int | None]:  # noqa: N802 — the registry's module-level name
    """The READ-HORIZON declaration (``signals/horizons.py``) for this THESIS-LEVEL reader: exactly the
    lookback ``find_holds`` passes to ``price_history``."""
    return {"fact_price_eod": HOLD_LOOKBACK_DAYS}


def find_holds(pit: Any, thesis: Thesis, asof: date) -> dict[UUID, Hold]:
    """The basket members held tonight (``domain.split_hold.hold_for``), keyed by security id. Reads only
    ``price_history`` + ``split_events`` — the view's own tape and split list (#1)."""
    out: dict[UUID, Hold] = {}
    seen: set[UUID] = set()
    for member in thesis.basket:
        sid = member.security_id
        if sid is None or sid in seen:
            continue
        seen.add(sid)
        bars = pit.price_history(sid, lookback_days=HOLD_LOOKBACK_DAYS)
        hold = hold_for(bars, pit.split_events(sid), asof=asof)
        if hold is not None:
            out[sid] = hold
    return out


class HeldPriceView:
    """The point-in-time view a HELD member's price-path triggers read: ``price_history`` for a held security
    returns only its bars BEFORE the step (``Hold.cut``); everything else — every other security, every other
    accessor, ``asof`` / ``known_at`` / ``tenant_id`` — is the wrapped view's, untouched."""

    def __init__(self, inner: Any, holds: Mapping[UUID, Hold]) -> None:
        self._inner = inner
        self._cuts = {sid: h.cut for sid, h in holds.items()}

    @property
    def asof(self) -> date:
        return self._inner.asof

    @property
    def known_at(self) -> Any:
        return self._inner.known_at

    @property
    def tenant_id(self) -> UUID:
        return self._inner.tenant_id

    def price_history(
        self, security_id: UUID, lookback_days: int | None = None
    ) -> list[dict[str, Any]]:
        bars = self._inner.price_history(security_id, lookback_days)
        cut = self._cuts.get(security_id)
        return bars if cut is None else [b for b in bars if b["d"] < cut]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def identity(event: SignalEvent | None) -> tuple[Any, ...] | None:
    """WHICH trigger an event is — role, kind, grade, de-arm grade and fire date — leaving out its score and
    label (a reading of the same trigger). A trigger is WITHHELD when the full tape's identity is not what the
    held view reads; the same trigger re-read on the bars before the step (another close in its label) is
    the reading held, not a trigger withheld."""
    if event is None:
        return None
    return (event.role, event.kind, event.grade, event.dearm_grade, event.asof)


def notes(
    thesis: Thesis, holds: Mapping[UUID, Hold], held: Mapping[UUID, Sequence[str]]
) -> list[str]:
    """The ``missing[]`` lines, in basket order: one per HELD member, every night its hold is open — "Breakout on
    BTOC held: possible unconfirmed 1-for-16 reverse split (SEC 8-K filed 10-09; waiting for a second source)".
    ``held[sid]`` names the triggers WITHHELD there; empty means none was (nothing read across the step fired,
    or a trigger was only re-read on the bars before it), and the line then reads "Price triggers on ...".
    Then ONE line for the co-members whose laggard confirmation was
    withheld because it read a held member's price (a fake breakout as the leader cue, a fake return in the
    basket median): "Laggard confirmation on ALAB and ARM held: it reads the price of SMTK, held for a possible
    unconfirmed split"."""
    tickers: dict[UUID, str] = {}
    for m in thesis.basket:
        if m.security_id is not None:
            tickers.setdefault(m.security_id, m.ticker)
    out: list[str] = []
    readers: list[str] = []
    for sid, ticker in tickers.items():
        if sid not in held:
            continue
        if sid in holds:
            out.append(note(list(held[sid]) or [PRICE_TRIGGERS], ticker, holds[sid].reason))
        elif held[sid]:
            readers.append(ticker)
    if readers:
        sources = join([tickers[s] for s in tickers if s in holds])
        out.append(
            f"{LAGGARD_TRIGGER} on {join(readers)} held: it reads the price of {sources}, held for a "
            "possible unconfirmed split"
        )
    return out
