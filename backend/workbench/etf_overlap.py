"""Basket-overlap classification for an ETF's N-PORT holdings (ETF Sleeve, Slice 2a). PURE — no I/O.

Partitions the fund's holdings against the operator's world into exactly three buckets:
- ``held``       — matched to a master row that IS in this thesis's basket (the ETF confirms a pick);
- ``available``  — matched to a master row NOT in the basket (an includable discovery candidate —
                   the include *button* is Slice 2b; here it is display);
- ``unresolved`` — no master match. SHOWN, never dropped (#9): a surfaced holding is prunable by the
                   operator, a dropped one is invisible. With the measured identifier coverage (some
                   filers put no ticker on any equity holding), this bucket legitimately DOMINATES for
                   some funds until the CUSIP→ticker upgrade (2b) — honest sparsity, not a bug.

The match key is the N-PORT ``ticker`` identifier against the master's ticker map — NEVER the master
``cusip`` column (effectively always NULL: OpenFIGI doesn't return CUSIPs, so a CUSIP join silently
returns nothing — the #9 trap the plan pins). Matching is EXACT (INVARIANT #2 — never a fuzzy
name-guess).

Display/context only: nothing here writes, nothing reaches ``calls/`` (#4/#6 — a holding never fires,
arms, or vetoes anything).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from ingest.edgar.nport import Holding


@dataclass(frozen=True)
class OverlapResult:
    """The three-way partition, each bucket sorted by ``pct_val`` desc (heaviest weight first; a
    weightless holding sorts last, never hides). ``held``/``available`` carry the matched security id;
    ``unresolved`` by type carries none."""

    held: list[tuple[Holding, UUID]] = field(default_factory=list)
    available: list[tuple[Holding, UUID]] = field(default_factory=list)
    unresolved: list[Holding] = field(default_factory=list)


def _by_weight_desc(h: Holding) -> tuple[int, float]:
    # None-weight sorts AFTER any real weight (shown last, still shown — #9)
    return (0, -h.pct_val) if h.pct_val is not None else (1, 0.0)


def classify(
    holdings: list[Holding],
    master_ids: dict[str, UUID],
    basket_sids: set[UUID],
) -> OverlapResult:
    """Place every holding in EXACTLY ONE bucket — the #9 recall bound is structural (asserted below:
    the buckets always sum to the input; nothing can be silently dropped).

    ``master_ids`` is ticker→security_id (``master.ids_for_tickers`` — already upper-keyed);
    ``basket_sids`` the thesis's bound member ids. A holding with no ticker identifier (the measured
    Global X shape) can never match and lands in ``unresolved`` — visible, prunable, honest.
    """
    held: list[tuple[Holding, UUID]] = []
    available: list[tuple[Holding, UUID]] = []
    unresolved: list[Holding] = []
    for h in holdings:
        sid = master_ids.get(h.ticker.upper()) if h.ticker else None
        if sid is None:
            unresolved.append(h)
        elif sid in basket_sids:
            held.append((h, sid))
        else:
            available.append((h, sid))
    # #9, structural: every holding in exactly one bucket — the partition can't lose a name.
    assert len(held) + len(available) + len(unresolved) == len(holdings)
    return OverlapResult(
        held=sorted(held, key=lambda t: _by_weight_desc(t[0])),
        available=sorted(available, key=lambda t: _by_weight_desc(t[0])),
        unresolved=sorted(unresolved, key=_by_weight_desc),
    )
