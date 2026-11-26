# Feed monitors: what the nightly pass checks and reports

After the per-thesis loop, the nightly pass ([FEED_LOOP](FEED_LOOP.md)) judges its own feeds and its own
record: monitors over the price tapes, the company identities and the filings it just loaded, a self-audit of
the rows it wrote, a run artifact, and one health page. The machine runs them; the steward reads and acts on
the results on the Admin page ([ADMIN](ADMIN.md)). Read this doc before you add a monitor, change what pages,
or change the run artifact.

## Code

| Piece | Module |
|---|---|
| Each monitor | `pipeline/` (the table below) |
| The carried baselines and the run artifact | `pipeline/cron_run_log.py` (`carried_inventory`, `write_cron_run_log`) |
| The self-audit | `pipeline/self_audit.py` (`DEFAULT_CHECKS`) |
| The health page | `pipeline/daily.py` (`assess_health`), `notify/__init__.py` (`HealthEvent`) |
| Admin's verdict, re-derived from the artifacts | `app/routers/admin.py` |

## How it works

### The monitor rule

Every monitor here follows one discipline, and a new one must too:

- **Never a call input.** A monitor imports nothing from `calls/` or `signals/` and neither imports it. It
  cannot fire, arm, veto or grade. Its output rides the run result, the artifact, the
  printed report, the health page and Admin, never the CallCard. Why: a day-varying card field would flap
  `record_if_changed` and break the pass's idempotency.
- **Pure judgment.** The judgment takes the as-of and the facts as parameters; the facts are read by the
  ingest or by one read-only query. Time is never ambient.
- **Fail-open for the pass, loud for the steward.** Each monitor runs on its own connection; a fault becomes
  an `error` on its block, a loud report line and a loud Admin line, and the pass goes on.
- **Page once.** Each monitor pages only what is new since the previous evaluated pass, read from the carried
  artifacts (`cron_run_log.carried_inventory`). A known item is one quiet line. A `--catch-up` that skipped a
  thesis carries that thesis's items from the last pass that covered it, so a catch-up neither re-pages nor
  silences anything.
- **Nothing on `--no-live`.** A cache-only pass makes no monitor fetch and is never a baseline.

### The monitors

| Monitor | Module | Flags | Loudness |
|---|---|---|---|
| Name errors | `pipeline/name_errors.py` | Each name's failed ingest leg, one row per name and leg | Newly failing names page; known ones are a quiet count |
| Stale feeds | `pipeline/tape_health.py`, `domain/feed_kinds.py` | A price tape `tape_stale_days` behind the as-of; an ETF sleeve's fund-shares sample `fund_shares_stale_days` behind | Newly stale pages once |
| Stopped-tape classes | `tape_health.classify_stop`, `ingest/edgar/form25.py` | A stale price tape is closed, renamed, unpriceable or a plain feed gap | Closed is quiet; the others are loud until repaired |
| Expected price misses | `pipeline/price_feed.py` | A vendor 404 on a name classified closed or acknowledged unpriceable | Quiet; removed from the name's errors |
| Rename probe and auto-adopt | `pipeline/rename_probe.py` (`run_rename_probe`, `run_auto_adopt`) | A splice-verified successor symbol for each renamed tape, and a same-class fresh one for an unpriceable name; adopts only for a name the company-list refresh pinned | Recommendation on Admin; one quiet line |
| Successor CIK | `pipeline/successor_cik.py`, `ingest/edgar/succession.py` | A member whose company now files under a new CIK | New ones page once |
| Split-basis defects | `pipeline/split_basis.py` | A stored tape that mixes two split bases (island, cliff, flap); a flap no reader sees is settled and named, never a defect | New ones page once |
| Split-shaped jumps | `pipeline/split_jumps.py` | A jump the split-night hold released with no split listed | Admin Splits panel and one quiet line; never a page |
| Missing closes | `pipeline/missing_closes.py` | A company with no close for the night's session | Quiet nightly; loud on Admin once still missing after the next session's pass; never a page |
| Form 4/A inventory | `pipeline/insider_amendment_scan.py` | Amendments owed a steward decision; amendments the system applied | New owed items page, named; system applications are one quiet count |
| Held insider rows | `pipeline/insider_hold_scan.py` | Rows held for an impossible transaction date, by bucket | New rows the read does not count page; the rest are quiet |
| SEC freshness | `pipeline/edgar_coverage.py` | Fewer than `freeze_completed_floor` of the EDGAR-eligible companies got a completed SEC fetch | Pages (the freeze alarm) |
| Skipped filings | `pipeline/ingest_skips.py` | Filings a leg skipped, diffed against the thesis's last covering pass | A new skip prints its accession; a known one is counted |
| Timings | `pipeline/pass_timings.py`, `ingest/instrument.py` | Where the pass spent its time, requests and memory, by leg and thesis | The `timings` block and one report line |

