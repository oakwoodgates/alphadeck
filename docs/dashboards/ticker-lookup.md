# Ticker Lookup

> Slug `ticker-lookup` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 220`.
> The operator approved the TRADING set and this page in it (2026-10-29: "write the plans"); the choices the build
> makes that this plan does not spell out go under "Choices to confirm" for the operator's review. The rules every
> dashboard follows: `docs/DASHBOARDS.md`. The fourth page of the TRADING set.

**Label:** Ticker Lookup. **Description:** "Type a ticker: every card the company sits on and its part in each, the
record's triggers on it, its tape, its filings and your decisions on it, at the as-of."

It answers "what does the platform know about THIS name": today a company is reached only through a card (the
Cockpit needs a thesis id; the Peer Table's find box filters a loaded table). This page starts from the company and
gathers what every other surface shows about it, one screen. Display only: it never feeds a call, never writes, never
calls the LLM. Nothing here is new data; every section is a reader another page already uses, scoped to one
`security_id`.

## Finding the company

- **The search box** (the first dashboard with a server-backed one) suggests as you type through the Workbench's
  existing read `GET /workbench/securities?q=&limit=10` (the shared `useResolveSecurities` hook, `api/hooks.ts`:
  substring on ticker or name, alphabetical). **Enter resolves an exact ticker first** (`securities/master.py::
  ids_for_tickers`, upper-cased), else the first suggestion; `/` focuses the box. The chosen company's ticker rides
  the URL as `?t=<ticker>` beside the shell's `?asof=`, so a lookup is a link. A ticker with no company reads "no
  company with ticker X" with the suggestions under it; the former-ticker search (`master.search(include_former=True)`)
  is offered as "search former tickers too".
- **The read:** `GET /dashboards/ticker-lookup/{security_id}?asof=`, one cached payload per (company, clock).

## The sections, top to bottom (each states its n)

1. **Identity:** ticker, name, CIK with its EDGAR company link (`https://www.sec.gov/cgi-bin/browse-edgar?action=
   getcompany&CIK=<cik>`), exchange, business type · supersector · royalty tag, origin, instrument kind, the vendor
   price symbol when it differs (`master.identity_for`, `securities/origin.py`); the approximate market cap at the
   as-of and its tier (the Peer Table's `cap_cell` rule and inputs, `dashboards/peer_table.py`; labeled approximate;
   its gap reason when unpinned); $vol 20d; the data flags reaching its price (`baskets.price_flags` reason, shown as a
   quiet line, never hidden).
2. **Cards:** one row per card the company sits on at the as-of (the counted cards' rosters, `baskets.cards_as_of`;
   a company in two links of a card is one row listing both links): card (opens the Cockpit on this name,
   `onSelectThesis(thesisId, ticker)`), link(s), the Context/Bench tag when silent, the context role when set,
   operator conviction as "weight 3 of 5" (display; never a call input, #4), signed off, the `thesis_fit` prose with its
   authorship ("model draft" / "your words", `authored_by`), the card's recorded state at the as-of, and **this name's
   place on the card in effect** (the What Changed read of the `calls` log): armed (verdict, grade, setup strength),
   watch, trigger-bearing (n live triggers, which key), or none; a silent name reads "not counted". Folded under the
   table: "also on N archived cards" (`thesis_repo.list_all(include_archived=True)`, names only) and "excluded from
   N cards" (`Thesis.exclusions` naming this security, with the reason).
3. **The record's triggers on this name:** every entry trigger and risk signal the `calls` log shows on this
   security across the counted cards in the last 365 days, the Trigger Log's identity and dating rules
   (`docs/dashboards/trigger-log.md` §What a row is): an identity is (role, kind, fire date) for this security, the
   label NOT part of it, so one trigger is ONE row however its label changed night to night. Each row shows its
   recorded night (the first night the record shows it), fire date, card, role, kind, grade, the label of that first
   night (hover: "label changed N times" when the record showed N distinct other labels for it, `label_changes`),
   source link (`dashboards/filings.py::source_url`), live at the as-of. Newest first.
4. **Tape:** the display members at the as-of (`signals/display/`, all registered members through one
   `PointInTimeData` with `display_bounds()`; `etf_flow` only for an ETF), as a compact grid of the Cockpit's own
   figures: close, vs 50d and 200d, 1D · 7D · 30D · 90D · 1Y, % off the 52-week high and above the low with their
   dates, RVOL 8 and 20, $vol 20d, RS vs SPY and IWM with the `leading` mark, VCP coiling when set, insider 90d
   (buys/sells, buyers, net $) and 30d; the 90-bar `price_path` sparkline with IWM aligned on the same sessions
   (`dashboards/series.py::aligned`). A member with no reading shows its note ("n/a: 12/20 volume bars"), counted.
5. **Filings:** the company's filing-derived facts and its EDGAR index rows for the last 90 days (toggle 365),
   `dashboards/filings.py::read_filing_rows` for this one id, grouped by day in the Filing Stream's shape (kind, form,
   headline, detail, link, routine rows folded per day), each row's `ratifier` / `ratified_on` shown where set.
6. **Catalysts:** the dated and undated catalysts of the cards it counts on (thesis-level, `Thesis.catalysts`, each
   with its card chip; passed dates marked) and the company's own catalyst facts (`pit.catalyst_facts`, already rows of
   kind `catalyst` in the filings reader, listed here under their own heading).
7. **Decisions:** the operator's logged decisions naming this security across the counted cards (the Idea Timeline's
   router read, `read_decisions(conn, ids)`, filtered to this `security_id`; `decision_date <= asof`): date, card,
   action (take · pass · close · void, voided rows struck through), shares and price (the operator's own fills), reason.
   Only the operator sees this page; the fills are theirs.

## Loudness

The trading set's tape look (`docs/dashboards/hot-board.md` §Look). The one accent: the "place on the card" cell when
it reads armed (the Board's armed tone on that word only). The sign tone only on signed tape figures. Section
headings are the quiet uppercase labels of the research set; nothing bold but the ticker.

## Past as-of

Every section at the as-of: the rosters from the snapshot in force (`thesis_repo.get_asof` through `cards_as_of`),
the card in effect from rows on or before the as-of, the display members on the public clock, filings and catalyst
facts public by the as-of, decisions dated on or before it. A basket row added after the as-of is not on the page; a
filing filed after it is not.

## The quiet "show the work" line

The as-of; the clock; cards counted / this company on / archived / excluded; triggers found; members with no
reading; filings in the window; data flags; the cache state and `compute_ms`.

## Cache

`dashboards.cache.cached(conn, "ticker-lookup", (str(security_id), clock), asof, ...)` holds the whole payload for
one company; the filings window toggle is client side (the read carries 365 days). The search suggestions are not
cached (small, already served). The cache clears on a new run or a basket edit (rule 7).

## Backend and frontend

- `backend/dashboards/ticker_lookup.py`, pure: the cards section from the rosters and the cards in effect (links
  merged per card, the place on the card), the archived and excluded folds, the trigger rows (the Trigger Log's
  identity rule, scoped to one security), the tape grid from the members' readings, the day grouping of filings, the
  decisions filter, the counts.
- `backend/app/routers/dashboards/ticker_lookup.py`: `GET /dashboards/ticker-lookup/resolve?t=` (exact ticker →
  `security_id`, else 404 with nothing more; the suggestions come from the Workbench read) and
  `GET /dashboards/ticker-lookup/{security_id}?asof=` (the payload; 404 for an unknown id). `known_at, clock =
  serve_view(asof)` threads into every read. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_ticker_lookup.py`: exact ticker wins over a substring match; the cards section from
  hand-built theses (two links → one row with both; the silent tag; the role; the place on the card: armed, watch,
  trigger-bearing, none); the archived and excluded folds; the trigger identity merge scoped to one security; the
  decisions filter and `decision_date <= asof`; and, DB-backed, **a basket row added after the as-of is not on the
  page** and **a filing filed after the as-of is not listed**; the router off one cached read.
