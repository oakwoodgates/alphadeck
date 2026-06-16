"""The LLM seam (M4b).

CLAUDE.md: *all* LLM calls go through this module — no scattered API calls; prompts and structured-output
schemas live here; responses are grounded in evidence the operator can see. The LLM **augments, never
sources**: it explains located evidence in plain English; it never invents a trigger, fires a call, or is
the authority for a number (INVARIANT #3).

The first (and, for this slice, only) seam is the FLAG-explanation drafter — ``flag_explanation``.
"""

from llm.client import LLMClient, LLMUnavailable

__all__ = ["LLMClient", "LLMUnavailable"]
