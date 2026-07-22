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
  tomorrow after ~8pm ET; the gate-2 catch). Scrubbing back replays the board as it *would have read* on
  that date — the no-lookahead rule applies to the UI too: a fill or fact dated after the as-of is
  invisible to it, by design.
- **The Decision Queue** — the one loud element: an armed-only strip so an actionable call can't be
  forgotten. A multi-name theme shows its **single top-ranked actionable name** with a quiet `+N` hint
  that a ranked menu sits behind it (anti-flooding — never every member). Empty state: *"Nothing armed.
  Nothing to do. ✓"* — a calm board is a working board.
- **"Calls that didn't compute" (keep-it-visible, #216)** — when a thesis's `/call` errors, it lands in a
  **visible strip at the bottom of the Board** ("⚠ Calls that didn't compute (N)") instead of silently
  vanishing from every column, and the Decision Queue **withholds its all-clear** ("Some calls didn't
  compute — see below."): a broken call might have been armed, so the calm empty state must not lie. A
  failed call is an exception the operator must see, never a quietly-dropped card — the same
  recall-is-sacred instinct as the basket's Quiet bucket.
- **The ThesisCard**: ticker (or a basket marker for a theme), name, narrative, and a state-appropriate
  footer — Armed leads with the **entry verdict** (STARTER/CORE — the categorical call-strength posture),
  with the conviction grade as secondary context ("core setup"); neither label sizes a trade. A bare CORE
  badge up front read as "go big"; this split exists to stop that over-commit misread. Non-armed cards show
  the two-key **readiness pips** (0–2). Armed
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

The Cockpit shares the Board's as-of; the call rail beside it recomputes live at that date.

### The grouped basket — per-name buckets

The basket table partitions by each member's **own** call state — the Board's column idiom applied
in-table, strongest → weakest, one **collapsible header** (chev · label · hint · count — the
Workbench's To Review heading idiom, the toggle bucket-colored) per **populated** bucket (an empty
bucket renders no header — loudness marks the exception) and a status dot per row:

- **Managing** — `verdict === "managing"`: the held name, when the open position carries its
  `security_id` (a take logged **on a name** — per-member Managing attribution, `CALL_LOGIC.md` §4).
  An unattributed position (a thesis-level take; the seed-era stored columns) emits no member
  verdict, so the group stays empty — **render-if-present** either way, never a guessed name.
- **Armed** / **Lapsing** / **Theme-armed** — the `armed_members` tier, split by its flags. When a
  member is lapsing *and* theme-armed, **Lapsing wins the bucket** (the clock is the urgent fact;
  the theme basis stays visible on the panel).
- **Warming** — the coverage bucket the wire forces: a conviction-only name sits in NEITHER
  `armed_members` NOR `watch_members` (watch is confirmation-only) yet its firing is live on the
  rail. Joined by ticker from `triggers_fired` (`TriggerRefOut` carries no security_id), so
  duplicate-ticker rows both light — visible over-inclusion, never a silent drop.
- **Watch** — `watch_members` ("moving, no conviction yet").
- **Quiet** — the remainder, incl. unresolved rows: every basket row lands somewhere
  (keep-it-visible), greyed rather than gone.

**The fold** is an explicit, reversible view filter — open by default, one click back, the count
stays on a closed header (a folded bucket never reads as dropped). Folded rows stay **mounted** with
`visibility: collapse`: a collapsed row still feeds the table's column-width algorithm, so folding
the bucket with the widest cells cannot re-flow the columns; the vertical reflow rides a View
Transition (instant where unsupported, off under reduced motion) instead of snapping.

Wire rank is preserved inside the armed/watch buckets (the call machinery already ranked them — the
FE never re-ranks the brain's output); Warming/Quiet keep the authored basket order. Columns:
`Dot · Ticker · Name · Archetype (only if decided — an unset one renders "—", never a default) ·
Mkt cap (bridged from the scoring read) · Exit-by` (the member's **own** signal-validity horizon; amber
"lapses ‹date›" on a Lapsing row). **Inside that exit-by cell, an armed-family row (Armed / Lapsing /
Theme-armed) also carries its entry-window (`arm_until`) clock** — "entry closes ‹date› · Nd", loud within a
week or once lapsed (Slice 2, #209). That is the **confirmation** clock, which actually governs how long the
member STAYS armed and can fall a month before the `exit_by` "lapses" date the cell leads with (the live
CRVO/MPLT confusion: "Armed · Dec 8" yet de-armed Jul 19). A **Watch**-tier row carries `arm_until` on the
wire too but must NOT light it up — the gate is bucket-based, not presence-based (the load-bearing negative).
The old Role/Detail columns are gone from the table; the
authored text survives on the per-name panel. No card yet (loading/error) → everything reads
Quiet, honestly.

### The per-name panel

Clicking a row slides a **read-only, non-modal** panel over the rail (no scrim — the table stays
clickable, so switching names is one click on the next row; the table never unmounts, and
Esc / ✕ / re-clicking the row closes it; the rail dims, never hides). Top to bottom:

- **The call · this name** — its own verdict + grade chips + setup-strength bar (wire field `confidence`), or the honest degrade
  line ("conviction fired — awaiting confirmation" / "moving, no conviction yet" / "no live signals
  at this as-of"), plus its two clocks (a lapsing signal-validity clock reads amber, "lapses in Nd").
- **Triggers · this name's own** — `MemberCallOut.triggers` with grade + source links (#6); a
  Warming name's come from `triggers_fired` filtered by ticker. **Risk signals · this name** —
  `risk_signals`, ticker-filtered.
- **The operator record · this name** — the open **position** when it's attributed to this name
  (`Position.security_id`), and the **decision rows logged on this name** (`DecisionOut.security_id`)
  with voided rows greyed, never hidden. Display-only slices of the rail's log (the same query, no
  new fetch): thesis-level rows — and acting / passing / undo — stay on the rail, the one write
  surface. *(This block only renders on real data because `GET /theses/{id}` now threads the
  decisions-log-derived position (`effective_position`, the SAME source the call path uses) onto the
  thesis, so `Position.security_id` is populated for an attributed take — #216. Before that the read path
  built the position from the seed columns alone, which carry no name, and the block sat dead.)*
- **Identity** — the free wire fields ("—" where a field didn't resolve, never a guess): archetype
  (+ the enrichment's quiet "figures suggest …" line when undecided, #10), segment, sector,
  exchange, category, mkt cap, the operator's **size weight — labeled "yours"** so it can never
  read as the signal conviction beside it (the two meanings never cross), and the authored
  role/detail. Then the **thesis-fit** prose with its authorship tag, and the **scoring snapshot**
  (the four meters — already fetched for the mkt-cap bridge).
- **Indicators · this name** — the read-only display signals (`GET /theses/{id}/display-signals`,
  the engine doc is `docs/DISPLAY_SIGNALS.md`): quiet metric chips (SMA position + % distances),
  muted dated flip lines (price × 50d/200d crosses, golden/death), and a fine-print basis line
  (bars used · through-date — the show-the-work, #6). Honest gaps read "—" with the why
  ("n/a: 140/200 bars"); no data at all reads one muted line. Ambient tape context beside the call,
  never an input to it and never loud (#7). Fetched once at Cockpit level, joined by security_id.

Everything on the panel is a wire field this page already fetched; fact/archetype decisions live in the
Workbench, while actual sizing lives in the firm's external OMS / execution / risk stack. Omitted deliberately: description/website — draft-time
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
- **The two clocks** (sticky-on-confirmation, §6): `arm_until` — the **entry window** (confirmation's
  clock; on an Armed card it's an act-by deadline, "act within Nd"; informational decay otherwise) — and
  `exit_by` — the **signal-validity horizon** (conviction's clock and post-fill monitoring/scoring yardstick,
  not a mandatory exit or sell-by date).
- **Decision capture — the action row** (`DecisionActions`): every button **logs**, nothing routes (#5).
  State-appropriate: Armed → the loud **"Act — log the fill"** (name select defaults to the platform's
  headline pick); Managing → the current **"Log exit"** control, which records a close the operator already
  decided rather than instructing one; a not-yet state shows **the gate** — friction copy plus
  "Override — log an early entry", and the take is logged with the platform's stance riding the row
  (*"the platform's verdict is not-yet — logging this take as an override"*). **"Pass (logged)"** is
  quiet and available at every state. The **decision log strip** lists recent rows (action · date ·
  size/price · reason · `platform: <stance>`); voided rows grey with a tag — visible, never vanished —
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
flags a member below the freshness dial; `theme-armed` marks one armed via the theme-conviction fallback
rather than its own conviction) and the quiet **watch tier** ("moving, no conviction yet"). A single-name
thesis IS its headline card — the menu self-hides. The menu stays the ranked *summary*; the per-name
panel (above) is the deep view — the thesis-wide lists are no longer the only per-member read.

## The nightly rhythm

At 22:30 ET (the `cron` compose profile, `FEED_LOOP.md`), for every **non-archived** thesis:
ingest (incremental Form 4 + EOD, with the **re-version pass** — a split re-base self-heals in one tick)
→ assemble today's call → **transition detection** (state/verdict vs the prior as-of's call-of-record —
the material-change line; churn is not a transition) → append the call-of-record only if it changed.
Transitions print in a loud `TRANSITIONS:` block **only when there are any**, and emit through the notify
seam (`backend/notify` — delivery deferred; a channel is one adapter). The next morning's Board is the
first thing the platform already checked without the operator.

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
