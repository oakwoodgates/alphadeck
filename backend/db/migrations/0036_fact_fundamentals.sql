-- Fundamentals fact family (§2.2) — the quarterly financial series behind the revenue/earnings
-- ACCELERATION inflection detector (the structural conviction behind the 5–10x breakout).
--
-- A provenance-carrying, bitemporal, append-only fact family modelled on fact_catalyst (0005) /
-- 0009's scoring facts: a correction/restatement is a NEW row (never an UPDATE — the no_update trigger),
-- and the as-of / replay reads stay honest. NEVER a model number — the value is a DETERMINISTIC
-- companyfacts (XBRL) parse (invariant #3); the LLM never sources it.
--
-- Designed EXTENSIBLE by row, not by column (§2.4, deferred: EPS / gross-margin / FCF): the metric is a
-- (metric_key, value, unit) triple, so a later margin/FCF metric adds ROWS under a new metric_key — no
-- migration, no column. period_end is the fiscal-period-end DATA column the detector reads for its YoY math.
--
-- KNOWABILITY (R17 / plan A.2 — the load-bearing trap): a period's fact becomes visible EXACTLY when it
-- was filed, never at the period end and never "today". The ingest/backfill stamps
--   valid_from = recorded_at = the 10-Q/10-K acceptance (`filed`) date
-- (companyfacts carries `filed`/`accn` per fact), so a period ending 2023-03-31 filed 2023-05-10 is
-- INVISIBLE to an as-of read at any T in [2023-03-31, 2023-05-10) and VISIBLE at T >= 2023-05-10. This is
-- what makes "what would §2.2 have fired in 2023?" honest and invariant-#1-clean (no lookahead).
CREATE TABLE IF NOT EXISTS fact_fundamentals (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenant (id),
    security_id   uuid NOT NULL REFERENCES security_master (id),
    metric_key    text NOT NULL,                 -- 'revenue' (v0); EPS/margin/FCF later (§2.4) — rows, not columns
    period_end    date NOT NULL,                 -- the fiscal-period END — the DATA column the detector reads (YoY)
    fiscal_period text,                          -- 'Q1'|'Q2'|'Q3'|'Q4' (companyfacts fp, or 'Q4' derived) — aid
    fiscal_year   integer,                       -- companyfacts fy (or the FY's year for a derived Q4) — aid
    value         numeric NOT NULL,              -- the metric value for the period (USD revenue)
    unit          text NOT NULL DEFAULT 'USD',
    basis         text NOT NULL,                 -- 'native' (a standalone quarterly XBRL fact) | 'derived' (FY − 9moYTD)
    accession     text NOT NULL,                 -- the 10-Q/10-K accession (companyfacts accn) — provenance (#6)
    source        text NOT NULL DEFAULT 'companyfacts',
    valid_from    date NOT NULL,                 -- KNOWABILITY: the filing acceptance (`filed`) date — NEVER today
    valid_to      date,
    recorded_at   timestamptz NOT NULL DEFAULT now(),  -- the honest backfill stamps this = `filed` too
    supersedes    uuid REFERENCES fact_fundamentals (id),
    -- Natural key for the as-of dedup is (tenant, security, metric_key, period_end): a RESTATEMENT of a
    -- period is a NEW VERSION (a later recorded_at wins the DISTINCT ON), not a duplicate. recorded_at is in
    -- the UNIQUE so the original filing and a later restatement (different `filed` -> different recorded_at)
    -- coexist as versions, while a re-ingest of the SAME filing can never grow the table (count-the-table).
    UNIQUE (tenant_id, security_id, metric_key, period_end, recorded_at)
);
CREATE INDEX IF NOT EXISTS ix_fundamentals_asof
    ON fact_fundamentals (tenant_id, security_id, valid_from, recorded_at);
DROP TRIGGER IF EXISTS no_update ON fact_fundamentals;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_fundamentals
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();  -- reuses the shared guard from 0001 (append-only)
