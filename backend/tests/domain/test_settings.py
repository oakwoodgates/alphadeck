"""Settings — the typed home for operational, env-overridable config (refactor Slice 1).

A behavior-preserving move of the 6 LLM dials out of CallConfig, guarded by:
- the dial GOLDEN — defaults AND types identical to the pre-refactor CallConfig values,
- the LATE-READ rule (D5) for the 3 env-toggled vars (DATABASE_URL / ALPHADECK_TENANT_ID / ANTHROPIC_API_KEY)
  — monkeypatched AFTER import, the late edge read wins, and the ALPHADECK_ prefix does NOT capture them,
- the OVERRIDE smoke (ALPHADECK_LLM_MODEL reaches the client) + the is-None coalesce fix (F1 — an explicit 0
  is honored, never silently dropped): the path a defaults-only golden never covers.
No DB, no network, no key.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from db.session import current_tenant_id, database_url
from domain.config import CallConfig
from domain.settings import Settings, get_settings
from llm.client import LLMClient

_DIAL_ENV = (
    "ALPHADECK_LLM_MODEL",
    "ALPHADECK_LLM_MAX_TOKENS",
    "ALPHADECK_LLM_TIMEOUT_S",
    "ALPHADECK_LLM_DECOMPOSE_MODEL",
    "ALPHADECK_LLM_DECOMPOSE_MAX_TOKENS",
    "ALPHADECK_LLM_DECOMPOSE_TIMEOUT_S",
    "ALPHADECK_LLM_RESEARCH_MODEL",
    "ALPHADECK_LLM_RESEARCH_MAX_TOKENS",
    "ALPHADECK_LLM_RESEARCH_TIMEOUT_S",
    "ALPHADECK_LLM_RESEARCH_MAX_SEARCHES",
    "ALPHADECK_LLM_RESEARCH_CACHE_TTL_S",
    "ALPHADECK_RESEARCH_WEB_SEARCH_TOOL",
    "ALPHADECK_LLM_KEYWORD_MODEL",
    "ALPHADECK_LLM_KEYWORD_MAX_TOKENS",
    "ALPHADECK_LLM_KEYWORD_TIMEOUT_S",
    "ALPHADECK_ANTHROPIC_BASE_URL",
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """The get_settings() singleton is cached; clear it around every test so an ALPHADECK_* override in one
    test never leaks into another (and the first read always reflects the current env)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- the dial golden: defaults AND types identical to the pre-refactor CallConfig values ---


def test_llm_dial_defaults_match_the_old_callconfig_values_and_types(monkeypatch):
    for name in _DIAL_ENV:  # hermetic: an ambient ALPHADECK_* override must not mask the defaults
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert s.llm_model == "claude-haiku-4-5-20251001"
    assert s.llm_max_tokens == 256 and isinstance(s.llm_max_tokens, int)
    assert s.llm_timeout_s == 10.0 and isinstance(s.llm_timeout_s, float)
    assert s.llm_decompose_model == "claude-sonnet-4-6"
    assert s.llm_decompose_max_tokens == 2000 and isinstance(s.llm_decompose_max_tokens, int)
    assert s.llm_decompose_timeout_s == 60.0 and isinstance(s.llm_decompose_timeout_s, float)
    assert s.anthropic_base_url is None  # D7: None => the SDK default


def test_research_dial_defaults(monkeypatch):
    """The Slice-1 research dials (NEW — never in CallConfig): the Opus default + the bounded search budget,
    and the CODE-COUPLED web_search version field (a Settings field for visibility, not a free env flip).
    Env hermetic so an ambient override can't mask the defaults."""
    for name in _DIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert s.llm_research_model == "claude-opus-4-8"
    assert s.llm_research_max_tokens == 4000 and isinstance(s.llm_research_max_tokens, int)
    # 300s + 3 searches (raised/trimmed after the $8-and-nothing incident: at 120s the pass never completed; 3
    # searches finish inside the window). cache TTL defaults 0 (off) so the convergence gate-2 runs fresh.
    assert s.llm_research_timeout_s == 300.0 and isinstance(s.llm_research_timeout_s, float)
    assert s.llm_research_max_searches == 3 and isinstance(s.llm_research_max_searches, int)
    assert s.llm_research_cache_ttl_s == 0.0 and isinstance(s.llm_research_cache_ttl_s, float)
    assert s.research_web_search_tool == "web_search_20250305"


def test_keyword_dial_defaults(monkeypatch):
    """The Slice-2 keyword-gen dials (cheap Haiku — a bounded structured keyword list, no web search). Env
    hermetic so an ambient override can't mask the defaults."""
    for name in _DIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert s.llm_keyword_model == "claude-haiku-4-5-20251001"
    assert s.llm_keyword_max_tokens == 512 and isinstance(s.llm_keyword_max_tokens, int)
    assert s.llm_keyword_timeout_s == 20.0 and isinstance(s.llm_keyword_timeout_s, float)


def test_the_llm_dials_left_callconfig():
    """The move is real, not a copy: CallConfig no longer carries the llm_* fields, so an env override of a
    trust-validated threshold can never ride in on an llm dial, and there is one home for each dial.
    """
    fields = CallConfig.model_fields
    for gone in (
        "llm_model",
        "llm_max_tokens",
        "llm_timeout_s",
        "llm_decompose_model",
        "llm_decompose_max_tokens",
        "llm_decompose_timeout_s",
    ):
        assert gone not in fields
    with pytest.raises(
        Exception
    ):  # extra="forbid" — constructing with a moved field now errors loudly
        CallConfig(llm_model="x")


