# The Scoreboard — SCORE, the forward trust loop

The sixth stage: forward attribution over the platform's own record. Two tracks in v1 — **the
platform's calls** (the daily call-of-record, scored against realized prices on its own yardstick)
and **the operator's decisions** (the append-only decision log, joined to the episodes it answered).
The follow-blindly counterfactual track and its deltas are **v2** (additive on the same computation);
the immediate follow-up after v1 was surfacing **replay's historical episodes alongside** the live
record (clearly separated — the record stays clean); **editions** have since replaced that panel (a stored,
frozen re-run of today's rules, §Editions). "The platform feeds itself" became true at M2;
*this* is the instrument through which forward evidence accrues. Its existence — or a small sample crossing
a UI gate — does not by itself make the platform "validated forward."

> **Freshness caveat on the early record.** The record began when the daily cron first wrote (2026-07-10 in
> production), but the cron's insider data was **frozen** on a cache-first-forever EDGAR cache until R1's
> key-classed 12h TTL (#196, 2026-07-17). So "feeds itself" became true as a *record* at M2 but as *fresh
> insider data* only at #196 — the earliest cards were built on stale insider indexes. Full account:
> `POSTMORTEM_CRON_FREEZE_2026-07.md`. **Now marked per-episode (Slice 3):** the record-provenance flags
> below carry this caveat onto each episode (`ingest_flagged` + the INGEST badge) — freeze-era and
> thawed/partial-ingest arms stay ledger-visible but are excluded from the aggregate metrics.

Status: **v1 built** — SB1 (the scoring engine + CLI) + SB2 (`GET /scoreboard` + gated metrics) + SB3
(the operator track) + SB4 (the FE view: the ledger behind the Scoreboard nav, `frontend/src/scoreboard/`).
**RH (replay-alongside): built, then RETIRED by editions S8 (2026-10-23)** — the snapshot CLI, `GET
/scoreboard/replay`, its artifact reader and the collapsed panel are gone; a stored edition replaced them
(§"The historical panel" says what stayed). **Editions: S1–S4 + S6 built** (S6 = "what changed").
**The episode drill-down: built** — the row-opened drawer + four timing lenses + Summary|Timing toggle
(#227–228), the row/icon split (row → drawer, ↗ → Cockpit; #230), the episode price chart with a numbered
event overlay (SMA context + insider/trigger/lifecycle chips, relevance-floored + as-of-correct on both axes;
Slice A), and the event ledger sharing the chart's numbering + a Cockpit strip (Slice B). See
§"The episode drill-down".

## Editions — Edition 0 is the record; an edition >= 1 is a frozen re-run (editions S3/S4)

The Scoreboard shows one EDITION at a time (`GET /scoreboard?edition=N`, the header's quiet switcher, the
URL's `?edition=`). Everything below this section is **Edition 0, the original record**: the `calls` log, read
exactly as before editions existed (a golden taken before the change pins it byte for byte,
`tests/app/test_scoreboard_edition0_golden.py`). Edition 0 is never stored as an edition.

**Edition N >= 1** is a re-run of TODAY's rules from the night each company joined its basket, judged on the
PUBLIC clock (`docs/INVARIANTS.md` §4), frozen at its CUT and stored in its own append-only tables (migration
`0063`: `edition`, `edition_event`, `edition_entry` = the join dates it was cut with, `edition_call` = one
page per thesis-night). It is published by `python -m editions.run publish` (dry-run by default; `--apply`
writes; a second publish of the same edition is refused and appends nothing) and **never writes `calls`**.
The read is the same code with a different timeline source (`scoreboard/record.py`: `RecordSource` vs
`EditionSource`): an edition reads its pages only, so a `calls` row appended later never moves it. What differs
for an edition, and only this:

- **No reconstructed, annotated or late rows** — every page is computed the same way, on the public clock — so
  those lists come back empty, both read policies say `n/a`, and the page hides their lines and toggles.
- **No ingest provenance** — the INGEST badge describes what *we* had fetched; a public-clock re-run has none,
  so it never renders.
- **Censoring is G12's:** an arm on the company's own ESTIMATED join night has an unknowable true arm date,
  so the episode is `censored_start` (out of arm-anchored metrics). An arm on an exact join night is real.
  The drawer says "join date estimated" (`edition_join.note`) for every estimated join.
- **The drawer's provenance caption names the edition and its engine fingerprint** (the `run_identity_note`
  slot), and its chart overlays read on the public clock (the same reads #433 gave a past as-of).
- **A matured episode's score is INKED** (S5, migration 0064 `edition_outcome`): once its signal window ends
  (`exit_by`, never the de-arm) and the tape has printed a session past it, the nightly page job scores it once and
  freezes it, and the edition read uses that number from then on (a later price correction never re-scores it).
  A running episode — or a view scrubbed back before the window ended — is scored on read against today's
  realized prices, as before.

The CURRENT edition grows nightly: the cron sidecar appends tonight's page (and a catch-up page for any session it
missed) per live thesis after the nightly backup, so new theses and new companies join it from their own first
night (`FEED_LOOP.md` §the editions nightly page).

**The default is the NEWEST published edition** (operator decision 2026-10-23: "edition 1 is now kinda like
main, so the menu should just link to that by default"). `GET /scoreboard` and `price-window` with no `edition`
read it, `GET /scoreboard/editions` reports it as `default` (0 only when nothing is published), and the top
nav's plain `/scoreboard` lands on it; when a new edition is cut the default moves to it with nothing touched.
Edition 0 stays one click away in the switcher, and `?edition=0` still works. `Settings.scoreboard_default_edition`
(`ALPHADECK_SCOREBOARD_DEFAULT_EDITION`) is an optional OVERRIDE, unset by default: 0 pins the record, N pins
Edition N, and an override naming an unpublished edition falls back to the newest (the default view never
404s), while an explicit request for a missing edition is a 404.

### The automatic cut (S7) — when a new edition is cut, and why  `[BUILT 2026-10-23]`

An edition is never edited. It is REPLACED by a new one only when one of two things happened since its cut —
**late data alone never starts anything** (it simply reaches the next edition): `editions/cut.py` has the
whole contract; it runs inside the nightly page job, before tonight's page (`FEED_LOOP.md`), and by hand as
`python -m editions.run cut [--apply]` (dry-run by default) — e.g. right after a repair.

- **(a) The rules changed.** The running engine's fingerprint (`editions/fingerprint.py`, version 2: the
  normalized AST of the call path with comments and docstrings stripped, `domain/settings.py` and the rule log
  left out, the two `Settings` values the engine reads hashed by value — so a comment or docstring edit never
  counts) differs from the last one CHECKED for the current edition E: E's own, or the one the latest
  `trial_no_change` on E recorded. A fingerprint made by an older fingerprint VERSION counts as a change (it
  cannot be compared); the first trial records the current one.
- **(b) A data repair ran on purpose.** A `data_repair` journal row (migration 0065) applied since E's cut (or
  since its last no-change trial) that changed rows. Every repair CLI that rewrites fact history journals each
  `--apply` / `--revoke` (`repositories/data_repair_repo.py` lists them; a dry run journals nothing). Nightly
  data flow (`detect_splits`) is not a repair; tools that only touch `calls` are not either.
- **The rules trial (P)** re-runs E's RE-RUN pages (the nights E's publish computed) with the current engine at
  `known_by = E's own cut`, E's frozen join dates and E's frozen thesis bodies (kill criteria + catalysts; an
  edition published before S7 froze none — its comparison then leaves out `counter_case` and
  `catalyst_surface`, the two fields a body feeds, and says so). That is the data E was saved from, held fixed:
  the edition's own freeze point, not a replay of history on our store clock. Compared page by page (canonical
  card equality, money to the cent). **Named limitation:** E's nightly and catch-up pages are NOT re-tested —
  each was computed at its own night's cut, so there is no single cut to hold their data at.
- **The repair trial** compares N (below) with every page E holds: a repair's rows are stamped after E's cut, so
  only N sees them — and N cannot separate the repair from data that arrived late, so the label says so.
- **The decision.** P differs → a new edition. A repair pending and N differs on a night E covers → a new
  edition. Otherwise `trial_no_change` on E (the fingerprint, the rule-log ids, the repairs checked) — never
  tried again for the same change — and tonight's page goes on E as usual. At most one new edition per market
  day; never two at the same cut; a cut waits while a member of N's rosters still has history loading.