What the Form 4/A inventory owes the steward, and how the hold buckets are read: [FILINGS](FILINGS.md). How a
split-shaped jump is held on the card: [CALL_LOGIC](CALL_LOGIC.md).

#### Stopped price tapes

A tape whose vendor series stops returns zero new bars and no error, which reads like a market holiday, so
only the recency rule can see it. A stale tape is classified on read from the submissions the ingest already
cached (`classify_stop`, nothing persisted):

- **Closed** (quiet): a SEC delisting form filed within the `tape_closed_before_days` and
  `tape_closed_after_days` window around the last bar, on or before the as-of, and EDGAR lists no other ticker
  for the company. A Form 25 counts only when it removed this class of security; one naming warrants, units,
  rights, notes or preferred is passed over. An unreadable class still counts and prints a warning.
- **Renamed** (loud): EDGAR's current tickers hold neither the master `ticker` nor `price_symbol`. The rename
  probe fetches each candidate once into a throwaway cache and splices it against the stored tape; it is
  verified only on enough shared closes at a ratio near 1, which rejects another class such as warrants. The
  probe writes nothing.
- **Unpriceable** (loud): no stored bar ever, neither closed nor renamed. When EDGAR lists another symbol for
  it, the same probe fetches each once and marks its class by convention (an EDGAR name ending /ADR; the OTC
  fifth letter, Y an ADR, F a foreign ordinary). Only a same-class candidate with a fresh tape is recommended.
  When EDGAR lists no symbol and the company filed a Form 15, the row carries that filing and the 8-K events
  of the 45 days before it (items 1.03, 2.01, 3.03 or 5.01, or an 8-K15D5), worded only with SEC's item titles.
  The row stays loud; removing the name in the basket editor is the recommended action.
- Otherwise a **stale** repair row.

The repair is the steward's, because an automatic rename-follower could file another company's tape under a
member, which is worse than a visible gap. One narrow exception: the weekly company-list refresh pins a renamed
name to its old ticker, and the nightly adopts SEC's new ticker only on an exact splice
([DATA_SOURCES](DATA_SOURCES.md), the company list). A price-leg 404 on a closed or acknowledged name is
expected (`ExpectedPriceMiss`): it leaves the name's errors so `ingest_fresh` stays meaningful, and stays one
quiet line. The fetch still runs for those names, so a relisted name or a vendor that starts serving is
noticed. A renamed name not yet adopted, an unacknowledged unpriceable name and every non-404 failure stay
loud.

#### Known feed limitations

A vendor that serves one bar per pull for a name (no history) gives the price leg no overlap to restate, so a
provisional close is never corrected and a missed night is a permanent hole. The steward can mark such a feed
as a known limitation: an append-only `price_feed_ack` row with `action = 'limited_history'` (migration 0056),
a required reason, and the one vendor pull that evidenced it. The endpoint refuses a feed that returns no bars
or more than a handful. It is a separate action value because every quieting reader selects
`action = 'acknowledge'`, so a limitation can never make a stale row quiet or excuse a 404. Only two readers
note it: the named-bar repair (`pipeline/repair_benchmark_bars.py`) labels an unrepairable bar as a known
limitation, and the Cockpit name panel shows a quiet "price history limited" note (display only).

OTC closes often arrive a night late: at `RUN_AT` the vendor's candle for the day can carry a null close,
which the parser skips, and the next pull gets it. The missing-close monitor marks a company the master lists
on the OTC venue "OTC · known issue" instead of the usual-late marker. Logged, not fixed.

### The self-audit

At the end of a pass, after the loop has committed, `pipeline/self_audit.py` runs `DEFAULT_CHECKS`. Each
returns ok, failed or not applicable; not applicable is never read as a pass. A failed check pages
(`HealthEvent.audit_failed`) and Admin re-reads the list from the artifact:

| Check | Holds when |
|---|---|
| `on_time_rows` | Every thesis on the night's roster has a non-reconstructed `calls` row for the as-of recorded at or after `RUN_AT`. It counts the table, never the pass's own results (`night_coverage`) |
| `code_sha_known` | The pass's `code_sha` is known |
| `every_thesis_finished` | Every thesis the pass started reached its outcome, appended or unchanged; a `--no-live` withhold counts as finished |
| `split_defects_flagged` | The split-basis scan ran. It reports the defect count and fails only when the scan could not run |
| `no_unsettled_bars` | No `fact_price_eod` row for day d was recorded before d's settle instant, judged over the rows recorded since the previous judging pass |
| `splits_applied` | Every split in force on the list is applied to its stored tape. A recent unapplied split (never applied, or undone by a vendor revert) fails; an older one is the repair backlog, named and passed. Each name carries its own command, `python -m pipeline.repair_split_basis --ticker X` (dry run, then `--apply`). A split the tape cannot judge is named "not verified" |

