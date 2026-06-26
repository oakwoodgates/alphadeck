"""The tier RECOMMENDER (INVARIANT #10) — the LLM recommends a tier per term, the operator decides.

Given a thesis's narrative + its term set, recommend ``signal``/``broad`` per term WITH a one-line reason. This
is a **recommendation, not a decision**: it is VISIBLE + PENDING and changes nothing until the operator confirms
via the existing tier toggle (where ``stamp_edited_term_set`` stamps ``operator_edited``). It rides DISPLAY-ONLY
to the FE (like ``matched_terms``), is never persisted, and never mutates ``authored_by``. INVARIANT #10: "The
LLM recommends; the operator decides."

It catches the nuclear flood at edit time (recommend BROAD for "nuclear power" / "decarbonization" / SMR BEFORE
they place 380 junk names) and surfaces discriminating terms the operator didn't seed (recommend SIGNAL — the
offense). It is OFF ``produce_term_set``'s determinism path (``assign_tier`` is untouched) — advisory metadata,
never a tier the producer applies. It sources NO number (#3 — a tier label + a reason string). Fail-open: any
trouble -> ``[]`` (the chips render with no recommendation, exactly as today).
"""

from __future__ import annotations

from typing import Any

from llm.keyword_gen import _clean
from llm.prompt_loader import load_prompt

# Structured-output contract — the model MUST call this tool; we read back its validated input. A tier label +
# a SHORT reason per term: no number, no company/ticker anywhere in the schema (INVARIANT #1/#3). The reason is
# the feature's value — DEMAND a specific WHY (what makes the term collision-prone vs discriminating), never a
# bare tier restatement.
TIER_REC_TOOL: dict[str, Any] = {
    "name": "tier_recommendations",
    "description": (
        "For each EDGAR full-text discovery keyword, recommend whether it is a SIGNAL term (specific / "
        "discriminating — a single hit should PLACE a company) or a BROAD term (collision-prone — counts only "
        "toward corroboration, never places alone), with a SHORT, SPECIFIC reason. Judge each term on its own "
        "discriminating power for the thesis, independent of any current tiering. NO numbers, NO company or "
        "ticker names — a tier label + a brief WHY only."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {
                            "type": "string",
                            "description": "The keyword, echoed back VERBATIM (so it can be matched).",
                        },
                        "tier": {"type": "string", "enum": ["signal", "broad"]},
                        "reason": {
                            "type": "string",
                            "description": (
                                "At most ~15 words: the SPECIFIC reason this term is discriminating or "
                                "collision-prone (e.g. 'a named reactor design' / 'common across unrelated "
                                "energy filings'), NOT a bare tier restatement. NO number, NO company/ticker."
                            ),
                        },
                    },
                    "required": ["term", "tier", "reason"],
                },
            }
        },
        "required": ["recommendations"],
    },
}


def recommend_tiers(client: Any, narrative: str, terms: list[str]) -> list[dict[str, str]]:
    """Recommend ``{term, tier, reason}`` per term, or ``[]`` on ANY failure (fail-open, like
    ``generate_keywords``). Advisory metadata ONLY — never persisted (#10), never a number (#3), and OFF
    ``produce_term_set``'s determinism path. The model judges each term INDEPENDENTLY of its current tier (the FE
    does the agree/disagree compare). Malformed / duplicate / out-of-enum rows are dropped defensively, never
    raised. ``client`` only needs a ``draft_structured(system, user, tool)`` method (the real ``LLMClient`` or a
    test fake)."""
    clean = _clean(terms)
    if not narrative or not narrative.strip() or not clean:
        return []
    # fail-loud on a missing prompt (a deploy bug), OUTSIDE the fail-open try.
    system = load_prompt("tier_recommend")
    user = f"Narrative:\n{narrative.strip()}\n\nTerms:\n" + "\n".join(f"- {t}" for t in clean)
    try:
        out = client.draft_structured(system=system, user=user, tool=TIER_REC_TOOL)
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return []
    if not isinstance(out, dict):
        return []
    recs: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in out.get("recommendations", []) or []:
        if not isinstance(r, dict):
            continue
        term = str(r.get("term", "")).strip()
        tier = str(r.get("tier", "")).strip().lower()
        key = term.lower()
        if not term or tier not in ("signal", "broad") or key in seen:
            continue  # drop malformed / out-of-enum / duplicate defensively, never raise
        seen.add(key)
        recs.append({"term": term, "tier": tier, "reason": str(r.get("reason", "")).strip()})
    return recs
