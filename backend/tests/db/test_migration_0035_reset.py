"""The 0035 honest-authorship RESET, proven on real legacy rows (Discovery cleanup S1).

The runner applies each migration ONCE (``schema_migrations``), so the suite's schema already carries
0035 and the tracked run can never re-fire over post-migration rows. These tests therefore re-execute
the FILE's SQL directly (it is idempotent by construction: ``ADD COLUMN IF NOT EXISTS`` + two narrow
``UPDATE``s) over freshly-inserted LEGACY-shaped rows, asserting the locked reset mapping:

    operator_set    -> signed_off = true,  authored_by = 'system_drafted'   (old accept = ENDORSED)
    operator_edited -> signed_off = false, authored_by = 'system_drafted'   (the accepted relabel)
    system_drafted  -> untouched (signed_off stays the column default false)

NON-DESTRUCTIVE: the content columns (the description / segment / ticker) are byte-identical after
the reset — only the authorship LABEL and the new flag re-base.
"""

from __future__ import annotations

import uuid

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID

_SQL = (MIGRATIONS_DIR / "0035_basket_member_signed_off.sql").read_text(encoding="utf-8")


def _legacy_thesis(db) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "legacy", "x"),
        )
    return tid


def _legacy_member(db, tid: uuid.UUID, ordinal: int, ticker: str, authored_by: str, fit: str):
    """Insert a PRE-0035-shaped row via direct SQL — the repo path would already stamp the new field."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO basket_member"
            " (tenant_id, thesis_id, ordinal, ticker, role, segment, thesis_fit, authored_by)"
            " VALUES (%s, %s, %s, %s, 'r', 'reactors', %s, %s)",
            (DEFAULT_TENANT_ID, tid, ordinal, ticker, fit, authored_by),
        )


def _rows(db, tid: uuid.UUID) -> dict[str, dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT ticker, authored_by, signed_off, thesis_fit, segment"
            " FROM basket_member WHERE thesis_id = %s ORDER BY ordinal",
            (tid,),
        )
        return {r["ticker"]: r for r in cur.fetchall()}


def test_0035_reset_rebases_legacy_authorship_and_preserves_content(db):
    tid = _legacy_thesis(db)
    _legacy_member(db, tid, 0, "ACC", "operator_set", "accepted-era prose")  # the old accept
    _legacy_member(db, tid, 1, "EDT", "operator_edited", "edited-era prose")
    _legacy_member(db, tid, 2, "DRF", "system_drafted", "still-drafted prose")
    db.commit()

    with db.cursor() as cur:
        cur.execute(_SQL)  # the file's own SQL, re-fired over the legacy rows
    db.commit()

    rows = _rows(db, tid)
    # operator_set: the old accept meant ENDORSED -> the flag; the text stays the model's -> the label
    assert rows["ACC"]["authored_by"] == "system_drafted"
    assert rows["ACC"]["signed_off"] is True
    # operator_edited: re-based to model draft (the accepted relabel — legacy edits were test-only),
    # NOT endorsed (editing was never an endorsement)
    assert rows["EDT"]["authored_by"] == "system_drafted"
    assert rows["EDT"]["signed_off"] is False
    # system_drafted: untouched — the ground state
    assert rows["DRF"]["authored_by"] == "system_drafted"
    assert rows["DRF"]["signed_off"] is False
    # NON-DESTRUCTIVE: every content column is byte-identical (only label + flag re-based)
    assert rows["ACC"]["thesis_fit"] == "accepted-era prose"
    assert rows["EDT"]["thesis_fit"] == "edited-era prose"
    assert rows["DRF"]["thesis_fit"] == "still-drafted prose"
    assert all(r["segment"] == "reactors" for r in rows.values())


def test_0035_is_idempotent_and_leaves_post_reset_rows_alone(db):
    """Re-running the file over ALREADY-reset rows changes nothing (the belt-and-braces idempotence the
    migration convention requires) — and COUNT THE TABLE: no row appears or vanishes."""
    tid = _legacy_thesis(db)
    _legacy_member(db, tid, 0, "ACC", "operator_set", "p")
    db.commit()
    with db.cursor() as cur:
        cur.execute(_SQL)
    db.commit()
    first = _rows(db, tid)

    with db.cursor() as cur:
        cur.execute(_SQL)  # second run — a no-op over reset rows
        cur.execute("SELECT count(*) AS n FROM basket_member WHERE thesis_id = %s", (tid,))
        n = cur.fetchone()["n"]
    db.commit()
    assert _rows(db, tid) == first
    assert n == 1  # the table did not grow or shrink


def test_0035_never_touches_term_set_authorship(db):
    """`operator_set` stays LOAD-BEARING for the TERM SET (the "seed" marker) — the reset re-bases
    basket members only; a thesis.term_set entry carrying it is byte-identical after the file runs.
    """
    tid = _legacy_thesis(db)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE thesis SET term_set ="
            ' \'[{"term": "psilocybin", "tier": "signal", "authored_by": "operator_set"}]\'::jsonb'
            " WHERE id = %s",
            (tid,),
        )
        cur.execute(_SQL)
        cur.execute("SELECT term_set FROM thesis WHERE id = %s", (tid,))
        term_set = cur.fetchone()["term_set"]
    db.commit()
    assert term_set == [{"term": "psilocybin", "tier": "signal", "authored_by": "operator_set"}]
