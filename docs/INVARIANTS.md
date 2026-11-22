# Invariants

The fourteen rules every change keeps. Breaking one is a bug, not a trade-off: if a request seems to need it,
stop and ask the operator. Read this before you change a data read, the call path, tenancy, discovery or
anything a model touches. Root `CLAUDE.md` lists the same numbers in one line each; this file holds each rule,
its reason where the code does not show it, and the modules and tests that enforce it.

Cite a rule as "invariant N" (`invariant #N` in code). Never renumber or reuse a number. Cites written before
the two old lists were merged use old numbers: see [Former numbers](#former-numbers).

## 1. No lookahead: history is judged on the public clock

Every historical read is as-of a timestamp, and a fact counts only from when it became public: a filing from
its SEC acceptance, a price bar from its settled session, a correction (a Form 4/A, a restatement, a vendor
re-version) from its own publication, never the original's. `db/public_clock.py` holds the one per-table
definition of that instant. A night runs from its session's close to EDGAR's next open, 06:00 market time on
the next weekday (`domain/market_time.next_edgar_open`). Signals, backtests, the Scoreboard and every history
view use only what was public at the simulated point in time.

- An as-of before the current night is a history view on the public clock; a live view reads the record clock,
  where the two coincide. `domain/market_time.serve_view` decides both for every serve path and the missed-night
  backfill; the backtest's public mirror runs the same SQL (`replay/export.py`).
- `recorded_at` is bookkeeping, not the history gate: every writer stamps the database's `now()` and never
  backdates. Do not add machinery, tests or measurements that replay what our database held at time T; measure
  call effects on the public clock.
- Facts are append-only: a correction is a new version, never an `UPDATE`. Firings are re-derived from facts on
  every read; nothing stores them.
- A price bar is withheld until its session settles (`ingest/prices/settle.py`).
- Read bounds derive from each reader's declared horizon (`signals/horizons.py`); a reader without a declaration
  fails a test. The per-request memo never crosses an as-of, `known_at` or tenant boundary.
- A read may skip a stored version only by a rule that uses nothing published after it (`db/set_aside.py`). An
  unchanged number re-reported later keeps the date of the filing that first made it public
  (`db/bitemporal.first_knowable_versions`).

Why: history must answer "how would the platform have judged this with what was public then?" Judging it by
what our database held replays our own outages and onboarding order.

Enforced by: the as-of accessors in `db/bitemporal.py`; the `no_update` triggers on fact tables
(`tests/db/test_bitemporal.py`); `tests/db/test_public_clock.py`; the mirror parity gate
`tests/replay/test_public_clock.py::test_the_public_mirror_is_row_for_row_the_live_public_read`;
`tests/pipeline/test_ingest_benchmarks.py::test_no_production_module_writes_the_backdated_stamp`; the self-audit's
`no_unsettled_bars` check (`tests/pipeline/test_settle_audit.py`); `tests/signals/test_horizons.py`;
`tests/domain/test_market_time.py`.

## 2. Every feature attaches to a thesis

Features attach to the thesis object: its narrative, basket, evidence, catalysts, signals, kill criteria and
expression. Do not build orphan screeners or standalone tools that float free of a thesis.

Why: the platform preserves the user's edge, the narrative, and patches its timing; a tool with no thesis does
neither.

Enforced by: review. `domain/thesis.py` (`Thesis`) is the object features attach to. A benchmark is reference
data, never a basket member
(`tests/pipeline/test_ingest_benchmarks.py::test_seed_benchmarks_is_idempotent_and_not_a_basket_member`); an ETF
joins a card only as a sleeve under a thesis (`tests/app/test_resolve_etf_api.py`).

## 3. The model augments; it never sources a number or a firing

Triggers fire from deterministic code against data. A model may read filings, draft prose and propose names or
structure. It never fires a trigger, sets a state, verdict or grade, or is the authority for a number. Every
trigger and grade traces to a deterministic parse or to a person's entry.

| Seam in `backend/llm/` | What keeps it from sourcing |
|---|---|
| Flag explanation | `POST /workbench/facts/explain` writes nothing and is not a ratify field (`tests/app/test_workbench_api.py::test_explaining_writes_no_fact`) |
| Chain drafter | no value field in the tool schema or `ChainDraftOut`; the draft endpoint writes nothing (`test_draft_endpoint_writes_nothing`); a drafted name is unscored until a person adds it |
| Purity estimate | grounded only in the passage it carries, else discarded (`tests/llm/test_purity_estimate.py`); computed on read, never a `fact_*` row |
| Tier recommender, keyword drafts | no number, company or ticker in the schema; display only (invariant 10) |

The missing write path is the guarantee; the prompt's no-number guard (`tests/llm/test_prompts.py`) is a
courtesy. A counting catalyst is a person's entry, and no module under `llm/` or `agent_tools/` reaches its
writer (`tests/repositories/test_thesis_catalyst_writers.py`). The platform-value machine writer reads only the
extractor's own parse (`platform_values/machine.py`).

Why: a model-sourced number corrupts a call without raising an error.

Enforced by: the assembler's only model hook fills `counter_case` (`calls/assembler.py`); detectors are pure
functions of point-in-time data; the tests above.

## 4. Opinionated on timing, deferential on the thesis

The system grades the call's strength and times the signal window. It does not judge whether a trade idea is
good: no feature ranks or vetoes theses on conviction. Expression text is advisory research context, never a
position size, an instrument choice or an order. A user's conviction on a member is a displayed weight that
never reaches the call; a signal's conviction grade is a different thing.

Why: the user's edge is the thesis; the platform's job is timing.

Enforced by: `backtest/sweep.MetricSliceError` refuses a thesis-level slice (`tests/backtest/test_slice.py`); the
backtest ledger is ordered by name, never by result (`tests/backtest/test_ledger.py`); a market-cap tier is a
slicing dimension only (`domain/market_cap.py`);
`tests/dashboards/test_ticker_lookup.py::test_operator_conviction_is_a_displayed_weight_and_never_reaches_the_place`;
`frontend/src/components/__tests__/gradeLabels.test.tsx`.

## 5. Advisory only

No order routing, brokerage writes, execution, sizing or portfolio-risk management. The gate withholds the
platform's go-signal and logs an override; it never blocks the user or places a trade. The user logs their own
fills; the firm's order, execution and risk systems own the trade.

Why: the product ends at the research-and-monitoring handoff.

Enforced by: the decision routes only append to the decisions log (`app/routers/theses.py`); `tests/calls/test_expression_boundary.py`;
`tests/calls/test_breakdown_dearm.py::test_dearm_expression_reads_as_signal_validity_never_a_sell`. No brokerage
client exists in the code.

## 6. Explainability: every output shows its source

Every call, score and signal carries its evidence and provenance. A fired trigger's provenance resolves to a
real, checkable source (an EDGAR filing, a USAspending award, a price computation) plus the parsed terms. No
black-box outputs: if you cannot show the work, do not surface the result.

Why: a user acts on a call only if they can check it.

Enforced by: `domain/signal.SignalEvent` refuses a fired signal without provenance
(`tests/domain/test_signal.py::test_fired_signal_requires_provenance`); `tests/ingest/test_doe_feed.py` and
`tests/calls/test_hims_armed_call.py` assert the award and accession references.

## 7. Inverse loudness

Quietness scales with how early a thesis is. Incubating does not nag: no alerts, low visual energy. Loudness is
reserved for Armed, in notifications as well as on screen.

Why: an alert the user learns to ignore is lost when it matters.

Enforced by: the push policy (`notify/policy.py`): a move that does not land in Armed is logged, never pushed,
and only entering Armed or an armed upgrade earns the loud header (`LOUD_KINDS`;
`tests/notify/test_slack_notifier.py::test_does_not_post_on_non_armed_transition`). Interaction principle WB#3
(`CLAUDE.md`) is the interface reading of this rule.

## 8. The dilution overhang is computed once

`signals/dilution_clock.py` computes the raw convertible-note overhang once (`_live_converts`, `_pct`). The risk
veto (`score`) and the Workbench dilution meter (`overhang_pct`, read by `workbench/scoring._dilution`) both use
that computation; the meter never recovers the overhang from the risk severity. With no live converts the meter
shows a dash, never a zero.

Why: severity saturates at the severe bar (`dilution_overhang_severe_pct`), so above it the overhang cannot be
recovered from it.

Enforced by: the shared helpers; the dilution pips in `tests/workbench/test_scoring.py`;
`tests/signals/test_dilution_clock.py`.

## 9. Recall is sacred

Surfacing every real on-thesis name is the point of discovery. Discovery over-includes; precision is a person
deleting visible junk, never a filter that might silently drop a real name. For any change to discovery,
classification, filters, caps or term tiers:

1. No change may reduce recall for precision, speed or cost without proving recall holds on the answer key.
2. No name loses placement or surfacing silently: a tier change, a filter or a lost placement is logged, flagged
   or shown.
3. Degradation is loud: a condition that cannot enumerate the full universe raises (a 503 with
   `DiscoveryDegraded`, `DiscoveryEmpty` or `DiscoveryNoTerms`), never returns a smaller set as if complete.
4. Caps and thresholds are backstops against pathology, never recall limiters; a normal run must never hit them.
5. Making a failure rarer is not making it safe: the failure mode itself must be visible or impossible.

When recall conflicts with tightness, determinism, cost or convenience, recall wins, and the trade-off goes to
the operator, not into code.

Why: a false positive is visible and deletable; a false negative is invisible and gone. Each past recall loss
was a plausible local fix that passed its own tests and was caught only by a recall re-score.

Enforced by: the answer-key re-score on every discovery-touching change (the `recall-rescore` skill): every
name in `ANSWER` (`tests/fixtures/recall_answer_key.py`) must surface; `SURFACED_ONLY_CANARY` names are reported,
never counted. Each draft carries its coverage report and capped terms (`ingest/edgar/fulltext.DiscoveryRun`);
`workbench/chain_draft.resolve_discovered_chain` brings every discovered in-master company to the draft; the
VERIFY tier keeps low-confidence names visible. Detail: `docs/DISCOVERY.md`.

## 10. The model recommends; a person decides

A model may flag, suggest or estimate as long as the recommendation is visible and pending and nothing changes
until a person confirms it. A recommendation is the model's loudest disagreement that still changes nothing on
its own; a suggestion that acts without confirmation is a decision, and invariants 3, 9 and 11 apply. A
confirmation is recorded by object: a term-set edit stamps authorship (`workbench/term_set.stamp_edited_term_set`),
a ratified fact records who ratified it, a basket sign-off sets `signed_off` and never `authored_by`.

A filer's own Form 4/A is not a recommendation. The system applies an amendment that the deterministic linker
resolves to the filing it amends, as an append-only system decision shown on Admin with its evidence and an
Undo; the filer's words are read by a fixed phrase list, never by a model. A person rules only on what the
system cannot resolve ([FILINGS](FILINGS.md), "Form 4/A: stored apart, resolved at read").

Why: the danger was never the recommendation; it was a recommendation applied without anyone seeing it.

Enforced by: `tests/workbench/test_term_set.py`; the chain drafter's propose-then-confirm seam
(`workbench/chain_draft.resolve_placements`); tier recommendations ride display only and are never persisted
(`tests/llm/test_tier_recommendation.py`).

## 11. Entity resolution is an exact allowlist

A name resolves to a security only by exact membership in a curated table or an exact identifier map. Fuzzy
search may discover; it never decides a mapping. An unknown entity is dropped as unresolved, never guessed.

- The master search surfaces exact rows, with ticker and CIK, for a person to pick.
- The company-list refresh writes only exact SEC mappings; an incomplete row is dropped, and a re-key onto a
  ticker another company holds is a surfaced conflict, never applied.
- The chain drafter places a name only on a unique exact ticker or name match; otherwise a person picks, and a
  name with no match is shown, never placed.
- Promote is the single write-side check: every placed `security_id` must be an exact member of the caller's
  master, else 404. It moves a non-primary sibling (another share class, a foreign ordinary line) to the CIK's
  primary instrument, visibly; a deliberate non-primary line cannot be represented, by choice.
- The per-thesis ingest targets each member's resolved `security_id`, never a fresh resolve.
- Display provenance (`matched_terms`, `discovery_source`, identity) is never promoted onto the decided
  `BasketMember`. The exception is `basket_member.surfaced_terms`, frozen at basket entry: it decides nothing
  and is never on the call path. Do not move it off the member.

Why: a fuzzy match once put a homonym's contracts on the wrong company; an allowlist fails closed.

Enforced by: `ingest/doe/entities.resolve` (`tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy`);
`securities/master_refresh.plan_refresh` (`tests/securities/test_master_refresh.py`); the drafter
(`tests/workbench/test_chain_draft.py`); the promote guard in `app/routers/workbench.py`
(`tests/app/test_workbench_api.py::test_promote_rejects_a_security_not_in_this_tenants_master`,
`::test_promote_canonicalizes_a_non_primary_sibling`); `tests/securities/test_canonical_primary.py`;
`tests/pipeline/test_ingest_thesis.py`.

## 12. Two tenant scopes; every read names its scope

Every tenant-bearing table belongs to one scope, named once in `domain/scopes.py` (`SCOPE_OF`); every other
table is named in `UNSCOPED_TABLES`. DATA is the one shared tenant (`DATA_TENANT_ID`): the company master,
every ingested fact and the platform values. WORKSPACE is one per user: the thesis and its children, the calls
of record, the decisions, the editions store and purity. Nothing else hard-codes a table's tenant. Detail:
`docs/TENANT_SCOPES.md`.

1. Every read names its scope through the map: fact reads go through the tenant-filtered accessors
   (`tenants_for`), never a raw query with a hand-picked tenant. A DATA writer writes the data tenant only
   (`require_write_tenant`).
2. The map grows with the schema; the isolation test grows with each new read surface.
3. No test uses the data tenant as a user's workspace: with the two equal, a missing filter reads the right rows
   by accident.
4. Every card-selecting read names its workspace: the caller's on a request path (`app.deps.get_workspace`; a
   thesis in another workspace is a 404). `WORKSPACE_ANY` is for system code only, never under `app/`,
   `dashboards/` or the route-facing radar.
