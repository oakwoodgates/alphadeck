"""The `accepted` backfill — NULL-only targeting, append-only re-versioning, idempotency
(count-the-table), the recall-safe residuals (#9), the display/metrics `accepted` clock set WITHOUT moving
the as-of gate (that keys on `recorded_at`, #1), and the 0037 dual-security guard.

The scenario mirrors prod's gap: rows ingested from a filing WITHOUT reading the acceptance datetime are
frozen NULL (``existing_accessions`` never re-parses a stored accession); the backfill re-resolves the
acceptance from the per-CIK submissions JSON and appends a correction version. The source is a FAKE
EdgarClient returning canned submissions (the real client is cache-first over the same shape).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest import CacheMiss
from ingest.edgar.form4 import ingest_form4
from pipeline.backfill_accepted import run_backfill, run_verify

_XML = (
    Path(__file__).resolve().parent.parent / "fixtures" / "edgar" / "form4_sample.xml"
).read_text(
    encoding="utf-8"
)  # two non-derivative txns (P + S) -> two rows per accession

_UTC = timezone.utc


def _subs(rows: list[tuple[str, str]]) -> dict:
    """A submissions JSON whose ``filings.recent`` carries ``(accession, acceptanceDateTime)`` as the
    parallel arrays the backfill reads (all form '4')."""
    return {
        "filings": {
            "recent": {
                "form": ["4"] * len(rows),
                "accessionNumber": [a for a, _ in rows],
                "acceptanceDateTime": [t for _, t in rows],
                "primaryDocument": ["doc.xml"] * len(rows),
                "filingDate": ["2026-01-01"] * len(rows),
            }
        }
    }


class _FakeClient:
    """Duck-types EdgarClient for ``fetch_submissions`` (``get_json``): maps a CIK -> submissions, and
    raises ``CacheMiss`` for an unknown CIK (the uncached / --no-live path -> the scope stays NULL, #9).
    """

    def __init__(self, by_cik: dict[str, dict]) -> None:
        self.by_cik = by_cik

    def get_json(self, url: str, cache_key: str) -> dict:
        m = re.search(r"CIK(\d+)\.json", cache_key)
        cik = str(int(m.group(1))) if m else ""
        if cik not in self.by_cik:
            raise CacheMiss(cache_key)
        return self.by_cik[cik]


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        return cur.fetchone()["n"]


def _latest_accepted(db, accession: str) -> set[datetime | None]:
    """The LATEST version's ``accepted`` per natural key under ``accession`` — the value the as-of read
    serves."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (tenant_id, security_id, accession, insider_name, valid_from, "
            "txn_seq) accepted FROM fact_insider_txn WHERE accession = %s "
            "ORDER BY tenant_id, security_id, accession, insider_name, valid_from, txn_seq, "
            "recorded_at DESC, id DESC",
            (accession,),
        )
        return {r["accepted"] for r in cur.fetchall()}


