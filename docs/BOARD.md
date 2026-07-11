# BOARD.md — the MONITOR surface (Board · Cockpit · CallCard)

> Repo path: `docs/BOARD.md`. **How the operator-facing MONITOR surface works** — the Board's lifecycle
> columns, the Cockpit's deep view, the CallCard rail, decision capture, and the nightly rhythm that feeds
> them. This is the *surface* doc; the *brain* is `CALL_LOGIC.md` (how signals become a call), the *rhythm*
> is `FEED_LOOP.md` (the ingest + the call-of-record cron), and the *frame* is `STAGE_MODEL.md` (MONITOR is
> the back half: the chosen basket parks as a thesis → incubate → warm → arm → manage).
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
  → Armed (act now) → Managing (in position)`. The lifecycle is a loop, not a ratchet — cards move back
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
- **The ThesisCard**: ticker (or a basket marker for a theme), name, narrative, and a state-appropriate
  footer — Armed leads with the **entry verdict** (STARTER/CORE — *what to do*), with the conviction
  grade as secondary context ("core thesis"); a bare CORE badge up front read as "go big", the over-commit
  misread this split exists to stop. Non-armed cards show the two-key **readiness pips** (0–2). Armed
  cards carry the `CALL READY` flag.
- **Archive, never delete** (board hygiene): hover a card → a quiet **✕** appears (a *sibling* of the
  card, top-right). Archiving drops the thesis into the collapsed **Archived (N)** section at the bottom —
  visible, restorable in one click, out of the default lists and the nightly cron's walk, and **its call
  is not computed** (cost stays the operator's). The spine, calls log, and decision log all stay; a
  promote can neither archive nor resurrect one (the structural guard on `archived_at`).

## The Cockpit — one thesis, deep

Selecting a card opens the Cockpit: the narrative (the operator's words, preserved), the basket table
(archetype shown **only if decided** — an unset one renders "—", never a default; computed market cap
bridged from the scoring read), evidence, and two **operator-authored lists** that render *even at zero*
(an empty section used to vanish, which made "there's no way to author one" invisible):

- **Catalyst calendar** — the thesis-level *surface* events (label · kind · date or a fuzzy "~Q3").
  Dated entries within the hold horizon ride the CallCard's catalyst surface; entries ≤ 21 days out
  highlight as `soon`. Edited in place (`✎ edit` / `+ add catalysts`); saves through the sole-writer
  `PUT /theses/{id}/catalysts` — a promote can never wipe the list. **Distinct from the per-name
  conviction FACTS** (the Key-1 arming inputs), which are authored on the Workbench rail with a required
  citation (`WORKBENCH_EXTRACTION.md`).
- **Kill criteria** — the documented "what would kill this thesis." Consumed by the deterministic
  counter-case on the CallCard — an authored thesis stops reading "no documented counter-case."

The Cockpit shares the Board's as-of; the call rail beside it recomputes live at that date.

## The CallCard — the opinionated, auditable rail

State-classed (its accent follows the lifecycle), recomputed at `card.asof`. Top to bottom:

- **Verdict + expression** — the call in words: `not_yet` with "hold for a volume-confirmed breakout"
  through `starter_entry` / `core_entry` / `flip_only` / `managing` (§5/§8 of `CALL_LOGIC.md` decide).
- **The two keys, explicit** — Conviction and Confirmation, the arming model made visible. A turned-but-
  weak confirmation (momentum-only, flip-grade) renders **amber, not green** — the loudest element on the
  card must not overstate a starter.
- **Confidence bar** — Armed-only (§7): the backend nulls it for a not-yet card, so a Warming card never
  wears the Armed card's bar.
- **Triggers fired** — each with its ticker, grade, and a **clickable source link** (the Form 4, the 8-K —
  provenance is a feature, #6). **Still missing** names what hasn't fired; **Risk signals** ride with a
  warning glyph and no grade.
- **Counter-case** — deterministic: active risk signals + the authored kill criteria + the missing
  triggers.
- **The two clocks** (sticky-on-confirmation, §6): `arm_until` — the **entry window** (confirmation's
  clock; on an Armed card it's an act-by deadline, "act within Nd"; informational decay otherwise) — and
  `exit_by` — the **hold horizon** (conviction's clock, the one that governs once a fill is logged).
- **Decision capture — the action row** (`DecisionActions`): every button **logs**, nothing routes (#5).
  State-appropriate: Armed → the loud **"Act — log the fill"** (name select defaults to the platform's
  headline pick); Managing → **"Log exit"**; a not-yet state shows **the gate** — friction copy plus
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
  (the Scoreboard's operator record)."

## The MemberMenu — the ranked basket (themes only)

Below the card, when a theme has more than one moving name: the **ranked armed rows** (the headline is
[0]; ranking = freshness band on the liveness runway first, grade within — `CALL_LOGIC.md`; `lapsing`
flags a member below the freshness dial; `theme-armed` marks one armed via the theme-conviction fallback
rather than its own conviction) and the quiet **watch tier** ("moving, no conviction yet"). A single-name
thesis IS its headline card — the menu self-hides.

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
  never trades. The operator logs their own fills.
- **No persisted display state** — columns, queue, and card all re-derive from facts on every read
  (Option B; a fact correction propagates automatically).
- **No thesis-vs-thesis ranking** (#4) — the board organizes by lifecycle and times entries; it has no
  opinion on which *idea* is better.
- **No silent loudness** — anything loud (the queue, `CALL READY`, a `TRANSITIONS:` block, a
  `RE-VERSIONED` line) marks an exception that wants the operator's eyes; the common case stays quiet.
