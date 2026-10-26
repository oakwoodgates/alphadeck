# BOARD.md — the MONITOR surface (Board · Cockpit · CallCard)

> Repo path: `docs/BOARD.md`. **How the operator-facing MONITOR surface works** — the Board's lifecycle
> columns, the Cockpit's deep view, the CallCard rail, decision capture, and the nightly rhythm that feeds
> them. This is the *surface* doc; the *brain* is `CALL_LOGIC.md` (how signals become a call), the *rhythm*
> is `FEED_LOOP.md` (the ingest + the call-of-record cron), and the *frame* is `STAGE_MODEL.md` (MONITOR is
> the back half: the chosen basket parks as a thesis → incubate → warm → arm → monitor after an
> operator-entered position).
> Code: `frontend/src/board/` · `frontend/src/cockpit/` · `frontend/src/components/{CallCard,MemberMenu,
> DecisionActions}.tsx` · the reads they render come from `GET /theses` + `GET /theses/{id}/call`.
>
> **The design law of the whole surface is inverse loudness (invariant #7):** quietness scales with how
> early it is. Incubating must not nag; loudness is reserved for Armed. Every element below is calibrated
> to that — if something reads loud, it should be the exception demanding action, never the rule.

---

## The Board — four columns, one loud strip

**Everything on the Board is recomputed on read.** A thesis card sits in the column its *live call*
computes at the selected as-of (`useCalls` per thesis → `card.state`); nothing about placement is
persisted for display. Change the as-of, and the whole board re-derives — it is a point-in-time view.

- **The columns are the lifecycle, not a ranking:** `Incubating (quiet · do not act) → Warming (stirring)
  → Armed (act now) → Managing (entered position · thesis monitoring)`. Managing does not mean Alpha Deck
  manages the position or portfolio risk. The lifecycle is a loop, not a ratchet — cards move back
  when triggers age out or a position closes (`CALL_LOGIC.md` §2). Columns never rank theses against each
  other (#4: the platform is deferential on thesis — it times, it doesn't judge ideas).
- **The as-of scrub** (top right) defaults to **local today** (`todayISO` — deliberately not UTC, which is
  tomorrow after ~8pm ET; the gate-2 catch). Scrubbing back recomputes the board with TODAY's rules on
  what was PUBLIC by the end of that date — the no-lookahead rule applies to the UI too, judged on the public
  clock (operator decision 2026-10-21): a fact dated after the as-of is invisible, and so is one PUBLISHED
  after it (a Form 4 accepted later, a vendor correction made later); a fact public by then but ingested
  late by our database is NOT hidden for it (`GET /theses/{id}/call?asof=` threads `serve_view(asof)` —
  `(known_at_for_asof(asof), "public")` for a past as-of, `(None, "record")` for the live one;
  `INVARIANTS.md` #4, `db/public_clock.py`). A fill the operator logged after the as-of stays invisible too
  (an operator decision counts from when it was made). The past board says so in one quiet line — "Recomputed
  as of <date> — today's rules on what was public then, not the recorded call" (F11). The remaining caveat:
  the roster is point-in-time only from 2026-09-15 (`basket_snapshot`; before that today's basket stands in —
  `INVARIANTS.md` §Known gaps), so a scrubbed-back card is a labeled recompute; what the platform actually said
  that night is the Scoreboard's record, never this recompute.
- **The Decision Queue** — the one loud element: an armed-only strip so an actionable call can't be
  forgotten. A multi-name theme shows its **single top-ranked actionable name** with a quiet `+N` hint
  that a ranked menu sits behind it (anti-flooding — never every member). Empty state: *"Nothing armed.
  Nothing to do. ✓"* — a calm board is a working board. The headline name — and a single-name card's ticker —
  carries the same quiet **data-flag mark** as its Cockpit row (S3, `GET /data-flags`), only when a flag
  reaches a number on it. Each item also states its **act-by clock** — "act within 3d", the card's
  `arm_until` (the headline name's entry window) — muted, and warm with "· entry window closing" only when the
  call's own `entry_window_closing` flag on the headline name is set (Q14; read, never re-derived). It states
  the clock; it never reorders the queue by it (columns and queue do not rank theses, #4).
- **"Calls that didn't compute" (keep-it-visible, #216)** — when a thesis's `/call` errors, it lands in a
  **visible strip at the bottom of the Board** ("⚠ Calls that didn't compute (N)") instead of silently
  vanishing from every column, and the Decision Queue **withholds its all-clear** ("Some calls didn't
  compute — see below."): a broken call might have been armed, so the calm empty state must not lie. A
  failed call is an exception the operator must see, never a quietly-dropped card — the same
  recall-is-sacred instinct as the basket's Quiet bucket.
- **The ThesisCard**: ticker (or a basket marker for a theme), name, narrative, and a state-appropriate
  footer — Armed leads with the **entry verdict** (STARTER/CORE — the categorical call-strength posture),
  with the conviction grade as secondary context ("core setup" — the same "core setup" / "flip setup" wording
  on the rail's head chip and the ranked member menu: it rates the call's strength class, never the idea, so it
  is never called a "thesis", #4); neither label sizes a trade. A bare CORE
  badge up front read as "go big"; this split exists to stop that over-commit misread. Non-armed cards show
  the two-key **readiness pips** (0–2) — one pip per KEY, conviction left and confirmation right, the hover
  naming which has turned ("Conviction ✓ · Confirmation ○"), so a confirmation-only card never reads as
  "conviction fired". Armed
  cards carry the `CALL READY` flag.
- **Archive, never delete** (board hygiene): hover a card → a quiet **✕** appears (a *sibling* of the
  card, top-right). Archiving drops the thesis into the collapsed **Archived (N)** section at the bottom —
  visible, restorable in one click, out of the default lists and the nightly cron's walk, and **its call
  is not computed** (cost stays the operator's). The spine, calls log, and decision log all stay; a
  promote can neither archive nor resurrect one (the structural guard on `archived_at`).

## The Cockpit — one thesis, deep

Selecting a card opens the Cockpit: the narrative (the operator's words, preserved), the **grouped
basket table + the per-name panel** (the next two sections), evidence, and two **operator-authored
lists** that render *even at zero* (an empty section used to vanish, which made "there's no way to
author one" invisible):

- **Catalyst calendar** — the thesis-level *surface* events (label · kind · date or a fuzzy "~Q3").
  Dated entries within the `exit_by` signal-validity horizon ride the CallCard's catalyst surface; entries ≤ 21 days out
  highlight as `soon`. Edited in place (`✎ edit` / `+ add catalysts`); saves through the sole-writer
  `PUT /theses/{id}/catalysts` — a promote can never wipe the list. **Distinct from the per-name
  conviction FACTS** (the Key-1 arming inputs), which are authored on the Workbench rail with a required
  citation (`WORKBENCH_EXTRACTION.md`).
- **Kill criteria** — the documented "what would kill this thesis." Consumed by the deterministic
  counter-case on the CallCard — an authored thesis stops reading "no documented counter-case."

The Cockpit shares the Board's as-of; the call rail beside it recomputes live at that date — under the same
`known_at` cap for a past as-of, and so do the display signals (`/display-signals?asof=` threads it too).

### The grouped basket — per-name buckets

The basket table partitions, **in the default lens**, by each member's **own** call state — the
Board's column idiom applied in-table, strongest → weakest, one **collapsible header** (chev · label ·
hint · count · the moving line — the Workbench's To Review heading idiom, the toggle bucket-colored) per
**populated** bucket (an empty bucket renders no header — loudness marks the exception) and a status dot
per row:

- **Managing** — `verdict === "managing"`: the held name, when the open position carries its
  `security_id` (a take logged **on a name** — per-member Managing attribution, `CALL_LOGIC.md` §4).
  An unattributed position (a thesis-level take; the seed-era stored columns) emits no member
  verdict, so the group stays empty — **render-if-present** either way, never a guessed name.
- **Armed** / **Hold clock closing** / **Theme-armed** — the `armed_members` tier, split by its flags.
  The middle bucket is the `lapsing` flag (bucket key + CSS class keep that wire name): the member's
  **hold clock** (`exit_by`) is inside the freshness dial — header "Hold clock closing · signal-validity
  horizon near". It was headed "Lapsing · entry window closing" until Q14 (2026-10-18), which named the
  wrong clock: `lapsing` never read `arm_until` (69% of arm-window expiries never showed it, MEASURED).
  When a member is lapsing *and* theme-armed, **the hold-clock bucket wins** (the clock is the urgent
  fact; the theme basis stays visible on the panel). The entry window's own flag,
  `entry_window_closing`, is not a bucket — it rides the Entry-by cell (below).
- **Warming** — the coverage bucket the wire forces: a conviction-only name sits in NEITHER
  `armed_members` NOR `watch_members` (watch is confirmation-only) yet its firing is live on the
  rail. Joined by ticker from `triggers_fired` (`TriggerRefOut` carries no security_id), so
  duplicate-ticker rows both light — visible over-inclusion, never a silent drop.
- **Watch** — `watch_members` ("moving, no conviction yet").
- **Quiet** — the remainder, incl. unresolved rows: every basket row lands somewhere
  (keep-it-visible), grayed rather than gone.

**The fold** is an explicit, reversible view filter — open by default, one click back, the count
stays on a closed header (a folded bucket never reads as dropped). Folded rows stay **mounted** with
`visibility: collapse`: a collapsed row still feeds the table's column-width algorithm, so folding
the bucket with the widest cells cannot re-flow the columns; the vertical reflow rides a View
Transition (instant where unsupported, off under reduced motion) instead of snapping.

Wire rank is preserved inside the armed/watch buckets (the call machinery already ranked them — the
FE never re-ranks the brain's output); Warming/Quiet keep the authored basket order.

**Three lenses over the same names.** A toggle above the table — `call state · business type · value
chain` — re-groups the *identical* basket; it is a view choice, never a filter (nothing hides, nothing
drops, every name is present in every lens). **Call state** is the default (the call is the product) —
the buckets above. **Business type** re-partitions by the scored business-type **super-sector** ("are
the utilities moving?"), with the fixed super order and its visible tails last (a mapped-`other` or
un-enriched name still has a row, #9). **Value chain** re-partitions by each member's **value-chain
segment** ("which part of the chain is moving?") and renders **only when the basket is decomposed into
links** (nothing to group by otherwise — honest loudness, #7); unlike the other two it shows **every
link-row**, so a name placed in N links appears under each of its N headers, and a null/empty-segment
row lands in the keep-visible `Unsegmented` tail. The two non-segment lenses **dedupe to one row per
`security_id`** (a multi-segment name's otherwise-identical duplicate rows collapse — losslessly, since
same `security_id` ⇒ same call bucket + scored join); the value-chain lens keeps them all (that's the
point). Whichever lens is active, each row keeps its **own call-state dot and exit-by** — the call never
disappears behind a view.

**The header's moving line — "is this group moving?"** Every group header, in every lens, ends with
`· N names · 7d median ±X.X%` (e.g. `Miner · 41 names · 7d median -6.0%`): the **median** 7d trailing
return over the group's **priced** rows, computed client-side off the same `ret_7d` the 7d column shows
(zero wire — nothing fetched, nothing re-derived). **Median, not mean**, so a single blowup can't drag a
group of −1%s into "moving"; **7d only** (one figure — no other window in v1); **N is the full group
size** (every row counts, priced or not, #9) while the median is over the priced subset (a thin-history
"—" is excluded, never counted as 0); **fewer than 3 priced rows reads "—"** (a "median" of one or two
names is not a group read, and a fabricated 0.0% is worse than none). It tints **muted** green up / red
down with no threshold accent — always-present context, not a badge (#7 / interaction principle #3) —
and the population ("5 priced of 6 names", or the why behind a "—") rides the hover. A display
aggregate, never a call input (#4): it changes no membership, no order, no call; a fold or a sort leaves
it exactly where it was. Re-computed per group per lens — the value-chain lens counts a multi-link name
once per link (each link-row is a real membership).

**The data flag — "is this real?" (split plan S3).** A number on a row can rest on data the platform knows is
wrong or cannot vouch for: a split-night hold (S5), a split that still needs review, a price tape that mixes two
scales (T4), a stopped feed (renamed / closed / a plain gap), a market cap on a pre-split or unreadable share
count. `GET /theses/{id}/data-flags?asof=` computes these **on read** (never stored, never on the call card,
never a call input — `domain/data_flags.py` + `pipeline/data_flags.py`, on the view's clock) and names, per flag,
the exact **cells** its dates reach (`cells_for`: a step a window straddles, a bar a window reads). Only those
cells change: **in doubt** keeps the number, grayed and dotted, the reason on hover; **known wrong** replaces it
with a muted "—" carrying the reason; every cell no flag names renders normally. The row gets a quiet small-flag
(⚑ — never the ◇, which already means "a basket / no ticker") **mark after the ticker** only when some flag
reaches a cell on it (WB#3 — a split two years back that no column reads marks nothing; the panel and Admin
still list it, and a name's flags list the reaching ones first). Flags apply only once this view's display and
scored reads have landed: the flag read is the faster one, and a mark beside a row of loading "—" (or another
as-of's placeholder numbers) read as "the flag blanked the row". The header's 7d median **leaves flagged 7d values out** and says so ("· 2 left out", the why on
hover). The sparkline gets a dashed **tick** at an applied split inside its window, nothing more. The
cells the server's flag vocabulary does not name (`Off 52w high`, `ADV 20d`, and each `Tape` chip) take their reach
from the **named cells whose bar window contains their own** (`cockpit/dataFlags.ts` `DERIVED_CELL_REACH`: 52w ←
1Y; ADV ← 30d and RVOL 20D; coil and RS lead ← 90d; a cross ← 1Y), so a flag that could reach them grays or blanks them too; the one named limit is a step in the oldest
~10 of the 52w window's ~262 bars, which `1Y`'s 252 do not reach — the long-run fix is to name the cells in
`domain/data_flags.py`, which moves the engine fingerprint, so it rides a deliberate backend change. No alert,
ever (#7). MEASURED on a read-only copy of prod (2026-10-22): 26 of 688 names carry a mark (3/90 AI Memory,
1/34 Digital Asset, 4/190 Modern Defense, 2/79 Nuclear, 8/155 Rainbow Rush, 9/159 Everything Exchange), and
all twelve live call cards were byte-identical with and without the flags read.

**Columns:** `Dot · Ticker · Name · Type · SMA · 1d · 7d · 30d · 90d · 1Y · Path · Off 52w high · Tape ·
RVOL 8D · RVOL 20D · Ins 30d · Ins 90d · Mkt cap · ADV 20d · Entry-by · Exit-by`.

**The table scrolls in its own box** (`.basket-scroll`), because twenty-one columns do not fit a laptop's
main column beside the 380px call rail (the widths MEASURED when this was built — 17 columns, 1025px of
min-content — predate Off 52w high, Tape and ADV 20d; the table is wider now and has not been re-measured) — before it, the overflow painted *over* the rail and those columns
were simply unreachable. Three things hold their place inside that box: the **status dot + ticker** stick
to the left, so scrolling the columns never costs a row its identity; the **column header** sticks to the
top; and a **bucket heading** ("Armed · act now · 7 names") sticks to the left of its full-width row. The
company name is capped with an ellipsis (the full name is on the hover and in the per-name panel) and the
call rail collapses from the top bar (`?rail=0`) when the table wants its width back.

**That machinery is scoped `.basket-scroll table.basket`, never bare `table.basket`.** `.basket` is a
shared table *skin* — the Scoreboard ledger and the Admin history tables wear it too — and the containment
rules are not skin: a table that takes its `max-content` width with no scroller to absorb it overflows the
*page*, and a `<thead>` told to stick with nothing scrolling it pins itself to the viewport. Both happened:
written unscoped, these rules escaped onto the Scoreboard, which overflowed by 498px at a 1091px viewport
until the scoping landed. A `.basket` table outside a scroller stays fluid and wraps, which is right for
one laid out in a flowing document.

- **Type** — the business-type **leaf** (Business-Type M1, the retired *archetype*'s replacement),
  colored by super-sector with a ◈ **royalty/streaming** overlay; an ETF sleeve reads "ETF sleeve" (a
  fund has no SIC), an un-enriched name a quiet "—" (never a guessed default).
- **SMA · 1d/7d/30d/90d/1Y · RVOL 8D · RVOL 20D · Ins 30d · Ins 90d** — the read-only **display-signal**
  columns (the engine doc is `docs/DISPLAY_SIGNALS.md`): quiet per-name tape context bridged onto the row
  by `security_id`, **structurally off the call path**. SMA is the posture glyph + % vs the slow line; the
  ladder is the trailing EOD returns (green up / red down; `1d` = last close vs the prior close, not a
  24h move; `1Y` = 252 trading bars); RVOL 8D is the as-of bar's volume vs its prior 8-bar mean (mirrors
  the breakout detector — the **call-matched** read) and RVOL 20D the same over 20 bars (the "vs its month"
  trader convention, **call-decoupled**), each warming a 'hot' accent past its **own** loud threshold; the
  insider cells read `{open-market buys}/{distinct buyers}` per trailing window (a ≥2-buyer **cluster**
  takes the leader-blue accent — breadth is the tell). Every display cell is a muted "—" when absent — the
  accent marks the exception (#7).
- **Path** — the close-path **sparkline** behind the ladder's endpoint numbers (the `price_path` member's
  fixed-slot series: the last 90 closes, one slot per trading bar, newest at the right). A **neutral**
  hairline — no accent, so the shape never blurs into the return green/red, the RVOL warm, or the insider
  blue — that **breaks on a gap, never interpolates**: a young name draws a genuinely shorter, right-aligned
  path (its thinness named on hover with the exact bars), and under two closes the cell is "—" (a point is
  not a path). One of the two display columns that are **not sortable** (the other is Tape) — a shape with no scalar to
  rank on.
- **Mkt cap** — bridged from the scoring read ("—" when un-scored).
- **Off 52w high** — how far the last close sits below the 52-week closing high (`range_52w`'s
  `pct_off_52w_high`; "-23.4%", 0.0% = at the high). Muted mono, no accent — the number itself states "at the
  high" — with the high and the bar it printed on in the hover. Sortable; desc puts the names nearest their high
  first. Sits behind Path, the shape it summarizes.
- **Tape** — the **exceptional** tape readings only, each a display member's own verdict: **coil** (`vcp`'s
  `coiling` headline — a tightening base above a rising 50d), **RS lead** (`relative_strength`'s `leading`
  headline — RS at a 13-week high vs SPY or IWM), and **golden / death** (the `sma_position` member's last 50d/200d
  cross, while it printed within `TAPE_CROSS_DAYS` = 30 calendar days of the as-of — a display dial in
  `cockpit/tapeMarks.ts`, never a call input). Small neutral outlined chips with only the direction glyph tinted
  (a coil is sideways — never a forecast, #4); the member's own statement and the cross's date ride the hover.
  A muted "—" on most rows: a mark is the exception, never the rule (#7). **Not sortable** (a set of marks is not
  a number to rank on). Each chip is flagged on its own reach — a coil / RS read through the 90d cell's window, a
  cross through 1Y's.
- **ADV 20d** — the 20-bar average **dollar** volume (close × volume; `volume_regime`'s `adv_usd_20d`; "$1.2M"),
  the liquidity read beside the size it qualifies. Muted, with **no threshold of its own**: where "thin" begins
  is the operator's call, not the table's. Sortable; desc is the most liquid first. A name short of 20 volume
  bars is an honest "—" with the why. Both cells are display members already rendered in the panel's
  Indicators section, surfaced here with no backend change.

**The two clocks are two columns — `Entry-by` then `Exit-by`, in the order they run out.** They were
one stacked cell until the basket-table containment pass; separating them makes each **independently
sortable**, and "which entry window closes soonest" is the sort the armed bucket actually wants.

- **Entry-by** — the member's entry-window (`arm_until`) clock, "‹date› · Nd". **Loud only when the
  call flags it** — `entry_window_closing` (Q14): at most two trading sessions left, decided by the
  assembler (`CALL_LOGIC.md` §6), and then the cell also reads "· entry window closing". That replaced the
  cell's own front-end "within a week" tint (Slice 2, #209), so "closing" has one definition. This is the
  **confirmation** clock, which governs how long the member
  STAYS armed, and it can fall a month before its `exit_by` (the live CRVO/MPLT confusion: "Armed ·
  Dec 8" yet de-armed Jul 19). Rendered for the armed family ONLY (`ENTRY_WINDOW_BUCKETS` — Armed /
  Hold clock closing / Theme-armed): a **Watch**-tier row carries `arm_until` on the wire too but must NOT light
  it up, because watch is confirmation without conviction, so its clock is a **decay** rather than an
  entry the system endorses. The gate is bucket-based, not presence-based (the load-bearing negative),
  and the SAME set gates the sort key — a sort must rank what the cell shows, never a hidden value.
- **Exit-by** — the member's **own** signal-validity horizon (amber "‹date› · hold clock closing" on a
  `lapsing` row — formerly "lapses ‹date›").

**Sorting — within-group, reversible, FE-only.** Clicking a column header ranks the basket by that
column **inside each group** — the call hierarchy stays put (the biggest mover inside Quiet surfaces
without leaving Quiet). The header cycles **desc → asc → off** (off restores the default order:
call-rank, then authored ordinal); a "—" cell is **ABSENT** (not a low/high value) and **sinks last in
both directions** (#9/#2 — the sort re-orders, never drops), and the insider columns sort
**buyers-first, then buys** (breadth-first, matching the cluster accent). The sort **persists across a
lens switch** (re-ranking within the new lens's groups). A flat leaderboard *across* groups is deferred,
not shipped.

**Keyboard stepping.** Every basket row is focusable (Enter / Space toggles its panel). **↑/↓** on a focused row
moves focus to the previous / next **visible** row — the rendered order, so the active lens and sort count, and
a **folded group is skipped** (its rows stay mounted, collapsed, but are not stops); it stops at the first and
last row, and a shift/ctrl/alt/meta-modified arrow is left to the browser. **With the panel open, the panel
follows focus**, so a basket is scanned a row at a time with the panel showing each name; Enter on the focused
row then closes it, and stepping with the panel closed only moves focus. The scoping is the row, not the page:
the arrows never hijack the panel's own scrolling. In the value-chain lens a name shown under several links
resolves to its **first** row (the click rule), so a later link-row takes focus and leaves the panel where it is
instead of jumping the highlight to a row far above.

The old Role/Detail columns are gone from the table; the authored text survives on the per-name panel.
No card yet (loading/error) → everything reads Quiet, honestly.

### The per-name panel

Clicking a row slides a **read-only, non-modal** panel over the rail (no scrim — the table stays
clickable, so switching names is one click on the next row — or **↑/↓** on the focused row, above; the table
never unmounts, and Esc / ✕ / re-clicking the row closes it; the rail dims, never hides). Top to bottom:

- **The call · this name** — its own verdict + grade chips + setup-strength bar (wire field `confidence`), or the honest degrade
  line ("conviction fired — awaiting confirmation" / "moving, no conviction yet" / "no live signals
  at this as-of"), plus its two clocks (a lapsing signal-validity clock reads amber, "hold clock closing ·
  Nd"; a flagged entry window adds "· entry window closing" to the entry row).
- **Triggers · this name's own** — `MemberCallOut.triggers` with grade + source links (#6); a
  Warming name's come from `triggers_fired` filtered by ticker. **Risk signals · this name** —
  `risk_signals`, ticker-filtered.
- **The operator record · this name** — the open **position** when it's attributed to this name
  (`Position.security_id`), and the **decision rows logged on this name** (`DecisionOut.security_id`)
  with voided rows grayed, never hidden. Display-only slices of the rail's log (the same query, no
  new fetch): thesis-level rows — and acting / passing / undo — stay on the rail, the one write
  surface. *(This block only renders on real data because `GET /theses/{id}` now threads the
  decisions-log-derived position (`effective_position`, the SAME source the call path uses) onto the
  thesis, so `Position.security_id` is populated for an attributed take — #216. Before that the read path
  built the position from the seed columns alone, which carry no name, and the block sat dead.)*
- **Identity** — the free wire fields ("—" where a field didn't resolve, never a guess): the
  business-type **Type** (leaf · super-sector, + the royalty/streaming overlay and a "your tag" mark
  when the operator has re-tagged it), segment, sector, exchange, category, origin (with the
  foreign-filer tell), mkt cap, the operator's **size weight — labeled "yours"** so it can never
  read as the signal conviction beside it (the two meanings never cross), and the authored
  role/detail. Then the **thesis-fit** prose with its authorship tag, and the **scoring snapshot**
  (the four meters — already fetched for the mkt-cap bridge).
- **Data · this name** (S3, only when there is something to say) — each data flag in full: its level, the
  reason, the evidence (the SEC filing link, the Massive.com row), since when, what clears it, and the cells it
  touches (or "no number on the row is affected"); then the company's **split rows** with their events —
  announced (the SEC filing), confirmed (by which sources), took effect (and the factor the prices before it
  carry), history corrected (only when Alpha Deck re-scaled the vendor's bars — the repair's provenance).
- **Indicators · this name** — the read-only display signals (`GET /theses/{id}/display-signals`,
  the engine doc is `docs/DISPLAY_SIGNALS.md`): every registered member rendered uniformly as quiet
  metric chips (SMA position + % distances, the trailing-return ladder, RVOL 8D / RVOL 20D, insider
  open-market buys, the 52w range, …), muted dated flip lines (price × 50d/200d crosses,
  golden/death), and a fine-print basis line (bars used · through-date — the show-the-work, #6).
  Honest gaps read "—" with the why ("n/a: 140/200 bars"); no data at all reads one muted line.
  Ambient tape context beside the call, never an input to it and never loud (#7). Fetched once at
  Cockpit level, joined by security_id — the SAME query the basket-table display-signal columns read.

Everything on the panel is a wire field this page already fetched; fact ratifications and the
business-type re-tag live on the TRIAGE rail, while actual sizing lives in the firm's external OMS / execution / risk stack. Omitted deliberately: description/website — draft-time
enrichment fields that are never promoted onto a `BasketMember`.

## The CallCard — the opinionated, auditable rail

State-classed (its accent follows the lifecycle), recomputed at `card.asof`. Top to bottom:

- **Verdict + expression** — the call-strength posture plus explanatory research context:
  `not_yet` through `starter_entry` / `core_entry` / `flip_only` / `managing` (§5/§8 of
  `CALL_LOGIC.md` decide). The legacy `expression` wire string is not sizing, instrument, or execution guidance.
- **The two keys, explicit** — Conviction and Confirmation, the arming model made visible. A turned-but-
  weak confirmation (momentum-only, flip-grade) renders **amber, not green** — the loudest element on the
  card must not overstate a starter.
- **Setup-strength bar** (wire field `confidence`) — Armed-only (§7): the backend nulls it for a not-yet
  card, so a Warming card never wears the Armed card's bar. It is an experimental relative indicator, not
  a success probability; forward Scoreboard outcomes must support calibration.
- **Triggers fired** — each with its ticker, grade, and a **clickable source link** (the Form 4, the 8-K —
  provenance is a feature, #6). **Still missing** names what hasn't fired; **Risk signals** ride with a
  warning glyph and no grade.
- **Counter-case** — deterministic: active risk signals + the authored kill criteria + the missing
  triggers.
- **Catalysts through exit-by** — the card's `catalyst_surface`: the dated binary events the assembler keeps
  inside the `exit_by` window (it keeps every dated entry on or before it, so a date already past rides along),
  listed soonest-first with a day count, a past one grayed and marked "passed". Ambient context beside the clocks
  it sits between; an empty surface draws no panel. (`safe_sleeve` is not rendered — the assembler never fills it.)
- **The two clocks** (sticky-on-confirmation, §6): `arm_until` — the **entry window** (confirmation's
  clock; on an Armed card it's an act-by deadline, "act within Nd", plus "· entry window closing" when the
  headline member's `entry_window_closing` is set; informational decay otherwise) — and
  `exit_by` — the **signal-validity horizon** (conviction's clock and post-fill monitoring/scoring yardstick,
  not a mandatory exit or sell-by date).
- **Decision capture — the action row** (`DecisionActions`): every button **logs**, nothing routes (#5).
  State-appropriate: Armed → the loud **"Act — log the fill"** (name select defaults to the platform's
  headline pick); Managing → the current **"Log exit"** control, which records a close the operator already
  decided rather than instructing one; a not-yet state shows **the gate** — friction copy plus
  "Override — log an early entry", and the take is logged with the platform's stance riding the row
  (*"the platform's verdict is not-yet — logging this take as an override"*). **"Pass (logged)"** is
  quiet and available at every state. The **decision log strip** lists recent rows (action · date ·
  size/price · reason · `platform: <stance>`); voided rows gray with a tag — visible, never vanished —
  and **undo** appends a `void` (reversibility: the inverse is an append, nothing is deleted). Note: the
  stance on a row is read from the **latest call-of-record at logging time**, which can lag the live
  card until the next cron tick — the record, not the recompute, is attribution's source. A logged take
  derives the position that flips the thesis to **Managing** on the next read; a close returns it to the
  signals-driven state (`CALL_LOGIC.md` §2, the Managing row).
- **The advisory line** — "order routing never; every act, pass, and override above is a logged decision
  (the Scoreboard's operator record)." Execution, sizing, and portfolio risk remain in the firm's OMS /
  execution / risk systems.

## The MemberMenu — the ranked basket (themes only)

Below the card, when a theme has more than one moving name: the **ranked armed rows** (the headline is
[0]; ranking = freshness band on the liveness runway first, grade within — `CALL_LOGIC.md`; `lapsing`
flags a member below the freshness dial — its runway reads "hold clock closing · ‹date›"; an
"entry window closing" chip marks `entry_window_closing`; `theme-armed` marks one armed via the theme-conviction fallback
rather than its own conviction) and the quiet **watch tier** ("moving, no conviction yet"). A single-name
thesis IS its headline card — the menu self-hides. The menu stays the ranked *summary*; the per-name
panel (above) is the deep view — the thesis-wide lists are no longer the only per-member read.

## The nightly rhythm

At 22:30 ET (the `cron` compose service — on by default, no profile; it fires for the INTENDED night with the
target as-of fixed at schedule time and waits in wall-clock slices, `FEED_LOOP.md`), for every **non-archived** thesis:
ingest (incremental Form 4 + EOD, with the **re-version pass** — a split re-base self-heals in one tick)
→ assemble today's call → **transition detection** (state/verdict vs the prior as-of's call-of-record —
the material-change line; churn is not a transition) → append the call-of-record only if it changed.
Transitions print in a loud `TRANSITIONS:` block **only when there are any**, and emit through the notify
seam (`backend/notify`: Slack when `SLACK_WEBHOOK_URL` is set, else a log line). **Push loudness follows the
Board's (#7, the Q13 push policy, `FEED_LOOP.md` §"The push policy"):** only a real arm is loud — a thesis
ENTERING Armed, or an armed thesis that genuinely strengthened (its grade rose or more names armed, nothing
fell). A weaker or mixed move while armed is a quiet, honestly worded push ("still armed, weaker: core → flip;
4 → 3 names"); names that arm inside an already-armed thesis ride ONE quiet digest per pass (first-time
arms before the marked re-arms; every name listed, never a count); a de-arm and
every pre-Armed move are recorded, never pushed — Incubating never nags. The next morning's Board is the
first thing the platform already checked without the operator.

## Known gaps and limits (this surface)

Documented, not hidden: each is either unmeasured, a deliberate simplification, or a choice the operator has not
yet confirmed. Nothing here changes a call, and none of it touches the edition engine (all frontend).

**The derived cells — `Off 52w high`, `ADV 20d`, the `Tape` chips (data flags)**
- **They are not named by the server's flag vocabulary** (`domain/data_flags.py`), so each borrows the flag state
  of the named cells whose bar window contains its own (`DERIVED_CELL_REACH`, above). The one under-warning gap: a
  split or step in the oldest ~10 of the 52w window's ~262 bars is not reached by `1Y`'s 252, so `Off 52w high`
  can show a normal-looking number that rests on mixed-scale bars. Rare (it needs the step in those ~10 sessions
  *and* the pre-step bars to hold the high), but silent when it happens.
- **It over-warns elsewhere, on purpose** (the safe direction): `ADV 20d` grays on a `30d` flag even when the step
  sits outside its 20 bars; a coil / RS lead grays on a `90d` flag though they read ~65 bars.
- **The containment is reasoned, not enforced.** It rests on the server's *current* window sizes (`BAR_WINDOWS`);
  `DERIVED_CELL_REACH` is hand-kept in the frontend and nothing fails if the server widens or narrows a window
  later (the server pins its own windows against the display members; no test crosses the language line).
- **The row's ⚑ hover lists only the server's cells** ("affects 1Y, SMA"); a grayed 52w / ADV / Tape cell carries
  its own reason on hover, but the mark does not name it.
- **On a ⚑ row, an absent Tape mark means "can't tell", not "quiet"** — the detectors ran over data the platform
  flags, so a missing chip is not evidence of a quiet tape.
- **Not measured on real data.** These cells are covered by fixture tests only (the cloud session has no
  database); how often a derived cell grays on prod — and the 26-of-688 mark rate above — is not re-measured for them.
- **The fix, not built:** name the three cells in `domain/data_flags.py` (moves the engine fingerprint, so it rides
  one deliberate backend change and its single trial run), or test the 52w window directly against the flags' own
  `dates` (the affected bar dates, present even on a flag that names no cell) — frontend-only, but not yet
  verified against a real flag.

**The `Tape` column**
- **The three marks and the 30-day cross recency (`TAPE_CROSS_DAYS`) were picked by the builder, not specified**:
  they are display dials awaiting operator confirmation. Only golden / death crosses are marks — the
  price-vs-50d / price-vs-200d flips stay in the panel.
- **It does not replace the panel.** The per-name panel's *Indicators* section is unchanged: it still lists every
  event each member emits (the last flip of each kind, however old). A 12-week-old golden cross is in the panel
  and is deliberately *not* a Tape chip.

**Keyboard stepping**
- Arrows act only while a basket **row** has focus: not from inside the panel, no Home / End / PageUp / PageDown,
  no j / k. The stepped-to row is scrolled clear of the sticky header by a fixed `scroll-margin-top: 40px` — an
  estimate of that header's height, not a measurement.

**The Decision Queue**
- **The strip clips, it does not wrap or scroll** (`.dq-items` is `overflow: hidden`, items are no-wrap): more armed
  items than fit are cut off at the right edge. That predates the act-by clock, but the clock lengthens every item,
  so fewer fit. Not measured at real widths. A hidden armed item is a visibility risk (#9's instinct applied to
  the queue); the fix — wrap or scroll — is a design call, not made here.
- A theme shows only the **headline** name's clock (the card's `arm_until`); the other armed names' clocks are on
  the rail's ranked menu. The queue's order is deliberately unchanged (it does not rank theses by deadline, #4).

**The CallCard's catalyst panel**
- It shows the assembler's `catalyst_surface`, which keeps only **dated** catalysts on or before `exit_by` with no
  lower bound: a fuzzy entry ("~Q3", a label with no date) never reaches the rail (it lives in the Cockpit's
  calendar), and a past-dated one keeps showing as "passed" until the operator removes it.

## What the Board never does (decisions, not gaps)

- **No execution, ever** (#5) — the gate withholds a go-signal and logs overrides; it never blocks and
  never trades. The operator logs their own fills; Alpha Deck hands off to the firm's OMS / execution /
  sizing / portfolio-risk systems and does not replace them.
- **No persisted display state** — columns, queue, and card all re-derive from facts on every read
  (Option B; a fact correction propagates automatically).
- **No thesis-vs-thesis ranking** (#4) — the board organizes by lifecycle and times entries; it has no
  opinion on which *idea* is better.
- **No silent loudness** — anything loud (the queue, `CALL READY`, a `TRANSITIONS:` block, a
  `RE-VERSIONED` line) marks an exception that wants the operator's eyes; the common case stays quiet.
