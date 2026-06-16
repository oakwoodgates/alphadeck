"""The FLAG-explanation drafter — the first (and only, this slice) LLM seam.

For an extracted **FLAG** candidate, draft a ≤2-sentence plain-English explanation of what the flagged figure
is composed of, **grounded only in the located passage** the operator can see. It is a reading aid that sits
*alongside* the raw passage — never the ratify, never a fact.

THE BOUND (INVARIANT #3, decision #1 of the slice): the drafter may name the component figures that appear in
the passage and the DIRECTION an adjustment implies; it must **not** state the final adjusted value. The
operator does the arithmetic and types the number. This is enforced two ways:
- *Structurally* — the returned string is never a field on the ratify request and never reaches ``ingest_*``
  (the real guarantee; lives at the endpoint/wire).
- *By prompt* — the system prompt below (the courtesy; the watch-item — pull the Sonnet lever if it slips).

Every failure path (no key, live disabled, timeout, SDK error, ungrounded, malformed) returns
``("", False)`` — fail-open: the facts panel works exactly as today.
"""

from __future__ import annotations

from typing import Any

from domain.extraction import ExtractedFact, Tier

# The structured-output contract — the model MUST call this tool; we read back its validated input.
EXPLAIN_TOOL: dict[str, Any] = {
    "name": "flag_explanation",
    "description": (
        "Return a short plain-English explanation of the flagged figure, grounded ONLY in the provided "
        "filing passage. Decline (grounded=false) if the passage doesn't support one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "explanation": {
                "type": "string",
                "description": (
                    "At most two sentences. Name the component the flag points to and restate ITS figure "
                    "exactly as it appears in the passage; state the direction an adjustment implies. Do "
                    "NOT state any final adjusted value, and use no number that is not in the passage."
                ),
            },
            "grounded": {
                "type": "boolean",
                "description": "true only if the passage supports the explanation; false to decline.",
            },
        },
        "required": ["explanation", "grounded"],
    },
}

SYSTEM_PROMPT = """\
You explain ONE flagged figure from an SEC filing to an analyst who is looking at the same passage you are \
given. Your explanation is a reading aid, never a decision and never a recommendation.

Rules:
- Use ONLY facts in the provided passage. No outside knowledge. Use no number that is not already in the \
passage.
- Name the specific component the flag points to (e.g. a one-time settlement or milestone payment, a \
year-to-date basis, a dual-class share split) and restate ITS figure exactly as written in the passage.
- State the DIRECTION an adjustment would imply in words (e.g. "the recurring figure is lower"). Do NOT \
compute or state any final adjusted value — the analyst decides and enters that number themselves.
- At most two sentences. Plain English, no preamble.
- If the passage does not actually support an explanation of the flag, set grounded=false and leave \
explanation empty.

Always answer by calling the flag_explanation tool."""


def _flagged_figure(c: ExtractedFact) -> str:
    """The figure under review, labelled by fact type (context for the model — the passage is the ground
    truth for any number it cites)."""
    if c.fact_type == "cash_burn" and c.quarterly_burn_usd is not None:
        return f"quarterly operating cash use = {c.quarterly_burn_usd:,.0f}"
    if c.fact_type == "shares_outstanding" and c.value is not None:
        return f"shares outstanding = {c.value:,.0f}"
    return "(see passage)"


def _build_user(c: ExtractedFact) -> str:
    passages = "\n".join(f"- [{p.kind} · {p.anchor}] {p.excerpt}" for p in c.located_passages)
    flags = ", ".join(c.flags) if c.flags else "(none)"
    return (
        f"Fact type: {c.fact_type}\n"
        f"Figure under review: {_flagged_figure(c)}\n"
        f"Detected flag(s): {flags}\n"
        f"Filing passage(s):\n{passages}"
    )


def explain_flag(client: Any, candidate: ExtractedFact) -> tuple[str, bool]:
    """Draft a grounded explanation for a FLAG candidate. Returns ``(explanation, grounded)``.

    FLAG-only by contract (any other tier returns ``("", False)`` — the AUTO value is clean and HUMAN/purity
    is the operator's edge, deliberately not model-explained). Fail-open on every error path. ``client`` only
    needs a ``draft_structured(system, user, tool)`` method (the real ``LLMClient`` or a test fake).
    """
    if candidate.tier != Tier.FLAG:
        return ("", False)
    if not candidate.located_passages:
        return ("", False)  # nothing to ground an explanation in
    try:
        out = client.draft_structured(
            system=SYSTEM_PROMPT, user=_build_user(candidate), tool=EXPLAIN_TOOL
        )
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return ("", False)
    if not out:
        return ("", False)
    explanation = str(out.get("explanation", "")).strip()
    grounded = bool(out.get("grounded", False))
    if not grounded or not explanation:
        return ("", False)  # the model declined to ground it -> say-so (no fabricated explanation)
    return (explanation, True)
