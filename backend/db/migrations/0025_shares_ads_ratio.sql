-- Alpha Deck — the ADS ratio on a shares-outstanding fact (Retrieval Slice 1 addendum, spec §10).
--
-- WHY: `_market_cap` computes shares × price. An annual foreign filer's cover states ORDINARY shares,
-- while the price feed carries the ADR/ADS price — those only multiply at 1:1. Measured from the
-- filings' own words, ratios run 2:1 up to 120:1 (each ADS representing N ordinary shares), so a raw
-- product overstates the cap N-fold: a $10.9T display for a ~$2.2T company at 5:1. The big ones the
-- operator would catch; a 2x or 5x on a mid-cap they would not — a plausible, silently-wrong number on
-- exactly the figure the extractor design leans on as the human check.
--
--   ads_ratio        — ordinary shares per ADS, N >= 1, when READ from the filing itself
--   ads_ratio_status — 'known'  (ratio read; the cap derivation divides ordinary by N)
--                      'unread' (ADR evidence present but NO defensible ratio — a missing/fractional/
--                                CONFLICTING read; the cap is SUPPRESSED, never guessed at 1:1)
--                      NULL     (not applicable: no ADR evidence — every domestic 10-Q name, every
--                                legacy row — compute at 1:1 exactly as before)
--
-- THE TRAP THIS ENCODING AVOIDS (spec §10.4): "unread" must NOT be encoded as a NULL ratio — every
-- existing row has NULL, so NULL-means-suppress would blank every market cap in the app. NULL/NULL is
-- NOT APPLICABLE -> 1:1, byte-identical to the pre-change derivation; suppression requires the explicit
-- 'unread' stamp written by the annual extract's detector.
--
-- The fact KEEPS the true ordinary count from the cover (changing it would corrupt the fact and divorce
-- it from its located passage); the ratio modulates the DERIVATION in the scorer, never the fact.
--
-- Additive + idempotent; the natural key and the `no_update` trigger are untouched.

ALTER TABLE fact_shares_outstanding ADD COLUMN IF NOT EXISTS ads_ratio        integer;
ALTER TABLE fact_shares_outstanding ADD COLUMN IF NOT EXISTS ads_ratio_status text;
