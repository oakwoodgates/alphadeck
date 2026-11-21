# The V2 cutover runbook

Read this whole page once before the day. Then follow Part 4 in order. Do not skip a step and do not reorder
them.

## 1. Who this is for, and what the cutover does

You are the operator and the steward, doing this once, by hand, on the one host that runs production
(Docker Compose, `docs/DEPLOY.md`). Every command below is meant to be copied and run from the **main checkout**
in Git Bash, not from a worktree and not from PowerShell. Where a value is not in the code, this page says so.

**What the cutover does.** V2 goes live with one change to the production database, in this order:

1. Deploy the V2 code with the cron sidecar held stopped.
2. Apply the V2 migrations (the backend does this when it boots).
3. Move the platform facts (share counts, cash/burn) to the shared data tenant, in one transaction
   (`python -m pipeline.v2_cutover run --apply`). V1's history is kept: nothing is deleted from it.
4. Run the first extraction backfill.
5. Start the cron.
6. Cut Edition 2 by hand (`python -m editions.run cut`), before the first V2 night. This is how V2's rule
   changes reach the Scoreboard. Then, only if you chose it (O1), turn the editions nightly job on (step 11b).
7. Verify, after the first V2 night.

**What is kept: V1's history.** The operator decided on 2026-11-19 to keep V1's record. The cutover deletes
nothing from these eight tables. It counts them before and after, inside its one transaction, and refuses (and
rolls back) if any count differs:

| Table | What it holds |
|---|---|
| `calls` | the immutable calls log: what the system said each night |
| `call_annotation` | notes on past calls (nothing on `v2` reads them; decision K1 keeps them) |
| `edition` | the Scoreboard's editions |
| `edition_event` | each edition's event log |
| `edition_entry` | each thesis's join date in an edition |
| `edition_call` | the re-run pages |
| `edition_outcome` | matured scores |
| `edition_cause` | the causes for a change of edition |

The only delete anywhere in the cutover is the stray unratified purity row on the data tenant, and only if you
pass `--orphan-purity delete` (decisions N1 and K7). The default, `leave`, deletes nothing.

**What this changes on screen.**

- The Board keeps V1's last call for each card until the first V2 night writes a new one (the plan's reading;
  not seen in a rehearsal, because no one signed in).
- Edition 0 (the calls log) now covers V1's nights and V2's nights together. It scores V1's annotated rows too:
  V2 removed the set-aside for them (decision K5). Its numbers will differ from what production shows today.
- Edition 1 stays, closed and readable. Edition 2, cut in step 11, becomes the Scoreboard's default.
- The first V2 night compares each card with V1's last call, so it can push (decision K3, step 12).

**What is moved (relabeled, not copied or deleted).** The founding workspace's rows in `fact_shares_outstanding`
and `fact_cash_burn` change owner to the shared data tenant. Ratified `fact_revenue_mix` rows under the data
tenant, if any, change owner to the founding workspace. The row ids, values and `recorded_at` do not change.

