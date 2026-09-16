-- Alpha Deck — calls run identity: config_hash + code_sha + run_kind (F1).
--
-- WHY. The record confounds "the facts changed" with "the policy or the code changed." MEASURED on the honest
-- rows: the two largest arm bursts in the record were weekend MANUAL runs on deploy days, not market events
-- (2026-08-16 is a Sunday: 35 new arms across four theses; 2026-08-22 a Saturday: 11 on one), and 2026-08-25
-- carries two honest rows for one thesis with different armed counts. Nothing on the row says any of that:
-- `calls` carried asof / state / verdict / card / recorded_at / ingest_fresh / ingest_errors / reconstructed
-- and NO policy, code or run-kind fingerprint. So no dial change can be evaluated against the record — only
-- forensics can reconstruct what happened, which is not a system.
--
-- THE SHAPE follows `ingest_fresh` (0023) and `reconstructed` (0042) EXACTLY: PROVENANCE that rides the write
-- BESIDE the card, never INSIDE it and never inside `repositories.calls_repo._canonical`'s substance compare.
-- A field IN the card would fake a change and re-record every thesis the first time a dial moved (the
-- expression-reword churn gate). The three columns are written by the same `append` / `record_if_changed`
-- keyword path the other two stamps use, and every scoring read ignores them.
--
--   config_hash — sha256 of the canonical JSON dump of the CallConfig the assembler ran with
--                 (`domain.config.config_hash`; the backtest reuses that helper verbatim, so the lab and the
--                 record fingerprint the same object the same way). 64 lowercase hex chars.
--   code_sha    — the git SHA baked into the image at build time
--                 (Dockerfile `ARG GIT_SHA` -> `ENV ALPHADECK_IMAGE_SHA` -> `Settings.image_sha`). NULL when
--                 unknown — NEVER fabricated: a rebuild that omits GIT_SHA stamps NULL, not a wrong value.
--                 On bind-mounted tiers (dev / sig / fork) this is the IMAGE's sha, which can lag the mounted
--                 code; the Settings field is named `image_sha` so the name cannot overclaim (operator
--                 decision Q19). Prod and cron run the image as built, which is where legibility matters.
--   run_kind    — 'cron' | 'manual' | 'backfill'. `pipeline.daily` takes `--run-kind` (default 'manual'; the
--                 sidecar `scripts/daily_cron.sh` passes 'cron' at every invocation), `pipeline.backfill`
--                 stamps 'backfill' beside the `reconstructed` marker it already sets, and the one-off
--                 `pipeline.run` CLI stamps 'manual'. An explicit flag, never an ambient env var: a hand-run
--                 `docker exec <cron container> python -m pipeline.daily` must not read as the nightly pass.
--
-- NOTHING RETROACTIVE, and that is the honest choice. Legacy rows stay NULL because we do not know what
-- config or code produced them — that IS the finding. No derived stamp (0042 could derive one; here there is
-- nothing to derive from), so there is no UPDATE, so the `no_update` trigger needs no dance and stays armed
-- throughout. An existing as-of's row also keeps its NULLs until its card next genuinely changes; that is
-- correct and must not be read as a bug.
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; the CHECK is dropped-then-added so a re-execution is a
-- clean no-op). The CHECK validates the existing rows on creation — all NULL, so it passes trivially.

ALTER TABLE calls ADD COLUMN IF NOT EXISTS config_hash text;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS code_sha    text;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS run_kind    text;

ALTER TABLE calls DROP CONSTRAINT IF EXISTS calls_run_kind_check;
ALTER TABLE calls ADD CONSTRAINT calls_run_kind_check
    CHECK (run_kind IS NULL OR run_kind IN ('cron', 'manual', 'backfill'));
