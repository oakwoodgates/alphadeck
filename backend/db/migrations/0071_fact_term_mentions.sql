-- Term-mentions fact family (2026-10-29) — how many EDGAR filings mention a card's SIGNAL terms, per month.
--
-- One row per (tenant, thesis, term, month) VERSION: the EDGAR full-text search count of the documents that
-- match the term's phrase query in that calendar month (``documents``), split by who files them (``reports`` =
-- 10-K / 10-Q / 20-F / 40-F / 6-K, ``events`` = 8-K, ``issuance`` = S-1 / S-3 / F-1 / F-3 / S-11 / 424B* / FWP,
-- ``other`` = the rest, so the four always sum to ``documents``), the distinct filer CIKs it saw (``filer_ciks``,
-- ``filers`` = their count, ``filers_sampled`` = "at least": the reply did not show every filer) and how many of
-- those CIKs no EARLIER stored month of the same card-term had (``new_filers``; NULL when the term has no earlier
-- stored month to compare with — never a fake "all new"). The Narrative Adoption Curve dashboard reads it
-- (``docs/dashboards/narrative-adoption.md``).
--
-- DISPLAY ONLY: no detector, no call, no grade reads it (the accessor is off the detectors' point-in-time
-- protocol, ``signals.base.SignalPointInTimeData``), it is kept OUT of the replay mirror
-- (``db.bitemporal.DISPLAY_ONLY_FACT_TABLES``), it never places a name (discovery is untouched) and no LLM
-- writes it. THE JOB (``pipeline.term_mentions``, weekly in the cron sidecar) appends a new version only when a
-- re-fetched month's values changed (store-on-diff), so a re-run appends nothing.
--
-- KNOWABILITY: valid_from = the month's LAST day (the month's count is a statement about the closed month; the
-- public clock reads ``valid_from``, ``db.public_clock``). recorded_at is the DB's now(), NEVER backdated
-- (invariant #1); fetched_at is when the EFTS reply was read; source_ref is that request's URL (#6).
--
-- THESIS-SCOPED (``db.public_clock.SCOPE_COL``): the as-of read dedups on (term, month) inside its
-- ``WHERE thesis_id = …`` scope (``db.bitemporal._FACT_IDENTITY``); this constraint is that grain + the version
-- axis (recorded_at). No extra index: the job and the page read one card at a time, which the UNIQUE index's
-- (tenant_id, thesis_id, …) prefix already serves.
CREATE TABLE IF NOT EXISTS fact_term_mentions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenant (id),
    thesis_id       uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,  -- the card (not co-located)
    term            text NOT NULL,      -- the SIGNAL term as the card's term set writes it
    month           date NOT NULL,      -- the calendar month, as its first day
    documents       integer NOT NULL,   -- documents matching the phrase query that month
    reports         integer NOT NULL,   -- 10-K / 10-Q / 20-F / 40-F / 6-K (+ amendments, 10-KT / 10-QT)
    events          integer NOT NULL,   -- 8-K (+ amendments)
    issuance        integer NOT NULL,   -- S-1 / S-3 / F-1 / F-3 / S-11 (+ /A, MEF, ASR), 424B*, FWP
    other           integer NOT NULL,   -- documents minus the three named groups
    filers          integer NOT NULL,   -- distinct CIKs in filer_ciks
    filers_sampled  boolean NOT NULL,   -- TRUE = "at least": the reply did not show every filer
    filer_ciks      text[]  NOT NULL,   -- the CIK set ``filers`` counted (later months' new_filers read it)
    new_filers      integer,            -- CIKs no earlier stored month had; NULL = no earlier month stored
    source_ref      text NOT NULL,      -- the EFTS request URL (#6: every row traces to its query)
    fetched_at      timestamptz NOT NULL,  -- when the reply was read
    valid_from      date NOT NULL,      -- = the month's last day (no-lookahead #1)
    valid_to        date,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT term_mentions_month_first CHECK (extract(day FROM month) = 1),
    CONSTRAINT term_mentions_valid_from_last
        CHECK (valid_from = (month + interval '1 month' - interval '1 day')::date),
    CONSTRAINT term_mentions_counts_nonneg
        CHECK (documents >= 0 AND reports >= 0 AND events >= 0 AND issuance >= 0 AND other >= 0),
    CONSTRAINT term_mentions_groups_sum CHECK (reports + events + issuance + other = documents),
    CONSTRAINT term_mentions_filers_set CHECK (filers = cardinality(filer_ciks)),
    CONSTRAINT term_mentions_new_filers
        CHECK (new_filers IS NULL OR (new_filers >= 0 AND new_filers <= filers)),
    UNIQUE (tenant_id, thesis_id, term, month, recorded_at)
);

DROP TRIGGER IF EXISTS no_update ON fact_term_mentions;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_term_mentions
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();  -- append-only (0001's shared guard)
