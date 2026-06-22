"""A thin, model-agnostic LLM client — the single place the app talks to the model API.

Mirrors the repo's ``EdgarClient`` idiom (``ingest/edgar/client.py``): live calls are explicit opt-in
(``allow_live``); the SDK is imported lazily inside the live path so the package imports — and the whole
test suite runs — with **no** ``anthropic`` installed and **no** API key. With ``allow_live=False`` or no key,
``draft_structured`` raises ``LLMUnavailable`` (the offline gate), which every caller turns into fail-open.

Interface is deliberately generic — ``draft_structured(system, user, tool) -> dict`` (one forced tool call,
its validated args returned), plus its auto-tool sibling ``research(system, user, tool) -> str`` (a web-search
pass → free-text synthesis, the Slice-1 research step) — so a different provider is a one-file swap. Prompts +
schemas live with the feature module (``llm/flag_explanation.py``, ``llm/chain_decomposition.py``), never here
(CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Any

from domain.settings import get_settings


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
        base_url: str | None = None,
    ) -> None:
        self.allow_live = allow_live
        # read the key inside the client, exactly as EdgarClient reads ALPHADECK_USER_AGENT — a LATE read of
        # ANTHROPIC_API_KEY (Settings declares it for the inventory, but this env read is the off-switch, so a
        # test that delenv's the key after import still gates offline). `or` is deliberate: ""/unset -> env.
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # operational dials default from Settings (the env-overridable home), overridable per call. `is None`
        # (NOT `or`): an explicit 0 / 0.0 / "" override must be honored, never silently coalesced to the default.
        _s = get_settings()
        self.model = model if model is not None else _s.llm_model
        self.max_tokens = max_tokens if max_tokens is not None else _s.llm_max_tokens
        self.timeout_s = timeout_s if timeout_s is not None else _s.llm_timeout_s
        self.base_url = base_url if base_url is not None else _s.anthropic_base_url

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

        # base_url only when truthy — None/"" means the SDK default (api.anthropic.com); base_url="" is broken.
        kwargs: dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout_s}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = anthropic.Anthropic(**kwargs)
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

    def research(self, *, system: str, user: str, tool: dict[str, Any]) -> str | None:
        """Run a web-search RESEARCH pass and return the model's synthesized TEXT (the concatenated text
        blocks), or ``None`` if it produced none. ``tool`` is a SERVER-side tool (the ``web_search`` spec); the
        model MAY use it (``tool_choice=auto``) — the opposite of ``draft_structured``'s one FORCED tool. The
        result is free text used as CONTEXT for the decompose step, never structured output and never a written
        fact (INVARIANT #3 — the chain stays value-free by the decompose tool's schema, not by this text).

        Raises ``LLMUnavailable`` when no live call is possible (offline gate / no key) — the caller fails open
        to the recall-only decompose. The SDK is imported HERE so the module stays import-clean without it.
        """
        if not self.allow_live:
            raise LLMUnavailable("live LLM calls disabled (allow_live=False)")
        if not self.api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY not set")

        import anthropic  # lazy — like draft_structured

        kwargs: dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout_s}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = anthropic.Anthropic(**kwargs)
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={
                "type": "auto"
            },  # the model MAY search (vs draft_structured's forced tool)
        )
        parts = [getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"]
        text = "\n".join(p for p in parts if p).strip()
        return text or None
