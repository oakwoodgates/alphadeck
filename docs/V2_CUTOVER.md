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
3. Move the platform facts (share counts, cash/burn) to the shared data tenant, and empty V1's history, in
   one transaction (`python -m pipeline.v2_cutover run --apply`).
4. Run the first extraction backfill.
5. Start the cron.
6. Verify, then publish Edition 1 after the first V2 night.

**What is deleted.** Exactly eight tables are emptied, row by row, with `DELETE FROM` (never `TRUNCATE`, never
`CASCADE`, never `DROP`). The tables stay; their rows go. This was the operator's decision (V1's record is not
carried into V2):

| Table | What it held |
|---|---|
| `calls` | the immutable calls log: what the system said each night |
| `call_annotation` | notes on past calls |
| `edition` | the Scoreboard's editions |
| `edition_event` | each edition's event log |
| `edition_entry` | each thesis's join date in an edition |
| `edition_call` | the re-run pages |
| `edition_outcome` | matured scores |
| `edition_cause` | the causes for a change of edition |

**What is moved (relabeled, not copied or deleted).** The founding workspace's rows in `fact_shares_outstanding`
and `fact_cash_burn` change owner to the shared data tenant. Ratified `fact_revenue_mix` rows under the data
tenant, if any, change owner to the founding workspace. The row ids, values and `recorded_at` do not change.

