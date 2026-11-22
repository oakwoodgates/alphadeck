# Board, Cockpit and call card

The Board is the home dashboard: every card in the user's workspace in its lifecycle column, under the
Decision Queue. A card opens the Cockpit: the grouped basket, a per-name panel, the catalyst calendar, kill
criteria and the call rail. Read this before you change `frontend/src/board/`, `frontend/src/cockpit/` or the
call card. How a call is made is [CALL_LOGIC](CALL_LOGIC.md); the nightly pass is [FEED_LOOP](FEED_LOOP.md).
Every user, the steward included, sees only their own workspace's cards here; no control is steward-only.

The Board is the `board` dashboard (`frontend/src/dashboards/board/entry.ts`); `HOME_DASHBOARD` in
`frontend/src/dashboards/registry.ts` makes it `/`, and the docs checker's parity check maps the `board` slug to
this file ([DASHBOARDS](DASHBOARDS.md), "The Board is the home dashboard").

## Code

| Piece | Module |
|---|---|
| Board, Decision Queue, card controls | `frontend/src/board/` (`Board.tsx`, `ThesisCard.tsx`, `useRowCap.ts`) |
| Cockpit page and basket table | `frontend/src/cockpit/Cockpit.tsx` |
| Buckets, lenses, the Entry-by gate | `cockpit/buckets.ts` |
| Sorting; moving line; data flags; tape marks | `cockpit/sortBasket.ts`; `groupAggregate.ts`; `dataFlags.ts`; `tapeMarks.ts` |
| Per-name panel | `cockpit/NamePanel.tsx` |
| Calendar editors; the counting switch | `cockpit/SpineListEditors.tsx`; `cockpit/CatalystCounting.tsx` |
| Call card, member menu, decision capture | `frontend/src/components/CallCard.tsx`, `MemberMenu.tsx`, `DecisionActions.tsx` |
| Routes | `backend/app/routers/theses.py`, `clock.py`, `data_flags.py`, `fund_radar.py`, `account.py` |
| Per-workspace notifications | `backend/notify/__init__.py` (`notifier_for_workspace`); `backend/pipeline/daily.py` |

## How it works

### The Board

- **Four columns, the lifecycle:** Incubating, Warming, Armed, Managing. A card sits in the column of its
  live call's `state` at the selected as-of (`GET /theses/{id}/call?asof=`). Placement is never stored, so a
  new as-of re-derives every card, and cards move back when triggers age out or a position closes
  ([CALL_LOGIC](CALL_LOGIC.md) section 2). Managing means the user logged a fill and the platform monitors
  the thesis; it never manages the position.
- **No ranking.** Columns and the queue keep list order and never rank theses (invariant 4).
- **The as-of defaults to the current night**, the night the pass last recorded (`GET /clock`,
  `domain/market_time.current_night`), never the browser's date. It is read live, with the user's decisions
  up to the action day, so a fill logged this morning shows at once (`serve_decisions_asof`).
- **A past as-of is a labeled recompute:** today's rules on what was public by the end of that night
  (invariant 1, `serve_view`); a fill logged later stays invisible. One quiet line on the Board and the call
  card says it is not the recorded call. The roster is point-in-time only from 2026-09-15
  ([INVARIANTS](INVARIANTS.md), Known gaps). What the platform said that night is the Scoreboard's record.
