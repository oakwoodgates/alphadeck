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
snapshot, kill criteria, evidence, exclusions, thesis catalysts, term mentions, `fact_theme_conviction`,
`operator_decision`, positions, users, sessions, tokens, tenants, and every market, filing and price fact. The
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
| N2 | When to drop the empty `call_annotation` table | (a) keep the empty table, drop it later by migration 0212; (b) drop it at cutover | nothing in the cutover | (a): the table stays | (a) | |
| N4 | The first backfill | (a) live, now; (b) cache only (`--no-live`), live later | `--apply` or `--apply --no-live` on `pipeline.extract_values` | (a) live. On the fork, the cache-only run failed 1,016 companies with "not cached", so (b) leaves most companies with no value yet | (a) | |
| N5 | The first V2 night | (a) accept: names already armed at cutover are on the Board but never pushed; (b) one summary push listing what is armed (a new CLI, not built); (c) change the push rule (call-logic work, not proposed) | none | (a) | (a), and open the Board the next morning | |
| N6 | Edition 1 | For the existing workspace: (a) publish after the first night, or (b) skip. For later workspaces: (c) leave on Edition 0, or (d) auto-publish (not built) | the Edition 1 step (Part 4, step 13); (d) would need code | (a) and (c) | (a) and (c) | |
| N7 | Caps on the existing workspaces | (a) leave them unset (no cap); (b) set them | `python -m pipeline.users caps` (any time) | (a): NULL means no cap | (a) | |
| N8 | In-flight user state (decisions, positions) | (a) keep; (b) reset | the only built option is (a); the dry run prints the counts | (a) | (a) | |
| N9 | A separate archive of V1's history | (a) take it; (b) the full backup is enough | `--no-history-archive` on `run` | (a): archive taken | (a) | |
| N10 | Cut over before or after W6 (catalyst conversion) | (a) after W6 is merged and rehearsed; (b) before | the go-live date; no code | none: it is a date choice | (a) | |
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
      in the plan's measurement (estimate: about 7 GB in all). Check the free space where `./data/backups` lives.
- [ ] Production `.env` (open it yourself; this page never shows it) has, by name: `ALPHADECK_USER_AGENT` (the
      backfill refuses a live run without it) and `ALPHADECK_OPS_TOKEN` (`deploy.sh` reads it; `docs/DEPLOY.md`).
      Also check any `ALPHADECK_ALLOWANCE_*` override, and the variable changes in Part 8.
- [ ] You are on the main checkout, the tree is clean, and you have Git Bash open (not PowerShell's `bash`, which
      is WSL).
- [ ] It is a weekday morning or another time that leaves hours before 21:30 ET. Do not start within an hour of
      21:30 ET (see Part 9, "Known limits").
- [ ] You have the time. The plan's estimate for steps 1 to 10 is a few hours, mostly the backfill (about 30
      minutes), two or three backups (about 6 minutes each) and the builds. Rehearsal 2 replaces these estimates.
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
- Durations marked **estimate** come from the plan (a measured backup on a production-size dump, or a guess) and
  stay estimates until a rehearsal times them. Part 7 has a table to fill in.
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

*You should see.* Three `v1-pre-v2` tags in `docker images`. **To be filled in by the rehearsal:** the image
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
the backend boots it applies the V2 migrations (0200 to 0211 per the plan; check the boot log). **This backup
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
That is expected: it counts platform rows still under a workspace. **To be filled in by the rehearsal:** the
exact output of a real run, and whether this Admin line trips `deploy.sh`'s alarm comparison (the plan lists it
as a risk it could not confirm; it checks that `cron.alarms` did not grow).

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
differ. **To be filled in by the rehearsal:** the production-shaped output.

*Duration.* Under a minute (estimate).

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
| `count: P9 kept: operator_decision n, theses with position fields n, fact_theme_conviction n` | counts only; never a refusal |
| `ok: P11 only the reset tables reference the reset tables` | so no delete can reach another table |
| `ok: P10 not cut over yet` | |
| `plan: move fact_shares_outstanding n row(s) (...)`, `plan: move fact_cash_burn ...`, `plan: move fact_revenue_mix ...` | what will move, by `ratified_by` |
| `plan: reset calls n row(s)` and one line for each of the other seven tables | what will be deleted |
| `plan: orphan purity n row(s) (leave)` | |
| `plan-out: /data/cutover_runs/prod-run-plan.json` | |
| `backup: would take a new 'pre-v2-reset' backup and the V1 history archive` | |
| `RESULT: DRY RUN - nothing written. Exit 0.` | |

Read the plan lines. Do they match your expectation (the number of share-count and cash/burn rows to move, the
number of calls to delete, the count of kept decisions and positions)? **To be filled in by the rehearsal:** a
full sample of this output on production-shaped data.

*Duration.* Under a minute (estimate).

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