**What is kept.** Everything else: every thesis (ids and `created_at` unchanged), every basket member and
snapshot, kill criteria, evidence, exclusions, thesis catalysts (`fact_thesis_catalyst`: a card's counting catalyst
entries; W6's migration 0213 turned each `fact_theme_conviction` version into one whole-card entry), term
mentions, `fact_theme_conviction` (kept, and read by nothing), `operator_decision`, positions, users, sessions, tokens, tenants, and every market, filing and price fact. The
dry run counts them before the apply, and the provenance file records a baseline so `verify` can prove that no
company was dropped and no row of V1's history was lost.

**The one rule exception.** `docs/INVARIANTS.md` says the calls log and the editions store are immutable. It names
the cutover (`backend/pipeline/v2_reset.py`, the `run` command) as the only code allowed to delete from them. As
built, it deletes nothing from them. It leaves one `data_repair` journal row with tool `v2_cutover`.

## 2. Decisions to make before the day

Each row has a flag or setting with a safe default, so you can do the cutover without answering the open ones,
and the default is what happens if you do not choose. The "plan's recommendation" column is only what the plan
wrote down. Rows K1 to K7 were decided by the operator on 2026-11-19, except that K3 still needs your
confirmation: the last column shows each decision. Rows N2, N5 and N6 are superseded by K1, K3 and K4 and are
kept here for the record. Row O1 is open.

| # | Decision | Options | Carried by | Safe default | Plan's recommendation (not a decision) | Your decision |
|---|---|---|---|---|---|---|
| N1 | The one stray unratified `fact_revenue_mix` row on the data tenant (on the fork it was DEVCO, a seed demo company on no card; production may differ) | (a) leave it, unread; (b) delete it (journaled, and it is in the backup); (c) ratify it into the founding workspace | `--orphan-purity leave` or `--orphan-purity delete` on `run` | `leave`. Option (c) is not built | (a) | Decided as K7: (a) leave |
| N2 | SUPERSEDED by K1. (Was: when to drop the empty `call_annotation` table.) The table is no longer emptied: it keeps its V1 rows | none | nothing in the cutover | the table stays, with its rows | none | Superseded by K1 |
| N4 | The first backfill | (a) live, now; (b) cache only (`--no-live`), live later | `--apply` or `--apply --no-live` on `pipeline.extract_values` | (a) live. On the fork, the cache-only run failed 1,016 companies with "not cached", so (b) leaves most companies with no value yet | (a) | |
| N5 | SUPERSEDED by K3. (Was: the first V2 night is silent.) It is not silent any more: V1's calls are kept, so the first night compares with V1's last call | none | none | none | none | Superseded by K3 |
| N6 | SUPERSEDED by K4. (Was: publish Edition 1 after the first night.) Edition 1 already exists and stays; Edition 2 comes from the cut (step 11). `editions.run publish` now refuses: Edition 1 is already published | none | none | none | none | Superseded by K4 |
| N7 | Caps on the existing workspaces | (a) leave them unset (no cap); (b) set them | `python -m pipeline.users caps` (any time) | (a): NULL means no cap | (a) | |
| N8 | In-flight user state (decisions, positions) | (a) keep; (b) reset | the only built option is (a); the dry run prints the counts | (a) | (a) | |
| N9 | A separate archive of V1's history | (a) take it; (b) the full backup is enough | `--no-history-archive` on `run` | (a): archive taken | (a) | Decided as K2: (a) take it |
| N10 | Cut over before or after W6 (catalyst conversion) | (a) after W6 is merged and rehearsed; (b) before | the go-live date; no code. Status: W6 is merged. Rehearsal 2 ran on a commit that includes it (R0 to R9, a first night by hand, and the Edition 2 cut); R10, R11b and R13 are not yet rehearsed | none: it is a date choice | (a) | |
| N11 | Day and time | (a) a weekday morning; (b) a Saturday | the tools refuse only inside 21:30-23:30 ET (`run`, the backfill) and 22:20-22:50 ET (`deploy.sh`). A morning also makes the first V2 night a new night after the cutover (see step 13: N2, N5) | a time that leaves hours before 21:30 ET, with the cut (step 11) done before 21:00 ET | (a) | |
| N12 | Retag V1's images for a fast rollback | (a) yes; (b) roll back by a revert merge on `main` and `deploy.sh` (slower: CI) | Part 4, step 2 | (a) yes | (a) | |
| N13 | The `/backtest` archive (frozen V1 passes bound read-only by `docker-compose.prod.yml`) | (a) keep showing it; (b) unbind it | no change needed | (a) | (a) | |
| N14 | The `CLAUDE.md` Commands lines for this work (the file is write-protected for agents) | check them by hand. The lines for `v2_cutover run` and `verify` already say V1's history is kept (checked in this worktree) | by hand | not changed until you do | check them | |
| K1 | The 176 `call_annotation` rows of V1 (the fork's count) | (a) keep (nothing on `v2` reads them); (b) delete them alone | nothing in the cutover: the table is in `HISTORY_TABLES` | (a): the cutover deletes no row from it | (a). (b) would make the journal row a deleting repair, and Edition 2's rule change would be "not separated" | Decided 2026-11-19: (a) keep |
| K2 | The V1 history archive (`-v1-history`) | (a) keep taking it (a frozen snapshot of the eight kept tables); (b) skip it (the full backup holds them) | `--no-history-archive` on `run` | (a): archive taken | (a) | Decided 2026-11-19: (a) keep taking it |
| K3 | The first V2 night's pushes | (a) accept them, with a heads-up; (b) mute delivery for one night, with no code (clear the Slack webhook of each founding-workspace user in Settings, and unset the environment `SLACK_WEBHOOK_URL` for the cron for the night; restore both the next morning); (c) code a notify baseline (not built; no PR) | none for (a); by hand for (b) | (a) | (b). Decide after rehearsal 2 shows the count: if it is small, (a) is fine | Recommended: accept the pushes (no mute). Operator to confirm. Rehearsal 2 measured the count (step 12) |
| K4 | When Edition 2 is cut | (a) by hand on cutover day, after the cron starts (step 10) and before 21:00 ET (dry run, then `--apply`); (b) let the first night's editions job cut it (not possible while the editions nightly job is off: see O1); (c) by hand the next morning | step 11 | (a) | (a): the dry run shows the label before it becomes permanent, and the first night's job then has no cut to make | Decided 2026-11-19: (a) by hand |
| K5 | Edition 0 now scores V1's annotated phantom arms (V2 removed the set-aside) | (a) accept: Edition 0 is what the system said, and the Scoreboard defaults to Edition 2; (b) restore a V1-only set-aside (code; not built) | none | (a) | (a) | Decided 2026-11-19: (a) accept |
| K6 | The `--confirm "reset NAME"` phrase | (a) keep it; (b) rename it | the guard in `v2_reset.py` | (a): the phrase is unchanged though nothing is reset | (a) | Decided 2026-11-19: (a) keep |
| K7 | The orphan purity row under keep-history | `leave` or `delete` | `--orphan-purity` on `run` | `leave`. `delete` makes the journal row a deleting repair | `leave` | Decided 2026-11-19: `leave` |
| O1 | OPEN: the editions nightly job on production. It is off: production's cron log says `EDITIONS_NIGHTLY=0`. The compose default, `ALPHADECK_EDITIONS_NIGHTLY`, is 1. Decide before go-live | (a) turn it on; (b) leave it off | `ALPHADECK_EDITIONS_NIGHTLY` in production's `.env`, read when the cron starts (`docs/FEED_LOOP.md` calls it an env-only cron deploy) | leave it as it is (off) | none: the operator has not decided. The read-only review of the cron's first start (`docs/temp/v2-cron-first-start-review-2026-11-20.md` in the main checkout) found no technical reason for it being off, and suggests turning it on only after Edition 2 is cut and verified (step 11b). Keep it at 0 until then (Part 3) | OPEN |

**What O1 means.** `ALPHADECK_EDITIONS_NIGHTLY=0` turns off the whole `editions.run nightly` call in the cron
(`backend/scripts/daily_cron.sh`): tonight's page, catch-up pages, a new card's join row, the ink, the self-check
**and the automatic cut**. So while it is off, nothing cuts an edition except you, by hand. After the hand cut in
step 11, Edition 2 ends at the newest night on record then (V1's last night, because step 11 comes before the first
V2 night). With the job off, no later night is added to Edition 2, the Scoreboard's default view stops at the
cutover while Edition 0 keeps growing, and a card made after the cut never joins Edition 2 (Part 9).
Production's Edition 1 holds only 10 theses (669 re-run pages) and was paged once (2026-10-24), which fits a job
that has been off.

The review found no bug note, performance note or rule-log entry that explains why it is off. The only stated
reason it found is "OFF by the operator's hold" (`docs/temp/dashboard-choices-2026-10-30.md`, line 1255, in the
main checkout; not checked from this branch). So it reads as a hold with a motive only you know. Whether it goes
on after go-live is your decision.

**Keep it at 0 through the cut (steps 10 and 11).** If you turn it on, do it in step 11b, after Edition 2 is cut
and verified. Turned on earlier, the first night's job would run the cut itself with no dry run, and if that cut
deferred or failed, that night's V2 pages would go onto Edition 1.

## 3. Preconditions checklist

Do not start Part 4 until every box is ticked. Tick them on the day, not from memory.

- [ ] Rehearsal 2's record (Part 7, "Results") has been read, with its two timing artifacts and its limits (Part
      9). It ran on `e54c0d0b`, the merge of the pull request that made the cutover keep V1's history. As this page is
      written, `v2` differs from `e54c0d0b` only in docs (`CLAUDE.md`, `docs/INVARIANTS.md`). Any code change after
      rehearsal 2 means rehearsal 3 (Part 7). A go-live-shaped night has not been rehearsed (Part 9).
- [ ] The merge-main routine in `docs/V2_BRANCH.md` has been done one last time, and that pull request is merged
      into `v2`.
- [ ] The `v2` to `main` pull request is open, CI is green on it, and it is **not merged yet**. Plan to merge it
      with a merge commit, not a squash and not a rebase (this keeps `v2`'s history and gives one commit for
      `git revert -m 1`).
- [ ] The decisions in Part 2 are answered, or you have accepted the defaults on purpose. In particular K3 (the
      first night's pushes) is confirmed, and O1 (the editions nightly job) is decided.
- [ ] Disk space: room for four labeled full dumps and the history archive. A production dump was about 1.7 GB
      in the plan's measurement (estimate: about 7 GB in all). In rehearsal 2 the `-pre-v2-reset` dump was
      1,712,905,676 bytes, the `-v1-history` archive 204,627,535 bytes and the `-pre-edition-2` dump
      1,719,725,430 bytes. Check the free space where `./data/backups` lives.
- [ ] Production `.env` (open it yourself; this page never shows it) has, by name: `ALPHADECK_USER_AGENT` (the
      backfill refuses a live run without it) and `ALPHADECK_OPS_TOKEN` (`deploy.sh` reads it; `docs/DEPLOY.md`).
      Also check any `ALPHADECK_ALLOWANCE_*` override, `ALPHADECK_EDITIONS_NIGHTLY` (O1), and the variable changes
      in Part 8.
- [ ] **V1 recorded the last session before today (call it night D-1).** Open the Admin page. The cron status
      reads current, with no missed night, and the newest run record is finished, live, started at or after 22:30 ET
      on D-1, and recorded for at least one thesis. (In the files, `/data/cron_runs/*.json`, that is `status`
      `finished`, `mode` `live`, a `started_at` after 22:30 ET on `asof` D-1.) If not, let V1 catch it up first:
      `docker restart alphadeck-cron-1`, which is the V1 cron and runs V1's own boot catch-up. Watch
      `docker logs alphadeck-cron-1` until that pass ends and the log shows `daily-cron: next run`, then check the
      Admin page again. Do not restart it inside 22:20 to 22:50 ET. **Never leave night D-1 to V2.** A V2 catch-up
      pass would write call rows dated before the cutover. N5 would then fail for good (the calls log is
      immutable), and V2 would push those changes to Slack labeled "recorded late" (step 10).
- [ ] **Production's `EDITIONS_NIGHTLY` value is written down, and it stays 0 through the cut (O1).** Read it:
      `docker exec alphadeck-cron-1 printenv EDITIONS_NIGHTLY` (it should print `0`; the cron log's
      `daily-cron: EDITIONS_NIGHTLY=0` line says the same after each night's backup). In production's `.env` (open it
      yourself), check that `ALPHADECK_EDITIONS_NIGHTLY=0` is set by name. The compose default is 1, so a missing
      line would turn the job on when step 10 recreates the cron. Do not change it before step 11b. Decide now whether
      it goes on after go-live (O1).
- [ ] You are on the main checkout, the tree is clean, and you have Git Bash open (not PowerShell's `bash`, which
      is WSL).
- [ ] It is a weekday morning or another time that leaves hours before 21:30 ET, so the first V2 night is a new
      night after the cutover. Do not start within an hour of 21:30 ET (see Part 9, "Known limits").
- [ ] You have the time. Steps 1 to 11 must end before 21:00 ET. The plan's estimate for steps 1 to 10 is a few
      hours, mostly the backfill (25 minutes 23 seconds on the fork at 1.5 requests a second, rehearsal 2), two
      or three backups (about 5 to 6 minutes each) and the builds. Step 11 adds a backup and the cut: a dry run
      of 1,132 seconds and an apply of 1,103 seconds at 4 workers on the fork (rehearsal 2). Start step 11 no later
      than about 19:30 ET: its backup, dry run and apply take about 45 minutes, and it must end well before 21:30 ET.
- [ ] The user is told that the Board keeps V1's last calls until the first V2 night, that Edition 0's numbers
      change (K5), that the Scoreboard will default to Edition 2, and that nobody edits cards from step 1 until
      step 8 is done.

## 4. The steps

Conventions used below:

- The production compose command is always written out in full, as `docs/DEPLOY.md` gives it:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml`.
- The production containers are `alphadeck-backend-1`, `alphadeck-cron-1` and `alphadeck-postgres-1`.
- Inside the backend container, `/data/backups` is the host's `./data/backups`. `/data/cutover_runs` is on the
  `appdata` volume (not visible on the host unless you copy it out with `docker cp`).
- `<ts>` is a UTC timestamp such as `20261125T140000Z`. Copy the real one from the output of the step before.
- A duration written "measured on the fork, 2026-11-20" comes from rehearsal 2 (Part 7). The fork runs on the
  same host as production, but production's database is larger or equal, so it is a fork measurement, not a
  promise. Durations marked **estimate** come from the plan (a measured backup on a production-size dump, or a
  guess); the rehearsal did not time them.
- "Expected output" quotes the program text. Where a line depends on production data, it says so. Example output
  blocks are rehearsal 2's, from the fork: its database is `alphadeck_v2`, so its `target DB` line differs from
  production's.

### Step 1. Announce, then freeze (stop the cron)

**Before this step**, the two Part 3 boxes about V1's last night and `EDITIONS_NIGHTLY` must be ticked. Once the V1
cron is stopped, a missed night can be caught up only by V2, and that breaks N5 (step 10).

```bash
cd /c/Users/funky/sites/oakwoodgates/alphadeck && git branch --show-current && git status --short
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop cron
```

*What it does.* Tells you which checkout you are in, then stops the V1 cron so no V1 pass writes while you work.
Do not edit cards from now until step 8 is done. Do not run this inside 22:20 to 22:50 ET, and not while a pass
is running (check the Admin page).

*You should see.* `main` (or the branch you expect) and no changed files; then Compose reporting the cron
container stopped.

*Duration.* Seconds (estimate). **Not yet rehearsed** (production only).

*If it fails.* If the cron will not stop, stop here and look at `docker ps`. Do not continue with a cron that is
running: `deploy.sh --hold-cron` refuses (exit 3) when it is.

### Step 2. Keep V1's images for rollback (decision N12)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml images
```

Then tag each image the command lists for the backend, cron and frontend services. The image names are not in
the code; copy them from the output. For each one:

```bash
docker tag <image name>:<current tag> <image name>:v1-pre-v2
```

*What it does.* Keeps V1's images under a second tag. The V2 build in step 4 overwrites the current tags, and
nothing else keeps V1's images. Without these tags, rollback means a revert merge on `main`, then `deploy.sh`.

*You should see.* Three `v1-pre-v2` tags in `docker images`. **Not yet rehearsed:** the image
names (the rehearsal runs on the fork, whose names differ).

*Duration.* About 1 minute (estimate).

*If it fails.* A failed tag command changes nothing. Fix the name and retry. Do not go on without the tags
unless you chose option (b) in N12.

### Step 3. Merge `v2` into `main`, then check out `main`

Merge the `v2` to `main` pull request on GitHub (merge commit). Then, in the main checkout:

```bash
git checkout main && git pull --ff-only origin main
git rev-parse HEAD
```

*What it does.* Puts the main checkout on the merged commit, which is the only thing `deploy.sh` will build.
Write the commit id down: you need it if you roll back (`git revert -m 1 <that commit>`).

*You should see.* `main` at the merge commit.

*Duration.* About 5 minutes plus CI (estimate).

*If it fails.* If `--ff-only` refuses, your local `main` has commits that are not on `origin/main`. Stop and
sort that out. `deploy.sh` refuses unless `HEAD` equals `origin/main`.

### Step 4. Deploy V2's backend and frontend with the cron held stopped

```bash
bash scripts/deploy.sh --label pre-v2-cutover --hold-cron --dry-run frontend backend
bash scripts/deploy.sh --label pre-v2-cutover --hold-cron frontend backend
```

*What it does.* The first command prints the plan and touches nothing. The second takes a labeled backup
(`alphadeck-<ts>-pre-v2-cutover.sql`: V1's schema and V1's data), builds the two services from `main`, and
verifies the result. `--hold-cron` keeps the cron stopped: the build is `--no-deps`, so it never starts it. When
the backend boots it applies the V2 migrations (0200 to 0213; check the boot log). **This backup
is your V1 rollback point.**

*Refuses (exit 3, nothing changed)* on a linked worktree, no `.env`, a dirty tree, a branch other than `main`,
`HEAD` not equal to `origin/main`, a clock inside 22:20-22:50 America/New_York, a pass running, the
call-of-record lock held, or (the extra check from `--hold-cron`) the cron container not stopped. Exit 2 means a
usage error: `--hold-cron` with `cron` named, or with `--env-only`.

*You should see.* In the dry run, the line `deploy PLAN for frontend backend (rebuild) (cron held stopped)`
followed by the rest of the plan. In the real run, the verification line
`ok: alphadeck-cron-1 is stopped (held by --hold-cron)` and a final line that begins
`OK: deployed <sha> to prod: frontend backend (rebuild) (cron held stopped)` and ends with
`every verification passed`. The Admin page may show a loud `platform_rows_in_workspaces` line until step 7.
That is expected: it counts platform rows still under a workspace. **Not yet rehearsed** (the rehearsal rebuilt the
fork's images with `fork.sh up` and never ran `deploy.sh`): the exact output of a real run, and whether this
Admin line trips `deploy.sh`'s alarm comparison (the plan lists it as a risk it could not confirm; it checks
that `cron.alarms` did not grow). The fork rebuild (R0) took 94 seconds (measured on the fork, 2026-11-20); it is
not the production build.

*Duration.* Backup about 6 minutes (measured on a production dump); build and boot 5 to 10 minutes (estimate).

*If it fails.*
- Exit 3: nothing changed. Read the `REFUSE:` lines, fix the cause, run again.
- Exit 4: a step failed (the backup, or the build). The rest of the stack may be half-changed. Follow
  `docs/DEPLOY.md` (the stack-recovery guidance) and do not go on to step 5.
- Exit 5: it deployed, but a verification check failed. Read the `FAIL:` lines. If the cron came up, stop it
  again (`docker compose -f docker-compose.yml -f docker-compose.prod.yml stop cron`) before you go on.

### Step 5. The read-only dry run (`plan`)

```bash
docker exec alphadeck-backend-1 mkdir -p /data/cutover_runs
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover plan --json /data/cutover_runs/prod-plan.json
```

*What it does.* Reads every table, says who owns it and what the cutover does with it, and lists every surprise
(a row where no plan expects one). It writes nothing to the database. The `mkdir` is here because `plan --json`
does not create the folder.

*You should see.* The table, then `SURPRISES (n)`, `UNMAPPED TABLES: none` and one of:
`RESULT: CLEAN - no surprises. Exit 0.`, `RESULT: REFUSE - 1 surprise(s). Exit 1.` or the same with `Exit 2`.
If production still has the one stray unratified purity row (N1), expect exactly that one surprise and exit 1;
the apply acknowledges it. Compare the counts with rehearsal 2's output: only the days since that dump should
differ. In rehearsal 2 on the fork it ended `RESULT: REFUSE - 1 surprise(s). Exit 1.`, with the one surprise
being the stray purity row (`fact_revenue_mix: 1 row(s) under the data tenant with ratified_by NULL - purity is
read from the workspace only in V2 and no plan moves an unratified row`), and these rows:
`fact_shares_outstanding` 2219 `move→data` (auto 1977, operator 242), `fact_cash_burn` 64 `move→data`, and the eight
history tables each `keep (V1 history: operator decision 2026-11-19)` (`calls` 2040, `call_annotation` 176,
`edition` 1, `edition_call` 682, `edition_entry` 855, `edition_event` 2, `edition_outcome` 113, `edition_cause` 0).
The full counts are in Part 7.

*Duration.* Measured on the fork, 2026-11-20: 2 seconds.

*If it fails.* Exit 2 means a table the registry does not name, or a named table the database lacks. Stop. This
needs a code fix (the merge-main routine in `docs/V2_BRANCH.md`), not a workaround. Any surprise other than the
stray purity row (a platform row under a workspace, a workspace company catalyst, and so on): stop and
investigate; `run` will refuse it too.

### Step 6. The cutover dry run (`run` without `--apply`)

```bash
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover run --database alphadeck --prod --confirm "reset alphadeck" --plan-out /data/cutover_runs/prod-run-plan.json
```

Add `--orphan-purity delete` only if you chose N1 option (b). Add `--no-history-archive` only if you chose K2
option (b). Use the same flags in step 7.

*What it does.* Reads the database name from its own connection and refuses unless it matches `--database` and
`--confirm` is exactly `reset alphadeck` (the phrase is unchanged though nothing is reset any more; decision K6).
Then it runs the preflight and prints the plan. It is a read-only session: it cannot write. It takes no locks and
no backup. It writes only the plan file you name.

*You should see.* The first lines, `V2 CUTOVER - RUN - DRY RUN` and
`target DB : alphadeck (confirmed; production, --prod)`, then `ok: P3 outside the nightly window`, then the
preflight lines (in this table), then the plan lines and the end:

| Line | Meaning |
|---|---|
| `ok: P1 every migration file is applied` | the V2 migrations ran in step 4 |
| `ok: P2 the data tenant and the founding workspace exist` | |
| `ok: P5 no surprises` or `ok: P5 rule 8 acknowledged by --orphan-purity leave: n unratified data-tenant purity row(s)` | the stray purity row, if any |
| `ok: P6 no workspace company catalysts` | |
| `ok: P7 every share-count and cash/burn ratified_by is in 0206's vocabulary` | so the constraint can be validated |
| `ok: P8 the move has no refusals` | |
| `count: P9 kept: operator_decision n, theses with position fields n, fact_theme_conviction n, fact_thesis_catalyst n` | counts only; never a refusal |
| `count: P9 V1 history kept: calls n, call_annotation n, edition n, edition_event n, edition_entry n, edition_call n, edition_outcome n, edition_cause n` | V1's history, which the cutover keeps; counts only |
| `ok: P10 not cut over yet` | |
| `plan: move fact_shares_outstanding n row(s) (...)`, `plan: move fact_cash_burn ...`, `plan: move fact_revenue_mix ...` | what will move, by `ratified_by` |
| `plan: orphan purity n row(s) (leave)` | |
| `plan-out: /data/cutover_runs/prod-run-plan.json` | |
| `backup: would take a new 'pre-v2-reset' backup and the V1 history archive` | |
| `RESULT: DRY RUN - nothing written. Exit 0.` | |

There are no `plan: reset` lines: nothing is deleted. Read the plan lines. Do they match your expectation (the
number of share-count and cash/burn rows to move, the counts of V1's history, the count of kept decisions and
positions)? This is the dry run of rehearsal 2 on the fork:

```
V2 CUTOVER - RUN - DRY RUN
target DB : alphadeck_v2 (confirmed)
ok: P3 outside the nightly window
ok: P1 every migration file is applied
ok: P2 the data tenant and the founding workspace exist
ok: P5 rule 8 acknowledged by --orphan-purity leave: 1 unratified data-tenant purity row(s)
ok: P6 no workspace company catalysts
ok: P7 every share-count and cash/burn ratified_by is in 0206's vocabulary
ok: P8 the move has no refusals
count: P9 kept: operator_decision 0, theses with position fields 0, fact_theme_conviction 1, fact_thesis_catalyst 1
count: P9 V1 history kept: calls 2040, call_annotation 176, edition 1, edition_event 2, edition_entry 855, edition_call 682, edition_outcome 113, edition_cause 0
ok: P10 not cut over yet
plan: move fact_shares_outstanding 2219 row(s) (auto 1977, operator 242)
plan: move fact_cash_burn 64 row(s) (operator 64)
plan: move fact_revenue_mix 0 row(s) (none)
plan: orphan purity 1 row(s) (leave)
plan-out: /data/cutover_runs/r2k-run-plan.json
backup: would take a new 'pre-v2-reset' backup and the V1 history archive
RESULT: DRY RUN - nothing written. Exit 0.
```

*Duration.* Measured on the fork, 2026-11-20: 4 seconds.

*If it refuses or fails.*

| Exit | Meaning | What to do |
|---|---|---|
| 2 | The guard refused (`REFUSE:` names the reason, `RESULT: REFUSED - nothing read or written`), or a table is unmapped or missing (`REFUSED - an unmapped or missing table`) | Fix the flag: `--database` must equal the database the connection is on, `--confirm` must be exactly `reset alphadeck`, and `alphadeck` needs `--prod`. An unmapped table needs a code fix; stop |
| 1 | A preflight line says `REFUSE:` (nothing written) | Read each `REFUSE:` line. P1: an unapplied migration (deploy or migrate first). P5/P6: a surprise (stop and investigate). P7: a row that would fail 0206's vocabulary. P8: the move's own refusal. P10: already cut over, but a row to move or delete is present (do not run again; see the note under step 7) |
| 75 | The nightly window (`REFUSE: P3 inside the nightly window (21:30-23:30 ET)`) | Wait until it ends, or run earlier |

### Step 7. The cutover apply (THE POINT OF NO RETURN)

```bash
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover run --database alphadeck --prod --confirm "reset alphadeck" --apply --expect-plan /data/cutover_runs/prod-run-plan.json
```

Use the same `--orphan-purity` and `--no-history-archive` flags you used in step 6, or the plan will not match.
`--prod --apply` refuses without `--expect-plan`. A plan file written by an older build of the tool differs from
the plan now (it carries a `resets` section) and is refused: take a new dry run.

*What it does, in order.*
1. The guard and the preflight again, then the repair lock (call-of-record lock and fact-writer gate) and job
   locks 3 to 7 are taken and held for the whole run. Any busy lock is exit 75 and names the holder.
2. The plan is compared with the file from step 6. Any difference refuses (exit 1) before any write.
3. The backup, `pipeline.backup.run_backup(label="pre-v2-reset")` of the same database (it refuses if the
   connection string names a different database), checked by `verify_dump`: the file is in the backups
   folder, has a backup name, is not empty, and ends with the `-- PostgreSQL database dump complete` trailer.
   Then the history archive (`-v1-history`: a frozen dump of exactly the eight history tables, never an empty
   table list, which would dump the whole database), checked the same way. Failure is exit 3 with nothing
   written.
4. **One transaction:** re-plan and compare again; read the baseline (the recall baseline, plus each history
   table's count and the list of editions); the three move legs; delete the orphan purity rows (only with
   `--orphan-purity delete`); re-read the eight history counts and compare them with the baseline (any difference
   refuses and rolls back: nothing of V1's history may change); mark the touched companies changed;
   `VALIDATE CONSTRAINT` on the two 0206 constraints; and the `data_repair` journal row (tool `v2_cutover`;
   `deletes` is false unless an orphan purity row was deleted), written inside the same transaction. The
   provenance file is written as `<ts>-alphadeck.json.pending` before the commit.
5. **Commit.** Then the provenance file is renamed to `<ts>-alphadeck.json`.

**The point of no return is the commit in item 5.** Here is what that means.

| | Before the commit | After the commit |
|---|---|---|
| The live database | Unchanged. Any failure rolls back everything (exit 1 or exit 4), re-enables every trigger it disabled, and removes the `.pending` file | The platform facts are under the data tenant; the journal row exists. V1's history is untouched: it was never deleted |
| What you can undo | All of it: nothing was written (the backup files stay on disk) | Only by restoring a backup (Part 5). There is no "undo" command |
| What a restore costs | Nothing | Everything written after the backup. Right after the commit, that is almost nothing. It grows with every hour the V2 stack runs |
| Running the command again | Safe | Does nothing when nothing is left to move (see "second run" below) |

*Second run.* If the cutover has committed, a second run of `run` finds the `v2_cutover` journal row. With
nothing left to move (and, with `--orphan-purity delete`, nothing left to delete) it prints
`ok: P10 already cut over on <date>; nothing to do` and
`RESULT: ALREADY CUT OVER - nothing to do, no backup, no journal. Exit 0.`, whatever the history tables hold (V1's
rows and V2's nights alike). With a row still to move or delete it refuses, exit 1:
`P10 already cut over on <date>, but <n> row(s) to move or delete are present: a second cutover is refused
(investigate by hand)`. Never work around this refusal.

*You should see.* This is rehearsal 2's apply on the fork:

```
V2 CUTOVER - RUN - APPLY
target DB : alphadeck_v2 (confirmed)
ok: P3 outside the nightly window
ok: P4 holding the repair lock and job locks 3-7
ok: P1 every migration file is applied
ok: P2 the data tenant and the founding workspace exist
ok: P5 rule 8 acknowledged by --orphan-purity leave: 1 unratified data-tenant purity row(s)
ok: P6 no workspace company catalysts
ok: P7 every share-count and cash/burn ratified_by is in 0206's vocabulary
ok: P8 the move has no refusals
count: P9 kept: operator_decision 0, theses with position fields 0, fact_theme_conviction 1, fact_thesis_catalyst 1
count: P9 V1 history kept: calls 2040, call_annotation 176, edition 1, edition_event 2, edition_entry 855, edition_call 682, edition_outcome 113, edition_cause 0
ok: P10 not cut over yet
plan: move fact_shares_outstanding 2219 row(s) (auto 1977, operator 242)
plan: move fact_cash_burn 64 row(s) (operator 64)
plan: move fact_revenue_mix 0 row(s) (none)
plan: orphan purity 1 row(s) (leave)
ok: the plan equals --expect-plan /data/cutover_runs/r2k-run-plan.json
backup: /data/backups/alphadeck-20261120T050517-pre-v2-reset.sql verified (complete, 1712905676 bytes)
archive: /data/backups/alphadeck-20261120T051034-v1-history.sql verified (complete, 204627535 bytes)
moved: fact_shares_outstanding 2219
moved: fact_cash_burn 64
moved: fact_revenue_mix 0
kept: V1 history 3869 row(s) (2040 calls)
orphan purity: 1 row(s), leave (0 deleted)
validated: 2 constraints
companies stamped changed: 1224
journal: data_repair 151f0d78-4b4c-4040-b01d-7a0ba802d192
provenance: /data/cutover_runs/20261120T050517Z-alphadeck_v2.json
RESULT: CUT OVER. Exit 0.
```

On production the second line reads `target DB : alphadeck (confirmed; production, --prod)`. There is one `moved:`
line per moved table, and the numbers depend on production's data. The `kept:` line is the sum of the eight history
counts, then the `calls` count. After the apply, the rehearsal counted the history tables again: all eight were
equal to the counts before (Part 7).

Copy the `provenance:` path. Step 8 needs it.

*Duration.* Measured on the fork, 2026-11-20: 327 seconds for the whole apply (backup of 1,712,905,676 bytes,
archive of 204,627,535 bytes, and the transaction). The file-name stamps of the backup and the archive are 05:05:17
and 05:10:34 UTC; the log does not time the three parts separately. The plan's measurement of a backup on a
production dump, about 6 minutes, fits that. The nightly-window check happens once, at the start (see Part 9).

*If it refuses or fails, by exit code.*

| Exit | What happened | State of the database | What to do |
|---|---|---|---|
| 0 | `CUT OVER`, or `ALREADY CUT OVER` | Cut over (or nothing to do) | Go to step 8 |
| 1 | Refused before any write, or `REFUSED - rolled back, nothing written (backup kept: <file>)` | Unchanged | Read the `REFUSE:` lines. A plan that differs from `--expect-plan` means data changed after step 6 (someone edited, or a job ran): find out why, then repeat steps 6 and 7 |
| 2 | The guard, or an unmapped table | Unchanged | As in step 6 |
| 3 | `BACKUP FAILED - nothing written` | Unchanged | Read the `REFUSE: backup...` line (disk space, a `pg_dump` error, the 900-second dump timeout, a missing trailer, or a connection string that names a different database). A half-written `.tmp` file in `data/backups` is not a backup. Fix the cause and run step 7 again (it takes a new backup) |
| 4 | `FAILED: ...` then `ROLLED BACK - nothing written (backup kept: <file>)` | Unchanged (rolled back) | Note the error. Run steps 5 and 6 again; do not run step 7 until you understand the error |
| 75 | A lock is busy (`REFUSE: P4 ...` names the holder) or the nightly window | Unchanged | Wait for the holder to finish, or for the window to end, and run step 7 again |

If the `RESULT: CUT OVER` line never appears and you cannot tell whether the commit happened (for example, the
terminal closed), do not run anything yet. Run step 6 again: `ALREADY CUT OVER` (exit 0) means it committed;
`P10 not cut over yet` means it did not. If you see a `WARNING: the cutover COMMITTED, but the provenance file
could not be finalized`, the file is at the `.pending` path it prints: rename it by hand to drop `.pending`.

### Step 8. Verify after the cutover, then sign in by hand

```bash
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage reset --database alphadeck --prod --provenance /data/cutover_runs/<ts>-alphadeck.json --health-url http://localhost:8000/health
```

Use the `provenance:` path that step 7 printed. Add `--json /data/cutover_runs/prod-verify-reset.json` to keep the report.

*What it does.* `verify` is read-only: its session cannot write, and it writes nothing but its report (and the
`--json` file). It reads the database name from its own connection and compares it with `--database`
(`alphadeck` needs `--prod`; there is no `--confirm`). It refuses a provenance file that is missing, is a
`.pending` file (an unfinished run), cannot be read, or names another database. The stages are cumulative, and
each check prints one line. For `--stage reset` the checks are: V1 no surprise beyond the acknowledged orphan
purity row; V2 every migration applied and the 0206 constraints validated; V3 no thesis or user on the data
tenant, an enabled `super_admin`, and users unchanged; V4 every thesis unchanged against the baseline; **V5
recall**: every basket member of every thesis is still there (a failure names the thesis, the ticker and the
security id), the nine per-thesis child tables equal the baseline, and `operator_decision` is unchanged; V6 no
share-count or cash/burn row outside the data tenant, and no company lost; V7 purity; **V8 V1's history kept**:
each of the eight history tables holds exactly the baseline's rows (from the `nightly` stage on, the V2 cron and
the editions job append, so V8 then checks that the `calls` recorded at or before the cutover equal the baseline's,
that no other history table has fewer rows, and that every baseline edition is still there); V9 exactly one
`v2_cutover` journal row that matches the provenance; V10 `--health-url` answers 200 (a `note:` when you leave it
out).

*You should see.* Every check line starts with `ok:`, `note:` lines never fail, and the last line is
`RESULT: VERIFIED. Exit 0.` The recall line reads `ok: V5 recall: N theses, M basket rows, 0 dropped`. With
`--health-url` there is also `ok: V10 health: http://localhost:8000/health answered 200`. Rehearsal 2 on the fork
printed these (the numbers are the fork's, from a same-day nightly dump):

```
note: provenance 20261120T050517Z-alphadeck_v2.json, cut over on 2026-11-20
ok: V1 surprises: no surprise beyond the acknowledged orphan purity; every table is in a known scope
note: V1 rule 8 acknowledged (--orphan-purity leave): 1 unratified data-tenant purity row(s)
ok: V2 schema: every migration applied; both 0206 constraints validated
ok: V3 tenants: no thesis or user on the data tenant; 1 enabled super_admin
ok: V3 users: 2 users, unchanged
ok: V4 theses: 69 theses: ids, tenants and dates unchanged
ok: V5 recall: 69 theses, 2599 basket rows, 0 dropped
ok: V5 children: per-thesis counts of 9 child tables equal the baseline
ok: V5 decisions: operator_decision 0 (the baseline's)
ok: V6 platform facts: share counts and cash/burn only in the data tenant; 1224 companies with a share count and 59 with cash/burn, none lost
ok: V7 purity: no ratified purity on the data tenant; founding purity 21
ok: V8 history: V1's history kept: calls 2040, call_annotation 176, edition 1, edition_event 2, edition_entry 855, edition_call 682, edition_outcome 113, edition_cause 0 (the baseline's)
ok: V9 journal: one 'v2_cutover' journal row (151f0d78-4b4c-4040-b01d-7a0ba802d192), its counts equal the provenance's
note: V10 health not checked (no --health-url)
RESULT: VERIFIED. Exit 0.
```

The rehearsal ran without `--health-url`, so it printed `note: V10 health not checked (no --health-url)`: the
V10 check against a real URL is **not yet rehearsed**.

*Exit codes.* 0 is verified. 2 is `RESULT: CANNOT VERIFY - nothing checked. Exit 2.`: it could not check (a wrong
`--database`, `--prod` missing or misplaced, a provenance file that is missing, `.pending`, unreadable or from
another database; read the `REFUSE:` line). A table the registry does not name also ends in exit 2. 5 is
`RESULT: <n> check(s) failed. Exit 5.`: at least one `FAIL:` line.

*Then, by hand.* No script signs in for you. Open the app. Sign in as the user and as the steward. Check:
- the Board shows the live cards with V1's last call on each (until the first V2 night);
- the Scoreboard lists Edition 0 and Edition 1 and defaults to Edition 1; Edition 1's page and episode drawer open,
  and the diff from Edition 0 to Edition 1 works;
- Edition 0 includes V1's nights. Its numbers differ from what production showed before, because V1's annotated
  rows are scored too (K5);
- `GET /record/start` gives V1's first honest night for the founding workspace, and `null` for a workspace with no
  calls on record;
- the steward queue is populated;
- the Admin page shows `data_tenant_theses` 0 and `platform_rows_in_workspaces` 0.

**Not yet rehearsed** (R10): nobody signed in during rehearsal 1 or 2. These by-hand checks come from the plan.

*Duration.* `verify` took 3 seconds (measured on the fork, 2026-11-20); then your own time (estimate).

*If it fails.* Exit 5 (any `FAIL:` line) or exit 2: stop. Do not start the cron. Read the line. If you cannot explain it and fix it
without writing to the database, roll back (Part 5).

### Step 9. The extraction backfill

```bash
docker exec alphadeck-backend-1 python -m pipeline.extract_values
docker exec alphadeck-backend-1 python -m pipeline.extract_values --apply
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage backfill --database alphadeck --prod --provenance /data/cutover_runs/<ts>-alphadeck.json
```

*What it does.* The first command is the dry run: the cost plan, with no SEC request and no write. The second is
the live backfill (N4 option (a)). For option (b), use `--apply --no-live` instead and expect most companies to
fail with "not cached". It extracts every due company's share count and cash/burn, one attempt row each, and
unlocks decision B4 (the nightly leg takes never-extracted companies only after a backfill extracted at least
one). Run it **before** the cron starts. `--max-names N`, `--budget-s S` and `--ticker T` bound a run. It is
resumable: a finished company is not due again, so re-run it after any stop.

*Refuses (exit 75, nothing more written)* inside the nightly window (21:30-23:30 ET), while another backfill
holds lock 7, or while a pass holds the call-of-record lock. It also stops cleanly, mid-run, when the window
begins. Exit 1: a live run without `ALPHADECK_USER_AGENT` (`REFUSED: set ALPHADECK_USER_AGENT ...`). Exit 2: an
unknown `--ticker`.

*You should see.* A summary (applied, flagged, nothing found, failed by reason) and a line that starts
`B4:` saying whether the nightly is unlocked. A run that extracted nothing says
`B4: nothing was extracted (...)`: the nightly stays held and you should find out why before step 10. Then
`verify --stage backfill`: it adds B1 (every non-ETF company on a live card has at least one `backfill` attempt),
B2 (a `done` backfill attempt, which proves B4) and B3 (the share of those companies with a share count, and with
cash/burn, is at or above the baseline's). Rehearsal 2 on the fork printed this summary (the dry run was not run
there; it went straight to `--apply`):

```
extract-values: Shared facts · Nov 20: 1768 extracted: 1513 applied, 211 to the steward, 42 nothing found, 2 unchanged · 2215 SEC requests · 25m 23s
extract-values: attempted 1,768 of 1,768 due · applied 1,513 · candidates 253 (211 flagged to the steward, 42 nothing found) · unchanged 2 · skipped 0 · busy 0 · failed 0 · deferred 0 · 2,215 live SEC fetches
```

and this verify output, `RESULT: VERIFIED. Exit 0.` (the lines of stage `reset` print first, as in step 8; only
V8 and the new lines are shown):

```
ok: V8 history: V1's history kept: calls 2040, call_annotation 176, edition 1, edition_event 2, edition_entry 855, edition_call 682, edition_outcome 113, edition_cause 0 (the baseline's)
ok: B1 attempts: every one of 1768 companies on a live card was attempted
note: B attempts by status: done 1768
ok: B2 B4: B4 unlocked: a backfill attempt extracted a company
ok: B3 coverage: share count 1396/1768 (79.0%; baseline 63.3%); cash/burn 1269/1768 (71.8%; baseline 3.2%)
```

A `FAIL:` line is exit 5.

*Duration.* Measured on the fork, 2026-11-20: 1,526 seconds (25 minutes 23 seconds) for the live run, 2,215 SEC
requests, at `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5`; `verify` took 4 seconds. The compose default for that
setting is 8.5 (`docker-compose.yml`); production's own value is in its `.env`, and a different rate
changes the time. The rehearsal run also passed `--budget-s 14400 --max-names 2500`, which bound a run and are not
needed here. A fork measurement, not a promise.

*If it fails.* Failed companies are listed and are not fatal (exit 0). For exit 75 or a stop, wait and run the
same command again. For exit 1 with no User-Agent, set it in `.env` and recreate the backend (see `docs/DEPLOY.md` on environment changes), then run
again.

### Step 10. Start the V2 cron

```bash
bash scripts/deploy.sh --label post-v2-reset cron
```

*What it does.* Takes another labeled backup, builds the cron image and starts it. From here the V2 cron writes
calls, onboarding and extraction rows, and the 22:30 ET pass will run tonight. Do not run this inside 22:20 to
22:50 ET. Keep `ALPHADECK_EDITIONS_NIGHTLY` at 0 in `.env` for this deploy (Part 3, O1): the build recreates the
cron from `.env`.

*You should see.* A final line that begins `OK: deployed <sha> to prod: cron (rebuild)` and ends with
`every verification passed`.

**Then read the cron log, and do not start step 11 before you have.** `deploy.sh` returns as soon as it sees
`daily-cron: each pass bounded at`, and that line is printed before the boot catch-up runs. So the boot steps may
still be running when it says `OK: deployed`. Run `docker logs --tail 80 alphadeck-cron-1` until you see these
lines, in this order (lines from the background onboarding run can sit between them):

1. `daily-cron: scheduled for 22:30 ...` and `daily-cron: each pass bounded at ...`.
2. `daily-cron: booted <date>`, then `catch-up for the last expected night <D-1>`. On a weekday morning that
   night is the last session before today (on a Monday, Friday).
3. `daily-cron: a live pass for <D-1> started at/after 22:30 America/New_York already ran`, ending
   `catch-up is a no-op`. This is the line that matters. The boot catch-up found V1's finished pass for that night
   in `/data/cron_runs` (the volume the V1 and V2 containers share) and wrote nothing: no lock, no row, no push.
4. The boot backup line. On a normal weekday morning it starts `backup: skipped`, because the newest unlabeled
   dump (V1's nightly one, about 11 hours old at 10:00 ET) is younger than the 20-hour gate. Labeled dumps such as
   `post-v2-reset` do not count toward the gate. After a weekend or after about 18:45 ET it dumps instead (about
   6 minutes).
5. `onboard-cron: every 1800s ...`, `master-refresh: weekly on day ...` and `term-mentions: weekly on day ...`.
6. `daily-cron: next run <date and time> ... asof <D>`, where D is today. That line ends the boot block.

The editions line (`daily-cron: EDITIONS_NIGHTLY=0`) is not a boot line. It appears only after a night's backup.

**Stop if the catch-up ran a real pass instead of skipping itself.** You will see pass output after the `booted`
line, and no `catch-up is a no-op` line. (A failed one prints `daily-cron: boot catch-up <D-1> FAILED`.) That
means V1 had not recorded night D-1 (Part 3). The real pass takes the call-of-record lock, writes a V2 row dated
D-1 for every card with no on-time V1 row, and pushes their changes to Slack labeled "recorded late". N5 then fails
on every later verify, because the calls log is immutable. Let the pass finish, do not start step 11 while it runs
(the cut refuses with exit 75 while the lock is held), and write down that N5 will fail and why.

**Not yet rehearsed.** The fork has no running cron (its service is in a profile that never starts), so this step is
only confirmed on the first production run. The lines above come from a read-only review of the code (the review
named in O1), which traced them; no run has shown them yet.

*Duration.* Backup about 6 minutes (measured); build about 5 minutes (estimate).

*If it fails.* Same exit codes as step 4. If it refuses on the `HEAD` check, you are not on the merged `main`.

### Step 11. Cut Edition 2 (decision K4; replaces the old "publish Edition 1" step)

Do this after step 10 and before the first V2 night. The cron container carries the `replay` extra (duckdb and
pyarrow) that the cut needs; the backend image does not. Run the dry run first and read it.

**Timing.**
- Start only after the cron log shows `daily-cron: next run ...` with `asof <D>` for tonight (step 10: the boot
  block is finished).
- Start by about 19:30 ET. The backup (about 6 minutes), the dry run (about 19) and the apply (about 18) take about
  45 minutes, and the cut must end well before 21:30 ET and long before 22:30.
- Never during a cron deploy or recreate (`deploy.sh ... cron`, including `--env-only cron`). A redeploy kills a cut
  running in the cron container. The store is one transaction, so nothing partial lands, but the run is lost.
- The hand cut and the editions nightly share one lock (the editions lock). Whichever comes second exits 75 and
  writes nothing.
- The cut also refuses (exit 75) while a pass, a catch-up or a backfill holds the call-of-record lock, at its start
  and again just before it writes. It never makes the 22:30 pass wait. If a pass starts mid-cut, the cut runs to the
  end and is then refused at the write (about 19 minutes lost).

**First, check the onboarding queue.** A company on a live card that is still loading its history makes the cut
`deferred` (exit 0, nothing written). The onboarding job runs at the cron's start and every 30 minutes.

```bash
docker exec alphadeck-backend-1 python -m pipeline.onboarding --status
```

No company on a live card may be `queued`, `in_progress` or `failed`. If one is, wait for the next batch and check
again. A `failed` row (after its maximum attempts) blocks every cut until you re-queue it:
`docker exec alphadeck-backend-1 python -m pipeline.onboarding --retry-failed`, then wait for the next batch.

Then the backup, the dry run and the apply (read the dry run before you run the apply):

```bash
docker exec alphadeck-backend-1 python -m pipeline.backup --label pre-edition-2
docker exec alphadeck-cron-1 python -m editions.run cut --workspace 00000000-0000-0000-0000-000000000002 --workers 4
docker exec alphadeck-cron-1 python -m editions.run cut --workspace 00000000-0000-0000-0000-000000000002 --workers 4 --apply
```

*What it does.* A labeled backup first. Then the cut: it checks whether the rules changed or a data repair ran since
Edition 1's cut. If so, it tries a new edition in two parts. P re-runs Edition 1's stored pages with today's engine
at Edition 1's own cut, to see what the rules changed. N re-runs every thesis from each company's join date through
the newest night on record (before the first V2 night, that is V1's last night), to see what the new edition holds.
With `--apply` it stores Edition 2 (parent Edition 1) and closes Edition 1, which stays readable. The commands
name the founding workspace with `--workspace` (`FOUNDING_WORKSPACE_ID` in `backend/domain/scopes.py`). Without it
the cut checks every workspace, and a workspace with no edition prints
`Editions · nothing published yet, nothing to try` and writes nothing. When the editions nightly job is on, it runs
the same cut on its own. An edition is append-only: one new edition per market day, never a second at the same cut.
That is why the dry run comes first.

*Refuses (exit 75, nothing written)* while the call-of-record lock is held (a pass, a backfill or a repair) or
another editions job runs: `editions cut: REFUSED` and a reason. Run it outside 21:30-23:30 ET.

*You should see.* The backup line `backup: wrote alphadeck-<ts>-pre-edition-2.sql (<n> bytes)`. Then, from the dry run:

- a first line `Edition 2 would be cut (dry run) · <label> · <n> company-nights changed`;
- an `events:` line: `rules yes` with the reason (on the fork: the stored fingerprint was made by fingerprint
  version 1, and the engine's is version 2), the number of repairs journaled since Edition 1's cut, and the
  declared rules (on the fork, all 16 ids: Edition 1 carries none, so every declared rule goes in the label);
- a `rules trial (P at Edition 1's cut ...)` line and up to ten `differs:` lines (only the first ten print);
- a `new edition run (N at ...)` line with page counts and timings;
- a last line that begins `DRY RUN` and says nothing was written.

**Check the first line before anything else.** It must start `Edition 2 would be cut (dry run)`: the status is
`cut`. If it reads `Edition 1 · a new edition is due but waits: history still loading for ...`, the status is
`deferred`. Nothing was written and the exit code is 0. Do not apply. Go back to the onboarding check, wait, and run
the dry run again.

Read the label before you apply. It is permanent. **If it reads `unexplained change`, do not apply:** the engine's
output moved with no declared rule, and the cut would also page the steward. Stop and find out why.

The apply prints the same lines, then a notice line that begins `EDITION Scoreboard Edition 2` (the quiet notice;
the plan says one quiet Slack line goes out through the environment notifier), then
`PUBLISHED Edition 2 (<id>) · causes ['rule_change', 'data_repair']`. If the apply prints no `PUBLISHED` line,
nothing was cut (it deferred, was refused, or failed): read its line and treat the cut as not done (step 11b says
what that changes).

*The label comes in two shapes.*

| Case | Label | Why |
|---|---|---|
| No pending repair deleted rows | `rule change: <declared rules> · data repair: <summaries> · includes data that arrived since Edition 1 · <n> company-nights changed` | the engine can tell what the rules changed from what the repairs changed |
| A pending repair deleted rows (the rehearsal's case) | `data repair: <up to three summaries> (+N more) · rule change: <declared rules> · includes data that arrived since Edition 1 · <n> company-nights changed` | a repair that deletes rows shows up in the rules trial too, so the engine cannot separate the rule effects from the repair effects. Every changed arm's cause is `not_separated`, and no page is sent |

On the fork the label took the second shape. The fork's journal held 36 repairs since Edition 1's cut, and one of
them deleted rows: `repair_catalyst_dates`, applied 2026-11-11. The fork's journal is production's (from the dump)
plus the cutover's own row, so expect the same shape on production, with the repair count a little higher. The
cutover's own journal row is not
the cause: with `--orphan-purity leave` it deletes nothing (K7). Edition 2's label still holds `rule change:`,
which is what `verify --stage edition` looks for.

*Check afterward.* Open the Scoreboard: Edition 2 exists and is the default, its label shows, the diff from Edition
1 to Edition 2 opens, and Edition 1 still reads and is closed. Run `verify --stage edition` in step 13, not now: it
includes the night checks, so it cannot pass before the first V2 night (N1 says "not yet"). Its E1 check is the proof
that Edition 2 exists after the kept Edition 1, that its parent is Edition 1, that its label names a rule change,
and that Edition 1 is closed.

*Duration.* Measured on the fork, 2026-11-20, at 4 workers: the dry run 1,132 seconds, the apply 1,103 seconds. The
backup before them (1,719,725,430 bytes) was not timed on its own; the log's clock puts the backup and the dry run
together at 23 minutes 53 seconds. Allow about 40 minutes for the dry run and the apply, plus the backup.

*If it fails.* The cut writes nothing on a refusal.

| What you see | What it means | What to do |
|---|---|---|
| `editions cut: REFUSED` with a lock reason, exit 75 | a pass, a backfill, a repair or another editions job (the editions nightly counts) holds a lock | wait, then run it again. If a cron deploy killed a running cut, nothing was stored: run it again |
| `Edition 1 · no trial tonight: ...` | one new edition per market day (not expected here) | read the reason |
| `Edition 1 · a new edition is due but waits: ...` (exit 0, nothing written) | a company on a live card is still loading its history, or its onboarding row is `failed` | run `pipeline.onboarding --status`, wait for the batch, re-queue a failed row with `--retry-failed`, then run the dry run again |
| `Edition 1 · the edition trial failed (...)` | the trial raised an error; the command still exits 0 | read the reason; do not apply until you understand it |
| `Edition 1 · no rule change and no data repair since its cut ...` | no event: nothing to cut (not expected after a V2 deploy) | stop and find out why |

**Not yet rehearsed:** the production command in `alphadeck-cron-1` (rehearsal 2 ran the cut in the fork's backend
container), and the cut before the first V2 night (rehearsal 2 cut Edition 2 after its first night; Part 7).

### Step 11b. Turn the editions nightly on (only if you chose "on" in O1)

Skip this step if you chose to leave the job off. Then Edition 2 stays frozen at the cutover night (Part 9).

Do it only if step 11's apply printed `PUBLISHED Edition 2`. **If the cut did not happen, leave the job at 0 for night
1.** Otherwise the first night's job would run the cut itself, with no dry run and no label review. If that cut
deferred or failed, that night's V2 pages, and up to 30 catch-up pages per card, would go onto Edition 1. Cut by hand
on a later day, then do this step.

1. Set `ALPHADECK_EDITIONS_NIGHTLY=1` in production's `.env` (open it yourself; deleting the line also works, because
   the compose default is 1).
2. Before 22:20 ET, and with no cut running:

   ```bash
   bash scripts/deploy.sh --env-only cron --dry-run
   bash scripts/deploy.sh --env-only cron
   ```

3. Confirm the container has it:

   ```bash
   docker exec alphadeck-cron-1 printenv EDITIONS_NIGHTLY
   ```

   It should print `1`.

*What it does.* `--env-only cron` recreates the cron with no build (`up -d --no-deps --no-build cron`: the same image,
and no backup unless you pass `--label`). `deploy.sh` refuses inside 22:20-22:50 ET and while the call-of-record lock
is held. The recreate runs the boot block again: a catch-up for night D-1 that is a no-op, an age-gated backup, and
an immediate onboarding run. Read the log for the same lines as in step 10.

*If it fails.* Same exit codes as step 4. If the value does not read `1`, the container was not recreated with the
new `.env`: read `deploy.sh`'s output. The job stays off until it reads `1`.

*Not yet rehearsed.*

### Step 12. The first V2 night (automatic)

The cron runs the pass at 22:30 ET on its own. Do nothing, and do not run the pass by hand. Do not start a cut or a
cron deploy after 22:20 ET.

*In the cron log, after the backup.* The order is the pass, the backup, then the editions job. If the job is off, the
log says `daily-cron: EDITIONS_NIGHTLY=0`. If it is on, you see its own output instead, or a line that says
`daily-cron: editions nightly REFUSED for <D>` (a lock was held; nothing was written, and the next night catches up).

**The first V2 night is not silent.** V1's calls are kept, so the pass compares each live card's call with V1's
last call for that card (decision K3; the old decision N5, a silent night, is superseded). The push rule
(`backend/notify/policy.py`):
- a card into Armed from another state: the loud `ARMED` push;
- a card Armed to Armed with more names or a higher grade: a loud upgrade;
- a card Armed to Armed that is weaker, mixed or level: a quiet push;
- a card out of Armed: recorded, never pushed;
- one quiet digest lists the newly armed names in cards that were already armed (a name armed under V1 before is
  marked `(re-arm)`);
- a card with no V1 call (made after V1's last night) is silent.

*What rehearsal 2 measured.* Of 67 live cards, 1 changed state (the Golden Dome card, armed to warming), 0 entered
Armed, and the log shows one `DIGEST` line with 4 names on 4 cards (2 marked `(re-arm)`). That is what the new
rules alone did to V1's own call for the same night. A typical V1 night since 2026-09-15 (48 nights) had a median of
1 state change (maximum 15) and a median of 0.5 cards entering Armed (maximum 9). So expect the rehearsal's one
record-only change (armed to warming) and no card entering Armed from the new rules alone, plus a normal day's
movement on top. A busy day can still send loud pushes. **Recommended (K3): accept the pushes, with no mute. The operator is to
confirm.** If you choose the mute instead, clear the Slack webhooks before 22:30 ET (each founding-workspace user's
Settings, and the cron's environment `SLACK_WEBHOOK_URL`), and restore both the next morning. With the environment
webhook unset, that night's ops health pages and the edition notice go to the log only.

Open the Board the next morning and read it yourself.

*Duration.* The pass takes about 7 minutes (a 2026-10-21 measurement of 396 seconds), plus the extraction leg
(up to 900 seconds). Rehearsal 2's by-hand pass took 2,469 seconds on the fork at 1.5 SEC requests a second:
a fork figure, not production's.

**Not yet rehearsed:** a go-live-shaped night, that is a new night after the cutover, run by the cron. Rehearsal 2
ran its first night by hand, for the same night V1 had already recorded (Part 7).

### Step 13. The next morning: verify the night and the edition

```bash
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage nightly --database alphadeck --prod --provenance /data/cutover_runs/<ts>-alphadeck.json
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage edition --database alphadeck --prod --provenance /data/cutover_runs/<ts>-alphadeck.json
```

*What it does.* The stages are cumulative, so `--stage nightly` runs every check of the earlier stages, and
`--stage edition` runs all of them, including the night checks. The night checks read V2's rows only: the `calls`
rows recorded after the cutover's journal row. V1's kept rows are history, checked by V8. N1 the night has V2 call
rows (the newest `calls.asof` recorded after the cutover, or the night you give with `--night YYYY-MM-DD`; it says
"not yet" until the first V2 night has written rows, so V1's last night never passes for it), N2 a row for
every live thesis, N3 no reconstructed V2 row, N4 the run kind is `cron`, N5 no V2 row dated before the cutover's
date (market time), N6 none under the data tenant. Then E1: for each workspace whose baseline held an edition
(newest N), Edition N+1 exists, its parent is the kept Edition N, its label contains `rule change:`, and Edition N
is closed ("not yet" before the cut); E2 an `edition_entry` row for every live thesis in the newest edition; E3 no
edition made since the cutover for a workspace with no live card. From the `nightly` stage on, a difference in what
users may edit (users, archived cards, new cards, child counts, purity) is a `note:`, not a `FAIL:`; recall and the
platform-fact checks still fail.

*You should see.* `ok:` on every check and `RESULT: VERIFIED. Exit 0.` For the edition stage the new lines read
(rehearsal 2, the fork's numbers):

```
ok: E1 edition: Edition 2 after the kept Edition 1 in workspace 00000000-0000-0000-0000-000000000002
ok: E2 entries: entry rows for each of 67 live theses
ok: E3 no card: no edition made since the cutover for a workspace with no live card
note: V8 history appended since the cutover: calls 2040 -> 2084, edition 1 -> 2, edition_event 2 -> 4, edition_entry 855 -> 4355, edition_call 682 -> 2550, edition_outcome 113 -> 501, edition_cause 0 -> 767
```

**Two checks failed in rehearsal 2, and both were timing artifacts, not faults.** Rehearsal 2 ran the cutover
just after midnight (00:05 ET on 2026-11-20) and then ran the first V2 night for night 2026-11-19, the same night V1
had already recorded:
- `FAIL: N2 one per thesis: 23 live theses of 67 with no row for night 2026-11-19`. The 23 cards had no new row
  because their V2 call was identical to V1's for the same night, and the nightly writes only on a change. On a new
  night every card gets a row, so N2 is not expected to fail on go-live night (expected, not yet seen).
- `FAIL: N5 after the cutover: 44 call rows recorded after the cutover but dated before it (2026-11-20)`. The night
  is dated before the cutover's date because the cutover ran after midnight. A weekday-morning cutover cannot hit
  this (expected, not yet seen).

If either fails on go-live night, treat it as real and read the cron log and the Admin page.

**N5 is global, so one bad row fails it for good.** It counts every `calls` row recorded after the cutover and
dated before the cutover's date, from any night, not only the night you verify. The calls log is immutable, so such a
row cannot be removed, and N5 then fails on every later `verify --stage nightly` and `--stage edition`. The number in
the failure line is the count of those rows. On a weekday-morning cutover, the usual cause is a V2 pass that wrote
rows for night D-1: V2's boot catch-up in step 10 (see its stop condition). Unlike rehearsal 2's N5, it is not a
timing artifact. Tell the operator, and write down the count.

**If the editions nightly is on** (step 11b), also read the Admin page's `Editions ·` line. It should read
`Edition 2 · page for <Mon D> added · <n> theses ... self-check passed`, with `<Mon D>` the night you verify. A cut
part in it should read `no trial tonight: ...` (refused) or be absent (no event). A line saying a new edition was
cut that night is not expected, because step 11 made Edition 2: read it before you go on.

**Expect** `note: N ingest: ... have ingest_fresh=false` for companies still onboarding. It is a note, not a `FAIL:`.

Also open the Board and the Admin page (cron status and alarms, the steward queue).

*If it fails.* A missing row for a live thesis: read the cron log and the Admin page. E1 saying "not yet" means
step 11 was not done: do it now (outside 21:30-23:30 ET).

## 5. Rollback

Two procedures. Both restore a plain-SQL dump into an empty database, and both lose everything written after
that dump.

**A. Back to V1 (code and database).** Use this at any step. It is lossless before the V2 cron starts (step 10);
after that it loses every V2 night, the Edition 2 cut, steward decisions, flags, extraction results and card edits
since the dump.

Do the commands in this order. **Start V1's images before any V2 image touches the restored database**, or the
V2 boot applies its migrations to V1 data again.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop backend frontend cron
docker exec alphadeck-postgres-1 psql -U alphadeck -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS alphadeck WITH (FORCE)" -c "CREATE DATABASE alphadeck"
docker exec -i alphadeck-postgres-1 psql -U alphadeck -d alphadeck -v ON_ERROR_STOP=1 -f - < ./data/backups/alphadeck-<ts>-pre-v2-cutover.sql
docker tag <backend image>:v1-pre-v2 <backend image>:<current tag>
docker tag <cron image>:v1-pre-v2 <cron image>:<current tag>
docker tag <frontend image>:v1-pre-v2 <frontend image>:<current tag>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build backend frontend cron
```

`alphadeck-<ts>-pre-v2-cutover.sql` is the dump `deploy.sh` took in step 4 (V1 schema, V1 data). `docs/DEPLOY.md`
says never to `DROP DATABASE alphadeck`. The restore needs it, because the dump replays only into an empty
database (`docs/FEED_LOOP.md`, "RESTORE is CLI-only"). This is the only place this runbook drops anything. Run
it only to roll back.

Afterward, open a pull request into `main` with `git revert -m 1 <the merge commit from step 3>`, so `main`
matches what runs before the next `deploy.sh`. Check `/admin/status` and one thesis by hand.

*How long.* The plan's estimate was 15 to 30 minutes. In rehearsal 2, R8 restored the 1.6 GB
`-pre-v2-reset` dump (1,712,905,676 bytes) into an empty database on the fork's Postgres in 105 seconds, with no
error lines (measured on the fork, 2026-11-20; a fork measurement, not a promise). R1 (drop, restore and migrate
the fork from a nightly dump) took 138 seconds. The rollback drill itself (R13, restoring into the live name and
restarting V1's images) is **not yet rehearsed**.

*What is lost.* Everything written after the step 4 dump. Before step 10 that is nothing a user made. After it,
all V2 activity.

**B. Redo the cutover only, staying on V2 (rare).** Restore `alphadeck-<ts>-pre-v2-reset.sql` (V2's schema, V1's
data, before the move) the same way, with the V2 images, then run steps 5 to 7 again. That dump holds everything
the step 7 backup held. The V1 history archive (`alphadeck-<ts>-v1-history.sql`) holds only the eight history
tables, as they stood at step 7: a frozen snapshot, for reading V1's record, not for restoring a database.

## 6. After the cutover

- **The first night.** It is not silent (step 12, decision K3). Open the Board the next morning. If you muted the
  webhooks for the night, restore them now.
- **Where Slack messages go.** Call pushes (a card's state changes), the digest and the SPAC-radar pages go to each
  enabled workspace user's own webhook (Settings). If no user in the workspace has one, they go to the environment
  `SLACK_WEBHOOK_URL`, but only when the workspace has an enabled super admin. Otherwise they are only logged. The
  rest goes to the environment `SLACK_WEBHOOK_URL`: the pass's health pages and crash pages, the cron shell's own
  failure pages, the quiet Edition cut notice and the cut's drift page, and the editions self-check page. Never put
  a webhook in a doc or a commit.
- **Editions.** Edition 1 is closed and stays readable. Edition 2 is the default. While the editions nightly job is
  off (O1), nothing adds a page to Edition 2 after the cut and nothing cuts a later edition by itself: you run
  `editions.run cut` by hand (dry run first) after any declared rule change or data repair. Where the job runs, it
  adds each night's page to Edition 2 and runs the automatic cut. If you leave it off, you can add the last night's
  page by hand with `docker exec alphadeck-cron-1 python -m editions.run nightly` (dry run) and then the same with
  `--apply`. It runs only after a finished nightly pass, and it includes the automatic cut check, so read the dry run
  first. A workspace with no edition (a new one) prints
  `Editions · no open edition to add pages to` from `editions.run nightly` and
  `Editions · nothing published yet, nothing to try` from the cut. That is normal.
- **Caps (N7).** Existing workspaces have no caps (NULL). A new workspace gets the default caps from settings.
  To print a workspace's caps and counts, or to set them:
  ```bash
  docker exec alphadeck-backend-1 python -m pipeline.users caps --workspace 00000000-0000-0000-0000-000000000002
  docker exec alphadeck-backend-1 python -m pipeline.users caps --workspace 00000000-0000-0000-0000-000000000002 --live-cards 25 --companies 1000 --terms 50
  docker exec alphadeck-backend-1 python -m pipeline.users caps --workspace 00000000-0000-0000-0000-000000000002 --clear
  ```
  The numbers in the second line are placeholders, not recommendations.
- **Companies that switched between quarterly and annual filings.** For a company whose newest periodic filing
  is of a different kind than its older ones (`docs/WORKBENCH_EXTRACTION.md` names EUDA, CURR, SCNI, CHR and BTCT
  as the audit's examples), the backfill can leave an older value on file and mark the attempt done, so the
  nightly does not retake it until a new periodic filing. In the Admin page, open each such company's page under
  platform values and press **Extract now** (`docs/ADMIN.md`, `docs/PLATFORM_VALUES.md`). Not yet rehearsed:
  which companies on production this affects, and whether the backfill reports them (the rehearsal logs do not
  say).
- **The annotation table (superseded N2).** `call_annotation` keeps its V1 rows, and nothing reads them. It can be
  dropped later by a migration (0214 is the next free number; 0212 and 0213 are W6's), with its entries in the
  scope map, in `CUTOVER_ACTION` and in `tests/app/test_v1_annotation_gone.py`. There is no date. Do it after
  go-live when you are sure nothing reads it.
- **The V1 history archive and the V1 rollback dump.** `alphadeck-<ts>-v1-history.sql`,
  `alphadeck-<ts>-pre-v2-reset.sql`, `alphadeck-<ts>-pre-v2-cutover.sql` and `alphadeck-<ts>-pre-edition-2.sql` are
  labeled dumps. They are removed only when both older than 90 days and outside the newest 20 labeled dumps
  (`docs/FEED_LOOP.md`). To remove them sooner, delete the single files by hand. The plan sets no date. A
  reasonable rule, which is yours to change: keep the rollback dumps and the V1 image tags until Edition 2 is cut
  and a week of V2 nights has passed, and keep the archive as long as you want a frozen copy of V1's record. Then
  remove the `v1-pre-v2` image tags with `docker rmi`.
- **Fork refresh.** After go-live, production dumps are V2 dumps. The plan lists a follow-up (W10f) that drops
  the default platform-facts move from `scripts/refresh-fork.sh` and removes `--v1-state`.
- **The rehearsal fork.** Destroy it when you no longer need it (`scripts/fork.sh destroy --yes`, `docs/FORKS.md`).
- **`CLAUDE.md`.** The file is protected, so check its Commands lines yourself (decision N14).

## 7. The rehearsal procedure (on the fork)

Rehearsals run on the fork (`docs/FORKS.md`), never on production. Run them by hand with the operator present.
Rehearsal 1 ran the old reset design and is superseded. Rehearsal 2 ran the keep-history cutover on `v2` commit
`e54c0d0b`. A code change after rehearsal 2 means rehearsal 3. A go-live-shaped rehearsal (below) has not been run.

Setup, from the fork worktree root:

```bash
# F is the fork's compose command. The fork database is alphadeck_v2 in the plan.
F="-f docker-compose.yml -f docker-compose.fork.yml -p alphadeck_v2 --env-file .env.fork"
B="docker compose $F exec backend"
```

Never run a bare `docker compose up` while you mean the fork: it rebuilds production.

**Before each rehearsal**
- [ ] The fork worktree is on the `v2` commit under test, and its image is rebuilt (`bash scripts/fork.sh up`).
- [ ] You set the fork's SEC User-Agent on purpose, and you lowered `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC` on the
      fork for the live steps. Run every live step outside production's 21:30-23:30 ET window: the two stacks
      share one SEC limit.
- [ ] Keep each console log and JSON under the fork's `data/cutover_runs/`, and write the results into Part 7's
      tables. Write each step's full output to a log file, with its exit code and its wall time.
- [ ] If the fork's own SEC cache holds a live backfill from an earlier run, pass `--no-cache` to `refresh-fork.sh`
      in R1: it skips the copy of production's cache, so the fork keeps its own. Without it, the default copies
      production's cache over the fork's.
- [ ] **Start the cutover in the morning**, as production will, so that the first night (R11) is a new night after
      the cutover. Rehearsal 2 ran just after midnight, and its first night was the same night V1 had already
      recorded (see Results).

**The run (time every step)**
- [ ] **R1** `bash scripts/refresh-fork.sh --reset --fresh --v1-state`. `--fresh` takes a read-only dump of
      production first (rehearsals 1 and 2 used `--from-latest --no-cache` instead, so the dump was the newest
      existing nightly dump); `--v1-state` skips the platform-facts move, so the fork keeps V1-shaped data. Pass:
      the last line ends `(V1 state: run the cutover next)`. The drop, restore and migrate time is the rollback
      restore estimate. Write down the eight history counts and V1's last night (`max(asof)` of `calls`).
- [ ] **R1b Clear the restored Slack webhooks. FORK ONLY: never run this on production.** The fork restores
      production's `user_meta.slack_webhook_url`, and with V1's history kept the first night would push to real
      Slack channels. Run, with the fork's Postgres container:
      `docker exec -i alphadeck_v2-postgres-1 psql -U alphadeck -d alphadeck_v2 -c "UPDATE user_meta SET slack_webhook_url = NULL WHERE slack_webhook_url IS NOT NULL"`
      then check that
      `docker exec -i alphadeck_v2-postgres-1 psql -U alphadeck -d alphadeck_v2 -At -c "select count(*) from user_meta where slack_webhook_url is not null"`
      prints 0. Also confirm the fork's environment has no `SLACK_WEBHOOK_URL` (the plan asks for this). Do it
      before R11. Every fork rehearsal must do this.
- [ ] **R2** `$B python -m pipeline.v2_cutover plan --json /data/cutover_runs/r<N>-plan.json`. Pass: the
      founding workspace's share and cash rows are planned `move→data`; the eight history tables are planned
      `keep (V1 history: operator decision 2026-11-19)`; the only surprise is the stray purity row (if any); exit 1
      (or 0 if none).
- [ ] **R3** Negative checks, each exit 2 with nothing written: a wrong `--database`; a wrong `--confirm`;
      `--prod` on `alphadeck_v2`.
- [ ] **R4** `$B python -m pipeline.v2_cutover run --database alphadeck_v2 --confirm "reset alphadeck_v2" --plan-out /data/cutover_runs/r<N>-run-plan.json`.
      Pass: exit 0, `RESULT: DRY RUN - nothing written. Exit 0.`, no `plan: reset` lines, and the
      `count: P9 V1 history kept` line equals R1's counts.
- [ ] **R5** The same plus `--apply --expect-plan /data/cutover_runs/r<N>-run-plan.json`. Pass: exit 0,
      `RESULT: CUT OVER. Exit 0.`, the printed moved counts equal R4's plan, and the eight history counts, read with
      SQL after the apply, equal R1's.
- [ ] **R6** R5 again. Pass: `ALREADY CUT OVER - nothing to do, no backup, no journal`, exit 0, no new backup file,
      no new journal row.
- [ ] **R7** `$B python -m pipeline.v2_cutover verify --stage reset --database alphadeck_v2 --provenance <file>`.
      Pass: exit 0, every check line `ok:`, `V5 recall: N theses, M basket rows, 0 dropped`, the V8 line
      `V1's history kept: ... (the baseline's)`, and `RESULT: VERIFIED. Exit 0.` The file is the `provenance:` path
      that R5 printed.
- [ ] **R8** The backup restores. On the fork Postgres, create `alphadeck_v2_restorecheck`, restore
      `...-pre-v2-reset.sql` into it, compare the counts of `thesis`, `basket_member`, the eight history tables and
      `fact_shares_outstanding` with the baseline, then drop that check database. Time the restore. Pass: the
      counts equal the baseline.
- [ ] **R9** `$B python -m pipeline.extract_values`, then `--apply` (in the mode you chose in N4), then
      `$B python -m pipeline.v2_cutover verify --stage backfill --database alphadeck_v2 --provenance <file>`.
      Pass: exit 0. Rehearsal 2 set the rate for these commands with `docker exec -e ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5`.
- [ ] **R10** On the fork's app (port 8083 per the plan): sign in as the user and as the steward. Pass: the Board
      shows V1's last calls; the Scoreboard lists Editions 0 and 1 and defaults to 1; Edition 1's page and episode
      drawer open; the diff from Edition 0 to Edition 1 works; Edition 0 includes V1's nights (note its numbers
      against production's current Scoreboard, K5); `GET /record/start` gives V1's first honest night for the
      founding workspace and `null` for a workspace with no calls; the steward queue is populated; Admin shows
      `data_tenant_theses` 0 and `platform_rows_in_workspaces` 0.
- [ ] **R10b The cut (go-live order: before the first night).** `$B python -m pipeline.backup --label pre-edition-2`,
      then `$B python -m editions.run cut --workers 4` (dry run, timed), then the same with `--apply` (timed). Record
      the printed label, P's page count and how many differ, N's page count and the duration. Pass: exit 0;
      Edition 2 is stored with parent Edition 1; its label holds `rule change:` (in the "data repair" first shape
      when a pending repair deleted rows; read the journal to explain it); Edition 1 is closed; no health page was
      sent.
- [ ] **R11** The first night, by hand (the fork's cron never starts): `$B python -m pipeline.daily --run-kind manual`
      (live; set `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5` for it as rehearsal 2 did; run it on the cutover's date,
      after the close, so the night is dated the day of the cutover). Pass: count the
      `TRANSITION` and `DIGEST` lines in its output (the log notifier prints them: that is the number production
      would push), and compare V1's last call with V2's first, card by card (the query is in
      `cutover_rehearsal2b.sh`, step B2, in `docs/temp/v2-rehearsal-2-2026-11-20/` of the main checkout); then
      `$B python -m pipeline.v2_cutover verify --stage nightly --database alphadeck_v2 --provenance <file> --run-kind any`
      exits 0 (`--run-kind any` because a fork's pass is a manual one, not the cron's).
- [ ] **R11b** `$B python -m editions.run nightly --apply --end <night>`. Pass: no cut (no event); one page per live
      thesis on Edition 2; the self-check is ok; no page is added to Edition 1.
- [ ] **R12** `$B python -m pipeline.v2_cutover verify --stage edition --database alphadeck_v2 --provenance <file> --run-kind any`.
      Pass: exit 0, E1 reads `Edition 2 after the kept Edition 1`, and the Scoreboard lists Editions 0, 1 and 2 and
      defaults to 2.
- [ ] **R13** Rollback drill: run R1 again (it is a V1 restore) and run R2. Pass: R2 reads V1 state again.

**Pass criteria (all must hold).** Every step exits as listed. Every `verify` stage exits 0 with no `FAIL:` line.
The apply's counts equal its dry run. The eight history counts after the apply equal the counts before it. The
recall check reports 0 dropped. R6 does nothing. R8's counts equal the baseline. R10b cuts Edition 2 with Edition 1
closed. R11 recorded one row per live thesis. Steps R4 to R9 together fit in 2 hours, which is the production
window between step 4 and step 10 with the 21:30 window hours away.

### Results

#### Rehearsal 2 (the keep-history cutover)

Run on 2026-11-20 on the fork, from 00:01 to 02:34 ET, on `v2` commit `e54c0d0b` (the fork was rebuilt at that
commit in R0), by an agent without the operator. It seeded from production's nightly dump
`alphadeck-20261120T040004.sql` (`refresh-fork.sh --reset --from-latest --no-cache --v1-state`, not `--fresh`; 69
theses, 2040 calls), and kept the fork's own SEC cache. The live backfill ran at
`ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5`. The users' Slack webhooks restored from production were cleared before the
first night (`UPDATE 1`; 0 left). Part A is R0 to R9 (00:01:01 to 00:38:09). Part B is the first V2 night, the
transition count, Edition 2 and two verify stages (01:10:46 to 02:34:22). **Not run:** R10 (sign-in by hand), R11b
(the editions nightly after the cut) and R13 (the rollback drill). The logs are `cutover_rehearsal2a.log`,
`cutover_rehearsal2b.log` and `r2k-*.txt` in `docs/temp/v2-rehearsal-2-2026-11-20/` of the main checkout (not on
this branch). All times are wall times on the fork, a fork measurement, not a promise.

**The order differs from go-live.** The cutover ran just after midnight (00:05 ET on 2026-11-20), so part B's first
night was night 2026-11-19, the same night V1 had already recorded. Every card change that night therefore came
from the new rules alone. And Edition 2 was cut after that night, not before it as step 11 says.

Timings and results:

| Step | Result | Time |
|---|---|---|
| R0 rebuild the fork images | exit 0 | 94 s |
| R1 refresh (drop, restore, migrate) | exit 0; 69 theses, 2040 calls | 138 s |
| R1b clear the Slack webhooks | `UPDATE 1`; webhooks left: 0 | not timed |
| R2 plan | exit 1: `RESULT: REFUSE - 1 surprise(s). Exit 1.` (the stray purity row) | 2 s |
| R3 negative checks | wrong `--database` exit 2; wrong `--confirm` exit 2; `--prod` on the fork exit 2; each `RESULT: REFUSED - nothing read or written. Exit 2.` | 8 s for the three, by the log's clock |
| R4 dry run | exit 0, `RESULT: DRY RUN - nothing written. Exit 0.` | 4 s |
| R5 apply (backup, archive, transaction) | exit 0, `RESULT: CUT OVER. Exit 0.`; moved counts equal R4's plan | 327 s in all; the backup's and the archive's file-name stamps are 05:05:17 and 05:10:34 UTC |
| R6 second run | exit 0, `RESULT: ALREADY CUT OVER - nothing to do, no backup, no journal. Exit 0.`; backup files 7 before, 7 after | 3 s |
| R7 verify reset | exit 0, `RESULT: VERIFIED. Exit 0.`; `V5 recall: 69 theses, 2599 basket rows, 0 dropped` | 3 s |
| R8 restore check | exit 0; 0 restore stderr lines; trailer line count 1; the `-pre-v2-reset` dump 1.6G, the archive 196M | 105 s |
| R9 backfill and verify | backfill exit 0; `verify --stage backfill` exit 0, `RESULT: VERIFIED. Exit 0.` | backfill 1,526 s (25 m 23 s); verify 4 s |
| R4 to R9 total | | 33 m 00 s by the log's clock (R4 started 00:05:09, part A finished 00:38:09) |
| B1 first V2 night (night 2026-11-19), by hand | exit 0 | 2,469 s |
| B2 transitions, V1's last call against V2's first | see below | 1 s by the log's clock |
| B3 `verify --stage nightly --night 2026-11-19 --run-kind any` | exit 5: two failed checks, N2 and N5 (see below) | 3 s |
| B4 backup, then the cut dry run | exit 0 | dry run 1,132 s (backup 1,719,725,430 bytes, not timed alone) |
| B5 the cut apply | exit 0; Edition 2 published | 1,103 s |
| B6 `verify --stage edition --night 2026-11-19 --run-kind any` | exit 5: the same two failed checks, N2 and N5; E1, E2 and E3 passed | 4 s |
| R10 by-hand checks, R11b, R13 | not run | |

History counts, before, after and restored (the fork; `thesis`, `basket_member` and `fact_shares_outstanding` are
there as a check on the move):

| Table | Before the cutover (R1) | After the cutover (R5) | Restored from the dump (R8) |
|---|---|---|---|
| `calls` | 2040 | 2040 | 2040 |
| `call_annotation` | 176 | 176 | 176 |
| `edition` | 1 | 1 | 1 |
| `edition_event` | 2 | 2 | 2 |
| `edition_entry` | 855 | 855 | 855 |
| `edition_call` | 682 | 682 | 682 |
| `edition_outcome` | 113 | 113 | 113 |
| `edition_cause` | 0 | 0 | 0 |
| `thesis` | 69 | 69 | 69 |
| `basket_member` | 2599 | 2599 | 2599 |
| `fact_shares_outstanding` | 2219 | 2219 | 2219 |

After the first night and Edition 2 (the V8 note of `verify --stage edition`): `calls` 2040 to 2084, `edition` 1 to 2,
`edition_event` 2 to 4, `edition_entry` 855 to 4355, `edition_call` 682 to 2550, `edition_outcome` 113 to 501,
`edition_cause` 0 to 767.

The apply (R5), by table:

| Table | Action | Rows |
|---|---|---|
| `fact_shares_outstanding` | moved to the data tenant | 2219 |
| `fact_cash_burn` | moved to the data tenant | 64 |
| `fact_revenue_mix` | moved to the founding workspace | 0 |
| the eight history tables | kept | 3869 in all (2040 calls) |

Also printed by R5: `orphan purity: 1 row(s), leave (0 deleted)`, `validated: 2 constraints`,
`companies stamped changed: 1224`.

- Backup: `/data/backups/alphadeck-20261120T050517-pre-v2-reset.sql`, 1,712,905,676 bytes.
- Archive: `/data/backups/alphadeck-20261120T051034-v1-history.sql`, 204,627,535 bytes.
- Provenance: `/data/cutover_runs/20261120T050517Z-alphadeck_v2.json`.
- Journal: `data_repair` `151f0d78-4b4c-4040-b01d-7a0ba802d192`.

The backfill (R9). 1,768 companies attempted of 1,768 due; 1,513 applied; 253 candidates (211 flagged to the
steward, 42 nothing found); 2 unchanged; 0 failed; 2,215 live SEC fetches; 25 m 23 s. By fact:
`shares_outstanding` already on file 282, applied 1076, flagged 349, nothing found 51, older than on file 10;
`cash_burn` applied 1255, flagged 415, nothing found 98. Run artifact:
`/data/extraction_runs/20261120T051241.456320Z-backfill-8d7d26.json`. Coverage of the companies on a live card,
before and after: share count 63.3% (baseline) to 1396 of 1768 (79.0%); cash/burn 3.2% (baseline) to 1269 of 1768
(71.8%).

The first night and the transitions (B1, B2). The pass ended
`done: 67 theses · 44 appended · 23 unchanged · 1 transitions · 0 errored`. The one transition was the Golden Dome
card: armed to warming (`starter_entry` to `not_yet`). One `DIGEST` line named 4 names on 4 cards (2 marked
`(re-arm)`). The comparison of V1's last call with V2's first, per live card: 67 live cards; 23 with no V2 row;
1 state change (armed to warming); 0 entering Armed; 1 verdict change. For a typical V1 night since 2026-09-15 (48
nights): a median of 1 state change (maximum 15) and a median of 0.5 entering Armed (maximum 9). The pass also
printed a `CRON HEALTH` self-audit failure, `code_sha_known`, because the fork's image was built without `GIT_SHA`
(production's `deploy.sh` builds with it).

The cut (B4, B5). On the fork:
- The events line: the rules changed (the stored fingerprint was made by fingerprint version 1; the engine's is
  version 2); 36 repairs that changed rows, journaled since Edition 1's cut (2026-10-23); 16 declared rules.
- P, the rules trial at Edition 1's cut: 669 re-run pages over 10 theses; 569 differ (4 missing); 0 matched within a
  cent; the bodies are today's, so `counter_case` and `catalyst_surface` were left out of the comparison. The
  first ten `differs:` lines all belong to one thesis and name `triggers_fired` and `armed_members`.
- N, the new edition run: 1,868 pages; against Edition 1, 682 pages compared and 588 differ; 675 company-nights
  changed, 145 companies, 11 theses; 18 episodes appeared, 33 vanished, 4 moved.
- The label began `data repair: appended 37 split-list version(s) checked against SEC; re-scaled 91 stored price
  bar(s) onto the split list's scale (ABTS); re-dated the SEC acceptance time of 6610 insider filing record(s) stored
  on the wrong night (+33 more) · rule change: Context and Bench links never count toward the call; ...` (all 16
  declared rules) and ended `includes data that arrived since Edition 1 · 675 company-nights changed`. This is the
  "not separated" shape (step 11): the journal holds 36 repairs since Edition 1's cut, and one of them,
  `repair_catalyst_dates` (2026-11-11), deleted rows. Every changed arm's cause is `not_separated`; the edition's
  causes read `['rule_change', 'data_repair']`.
- `PUBLISHED Edition 2 (3cabf411-2e85-4f78-8938-d76a77d20353)`. Edition 1 is closed and readable (E1 proves it). No
  page was sent. Production's Edition 1 covers only 10 theses (669 pages) and was paged once (2026-10-24).

Verify results. `reset` exit 0 and `backfill` exit 0 (step 8 and step 9 show their lines). At the `nightly` stage
(B3) and the `edition` stage (B6), 2 checks failed, both timing artifacts of running the first night for the
same night V1 had recorded, and not faults (step 13 explains them):
- `FAIL: N2 one per thesis: 23 live theses of 67 with no row for night 2026-11-19`;
- `FAIL: N5 after the cutover: 44 call rows recorded after the cutover but dated before it (2026-11-20)`.

All other checks passed, including `ok: N1 night: night 2026-11-19: 44 call rows`, N3, N4
(`run kinds manual 44`), N6, and at the edition stage `ok: E1 edition: Edition 2 after the kept Edition 1`,
`ok: E2 entries: entry rows for each of 67 live theses` and `ok: E3 no card`.

#### Rehearsal 1 (the old reset design, superseded)

Run on 2026-11-18 on the fork, on `v2` commit 47fb4514 (the fork was rebuilt at that commit in R0). It ran the
cutover as it was then designed: it deleted V1's history, with an empty calls log afterward. That design was
replaced on 2026-11-19 (decision: keep the history). It seeded from the newest existing production nightly dump,
`alphadeck-20261118T035357.sql` (`--from-latest`, not `--fresh`; 69 theses, 1906 calls), and kept the fork's own SEC
cache (`--no-cache`). The live backfill ran at `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5`. An agent ran it without the
operator present. It covers R0 to R9 only: R10 (signing in), R11 (the first nightly by hand), R12 (Edition 1) and
R13 (the rollback drill) were **not run**. An earlier run of the same steps on commit ca3f8eb8 (before W6) the same
day gave the same move, reset and coverage counts; its per-step timings were not kept. The log is
`v2-cutover-rehearsal-1b-2026-11-18.log` (in `docs/temp/` of the main checkout, not on this branch).

| Step | Pass? | Rehearsal 1 result | Time |
|---|---|---|---|
| R0 rebuild the fork images | yes | | 91 s |
| R1 refresh (drop, restore, migrate) | yes | | 160 s |
| R2 plan | yes | exit 1, `RESULT: REFUSE - 1 surprise(s). Exit 1.` (the stray purity row) | 3 s |
| R3 negative checks | yes | three exit 2, each `RESULT: REFUSED - nothing read or written. Exit 2.` | |
| R4 dry run | yes | exit 0, `RESULT: DRY RUN - nothing written. Exit 0.` | 5 s |
| R5 apply (backup, archive, transaction) | yes | exit 0, `RESULT: CUT OVER. Exit 0.`; counts below | 389 s in all; the stamps are 23:06:06 and 23:12:24 UTC |
| R6 second run | yes | exit 0, `ALREADY CUT OVER - nothing to do, no backup, no journal`; backup files 4 before, 4 after | 3 s |
| R7 verify reset | yes | exit 0; `V5 recall: 69 theses, 2599 basket rows, 0 dropped` | 3 s |
| R8 restore counts | yes | restored: `thesis` 69, `basket_member` 2599, `calls` 1906, `fact_shares_outstanding` 2219; the fork after the cutover: 69, 2599, 0, 2219; 0 restore stderr lines | 106 s |
| R9 backfill, `verify --stage backfill` | yes | `verify` exit 0 | backfill 1,531 s (25 m 28 s); verify 5 s |
| R10 to R13 | not run | | |

Rehearsal 1, the apply (R5), by table:

| Table | Action | Rows |
|---|---|---|
| `fact_shares_outstanding` | moved to the data tenant | 2219 |
| `fact_cash_burn` | moved to the data tenant | 64 |
| `fact_revenue_mix` | moved to the founding workspace | 0 |
| `edition_cause` | reset (deleted) | 0 |
| `edition_outcome` | reset (deleted) | 113 |
| `edition_call` | reset (deleted) | 682 |
| `edition_entry` | reset (deleted) | 855 |
| `edition_event` | reset (deleted) | 2 |
| `edition` | reset (deleted) | 1 |
| `call_annotation` | reset (deleted) | 176 |
| `calls` | reset (deleted) | 1906 |

- Backup: `/data/backups/alphadeck-20261118T230606-pre-v2-reset.sql`, 1,704,637,697 bytes (1.6G on disk).
- Archive: `/data/backups/alphadeck-20261118T231224-v1-history.sql`, 199,017,663 bytes (190M on disk).
- Journal: `data_repair` `b794de1e-d97e-4fe7-b83b-775a4d1ebeb2`.

Rehearsal 1, the backfill (R9). The dry run said: 1,768 due, 2,080 SEC requests known (up to 4,448 with what only
a fetch can settle), at least 1,387 s to 2,965 s at 1.5 requests a second. The live run: 1,768 companies attempted;
1,512 applied; 254 candidates (211 flagged to the steward, 43 nothing found); 2 unchanged; 0 failed; 2,220 live SEC
fetches; 25 m 28 s. Coverage before and after: share count 63.3% (baseline) to 1395 of 1768 (78.9%); cash/burn 3.2%
(baseline) to 1269 of 1768 (71.8%).

## 8. From `v2` to `main`: environment changes

These are from the plan (it compared `docker-compose.yml` and `backend/domain/settings.py` on `main` against `v2`).
Check them against your `.env` yourself before the day. Names only.

- **New (optional; compose defaults equal the settings):** `ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES`,
  `ALPHADECK_PLATFORM_EXTRACTION_BUDGET_S`, `ALPHADECK_PLATFORM_EXTRACTION_ONBOARD_MAX_NAMES`,
  `ALPHADECK_PLATFORM_EXTRACTION_HOLD_H`, `ALPHADECK_PLATFORM_EXTRACTION_RETRY_MAX`,
  `ALPHADECK_STEWARD_SLACK_WEBHOOK_URL`, `ALPHADECK_WORKSPACE_CAP_LIVE_CARDS_DEFAULT`,
  `ALPHADECK_WORKSPACE_CAP_COMPANIES_DEFAULT`, `ALPHADECK_WORKSPACE_CAP_TERMS_PER_CARD_DEFAULT`,
  `ALPHADECK_ALLOWANCE_FLAG_PER_DAY`.
- **Retired:** `ALPHADECK_ALLOWANCE_AUTO_CONFIRM_PER_DAY` and `ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED`. These two
  names come from the plan and no longer appear in the code. A leftover value is ignored (the plan says so; it
  is not checked here).
- **Changed defaults:** the daily extract, price pull, holdings, chain draft and term run limits are lower on `v2`
  (the plan lists extract 5,000 to 300, price pull 5,000 to 200, holdings 1,000 to 200, chain draft 100 to 20,
  term run 200 to 50 a day). A value in production's `.env` overrides the default; you decide whether to keep it.
- **Needed for the backfill:** `ALPHADECK_USER_AGENT` on the backend (compose already passes it).
- **The editions nightly job (O1):** `ALPHADECK_EDITIONS_NIGHTLY` (compose default 1) and
  `ALPHADECK_EDITIONS_TIMEOUT_S` (compose default 3600). Production's cron log says `EDITIONS_NIGHTLY=0`, so the job
  is off there. Decide before go-live.
- **The cron on the first night:** `RUN_AT` stays 22:30 ET. The boot catch-up does nothing only when V1's pass for
  the last expected night is on record as a finished, live pass that started at or after 22:30 ET that night and
  recorded for at least one thesis (Part 3, step 10). Otherwise it runs a real pass, and its rows are dated before the
  cutover. Onboarding runs once at the cron's start and then every 1,800 seconds; the extraction leg takes up to 300
  names in up to 900 seconds; the backup runs after the pass; the weekly jobs run on Saturday (the company-list
  refresh at 09:00 ET, the term-mentions count at 10:00 ET).

## 9. Known limits

Read these before the day. Each is something this runbook cannot promise.

- **The nightly window is checked once.** `run` and the backfill read the clock at the start. The backup then
  takes several minutes. Do not start `run --apply` within an hour of 21:30 ET. The cron is stopped during the
  cutover, so no pass will start, but the check is not repeated.
- **The `--prod` path has never run.** It is built and tested against test databases only. The first run
  against the name `alphadeck` is the real one. The rehearsals use the fork's name and cannot exercise `--prod`
  (R3 checks that `--prod` on the fork refuses).
- **The backup check's trailer is confirmed.** The apply accepts a dump only if its last 4,096 bytes hold the line
  `-- PostgreSQL database dump complete`. Both rehearsals showed a real 1.6 GB `pg_dump` ends with it (in rehearsal
  2 the line was found once in the last 4,096 bytes, and the apply printed `verified (complete, 1712905676 bytes)`).
- **A go-live-shaped night has not been rehearsed.** Rehearsal 2 ran the first V2 night by hand, for night
  2026-11-19, the same night V1 had already recorded, because the cutover ran just after midnight. So its N2 and N5
  checks failed (step 13), the card changes it counted were the new rules alone, and `verify --stage nightly` and
  `--stage edition` have not yet exited 0. A new night after a morning cutover, run by the cron, has not been seen.
- **Edition 2 was cut after the first night in rehearsal 2, and in the fork's backend container.** This runbook cuts
  it before the first night, in `alphadeck-cron-1` (step 11). Neither of those has been rehearsed.
- **Edition 2's label is the "not separated" shape.** The fork's journal holds 36 repairs since Edition 1's cut, and
  one of them (`repair_catalyst_dates`, 2026-11-11) deleted rows. The engine cannot then tell the rule effects from the
  repair effects, so Edition 2 cannot say which of its changes the rules made, and every changed arm's cause is
  `not_separated`. The fork's journal is production's (from the dump) plus the cutover's own row, so expect the same
  on production.
- **P's differing pages were not checked against the declared rules** beyond the first ten the cut prints (all on one
  thesis, fields `triggers_fired` and `armed_members`). The plan asks that every differing field fit a declared rule;
  that was not checked for the other 559 pages that differ.
- **The editions nightly job is off on production** (O1). Edition 2 is a hand cut and gets no later pages while the
  job stays off. Production's Edition 1 holds 10 theses (669 pages) and was paged once, on 2026-10-24.
- **With the editions nightly off, Edition 2 freezes at the cutover night.** It holds only its re-run pages, through
  V1's last night. No V2 night is added, inked or self-checked. The Scoreboard's default view stops at the cutover
  while Edition 0 keeps growing. A card made after the cut never joins Edition 2, so a later `verify --stage edition`
  should fail E2 for it (expected from E2's definition; not seen). A later rule change never yields Edition 3 unless
  you cut it by hand.
- **Onboarding's split-history fill journals a data repair.** For companies that join a card, the onboarding job
  fills their split history and journals a `data_repair` row under the data tenant (the shared one). V2's cut counts
  data-tenant repairs, so a fill after your hand cut makes that night's editions job refuse a trial (one cut per market
  day) and the next night's run one. With the job on, an Edition 3 can appear within days of go-live. A trial of this
  size took about 12 minutes of re-run in rehearsal 2. On V1 these repairs never started a trial. Not seen on V2.
- **A `failed` onboarding row blocks every cut** (status `deferred`) and holds that card's edition pages, until
  someone runs `python -m pipeline.onboarding --retry-failed`.
- **Option, not a step (code):** make the editions nightly refuse to page onto an edition whose stored fingerprint
  version differs from the engine's. That would close the "V2 pages land on Edition 1" path by code, instead of by this
  runbook (step 11b). The review says it touches `editions/`, which is fingerprinted, so it would start a trial and
  need a rule-log decision. It is not built and not needed if steps 11 and 11b are followed. It is your call.
- **The cron's first start has never been rehearsed.** The fork has no cron (its service is in a profile that never
  starts). A read-only review traced the code (step 10 lists the boot lines it expects), but no run has shown them.
  Also not rehearsed: the editions nightly paging onto Edition 2 (R11b), including its first ink of every matured
  Edition 2 episode at once (the cost is not measured), and Saturday's weekly jobs (the company-list refresh and the
  term-mentions count) under V2. Their code is unchanged from V1, but they have not run against the V2 schema.
- **R10, R11b and R13 have not been run.** Nobody signed in during rehearsal 1 or 2, the editions nightly after the
  cut was not run, and the rollback drill was not done. The `--health-url` check (V10) against a real URL, and the
  production-only steps (the `--prod` path, `deploy.sh`, the cron's first boot, the compose image names) have not
  been seen to run.
- **The first night's pushes and the cut's Slack line were never seen in a real channel.** The fork's Slack
  webhooks were cleared on purpose, so the numbers in step 12 come from the log, not from Slack.
- **No user-made counting catalyst entry existed in the rehearsals.** The `fact_thesis_catalyst` rows they kept were
  W6's conversion of `fact_theme_conviction`, so the recall baseline's ninth child table was never tested against
  entries an operator added.
- **Neither rehearsal used `--fresh`.** Their data is production's as of a nightly backup
  (`alphadeck-20261118T035357.sql` and `alphadeck-20261120T040004.sql`), not a dump taken for the rehearsal.
- **The measured times are fork times.** The fork runs on the same host as production, but production's database is
  larger or equal. The backfill's time depends on the SEC rate setting (1.5 requests a second in the rehearsals).
- **The compose image names and the cron's first boot** are not in the code, so they are marked "not yet rehearsed".
- **A host command with no database setting points at production.** A `python -m pipeline...` command run on the
  host, with no `DATABASE_URL` in its environment, connects to `localhost:5544` and the database `alphadeck`:
  production. This runbook runs every command inside a container for that reason. The cutover guard reads the
  database name from its own connection, not from a URL.
- **Join dates before 2026-09-15** are estimates (exact from basket snapshots after that date). V1's `calls` is
  kept, so nothing about them is lost with the cutover, and Edition 2 carries Edition 1's frozen join dates.
- **The cut takes a lock.** It takes the editions job lock and refuses (exit 75) while the call-of-record lock is
  held. Run it in the daytime, outside 21:30-23:30 ET.
- **A later change to call logic** changes the engine fingerprint. Declare it in `domain/rule_log.py` (in the same
  pull request); the next cut (the nightly job's, or yours by hand) then cuts the next edition. Edition 2 is V2's
  own change.

## 10. Reference

**`python -m pipeline.v2_cutover run` exit codes:** 0 done, a dry run, or already cut over with nothing left; 1
refused (nothing written), including a rollback by `MoveRefused`; 2 wrong database or confirmation, or an unmapped
or missing table; 3 the backup failed or did not verify (nothing written); 4 the transaction failed and rolled
back (backup kept); 75 a lock is busy or the nightly window.

**`plan` exit codes:** 0 clean, 1 surprises, 2 an unmapped or missing table.

**`scripts/deploy.sh` exit codes:** 0 deployed and verified (or a clean dry run); 2 usage; 3 refused (nothing
changed); 4 a step failed; 5 deployed but a verification failed.

**`pipeline.extract_values` exit codes:** 0 ran (or the dry run); 1 could not run (no User-Agent for a live run, no
database); 2 a bad argument; 75 refused or stopped.

**`python -m pipeline.v2_cutover verify` exit codes:** 0 verified; 2 cannot verify (wrong database or flags, a
missing, `.pending`, unreadable or other-database provenance file, or an unmapped or missing table); 5 a check
failed.

**`python -m editions.run cut` exit codes:** 0 ran (a cut, a no-change trial, a deferred cut that wrote nothing, or nothing to try); 75 refused because
a lock is held (nothing written). It is a dry run unless `--apply`.

**`editions.run publish` exit code on a refusal:** 2. It is not part of the cutover: Edition 1 is already
published, so it refuses.

**Files.**
- Backups: the host's `./data/backups` (`/data/backups` in the containers). Names: `alphadeck-<UTC ts>[-<label>].sql`.
  The ones this runbook makes: `-pre-v2-cutover` (step 4), `-pre-v2-reset` and `-v1-history` (step 7),
  `-post-v2-reset` (step 10), `-pre-edition-2` (step 11).
- Plans and provenance: `/data/cutover_runs/` in the backend container (`prod-plan.json`, `prod-run-plan.json`,
  `<ts>-alphadeck.json`). Copy them off with `docker cp alphadeck-backend-1:/data/cutover_runs/<file> .`
- Provenance keys: `tool`, `database`, `started_at`, `committed_at`, `journal_id`, `backup`, `archive`, `plan`,
  `preflight`, `counts` (`moved`, `by_ratified`, `history_kept`, `orphan_purity`, `validated`, `companies`, `backup`,
  `archive`), and `baseline` (theses, basket members, child counts, `operator_decision`, the security ids with a
  share count or a cash row, the per-tenant counts of the three moved tables, `app_users`, `history` (the eight
  tables' counts), `editions` (id, tenant, number, `created_at`), and the `plan --json` document). The full list is
  in the docstring of `backend/pipeline/v2_reset.py`.
- The journal row: table `data_repair`, tool `v2_cutover`, `deletes` false (true only if `--orphan-purity delete`
  removed a row), `provenance` set to the provenance path.
- Code: `backend/pipeline/v2_reset.py` (the apply path), `backend/pipeline/v2_cutover.py` (the commands),
  `backend/pipeline/v2_move.py` (the move), `backend/pipeline/v2_verify.py` (the verify stages),
  `backend/editions/run.py` and `backend/editions/cut.py` (the cut), `backend/pipeline/backup.py`,
  `scripts/deploy.sh`, `scripts/refresh-fork.sh`.
- Related pages: `docs/DEPLOY.md`, `docs/FORKS.md`, `docs/V2_BRANCH.md`, `docs/FEED_LOOP.md` (backups and restore),
  `docs/PLATFORM_VALUES.md` (the backfill and decision B4), `docs/EDITIONS.md` ("A new workspace's
  Edition 1").