- **The card.** An Armed card leads with its verdict, colored by the entry grade, then "core setup" or
  "flip setup" (the grade rates the call's strength, never the thesis) and a `CALL READY` flag. A Managing
  card shows its verdict. Others show two readiness pips, conviction then confirmation, so a
  confirmation-only card never reads as "conviction fired".
- **During a scrub** a card keeps its previous call dimmed and dated, and is barred from the queue.

### The Decision Queue

The one loud element: a strip of the Armed cards, so an actionable call is not forgotten.

- A theme shows only its headline name (`armed_members[0]`, ranked by `calls/assembler.rank_members`) and a
  quiet `+N`; a click opens the Cockpit on that name (`?name=`).
- Each item states its act-by clock, the days from the action day to `arm_until` (on a past view, from the
  as-of, with the date). It turns warm and says "entry window closing" only when the headline member's
  `entry_window_closing` flag is set; the flag is the call's, never re-derived. The clock never reorders.
- **No armed item is clipped.** The strip wraps; past `DQ_ROWS` rows (counted off the laid-out rows,
  `useRowCap`) the rest wait behind an always-visible "+N more" that opens every item, and "show fewer"
  folds it back (WB#1). The fold goes by position, never a ranking.
- **The all-clear is withheld** while any call is computing, and when any call failed, because a failed call
  might have been armed. A failed card shows in a "Calls that didn't compute" strip at the bottom of the
  Board and opens the Cockpit, which shows the error.

### Card controls

Quiet hover controls beside each card, reversible in one click (WB#1):

- **Archive (✕).** The card moves to the collapsed "Archived (N)" section: restorable, out of the default
  lists and the nightly pass, its call not computed. Its calls log, decisions and body stay, and a promote
  can neither archive nor restore it. A restore past the workspace caps answers 409 with a sentence
  ([AUTH](AUTH.md), "Workspace caps").
- **Meta index.** Marks a broad card for watching a sector. Its names never count toward the call: it stays
  Incubating and never warms, arms, notifies or enters the queue ([CALL_LOGIC](CALL_LOGIC.md) section 1). A
  quiet "meta index" tag shows on the card and the Cockpit header.

### The Cockpit

The Cockpit opens at the Board's as-of. Top to bottom: the narrative; the rotation strip (theme breadth and
sector relative strength); the basket table with its exports (JSON, TradingView watchlist); the fund launches
line; evidence; the catalyst calendar; kill criteria. The call rail sits beside them and collapses from the top
bar (`?rail=0`). The call, display signals, scored read and data flags all read the same as-of on the same clock
(`serve_view`).

- **Catalyst calendar.** The card's events in date order, fuzzy dates ("~Q3") last; a past date grays and
  stays. The sole writer is `PUT /theses/{id}/catalysts`, which saves the authoring order; a promote never
  touches the list.
- **Counts toward the call.** Each saved row has a quiet "Counts toward the call…" control
  (`CatalystCounting.tsx`), the one place a user makes a catalyst count. The form takes voting names (silent
  link names are disabled), a grade (Core needs a name; a whole-card entry is Flip), a citation (a link or
  an SEC accession number) and an optional "relevant until" date; Stop counting is its inverse. It counts
  from tonight, never from the calendar date. A meta index card shows a note instead. A calendar save that
  drops a counting row is refused (409). On the rail such a trigger carries a "your entry" chip, and a
  platform catalyst live on the same member shows as "also live, not counted". The rules:
  [CALL_LOGIC](CALL_LOGIC.md) section 3c.
- **Kill criteria** (sole writer `PUT /theses/{id}/kill-criteria`) feed the recorded `counter_case` text.
- **Fund launches line** (`GET /theses/{id}/fund-launches`): one collapsed line of fund filings that fit the
  card, absent when there are none. Its "+ add to ETFs" files a fund into the ETFs link, which never counts
  toward the call ([FUND_RADAR](FUND_RADAR.md)).

### The grouped basket

The default lens groups names by each member's own call, strongest first, under collapsible headers; an empty
group has no header (WB#3). Precedence in `buckets.groupBasket`, first match wins:

| Bucket | Test |
|---|---|
| Managing | the member's call has verdict `managing` (the held name of a position logged on a name) |
| Armed, Hold clock closing, Theme-armed | in `armed_members`; `lapsing` wins over `theme_armed` |
| Watch | in `watch_members` (confirmation only), tested before the trigger join |
| Warming | its ticker is in `triggers_fired` (trigger rows carry no `security_id`, so duplicate tickers both light) |
| Context, ETFs, Bench | the name sits in that silent link |
| Quiet | everything else, unresolved rows included |

- **Why the silent links come last:** the roster is today's but the card can be a past recompute, so a name
  that voted that night and sits in Context today keeps its call bucket and its call. A live call never lists
  a silent name, and a held name stays Managing even in a silent link.
- **Order and fold.** Armed and Watch keep the call's ranking; other groups keep the authored order. A fold
  is a view filter: the count stays on the closed header and folded rows stay mounted, so columns never
  reflow.
- **Lenses:** call state, business type (super-sector) and value chain (shown only when the basket has
  links). Every lens shows every name, and each row keeps its call-state dot and clocks. The value-chain lens
  shows a name once per link; the others once per `security_id`. Context, ETFs and Bench are the last groups
  except in the business-type lens, where a silent name carries a link tag. A context role
  ([WORKBENCH](WORKBENCH.md#context-roles)) shows as a quiet tag only when set.
- **The moving line.** Each header ends with the group size and the median 7-day return of its priced rows
  (`groupAggregate.groupMoving`): a median so one blowup cannot move it, "—" below a minimum of priced rows,
  flagged values left out and counted. It moves no row, bucket or call.
- **Columns** are defined in `Cockpit.tsx`: business type and market cap from `GET /theses/{id}/scored`, the
  tape columns from the display-signals read ([DISPLAY_SIGNALS](DISPLAY_SIGNALS.md)). `Last close` is an
  end-of-day close, never a live quote, dated only when it lags the basket's newest bar, its member calls it
  stale or the feed is flagged stopped. `Tape` shows only exceptional readings (a coiling base, relative
  strength at a 13-week high, a cross within `TAPE_CROSS_DAYS`).
- **Two clocks, two columns.** `Entry-by` is the entry window (`arm_until`), which governs how long a member
  stays armed and can end weeks before `Exit-by`. It renders only for the armed buckets
  (`ENTRY_WINDOW_BUCKETS`), since a Watch row's `arm_until` is a decaying confirmation, not an endorsed
  entry, and is loud only when `entry_window_closing` is set. `Exit-by` is the signal-validity horizon
  (`exit_by`), marked "hold clock closing" when `lapsing`. Neither is a sell date.
- **Sorting** works inside each group (desc, asc, off). A "—" cell sinks last both ways, and a sort key
  reads the same gate as its cell, so it never ranks a hidden value.
- **Containment.** The table scrolls in its own box with sticky identity columns and header; the rules are
  scoped `.basket-scroll table.basket` because the Scoreboard and Admin tables share the `.basket` skin.

### Data flags

`GET /theses/{id}/data-flags?asof=` (and `GET /data-flags` for the Board) computes on read which cells rest on
data the platform cannot vouch for, such as a split under review, a mixed-scale tape or a stopped feed
(`domain/data_flags.py`). Only the named cells change: "in doubt" grays the number, "known wrong" shows "—",
each with the reason on hover. A row gets a quiet ⚑ only when a flag reaches one of its cells (WB#3), on the
Board too. Flags apply only once the view's display and scored reads have landed. Unnamed cells borrow the
state of named cells whose windows contain theirs (`DERIVED_CELL_REACH`). A flag never alerts.

### The per-name panel

A row click opens a read-only, non-modal panel over the rail; the table stays live. It shows this name's call
(verdict, grades, setup strength, clocks), the position and decisions logged on it, its triggers and risk
signals with sources, identity (including "Size weight (yours)"), data-health notes and full data flags, thesis
fit with its authorship tag, the scoring snapshot with quiet lines on what a blank meter waits for
([PLATFORM_VALUES](PLATFORM_VALUES.md)), and every display signal. Acting and undo stay on the rail.

### The call card

The rail renders the live call at `card.asof` (`CallCard.tsx`):

- **Verdict and expression;** the expression is research context, never a size or an order (invariant 5).
- **The two keys.** A turned but flip-grade confirmation is amber, not green, so a starter never reads strong.
- **Setup strength** (wire field `confidence`), drawn only when the call carries it, which the assembler
  does only for an Armed card. An experimental relative read, not a probability.
- **Triggers fired**, newest first, each with grade, fire date and source link (invariant 6); **Still
  missing**; **Risk signals**.
- **Counter-case:** a count of the risk and still-missing rows. The rail never shows the `counter_case`
  text, which restates them ([CALL_LOGIC](CALL_LOGIC.md) section 8).
- **Catalysts through exit-by:** the call's `catalyst_surface`, every dated entry on or before `exit_by`;
  a past one reads "passed".
- **Two clocks.** On an Armed card the entry window (`arm_until`) is an act-by count from the action day,
  with "entry window closing" when the headline's flag is set; on other cards it is a decaying confirmation
  clock. The hold clock (`exit_by`) is the signal-validity horizon and the yardstick after a fill.

When a card has more than one armed name or any watch name, the **member menu** below lists the ranked armed
members, headline first, each with its verdict, a "theme" tag when armed on the theme fallback, "entry window
closing" when flagged and its hold clock, then the watch tier.

### Decision capture

The action row (`DecisionActions.tsx`) logs decisions the user made elsewhere; nothing routes or blocks
(invariant 5).

- Armed: "Act — log the fill" (the name defaults to the headline) and "Pass (logged)". Managing: "Log exit"
  and "Note a pass". Other states show the gate: friction copy and "Override — log an early entry".
- `POST /theses/{id}/decisions` appends take, pass, close or void. One open position per card; a future
  date is refused. Undo appends a void; voided rows gray and stay.
- A row's stance comes from the latest call of record at logging time, which can lag the live card until
  the next pass: the record, not the recompute, is attribution's source ([SCOREBOARD](SCOREBOARD.md)).
- A take moves the card to Managing on the next read; a take on a name makes it the Managing member.

### Notifications

The nightly pass (machine) records a call only when it changed and pushes a transition to the card's workspace
([FEED_LOOP](FEED_LOOP.md), step 3). The destination is per workspace (`notifier_for_workspace`):

1. every enabled user's own Slack webhook, set on the Settings page, each reached once per push;
2. else the deployment's `SLACK_WEBHOOK_URL`, only when the workspace holds an enabled super admin;
3. else the log only.

A plain user's workspace never reaches the deployment's channel. How loud a push is follows `notify/policy.py`
(invariant 7): entering Armed or an armed upgrade is loud, a weaker armed move quiet, and a de-arm or any move
short of Armed is recorded, never pushed.

## Contracts

- **Display only.** Buckets, lenses, the moving line, sorting, data flags, display signals and the fund
  launches line read the call; the call never reads them (invariant 4). No display state is stored.
- **No silent loudness.** Anything loud (the queue, `CALL READY`, a warm clock, the failed-calls strip)
  marks an exception that needs the user; the common case stays quiet (invariant 7, WB#3).
- **Keep it visible.** Every basket row lands in a bucket; failed calls, archived cards, voided decisions
  and past catalysts stay on screen (WB#2).
- **Sole writers.** Archive, meta index, the catalyst list, its counting entries and the kill criteria each
  have one route; the Workbench's promote never writes them.

## Who can do what

Every router here is mounted behind `require_user` (`app/main.py`; `tests/app/test_auth_gate.py`). Thesis
routes load the card from the caller's workspace (`app.deps.get_thesis_or_404`), so another workspace's card
is a 404 (`tests/app/test_workspace_isolation.py::test_every_thesis_id_operation_answers_404_to_b`); the
steward sees only their own (`::test_the_super_admin_sees_no_other_workspace_theses`).

| Route, control or job | Who | Enforced by; test |
|---|---|---|
| Reads: `GET /theses`, `/theses/{id}`, `/call`, `/display-signals`, `/scored`, `/data-flags`, `/fund-launches`, `/clock` | user, own workspace | `get_workspace`, `get_thesis_or_404` |
| `POST /theses/{id}/archive`, `/unarchive` | user; restore within the workspace caps | `app/workspace_caps.py`; `tests/app/test_archive_api.py`, `test_workspace_caps.py` |
| `PUT /theses/{id}/meta-index` | user | `tests/app/test_meta_index_api.py` |
| `PUT /theses/{id}/catalysts` | user | wipe guard; `tests/app/test_catalyst_wipe_guard.py` |
| `PUT /theses/{id}/catalysts/{entry_id}/counting` | user; never a model | `tests/app/test_thesis_catalyst_counting.py`, `tests/repositories/test_thesis_catalyst_writers.py` |
| `PUT /theses/{id}/kill-criteria` | user | `tests/app/test_thesis_lists_api.py` |
| `POST`, `GET /theses/{id}/decisions` | user | `tests/app/test_decisions_api.py` |
| "+ add to ETFs": `POST /radar/funds/attach`, `/detach` | user, for a fund already in the shared company list (else 404), one `price_pull` allowance unit, within the company cap; the steward may add a new fund to the list | `user.is_super_admin` branch; `tests/app/test_shared_master_writes.py` |
| Own Slack webhook: `/account/notifications/slack-webhook` | user, own only | `tests/app/test_account.py` |
| Nightly call and push | machine (cron) | `pipeline/daily.py`; `tests/notify/test_fanout.py` |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `SLACK_WEBHOOK_URL` (the deployment's channel) | `domain/settings.py` | yes, backend and cron |
| `ALPHADECK_CRON_AT` (the pass time, which sets the current night) | `domain/settings.py` (`cron_run_at`) | yes |
| `headline_lapsing_soon_days`, `entry_window_closing_sessions` | `domain/config.py` (`CallConfig`) | no, code dials |
| `DQ_ROWS`, `TAPE_CROSS_DAYS`, `DERIVED_CELL_REACH` | `board/Board.tsx`, `cockpit/tapeMarks.ts`, `cockpit/dataFlags.ts` | no, frontend constants |

## Known limits

- `DERIVED_CELL_REACH` is kept by hand; no test checks it against the server's `BAR_WINDOWS`. A step in the
  oldest bars of the 52-week window lies outside `1Y`, so `Off 52w high` can read normal on mixed-scale bars;
  elsewhere it over-warns. On a flagged row a missing Tape mark means "cannot tell", not "quiet".
- The Warming bucket also holds a member whose keys are live and co-located but whose arm a risk signal
  withholds or a breakdown undid, under the hint "conviction in · awaiting confirmation".
- Readiness pips read the card-level keys, computed across the whole basket when nothing is armed, so a
  Warming card can show both pips lit.
- `Last close` dates a row against the basket's newest bar, so a stall of the whole feed shows no dates. A
  flag-blanked cell still sorts by its number.
- Nothing archives a past catalyst; it stays on the calendar and the rail until the user removes it.
- The queue and the clocks count calendar days; `entry_window_closing` counts sessions. The queue's fold
  resets on reload.
- "your tag" on a business type marks the steward's re-tag of the shared company list, not the user's.
  "Size weight (yours)" is stored, but no control sets it.

## Related

[CALL_LOGIC](CALL_LOGIC.md) (the call), [INVARIANTS](INVARIANTS.md), [FEED_LOOP](FEED_LOOP.md) (the nightly
pass), [DISPLAY_SIGNALS](DISPLAY_SIGNALS.md), [DASHBOARDS](DASHBOARDS.md), [SCOREBOARD](SCOREBOARD.md) (the
record), [WORKBENCH](WORKBENCH.md) (the Workbench and the basket), [AUTH](AUTH.md) (Settings page, allowances, caps).
