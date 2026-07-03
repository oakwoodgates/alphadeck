# Equity Alpha Research Platform — Project Overview

> Working reference doc. Captures the high-level goals and design converged on during planning.
> Scope: equities first (US), with commodities / crypto / prediction markets as later additions.
> Codename: **Alpha Deck**.
> This doc is the *design rationale* (the "why"). **The organizing frame — the platform as a pipeline of stages
> (SEED → DISCOVER → SURFACE → TRIAGE → MONITOR → SCORE) — is `docs/STAGE_MODEL.md` (read it first).** Stack, UI
> surfaces, and v1 scope live in `README.md`; build conventions + invariants in `CLAUDE.md`; the **file-by-file
> repo layout** in `docs/PROJECT_LAYOUT.md`; where data lives at runtime in `docs/DATA_FLOW.md`; **how the platform
> feeds itself** in `docs/FEED_LOOP.md`; the front door (narrative → chain) in `docs/DISCOVERY.md` (how the names
> are found — EDGAR-first) + `docs/CHAIN_DRAFTER.md` (the authoring / ratify / promote surface); the SURFACE stage
> in `docs/WORKBENCH_EXTRACTION.md` + `docs/WORKBENCH_ENRICHMENT.md`, basket crafting in `docs/TRIAGE.md`; the live
> sequencing in `docs/ROADMAP.md`.
>
> **Status — the MVP is COMPLETE.** Both halves on one spine are built: the **front-half loop closes**
> (narrative → draft → ratify → promote → extract → score — S5 + the create-thesis front door, M1) and the
> **back half feeds itself** (the per-thesis ingest + the daily call-of-record cron, M2). The **North Star is
> reachable end to end on real data.** *Trust caveat (kept honest):* the calls are tuned **in-sample (n=19)**,
> and "feeds itself daily" is the DATA loop — **not** forward validation. The **live Scoreboard** (the forward
> trust loop) is the post-MVP open. See `ROADMAP.md`.

---

## 1. What this is

A research platform for **hunting, validating, timing, and managing equity theses** — a "DD engine" for finding and executing on alpha. It is not a quant black box and not an execution system. It is a decision-support tool that helps a discretionary trader turn early narrative convictions into well-timed trades.

The trade expression (spot / futures / options) is out of scope as a design concern — the platform is about *research and timing*, not order routing.

## 2. Operator profile & design philosophy

The platform is built around a specific operator pattern:

- **Edge:** spotting macro narratives early — the "what comes next." Strong at reading the *narrative clock*.
- **Flaw:** timing ("terrible at dates"), and translating a narrative into the *specific* names that best express it (defaults to broad ETFs, e.g. long PSIL for a psychedelics view).

Design principle: **preserve the edge, patch the flaw.**
- Preserve: capture early convictions so they aren't lost or forgotten (the "vault").
- Patch: supply name selection + DD, impose timing discipline, and tell the operator the moment the market is catching up.

