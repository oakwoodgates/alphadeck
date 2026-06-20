# INVARIANTS.md — the load-bearing rules, in one place

> Repo path: `docs/INVARIANTS.md`. `CLAUDE.md` states the **product** invariants (advisory-only, thesis-is-
> the-spine, opinionated-on-timing, inverse-loudness, …). This file states the **implementation** invariants
> that those depend on — the ones that were load-bearing in practice but lived only in our heads and in
> scattered code comments. A change that violates one of these is a bug, not a trade-off. Each names where
> it's enforced so it can't quietly rot.

---

## 1. No model-sourced numbers or firings  (CLAUDE.md invariant #3)

The LLM **augments, never sources.** It may draft the `counter_case` / explanatory prose citing existing
evidence IDs, a grounded plain-English explanation of an extracted FLAG candidate, or a value-chain DRAFT —
segments + names + thesis-fit prose — from a narrative (the two Workbench seams, below). It must **never** fire
a trigger, set a state / verdict / grade, or invent a number. Every trigger and grade comes from a
**deterministic parse** of data **or** a **one-time operator ratification**.

- *Enforced by:* the assembler signature (the LLM hook only injects `counter_case`); detectors are pure
  `f(point_in_time_data) -> SignalEvent`; the catalyst grade is `_derive_grade` (deterministic) or set at
  ratification — see `ingest/doe/feed.py`, `ingest/catalyst.py`, `calls/assembler.py`. The **theme
  conviction**'s grade + horizon are operator inputs on the ratified fact (`ingest/theme_conviction.py`),
  never the model's.
- *Also enforced by (the first LLM seam — the flag-explanation drafter, `backend/llm`):* the explanation
  rides a **separate rail** — `POST /workbench/facts/explain` has **no DB connection, writes nothing, and is
  never a field on `RatifyFactRequest`** — so it **structurally cannot become a fact**; the ratified number
  comes only from the operator's typed field. The prompt asks for components + direction, never the final
  value, but the **missing rail is the guarantee, the prompt only the courtesy**. Guarded by a
  no-ratify-field test + a **zero-`fact_*`-write** test on the explain endpoint. See `WORKBENCH_EXTRACTION.md`.
- *Also enforced by (the second LLM seam — the narrative→chain drafter, `backend/llm/chain_decomposition.py`):*
  it sources **no number** three ways — the tool schema + `ChainDraftOut` have **no value field** (structural);
  the prompt forbids any figure (Sonnet the adherence lever, the gate-2 manual no-number check its real test);
  and a drafted name is **UNSCORED** until the operator extract→ratifies it. The draft endpoint
  (`POST /workbench/theses/{id}/draft-chain`) is **RESPONSE-ONLY + test-enforced** — it holds a read-only conn
  (so NOT structural-by-absence like the flag seam) and writes nothing (`test_draft_endpoint_writes_nothing`:
  zero `fact_*` AND `basket_member`); the operator's promote is the only writer. See `CHAIN_DRAFTER.md`.

## 2. Entity resolution is an exact-membership ALLOWLIST, never fuzzy, never a denylist

A name is resolved to a security only by **exact membership** in a hand-curated table (e.g. the DOE feed's
`recipient_id` → ticker). Fuzzy search may be a **discovery net**, but it never *decides* a mapping. An
unknown entity is **dropped** (unresolved), not guessed — an allowlist, so noise fails closed. (Why this is
non-negotiable: fuzzy "Oklo" matches a polluted homonym holding **$48B** of national-lab contracts;
"Centrus" matches an unrelated NAC International. See `docs/DATA_SOURCES.md`.)

- *Enforced by:* `ingest/doe/entities.resolve` (exact `recipient_id` lookup); rejection test
  `tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy`.

**The Workbench securities resolver is the same rule on the populated universe.** The broadener
(`pipeline.populate_master`) loaded the SEC `company_tickers` universe into the master, so `master.search` now
spans thousands of names — but it is still a **discovery NET**: it surfaces exact master rows for the operator
to PICK (the row shows ticker + CIK, so a homonym like Oklo is disambiguated by sight), and it **never decides
a mapping** — the operator commits the exact `security_id`. More rows strengthen the net; exact-identifier-
decides is unchanged. The broadener itself writes **only** exact `company_tickers` mappings — an incomplete
row (missing CIK or ticker) is **dropped**, never guessed.