def _security(db, *, ticker: str, cik: str | None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    db.commit()
    return sid


def test_backfill_corrects_null_only_and_never_rewrites_a_captured_value(db, security_id):
    """acc-null (ingested pre-capture -> accepted NULL) is corrected from submissions; acc-captured
    (already carrying an acceptance) is NEVER rewritten, even when submissions says something else (a
    disagreement is --verify's to report)."""
    captured = datetime(2025, 1, 1, 12, 0, tzinfo=_UTC)
    ingest_form4(db, security_id, _XML, "acc-null")  # accepted NULL
    ingest_form4(db, security_id, _XML, "acc-captured", accepted=captured)  # already captured
    db.commit()
    # security_id's cik is 0001234567 -> normalized '1234567'
    client = _FakeClient(
        {
            "1234567": _subs(
                [
                    ("acc-null", "2025-09-27T18:30:41.000Z"),
                    (
                        "acc-captured",
                        "2099-12-31T00:00:00.000Z",
                    ),  # a DIFFERENT value; must be ignored
                ]
            )
        }
    )
    before = _count(db)  # 4 rows: two per filing (P + S)

    res = run_backfill(db, client=client, execute=True, log=lambda *_: None)

    assert res.scopes_targeted == 1 and res.accessions_resolved == 1
    assert res.rows_corrected == 2 and res.rows_residual_null == 0
    assert (
        _count(db) == before + 2 == res.table_rows_after
    )  # append-only: grew by the corrected rows
    assert _latest_accepted(db, "acc-null") == {datetime(2025, 9, 27, 18, 30, 41, tzinfo=_UTC)}
    assert _latest_accepted(db, "acc-captured") == {captured}  # untouched, no new versions
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n, count(supersedes) AS sup FROM fact_insider_txn "
            "WHERE accession='acc-captured'"
        )
        row = cur.fetchone()
    assert row["n"] == 2 and row["sup"] == 0  # the two originals, no corrections
    with db.cursor() as cur:  # provenance: each correction links the version it supersedes
        cur.execute(
            "SELECT count(*) AS sup FROM fact_insider_txn "
            "WHERE accession='acc-null' AND supersedes IS NOT NULL"
        )
        assert cur.fetchone()["sup"] == 2


def test_backfill_is_idempotent_count_the_table(db, security_id):
    """The convention test: a re-run appends ZERO rows — assert the TABLE COUNT, not the read (the as-of
    read dedups, so a duplicate append would hide behind a correct-looking read)."""
    ingest_form4(db, security_id, _XML, "acc-null")
    db.commit()
    client = _FakeClient({"1234567": _subs([("acc-null", "2025-09-27T18:30:41.000Z")])})

    first = run_backfill(db, client=client, execute=True, log=lambda *_: None)
    count_after_first = _count(db)
    second = run_backfill(db, client=client, execute=True, log=lambda *_: None)

    assert first.rows_corrected == 2
    assert second.scopes_targeted == 0  # nothing left with a latest-version NULL
    assert second.rows_corrected == 0
    assert _count(db) == count_after_first == second.table_rows_after  # the table did not grow


def test_unresolved_stays_null_recall_safe(db, security_id):
    """Recall-safe residuals (#9): an accession out of the recent window stays NULL; a security with no
    CIK stays NULL; a security whose submissions is uncached stays NULL — and the run still corrects the
    correctable one."""
    ingest_form4(db, security_id, _XML, "acc-in-window")  # resolvable
    ingest_form4(
        db, security_id, _XML, "acc-out-of-window"
    )  # NOT in the recent submissions -> NULL
    no_cik = _security(db, ticker="NOCIK", cik=None)
    ingest_form4(db, no_cik, _XML, "acc-nocik")  # security has no CIK -> NULL
    uncached = _security(db, ticker="UNCACHED", cik="0009999999")
    ingest_form4(
        db, uncached, _XML, "acc-uncached"
    )  # CIK present but no cached submissions -> NULL
    db.commit()
    client = _FakeClient({"1234567": _subs([("acc-in-window", "2025-09-27T18:30:41.000Z")])})

    res = run_backfill(db, client=client, execute=True, log=lambda *_: None)

    assert res.scopes_targeted == 3  # DEVCO, NOCIK, UNCACHED
    assert res.scopes_no_cik == 1 and res.scopes_no_submissions == 1
    assert res.accessions_resolved == 1 and res.accessions_unresolved == 1
    assert (
        res.rows_corrected == 2 and res.rows_residual_null == 6
    )  # 3 accessions x 2 rows left NULL
    assert _latest_accepted(db, "acc-in-window") == {datetime(2025, 9, 27, 18, 30, 41, tzinfo=_UTC)}
    assert _latest_accepted(db, "acc-out-of-window") == {None}  # never dropped, never guessed
    assert _latest_accepted(db, "acc-nocik") == {None}
    assert _latest_accepted(db, "acc-uncached") == {None}