*Duration.* Backup about 6 minutes (measured on a production dump); archive about 1 minute (estimate);
transaction under a minute (estimate). The nightly-window check happens once, at the start (see Part 9).

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
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage reset --provenance /data/cutover_runs/<ts>-alphadeck.json --health-url http://localhost:8000/health
```

*What it does.* The `verify` command is built by a separate change (PR W10b) and is not in this runbook's
branch. Per the plan, it reads the database without writing, and checks: no surprise beyond the acknowledged
one; every migration applied and the 0206 constraints validated; no thesis or user on the data tenant and an
enabled `super_admin` user; every thesis unchanged against the baseline; **recall**: every basket member of
every thesis is still there (a failure names the thesis id and each missing security id); the child counts and
`operator_decision` unchanged; no share-count or cash/burn row left outside the data tenant; the eight tables
empty; exactly one `v2_cutover` journal row that matches the provenance; and that `--health-url` answers 200.

*You should see.* **To be filled in by the rehearsal (PR W10e).** The plan expects each check to print `ok:`, a
recall line of the form "N theses, N basket rows, 0 dropped", and `RESULT: VERIFIED. Exit 0.`

*Then, by hand.* No script signs in for you. Open the app. Sign in as the user and as the steward. Check: the
Board shows the live cards with no call; the Scoreboard reads "The record starts with the first nightly call.";
the steward queue is populated; the Admin page shows `data_tenant_theses` 0 and `platform_rows_in_workspaces` 0.

*Duration.* Under a minute for `verify`, then your own time (estimate).

*If it fails.* Any `FAIL:` line: stop. Do not start the cron. Read the line. If you cannot explain it and fix it
without writing to the database, roll back (Part 5).

### Step 9. The extraction backfill

```bash
docker exec alphadeck-backend-1 python -m pipeline.extract_values
docker exec alphadeck-backend-1 python -m pipeline.extract_values --apply
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage backfill
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
`verify --stage backfill`. **To be filled in by the rehearsal:** the summary and the verify output. The plan
expects `verify` to find, for each non-ETF company on a live card, at least one `backfill` attempt, and a `done`
`backfill` attempt to prove B4.