A new check is a pure `check_x(ctx: AuditContext) -> AuditResult` appended to `DEFAULT_CHECKS`.

### The health page

`assess_health` (`pipeline/daily.py`) builds one `HealthEvent` per pass (`notify/__init__.py`); a healthy pass
sends nothing. It goes to the env notifier, never a workspace's webhook. Two classes:

| Class | What | Admin verdict |
|---|---|---|
| Alarm | The pass crashed; SEC data froze (zero completed EDGAR fetches, or below the ratio floor); a total ingest failure withheld a call; a thesis errored; the benchmark refresh failed; a self-audit check failed | `unhealthy` |
| Benign | A `--no-live` withhold; newly failing names; newly stale feeds; new split-basis defects; new successor CIKs; new owed Form 4/As; new held insider rows | Unchanged: the run did its job and a feed needs a repair |

Alarms come first on the page. The freeze verdict reads completed fetches (a 2xx body,
`EdgarClient.live_completed`) per company across the pass, because each thesis has its own client and a
company fetched minutes earlier is a cache hit for the next thesis. A company another writer held, or with no
CIK, is not eligible. A pass that dies outside the per-thesis isolation sends a crash page from `main` and
leaves its start artifact. A missing pass is the sidecar heartbeat's to catch ([CRON_SIDECAR](CRON_SIDECAR.md)).

## Contracts

### The run artifact

Each pass writes `data/cron_runs/<asof>_<started>.json` (`pipeline/cron_run_log.py`): `running` at start,
`finished` at the end, with every leg's block, the per-thesis results, `audit` and `timings`. A pass killed
before it finishes leaves `running`, or `crashed` when the pass caught its own exception; Admin and the
catch-up guard never read either as a finished night. The artifact is the baseline every monitor diffs
against and the record Admin re-derives its verdict from. It holds counts, ids, tickers and accessions,
never a thesis name or card content.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| The monitors, self-audit, artifact and health page | Machine (the nightly pass) | Run only inside `run_daily_pass` |
| `GET /admin/status`: the cron verdict, freshness, feeds, monitors, missing closes | Steward | `require_super_admin` on the admin router (`app/main.py`); `tests/app/test_auth_gate.py` |
| Price feed repairs: `/admin/price-feed/...` adopt, set-symbol, acknowledge, limited-history, adopt-cik and their undo or revoke | Steward | Same |
| Splits panel: `/admin/splits/...` confirm (a source's or cited values), reject, revoke, restore, jump dismiss and undo | Steward | Same |
| Form 4/A and held-row queues: `/admin/insider-amendments/...`, `/admin/insider-holds/...` | Steward | Same |
| The Cockpit data flag and the "price history limited" note | Any signed-in user, for their workspace's theses | `require_user`; display only |

Every steward decision here is an append-only row with an undo; nothing is applied by the pass.

## Settings

| Knob | Owner | Passed by compose to cron? |
|---|---|---|
| `ALPHADECK_TAPE_STALE_DAYS`, `ALPHADECK_FUND_SHARES_STALE_DAYS` | `domain/settings.py` | No |
| `ALPHADECK_TAPE_CLOSED_BEFORE_DAYS`, `ALPHADECK_TAPE_CLOSED_AFTER_DAYS` | `domain/settings.py` | No |
| `ALPHADECK_FREEZE_COMPLETED_FLOOR` | `domain/settings.py` | No |

## Known limits

- The ratio floor pages when a freeze is partial, but a catch-up running right behind a killed pass reads the
  killed pass's fetches as cache hits and can page once. A repeat page, never a silenced freeze.
- A close the vendor never serves while later bars keep arriving stays loud indefinitely, because the tape
  never reads stale. The fix is a decision: a known-limitation mark or an age-out.
- The `--adopt` CLI and the master's finalize self-heal write `price_symbol` without a `price_symbol_change`
  row, so an Admin Undo refuses rather than clobber what they wrote.
- Lone downward cliffs are never flagged as split-basis defects: a crash on heavy volume has the same shape as
  an unadjusted forward split. The flap check and `splits_applied` still catch a listed one.
- After a successor CIK is adopted, the card's filing links are built from the current CIK
  (`schemas_api.edgar_url`), so an older filing's link names the successor's CIK. EDGAR resolves the filing by
  accession whatever the CIK segment, so the link works; only the segment is wrong.

## Related

[FEED_LOOP](FEED_LOOP.md), [INGEST](INGEST.md), [CRON_SIDECAR](CRON_SIDECAR.md), [ADMIN](ADMIN.md),
[FILINGS](FILINGS.md), [DATA_SOURCES](DATA_SOURCES.md), [INVARIANTS](INVARIANTS.md).
