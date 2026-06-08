"""Curated, deterministic awardee → ticker resolution for the DOE/USASpending feed.

Entity resolution is **by hand and EXACT** (on the USASpending ``recipient_id`` hash), never fuzzy. The
spike proved why fuzzy matching is a trap:
- ``recipient_search_text="Centrus"`` also matches **NAC INTERNATIONAL INC.** (unrelated).
- ``recipient_search_text="Oklo"`` surfaces **OKLO TECHNOLOGIES, INC.** — a *different, polluted*
  recipient whose id carries **$48B of national-lab management contracts** (Sandia/LANL/ORNL ``DEAC…``).
  The real awardee is **OKLO INC.** (a distinct recipient_id).
- A company spans **several** recipient ids (subsidiary + parent), each holding different awards.

So a fuzzy search is only a **discovery net**; the ticker is assigned solely by membership in this table,
keyed on the exact ``recipient_id``. Subsidiary → parent → ticker is encoded by giving each recipient id
its own row pointing at the same ticker. Adding a name = add a row here (by hand), never a heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedAwardee:
    """One hand-verified USASpending recipient → ticker mapping (the exact resolution key)."""

    recipient_id: str  # USASpending recipient hash — the exact, stable resolution key
    recipient_name: str  # as it appears on USASpending (provenance / human cross-check)
    ticker: str  # the resolved security
    parent: str | None  # the public-company parent (the subsidiary → parent → ticker chain)
    uei: str | None = None  # SAM UEI, where known (a second human cross-check; not the key)


# Hand-curated. Row 1 is American Centrifuge Operating, LLC → Centrus → LEU (the operator's first row).
# Centrus holds DOE awards under BOTH the ACO LLC subsidiary (the $317M HALEU contract) and the parent
# Centrus Energy Corp (a separate ~$148M assistance award) — hence two rows, one ticker.
_AWARDEES: tuple[CuratedAwardee, ...] = (
    CuratedAwardee(
        recipient_id="d527144c-7fea-82ff-aff0-e95e5fd6e488-C",
        recipient_name="AMERICAN CENTRIFUGE OPERATING, LLC",
        ticker="LEU",
        parent="Centrus Energy Corp",
    ),
    CuratedAwardee(
        recipient_id="73df3ffc-1d75-bc33-6745-2739475d907d-C",
        recipient_name="CENTRUS ENERGY CORP.",
        ticker="LEU",
        parent="Centrus Energy Corp",
    ),
    CuratedAwardee(
        recipient_id="0bf298ad-ffe8-996a-d34e-70e1621fe8ee-R",
        recipient_name="OKLO INC.",
        ticker="OKLO",
        parent=None,
        uei="G44RGGAVDQL7",
    ),
)

_BY_ID: dict[str, CuratedAwardee] = {a.recipient_id: a for a in _AWARDEES}

# Discovery search terms — the fuzzy NET only. Resolution stays exact-by-recipient_id (``resolve`` below),
# so an over-matched recipient (NAC, OKLO TECHNOLOGIES) is dropped no matter what a term drags in.
SEARCH_TERMS: tuple[str, ...] = ("Centrus", "American Centrifuge", "Oklo")


def resolve(recipient_id: str | None) -> CuratedAwardee | None:
    """Exact, deterministic resolution. Returns the curated awardee, or ``None`` to **drop** the award —
    never a fuzzy guess. An unknown recipient_id (not hand-curated) is intentionally unresolved."""
    if not recipient_id:
        return None
    return _BY_ID.get(recipient_id)


def curated_tickers() -> frozenset[str]:
    """The set of tickers the feed can currently resolve (for logging / coverage checks)."""
    return frozenset(a.ticker for a in _AWARDEES)
