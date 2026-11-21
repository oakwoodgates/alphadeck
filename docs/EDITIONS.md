# Editions

An edition is a frozen re-run of today's call rules over a workspace's history, stored beside the record and
scored by the [Scoreboard](SCOREBOARD.md). Edition 0 is the record itself, the `calls` log, and is never
stored as an edition. Edition N >= 1 re-runs every thesis from the night each company joined its basket, on
the public clock, frozen at its cut. The machine grows the current edition every night and cuts a new one
when the rules change or a data repair runs; the operator publishes a new workspace's first edition by
hand. Users read editions on the Scoreboard and the Track Record; the steward sees two Admin lines. Read
this doc before you change `backend/editions/`, the editions store, `domain/rule_log.py` or a repair CLI.

## Code

| Piece | Module |
|---|---|
| CLI (`trial`, `publish`, `nightly`, `cut`) and the one store path, `store_edition` | `editions/run.py` |
| The engine: replay's public-clock mirror and the live assembly | `editions/engine.py` |
| Join dates | `editions/entry.py` |
| The nightly page job | `editions/nightly.py` |
| The automatic cut | `editions/cut.py` |
| The engine fingerprint | `editions/fingerprint.py` |
| The live-edge self-check; a trial's preview against the record | `editions/selfcheck.py`, `editions/preview.py` |
| The store; the repair journal | `repositories/editions_repo.py`, `repositories/data_repair_repo.py` |
| Declared rule changes | `domain/rule_log.py` |
| Which edition a read serves | `_resolve_edition` in `app/routers/scoreboard.py` |
| What changed between two editions | `scoreboard/diff.py` |
| The cron step | `editions_nightly` in `backend/scripts/daily_cron.sh` |
| The header switcher and the "what changed" drawer | `frontend/src/scoreboard/editions.ts`, `EditionChanges.tsx` |

## How it works

### What an edition is

The Scoreboard reads a stored edition with the same code as the record and a different timeline source
(`EditionSource` instead of `RecordSource` in `scoreboard/record.py`). An edition reads only its own pages,
so a `calls` row appended later never moves it, and it never writes `calls`. A golden pins Edition 0's read
byte for byte (`tests/app/test_scoreboard_edition0_golden.py`). What differs for an edition:

- No reconstructed or late rows: every page is computed one way, so those lists are empty, the late policy
  reads `n/a`, and the page hides their lines and toggles.
- No ingest provenance: the INGEST badge describes what we had fetched, and a public-clock re-run has none.
- An arm on a company's estimated join night is `censored_start`, its true arm date unknowable; the drawer
  notes every estimated join.
- The drawer's run-identity caption names the edition and its engine fingerprint, and its chart overlays
  read on the public clock.
- A matured episode's score is inked (below), so a later price correction never re-scores it. A running
  episode, or a view scrubbed back before its window ended, is scored on read.

The engine (`editions/engine.py`) is the replay engine over a public-clock mirror, assembled by the live
call's `pipeline.core.assemble_from_pit`. Each night reads what was public by that night's end, never later
than the cut (invariant 1), in one consistent snapshot; the engine itself writes nothing.

### Which edition a read serves

With no edition named, `GET /scoreboard`, `GET /scoreboard/price-window` and the Track Record
([track-record](dashboards/track-record.md)) read the caller's workspace's newest published edition through
one resolver, `_resolve_edition`; with none published they read Edition 0. When a new edition is cut the
default moves to it with nothing touched. `edition=0` reads the record; an explicit edition that does not
exist is a 404. `Settings.scoreboard_default_edition` is an optional override: 0 pins the record, N pins
Edition N, and an override naming an unpublished edition falls back to the newest, so the default view
never 404s (`tests/app/test_scoreboard_editions_api.py`; the Track Record's twin is in
`tests/dashboards/test_track_record.py`). The dashboard warm-up asks with no edition, so its cache key
follows the default (`pipeline/warm_dashboards.py`).

`GET /scoreboard/editions` lists Edition 0 and every stored edition, the `default` and the cut `notice`. The
page's header switcher and `?edition=` URL parameter pick another edition.

### Join dates

