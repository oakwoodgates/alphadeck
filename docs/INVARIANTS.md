# INVARIANTS.md — the load-bearing rules, in one place

> Repo path: `docs/INVARIANTS.md`. `CLAUDE.md` states the **product** invariants (advisory-only, thesis-is-
> the-spine, opinionated-on-timing, inverse-loudness, …). This file states the **implementation** invariants
> that those depend on — the ones that were load-bearing in practice but lived only in our heads and in
> scattered code comments. A change that violates one of these is a bug, not a trade-off. Each names where
> it's enforced so it can't quietly rot.

---

## 1. No model-sourced numbers or firings  (CLAUDE.md invariant #3)

The LLM **augments, never sources.** It may draft the `counter_case` / explanatory prose citing existing
evidence IDs. It must **never** fire a trigger, set a state / verdict / grade, or invent a number. Every
trigger and grade comes from a **deterministic parse** of data **or** a **one-time operator ratification**.

- *Enforced by:* the assembler signature (the LLM hook only injects `counter_case`); detectors are pure
  `f(point_in_time_data) -> SignalEvent`; the catalyst grade is `_derive_grade` (deterministic) or set at
  ratification — see `ingest/doe/feed.py`, `ingest/catalyst.py`, `calls/assembler.py`. The **theme
  conviction**'s grade + horizon are operator inputs on the ratified fact (`ingest/theme_conviction.py`),
  never the model's.

## 2. Entity resolution is an exact-membership ALLOWLIST, never fuzzy, never a denylist

A name is resolved to a security only by **exact membership** in a hand-curated table (e.g. the DOE feed's
`recipient_id` → ticker). Fuzzy search may be a **discovery net**, but it never *decides* a mapping. An
unknown entity is **dropped** (unresolved), not guessed — an allowlist, so noise fails closed. (Why this is
non-negotiable: fuzzy "Oklo" matches a polluted homonym holding **$48B** of national-lab contracts;
"Centrus" matches an unrelated NAC International. See `docs/DATA_SOURCES.md`.)

- *Enforced by:* `ingest/doe/entities.resolve` (exact `recipient_id` lookup); rejection test
  `tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy`.

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
