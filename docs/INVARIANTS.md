# INVARIANTS.md — the load-bearing rules, in one place

> Repo path: `docs/INVARIANTS.md`. `CLAUDE.md` states the **product** invariants (advisory-only, thesis-is-
> the-spine, opinionated-on-timing, inverse-loudness, …). This file states the **implementation** invariants
> that those depend on — the ones that were load-bearing in practice but lived only in our heads and in
> scattered code comments. A change that violates one of these is a bug, not a trade-off. Each names where
> it's enforced so it can't quietly rot.

---

## 1. No model-sourced numbers or firings  (CLAUDE.md invariant #3)

The LLM **augments, never sources.** It may draft the `counter_case` / explanatory prose citing existing
evidence IDs, a grounded plain-English explanation of an extracted FLAG candidate, or a value-chain DRAFT —
segments + names + thesis-fit prose — from a narrative (the two Workbench seams, below). It must **never** fire
a trigger, set a state / verdict / grade, or invent a number. Every trigger and grade comes from a
**deterministic parse** of data **or** a **one-time operator ratification**.

- *Enforced by:* the assembler signature (the LLM hook only injects `counter_case`); detectors are pure
  `f(point_in_time_data) -> SignalEvent`; the catalyst grade is `_derive_grade` (deterministic) or set at
  ratification — see `ingest/doe/feed.py`, `ingest/catalyst.py`, `calls/assembler.py`. The **theme
  conviction**'s grade + horizon are operator inputs on the ratified fact (`ingest/theme_conviction.py`),
  never the model's.
- *Also enforced by (the first LLM seam — the flag-explanation drafter, `backend/llm`):* the explanation
  rides a **separate rail** — `POST /workbench/facts/explain` has **no DB connection, writes nothing, and is
  never a field on `RatifyFactRequest`** — so it **structurally cannot become a fact**; the ratified number
  comes only from the operator's typed field. The prompt asks for components + direction, never the final
  value, but the **missing rail is the guarantee, the prompt only the courtesy**. Guarded by a
  no-ratify-field test + a **zero-`fact_*`-write** test on the explain endpoint. See `WORKBENCH_EXTRACTION.md`.
- *Also enforced by (the second LLM seam — the narrative→chain drafter, `backend/llm/chain_decomposition.py`):*
  it sources **no number** three ways — the tool schema + `ChainDraftOut` have **no value field** (structural);
  the prompt forbids any figure (Sonnet the adherence lever, the gate-2 manual no-number check its real test);
  and a drafted name is **UNSCORED** until the operator extract→ratifies it. The draft endpoint
  (`POST /workbench/theses/{id}/draft-chain`) is **RESPONSE-ONLY + test-enforced** — it holds a read-only conn
  (so NOT structural-by-absence like the flag seam) and writes nothing (`test_draft_endpoint_writes_nothing`:
  zero `fact_*` AND `basket_member`); the operator's promote is the only writer. See `CHAIN_DRAFTER.md`.
- *Also enforced by (the third grounded seam — the purity estimate, `backend/llm/purity_estimate.py`):* it PROPOSES
  a purity %, but **grounded ONLY in the fetched segment-footnote passage it carries** — a non-grounded proposal
  (or a % out of range) is discarded, never surfaced as a number; the estimate is **computed-on-read, never a
  `fact_*` row** (the leak-proof SURFACE constraint — `WORKBENCH_EXTRACTION.md`), and the operator's
  confirm/override is the only writer. Fail-open to HUMAN. The narrator's **`off_thesis` bool** rides the same
  discipline — a display-only opinion on `ResolvedPlacement`, never a number, never on the call path
  (`CHAIN_DRAFTER.md`).

## 2. Entity resolution is an exact-membership ALLOWLIST, never fuzzy, never a denylist

A name is resolved to a security only by **exact membership** in a hand-curated table (e.g. the DOE feed's
`recipient_id` → ticker). Fuzzy search may be a **discovery net**, but it never *decides* a mapping. An
unknown entity is **dropped** (unresolved), not guessed — an allowlist, so noise fails closed. (Why this is
non-negotiable: fuzzy "Oklo" matches a polluted homonym holding **$48B** of national-lab contracts;
"Centrus" matches an unrelated NAC International. See `docs/DATA_SOURCES.md`.)

- *Enforced by:* `ingest/doe/entities.resolve` (exact `recipient_id` lookup); rejection test
  `tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy`.

**The Workbench securities resolver is the same rule on the populated universe.** The broadener
(`pipeline.populate_master`) loaded the SEC `company_tickers` universe into the master, so `master.search` now
spans thousands of names — but it is still a **discovery NET**: it surfaces exact master rows for the operator
to PICK (the row shows ticker + CIK, so a homonym like Oklo is disambiguated by sight), and it **never decides
a mapping** — the operator commits the exact `security_id`. More rows strengthen the net; exact-identifier-
decides is unchanged. The broadener itself writes **only** exact `company_tickers` mappings — an incomplete
row (missing CIK or ticker) is **dropped**, never guessed.

- *Enforced by:* `securities/master.search` (read-only — never an ingest, never a write); the broadener's
  exact `(cik, ticker)` keying that drops incomplete rows (`securities/master.populate_universe`,
  `tests/securities/test_populate_master.py`).

**The chain drafter is where that net DECIDES, and promote is where it's enforced (S5).**
`resolve_placements` (`backend/workbench/chain_draft.py`) runs each drafter-proposed name through the master
and auto-places **only on a unique EXACT ticker or name match**; several / partial / token-only matches or a
ticker/name contradiction go to an **operator pick** (ticker + CIK shown), and a no-match name is **ABSENT**
(shown, never placed). Auto-place **never rests on a judgment call** — a lone substring match is the Oklo-trap
heuristic, not membership. The UI makes this VISIBLE: PLACED auto-loads (drafted, prunable), AMBIGUOUS enters
the basket **only by an explicit operator pick**, ABSENT is shown-not-placed. And because the drafter writes
nothing, **promote is the single write-side check** — every placed `security_id` must be an exact master
member, else `404`.

**Promote also CANONICALIZES the sibling (the canonical-primary slice, operator-ratified coerce-all).** A
multi-sibling CIK (dual-class / ADR-vs-foreign-ordinary / warrants) has ONE `is_primary` instrument (the
composite rank in `securities/sec_tickers.py`, empirically validated against every multi-row CIK); `exists`
proves a posted id is *a* master row, and `master.canonicalize_ids` then re-points a non-primary sibling to
the CIK's primary (id + ticker) before the spine write — visible in the response, logged, never silent. The
accepted consequence: a deliberate non-primary line (intentionally trading the foreign ordinary) is
unrepresentable in v1.

- *Enforced by:* `workbench/chain_draft.resolve_placements` (exact ticker/name → PLACED, else pick / absent;
  `tests/workbench/test_chain_draft.py`); the promote membership guard + honored-authorship + the
  canonicalization (`app/routers/workbench.py`; `tests/app/test_workbench_api.py` —
  `test_promote_canonicalizes_a_non_primary_sibling`); the rank's pinned violation fixtures
  (`tests/securities/test_canonical_primary.py`). See `CHAIN_DRAFTER.md` + `DISCOVERY.md` §3.

**The M2 back-half ingest honors the same rule on the WRITE side.** `pipeline.ingest_thesis` targets each
basket member's already-resolved `security_id` via `master.get` (issuer ticker + CIK) — **never a fresh fuzzy
resolve**; an unresolved member is skipped and a foreign id reported, never guessed. (It adds no invariant; it
honors this one.) See `FEED_LOOP.md`.

- *Enforced by:* `pipeline.ingest_thesis` (resolves via `master.get`, skips null / foreign ids;
  `tests/pipeline/test_ingest_thesis.py`).