- **The new edition N** is today's engine at `known_by = now` over every thesis from each company's join date —
  Edition 1's own path (`editions.run.store_edition`): `rerun` pages, E's join dates carried over (an exact
  snapshot date for a company E never had), the thesis bodies frozen with it, and in the SAME transaction E's
  `closed` event and E's ink (an episode still running at E's last night is inked `running_at_close`). E stays
  viewable; the default moves to N on its own; N already holds tonight, so the job stops paging.
- **Labels.** "rule change: ‹label›" (declared in `domain/rule_log.py`: an entry in code that E does not carry),
  "unexplained change" (no declared rule: published MARKED — causes `code_drift` — and `notify_health` pages),
  "data repair: ‹summary›" — each followed by "includes data that arrived since Edition E". A deleting repair
  riding the same trial as a rules change makes P's differences `not_separated` (a deletion is visible even at
  E's cut), and nothing pages.
- **The notice.** For seven days after a cut, one quiet line under the switcher ("Edition 2 · rule change: … ·
  37 company-nights changed · see what changed"), freshness-line style, never blinking, nothing stored in the
  browser (`GET /scoreboard/editions` → `notice`), one line on Admin (`editions_cut`), and one plain Slack line
  (`Notifier.notify_edition`, no emoji; an unexplained change pages instead).
- **Cost (MEASURED on dev 2026-10-23, 659 pages over 10 theses, 4 workers):** P ≈ 265 s (export 64 s + re-run
  192 s); N ≈ 278 s; both ≈ 9 min on a night with an event, nothing on any other night. Dev's Edition 1 (cut
  before S7, fingerprint version 1): P == E on all 659 pages, with the body fields left out AND included, and N
  == E too (dev holds no data newer than its cut) — the first trial after deploy records `trial_no_change`.

### What changed (S6) — one edition against the one before it

`GET /scoreboard/editions/diff?from=A&to=B` (A < B; Edition 0 allowed as `from`) compares two editions **on
read** — nothing is stored, nothing is written (`scoreboard/diff.py`). The header shows a quiet "what changed
since Edition A" link beside the switcher whenever the viewed edition has one before it (so never on Edition 0,
and never with a single edition); it opens a drawer listing the changes in plain English, thesis → company,
with the counts on top. Every thesis is listed (an unchanged one says "no change"), only the companies that
changed are listed under it (the rest are counted), and nothing in it is colored — a comparison is context,
never an alarm (#7).

- **Edition 0 is read the way the default Scoreboard reads it** — its HONEST default read: reconstructed rows
  never, annotated and late rows under the server's default policies.
- **The window.** Through the earlier of the two editions' last nights — the earlier edition's last night once
  the later one has reached it (the designed case: a closed edition against the one that replaced it) — and,
  per thesis, only the nights BOTH editions cover. Pages only one edition holds (Edition 1's nights before the
  record began, say) are reported as spans; a run lying wholly on them is counted as `outside`, never called a
  change, while one that armed there and runs into the shared nights is listed (`armed_before_window`).
- **Per company-night:** armed or not in each edition; on a night both arm the company, its verdict, entry
  grade, conviction grade and exit-by. A night with no page reads that edition's last page before it (the
  record last spoke on the prior row), so a cron gap is never a change. Consecutive identical changes fold into
  `runs`, so 30 nights read as one line.