- `frontend/src/dashboards/ticker-lookup/`: `entry.ts` (slug `ticker-lookup`, order 220, `asofAware: true`),
  `TickerLookup.tsx` (the search box with suggestions, the URL `t` param, the seven sections; a pure `lookupView.ts`
  for grouping), `useTickerLookup.ts`, `ticker-lookup.css`, tests. The Filing Stream's day grouping and the Peer
  Table's find box are the patterns to follow.
- **Layout:** the tape look; sections stacked, each with its heading and n; on a phone the tape grid wraps two figures
  per line and the cards table scrolls sideways.

## Known limits (stated on the page too)

- **One company at a time;** a company not on any counted card shows identity, tape and filings only, and says so.
- **The record's triggers are the live ones the log kept;** a trigger that lapsed between recorded nights is not here.
- **The cap is approximate** at the as-of.
- **Decisions are the operator's own fills**, shown as logged.

## Out of scope

Any score or ranking of the company; a company-only Cockpit route (the Cockpit stays per card); peers (the Peer
Table); news; options or intraday data; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or
change. Pre-decided here for the operator's review: the Workbench search as the suggestion source; exact ticker on
Enter; `?t=` in the URL; the 365-day trigger window and the 90-day filings default; conviction shown as "weight";
decisions shown with shares and price.


