# Post-mortem — the 11-day EDGAR cache freeze (2026-07)

> **This is a POINT-IN-TIME record. Do NOT maintain it.** It captures what happened and what was true
> **as of 2026-07-17**. Do not edit it to reflect later state — that would rebuild, in miniature, the exact
> doc/code divergence this whole episode was about. When an open item below is resolved or a fact changes,
> update the **living homes**, never this file:
> - **What the cron IS** → `FEED_LOOP.md`, `DATA_SOURCES.md:45–58`, `DATA_FLOW.md`.
> - **The open correctness items** → `FEED_LOOP.md` ("Known gaps"), `INVARIANTS.md`.
> - **The conventions/lessons** → `CLAUDE.md`, `INVARIANTS.md`.
>
> This file exists so the *evidence trail* survives in git — because the sentence that started this
> investigation was "unfalsifiable — indistinguishable from *we stopped looking*," and the numbers below are
> what killed that. The forensics were derived in the (gitignored) working notes
> `docs/temp/cron-research-2026-07-17.md` + `docs/temp/thaw-boundary-2026-07-17.md`; this is the durable distillation.

---

## TL;DR

For ~11 days the daily call-of-record cron ran "successfully" every night while **seeing no new insider
filings for any name it had already cached.** The EDGAR client was cache-first *forever*: once a name's
`submissions` index was on disk, it was never re-fetched, so a Form 4 filed after the cache date was
invisible. Every night's run therefore recorded a call built on frozen insider data — with **fact tallies
(0 appended) identical to a healthy nothing-filed night**, so nothing looked wrong.

