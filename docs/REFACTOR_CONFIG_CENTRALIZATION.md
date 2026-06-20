# Alpha Deck — REFACTOR GATE-1: centralize prompts / model dials / base URLs

> **Gate-1 plan — discovery + design, NO build, no self-start, no self-merge.** The map below is verified
> against the code (3 Explore agents + direct reads + one Plan-agent red-team), not taken on faith. Open
> decisions carry a recommendation to ratify at the gate. Build proceeds slice-by-slice, each its own PR +
> gate-2, after approval. **The bar is ZERO behavior change, proven by byte-identity characterization tests.**

## Context

Opening a testing-and-refactoring cycle. First target: **prompts, model dials, and base URLs are scattered
across logic files.** Today a prompt tweak is a code-edit-and-redeploy chore, a model/URL change means editing
a logic module, and there's no single typed home for environment config. Goal — three kinds of thing, three
homes:

- **Prompts** (multi-line prose, high-iteration) → **files** (a tweak becomes a reviewable one-file PR).
- **Model dials + base URLs + throttle** (operational, env-overridable) → a typed **`Settings`** object.
- **Secrets + deploy values** (already in `.env`) → absorbed by the same `Settings` as the single typed home.

**Honest scoping note (a correction to the brief's premise):** the current seams are *not* especially dirty.
The two LLM seams already inject a fake client as a **parameter** (no deep monkeypatching); the fetchers
monkeypatch `polite_get` at a clean module seam. So the win here is **not** "untangle tests reaching into
internals" — it's (a) prompt iteration as a one-file PR, (b) model/URL change as a config/`.env` edit not a
code edit, and (c) one typed definition of every env var. Right-sizing the claim keeps the gate honest.

---

## Discovery — the map VERIFIED (and what the brief missed)

### Tier 1 — Prompts (→ files). The brief's read is exactly right; two subtleties matter.
- **Exactly two prompts, both in `backend/llm/`** — confirmed, nothing elsewhere; the extract path is 100%
  deterministic (XBRL + regex), never LLM-prompted.
  - `chain_decomposition.py`: `SYSTEM_PROMPT` (88-105), `DECOMPOSE_TOOL` dict (27-86), user msg built inline
    (`f"Narrative:\n{narrative.strip()}"`).
  - `flag_explanation.py`: `SYSTEM_PROMPT` (51-66), `EXPLAIN_TOOL` dict (25-49), `_build_user` (79-87).
- **⚠️ Byte-identity subtlety (the crux of Slice 3):** both prompts open `"""\` (no leading newline) and use
  backslash line-continuations mid-bullet, so the **string VALUE** has *collapsed long-line bullets* mixed
  with *real structural newlines* (blank line after the intro; real breaks between bullets), no trailing
  newline. The `.md` must store the **evaluated value**, not the source text — and the test must hash the
  **loader's output**, not the raw file (see the gate).
- Tool schemas stay in code: `DECOMPOSE_TOOL` is asserted by **identity** (`is DECOMPOSE_TOOL`) in a test, and
  the LLM tests assert on the **tool dict, not the prompt string** — so "LLM tests pass unchanged" proves
  *nothing* about prompt bytes. Slice 3's gate is a **new** guard, not a preserved one.

### Tier 2 — Model dials (→ Settings). Confirmed, clean, low blast radius.
- The 6 LLM dials live in `CallConfig` (`domain/config.py` 185-211), each with a **load-bearing rationale
  comment** (the Sonnet-bump adherence lever; the 60s-vs-20s tail-latency story): `llm_model`,
  `llm_max_tokens=256`, `llm_timeout_s=10.0`, `llm_decompose_model`, `llm_decompose_max_tokens=2000`,
  `llm_decompose_timeout_s=60.0`.
- Read in **exactly two places**: `llm/client.py:42-44` + `app/deps.py:41-43`. **No test constructs
  `CallConfig(llm_…=)`** (tests pass only non-llm kwargs) → the move is safe. The other ~30 CallConfig fields
  are the **trust-validated (n=19) call-engine dials** — they STAY in CallConfig and must **not** become
  env-overridable (an env override of a validated threshold would silently change the calls).
- **⚠️ `or`-coalesce trap (a latent bug the refactor would *activate*):** `client.py:40-44` uses
  `model or DEFAULT_CONFIG.llm_model` — a **falsy**-coalesce. The instant a dial is env-overridable, a `0` /
  `0.0` / `""` value is silently discarded. Same class as the #72 latent freeze. The rewire must convert to
  explicit `X if X is not None else …`.

### Tier 2 — Base URLs (→ Settings). The brief undercounted; here is the full set.
**11 distinct hosts**, not the ~8 implied. 4 are module-level constants; **7 are inlined in builder f-strings.**

| Host | Where | Shape |
|---|---|---|
| `data.sec.gov` (submissions + companyfacts XBRL) | `edgar/submissions.py`, **`edgar/extract.py` (2nd URL — missed)** | inlined |
| `www.sec.gov/Archives` (filing docs) | `edgar/submissions.py`, `edgar/extract.py`, `edgar/converts.py`, **`app/schemas_api.py` `_EDGAR_ARCHIVES` (missed — the user-facing provenance link on call cards)** | inlined + 1 const |
| `www.sec.gov/files` (company_tickers.json) | `securities/sec_tickers.py` `_SEC_URL` | const |
| `query1.finance.yahoo.com` / `stooq.com` | `ingest/prices/eod_loader.py` (yahoo_chart_url, stooq_url) | inlined |
| `api.openfigi.com` | `securities/figi.py` `_OPENFIGI_URL` | const |
| `api.usaspending.gov` (API) + **`www.usaspending.gov` (human award link — missed)** | `doe/client.py` `_BASE`, **`doe/feed.py`** | const + inlined |
| `api.anthropic.com` | `llm/client.py` | **SDK default — no literal in code** (override only if we add a field) |

**Three distinct SEC hosts** → model them as **separate** Settings fields, never one "SEC base." The hazard in
Slice 2 isn't the host strings — it's **which builder reads which host** (a copy-paste that points
`submissions_url` at `/Archives` differs only in the host segment) and **slash-joins** when host moves to config.

### Tier 3 — Env reads + throttle (→ Settings as the typed definition).
- **Env vars (7):** `DATABASE_URL` + `ALPHADECK_TENANT_ID` (`db/session.py`, fn-wrapped, **late-bound**),
  `ANTHROPIC_API_KEY` (`llm/client.py:40`), `ALPHADECK_USER_AGENT` (×3: edgar/doe/sec_tickers),
  `OPENFIGI_API_KEY` (figi). Plus cron `TZ`/`RUN_AT` (compose + `scripts/daily_cron.sh`).
- **Throttle:** `ingest/http.py polite_get` has injectable **function-default** args (timeout 30, retries 3,
  backoff 1/30) — already test-injectable; **leave as-is.** Per-client rate limits (EdgarClient 8/s,
  UsaSpending 5/s) + scattered HTTP timeouts (30/60) → Settings.
- **⚠️ Instantiation-timing hazard (changes the design):** `db/session.py` and the LLM offline-gate test
  **monkeypatch env AFTER import** (`db` fixture sets `DATABASE_URL`; `test_flag_explanation` does
  `delenv ANTHROPIC_API_KEY`). A module-level `settings = Settings()` at import would **freeze env before
  those monkeypatches** → tests could read the wrong DB (the demo DB the `db` fixture **truncates**). The three
  vars the suite toggles post-import — **`DATABASE_URL`, `ALPHADECK_TENANT_ID`, `ANTHROPIC_API_KEY`** — must
  keep a **late read**.

### Stack facts that constrain the build
- `pydantic==2.13.4` is pinned **exactly** (with `fastapi==0.136.3`) for **openapi byte-reproducibility** (CI
  regenerates `openapi.json` and runs `git diff --exit-code`). **`pydantic-settings`/`BaseSettings` is NOT
  installed** — a new dependency, and a careless version could resolve `pydantic` **up** and break the FE
  contract drift-guard. Must be pinned to a release whose range admits 2.13.4.
- setuptools uses an **explicit `packages=[…]` allow-list** (`domain` is in it → Settings there needs no
  packaging change). Dockerfile is `COPY . .` + `pip install -e .` (editable over the copied tree; files read
  relative to the package, exactly like `seed_data/`) → **prompt `.md` files under `backend/llm/prompts/` ship
  fine; no `package_data` needed** (a non-editable wheel build would be the only thing that changes this).

---

## The design

**Settings home — `backend/domain/settings.py`** (new module beside `config.py`). Keeps the env-mutable
**`Settings`** visibly separate from the deliberately-NOT-env-mutable trust-validated **`CallConfig`** — the
"don't env-override a validated threshold" boundary becomes a file boundary. `domain` is already a declared
package (no `pyproject` change).

**The instantiation rule (load-bearing).** Access via a lazy **`get_settings()`** (cached) for **stable**
config (LLM dials, base URLs, rate limits, HTTP timeouts, user-agent, OpenFIGI key) — none of which any test
toggles post-import. The **three toggled vars** (`DATABASE_URL`, `ALPHADECK_TENANT_ID`, `ANTHROPIC_API_KEY`)
keep a **late read** (a fresh read per call, byte-identical to today's per-call `os.environ.get`); `Settings`
still **declares** them so the typed definition is single-source. Every characterization test that monkeypatches
one of these does so **after import** and asserts the late read wins (mirrors the existing offline-gate + #46
tests).

**Env-name safety (BaseSettings auto-reads env by field name).** Set `env_prefix="ALPHADECK_"` so generic
fields read `ALPHADECK_*` (kills accidental capture of a stray `TZ`/`HOST`/`MODEL` in CI/Docker, and makes the
LLM dials *deliberately* overridable as `ALPHADECK_LLM_MODEL` etc.). **Pin the 5 legacy names by explicit
alias** (`AliasChoices("DATABASE_URL")`, `ANTHROPIC_API_KEY`, `OPENFIGI_API_KEY`, `ALPHADECK_TENANT_ID`,
`ALPHADECK_USER_AGENT`) so they keep their exact current names (CI + compose inject them). **Do not enable
`env_file`** (today nothing reads a `.env` at the Python layer; compose injects env — enabling it would be a
silent behavior change).

**Prompts → files.** `backend/llm/prompts/chain_decompose.md` + `flag_explain.md` store the **evaluated string
value**. A small loader resolves `Path(__file__).resolve().parent / "prompts" / f"{name}.md"` (the `seed.py`
precedent), **normalizes newlines unconditionally** (`\r\n`/`\r` → `\n`, CRLF-proof), load-once-cached with a
`prompt_reload` flag for dev (re-read per call), and **fails LOUD (raises) on a missing file** — a missing
prompt is a deploy bug, *distinct* from the API-key fail-open. **Tool schemas + user-builders stay in code**
(structured logic; should break at import; the `is DECOMPOSE_TOOL` identity test keeps them constants). Add
`.gitattributes`: `backend/llm/prompts/*.md text eol=lf`.

---

## Open decisions — recommendations to ratify

| # | Decision | Recommendation |
|---|---|---|
| D1 | Where `Settings` lives | **`backend/domain/settings.py`** (new file, beside `config.py`) — file boundary = the "not env-overridable" boundary. |
| D2 | Tool schemas: code or JSON | **Keep in code** — they're logic, tested by `is`-identity, should break loudly; JSON gains nothing and breaks the identity assert. |
| D3 | Prompt reload | **Cache-by-default + a `prompt_reload` Settings flag** (re-read per call in dev); fail-LOUD on missing file. |
| D4 | Prompt byte-identity vs readable wrapping | **Store the evaluated value verbatim** (strict byte-identity; long-line bullets) this pass. Reflowing to natural newlines is a *deliberate, separate prompt-eng PR* — exactly the workflow this refactor unlocks. |
| D5 | Env instantiation | **Lazy `get_settings()` (cached) for stable config; late read for the 3 toggled vars.** The single most important correctness rule (defends the truncate-the-demo-DB hazard). |
| D6 | Throttle granularity | Move **rate limits + HTTP timeouts** to Settings; **leave `polite_get`'s backoff args** as injectable function defaults (retry mechanics, not deploy config). |
| D7 | Anthropic `base_url` | Add optional `anthropic_base_url: str \| None = None`; pass to the SDK **only when truthy** (today nothing is passed — `base_url=""` is a broken URL). Cheap; enables a future proxy + a test seam. |

---

## The slices (each independently reversible + its own PR + gate-2)

### Slice 1 — `Settings` foundation + the LLM dials
- Add `pydantic-settings` (pinned to admit pydantic 2.13.4). New `domain/settings.py`: `Settings(BaseSettings)`
  with `env_prefix="ALPHADECK_"` + legacy-name aliases; lazy `get_settings()`. **Declare every env var** (the
  typed inventory), but keep the late read for `DATABASE_URL`/`TENANT`/`ANTHROPIC_API_KEY`.
- Move the 6 `llm_*` dials `CallConfig` → `Settings` (**verbatim rationale comments; exact types** — `int`
  256, `float` 10.0). Rewire `client.py` + `deps.py` to read from Settings with **`is None`** coalesce (kills
  the F1 trap). Add `anthropic_base_url` (D7).
- **Gate:** golden test `Settings()` defaults **== old CallConfig llm values AND types**; `pip freeze` shows
  `pydantic` still 2.13.4 **and** `openapi.json`/`types.gen.ts` regenerate to a clean `git diff` (the drift
  guard); a test that sets a legacy var **after import** and asserts it's read (and the prefixed name is not);
  the LLM suite passes unchanged. Grep `model_dump`/whole-config snapshots first (removing 6 fields changes
  `CallConfig`'s dump).
- **Files:** `domain/settings.py` *(new)*, `domain/config.py`, `llm/client.py`, `app/deps.py`,
  `pyproject.toml`, `.env.example`.

### Slice 2 — base URLs + rate limits + the safe remaining env
- Move all **11 hosts** → distinct Settings fields (3 SEC hosts separate). Refactor the **7 inlined builders**
  to read the base from Settings (path-building stays in code; **watch slash-joins**). Move per-client rate
  limits + HTTP timeouts. Reroute `ALPHADECK_USER_AGENT`×3 + `OPENFIGI_API_KEY` through Settings (safe — not
  toggled post-import).
- **Gate:** a frozen-literal byte-identity test **per builder** (all 11; capture each builder's output for a
  fixed input *before* refactoring — the `test_yahoo_adapter_matches_fetch_eod_exactly` pattern); ingest suite
  unchanged; confirm the **cron sidecar still resolves** DB+tenant+UA (it has no fast feedback loop).
- **Files:** `eod_loader.py`, `edgar/submissions.py`, `edgar/extract.py`, `edgar/converts.py`,
  `app/schemas_api.py`, `doe/client.py`, `doe/feed.py`, `securities/sec_tickers.py`, `securities/figi.py`,
  `ingest/edgar/client.py`, `domain/settings.py`, `.env.example`.

### Slice 3 — prompts → files + loader + contract test
- Externalize the two `SYSTEM_PROMPT`s to `backend/llm/prompts/*.md` (the evaluated value). Add the loader
  (CRLF-normalizing, `__file__`-relative, cached + `prompt_reload`, fail-loud). Keep tool schemas + builders in
  code. Add `.gitattributes`.
- **Capture mechanically, not by hand:** in a throwaway REPL, `import` the module, author each `.md` until
  `loader.load(name) == SYSTEM_PROMPT` in-process, then record `sha256` + `len`. Don't commit a writer.
- **Gate (two durable tests):** (1) **frozen-hash golden** — `sha256(load(name).encode()) == "<captured>"`
  (survives the constant's deletion; a stray newline/CRLF flips it); (2) **prompt-contract** — the
  load-bearing guards are present in the *loaded* prompt (decompose still FORBIDS numbers — invariant #3;
  explain still demands grounding + direction-only). The hash catches bytes; the contract catches "easy to
  edit → silently dropped the guard." LLM suite unchanged.
- **Files:** `llm/chain_decomposition.py`, `llm/flag_explanation.py`, `llm/prompts/*.md` *(new)*, a loader
  (`llm/prompts.py` *new*), `.gitattributes`, `tests/llm/…`.

**Ordering:** S1 → S2 → S3, each reversible. S1 is the foundation; S2 depends on `Settings` existing; S3 is
independent of S2 but sequenced last (lowest behavioral risk *given* the hash+CRLF defenses).

---

## Invariants honored
| Invariant | How |
|---|---|
| **#3 LLM never sources a number** | the decompose prompt's no-number guard is now a **durable contract test**; model dials becoming env-overridable is safe *because* the LLM never sources a number (only draft *quality* changes, never a computed value). |
| **Trust-validated call logic** | the n=19 CallConfig dials stay in `CallConfig`, deliberately **not** env-overridable (the file boundary enforces it). |
| **No-lookahead / advisory-only** | untouched (no data-path or call-path logic changes). |
| **OpenAPI contract** | no FastAPI schema/docstring changes expected; the only risk is a transitive pydantic bump — **gated** in S1. |

---

## Verification (end-to-end)
- **Per-slice byte-identity** is the gate (above): Settings defaults == old values/types; 11 frozen URL
  literals; the prompt frozen-hash + contract.
- **Full suite green, DB tests EXECUTED** against `alphadeck_test` (0 skipped — a large skip count is not a
  pass), **never** the demo DB the `db` fixture truncates. `ruff`/`black` clean.
- **Override smoke (proves the F1 fix + env wiring):** set `ALPHADECK_LLM_MODEL` / a base-URL override / the
  `prompt_reload` flag and confirm each takes effect — the *point* of the refactor, and the path no
  defaults-only test covers.
- **Contract drift:** regenerate `openapi.json` + `types.gen.ts`; `git diff --exit-code` clean.

## Process
Gate-1 only — **no build**. On approval, the first step honors the established process: **commit this plan as
the gate-1 doc on a branch + send the head SHA** for codeload review, then build **Slice 1** and bring it at
gate-2 (no self-merge).

## Critical files
- `backend/domain/settings.py` *(new)* — the typed home; `get_settings()` + prefix/alias/instantiation rule
- `backend/domain/config.py` — the 6 dials + rationale to move; whole-config-dump risk
- `backend/llm/client.py` — the `or`→`is None` fix; the dial + late key read; `anthropic_base_url`
- `backend/app/deps.py` — the decompose-client dials
- `backend/db/session.py` — the late-bound DB/tenant reads (preserve late binding)
- `backend/pyproject.toml` — the pinned `pydantic-settings` add (openapi-pin risk)
- `backend/ingest/prices/eod_loader.py`, `edgar/*.py`, `app/schemas_api.py`, `doe/*.py`, `securities/*.py` — the 11 URL builders
- `backend/llm/prompts.py` *(new)* + `backend/llm/prompts/*.md` *(new)* — the loader + externalized prompts
