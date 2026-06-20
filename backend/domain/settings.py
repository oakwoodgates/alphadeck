"""The typed home for operational, env-overridable configuration.

This is the env-mutable sibling of ``CallConfig`` (``domain/config.py``), kept in a SEPARATE file on purpose:
``CallConfig`` holds the trust-validated (n=19) call-engine dials that must NEVER be silently env-overridden
(an env override of a validated threshold would change the calls); ``Settings`` holds the operational knobs —
LLM model dials, and in later slices the base URLs + throttle — that an operator SHOULD be able to change with
a ``.env`` / environment edit, not a code edit. The file boundary IS the "don't env-override a validated
threshold" boundary.

Access rule (LOAD-BEARING — D5 of the refactor plan):
- ``get_settings()`` is a cached singleton — use it for STABLE config (the LLM dials now; base URLs + rate
  limits in Slice 2). It reads the process env ONCE.
- The few env vars the test suite toggles AFTER import — ``DATABASE_URL``, ``ALPHADECK_TENANT_ID``,
  ``ANTHROPIC_API_KEY`` — are DECLARED here for the typed inventory but are still read LATE at their edge
  (``db.session`` / ``LLMClient``), NEVER off the cached instance. A module-level ``Settings()`` frozen at
  import would read env before a test's monkeypatch (the ``db`` fixture sets ``DATABASE_URL``; an LLM test
  ``delenv``s the key) — and a frozen ``DATABASE_URL`` could point a test at the demo DB the fixture
  truncates. So: cached for stable config, late at the edge for the toggled three.

Env names: generic fields read ``ALPHADECK_<FIELD>`` (``env_prefix``) so a stray ``MODEL`` / ``TZ`` / ``HOST``
in CI or Docker can't accidentally capture one (and the compose sidecar's ``ALPHADECK_CRON_*`` vars are simply
ignored). The legacy-named vars keep their EXACT current names via an explicit alias (CI + docker-compose
inject them under those names; the prefix would otherwise demand ``ALPHADECK_DATABASE_URL`` — a different,
wrong var). ``env_file`` is deliberately NOT enabled — nothing reads a ``.env`` at the Python layer today
(compose injects env); enabling it would be a silent behavior change.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Operational, env-overridable config — see the module docstring for the access rule + env-name policy."""

    model_config = SettingsConfigDict(
        env_prefix="ALPHADECK_",
        case_sensitive=False,
        extra="ignore",  # the process env carries many unrelated vars (incl. ALPHADECK_CRON_*) — ignore them
    )

    # --- LLM seam (M4b — the FLAG-explanation drafter, the FIRST LLM call) — operational dials only ---
    # The one LLM seam in an otherwise-deterministic system: a plain-English explanation of an extracted
    # FLAG candidate, grounded in its located passage, shown ALONGSIDE the raw text (an aid to the ratify,
    # never the ratify). The PROMPT + structured-output schema live with the module (llm/flag_explanation.py,
    # per CLAUDE.md); only these operational dials live here, under the same no-magic-number discipline.
    # There is deliberately NO `enabled` flag — the absence of ANTHROPIC_API_KEY is the off switch
    # (fail-open: no key -> no explanation, the facts panel works exactly as today).
    llm_model: str = (
        "claude-haiku-4-5-20251001"  # fast/cheap — a display aid, not a sourced number;
    )
    # the Sonnet bump (claude-sonnet-4-6) is the ADHERENCE lever if it ever states a final value (the one
    # part of the bound that rests on the prompt, not the rail — see docs / the slice plan).
    llm_max_tokens: int = 256  # <=2 sentences — an output ceiling and a cost guard
    llm_timeout_s: float = (
        10.0  # fail-open FAST if the API hangs (the panel must never block on it)
    )

    # --- LLM seam (S5 — the narrative→chain DECOMPOSE drafter, the SECOND LLM call) — operational dials ---
    # Decomposing a narrative into a value chain is reasoning-heavy and IS the product (a weak chain defeats
    # the name-selection flaw-patch), so this seam runs on SONNET, NOT the Haiku flag dials above — kept
    # separate so the flag drafter is undisturbed. Prompt + tool schema live with the module
    # (llm/chain_decomposition.py); only these operational dials live here. Fail-open like the flag seam (no
    # ANTHROPIC_API_KEY -> the draft endpoint is a no-op). Staged decomposition is the deferred fallback if a
    # single call underperforms (a logged trigger, not a default).
    llm_decompose_model: str = "claude-sonnet-4-6"  # reasoning-heavy; the chain IS the product
    llm_decompose_max_tokens: int = (
        2000  # a whole value chain (segments + names + prose), not a sentence
    )
    llm_decompose_timeout_s: float = (
        # Measured ~13s fast-path for a 3-segment chain (a 2000-token reasoning call); 20s overran on tail
        # latency and failed OPEN (an empty draft), so the seam looked broken to the operator. 60s gives
        # ~4.5x headroom for this on-demand action — the rare slow wait beats a silently lost draft.
        60.0
    )

    # Optional Anthropic base_url override (refactor D7): None => the SDK default (api.anthropic.com); passed
    # to the SDK by LLMClient ONLY when truthy (base_url="" is a broken URL). Buys a future proxy / self-host
    # + a test seam at zero cost today (nothing sets it). Read at the field name ALPHADECK_ANTHROPIC_BASE_URL.
    anthropic_base_url: str | None = None

    # --- Secrets / deploy values: DECLARED here as the typed inventory; READ LATE at the edge (D5) ---
    # These three are toggled by the test suite AFTER import (the `db` fixture sets DATABASE_URL +
    # ALPHADECK_TENANT_ID; an LLM test delenv's ANTHROPIC_API_KEY), so their RUNTIME read stays a fresh,
    # late os.environ.get at the edge (db.session / LLMClient) — NOT off the cached get_settings(). Declared
    # here only so the env inventory is single-source + typed; the explicit alias keeps each var's EXACT
    # legacy name (the env_prefix would otherwise read ALPHADECK_DATABASE_URL — a different, wrong var).
    database_url: str = (
        Field(  # mirrors db.session.DEFAULT_DATABASE_URL (that module remains the late reader)
            default="postgresql://alphadeck:alphadeck@localhost:5544/alphadeck",
            validation_alias=AliasChoices("DATABASE_URL"),
        )
    )
    tenant_id: str | None = Field(
        default=None, validation_alias=AliasChoices("ALPHADECK_TENANT_ID")
    )
    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The cached ``Settings`` singleton for STABLE config (the LLM dials now; base URLs + rate limits in
    Slice 2). Reads the process env ONCE.

    Do NOT use this for ``DATABASE_URL`` / ``ALPHADECK_TENANT_ID`` / ``ANTHROPIC_API_KEY`` — those are read
    late at the edge (see the module docstring). A test that overrides an ``ALPHADECK_*`` dial must call
    ``get_settings.cache_clear()`` after monkeypatching the env.
    """
    return Settings()