5. The dashboard cache key carries the workspace.

Seeds are idempotent and additive. Never build a destructive reset path or assume a single global tenant.

Why: isolation is discipline plus the poison-row test. The `security_id` key carries no tenant, so nothing in
the database forces a read to pass the right one.

Enforced by: the boot check `scopes.assert_data_tenant`; `tests/db/test_scopes.py` (`test_every_table_has_one_owner`);
the poison-row proof `tests/db/test_tenant_isolation.py`; the two-user suite `tests/app/test_workspace_isolation.py`
(including `test_workspace_any_never_appears_in_request_path_code`); `tests/app/test_auth_gate.py`.

### The one exception

Closed: nothing deletes from the `calls` log or the editions store (`call_annotation` and the `edition*` tables).
The V2 cutover (`pipeline/v2_reset.py`), the one code path once allowed to, kept every row of them
(`HISTORY_KEPT` in `pipeline/v2_cutover.py`; `tests/pipeline/test_v2_reset.py`). Its only delete is unratified
orphan purity rows in `fact_revenue_mix`, and only with `--orphan-purity delete`.

## 13. The call assembler is pure and deterministic

`calls/assembler.assemble_call` is a pure function: the same thesis, events, as-of, config and held lines give a
byte-identical `CallCard`. No database, network or clock inside it; the as-of is always a parameter.