**What is kept.** Everything else: every thesis (ids and `created_at` unchanged), every basket member and
snapshot, kill criteria, evidence, exclusions, thesis catalysts (`fact_thesis_catalyst`: a card's counting catalyst
entries; W6's migration 0213 turned each `fact_theme_conviction` version into one whole-card entry), term
mentions, `fact_theme_conviction` (kept, and read by nothing), `operator_decision`, positions, users, sessions, tokens, tenants, and every market, filing and price fact. The
dry run counts them before the apply, and the provenance file records a baseline so `verify` can prove that no
company was dropped.

**The one rule exception.** `docs/INVARIANTS.md` says the calls log and the editions store are immutable. The
cutover is the only code allowed to delete from them, once: `backend/pipeline/v2_reset.py` (the `run` command),
which leaves a `data_repair` journal row with tool `v2_cutover`.

## 2. Decisions to make before the day

Nothing here is decided for you. Each row has a flag or setting with a safe default, so you can do the cutover
without answering any of them, and the default is what happens if you do not choose. The "plan's
recommendation" column is only what the plan wrote down. Fill in the last column yourself.

| # | Decision | Options | Carried by | Safe default | Plan's recommendation (not a decision) | Your decision |
|---|---|---|---|---|---|---|
| N1 | The one stray unratified `fact_revenue_mix` row on the data tenant (on the fork it was DEVCO, a seed demo company on no card; production may differ) | (a) leave it, unread; (b) delete it (journaled, and it is in the backup); (c) ratify it into the founding workspace | `--orphan-purity leave` or `--orphan-purity delete` on `run` | `leave`. Option (c) is not built | (a) | |
| N2 | When to drop the empty `call_annotation` table | (a) keep the empty table, drop it later by a new migration (0214 is the next free number; 0212 and 0213 are W6's); (b) drop it at cutover | nothing in the cutover | (a): the table stays | (a) | |
| N4 | The first backfill | (a) live, now; (b) cache only (`--no-live`), live later | `--apply` or `--apply --no-live` on `pipeline.extract_values` | (a) live. On the fork, the cache-only run failed 1,016 companies with "not cached", so (b) leaves most companies with no value yet | (a) | |
| N5 | The first V2 night | (a) accept: names already armed at cutover are on the Board but never pushed; (b) one summary push listing what is armed (a new CLI, not built); (c) change the push rule (call-logic work, not proposed) | none | (a) | (a), and open the Board the next morning | |
| N6 | Edition 1 | For the existing workspace: (a) publish after the first night, or (b) skip. For later workspaces: (c) leave on Edition 0, or (d) auto-publish (not built) | the Edition 1 step (Part 4, step 13); (d) would need code | (a) and (c) | (a) and (c) | |
| N7 | Caps on the existing workspaces | (a) leave them unset (no cap); (b) set them | `python -m pipeline.users caps` (any time) | (a): NULL means no cap | (a) | |
| N8 | In-flight user state (decisions, positions) | (a) keep; (b) reset | the only built option is (a); the dry run prints the counts | (a) | (a) | |
| N9 | A separate archive of V1's history | (a) take it; (b) the full backup is enough | `--no-history-archive` on `run` | (a): archive taken | (a) | |
| N10 | Cut over before or after W6 (catalyst conversion) | (a) after W6 is merged and rehearsed; (b) before | the go-live date; no code. Status: W6 is merged, and rehearsed through R9 on the fork (rehearsal 1, Part 7); R10 to R13 are not yet rehearsed | none: it is a date choice | (a) | |
| N11 | Day and time | (a) a weekday morning; (b) a Saturday | the tools refuse only inside 21:30-23:30 ET (`run`, the backfill) and 22:20-22:50 ET (`deploy.sh`) | a time that leaves hours before 21:30 ET | (a) | |
| N12 | Retag V1's images for a fast rollback | (a) yes; (b) roll back by a revert merge on `main` and `deploy.sh` (slower: CI) | Part 4, step 2 | (a) yes | (a) | |
| N13 | The `/backtest` archive (frozen V1 passes bound read-only by `docker-compose.prod.yml`) | (a) keep showing it; (b) unbind it | no change needed | (a) | (a) | |
| N14 | The `CLAUDE.md` Commands lines for this work (the file is write-protected for agents) | apply the proposed lines | by hand | not applied until you do | apply them | |

## 3. Preconditions checklist

Do not start Part 4 until every box is ticked. Tick them on the day, not from memory.

- [ ] Rehearsal 2 passed on the exact `v2` commit that will merge, with no code change after it (Part 7). Its
      record is in Part 7, "Results".
- [ ] The merge-main routine in `docs/V2_BRANCH.md` has been done one last time, and that pull request is merged
      into `v2`.
- [ ] The `v2` to `main` pull request is open, CI is green on it, and it is **not merged yet**. Plan to merge it
      with a merge commit, not a squash and not a rebase (this keeps `v2`'s history and gives one commit for
      `git revert -m 1`).
- [ ] The decisions in Part 2 are answered, or you have accepted the defaults on purpose.
- [ ] Disk space: room for four labeled full dumps and the history archive. A production dump was about 1.7 GB
      in the plan's measurement (estimate: about 7 GB in all). In rehearsal 1 the `-pre-v2-reset` dump was
      1,704,637,697 bytes and the `-v1-history` archive 199,017,663 bytes. Check the free space where `./data/backups` lives.
- [ ] Production `.env` (open it yourself; this page never shows it) has, by name: `ALPHADECK_USER_AGENT` (the
      backfill refuses a live run without it) and `ALPHADECK_OPS_TOKEN` (`deploy.sh` reads it; `docs/DEPLOY.md`).
      Also check any `ALPHADECK_ALLOWANCE_*` override, and the variable changes in Part 8.
- [ ] You are on the main checkout, the tree is clean, and you have Git Bash open (not PowerShell's `bash`, which
      is WSL).
- [ ] It is a weekday morning or another time that leaves hours before 21:30 ET. Do not start within an hour of
      21:30 ET (see Part 9, "Known limits").
- [ ] You have the time. The plan's estimate for steps 1 to 10 is a few hours, mostly the backfill (25 minutes 28
      seconds on the fork at 1.5 requests a second, rehearsal 1), two or three backups (about 6 minutes each) and
      the builds. Rehearsal 2 replaces the rest of these estimates.
- [ ] The user is told that the Board will have no calls until the first V2 night, and that nobody edits cards
      from step 1 until step 8 is done.

## 4. The steps

Conventions used below:

- The production compose command is always written out in full, as `docs/DEPLOY.md` gives it:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml`.
- The production containers are `alphadeck-backend-1`, `alphadeck-cron-1` and `alphadeck-postgres-1`.
- Inside the backend container, `/data/backups` is the host's `./data/backups`. `/data/cutover_runs` is on the
  `appdata` volume (not visible on the host unless you copy it out with `docker cp`).
- `<ts>` is a UTC timestamp such as `20261125T140000Z`. Copy the real one from the output of the step before.
- A duration written "measured on the fork, 2026-11-18" comes from rehearsal 1 (Part 7). The fork runs on the
  same host as production, but production's database is larger or equal, so it is a fork measurement, not a
  promise. Durations marked **estimate** come from the plan (a measured backup on a production-size dump, or a
  guess); the rehearsal did not time them.
- "Expected output" quotes the program text. Where a line depends on production data, it says so.

### Step 1. Announce, then freeze (stop the cron)

```bash
cd /c/Users/funky/sites/oakwoodgates/alphadeck && git branch --show-current && git status --short
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop cron
```

*What it does.* Tells you which checkout you are in, then stops the V1 cron so no V1 pass writes while you work.
Do not edit cards from now until step 8 is done. Do not run this inside 22:20 to 22:50 ET, and not while a pass
is running (check the Admin page).

*You should see.* `main` (or the branch you expect) and no changed files; then Compose reporting the cron
container stopped.

*Duration.* Seconds (estimate).

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
that `cron.alarms` did not grow). The fork rebuild (R0) took 91 seconds (measured on the fork, 2026-11-18); it is
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
the apply acknowledges it. Compare the counts with rehearsal 2's JSON: only the days since that dump should
differ. In rehearsal 1 on the fork it ended `RESULT: REFUSE - 1 surprise(s). Exit 1.`, with the one surprise
being the stray purity row (the log kept the line `workspace only in V2 and no plan moves an unratified row`),
and these rows: `fact_shares_outstanding` 2219 `move→data` (auto 1977, operator 242),
`fact_cash_burn` 64 `move→data`, `calls` 1906 `reset`, `edition_call` 682 `reset`. The full counts are in Part 7.

*Duration.* Measured on the fork, 2026-11-18: 3 seconds.

*If it fails.* Exit 2 means a table the registry does not name, or a named table the database lacks. Stop. This
needs a code fix (the merge-main routine in `docs/V2_BRANCH.md`), not a workaround. Any surprise other than the
stray purity row (a platform row under a workspace, a workspace company catalyst, and so on): stop and
investigate; `run` will refuse it too.

### Step 6. The cutover dry run (`run` without `--apply`)

```bash
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover run --database alphadeck --prod --confirm "reset alphadeck" --plan-out /data/cutover_runs/prod-run-plan.json
```

Add `--orphan-purity delete` only if you chose N1 option (b). Add `--no-history-archive` only if you chose N9
option (b). Use the same flags in step 7.

*What it does.* Reads the database name from its own connection and refuses unless it matches `--database` and
`--confirm` is exactly `reset alphadeck`. Then it runs the preflight and prints the plan. It is a read-only
session: it cannot write. It takes no locks and no backup. It writes only the plan file you name.

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
| `ok: P11 only the reset tables reference the reset tables` | so no delete can reach another table |
| `ok: P10 not cut over yet` | |
| `plan: move fact_shares_outstanding n row(s) (...)`, `plan: move fact_cash_burn ...`, `plan: move fact_revenue_mix ...` | what will move, by `ratified_by` |
| `plan: reset calls n row(s)` and one line for each of the other seven tables | what will be deleted |
| `plan: orphan purity n row(s) (leave)` | |
| `plan-out: /data/cutover_runs/prod-run-plan.json` | |
| `backup: would take a new 'pre-v2-reset' backup and the V1 history archive` | |
| `RESULT: DRY RUN - nothing written. Exit 0.` | |

Read the plan lines. Do they match your expectation (the number of share-count and cash/burn rows to move, the
number of calls to delete, the count of kept decisions and positions)? This is the dry run of rehearsal 1, as the
rehearsal's log kept it (the `count: P9` line was not kept, and the run had no `--prod`, so its first lines
differ):

```
ok: P3 outside the nightly window
ok: P1 every migration file is applied
ok: P2 the data tenant and the founding workspace exist
ok: P5 rule 8 acknowledged by --orphan-purity leave: 1 unratified data-tenant purity row(s)
ok: P6 no workspace company catalysts
ok: P7 every share-count and cash/burn ratified_by is in 0206's vocabulary
ok: P8 the move has no refusals
ok: P11 only the reset tables reference the reset tables
ok: P10 not cut over yet
plan: move fact_shares_outstanding 2219 row(s) (auto 1977, operator 242)
plan: move fact_cash_burn 64 row(s) (operator 64)
plan: move fact_revenue_mix 0 row(s) (none)
plan: reset edition_cause 0 row(s)
plan: reset edition_outcome 113 row(s)
plan: reset edition_call 682 row(s)
plan: reset edition_entry 855 row(s)
plan: reset edition_event 2 row(s)
plan: reset edition 1 row(s)
plan: reset call_annotation 176 row(s)
plan: reset calls 1906 row(s)
plan: orphan purity 1 row(s) (leave)
plan-out: /data/cutover_runs/r1b-run-plan.json
RESULT: DRY RUN - nothing written. Exit 0.
```

*Duration.* Measured on the fork, 2026-11-18: 5 seconds.

*If it refuses or fails.*

| Exit | Meaning | What to do |
|---|---|---|
| 2 | The guard refused (`REFUSE:` names the reason, `RESULT: REFUSED - nothing read or written`), or a table is unmapped or missing (`REFUSED - an unmapped or missing table`) | Fix the flag: `--database` must equal the database the connection is on, `--confirm` must be exactly `reset alphadeck`, and `alphadeck` needs `--prod`. An unmapped table needs a code fix; stop |
| 1 | A preflight line says `REFUSE:` (nothing written) | Read each `REFUSE:` line. P1: an unapplied migration (deploy or migrate first). P5/P6: a surprise (stop and investigate). P7: a row that would fail 0206's vocabulary. P8: the move's own refusal. P10: already cut over, with history or rows still present (do not run again; see the note below). P11: a foreign key from another table into a reset table (stop; a code fix is needed) |
| 75 | The nightly window (`REFUSE: P3 inside the nightly window (21:30-23:30 ET)`) | Wait until it ends, or run earlier |

### Step 7. The cutover apply (THE POINT OF NO RETURN)

```bash
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover run --database alphadeck --prod --confirm "reset alphadeck" --apply --expect-plan /data/cutover_runs/prod-run-plan.json
```

Use the same `--orphan-purity` and `--no-history-archive` flags you used in step 6, or the plan will not match.
`--prod --apply` refuses without `--expect-plan`.

*What it does, in order.*
1. The guard and the preflight again, then the repair lock (call-of-record lock and fact-writer gate) and job
   locks 3 to 7 are taken and held for the whole run. Any busy lock is exit 75 and names the holder.
2. The plan is compared with the file from step 6. Any difference refuses (exit 1) before any write.
3. The backup, `pipeline.backup.run_backup(label="pre-v2-reset")` of the same database (it refuses if the
   connection string names a different database), checked by `verify_dump`: the file is in the backups
   folder, has a backup name, is not empty, and ends with the `-- PostgreSQL database dump complete` trailer.
   Then the history archive (`-v1-history`: the eight tables only), checked the same way. Failure is exit 3 with
   nothing written.
4. **One transaction:** re-plan and compare again; read the baseline; the three move legs; delete the orphan
   purity rows (only with `--orphan-purity delete`); `DELETE FROM` each of the eight tables, children first,
   each count equal to the plan; mark the touched companies changed; `VALIDATE CONSTRAINT` on the two 0206
   constraints; and the `data_repair` journal row (tool `v2_cutover`, with `deletes` true), written inside the
   same transaction. The provenance file is written as `<ts>-alphadeck.json.pending` before the commit.
5. **Commit.** Then the provenance file is renamed to `<ts>-alphadeck.json`.

**The point of no return is the commit in item 5.** Here is what that means.

| | Before the commit | After the commit |
|---|---|---|
| The live database | Unchanged. Any failure rolls back everything (exit 1 or exit 4), re-enables every trigger it disabled, and removes the `.pending` file | V1's history is gone from the live database; the platform facts are under the data tenant; the journal row exists |
| What you can undo | All of it: nothing was written (the backup files stay on disk) | Only by restoring a backup (Part 5). There is no "undo" command |
| What a restore costs | Nothing | Everything written after the backup. Right after the commit, that is almost nothing. It grows with every hour the V2 stack runs |
| Running the command again | Safe | Refused when anything is left (see "second run" below) |

*Second run.* If the cutover has committed, a second run of `run` finds the `v2_cutover` journal row. With
nothing left to move and nothing left to delete, it prints `ALREADY CUT OVER - nothing to do, no backup, no
journal` and exits 0. With any history row present (including after a V2 night has recorded calls) it refuses,
exit 1: `a second reset would delete V2 nights`. Never work around this refusal.

*You should see.*
- `V2 CUTOVER - RUN - APPLY`, `ok: P3 ...`, `ok: P4 holding the repair lock and job locks 3-7`, the preflight
  lines and plan lines as in step 6, then `ok: the plan equals --expect-plan /data/cutover_runs/prod-run-plan.json`.
- `backup: /data/backups/alphadeck-<ts>-pre-v2-reset.sql verified (complete, <n> bytes)`
- `archive: /data/backups/alphadeck-<ts>-v1-history.sql verified (complete, <n> bytes)`
- then, after the commit: one `moved: <table> <n>` line per moved table, one `reset: <table> <n>` line per reset
  table (eight lines), `orphan purity: <n> row(s), leave (0 deleted)`, `validated: 2 constraints`,
  `companies stamped changed: <n>`, `journal: data_repair <id>`, `provenance: /data/cutover_runs/<ts>-alphadeck.json`
  and `RESULT: CUT OVER. Exit 0.`

Copy the `provenance:` path. Step 8 needs it.

*Duration.* Measured on the fork, 2026-11-18: 389 seconds for the whole apply (backup of 1,704,637,697 bytes,
archive of 199,017,663 bytes, and the transaction). The plan's measurement of a backup on a production dump,
about 6 minutes, fits that. The log does not time the three parts separately. The nightly-window check happens once, at the start (see Part 9).

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

### Step 8. Verify after the reset, then sign in by hand

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
share-count or cash/burn row outside the data tenant, and no company lost; V7 purity; V8 the eight history tables
empty; V9 exactly one `v2_cutover` journal row that matches the provenance; V10 `--health-url` answers 200 (a
`note:` when you leave it out).

*You should see.* Every check line starts with `ok:`, `note:` lines never fail, and the last line is
`RESULT: VERIFIED. Exit 0.` The recall line reads `ok: V5 recall: N theses, M basket rows, 0 dropped`. With
`--health-url` there is also `ok: V10 health: http://localhost:8000/health answered 200`. Rehearsal 1 on the fork
printed these (the numbers are the fork's, from a same-day nightly dump):

```
ok: V1 surprises: no surprise beyond the acknowledged orphan purity; every table is in a known scope
ok: V2 schema: every migration applied; both 0206 constraints validated
ok: V3 tenants: no thesis or user on the data tenant; 1 enabled super_admin
ok: V3 users: 2 users, unchanged
ok: V4 theses: 69 theses: ids, tenants and dates unchanged
ok: V5 recall: 69 theses, 2599 basket rows, 0 dropped
ok: V5 children: per-thesis counts of 9 child tables equal the baseline
ok: V5 decisions: operator_decision 0 (the baseline's)
ok: V6 platform facts: share counts and cash/burn only in the data tenant; 1224 companies with a share count and 59 with cash/burn, none lost
ok: V7 purity: no ratified purity on the data tenant; founding purity 21
ok: V8 history: empty: edition_cause, edition_outcome, edition_call, edition_entry, edition_event, edition, call_annotation, calls
ok: V9 journal: one 'v2_cutover' journal row (b794de1e-d97e-4fe7-b83b-775a4d1ebeb2), its counts equal the provenance's
RESULT: VERIFIED. Exit 0.
```

The rehearsal ran without `--health-url`, so it printed `note: V10 health not checked (no --health-url)`: the
V10 check against a real URL is **not yet rehearsed**.

*Exit codes.* 0 is verified. 2 is `RESULT: CANNOT VERIFY - nothing checked. Exit 2.`: it could not check (a wrong
`--database`, `--prod` missing or misplaced, a provenance file that is missing, `.pending`, unreadable or from
another database; read the `REFUSE:` line). A table the registry does not name also ends in exit 2. 5 is
`RESULT: <n> check(s) failed. Exit 5.`: at least one `FAIL:` line.

*Then, by hand.* No script signs in for you. Open the app. Sign in as the user and as the steward. Check: the
Board shows the live cards with no call; the Scoreboard reads "The record starts with the first nightly call.";
the steward queue is populated; the Admin page shows `data_tenant_theses` 0 and `platform_rows_in_workspaces` 0.

*Duration.* `verify` took 3 seconds (measured on the fork, 2026-11-18); then your own time (estimate).

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
cash/burn, is at or above the baseline's). Rehearsal 1 on the fork printed this summary:

```
extract-values: Shared facts · Nov 18: 1768 extracted: 1512 applied, 211 to the steward, 43 nothing found, 2 unchanged · 2220 SEC requests · 25m 28s
extract-values: attempted 1,768 of 1,768 due · applied 1,512 · candidates 254 (211 flagged to the steward, 43 nothing found) · unchanged 2 · skipped 0 · busy 0 · failed 0 · deferred 0 · 2,220 live SEC fetches
```

and this verify output, `RESULT: VERIFIED. Exit 0.`:

```
ok: B1 attempts: every one of 1768 companies on a live card was attempted
note: B attempts by status: done 1768
ok: B2 B4: B4 unlocked: a backfill attempt extracted a company
ok: B3 coverage: share count 1395/1768 (78.9%; baseline 63.3%); cash/burn 1269/1768 (71.8%; baseline 3.2%)
```

The check lines of stage `reset` print first, as in step 8. A `FAIL:` line is exit 5.

*Duration.* Measured on the fork, 2026-11-18: 1,531 seconds (25 minutes 28 seconds) for the live run, 2,220 SEC
requests, at `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5`; `verify` took 5 seconds. The compose default for that
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
22:50 ET.

*You should see.* a final line that begins `OK: deployed <sha> to prod: cron (rebuild)` and ends with
`every verification passed`, and in the cron log
the boot lines, `daily-cron: each pass bounded at ...`, and the plan's expectation of a catch-up that does
nothing (the V1 pass ran last night) and `onboard-cron: every 1800s`. **Not yet rehearsed.**
The fork has no running cron (its service is in a profile that never starts), so this step is only confirmed on
the first production run.

*Duration.* Backup about 6 minutes (measured); build about 5 minutes (estimate).

*If it fails.* Same exit codes as step 4. If it refuses on the `HEAD` check, you are not on the merged `main`.

### Step 11. The first V2 night (automatic)

The cron runs the pass at 22:30 ET on its own. Do nothing, and do not run the pass by hand.

**The first V2 nightly sends NO notifications.** With the calls log empty, there is no earlier call to compare
against, so no thesis has a "new arm" to push, and the digest has no line for it
(`backend/pipeline/daily.py`: "First-ever call = no prior = no event"). The consequence is the opposite of
what an earlier plan said: a name that is armed at cutover is never announced, because the second night compares
Armed with Armed. **Open the Board the next morning** and read it yourself (decision N5).

*Duration.* The pass takes about 7 minutes (a 2026-10-21 measurement of 396 seconds), plus the extraction leg
(up to 900 seconds).

### Step 12. The next morning: verify the night

```bash
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage nightly --database alphadeck --prod --provenance /data/cutover_runs/<ts>-alphadeck.json
```

*What it does.* It runs every check of the earlier stages, then N1 the night has call rows (the newest
`calls.asof`, or the night you give with `--night YYYY-MM-DD`), N2 a row for every live thesis, N3 none
reconstructed, N4 the run kind is `cron`, N5 none dated before the cutover, N6 none under the data tenant. From
this stage on, a difference in what users may edit (users, archived cards, new cards, child counts, purity)
is a `note:`, not a `FAIL:`; recall and the platform-fact checks still fail. *You should see:* `ok:` on every
check and `RESULT: VERIFIED. Exit 0.` **Not yet rehearsed** (R11 was not run, so the output of this stage has
not been seen). Exit 2 and exit 5 mean what they do in step 8.

Also open the Board and the Admin page (cron status and alarms, the steward queue).

*If it fails.* A missing row for a live thesis: read the cron log and the Admin page. Do not publish Edition 1
(it needs a recorded night).

### Step 13. Publish Edition 1 (decision N6)

```bash
docker exec alphadeck-backend-1 python -m pipeline.backup --label pre-edition-1
docker exec alphadeck-cron-1 python -m editions.run publish --workspace 00000000-0000-0000-0000-000000000002 --workers 4
docker exec alphadeck-cron-1 python -m editions.run publish --workspace 00000000-0000-0000-0000-000000000002 --workers 4 --apply
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage edition --database alphadeck --prod --provenance /data/cutover_runs/<ts>-alphadeck.json
```

*What it does.* A labeled backup first. Then the publish dry run, then the real publish into the editions store
(the cron container is used because it carries the `replay` extra and the backend image does not). It re-runs
every live thesis night with today's rules into the labeled "first re-run" edition. It is not the record: the
record (Edition 0) starts with the first nightly call. A second publish at the same cut is refused.

*Refuses (exit 2, nothing written)* with one sentence: `REFUSED: this workspace has no live cards; there is
nothing to re-run. Nothing was run or written.`, or `REFUSED: this workspace has no nightly call on record yet;
publish Edition 1 after its first nightly run. Nothing was run or written.`, or `REFUSED: Edition 1 ... is
already published ...`. Run it outside 21:30-23:30 ET. The plan believes publish takes no lock; that was read
from the command dispatch only, so do not run it during the nightly pass.

*You should see.* The dry run ends with `DRY RUN`, then `nothing written to the database (re-run with --apply to
publish)`; the real run prints `PUBLISHED edition <id>` and the before and after counts for `edition`,
`edition_event`, `edition_entry`, `edition_call` and `calls`. `verify --stage edition` adds E1 (exactly one edition, number 1, for each workspace with a live card), E2 (an
entry for every live thesis of that workspace) and E3 (no edition for a workspace with no live card), and should
end `RESULT: VERIFIED. Exit 0.` The Scoreboard should default to Edition 1. **Not yet rehearsed** (R12 was not
run).

*Duration.* Backup about 6 minutes (measured); publish about 10 to 20 minutes (estimate; the dev trial took
about 9 minutes at 4 workers for 659 pages).

*If it fails.* Nothing is written by a refusal. Fix the cause and run it again.

## 5. Rollback

Two procedures. Both restore a plain-SQL dump into an empty database, and both lose everything written after
that dump.

**A. Back to V1 (code and database).** Use this at any step. It is lossless before the V2 cron starts (step 10);
after that it loses every V2 night, steward decision, flag, extraction result and card edit since the dump.

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

*How long.* The plan's estimate was 15 to 30 minutes. In rehearsal 1, R8 restored the 1.6 GB
`-pre-v2-reset` dump (1,704,637,697 bytes) into an empty database on the fork's Postgres in 106 seconds, with no
error lines (measured on the fork, 2026-11-18; a fork measurement, not a promise). R1 (drop, restore and migrate
the fork from a nightly dump) took 160 seconds. The rollback drill itself (R13, restoring into the live name and
restarting V1's images) is **not yet rehearsed**.

*What is lost.* Everything written after the step 4 dump. Before step 10 that is nothing a user made. After it,
all V2 activity.

**B. Reset only, staying on V2 (rare).** Restore `alphadeck-<ts>-pre-v2-reset.sql` (V2's schema, V1's data) the
same way, with the V2 images, then run steps 5 to 7 again. That dump holds everything the step 7 backup held. The
V1 history archive (`alphadeck-<ts>-v1-history.sql`) holds only the eight tables; it is for reading V1's record,
not for restoring a database.

## 6. After the cutover

- **The first night sends nothing.** Open the Board the next morning (step 11).
- **Edition 1.** Step 13. Until it is published, `editions.run nightly` prints
  `Editions · no open edition to add pages to` and exits 0, and the cut prints
  `Editions · nothing published yet, nothing to try`. That is normal.
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
  which companies on production this affects, and whether the backfill reports them (the rehearsal 1 log does not
  say).
- **Leftover table (N2).** `call_annotation` stays as an empty table. It can be dropped later by a migration
  (0214 is the next free number; 0212 and 0213 are W6's), with its entries in the scope map, in `CUTOVER_ACTION` and in
  `tests/app/test_v1_annotation_gone.py`. There is no date. Do it after go-live when you are sure nothing reads
  it.
- **The V1 history archive and the V1 rollback dump.** `alphadeck-<ts>-v1-history.sql`,
  `alphadeck-<ts>-pre-v2-reset.sql` and `alphadeck-<ts>-pre-v2-cutover.sql` are labeled dumps. They are removed
  only when both older than 90 days and outside the newest 20 labeled dumps
  (`docs/FEED_LOOP.md`). To remove them sooner, delete the single files by hand. The plan sets no date. A
  reasonable rule, which is yours to change: keep the rollback dumps and the V1 image tags until Edition 1 is
  published and a week of V2 nights has passed, and keep the archive as long as you want to read V1's record. Then
  remove the `v1-pre-v2` image tags with `docker rmi`.
- **Fork refresh.** After go-live, production dumps are V2 dumps. The plan lists a follow-up (W10f) that drops
  the default platform-facts move from `scripts/refresh-fork.sh` and removes `--v1-state`.
- **The rehearsal fork.** Destroy it when you no longer need it (`scripts/fork.sh destroy --yes`, `docs/FORKS.md`).
- **`CLAUDE.md`.** The proposed Commands lines are in the pull request body of this change. The file is
  protected, so apply them yourself (decision N14).

## 7. The rehearsal procedure (on the fork)

Rehearsals run on the fork (`docs/FORKS.md`), never on production. Run them by hand with the operator present.
Rehearsal 1 may find bugs: fix each by its own pull request, then rehearse again. **Rehearsal 2** must use a
production dump from the cutover week and the exact `v2` commit that will merge, and must pass with no code change
afterward. A code change after rehearsal 2 means rehearsal 3.

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
      tables. Write each step's full output to a log file (rehearsal 1 did, and only the lines it chose to print
      survived in its summary log; keep the whole output of every step, with its exit code and its wall time).
- [ ] If the fork's own SEC cache holds a live backfill from an earlier run, pass `--no-cache` to `refresh-fork.sh`
      in R1: it skips the copy of production's cache, so the fork keeps its own. Without it, the default copies
      production's cache over the fork's.

**The run (time every step)**
- [ ] **R1** `bash scripts/refresh-fork.sh --reset --fresh --v1-state`. `--fresh` takes a read-only dump of
      production first (rehearsal 1 used `--from-latest --no-cache` instead, so its dump was the newest existing
      nightly dump; rehearsal 2 must use `--fresh`); `--v1-state` skips the platform-facts move, so the fork keeps V1-shaped data. Pass: the last
      line ends `(V1 state: run the cutover next)`. The drop, restore and migrate time is the rollback restore
      estimate.
- [ ] **R2** `$B python -m pipeline.v2_cutover plan --json /data/cutover_runs/r<N>-plan.json`. Pass: the
      founding workspace's share and cash rows are planned `move→data`; the only surprise is the stray purity row
      (if any); exit 1 (or 0 if none).
- [ ] **R3** Negative checks, each exit 2 with nothing written: a wrong `--database`; a wrong `--confirm`;
      `--prod` on `alphadeck_v2`.
- [ ] **R4** `$B python -m pipeline.v2_cutover run --database alphadeck_v2 --confirm "reset alphadeck_v2" --plan-out /data/cutover_runs/r<N>-run-plan.json`.
      Pass: exit 0, `RESULT: DRY RUN - nothing written. Exit 0.`
- [ ] **R5** The same plus `--apply --expect-plan /data/cutover_runs/r<N>-run-plan.json`. Pass: exit 0,
      `RESULT: CUT OVER. Exit 0.`, and the printed counts equal R4's plan.
- [ ] **R6** R5 again. Pass: `ALREADY CUT OVER - nothing to do, no backup, no journal`, exit 0, no new backup file,
      no new journal row.
- [ ] **R7** `$B python -m pipeline.v2_cutover verify --stage reset --database alphadeck_v2 --provenance <file>`.
      Pass: exit 0, every check line `ok:`, `V5 recall: N theses, M basket rows, 0 dropped`, and
      `RESULT: VERIFIED. Exit 0.` The file is the `provenance:` path that R5 printed.
- [ ] **R8** The backup restores. On the fork Postgres, create `alphadeck_v2_restorecheck`, restore
      `...-pre-v2-reset.sql` into it, compare the counts of `thesis`, `basket_member`, `calls` and
      `fact_shares_outstanding` with the baseline, then drop that check database. Time the restore. Pass: the
      counts equal the baseline.
- [ ] **R9** `$B python -m pipeline.extract_values`, then `--apply` (in the mode you chose in N4), then
      `$B python -m pipeline.v2_cutover verify --stage backfill --database alphadeck_v2 --provenance <file>`.
      Pass: exit 0. Rehearsal 1 set the rate for these commands with `docker exec -e ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5`.
- [ ] **R10** On the fork's app (port 8083 per the plan): sign in as the user and as the steward. Pass: the Board
      shows live cards with no call; the Scoreboard says "The record starts with the first nightly call."; the
      steward queue is populated; Admin shows `data_tenant_theses` 0 and `platform_rows_in_workspaces` 0.
- [ ] **R11** The first night, by hand (the fork's cron never starts): `$B python -m pipeline.daily --run-kind manual`
      (live). Pass: its output shows no transition push and no digest entry; then
      `$B python -m pipeline.v2_cutover verify --stage nightly --database alphadeck_v2 --provenance <file> --run-kind any`
      exits 0 (`--run-kind any` because a fork's pass is a manual one, not the cron's).
- [ ] **R12** `$B python -m pipeline.backup --label pre-edition-1`, then
      `$B python -m editions.run publish --workspace 00000000-0000-0000-0000-000000000002 --workers 4`, then the same with
      `--apply`, then `verify --stage edition --database alphadeck_v2 --provenance <file>`. Pass: exit 0, Edition 1 published with an entry per live thesis, and
      the Scoreboard defaults to Edition 1.
- [ ] **R13** Rollback drill: run R1 again (it is a V1 restore) and run R2. Pass: R2 reads V1 state again.

**Pass criteria (all must hold).** Every step exits as listed. Every `verify` stage exits 0 with no `FAIL:` line.
The apply's counts equal its dry run. The recall check reports 0 dropped. R6 does nothing. R8's counts equal the
baseline. R11 recorded one row per live thesis. R12 published Edition 1 with an entry per live thesis. Steps R4 to
R9 together fit in 2 hours, which is the production window between step 4 and step 10 with the 21:30 window hours
away.

### Results

**Rehearsal 1.** Run on 2026-11-18 on the fork, on `v2` commit 47fb4514 (the fork was rebuilt at that commit in R0).
It seeded from the newest existing production nightly dump, `alphadeck-20261118T035357.sql` (`--from-latest`, not
`--fresh`; 69 theses, 1906 calls), and kept the fork's own SEC cache (`--no-cache`). The live backfill ran at
`ALPHADECK_SEC_GLOBAL_RATE_PER_SEC=1.5`. An agent ran it without the operator present. It covers R0 to R9 only: R10
(signing in), R11 (the first nightly by hand), R12 (Edition 1) and R13 (the rollback drill) were **not run**. An
earlier run of the same steps on commit ca3f8eb8 (before W6) the same day gave the same move, reset and coverage
counts; its per-step timings were not kept. The log is `v2-cutover-rehearsal-1b-2026-11-18.log` (in `docs/temp/` of
the main checkout, not on this branch). All times are wall times on the fork, a fork measurement, not a promise.

**Rehearsal 2** is still to be run. Leave its column empty until it has.

Timings (for each step, wall time and the commit it ran on):

| Step | Rehearsal 1 | Rehearsal 2 |
|---|---|---|
| Commit tested | 47fb4514 | |
| Production dump used (date) | `alphadeck-20261118T035357.sql` (newest existing nightly dump, 2026-11-18) | |
| R0 rebuild the fork images | 91 s | |
| R1 refresh (drop, restore, migrate) | 160 s | |
| R2 plan | 3 s | |
| R4 dry run | 5 s | |
| R5 apply (backup, archive, transaction) | 389 s in all; the log does not time the parts. The backup's and the archive's file-name stamps are 23:06:06 and 23:12:24 UTC | |
| R6 second run | 3 s | |
| R7 verify reset | 3 s | |
| R8 restore check | 106 s (the restore of the 1.6 GB `-pre-v2-reset` dump) | |
| R9 backfill and verify | backfill 1,531 s (25 m 28 s); verify 5 s | |
| R11 first night and verify | not run | |
| R12 backup, publish, verify | not run | |
| R13 rollback drill | not run | |
| R4 to R9 total | 34 m 19 s by the log's clock (R4 started 18:05:57, the run finished 18:40:16) | |

Outputs and pass record (rehearsal 1 only; the Pass? column is for rehearsal 1):

| Step | Pass? | Output as printed, or the file where it is kept | Rehearsal 1 | Rehearsal 2 |
|---|---|---|---|---|
| R2 plan (exit, surprises) | yes | the log; `/data/cutover_runs/r1b-plan.json` on the fork | exit 1, `RESULT: REFUSE - 1 surprise(s). Exit 1.` (the stray purity row) | |
| R3 negative checks (three exits) | yes | the log | wrong `--database` exit 2; wrong `--confirm` exit 2; `--prod` on the fork exit 2; each `RESULT: REFUSED - nothing read or written. Exit 2.` | |
| R4 dry run (exit, plan lines) | yes | the log; `/data/cutover_runs/r1b-run-plan.json` | exit 0, `RESULT: DRY RUN - nothing written. Exit 0.` (lines in step 6) | |
| R5 apply (exit, counts) | yes | the log | exit 0, `RESULT: CUT OVER. Exit 0.`; counts below; equal to R4's plan | |
| R6 second run | yes | the log | exit 0, `RESULT: ALREADY CUT OVER - nothing to do, no backup, no journal. Exit 0.`; backup files 4 before, 4 after | |
| R7 `verify --stage reset` | yes | the log; `/data/cutover_runs/r1b-verify-reset.json` | exit 0, `RESULT: VERIFIED. Exit 0.`; `V5 recall: 69 theses, 2599 basket rows, 0 dropped` (lines in step 8) | |
| R8 restore counts | yes | the log | restored: `thesis` 69, `basket_member` 2599, `calls` 1906, `fact_shares_outstanding` 2219; the fork after the cutover: 69, 2599, 0, 2219; 0 restore stderr lines | |
| R9 backfill summary, `verify --stage backfill` | yes | the log; `/data/cutover_runs/r1b-verify-backfill.json`; the run artifact `/data/extraction_runs/20261118T231442.495118Z-backfill-a43d35.json` | see below; `verify` exit 0, `RESULT: VERIFIED. Exit 0.` | |
| R10 by-hand checks | not run | | not yet rehearsed | |
| R11 `verify --stage nightly` | not run | | not yet rehearsed | |
| R12 `verify --stage edition` | not run | | not yet rehearsed | |
| R13 rollback drill | not run | | not yet rehearsed | |
| `pg_dump` trailer confirmed on a real dump (Part 9) | yes | the log | the `-pre-v2-reset` dump (1.6G on disk): trailer line count 1 in its last 4,096 bytes; the apply printed `verified (complete, 1704637697 bytes)` | |

Rehearsal 1, the apply (R5), by table:

| Table | Action | Rows |
|---|---|---|
| `fact_shares_outstanding` | moved to the data tenant | 2219 |
| `fact_cash_burn` | moved to the data tenant | 64 |
| `fact_revenue_mix` | moved to the founding workspace | 0 |
| `edition_cause` | reset | 0 |
| `edition_outcome` | reset | 113 |
| `edition_call` | reset | 682 |
| `edition_entry` | reset | 855 |
| `edition_event` | reset | 2 |
| `edition` | reset | 1 |
| `call_annotation` | reset | 176 |
| `calls` | reset | 1906 |

Also printed by R5: `orphan purity: 1 row(s), leave (0 deleted)`, `validated: 2 constraints`,
`companies stamped changed: 1224`.

- Backup: `/data/backups/alphadeck-20261118T230606-pre-v2-reset.sql`, 1,704,637,697 bytes (1.6G on disk).
- Archive: `/data/backups/alphadeck-20261118T231224-v1-history.sql`, 199,017,663 bytes (190M on disk).
- Provenance: `/data/cutover_runs/20261118T230606Z-alphadeck_v2.json`.
- Journal: `data_repair` `b794de1e-d97e-4fe7-b83b-775a4d1ebeb2`.

Rehearsal 1, the backfill (R9). The dry run said: 1,768 due, 2,080 SEC requests known (up to 4,448 with what only
a fetch can settle), at least 1,387 s to 2,965 s at 1.5 requests a second. The live run: 1,768 companies attempted;
1,512 applied; 254 candidates (211 flagged to the steward, 43 nothing found); 2 unchanged; 0 failed; 2,220 live SEC
fetches; 25 m 28 s. By fact: `shares_outstanding` already on file 283, applied 1073, flagged 349, nothing found 52,
older than on file 11; `cash_burn` applied 1255, flagged 414, nothing found 99. Coverage of the companies on a live
card, before and after: share count 63.3% (baseline) to 1395 of 1768 (78.9%); cash/burn 3.2% (baseline) to 1269 of
1768 (71.8%).

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
- **The cron on the first night:** `RUN_AT` stays 22:30 ET; the plan expects the boot catch-up to do nothing when
  the V1 pass ran the last expected night; onboarding runs every 1,800 seconds; the extraction leg takes up to 300
  names in up to 900 seconds; the backup runs after the pass; the weekly jobs run on Saturday.

## 9. Known limits

Read these before the day. Each is something this runbook cannot promise.

- **The nightly window is checked once.** `run` and the backfill read the clock at the start. The backup then
  takes several minutes. Do not start `run --apply` within an hour of 21:30 ET. The cron is stopped during the
  cutover, so no pass will start, but the check is not repeated.
- **The `--prod` path has never run.** It is built and tested against test databases only. The first run
  against the name `alphadeck` is the real one. The rehearsals use the fork's name and cannot exercise `--prod`
  (R3 checks that `--prod` on the fork refuses).
- **The backup check's trailer is confirmed.** The apply accepts a dump only if its last 4,096 bytes hold the line
  `-- PostgreSQL database dump complete`. Rehearsal 1 showed a real 1.6 GB `pg_dump` ends with it (the line was found
  once in the last 4,096 bytes, and the apply printed `verified (complete, 1704637697 bytes)`).
- **R10 to R13 have not been rehearsed.** Rehearsal 1 stopped after R9: nobody signed in, no first nightly ran by
  hand, no Edition 1 was published, and the rollback drill was not done. The stages `verify --stage nightly` and
  `--stage edition`, the `--health-url` check (V10) against a real URL, and the production-only steps (the
  `--prod` path, `deploy.sh`, the cron's first boot, the compose image names) have not been seen to run.
- **No user-made counting catalyst entry existed in the rehearsal.** The `fact_thesis_catalyst` rows it kept were
  W6's conversion of `fact_theme_conviction`, so the recall baseline's ninth child table was never tested against
  entries an operator added.
- **Rehearsal 1 used a same-day nightly dump, not `--fresh`.** Its data is production's as of the nightly backup
  (`alphadeck-20261118T035357.sql`), not a dump taken for the rehearsal. Rehearsal 2 must use `--fresh`.
- **The measured times are fork times.** The fork runs on the same host as production, but production's database is
  larger or equal. The backfill's time depends on the SEC rate setting (1.5 requests a second in the rehearsal).
- **The compose image names and the cron's first boot** are not in the code, so they are marked "not yet rehearsed".
- **A host command with no database setting points at production.** A `python -m pipeline...` command run on the
  host, with no `DATABASE_URL` in its environment, connects to `localhost:5544` and the database `alphadeck`:
  production. This runbook runs every command inside a container for that reason. The cutover guard reads the
  database name from its own connection, not from a URL.
- **Join dates before 2026-09-15** are estimates, and the card-proof tightening that `calls` gave them is lost
  with the reset. Edition 1 uses the estimates.
- **Edition 1's publish takes no lock** (the plan read only the command's dispatch). Run it in daytime.
- **A later change to call logic** (decision N10, W6) changes the engine fingerprint, so the next nightly cut runs
  a trial and, if a rule change is declared, cuts Edition 2. That is expected.

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

**`editions.run publish` exit code on a refusal:** 2.

**Files.**
- Backups: the host's `./data/backups` (`/data/backups` in the containers). Names: `alphadeck-<UTC ts>[-<label>].sql`.
  The ones this runbook makes: `-pre-v2-cutover` (step 4), `-pre-v2-reset` and `-v1-history` (step 7),
  `-post-v2-reset` (step 10), `-pre-edition-1` (step 13).
- Plans and provenance: `/data/cutover_runs/` in the backend container (`prod-plan.json`, `prod-run-plan.json`,
  `<ts>-alphadeck.json`). Copy them off with `docker cp alphadeck-backend-1:/data/cutover_runs/<file> .`
- Provenance keys: `tool`, `database`, `started_at`, `committed_at`, `journal_id`, `backup`, `archive`, `plan`,
  `preflight`, `counts`, and `baseline` (theses, basket members, child counts, `operator_decision`, the security
  ids with a share count or a cash row, the per-tenant counts of the three moved tables, `app_users`, and the
  `plan --json` document). The full list is in the docstring of `backend/pipeline/v2_reset.py`.
- The journal row: table `data_repair`, tool `v2_cutover`, `deletes` true, `provenance` set to the provenance path.
- Code: `backend/pipeline/v2_reset.py` (the apply path), `backend/pipeline/v2_cutover.py` (the commands),
  `backend/pipeline/v2_move.py` (the move), `backend/pipeline/backup.py`, `scripts/deploy.sh`,
  `scripts/refresh-fork.sh`.
- Related pages: `docs/DEPLOY.md`, `docs/FORKS.md`, `docs/V2_BRANCH.md`, `docs/FEED_LOOP.md` (backups and restore),
  `docs/PLATFORM_VALUES.md` (the backfill and decision B4), `docs/SCOREBOARD.md` ("After the V2 reset").
