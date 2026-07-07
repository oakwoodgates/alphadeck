# TRIAGE.md — basket crafting (the ~90 → ~15 stage)

> Repo path: `docs/TRIAGE.md`. The **TRIAGE** stage (`STAGE_MODEL.md`): after DISCOVER finds the names and SURFACE
> populates them, the operator turns a ~90-name discovered draft into a **chosen, ordered, weighted basket** — in
> minutes, not by scrolling a flat list. This is the "construction" stage of the buy-side funnel. Mostly a
> frontend + wiring stage (no new spine): the promote endpoint already **full-replaces** the basket from a
> client-sent list, so crafting is a matter of sending the right subset. Surface:
> `frontend/src/workbench/ChainEditor.tsx` + `useChainDraft.ts`; the writer:
> `POST /workbench/theses` (`app/routers/workbench.py`).
>
> **Status: BUILT** — include-controls (#113), the sortable/filterable view (#114), the conviction field (#115) +
> the naming-collision guard (#116), the To-Review triage ruleset + the "Discovered" holding pen + the wired seg
> dropdown (#118), and the cheap-cut board relief (the placed-board display partitions incl. the acronym-collision
> lens, the hoisted noise sections, and the Save re-entry note — PR-A of the three-gate TRIAGE round).

---

## The prune — include-controls (#113)

A per-name **include toggle** on every placed row. **Save persists ONLY the included subset** (the promote
full-replaces, so excluded names simply aren't sent; the draft is reproducible by re-drafting, so nothing is truly
lost).

- **Default-INCLUDED (#9):** a discovered name starts IN; the operator *unchecks* to exclude. Nothing is silently
  dropped — an excluded row stays **visible** (greyed), one click from re-inclusion.
- **Orthogonal to accept / authorship.** Include ≠ accept. Accept is the authorship flip (`system_drafted →
  operator_set/operator_edited`, the re-roll survivor); include is "goes in the saved basket." A name can be
  **accepted-but-excluded** or **included-but-not-yet-accepted**. `include` is FE-only state (`excluded: Set` in
  `useChainDraft`), never persisted, never touches `authored_by`.
- **Bulk actions:** include all / exclude all / **clear un-accepted** (exclude every still-`system_drafted` name —
  the fast path to just-my-vouched names — without touching authorship).

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
  full-replace promote, mirrors `thesis_fit`); it never touches the meters / verdict / grade / exit-by. The entry
  grade stays **signal-derived + deterministic** — the system sizes from the triggers, it does not judge the idea.
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
- **Off-thesis** (the narrator's `off_thesis` bool — see `CHAIN_DRAFTER.md`) → **quiet, collapsed** into a "Low
  signal" section. **No yellow flag** — flagging the majority just moves the noise around; loudness marks the rare
  exception, which in To-Review is the *keeper*, not the junk.
- **Ticker-less** (a resolved filer with no listed ticker — likely a sub / holdco / debt issuer) → collapsed into a
  "No listed ticker" section. Probably not directly investable, so its
  **check-to-add is disabled** (it never enters the basket by a stray click). Still **not dropped** (#9 — the row
  is surfaced, and a name that genuinely belongs is reachable via the master **name search**, which promotes by
  `security_id`).
- **Precedence:** off-thesis > ticker-less > keeper.
- **The two noise buckets are TOP-LEVEL collapsible sections** (siblings *after* To review, not children inside
  it — the C-A hoist): they're distinct buckets, each independently collapsible, so a big draft's To-review block
  stays keeper-sized. The To-review header count is **keepers-only**.

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
- **"Placed, acronym-only"** (G) — the collision lens, a **cheap-cut accelerant**: a name whose ONLY discovery
  match is a single collision-prone SIGNAL term clusters here for one scan-and-clear pass. *Collision-prone* =
  a single all-caps token (`isAcronymTerm` in `workbench/format.ts` — HBM ✓, DRAM ✓, "high-bandwidth memory" ✗;
  NAND-style real words cluster **by design**, the v1 rule is deliberately simple and gets tweaked on live
  behavior). A genuine name matches the acronym PLUS the spelled-out phrases, so it never clusters. Fully
  mechanical — derived from the row's `matched_terms` + the working term set; **no model, no authoring step**.
  Starts **collapsed** (a cluster to visit, not a wall), with a group-level **"exclude all N"** (visible bulk;
  every row stays greyed-in-place and re-includable — `excludeKeys`, the same additive contract as
  clear-un-accepted).
- **Precedence:** collision > flagged > clean (the To-Review precedence idiom). Grouping renders **only when it
  discriminates** — everything-in-one-group is just the flat list (a partition that doesn't discriminate is
  noise, #7).
- **Run-state caveat (by design):** `matched_terms` and the off-thesis flag are draft-run provenance, never
  promoted — so these lenses exist during a draft session, not on re-entry of a saved thesis. They serve the
  cheap cut, which is when the run state is live.
- **The #9 spine is untouched:** membership, include, and Save are computed over the whole draft regardless of
  grouping (test-guarded, same as the sort/filter view).

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
  off-thesis flag and the acronym-collision cluster change nothing on their own (grouping is a lens — the
  exclude is still the operator's click).
- **#4 (deferential on thesis):** conviction is the operator's weight, never an input to the call.
