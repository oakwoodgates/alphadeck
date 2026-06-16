"""A thin, model-agnostic LLM client — the single place the app talks to the model API.

Mirrors the repo's ``EdgarClient`` idiom (``ingest/edgar/client.py``): live calls are explicit opt-in
(``allow_live``); the SDK is imported lazily inside the live path so the package imports — and the whole
test suite runs — with **no** ``anthropic`` installed and **no** API key. With ``allow_live=False`` or no key,
``draft_structured`` raises ``LLMUnavailable`` (the offline gate), which every caller turns into fail-open.

Interface is deliberately generic — ``draft_structured(system, user, tool) -> dict`` (one forced tool call,
its validated args returned) — so a different provider is a one-file swap. Prompts + schemas live with the
feature module (``llm/flag_explanation.py``), never here (CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Any

from domain.config import DEFAULT_CONFIG


class LLMUnavailable(RuntimeError):
    """Raised when a live LLM call can't be made (live disabled, or no ``ANTHROPIC_API_KEY``). Callers treat
    it as fail-open — the feature degrades to no-explanation, never an error to the operator."""


class LLMClient:
    """Cache-less, fail-open wrapper over the Anthropic Messages API (tool-use for structured output)."""

    def __init__(
        self,
        *,
        allow_live: bool = False,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.allow_live = allow_live
        # read the key inside the client, exactly as EdgarClient reads ALPHADECK_USER_AGENT
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # operational dials default from CallConfig (the no-magic-number home), overridable per call
        self.model = model or DEFAULT_CONFIG.llm_model
        self.max_tokens = max_tokens or DEFAULT_CONFIG.llm_max_tokens
        self.timeout_s = timeout_s or DEFAULT_CONFIG.llm_timeout_s

    def draft_structured(
        self, *, system: str, user: str, tool: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Force the model to call ``tool`` once and return its (schema-validated) input dict.

        Raises ``LLMUnavailable`` when no live call is possible (offline gate / no key). Returns ``None`` if
        the model returned no tool call. The Anthropic SDK is imported HERE so the module is import-clean
        without it (the suite never needs the dependency)."""
        if not self.allow_live:
            raise LLMUnavailable("live LLM calls disabled (allow_live=False)")
        if not self.api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY not set")

        import anthropic  # lazy — like httpx in EdgarClient._fetch

        client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_s)
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},  # must call our tool
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
                return dict(block.input)
        return None