- **The fix (R1, #196):** the EDGAR cache is now **key-classed** — `forms/<accession>/<doc>` is immutable
  (cached forever), every other prefix (`submissions`/`companyfacts`/`efts`) refreshes on a **12h TTL** when
  live. Structural, on the cache-key prefix; no caller threads a flag.
- **The observability that would have caught it (R2–R6, #197–#200):** a `live_fetches` freeze detector, a
  two-condition recording gate, a per-run log, a health pager, and catch-up-on-boot.
- **Outcome:** the freeze is dead (a real run pulled **18,190** live EDGAR requests where a frozen run pulled
  0), and the North Star was reached — a fresh thesis (Rainbow Rush) **armed on real insider + price data**.

## Timeline / narrative

1. **The trigger.** The operator: *"Rainbow Rush has no insider buys — that doesn't seem right."* It didn't,
   and the trail led not to the detector but to the cache underneath it.
2. **The root cause.** `EdgarClient` served any cached key forever. `submissions/*` (the per-issuer filing
   index) is *mutable* — a new Form 4 adds to it — but was treated like an immutable document. So the daily
   cron, re-reading the cached index, could not see a filing newer than the cache.
3. **Why it hid.** A frozen index and a genuinely quiet day produce the *same* result: 0 new facts appended.
   With no signal for "did we even reach out to the network," the two were indistinguishable.
4. **The mask (#125).** This freeze was **latent for far longer** — earlier, every `docker compose up --build`
   *wiped* the cache, which was an accidental refresh. PR #125 (persist the cache across rebuilds on the
   `appdata` volume) was correct and desirable — and it **removed the accidental mitigation**, turning a
   latent bug into a permanent one. Latent bug + accidental mitigation + fix-the-mitigation = the bug arrives.
5. **The fix + the instruments (R1–R6).** R1 made freshness structural; R2–R6 built the observability so a
   recurrence is *visible* (see "What the cron now is").
6. **The recovery.** A live catch-up thawed the cache (18,190 fetches). A downstream TZ bug (below) mislabeled
   the run's date; restarting the sidecar fired a **cache-served** correction (1 fetch, 53 s) and the record
   landed on the right trading day.

## What the cron now is (R1–R6 — details in the living docs)

- **R1 — key-classed EDGAR cache TTL** (#196). `forms/` immutable; `submissions`/`companyfacts`/`efts` on a
  12h TTL when `allow_live`. → `DATA_SOURCES.md:45–58`, `client.py`.
- **R2 — recording gate** (#198). A run **withholds** the call-of-record on total ingest failure or a
  `--no-live` run, and stamps `ingest_fresh`/`ingest_errors` on the `calls` row. → `FEED_LOOP.md`.
- **R3 — run-of-record log** (#197). One JSON per run under `data/cron_runs/*.json` (incl. the per-thesis
  `edgar_fetches` freeze counter). → `FEED_LOOP.md`.
- **R4 — health pager** (#199). `assess_health` pages on freeze / withheld / errors (Slack, fail-open). The
  freeze predicate is `frozen = allow_live and theses > 0 and edgar_fetches == 0`. → `FEED_LOOP.md`.
- **R6 — catch-up on boot** (#200). The sidecar re-fires a missed run when it boots past `RUN_AT`, guarded by
  `already_ran_live(asof)`. → `FEED_LOOP.md`.
- **TZ pin** (#202). The backend container now sets `TZ` so a manual `pipeline.daily` agrees with the cron
  (container-scoped stopgap; see open item 3). → `docker-compose.yml`.

## Open items — as of 2026-07-17 (authoritative homes in the living docs)

These are recorded here for the narrative; the **living, updatable** statement of each is in the doc named.
Do not track their status in this file.

1. **R8-A — Form 4/A amendments (correctness gap, held).** An amended-away code-P buy still fires Key-1 (a
   false **positive** in the arm path): `accession` is in the `fact_insider_txn` natural key, so a 4/A is a
   distinct surviving row; `supersedes` is vestigial; the insider-conviction detector applies no amendment
   filter. The fix is non-trivial (dropping `accession` or naively ingesting the 4/A both double-count) —
   **measure first**, and it waits on the operator lifting the signal-change hold. → `INVARIANTS.md`.
2. **R4 false-positive (paging).** The freeze page fires on `edgar_fetches == 0`, but ~0 is *also* a healthy
   run entirely inside the 12h TTL (all cache hits). The 07-17 recovery run recorded `edgar_fetches = 1` —
   one fetch short of a false page. Nightly runs are safe (always outside the TTL); catch-ups / manual /
   second-runs are exposed — **the paths R6 made routine.** Two fixes: (A, correct) page on 0 only when the
   cache was *outside* its TTL; (B, simpler) restrict the freeze page to scheduled runs. → `FEED_LOOP.md`.
3. **`market_today()` — "today" is ambient (durable TZ fix).** `date.today()` reads the ambient timezone at
   **9** non-test sites. The TZ pin (#202) fixes the two containers, **not** a CLI run elsewhere — which is
   what caused the mislabel. The trading day is a *domain* fact, not an *environment* fact; the durable fix is
   a shared helper on an explicit `ZoneInfo`. → `INVARIANTS.md`.
4. **Missing-sidecar gap (dead-man's switch).** `restart: unless-stopped` restarts on crash, **not** after a
   deliberate `docker compose stop`. A run that never happens produces no run log and no page — identical to a
   healthy silent night. R4 pages a *bad* run, not an *absent* one; the last inch is an **external** heartbeat
   that alerts when today's log is missing. → `FEED_LOOP.md`.

## Lessons

1. **A recurring fetch must stay fresh — structurally, not with a flag.** The #72 lesson was learned on the
   *price* leg and written as a per-call `force_refresh=True`. The same bug class then bit the *EDGAR* leg,
   uncarried. The durable answer is **key-classed / default-refresh** freshness (a new mutable endpoint is
   safe-by-default), not a boolean the next author has to remember to thread. → `CLAUDE.md` convention.
2. **"It works when you test it" is the signature of this bug class.** The freeze demoed perfectly on any
   *new* name and was dead on every *old* one — new names/terms fetch fresh, so a spot-check on a fresh name
   always passes. (Third instance of this shape this week, alongside the two enrichment/discovery cases.) A
   test on a fresh entity cannot prove freshness for aged ones.
3. **#125 didn't cause the freeze — it removed the mask.** Cache-wiping rebuilds had been an accidental
   refresh; persisting the cache made a latent bug permanent. A correct change that removes an *accidental*
   mitigation surfaces the real defect — that is the change working, not failing.
4. **A structural guard firing is information, not an obstacle.** R6's `already_ran_live` returned `False`
   after a clean run and surfaced the TZ bug — a defect it was **never built to detect** — because it asks a
   question with a checkable answer. That is the argument for building the boring instrument: it pays off
   outside its brief. → `INVARIANTS.md`.

## Forensic appendix — the evidence trail

**The freeze, measured (DELL).** Cached latest Form 4 = **2026-06-30**; live reality = **2026-07-14** (a
filing the cron could not see); the `submissions` cache was written 2026-07-06 23:54 and never re-pulled.
Names agreed with reality only when they had filed nothing since their cache date (IBM, MU, NVDA).

**The blind cohort.** 1,477 cached `submissions` files, bucketed by cache date:

| cache date | names | days blind (at 2026-07-17) |
|---|---|---|
| 2026-07-06 | 423 | 11 (the original seed cohort) |
| 2026-07-13 | 272 | 4 |
| 2026-07-10 | 211 | 7 |
| 2026-07-17 | 200 | fresh |
| 2026-07-09 | 140 | 8 |
| 2026-07-08 | 117 | 9 |
| 2026-07-14 | 77 | 3 |
| 2026-07-11 | 39 | 6 |

**Post-R1 thaw (gate-2 proof).** A live ingest took AI Memory from **+0** insider txns (frozen) to **+583**;
**DELL advanced 2026-06-26 → 2026-07-10** (its 07-14 filing, +246 rows); the 07-06 cohort thawed **423 → 351**.

**The production catch-up.** `edgar_fetches = 18,190` (a frozen run was 0), `duration_s ≈ 4,170` (~70 min),
6/6 theses fresh, 0 errored, no health page — and **Rainbow Rush ARMED** (core_entry, 53 triggers,
`insider` + `technical_breakout`), the North Star.

**The recovery re-run (inside the TTL).** Cache-served: `edgar_fetches = 1`, `duration_s = 53.6 s` — one fetch
short of tripping the R4 false-positive freeze page (open item 2's evidence).

**The three duration-nights (a retracted "schedule drift").** 07-13 logged 22:30:01 ⇒ ~1 s; 07-14 23:00:39 ⇒
~30 min; 07-15 23:35:57 ⇒ ~65 min. The spread is *duration* (how long the run took), not the sidecar drifting
its start time — the loop re-anchors to the wall clock each iteration.

**The 3.04 s manual run.** All six theses logged `asof=2026-07-16` within 3.04 s (17:33:34.888 → 17:33:37.926
UTC = 13:33 EDT) — a manual `--no-live` (zero-network) run, not the 22:30 cron.

**The 1.64 s cron run (Source C).** The 07-13 22:30 cron (recorded 02:30:01 UTC) logged all 6 theses in 1.64 s
having ingested **0 insider facts / 24 price bars** — evidence that a total ingest failure fell through and
recorded anyway (the missing `continue`, since gated by R2).

**Amendments prevalence (for R8-A sizing).** Across the 250-name basket universe (cached submissions
2026-07-17): **1,126 Form 4/A · 1.8% of all Form 4s · on 162 of 250 names**. Amended code-P *buys* are a much
smaller, still-unmeasured subset.

**The ENDV stale-cover finding (distinct from the freeze).** The AUTO-shares auto-apply faithfully reproduced
a **2.5-year-old** cover count — **318,751,597 as of 2023-12-28** — with no age signal, because companyfacts
had nothing newer (ENDV dark since 2023). Spot-checking 4 of the 11 pre-R1 auto-applied counts against live
thawed companyfacts showed **0.0% delta each** — the harm didn't land here (mid-quarter timing luck), but the
mechanism (a plausible-but-wrong cap on a name the operator doesn't know) is real → R8 Part B (#201, the
stale-shares age flag).

**The TZ forensic.** The manual catch-up ran in the backend container (`TZ` unset → UTC); at ~02:xx UTC it
computed `date.today() = 2026-07-18` and recorded tomorrow's `asof`. The cron container (EDT) → the 17th.
`already_ran_live(2026-07-17)` returned **False** after a clean run — the guard surfaced the UTC/EDT split
(open items 3 + lesson 4).
