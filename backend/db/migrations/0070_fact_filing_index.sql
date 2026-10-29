-- Filing-index fact family (2026-10-29) — every filing in a basket company's EDGAR index.
--
-- One row per (security, filing) for EVERY form the company's submissions index lists (``filings.recent``:
-- at least a year, or the last 1,000 filings, whichever reaches further): the form as filed, the accession,
-- the filing date, the primary document and the SEC acceptance when it is trustworthy. Stored as a
-- bitemporal, append-only, provenance-carrying fact so the research dashboards (the Filing Stream, the
-- Read-Throughs and Chain Map "last filing") can list an S-1, a 424B5, a DEF 14A, a 10-Q... that no detector
-- reads. DISPLAY ONLY: no detector, no call, no grade reads it (it is off the detectors' point-in-time
-- protocol, ``signals.base.SignalPointInTimeData``) and it is kept OUT of the replay mirror
-- (``db.bitemporal.DISPLAY_ONLY_FACT_TABLES``).
--
-- THE INGEST (``pipeline.ingest_thesis._filing_index_leg``) reads the SAME cached submissions document the
-- Form 4 / 8-K / 13D/G legs read moments earlier, so it costs ZERO extra SEC requests on a normal pass. It is
-- INSERT-ONLY: a stored accession is never re-versioned (first sighting wins). SEC's listed acceptance times
-- drift (``ingest/edgar/acceptance.py``), so re-versioning on them would churn the table nightly; the named
-- residual is that a later correction of a listed field is not picked up.
--
-- KNOWABILITY: valid_from = filed (the EDGAR filing date IS the public date; the public clock reads ``filed``,
-- ``db.public_clock``). recorded_at is the DB's now(), NEVER backdated (invariant #1).
--
-- accepted is the listed SEC acceptance ONLY when its ET date equals the filing date within EDGAR's hours
-- (``ingest.edgar.acceptance.trusted_listed`` — request-free); otherwise NULL (counted by the ingest, never
-- guessed). primary_doc is NULL when EDGAR leaves it blank.
--
-- THE NATURAL-KEY CONSTRAINT CARRIES security_id FROM BIRTH (the 0037 lesson, as 0038's 8-K tape): one logical
-- filing is (tenant, SECURITY, accession) — an issuer held as two master rows (share classes / dual listings)
-- legitimately stores the same filing once per security scope. The as-of read (db/bitemporal.py
-- _FACT_IDENTITY) dedups on accession INSIDE its `WHERE security_id = …` scope; this constraint is that same
-- grain + the version axis (recorded_at).
CREATE TABLE IF NOT EXISTS fact_filing_index (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    security_id  uuid NOT NULL REFERENCES security_master (id),  -- basket-bounded, NOT NULL
    form         text NOT NULL,     -- the form as filed ('S-1', '424B5', '10-Q', '4', 'SC 13G/A', ...)
    accession    text NOT NULL,     -- the filing identity (an accession never changes)
    filed        date NOT NULL,     -- the EDGAR filing date
    primary_doc  text,              -- the primary document's name; NULL when EDGAR leaves it blank
    accepted     timestamptz,       -- the SEC acceptance when trusted (see above); else NULL
    source_ref   text NOT NULL,     -- the EDGAR filing-index URL (#6: every row traces to its filing)
    valid_from   date NOT NULL,     -- = filed (the public date; no-lookahead #1)
    valid_to     date,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, security_id, accession, recorded_at)
);

-- NO separate as-of index (unlike 0038's 8-K tape), on purpose: the as-of read (``WHERE tenant_id = … AND
-- security_id = …``) and the leg's stored-accessions SELECT both use the UNIQUE natural key's (tenant_id,
-- security_id) prefix. MEASURED on a dev copy of prod (2026-10-29, 925,651 rows): the as-of read of the three
-- biggest baskets took 393 / 687 / 442 ms without a (tenant, security, valid_from, recorded_at) index and
-- 417 / 682 / 422 ms with it (the past public-clock view the same), while that index cost 63 MB of the table's
-- 219 MB. The primary key and the natural-key UNIQUE stay.

DROP TRIGGER IF EXISTS no_update ON fact_filing_index;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_filing_index
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();  -- append-only (0001's shared guard)
