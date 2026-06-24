"""The EDGAR-first discovery orchestrator (Slice 4a) — the deterministic universe behind the chain draft.

One call, three demoted-LLM-and-deterministic steps, end to end:

1. **keyword-gen** (LLM, cheap, Slice 2) — narrative -> SIGNAL + BROAD EFTS keywords.
2. **EFTS enumerate** (FREE, deterministic, Slice 1) — ``discover`` unions the distinct CIKs across the
   keywords; the precision/keyword tiers stay attached for ``classify``.
3. **CIK -> placeable** (FREE, deterministic) — ``master.ids_for_ciks`` resolves each CIK to an EXACT in-master
   member (INVARIANT #2, the cleanest form), then ``classify`` splits PLACED (high-confidence) vs VERIFY
   (single-BROAD, lower-confidence) and omits the not-in-master tail (the LLM tail-sweep's job).

The OUTPUT is a ``DiscoveredUniverse``: the placeable CIKs as ``security_id``s by tier, plus the raw ``Filer``
map (name / ticker / keyword overlap) the chain reconciler matches the organizer's layout back against. This
layer OWNS COMPLETENESS — it deterministically finds the universe; the downstream organizer owns only LAYOUT,
and a per-CIK reconciliation guarantees no discovered name is ever silently dropped (``workbench.chain_draft``).

It sources NO number (#3) and only PROPOSES (#2 — exact CIK membership decides). **Fail-open by contract:** no
keywords (no key / blank narrative) -> an empty universe; any EFTS / DB trouble in the enumerate step -> an
empty universe too (the draft then degrades to the recall-only decompose — never a 5xx).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from ingest.edgar.fulltext import Filer, classify, discover
from llm.keyword_gen import generate_keywords
from securities import master


@dataclass
class DiscoveredUniverse:
    """The EDGAR-first discovered universe for a thesis. ``placed`` / ``verify`` are ``cik -> security_id`` (the
    placeable, in-master names, by confidence tier); ``filers`` is the raw enumerated set (``cik -> Filer``) the
    chain reconciler needs for the organizer name/ticker match-back and the 'Discovered' fallback labels.
    ``signal`` / ``broad`` are the keyword tiers used (carried for visibility / debugging)."""

    placed: dict[str, UUID] = field(default_factory=dict)
    verify: dict[str, UUID] = field(default_factory=dict)
    filers: dict[str, Filer] = field(default_factory=dict)
    signal: list[str] = field(default_factory=list)
    broad: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """No placeable names — the draft falls back to the recall-only decompose."""
        return not self.placed and not self.verify


def discovered_names(universe: DiscoveredUniverse) -> list[str]:
    """The placeable discovered names (placed then verify), for the tail-sweep's already-found list — so the
    directed sweep looks for what's MISSING, never re-lists the deterministic core."""
    out: list[str] = []
    for cik in (*universe.placed, *universe.verify):
        f = universe.filers.get(cik)
        if f is not None and f.name:
            out.append(f.name)
    return out


def discovery_context(universe: DiscoveredUniverse, tail_sweep: str | None = None) -> str | None:
    """Render the research-context block the organizer decompose arranges: the EDGAR-found US-listed names
    (name + ticker, the deterministic spine) followed by the tail-sweep synthesis (the foreign/brand-new tail).
    Returns ``None`` when BOTH are empty (the decompose then runs recall-only).

    The names are listed with their CURRENT ticker so the organizer emits a ticker the reconciler can match back
    to the discovered CIK; a single-BROAD verify name is tagged so the organizer keeps it but the operator sees
    its lower confidence. No number is emitted (#3 — names + tickers + tags only)."""
    lines: list[str] = []
    for cik in (*universe.placed, *universe.verify):
        f = universe.filers.get(cik)
        if f is None or not f.name:
            continue
        ticker = f" ({f.ticker})" if f.ticker else ""
        tag = "" if cik in universe.placed else " [verify — single broad-keyword hit]"
        lines.append(f"- {f.name}{ticker}{tag}")

    block = ""
    if lines:
        block = "US-listed companies found by EDGAR full-text search:\n" + "\n".join(lines)
    if tail_sweep and tail_sweep.strip():
        prefix = block + "\n\n" if block else ""
        block = prefix + "Additional names (directed web-search tail-sweep):\n" + tail_sweep.strip()
    return block or None


def run_discovery(
    conn: psycopg.Connection,
    edgar: Any,
    keyword_llm: Any,
    narrative: str,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    hit_cap: int | None = None,
) -> DiscoveredUniverse:
    """Run the EDGAR-first discovery for ``narrative``: keyword-gen -> EFTS enumerate -> CIK-resolve -> classify.

    Returns a ``DiscoveredUniverse`` (placeable CIKs by tier + the raw filer map). Fail-open everywhere: a blank
    narrative / no key / empty keyword result -> an empty universe; any error in the FREE enumerate+resolve step
    (EFTS network / DB) -> an empty universe carrying the keywords. ``edgar`` needs a ``get_json(url, cache_key)``
    method (the real ``EdgarClient`` or a fake); ``keyword_llm`` a ``draft_structured`` method.
    """
    kws = generate_keywords(keyword_llm, narrative)
    if kws is None:
        return DiscoveredUniverse()
    signal, broad = kws
    cap = hit_cap if hit_cap is not None else get_settings().discovery_hit_cap
    try:
        filers = discover(edgar, [*signal, *broad], hit_cap=cap)
        in_master = master.ids_for_ciks(conn, filers.keys(), tenant_id=tenant_id)
        disc = classify(filers, in_master_ids=in_master, signal=signal, broad=broad)
    except Exception:  # noqa: BLE001 — EFTS / DB trouble degrades to recall-only, never a 5xx
        return DiscoveredUniverse(signal=signal, broad=broad)
    return DiscoveredUniverse(
        placed=disc.placed, verify=disc.verify, filers=filers, signal=signal, broad=broad
    )
