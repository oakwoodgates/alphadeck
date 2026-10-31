# Daily Digest

> Slug `daily-digest` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 280`.
> The first page of the MANAGING set (operator go 2026-10-30, "Yes, write it") and the "Daily digest" of the first
> brainstorm: the morning page. The choices the build makes that this plan does not spell out go under "Choices to
> confirm" for the operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`; invariant #7 (inverse
> loudness) decides what on this page may be loud.

**Label:** Daily Digest. **Description:** "The morning page: what last night's pass changed, the positions, what is due
this week, the insider buys just filed, the tape on the armed names and what is forming, each line opening its full
page."

Every section is a page that already exists, read by THAT page's own rules and cut to the lines that are new or due
this morning: the digest adds no reading rule of its own, so a number here is the same number on the full page. It is
a page, not a message: it never notifies (the notify seam is the cron's; rule 1), never calls the LLM (no prose
summary: every line is a recorded label or a computed figure), never writes, never ranks a card by promise (#4) and
never aggregates what happened after an event (rule 6: that is the Track Record's job, by cap tier).

## The morning

- **The as-of is the morning.** The page reads the record in effect at the as-of D: **N** = the newest recorded night
  across the counted cards with `asof <= D` (the What Changed read of the `calls` log: one row per card, the greatest
  `(asof, seq)` with `asof <= D`; the log appends only on change, so a card with no row on N is carried from its last
  row), **P** = the newest recorded night before N across the same cards. Before the 22:30 pass, D's own N is the night
  before; after it, tonight's. The header says which: "the night of N, against P". A Monday morning reads Friday's
  night against Thursday's, and says so.
- **The cards** are the counted cards at the as-of (`baskets.cards_as_of`: archived cards out), each read as its
  recorded call in effect at D. A card with no recorded call is counted ("no call yet"), never dropped (rule 5). A
  card whose row on N carries `ingest_fresh = false` (the `calls` column) is marked on every line it has ("ingest not
  fresh"), as the Board marks it; a reconstructed row is marked "R" (the Idea Timeline's rule).
- **Names.** The tape and insider sections count voting names only: Context and Bench names never count here (the Hot
  Board's and Insider Buying's rule). The held name of a position counts wherever the position names it.

## The sections, in order

Each section is one block: a header naming the full page it comes from (a link carrying `?asof=`; a "now" page ignores
it), the section's count, the lines, and a quiet "nothing" line when there is nothing ("Nothing changed last night."
"No position logged." "Nothing due this week."). No section is ever hidden for being empty.

### 1. Last night: What Changed, P → N

The What Changed diff, by its own pure function (`dashboards/what_changed.py::what_changed`) over the two in-effect reads
(`now` at N, `baseline` at P, the live cards' names and the master's tickers; the digest's router copies What Changed's
three SELECTs, which are plain reads of the `calls` log scoped to the counted ids). The lines it carries are What
Changed's own: a state move (both directions), a key on or off (the key's recorded label), a name newly armed or no
longer armed, a name new on watch, a trigger newly fired, gone or re-dated (identity = kind, security, event date; never
the label), a new risk signal, the clocks on the N card (an entry window closing within 3 calendar days of N, an exit-by
reached in (P, N]), and a new card. A trigger line carries its first source link (`dashboards/filings.py::source_url`,
the Evidence Ledger's rule).

**Grouped by card, the card's state move as the headline.** Order: cards whose change touches Armed first (entered or
left Armed, a name newly armed or no longer armed, a window closing, an exit-by reached), then cards whose change is
Warming, then Incubating last and gray (invariant #7: Incubating never nags). Inside a card, What Changed's own order.
Each card opens the Cockpit (`onSelectThesis(thesisId, nameKey)`, the name's key on a name or trigger line).

### 2. Positions: the Managing cards

One line per counted card whose recorded call in effect at D is **Managing**, with the operator's effective position as
the thesis read at the as-of carries it (`Thesis.position`: the decision log's take rows, the seed-era columns as the
fallback; the roster and the position stay as of the view's instant, `serve_view`). The line: the card, the held name
(`position.security_id` → ticker; "unattributed" when the position names no security), the fill date (`opened_on`) and
the logged price when there is one, the name's **as-traded return since the fill** (from the logged `entry_price`, else
from the first close on or after `opened_on`, stated which) to the last close on or before D, with IWM over the same
dates, the **last session's return** (the last close over the one before), the **hold clock** (`exit_by`, as the
sessions left by the Hot Board's `sessions_left` rule, or "passed N sessions ago"), the card's **risk signals** (count;
one marked "new last night" when section 1 carries it), and the card's **kill criteria** as a count with the texts in
the hover (they are the operator's own words, shown verbatim, never judged). Shares × price shows only when BOTH were
logged, labeled "logged fill"; the page never sizes, never suggests an exit (#4, #5).

A Managing card whose position names no security still has its line (the clocks and risks are the card's); a flagged
price history (`baskets.price_flags`) reads the flag's reason in its price cells and keeps its line.

### 3. Due this week: the Catalyst Calendar's bucket

The Catalyst Calendar's items by its own pure function (`dashboards/catalyst_calendar.py::catalyst_calendar`, with
`today = O`, the **countdown origin** (F2, `DASHBOARDS.md` rule 2: the action day, the session after the night, on a
live view; the as-of D on a past one), and each card's `LatestCall` built from the card in effect at D, not the newest
row of the log), cut to the **`this_week` bucket** (through the coming Sunday; O alone on a Sunday): every dated
catalyst on a counted card, and each card's two deadlines ("entry window ends" = `arm_until`, "exit-by" = `exit_by`)
when they fall in it. An item dated O is marked **today** (on a past view the line carries its date instead). The
"Last night" clocks (an entry window closing within 3 days) count from O too; `days_after_night` is an age and stays
on the as-of. The Calendar's limit stands and is stated: catalysts are edited in place with no history, so a
past as-of reads today's catalysts against that day's cards. Below the bucket, one quiet count: the catalysts that
PASSED since P and are not yet pruned (the Calendar's "passed" list is the place to prune them). Each line opens the
Cockpit; the header opens the Calendar.

### 4. Insider buys filed since P

Insider Buying's rows (`dashboards/insider_tracker.py`: `purchase_of` on the shared reader's Form 4 rows,
`read_filing_rows(..., kinds=FORM4_KINDS)`, read from P − 30 days so a cluster at the edge counts its neighbors) cut to
the purchases whose **FILING date is in (P, N]** (the public clock, the page's own dating rule). The line: ticker and
its cards (chips), the buyer and role class, the purchase's shares, price and dollars (the reader's own code-P summary,
never re-parsed), the trade date with "filed N days after the trade" when it differs, the **cluster** word when two
distinct buyers filed within 30 days (the Leaders rule, `CLUSTER_MIN_BUYERS`), and **became a trigger** as Insider
Buying reads it (an insider entry trigger on a recorded card in the window whose sources carry this accession; "not a
trigger" says nothing about why). No "since" column: a buy filed last night has at most one session behind it, and the
move after a buy is the Track Record's statistic, never this page's. Over a weekend the span (P, N] holds nothing new
(EDGAR accepts filings on weekdays), and the section says so rather than widening.

### 5. The tape: the armed names' last session

The Hot Board's rows of kind `armed` (`dashboards/hot_board.py::candidates` on the cards in effect at D, one row per
`armed_members` entry per Armed card; a name on two cards is two rows), read as **closes only** (the bars the Hot
Board's `since_arm` reads, from the arm night; no display member runs here): the **last session's return** (the last
close on or before D over the one before) and the **move since the arm night** (`since_arm`: the first close on or after
the arm night, the Scoreboard's entry rule, to the last close on or before D) with IWM over both spans, and the Hot
Board's two clocks (sessions into the window, sessions left). The arm night is the Hot Board's (`arm_night`: the first
night of the member's current recorded run, the winning row per night).

Shown: the **five largest rises and five largest falls on the last session** (by the last session's return, with n =
the armed names priced, and the count unpriced or flagged), and "show every armed name" for the rest, in the same
order. A flagged price history keeps its row with the flag's reason; a name with no bar since the arm night reads
"unpriced". Neutral words only ("largest last-session rise"), never "winner" or "leader". The header opens the Hot
Board; the Entry Windows link sits beside it.

### 6. Forming: a count

One quiet line, gray, last: the Warming cards (count), of which the cards whose key turned LAST NIGHT (section 1's
`key_on` lines on a Warming card) by name, and the Incubating cards (count). No roster read, no quiet signals: the
list is Forming Quietly's, and the header opens it.

## Loudness

The trading set's tape look (`docs/dashboards/hot-board.md` §Look). **One accent, the Board's Armed color, on exactly
these lines:** a card that entered Armed since P, a name newly armed, and an entry window closing within 3 calendar
days of N. A card that LEFT Armed and an exit-by reached are set in the warm tone the What Changed page uses for a
clock, never the accent. Sign tone (green / red) only on a return. Everything about Warming is in the plain text color
and everything about Incubating in the gray scale, at the end of its section. The empty states are quiet sentences,
not badges. The menu entry carries no count, and the page carries no sound, no toast and no badge anywhere.

## Past as-of

Every section reads at D on the public clock: N and P are the record's nights on or before D; the cards and rosters are
the as-of snapshot; the position is as of the view's instant (a fill logged after D is not a position then); the
filings and bars count from when they became public. The Calendar's catalysts are the one "now" input, stated in its
section. A past D before the record's second night has no P: section 1 says "no earlier night to compare".

## The quiet "show the work" line

The as-of; N and P with the sessions between them; the clock; cards counted / recorded on N / carried from an earlier
night / Managing / Armed / Warming / Incubating / no call yet / ingest not fresh / reconstructed; names read in the tape
(priced / unpriced / flagged, the flagged note opening to the tickers, `FlaggedNote`); purchases read / in the span;
the cache state and `compute_ms`.

## Cache and the warm-up

`dashboards.cache.cached(conn, "daily-digest", (clock,), asof, compute)` holds the whole payload (every section); the
"show every armed name" toggle is client side. The cache clears on a new run or a basket edit (rule 7). The page is
the morning visit the post-nightly warm-up exists for: the build adds `("daily-digest", {})` to
`pipeline.warm_dashboards.PAGES` and the frontend's default query to its `FRONTEND_DEFAULT_QUERY` mirror, so the
first visit after a pass is a hit (the warm-up's drift tests fail otherwise).

## Backend and frontend

- `backend/dashboards/daily_digest.py`, pure: the night pair (N, P) from the log's nights; the section builders, each
  calling the source page's pure function on the digest's reads (`what_changed`, `catalyst_calendar` with `today = D`,
  `purchase_of` / `clusters` / `trigger_hits`, `candidates` / `arm_night` / `since_arm` / `sessions_left`); the
  Armed-first order of section 1; the last-session return from two closes; the five-up / five-down cut; the counts.
  Nothing here re-derives a rule a source page owns: a source page's rule change changes the digest with it.
- `backend/app/routers/dashboards/daily_digest.py`: `GET /dashboards/daily-digest?asof=` (every section + the counts).
  `known_at, clock = serve_view(asof)` threads into the `calls` reads, `cards_as_of`, the thesis read, `price_flags`,
  `read_filing_rows` and the bars. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_daily_digest.py`: the night pair (N on D before and after a pass; a Monday D reads
  Friday against Thursday; a single-night record has no P); section 1 carries What Changed's lines in the Armed-first
  order, Incubating last; a Managing card's line (logged price vs the first close since the fill, stated which;
  unattributed position; shares × price only when both logged); the this-week cut and the "today" mark; the purchases
  cut to (P, N] with a cluster counted from the 30-day reach; the five-up / five-down cut with n; every empty section
  present; and, DB-backed, **a card that armed after the as-of is not in section 1 then**, **a Form 4 filed after the
  as-of is not in section 4 then**, and **a fill logged after the as-of is not a position then**; the router off one
  cached read.
- `frontend/src/dashboards/daily-digest/`: `entry.ts` (slug `daily-digest`, order 280, `asofAware: true`),
  `DailyDigest.tsx`, a pure `digestView.ts` (section order, the Armed-first grouping, the five-up / five-down cut), a
  `useDailyDigest.ts` hook, `daily-digest.css`, `thumbnail.svg`, tests (the rendered sections, the empty states, the
  loudness rule read off the stylesheet and the markup, the links carrying `?asof=`).
- **Layout:** one column of six blocks in the order above, each a header row (page name as the link, count) over its
  lines; the tape block a short table; on a phone the blocks stack and each line wraps under its ticker. In section 1 each
  card's routine nightly churn (triggers re-dated, new or gone, new risk signals, names new on watch) is ONE quiet gray
  count line under the card, closed by default and opened in place by a click (choice 17).

## Thumbnail

Six horizontal bands of unequal height stacked top to bottom in the gray scale (the sections), each with two or three
short round-capped strokes (the lines); the top band's first stroke carries one accent dot at its left (the one loud
line). No words.

## Known limits (stated on the page too)

- **The digest is a cut of other pages, never a judgment:** the lines are the record's own labels and computed figures;
  nothing here says what to do.
- **Catalysts have no history**, so a past morning reads today's catalysts (the Calendar's limit).
- **A weekend or holiday morning repeats Friday's night**: nothing new is public, and the page says so.
- **The tape reads closes only**; the Hot Board's heat, RVOL and relative strength are on the Hot Board.
- **Context and Bench names never count** in the tape or insider sections.

## Out of scope

Any notification, e-mail, push or badge (#7, rule 1); an LLM summary of the morning; a pooled "average move" of any
kind (rule 6); position sizing, exposure, P&L in dollars beyond a logged fill's shares × price, or any exit suggestion
(#4, #5: the exposure and risk pages of the managing set are their own plans, after the positions-proxy decision);
intraday data.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
Pre-decided here for the operator's review:

1. **The night pair** N / P = the newest recorded night on or before the as-of and the one before it, across the
   counted cards (What Changed's "last night", at an as-of).
2. **The section order:** Last night → Positions → Due this week → Insider buys → The tape → Forming. Positions second
   because the page's headline is what changed; an operator who wants positions first says so and it is a one-line
   change.
3. **The positions proxy, v1:** a position is a Managing card's effective position as the thesis carries it (the
   decision log's take rows; the seed-era columns as the fallback). Operator conviction weights never enter
   (`BasketMember.conviction` stays TRIAGE metadata); a weights input is the exposure page's question, not this page's.
4. **The insider span** is (P, N] by filing date, with the cluster reach 30 days back; no "since" column.
5. **The tape cut** is five rises and five falls by the last session's return, every armed name behind a toggle; closes
   only, no display member.
6. **"This week"** is the Catalyst Calendar's own bucket (through Sunday), with the "today" mark; the passed catalysts
   since P are a count.
7. **The accent** marks a card entering Armed, a name newly armed and a window closing within 3 days; nothing else.

### Built choices to confirm

The build (2026-10-30) made these choices that the plan above does not spell out, each the operator's to confirm or change.
Nothing in this list was measured against prod data: the build ran on neutral fixtures, and the timing under "Cost" below is
for the orchestrator to measure on dev with a prod copy.

1. **No P means no baseline, and two sections say so.** With no earlier recorded night (the record's first night, or none),
   section 1 shows no line (not a "new card" line for every card) and section 4 has no span; both say "No earlier night to
   compare." The other four sections still read (they need no baseline).
2. **Which group a card sits in, in section 1.** The plan names three groups for four states. Built: the first group holds a
   card that is, or was at P, **Armed or Managing** (a Managing card holds a position on an armed name, so its changes sit
   with Armed, not below Warming); then Warming; then Incubating, last. A card that moved Warming to Incubating is Incubating
   (gray); one that moved Armed to Warming is in the first group. Inside a group, cards by name. The grouping is the server's
   alone (`digestView.ts` does not regroup it).
3. **The tone rule, exactly.** Accent: a state move to Armed, a **new card born Armed**, a name newly armed, an entry window
   closing within 3 days. Warm: a state move from Armed (but not Armed to Managing, a position entered is not a card that
   left Armed), an exit-by reached. A Managing-to-Armed move is the accent (the position closed, the card is Armed again).
   The same facts in other sections carry no tone: an "entry window ends" item in the this-week list, a tape row with its
   window closing, and a position's hold clock are all plain, so the accent stays on exactly the lines of section 1.
4. **A trigger line's source** is found by the trigger's identity (role, security, kind, event date) in the card the line was
   read from (the baseline card for "no longer firing") and linked by the Evidence Ledger's rule with the issuer's CIK; a
   price reference has no link and shows as text; a trigger that states no source shows none.
5. **Positions are the cards whose recorded state in effect is Managing**, each with `decisions_repo.effective_position`
   at the as-of and the view's instant (a fill logged after the as-of is not a position then; the card keeps its line
   saying "no position logged as of this date", with its clocks, risks and kill criteria). The position model carries no
   shares, so the fill's shares come from the take row the position came from (the latest non-voided take of that day on
   that name as the log stood at the view's instant); a seed-era position has none. Shares times price shows only when both
   were logged.
6. **The since-fill basis.** The logged price is the start unless a split in force at the as-of has its first session after
   the fill date (the bars are on the view's scale, the logged price on the old one): then the first close on or after the
   fill date stands in and the line says why. A fill dated after the last close is "unpriced" (no close since the fill); with
   the first-close start, a fill dated before the price window is "unpriced". With the logged price, IWM runs from its first
   close on or after the fill date to its close on the name's last-close date; with the first-close start, IWM is read on
   that close's date and the last-close date (Trigger Log's `since_move`).
7. **The last session is exactly the calendar's last two sessions** (`series.session_calendar`, as the Hot Board's clocks use
   it): a name needs a close on BOTH, and one missing either reads "unpriced" with the date it lacks, never a longer span
   under the "last session" label. The same return is used for the held names in section 2 and the armed names in section 5.
8. **The hold clock** counts sessions left by the Hot Board's `sessions_left` (Mon-Fri, holidays count) and, once passed, the
   calendar's sessions after the exit-by through the as-of (the Hot Board's `closed_ago`). The exit-by date itself reads "its
   last day" with 0 left.
9. **Risk signals** on a position are the card's distinct (kind, security, event date) signals, not its labels; "new last
   night" is how many of them section 1 lists as new risk signals on that card (0 with no P). Kill criteria and catalysts are
   read as they stand today (they are edited in place, no history), and the page says so.
10. **Catalysts passed since P** are the dated catalysts after P and before the as-of that the Calendar still lists as passed
    (strictly after P: one dated P itself is not "since P"); the Calendar's whole passed count is beside it. Nothing with no P.
11. **Insider buys: the filings read are cut to N.** The cluster rule sees filings from P less 30 days through N, never a
    filing after N, so a morning after N (a weekend, before the pass) does not let a later buyer make a row a cluster. The
    reader takes `days`, not a start date, so the read is `days = as-of - (P - 30 days)`; the recorded nights read for
    "became a trigger" are those in (P, N]. Context and Bench roster rows left out are counted on the work line.
12. **The tape cut runs over distinct names.** A name on two cards is two rows (the Hot Board's rule) and ONE name in the
    cut: rises are the positive last-session returns (largest five), falls the negative ones (largest five), ties by ticker;
    every row of a cut name is flagged. A flat name is neither. Rows come largest rise to largest fall, then the names with no
    return by ticker; the toggle shows every row in that order, and renders only when the cut leaves rows behind it.
13. **The page links** to a full page carry `?asof=` for a day other than today and leave a live visit's URL clean (an absent
    param already reads today; the page cannot tell an absent param from today's). Positions has no full page to open, so its
    header is plain; Entry Windows sits beside the Hot Board link in the tape's header.
14. **A flagged price history** keeps its tape row and its position line with the flag's reason in the price cells, is left out
    of n, the cut and the figures, and is listed once under the page's flagged note.
15. **Marks.** A card whose row in effect carries `ingest_fresh = false` reads "ingest not fresh" and a reconstructed row "R",
    on its section-1 card header and its position line (not on every line, a gray tag by the name).
16. **Cache and warm-up.** The whole payload is one cache entry per (clock, as-of); `("daily-digest", {})` sits after Insider
    Buying in the warm-up's `PAGES` (its cold read is mostly the same Form 4 read).
17. **The fold (added after the dev measurement, client side only).** Per card in section 1, the lines whose change is a
    trigger re-dated, a trigger new, a trigger gone, a new risk signal or a name new on watch fold into ONE gray count line
    under the card ("12 triggers re-dated · 4 triggers new · 1 gone · 9 risk signals new · 2 new on watch": only the non-zero
    parts, in that fixed order, singular for one). It is a button, closed by default, that opens the folded lines in place
    (the server's order, each still a line that opens the Cockpit and shows its source) and closes them again: plain
    component state, nothing removed, per card. A state move or new card (the headline), a key on or off, a name newly armed
    or no longer armed, an entry window closing and an exit-by reached stay lines. No folded kind ever carries a tone (the
    server's `tone_of` marks only state, new_card, armed, entry_window and exit_by, and a test reads that function and pins
    it), so no accent or warm line is ever hidden. A card whose only lines are churn keeps its header and its count line. The
    section header counts what is folded: "58 cards · 405 lines · 368 folded into counts" (the figure is a count of the
    folded kinds above; the MEASURED section's 249 is the re-dates and new risk signals alone). The payload, the router, the
    cache and the backend tests are unchanged.

### Cost (PROPOSED, not measured)

Section 1 is two SELECTs over the `calls` log (What Changed's reads); section 3 is a read of the cards' catalysts;
section 4 is the Form 4 read over the counted names from P − 30 days (Insider Buying's 30-day read MEASURED 37 s cold;
this one spans about a month too, so expect the same order); section 5 reads closes for about a hundred armed names
from their arm nights (the Hot Board's bar read, without its eight members); section 2 a handful of names. Expect the
cold read near Insider Buying's; the warm-up makes the morning visit a hit. MEASURE on dev with a prod copy and record
the cold `compute_ms`, the payload and the counts here (the page's own work line prints the counts and `compute_ms`). The
build ran on neutral fixtures only and has measured nothing.

### MEASURED (dev with a prod data copy taken after the 2026-10-29 nightly, read 2026-10-30 afternoon)

- **Live as-of 2026-10-30** (the night of 2026-10-29 against 2026-10-28; 62 counted cards: 43 Armed, 18 Warming, 1
  Incubating, 0 Managing; 3 cards ingest-not-fresh): 257 KB, cold compute **35,683 ms** (the Form 4 read over 1,150
  voting names is most of it, as the plan expected: Insider Buying's 30-day read measured 37 s), cache hit **27 ms**.
  Section 1: 58 cards with a change, **405 lines** (126 triggers re-dated, 123 risk signals new, 70 triggers new, 37
  names new on watch, 19 names newly armed, 12 triggers gone, 6 windows closing, 6 names no longer armed, 4 state
  moves, 1 key on, 1 key off; 28 accent lines, 0 warm; the longest card 26 lines). Positions: none (no Managing card on
  prod). Due this week: 12 items (6 entry windows ending, 5 catalysts, 1 exit-by). Insider buys filed in the span: 5
  purchases on 5 names (4 became triggers, 0 clusters; 50 filings read, 45 folded as routine). The tape: 95 armed rows,
  78 names, 77 priced, 1 unpriced, 0 flagged; 5 rises and 5 falls shown. Forming: 18 Warming, 1 Incubating, 0 keys
  turned last night.
- **Past as-of 2026-09-15** on the public clock (10 counted cards then; the night of 2026-09-15 against 2026-09-14):
  97 KB, cold **17,002 ms**; section 1: 8 cards, 167 lines; 1 item due; 6 purchases (4 clusters, 4 triggers); 20 tape
  rows on 19 names.
- **The line count is the finding.** Two consecutive nights differ by about 400 What Changed lines, and 368 of them are
  the five churn kinds (triggers re-dated, new or gone, new risk signals, names new on watch): the common nightly churn,
  true of nearly every Armed card. On the morning page
  those lines are folded per card into one quiet count line that opens on click (honest loudness, Workbench principle
  3: the common noise collapses, the exception stays a line); the state moves, keys, names armed or no longer armed and
  the clocks stay lines. The server payload is unchanged (nothing is dropped), so the cache and the numbers above hold. MEASURED after the
  fold on the same copy: 58 fold lines, 45 lines shown of 405, the 28 accent lines unchanged, the page 5,858 px tall
  at 1,280 px wide (33,750 px before).
