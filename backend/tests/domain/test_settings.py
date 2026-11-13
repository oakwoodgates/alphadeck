"""Settings — the typed home for operational, env-overridable config (refactor Slice 1).

A behavior-preserving move of the 6 LLM dials out of CallConfig, guarded by:
- the dial GOLDEN — defaults AND types identical to the pre-refactor CallConfig values,
- the LATE-READ rule (D5) for the 2 env-toggled vars (DATABASE_URL / ANTHROPIC_API_KEY)
  — monkeypatched AFTER import, the late edge read wins, and the ALPHADECK_ prefix does NOT capture them,
- the OVERRIDE smoke (ALPHADECK_LLM_MODEL reaches the client) + the is-None coalesce fix (F1 — an explicit 0
  is honored, never silently dropped): the path a defaults-only golden never covers.
No DB, no network, no key.
"""

from __future__ import annotations

import pytest

from db.session import database_url
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
    assert s.llm_decompose_max_tokens == 8000 and isinstance(s.llm_decompose_max_tokens, int)
    assert s.llm_decompose_timeout_s == 180.0 and isinstance(s.llm_decompose_timeout_s, float)
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


def test_discovery_hit_cap_defaults_are_per_tier(monkeypatch):
    """The per-tier EFTS hit caps: SIGNAL deep (5000 — a seed hit PLACES a name, so its deep pages are exactly
    what a low cap silently dropped) / BROAD shallow (1000 — collision-prone, corroboration only). Env
    hermetic so an ambient override can't mask the defaults."""
    monkeypatch.delenv("ALPHADECK_DISCOVERY_HIT_CAP", raising=False)
    monkeypatch.delenv("ALPHADECK_DISCOVERY_BROAD_HIT_CAP", raising=False)
    s = Settings()
    assert s.discovery_hit_cap == 5000 and isinstance(s.discovery_hit_cap, int)
    assert s.discovery_broad_hit_cap == 1000 and isinstance(s.discovery_broad_hit_cap, int)


def test_discovery_hit_cap_env_overrides_reach_the_cached_settings(monkeypatch):
    """Both caps are env dials (ALPHADECK_DISCOVERY_HIT_CAP / ALPHADECK_DISCOVERY_BROAD_HIT_CAP) — an operator
    tunes either without a code edit, independently."""
    monkeypatch.setenv("ALPHADECK_DISCOVERY_HIT_CAP", "7000")
    monkeypatch.setenv("ALPHADECK_DISCOVERY_BROAD_HIT_CAP", "300")
    get_settings.cache_clear()  # re-read the env (the singleton may have been built at the defaults)
    assert get_settings().discovery_hit_cap == 7000
    assert get_settings().discovery_broad_hit_cap == 300


def test_compose_hit_cap_fallbacks_agree_with_the_settings_defaults(monkeypatch):
    """docker-compose.yml ALWAYS injects both cap vars (with a ``:-`` fallback, because an empty env value can't
    parse as an int), so a compose fallback that drifted from the Settings default would SILENTLY override it
    on every containerized boot. Pin the two sources to each other."""
    import re
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    monkeypatch.delenv("ALPHADECK_DISCOVERY_HIT_CAP", raising=False)
    monkeypatch.delenv("ALPHADECK_DISCOVERY_BROAD_HIT_CAP", raising=False)
    s = Settings()
    for var, default in (
        ("ALPHADECK_DISCOVERY_HIT_CAP", s.discovery_hit_cap),
        ("ALPHADECK_DISCOVERY_BROAD_HIT_CAP", s.discovery_broad_hit_cap),
    ):
        m = re.search(rf"{var}: \$\{{{var}:-(\d+)\}}", compose)
        assert m is not None, f"{var} fallback not found in docker-compose.yml"
        assert (
            int(m.group(1)) == default
        ), f"{var}: compose fallback {m.group(1)} != Settings {default}"