### Built choices to confirm

What the build chose where this plan is silent, each the operator's to confirm or change. Nothing here was
measured on real data yet: the DB-backed tests and a dev run are still to come, and no compute time is recorded.

**Triggers**
- **Identity leaves the label out** (the Trigger Log's rule since 2026-10-29: role, security, kind, fire date; this
  page first built it with the label in, then aligned). A label can carry a number that moves every night (a
  breakdown's names that night's close), so the same fire date under a new label is the same trigger. MEASURED for
  the Trigger Log on a prod copy (not re-measured on this page): 1,675 of 11,536 (card, security, kind, fire date)
  groups carried more than one label, up to 56 labels for one signal. The row shows the label of its first recorded
  night and `label_changes` = the distinct other labels the record showed for it from that night through the
  window's end (another copy the same night counts); the hover reads "label changed N times" when N > 0. The rule is
  one function, `trigger_id` in `backend/dashboards/ticker_lookup.py`. It also sets the place cell's counts: a
  relabeled trigger is one live trigger (or one risk signal), not several.
- **Read from the cards the company sits on at the as-of**, not from every counted card (the whole log's cards are
  too many `calls` rows for one lookup). A trigger recorded on a card the company has since left is not here; the
  page's limits say so.
- **A trigger on the row in effect the night before the window opens counts as recorded before it**: not a row, and
  counted on the work line when still live (the Trigger Log's rule).
- **The key a trigger turns** is read from its kind (`DEFAULT_CONFIG.conviction_kinds` / `confirmation_kinds`). The
  assembler's revenue-screen exception (`revenue_accel_key`) needs the detector, which `TriggerRef` does not carry;
  its default is `conviction`, which is also what the `catalyst` kind reads, so the two agree today.
- The role is a glyph (filled = entry trigger, hollow = risk signal); the "now" column reads `live` or a quiet dash.

**Cards**
- **Place on the card:** armed (with verdict, grade, setup strength) beats watch beats trigger-bearing beats none;
  a silent name is always "not counted"; a card with no recorded call yet reads "no recorded call yet". A risk signal
  alone does not make a name trigger-bearing: it shows as a count beside the place.
- **A company in several links** is one row, links in the card's chain order. Weight and authorship are per link; the
  weight cell reads "weight 3 of 5" (or "2 and 4 of 5" when the rows differ); "signed off" shows only when every row of
  the company on the card is signed off.
- The archived fold lists archived cards that existed by the as-of and hold the company in their roster then
  (`get_asof`); the exclusions fold lists the counted cards' stored exclusions as they stand today. The archived read
  is a `SELECT` that narrows to the archived cards whose live roster or any roster snapshot holds the company, then
  `get_asof` on those, not `list_all(include_archived=True)` (the same answer, without loading every archived card).
- **The counted cards are their own cache entry** per (clock, as-of) beside the per-company page (the Filing Stream's
  `("cards", clock)` pattern): `cards_as_of` loads every live card, so only the first lookup after a run or a basket
  edit pays for it. Unmeasured.

**Identity and the tape**
- **A flagged price history:** the tape figures stay on the page but gray (the Cockpit's grayed cells), the market
  cap is withheld (the Peer Table's rule), and a quiet line names the flag. The insider and fund-flow groups read no
  price and stay.
- **The market cap rule is imported** from the Peer Table (`dashboards.peer_table.cap_cell`), not copied, so the two
  pages cannot disagree; the cost is that cutting the Peer Table now breaks this page (copy the rule here first).
- The tape shows the figures this plan lists and no more (no SMA flips, no 20d/60d volume ratio). RVOL reads "RVOL 8" and
  "RVOL 20". "Off 52w high" and "above 52w low" carry no sign tone (each always has one sign). The fund-flow group
  shows only for an ETF. The `$vol 20d` figure appears in Identity and in the Volume group (one figure).
- **The sparkline** is the price-path member's 90 closes with IWM on the same sessions, both indexed to 100 at the
  first session both have a bar; a missing IWM bar breaks its line.
- Instrument kind shows only for an ETF (equity is every other row); the vendor price symbol only when it differs
  from the ticker; former tickers come from `security_ticker_history`.

**Filings and catalysts**
- The read carries 365 days; the routine rows are capped at 200 per fold group, newest first (the Filing Stream's
  number), the rest counted and named on the page. The window toggle (90 / 365) and the day fold are client side.
- **The company's catalyst facts leave the Filings list** and show once, under Catalysts (the plan lists them in both
  places; listing a row twice would double its count).
- Card catalysts come from the cards where the company has a non-silent row, as the operator's list stands today (not
  point-in-time); "passed" is judged against the as-of.

**Decisions**
- **A separate `SELECT`, not `read_decisions`:** the Idea Timeline's read has no shares or price columns. The rows
  naming the company on the counted cards, plus every void row (a void need not carry a security).
- **A decision counts from its `decision_date` AND, on a past view, only once it was logged by that view's instant**
  (`recorded_at <= known_at`): `serve_view` keeps the position as of the same instant, and `decisions_repo` reads both
  axes. The plan names only the date; drop the second gate if you want the date alone.
- A void row is listed as its own line and strikes the row it names; a void dated after the as-of has struck nothing.
  A card-level pass (no security) is not listed.

**Search and URL**
- `GET /resolve?t=` upper-cases and trims; `former=true` adds an exact match on a former ticker
  (`security_ticker_history.old_ticker`). The suggestions come from the Workbench search, which already matches former
  tickers by substring; "search former tickers too" finds an exact rename.
- **Enter:** the exact ticker; else the first suggestion, only when the suggestions are for the text in the box; else
  the typed text goes to the URL and the page says there is no such company. Arrow keys move through the suggestions;
  a pick fills the box.
- `?t=` is upper-cased and set with `replace` (a view dial, like `?asof=`), keeping every other parameter.

**Not followed as written**
- Section 2 names `thesis_repo.list_all(include_archived=True)` and section 7 `read_decisions(conn, ids)`; the build
  uses the equivalents above, for the reasons given.
- The Filing Stream's day grouping is rewritten here (the dashboards import no other dashboard); the Peer Table's
  `cap_cell` is the one cross-dashboard import.
