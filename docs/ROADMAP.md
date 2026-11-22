# Roadmap

What is open: a decision waiting on the operator, defects that are known and unfixed, work not yet built, and what
is deliberately not planned. It also holds the one statement of how far to trust the calls. Behavior lives in the
owner docs; history lives in git and `backend/domain/rule_log.py`. An idea nobody has committed to is not listed
(`git log -p -- docs/ROADMAP.md` holds the earlier backlog). One line per item, with its owner.

## Trust state

> No dial has been fitted to outcomes. Defaults are priors; the one tuning pass changed nothing; pre-registered
> backtests promoted nothing; the forward record has accrued since 2026-07-10. The nightly pass feeding itself is
> a data loop, not forward validation. Setup strength (wire field `confidence`) is experimental and not a
> probability until matured Scoreboard outcomes support calibration. The Scoreboard's `n >= 5` gate only stops a
> tiny sample being over-read; passing it is not evidence.

The verdict of each pass is in `docs/archive/BACKTEST_PASSES.md`. How a dial may change:
[CALL_LOGIC](CALL_LOGIC.md), "Changing a rule or a dial". The gate: [SCOREBOARD](SCOREBOARD.md), "Setup strength
and the n >= 5 gate". Other docs link here and do not restate it.

## Open decision

- **`revenue_accel_key`.** The dormant dial would move the revenue re-acceleration screen between Key 1 and Key 2.
  A recorded `TriggerRef` carries no key and no catalyst `type`, so a past trigger's key is read from today's
  config: the Trigger Log follows the dial, while Ticker Lookup and Hot Board read the kind sets and ignore it.
  Decide before anyone flips it: record the key on the card (a `CallCard` change with a rule-log entry; every card
  holding a catalyst trigger re-records) or give those pages the config in force each night.
  [CALL_LOGIC](CALL_LOGIC.md), section 1.

## Known defects

Call logic ([CALL_LOGIC](CALL_LOGIC.md), "Known limits"):

- A company in several links counts once per link. Only the lists repeat today, but raising
  `warming_min_entry_triggers` above 1 would let one trigger in two links warm a card. The fix (dedupe by
  `security_id` at the seam) re-records cards.
- A held name moved into a silent link stays Managing, but its own triggers and risks no longer reach the card.
  Whether to exempt it is open.

Access and safety:

- `GET .../extract` and `GET .../etf-holdings` charge the daily allowance, so another site can spend a signed-in
  user's units by navigation ([AUTH](AUTH.md), "The cookie and CSRF"). Make them POST.
- Four repair CLIs (`backfill_accepted`, `backfill_aff10b5_1`, `repair_activist_misattribution`,
  `repair_adr_insider_misattribution` in `backend/pipeline/`) say in `--database-url` help that the default is "the
  dev default". It is prod (`db/session.py`).
- The database refuses `UPDATE` on `calls`, `call_annotation` and the `edition*` tables but not `DELETE`; that
  nothing deletes from them rests on review ([INVARIANTS](INVARIANTS.md), "Known gaps").

Records and deploys:

- `/backtest` labels every run "public clock" while `backtest.run` defaults to the record clock
  ([BACKTEST](BACKTEST.md), "Known limits"). Default the CLI to public, or make the label follow the run.
- `deploy.sh` does not check the editions lock, so a `cron` deploy can kill a hand cut, and `editions.run publish`
  takes no editions lock ([OPERATIONS](OPERATIONS.md), [EDITIONS](EDITIONS.md)).
- `pipeline.run` appends a `calls` row outside the call-of-record lock, with no `reconstructed` mark, and accepts a
  past date ([FEED_LOOP](FEED_LOOP.md), "Known gaps"). Decide whether the CLI should write at all.
- Market Map is not in `warm_dashboards.PAGES`, so its first read after each nightly is cold
  ([market-map](dashboards/market-map.md)).

## Open work

Operations ([OPERATIONS](OPERATIONS.md), [SECURITY](SECURITY.md)):

- **The server move.** Prod runs as the local Compose stack on the operator's machine. A move needs TLS in front
  (hosted `/mcp` and OAuth need an https origin off localhost), the Content-Security-Policy enforced (it is
  report-only), port 8000 unpublished and `set_real_ip_from` set in nginx.
- A durable fix for the 502 after a backend recreate: an nginx `resolver` with a variable `proxy_pass`. Today
  `deploy.sh` restarts the frontend.
- After the rollback window: remove the `v1-pre-v2` image tags and the V1 dumps, then drop `refresh-fork.sh`'s
  platform-facts move and `--v1-state` and retire the `v2_cutover` run and verify tools.
- Retire or keep the sig stack, which duplicates what a fork does and has no branch of its own.

Scoring and calls:

- Setup strength that fades with a conviction's age. The give-back after a signal's peak is a shape problem, not a
  liveness dial ([CALL_LOGIC](CALL_LOGIC.md), section 7).
- Run or retire the registered 52-week and laggard session ladders (`backend/backtest/overlays/`), and measure
  `catalyst_max_horizon_days`, which no pass has tested ([BACKTEST](BACKTEST.md)).
- A new workspace's Edition 1 is published by hand; decide whether to automate it ([EDITIONS](EDITIONS.md)).
- Signal and filing gaps are listed where the code lives, for example the DOE loans award group is not queried,
  large fund or affiliate code-P blocks count as insider buys, a joint 13D filing stores its lead filer only, and a
  spouse's duplicate Form 4 row is not collapsed ([DETECTORS](DETECTORS.md), [FILINGS](FILINGS.md), "Known limits").

Every other doc's "Known limits" lists its own residuals. Vendors looked at and not used: FINRA short interest,
on-chain data, Stooq and Tiingo prices, paid borrow and options data ([DATA_SOURCES](DATA_SOURCES.md)).

## Not planned

- Trade execution, brokerage writes, position sizing and portfolio-risk management: the firm's systems own them
  ([INVARIANTS](INVARIANTS.md), 5).
- Intraday or streaming data, options-gamma and paid borrow-data signals, crypto, commodities and prediction
  markets, a separate graph database.
- Teams or shared workspaces, user-login SSO and impersonation. A private workspace per user is built
  ([AUTH](AUTH.md), [TENANT_SCOPES](TENANT_SCOPES.md)), and so is OAuth sign-in for agent connectors
  ([CONNECTORS](CONNECTORS.md)).
- **Row-level security.** Not planned. Tenant isolation is the scope map and its tests; a raw query that names the
  wrong tenant would leak ([TENANT_SCOPES](TENANT_SCOPES.md), "Known limits").
