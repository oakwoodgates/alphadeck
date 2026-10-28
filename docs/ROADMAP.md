# ROADMAP.md — the live plan

> Repo path: `docs/ROADMAP.md`. **This is the live plan** — where we are, what's next, what's locked, what's
> parked. History lives in the **shipped log** (one line per arc + a pointer); the detail lives in the canon
> docs, never here. Siblings: **`STAGE_MODEL.md` (the organizing frame — read first)**, `CALL_LOGIC.md` (the
> brain), `INVARIANTS.md` (the load-bearing rules), the stage docs (`DISCOVERY.md`, `WORKBENCH_EXTRACTION.md`,
> `WORKBENCH_ENRICHMENT.md`, `TRIAGE.md`, `CHAIN_DRAFTER.md`, `FEED_LOOP.md`), `RECALIBRATION.md` (the dial
> backlog), `PROJECT_OVERVIEW.md` (design rationale).

---

## Where we are

**Both halves are built; the whole pipeline runs on real data.**

- **The back half** (MONITOR): bitemporal store, two-key arming, the pure call-assembler, the catalyst
  subsystem, the M5 per-member ranked menu + theme arming — and it **feeds itself** (per-thesis ingest +
  the daily call-of-record cron, `FEED_LOOP.md`). Trust work shipped: the replay harness, the first
  recalibration pass, the production-tenant cut.
- **The front half** (DISCOVER → SURFACE → TRIAGE): narrative → EDGAR-first discovery (deterministic EFTS
  off an operator-seeded term set, recall 31/32 against the answer key, honest coverage/cap/sweep reporting)
  → drafted chain the operator ratifies → the **three-gate TRIAGE flow** (cheap cut → mark-for-data →
  finalize on data; `TRIAGE.md`) with per-section data pulls, honest extraction flags (one observed
  condition, one label), canonical-primary CIK resolution, and the archetype decided once on the rail.
- **Live-fire hardening happened on a real operator thesis** (the MVP-gate walkthrough): the every-name
  dual-class mis-flag, the invisible ratify, the 2018 stale-marketable composition, and the ticker-less
  `master.get` crash were each caught live and fixed with regression tests.

**The MVP gate RAN (2026-07-10)** — a real 68-name operator thesis end-to-end on real data: 68 names ingested
(+41k insider txns, +8k bars), the call computed **WARMING / not-yet / `exit_by` signal-validity horizon**
with a real Form 4-linked insider
conviction on a mega-cap member against volume-backed watch names — the two-key discipline live; the daily
cron sidecar runs nightly and its same-day idempotency held in production (0 appended · 11 unchanged).
The North Star's *Armed* call now awaits a real two-key coincidence — correct behavior, which the cron
will catch.