def test_dry_run_reports_but_writes_nothing(db, security_id):
    ingest_form4(db, security_id, _XML, "acc-null")
    db.commit()
    client = _FakeClient({"1234567": _subs([("acc-null", "2025-09-27T18:30:41.000Z")])})
    before = _count(db)

    res = run_backfill(db, client=client, execute=False, log=lambda *_: None)

    assert res.rows_corrected == 2 and res.accessions_resolved == 1  # the full report...
    assert _count(db) == before == res.table_rows_after  # ...but not one row written
    assert _latest_accepted(db, "acc-null") == {None}


def test_backfill_sets_the_display_clock_without_moving_the_asof_gate(db, security_id):
    """The backfill populates ``accepted`` (the DISPLAY + metrics "disclosed" clock) but does NOT change
    the as-of read's visibility: that transaction-time no-lookahead gate keys on ``recorded_at`` for every
    fact table (#1, one strict definition). A Form 4 transacted 2025-09-25, accepted 2025-09-27, but
    RE-INGESTED 2026-08-17 is INVISIBLE to a read pinned 2025-10-01 BOTH before and after the backfill
    (recorded_at is 2026 — the row genuinely was not in our store until then). The backfill only fills the
    honest ``disclosed`` date for display/metrics; the count never changes (a correction is a new VERSION,
    not a new fact)."""
    ingested_at = datetime(2026, 8, 17, tzinfo=_UTC)  # the demo's late re-ingest stamp
    ingest_form4(db, security_id, _XML, "acc-mrvl", recorded_at=ingested_at)
    db.commit()
    read = dict(security_id=security_id, asof=date(2026, 12, 1), tenant_id=DEFAULT_TENANT_ID)
    pinned = datetime(2025, 10, 1, tzinfo=_UTC)  # after acceptance, LONG before our ingest

    before_rows = as_of(db, "fact_insider_txn", known_at=pinned, **read)
    assert before_rows == []  # pre-backfill: recorded_at (2026-08-17) > the pin -> invisible

    client = _FakeClient({"1234567": _subs([("acc-mrvl", "2025-09-27T18:30:41.000Z")])})
    run_backfill(db, client=client, execute=True, log=lambda *_: None)

    # the as-of gate keys on recorded_at, NOT accepted: setting accepted does NOT make the buy visible
    # earlier — a display/metrics correction, never a change to what the system had recorded.
    still_blind = as_of(db, "fact_insider_txn", known_at=pinned, **read)
    assert still_blind == []  # recorded_at (2026-08-17) is still > the 2025-10 pin

    # a full-knowledge read sees the two logical facts, now carrying the honest disclosed date (display)
    now_rows = as_of(db, "fact_insider_txn", known_at=datetime.now(_UTC), **read)
    assert len(now_rows) == 2  # no double-count: the correction is a new VERSION
    assert {r["accepted"] for r in now_rows} == {datetime(2025, 9, 27, 18, 30, 41, tzinfo=_UTC)}


def test_verify_cross_checks_stored_values_against_submissions(db, security_id):
    """--verify is the independent gate: 0 mismatches on an honest table; a stored value the enumeration
    disagrees with IS a mismatch; a remaining resolvable NULL is counted (0 for it post-backfill).
    """
    good = datetime(2025, 9, 27, 18, 30, 41, tzinfo=_UTC)
    ingest_form4(db, security_id, _XML, "acc-good", accepted=good)  # stored == submissions
    ingest_form4(
        db, security_id, _XML, "acc-wrong", accepted=datetime(2020, 1, 1, tzinfo=_UTC)
    )  # stored disagrees with submissions
    ingest_form4(db, security_id, _XML, "acc-null")  # stored NULL, submissions has it
    db.commit()
    client = _FakeClient(
        {
            "1234567": _subs(
                [
                    ("acc-good", "2025-09-27T18:30:41.000Z"),
                    ("acc-wrong", "2025-09-27T18:30:41.000Z"),
                    ("acc-null", "2025-09-27T18:30:41.000Z"),
                ]
            )
        }
    )

    res = run_verify(db, client=client, log=lambda *_: None)
    assert res.keys_total == 6 and res.keys_stored_nonnull == 4 and res.keys_compared == 4
    assert res.keys_null_but_resolvable == 2
    assert {(m[0]) for m in res.mismatches} == {"acc-wrong"}  # the two acc-wrong rows

    run_backfill(db, client=client, execute=True, log=lambda *_: None)
    res2 = run_verify(db, client=client, log=lambda *_: None)
    assert res2.keys_null_but_resolvable == 0  # acc-null now captured
    assert {(m[0]) for m in res2.mismatches} == {"acc-wrong"}  # the disagreement still reported