An edition re-runs a thesis only over the nights each company was in its basket. `basket_member` has no time
axis, so the join date is derived (`editions/entry.py`; `python -m editions.entry --report` prints them):

- Exact: the first `basket_snapshot` holding the company (snapshots begin 2026-09-15).
- Estimated: for members of the seed snapshot, the night we first pulled the company's price or insider
  data, never before the thesis existed, refined to the thesis's own batch, never after the first recorded
  card naming the company, and never after the seed snapshot.

Every input is read as known at the edition's cut, so its join dates are fixed and reproducible. A later
edition carries its parent's join dates over; the nightly adds an exact date for a company new to it.

### Trial and publish

`editions.run trial` re-runs one workspace (default the founding workspace) into a run directory and writes
no database row: the public-clock mirror cut at `--known-by`, the cards, the join dates, a self-check and a
preview against the record (`editions/preview.py`). It is how you look before publishing.

`editions.run publish` stores Edition 1 for one workspace: it runs the trial at the cut, then writes the
edition, its join dates, one `rerun` page per thesis-night and a `published` event in one transaction. It is
a dry run unless `--apply`. It refuses (exit 2, nothing run or written) when the workspace already holds
Edition 1 or an edition at the same cut, when it has no live card (`NO_LIVE_CARDS`), or, unless `--end`
names the last night, when it has no honest night on its own record yet (`NO_RECORDED_NIGHT`). The default
last night is the workspace's own latest recorded night (`editions.engine.latest_recorded_night`). Edition 1
and every later cut store through one path, `store_edition`.

### A new workspace's Edition 1

V1's history was kept at the V2 cutover: the founding workspace keeps its `calls` log (Edition 0) and its
Edition 1, and V2's rule changes are its Edition 2, cut through the automatic cut. A publish there refuses
as already published.

A new workspace has no edition until its Edition 1 is published, and today that is done by hand: the
operator runs `publish --workspace <id>`, and nothing schedules it. Until then the workspace's Scoreboard
default is Edition 0. Its record starts on the night its first nightly call is recorded, so workspaces start
on different nights; Edition 1 is a re-run from each company's join date, not the record. On a workspace
with no edition the nightly adds no page and the cut tries nothing; each prints one line and exits 0. After
Edition 1 is published the nightly appends only nights after its last night.

### The nightly page

The current edition (the newest, not closed) grows: `editions.run nightly --apply` adds one page per live
thesis for every market session it lacks, in every workspace. The cron sidecar runs it after the nightly
backup; `editions/nightly.py` holds the contract.

- Gate: it runs only after a finished, live nightly pass and pages through that pass's as-of; `--end`
  bypasses the gate for a manual run.
- Locks: it holds the editions lock and refuses (exit 75) while another editions job or the call-of-record
  lock (a pass, a backfill, a repair) is held. It only probes the call-of-record lock, again before the
  write, so it never delays a pass.
- Pages: per thesis, every real session after its newest page; tonight's is `nightly`, a missed one
  `catchup`. The roster per night is the latest `basket_snapshot` by that night's cap; an archived thesis
  gets no page. Appends are idempotent: a second run the same night adds no row.
- The hold: a thesis with a member still onboarding gets no page, because on the public clock it would be
  computed on a partial filing history and then frozen. The next run catches up once the name has loaded.
- The ink: an episode whose signal window ended (`exit_by`, never the de-arm) and whose closes are all in
  is scored once into `edition_outcome`, and the Scoreboard uses that score from then on.
- The self-check (`editions/selfcheck.live_edge`): tonight's page against tonight's recorded call, full-card
  equality with money to the cent. A row recorded under another policy or image (a deploy day) is skipped;
  a real mismatch pages through `notify_health` with counts only.
- The artifact in `data/edition_runs/` carries the one line the Admin page shows.

The nightly is on by default (`EDITIONS_NIGHTLY` in `docker-compose.yml`) and on in production. With it off,
nothing grows the current edition and nothing cuts a new one except a hand cut.

### The automatic cut

An edition is never edited. It is replaced only when one of two events happened since its cut or its last
no-change trial; late data alone starts nothing and reaches the next edition. `editions/cut.py` holds the
contract. The nightly runs it first, after the locks and before the read; `editions.run cut` runs it by
hand.