*Duration.* About 30 minutes (the plan's fork measurement; not re-measured; about 2,600 SEC requests).

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
nothing (the V1 pass ran last night) and `onboard-cron: every 1800s`. **To be filled in by the rehearsal.**
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
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage nightly
```

*What it does.* Per the plan: one call row per live thesis for the newest night, none reconstructed, the run
kind `cron`, none dated before the cutover, none under the data tenant. *You should see:* **to be filled in by
the rehearsal (PR W10e).** The plan expects `RESULT: VERIFIED. Exit 0.`

Also open the Board and the Admin page (cron status and alarms, the steward queue).

*If it fails.* A missing row for a live thesis: read the cron log and the Admin page. Do not publish Edition 1
(it needs a recorded night).

### Step 13. Publish Edition 1 (decision N6)

```bash
docker exec alphadeck-backend-1 python -m pipeline.backup --label pre-edition-1
docker exec alphadeck-cron-1 python -m editions.run publish --workspace 00000000-0000-0000-0000-000000000002 --workers 4
docker exec alphadeck-cron-1 python -m editions.run publish --workspace 00000000-0000-0000-0000-000000000002 --workers 4 --apply
docker exec alphadeck-backend-1 python -m pipeline.v2_cutover verify --stage edition
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
`edition_event`, `edition_entry`, `edition_call` and `calls`. `verify --stage edition`: **to be filled in by the
rehearsal (PR W10e).** The plan expects exactly one edition, number 1, with an entry for every live thesis, and the
Scoreboard defaulting to Edition 1.

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

*How long.* The restore of a plain dump of about 1.7 GB is not measured: the plan's estimate is 15 to 30 minutes,
and rehearsal step R1 times it (`refresh-fork.sh` runs the same restore). **To be filled in by the rehearsal.**

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
  platform values and press **Extract now** (`docs/ADMIN.md`, `docs/PLATFORM_VALUES.md`). To be filled in by the
  rehearsal: which companies on production this affects, and whether the backfill reports them.
- **Leftover table (N2).** `call_annotation` stays as an empty table. It can be dropped later by a migration
  (0212 is the next free number, the plan says), with its entries in the scope map, in `CUTOVER_ACTION` and in
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
      tables.

**The run (time every step)**
- [ ] **R1** `bash scripts/refresh-fork.sh --reset --fresh --v1-state`. `--fresh` takes a read-only dump of
      production first; `--v1-state` skips the platform-facts move, so the fork keeps V1-shaped data. Pass: the last
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
- [ ] **R7** `$B python -m pipeline.v2_cutover verify --stage reset --provenance <file>`. Pass: exit 0, no
      `FAIL:` line, recall "0 dropped".
- [ ] **R8** The backup restores. On the fork Postgres, create `alphadeck_v2_restorecheck`, restore
      `...-pre-v2-reset.sql` into it, compare the counts of `thesis`, `basket_member`, `calls` and
      `fact_shares_outstanding` with the baseline, then drop that check database. Time the restore. Pass: the
      counts equal the baseline.
- [ ] **R9** `$B python -m pipeline.extract_values`, then `--apply` (in the mode you chose in N4), then
      `$B python -m pipeline.v2_cutover verify --stage backfill`. Pass: exit 0.
- [ ] **R10** On the fork's app (port 8083 per the plan): sign in as the user and as the steward. Pass: the Board
      shows live cards with no call; the Scoreboard says "The record starts with the first nightly call."; the
      steward queue is populated; Admin shows `data_tenant_theses` 0 and `platform_rows_in_workspaces` 0.
- [ ] **R11** The first night, by hand (the fork's cron never starts): `$B python -m pipeline.daily --run-kind manual`
      (live). Pass: its output shows no transition push and no digest entry; then
      `$B python -m pipeline.v2_cutover verify --stage nightly --run-kind any` exits 0.
- [ ] **R12** `$B python -m pipeline.backup --label pre-edition-1`, then
      `$B python -m editions.run publish --workspace 00000000-0000-0000-0000-000000000002 --workers 4`, then the same with
      `--apply`, then `verify --stage edition`. Pass: exit 0, Edition 1 published with an entry per live thesis, and
      the Scoreboard defaults to Edition 1.
- [ ] **R13** Rollback drill: run R1 again (it is a V1 restore) and run R2. Pass: R2 reads V1 state again.

**Pass criteria (all must hold).** Every step exits as listed. Every `verify` stage exits 0 with no `FAIL:` line.
The apply's counts equal its dry run. The recall check reports 0 dropped. R6 does nothing. R8's counts equal the
baseline. R11 recorded one row per live thesis. R12 published Edition 1 with an entry per live thesis. Steps R4 to
R9 together fit in 2 hours, which is the production window between step 4 and step 10 with the 21:30 window hours
away.

### Results

**To be filled in by PR W10e.** Leave every cell empty until a rehearsal has run.

Timings (for each step, wall time and the commit it ran on):

| Step | Rehearsal 1 | Rehearsal 2 |
|---|---|---|
| Commit tested | | |
| Production dump used (date) | | |
| R1 refresh (drop, restore, migrate) | | |
| R2 plan | | |
| R4 dry run | | |
| R5 apply (backup, archive, transaction) | | |
| R6 second run | | |
| R7 verify reset | | |
| R8 restore check | | |
| R9 backfill and verify | | |
| R11 first night and verify | | |
| R12 backup, publish, verify | | |
| R13 rollback drill | | |
| R4 to R9 total | | |

Outputs and pass record:

| Step | Pass? | Output as printed, or the file where it is kept | Rehearsal 1 | Rehearsal 2 |
|---|---|---|---|---|
| R2 plan (exit, surprises) | | | | |
| R3 negative checks (three exits) | | | | |
| R4 dry run (exit, plan lines) | | | | |
| R5 apply (exit, counts) | | | | |
| R6 second run | | | | |
| R7 `verify --stage reset` | | | | |
| R8 restore counts | | | | |
| R9 backfill summary, `verify --stage backfill` | | | | |
| R10 by-hand checks | | | | |
| R11 `verify --stage nightly` | | | | |
| R12 `verify --stage edition` | | | | |
| R13 rollback drill | | | | |
| `pg_dump` trailer confirmed on a real dump (Part 9) | | | | |

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

- **The backup check's trailer is confirmed only on sample text.** The apply accepts a dump only if its last
  4,096 bytes hold the line `-- PostgreSQL database dump complete`. That is the standard `pg_dump` trailer, but no
  test may run `pg_dump`, so the code has never been shown a real dump. If rehearsal 1 shows a real dump lacking
  that line, the apply would refuse every backup (exit 3), and a code fix is needed. Rehearsal 1 confirms or
  refutes this; record the result in Part 7.
- **The nightly window is checked once.** `run` and the backfill read the clock at the start. The backup then
  takes several minutes. Do not start `run --apply` within an hour of 21:30 ET. The cron is stopped during the
  cutover, so no pass will start, but the check is not repeated.
- **The `--prod` path has never run.** It is built and tested against test databases only. The first run
  against the name `alphadeck` is the real one. The rehearsals use the fork's name and cannot exercise `--prod`
  (R3 checks that `--prod` on the fork refuses).
- **`verify` is not in this runbook's branch.** Its command lines (`--stage`, `--provenance`, `--health-url`, `--run-kind`) are written as the plan gives them.
  Its flags and output are unconfirmed here (to be filled in by PR W10e after the rehearsals).
- **The compose image names and the cron's first boot** are not in the code, so they are marked "to be filled in".
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
