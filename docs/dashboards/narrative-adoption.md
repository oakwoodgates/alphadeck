# Narrative Adoption Curve

> Slug `narrative-adoption` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 170`.
> The operator approved this dashboard and the weekly EDGAR full-text count job it needs (2026-10-29, choice 3 of the
> research set); the choices the build makes that this plan does not spell out go under "Choices to confirm" for the
> operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`. The sixth page of the RESEARCH set, and
> the only one that needs NEW DATA: the job is a pipeline change in its own PR (§The job); the page reads its table.

**Label:** Narrative Adoption. **Description:** "How many filings mention a card's terms each month, over three years,
split by who files them: annual and quarterly reports, 8-Ks, or new-issuance paper, and new filers against repeat
filers."

It answers "how early or crowded is this narrative in disclosures": a deterministic measure of the operator's own edge.
A narrative that only 10-Ks mention is early; one that S-1s and 424Bs start carrying has promoters arriving. The curve
is counted by EDGAR full-text search over the card's **SIGNAL terms** (the operator's seeds, the discriminating ones;
BROAD terms are collision-prone and are not counted), per calendar month, and stored as a fact so the page is a read,
never a live search. Display only: it never feeds a call, never writes, never calls the LLM. Discovery itself is
untouched (`docs/DISCOVERY.md`): the job counts, it never places a name.

## The job (its own PR: `backend/pipeline/term_mentions.py`, a table, a weekly cron loop)

- **What is counted.** For each live card and each SIGNAL term in its persisted term set (`thesis.term_set`,
  `tier = signal`; 775 terms across the 62 live cards on 2026-10-29), one EDGAR full-text search per calendar month
  (`efts.sec.gov/LATEST/search-index?q="<term>"&dateRange=custom&startdt=<first>&enddt=<last>`, the same endpoint,
  client, cache, User-Agent and global budget the discovery walk uses: `ingest/edgar/fulltext.py`, `ingest/sec_budget.py`,
  lane `recurring`). One response gives everything the row needs: `hits.total.value` (documents), the `form_filter`
  aggregation (documents per form type), the `entity_filter` aggregation (filers, top 30 buckets) and the first page
  of hits with each document's `ciks`, `form`, `file_date`, `adsh`.
- **The stored row** (`fact_term_mentions`, one per (thesis, term, month); append-only; `valid_from` = the month's
  last day, `recorded_at` stamped now; natural key (tenant, thesis, term, month)): `documents` (the total), the
  per-form-group counts derived from `form_filter` (**reports** = 10-K, 10-Q, 20-F, 40-F, 6-K and their amendments;
  **events** = 8-K and amendments; **issuance** = S-1, S-3, F-1, F-3, 424B*, FWP, S-11 and amendments; **other** = the
  rest, so the groups sum to the total), `filers` = the distinct CIKs in the hits (exact when `documents <= 100`, else
  "at least" the distinct CIKs of the first page, with `filers_sampled = true`), `new_filers` = filers not seen for
  that term in any earlier stored month, and the request's `fetched_at`.
- **Request budget (PROPOSED, to be measured on the first run):** the backfill is 36 months × 775 terms = 27,900
  requests, about 55 minutes if the lane runs alone at the 8.5/s budget and about two hours sharing it
  (MEASURED by the job's dry run on a copy of dev's 62 live cards, 2026-10-29: 746 distinct terms, so **26,856**
  requests, and **1,492** per weekly run after it; the run time is still to be measured on the first run); it runs ONCE,
  on a weekend day outside the nightly's window, and is resumable (a stored month is never re-fetched). The weekly
  run re-fetches only the current month and the one before (a filing accepted late settles the prior month):
  about 1,550 requests, in the cron sidecar's weekly loop beside the company-list refresh (`scripts/daily_cron.sh`,
  Saturday, after `master_refresh_loop`), with its own kill switch env. A term added to a card later gets its 36
  months on the next weekly run. A term removed stays stored (history is never deleted) and the page shows the
  card's CURRENT terms.
