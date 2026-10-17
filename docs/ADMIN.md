# ADMIN.md — the operator ops surface (freshness · run-now · backups)

> Repo path: `docs/ADMIN.md`. The **laptop-deploy ops surface**: is the record current, did last night's
> run actually work, run it NOW if not, and keep a DB-snapshot safety net. A READ surface over the cron's
> own instrumentation (`FEED_LOOP.md`) plus **two explicit operator triggers**. Companion to `FEED_LOOP.md`
> (the cron it watches + the backup mechanics) and `SCOREBOARD.md` (which surfaces the SAME staleness line).
> Code: `backend/app/routers/admin.py` · `frontend/src/admin/Admin.tsx`; the schedule math is
> `backend/pipeline/schedule.py`, the snapshot runner `backend/pipeline/backup.py`.
>
> **Status: BUILT** — Slice 1 (freshness/health + Run-daily-now, #208) + Slice 4 (the DB-snapshot button,
> #215) + the hole-aware freshness read and the `gappy` verdict (#330). Reached via the FE **Admin** nav tab (Board · Workbench · Scoreboard · Admin).

---

## Why it exists

The deploy reality it answers: a **sleep-loop cron on a laptop** (`scripts/daily_cron.sh`) that can miss
nights, and containers that don't always restart. Three questions and one safety net:

1. **Is the record current?** — the record-freshness readout.
2. **Did last night's run work?** — the cron-health verdict.
3. **Run it NOW if not** — the one explicit trigger.
4. **…and never lose the DB again** — the snapshot button (Slice 4, born of the 2026-07-21 truncation below).

## The bounds (what the surface may and may not do)

- **Pure ops READ surface.** `status` / `runs` / `backups` own **no tables and write nothing** — test-proved
  by counting every public table before/after. They read the calls log's `MAX(asof)`, the run-of-record
  artifacts (`data/cron_runs/`), the backup directory, and the schedule math. **No LLM anywhere near this
  router.**
- **Operator-initiated triggers only.** *Run daily now* and *Create snapshot* fire **only** on an explicit
  click — never on a page load, mount, or poll (the **cost thread**: cost is the operator's to spend, never
  ambient). Reads may poll; the triggers never do.
- **The price-feed repairs are the ONE place this surface writes data** (T2 + Q8, `/admin/price-feed/*`,
  its own router): the operator's Adopt / Set / Undo of a vendor price symbol and Acknowledge / Revoke of an
  unpriceable name — each an explicit click, each with its exact inverse beside it, each recorded append-only
  (migration 0046) with the operator named as its author. `GET /admin/status` still writes nothing: it reads
  that log, never appends to it.
- **Honest loudness (#7).** Staleness is measured against the last **expected** scheduled run (never raw
  `today − edge`), so a weekend never cries wolf; and a bad LAST run (freeze / errors / total ingest failure)
  is its own loud `unhealthy` verdict, **peer to** `stale` — the R1 freeze must never hide behind a green
  "healthy". A night with **no call-of-record inside the recent window** is its own loud `gappy` verdict too
  (the wrong-day fire the edge check cannot see). Loud styling is reserved for `stale` / `unhealthy` /
  `gappy`; "current", "never begun", and "never ran" stay quiet, and the missed-nights list renders only when
  there is one to show.
- **Auth stays deferred** project-wide (these routes ride the same tenancy seam as the rest).

## Record freshness — "is the record current?"

`GET /admin/status` → the summary the page opens on. The **record edge** is the calls-log `MAX(asof)`
(`calls_repo.record_edge`), measured against the last **expected** Mon-Fri + `RUN_AT` run
(`schedule.py::last_expected_asof` / `expected_runs_behind`, on the market clock — `market_now()`):

- A **Friday** edge read on a **Monday morning** is `0` behind — **current**, never a weekend false alarm;
  the same edge Monday **night** is `1` behind.
- `edge is None` → **"the record has never begun"** — the quiet fresh-install state (`days_behind` null,
  `stale` false), never an alarm.

**The hole check (hole-aware freshness, 2026-09-09).** The edge check above sees only `MAX(asof)` — and a
run that fires on the **wrong day** (the laptop's sleep drift: a 00:24 or 09:09 wake recorded the *next*
day's as-of) advances the edge right over the night it skipped. MEASURED on prod, 6 of 13 weekdays had no
`calls` row while the page read "current". So `/admin/status` also scans the last
**`ALPHADECK_ADMIN_MISSED_WINDOW`** (default **10**; `0` disables) scheduled weekdays ending at the last
expected run (`schedule.py::scheduled_window` / `missed_asofs`; `calls_repo.recorded_asof_stamps` +
`record_first`) for nights with **no covering call-of-record**, bounded to the record's own span (a night
before the record began is pre-history, never a "miss"; a fresh install has no holes). `record.missed` counts
them, `record.missed_asofs` lists them ascending, `record.window_days` says how many were scanned. `stale` /
`days_behind` keep their exact edge-check meaning; the expected day itself, when missing, appears in both.
The page lists the missed dates **only when there are any** (a control that doesn't discriminate doesn't
render). The cause — the sidecar firing for the day it *woke* rather than the night it was scheduled for — is
fixed in `FEED_LOOP.md` §the scheduling sidecar (the target as-of is now fixed at schedule time).

**What "covered" means — a POST-`RUN_AT` row (G2c).** A weekday counts as recorded only if some
call-of-record for that as-of was **`recorded_at` at or after that night's `RUN_AT` in market time**
(`datetime.combine(asof, RUN_AT, market_tz())`). Mere existence of a row was too weak a test: a pre-open
**"Run daily now"** writes a row for *today's* as-of off the **prior** session's bars, so a night whose 22:30
pass then failed still had a row and read as **covered** — the hole was invisible, which is exactly the
shape G2 exists to close (MEASURED on prod for 2026-09-09: two pre-open passes at 09:09 / 09:15 ET, the host
then off at 22:30).

- `record.daytime_only_asofs` names the as-ofs whose **only** row is such a daytime one. Every one of them
  also appears in `missed_asofs`, and both the page and the `gappy` detail mark them
  (`2026-09-08 (daytime row only)`) — because "nothing fired" and "something ran, but not after the close"
  call for different responses: the first wants a catch-up, the second wants to know *why the night's pass
  failed*.
- A **next-morning catch-up** row is recorded after the cutoff and correctly covers the night — which is why
  the comparison is against the cutoff rather than against the as-of's own calendar day.
- A night carrying **both** a daytime row and a proper post-close row is covered: the read takes
  `MAX(recorded_at)` per as-of, so the later row wins.
- The cutoff uses `Settings.cron_run_at` (`ALPHADECK_CRON_AT`), the same host var the sidecar fires on —
  mirrored into both services by compose so the trigger and its readout cannot drift.

**A reconstructed night reads as recorded here — on purpose: after a backfill, the hole CLEARS.**
`record_edge` / `record_first` / `recorded_asof_stamps` count EVERY `calls` row, including the rows
`pipeline.backfill` wrote (`calls.reconstructed`, migration 0042): after a backfill the night is no longer a
hole, `gappy` clears, and the edge can advance. The per-thesis PARTIAL check (M1, "Cron health") honors the
same decision — a backfilled thesis is RESOLVED, never a partial night — and only keeps the reconstruction
visible as a quiet count (`record.reconstructed_nights`). The backfill is the operator's answer to the hole,
so the page goes quiet and a NEW miss stands out.
Freshness asks whether the log advanced, not whether a row is scoreable — the Scoreboard's record path is the
one reader that filters reconstructed rows out (`SCOREBOARD.md`), so "healthy" here and "N nights
reconstructed · not scored" there can both be true of the same night.

### Stopped feeds — "is every name still being fed?"  `[BUILT, G5a price tapes · F1 fund shares]`

`status.tape` is the feed-freshness panel. It watches **two** per-name feeds, each with its own threshold,
its own rows (tagged `kind`) and its own block on the page, because they stop for different reasons and are
repaired differently. The price tape is below; the ETF fund-shares feed follows it.

#### Price tapes — "is every name still being priced?"

This closes the "monitor health: is it watched?" gap for prices.
**The failure it makes visible:** the price leg appends bars after the latest stored one, so a name whose
vendor series simply **STOPS** — the SEC ticker stays canonical but the vendor prices the name under a new
symbol after a rename, or the name delisted — returns a series that ends at the stop: **zero bars appended,
no error, indistinguishable from a market holiday.** Nothing read the tape's edge, so it was invisible, and
for that name every price-driven signal goes dark from the stop date (no breakout, no SMA flip, no RVOL — and
no price-based de-arm either) while the CIK-keyed filing feeds keep flowing, so it can still WARM on a filing
and never confirm on price.

- **What counts as stale:** the latest stored EOD bar is `ALPHADECK_TAPE_STALE_DAYS` (default **5**) or more
  **calendar** days before the run's as-of, or the name has no bars at all. Calendar days because the trading
  clock deliberately has no holiday calendar. A live tape gets that session's bar appended nightly, so its
  edge sits at 0–1 days and the threshold never comes near it — the number only matters once bars **stop**
  arriving: 5 means a tape may lag up to **four** calendar days before it reads stale, and a dead tape
  surfaces within a week. Counted out: a Friday close is fresh through Tuesday's pass (4 days) and reads
  stale on **Wednesday's** (5), so a weekend — or a weekend plus a Monday or Friday holiday — is inside the
  window. The accepted edge: a rare **two-session** closure beside a weekend (a Thursday+Friday shutdown
  leaves a Wednesday edge and a Monday pass = 5 days) flags for one night and clears on the next session.
  `0` disables the monitor; raise it if that night ever costs more than catching a dead tape a day sooner.
- **Where it comes from:** the nightly pass records each name's feed edges and its stale set into the
  run-of-record artifact, and this panel reads the artifacts that actually **evaluated** recency. So it
  is "as of last night" (the right granularity for a nightly feed), it costs no query, and this surface still
  owns no tables. `tape` is **null** until a pass has looked — a `--no-live` pass never counts.
  **It is the SAME inventory the page diffs against** (2026-10-17): each thesis's rows come from the newest
  finished live pass that COVERED that thesis, so a `--catch-up` that skipped theses (M2) shows what it found
  in the theses it did cover, and a skipped thesis keeps the rows of the last pass that covered it (normally
  the night before, in the killed-night shape). The loud rows are exactly what the next pass treats as already
  told, and `newly_stale` is what the newest such pass paged — a catch-up included — so a name the panel
  marks new is a name that paged. `asof` / `ran_at` name that newest pass (`FEED_LOOP.md` §"the stale-tape
  and name-error baselines carry each thesis forward").
  The **thresholds shown are the ones that pass judged under**, read off its artifact rather than live: a
  setting changed between the pass and the read would otherwise describe the list on screen with a number
  that never produced it. `0` on a threshold means that feed's monitor was disabled for the pass.
- **One row per security**, even when two theses hold the name, with its last-bar date and the thesis it was
  seen under; a name with no ticker renders by id rather than vanishing (#9). The list renders **only when
  something is stale** — "no stale tapes" is the normal night.
- **It pages, but it never changes `cron.status`.** A stopped tape is a **feed** gap to repair, not a cron
  fault: the run did its job. So the night's run row carries a problem line naming the tape and the notifier
  pushes it, while the one-word verdict stays about the cron (an `unhealthy` chip that really meant "a vendor
  renamed a ticker" would teach you to ignore the chip). Only **newly** stale tapes page — the diff is against
  the inventory this panel shows (each thesis as the last pass that covered it saw it), keyed on the security
  (not the ticker), so a handful of known-dead tapes do not re-page every night, not even across a
  skipped-thesis catch-up. The **first** evaluated pass after this shipped pages the whole current inventory
  once, on purpose.
- **Closed vs renamed vs a feed gap** (G5b/T1). A stopped tape whose company filed a SEC delisting form
  (25 / 25-NSE / 15-12B / 15-12G) **near its last bar** (−30 / +45 calendar days by default,
  `ALPHADECK_TAPE_CLOSED_BEFORE_DAYS` / `_AFTER_DAYS`), on a company EDGAR no longer lists under a different
  symbol, renders in a separate **quiet** "closed" block — "stopped trading <date>", the date being the form
  nearest the last bar — and never pages: there is nothing to repair. A stopped tape whose company EDGAR
  lists under symbols that include neither the master ticker nor its `price_symbol` stays a loud repair row
  marked **"renamed — EDGAR lists X"**: the symbol to point the price leg at, shown, never adopted for you.
  Everything else is a plain repair row. A form filed at some other time does not close a tape (a company
  files these for any listed class — VRME's 2025 warrant removal once silenced its common stock in 2026),
  and a never-priced name can never read closed. **Nor does a Form 25 that removed another class** (Q6): the
  pass reads each window Form 25's own class line, and one removing warrants, units, rights, notes or
  preferred is not a close of the common (ACHR's, IONQ's and four other still-trading names' recent 25-NSEs
  were all warrants). A Form 25 whose class cannot be read still counts, as before, and the cron log says so
  in a `warn:` line. A name that moves from closed back to repair pages once.
- **The repair is yours, and it is data, not code:** check each listed name for a ticker rename or a
  delisting, then set the vendor symbol override on the security master (`security_master.price_symbol`, the
  OTC fix's seam). The next nightly pass re-pulls the two-year window under the new symbol, appends the
  missing tail and hole-fills the overlap. Teaching the symbol resolver to follow renames **automatically** is
  deliberately not built: a wrong auto-resolve would file another company's tape under your member, which is
  worse than a visible gap (`INVARIANTS.md` #4/#6). See `DATA_SOURCES.md` §free EOD prices. **Since T2/Q8
  the renamed, never-priced and closed names move to the "Price feed repairs" panel below**, where the
  repair is one click with its Undo; this block keeps only the stopped tapes that panel does not hold (a
  plain feed gap — or every row, against an older backend that has no panel).
- **Unpriceable** (Q8). A stopped price row with **no stored bar ever** that is neither closed nor renamed
  (WONDF — a 40-F filer EDGAR lists no symbol for) is its own class: loud, it pages once when new, and it stays
  until you set a symbol, acknowledge it, or remove it (the panel below).

### Price feed repairs — "what do I owe the price feed?"  `[BUILT, T2 + Q8 + T3]`

One card (`price_repairs` on `/admin/status`; the writes are `/admin/price-feed/*`) for every price-feed
decision. Its INVENTORY is the freshness block's — the per-thesis carry-forward of the finished live passes
that evaluated recency (each thesis as the newest pass that covered it saw it; a renamed row keeps the splice
probe of the pass that saw it) — so it lists exactly what the page treats as already told; its DECISIONS
are read live from the append-only repair log (0046), so a click shows at once. Rendered only when
something is in it.

- **Renamed** (loud) — each stopped tape whose company EDGAR lists under a symbol we don't price, with the
  nightly **splice probe**'s verdict on every listed symbol (`FEED_LOOP.md` §the tape monitor, part 3): its
  shared dates with our stored tape, the median close ratio, verified (>= 5 dates within 1%) or not, and how
  far its bars run — plus the EDGAR evidence (current name, former names, the item-5.03 8-K). **Adopt <SYM>**
  (the verified one is the primary button; an unverified one stays adoptable on your judgment) sets
  `price_symbol` through the existing writer (basis `operator:adopt`) and refreshes the master's display name
  to EDGAR's current entity name; the row then reads "adopted SDCO — the next nightly pass re-pulls the tape
  under it" with **Undo adopt**. A symbol EDGAR does not list for the name is refused (409 — use Set).
- **Never priced** (loud) — **Set price symbol** (you type a vendor symbol, e.g. a TSX listing; it is
  validated by ONE vendor fetch into a throwaway cache — no bars, nothing written, 422), **Acknowledge —
  keep unpriced** (an optional reason; append-only, basis `operator:acknowledge`), or **Remove from basket →**
  (a link to the Workbench basket editor — there is no second delete path here).
- **Acknowledged** (quiet) — a collapsed count that opens to each acknowledged name with its reason and
  **Revoke**. An acknowledged name never pages, its vendor 404 is expected (T3), and it stays listed (#9); a
  revoke makes it loud again from the next pass (it pages once).
- **Closed** (quiet) — a collapsed count of the names that stopped trading; nothing to repair, their vendor
  404 is expected.
- **In force** (quiet) — every standing Adopt / Set whose name already left the inventory (its tape healed),
  each with its **Undo** — the inverse never vanishes with the problem (WB#1).

**Undo is exact.** Every change row carries the master's PRIOR and NEW `price_symbol`, `price_symbol_basis`
and (when an Adopt refreshed it) `name`; Undo writes the prior values back verbatim and appends an `undo` row.
It applies only to a name's newest standing change, and only while the master still holds that change's
values — if anything else re-pointed the symbol since (the `--adopt` CLI, the finalize self-heal), the Undo
refuses (409) rather than clobber a value it did not write. Nothing on this card is ever applied by the
system (#3); every row names the operator as its author.

**Successor CIK** `[BUILT, T7]` — the same card, its own inventory and its own append-only log
(`security_cik_change`, 0049). The inventory is the one the page diffs against — the per-thesis
carry-forward of the finished live passes whose successor scan RAN (each thesis as the newest such pass that
covered it saw it, a skipped-thesis catch-up included), so it lists exactly what the next pass treats as
already told; `successor_asof` names the newest such pass, and an Adopt records the night of the pass that
wrote the row.

- **Now files under a NEW CIK** (loud) — a member whose stored CIK lists no symbol on EDGAR while SEC's ticker
  map assigns ours to another registrant (`FEED_LOOP.md` §the identity monitor): the row names the stored CIK
  with its EDGAR name and deregistration ("UROY — stored CIK 0001711570 (Uranium Royalty Corp. /CA) lists no
  symbol · 15-12G filed 2026-07-31") and the successor with its evidence ("SEC now assigns UROY to CIK
  0002143673 (Uranium Royalty Corp.) · 8-K12B filed 2026-07-28 · incorporated DE · files 10-K / 10-Q").
  **Adopt CIK <cik>** — only the recommended one, and only when the successor's submissions were read that
  night — re-points `security_master.cik` in place (the row id stays: tape, facts, basket links, sign-off)
  and refreshes the CIK-dependent identity from the successor's filings (the `enrich_identity` derivation +
  the entity name), with no network request; the row then reads "adopted CIK 0002143673 — the next nightly
  pass reads its filings" with **Undo adopt**. Refused (409): a CIK the detector did not recommend, a name it
  did not flag, a master whose CIK moved since the pass, a successor CIK another master row already holds.
- **CIK re-points in force** (quiet) — every standing Adopt whose name left the inventory (its new CIK lists
  the symbol, so the next pass no longer flags it), each with its **Undo** (WB#1).
- A failed scan is one loud line (`successor_error`).

Undo is exact the same way: each change row stores the PRIOR and NEW value of every column the Adopt touched
(cik, name, the submissions-sourced identity, `enriched_source` / `enriched_at`); Undo writes the prior ones
back verbatim, only for the name's newest standing change, and refuses (409) if the master drifted since.

#### ETF fund-shares tapes — "is every sleeve still being sampled?"  `[BUILT, F1]`

The same silent-end class, one layer over. An ETF sleeve's shares-outstanding sample is what the net-flow
read is built from (`signals/display/etf_flow.py` — display context, never a call input), and its source is
a fallback chain (Polygon when a key is set → the issuer page → the aggregator) whose legs miss
independently. **Two ways it stops with no error:** every leg misses (the fund closed or renamed, a source
page was redesigned), or — the subtler one — a page's own **stated as-of date freezes** while it keeps
serving the same count, which the incremental compare correctly skips as an unchanged sample. Either way
the leg reports success and the series stands still.

- **What counts as stale:** the latest stored sample is `ALPHADECK_FUND_SHARES_STALE_DAYS` (default **7**)
  or more calendar days before the run's as-of, or the sleeve has no samples at all. Its own number, not the
  price tape's 5, because the feeds differ. MEASURED on dev from the stored samples: the primary source
  states the **pull date exactly** (lag 0 across 28 samples) while the aggregator fallback stated a
  **two-day-old** date — so a healthy sleeve's edge sits 0–2 days behind the pass that sampled it, where a
  live price tape's sits at 0–1. **7 therefore clears the two-day stated lag plus a long weekend plus two
  failed nights** before it calls a sampler dead, and a genuinely dead one still surfaces inside a week.
  `0` disables this feed's monitor independently of the price one.
- **Only ETF sleeves are judged.** A member is evaluated only if the fund-shares leg applies to it (an
  `instrument_kind = 'etf'` member with a ticker — the leg's own gate, shared with the monitor so the two
  cannot drift). This is load-bearing, not an optimization: an equity has no samples, so its edge is `null`,
  and `null` means **stale** to the rule — ungated, every equity in every basket would be reported as a dead
  fund tape every night. A tracked sleeve with no samples at all is the opposite case and is reported
  loudly: its sampler has never worked.
- **A name can be stale on both feeds at once** — two rows, two repairs. The diff that decides what pages is
  keyed on the feed **and** the security, so a sleeve whose price tape died last month and whose sampling
  dies today pages again today, as the new thing it is.
- **The repair is yours, and it is NOT the price symbol:** check that the fund still trades under that
  ticker, look in the run's output for a fund-shares warning (the source chain warns visibly when a primary
  leg errors, which is what a redesigned page looks like), and confirm the shares-source key is set.
- **Expect this block to be empty, and that is the point.** It is quiet unless a sleeve's sampling actually
  stops, and today very few baskets hold an ETF sleeve at all — the monitor exists so the sleeve can grow
  without re-opening the same silent-end gap the price tape had.

### Split-basis defects — "is every tape on ONE split basis?"  `[BUILT, T4]`

A tape can be fed every night and still be wrong: the vendor re-bases a name's history on a split, and
sometimes serves it HALF-adjusted or flips a bar between bases from one pull to the next. The stored tape
then mixes two bases, and a basis cliff reads as a +1,800% breakout. The nightly pass scans the basket's
stored tapes for it (`pipeline/split_basis.py`; the rule, its thresholds and the measured precision are in
`FEED_LOOP.md` §the tape monitor, part 2) and `/admin/status` carries the result as `split_defects`, read
from the newest live artifact whose scan ran, from a pass that FINISHED (M1) — a `--catch-up` that skipped
theses (M2) counts, since the scan reads the whole basket union; the same pass the next night's diff
reads — artifact-sourced like the stopped-feed panel, so the surface still owns no tables:

- **One row per name per kind**, each with its `factor` (the step: 20 for a 1-for-20 split), its `dates`
  (the affected bars, at most the 12 newest; `events` is the full count) and its `kind`:
  - **`island`** — a run of bars on another basis than both neighbors (the half-adjusted shape: VWAV's
    09-15 / 09-16 unadjusted between adjusted bars);
  - **`cliff`** — a lone UP step whose price stays up and volume stays down by about the same factor: a
    reverse split the history before it never got;
  - **`flap`** — a stored bar whose versions changed basis in both directions (the vendor flipped it and
    flipped it back), the latest flip within 90 days.
- **`newly_flagged`** are the labels that PAGED that night — new since the last finished pass that scanned
  (a catch-up included), keyed on the kind and the security, so a standing defect never re-pages, not even
  across a skipped-thesis catch-up; the run row carries them too (`split_defects_new`, a
  `problems` line with the benign marker).
- **`error`** is set when the newest live pass's scan could not run; `defects` then still shows the last good
  inventory, and its `asof` / `ran_at` say which pass that was. The self-audit fails on a scan that could not
  run (an alarm), and **passes with defects present** — it reports the count (`split_defects_flagged`).
- **`null`** until a pass has scanned (the quiet state right after deploy, and for a `--no-live`-only
  history).
- **It never changes the verdict.** A defect is a vendor tape problem, flag only: nothing it finds reaches a
  detector, a call or a stored bar. What to do with a flagged name (treat its price triggers with suspicion;
  whether detectors should abstain on it is an open decision, plan Q10) is yours. The Admin page renders the
  rows under "Record freshness", loud, only when there is at least one, with a "NEW — paged on this pass"
  marker on the rows that paged.

This is the **same staleness the Scoreboard shows** (Slice 2, `SCOREBOARD.md`) — one contract
(`pipeline/schedule.py`), two surfaces — both now feeding it `domain/market_time.market_now()` (an explicit
`ZoneInfo`) rather than an ambient `datetime.now()`. *(Earmark, still open: `schedule.py` remains the second
home of the Mon-Fri + `RUN_AT` contract alongside the shell's sleep-loop. `market_today()` did **not** unify
them — it answers "what day is it in market time" and does no trading-calendar logic; the remaining work is
shrinking the shell to a dumb trigger. See `ROADMAP.md` "what's next".)*

## Cron health — "did last night's run work?"

The one-word `cron.status` verdict, plus the last run's counts and any problems:

| Verdict | Meaning |
|---|---|
| `never_ran` | no run-of-record artifact yet — run one below, or bring the `cron` sidecar up |
| `unhealthy` | the last run **froze / errored / totally failed / could not refresh the benchmark tape / failed its self-audit**, or a status-level alarm is up (`cron.alarms`: unknown or mismatched `code_sha`, the LOG notifier, an unset User-Agent, a stale nightly backup) — as loud as `stale`, so a bad run can't hide behind green |
| `stale` | the record missed an expected scheduled run (freshness above) |
| `gappy` | the edge is current and the last run clean, but a night inside the last `ALPHADECK_ADMIN_MISSED_WINDOW` scheduled runs has **no call-of-record** — a run fired on the wrong day (the hole check above) — or is **PARTIAL** (M1: some thesis in that night's roster has no on-time row — a pass killed mid-loop); the detail names the dates, and each partial night as "4 of 12 theses" |
| `running` | (M1) a pass is in flight inside the sidecar's run timeout and nothing else is wrong — quiet, never an alarm; its own night is not judged until it finishes |
| `healthy` | the last run is clean, the record is current, and the window has no holes |

Priority: `never_ran` > `unhealthy` > `stale` > `gappy` > `running` > `healthy` (a stale edge with a hole
reads `stale` — the louder, more actionable verdict — while `record.missed_asofs` still lists both).

**A killed or partial pass never reads healthy (M1, 2026-10-16).** MEASURED on 2026-10-12: the 22:30 pass
died at ~22:42 when the host VM went down, 4 of 12 theses had rows, there was no artifact, and this page read
**healthy for ~18 h** — the hole check credited the night because ONE row landed after `RUN_AT`, and
`last_run` showed the previous night's artifact. Now:

- **The pass's artifact is written at its START** (`status: "running"`) and finalized at its end
  (`"finished"`); an exception re-marks it `"crashed"`, a hard kill leaves `"running"`. `last_run` is the
  newest FINISHED pass (an artifact from before M1 has no status and reads finished), and
  **`unfinished_run`** describes a newer one that has not finished (`status`, `started_at`, `asof`,
  `run_kind`, `past_timeout`, `timeout_s`, `error`). The run history (`/admin/runs`) lists finished passes
  only — an unfinished one has no outcome to show, and is the status alarm's to report.
- **"The last pass did not finish (started …)"** is a `cron.alarms` line when the newest artifact still reads
  `running` past the sidecar's whole-pass ceiling (`ALPHADECK_CRON_RUN_TIMEOUT_S`, default 5400 s — the same
  host var the sidecar's C2 `timeout` uses, mirrored to the backend by compose as
  `Settings.cron_run_timeout_s`); a `crashed` one alarms at once with its reason. Inside the timeout the
  verdict is `running`, not an alarm. An Admin "Run daily now" pass is bounded by the SAME value (since
  2026-10-17 — see "Run daily now" below): past it the pass is stopped and its artifact reads `crashed`
  ("ran past the run timeout"), so the alarm names what happened instead of waiting for a pass to land.
- **Per-thesis coverage — `record.partial_nights`.** Every window night (not already in `missed_asofs`) is
  judged PER THESIS by `pipeline/night_coverage.py` — the module the nightly self-audit's `on_time_rows`
  check uses, so "covered" has one definition: a **non-reconstructed** row recorded at/after that night's
  `RUN_AT`. A night is PARTIAL when some thesis in its ROSTER is neither covered NOR reconstructed — a
  thesis `pipeline.backfill` reconstructed is **resolved** (operator decision, 2026-10-16: the backfill is
  the answer to that hole), so a fully backfilled night is not partial and never an alarm; it is listed
  quietly in **`record.reconstructed_nights`** (`{asof, reconstructed, expected}`) and on the freshness card
  as a quiet "backfilled" line, so it stays visible while a NEW miss stands out. (The self-audit keeps the
  strict rule — a reconstructed row never counts for it — because it judges the night its own pass just
  ran, where no backfill exists.) The roster is the theses **created
  before** the night's `RUN_AT` instant and **not archived before** it — point in time, so archiving a thesis
  never hides a night it missed (known limit: `archived_at` is one stamp, so a thesis archived and later
  restored reads as expected on the nights it sat archived). A thesis created after 22:30 was never in that
  pass's walk and is not owed the night. Each partial night is `{asof, covered, expected,
  reconstructed_only, daytime_only, late}` — `late` counts covered theses whose row landed only after the
  night's on-time window (the next weekday's 06:00 ET EDGAR open); such rows still COVER the night here (the
  pass recorded it), while the Scoreboard never SCORES them (the late-row policy L1 — `SCOREBOARD.md`
  §"Late-recorded rows"). The page lists them on the freshness card
  ("2026-10-12: 4 of 12 theses") only when there is one.
- **A recorded night with NO run artifact at all** (post-`RUN_AT` rows for a night newer than every
  artifact's as-of) is a `cron.alarms` line too — a pass that recorded and died before its start artifact
  existed (the pre-M1 shape), or rows written outside the daily pass. Like the backup alarm, only on a stack
  whose run history shows the cron sidecar: dev's `calls` table is a copy of prod's while its artifacts are
  only dev's own hand runs, so there every night would read "no artifact".

**Reconstructed rows — quiet in both checks.** After a backfill the hole clears: `missed_asofs` counts a
reconstructed row (above), and `partial_nights` treats a reconstructed thesis as resolved. A partial night
that ALSO has backfilled theses still names them (`reconstructed_only`, "N reconstructed only" in the
detail) — the night is partial only because of the theses with no row at all. The reconstructed rows stay
what they are everywhere else: reported, never scored (the Scoreboard filters them).

The verdict re-derives via `assess_health` over the newest **readable** run-of-record artifact
(`data/cron_runs/`, skip-unreadable fail-open). The **benign** `--no-live` cache-only note is excluded from
the alarm set, so a hand-run dev pass never paints the cron `unhealthy` (honest loudness). A **`--catch-up`**
pass (the sidecar's boot / late-wake catch-up) is re-read with the freeze check skipped, exactly as the run
itself was assessed — it runs inside the EDGAR 12h TTL and legitimately fetches ~0 — so the history never
shows a catch-up as unhealthy; its row carries a small **catch-up** tag (`AdminRunOut.catch_up`).
`GET /admin/runs` returns the run history — the last N artifacts parsed, newest first.

A **newly stale feed** — a price tape (G5a) or an ETF sleeve's fund-shares sampling (F1) — also appears in
`problems`, one line per feed with that feed's own count, names and repair, but carrying the same benign
marker as the `--no-live` note: it never makes the verdict `unhealthy`, because it is a feed gap, not a cron
fault (see "Stopped feeds" above).

**A night that errored on some theses but still recorded is no longer a failed run to the scheduler**
(F4, 2026-09-13). Nothing on this page changes — the errors still show in `problems` and the run row, and
the page still pages — but `pipeline.daily` now exits non-zero only when the night has **no**
call-of-record at all, so the sidecar stops pausing 20 minutes to retry a night that recorded 11 of 12.
A "Run daily now" that errors on a thesis reports exactly as before.

**Phase 1B — "a bad night is never silent" (2026-10-16).** Four more things now reach this card, each read
from the run artifacts or the backups dir (the surface still writes nothing):

- **The nightly self-audit (V1).** Each pass ends by checking the record it just left
  (`pipeline/self_audit.py`) and writes `audit: [{check, ok, detail}]` into its artifact; `last_run.audit`
  shows it. Phase-1 checks: every active thesis has a non-reconstructed row for the as-of recorded at/after
  that night's `RUN_AT` (counted in the `calls` table), the pass's `code_sha` is known, and every thesis the
  pass started finished. A **failed** check is an alarm (`SELF-AUDIT FAILED — …` in `problems`, the verdict
  `unhealthy`, and it paged); `ok: null` is **not applicable** (a daytime or `--no-live` pass) and is never
  read as a pass. **T4 adds `split_defects_flagged`**: the split-basis scan ran over tonight's tapes, so every
  defect it can see is flagged — it reports the count, passes with defects present (there is no
  "acknowledged" state, and failing on a standing defect would page nightly), and fails only when the scan
  could not run.
- **Per-name ingest errors (O1).** The artifact now keeps WHY each name errored, one row per failed leg.
  Names whose leg started failing since the last-known inventory (each thesis as the newest live pass that
  covered it saw it — a skipped-thesis catch-up counts for the theses it covered, so a name it first flagged
  is new there and only there) show as `name_errors_new` and a `problems` line with the **benign** marker
  (the thesis still recorded — not a cron fault); the known-failing ones are the quiet `name_errors_known`
  count.
- **Status-level alarms (`cron.alarms`, I1/I2 + B4)** — each makes the verdict `unhealthy`: the cron's last
  pass ran with an **unknown `code_sha`**; the cron's sha **differs** from this backend's (`cron_sha` vs
  `backend_sha` — compared only for a cron pass that started after this backend did, so a full deploy is not
  a false alarm until the night's pass confirms it); the last live pass paged through the **LOG notifier**
  (`SLACK_WEBHOOK_URL` unset) or ran with **`ALPHADECK_USER_AGENT` unset**; the **newest unlabeled DB dump is
  older than `ALPHADECK_BACKUP_STALE_HOURS`** (default 30) in *schedule* time — the dump for the last
  expected night is due by that night's `RUN_AT` + 6 h, so a Friday dump is current all weekend. The backup
  alarm applies only on a stack whose run history shows the cron sidecar (dev runs cron OFF). An artifact
  written before these keys existed raises no alarm by absence.
- **A crashed pass pages (H3).** An unhandled exception in `run_daily_pass` sends ONE health page
  (`PASS CRASHED — …`) before it re-raises; since M1 its start artifact is also re-marked `crashed`, which
  this page reads as an alarm (above).

A **failed benchmark refresh** is one of the alarms (G4). The SPY/IWM tape is a *shared* call-logic input
(`benchmark_rs`), refreshed by a fail-open passenger leg before the per-thesis loop; its faults used to reach
stdout only, so a night could produce calls against a **stale** tape and still read green. Two problem lines,
reported distinctly because they are different news: the leg **failing outright** (nothing refreshed) and
**N individual benchmark pulls** failing (a partly stale tape). The counts are written into the artifact, so a
night that paged re-reads as `unhealthy` in the history forever; an artifact written before this shipped reads
clean, never broken.

## Run daily now — the one trigger

`POST /admin/run-daily` **kicks a background job** and returns immediately (**202** + `job_id`); poll
`GET /admin/run-daily/jobs/{job_id}`. It runs the cron's **exact** unit of work (`run_daily_pass` — live
ingest → call-of-record → the run-log artifact → the health page), so a manual run **lands in the run
history like the nightly one**. A **409** means a pass is already running **anywhere** (C1, 2026-10-16): the
pass holds a cross-process Postgres advisory lock for its whole run (`pipeline/run_lock.py`), the route probes
it before starting a job and names the holder (e.g. `alphadeck-pass:cron`), and the in-process single slot
still catches a double-click; **503** when the DB cannot be reached to check. (Before C1 the guard could not
see the sidecar's own pass — the 09-01 / 09-09 overlaps.) It does a **LIVE EDGAR pull** (~2 min warm, up to ~65 min on a cold cache) and is safe to re-click once
finished — the pass is idempotent (`record_if_changed` appends nothing on unchanged facts). **Safe at any hour
(G1):** it used to be a morning-only button, because it warmed every company's filing index for up to 12h and
the night's scheduled pass then served that warm index — blind to the afternoon's filings. The recurring
client now carries the **recurring TTL (five minutes)**, so this button *refreshes* the cache rather than
warming it and the night re-fetches regardless — anything read more than five minutes ago is re-pulled, and
nothing can be filed in the five minutes before the 22:30 pass (`FEED_LOOP.md` §Fresh data). The one thing
that still reads a warm cache is a pass fired **within five minutes** of another, which is why a back-to-back
re-click can legitimately report ~0 EDGAR fetches. The job opens its
own DB connection (it outlives the request); a lost job (server restart / expiry) shows "lost from view", not
an infinite spinner — the run history + record edge are the durable authority. **Bounded like the sidecar's
pass (2026-10-17):** the job runs the pass in a CHILD PROCESS (`pipeline/bounded_pass.py`) for at most the
run timeout — `Settings.cron_run_timeout_s`, the backend's mirror of the sidecar's `RUN_TIMEOUT_S`, both
fed by `ALPHADECK_CRON_RUN_TIMEOUT_S` (default 5400 s), so there is one value. Past it the child is stopped
the way `timeout -k 60` stops the sidecar's (SIGTERM, SIGKILL 60 s later); its death ends its Postgres
session, which releases the run lock (a hung pass can no longer hold it — and so block every later night —
for as long as the backend lives); its start artifact is re-marked `crashed` with the reason, so the status
alarm says "ran past the run timeout" at once; ONE crash page goes out; and the poll fails with the same
words. A pass process killed from outside (the OOM killer) is marked and paged the same way, with its exit
code. A thread cannot be stopped from outside, which is why the bound needs the process. A manual pass that starts
**before** that night's `RUN_AT` (a pre-open click, on the prior session's bars) does **not** satisfy the
sidecar's `--catch-up` guard — the night's post-close pass still runs (2026-09-10; on Sep 9 two 09:09 / 09:15
ET passes had masked a missed post-close pass, and the record stayed a pre-open call).

## Backups — the DB-snapshot safety net (Slice 4)

The operator-facing view of the backup net; the **mechanics live in `FEED_LOOP.md` §Backups & restore** (the
runner, retention, the host bind, the client version) — this section is the surface, not the source.

- **Create + list.** `POST /admin/backup` kicks a background job (**202** + `job_id`, poll
  `GET /admin/backup/jobs/{job_id}`, **409** single-slot guard) that shells `pg_dump` — **READ-ONLY**, opens
  no app connection and mutates no row — to `./data/backups/alphadeck-<UTC>[-<label>].sql` (a host bind, so a
  dump is copyable off-box). `GET /admin/backups` lists them newest-first, and `/admin/status` carries the
  **last-snapshot age**. Same operator-initiated discipline as *Run daily now* (fires only on the click; the
  list + age are reads).
- **Retention = keep-last-N unlabeled; labeled "90 days, or the last 20".** After a successful dump the
  newest `ALPHADECK_BACKUP_KEEP` (default **7**) **unlabeled** snapshots are kept and older unlabeled ones
  pruned. A **labeled** dump (e.g. `pre-migration`) is deleted only when it is both older than 90 days and
  outside the newest 20 labeled dumps (Q16; the dials and the `--prune-dry-run` listing are in
  `FEED_LOOP.md` §Backups & restore). A failed dump never prunes (it must not shrink the safety net).
- **Nightly, too.** `scripts/daily_cron.sh` runs `python -m pipeline.backup` right after the scheduled
  `pipeline.daily` (weekday branch), fail-open — deliberately **not** folded into `run_daily_pass`, so a
  manual "Run daily now" does not also dump. Since M3 (2026-10-16) the sidecar's boot also runs an
  age-gated `pipeline.backup --if-older-than-h 20`, so a night caught up at boot gets a snapshot too, and
  a failed backup pages from the shell (`FEED_LOOP.md` §Backups & restore).
- **RESTORE is CLI-only — never a button.** A restore is destructive (drop-schema + reload) and belongs in
  human hands. The documented sequence (the one actually used on 2026-07-21):
  `docker exec -i alphadeck-postgres-1 psql -U alphadeck -d alphadeck < ./data/backups/<file>`.

**Why the net exists (the honest note).** On **2026-07-21** a shared-Postgres pytest hazard **truncated the
whole demo DB**. Recovery worked **only** because an ad-hoc `pg_dump` happened to exist — it was a **real
restore** from a 2026-07-17 snapshot (all six theses and the real call-of-record, migrations `0021→0024`),
**not** a synthetic rebuild; a forward `daily` run then re-armed one of the restored theses. This slice turns that lucky
ad-hoc into a one-click **and** nightly net (and #217 removed the root cause — a fail-closed guard that
refuses to truncate any non-`alphadeck_test` DB). Full account of the truncation hazard: the operator memory;
the freeze it is adjacent to: `POSTMORTEM_CRON_FREEZE_2026-07.md`.

## The endpoints

| Endpoint | Shape | Notes |
|---|---|---|
| `GET /admin/status` | freshness + cron verdict + last-snapshot age + the stopped-feed, split-basis and price-feed-repairs panels | read-only; the page opens on it |
| `POST /admin/price-feed/{security_id}/adopt` | `{symbol}` → the change row | a symbol EDGAR lists for a renamed name; 409 otherwise / nothing to change |
| `POST /admin/price-feed/{security_id}/set-symbol` | `{symbol}` → the change row | ONE vendor fetch validates first (no bars → 422, nothing written) |
| `POST /admin/price-feed/changes/{change_id}/undo` | → the undo row | exact restore; 409 already undone / a later change stands / the master moved |
| `POST /admin/price-feed/{security_id}/acknowledge` | `{reason?}` → the acknowledgment | unpriceable names only (409 otherwise); idempotent |
| `POST /admin/price-feed/acks/{ack_id}/revoke` | → the revoke row | 404 when no active acknowledgment |
| `POST /admin/price-feed/{security_id}/adopt-cik` | `{cik}` → the CIK change row | the detector's recommended successor only (409 otherwise / master moved / CIK held by another row); no network |
| `POST /admin/price-feed/cik-changes/{change_id}/undo` | → the undo row | exact restore; 409 already undone / a later change stands / the master moved |
| `GET /admin/runs?limit=` | the run-of-record history, newest first | read-only file parse; skip-unreadable |
| `POST /admin/run-daily` | **202** + `job_id` | the cron's exact unit, in a child process bounded by the run timeout; 409 when any process runs a pass (C1 lock) or the slot is busy; 503 DB unreachable |
| `GET /admin/run-daily/jobs/{job_id}` | job status → run-history row | 404 → "lost from view", never a spinner |
| `POST /admin/backup` | **202** + `job_id` | read-only `pg_dump`; 409 single-slot |
| `GET /admin/backup/jobs/{job_id}` | job status → `BackupOut` | 404 → "lost from view" |
| `GET /admin/backups` | the snapshot list, newest first | read-only directory scan |

## What it deliberately is NOT

- **Not the scheduler.** The `cron` sidecar fires the nightly run (`FEED_LOOP.md`); this surface only
  **triggers on demand** and **observes**. It does not change the schedule.
- **Not a restore surface.** Restore stays a documented CLI act (above) — destructive, human-only.
- **Not the dead-man's switch.** A run that never happens produces no artifact → no page (a known gap:
  `restart: unless-stopped` covers crash/reboot, not a deliberate `stop`). A *persistent* absence needs an
  **external** heartbeat — the sidecar can't page about its own absence. See `FEED_LOOP.md` "Known gaps".
- **Not the replay-regenerate button.** Regenerating the Scoreboard's replay artifact stays out of scope,
  deferred to the replay-panel work (`ROADMAP.md`).
