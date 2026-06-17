"""The narrative→chain DECOMPOSE drafter — the SECOND LLM seam (Slice 5b), on the proven `backend/llm`
plumbing the flag drafter (#59) established.

Given an operator's narrative, draft a value chain: 2-6 **segments** (links in the chain), the **names** that
sit in each, and one short thesis-fit **prose** sentence per name. The output is STRUCTURE + NAMES + REASONING
only — it is a drafting aid the operator ratifies, never a decision.

THE BOUNDS (carried from the gate-1 plan):
- **Never a number** (INVARIANT #1/#3). The prompt + tool schema forbid any price / %% / share count / cash /
  runway / market cap / catalyst value; the response carries no value field. This half of the bound rests on
  the PROMPT — Sonnet is the adherence lever, and the gate-2 MANUAL no-number-in-the-prose check is its real
  test (a fake-client unit test cannot exercise a prompt).
- **A name is a discovery suggestion, never a decision** (INVARIANT #2). This module proposes
  ``{name, ticker?, prose}``; exact master membership DECIDES, downstream in ``workbench.chain_draft`` — the
  model's ticker is a best-guess key, never trusted as the id.
- **Fail-open.** Every failure path (no key, live disabled, timeout, SDK error, no tool call, blank
  narrative) returns ``None`` — the draft endpoint then returns an empty draft and hand-authoring is
  untouched.
"""

from __future__ import annotations

from typing import Any

# The structured-output contract — the model MUST call this tool; we read back its validated input. STRUCTURE
# + names + reasoning ONLY: there is no value/score/number field anywhere in the schema (INVARIANT #1).
DECOMPOSE_TOOL: dict[str, Any] = {
    "name": "draft_value_chain",
    "description": (
        "Return a value-chain decomposition of an investment narrative: 2-6 segments (links in the chain), "
        "the publicly-listed US companies in each, and one short reasoning sentence per company. Structure, "
        "names, and reasoning ONLY — never a number."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "minItems": 2,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short segment name, e.g. 'Enrichment & fuel'.",
                        },
                        "descriptor": {
                            "type": "string",
                            "description": "Optional one-phrase tag for the link's role in the chain (no numbers).",
                        },
                        "placements": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "The company's common name.",
                                    },
                                    "ticker": {
                                        "type": "string",
                                        "description": (
                                            "Your best-guess US exchange ticker (we verify it against our own "
                                            "master; omit ONLY if you truly have none — never fabricate one)."
                                        ),
                                    },
                                    "prose": {
                                        "type": "string",
                                        "description": (
                                            "At most 25 words: why this company sits in this segment, grounded "
                                            "in the narrative. NO numbers, prices, %, share counts, or valuations."
                                        ),
                                    },
                                },
                                "required": ["name", "prose"],
                            },
                        },
                    },
                    "required": ["label", "placements"],
                },
            }
        },
        "required": ["segments"],
    },
}

SYSTEM_PROMPT = """\
You decompose an investment narrative into a value chain for an analyst. Output ONLY structure, company \
names, and short reasoning — you are a drafting aid, never a decision and never a recommendation.

Rules:
- Break the narrative into 2 to 6 value-chain SEGMENTS (links in the chain, e.g. "Reactor developers", \
"Enrichment & fuel", "Utilities / offtake").
- In each segment, list real, publicly-listed US companies you believe sit there. For each give: its common \
NAME; your BEST-GUESS US exchange TICKER (we verify every ticker against our own security master, so a wrong \
guess just costs the analyst a manual pick — always give your best guess, but never fabricate any OTHER \
fact); and ONE sentence (at most 25 words) on why it sits in this segment, grounded in the narrative.
- You are FORBIDDEN from emitting ANY number: no price, market cap, valuation, percentage, share count, \
cash, runway, revenue, dollar figure, date, or catalyst value. Those come from a separate deterministic \
system, never from you. If tempted to cite a figure, describe it in words or omit it.
- Do not rank, size, or recommend; do not say which name is "best".
- This reasoning is DRAFTED text for the analyst to ratify or rewrite, not fact.

Always answer by calling the draft_value_chain tool."""


def decompose_narrative(client: Any, narrative: str) -> dict[str, Any] | None:
    """Draft a value-chain decomposition from a narrative. Returns the validated tool input
    (``{"segments": [...]}``) or ``None`` on ANY failure — fail-open: no key / live disabled / timeout / SDK
    error / no tool call / blank narrative → ``None`` (the draft endpoint then returns an empty draft, and
    hand-authoring is untouched).

    ``client`` only needs a ``draft_structured(system, user, tool)`` method (the real ``LLMClient`` or a test
    fake). It sources NO number; the no-number bound rests on the prompt (Sonnet is the adherence lever) — the
    gate-2 manual check is its real test. Parsing/validation of the shape happens downstream
    (``workbench.chain_draft.proposed_from_decomposition``), also fail-open.
    """
    if not narrative or not narrative.strip():
        return None
    try:
        out = client.draft_structured(
            system=SYSTEM_PROMPT, user=f"Narrative:\n{narrative.strip()}", tool=DECOMPOSE_TOOL
        )
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return None
    if not isinstance(out, dict):
        return None
    return out
