"""The 0042 legacy stamp — ``calls.reconstructed`` — proven on rows shaped exactly like the log's history.

The runner applies each migration ONCE (``schema_migrations``), so the suite's schema already carries
0042 and the tracked run can never re-fire over post-migration rows. These tests re-execute the FILE's SQL
directly (idempotent by construction: ``ADD COLUMN IF NOT EXISTS`` + a ``NOT reconstructed`` predicate)
over freshly-inserted rows, asserting the exact derived rule the migration stamps by:

    ingest_fresh IS NULL AND (recorded_at AT TIME ZONE 'UTC')::date - asof > 1   ->  reconstructed = true

MEASURED on the dev copy (2026-09-12): 144 rows match, every one at a lag >= 5 days; the 39 honest
pre-R2b nightly rows (also NULL-stamped) all sit at lag 0 or 1. So the rule is exact on the existing
rows, and these fixtures put one row on each side of every leg of it. NON-DESTRUCTIVE: card / state /
verdict / asof / recorded_at are byte-identical after the stamp — the ``no_update`` guard is disabled for
that ONE statement and is proven re-armed afterwards.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID

_SQL = (MIGRATIONS_DIR / "0042_calls_reconstructed.sql").read_text(encoding="utf-8")
_ASOF = date(2026, 8, 7)


def _thesis(db) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "legacy", "x"),
        )
    return tid


def _row(
    db,
    tid: uuid.UUID,
    *,
    lag_days: int,
    ingest_fresh: bool | None,
    reconstructed: bool | None = None,
    state: str = "warming",
) -> uuid.UUID:
    """Insert a calls row via direct SQL with an explicit UTC ``recorded_at`` = asof + ``lag_days``
    (13:00Z, well inside the day under any zone the rule could be read in). ``reconstructed=None``
    leaves the column at its default (a pre-0042 legacy row); ``True`` models a row the NEW backfill
    already marked."""
    cid = uuid.uuid4()
    recorded_at = datetime.combine(_ASOF, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
        days=lag_days, hours=13
    )
    cols = "id, tenant_id, thesis_id, asof, state, verdict, card, recorded_at, ingest_fresh"
    vals = [cid, DEFAULT_TENANT_ID, tid, _ASOF, state, "watching", "{}", recorded_at, ingest_fresh]
    if reconstructed is not None:
        cols += ", reconstructed"
        vals.append(reconstructed)
    with db.cursor() as cur:
        cur.execute(f"INSERT INTO calls ({cols}) VALUES ({', '.join(['%s'] * len(vals))})", vals)
    return cid


def _snapshot(db, tid: uuid.UUID) -> dict[uuid.UUID, dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, asof, state, verdict, card, recorded_at, ingest_fresh, reconstructed "
            "FROM calls WHERE thesis_id = %s ORDER BY seq",
            (tid,),
        )
        return {r["id"]: r for r in cur.fetchall()}


def test_0042_stamps_exactly_the_derived_rule_and_nothing_else(db):
    tid = _thesis(db)
    honest_lag0 = _row(db, tid, lag_days=0, ingest_fresh=None)  # pre-R2b nightly, same day
    honest_lag1 = _row(db, tid, lag_days=1, ingest_fresh=None)  # pre-R2b nightly, next morning
    legacy_lag5 = _row(db, tid, lag_days=5, ingest_fresh=None)  # the backfill's earliest lag
    legacy_lag33 = _row(db, tid, lag_days=33, ingest_fresh=None)  # a mid-August night, run in Sep
    stamped_lag5 = _row(db, tid, lag_days=5, ingest_fresh=True)  # R2b-stamped: NEVER a backfill
    stamped_partial = _row(
        db, tid, lag_days=9, ingest_fresh=False
    )  # a partial ingest, still nightly
    db.commit()
    before = _snapshot(db, tid)
    assert all(r["reconstructed"] is False for r in before.values())  # the column default

    with db.cursor() as cur:
        cur.execute(_SQL)  # the file's own SQL, re-fired over legacy-shaped rows
    db.commit()

    after = _snapshot(db, tid)
    assert {cid: r["reconstructed"] for cid, r in after.items()} == {
        honest_lag0: False,
        honest_lag1: False,
        legacy_lag5: True,
        legacy_lag33: True,
        stamped_lag5: False,
        stamped_partial: False,
    }
    # NON-DESTRUCTIVE: nothing the call SAYS moved — only the provenance flag
    for cid, row in before.items():
        for col in ("asof", "state", "verdict", "card", "recorded_at", "ingest_fresh"):
            assert after[cid][col] == row[col], f"{col} changed on {cid}"
    assert len(after) == len(before) == 6  # COUNT THE TABLE: no row appeared or vanished


def test_0042_is_idempotent_and_never_unstamps_a_new_backfill_row(db):
    """A second run over already-stamped rows changes nothing; a row the NEW backfill wrote at lag 0
    (the morning-after case — the forward hole the derived rule cannot see) keeps its ``true``: the
    file only ever sets the flag, never clears it."""
    tid = _thesis(db)
    legacy = _row(db, tid, lag_days=6, ingest_fresh=None)
    new_backfill_lag0 = _row(db, tid, lag_days=0, ingest_fresh=None, reconstructed=True)
    db.commit()
    with db.cursor() as cur:
        cur.execute(_SQL)
    db.commit()
    first = _snapshot(db, tid)
    assert first[legacy]["reconstructed"] is True
    assert first[new_backfill_lag0]["reconstructed"] is True

    with db.cursor() as cur:
        cur.execute(_SQL)  # second run — a zero-row no-op (NOT reconstructed in the predicate)
        cur.execute("SELECT count(*) AS n FROM calls WHERE thesis_id = %s", (tid,))
        n = cur.fetchone()["n"]
    db.commit()
    assert _snapshot(db, tid) == first
    assert n == 2


def test_0042_leaves_the_no_update_guard_re_armed(db):
    """The trigger is disabled for the ONE stamp statement and re-enabled in the same transaction: after
    the file runs, an UPDATE that would rewrite what a call says still raises."""
    tid = _thesis(db)
    cid = _row(db, tid, lag_days=6, ingest_fresh=None)
    db.commit()
    with db.cursor() as cur:
        cur.execute(_SQL)
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with db.cursor() as cur:
            cur.execute("UPDATE calls SET state = 'armed' WHERE id = %s", (cid,))
    db.rollback()
    with db.cursor() as cur:  # the guard also refuses to un-mark a reconstruction by hand
        cur.execute("SELECT reconstructed, state FROM calls WHERE id = %s", (cid,))
        row = cur.fetchone()
    assert row["reconstructed"] is True and row["state"] == "warming"