**One blessed corollary-exception — `surfaced_terms`.** Discovery's display provenance (`matched_terms` /
`discovery_source`) is never promoted onto the decided `BasketMember` (`DISCOVERY.md` §5). Its **frozen twin
`basket_member.surfaced_terms`** deliberately IS — the discovery terms that surfaced a name, captured once at
Basket entry. It crosses that corollary narrowly and on the record: membership was already decided by exact CIK
+ promote, this record **decides nothing**, is never a number and never on the call path (#3), and is exactly
the provenance #3/#6 want durable. Do **not** "fix" it back off the member. Full rationale: `DISCOVERY.md`
*Re-scope*. *Enforced by:* migration 0029; the promote freeze pass (`app/routers/workbench.py`);
`pipeline.backfill_surfaced_terms`.

## 3. Provenance to a real, checkable source on every trigger

Every fired trigger carries provenance that resolves to a **real source** — a working EDGAR Form-4 URL, a
`usaspending.gov/award/…` URL, a `price:TKR:date` computation record — **plus the parsed terms** (show the
work). No black-box outputs; if you can't show the source, don't surface the result. (CLAUDE.md #6.)

- *Enforced by:* `Provenance` on every `SignalEvent`; the card resolves them to clickable URLs; tests assert
  the award/accession refs are present (`test_doe_feed`, `test_hims_armed_call`).

## 4. No lookahead — every read is as-of a timestamp  (CLAUDE.md invariant #1)

> **RULE CHANGE — the PUBLIC clock judges history (operator decision 2026-10-21).** The platform is still
> being proved, so the question history must answer is "how would the platform have judged this with what was
> PUBLIC at the time?" — not "what did our database happen to contain at that moment?". The second question
> only replays our own build history (the July freeze, laptop sleeps, the order companies were onboarded, the
> day Form 4/A reading started). So:
> - **No lookahead stays non-negotiable**, judged on the public clock: a fact version counts only from when it
>   became public — its filing / acceptance time, its settled session; a correction (4/A, restatement, vendor
>   re-version) from its OWN publication, never the original's.
> - **`recorded_at` is honest bookkeeping, not the history gate.** New rows are stamped `now` (never
>   backdated) because it is cheap and lets us measure "were we late?" — but nothing is built to REPLAY it.
>   Builds no longer add record-clock parity tests, "a replay pinned at an earlier store time still reads the
>   old state" proofs, or counterfactuals at a call's own `(asof, recorded_at)`; call effects are measured on
>   the public clock.
> - **History views move to the public clock** (the Board/Cockpit scrub-back, missed-night backfills, history
>   re-runs). The Scoreboard editions re-run already uses it.
> - **The immutable calls log is the record** of what the system actually said each night — unaffected.
> - The live nightly is unchanged: at `asof = today` the two clocks coincide.
>
> The record-clock material below (the serve-path cap, `known_at` pins, the D3a held-date term) is the
> HISTORY of how this was built; where it conflicts with this block, this block wins.

The store is bitemporal (`valid_from` = event time, `recorded_at` = when we learned it). Detectors read
**only** through the as-of accessor; a fact dated after the query `asof`, or a correction recorded after
`known_at`, is invisible. Facts are **append-only** — a correction is a new row, never an in-place `UPDATE`.
Firings are **re-derived from facts on every read** (no persisted firing layer), so corrections propagate and
replay stays honest.

- *Enforced by:* `db/bitemporal.as_of` (security-scoped) + `as_of_thesis` (thesis-scoped, e.g.
  `fact_theme_conviction`), both delegating to the shared `_as_of`; the append-only DB trigger; the
  correction-axis test (`tests/ingest/test_pit.py` / the bitemporal tests).
- *Also honored by (M2):* the per-thesis ingest leaves `recorded_at` to the DB default `now()` — **never
  backdated** — and the daily cron pins `asof=today` / `known_at=now` (a live read), so a fact ingested today
  is invisible to an as-of read pinned at an earlier transaction time (`tests/pipeline/test_ingest_thesis.py`,
  the no-lookahead test). See `FEED_LOOP.md`.
- *Also honored by (D1 + D2, 2026-10-16 — the price write path):* the nightly SPY/IWM leg used to reuse the
  one-shot backfill's stamp (`recorded_at = D 00:00 UTC`, ~26.5 h before ingest), and a daytime pass stored the
  vendor's in-progress bar as a close. Now the benchmark path stamps `recorded_at = now` from the database
  clock on every caller (the one-shot CLI too, since 2026-10-21 — no production writer backdates, pinned by
  `test_no_production_module_writes_the_backdated_stamp`), a correction is always stamped
  `now`, and every price write withholds a bar until its session settles (`ingest/prices/settle.py`,
  `Settings.price_settle_time`). *Enforced by:* `tests/pipeline/test_ingest_benchmarks.py` (a bar ingested
  22:35 ET on D is visible to the asof-D call and the scrub-back cap, invisible before 22:35 and to asof < D;
  the window-bounded restatement; count-the-table) and `tests/ingest/test_price_settle*.py`; and AUDITED every
  night over the table (2026-10-21): the self-audit's `no_unsettled_bars` check fails — and pages — on any
  `fact_price_eod` row recorded before its bar's settle instant, written since the last judging pass by any
  writer (`tests/pipeline/test_settle_audit.py`). See `FEED_LOOP.md` §Settled closes.
- *Also honored by (the HISTORY views — judged on the PUBLIC clock since 2026-10-21):* every past-asof read
  — `GET /theses/{id}/call`, `/display-signals`, `/workbench/…/scored`, the Scoreboard drawer's dated overlays,
  `pipeline.backfill`'s missed-night reconstruction, `pipeline.run` for a past asof, and D3b's re-assembly —
  threads `domain.market_time.serve_view(asof)` = `(known_at_for_asof(asof), "public")`: each fact VERSION
  counts from its PUBLIC instant (`db/public_clock.py`, the ONE definition, shared with the backtest's
  `clock=public` mirror) up to the end of that **market** day. Per table: a Form 4 from its SEC acceptance
  (filled across a fact's re-parses; none on any version → from the first time we held it, an upper bound —
  never early, never dropped, #9); a bar's FIRST version from its session settle (`d` at
  `Settings.price_settle_time`), a vendor RE-version from its own `recorded_at` (it carries no publication time
  of its own — we cannot know it earlier); an 8-K / 13D from its `filed` date; fundamentals from `valid_from`
  (= `filed`; a restatement from its own filing); catalyst / dilution / theme conviction from `valid_from` (the
  backtest's operator decision); the operator-RATIFIED Workbench facts (revenue mix, shares, cash burn) and the
  fund-shares samples from `recorded_at` (their `valid_from` is a period / as-of date, not a publication date).
  The 4/A tables: the amendment and the system's link from the 4/A's acceptance, a SYSTEM decision from that
  same publication, an OPERATOR decision from when the operator made it. The version a past view reads is the
  NEWEST public by then (`public DESC, recorded_at DESC, id DESC` — a re-parse resolves to our best parse); a
  public-read row carries `recorded_at` = its public instant and our honest ingest instant as `ingested_at`
  (the drawer's "ingested" line). The roster and the position stay as of the same instant on the record axis
  — they ARE operator decisions. A live `asof` threads `(None, "record")`: the unchanged live read (the PIT's
  own `now` for facts, the DATABASE clock for the decisions log — `decisions_repo`'s one-clock rule), where
  the two clocks coincide (a version's public instant never decreases as its `recorded_at` increases, so with
  every version visible the public pick IS the record pick — MEASURED 2026-10-21 on prod: 12/12 live cards
  identical on both clocks). What this does NOT make identical to the record: the recompute runs TODAY's
  code and rules over what was public then — never a replay of the row; the immutable calls log is the
  record. *Superseded (record-clock history):* from 2026-09-09 (#336) to 2026-10-21 these routes capped
  `recorded_at` at the end of the as-of day — honest about what our database held (MEASURED 2026-09-09: one
  thesis at a late-August as-of had recomputed ARMED on a member's bars ingested a week after it), but it
  replayed our outages and onboarding order (MEASURED 2026-10-21 on prod: on the 15 Fridays 07-10..10-16, 116
  of 180 thesis-cards differ between the two clocks — the 13D / 8-K / insider / price backfills of
  August–September were all public long before). *Enforced by:* `tests/db/test_public_clock.py` (late-ingested
  but public → visible; a correction published after the as-of → invisible; acceptance, settle and the
  first-held fallback; the best-parse pick; public pick == record pick with every version visible; the basket
  read == the scoped read; D3a on the public clock), the per-site template + public tests in
  `tests/app/test_theses_api.py`, `test_display_signals_api.py`, `test_workbench_api.py`,
  `test_scoreboard_price_window_api.py`, `tests/pipeline/test_backfill.py`, the parity gate
  `tests/replay/test_public_clock.py::test_the_public_mirror_is_row_for_row_the_live_public_read` (+ the 4/A
  twin in `test_pit_insider_amendments.py`), and `tests/domain/test_market_time.py` (`serve_view`, the
  market-day cap, the post-20:00-ET live identity, the configured zone).
- *Also honored by (Board/Cockpit perf PR-1b — the PIT memo rule + the horizon registry):* one
  `PointInTimeData` per request / per cron assemble **memoizes** each `(table, security_id)` as-of result
  and **prefetches** a whole basket per table in ONE `db.bitemporal.as_of_many` query — the SAME
  `valid_from <= asof AND recorded_at <= known_at` gate, the same latest-version pick and tiebreak, tenant-
  filtered exactly like `as_of`. A memo can never cross a time or tenant boundary: `asof` / `known_at` /
  `tenant_id` are fixed at construction and a per-call value (a reader's `lookback_days`) is never a memo key.
  **Read bounds are derived, never hand-typed.** A bounded table (`fact_price_eod`, `fact_insider_txn`) is
  floored at `valid_from >= asof - bound`, where every reader DECLARES its max horizon (`Detector.horizons(cfg)`
  from the SAME `CallConfig` the assembler runs with; `DisplayMember.horizons` from the module `LOOKBACK_DAYS`;
  a module `HORIZONS` on the thesis-level readers and the Workbench scorer) and the PIT's bound per table is
  `max(declared) + MARGIN_DAYS` (`signals/horizons.py` — today 460 d price / 240 d insider for the call PIT,
  630 d / unbounded for the display PIT, unbounded for the scored PIT). A reader declaring `None` unbounds the
  table; a reader with NO declaration fails a TEST — never a truncated read. **The memo rule:** the memo holds
  ONLY that max-horizon window per `(table, security_id)` (the prefetch floors it in SQL, the per-security
  fallback trims to the same floor in Python) and every caller trims its OWN window from it. Version-safe:
  `valid_from` is (or equals) a natural-key column on both bounded tables, so a floor drops whole facts and can
  never change which version wins. The replay mirror (`replay/pit.py`) is untouched and stays parity-gated.
  *Enforced by:* `tests/signals/test_horizons.py` (every reader declared; a RECORDING PIT proves each reader's
  touched tables ⊆ its declaration and its requested window ≤ its declared horizon; the derived numbers pinned;
  a reader without a declaration cannot be constructed), `tests/db/test_as_of_many.py` (accessor-level equality
  vs the per-security read at `known_at = now` AND a `known_at` pinned BETWEEN two recorded versions of a fact),
  `tests/pipeline/test_pit_prefetch_equality.py` (canonical CallCard + display-body equality, incl. the
  `insider_flow_90d` '—' vs '0/0' shapes), and the grown poison-row test (`test_basket_prefetch_read_is_tenant_isolated`).
- *Also honored by (D3a — FIRST knowability for fundamentals):* a re-reported, UNCHANGED number is not new
  knowledge. The `fact_fundamentals` as-of read (`db/bitemporal.first_knowable_versions`, shared verbatim by the
  DuckDB mirror) picks the latest FILING visible at `(asof, known_at)` (versions ordered `valid_from,
  recorded_at, id`) and annotates each row with `knowable_from` / `knowable_accession`: the earliest filing
  date (`valid_from`) over the trailing run of value-identical versions visible at `(asof, known_at)`, so a
  version recorded after `known_at` can influence nothing. Detectors date, cite and age a fundamentals fact by
  `knowable_from`, never `valid_from` — the 10-Q comparative re-dating had kept year-old revenue inflections
  live (CALL_LOGIC.md §2, the GEV example). Both the date and the pick are on the PUBLIC clock (the filing).
  The run counts each filing ONCE, by its latest-recorded version (the revenue-label fix, 2026-10-20: a
  same-filing re-parse under the current tag replaces the old tag's number — migration 0060), so a superseded
  old-tag version between two filings of the current number can never break the run and date it at the
  year-later comparative (MEASURED on the dev copy 2026-10-20: 55 periods in 10 companies would otherwise date
  late) — the same per-filing collapse the public-clock read applies.
  *(Until migration 0057 the per-version date read `max(valid_from, market date of recorded_at)` — D3a's
  held-date term, retired with the operator's approval 2026-10-21 — and the pick was `recorded_at DESC, id
  DESC`; on every row stored before 0057 — `recorded_at = valid_from 00:00 UTC`, all 120,876 on prod — both are
  identical to today's rule. See the next bullet for why the honest stamp retired them.)* On a HISTORY
  (public-clock) read the visible versions also carry their PUBLIC instant as `recorded_at` (the start of their
  filing day), collapsed to one per filing, so the same filing-based pick and date come out
  (`tests/db/test_public_clock.py::test_first_knowability_reads_the_public_clock`).
  *Enforced by:* `tests/db/test_first_knowability.py` (fixed `known_at` pins between and after the filings, a
  restatement, a reverted value, the shared-filing tie-break, and a sweep proving nothing recorded after `known_at`
  moves the pick or the annotation — all on real prod version histories) and `tests/replay/test_pit_first_knowability.py`
  (row-for-row + event-for-event parity, scoped and prefetched), and `tests/ingest/test_revenue_label.py` (the
  label-fix re-ingest on real companyfacts — the 2026-10-20 revenue-label fix: first knowability survives it,
  and a weekly sweep dates every revenue-acceleration event at the filing that first made its number public —
  never before, never after).
- *Also honored by (migration 0057, 2026-10-21 — the fundamentals write path; the public-clock rule's
  bookkeeping half):* `fact_fundamentals` was the last writer to backdate its bookkeeping stamp (`recorded_at =
  filed 00:00 UTC` — MEASURED all 120,876 prod rows, a median ~6.2 years before the row was actually written,
  since a name's onboarding pull carries its whole history). The writer now stamps the database instant it
  stores a batch (`ingest.fundamentals.store_quarters`, `db_now`, never backdated); existing rows stay as
  written, which is moot under the public clock. The CALL does not move because a fundamentals fact's date
  (`knowable_from`) and its pick are both stated on its public FILING (bullet above) — under one shared honest
  stamp a history reads exactly like the per-filing backdated stamps (MEASURED on prod: 0 of 12 cards differ at
  asof 2026-10-21; 0 of 661 securities' rows differ old reader vs new). The table's UNIQUE moved to `(…,
  period_end, valid_from)`, the ingest's own idempotency key, so a duplicate filing is still refused. Migration
  0060 (the revenue-label fix) adds `supersedes` to that key, NULLS NOT DISTINCT: still ONE original per filing,
  and a filing's re-parse under the current tag is admitted only as a link naming the version it replaces, once
  per version, and only when it changes the number (a trigger refuses a no-op or cross-filing link) — so a
  duplicate can never hide behind a correct read, and a tag that switches back is one more link
  (`tests/ingest/test_revenue_label.py::test_the_key_admits_only_a_linked_reparse_that_changes_the_number`).
  *Enforced by:* `tests/ingest/test_fundamentals_honest_stamp.py` (a new row stamped inside the DB-clock window
  of its run; one honest stamp reads row-for-row and event-for-event like the backdated stamps on real
  histories; existing rows read exactly as the pre-0057 reader; a rerun appends zero and a duplicate filing is
  refused, counting the table) and `tests/replay/test_pit_first_knowability.py` (a public-clock mirror reads
  honest and backdated rows identically at every pin). See `FEED_LOOP.md` §Honest stamps — the fundamentals
  write path.
- *Also honored by (migration 0062, 2026-10-22 — the SET-ASIDE fundamentals versions):* a stored revenue row
  #417's data-quality screen would have refused (written by the old ingest before the screen existed) is named
  in the append-only `fundamentals_set_aside` table (`pipeline.set_aside_fundamentals`), and every read skips it
  at every asof — both clocks, the basket prefetch, and the replay mirror (`db.set_aside`, applied in the one
  public builder and the record reads). That is not lookahead: the screen judges a row only against filings on or
  before the row's OWN filing (the A2 rule), so skipping it is what the ingest screen would have done the day it
  was filed; the version it superseded is read instead. Reversible (a `revoke` row), the fact rows never touched,
  and a number another revenue label keeps as genuine is never set aside (recall, #9). *Enforced by:*
  `tests/pipeline/test_set_aside_fundamentals.py` (a later filing never decides; count the table on a re-apply;
  the fallback and the revoke on both clocks and the prefetch; record + public mirror parity).
- *Also honored by (the BACKTEST's public clock — the SAME definition as the history views):* a research
  run asks "when could ANYONE have known this?" instead of "when did THIS system record it?" — since
  2026-10-21 the question every history view asks too, so the per-table definition lives ONCE in
  `db/public_clock.py` and `replay/export.py::export_snapshot(clock="public")` materializes the SAME SQL the
  live history read runs (`public_versions_sql`): the exported `recorded_at` is each version's public instant
  (the honest ingest instant beside it as `ingested_at`), and the mirror's reader and gate are unchanged, so a
  public mirror read is row-for-row the live `clock="public"` read (the parity gate below). What the shared
  definition changed in the mirror (2026-10-21): a bar's re-version counts from its own publication (it used
  to collapse onto the bar's date — a correction back-dated), its first version from the session settle; the
  Workbench-ratified tables and the fund samples are no longer excluded (they count from their ratification /
  sampling); the 4/A tables carry every row with its honest `recorded_at` plus `public_at` (an operator
  decision from when it was made — the old "standing decisions stamped at the acceptance" hindsight is gone).
  The ONE named asymmetry: a Form 4 with no acceptance on any version is excluded + COUNTED by the export (its
  contract) and shown by the history read from the first time we held it. Three rules make the swap honest
  rather than merely different. A table
  with **no declared clock is excluded WHOLE** and named in the manifest with the detectors it blinds; a ROW
  whose clock column is NULL is excluded and **COUNTED** (both counts ride the artifact and the surface, so
  the size of the hole is part of the result). And versions are resolved AT EXPORT partitioned by
  `(identity, clock_value)` — because for insider and price every re-version carries the SAME public clock
  (our own repairs, not new filings: MEASURED 44,982 and 17,373 tying identity-groups, 8,947 of the price
  ties differing on `close`), so a naive rewrite would leave the reader's `ORDER BY recorded_at DESC, id DESC`
  to be decided by a random UUID. After the partition every surviving row for an identity carries a distinct
  derived `recorded_at` and no tie is left to break. The scoring pass is unaffected: it reads FORWARD by
  design (the no-lookahead rule binds the decision, not the measurement of it). Full detail, the registry
  table and the NULL rates: `docs/BACKTEST.md`. *Enforced by:* `tests/replay/` (the record-mode parity gate
  stays byte-identical; `test_public_clock.py::test_the_public_mirror_is_row_for_row_the_live_public_read`, the
  exclusion + NULL-count tests).
- *Also honored by (R8-A — Form 4/A amendments):* an amendment changes what the insider read returns only from
  the moment THIS system held it. The 4/A, its link and every operator decision carry their own `recorded_at`,
  and the resolver (`domain/insider_amendments.resolve_insider_rows`, shared verbatim by the Postgres read and
  the DuckDB mirror) counts each iff `recorded_at <= known_at`; `accepted` (the SEC acceptance) only orders two
  amendments of one original and is displayed, never a gate. So a replay pinned before tonight's link reads the
  original exactly as stored, and one pinned before a ratification reads the pending state; the backfill of the
  existing 4/As (stamped `recorded_at = now`) moves no past replay. An amended row is dated by its OWN
  transaction date (`valid_from <= asof`, and the registry floor, apply to it too). ON THE PUBLIC CLOCK (every
  history view, 2026-10-21) each input is gated on its PUBLIC instant instead (`db/public_clock.aux_public_sql`,
  read by `db/insider_resolution.load_inputs(clock="public")` and carried by the mirror as `public_at`): the 4/A
  and its link from the amendment's SEC acceptance, a SYSTEM decision from that same publication, an OPERATOR
  decision from when the operator made it — while the order the resolver reads (newest link, newest standing
  verdict) stays the honest `recorded_at`. *Enforced by:* `tests/db/test_insider_amendments.py`
  (a read pinned before the link / before a decision), `tests/replay/test_pit_insider_amendments.py` (row-for-row
  parity at four pins, both read shapes; the public-clock acceptance; the public-clock parity gate and
  `test_public_clock_counts_an_operator_decision_from_when_it_was_made`).
- *Also honored by (R8-B — the system resolves the filer's own correction):* the system's resolution (an
  `apply`, or a `keep` of a void its words do not retract) is an append-only decision like the operator's,
  carrying its own `recorded_at` — in the link's transaction at ingest (so a new 4/A is never seen pending), or
  `now` when the nightly sweep resolves the pre-R8-B backlog. So a replay pinned before the sweep reads the
  state the system actually held (pending: a void suppressing, a partial with no effect) — the backlog is
  resolved forward, never back-dated. On the public clock a system decision counts from the 4/A's acceptance
  (the system's reading of a public filing under today's rules), on the live public read and the mirror alike.
  The row match a system (or operator) `apply` carries is computed when it is written
  and stored on it (`replaced_rows`), so a windowed read never recomputes it, and both engines read it.
  *Enforced by:* `tests/db/test_insider_amendments.py::
  test_a_replay_pinned_before_the_sweep_reads_the_pending_state_it_held`,
  `tests/replay/test_pit_insider_amendments.py::test_the_mirror_reads_the_sweep_exactly_like_postgres_before_and_after_it`.
- *Also honored by (the R8 linker fix, 2026-10-21):* a stored link the fixed cascade moves is RE-LINKED forward,
  never rewritten — a new link version, the system's `revoke` (`system:relink`) of its stale decision and its new
  one, all stamped `recorded_at = now` (`pipeline.relink_amendments`); a read pinned before the re-link reads the
  old link exactly as held. *Enforced by:* `tests/pipeline/test_relink_amendments.py::
  test_the_iehc_mislink_is_relinked_append_only_and_an_earlier_read_is_unchanged`.
- *Also honored by (the R8 re-derivation, 2026-10-21):* a legacy system decision written before the row match
  (`replaced_rows` NULL — the whole original) is RE-DERIVED forward, never rewritten — the system's `revoke`
  (`system:rederive`) and its `apply` carrying the row match, both stamped `recorded_at = now`
  (`pipeline.rederive_amendment_matches`); a read pinned before reads the whole-original state the system held.
  *Enforced by:* `tests/pipeline/test_rederive_amendment_matches.py::
  test_czrs_legacy_whole_decisions_are_rederived_append_only_and_an_earlier_read_is_unchanged`.
- *Also honored by (joint reports — one trade several persons reported, counted once; migration 0054):* the
  collapse (`domain/insider_joint_reports.py`) is one pure function inside the same resolver seam, so both engines
  read it identically, and it acts only on the ownership evidence a row carries. On stored history that evidence
  arrives only by `pipeline.repair_insider_ownership`'s append-only versions stamped `recorded_at = now`: a read
  pinned before the repair reads the double count the system held (operator decision 4, 2026-10-21). *Enforced
  by:* `tests/pipeline/test_repair_insider_ownership.py::test_a_pre_repair_replay_still_reads_what_the_system_held`,
  `tests/replay/test_pit_joint_reports.py` (row-for-row parity, both read shapes; an old mirror reads as before).
- *Also honored by (internal transfers — a buy and a sale between linked filers; migration 0055):* the screen
  (`domain/insider_internal_transfers.py`) is one pure function in the same seam, after the joint collapse, and it
  reads only the legs' OWN filings' evidence (never another date's filing) — so every reader, bounded or not,
  screens alike, and both engines identically. The named-holding evidence arrives on stored history only by
  `pipeline.repair_insider_filer_text`'s append-only versions stamped `recorded_at = now`: a read pinned before the
  repair reads the unscreened legs the system held. *Enforced by:*
  `tests/pipeline/test_repair_insider_filer_text.py::test_a_pre_repair_replay_still_reads_what_the_system_held`,
  `tests/replay/test_pit_internal_transfers.py` (row-for-row parity, both read shapes; an old mirror links nothing
  by name).
- *The record-clock proofs above* ("a read pinned before the repair / sweep / re-link reads what the system
  held") are the HISTORY of how those repairs were built and stay true of the record clock. Since 2026-10-21 no
  history view reads that clock: on the public clock a repair, re-parse, re-link or re-derivation of a PUBLIC
  filing counts from that filing's publication, so a past view reads today's best reading of what was public
  (`tests/db/test_public_clock.py::test_a_reparse_shares_its_filings_public_instant_so_the_best_parse_is_read`).
  New builds add no record-clock replay proofs (the rule block above).
- *Also honored by (S2 — the split list and the tape's scale, on the PUBLIC clock, 2026-10-22):* a split counts
  from its OWN publication — the earliest `valid_from` (its first SEC filing, else its first session) or
  `effective_date` across its versions — never from the day we listed it; a history view reads the split's
  newest version (`db/public_clock.split_versions_sql`, the one builder the serve path's `split_repo` and the
  public mirror run). A price bar before a live listed split whose first session is on or before the security's
  tape edge has every version public at the session settle and is read from its newest version, then put on the
  VIEW's scale (`domain.splits.on_view_scale`, the one function both engines run): x the splits in force by the
  view's day, / the ones the store carries. So no view reads a split that was not in force by its day — the old
  rule did (MEASURED: 22,654 of 29,057 pre-split bars were first stored after their split, already adjusted,
  and their first version, public at the settle, carried it) — and repaired bars clean every past view, not just
  today's. The insider price screens and the market cap apply the splits in force by the view's market day
  (`tape_scale`), the scale the view's bars are on. The live read (record clock) is unchanged. This differs from
  the 4/A rule on purpose: an operator's split confirm counts from the split's publication, because a split
  gated on the confirm would leave every view between the two on two scales. Named residuals: a non-split vendor
  correction to such a bar counts from the settle (MEASURED: 34 changes > 3% across the 29,057 bars); a store
  that does not carry a listed split (the `splits_applied` audit names it) reads wrong in history until repaired.
  A mirror exported before S2 carries no `security_split.parquet` and reads no splits; one whose manifest (or
  run `MirrorInfo.split_table`) records the file but lacks it refuses to open. *Enforced by:*
  `tests/pipeline/test_repair_split_basis.py::test_after_the_repair_every_history_view_reads_its_own_days_scale`
  (BTOC's real tape vs Polygon's independent raw prints, T4-clean, four views), `tests/signals/test_insider_split_scale.py`
  (the split from its publication; the CEO's buys kept before and after the split on history views; the public
  mirror row-for-row with Postgres), `tests/db/test_public_clock.py` (public pick == record pick, every version
  visible), `tests/pipeline/test_fetch_ahead.py` (the normalization identical under the concurrent fetch-ahead).
- *Also honored by (S5 — the split-night hold, 2026-10-22; `CALL_LOGIC.md` §2b):* whether a member's price-path
  triggers are held is computed from the VIEW's own bars and split list (`price_history` / `split_events`) at
  the call's asof — a history view on the PUBLIC clock sees a split from its own filing or first session, the
  live view what the list holds now — inside the one seam every path assembles through
  (`pipeline.core.assemble_from_pit`), so the nightly, the serve path, `pipeline.backfill`, D3b and the replay
  mirror hold identically. No record-clock replay proof was added (the rule above). *Enforced by:*
  `tests/signals/test_split_hold.py` (real stored versions of GAME / NXL / MDCX / VRME's split nights: held with
  the split unlisted, not held with it listed — the list row the only variable; the public mirror's card ==
  Postgres's on a held night).

## 5. Tenant isolation; production is a fresh tenant, never a destructive wipe

Every row carries `tenant_id`. Dev/demo data is a tenant; **production is a new tenant**. Never build a
destructive reset path or assume a single global tenant — seeds are idempotent and additive.

**Isolation is discipline + the poison-row test, NOT RLS** — the `security_id` FK carries no tenant, so
nothing at the database layer *forces* a read to pass the right `tenant_id`. The standing obligation: **every
new read path MUST route through the tenant-filtered accessors** (`db.bitemporal.as_of` / `as_of_thesis`,
`securities/master.*`, the `PointInTimeData` methods) — never a raw fact query — **and the isolation test
MUST GROW with each new read surface**. A forgotten filter on a raw query would leak with no DB backstop to
catch it. (RLS is the auth-era defense-in-depth; see `docs/PRODUCTION_TENANT.md`.)

- *Enforced by:* `tenant_id` on every table; `DEFAULT_TENANT_ID` for the demo; seeds upsert/append; the
  poison-row proof `tests/db/test_tenant_isolation.py` (grown for each new accessor — insider/price/theme,
  the three scoring facts, the Workbench scored read, the known-feed-limitation read of 0056, the S2 split
  list on the repo / live PIT / replay mirror paths, the S5 split-jump scan), plus the Scoreboard price-window's direct split read
  (`tests/app/test_scoreboard_price_window_api.py`).

## 6. The call-assembler is pure and deterministic

`assemble_call(thesis, events, asof, cfg)` is a pure function: same inputs → byte-identical CallCard. No
DB/network/clock inside it; `asof` is always a parameter (no implicit "now"). The `calls` table is an
**accountability record**, never the read path (the API recomputes live). The Scoreboard's Edition 0 scores
it AS the record; a Scoreboard edition >= 1 (editions S3/S4, migration `0063`) reads only its own frozen
`edition_call` pages — a re-run published beside the log, which never writes `calls` and is never moved by a
later `calls` row (`docs/SCOREBOARD.md` §Editions).

**And the record now names the policy it ran under (F1, migration `0044`).** Purity makes the card a function
of `(thesis, events, asof, cfg)` — but until now the row recorded only the card, so `cfg` (and the code) were
invisible and the record could not distinguish "the facts changed" from "a dial changed." Every recorded row
carries `config_hash` (the sha256 fingerprint of that `CallConfig`), `code_sha` (the image's git SHA — `NULL`
when unknown, **never fabricated**) and `run_kind` (`cron` | `manual` | `backfill`). They ride **beside** the
card and **outside** `calls_repo._canonical`'s substance compare — the `reconstructed` precedent — so the
immutable log stays immutable, a dial edit alone never re-records an unchanged call, and legacy rows stay
`NULL` rather than carrying a guess. The fingerprint's canonicalization is deliberately *not* a
`model_dump` one-liner: `CallConfig` holds `frozenset` dials whose iteration order depends on
`PYTHONHASHSEED`, so the naive recipe produces a different digest in every process (MEASURED) and would have
stamped a fresh "policy" on every cron night.

- *Enforced by:* the assembler takes `cfg` + `asof` as parameters; determinism golden tests; the read path
  recomputes via `pipeline/call_for_thesis`; `tests/domain/test_config_hash.py` (the fingerprint is stable
  **across processes**, moves on a dial change, and raises rather than falling back to a repr);
  `tests/repositories/test_calls_repo.py` (a changed hash on an identical card appends nothing —
  **counting the table**; `_canonical` is blind to all three columns);
  `tests/db/test_migration_0044_run_identity.py` (legacy rows NULL, the `no_update` trigger still armed).
- *Also honored by (S5 — the split-night hold):* `assemble_call` takes the hold's `missing[]` lines as one more
  parameter (`held`, default empty = byte-identical); which triggers are held is decided before it, in
  `pipeline.core`, from the point-in-time view — the assembler stays pure (`tests/calls/test_split_hold_missing.py`).
- *Also honored by (M2 — Option B intact):* the daily cron (`pipeline.daily`) appends the day's call-of-record
  via `calls_repo.record_if_changed` — idempotent (the `calls` log is immutable [`no_update`] + non-unique, so
  a conditional append, not an UPSERT) — but it builds **no read-serving signal/score cache**; the serve path
  still recomputes from facts, and the log is never read back to serve. (`_canonical` makes the compare
  order-independent so a pure reorder can't re-append; `tests/repositories/test_calls_repo.py`,
  `tests/pipeline/test_daily.py`.) See `FEED_LOOP.md`.
- *Also honored by (the reconstructed row — `pipeline.backfill`, migration 0042):* a missed night's row is
  appended by the SAME assembly with `known_at` pinned, and carries `calls.reconstructed = true` OFF the card
  (a field IN the card would fake a change in `record_if_changed`'s canonical compare). The record stays
  immutable — 0042's one-time legacy stamp set the flag under a single-statement trigger disable and rewrote
  nothing the call says. **A reconstructed row never defines an episode boundary:** the Scoreboard's record
  path reads `latest_for_thesis(include_reconstructed=False)` (filtered BEFORE the per-as-of dedup, so a night
  carrying both scores its nightly row); every other reader — the cron's transition compare, the backfill's
  prior-row report, the freshness reads, the decision log's stance — keeps the default and sees every row.
  (`tests/scoreboard/test_record.py`, `tests/repositories/test_calls_repo.py`,
  `tests/db/test_migration_0042_reconstructed.py`.) See `SCOREBOARD.md`, `FEED_LOOP.md`.
- *Also honored by (the annotated row — D3b, migration 0045):* a JUDGMENT about an honest row (a phantom arm
  under D3a's dating fix) lives in the append-only `call_annotation` side table, never on the row — `calls`
  is not touched, not even under a trigger disable. The Scoreboard drops rows with an ACTIVE `exclude` by
  READ POLICY (`Settings.scoreboard_exclude_annotated`, overridable per request with `include_annotated`),
  BEFORE the per-as-of dedup exactly like the reconstructed filter; flipping the policy or appending a revoke
  restores the pre-annotation reading byte-for-byte. Every other reader keeps the default and sees every row.
  The annotator's D3a-off re-assembly is a diagnostic view that lives only in the CLI — no live-path switch.
  (`tests/pipeline/test_annotate_phantom_arms.py`, `tests/repositories/test_call_annotations_repo.py`,
  `tests/db/test_migration_0045_call_annotation.py`.) See `SCOREBOARD.md`, `FEED_LOOP.md`.
- *Also honored by (the on-time row — M2 + C3, 2026-10-16):* nothing recorded LATER may supersede a night's
  honest on-time call-of-record, and nothing typed by hand may write a scored row for a past night with
  today's knowledge. A `--catch-up` pass skips (no ingest, no write) every thesis already holding a
  non-reconstructed row for the night inside its on-time window — `[RUN_AT, the next weekday's 06:00 ET)`,
  `pipeline/night_coverage.on_time_window` (MEASURED 10-12: a next-day catch-up re-versioned an on-time row
  — AI Memory & Storage — with restated bars and next-day Form 4s, and recorded 8 more theses late); and
  `pipeline.daily --asof <older than the last expected night>`
  is refused (exit 2) unless `--catch-up` / `--late-ok` / `--no-live` — a missed night is
  `pipeline.backfill`'s, pinned and marked reconstructed. Whether a LATE row is scored is the separate
  late-row policy (L1, next). (`tests/pipeline/test_killed_runs.py`, `tests/pipeline/test_night_coverage.py`.)
  See `FEED_LOOP.md`.
- *Also honored by (the late row — L1, operator Q5 = A, 2026-10-16):* a non-reconstructed row recorded at or
  after its night's on-time window closed (the next weekday's 06:00 ET EDGAR open,
  `pipeline/night_coverage.is_late` — the SAME window as M2's) can have read filings the night could not, so
  it NEVER defines a Scoreboard episode. The Scoreboard drops late rows by READ POLICY
  (`Settings.scoreboard_exclude_late`, overridable per request with `include_late`) BEFORE the per-as-of
  dedup, on all four winning-row reads — so an on-time row for the night wins over a later re-version and a
  late-only night reads like a cron gap. Lateness is DERIVED from `recorded_at` on every read (no column, no
  stamp, `calls` untouched); flipping the policy restores the pre-L1 reading exactly. The three filters
  compose by AND (reconstructed always · annotated by its policy · late by this one). The cron still WRITES
  late rows (the log records what was said, and when); every other reader sees them. A push for a late night
  says so (N1) — a transition push and the Q13 newly-armed digest alike. (`tests/scoreboard/test_late_rows.py`,
  `tests/pipeline/test_night_coverage.py`, `tests/notify/test_slack_notifier.py`,
  `tests/pipeline/test_push_policy.py`.) See `SCOREBOARD.md`, `FEED_LOOP.md`.
- *CLOSED — "today" is a DOMAIN fact, not an ambient one.* Inside the assembler `asof` is always a
  parameter (above). But at the **edges** that choose the `asof` for a run, "today" was derived from
  `date.today()`, which reads the process's **ambient timezone**. **This bit in production:** a manual
  `pipeline.daily` in a UTC container after ~20:00 ET recorded **tomorrow's** `asof` — a materially different
  call (a signal whose liveness window closed that day drops out of the inclusive `entry_signal_is_live`;
  the derived countdowns shift), not a mislabel. The `TZ` pin (#202) fixed the two containers but **not** a
  CLI run elsewhere. The trading day is a domain fact, not an environment fact — which is why the fix lives
  in `domain/`, not in ops.
  **The rule now:** no backend module reads the ambient clock; every trading-day answer comes from
  `domain/market_time.market_today()` / `market_now()`, on an explicit `ZoneInfo` from `Settings.market_tz`
  (env `ALPHADECK_MARKET_TZ`, default `America/New_York`; a bad zone fails loud, never a silent UTC
  fallback). `market_today()` is *today in market time* and does **no trading-calendar logic** — no weekend
  skip, no holidays; the Mon-Fri + `RUN_AT` math stays in `pipeline/schedule.py`. `datetime.now(timezone.utc)`
  is untouched and still correct: that is transaction time (`recorded_at`, #4), a different axis.
  *The growth is the argument for the guard:* the gap was written up at **9** call sites and was **18** by
  the time it was fixed — new modules kept reaching for `date.today()` because there was nothing else to
  reach for, and nothing noticed.
- *Enforced by:* `domain/market_time.py` (the single helper) + **two AST repo scans** in
  `tests/domain/test_market_time.py`. `test_no_backend_module_reads_the_ambient_clock` fails on any new bare
  `date.today()` / no-arg `datetime.now()` in non-test backend code (allowlist: the helper itself);
  `test_no_test_module_reads_the_ambient_clock` extends the same discipline to the tests tree — the test
  half of the fix (**option (b)**: a production-date-contract test reads `market_today()`, so test and code
  agree by construction) with its OWN allowlist `_TEST_ALLOWED`, empty by design, so a future ambient-clock
  test is a review-visible allowlist decision rather than a silent, time-of-day-flaky failure. Each scan has
  a meta-test proving it detects a violation rather than reading vacuously green. The contract tests pin a
  UTC-evening instant to the previous ET date, and pin that the helper does NOT skip weekends.
  `docker-compose.yml`'s `TZ` pin remains as defense-in-depth, no longer the mechanism.
  See `POSTMORTEM_CRON_FREEZE_2026-07.md`.

## 7. Factor behavior on the property that drives it — never on grade-as-a-bundle or signal kind

The through-line (CALL_LOGIC). Categorical **call strength** ← grade; **signal-validity window** ← horizon;
provisional-call wording + **setup-strength cap** (wire/config name `confidence`) ← the weaker entry key;
catalyst **liveness** ← the agreement's horizon. Position size, instrument, execution, and portfolio risk
belong to the operator and the firm's external systems — never to grade or another CallCard field. **Never**
re-couple these, and never add an `if kind == …` branch where a property already carries the signal. A new
signal kind inherits correct behavior from its own properties.

**Theme conviction (M5b) is the canonical case.** It's a Key-1 *fallback* emitted at **`flip`**, so it caps
the call's setup strength through the weaker-key path exactly like any flip — there is **no** "theme is
capped" branch. The
only reads of the theme kind are: set membership in `conviction_kinds`; the **`own_conviction_kinds`**
exclusion (`conviction_kinds − {THEME_CONVICTION}` — the seam that keeps a future conviction kind inheriting
"own" automatically); the `theme_armed` **display flag**; and the `is_own` **ranking tiebreak**. None is a
behavior branch.

**The Workbench scorer is the front-half case.** Each of the four meters is factored on its own driving
property — purity on revenue-mix %, runway on cash/burn, catalysts on live-count + grade, dilution on
overhang % — with **no `if kind ==` branch anywhere in the engine**. The 0-4 pip cutoffs all come from
`CallConfig` (no hardcoded thresholds), the same property-keyed discipline.

- *Enforced by:* CALL_LOGIC §3/§4/§7; the verdict keys on `conviction_hold_threshold_days`, not kind; the
  setup-strength cap (`starter_confidence_cap`) keys on `is_starter` (entry grade), not kind; the theme
  conviction caps via its `flip`
  grade (`signals/theme_conviction.broadcast` emits flip), with the `own_conviction_kinds` property in
  `domain/config.py` — see `docs/THEME_CONVICTION.md` §5. For the scorer: a **behavioral** magic-number test
  (a changed cutoff changes a pip) + a **lexical** float-literal scan of `workbench/scoring.py`
  (`tests/workbench/test_scoring.py`).

## 8. The dilution overhang is computed ONCE — never backed out of the clamped severity

`dilution_clock.overhang_pct(facts, asof)` is the **single source** of the raw convert-overhang % —
read by **both** the back-half risk-veto (`dilution_clock.score`) and the Workbench dilution meter. The meter
buckets on this raw %; it **must NEVER recover the overhang from the risk `severity`**, because severity
*saturates*: `severity = min(overhang / dilution_overhang_severe_pct, 1) × risk_block_severity`, so above the
severe bar the overhang is unrecoverable from it. A future reader will be tempted to derive the meter from
the already-computed severity — that is the trap. One raw computation, shared; "—" when there are no live
converts (no fake zero). *(The same one-source discipline as the back half re-deriving firings on read — #4.)*

- *Enforced by:* `signals/dilution_clock.overhang_pct` (the shared pure helper) used by both `score` and
  `workbench/scoring._dilution`; the dilution-meter golden test asserts the raw % bucket
  (`tests/workbench/test_scoring.py`); the existing `tests/signals/test_dilution_clock.py` covers `score`.

## 9. Recall is sacred — a silently dropped name is a system failure

Surfacing every real on-thesis name is the point of discovery. The asymmetry is load-bearing: a
**false positive** (junk surfaced) is **visible and deletable** — a nuisance; a **false negative**
(a real name dropped) is **invisible and gone** — the operator can't evaluate, delete, or even know
about a name they never see. So discovery **optimizes for recall and over-includes**; precision is
handled by the operator deleting visible junk in a lower-confidence tier, **never** by a filter that
might silently eat a real name. (Why this is non-negotiable: this rule has been violated five distinct
ways — the EFTS hit-cap, the pagination cap, the silent-recall fallback, the per-page skip, and a
seed-demotion-to-BROAD — each a plausible local fix that quietly cost real names. Every one passed its
own unit gate; none was caught by anything but a recall re-score.)

Rules this imposes on any change touching discovery / classify / filters / caps / term tiers:
1. **No change may REDUCE recall to gain precision, speed, or cost without PROVING recall is preserved.**
   Re-run the answer-key score; recall must hold (currently 31/32). "Probably fine" / "the dropped pages
   are noise anyway" is an assumption, not proof — and exactly the wording that preceded the misses above.
2. **No name may lose placement/surfacing authority SILENTLY.** If a name changes tier, gets filtered,
   or stops placing, that effect must be VISIBLE — logged, flagged, or surfaced. A demotion the operator
   can't see IS a drop.
3. **Degradation must be LOUD.** A capacity / error / empty condition that can't enumerate the full
   universe must raise or flag (the 503s, `DiscoveryDegraded`), never quietly return a smaller set as if
   complete. Silent fallback to a worse method is worse than an error.
4. **Caps and thresholds are backstops against pathology, never recall limiters.** Default them generous;
   a cap that could drop real names on a NORMAL run is too low — prove it doesn't.
5. **"Make the failure rarer" is not "make the failure safe."** Robustness reduces frequency; it does not
   satisfy this invariant. The failure MODE itself must be visible or impossible.

When this conflicts with tightness, determinism, cost, or convenience: **recall wins, and the trade-off is
surfaced to the operator, not silently resolved in code.**

- *Enforced by:* the answer-key recall re-score on every discovery-touching change (2026-07 live re-score:
  **31/32 holds** — ATAI, the historical miss, is now RECALLED, and the one non-placeable name is PRTG, which
  DELISTED out of SEC `company_tickers`: structurally unplaceable but SURFACED shown-not-placed → counted
  recalled by operator ruling, since #9 tests silent drops, not placeability; the full record is in the
  fixture's header) — the ground
  truth is the committed fixture `backend/tests/fixtures/recall_answer_key.py` (seeds + 32 acceptable-ticker groups
  + the collision-junk set), so the gate is re-runnable; the
  reliability raises (`DiscoveryDegraded` / `DiscoveryEmpty` / `DiscoveryNoTerms` → 503, never a recall
  fallback — `workbench/discovery.py`), with the degraded raise on **post-retry** counts that ride the
  operator-facing message; the per-CIK reconciler that set-difference-guarantees every
  discovered in-master CIK reaches the draft (`workbench/chain_draft.resolve_discovered_chain`); the VERIFY
  tier that surfaces low-confidence adjacents rather than dropping them (`ingest/edgar/fulltext.classify`). The
  full discovery system this invariant governs: `docs/DISCOVERY.md`.
- *Also enforced by (the honest-discovery slice — rules 2/3/4 made structural, not log-only):* the per-run
  **coverage report** (`DiscoveryRun.coverage` → `ChainDraftOut.report` → the Workbench **status strip**) — a
  sub-threshold EFTS gap used to pass looking complete (a log line the operator never reads); now pages
  ok/attempted + the failed TERMS ride every draft, after **one politeness-budgeted retry pass** over the
  failed subset (a recovered page-0 also fetches the deep pages it owed — the silent-partial trap, pinned by
  `test_discover_retry_recovered_page0_fetches_its_deep_pages`); the **hit-cap flag** (`capped_terms` + the
  `⚠ capped` chip — rule 4's "hitting the backstop goes on the record", `test_discover_reports_capped_term`),
  now **per tier** — SIGNAL deep / BROAD shallow (`discover(broad=, broad_hit_cap=)`; a term in both tiers
  enumerates deep; each capped term flags independently inside its own tier list,
  `test_discover_per_tier_caps_flag_each_tier_independently`): against the single-cap era BROAD is unchanged
  and SIGNAL only deepens, so the default universe is a **monotone superset**, never a drop — proven by the
  answer-key re-score at both configs, not asserted;
  the **tail-sweep tri-state** (`TailSweep.status` — a LOST sweep reads `failed`, never conflated with
  "ran-and-found-nothing" or the deliberate no-key `skipped`); the **narration fill count** (M of N on the
  report); and the **single-worker startup guard** (`draft_jobs.assert_single_worker`, the app lifespan +
  the Dockerfile's explicit `--workers 1` — >1 worker silently broke the 409 guard and job polls).
- *Also honored by (SURFACE/TRIAGE):* the **off-thesis flag** RECOMMENDS removal but the name **STAYS PLACED** —
  a flagged name is never a silent drop, the operator prunes it (`CHAIN_DRAFTER.md`). In **TRIAGE**, every "hide"
  is a visible, reversible, still-promotable collapse (excluded rows, the To-Review "Low signal" / "No listed
  ticker" drawers) and the sort/filter VIEW never changes what Save persists — precision is the operator pruning
  visible names, never a filter that silently eats one (`TRIAGE.md`).
- *Also honored by (R8-A — Form 4/A amendments and the other-issuer screen):* no insider row is DELETED and no
  amendment is silently dropped. A 4/A is stored as filed (never written into, never subtracted from,
  `fact_insider_txn`); what it does is decided per disposition and SHOWN on Admin with its evidence — every
  amendment stays listed. Since R8-B (2026-10-20, revised 2026-10-21) every amendment resolved to a definite
  original is resolved by the system (an append-only `system:*` decision) and LISTED on Admin as "Resolved
  automatically" with its evidence and its exact Undo, never hidden — what an applied void hides is suppressed
  at read, never deleted. What the system cannot resolve is never system-resolved: an AMBIGUOUS link, an
  unresolved row match, a held void — owed the operator when it touches a buy/sell row inside the review
  horizon, otherwise listed as "unresolved — historical" with its originals standing (hidden from the PAGE,
  never from the panel). **No buy or sale is dropped that the filer did not account for (operator decision
  2026-10-21):** an applied partial / consolidation / addition / restatement / field change replaces only the
  original rows it accounts for by EXACT shape (identical, a price / share / date fix, a recode, a same-day
  sum) or that the filer's own FIX words restate; ADD words keep every original (IBKR's omitted sale beside two
  real same-day sales — the old same-date rule replaced both, $3,275,356); words that neither fix nor add
  leave it to the operator, the original standing (YYAI). **A zero-row VOID removes its original only when its
  own words retract a trade AND the original holds a single `P`/`S` transaction** (FLS, AVEX, MNKD, DVLT ×2);
  no retraction words → the original stands (the 11 holdings / Table II / power-of-attorney fixes inside the
  horizon — SMR, AZTA, VWAV, HIMS ×2, …); retraction words against several trades → held for the operator, the
  original standing (FIGR's $7.43M sale). The phrase lists are deterministic and named in `FEED_LOOP.md` §R8-B.
  **No applied amendment's rows are dropped either (2026-10-21):** the resolver COMPOSES every applied amendment
  of one original in SEC-acceptance order — a whole replacement supersedes the original and every earlier
  amendment, a row-level one replaces only the earlier rows of the same trade it accounts for — where it used to
  emit only the newest and silently lost an older amendment's rows (COIN, AVAV, AIR; AMSC after the R8 re-link).
  *Enforced by:* `tests/db/test_insider_amendments.py::test_coin_two_row_level_corrections_of_one_original_both_stand`,
  `::test_amsc_amendments_of_different_trades_of_one_original_all_stand`,
  `::test_lfst_a_second_correction_of_the_same_sale_replaces_the_first`, and the mirror's parity seed.
  *Enforced by:* `tests/db/test_insider_amendments.py::test_the_real_row_check_table` (each case on its real
  filing), `tests/domain/test_insider_amendments.py` (the `match_rows` / `void_reading` contracts). Rows stored
  before the
  other-issuer screen existed are KEPT and screened at read (keep-and-screen); the screen keeps any row whose
  issuer or member CIK is unknown, and counts every CIK the security has been known by (the T7 change log),
  so a successor-CIK re-point never hides the predecessor's own history. A filing the ingest no longer stores
  is recorded as a `form4_other_issuer` skip, never silently. *Enforced by:* `tests/db/test_insider_amendments.py`
  (the stored rows are counted before and after; the screened rows are still in the table).
- *Also honored by (joint reports, 2026-10-21):* counting a trade once drops no trade and no NAME. The survivor
  carries every reporter's name (the call label "counted once: A / B (joint report)", the Scoreboard ledger, the
  Cockpit flow note), role (seniority — never demoted) and filing (cited in the provenance); the other reporters'
  rows stay stored. Two DIRECT holders are never merged (two persons' own positions — ALAB's co-founders), rows
  of one filing never merge (separate lots), and a row without the ownership evidence never merges — so an
  unrepaired row can only stay double-counted, never vanish. *Enforced by:*
  `tests/ingest/test_form4_joint_reports.py` (the real-filing table: INFQ, AMKR, SMCI, OKLO; the ALAB and TSM
  negatives), `tests/domain/test_insider_joint_reports.py` (the rule's contract).
- *Also honored by (internal transfers, 2026-10-21):* screening a transfer drops no row and hides no leg. Every
  leg stays on the read, TAGGED; the detectors name what they left out ("…; 2 internal-transfer txns ($X)
  screened"), the Cockpit flow names it in its basis, the Scoreboard ledger shows each leg grayed with its
  counterparty ("internal transfer within the filer group (with …) — not counted"). A private purchase between
  unrelated people is never screened (it stays counted; its ledger note is display only), a buy and a sale with no
  linking evidence in their own filings stay counted, and a row without the 0055 capture links nothing by name —
  over-counting is the safe direction. *Enforced by:* `tests/ingest/test_form4_internal_transfers.py` (the
  real-filing table: AMKR, SLSN, Pershing Square, ValueAct, Goldman; FTHM / RDNW / ASTH / FRMM / MGM counted;
  the joint-report cases untouched), `tests/domain/test_insider_internal_transfers.py` (the rule's contract).

## 10. The LLM recommends; the operator decides — a recommendation is pending until confirmed, never auto-applied

The invariants above forbid the LLM **deciding** — sourcing a number (#1), deciding a mapping (#2),
firing a trigger, setting a tier that places names. They do **not** forbid the LLM **recommending**: a
**visible, pending** suggestion the operator confirms. The danger was never the recommendation — it was
a recommendation being **auto-applied**. A recommendation that changes nothing until the operator
confirms cannot cause the silent flood or silent drop the other invariants exist to prevent. So the LLM
may be maximally helpful — flag, suggest, recommend a tier — as long as the operator's confirmation is
what acts.

**The boundary:** a recommendation is the model's *loudest possible disagreement that still changes
nothing on its own.* The moment a suggestion can act without operator confirmation, it is a decision,
and the other invariants apply.

This is a **clarification of restraint, not a relaxation.** "Never decides" means never **autonomously
or silently** — not "never informs a decision the operator makes." The platform already runs on this
pattern: the chain drafter PROPOSES names, the operator RATIFIES; exact membership still DECIDES (#2).
A tier recommendation is the same shape one level up.

What this does NOT loosen (state it so the freedom isn't misread): no LLM-proposed term ever places
autonomously; no model-sourced number (#1); recall stays sacred (#9). On confirm, **authorship transfers
to the operator** (`system_drafted` recommendation → `operator_edited`) — so the record shows the
operator as the decider, and a confirmed recommendation is operator-authored and **stable across a
regenerate** (it does not return determinism to the model).

The illustrating case is the *valuable* one, not the defensive one: the model surfaces a discriminating
term the operator **didn't** seed, recommends it as SIGNAL, shows why — and it sits as an opportunity
until the operator adopts it. The project's core value (surface what the operator missed) and its core
restraint (the LLM never autonomously places) live in the same act; pending-until-confirmed is what lets
them coexist.

- *Enforced by:* the authorship model (`system_drafted` → `operator_edited` on confirm; the #100
  diff-stamp `stamp_edited_term_set`, `tests/workbench/test_term_set.py`); the chain drafter's
  propose-then-ratify seam (`workbench/chain_draft.resolve_placements`); the rule that no recommendation
  is persisted as a tier or auto-applied (a recommendation rides display-only, like the `matched_terms`
  provenance tags, never mutating `authored_by` until the operator acts).

**What #10 does NOT cover — a filer's own Form 4/A (R8-B, operator decision 2026-10-20).** The system applying
a Form 4/A that the deterministic linker resolves to the Form 4 it amends is not an auto-applied recommendation:
no model is involved, and the content is the insider's own legal correction of their own filing, filed under
penalty. Applying it is DATA HANDLING (like storing the original Form 4), recorded as an append-only system
decision (`system:filer-correction` / `system:no-signal-effect`, or `system:no-retraction` / `system:held` for a
void it keeps), listed on Admin with its evidence and an exact Undo. Where the filer's own words settle a
leftover or a void, they are read by a deterministic phrase list — a located passage, never a model's reading.
The operator rules only on what the system cannot resolve — an ambiguous link, an unresolved row match, a held
void — when a call can read the rows it touches, with three verdicts (Use the correction / Keep the original /
Add to the original). `CALL_LOGIC.md` §3 insider; `FEED_LOOP.md` §R8-B. The R8 linker fix's two rules are the same
kind of data handling (`FEED_LOOP.md` §the R8 linker fix): an original made public after its amendment is refused
by the SEC's own acceptance instants, and an ambiguous set is settled only when exactly one candidate holds a row
the amendment accounts for by exact shape — no words, no model; anything else stays the operator's, and a settled
link keeps every candidate on show with Keep the original as its Undo.

**The #10 family — the shipped recommend→confirm seams** (the pattern the frame calls out, `STAGE_MODEL.md`): the
**tier recommendation** (SIGNAL/BROAD per term — `DISCOVERY.md`), the **off-thesis flag** (the narrator's opinion —
`CHAIN_DRAFTER.md`), the **grounded purity estimate** + the **market-cap estimate** (SURFACE values —
`WORKBENCH_EXTRACTION.md`), and the **derived archetype** (`WORKBENCH_ENRICHMENT.md`). Each is the same shape: a
visible, pending recommendation that changes nothing until the operator confirms, on which authorship transfers to
the operator (`system_drafted` → `operator_edited`). Every stage boundary is one of these handoffs.

**The archetype's single home (item F, the three-gate round).** The recommend→confirm seam only reads cleanly when
"my decision" and "the system's suggestion" can't blur — so the archetype is **decided ONCE, on the finalize rail**
(the DDRail hint → the operator applies it or picks manually; either way `operator_edited`). Placement **never
stamps a default**: a placed-but-not-finalized member carries `archetype = NULL` end-to-end (nullable column,
optional on the models, never coerced on save) — the old hardcoded `high_beta` placement default asserted the most
aggressive risk class before anyone decided, and once saved it read as an operator decision that never happened.
Un-decided is un-decided all the way through the spine. *Enforced by:* migration `0018_archetype_nullable`;
`tests/repositories/test_thesis_repo.py` (null round-trip, never-coerced resave) +
`tests/app/test_workbench_api.py::test_promote_and_scored_carry_a_null_archetype`; the editor/AddName carry no
archetype control (FE tests).

## Operating principles + known gaps

Not code-enforced invariants — a principle the cron-freeze episode earned, and a correctness gap the code does
**not** yet honor. Full account: `POSTMORTEM_CRON_FREEZE_2026-07.md`.

**A structural guard firing is information, not an obstacle.** R6's `already_ran_live(asof)` — built only to
answer "did today's run already happen" — returned `False` after a visibly-clean run and thereby surfaced the
timezone bug (#202), a defect it was **never designed to detect**. It caught it because it asks a question with
a checkable answer. When a guard/assertion you added fires unexpectedly, the default is "the guard found
something," not "the guard is in the way." This is the argument for building the boring instrument (the run
log, the fetch counter, the recording gate): they pay off outside their brief.

**Closed (R8-A, 2026-10-17 / 18) — an amended-away insider buy no longer fires Key-1.** Form 4/A amendments
were not reconciled (the 4/A was never ingested at all, and `accession` in the natural key meant a naive ingest
would double-count), so a cancelled code-`P` buy stayed live to the conviction detector (FLS's $164k director
buy, cancelled by the broker, was a CORE trigger). R8-A stores each 4/A as filed in `insider_amendment` (never
in `fact_insider_txn`), links it to its original, and resolves every as-of insider read through one pure
resolver shared with the replay mirror. Since R8-B (2026-10-20, revised 2026-10-21) every amendment resolved
to a definite original is resolved by the system — the filer's own correction, replacing only the original rows
it accounts for (exact shape, or the filer's own FIX words), and a zero-row void removing its original only on
explicit retraction words against a single-trade original — and listed with an Undo; only an ambiguous one, an
unresolved row match or a held void, touching a buy/sell inside the review horizon, waits for the operator. The
`supersedes` column is still vestigial. See `DATA_SOURCES.md` §Form 4/A, `CALL_LOGIC.md` §3 (insider) and
`FEED_LOOP.md` §R8-B.

**Known gap — basket composition is point-in-time from 2026-09-15 FORWARD, and unknowable before it.**
`basket_member` is full-replace on promote with no timestamps, so which names were in a basket on a past
night could not be known at all. `basket_snapshot` (migration 0043) versions the roster — one jsonb row per
promote — and `thesis_repo.get_asof(known_at)` reads the latest snapshot with `taken_at <= known_at` (no
lookahead, #1). **All four recompute surfaces are now wired to it**: the single assembly funnel
`pipeline.call_for_thesis` (serve / `pipeline.run` / the nightly record / `pipeline.backfill`) via F12, and
the replay harness via F4 — which resolves the roster PER SESSION at
`min(pin, known_at_for_asof(T))`, the same market-day cap #4 uses, because the harness pins one `known_at`
for the whole sweep and resolving once at that pin would hand the `now`-pinned Scoreboard panel today's
basket.

**What remains a counterfactual, and is LABELED rather than assumed away:** snapshot history begins
2026-09-15, so any recompute reaching further back still runs TODAY's roster. That fallback is correct (#9 —
never replay a thesis with an empty basket for want of its history) and is now said out loud:
`replay_all` returns a `ReplayResult` carrying a per-thesis `RosterSource(source, fallback_days,
total_days)`, `replay.run` prints it, and the Scoreboard's replay banner names it per run instead of
asserting the old blanket "baskets are not versioned" (which is no longer true). The Board/Cockpit/Workbench
scrub-back and `pipeline.backfill` keep their own labels (#4 above; `FEED_LOOP.md` — which is why a
reconstructed row is reported, never scored). The ONE surface immune throughout is the Scoreboard's record
path: it scores recorded cards, never a recompute, and excludes reconstructed rows.

**Still true, and unchanged by F4:** the rest of the thesis definition (narrative, catalysts, kill criteria)
and `security_master` are read from the CURRENT SoR on every recompute — only the roster is versioned. And a
reconstructed row still never counts: a pre-2026-09-15 night has no roster to reconstruct against. No surface
may present a past-asof recompute as "what the platform said"; that is the Scoreboard's record.