def test_compose_injects_the_fetch_and_budget_dials_into_BOTH_services_at_the_settings_defaults(
    monkeypatch,
):
    """X2 / X7 / the fetch-ahead: compose passes an explicit ``environment:`` list (no ``env_file``), so a dial
    reaches a container only if it is listed there — the kill switch ``ALPHADECK_INGEST_FETCH_CONCURRENCY=1``
    would otherwise be dead on prod. Each must be injected into the backend AND the cron sidecar, and every
    fallback must equal the Settings default (compose always injects, so a drifted fallback silently wins).
    """
    import re
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    for var in (
        "ALPHADECK_INGEST_FETCH_CONCURRENCY",
        "ALPHADECK_SEC_GLOBAL_RATE_PER_SEC",
        "ALPHADECK_YAHOO_RATE_PER_SEC",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    for var, default in (
        ("ALPHADECK_INGEST_FETCH_CONCURRENCY", s.ingest_fetch_concurrency),
        ("ALPHADECK_SEC_GLOBAL_RATE_PER_SEC", s.sec_global_rate_per_sec),
        ("ALPHADECK_YAHOO_RATE_PER_SEC", s.yahoo_rate_per_sec),
    ):
        found = re.findall(rf"{var}: \$\{{{var}:-([0-9.]+)\}}", compose)
        assert len(found) == 2, f"{var} must be injected into backend AND cron, found {len(found)}"
        assert all(float(v) == default for v in found), f"{var}: {found} != Settings {default}"


# The security-hardening settings the backend container must receive (PR 2's timeouts and job caps, the login
# throttle, the daily allowance), each mapped to its Settings field. Compose lists the environment explicitly (no
# ``env_file``), so a variable missing here never reaches the container and its `.env` value is silently ignored.
_BACKEND_PASSED_THROUGH = {
    "ALPHADECK_DB_CONNECT_TIMEOUT_S": "db_connect_timeout_s",
    "ALPHADECK_DB_STATEMENT_TIMEOUT_S": "db_statement_timeout_s",
    "ALPHADECK_DB_LOCK_TIMEOUT_S": "db_lock_timeout_s",
    "ALPHADECK_DB_IDLE_IN_TRANSACTION_TIMEOUT_S": "db_idle_in_transaction_timeout_s",
    "ALPHADECK_JOB_STATEMENT_TIMEOUT_S": "job_statement_timeout_s",
    "ALPHADECK_JOB_LOCK_TIMEOUT_S": "job_lock_timeout_s",
    "ALPHADECK_DRAFT_JOBS_MAX_RUNNING": "draft_jobs_max_running",
    "ALPHADECK_INGEST_JOBS_MAX_RUNNING": "ingest_jobs_max_running",
    "ALPHADECK_LOGIN_MAX_FAILURES": "login_max_failures",
    "ALPHADECK_LOGIN_WINDOW_S": "login_window_s",
    "ALPHADECK_LOGIN_LOCKOUT_S": "login_lockout_s",
    "ALPHADECK_ALLOWANCE_EXTRACT_PER_DAY": "allowance_extract_per_day",
    "ALPHADECK_ALLOWANCE_PRICE_PULL_PER_DAY": "allowance_price_pull_per_day",
    "ALPHADECK_ALLOWANCE_AUTO_CONFIRM_PER_DAY": "allowance_auto_confirm_per_day",
    "ALPHADECK_ALLOWANCE_HOLDINGS_PER_DAY": "allowance_holdings_per_day",
    "ALPHADECK_ALLOWANCE_CHAIN_DRAFT_PER_DAY": "allowance_chain_draft_per_day",
    "ALPHADECK_ALLOWANCE_TERM_RUN_PER_DAY": "allowance_term_run_per_day",
    # security PR 5: the sign-in surface
    "ALPHADECK_OAUTH_AUTHORIZE_PER_ADDRESS_HOUR": "oauth_authorize_per_address_hour",
    "ALPHADECK_OAUTH_AUTHORIZE_PER_HOUR": "oauth_authorize_per_hour",
    "ALPHADECK_PASSWORD_CHECK_CONCURRENCY": "password_check_concurrency",
    "ALPHADECK_PASSWORD_CHECK_WAIT_S": "password_check_wait_s",
}
# The nightly prune (security PR 5, ``db.housekeeping``) runs in the CRON sidecar, so its retention days are passed
# to the cron service (the backend never reads them).
_CRON_PASSED_THROUGH = {
    "ALPHADECK_PRUNE_SESSION_DAYS": "prune_session_days",
    "ALPHADECK_PRUNE_OAUTH_DAYS": "prune_oauth_days",
    "ALPHADECK_PRUNE_DCR_CLIENT_DAYS": "prune_dcr_client_days",
    "ALPHADECK_PRUNE_ALLOWANCE_DAYS": "prune_allowance_days",
}


def test_compose_passes_the_prune_days_to_the_cron_at_the_settings_defaults(monkeypatch):
    import re
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    cron = _compose_service_block(compose, "cron")
    for var in _CRON_PASSED_THROUGH:
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    for var, field in _CRON_PASSED_THROUGH.items():
        m = re.search(rf"^\s+{var}: \$\{{{var}:-([0-9.]+)\}}", cron, re.M)
        assert m is not None, f"{var} is not passed to the cron in docker-compose.yml"
        assert float(m.group(1)) == getattr(s, field), f"{var}: {m.group(1)}"


def test_the_sign_in_surface_defaults(monkeypatch):
    """Security PR 5: self-registration is OFF unless the operator turns it on; the /authorize limits, the
    password-check cap and the prune's retention days (plan D4, D5, D7)."""
    for var in (
        *_BACKEND_PASSED_THROUGH,
        *_CRON_PASSED_THROUGH,
        "ALPHADECK_OAUTH_DYNAMIC_REGISTRATION",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.oauth_dynamic_registration is False
    assert (s.oauth_authorize_per_address_hour, s.oauth_authorize_per_hour) == (30, 300)
    assert (s.password_check_concurrency, s.password_check_wait_s) == (4, 10)
    assert (
        s.prune_session_days,
        s.prune_oauth_days,
        s.prune_dcr_client_days,
        s.prune_allowance_days,
    ) == (7, 1, 7, 7)
    assert (s.login_max_failures, s.login_window_s, s.login_lockout_s) == (5, 60, 60)
    monkeypatch.setenv("ALPHADECK_OAUTH_DYNAMIC_REGISTRATION", "true")
    assert Settings().oauth_dynamic_registration is True


def test_compose_passes_dynamic_registration_off_by_default():
    import re
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    backend = _compose_service_block(compose, "backend")
    m = re.search(
        r"^\s+ALPHADECK_OAUTH_DYNAMIC_REGISTRATION: \$\{ALPHADECK_OAUTH_DYNAMIC_REGISTRATION:-(\w+)\}",
        backend,
        re.M,
    )
    assert m is not None, "ALPHADECK_OAUTH_DYNAMIC_REGISTRATION is not passed to the backend"
    assert m.group(1) == "false"


def _compose_service_block(compose: str, service: str) -> str:
    """The text of one top-level service in docker-compose.yml (from ``  <service>:`` to the next service)."""
    import re

    m = re.search(rf"^  {service}:\n(.*?)(?=^  [a-z_-]+:\n|^[a-z]+:\n|\Z)", compose, re.M | re.S)
    assert m is not None, f"service {service} not found in docker-compose.yml"
    return m.group(1)


def test_compose_passes_the_hardening_settings_to_the_backend_at_the_settings_defaults(monkeypatch):
    """Security cleanup (Part B item 1): every timeout, job cap, login-throttle and allowance setting is listed in
    the backend's ``environment:`` with a numeric fallback EQUAL to its Settings default (compose always injects,
    so a drifted fallback would silently win; an empty one would fail boot)."""
    import re
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    backend = _compose_service_block(compose, "backend")
    for var in _BACKEND_PASSED_THROUGH:
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    for var, field in _BACKEND_PASSED_THROUGH.items():
        m = re.search(rf"^\s+{var}: \$\{{{var}:-([0-9.]+)\}}", backend, re.M)
        assert m is not None, f"{var} is not passed to the backend in docker-compose.yml"
        default = getattr(s, field)
        assert (
            float(m.group(1)) == default
        ), f"{var}: compose fallback {m.group(1)} != Settings {default}"


def test_compose_passes_the_uvicorn_concurrency_limit_to_the_backend_at_the_image_default():
    """``ALPHADECK_UVICORN_LIMIT_CONCURRENCY`` is a Dockerfile ENV (not a Settings field): its compose fallback must
    equal the image's build-arg default, or an unset `.env` would change the bound."""
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    backend = _compose_service_block(
        (repo / "docker-compose.yml").read_text(encoding="utf-8"), "backend"
    )
    dockerfile = (repo / "backend" / "Dockerfile").read_text(encoding="utf-8")
    image_default = re.search(r"^ARG UVICORN_LIMIT_CONCURRENCY=(\d+)$", dockerfile, re.M)
    assert image_default is not None
    m = re.search(
        r"^\s+ALPHADECK_UVICORN_LIMIT_CONCURRENCY: \$\{ALPHADECK_UVICORN_LIMIT_CONCURRENCY:-(\d+)\}",
        backend,
        re.M,
    )
    assert m is not None, "ALPHADECK_UVICORN_LIMIT_CONCURRENCY is not passed to the backend"
    assert m.group(1) == image_default.group(1)


# --- the late-read rule (D5): the 3 env-toggled vars, monkeypatched AFTER import ---


def test_database_url_is_read_late_at_the_edge(monkeypatch):
    """db.session.database_url() reads DATABASE_URL LATE (a fresh os.environ.get per call), so a monkeypatch
    AFTER import wins — never off the cached Settings. (A cached Settings frozen at import could point a test
    at the demo DB the db fixture truncates — the hazard this rule defends.)"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://late:read@localhost:5544/proof")
    assert database_url() == "postgresql://late:read@localhost:5544/proof"


def test_the_deployment_tenant_setting_is_gone():
    """``ALPHADECK_TENANT_ID`` and ``db.session.current_tenant_id`` are retired (auth PR 2): a request names its
    caller's workspace, a data read names the data tenant, a CLI names its workspace explicitly."""
    import db.session as session
    from domain.settings import Settings

    assert not hasattr(session, "current_tenant_id")
    assert "tenant_id" not in Settings.model_fields


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


# --- image_sha: the build-time code stamp (F1, migration 0044) -----------------------------------------


def test_image_sha_defaults_to_None_when_the_env_is_absent():
    """No build arg, no stamp. The `calls.code_sha` column's rule is "NULL when unknown, never
    fabricated", and that starts here."""
    assert Settings(_env_file=None).image_sha is None


def test_a_BLANK_image_sha_is_UNKNOWN_not_an_empty_string(monkeypatch):
    """LOAD-BEARING, not tidiness. The Dockerfile's `ENV ALPHADECK_IMAGE_SHA=$GIT_SHA` ALWAYS sets the
    variable — to "" when the rebuild forgot `--build-arg GIT_SHA=...`. Without the normalizer that empty
    string would be stamped on every call row as though it were a sha, which is a fabricated value by
    omission: the record would claim to know the code and be wrong, which is worse than NULL.
    """
    for blank in ("", "   ", "\t"):
        monkeypatch.setenv("ALPHADECK_IMAGE_SHA", blank)
        get_settings.cache_clear()
        assert Settings().image_sha is None, f"{blank!r} must read as unknown"
    get_settings.cache_clear()


def test_a_real_sha_passes_through_verbatim(monkeypatch):
    """No truncation, no normalization: the FULL sha is what lands on the row, so provenance can always
    show its work (#6). Shortening for display happens once, in `domain.config.short_hash`."""
    sha = "abc0b8c1d2e3f405162738495a6b7c8d9e0f1a2b"
    monkeypatch.setenv("ALPHADECK_IMAGE_SHA", sha)
    get_settings.cache_clear()
    try:
        assert Settings().image_sha == sha
        assert get_settings().image_sha == sha  # and it reaches the cached singleton
    finally:
        get_settings.cache_clear()


# --- public_origin: the hosted agent tools endpoint's switch (docs/AGENT_TOOLS.md §Hosted endpoint) -------------


def test_public_origin_is_unset_by_default_and_blank_is_unset(monkeypatch):
    monkeypatch.delenv("ALPHADECK_PUBLIC_ORIGIN", raising=False)
    assert Settings(_env_file=None).public_origin is None
    for blank in ("", "   "):
        monkeypatch.setenv("ALPHADECK_PUBLIC_ORIGIN", blank)
        assert Settings(_env_file=None).public_origin is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://deck.example.com", "https://deck.example.com"),
        ("https://deck.example.com/", "https://deck.example.com"),  # one trailing slash stripped
        ("  https://deck.example.com:8443  ", "https://deck.example.com:8443"),
        ("http://localhost:8081", "http://localhost:8081"),
        ("http://127.0.0.1:8001/", "http://127.0.0.1:8001"),
        ("http://localhost", "http://localhost"),  # a port is optional here
    ],
)
def test_public_origin_accepts_https_and_loopback_http(monkeypatch, raw, expected):
    monkeypatch.setenv("ALPHADECK_PUBLIC_ORIGIN", raw)
    assert Settings(_env_file=None).public_origin == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://deck.example.com",  # http on a public host
        "https://deck.example.com/mcp",  # a path
        "https://deck.example.com//",  # only ONE trailing slash is forgiven
        "https://deck.example.com?x=1",
        "https://deck.example.com#top",
        "https://user:pw@deck.example.com",
        "deck.example.com",  # no scheme
        "ftp://deck.example.com",
        "https://deck.example.com:99999",
    ],
)
def test_public_origin_refuses_anything_but_a_bare_origin(monkeypatch, raw):
    from pydantic import ValidationError

    monkeypatch.setenv("ALPHADECK_PUBLIC_ORIGIN", raw)
    with pytest.raises(ValidationError, match="ALPHADECK_PUBLIC_ORIGIN"):
        Settings(_env_file=None)
