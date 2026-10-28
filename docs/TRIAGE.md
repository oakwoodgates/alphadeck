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
> the three-gate round #127–#129, sections + honest flags #132–#136; **Discovery cleanup S1** replaced
> include+accept with the confidence ladder + honest authorship — this doc reflects the post-S1 surface).

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
   re-tag the business type on the rail if the maps misread it, weight with conviction, promote.
   `WORKBENCH_EXTRACTION.md` owns the flags.

## The prune — the confidence LADDER (Discovery cleanup S1) + the durable NO (#7)

The old include+accept pair (two orthogonal axes) collapsed into **ONE ladder** per name — **Excluded →
Included (system-recommended) → Signed off**, excluded wins. The affordance: the per-name **include
toggle** stays (the gate — **Save persists ONLY the included subset**; the promote full-replaces, so
excluded names simply aren't sent) and a **sign-off toggle** rides the row, reachable only while included
(the excluded stub hides its controls).

- **Default-INCLUDED (#9):** a discovered name starts IN; the operator *unchecks* to exclude. Nothing is silently
  dropped — an excluded row stays **visible** (grayed), one click from re-inclusion.
- **Sign-off endorses the NAME, nothing else.** `BasketMember.signed_off` is a per-NAME marker ("this
  company belongs in the thesis"): it **never sets authorship** (the description stays a model draft
  until the operator edits it — the honest-authorship rule below), **never gates Save** (include gates;
  sign-off marks), and **never feeds the call/score (#4)**. It does **not pin** against a re-draft —
  only an operator-EDITED description pins; the flag is *carried* across the re-roll, and a signed-off
  name is never dropped (#9). A **hand-add** (master search / ETF surface / holdings include) enters
  auto-signed-off — the operator's pick IS the endorsement.
- **HONEST AUTHORSHIP.** `authored_by` records who wrote the member's *description* (`thesis_fit`):
  `system_drafted` renders **"model draft"** until the operator EDITS the text → `operator_edited`,
  **"your words"**. Nothing else flips it (the retired old `accept` flipped authorship *without changing
  the text* — the lie S1 removed; migration 0035 re-based the stored rows, and promote legacy-translates
  a stray `operator_set` payload to `signed_off=true` + `system_drafted`). Editing the description does
  NOT auto-sign-off — writing a note and endorsing the name are separate acts.
- **Bulk actions:** include all / exclude all / **clear not signed-off** (exclude every name the operator
  hasn't endorsed — the fast path to just-my-endorsed names — keyed on the flag, touching neither
  authorship nor the flag itself; working-scoped, the frozen Basket is never swept).
- **The exclusion is DURABLE (#7).** Save also persists the current exclusion set — with the optional
  **"rejected because X"** reason (a quiet inline input on the grayed row; skippable, editable) — through the
  sole-writer `PUT /theses/{id}/exclusions` (`thesis_exclusion`; the term_set structural wipe-guard, so a
  promote can never blank the pruning). On the next session or re-draft the editor **seeds** its excluded
  state (and the To-Review keeper set-asides, for resolved names) from the persisted set: a rejected name
  arrives **pre-grayed, visible, one click back**. **THE #9 LINE: discovery never filters on exclusions** —
  a re-draft still surfaces every name; the NO is an editor default, never a recall cut. Prior NOs the
  session never re-surfaced are carried forward on Save; re-including a name withdraws its NO. (v1 scope:
  keyed by `security_id` — unresolved names' set-asides stay session-local.)

**Pick-mode (cherry-pick) — the inverted ENTRY gesture for a starter basket.** Prune-mode's default-included
flood fits an established thesis; for a small starter basket, unchecking hundreds is the wrong gesture. In
pick-mode (⚡ quick draft's default; a tri-state select overrides either lane) genuinely-new placed names land
in a visible **Recommended** pile and the operator **checks keepers IN** — the To-Review add, with the default
flipped. A pick lands on the ladder's **middle rung** (Included, `signed_off: false` — never auto-endorsed),
and the bulk **"✓ sign off all picked"** endorses the origin-tracked picked set in one act — endorsement stays
a DISTINCT act from picking, exactly as sign-off stays distinct from include. Existing basket members are never
touched by the mode (only the append-new branch redirects), and unpicked rows stay visible working state. Full
mechanics: `CHAIN_DRAFTER.md` §the draft/ratify UI.

**The placed mode — Research ⇄ Pick (the PLACED section's gesture toggle).** Distinct from the load mode above
(which decides WHERE a new name lands), the placed-mode toggle at the head of the bulk bar decides HOW the operator
decides the placed list. **Research** is the prune above: every name starts checked + open, unchecking is the
durable NO (`excluded`, persisted on Save). **Pick** is the additive select: every name starts un-picked + open
(available), checking **picks** it into the basket and collapses it to a quiet `picked` stub (its sign-off and
send-back stay reachable — #1), and **pick never writes a durable exclusion**: an un-picked name is *available,
not rejected* — a pick Save persists the picked rows through the full-replace promote and carries the prior NOs
forward verbatim minus any name picked this session (a persisted NO renders open + un-picked with a quiet
`excluded in research` tag; picking it withdraws the NO). The two sets are per-mode and the other lies dormant
(`selected` vs `excluded`); the reads keep their names (`isIncluded` / `includedBasket`) so Save is mode-blind.
Switching is a **working-scoped reset, never a restore** — entering Pick keeps the saved Basket's kept names and
every signed-off name checked (mode entry never empties the saved basket) and un-picks the rest; entering Research
returns to the fresh-mount state (persisted NOs pre-grayed, signed-off names checked, all else included) — never a
literal all-check, which would silently withdraw every durable NO on the next Save. Sign-off is untouched by either
reset; a sign-off in Pick also picks the name (endorsed ⇒ in), a withdraw never un-picks. Explicit arrivals (a
To-Review add, a pile pick, a hand-add, an ETF sleeve) enter picked; a bulk draft arrival enters undecided. The
research-only bulk prunes (include/exclude all new, clear not signed-off, the low-quality group's exclude-all)
don't render in Pick (they can't act — WB#3). The mode + selection ride the autosaved session (additive fields, no
schema bump) and a Save writes the session directly before the promote, so a click-then-Save can't lose its pick.

## The find — the sortable / filterable view (#114)

The placed list becomes a triage instrument: **sort** by name / segment / sector, **filter** by
segment / fundamentals / **sign-off** (the S1 rename of the old authorship filter — the ladder rung is
what the operator prunes by) / include / off-universe, and a **compact** toggle that collapses
the thesis-fit prose for a scannable read. This is how pruning 90 names stays fast. The list renders
**one row per NAME** — a name the draft recommends into N links holds N real membership rows (see the
multi-membership note below), displayed as one row with a read-only chip per link; every count counts
names.

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
… 5 = full). **S1 moved the control OFF the discovery surface** (ChainEditor renders no conviction select;
weighting returns on the dedicated triage screen, a later slice) — the FIELD stays on the model/DB and a
stored weight rides Save untouched. The Board is read-only monitoring — re-weighting
while watching is a later MONITOR-stage feature. A number, so future size-weighted attribution can derive relative
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

**The recommended-link chips + the multi-membership rows (S1).** A placed row shows the draft's
recommended value-chain link(s) as **READ-ONLY chips** — styled as an LLM *recommendation* (blue),
deliberately distinct from the machine-fact identity chips (muted). **The chips are REAL memberships:**
a name the organizer recommends into N links is **N `basket_member` rows** (same `security_id`, one per
`segment`) that `loadDraft` preserves and the promote persists — the display groups them into one row
per NAME, and every per-name action (include / sign-off / description-edit) co-mutates all of a name's
rows via the `memberKey` keying. **The seg dropdown and the per-name re-segmenting are GONE from this
surface** — segment sorting moves to the triage screen (a later slice); this surface shows the draft's
placement, it doesn't edit it. (A per-membership add/remove — the `(security_id, segment)` re-key — is
likewise the triage slice, not this one.)

**The "Discovered" holding pen.** Names discovered-but-not-organized land in a catch-all segment labeled
"Discovered". It is an **unsorted holding pen, not a value-chain link** — de-linked visually (muted,
"unsorted — not a link"), with a quiet "N unsorted" nudge (honest about this surface: the draft didn't
arrange them; sorting lives on the triage screen). Each placed row also shows the **company name**
(bridged by `security_id`) and the SURFACE identity
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
  to visit, not a wall), with a group-level **"exclude all N"** (visible bulk; every row stays grayed-in-place and
  re-includable — `excludeKeys`, the same additive contract as clear-not-signed-off).
- **Precedence:** low quality > flagged > clean (the To-Review precedence idiom). Grouping renders **only when it
  discriminates** — everything-in-one-group is just the flat list (a partition that doesn't discriminate is
  noise, #7).
- **Run-state caveat (by design):** `matched_terms` and the off-thesis flag are draft-run provenance, never
  promoted — so these lenses exist during a draft session, not on re-entry of a saved thesis. They serve the
  cheap cut, which is when the run state is live.
- **The #9 spine is untouched:** membership, include, and Save are computed over the whole draft regardless of
  grouping (test-guarded, same as the sort/filter view).

**No characterization at placement (item F).** The placed row carries **no type control** — the business type
derives from the master identity (Business-Type M1) and is re-taggable ONCE, on the scored view's rail
(the per-security endpoint, #10); a placed-but-not-finalized
member is `NULL` end-to-end, never a default. See `WORKBENCH_ENRICHMENT.md` + `INVARIANTS.md` #10.

## The value-chain editor (the scored/triage view owns chain arrangement) — chain-editing #1–4

The **two-screen split**: the first screen (the `ChainEditor`, opened by **✎ Edit the basket**) owns basket
**MEMBERSHIP** — discover, *Draft-from-narrative*, add/remove names, the confidence-ladder prune (WHAT names
are in the basket). The **scored / triage view** owns the **value chain** — HOW it's arranged (the links) and
WHERE each name sits. Both persist the full thesis through the SAME full-replace `POST /workbench/theses`
promote; a scored-view edit **immediate-promotes** per action (the pattern the business-type re-tag / sleeve
include already use), and each screen re-reads on the other's write. The pure transforms live in
[`frontend/src/workbench/chainOps.ts`](../frontend/src/workbench/chainOps.ts), **mirroring** `useChainDraft`'s
draft mutators (never editing them) but wired to the promote instead of a local draft.

- **#4 — move a name between links (the DD-rail mover).** The per-name rail carries a **flat multi-select** of
  the real links (one checkbox per link) in place of the old read-only segment cell. `checked` = the name's
  live memberships; toggling rebuilds that name's rows into the checked set (a name in N links = N
  `basket_member` rows, one per link, sharing a `security_id`) and promotes — copying **every** per-name field
  forward so nothing is wiped, only the segment differs. Multi-membership is a normal check of >1 box.
- **#1–3 — the link editor (`✎ edit links` reveal on the tab strip).** Off by default (the tabs stay pure
  navigation — inverse-loudness #7); reveal → per-link **rename** (cascades the new label onto every placed
  member so none orphans), **reorder** (←/→ swap), **remove** (multi-safe: drops a name's redundant row when
  it's kept elsewhere; routes a **last** placement to the Discovered floor), plus **+ add link** — which works
  **flat** (no links yet), so the first link is buildable from SCORE without opening the editor.

**The Discovered FLOOR (the catch-all, never a vanish).** A name with **no real link** — cleared, its last
link removed, or a NULL / orphan (a label not in the chain) — normalizes to **Discovered**: a real, visible
tab, never `null` (a null-segment name drops out of every tab, unreachable to re-select). On **read** the
scored view groups by an `effectiveSegment` and **synthesizes a Discovered tab** when any name resolves there
(NULL/orphan names stay visible — #9 / WB#2). On **write** the promote payload self-heals any pre-existing
orphan → Discovered, so a stale label can't 422 the segment-consistency validator. For a **real** link
`segment` is display / structure only — these edits touch labels + rows, **never** verdict / grade / exit-by
(#4); the one exception is **Context / Bench** (below), which take a name *out of the call's inputs* — still an
operator placement, never a verdict. Every action is reversible (re-check / re-add / rename-back — WB#1). The promote round-trip is count-the-table–proven end to
end (`tests/app/test_workbench_api.py` — move / clear→Discovered / rename-cascade / remove-last→Discovered,
`count(*)` before + after).

## Context and Bench — the two silent links (the scored view's rail)

Every thesis carries two **fixed** value-chain links, **Context** ("Watched for context: buyers, counter-case
names, giants where the theme is a slice. Never counts toward the call.") and **Bench** ("Speculative names:
pivots, distressed or thinly traded. Never counts toward the call."). A company in either is **on the card and
visible but never counts toward the call** — the backend removes those rows at the call seam; the frontend
computes nothing about the call (#4: a placement is the operator's, never a verdict). The pair is defined once
in [`useChainDraft.ts`](../frontend/src/workbench/useChainDraft.ts) (`CONTEXT`, `BENCH`, `withSilentLinks`,
`reservedLinkName`) and mirrored by `chainOps.ts`. **Naming:** never call either link "watch" —
`CallCard.watch_members` already means the confirmation-only members; the Cockpit's tags read lowercase
`context` / `bench`.

- **Fixed, last, protected.** Always the **last two links**; can't be renamed, deleted or reordered, and no
  other link may take either name (any case — the add and rename inputs refuse it). A new link lands *before*
  them. **Discovered is not pinned** — it keeps its position and behavior (a new link can land after it, it
  can be reordered).
- **Silent XOR voting.** A company is one or the other, never both: moving it into Context/Bench collapses
  **all its rows to ONE row** there; moving it into a real link takes it out (the backend 422s a payload that
  breaks this, so the pure `toggleMemberLink` never produces one).
- **Where the operator moves a name:** the scored view's **DD rail**. The links list is the real links, a quiet
  divider, then Context and Bench (each with its descriptor on the hover) — the same checkboxes as the real
  links. **Only the operator** puts a name there: the chain drafter never does (a model-proposed
  "Context"/"Bench" link is rerouted to Discovered — by the backend, and again defensively on the client).
- **The checkbox IS the visible inverse (WB#1) — honestly, the MVP.** Unchecking Context floors the name to
  the Discovered pen; checking a real link moves it out of Context. The way back is *not* always the prior
  set: a multi-link name checked into Context collapses to that one row, and unchecking Context lands it in
  Discovered, not back in its earlier links (one click per link restores them). No new undo machinery,
  nothing deleted (WB#2, #9). After a move the view **follows the name** — the tab switches to its new home
  and the rail keeps showing the same company, so an immediate undo click acts on the name you just moved.
- **Hidden when empty, except in the editors (WB#3).** The first-screen editor and the scored view's
  **✎ edit links** always show both as **fixed, read-only rows** (no rename, ←/→ or ✕); the scored view's
  **tabs** and the Cockpit hide an empty one. A silent tab shows its descriptor when active. The pair never
  makes a flat basket "grouped" on its own (a thesis with no real link and no silent name still reads "No
  value chain yet").
- **Quiet by design (#7):** muted rows/tabs/chips, no badges — a name that never counts toward the call is the
  quietest thing on the page. The Cockpit side (buckets, lenses) is `BOARD.md` "The grouped basket".

## Context roles — what a company is on this card

Each basket membership may carry an operator-authored **context role** — what part this company plays on THIS
card — so a later phase can read signals from its own disclosures. **Phase 1 stores it, carries it end to end,
lets the operator set and clear it, and shows it quietly; nothing else.** Six values (wire value → label →
hover): `payer` → **Payer** ("The demand side: buys the theme's product or pays its price."), `counter_case`
→ **Counter-case** ("Loses if the thesis plays out, or wins if it fails."), `direct_read` → **Direct read**
("Not in the business, but its numbers forecast what the voting names report."), `participant` →
**Participant** ("Does the thesis itself, but it is a slice of the company."), `benchmark` → **Benchmark** ("A
price series that IS the underlying (a spot fund, a physical trust)."), `financier` → **Financier** ("Funds
the build-out (lenders, convertible buyers)."). Defined in `domain/enums.py` (`ContextRole`); the labels and
hovers in `frontend/src/workbench/format.ts` (exhaustive `Record`s over the generated union). **Naming:** it is
not `BasketMember.role` (the free-text prose field) and not `SignalEvent.role` (entry_trigger / risk_signal).

- **Per company per card.** Stored on `basket_member` (`context_role`, migration 0069 — a nullable column and a
  named CHECK on the six values), never on the security. A company in N links is N rows, and the role rides
  **every** one, identical. Allowed on any row — Context, Bench, a real link, Discovered. `NULL` = unset, never
  a default.
- **The promote rule** (`POST /workbench/theses`, after canonicalize and the coherence pass, keyed on
  `security_id` so sibling listings of one company count as one; a row with no `security_id` is never grouped):
  two **different** non-null roles on one company is a **422** naming the company and the roles it was sent,
  with nothing written; a `NULL` row beside a set role **inherits** it on write (so a re-draft or hand-add that
  puts an existing company into a second link succeeds); **clearing** is `NULL` on every row of the company.
  `domain/thesis.py`: `context_role_conflicts`, `with_inherited_context_roles`.
- **Where it is edited:** the scored view's **DD rail** — one small "Context role" select per company, an ETF
  sleeve's rail included (a spot or metal fund is the usual `benchmark`) ("— none" plus the six; each option's
  hover is its description), an immediate promote through the same writer as the
  link checkboxes (`setMemberContextRole` sets it on every row; the spine's rows ride along untouched). "— none"
  clears it — the visible inverse (WB#1). The first-screen editor never authors a role, and its Save
  full-replaces the basket, so a draft restored from an older autosaved session overlays the **saved spine's**
  role by `security_id` (the saved spine wins, even when it says unset) — otherwise an old blob would wipe it.
- **Shown quietly (WB#3):** a lowercase tag (`payer`, `counter-case`, `direct read`…) beside the name on the
  scored row and in the Cockpit basket table, **only when set** — never a "—" placeholder, never a badge.
- **Never a call input (#4), never written by the model (#3).** It is display metadata: no `CallCard` field
  records it, no detector, grade or veto reads it (a golden test assembles the same thesis with and without roles
  and asserts byte-identical cards), and the chain drafter, discovery and the reconciler leave it `NULL`.
  Snapshot rule: a roster snapshot object carries `context_role` **only when set**, so a role-less roster's
  content hash is unchanged and an unchanged re-promote still appends no snapshot row.
- **Not built (the next phase):** reading signals from a role-tagged company's disclosures — the **read-through**
  (its 8-K / earnings-release numbers shown beside the names it forecasts). Phase 1 has no read-through display,
  endpoint or fetch; the role is only the operator's label for it.

## Mark for data — gate 2's per-name opt-in + the per-SECTION run (the scored view)

**The section button** (`⇣ get data — {section} (N)`): one deliberate click covers the ACTIVE value-chain
section — for every member it pulls EOD price bars (the DECOUPLED price leg,
`POST /workbench/securities/{id}/ingest-prices` — incremental, cache-first; see `FEED_LOOP.md`) and
prefetches the extraction candidates into the same query the rows + rail read — so the section lands
mostly-complete: caps computed where shares are already ratified, business types derived, purity candidates
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
**✓ data ready — ratify N** (opens the name; the row control and the rail's FactsPanel share ONE query, so the
candidates render instantly) → per-name, retryable failure. The section header carries the **funnel**: "data
confirmed on K of N" (the `memberHasFundamentals` rule, shared with the badge). Deliberately NOT built until the
per-click flow proves annoying at real shortlist sizes: a mark-checkbox state, a "run marked (N)" batch runner,
marks persistence (the named fallback is checkbox + runner).

**Get-data also applies the AUTO shares count** (`WORKBENCH_EXTRACTION.md`) — a confirm nobody could actually
perform. A FLAGged count still goes to the operator; the market cap is the real check.

**The control counts what's LEFT to ratify**, and disappears only when nothing remains. It used to hide once
*any* fact was ratified — already a bug (one confirm silenced a name with two facts outstanding) that
auto-applying shares would have industrialized on every clean name. It uses the same on-file predicate the
FactsPanel does, so "ratify 2" and the panel's two un-filed rows can't disagree.

**Save legibility (D).** A saved exit from the editor surfaces a note on the scored view: the thesis is
re-openable with **✎ Edit the basket**. Honest scope: re-entry restores the **saved basket** — not the draft-time
discovery context (matched terms, flags, To-Review queues are run state; re-discovering is a re-draft). The
visible inverse of Save (reversibility principle #1); cleared on any navigation that changes what it refers to.

## Invariant fit

- **#9 (recall sacred):** default-included; every "hide" is a visible, reversible, still-promotable collapse
  (excluded rows, the To-Review drawers, filtered-out names) — never a silent drop; the VIEW never changes what
  Save persists.
- **#7 (inverse loudness):** a badge true of every row doesn't render (the fundamentals gate); loudness marks the
  minority/exception per bucket — Placed flags rare junk, To-Review highlights rare keepers.
- **#10 (recommends, operator decides):** the drafted placement — the link chips, the drafted description,
  the off-thesis flag — is a RECOMMENDATION the operator triages on the ladder; sort/filter/include/sign-off
  are all operator acts, and grouping is a lens (the exclude is still the operator's click). A field is the
  operator's **only if the operator changed it** — sign-off endorses, it never authors.
- **#4 (deferential on thesis):** conviction is the operator's weight and sign-off is the operator's
  endorsement — neither is ever an input to the call. The same holds for a company's context role: the
  operator's label, display only, never written by the model (#3).