- The rules changed: the engine fingerprint (`editions/fingerprint.py`) differs from the last one checked
  for the current edition E. It hashes the normalized logic of every module the engine's output depends on,
  without comments or docstrings, plus the values of the Settings the engine reads (`ENGINE_SETTINGS`);
  `domain/settings.py` and `domain/rule_log.py` are left out. A fingerprint from an older version counts as
  a change.
- A data repair ran: a `data_repair` journal row applied since then that changed at least one row. Every
  repair CLI that rewrites fact history journals each `--apply` and `--revoke` under the data tenant
  (`repositories/data_repair_repo.py` lists them; a dry run journals nothing). A workspace's cut reads its
  own journal rows and the data tenant's, never another workspace's, so one platform repair starts a trial
  in every workspace's current edition. Onboarding's split-history fill journals too
  (`pipeline/split_history.py`), so a company joining a card can start one. Nightly data flow and tools that
  touch only `calls` are not repairs.

With an event, the trial runs:

- The rules trial (P) re-runs E's `rerun` pages at E's own cut, with E's frozen join dates and thesis
  bodies, and compares them card by card with E's pages. E's nightly and catch-up pages are not re-tested:
  each has its own cut.
- The repair trial compares the new run N (today's engine at now, from every join date) with every page E
  holds. A repair's rows are stamped after E's cut, so only N sees them, and N cannot separate a repair from
  data that arrived late; the label says so.
- The decision: P differs, or a repair is pending and N differs on a night E covers, cuts a new edition.
  Otherwise a `trial_no_change` event on E records the fingerprint, rule ids and repairs checked, so the same
  change is never tried again, and tonight's page goes on E.
- The cut stores N (number E+1, parent E, the proven causes in `edition_cause`), closes E and inks E's
  running episodes, in one transaction. E stays readable; the default moves to N.
- Labels: "rule change" with the rule-log entries E does not carry; "unexplained change" when none is
  declared (published marked, and it pages through `notify_health`); "data repair" with the summaries; each
  noting the data that arrived since E. A deleting repair riding a rules change makes the rules trial's
  differences `not_separated`, and nothing pages.
- At most one new edition per market day, never two at one cut. A cut waits (status `deferred`, nothing
  written, exit 0) while a member of N's rosters has an onboarding row that is not done; a `failed` row
  blocks every cut until `python -m pipeline.onboarding --retry-failed` re-queues it. Inside the nightly a
  trial fault is reported and tonight's page still goes on.
- The notice: for seven days, one quiet line under the switcher with a "see what changed" link
  (`notice` on `GET /scoreboard/editions`), one Admin line (`editions_cut`) and one Slack line
  (`Notifier.notify_edition`).

A change to call logic declares itself by appending one entry to `domain/rule_log.py` in the same PR; the
next run tries it.

### What changed

`GET /scoreboard/editions/diff?from=A&to=B` compares two editions on read and stores nothing
(`scoreboard/diff.py`). Edition 0 is read the way the default Scoreboard reads it. Only nights both editions
cover count, through the earlier edition's last night, and a night with no page reads that edition's last
page, so a cron gap is never a change. It compares substance (armed state, verdict, grades, exit-by, episode
scores), never card text. `cause` is set only when the automatic cut made the later edition from the
earlier one; any other pair's causes stay null. The drawer is uncolored: a comparison is context, never an
alarm (invariant 7).

### Cutting by hand

These rules hold when the operator runs `editions.run cut` or `publish` on production:

- Run it in the cron container: it carries the replay extra the engine needs, and the API image does not.
- Run the dry run first and read it: an edition is append-only and its label permanent. Do not apply an
  "unexplained change" until you know why the output moved.
- It shares the editions lock with the nightly, so whichever comes second exits 75; it refuses (exit 75,
  nothing written) while the call-of-record lock is held, at the start and again before the write. Stay
  outside 21:30 to 23:30 ET, when the nightly pass and its editions job run.
- `scripts/deploy.sh` checks only the call-of-record lock, not the editions lock, so a cron deploy kills a
  running cut. The store is one transaction and nothing partial lands, but the run is lost: do not deploy
  `cron` during a hand cut.
- Check the onboarding queue first (`python -m pipeline.onboarding --status`): a loading company defers the
  cut and a `failed` row blocks it.

## Contracts

- Tables: `edition`, `edition_event`, `edition_entry`, `edition_call` (migration 0063), `edition_outcome`
  (0064), `data_repair` and `edition_cause` (0065). The edition tables are WORKSPACE scope and `data_repair`
  DATA scope (`domain/scopes.py`). All are append-only (`no_update` triggers), and nothing deletes from the
  editions store (invariant 12).
- Never `calls`: an edition only reads the log, and `pipeline/daily.py`, `pipeline/daily_job.py` and `calls/`
  import nothing from `editions/` (`tests/editions/test_import_graph.py`).
- Lock: `LOCK_ID_EDITIONS` in `pipeline/run_lock.py`, held by `nightly` and `cut`; `publish` takes none and
  refuses a duplicate inside its write transaction.
- Exit codes: 0 done (including deferred and nothing to do), 2 a publish refusal, 75 a lock held.
- Every edition and page carries its engine fingerprint. Contract text on the recorded card is code, so a
  change to it moves the fingerprint.
- The cron image carries the replay extra (`PIP_EXTRAS` in `docker-compose.yml`); the API reads stored
  editions with psycopg alone.
- Wire shapes: `backend/openapi.json`.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `GET /scoreboard/editions`, `GET /scoreboard/editions/diff`, `edition=` on the Scoreboard, price window and Track Record | anyone signed in, own workspace | `require_user` on the router (`app/main.py`), `get_workspace`; `tests/app/test_auth_gate.py`, `tests/app/test_workspace_isolation.py` |
| The header switcher and the "what changed" drawer | the viewer | page state |
| `editions.run nightly --apply` | machine: the cron sidecar, after the nightly backup | `daily_cron.sh` and `EDITIONS_NIGHTLY`; the locks |
| `editions.run cut`, `editions.run publish` (with `--apply`) | operator, in the cron container | dry run by default; the locks; the publish refusals |
| `editions.run trial`, `editions.entry --report` | operator or developer; no database row written | read-only by construction |
| The `editions_nightly` and `editions_cut` lines on the Admin page | steward | `require_super_admin` |
| A new `domain/rule_log.py` entry | developer, in the PR that changes call logic | review; an undeclared change publishes marked and pages |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `EDITIONS_NIGHTLY` (`ALPHADECK_EDITIONS_NIGHTLY`): the nightly's kill switch | `backend/scripts/daily_cron.sh` | yes, cron service |
| `EDITIONS_TIMEOUT_S` (`ALPHADECK_EDITIONS_TIMEOUT_S`) | `backend/scripts/daily_cron.sh` | yes, cron service |
| `scoreboard_default_edition` (`ALPHADECK_SCOREBOARD_DEFAULT_EDITION`) | `domain/settings.py` | no |

A change to `market_tz` or `price_settle_time` (`domain/settings.py`) changes the fingerprint and starts a
trial.

## Known limits

- A new workspace's Edition 1 is published by hand; until then its default view is Edition 0.
- The rules trial does not re-test nightly and catch-up pages; a change on one of those nights is
  `not_separated`.
- A repair trial cannot separate a repair from late data.
- An edition published before thesis bodies were frozen reads today's kill criteria and catalysts in a rules
  trial, and leaves `counter_case` and `catalyst_surface` out of the comparison.
- `scripts/deploy.sh` does not check the editions lock.
- A trial re-runs a whole edition, so its time grows with the edition; `EDITIONS_TIMEOUT_S` bounds the
  nightly, and a timed-out night catches up on the next.

## Related

[SCOREBOARD](SCOREBOARD.md) · [REPLAY](REPLAY.md) · [FEED_LOOP](FEED_LOOP.md) · [ADMIN](ADMIN.md) ·
[INVARIANTS](INVARIANTS.md) (1, 7, 12) · [TENANT_SCOPES](TENANT_SCOPES.md) ·
[Track Record](dashboards/track-record.md)
