"""The typed home for operational, env-overridable configuration.

This is the env-mutable sibling of ``CallConfig`` (``domain/config.py``), kept in a SEPARATE file on purpose:
``CallConfig`` holds the trust-validated (n=19) call-engine dials that must NEVER be silently env-overridden
(an env override of a validated threshold would change the calls); ``Settings`` holds the operational knobs —
LLM model dials, base URLs, throttle — that an operator SHOULD be able to change with a ``.env`` / environment
edit, not a code edit. The file boundary IS the "don't env-override a validated threshold" boundary.

Access rule (LOAD-BEARING — D5 of the refactor plan):
- ``get_settings()`` is a cached singleton — use it for STABLE config (LLM dials, base URLs, throttle, the
  user-agent / OpenFIGI key). It reads the process env ONCE.
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
        # A whole value chain (segments + names + prose), not a sentence. 2000 TRUNCATED a rich, discovery-
        # grounded organize (memory thesis ~2700 out tokens; a BROAD nuclear thesis: 6 segments / ~79 names /
        # ~3700-4000 out tokens) — `stop_reason=max_tokens` cut the tool JSON off empty → 0 segments → EVERY
        # discovered name fell to the 'Discovered' catch-all. 8000 covers the broad case with headroom; the model
        # stops on its own (~3700) — a bigger ceiling costs nothing (you pay only for tokens generated). The
        # `draft_structured` max_tokens guard (llm/client.py) makes any remaining truncation LOUD, not silent.
        8000
    )
    llm_decompose_timeout_s: float = (
        # A broad organize is a LONG generation — measured ~70-80s for the nuclear thesis (6 segments / ~79
        # names). 60s TIMED OUT it (fail-open → empty draft → all-Discovered), so 180s gives real headroom for
        # the on-demand action. (The call now STREAMS — llm/client.py — so a long request isn't dropped
        # server-side; the timeout is the client-side ceiling, the rare slow wait beating a silently lost draft.)
        180.0
    )

    # --- LLM seam (research upgrade — the narrative→chain RESEARCH pass, Slice 1) — operational dials ---
    # BEFORE the decompose call, a web-search research pass finds the CURRENTLY-LISTED companies in the thesis
    # space (recall -> research): it kills the run-to-run instability + the off-thesis drift, and proposes
    # CURRENT identities (post-rename) so the resolver places them. Runs on OPUS — research-synthesis quality
    # IS the leverage of this slice; the budget has ~10x headroom and one bounded pass stays inside it; the
    # draft is an infrequent, explicit, high-value action where the best model earns its cost. Separate dials so
    # the Sonnet decompose + Haiku flag seams are undisturbed. Fail-open: research trouble degrades to the
    # recall-only decompose (today's behavior), NOT an empty draft.
    llm_research_model: str = "claude-opus-4-8"  # best synthesis for the highest-leverage step
    llm_research_max_tokens: int = 4000  # a synthesis of many names + roles, not a sentence
    # 300s, raised in concert with the nginx + vite proxy timeouts: at 120s a thorough Opus multi-search pass
    # NEVER completed — it timed out, retried, and fell through to the recall-only decompose, so the research
    # substance was never actually tested. The SDK call timeout is the INNER bound; nginx alone wouldn't help.
    llm_research_timeout_s: float = 300.0
    # 3 (trimmed from 8) so ONE pass reliably FINISHES inside the 300s window — fewer searches that complete beat
    # more that time out. A dial: widen once real timings + convergence are seen. Also the per-call cost ceiling
    # (= the web_search tool's max_uses; the search-result context is the dominant Opus input cost).
    llm_research_max_searches: int = 3
    # Cache TTL (seconds) for the research SYNTHESIS, keyed by thesis + narrative-hash (see workbench/research_runner).
    # 0 DISABLES caching (always fresh) — the DEFAULT, precisely so the convergence gate-2 runs fresh each time (a
    # cache hit would mask convergence). After convergence is validated, set a production TTL (e.g. 21600 = 6h) so a
    # re-open / re-draft of the same narrative doesn't re-spend; the TTL also bounds staleness so the next rebrand
    # isn't re-stranded. The cached text is discovery CONTEXT, not a fact/signal — never the persisted-signal layer.
    llm_research_cache_ttl_s: float = 0.0

    # The web_search tool VERSION is a CODE-COUPLED capability contract, NOT a free-tuning dial. It lives here
    # for visibility + single-source, but flipping it via env WITHOUT the matching code change BREAKS the call:
    # web_search_20250305 (the default — simpler, no extra deps) and web_search_20260209 (dynamic filtering)
    # are NOT interchangeable — 20260209 REQUIRES the code-execution tool wired in ALONGSIDE it. Moving to
    # 20260209 is a deliberate PR that changes the version AND wires code execution as ONE gated change — never
    # an env flip. (Same dial-vs-code distinction as the config refactor: looks like config, coupled to code.)
    research_web_search_tool: str = "web_search_20250305"

    # --- Async draft job (the kick-off → poll registry, workbench/draft_jobs) — operational dials ---
    # The draft runs as an in-memory job (a daemon thread) the FE polls, so a multi-minute draft is never a
    # held-open request. The reaper bounds the registry: a finished job is dropped after FINISHED_TTL; a
    # still-running job past RUNNING_TTL is flipped to failed (the abandoned-job backstop). RUNNING_TTL sits
    # ABOVE the FE poll-cap (~600s) so the operator sees "timed out, try again" BEFORE the reaper acts — the job
    # is never orphaned under an active poll. A real draft floor is the Opus tail-sweep (~300s) + EDGAR discovery
    # over a large universe + decompose + narrate, so both are generous. (The real cost bound is the Opus client's
    # max_retries=0 + 300s SDK timeout — one bounded pass per job; this TTL is only the leak/stuck-thread backstop.)
    draft_job_running_ttl_s: float = 900.0
    draft_job_finished_ttl_s: float = 1800.0

    # --- LLM seam (discovery Slice 2 — the thesis→keyword generator) — operational dials ---
    # The LLM's FIRST bounded job in the EDGAR-first discovery: narrative -> SIGNAL + BROAD search keywords for
    # the EFTS enumerator. Cheap + bounded (a structured keyword list, NO web search) -> the Haiku flag dials'
    # cost class, but its OWN dials so the seams stay independent. Fail-open: no key -> no EFTS keywords -> the
    # caller degrades to the LLM tail-sweep / hand-authoring.
    llm_keyword_model: str = "claude-haiku-4-5-20251001"  # cheap; a keyword list, not reasoning
    llm_keyword_max_tokens: int = 512  # two short keyword lists, not prose
    llm_keyword_timeout_s: float = 20.0

    # --- LLM seam (the tier RECOMMENDER, INVARIANT #10) — operational dials ---
    # Haiku recommends signal/broad + a one-line reason per term in the operator's term set — VISIBLE + PENDING,
    # the operator confirms via the existing tier toggle. Cheap + bounded (a tier label + a short reason per term,
    # NO web search), its OWN dials so the seams stay independent. Higher max_tokens than keyword-gen: the output
    # is a row PER term (reason included), not two short lists. Fail-open: no key -> [] -> chips render with no
    # recommendation. If a term set ever exceeds this budget, adopt narrate_placements's batch+parallel pattern.
    llm_tier_rec_model: str = (
        "claude-haiku-4-5-20251001"  # cheap; a tier label + a short reason, not reasoning
    )
    llm_tier_rec_max_tokens: int = (
        2048  # the whole bounded term set + one-line reasons in a single call
    )
    llm_tier_rec_timeout_s: float = 30.0

    # --- LLM seam (the PURITY-ESTIMATE drafter, SURFACE 1b) — operational dials ---
    # A grounded proposal of the on-thesis revenue % — reads the located segment-footnote passage + the thesis
    # narrative, proposes the segment + its % of total revenue (from figures IN the passage), fail-open to
    # today's HUMAN. Its OWN dials so the seams stay independent. Haiku by default (grounded extraction +
    # a ratio, not deep reasoning); Sonnet is the ADHERENCE LEVER (as with the flag drafter) if the live gate-2
    # shows the arithmetic / grounding slipping. One on-demand call per name; no web search.
    llm_purity_model: str = (
        "claude-haiku-4-5-20251001"  # grounded extraction + a ratio, not reasoning
    )
    llm_purity_max_tokens: int = 512  # a segment + a % + a one-line reason, not prose
    llm_purity_timeout_s: float = 30.0

    # --- EDGAR-first discovery (the EFTS enumerator) — operational dials ---
    # The per-keyword pagination cap. NOT a recall limiter — a BACKSTOP against a pathological keyword: a low
    # cap silently drops real on-thesis names that surface deep (the Slice-1 gate measured 25 dropped at 200).
    # Speed comes from CONCURRENCY (``discover`` fans the pages over a thread pool under the shared rate limit),
    # never from capping recall. On-thesis filers file repeatedly + hit several keywords, so signal keywords
    # never reach this; the precision filter drops the deep collision tail. Keep it generous.
    discovery_hit_cap: int = 1000
    # The discovery thread-pool size: how many EFTS pages are in flight at once. Set a hair above
    # ``edgar_rate_per_sec`` so the SHARED RateLimiter (the global SEC budget) stays saturated despite
    # per-request latency, never to exceed it — the limiter, not the pool, is the throttle.
    discovery_max_workers: int = 10
    # COMPLETENESS-OR-FAIL: a run that fails to fetch more than this FRACTION of its EFTS pages (after
    # ``polite_get``'s retries) is DEGRADED — ``discover`` RAISES rather than return a partial universe as if
    # whole, and the draft surfaces "discovery unavailable" to the operator. A deterministic layer must FAIL
    # VISIBLY, never silently degrade to recall. Every skipped page is logged regardless; this only governs
    # when the residual is too large to call the universe complete. Small on purpose (retries heal transients,
    # so a post-retry failure is a real one).
    discovery_degraded_ratio: float = 0.05

    # Optional Anthropic base_url override (refactor D7): None => the SDK default (api.anthropic.com); passed
    # to the SDK by LLMClient ONLY when truthy (base_url="" is a broken URL). Buys a future proxy / self-host
    # + a test seam at zero cost today (nothing sets it). Read at the field name ALPHADECK_ANTHROPIC_BASE_URL.
    anthropic_base_url: str | None = None

    # The two LLM SYSTEM_PROMPTs live in files (backend/llm/prompts/*.md, Slice 3) so a prompt tweak is a
    # one-file PR. The loader caches after first read; set ALPHADECK_PROMPT_RELOAD=true in dev to re-read per
    # call (iterate the prose without a restart). Prod leaves it off — load once.
    prompt_reload: bool = False

    # --- Base URLs (Slice 2): the host/prefix is config, the PATH is logic (the builders append it) ---
    # Three distinct SEC hosts modeled separately (data.sec.gov · www.sec.gov/Archives · www.sec.gov/files),
    # so a copy-paste can't point one builder at another's host. Bases carry NO trailing slash; the builders
    # join with an explicit "/" (the slash-join discipline — a trailing slash here would double it). Defaults
    # == the exact pre-refactor literals (byte-identity gated). Override per host with ALPHADECK_<FIELD>.
    sec_data_base: str = "https://data.sec.gov"  # submissions + companyfacts XBRL
    sec_archives_base: str = (
        "https://www.sec.gov/Archives/edgar/data"  # filing docs + the provenance index link
    )
    sec_company_tickers_url: str = (
        # The EXCHANGE variant ({fields, data} rows in the SEC's own order) — it carries a PER-INSTRUMENT
        # exchange for the whole universe (ASML=Nasdaq vs ASMLF=OTC), which the canonical-primary rank needs;
        # the plain company_tickers.json has no exchange. Same host, same one-GET etiquette.
        "https://www.sec.gov/files/company_tickers_exchange.json"
    )
    stooq_base: str = "https://stooq.com"
    yahoo_chart_base: str = "https://query1.finance.yahoo.com"
    openfigi_url: str = "https://api.openfigi.com/v3/mapping"  # the fixed POST endpoint
    usaspending_api_base: str = "https://api.usaspending.gov/api/v2"
    usaspending_award_url_base: str = (
        "https://www.usaspending.gov/award"  # the human award link (a catalyst source_ref)
    )

    # --- Throttle (Slice 2): per-client rate limits + HTTP timeouts ---
    # Rate limits are per-client API etiquette (SEC 8/s, USASpending 5/s). Timeouts are shared at 30s for the
    # SEC / Stooq / FIGI GETs and 60s for the heavier USASpending POST — the pre-refactor values exactly.
    # polite_get's BACKOFF (retries/base/cap) stays an injectable function default (retry mechanics, not deploy
    # config) — see ingest/http.py.
    edgar_rate_per_sec: float = 8.0
    usaspending_rate_per_sec: float = 5.0
    http_timeout_s: float = 30.0  # EdgarClient + Stooq fetch + sec_tickers + FIGI
    usaspending_timeout_s: float = 60.0

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
    # Also secrets/deploy values, but NOT toggled by any test post-import (verified) — so these ride the
    # cached get_settings() at their edge (EdgarClient / UsaSpendingClient / sec_tickers / figi), unlike the
    # three above. user_agent maps to ALPHADECK_USER_AGENT via the prefix (alias for symmetry); openfigi_api_key
    # NEEDS the alias (its legacy name is unprefixed).
    user_agent: str | None = Field(
        default=None, validation_alias=AliasChoices("ALPHADECK_USER_AGENT")
    )
    openfigi_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENFIGI_API_KEY")
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The cached ``Settings`` singleton for STABLE config (the LLM dials, base URLs, throttle, and the
    user-agent / OpenFIGI key). Reads the process env ONCE.

    Do NOT use this for ``DATABASE_URL`` / ``ALPHADECK_TENANT_ID`` / ``ANTHROPIC_API_KEY`` — those are read
    late at the edge (see the module docstring). A test that overrides an ``ALPHADECK_*`` field must call
    ``get_settings.cache_clear()`` after monkeypatching the env.
    """
    return Settings()
