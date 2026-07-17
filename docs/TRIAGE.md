# TRIAGE.md — basket crafting (the ~90 → ~15 stage)

> Repo path: `docs/TRIAGE.md`. The **TRIAGE** stage (`STAGE_MODEL.md`): after DISCOVER finds the names and SURFACE
> populates them, the operator turns a ~90-name discovered draft into a **chosen, ordered, weighted basket** — in
> minutes, not by scrolling a flat list. This is basket curation, **not** position sizing or portfolio
> construction; those live in the firm's external OMS / execution / risk systems. Mostly a
> frontend + wiring stage (no new spine): the promote endpoint already **full-replaces** the basket from a
> client-sent list, so crafting is a matter of sending the right subset. Surface:
> `frontend/src/workbench/ChainEditor.tsx` + `useChainDraft.ts`; the writer:
> `POST /workbench/theses` (`app/routers/workbench.py`).
>
> **Status: BUILT, end to end** — the three-gate flow is live (the PR trail: include/find/weight #113–#118,
> the three-gate round #127–#129, sections + honest flags #132–#136).

---

## The three gates — the stage's organizing shape

Real usage falsified "you'll want data on everything placed" (a 370-name draft made extract-everything
unaffordable *and unnecessary*). TRIAGE is **three gates**, each cheaper judgment before more expensive data —
the buy-side screen → shortlist → diligence funnel (`STAGE_MODEL.md`, "the third thread"):

1. **CHEAP CUT** (zero API) — judge on already-visible row data (name, ticker, sector, matched terms,
   off-thesis flag). Dashes are fine here: you cut "a bank that matched the word memory," not on purity.
   The sections below through *the placed-board partitions* serve this gate.
2. **MARK FOR DATA** (bounded spend) — only survivors the operator is *unsure* about get data: per-name or
   per-section, the control is the trigger, cost visible per click. The **shortlist** (the survivors) is the
   only set expensive operations ever touch. *The "Mark for data" section below.*
3. **FINALIZE ON DATA** (the existing ratify) — confirm each fact against its honest flag, decide the
   archetype on the rail, weight with conviction, promote. `WORKBENCH_EXTRACTION.md` owns the flags.

## The prune — include-controls (#113) + the durable NO (#7)

A per-name **include toggle** on every placed row. **Save persists ONLY the included subset** (the promote
full-replaces, so excluded names simply aren't sent).

- **Default-INCLUDED (#9):** a discovered name starts IN; the operator *unchecks* to exclude. Nothing is silently
  dropped — an excluded row stays **visible** (greyed), one click from re-inclusion.
- **Orthogonal to accept / authorship.** Include ≠ accept. Accept is the authorship flip (`system_drafted →
  operator_set/operator_edited`, the re-roll survivor); include is "goes in the saved basket." A name can be
  **accepted-but-excluded** or **included-but-not-yet-accepted**. Include never touches `authored_by`.
- **Bulk actions:** include all / exclude all / **clear un-accepted** (exclude every still-`system_drafted` name —
  the fast path to just-my-vouched names — without touching authorship).
- **The exclusion is DURABLE (#7).** Save also persists the current exclusion set — with the optional
  **"rejected because X"** reason (a quiet inline input on the greyed row; skippable, editable) — through the
  sole-writer `PUT /theses/{id}/exclusions` (`thesis_exclusion`; the term_set structural wipe-guard, so a
  promote can never blank the pruning). On the next session or re-draft the editor **seeds** its excluded
  state (and the To-Review keeper set-asides, for resolved names) from the persisted set: a rejected name
  arrives **pre-greyed, visible, one click back**. **THE #9 LINE: discovery never filters on exclusions** —
  a re-draft still surfaces every name; the NO is an editor default, never a recall cut. Prior NOs the
  session never re-surfaced are carried forward on Save; re-including a name withdraws its NO. (v1 scope:
  keyed by `security_id` — unresolved names' set-asides stay session-local.)

## The find — the sortable / filterable view (#114)

The placed list becomes a triage instrument: **sort** by name / archetype / segment / sector, **filter** by
archetype / segment / fundamentals / authorship / include / off-universe, and a **compact** toggle that collapses
the thesis-fit prose for a scannable read. This is how pruning 90 names stays fast.

> **THE #9 SPINE (test-guarded): the VIEW never changes what Save persists.** Save is `basket − excluded` computed
> over the **whole draft**, regardless of the current sort/filter — a filtered-out but *included* name still saves.
> Filtering hides; only the include toggle decides what persists. A `clear filters` affordance is always one click
> away so a hidden-but-included name is never lost.

**The fundamentals badge** (a cheap read-time join, no fetch) shows which survivors still need a SURFACE extract —
but only once it **discriminates**: before any name in the basket has confirmed fundamentals the badge is
suppressed (it'd be true of every row = noise, inverse loudness), replaced by one quiet header hint. See
`WORKBENCH_EXTRACTION.md` for what "fundamentals loaded" means.

## The weight — the conviction field (#115)

A nullable **1–5** integer per name (`BasketMember.conviction`) — the operator's intended size weight (1 = starter
… 5 = full). Set in the crafting row (TRIAGE / ChainEditor only; the Board is read-only monitoring — re-weighting
while watching is a later MONITOR-stage feature). A number, so future size-weighted attribution can derive relative
weights directly; soft enough to set fast; and it dodges the sum-to-100 portfolio-construction trap a target-% would
drag in (position sizing is out of scope — `STAGE_MODEL.md`).

- **`NULL` ≠ 0.** Unset means "the operator hasn't said," **never** "zero size" — so attribution can't silently
  treat unset as zero-weight (the same estimate-vs-confirmed honesty, #6). Renders `—`.
- **Stored metadata, NEVER fed to the call (#4).** Conviction rides ON the member (persists through the
  full-replace promote, mirrors `thesis_fit`); it never touches the meters / verdict / grade / `exit_by`
  signal-validity horizon. The entry grade stays **signal-derived + deterministic** — the system derives a
  categorical call-strength class from the triggers; it does not size a position or judge the idea. Actual
  sizing remains an operator/external-system decision.
  Operator-authored by definition (no LLM recommendation).

> **NAMING GUARD (#116) — two unrelated "convictions", they must never cross.** **Operator conviction** =
> `BasketMember.conviction`, this 1–5 size weight (stored TRIAGE metadata). **Signal conviction** = the
> deterministic call machinery in `calls/` (`conviction_kinds` / `conviction_grade` / `key_conviction` — warm/arm
> triggers). Wiring operator conviction into the call is a **#4 violation**. Also stated on the field in
> `CLAUDE.md` and `domain/thesis.py`.

## The To-Review triage ruleset (#118) — inverse loudness (#7)

The reconciler-appended names (the term-matches the organizer didn't place into a link) are mostly noise. The
ruleset **highlights the signal, doesn't flag the noise** — the exact inverse of the Placed bucket:

- **Keepers** (on-thesis, has ticker) → **surfaced at top** (the keepers block). The top position **is** the
  recommendation — there is no per-row "recommend add" badge (it would be true of *every* visible keeper, which
  is noise; honest loudness #7). The action is a **"check to add" checkbox**: checking it promotes the candidate
  and the **row moves up to Placed** (the basket's single home) — the move is the honest signal of the state change
  ("haven't decided" → "in the basket"). The reverse is the Placed row's **send-back / exclude** (#121/#122).
  **There is no "skip"** — a candidate is never discarded, only added or left in the queue (a skip that dropped the
  row was a silent #1/#2 violation).
- **Off-thesis** (the narrator's `off_thesis` bool — see `CHAIN_DRAFTER.md`) → **quiet, collapsed**, and **split by
  keyword provenance** into two drawers so the flood is read at a glance: **"Low signal"** (matched **2+** discovery
  terms — the stronger keyword evidence, more likely a missed keeper) and **"Lowest signal"** (matched **≤1** term —
  the weakest). Within Low signal, more terms sort first; within Lowest signal, the **zero-term names sort to the
  top** — an off-thesis name with *no* keyword provenance is an **off-universe** name the model surfaced on its own
  (`discovery_source="off_universe"`, no term hit), worth the eyeball above the single incidental hits. Each drawer
  renders **only when non-empty** (honest loudness #7 — a bucket true of nothing doesn't render). **No yellow flag** —
  flagging the majority just moves the noise around; loudness marks the rare exception, which in To-Review is the
  *keeper*, not the junk. (The sort is drawer-local and view-only — it reads the already-present `matched_terms`,
  writes nothing, and adds no field to the persisted prune session.)
- **Ticker-less** (a resolved filer with no listed ticker — likely a sub / holdco / debt issuer) → collapsed into a
  "No listed ticker" section. Probably not directly investable, so its
  **check-to-add is disabled** (it never enters the basket by a stray click). Still **not dropped** (#9 — the row
  is surfaced, and a name that genuinely belongs is reachable via the master **name search**, which promotes by
  `security_id`).
- **Precedence:** off-thesis > ticker-less > keeper.
- **Up to four nested sub-drawers under one master To-Review collapsible** (mirrors the Placed section's
  `.wb-placed-groups`): **Keepers** (open by default — the signal), **Low signal**, **Lowest signal**, and **No
  listed ticker** (the last three collapsed). Each is independently collapsible so a big draft's To-review block stays
  keeper-sized, and collapsing the master hides the whole bucket in one click. The master header count is
  **keepers-only** (the
  headline is the signal; each sub-drawer carries its own count).

**The "Discovered" holding pen.** Names discovered-but-not-organized land in a catch-all segment labeled
"Discovered". It is a **sorting queue, not a value-chain link** — de-linked visually (muted, "unsorted — not a
link"), with a quiet "N unsorted" nudge. The placed-row **seg dropdown is wired** to `d.placeMember` — selecting a
real link **re-segments** the name (which flips `authored_by → operator_edited`, so the choice survives a re-roll);
there is no "remove" in the dropdown (pruning is the include-uncheck). Together these turn a dead catch-all into a
triage queue. Each placed row also shows the **company name** (bridged by `security_id`) and the SURFACE identity
chips incl. the **filer-category** maturity tell (`WORKBENCH_ENRICHMENT.md`).

## The placed-board partitions (C-B + G) — one membership, display groups

The Placed board renders the **ONE basket** flat until a partition discriminates, then as up to three
independently-collapsible **display groups** (same first word = same membership; the modifier is the lens):

- **"Placed"** / **"Placed, flagged"** (C-B) — a VISUAL partition by the narrator's off-thesis opinion, **not a
  second basket**: a flagged name is still in the basket and still saves; the split just lets the operator collapse
  the junk-heavy pile in one click. Both groups start open (the split itself hides nothing).
- **"Placed, low quality"** (G) — a **cheap-cut accelerant** gated on **two conditions** (both required):
  the narrator's `off_thesis` flag **AND** any registered junk-tell from [`frontend/src/workbench/junkTells.ts`](../frontend/src/workbench/junkTells.ts).
  The LLM flag is the recall guard — a loose tell can't demote a name the model approved (tell-only → stays in
  **Placed**; flag-only → **Placed, flagged**). Seed tells: sole SIGNAL acronym match (`isAcronymTerm` in
  `workbench/format.ts` — HBM ✓, DRAM ✓, "high-bandwidth memory" ✗) and name token co-occurrence pairs
  (BlackRock+Trust, Royce+Trust). Add a tell = one registry line in `JUNK_TELLS`. Starts **collapsed** (a cluster
  to visit, not a wall), with a group-level **"exclude all N"** (visible bulk; every row stays greyed-in-place and
  re-includable — `excludeKeys`, the same additive contract as clear-un-accepted).
- **Precedence:** low quality > flagged > clean (the To-Review precedence idiom). Grouping renders **only when it
  discriminates** — everything-in-one-group is just the flat list (a partition that doesn't discriminate is
  noise, #7).
- **Run-state caveat (by design):** `matched_terms` and the off-thesis flag are draft-run provenance, never
  promoted — so these lenses exist during a draft session, not on re-entry of a saved thesis. They serve the
  cheap cut, which is when the run state is live.
- **The #9 spine is untouched:** membership, include, and Save are computed over the whole draft regardless of
  grouping (test-guarded, same as the sort/filter view).

**No archetype at placement (item F).** The placed row carries **no archetype editor** — a stored value shows as
a read-only chip; an unset one shows nothing. The archetype is decided ONCE, on the scored view's rail (the
`archetype_hint` → apply, or the rail's manual set — both `operator_edited`, #10); a placed-but-not-finalized
member is `NULL` end-to-end, never a default. See `WORKBENCH_ENRICHMENT.md` + `INVARIANTS.md` #10.

## Mark for data — gate 2's per-name opt-in + the per-SECTION run (the scored view)

**The section button** (`⇣ get data — {section} (N)`): one deliberate click covers the ACTIVE value-chain
section — for every member it pulls EOD price bars (the DECOUPLED price leg,
`POST /workbench/securities/{id}/ingest-prices` — incremental, cache-first; see `FEED_LOOP.md`) and
prefetches the extraction candidates into the same query the rows + rail read — so the section lands
mostly-complete: caps computed where shares are already ratified, archetype hints live, purity candidates
staged one ratify away. **Bounded by the section** (a slice of the saved shortlist, never the draft),
**extract-and-proposes only** (nothing auto-confirms — purity stays HUMAN; every fact still passes the
operator's per-fact ratify), failures reported LOUD and named per ticker. The per-name row button stays
the surgical option and pulls the same full set (extraction + prices) for one name.

### The per-name opt-in

The three-gate flow's middle step, shipped as **the control IS the trigger**: a scored row without confirmed
fundamentals shows **⇣ get data**, which fires **that one name's** extraction through the existing per-name
endpoint (`GET /workbench/securities/{sid}/extract` — 2–4 EDGAR requests, cache-first, response-only). The mark
and the spend collapse into one deliberate click — **cost is the operator's to spend, never ambient**: extraction
never runs on draft, save, promote, or render, and never batches over the basket. Per-name states: fetching →
**✓ data ready — ratify** (opens the name; the row control and the rail's FactsPanel share ONE query, so the
candidates render instantly) → per-name, retryable failure. Once any fact is **ratified** the control disappears
(the meters + the badge take over), and the section header carries the **funnel**: "data confirmed on K of N" —
one `memberHasFundamentals` rule across the badge, the control, and the funnel. Deliberately NOT built until the
per-click flow proves annoying at real shortlist sizes: a mark-checkbox state, a "run marked (N)" batch runner,
marks persistence (the named fallback is checkbox + runner).

**Save legibility (D).** A saved exit from the editor surfaces a note on the scored view: the thesis is
re-openable with **✎ Edit the chain**. Honest scope: re-entry restores the **saved basket** — not the draft-time
discovery context (matched terms, flags, To-Review queues are run state; re-discovering is a re-draft). The
visible inverse of Save (reversibility principle #1); cleared on any navigation that changes what it refers to.

## Invariant fit

- **#9 (recall sacred):** default-included; every "hide" is a visible, reversible, still-promotable collapse
  (excluded rows, the To-Review drawers, filtered-out names) — never a silent drop; the VIEW never changes what
  Save persists.
- **#7 (inverse loudness):** a badge true of every row doesn't render (the fundamentals gate); loudness marks the
  minority/exception per bucket — Placed flags rare junk, To-Review highlights rare keepers.
- **#10 (recommends, operator decides):** sort/filter/include/accept/re-segment are all operator acts; the
  off-thesis flag and the low-quality cluster change nothing on their own (grouping is a lens — the
  exclude is still the operator's click).
- **#4 (deferential on thesis):** conviction is the operator's weight, never an input to the call.
