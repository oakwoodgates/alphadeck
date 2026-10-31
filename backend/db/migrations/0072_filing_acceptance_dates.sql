-- Alpha Deck — filings dated by when they appeared on EDGAR (F7) and share counts given a public date (F9).
--
-- F7 — THE GAP. `fact_corporate_event` (8-K), `fact_activist_stake` (13D/G) and the revenue rows of
-- `fact_fundamentals` stored only SEC's FILING date (`filingDate`; `valid_from = filed`). A filing submitted
-- after 17:30 ET carries the NEXT business day's filing date (Regulation S-T Rule 13(a)(2)) although EDGAR
-- disseminated it that evening, and the detectors gate `valid_from <= asof`, so both the live nightly and every
-- history view counted an evening 8-K a night late (late, never early).
--
-- F7 — THE COLUMN. `accepted` (timestamptz, NULL = not known): SEC's acceptance time, stored only when it is
-- trusted (`ingest.edgar.acceptance.resolve_public_acceptance` — the listed time consistent with the filing date,
-- allowing the evening-before roll, else the filing's own header, else NULL). A row's `valid_from` is then the
-- acceptance's MARKET-TIME date (else, as before, the filing date), and its public instant on history views is
-- the acceptance (`db.public_clock`). Old rows stay NULL — read exactly as before — until
-- `pipeline.repair_filing_dates --apply` appends a re-dated version (never an UPDATE).
--
-- F9 — THE GAP. `fact_shares_outstanding` has no filing-date column: `valid_from` is the cover date, and the
-- public clock used the ratification instant (`recorded_at`) — never early, often months late.
--
-- F9 — THE COLUMNS. `filing_accession` / `filing_accepted`: the filing whose companyfacts cover row carries this
-- count on this date (the earliest such filing — the cited one when the citation is right) and its acceptance.
-- NULL when the count matches no cover row (an operator-typed count): the ratification instant still stands.
--
-- Additive + idempotent: nullable columns, no default, no rewrite of any existing row (each table keeps its
-- `no_update` trigger; nothing here updates a row).

ALTER TABLE fact_corporate_event ADD COLUMN IF NOT EXISTS accepted timestamptz;
ALTER TABLE fact_activist_stake ADD COLUMN IF NOT EXISTS accepted timestamptz;
ALTER TABLE fact_fundamentals ADD COLUMN IF NOT EXISTS accepted timestamptz;

ALTER TABLE fact_shares_outstanding ADD COLUMN IF NOT EXISTS filing_accession text;
ALTER TABLE fact_shares_outstanding ADD COLUMN IF NOT EXISTS filing_accepted timestamptz;