- The `calls` table is the accountability record, never the read path: the API recomputes the card live. The
  nightly appends a row only when the card changed (`repositories/calls_repo.record_if_changed`) and keeps no
  mutable signal or score cache. The log is immutable (`no_update` trigger).
- Each row names the policy it ran under: `config_hash`, `code_sha` (null when unknown, never invented) and
  `run_kind`, kept outside the change compare so a dial edit alone never re-records an unchanged call.
  `domain/config.config_hash` is not a `model_dump` hash: `frozenset` dials iterate in an order that depends on
  `PYTHONHASHSEED`, so a naive hash differs in every process.
- Which rows the Scoreboard scores (reconstructed, late, on-time) is its read policy (`docs/SCOREBOARD.md`). An
  edition reads only its own frozen pages and never writes `calls`.
- "Today" is a domain fact, not an environment fact: no backend module reads the ambient clock. Trading-day
  answers come from `domain/market_time` (`market_today`, `current_night`) on `Settings.market_tz`; a bad zone
  fails loud. `datetime.now(timezone.utc)` stays correct for transaction time.

Why: a card must be a function of its inputs, or a changed call cannot be told apart from changed facts or
dials. A container on UTC once recorded the next day's as-of.

Enforced by: `tests/calls/test_assembler_golden.py`; `tests/domain/test_config_hash.py`;
`tests/repositories/test_calls_repo.py`; the two scans in `tests/domain/test_market_time.py`
(`test_no_backend_module_reads_the_ambient_clock`, `test_no_test_module_reads_the_ambient_clock`), each with a
meta-test that it catches a violation.