# --- the late-read rule (D5): the 3 env-toggled vars, monkeypatched AFTER import ---


def test_database_url_is_read_late_at_the_edge(monkeypatch):
    """db.session.database_url() reads DATABASE_URL LATE (a fresh os.environ.get per call), so a monkeypatch
    AFTER import wins — never off the cached Settings. (A cached Settings frozen at import could point a test
    at the demo DB the db fixture truncates — the hazard this rule defends.)"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://late:read@localhost:5544/proof")
    assert database_url() == "postgresql://late:read@localhost:5544/proof"


def test_tenant_id_is_read_late_at_the_edge(monkeypatch):
    monkeypatch.setenv("ALPHADECK_TENANT_ID", "00000000-0000-0000-0000-0000000000aa")
    assert current_tenant_id() == UUID("00000000-0000-0000-0000-0000000000aa")


def test_anthropic_key_is_read_late_at_the_client_edge(monkeypatch):
    """LLMClient reads ANTHROPIC_API_KEY LATE at construction — a delenv after import yields the offline-gate
    input; a setenv is picked up by a freshly-constructed client. Never off the cached get_settings().
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert LLMClient(allow_live=True).api_key is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-late-read")
    assert LLMClient(allow_live=True).api_key == "sk-late-read"


def test_alphadeck_prefix_does_not_capture_the_legacy_named_secrets(monkeypatch):
    """The non-ALPHADECK-named vars keep their EXACT names via alias — the env_prefix must NOT read
    ALPHADECK_DATABASE_URL / ALPHADECK_ANTHROPIC_API_KEY. Set only the (wrong) prefixed names and the fields
    stay at their defaults; set the correct legacy names and they ARE read."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ALPHADECK_DATABASE_URL", "postgresql://wrong:var@nope/db")
    monkeypatch.setenv("ALPHADECK_ANTHROPIC_API_KEY", "sk-wrong-var")
    s = Settings()
    assert (
        s.database_url == "postgresql://alphadeck:alphadeck@localhost:5544/alphadeck"
    )  # not captured
    assert s.anthropic_api_key is None  # not captured by the prefixed name
    monkeypatch.setenv("DATABASE_URL", "postgresql://right:var@host/db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-right-var")
    s2 = Settings()
    assert s2.database_url == "postgresql://right:var@host/db"
    assert s2.anthropic_api_key == "sk-right-var"


# --- the override smoke + the is-None coalesce fix (F1) ---


def test_env_override_of_a_dial_reaches_the_client(monkeypatch):
    """ALPHADECK_LLM_MODEL flows through Settings to a freshly-constructed LLMClient — the POINT of the move
    (a model change is an env edit, not a code edit), and the path a defaults-only golden never exercises.
    """
    monkeypatch.setenv("ALPHADECK_LLM_MODEL", "claude-test-override")
    get_settings.cache_clear()  # re-read the env (the singleton may have been built at the default)
    assert get_settings().llm_model == "claude-test-override"
    assert LLMClient(allow_live=True).model == "claude-test-override"


def test_research_model_override_reaches_a_research_client(monkeypatch):
    """ALPHADECK_LLM_RESEARCH_MODEL flows through Settings to a research-configured LLMClient — research is the
    one seam where the model choice is a live decision (the Opus default is overridable per environment).
    """
    monkeypatch.setenv("ALPHADECK_LLM_RESEARCH_MODEL", "claude-test-research")
    get_settings.cache_clear()  # re-read the env (the singleton may have been built at the default)
    assert get_settings().llm_research_model == "claude-test-research"
    assert (
        LLMClient(allow_live=True, model=get_settings().llm_research_model).model
        == "claude-test-research"
    )


def test_explicit_zero_override_is_honored_not_coalesced():
    """F1: dials use `is None`, NOT `or` — an explicit 0 / 0.0 / "" must survive (a falsy `or` would silently
    swap in the default, the #72-class latent freeze)."""
    assert LLMClient(max_tokens=0).max_tokens == 0
    assert LLMClient(timeout_s=0.0).timeout_s == 0.0
    assert LLMClient(model="").model == ""
    # max_retries: None -> the SDK default (2); an explicit 0 (the research one-shot) is honored, not coalesced.
    assert LLMClient().max_retries is None
    assert LLMClient(max_retries=0).max_retries == 0


def test_research_client_disables_sdk_retries():
    """get_research_client builds the Opus research client with max_retries=0 — an expensive web-search
    one-shot must NEVER auto-repeat at the SDK layer (a retry re-runs the whole search loop and re-spends; the
    $8-and-nothing amplification)."""
    from app.deps import get_research_client

    assert get_research_client().max_retries == 0


def test_anthropic_base_url_is_none_by_default_and_stored_when_given():
    """D7: base_url defaults None (=> the SDK default; LLMClient passes it to the SDK only when truthy); an
    explicit value is stored for the call path."""
    assert LLMClient().base_url is None
    assert (
        LLMClient(base_url="").base_url == ""
    )  # is-None honored; still falsy => SDK default at call time
    assert LLMClient(base_url="https://proxy.example/v1").base_url == "https://proxy.example/v1"
