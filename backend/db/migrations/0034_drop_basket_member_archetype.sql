-- Alpha Deck — DROP basket_member.archetype (Business-Type M1, S4 — the archetype retirement).
--
-- The size-derived archetype (leader/high_beta/lotto + shovel/adjacent/fund) was never adopted:
-- measured live 2026-08, 486/494 members carried NULL and the 8 stored values were demo-seed
-- artifacts (pipeline.seed wrote 7; the one 'fund' member's security already carries
-- instrument_kind='etf', which every fund gate keys on — nothing loses its information). The two
-- axis-borrowers were repointed before this drop: 'fund' -> security_master.instrument_kind;
-- 'adjacent' (off-thesis) -> the purity meter's existing off-thesis read. What replaced the rest is
-- the two-level BUSINESS TYPE: master-level, derive-on-read from the editable maps in
-- backend/securities/business_type/ (+ the 0033 re-tag column) — durable identity, not a per-thesis
-- spine field, so the spine column goes rather than lingering as a second home for the same metadata
-- (the two-homes trap the conviction naming guard exists for).
--
-- DESTRUCTIVE by design, with operator sign-off (ruling Q6, 2026-08): the 8 seed-era values are
-- dropped; a re-seed reproduces none (seed.py no longer writes the column). Idempotent via IF EXISTS.

ALTER TABLE basket_member DROP COLUMN IF EXISTS archetype;