- **Idempotency is proven by counting the table** (the repo's rule), and a re-run after a finished month changes
  nothing. Honesty rules: a failed request leaves the month unstored and listed in the run report (never a zero);
  a 429 pauses the lane like every other SEC client; the run writes a summary line and a `data/term_mentions/`
  artifact the Admin page can show (no `data_repair` journal row: that journal starts a Scoreboard editions repair
  trial, and this is ordinary display data flow).
- **The read:** `PointInTimeData.term_mentions(thesis_id)` as a THESIS-scoped DISPLAY accessor (like
  `theme_conviction_facts`; not on the detectors' protocol), gated by `valid_from <= asof` and the record clock's
  `recorded_at <= known_at`: at a past as-of the curve ends at the last month that had closed by then.

## The page (this dashboard; built after the job's PR merges)

One card at a time (the shared picker, `baskets.picker_cards`). Three panels over one 36-month axis ending at the
as-of's month:

1. **Documents per month** as bars, stacked by form group (reports, events, issuance, other), summed over the card's
   SIGNAL terms (a document matching two terms counts twice; the page says so). A hover shows the month, each group's
   count and the terms that contributed most (the top three by documents).
2. **Filers per month** as a line (distinct CIKs summed over terms, "at least" marked where any term was sampled),
   with **new filers** as a second, thinner line.
3. **The Narrative Index overlay:** the card's equal-weighted basket line against SPY from the Narrative Index's shared
   series math (`dashboards/series.py`), on the same axis, so the curve can be read against the tape. Flagged price
   histories are left out of the line and named (`baskets.price_flags`, `FlaggedNote`).

Below the panels, **the term table**: one row per SIGNAL term with its documents in the last month, the last three
months and the last twelve, its filers, and the month it first appeared in the stored window; sorted by last-month
documents; a term with no stored months reads "not counted yet" (a term added since the last weekly run), never zero.

**Loudness:** no badges, no colors by group beyond four muted fills; no "early" or "crowded" label on the page. The
reading is the operator's: the page shows which forms carry the terms and whether the filer count is widening.
Neutral wording (rule 3).

## The quiet "show the work" line

The card and the as-of; the clock; SIGNAL terms counted / terms not counted yet / BROAD terms left out; months stored
of 36; the last weekly run's date and the months it refreshed; sampled months (filers "at least"); the double-count
note; the Narrative Index's names priced / flagged; the cache state.

## Cache

`dashboards.cache.cached(conn, "narrative-adoption", (str(thesis_id), clock), asof, ...)` holds one card's 36 months,
its term table and its index line. The cache clears on a new run or a basket edit (rule 7); the weekly job's run is a
"new run" for the cache's generation token.

## Backend and frontend

- Job PR: `backend/db/migrations/0071_fact_term_mentions.sql`, `backend/pipeline/term_mentions.py` (the CLI: `--backfill
  --months 36`, `--weekly`, `--thesis`, `--dry-run` default, `--no-live` for the cache), its `daily_cron.sh` loop and
  env kill switch, the `PointInTimeData.term_mentions` accessor, `backend/tests/pipeline/test_term_mentions.py` (the
  form grouping on a recorded EFTS response fixture, the filer rule exact vs sampled, new filers, idempotency by
  COUNTING the table, a failed month left unstored and reported, the accessor's two-clock gate), and a
  `docs/FEED_LOOP.md` paragraph. The first backfill runs on prod with the operator's go (the cost thread: cost is the
  operator's to spend, never ambient); the numbers it measures replace the PROPOSED estimate above.
- Page: `backend/dashboards/narrative_adoption.py` (pure: the monthly axis, the group stacking, the filer and new-filer
  lines, the term table, the overlay alignment), `backend/app/routers/dashboards/narrative_adoption.py`
  (`GET /dashboards/narrative-adoption/cards?asof=` and `GET /dashboards/narrative-adoption/{thesis_id}?asof=`),
  `backend/tests/dashboards/test_narrative_adoption.py` (the stacking on hand-built rows, the double-count note, the
  sampled flag, the term table, a month stored after a past as-of invisible), `frontend/src/dashboards/
  narrative-adoption/` (`entry.ts` order 170, an inline-SVG page with a pure layout helper, hooks, CSS, tests).
- **Layout:** the research set's document-like look; three stacked panels on one axis, the term table below; on a
  phone the panels stack and the table scrolls sideways.

## Known limits (stated on the page too)

- **Counts are documents matching the term's full-text query**, SEC's own index: an exhibit and its filing may both
  count; a term inside a boilerplate risk factor counts like a term in an MD&A paragraph.
- **A document matching two of the card's terms counts twice** in the card's sum; the term table counts each term alone.
- **Filers are "at least" where a term-month has more than 100 documents** (only the first page's CIKs are read).
- **SIGNAL terms only;** a card's curve changes when the operator edits its term set, and the stored history keeps the
  old terms' months.
- **The weekly cadence:** the current month is partial until the first weekly run after it closes.
- **EDGAR full-text search indexes 2001 onward** and its relevance to a month is by `file_date`.

## Out of scope

Any "early" or "crowded" score; counting BROAD terms; a cross-card ranking of narratives (a later page, once the table
has months); any change to discovery; any write from the page.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
Pre-decided here for the operator's review: SIGNAL terms only; one request per term-month; the four form groups;
filers exact only under 100 documents; the weekly refresh of two months; the 36-month window.

**Made by the job's build (2026-10-29; `pipeline/term_mentions.py`, migration 0071):**

- **`filer_ciks` is stored** on every row (the CIK set `filers` counted), so a later month's `new_filers` is
  computed from stored data, never re-fetched.
- **`new_filers` is NULL for a term's first stored month** (no earlier month to compare with), never "all new".
  Earlier = every stored month before it, the newest version each, including months stored earlier in the same run.
- **Filers are also exact by the entity aggregation:** when `entity_filter` lists every filer
  (`sum_other_doc_count = 0`) and every key carries its CIK, the union of the page's CIKs and those keys is the
  whole set, even above 100 documents (MEASURED: on all 72 cached replies where both rules apply, the two CIK
  sets are identical; on the 106-document "classic psychedelics" reply the page shows 8 filers, the aggregation 9).
- **A capped total counts from the form aggregation:** when EFTS reports `hits.total` as "gte 10,000", `documents`
  is the `form_filter` aggregation's total (its buckets + `sum_other_doc_count`), which is not capped (MEASURED:
  equal to the exact total on all 126 cached exact replies). A capped month is rarer than the plan assumed:
  MEASURED, "artificial intelligence" in August 2026 matched 5,706 documents (an exact total).
- **Issuance includes the variants:** S-1 / S-3 / F-1 / F-3 / S-11 with `/A`, MEF and ASR (S-3ASR, F-3ASR,
  S-1MEF ...), every 424B*, FWP. 8-K12B, S-4, 424H, DRS and POS AM sit in other; so do the retired small-business
  forms (10-KSB, SB-2), which cannot occur in a 36-month window.
- **The groups are EFTS's aggregation counts** over `root_forms` (an amendment rolls into its form). Above the
  top 30 forms they are Elasticsearch's per-bucket counts, which it marks approximate
  (`doc_count_error_upper_bound`); whatever they leave lands in other, so the four still sum to the total.
- **Store-on-diff:** a re-fetched month appends a version only when a value changed; a re-run appends nothing.
  A stored month whose `new_filers` changes because an earlier month was filled after it (a larger `--months`
  later) is re-derived from the stored CIK sets: a new version, no request.
- **Deferral after a failed month:** when a month that is not stored fails, the card-term's later months are held
  back (listed as deferred) so `new_filers` is never computed across a gap; they are written on the next run that
  fills the gap. A failed month that IS stored keeps its older version.
- **The weekly empty-table guard:** `--weekly` with no stored row for the tenant is a no-op line naming the
  backfill's cost, so the Saturday loop can never start the ~hour-long first backfill on its own.
- **Cross-card de-duplication:** cards sharing a term (compared case-insensitively) share its request; each card's
  row stores its own spelling, and `source_ref` is the URL actually requested. A card's own duplicate spellings
  count once. A term whose spelling the operator later edits (case included) is a new term to the job (its 36
  months are fetched again); the old spelling's months stay stored.
- **Saturday 10:00 market time** for the weekly loop: after the 09:00 company-list refresh (bounded at 900 s),
  far outside the nightly window. A run refused or stopped (exit 75) retries hourly, at most six times.
- **The page's month boundary:** `valid_from` is the month's last day, so on the live read the current
  (partial) month appears only from its last day; until the first weekly run after it closes it reads partial.