def test_same_filing_under_two_securities_corrects_both_without_collision(db, security_id):
    """The 2026-08-17 abort shape: one filing key sits latest-version-NULL under TWO securities (one
    issuer held as two master rows — same CIK). Both corrected inside ONE batch (shared now()); the 0037
    security-scoped natural key keeps the two same-instant corrections from colliding."""
    sec_b = _security(db, ticker="DEVCO2", cik="0001234567")  # same CIK as security_id
    ingest_form4(db, security_id, _XML, "acc-dual", recorded_at=datetime(2026, 6, 5, tzinfo=_UTC))
    ingest_form4(db, sec_b, _XML, "acc-dual", recorded_at=datetime(2026, 6, 6, tzinfo=_UTC))
    db.commit()
    client = _FakeClient({"1234567": _subs([("acc-dual", "2025-09-27T18:30:41.000Z")])})
    before = _count(db)  # 4 rows: 2 txns x 2 securities

    res = run_backfill(
        db, client=client, execute=True, log=lambda *_: None
    )  # would collide pre-0037
    assert res.rows_corrected == 4 and res.rows_residual_null == 0
    assert _count(db) == before + 4 == res.table_rows_after

    second = run_backfill(db, client=client, execute=True, log=lambda *_: None)  # idempotent
    assert second.scopes_targeted == 0 and _count(db) == before + 4

    for sid in (security_id, sec_b):
        rows = as_of(
            db,
            "fact_insider_txn",
            security_id=sid,
            asof=date(2026, 8, 1),
            known_at=datetime.now(_UTC),
            tenant_id=DEFAULT_TENANT_ID,
        )
        assert len(rows) == 2 and all(r["security_id"] == sid for r in rows)
        assert {r["accepted"] for r in rows} == {datetime(2025, 9, 27, 18, 30, 41, tzinfo=_UTC)}


def test_execute_refuses_loud_on_a_pre_0037_constraint(db, security_id):
    """--execute refuses UP FRONT against the pre-0037 constraint (no security_id) — a SystemExit naming
    0037, before any write — instead of collapsing mid-batch. Teardown force-restores the 0037 shape.
    """
    ingest_form4(db, security_id, _XML, "acc-null")
    db.commit()
    client = _FakeClient({"1234567": _subs([("acc-null", "2025-09-27T18:30:41.000Z")])})
    with db.cursor() as cur:  # regress the constraint, uncommitted
        cur.execute("ALTER TABLE fact_insider_txn DROP CONSTRAINT fact_insider_txn_natural_key")
        cur.execute(
            "ALTER TABLE fact_insider_txn ADD CONSTRAINT fact_insider_txn_natural_key "
            "UNIQUE (tenant_id, accession, insider_name, valid_from, txn_seq, recorded_at)"
        )
    try:
        with pytest.raises(SystemExit, match="0037"):
            run_backfill(db, client=client, execute=True, log=lambda *_: None)
        assert _count(db) == 2  # refused BEFORE any write
    finally:
        db.rollback()
        with db.cursor() as cur:  # force-restore the 0037 shape regardless (idempotent)
            cur.execute(
                "ALTER TABLE fact_insider_txn DROP CONSTRAINT IF EXISTS fact_insider_txn_natural_key"
            )
            cur.execute(
                "ALTER TABLE fact_insider_txn ADD CONSTRAINT fact_insider_txn_natural_key UNIQUE "
                "(tenant_id, security_id, accession, insider_name, valid_from, txn_seq, recorded_at)"
            )
        db.commit()