Corollary principle for the opinion layer: **opinionated about timing, deferential about thesis.** The platform doesn't second-guess whether an idea is good (the operator's strength); it gets loud only on *when / what grade / what expression* (the operator's weak spot).

## 3. Original goals

1. "Picks and shovels" plays.
2. Obvious next plays / laggards (leader runs on a catalyst; correlated names lag then catch up).
3. Being early to emerging industries — previously high-risk, grey-area, or unloved by Wall St, where sentiment is starting to shift.
4. Alpha from SEC filings — insider buys before earnings, material events, dilution, timeline news.
5. Short-squeeze opportunities.
6. Sector tracking tools.

## 4. Core architecture — two halves on one spine

**Front half (hunt + research):**
narrative → decompose into the value chain → surface & score specific names by exposure purity, risk profile, cash runway, catalyst density → auto-draft DD per name.

**Back half (park + time):**
chosen names become a parked thesis → incubate → trigger detection → graded "call" → manage to exit.

**The spine:** the **thesis as a first-class object** connects the two halves. An idea flows front-to-back, from "psychedelics — here are the five names and why" to "OPTH broke its base on volume the week after the executive order — time to move."

## 5. The thesis object

A structured, versioned record containing:
- **Narrative** — the story / conviction (operator's words, preserved).
- **Basket** — the specific instruments chosen, tagged by archetype (leader / high-beta / lotto / shovels).
- **Evidence** — linked filings and data points (immutable references).
- **Catalysts** — dated events that should move it.
- **Signals** — the quantitative triggers being monitored.
- **Kill criteria** — disconfirming evidence / exit conditions (the discipline layer).
- **Expression / position** — sizing and current state, populated once entered.

## 6. Thesis lifecycle & trigger grading

A loop, not a one-way ratchet — a parked thesis can cycle through states and throw off multiple tradeable signals over its life.

- **Incubating** — banked idea, nothing confirmed. Platform watches *quietly and does not nag* (anti-FOMO; stops entering at month one of a six-month wait).
- **Warming** — early stirrings (volume, attention inflection, catalyst entering window).
- **Armed** — entry conditions met; alerts loudly with the call.
- **Managing** — position open; exit-timing / half-life logic governs.

**Trigger grading.** Each fire is classified by:
- **Grade:** *flip* (fast, sentiment-driven pop — trade small/fast, then fall back to Incubating) vs *core / structural* (real change to the setup — build the core position).
- **Type:** regulatory, promoter/attention-driven, technical breakout, clinical readout, squeeze, personnel/appointment.

**Expression follows grade:** flip → high-beta / lotto names or short-dated options; structural → core position in leaders + shovels.

**Timing discipline (entry & exit):**
- Holding period matched to each signal's **alpha half-life**; the platform derives an **exit-by date** and overlays the **catalyst surface** (binary events crossed before exit).
- Horizon target: mostly 1–3 weeks, acceptable to ~6–12 weeks for strong theses; longer only with a strong accumulation case and explicit opportunity-cost consideration.
- Personnel/appointments treated as leading indicators of regime change (a Warming signal in their own right).

## 7. The opinionated call layer

The platform **makes the call and shows its work.**

A single call is one **card**:
- **Verdict** — grade + suggested expression + exit-by date.
- **Triggers fired** — with links to the filings / data behind them.
- **What's missing** — the unmet conditions.
- **Counter-case** — what would invalidate it.
- **Confidence** — calibrated; a high-confidence core entry reads differently from a marginal 2-of-3 flip.
- **Actions** — act, override (logged), or snooze.

Because the work is shown, an **override becomes a documented bet against evidence**, not a gut feeling — the friction that patches the "too early" habit.

## 8. Behavioral / accountability layer

- **The gate (advisory).** No execution in v1, so the gate withholds the platform's *blessing* rather than blocking an order: it won't show "Armed / go" until conditions are met, and shows a **readiness scorecard** ("3 of 4 triggers missing").
- **Override logging.** Early/manual entries are logged, not blocked.
- **Scoreboards.** Three tracks scored over time: the platform's calls, the operator's actual decisions, and the counterfactual of following the platform blindly. The deltas turn "edge and flaw" from metaphor into measured data — showing empirically where discretion adds vs destroys value. The platform is held accountable for its own opinions (call accuracy by trigger type and grade).

## 9. The sector dashboard (discovery views — "section headlines")

Not one ranking; a shelf of lenses the operator walks when adding/working a sector:

- **Map the universe** — every name + market cap, float, liquidity (ADV/spread), jurisdiction, and a *pure-play-vs-diversified* exposure score.
- **By value-chain position** — end-product developers / picks-and-shovels (supply, manufacturing, distribution, energy, consumables) / downstream second-order beneficiaries.
- **By archetype** — industry leaders & favorites / high-beta pure plays / small-cap lotto tickets.
- **By live signal** —
  - *Already-ran, and why* (momentum-health classifier — see §10).
  - *Over-shorted* (SI %, days-to-cover, borrow).
  - *SEC / filing intelligence* (insider cluster buys, material 8-Ks, dilution clock).
  - *Personality-backed attention magnets* (see §10).
  - *Catalyst calendar + attention velocity* (forward dates + mention rate-of-change).
- **ETF radar** — which ETFs express the theme now, which are filed/coming, and what they hold and are accumulating. The low-torque, long-duration *expression* of the thesis (see §10).

## 10. Key analytic components

- **Momentum-health classifier** — for already-run names, judges *continuation vs last gasp*: breakout-from-base on expanding volume + broadening peer participation + news-justified + real buying (continues) vs extended / low-volume / isolated / promoter-pumped (exhausted). Lets the operator chase the strongest horse with eyes open.
- **Personality-backed = attention-catalyst proxy (double-edged).** Map notable backers/board members (13D/13F/board, free from EDGAR) + detect public promotion (media/social footprint spiking). Often the literal answer to "why did it run." Flip-grade by nature — great for catching the retail wave, dangerous to marry; pair with the momentum classifier.
- **Dilution clock** — months of runway at current burn + recent shelf/ATM filings + warrant overhang. For pre-revenue names, dilution kills more theses than the fundamentals do.
- **Laggard scanner** — beta-adjusted residual of theme peers vs a leader's move; surfaces names that "should have" moved and haven't.
- **Emergence detector** — composite of regulatory/personnel milestones + attention rate-of-change + institutional first-footprint (first 13F, first initiation, first thematic ETF) + capital-markets activity (IPO/SPAC pipeline, uplistings).
- **Insider-conviction scoring** — open-market buys only, weighted by opportunistic-vs-routine trader, clustering, role (CEO/CFO), size, and proximity to earnings.
- **Squeeze radar** — SI % / days-to-cover / borrow fee + utilization / gamma overlay, gated on a catalyst and an attention spike.
- **ETF radar** — three faces on free/EDGAR data: (1) *availability* — the thematic ETFs expressing a theme today; (2) *coming* — new ETF registrations (SEC N-1A / 485 filings), where a fresh thematic launch is itself an emergence marker (early signal, sometimes a top); (3) *holdings & flows* — ETF holdings seed the decomposition universe for free, and holdings changes + AUM flows are a positioning signal. Always surfaces fund internals (holdings, weights, expense ratio, AUM, liquidity/spread) so a mislabeled or thin/expensive fund is visible, not assumed. **Role:** the *safe-exposure sleeve* — the durable, low-torque expression of a thesis (give up the leader/lotto upside for duration and diversification), distinct from the single-name alpha sleeve.

## 11. Constraints & key decisions

- **Horizon:** swing trades; see §6.
- **v1 scope:** human-in-the-loop; **explainability over execution**. No trade execution — advisory only; operator logs fills so the thesis stays tied to the live position.
- **Data posture:** bootstrap on free sources (EDGAR incl. N-1A/485 for ETF launches, FINRA short interest, free price/fundamentals, public ETF holdings, on-chain/crypto data for crypto-adjacent themes). Pay case-by-case for the right thing (borrow data, options/gamma, premium fundamentals, ETF flows). See `docs/DATA_SOURCES.md`.
- **LLM-in-the-loop:** filing classification & summarization, DD drafting, and the reasoning behind each "call" (with citations to source).
- **Trajectory:** personal project first; possible SaaS later. Keep multi-tenant seams in the schema, but don't pay the multi-tenant tax yet.

## 12. Engineering foundations

- **Point-in-time / bitemporal data** (valid-time + transaction-time, as-of queries). The single thing that separates a real research platform from a toy; foundation and future moat. Design in from day one.
- **Canonical security master + entity resolution** (CIK ↔ ticker ↔ CUSIP ↔ FIGI), with corporate actions and survivorship-bias avoidance.
- **Layered pipeline:** ingestion → normalization / entity resolution → relationship graph (companies, people, supplier/customer, fund holdings, themes) → signal engine (composable, backtestable detectors emitting events) → thesis workspace → (later) execution / risk.
- **Differentiated wedge (build-vs-buy):** the thesis-object connective tissue + replay/backtest rigor + opinionated graded calls — not re-deriving screeners that already exist (Fintel, Quiver, Unusual Whales, Koyfin, etc.).

## 13. Build order

1. **Foundation** — security master + point-in-time data layer (expensive to retrofit; do it first).
2. **SEC / filing intelligence brick** — biggest operator gap, cheapest to build (EDGAR is free), and its insider/dilution/8-K signals feed both halves.
3. **Thesis object + workspace** — the spine; evidence linking, catalyst calendar, kill criteria, the call card.
4. **Laggard scanner + theme taxonomy** — over EOD price.
5. **Squeeze radar / options-gamma** — phase in when paying for borrow + options data.

*ETF radar phases across the above: holdings-as-universe-seed lands early (it feeds the decomposition in step 3); the new-launch detector rides the EDGAR brick (step 2); flows/positioning come later alongside the signal engine.*

## Status of earlier open questions

Resolved since this doc was drafted (now captured in `README.md` / `CLAUDE.md`):
- **UI design** — four surfaces (Board / Cockpit / Workbench / Scoreboard) with the inverse-loudness principle; three mockups built (Board+Cockpit, 2025 umbrella board, segment cockpit) in `docs/mockups/`.
- **Codename** — Alpha Deck.
- **Tech stack** — Postgres + DuckDB/Parquet, Python/FastAPI/Polars/Pydantic, React/Vite/Tailwind/TanStack Query, Anthropic API behind a model-agnostic interface.

Still open / deferred:
- Specific *paid* vendor choices per module (borrow-fee data, options/gamma) — stack decided, vendors case-by-case.
- Detailed replay/backtest harness design (approach chosen: point-in-time Parquet via DuckDB).
- Trader-group / social sentiment integration (later signal source).