- *Enforced by:* `securities/master.search` (read-only — never an ingest, never a write); the broadener's
  exact `(cik, ticker)` keying that drops incomplete rows (`securities/master.populate_universe`,
  `tests/securities/test_populate_master.py`).

**The chain drafter is where that net DECIDES, and promote is where it's enforced (S5).**
`resolve_placements` (`backend/workbench/chain_draft.py`) runs each drafter-proposed name through the master
and auto-places **only on a unique EXACT ticker or name match**; several / partial / token-only matches or a
ticker/name contradiction go to an **operator pick** (ticker + CIK shown), and a no-match name is **ABSENT**
(shown, never placed). Auto-place **never rests on a judgment call** — a lone substring match is the Oklo-trap
heuristic, not membership. The UI makes this VISIBLE: PLACED auto-loads (drafted, prunable), AMBIGUOUS enters
the basket **only by an explicit operator pick**, ABSENT is shown-not-placed. And because the drafter writes
nothing, **promote is the single write-side check** — every placed `security_id` must be an exact master
member, else `404`.

- *Enforced by:* `workbench/chain_draft.resolve_placements` (exact ticker/name → PLACED, else pick / absent;
  `tests/workbench/test_chain_draft.py`); the promote membership guard + honored-authorship
  (`app/routers/workbench.py`; `tests/app/test_workbench_api.py`). See `CHAIN_DRAFTER.md`.

**The M2 back-half ingest honors the same rule on the WRITE side.** `pipeline.ingest_thesis` targets each
basket member's already-resolved `security_id` via `master.get` (issuer ticker + CIK) — **never a fresh fuzzy
resolve**; an unresolved member is skipped and a foreign id reported, never guessed. (It adds no invariant; it
honors this one.) See `FEED_LOOP.md`.

- *Enforced by:* `pipeline.ingest_thesis` (resolves via `master.get`, skips null / foreign ids;
  `tests/pipeline/test_ingest_thesis.py`).

## 3. Provenance to a real, checkable source on every trigger