- **Per episode**, the Scoreboard's own episodes paired on (thesis, company, arm date): `matched` (listed only
  when the run differs — de-arm, exit-by, verdict, grades or score), `moved` (no same-night partner, but the
  same company armed in both over overlapping arm → exit-by windows; nearest arm date first), `appeared`,
  `vanished`. Each side carries its score (arm → exit-by on realized closes through the last night, the
  Scoreboard's own reader) and a pair carries the delta.
- **Substance only** — state, the armed set, verdict, grades, exit-by and scores; never card text.
- **`cause`** (S7) is filled only when the later edition was cut by the automatic cut from the earlier one (its
  parent; the response's `causes` is true): a change the rules trial proved carries `rule_change` (declared) or
  `unexplained`; a change on a night the rules trial re-tested and found unchanged carries `late_data`; a repair
  trial's changes carry `data_repair` (a repair or late data — not separable); a change on a night the rules
  trial did not re-test (a nightly page) carries `not_separated`. The drawer says each in plain English. Any
  other pair of editions has no single trial, so `cause` stays null — a cause is never guessed. A folded run
  only holds nights with the same cause.
- **Cost.** Two snapshot timelines per thesis plus one tape read per company. The comparison reads the cards
  without the keys a snapshot never uses (`SNAPSHOT_UNREAD_CARD_KEYS`: risk signals, triggers fired, counter
  case — 61 of 73 MB on dev's Edition 1) and loads each company's tape once (`MemoPrices`); tests pin that both
  give exactly the answers of the full read. MEASURED on dev, Edition 0 vs Edition 1 (12 theses, ~640
  thesis-nights): ~3.0 s median per request (8.7 s before those two), a ~1 MB response.

## The one rule everything hangs on

**The record is the scoring source — never a recompute** (Edition 0; editions are the section above). The
Scoreboard reads the immutable `calls`
log (what the platform actually said, when it said it) via `calls_repo.latest_for_thesis` (the
final card per as-of), and scores those cards. Re-deriving past calls with today's code/dials is
replay's job (`docs/REPLAY.md` — the historical twin); attribution's source is the record
(`docs/BOARD.md`). Consequences, all deliberate:

- **No backfill into the score.** The record began when the daily cron first wrote (2026-07-10 in
  production); earlier history is replay's domain. An empty early Scoreboard is the honest launch
  state. A missed night CAN be reconstructed (`pipeline.backfill`, `docs/FEED_LOOP.md`) — but a
  reconstructed row is reported, never scored (next bullet but one).
- **Censored starts.** An episode already armed on its thesis's *first recorded card* has an
  unknowable true arm date (`censored_start`) — shown in the ledger ("record began mid-arm"),
  **excluded from arm-anchored metrics**, never reconstructed.
- **Gaps are fine.** The log is dense per cron day (a new as-of always appends; `record_if_changed`
  dedups same-as-of only); weekends/downtime leave gaps, but episode boundaries stay exact because a
  membership change always recorded a row that day. `derive_episodes` consumes the gapped timeline
  as-is.
- **Reconstructed nights are reported, not scored.** `pipeline.backfill` reconstructs a missed night
  with `known_at` pinned — faithful on the clock, but every reconstruction runs on TODAY's basket
  (`basket_member` is full-replace, no timestamps: the roster on a past night is unknowable), and until
  the existence gate landed it also ran theses that did not exist yet. So a reconstructed row can never
  be shown honest, and **a reconstructed row never defines an episode boundary**: the record read
  (`thesis_timeline` → `calls_repo.latest_for_thesis(include_reconstructed=False)`, paired with
  `ingest_health_for_thesis` under the same filter) drops rows marked `calls.reconstructed` (migration
  0042) BEFORE the per-as-of dedup, so `derive_episodes`, `score_episode`, the metrics, the arm-day
  trigger enrichment, and the de-arm detail all see ONE honest list, and a night carrying both a
  nightly row and a later reconstruction scores the nightly row. A gap left by the exclusion reads
  exactly like a cron gap — the record last spoke on the prior honest row. The rows stay in the log
  (reversible; the evidence the backfill happened) and the summary names, once, the nights the record
  has NOTHING honest for — every row on the night reconstructed (`reconstructed_nights`, asof-capped;
  the ledger banner's one quiet line). A night that carries an honest row beside a reconstruction is
  scored from the honest row and is NOT listed (MEASURED on prod: 2026-09-09 had 8 reconstructed and 17
  honest rows and 13 episodes arming honestly — the first cut wrongly named it "not scored"). Never a
  per-row chip,
  because with the filter in place a reconstructed row produces no ledger row. This is the treatment
  the (since retired) historical panel got: a recompute is never the record, never pooled. MEASURED when the
  rule landed (dev copy, 2026-09-12): of the 106 episode starts that sat on a reconstructed night, 48
  survive with a later (honest) arm date, 56 drop (armed only on reconstructed rows), 2 drop (the
  thesis did not exist); August 7, a reconstructed night, had been the single largest arm date on the
  whole ledger. Pre-creation rows are additionally deletable by the operator
  (`pipeline.repair_reconstructed_precreation`, dry-run by default; `docs/FEED_LOOP.md`).
- **Phantom-arm (annotated) nights are set aside by a READ POLICY — reversibly (D3b).** An honest row can
  still be wrong. Before D3a (#390) the fundamentals read dated a re-reported comparative revenue quarter at
  its LATER filing, so some nightly arms rested only on a year-old inflection re-fired as fresh — phantom
  arms. The operator decided (Q2, 2026-10-16) to annotate them and exclude them from scoring, built so the
  decision can be flipped at any time. The mechanics mirror the reconstructed rule exactly, with one
  difference: reconstructed is a FACT about a row (always filtered), annotated is a JUDGMENT (filtered by
  policy).
  - **The annotation, never an edit.** `pipeline.annotate_phantom_arms` appends rows to `call_annotation`
    (migration 0045 — append-only, the `no_update` trigger) beside the `calls` row; `calls` is never touched.
    `exclude` = a phantom arm (the row armed, and with D3a's dating the same night is not armed); `note` =
    the arm stands but D3a changes its composition (verdict / headline / armed set) — it stays scored and
    shows only as a quiet caption in the episode drawer; `revoke` = withdraws one earlier annotation. A row is
    annotated ONLY for what D3a changes: the CLI re-assembles each honest row twice at its own `(asof,
    known_at = recorded_at)`, D3a off and D3a on, and a row whose D3a-off re-assembly does not reproduce the
    recorded state, verdict and armed set is DRIFT — reported, never annotated (`docs/FEED_LOOP.md`
    §"Annotating phantom arms").
  - **The policy.** `Settings.scoreboard_exclude_annotated` (env `ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED`,
    default `true`): rows carrying an ACTIVE `exclude` are dropped from `thesis_timeline`, the ingest-health
    stamp and the run-identity read — inside the WHERE, BEFORE the per-as-of dedup
    (`calls_repo._annotated_clause` beside `_reconstructed_clause`), so all three still describe one winning
    row. As with reconstructed rows, an excluded night reads like a cron gap (the record last spoke on the
    prior row), and a night with another honest row is scored from it. `note`s never filter.
  - **Both readings, side by side.** `GET /scoreboard?include_annotated=true` scores the annotated rows
    (exactly as before D3b), `=false` sets them aside, omitted = the setting; the summary says which reading
    it is (`annotated_policy: "excluded" | "included"`) and lists the nights either way (`annotated_nights`:
    thesis, as-of, reason, rule). The ledger renders one quiet line — "N phantom-arm nights set aside · not
    scored" (or "… included in this reading · scored") with the nights on hover; since F10 it names the KIND
    by each night's rule family ("N no-session nights …", or "N annotated nights (A phantom-arm, B no-session)
    …" when both are present) — and an **include annotated nights** toggle that flips the reading for that
    view; the next click returns to the default. Rendered only when some night is annotated. `python -m scoreboard.run --include-annotated` is the CLI twin.
  - **Reversing it.** Globally: flip the setting (`ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED=false`) and the
    record scores every annotated row again — byte-identical to the pre-annotation reading
    (`tests/pipeline/test_annotate_phantom_arms.py` pins it). Per row: `python -m
    pipeline.annotate_phantom_arms --revoke <annotation-id> --reason "…" --apply` (or `--revoke-all`)
    appends a revoke; a revoke sticks across re-runs of the annotator (a (call, action) its rule family
    already judged is never judged again). Nothing is deleted either way.
  - **No-session nights ride the SAME exclusion (F10).** A row dated a day with no market session — the daytime
    hand runs before the September fix, recorded under the date they ran (MEASURED on dev: 49 rows dated a
    Saturday or Sunday, 2026-08-16 .. 09-05, and 11 dated Labor Day 2026-09-07) — has no bar and no next
    session to score against, yet the Scoreboard has no weekday filter. `pipeline.annotate_no_session_rows`
    appends an `exclude` (reason `no_session_day`, rule `no-session-night@<sha>`) to each honest row for which
    `not domain.trading_days.is_scheduled_day(asof)`; it keeps no calendar of its own, so the holiday rows join
    when that predicate becomes holiday-aware. **There is ONE `include_annotated` toggle, and it covers EVERY
    active `exclude`, whatever its rule** — phantom arms and no-session nights flip together (no second toggle);
    `annotated_nights` lists both kinds (the `rules` field says which), and the episode drawer's caption names
    the kind by the annotation's rule family: "phantom-arm night(s) …", "no-session night(s) …", or the generic
    "set-aside night(s) …" for any other rule. Reversal is the same: the setting off, `include_annotated=true`,
    or `--revoke` / `--revoke-all` on that CLI (it revokes only its own family).
  - **Named limitation — the exclusion is ROW-level (the thesis-night), never member-level.** A night where
    D3a removes SOME armed members but the thesis stays armed is a `note`: the row stays scored, so those
    members' arm episodes stay on the ledger and in the metrics, marked only by the drawer caption. MEASURED
    (prod dry-run): 82 of the 106 notes drop at least one armed member — 147 member-nights over 18 (thesis,
    member) pairs (e.g. AI Memory & Storage's DELL / HPE / NLST before 2026-10-14). Setting those aside
    would need an episode-level filter — a separate operator decision, not built.
  - **Name the reading when quoting.** Any Scoreboard number quoted outside the app (e.g. in the expansion
    talks) must say which policy produced it — "phantom-arm nights excluded" or "included" — because the two
    readings differ by design. The API carries `annotated_policy` on every response for exactly this.
  - MEASURED (prod dry-run, 2026-10-16, read-only): 630 honest rows → 7 `exclude` (AI Memory & Storage
    2026-10-14/15, Nuclear Buildout 2026-10-14/15, Digital Asset Treasury Complex 2026-09-15/16 and
    2026-10-05), 106 `note`, 103 drift (all pre-2026-09-15 rows re-assembled on the LIVE roster; none of them
    a would-be exclusion), 414 unchanged. Nothing was applied; `--apply` on prod is the operator's call after
    a backup.
- **Late-recorded rows never define an episode — a READ POLICY over a DERIVED fact, reversibly (L1).** A
  call-of-record recorded after its night closed can have read what the night could not: MEASURED on
  2026-10-12, the 22:30 pass died after 4 theses, and the next afternoon's boot catch-up recorded the night
  for 9 theses at 17:04–17:13 ET on 10-13 — ~11 h after the window closed — three of them citing Form 4s SEC
  accepted on 10-13 (Modern Defense's HEI, Nuclear Buildout's IMSR, The Everything Exchange's API/UXIN). The
  operator decided (Q5 = A, 2026-10-16) that such a row never defines an episode, with on-time beating late.
  - **What "late" means.** A NON-reconstructed row whose `recorded_at` is at or after the END of its night's
    on-time window — 06:00 ET on the next weekday, the EDGAR open (`pipeline.night_coverage.is_late`, the
    window M2's catch-up skip already uses, so the word has one definition). A Friday night's window runs to
    Monday 06:00; a pre-`RUN_AT` daytime row is NOT late (only the window's end makes a row late); a
    reconstructed row is never judged late (it is set aside on its own marker). DERIVED on every read from
    `recorded_at` — no column, no migration, nothing stamped (`scoreboard/late.py`).
  - **The policy.** `Settings.scoreboard_exclude_late` (env `ALPHADECK_SCOREBOARD_EXCLUDE_LATE`, default
    `true`): the thesis's late rows are judged once per thesis (`late_call_ids`) and their ids dropped from
    all four winning-row peer reads — `thesis_timeline`, the ingest-health stamp, the run identity and the
    annotation caption — inside the WHERE, BEFORE the per-as-of dedup (`calls_repo._ids_clause` beside the
    other two clauses). So an on-time row for the same night WINS over a later late re-version (and its
    stamps caption the episode), and a night whose only rows are late reads exactly like a cron gap: the
    record last spoke on the prior honest row, and the next honest row opens or closes what changed.
  - **Both readings, side by side.** `GET /scoreboard?include_late=true` scores the late rows (exactly as
    before L1), `=false` sets them aside, omitted = the setting; the summary says which reading it is
    (`late_policy: "excluded" | "included"`) and lists the thesis-nights either way (`late_nights`: thesis,
    as-of, the first late `recorded_at`, the `edgar_open` it missed, `n_rows`, `earlier_recorded_at` = the
    earlier honest row the default reading scores instead or `null` = a gap, and the reason "recorded after
    the next EDGAR open"). The ledger renders one quiet line — "N late-recorded nights · late rows set aside"
    — with each night, how many hours late, and gap-or-scored-instead on hover, and an **include late
    nights** toggle (the D3b toggle's twin). `python -m scoreboard.run --include-late` is the CLI twin.
  - **Reversing it.** Flip the setting (`ALPHADECK_SCOREBOARD_EXCLUDE_LATE=false`) or pass the param: the
    record scores every late row again, identical to the pre-L1 reading (`tests/scoreboard/test_late_rows.py`
    pins it against an independent unfiltered read). Nothing is ever deleted or marked.
  - **How the three filters compose.** By AND: a row is scored only if it is not reconstructed (always), not
    annotated `exclude` (under its policy) and not late (under this one). Each reading toggles only its own
    rows, and the two policies are independent (`?include_annotated=` and `?include_late=` combine freely).
    Lists: `reconstructed_nights` names a night only when EVERY row on it is reconstructed — so a night with a
    reconstruction AND a late row is named in `late_nights` instead (as a gap); `earlier_recorded_at` is the
    late judgment's own answer and ignores the annotated filter (a phantom-arm on-time row is set aside by
    that policy and named in `annotated_nights`).
  - **Name the reading when quoting** — as with the annotated reading, `late_policy` rides every response.
  - **Admin says "recorded", this page says "gap" — and Admin names the difference.** Admin's per-(thesis,
    night) coverage counts a late row as COVERING its night (the pass did record it; `ADMIN.md` "Cron
    health"), so a thesis-night recorded ONLY late reads covered there and a gap here. Admin lists exactly
    those pairs — `GET /admin/status` `record.late_only_nights`: this banner's `late_nights` entries whose
    `earlier_recorded_at` is `null` (nothing scored instead — a gap), computed by the same judge
    (`scoreboard/late.py::late_only_nights`) — as one quiet line pointing back at this banner (operator
    decision 2026-10-17). A late night that has an on-time or daytime row scored instead is not a gap and is
    not listed there. Informational there: never an alarm, never the verdict.
  - MEASURED (prod, 2026-10-17, read-only — the branch code through a `PYTHONPATH` overlay on a
    `default_transaction_read_only` session): **9 late rows in the whole history** (793 rows, 151 of them
    reconstructed), all from the 2026-10-12 boot catch-up — AI Memory & Storage (seq 1367 at 17:04, which
    superseded its on-time seq 1334 at 22:42), and 8 late-only nights (Modern Defense, Modern Defense
    Buildout, Navy Shipbuilding Rebuild, Nuclear Buildout, Rainbow Rush, Small-scale nuclear, The Everything
    Exchange, UNH). The other three on-time theses that night (DEV, Digital Asset Treasury Complex, HIMS)
    re-assembled identical cards, so the catch-up appended nothing for them. At asof 2026-10-16 (annotated
    nights excluded): **325 episodes with late rows set aside vs 328 with them scored**. 3 episodes vanish
    (Rainbow Rush CYCN and The Everything Exchange MELI, armed ONLY on the late 10-12 row and de-armed 10-13;
    Nuclear Buildout VST's 10-13 re-arm, because without the late row its 10-09 run never broke), 1 moves
    (Nuclear Buildout PCG: armed on the late 10-12 row → on the honest 10-13 row), and 7 change shape (VST
    now runs continuously from 10-09; five de-arms move from the late 10-12 row to 10-13 — Modern Defense
    TLS/QUCY/MTSI, Rainbow Rush MDCX, The Everything Exchange STUB; YYAI's last armed day becomes 10-09).
    Every affected episode was already ingest-flagged, so the eligible metric pool (34) is unchanged.

## The scoring unit and its flags

The unit is replay's **arm episode** (`replay/episodes.py::derive_episodes`, reused as-is): a
contiguous run of one basket member in `armed_members`, keyed `(thesis_id, security_id, arm_date)`,
scored by `replay/scoring.py::score_episode` over `[arm_date, exit_by]` — the system's own
**signal-validity horizon**, the honest yardstick. It is not a mandatory trade exit or sell-by date. The
live additions (`scoreboard/schema.py`) are honesty about the
record, per episode:

| Flag | Meaning |
|---|---|
| `status` open/closed | open = still armed at the record edge ≤ asof (replay's `window_end`, read live); its return is a RUNNING return, not a verdict |
| `matured` | the episode's own `exit_by` signal-validity endpoint has elapsed (≤ asof). **Metrics judge only matured, non-censored, clean-ingest episodes** — a running return must never drift inside `false_arm_rate` before the scoring window ends |
| `censored_start` | armed since before the record began (above) |
| `arm_ingest_fresh` | **provenance A (run stamp):** the arm-date row's ingest health (migration 0023, cron R2b), read raw off the same winning row the scored card comes from — `false` = the arm rested on a PARTIAL ingest; `NULL` (legacy/manual append) is never coerced to a judgment |
| `freeze_era` | **provenance B1 (freeze window):** the arm falls inside the 2026-07 EDGAR cache freeze `[2026-07-10, 2026-07-17]` (`provenance.FREEZE_WINDOW`, dates per the postmortem) — the cohort-level marker B2 cannot see: an arm inside the window may rest on promptly-ingested older facts while the frozen index hid newer filings |
| `thaw_lag_days` | **provenance B2 (derived thaw marker):** max calendar-day ingest lag — first `recorded_at` vs latest `valid_from` — across the arm triggers' cited form4 accessions (`fact_insider_txn`'s bitemporal axes; the derivation the 0023 comment promises). Beyond `THAW_LAG_DAYS = 7` marks a thawed-late arm; `NULL` = no form4 sources or no fact rows (unknown, un-flagged). Deliberately also flags arms resting on facts backfilled at basket-add time — same semantics |
| `triggers_at_arm` | the arm-date card's member trigger evidence — the WHY rides every row (invariant #6) |

The three mechanisms roll up into `ingest_flagged` (+ the backend-authored `ingest_note`, the one
authority for the "why"): partial stamp OR freeze-era OR thawed-late. A flagged episode carries the
**INGEST** badge and is **excluded from the aggregate metrics, ON by default (no toggle)** — the
same conservative posture as `censored_start` — while staying **ledger-visible always** (the
recall-is-sacred cousin: nothing drops from the ledger). The banner's eligibility parenthetical
reads `matured + non-censored + clean-ingest`. Consequence, stated plainly: the launch record was
12/12 freeze-touched, so the metrics stayed honestly empty until the first clean-data arms matured; the
eligible pool has since begun to accrue, and it is built from honest (non-reconstructed) rows only.
The flags are composed AFTER `score_episode`, from reads the scoring path never sees
(`scoreboard/provenance.py` imports nothing from `calls/`, and nothing on the call/write path
imports it) — a clean, flagged, and legacy-NULL episode score identically, pinned by test.
**Named limitation (deliberate):** the withheld-arm metric's warming-run timelines are NOT
provenance-filtered — episodes are the provenance unit.

The summary also carries a **maturity horizon** (2e), turning the mute "0 eligible" gate into a
countdown: `next_maturity` (the earliest FUTURE `exit_by`, ledger-wide — every episode is still
judged at its own deadline), `n_maturing_30d`, and `projected_min_n_date` — the date the ELIGIBLE
pool could reach `MIN_N`, counting only non-censored, non-flagged future maturities (a flagged
immature episode does not advance it); `null` when already cleared or not reachable from current
episodes. Asof-pure, derived from episodes already in hand; **a projection over currently-recorded
episodes, never a promise** (new arms or de-arms shift it). The FE renders it as one quiet line
beside the metrics gate.

`Outcome.insufficient_prices` on a fresh arm means "no bar on/after the arm yet" (an arm recorded
Friday has no entry bar until the next trading close lands) — awaiting data, not an error. It ALSO
covers a scored window that held no bar at all: an episode whose `arm_date` and `exit_by` fall on the
same non-trading day has nothing between them to measure, so it reports no return rather than one
built out of an entry and an exit that are not in the same window (see "the exit read" below). On a
MATURED episode the ledger says **"no bars in the scored window"** rather than "awaiting first bar" —
the window has closed and no later bar can enter it, so there is nothing to await.

**`truncated` = the episode's horizon extends past the end of THIS NAME's price tape**
(`exit_by > tape_edge`, where `tape_edge` is the last bar for that name within the reader's own as-of
cap). One condition, and it asks the tape rather than a calendar.

It used to be `exit_date < exit_by`, which collapsed four unrelated situations into one flag: a
still-running episode (structural — an immature episode's window is capped at the as-of, so it was
*always* true and carried nothing `matured == false` did not already say); an `exit_by` that landed
on a Saturday, Sunday or market holiday (nothing missed — there is no Sunday close and no later bar
will ever change the number); a genuinely dead tape (the one case worth acting on); and a stale
per-name ingest, which it could not detect at all because it never asked about the tape. It fired on
**91% of episodes**, and every matured fire was a weekend or Labor Day — it had never once fired for
the reason it was documented for.

The tape-edge rule answers the trading-day question **without a calendar**: a Sunday `exit_by` in the
past sits *before* a live name's tape edge, so the tape itself proves the horizon was covered; a
market holiday resolves identically, with no holiday table to rot every January; a delisted or
stalled name's edge sits *before* its horizon and reads true, which is the flag doing its actual job.
Immature episodes stay `true` — load-bearing, because `moveNote`, `peakTimingPhrase` and the chart's
"last bar" exit marker all anchor their phrasing on it.

### `tape_behind_market` — the loudness half

```
tape_behind_market  =  truncated  AND  exit_by < tape_edge(market)
```

`tape_edge(market)` is the latest bar date **anywhere in the tenant's tape**, under the same
as-of/known-at caps. One sentence: *the market has printed past this horizon, and this name's tape
still has not reached it.*

It is a **second field, not a narrowing of `truncated`**, and the distinction is load-bearing.
`truncated` answers *"did the measurement reach the horizon?"* — on a running episode the honest
answer is no, and three sites read it precisely to phrase that honestly (`moveNote`'s "measured to
the last bar ≤ as-of", `peakTimingPhrase`'s "last bar Nd after the peak" rather than the false
"horizon closed Nd after", and the chart's exit marker reading "last bar" rather than "exit"). The
market leg is false for every running episode by construction (`market_edge ≤ asof < exit_by`), so
folding it into `truncated` would silence all of them and flip those three phrasings to statements
that are not true. It was tried: it turns `test_asof_cap_no_future_leak` red.

What the market leg removes, once a row is matured, are two classes where falling short is not this
name's fault:

- **an episode maturing today** — today's close does not exist until after the bell, so *every*
  same-day maturity is momentarily short of its horizon on a perfectly healthy feed. Un-suppressed,
  the badge would flicker for part of every day, which is how the old rule lost its credibility.
- **a globally stalled feed** — a frozen cron, or a dev stack with the cron off. When the whole tape
  is behind, no single name is starved; that alarm belongs to the record-freshness line, not to 200
  individual rows.

`tape_edge(market)` costs a scan (`fact_price_eod` has no `(tenant_id, d)` index — MEASURED at ~33 ms
over 350k rows), so it is resolved **once per tenant per request** in `scoreboard_records` and
threaded into the per-thesis readers. A reader resolving its own would put that scan on every thesis.

### The badge

The field states the fact; the badge decides it is worth saying. **`TAPE ENDS`** gates on
`matured && tape_behind_market && !insufficient_prices` and reads *"the horizon elapsed, but this
name's price tape stops at ‹exit_date›"*. On the current record it fires **zero** times, which is
the honest count — 99 securities in the master have tapes that stop before 2026-08-01, none of them
carrying an episode yet.

**One deliberate conservatism.** The market leg is strict (`exit_by < market_edge`), so an episode
whose `exit_by` lands exactly on the market's latest bar date is suppressed for that day even if the
name genuinely missed that bar. It fires the next trading day, once the market edge advances past the
horizon. That is a one-day latency on a true positive, bought in exchange for immunity to the
same-day flicker; `test_market_edge_equal_to_exit_by_is_suppressed` pins it so the choice stays
visible rather than accidental.

`truncated` is **not** a metric input. Eligibility is `matured & !censored_start & !ingest_flagged`
and the metric filter is `!insufficient_prices & forward_return is not None`; neither mentions it, and
nothing in `replay/metrics.py`, `scoreboard/assemble.py`, `domain/` or `calls/` reads it. `Outcome` is
computed on read and never persisted, so none of this is near the cron's `record_if_changed`.

**The exit read.** An episode's exit is the last bar of its own scored window `[arm_date, exit_by]`,
never the last bar anywhere `<= exit_by`. The two agree whenever the window holds a bar and diverge in
exactly one shape — when the last bar `<= exit_by` *precedes* `arm_date` — where the unbounded-below
read paired a later entry with an earlier exit and served a return measured **backwards in time**. Two
episodes on the record hit it (armed on a Sunday with `exit_by` the same Sunday: `market_today()` does
no weekend skip by design, so a Sunday backfill records a Sunday as-of and `derive_episodes` takes
card as-ofs as episode boundaries), one of them inside the live metrics as an adverse arm.

**A de-arm can postdate the scored exit, and that is the horizon working.** Seven episodes have
`exit_by < dearm_date` — the signal-validity horizon elapsed while the record kept the member armed.
`score_episode` scores `[arm_date, exit_by]` by design, so `exit_date <= exit_by < dearm_date` follows
by construction and `exit_vs_peak_days` can read negative. It is the documented "open-but-matured"
shape (`test_maturity_judged_only_at_exit_by` pins it), not a bug; those are also the episodes whose
de-arm marker has no place on the sparkline path (`dearm_index` is null, and the hover says so).

A related **single-bar** case gets its own honest label (Slice 2, #209): when the ONLY bar on/after the arm
is the arm-day bar itself (`exit_date === arm_date`), `forward_return` is a degenerate `0.0%` over one bar —
**not a flat move** — so the ledger reads **"awaiting forward bar"** and shows `—`, distinct from
`insufficient_prices`'s **"awaiting first bar"** (no bar at all). Once a forward bar lands
(`exit_date > arm_date`) the return becomes a real (running) number, even if ~0%. The check
(`frontend/src/scoreboard/rows.ts::awaitingForwardBar`) runs AFTER the realized check, so a degenerate
matured single-bar episode still reads "realized" — it only overrides the "running" label. Same instinct as
the arm-day dash: never let a mechanical 0.0% read as a real return.

## Setup strength and the small-sample gate

The per-call display is **setup strength**; its stable wire field remains `confidence`. It is an experimental
relative read of trigger composition and risk penalties, **not a probability of success**. The legacy metric
slug `grade_confidence_calibration` asks whether grade/setup-strength ordering discriminates realized outcomes
monotonically; only matured forward outcomes can support that calibration.

`MIN_N = 5` / `insufficient_n` controls how early aggregate metrics are presented in the UI. It is a
**safeguard against over-reading tiny summaries, not an evidence threshold**: clearing `n ≥ 5` does not make a
metric conclusive, establish calibration, or convert setup strength into a probability. Sample composition,
per-bucket counts, censoring, and stability over a materially larger forward record still matter.

## Prices: the Postgres twin, asof-capped

`scoreboard/prices.py::PgRealizedPrices` is the Postgres twin of replay's DuckDB `RealizedPrices` —
the same reader surface `score_episode` duck-types against (the three close reads, `bars_between`, `tape_edge`,
`market_tape_edge`), same latest-version-per-day dedup
and `recorded_at DESC, id DESC` tiebreak, plus two caps: `d <= asof` (the request as-of — scrubbing
the Scoreboard back can never see a later bar; open episodes' returns run to the last bar ≤ asof)
and `recorded_at <= known_at` (default now — a re-versioned/restated bar's latest version wins:
score against the corrected tape). A parity test (`tests/replay/test_pg_prices_parity.py`) pins the
two readers row-for-row equal, including the identical `Outcome`.

This required one 2-line enabler in replay: `replay/scoring.py`'s `import duckdb` moved under
`TYPE_CHECKING` (duckdb is the optional `.[replay]` extra, absent from the lean prod image; the
import was annotation-only). `tests/scoreboard/test_lean_import.py` pins that structurally.

## The operator track (SB3)

The decision log (`operator_decision` — append-only, "the Scoreboard's missing column") joined to
the episodes it answered. Voids resolve first (a voided decision is excluded from all math, still
counted in `n_voided`); the valid axis caps at the request asof (`decision_date <= asof`).

- **took** — the earliest take→close span whose take date falls inside an episode's window
  (`[arm_date, dearm or asof]`) on the same name fills that episode's `operator` slot. Prices: a
  logged fill always wins; a missing one falls back to the close, flagged `inferred`, never silent
  (entry = first close on/after the take — blind-entry parity; a running span's exit = last close
  ≤ asof). **No delta/counterfactual fields** — the row shows the record's return and the
  operator's side by side (deltas ride with the v2 follow-blindly track).
- **passed** — a pass inside an armed window fills the slot when no take did (same name; a
  thesis-level pass lands on the **headline** episode — Decision Queue semantics). No prices; the
  episode's own outcome sits beside it.
- **no decision logged** — an armed episode nobody answered keeps `operator: null`: the honest
  capture gap, rendered as such, never an error.
- **off-record spans / overrides** — a span answering no episode rides `operator_spans`, carrying
  the stance **frozen on the take row at logging time** (`call_state`/`call_verdict` — the record,
  not a recompute, is attribution's source). `override=true` when that stance was not
  armed/managing (`managing` means an operator-entered thesis is being monitored, not risk-managed):
  the gate's logged override, now with its outcome attached. A thesis-level take
  (no name) stays **unpriced** — visible, never guessed onto a name.
- **anomalies** — a log shape the API should have prevented (take-while-open, close-while-flat)
  surfaces as a per-thesis `decision_anomaly` note; the pairing never silently fixes the log.

## The historical panel (replay-alongside, RH) — RETIRED by editions S8 (2026-10-23)

The panel showed replayed history in a clearly-separated, collapsed section below the record's ledger: an
operator-kicked artifact (`python -m scoreboard.replay_snapshot` wrote `data/scoreboard_replay/latest.json`
from the dev venv; `GET /scoreboard/replay` served it, `available:false` when absent; a read-only compose bind
let the container read it and never write it). It was a RECOMPUTE labeled as one — today's code + dials over
historical facts, its dials named (F2) and its roster provenance stated per run (F4) — with the record's own
honesty rules (`censored_start` on the window's first day, maturity against the data edge, metrics over matured
and non-censored episodes, never pooled with the record).

Editions replaced it: a stored edition is a frozen re-run of today's rules on the PUBLIC clock from each
company's join date, read by the SAME ledger as the record and switched to in the header, so a second,
differently-built view of history no longer earns its place (operator, editions plan question 5). Removed: the
`ReplayPanel` component, `GET /scoreboard/replay` (+ its `ScoreboardReplayResponse` wire model and the
`useScoreboardReplay` hook), `scoreboard/artifact.py` (the artifact's reader and writer), the snapshot CLI
(`replay_snapshot.main`), the base compose file's `./data/scoreboard_replay` read-only bind (it existed only
for that artifact; `/backtest` reads its own `/data/backtest` binds, which the dev/sig overlays and
`docker-compose.prod.yml` carry, untouched) and the fork/sig scripts' creation of that directory. KEPT: the pure
`scoreboard/replay_snapshot.build_snapshot` and the models below, because `/backtest` builds its ledger with
them. A `data/scoreboard_replay/` directory left on a host is inert and safe to delete.


### What `/backtest` borrows from this page (and what it must never borrow back)

The Backtest surface (`docs/BACKTEST.md`) renders its per-thesis drill-down through THIS page's components —
`LedgerHead`, `EpisodeRow`, `EpisodeScorecard`, `MetricsStrip` — and its ledger artifact is built by
`scoreboard/replay_snapshot.build_snapshot`, the pure flattener the retired panel above used. That is
deliberate: its rows genuinely are replayed, scored arm episodes, so a parallel set of components would give
two surfaces two definitions of one row and let their renderers drift. Three consequences to hold:

- **The wire shape is shared, so it is load-bearing in two places.** `ScoreboardEpisodeOut` serves the
  Scoreboard's ledger AND `/backtest/runs/{id}`; `ScoreboardReplayThesisOut` (named for the retired panel) now
  serves `/backtest/runs/{id}` alone. A field added for one arrives on the other.
- **Backtest-only fields do NOT go on `ScoredEpisode`.** The breadth fields (co-arm counts, the Key-1 source,
  the confirmation grade at the arm) ride the backtest's own response and are joined on
  `(thesis_id, security_id, arm_date)`. They have no meaning on the record's ledger and must not be pushed
  into the shared model to save a join.
- **Nothing flows the other way.** This page's numbers come from the RECORD; a backtest is a recompute under
  chosen dials. They are never pooled, never merged, and the Scoreboard never links to a backtest run.

The retired `ReplayPanel` itself was never reused — it was bound to its own endpoint. Its children are where
the rendering lives, and those are what is shared.

## Record freshness on the live view (Slice 2, #209)

The Scoreboard also answers **"is the call-of-record current *now*?"** — the same question the Admin page
asks (`ADMIN.md`), surfaced here because the Board-vs-Scoreboard confusion happened on this page.
`GET /scoreboard` carries `record_edge` (the **uncapped** calls-log `MAX(asof)` — independent of the request
as-of, so it reads the same whether the view is scrubbed to the past or to today; it counts reconstructed rows
too — freshness asks whether the log advanced, not whether a row is scoreable) measured against the last
**expected** session + `RUN_AT` run (Mon-Fri minus NYSE closures since F5; `pipeline/schedule.py` — ONE contract shared with the Admin surface,
never raw `today − edge`). The FE shows it **only on the live view** (`asof >= today`, or on or after the server's
current night — F1: the app's default as-of, which is before today every morning): staleness answers
"current now", not "as of a past date", so a scrubbed-back view suppresses it. It goes **loud only when
stale** ("record last advanced ‹edge› · N expected run(s) behind"); **quiet** when current or never-begun
(honest loudness, mirroring the Admin copy). Compute-on-read — the freshness read still writes nothing.

## The ledger table itself

Three structural notes for anyone editing it.

**It wears the `.basket` skin but it is NOT the Cockpit's contained table.** The Cockpit's containment
machinery (`min-width: max-content`, its sticky header, its sticky identity columns) is scoped to
`.basket-scroll` precisely so it cannot reach here — unscoped, it overflowed this page by 498px (see
`BOARD.md`). The ledger has its own box, `.sb-scroll`, with its own rules; the two are deliberately
separate even where they agree.

**Both headers stick, and the height cap is the price.** `.sb-scroll` caps its height at
`--sb-scroll-max` (**the one knob** — set it to `none` and everything below reverts). That cap is what
gives the box a vertical scroll of its own, and a sticky `<thead>` can only stick to the box that
actually scrolls it: per spec `overflow-x: auto` computes the other axis to `auto`, so `.sb-scroll`
was already a scroll container on both axes and the page was never the header's scrollport. The cost
is real and was accepted knowingly — a nested vertical scroll inside a flowing document, with the
metrics strip above it — because the alternative on a ~10,000px record is a
column header that is off-screen for all of it. The thesis heading sticks **below** the column header
(`top: var(--sb-head-h)`, z-index 2 vs the header's 3), so a heading scrolling up slides *under* the
header rather than over it. `position: sticky` on a `<tr>` is unreliable — both rules target the
**cells**.

**Every column must be added in four places**: `LedgerHead` (the `<colgroup>` + `<th>`s), `EpisodeRow`,
`SpanRow` (in `Scoreboard.tsx` — the off-record operator spans, the one most easily missed), and
`ledgerColCount` (which the group/note rows' `colSpan` tracks). Miss one and that row's cells sit a
column off their headers, silently — a short `<tr>` just renders narrow. Pinned by a test that walks
every body row in both views and compares its colSpan-weighted cell count to the head's.

### Sorting — a within-group re-order, never a flattening

`sortLedger.ts` (pure, unit-tested, beside `rows.ts` the way `cockpit/sortBasket.ts` sits beside
`buckets.ts`) holds the comparators; its hosts — `Scoreboard` and the backtest `RunLedger` (the retired
`ReplayPanel` was the first) — each hold their **own** sort state, because a replayed set is a recompute and
the two are never pooled.

Sortable: **Name · Armed · De-armed · Return · Peak · Peak high · Worst · Worst low · Past peak**.
Not sortable, deliberately: **Path** (a shape has no honest scalar), **Status** (a badge set — plus, on a
closed row, one muted **close-reason chip** whose hover carries the composed detail and the raw wire token —
has no ordering the record gives it), and the Summary-only **Why** / **Operator** / **Exit-by**.

Three rules, inherited from the Cockpit's sort because they are the same rules:

1. The group is the **spine** — rows re-rank *inside* their thesis heading and the headings never move.
   Operator spans re-rank among themselves, never interleaved with the arm episodes above them (a
   logged take is not an episode).
2. A key is read off the **same access path and the same dash guard** as the cell, so the ranking always
   matches what the operator sees — `noForwardBar` dashes the whole timing lens, a wick is
   independently absent under the all-or-nothing rule, and a real `0.0` still ranks.
3. **Nulls last in both directions** (a missing measurement is not a small one) and the cycle is
   reversible: desc → asc → off, back to the record's own order. A sort is a re-order, never a filter —
   no row can be sorted off the ledger (#9).

## The episode drill-down: drawer, chart, ledger

Every ledger row opens a **drill-down drawer** (`components/Drawer.tsx` — a reusable slide-out, ~600px with
expand-to-full-screen; ✕/backdrop/Esc close, focus returns to the opener; read-only, a sibling overlay so
opening it never re-renders the ledger). A **row click** opens it; the row's **↗** icon is the distinct jump
to the fuller per-name Cockpit (#227–#230). It surfaces the per-episode `Outcome` fields the row can't fit.

**Four timing lenses** (`EpisodeScorecard.tsx`) — all from already-computed `Outcome` fields (it surfaces
more of what is computed; it changes no computation): *The move* (entry/exit close + the return — the
"show the prices" gap), *Horizon calibration* (`peak_return`/`peak_date`/`exit_vs_peak_days` — was `exit_by`
well-timed? hidden without a peak), *Edge preservation* (`warm_return` vs `forward_return` — armed in time or
missed the early move?), *Entry window + setup* (`arm_until_return` + grades + setup strength). A
**Summary | Timing** column toggle (`LedgerHead.tsx`) swaps the ledger's middle columns so one timing lens
can be scanned down the whole universe (#228). Summary is **9 columns** —
`Name · Armed · De-armed · Why · Exit-by · Status · Return · Peak · Operator`. Timing is **11** —
`Name · Armed · De-armed · Path · Return · Peak · Peak high · Worst · Worst low · Past peak · Status`.

**The run-identity line (F1, migration `0044`).** Under the ingest line the drawer carries one quiet caption —
`policy ‹hash8› · ‹run_kind› · code ‹sha8›` — naming the `CallConfig` fingerprint, the kind of run (`cron` /
`manual` / `backfill`) and the image SHA of the row that recorded this arm. Unlike the ingest line it is
**not** exception-only: it flags nothing and excludes nothing from the aggregates, so it is provenance on a
drill-down rather than loudness (#7 governs badges and alerts). It is **absent entirely** on a row recorded
before the stamp existed — an absent line is the honest reading of "we do not know", where a row of
em-dashes would read as a broken feature. The copy is backend-authored (the `ingest_note` precedent), so the
hash is shortened in exactly one place in the codebase; the FULL `config_hash` / `code_sha` ride the wire
beside it and appear on hover (#6 — provenance always shows its work). Why it matters: the two largest arm
bursts in the record were weekend **manual** runs on deploy days, and until now the row could not say so.

**Armed and De-armed are two columns**, not one cell reading `Aug 7 → Aug 11`: they are two
measurements, and packed together they could be neither scanned down nor sorted on. The censored-start
`*` stays with the **arm** date — it is the arm that is unknowable. A still-open episode's De-armed
cell reads `—`; so does a `SpanRow`'s, because an operator span is a logged take, not an arm.

### The excursion pair — how far it went each way, not just where it ended

A row used to say where an episode ENDED and how high it got, so a name that went straight up and a name
that was 12% underwater before recovering rendered **identically**. MEASURED on the record: of 98 episodes
that ended positive, **22% first drew down worse than −5% on closes** and **58% dipped worse than −5%
intraday**. The ledger could not tell "the call was right and easy" from "the call was right and would have
stopped you out" — which is the operator's stated weakness (timing), and something `false_arm_rate` cannot
see because it only looks at the endpoint.

Two bases, six `Outcome` fields, **one read**:

| field | basis | meaning |
|---|---|---|
| `peak_return` / `peak_date` | close | **MFE** — maximum favorable excursion |
| `trough_return` / `trough_date` | close | **MAE** — maximum adverse excursion |
| `intraday_high_return` / `intraday_high_date` | wick | the best price that actually traded |
| `intraday_low_return` / `intraday_low_date` | wick | the worst price that actually traded |

**All four are columns**, each close figure adjacent to its own wick so the pair reads together:
`Peak · Peak high · Worst · Worst low`. The close pair anchors the row — `forward_return` is close-based
and `exit_vs_peak_days` is anchored on the close-based `peak_date`, so Return · Peak · Worst read as three
numbers on one tape — and the wick sits beside each, set one tone quieter (`.sb-wick`) because the close
is the basis and the wick is the check against it.

The wick pair used to ride the close cells' hovers, on the grounds that MEASURED it differs from the close
on 96–97% of episodes but by a median of only **+2.0pp / +2.1pp**, and a 2pp correction is something you
*check*, not *scan*. **The operator overruled that** and asked for all four visible. Promoting them was a
pure FE change (the wire already carried all four). It came with a second half: each of the four hovers now
describes **only its own figure** — a hover that restated its neighbor would be exactly the repetition the
columns were promoted to remove, and the neighbor's own cell answers for the neighbor.

Three honesty rules:

- **Wick fields are all-or-nothing, per column.** One bar missing `high` nulls `intraday_high_*` entirely.
  The field asks *"what is the most extreme price this actually traded at?"* — if one bar's extremes are
  unknown, the true extreme could be **inside** that bar, so an extreme over the remaining bars is not
  conservative, it is **wrong**, and silently so (#6). A wick field **never** falls back to the close.
- **`trough_return` reads a real `0.0`**, not null, for a name that never closed below entry — **24% of the
  record**. `0.0` is the measurement; null would claim ignorance about a number we know exactly.
- **The two adverse figures never agree on "untouched".** MEASURED: `intraday_low_return` reads 0 on
  **0 of 250** episodes (its maximum across the whole record is **−0.2%**) — every name traded below its
  entry close at some point intraday. Only the *close* MAE can ever say "it never went against you".

### The episode path — the shape behind the endpoints

`path` is the scored window's closes, ascending; `dearm_index` is the de-arm's slot in it. Both come off the
**same `bars_between` read** as the excursions — the excursion window and the sparkline path are the *same
list*, not two reads that agree. `score_episode`'s window read swapped `closes_between` → `bars_between`:
same rows, same query count, four more columns.

The ledger cell (`EpisodeSparkline.tsx`) reuses the Cockpit's `sparkGeometry` (pure, unit-tested, breaks on
a gap rather than bridging it, floors at two real values). It is **variable-length**, which deliberately
**inverts** the Cockpit's fixed-slot choice: the Cockpit pads every name to one shared 90-bar window so
shapes are comparable down the column, but Scoreboard episodes share **no** window (median **13** bars, max
**42**, and 75 of 131 securities carry more than one episode). Padding them to a common width would draw a
13-bar episode as a stub beside a 42-bar one and **imply a shared time axis that does not exist**. The cost
is real and goes in the hover: **a steeper line does not mean a faster move.**

`dearm_index` is **null when the de-arm fell outside the scored window** — the real **8-episode** case where
the horizon elapsed while the record kept the member armed (see the de-arm note below). The marker is simply
not drawn there, and the hover says *why*: a silently absent tick would read as "never de-armed", which is
false.

**A de-arm can postdate the scored exit, by design.** MEASURED: **7 episodes** have `exit_by < dearm_date`.
`score_episode` scores `[arm_date, exit_by]`, so `exit_date ≤ exit_by < dearm_date` follows by construction —
that is the open-but-matured shape `test_maturity_judged_only_at_exit_by` already pins, not a defect.

**None of these fields moves a metric.** `compute_metrics` reads `forward_return`, `warm_return`,
`peak_return`, `exit_vs_peak_days`, `entry_grade`, `is_headline`, `close_reason` and `insufficient_prices`;
the excursion and path fields are descriptive. `Outcome` is computed on read and never persisted, so this is
nowhere near `record_if_changed`.

**The reader asymmetry is preserved, not harmonised.** `PgRealizedPrices.bars_between` keeps its double cap
(`d <= cap` on the valid axis, `recorded_at <= known_at` on the transaction axis); the DuckDB
`RealizedPrices.bars_between` stays deliberately **forward-unbounded**, as the scoring pass requires and as
`_closes` already is on that side. No as-of read is widened — the new read sees exactly the universe
`closes_between` already saw, and `test_ohlc_window_and_path_respect_the_asof_cap` pins that a bar past the
cap reaches neither the excursions nor the path.

### The episode chart — a numbered event overlay (Slice A)

The *move* lens mounts a **price chart**: an on-demand read (`GET /scoreboard/price-window` — the SAME
asof-capped `PgRealizedPrices`, served on request so the ledger payload stays lean; lightweight-charts,
lazy-loaded). It draws the **close line + faint SMA 50/200 context**, and overlays the episode's **recorded
events** as **colored numbered chips**:

- **What rides on it** — insider **code-P buys** (`fact_insider_txn`), the **arm's triggers**, and the
  **lifecycle** moments (warm/arm/dearm/exit). Each buy carries a server-classified **`character`**
  (Band 03 S2c — the display rail's `_screen`, the same predicates the NamePanel's `_is_open_market_buy`
  composes): `open_market` (the unbadged norm — "passed the available screens", never "proven
  discretionary") · `self_filing` (labeled — still counted in the panel's net-flow; that re-base is
  deferred) · `primary_market` / `implausible` (**set aside** — grayed chips + muted ledger rows, present
  and labeled instead of hidden, WB #2/#9) · `internal_transfer` (**set aside** — a buy matched to a sale
  between LINKED filers, `domain/insider_internal_transfers.py`; the line names the counterparties from
  `transfer_with`: "internal transfer within the filer group (with KIM JAMES J) — not counted"; sales carry the
  same character + line). The **counted** (non-set-aside) dots reconcile with the NamePanel's net-flow figure
  (the flow sets transfers aside on both sides too).
- **The private-purchase note (display only)** — a counted buy whose OWN footnote the deterministic phrase list
  locates (`private_passage`, `private_counterparty` — `domain/insider_private_purchases.py`) carries ONE quiet
  line, "private purchase from Joshua Harley — not on the open market" (no name unless the server grounded one:
  a same-day seller the passage names, or the issuer); the ledger row's detail cell shows the filer's passage
  verbatim on hover. Never grayed — a private purchase between unrelated people is real conviction (the
  operator's decision 3, 2026-10-21); a buy with no located passage has no line (#7).
- **The relevance floor** — the loaded universe is `[max(thesis.created_at − 365d, first_bar), now]`: a thesis
  born in 2026 does not plot a 2020 buy. A *relevance* bound on off-story events, never a recall cut (#9); the
  server owns it and echoes the effective floor.
- **Stable unified numbering** — the events number chronologically **1..N once over the whole universe** — a
  STABLE per-event id, identical across the compact/expanded views and shared with the ledger. The default
  visible range is the recent episode; **pan/zoom** reveals earlier events and de-crowds dense clusters
  (collision-stacking spills to a visible "+N", never a silent drop).
- **No-lookahead on both axes** — an insider buy is **positioned by its transaction date** (`valid_from`) but
  **gated by disclosure**: for a PAST as-of on the PUBLIC clock (since 2026-10-21 — its SEC acceptance, capped at
  the end of the as-of's night — EDGAR's next open, F6 — `domain.market_time.known_at_for_asof`; the same
  view the
  Board/Cockpit/Workbench scrub-back threads through `serve_view`), for today's as-of on the record clock
  (`recorded_at ≤ now`, where the two coincide). So a scrubbed-back Scoreboard hides later bars AND
  later-*disclosed* buys — the honesty a filing's days-to-months lag demands — but no longer hides a buy that was
  public and merely ingested late; each chip's "ingested" line stays our honest ingest date (`ingested_at`).
  8-Ks and 13D/Gs are gated by their filing date the same way (the price bar's `valid_from == d` needs only
  the valid-axis cap). SMA is a warm-up read with an honest `None` gap where
  history is short (`scoreboard/overlays.py`).
- **Explainability (#6)** — hover any chip for the buyer/$/date, the **disclosure lag**, the buy's
  **character line** (why it did or didn't count; open-market stays unbadged, the 10b5-1 plan note renders
  only on an explicit `true` — tri-state), and the market-price context (the close that day, % vs now),
  with a guide-line to the price point; every chip traces to a recorded row. A legend names only the
  present families (#7).

### The event ledger + Cockpit strip (Slice B)

Below the chart, an **event ledger** (`EventLedger.tsx`) lists the **SAME numbered events** — **row #N is
chip #N** (one shared array, built once in `EpisodeScorecard`, never re-numbered). It is the *complete* list,
the antidote to the chart's focused view (which hides off-view chips and clusters dense ones into "+N"). Hover
a row → its chip rings; hover a chip → the row tints (a lifted `activeN`, kept OUT of the chart's canvas
effect, so highlighting never rebuilds it).

A **Cockpit strip** brings over *some* per-name context without duplicating the NamePanel: an identity line
(archetype · sector · exchange · market-cap, from the workbench-scored read) + the present-only display-signal
**headlines** (SMA position · 52-week range · volume regime · insider-flow 90d, reusing the Cockpit's
`DisplayHeadlineRow`). For a **closed** episode those trailing windows are labeled **"current tape · as-of X"**
— a name-current 90d figure is not the episode's own period. Read-only, FE-only (reuses the workbench-scored
and display-signals endpoints; no new backend). The finer intra-run calls-log transitions have no chip — the
numbered ledger stays chip-events-only; they surface as the un-numbered **record trail** below it (Slice C3,
the `transitions[]` field — see §"Episode enrichment").

### Episode enrichment (Slice C)

Two additive episode fields feed the drawer from the recorded cards themselves — dict lookups over
the cards `derive_thesis_record` already holds, no new queries. Both DEFAULT on the replay path
(`CallSnapshot` deliberately drops risk/missing detail — never widened for this), so an old replay
artifact parses to the defaults and the endpoint never 500s:

- **`dearm_detail`** — the composed WHY behind a `dearmed_other` close, the ONE opaque token (the
  other four tokens self-explain; composing under all five would be noise, #7). Backend-authored
  copy, one authority (the `ingest_note` precedent), from the de-arm-day card in priority order:
  the member's own fired risk labels (first 2, joined) → `now missing: <missing[0]>` (deliberately
  BEFORE the state phrase — `missing` is non-empty exactly when a key un-turned, so state-first
  would make it dead code) → `thesis fell back to Warming/Incubating` → `left the armed set (thesis
  still Armed)` (the member-only de-arm) → nothing. The FE renders it on the drawer's close-reason
  line and the de-armed ledger row/tooltip as "de-armed — ‹detail›"; the raw wire token stays one
  hover away in `title=`.
- **`risk_events`** — the member's fired risk signals over the CLOSED interval `[arm_date,
  dearm_date or the record edge]` (arm day included: a risk live AT arm haircut the arm's own setup
  strength, and omitting it would show a clean tape for an arm the record itself discounted;
  de-arm day included: its risk is what ended the run — exactly what `dearm_detail` reads), DEDUPED
  by `(kind, event_date)` — a live risk re-fires on every daily card under a stable fact-anchored
  event date; different kinds on one day stay distinct (#9). A dateless (legacy) risk is stamped
  with the first card-asof it appeared on — a recorded fact (when the record first said it), not
  the market event date. On the chart/ledger they ride as the quiet **risk** chip family (hollow
  muted negative — deliberately distinct from the `sell` FACT family: a fact is what the tape did;
  a risk signal is what the record made of it, so one sell cluster appearing as both is by design).
- **`transitions`** (C3) — the member's intra-run RECORD changes over `[arm_date,
  last_armed_date]`: consecutive-card diffs of **verdict / entry_grade / conviction_grade only**
  (never the daily confidence wobble). The arm card is the baseline (the episode already carries
  the at-arm values); a change across a weekend/cron gap lands on the LATER card's asof — a
  recorded fact, the same stamp rule as `risk_events`. Rendered as the quiet un-numbered **record
  trail** below the event ledger — deliberately NOT chips and NOT in the 1..N array (the numbered
  ledger stays chip-events-only).

## Reading it

```powershell
python -m scoreboard.run --asof 2026-07-11              # the human-readable ledger
python -m scoreboard.run --asof 2026-07-11 --json       # the full analytical dump
python -m scoreboard.run --asof 2026-07-11 --exclude-archived
```

**Archived theses are INCLUDED by default** — archiving stops accrual (the cron skips archived); it
never erases the record. `--exclude-archived` (and the endpoint param, SB2) is the explicit,
reversible filter. Compute-on-read: the whole path owns no tables and writes nothing
(`test_scoreboard_writes_nothing` counts the tables to prove it), no LLM anywhere.

A thesis with an unreadable historical card (the log outlives `CallCard` schema changes;
`DomainModel` is `extra="forbid"`) surfaces a per-thesis `error` and never blanks the board —
keep `CallCard` evolution **additive-only** so old cards stay loadable.

## Deliberately NOT here (v1)

Follow-blindly track + deltas (v2) · a second metrics-led view behind a toggle (v2, once n
accrues) · persistence/caching of scores · cron changes · notifications · the second
recalibration (unlocked by this, not part of it) · a transaction-time (`known_at`) scrub
parameter · stitching replayed and recorded episodes across the seam (noted, never merged).
*(Charts moved from this list to built — the episode drill-down chart, Slice A; the chip-less
intra-run "record trail" likewise — Slice C3, §"Episode enrichment".)*
