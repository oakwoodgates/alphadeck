"""The purity-estimate drafter — the grounded segment→thesis-% proposal seam (SURFACE Slice 1b).

For a revenue_mix (purity) candidate, propose what % of the company's revenue is ON-THESIS — grounded ONLY in
the located segment-footnote passage the operator can see, with the thesis narrative selecting which segment
counts. It is an UNVERIFIED estimate the operator confirms/overrides — never a fact, never auto-accepted (the
#10 pattern; purity stays the operator's edge — the model PROPOSES, the operator DECIDES).

THE BOUND (INVARIANT #1/#3 — the operator's hard requirement): the model may propose a % ONLY from segment
revenue figures that appear IN the passage (segment $ / total $); it must NOT recall the company's revenue mix
from memory. "Read this footnote and propose 20%" is fine; "I know the company is ~20% nuclear" is a
model-sourced number and is forbidden. Enforced two ways:
- *Structurally* — the proposal is an ESTIMATE returned on the extract response (computed-on-read); it NEVER
  becomes a fact (only the operator's ratify writes one) and the candidate ALWAYS carries the passage it read.
- *By prompt* — the system prompt demands passage-only grounding + decline (grounded=false) when the passage
  lacks the segment revenue figures.

Every failure path (no key, live disabled, timeout, SDK error, ungrounded, malformed, out-of-range, no passage)
returns ``None`` — fail-open: the purity candidate stays today's HUMAN (located, no value; the operator types it).
"""

from __future__ import annotations

from typing import Any

from domain.base import DomainModel
from domain.extraction import ExtractedFact
from llm.prompt_loader import load_prompt


class PurityProposal(DomainModel):
    """A grounded, UNVERIFIED purity proposal: the on-thesis segment + its % of total revenue + a one-line
    reason citing the passage figures. The operator confirms/overrides — never a fact until they ratify.
    """

    segment: str
    pct: float
    reason: str


# The structured-output contract — the model MUST call this tool; we read back its validated input.
PURITY_TOOL: dict[str, Any] = {
    "name": "purity_estimate",
    "description": (
        "Propose what % of the company's revenue is ON-THESIS, grounded ONLY in the provided segment "
        "passage. Decline (grounded=false) if the passage lacks the segment revenue figures."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "segment": {
                "type": "string",
                "description": "the on-thesis segment name, exactly as it appears in the passage",
            },
            "pct": {
                "type": "number",
                "description": (
                    "that segment's revenue as a % of total revenue (0-100), computed ONLY from the "
                    "segment $ and total $ shown in the passage — never recalled from memory"
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "one sentence naming the segment $ and total $ from the passage that yield the %. "
                    "Use no number that is not in the passage."
                ),
            },
            "grounded": {
                "type": "boolean",
                "description": "true only if the passage contains the segment + total revenue figures; false to decline.",
            },
        },
        "required": ["segment", "pct", "reason", "grounded"],
    },
}


def _build_user(narrative: str, candidate: ExtractedFact) -> str:
    passages = "\n".join(
        f"- [{p.kind} · {p.anchor}] {p.excerpt}" for p in candidate.located_passages
    )
    return (
        f"Thesis narrative (what counts as ON-THESIS):\n{narrative}\n\n"
        f"Filing passage(s) — the ONLY source for any number:\n{passages}"
    )


def propose_purity(client: Any, narrative: str, candidate: ExtractedFact) -> PurityProposal | None:
    """Draft a grounded purity proposal for a revenue_mix candidate, or ``None`` (fail-open / declined).

    Purity-only (any other fact type returns ``None`` without consulting the model). Grounded ONLY in the
    candidate's located passage(s); the narrative selects WHICH segment is on-thesis. ``client`` only needs a
    ``draft_structured(system, user, tool)`` method (the real ``LLMClient`` or a test fake)."""
    if candidate.fact_type != "revenue_mix" or not candidate.located_passages:
        return None  # only purity; nothing to ground a proposal in
    # fail-loud: a missing prompt file is a deploy bug, raised HERE (outside the fail-open try) so it surfaces.
    system = load_prompt("purity_estimate")
    try:
        out = client.draft_structured(
            system=system, user=_build_user(narrative, candidate), tool=PURITY_TOOL
        )
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return None
    if not out or not bool(out.get("grounded", False)):
        return None  # declined (or empty) -> stay HUMAN; no fabricated number
    try:
        pct = float(out["pct"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 <= pct <= 100.0:
        return None  # a % outside [0,100] isn't a grounded revenue share
    segment = str(out.get("segment", "")).strip()
    reason = str(out.get("reason", "")).strip()
    if not segment:
        return None  # a proposal with no segment isn't actionable
    return PurityProposal(segment=segment, pct=pct, reason=reason)