**Since the gate:** **decision capture** shipped (#143/#144/#146 — the operator-decisions log:
take/pass/close/void, the derived position that makes **Managing** (in-position thesis monitoring, not risk
management) reachable, the gate's override
record, the Scoreboard's operator column accumulating from day one); **catalyst + kill-criteria
authoring** (#147 + the FE pair — the Key-1 conviction-fact form with a REQUIRED citation, the Cockpit
calendar/kill editors, and the third wipe-trap fixed structurally); **thesis archive** (board hygiene —
archive-never-delete; the cron walks only the live board); and the **notify PIPE** (transition
detection vs the prior call-of-record + a Notifier seam — delivery deliberately deferred; a channel is
one adapter away).

**Post-MVP — SCORE, then honesty + ops hardening (through #217).** The sixth stage shipped: the
**Scoreboard v1** (SCORE) — the episode ledger over the call-of-record + the operator track + gated
aggregate metrics, with **replay-history-alongside** now built beside it (#158–#164; `SCOREBOARD.md`).
Then, now that the platform is live, a run of **honesty + ops** work: the read-only **display-signal
framework** (SMA / 52-week / volume-regime / insider-flow tape context beside the call, never an input;
#192–#194, #204–#206, `DISPLAY_SIGNALS.md`); the **cron-freeze remediation** (R1–R8 — the ~11-day insider
freeze that made "feeds itself" only *literally* true at #196: the key-classed EDGAR TTL + a recording gate
+ a run-of-record log + a health pager + catch-up-on-boot + the backend TZ pin; #196–#203,
`POSTMORTEM_CRON_FREEZE_2026-07.md`); and the recent **ops + honesty batch (Slices 1–4)** — the operator
**admin ops surface** (freshness/health readout + Run-daily-now; `ADMIN.md`); **clock honesty** (the Cockpit
entry-window `arm_until` clock, the Scoreboard "awaiting forward bar" over a misleading 0.0%, and a record
staleness line); the **insider open-market + issuer-self screens** (SEC code-`P` primary-market subscriptions
and a company's Form 4 on *its own* stock, both screened out of Key-1); the **Scoreboard record-provenance +
maturity horizon** (freeze-touched episodes stay ledger-visible but leave the aggregate metrics); the
operator **DB-snapshot button + nightly backup**; and the **Board view fixes** (the dead per-name Position,
the silent error-vanish, the Decision-Queue deep-link) (#207–#216). The insider screens are **preventive and
currently latent** — correctness/honesty layers whose live payoff waits on matching live data, not fixes to a
visible number. Along the way the **demo DB was truncated by a shared-Postgres pytest hazard and RECOVERED
from a 2026-07-17 `pg_dump` — a REAL restore, not a synthetic rebuild** (all six theses + the real
call-of-record, migrations `0021→0024`; one restored thesis re-armed on a forward `daily` run), and **#217 removed
the root cause** (a per-worktree test DB + a fail-closed guard that refuses to truncate any
non-`alphadeck_test` name).

**The cockpit became a scannable instrument (through #262).** Past the Scoreboard episode drill-down
(#227–#230), a batch of SURFACE-identity, radar, and ops work landed — the ETF safe-exposure **sleeve** +
its net-flow ingest leg (#233–#237), the origin/country + **foreign-filer** identity tells and the
`enrich_identity` lifecycle (#238–#242, #251), the **SPAC radar** deal tape (#244), **OTC price-symbol
healing** (#252), **empty-seed** ("∅ no EDGAR hits") visibility (#253), and `market_today()` closing
INVARIANTS §6 (#250). Then the headline: **Business-type M1** (#255) retired the size-tier archetype for a
durable two-level **SIC → leaf → super-sector** characterization (operator-editable maps, the operator's
per-security re-tag winning), and the **cockpit-refinement arc** (#256–#262) turned the basket table into a
scannable **display-signal instrument** — trailing-return columns (1d/7d/30d/90d/1Y), `RVOL 8D`/`RVOL 20D`,
insider `Ins 30d`/`Ins 90d`, three lenses (call-state · business-type · value-chain), and within-group sort
— **every column and lens off the call path**, riding that business-type characterization.

> **Keep the trust state honest (the load-bearing recalibration caveat).** "The platform feeds itself daily"
> is TRUE (literally so only after #196 — the EDGAR cache silently froze insider data ~11 days until the
> key-classed 12h TTL; `POSTMORTEM_CRON_FREEZE_2026-07.md`); **"the forward trust loop is closed" is NOT** —
> the **Scoreboard v1** now stands as its instrument,
> but evidence arrives only as matured forward outcomes accrue (nothing is validated at n=0, and no small
> threshold closes the loop). The `n ≥ 5` aggregate-metric gate is a UI safeguard against over-reading early
> summaries, **not** an evidence threshold. The first
> recalibration pass (#45) tuned **direction from the literature + the mechanism**; the replay only
> *illustrated*, it never *selected* a value. Its seed is **n=19 episodes, only UNH a long forward arc**, so its
> honest claim is "the dials are grounded in replayed history + the one obvious lever was inspected" — **NOT**
> "the system is validated." It shipped **zero dial changes** (the disciplined result at n=19; the one give-back
> — the insider-core signal-validity window ended ~35d past the realized peak — is a setup-strength *shape*
> problem [wire field `confidence`; age-decay, `RECALIBRATION.md` B.4], not
> a magnitude the binary liveness window can fix). The daily call-of-record is the forward RECORD the Scoreboard
> now tracks — which **began 2026-07-10 and is still accruing** (a night the cron missed can be reconstructed
> with the clock pinned, but a reconstructed row is reported, never scored — `SCOREBOARD.md`). That launch
> record is itself freeze-touched, so Slice 3's per-episode provenance flags (#214) hold it out of the
> aggregate metrics: they stayed **honestly empty** until the first clean-data arms matured, and an eligible
> pool crossing the `n ≥ 5` gate is a sample passing a UI safeguard, not evidence (`SCOREBOARD.md`).
> Do not round "feeds itself" up to "validated forward."

## Organizing principle — two halves on one spine

- **Front half (hunt + research):** narrative → value chain → surfaced, scored names → a chosen basket.
- **Back half (park + time):** the basket becomes a parked thesis → incubate → trigger → graded call →
  monitor the entered thesis through the signal-validity window.
- **The spine:** the thesis as a first-class object connects the two (`PROJECT_OVERVIEW.md` §4).
- **Sequencing rule (locked): trust before front-half build** — honored; both are behind us.

---

## Shipped log — one line per arc (detail = the canon doc + the PRs)

| Arc | PRs | Canon |
|---|---|---|
| Back-half core: store, two-key arming, assembler, catalysts, through-line | ≤ #39 | `CALL_LOGIC.md`, `CATALYST_CONVICTION.md` |
| Confirmation honesty + the Workbench design pass (mockup + spec) | #40, #41 | `docs/mockups/mockup_workbench_nuclear.html` |
| M5: per-member ranked menu + theme arming | #42, #43 | `CALL_LOGIC.md`, `THEME_CONVICTION.md` |
| Phase 1 trust: replay harness · recalibration pass 1 (zero dial changes) · production tenant | #44, #45, #46 | `REPLAY.md`, `RECALIBRATION.md`, `PRODUCTION_TENANT.md` |
| Workbench MVP: persistence seam · ratify bridges + seed · scoring engine · UI · authoring | #47–#54 | `WORKBENCH_SCORING.md`, `TRIAGE.md` |
| Extract → ratify → score hybrid + the broadener (any-name universe) | #55–#58 | `WORKBENCH_EXTRACTION.md` |
| LLM seam 1: the FLAG-explanation drafter (structural no-write bound) | #59 | `WORKBENCH_EXTRACTION.md` |
| LLM seam 2: the narrative → chain drafter (response-only; exact membership decides) | #61–#65 | `CHAIN_DRAFTER.md` |
| M1 front door (create/edit thesis, the wipe-trap) + M2 feed loop (ingest, cron, price seam, sidecar) | #67–#73 | `CHAIN_DRAFTER.md`, `FEED_LOOP.md`, `DATA_SOURCES.md` |
| Config centralization (prompts/dials/URLs → files + typed Settings) + quick-win refactors | #75–#81 | — |
| EDGAR-first discovery: research two-step → EFTS pipeline → term set → seed-edit UI → async draft job | #83–#101 | `DISCOVERY.md` |
| The #10 recommendation family: tier rec · off-thesis flag · SURFACE estimates · derived archetype · enrichment | #102, #105–#118 | `INVARIANTS.md` #10, `WORKBENCH_ENRICHMENT.md` |
| TRIAGE crafting: include/find/weight + To-Review ruleset + row polish | #113–#122 | `TRIAGE.md` |
| Honest discovery: coverage + retry, capped terms, tail-sweep tri-state, status strip, worker guard | #123, #124 | `DISCOVERY.md`, `INVARIANTS.md` #9 |
| Draft persistence: the `/data` cache volume + the write-only draft run log + the run loader | #125, #131 | `DISCOVERY.md` |
| Canonical-primary CIK resolution (composite rank; the empirical gate killed SEC-file-order) | #126 | `DISCOVERY.md` §3, `INVARIANTS.md` #2 |
| The three-gate TRIAGE round: board partitions + low-quality lens · archetype-null (rail = single home) · per-name get-data | #127–#129 | `TRIAGE.md` |
| Gate-3 honesty: shares period-of-report + three labels · scored-view visibility · per-section data + decoupled price leg · runway honest flags + one-date rule | #132–#136 | `WORKBENCH_EXTRACTION.md`, `FEED_LOOP.md` |
| Runway re-tier (flags mark exceptions) · finalize feel-of-control (on-file values, AUTO shows work, honest empty purity) | #140, #141 | `WORKBENCH_EXTRACTION.md` |
| **The MVP gate, run live (2026-07-10)**: 68-name ingest → WARMING call w/ a Form 4-linked insider conviction → cron nightly, idempotency held in prod | — | `FEED_LOOP.md`, `CALL_LOGIC.md` |
| Decision capture: the operator-decisions log (take/pass/close/void) · Managing thesis-monitoring state reachable · the gate's override record | #143, #144, #146 | `CALL_LOGIC.md` §2 |
| Catalyst + kill-criteria authoring (cited conviction facts + the calendar/kill editors; wipe-trap #3 fixed structurally) · thesis archive · the notify pipe | #147–#150 | `WORKBENCH_EXTRACTION.md`, `FEED_LOOP.md` |
| Excluded-name permanence (#7): the durable NO + optional reason, seeded grayed on re-draft (discovery never filters) | #151 | `TRIAGE.md` |
| Re-version restated bars (source-strategy A): a split self-heals in one cron tick; replay stays honest | #152 | `DATA_SOURCES.md` |
| Cockpit per-name build: grouped per-member buckets + the read-only NamePanel + per-member Managing attribution | #153–#157 | `BOARD.md` |
| **Scoreboard v1 (SCORE)**: the episode ledger over the call-of-record + the operator track + gated metrics + replay-history-alongside | #158–#164 | `SCOREBOARD.md`, `REPLAY.md` |
| Ops/UX round: Slack notify adapter · cron-on-by-default · URL routing + name deep-links · seed rename · the ID-misbind (canonical-primary) fix | #165–#175 | `DISCOVERY.md`, `BOARD.md` |
| Signal + Workbench cleanup: detector-seam formalize + dilution parity · triage polish (junk tells, include filter, exports, prune-session persistence, Placed-from-master) | #176–#191 | `TRIAGE.md` |
| Display-signal framework: read-only NamePanel indicators (SMA / 52-week / volume / insider-flow) + the SMA posture headline/column + the insider-flow headline | #192–#194, #204–#206 | `DISPLAY_SIGNALS.md` |
| Cron-freeze remediation (R1–R8): key-classed EDGAR TTL · recording gate · run-of-record log · health pager · catch-up-on-boot · backend TZ pin | #196–#203 | `FEED_LOOP.md`, `POSTMORTEM_CRON_FREEZE_2026-07.md` |
| Form 4 ingest: strip the tz-offset `transactionDate` suffix (49 open-market buys were silently dropped) | #207 | `FEED_LOOP.md` |
| **Ops surface (Slice 1)** + **clock honesty (Slice 2)**: the admin freshness/health readout + Run-daily-now · the Cockpit entry-window (`arm_until`) clock, "awaiting forward bar", the record staleness line | #208, #209 | `ADMIN.md`, `BOARD.md`, `SCOREBOARD.md` |
| Insider honesty screens (preventive, latent): code-`P` open-market (call #210 + display #211) · issuer-self exclusion (#213) · discovery-walk audit — delete `scan.py`, harden convert-scan (#212) | #210–#213 | `CALL_LOGIC.md`, `DISPLAY_SIGNALS.md`, `DISCOVERY.md` |
| **Scoreboard record-provenance + maturity (Slice 3)** + the operator **DB-snapshot button + nightly (Slice 4)** | #214, #215 | `SCOREBOARD.md`, `FEED_LOOP.md`, `ADMIN.md` |
| Board view fixes (dead per-name Position, silent error-vanish, DQ deep-link) · per-worktree test-DB isolation (the shared-Postgres root-cause fix) | #216, #217 | `BOARD.md`, `PROJECT_LAYOUT.md` |
| **Scoreboard episode drill-down (SCORE)**: the row-opened drawer + 4 timing lenses + Summary\|Timing toggle + row/icon split · the price chart with a numbered insider/trigger/lifecycle overlay (relevance-floored, as-of-correct on both axes) · the event ledger sharing the chart's numbering + a Cockpit strip | #227–#230 + the chart/ledger (Slice A/B) | `SCOREBOARD.md` |
| ETF sleeve + net flow (the safe-exposure expression sleeve): CUSIP→ticker holdings resolution · holdings on the SleeveRail DD + include-a-holding (reversible) · fund internals (AUM / asset composition) · the fund-shares ingest leg with a Massive.com-primary flow read | #233–#237 | `PROJECT_OVERVIEW.md` §10, `FEED_LOOP.md` |
| SURFACE identity + Workbench editor round: the per-name origin/country chip (derive-on-read, migration 0028) · the `enrich_identity` lifecycle + the scored-join origin read · Country/Exchange find-bar filters · the additive **Basket section** (an established thesis's re-draft only ADDS, never invents links) | #238–#242 | `WORKBENCH_ENRICHMENT.md`, `TRIAGE.md` |
| Re-scope loop: frozen `basket_member.surfaced_terms` seed-provenance + "also matches now" + the ⟳ Re-scope button + the stale-session badge/expire | #243 | `DISCOVERY.md`, `INVARIANTS.md` |
| SPAC radar: the blank-check deal tape (daily-index DA scan) + the term-set thesis matcher + the quiet To-Review drawer / Type filter, on an always-on shared AppHeader nav | #244, #245 | `PROJECT_LAYOUT.md` |
| Ops/UX touchups: the Scoreboard's full company name + a drawn chart for a just-armed (no-forward-bar) episode · the TradingView-importable watchlist Export · `market_today()` — "today" as a market-tz domain fact (closes `INVARIANTS.md` §6) | #246–#248, #250 | `SCOREBOARD.md`, `BOARD.md`, `INVARIANTS.md` |
| Foreign-filer explainability: the §16-exempt "Canada · 40-F · no Form 4" tell on the origin cell | #251 | `WORKBENCH_ENRICHMENT.md`, `BOARD.md` |
| OTC price-symbol healing: resolve the vendor price symbol when the SEC ticker ≠ vendor symbol (ingest under it · self-heal at add-time · export the resolved symbol · thin-history data-health flag · shortened-name search fallback) | #252 | `DATA_SOURCES.md` |
| Empty-seed visibility: dead ("∅ no EDGAR hits") seed terms ride the draft report (the `empty_terms` chip + strip line) + a DecisionActions name-dedupe fix | #253, #254 | `DISCOVERY.md`, `INVARIANTS.md` #9 |
| **Business-type M1**: the two-level SIC → **leaf** → **super-sector** characterization (+ the royalty/streaming overlay) that **retired the size-tier archetype** — operator-editable maps in `backend/securities/business_type/` + the master re-tag · the cockpit **Type** column + the super-sector lens | #255 | `WORKBENCH_ENRICHMENT.md`, `BOARD.md` |
| **Cockpit-refinement arc**: trailing-return columns (1d/7d/30d/90d + 1Y) · RVOL\|8 (call-matched) + RVOL\|20 (call-decoupled) · insider **Ins 30d/90d** buys/buyers · the **value-chain lens** (a third basket grouping) · the business-type chip on discovery rows · within-group **sortable columns** — the cockpit as a scannable display-signal instrument, all off the call path | #256–#262 | `BOARD.md`, `DISPLAY_SIGNALS.md`, `DISCOVERY.md` |
| *(#263–#329 are not yet logged here — the canon docs carry them: `BOARD.md`, `DISPLAY_SIGNALS.md`, `SCOREBOARD.md`, `DEPLOY.md`, `ACTIVIST_STAKE.md`, `CORPORATE_EVENTS.md`)* | #263–#329 | — |
| **Cron missed-nights arc**: the sidecar fires for the INTENDED night (target as-of fixed at schedule time) + forward-only late-wake catch-up · the hole-aware freshness read + `gappy` · `pipeline.backfill` with a PINNED `known_at` (`--known-at next-run`) · boot catch-up to the LAST EXPECTED night + a post-`RUN_AT`-only `already_ran_live` + the sliced wall-clock wait (the monotonic-sleep overshoot under host suspend, MEASURED) | #330–#332 | `FEED_LOOP.md`, `ADMIN.md` |
| **Scoreboard Timing view + record bugs**: MAE/MFE excursions (close + wick, all four columns) · the episode path sparkline with the de-arm tick · split Armed/De-armed columns + close-reason chips · sortable columns + sticky headers · `truncated` redefined against the tape edge (+ `tape_behind_market`, the TAPE ENDS badge) · the exit = the last bar IN the window (the backwards-return fix) | #333, #334 | `SCOREBOARD.md` |
| **Serve-path `known_at` cap + reconstructed rows**: `serve_known_at(asof)` on `/call`, `/display-signals`, `/scored` (a scrub-back reads what was knowable by the end of that MARKET day) · `calls.reconstructed` (migration 0042) + the backfill's thesis-existence gate + `repair_reconstructed_precreation` · the Scoreboard scores honest rows only, `reconstructed_nights` named once | #336–#338 | `INVARIANTS.md` #4, `FEED_LOOP.md`, `SCOREBOARD.md` |

## Standing design decisions (made once, still steering)

- **Curation:** surface **and score every candidate**; the operator makes the final picks. The basket is a
  **quiet output** — ember loudness stays reserved for armed Board calls.
- **Drafted vs taxonomy — hybrid.** The MVP drafts fresh (novel narratives outrun any taxonomy); a maintained
  taxonomy **accrues from the operator's curation** over time — built out of normal use, never a separate
  maintenance project.
- **Per-thesis "specialist" = an accumulating per-thesis knowledge base** reasoned over by a **stateless LLM
  each time** — specialization lives in the data, with provenance; survives model swaps. The LLM proposes
  sources; the pipeline ingests the real number; the operator ratifies. **The LLM never supplies a number.**
- **The DD has two layers:** stored company-reference **facts** (sourced like any fact, never narrated) vs
  auto-drafted thesis-fit **reasoning** (clearly labeled drafted). Visibly distinct, always.
- **Reference-fact freshness = re-FETCH, not re-derivation** — a third freshness job (weekly, append-only),
  distinct from score re-derivation (no fetch) and the discovery cron (new entities). Filed below.

---

## What's next (by appetite, in rough order)

1. **The live Scoreboard — v1 BUILT** (the forward twin of replay; `docs/SCOREBOARD.md`): the episode ledger
   over the two record streams — the platform's calls scored on their own `exit_by` signal-validity yardstick
   + the operator's decisions joined to the episodes they answered (overrides with outcomes); the UI gates
   aggregate metrics below `n=5` and metrics include only matured, non-censored episodes. That gate prevents
   premature display; it is not an evidence threshold, and passing it does not establish calibration. The
   growing record still unlocks the **second, out-of-sample
   recalibration** — that arrives as the record accrues. **Replay-history-alongside is now built** beside
   the record (#163/#164, a collapsed historical panel served from an operator-kicked snapshot artifact).
   Next here: the v2 follow-blindly track + deltas (+ a metrics-led second view behind a toggle once n
   accrues); **2f "the real WHY"** — a deeper per-episode Scoreboard explanation (never specced, an open
   design question, not a committed feature); and the **replay-regenerate button** — regenerating the
   historical snapshot from the app is **deferred to the replay-panel work** (it needs DuckDB in the lean
   image + a replay-perf fix; today the snapshot is a dev-venv CLI artifact the app only reads); and the
   **point-in-time basket** (`basket_member` is full-replace, no timestamps — `INVARIANTS.md` §Known gaps): the
   precondition for a reconstructed night ever being scoreable and for a past-asof recompute (the Board/Cockpit
   scrub-back, replay) to stop being a labeled counterfactual.
2. **Notify DELIVERY** — BUILT: the Slack adapter (#165–#175) behind `get_notifier()`, under the Q13 push
   policy (loud = a real arm; quiet = a weaker / mixed move while armed + the nightly newly-armed digest; one
   SPAC push per announcement — `FEED_LOOP.md` §"The push policy"). Another channel (email / Telegram) is one
   more same-shape adapter.
3. **Cron scaling refinement** — decouple "record ALL theses" (cheap, keep daily) from "ingest ALL theses
   daily" (live pulls): ingest active theses daily, dormant less often. Fine at today's scale; jotted.
4. **Re-draft drift detection** — flag an accepted name when its underlying data changed since acceptance
   ("re-confirm?"). Shares the MONITOR/Board drift machinery — **build it with the Board**, not in the
   Workbench.
5. **Workbench backlog, as-encountered** (never a batch): ambiguous send-back symmetry, tier-rec badge noise,
   send-back discoverability, accept-color decoupling, pre-revenue purity chip, explain transport-error,
   FE async-boundary tests, off-universe label copy, dilution polarity styling.
6. **Cron ops hardening** (distinct from #3 scaling — this is freshness/observability, not load). The
   ambient-`date.today()` item is **DONE** — `domain/market_time.market_today()` + the repo-scan guard
   (`INVARIANTS.md` §6). It did **not** unify the Mon-Fri + `RUN_AT` schedule contract, which still has two
   homes (`scripts/daily_cron.sh` and `pipeline/schedule.py`, kept in step by hand) — `market_today()`
   deliberately does no trading-calendar logic, so shrinking the shell to a dumb trigger remains open.
   The **wrong-day fire is CLOSED** (#330–#332: target-at-schedule-time, the sliced wall-clock wait, boot
   catch-up to the last expected night, the hole-aware `gappy` read; a missed night is reconstructed with
   `pipeline.backfill`'s pinned clock — reported, never scored). The **fetch counter counts ATTEMPTS, not
   successes** gap is **CLOSED (O5, 2026-10-21)**: the freeze verdict reads COMPLETED fetches
   (`edgar_fetches_completed`; observed 2026-07-22, an empty `ALPHADECK_USER_AGENT` failed all 255 pulls and
   the run logged `255 EDGAR fetches`), and the `--catch-up` exemption is narrowed to the theses a catch-up
   SKIPPED (G1's five-minute TTL removed the reason it was wholesale). The zero threshold itself is
   **CLOSED by the ratio floor (2026-10-21)**: a live pass also pages FROZEN when fewer than 50% of its
   EDGAR-eligible companies got fresh SEC data (`Settings.freeze_completed_floor`; `FEED_LOOP.md` "Known
   gaps" — measured: it flags twelve stored passes, all pre-G1, none since). Still open: the **R4 0-fetch
   false-positive** for a second pass within five minutes of another (page on 0 only when the cache was
   outside its TTL). And an
   **external dead-man's-switch** heartbeat (a run that never happens produces no page — the
   sidecar can't alert about its own absence; the only thing that catches a host that is OFF at `RUN_AT` and
   stays off past the next boot's last-expected window); and the **`RUN_AT` choice** (an earlier fire while
   the host is usually awake, at the cost of same-evening filings landing a night later — an ops decision).
   Full account: `POSTMORTEM_CRON_FREEZE_2026-07.md`,
   `FEED_LOOP.md` "Known gaps".
7. **Insider honesty backlog** (extending the #210–#213 screens): insider **Class B** — 10%-owner
   fund/affiliate blocks (Baker Bros / Paulson-style) that are portfolio moves, not conviction, screened
   from Key-1; and the **`insider_flow` sell-side ceiling** (the display headline's symmetric guard). Both
   are preventive precision on the insider key, as-encountered against real filings.
8. **Context read-throughs — Phase 2** (the 2026-10-28 brief; Phase 1 shipped only the per-company context
   role on the membership, `TRIAGE.md` "Context roles"). Reading signals FROM a role-tagged name's disclosures,
   signed by its role, display-only and compute-on-read like `theme_breadth`: a payer's capex (trailing four
   quarters vs a year earlier), a direct read's or participant's RPO (else revenue), a counter-case's revenue
   (inverted), a benchmark's own price (trailing returns + ETF flow where sampled), a financier's passages only,
   and everyone's latest earnings-release passages (8-K Item 2.02, EX-99.1, LOCATED by keyword, never read by
   a model, never a number). Findings from the Phase 1 code review that the next build should start from:
   - **Store capex per XBRL concept** (the S4 share-count precedent: each concept its own `metric_key`, the
     read picks one and never mixes two in a number), not one `capex` key with a fallback — revenue's
     "pick the current tag" needed the 0060 re-parse chain to stay honest across a tag switch. Quarterly
     capex is year-to-date differencing (Qn = YTD n − YTD n−1, Q4 = FY − 9 months), dated at the later filing.
     The concept list (`PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsToAcquireProductiveAssets`) is
     UNMEASURED — no cached companyfacts in the repo carry capex; measure it on prod's cache first.
   - **The nightly lands the history on its own**: the fundamentals leg re-parses every basket company's
     CACHED companyfacts each pass (the Q17 gate only decides re-pulls), so new metric keys append their full
     history on the first night after deploy. A backfill CLI is for the dry-run report (which concept each
     company reads) and a chosen-time apply; its `--apply` journals a `data_repair`, which starts an editions
     repair trial.
   - **Every reader of `fact_fundamentals` filters on `metric_key`** (checked 2026-10-28), so new keys cannot
     reach a detector — keep it that way.
   - **The keyword locator needs a boilerplate screen**: "expect" / "outlook" / "guidance" sit in every
     release's forward-looking-statements paragraph. Proposed: skip the safe-harbor paragraph, count "expect"
     only in a sentence with a digit, cap the passages per release.
   - **Where the exhibit is fetched**: not in a display module (no I/O) and not on a Cockpit GET (no SEC on a
     page load). Proposed: the nightly, AFTER every call-of-record (it can never delay or cost the record), only
     for role-tagged members whose latest Item 2.02 is not cached — the full submission under the immutable
     `forms/<accession>/<accession>.txt` key the split readers already use, parsed with
     `split_filing.submission_documents` (import only; never change `split_filing.py`, it feeds split
     detection). The 8-K list itself is free: `fact_corporate_event` already stores every 8-K's item codes.
   - **The display seam** needs `fundamentals_facts` + `corporate_event_facts` on `DisplayPointInTimeData`
     (never on the detectors' protocol), a `HORIZONS` declaration, and its own `*Out` wire shape with filing
     links; the panel lists each company once, never once per row.
   - **Known Phase 2 gaps to decide**: 6-K filers have no Item 2.02 and IFRS filers (20-F / 40-F) use no
     us-gaap capex tag, so foreign payers read nothing; revenue comparison (latest quarter vs a year earlier,
     or trailing four quarters); whether a newly set role fetches its release at once or at the next nightly.
   - Membership **tags** (`medical`, `adult-use`, `crossover`; display and filter only) were also deferred;
     if built, they follow the context role's per-company rule.
   Phase 3 (undecided, the operator's `CALL_LOGIC` call): whether a read-through ever warms a card.

### Filed, post-MVP (additive)

- **Weekly discovery cron** — new names/links/ETFs *entering* a sector (discovery only; scores already
  re-derive on read).
- **Weekly reference-fact sweep** — re-fetch the stored company-reference facts not covered by the
  per-company EDGAR ingest (website, richer description). Append-only.
- **ETF-holdings-as-universe-seed** — folds into the Phase-3 ETF radar.
- **Rotation-awareness** — money rotating along the chain; *emerges* from two-key arming applied link by link,
  made legible on the Board. Reactive, never predictive.
- **Taxonomy accrual, the per-thesis knowledge base, liquidity, short interest.**

---

## Phase 3 — Breadth + depth (by appetite, not a strict order)

**Breadth:**
- **Laggard scanner** — a second confirmation path; `LAGGARD` is a wired confirmation kind, the detector unbuilt.
- **ETF radar** — universe seed; the N-1A/485 launch detector (emergence signal); holdings-derived flows; the
  `safe_sleeve` card field exists but is unpopulated.
- **More catalyst sources** (`CATALYST_CONVICTION.md` §3): the automated 8-K material-agreement detector; the
  NRC license-action feed; wire the DOE loans award-type group so the loan-guarantee → core rule fires.

**Depth:**
- **Umbrella / segment hierarchy + multi-segment board** — the `parent_id` seam exists; recursive-CTE reads +
  the umbrella board UI (the AI-buildout mockup).
- **Live LLM counter-case** behind the assembler's existing hook (the deterministic template stays the fallback).
- **Workbench post-MVP** (the filed items above) lands here by appetite.

---

## Locked — do not reopen

- **The through-line** (`INVARIANTS.md` #7): categorical call strength ← grade; signal-validity window ←
  horizon; provisional-call wording / setup-strength cap (`confidence` in code) ← the weaker key;
  catalyst-liveness ← the agreement horizon; sizing / instruments / execution / portfolio risk ← the operator
  and external systems. **Never re-couple; never add an `if kind ==` branch** where a property already carries
  the signal.
- **All of `INVARIANTS.md`.** In particular **no model-sourced numbers** — extended to the Workbench: the LLM
  drafts *structure* and *proposes sources*, never supplies a number. *Narrative is the operator's, structure
  is a draft, numbers are facts.*
- **The catalyst grade rule** — customer-vs-sponsor (contract ≥ $10M = core; loan / loan-guarantee = core;
  assistance / OTA / grant = flip); **grade is the nature/call-strength class of the commitment, not its
  obligation amount or a position-size instruction.**
- **The Workbench persistence seam** — the value-chain **structure** persists **operationally on the thesis
  spine** (`segment` + `authored_by` on `basket_member`, `segments` on `thesis` — editable config, **not**
  bitemporal); the **scores never persist** (re-derived on read). Chain-evolution *history* is a taxonomy-era
  addition — a future author must **not** assume it already exists.
- **The recalibration backlog stays consolidated in `RECALIBRATION.md`** — reference it, don't scatter it.
- **Trust before front-half build** *(honored — kept for the record)*.

## Parked — deliberate non-goals

- **OMS / execution / brokerage / sizing / portfolio risk — permanently external.** Advisory research and
  thesis monitoring are the product's identity; Alpha Deck hands off to the firm's existing systems and does
  not replace them.
- **Position sizing / portfolio construction, MNPI/compliance, quant/chart signals — out by design, not gaps**
  (`STAGE_MODEL.md` records the reasoning). TRIAGE captures a conviction *weight* but does no sizing math;
  the platform is EDGAR-only (no private-data surface → no MNPI vector); TA/price-pattern prediction is parked.
- **Deferred** (scope, not principle): intraday / streaming data; options-gamma; borrow + squeeze radar;
  crypto / commodities / prediction markets; runtime auth / multi-tenant serving; Dagster; Neo4j.
- **The live Scoreboard v1 is built** — the forward twin of the replay harness. It can earn trust only by
  *living with the calls over time*: the ledger is standing and forward outcomes accrue. The `n ≥ 5` UI gate
  merely suppresses tiny aggregates; it is not the point at which evidence becomes sufficient.
