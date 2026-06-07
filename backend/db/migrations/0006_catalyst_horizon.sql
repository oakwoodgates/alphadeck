-- #10 / option A: a catalyst's liveness is its relevance HORIZON (the agreement term), decoupled from
-- grade. `horizon_end` is the period-of-performance end from the structured record (e.g. an OKLO DOE
-- OTA -> 2029-07-01); NULL falls back to the configured default horizon. Grade still sets entry SIZE;
-- this only governs how long the catalyst stays live. Additive, append-only table unchanged.

ALTER TABLE fact_catalyst ADD COLUMN IF NOT EXISTS horizon_end date;