Every fired trigger carries provenance that resolves to a **real source** — a working EDGAR Form-4 URL, a
`usaspending.gov/award/…` URL, a `price:TKR:date` computation record — **plus the parsed terms** (show the
work). No black-box outputs; if you can't show the source, don't surface the result. (CLAUDE.md #6.)

- *Enforced by:* `Provenance` on every `SignalEvent`; the card resolves them to clickable URLs; tests assert
  the award/accession refs are present (`test_doe_feed`, `test_hims_armed_call`).

## 4. No lookahead — every read is as-of a timestamp  (CLAUDE.md invariant #1)

The store is bitemporal (`valid_from` = event time, `recorded_at` = when we learned it). Detectors read
**only** through the as-of accessor; a fact dated after the query `asof`, or a correction recorded after
`known_at`, is invisible. Facts are **append-only** — a correction is a new row, never an in-place `UPDATE`.
Firings are **re-derived from facts on every read** (no persisted firing layer), so corrections propagate and
replay stays honest.

- *Enforced by:* `db/bitemporal.as_of` (security-scoped) + `as_of_thesis` (thesis-scoped, e.g.
  `fact_theme_conviction`), both delegating to the shared `_as_of`; the append-only DB trigger; the
  correction-axis test (`tests/ingest/test_pit.py` / the bitemporal tests).
- *Also honored by (M2):* the per-thesis ingest leaves `recorded_at` to the DB default `now()` — **never
  backdated** — and the daily cron pins `asof=today` / `known_at=now` (a live read), so a fact ingested today
  is invisible to an as-of read pinned at an earlier transaction time (`tests/pipeline/test_ingest_thesis.py`,
  the no-lookahead test). See `FEED_LOOP.md`.

## 5. Tenant isolation; production is a fresh tenant, never a destructive wipe

Every row carries `tenant_id`. Dev/demo data is a tenant; **production is a new tenant**. Never build a
destructive reset path or assume a single global tenant — seeds are idempotent and additive.

**Isolation is discipline + the poison-row test, NOT RLS** — the `security_id` FK carries no tenant, so
nothing at the database layer *forces* a read to pass the right `tenant_id`. The standing obligation: **every
new read path MUST route through the tenant-filtered accessors** (`db.bitemporal.as_of` / `as_of_thesis`,
`securities/master.*`, the `PointInTimeData` methods) — never a raw fact query — **and the isolation test
MUST GROW with each new read surface**. A forgotten filter on a raw query would leak with no DB backstop to
catch it. (RLS is the auth-era defense-in-depth; see `docs/PRODUCTION_TENANT.md`.)

- *Enforced by:* `tenant_id` on every table; `DEFAULT_TENANT_ID` for the demo; seeds upsert/append; the
  poison-row proof `tests/db/test_tenant_isolation.py` (grown for each new accessor — insider/price/theme,
  the three scoring facts, the Workbench scored read).

## 6. The call-assembler is pure and deterministic

`assemble_call(thesis, events, asof, cfg)` is a pure function: same inputs → byte-identical CallCard. No
DB/network/clock inside it; `asof` is always a parameter (no implicit "now"). The `calls` table is an
**accountability record**, never the read path (the API recomputes live).

- *Enforced by:* the assembler takes `cfg` + `asof` as parameters; determinism golden tests; the read path
  recomputes via `pipeline/call_for_thesis`.
- *Also honored by (M2 — Option B intact):* the daily cron (`pipeline.daily`) appends the day's call-of-record
  via `calls_repo.record_if_changed` — idempotent (the `calls` log is immutable [`no_update`] + non-unique, so
  a conditional append, not an UPSERT) — but it builds **no read-serving signal/score cache**; the serve path
  still recomputes from facts, and the log is never read back to serve. (`_canonical` makes the compare
  order-independent so a pure reorder can't re-append; `tests/repositories/test_calls_repo.py`,
  `tests/pipeline/test_daily.py`.) See `FEED_LOOP.md`.

## 7. Factor behavior on the property that drives it — never on grade-as-a-bundle or signal kind

The through-line (CALL_LOGIC). Entry **size** ← grade; **hold-or-don't** ← horizon; **starter + confidence
cap** ← the weaker (entry) key; catalyst **liveness** ← the agreement's horizon. **Never** re-couple these,
and never add an `if kind == …` branch where a property already carries the signal. A new signal kind
inherits correct behavior from its own properties.

**Theme conviction (M5b) is the canonical case.** It's a Key-1 *fallback* emitted at **`flip`**, so it caps
the call through the weaker-key path exactly like any flip — there is **no** "theme is capped" branch. The
only reads of the theme kind are: set membership in `conviction_kinds`; the **`own_conviction_kinds`**
exclusion (`conviction_kinds − {THEME_CONVICTION}` — the seam that keeps a future conviction kind inheriting
"own" automatically); the `theme_armed` **display flag**; and the `is_own` **ranking tiebreak**. None is a
behavior branch.

**The Workbench scorer is the front-half case.** Each of the four meters is factored on its own driving
property — purity on revenue-mix %, runway on cash/burn, catalysts on live-count + grade, dilution on
overhang % — with **no `if kind ==` branch anywhere in the engine**. The 0-4 pip cutoffs all come from
`CallConfig` (no hardcoded thresholds), the same property-keyed discipline.

- *Enforced by:* CALL_LOGIC §3/§4/§7; the verdict keys on `conviction_hold_threshold_days`, not kind; the
  confidence cap keys on `is_starter` (entry grade), not kind; the theme conviction caps via its `flip`
  grade (`signals/theme_conviction.broadcast` emits flip), with the `own_conviction_kinds` property in
  `domain/config.py` — see `docs/THEME_CONVICTION.md` §5. For the scorer: a **behavioral** magic-number test
  (a changed cutoff changes a pip) + a **lexical** float-literal scan of `workbench/scoring.py`
  (`tests/workbench/test_scoring.py`).

## 8. The dilution overhang is computed ONCE — never backed out of the clamped severity

`dilution_clock.overhang_pct(facts, sid, asof)` is the **single source** of the raw convert-overhang % —
read by **both** the back-half risk-veto (`dilution_clock.score`) and the Workbench dilution meter. The meter
buckets on this raw %; it **must NEVER recover the overhang from the risk `severity`**, because severity
*saturates*: `severity = min(overhang / dilution_overhang_severe_pct, 1) × risk_block_severity`, so above the
severe bar the overhang is unrecoverable from it. A future reader will be tempted to derive the meter from
the already-computed severity — that is the trap. One raw computation, shared; "—" when there are no live
converts (no fake zero). *(The same one-source discipline as the back half re-deriving firings on read — #4.)*

- *Enforced by:* `signals/dilution_clock.overhang_pct` (the shared pure helper) used by both `score` and
  `workbench/scoring._dilution`; the dilution-meter golden test asserts the raw % bucket
  (`tests/workbench/test_scoring.py`); the existing `tests/signals/test_dilution_clock.py` covers `score`.
