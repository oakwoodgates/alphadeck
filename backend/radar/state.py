"""The SPAC deal-state machine — a pure, golden-tested READ-TIME derive over a CIK's event history.

State is never stored (0030 keeps facts only: form + items + dates), so these rules can be retuned
with zero backfill. The v1 rules, from the options doc (Rev 2 — announcement and termination are a
PAIR; a dead deal must never read live):

- ``searching``   — events exist but none marks a deal (e.g. an extension proxy, or an 8-K whose
                    items are unknown/other — an unclassified 8-K contributes NOTHING to state).
- ``announced``   — a deal marker: 8-K item 1.01, or any of 425 / S-4 / DEFM14A / PREM14A. For a
                    pre-close blank check an item 1.01 is overwhelmingly the combination agreement;
                    the approximation over-announces rather than misses (recall over precision) and
                    every row links its filing so the operator sees exactly what fired.
- ``terminated``  — 8-K item 1.02 while announced. A LATER deal marker re-announces (deal #2).
- ``completed``   — 8-K item 2.01 (completion of acquisition). TERMINAL: the shell became an
                    operating company; later agreement churn is ordinary-corporate, not deal state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

ANNOUNCE_FORMS = frozenset({"425", "S-4", "S-4/A", "DEFM14A", "PREM14A"})


@dataclass(frozen=True)
class StateEvent:
    """The minimal event shape the state walk reads (a projection of fact_spac_event)."""

    filed: date
    form: str
    items: tuple[str, ...] | None = None  # None = unknown (contributes nothing to state)
    accession: str = ""  # tie-breaker for same-day ordering (deterministic walks)


def deal_state(events: list[StateEvent]) -> str:
    """Fold a CIK's event history (any order) into its current deal state."""
    state = "searching"
    for ev in sorted(events, key=lambda e: (e.filed, e.accession)):
        items = set(ev.items or ())
        if ev.form.startswith("8-K"):
            if "2.01" in items:
                state = "completed"
            elif "1.01" in items and state != "completed":
                state = "announced"
            elif "1.02" in items and state == "announced":
                state = "terminated"
        elif ev.form in ANNOUNCE_FORMS and state != "completed":
            state = "announced"
    return state
