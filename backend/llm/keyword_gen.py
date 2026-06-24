"""The thesis→keyword generator (discovery Slice 2a) — the LLM's FIRST bounded job in the EDGAR-first
architecture (the second is the directed tail-sweep).

Given a narrative, generate the EDGAR full-text search keywords that enumerate the theme's US filers, split into
two tiers: **SIGNAL** (specific/discriminating terms — incl. adjacent-mechanism terms so CNS-adjacents surface —
a single hit places a company) and **BROAD** (collision-prone terms that count only toward the >=2-overlap rule;
a single BROAD-only match goes to the verify tier, never auto-placed). Cheap, bounded, no web search.

The LLM only PROPOSES keywords; the deterministic EFTS enumerator + the precision filter + exact CIK membership
DECIDE the universe (INVARIANT #2). It sources no number (#3). Fail-open: any trouble -> ``None`` (the caller
then has no EFTS keywords for this thesis and degrades to the LLM tail-sweep / hand-authoring).
"""

from __future__ import annotations

from typing import Any

from llm.prompt_loader import load_prompt

# Structured-output contract — the model MUST call this tool; we read back its validated input. Keyword TERMS
# only: no company, ticker, or number anywhere in the schema (INVARIANT #3).
KEYWORD_TOOL: dict[str, Any] = {
    "name": "thesis_keywords",
    "description": (
        "Return the EDGAR full-text search keywords for an investment thesis, split into SIGNAL (specific / "
        "discriminating — a single hit places a company) and BROAD (collision-prone — counts only toward the "
        ">=2-keyword rule, a single BROAD-only match is surfaced to verify). Keyword TERMS only — never a "
        "company name, ticker, or number."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "signal": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific discriminating terms (drug / compound / mechanism names + adjacent-mechanism "
                    "terms); a single hit PLACES a company."
                ),
            },
            "broad": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Collision-prone on-theme terms that ADD recall; count only toward the >=2-keyword rule, "
                    "never place alone (a single BROAD-only match goes to the verify tier)."
                ),
            },
        },
        "required": ["signal"],
    },
}


def _clean(terms: Any) -> list[str]:
    """A tier's terms -> a deduped (case-insensitive), stripped, non-empty string list. Defensive: a non-list
    or stray non-string is dropped, never raised."""
    if not isinstance(terms, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if isinstance(t, str) and t.strip() and t.strip().lower() not in seen:
            seen.add(t.strip().lower())
            out.append(t.strip())
    return out


def generate_keywords(client: Any, narrative: str) -> tuple[list[str], list[str]] | None:
    """Generate the ``(signal, broad)`` keyword tiers for a narrative, or ``None`` on ANY failure — fail-open:
    no key / live disabled / timeout / SDK error / no tool call / blank narrative / empty result -> ``None``.

    ``client`` only needs a ``draft_structured(system, user, tool)`` method (the real ``LLMClient`` or a test
    fake). It sources NO number; the no-number bound rests on the schema (no number field) + the prompt.
    """
    if not narrative or not narrative.strip():
        return None
    # fail-loud on a missing prompt (a deploy bug), OUTSIDE the fail-open try.
    system = load_prompt("keyword_gen")
    try:
        out = client.draft_structured(
            system=system, user=f"Narrative:\n{narrative.strip()}", tool=KEYWORD_TOOL
        )
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return None
    if not isinstance(out, dict):
        return None
    signal = _clean(out.get("signal"))
    broad = _clean(out.get("broad"))
    if not signal and not broad:
        return None
    return signal, broad