## 14. Behavior keys on the property that drives it

Factor behavior on the property that drives it, never on grade-as-a-bundle or on a signal kind. Call strength
comes from grade; the signal-validity window from horizon; the provisional wording and the setup-strength cap
(wire name `confidence`) from the weaker entry key; catalyst liveness from the agreement's horizon. Never add an
`if kind == ...` branch where a property already carries the signal.

The theme conviction is the canonical case: emitted at grade `flip`, it caps setup strength through the
weaker-key path like any flip, with no theme branch. Its kind is read only for set membership
(`conviction_kinds`, `CallConfig.own_conviction_kinds`), a display flag and a ranking tiebreak. The Workbench
scorer keys each meter on its own property, with every cutoff from `CallConfig`.

Why: a branch on kind misbehaves for the next kind; a property carries the behavior to it.

Enforced by: the verdict keys on `conviction_hold_threshold_days` and the cap on `is_starter`
(`calls/assembler.py`, `calls/confidence.py`); `signals/theme_conviction.broadcast` emits `flip`;
`tests/workbench/test_scoring.py::test_scorer_has_no_magic_number_thresholds`. Detail: `docs/CALL_LOGIC.md`.

## Former numbers

`CLAUDE.md` used to number seven product invariants and this file ten implementation invariants, and the two
overlapped. Decode an old cite in code, git history or a pull request by its list:

| Old list | Old number and rule | New number |
|---|---|---|
| `CLAUDE.md` | 1 to 7 | 1 to 7, unchanged |
| this file | 1, no model-sourced numbers or firings | 3 |
| this file | 2, exact-membership allowlist | 11 |
| this file | 3, provenance on every trigger | 6 |
| this file | 4, no lookahead | 1 |
| this file | 5, two tenant scopes | 12 |
| this file | 6, the assembler is pure; today is a domain fact | 13 |
| this file | 7, factor on the property | 14 |
| this file | 8, 9, 10 | 8, 9, 10, unchanged |

An old bare `#N` meant the `CLAUDE.md` list, with exceptions: `#2` often meant the allowlist (now 11), `#5`
sometimes the tenant scopes (now 12), `#4` sometimes "never backdated" (now 1), and `#1/#3` meant "no
model-sourced numbers" (now 3). Applied migrations keep their old cites; they are never edited.

## Known gaps

- The basket roster is point-in-time from 2026-09-15 forward (`basket_snapshot`). A recompute reaching further
  back runs today's roster and says so (`RosterSource`, shown on the `/backtest` manifest). Only the roster and
  the counting catalyst entries carry history; the rest of the thesis and the security master are read as they
  are now. No surface may present a past recompute as what the platform said: the calls log is the record.
- Tenant isolation has no database backstop, and row-level security is not planned; a raw query that names the
  wrong tenant would leak.
- The database refuses `UPDATE` on `calls`, `call_annotation` and the `edition*` tables but not `DELETE`; that
  nothing deletes from them rests on review and the cutover's history checks.
