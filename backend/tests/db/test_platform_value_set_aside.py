"""V2 W2a: an UNDONE steward approve / correct sets its fact version aside on every read (``db.set_aside``).

The steward decision table (``platform_value_decision``, migration 0205) is the second source of set-asides: the
fact version written by an approve or a correct that a later ``undo`` names is skipped by every read of
``fact_shares_outstanding`` / ``fact_cash_burn`` (the live record read, the basket prefetch, the public-clock
history read, and the replay mirror on both clocks), so the read falls back to the version it replaced.

THE TRAP this file pins first: a decision that wrote no fact (a dismiss, an undo) has a NULL ``fact_id``, and
``id NOT IN (<a list holding NULL>)`` is never true, so a registered SQL that let a NULL through would blank EVERY
read of both tables. ``test_an_undone_dismiss_does_not_blank_the_read`` is that case.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Facts and decisions are inserted by SQL
or the ingest functions: no steward writer exists yet (W2c).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

import pyarrow.parquet as pq
import pytest

from db.bitemporal import as_of, as_of_many
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.shares import ingest_shares_outstanding
from replay.export import export_snapshot

_KNOWN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_ASOF = date(2026, 11, 1)
_REF = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/q2.htm"
_STEWARD = "steward@example.test"

TABLES = {"shares_outstanding": "fact_shares_outstanding", "cash_burn": "fact_cash_burn"}


def _write(db, sid: UUID, fact_type: str, value: float, *, recorded_at: datetime) -> UUID:
    """One version of the ``_REF`` filing's value, dated 2026-06-30, under the data tenant."""
    if fact_type == "shares_outstanding":
        return ingest_shares_outstanding(
            db,
            sid,
            shares=value,
            source="cover",
            source_ref=_REF,
            event_date=date(2026, 6, 30),
            ratified_by="auto",
            tenant_id=DATA_TENANT_ID,
            recorded_at=recorded_at,
        )
    return ingest_cash_burn(
        db,
        sid,
        cash_usd=value,
        quarterly_burn_usd=1_000_000.0,
        source="10-Q",
        source_ref=_REF,
        event_date=date(2026, 6, 30),
        ratified_by="auto",
        tenant_id=DATA_TENANT_ID,
        recorded_at=recorded_at,
    )


def _candidate(db, sid: UUID, fact_type: str) -> UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, flags, source, "
            "source_ref, event_date) VALUES (%s, %s, %s, 'flag', '{dual-class}', 'cover', %s, %s) RETURNING id",
            (DATA_TENANT_ID, sid, fact_type, _REF, date(2026, 6, 30)),
        )
        return cur.fetchone()["id"]


def _decide(db, sid: UUID, fact_type: str, action: str, **cols) -> UUID:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "fact_type": fact_type,
        "action": action,
        "decided_by": _STEWARD,
        **cols,
    }
    names = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO platform_value_decision ({names}) VALUES ({marks}) RETURNING id",  # noqa: S608
            tuple(row.values()),
        )
        return cur.fetchone()["id"]


def _values(rows: list[dict], fact_type: str) -> list[float]:
    col = "shares" if fact_type == "shares_outstanding" else "cash_usd"
    return [float(r[col]) for r in rows]


def _reads(db, sid: UUID, fact_type: str) -> dict[str, list[float]]:
    """The value every read path returns for the company (the per-filing winner)."""
    table = TABLES[fact_type]
    common = {"asof": _ASOF, "known_at": _KNOWN, "tenant_id": DATA_TENANT_ID}
    return {
        "record": _values(as_of(db, table, security_id=sid, **common), fact_type),
        "public": _values(as_of(db, table, security_id=sid, clock="public", **common), fact_type),
        "many": _values(
            as_of_many(db, table, security_ids=[sid], **common).get(sid, []), fact_type
        ),
    }


def _mirror(db, tmp_path, name: str, fact_type: str, clock: str, known_by=None):
    out = tmp_path / name
    manifest = export_snapshot(db, out, clock=clock, known_by=known_by, workspace=DATA_TENANT_ID)
    db.commit()
    table = TABLES[fact_type]
    ids = {UUID(str(x)) for x in pq.read_table(out / f"{table}.parquet")["id"].to_pylist()}
    return ids, manifest["tables"][table].get("rows_set_aside", 0)


def _two_versions(db, sid: UUID, fact_type: str) -> tuple[UUID, UUID]:
    """The machine's version (100) and a later version of the same filing (200)."""
    first = _write(db, sid, fact_type, 100.0, recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    second = _write(
        db, sid, fact_type, 200.0, recorded_at=datetime(2026, 8, 2, tzinfo=timezone.utc)
    )
    return first, second


@pytest.fixture
def sid(db) -> UUID:
    from uuid import uuid4

    s = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (s, DATA_TENANT_ID, "UNDO", "0001234567", date(2026, 1, 1)),
        )
    db.commit()
    return s


# --- the NULL trap, first ------------------------------------------------------------------------------------


@pytest.mark.parametrize("fact_type", list(TABLES))
def test_an_undone_dismiss_does_not_blank_the_read(db, sid, fact_type):
    """A dismiss writes no fact (NULL ``fact_id``); undoing it must not put a NULL into the set-aside list, or
    ``NOT IN`` answers nothing and EVERY read of the table comes back empty."""
    _write(db, sid, fact_type, 100.0, recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    cand = _candidate(db, sid, fact_type)
    dismiss = _decide(db, sid, fact_type, "dismiss", candidate_id=cand, reason="wrong class")
    _decide(db, sid, fact_type, "undo", revokes=dismiss)
    db.commit()
    assert _reads(db, sid, fact_type) == {"record": [100.0], "public": [100.0], "many": [100.0]}


# --- the undone approve / correct ----------------------------------------------------------------------------


@pytest.mark.parametrize("fact_type", list(TABLES))
def test_an_undone_approve_is_skipped_and_the_earlier_version_comes_back(
    db, sid, fact_type, tmp_path
):
    first, second = _two_versions(db, sid, fact_type)
    cand = _candidate(db, sid, fact_type)
    approve = _decide(db, sid, fact_type, "approve", candidate_id=cand, fact_id=second)
    db.commit()
    assert _reads(db, sid, fact_type) == {"record": [200.0], "public": [200.0], "many": [200.0]}

    _decide(db, sid, fact_type, "undo", revokes=approve, reason="misread")
    db.commit()
    assert _reads(db, sid, fact_type) == {"record": [100.0], "public": [100.0], "many": [100.0]}
    for clock in ("record", "public"):
        ids, n = _mirror(db, tmp_path, f"m-{clock}", fact_type, clock)
        assert (first in ids, second in ids, n) == (True, False, 1), clock


@pytest.mark.parametrize("fact_type", list(TABLES))
def test_an_undone_correct_is_skipped(db, sid, fact_type):
    _, second = _two_versions(db, sid, fact_type)
    correct = _decide(db, sid, fact_type, "correct", fact_id=second)
    _decide(db, sid, fact_type, "undo", revokes=correct)
    db.commit()
    assert _reads(db, sid, fact_type)["record"] == [100.0]


@pytest.mark.parametrize("fact_type", list(TABLES))
def test_a_standing_approve_is_read(db, sid, fact_type, tmp_path):
    _, second = _two_versions(db, sid, fact_type)
    cand = _candidate(db, sid, fact_type)
    _decide(db, sid, fact_type, "approve", candidate_id=cand, fact_id=second)
    db.commit()
    assert _reads(db, sid, fact_type) == {"record": [200.0], "public": [200.0], "many": [200.0]}
    ids, n = _mirror(db, tmp_path, "standing", fact_type, "record")
    assert (second in ids, n) == (True, 0)


def test_a_decision_of_the_other_type_never_sets_a_version_aside(db, sid):
    """The registered list is per table: an undone cash decision never hides a share count."""
    _, second = _two_versions(db, sid, "shares_outstanding")
    cash = _write(db, sid, "cash_burn", 5.0, recorded_at=datetime(2026, 8, 3, tzinfo=timezone.utc))
    correct = _decide(db, sid, "cash_burn", "correct", fact_id=cash)
    _decide(db, sid, "cash_burn", "undo", revokes=correct)
    db.commit()
    assert _reads(db, sid, "shares_outstanding")["record"] == [200.0]
    assert _reads(db, sid, "cash_burn")["record"] == []


@pytest.mark.parametrize("clock", ["record", "public"])
def test_an_undo_after_the_cut_leaves_that_cuts_mirror_unchanged(db, sid, clock, tmp_path):
    _, second = _two_versions(db, sid, "shares_outstanding")
    approve = _decide(db, sid, "shares_outstanding", "correct", fact_id=second)
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        cut = cur.fetchone()["t"]
    db.commit()
    before = _mirror(db, tmp_path, "before", "shares_outstanding", clock, known_by=cut)
    _decide(db, sid, "shares_outstanding", "undo", revokes=approve)
    db.commit()
    assert _mirror(db, tmp_path, "after", "shares_outstanding", clock, known_by=cut) == before
    assert second in before[0] and before[1] == 0
    now_ids, now_n = _mirror(db, tmp_path, "now", "shares_outstanding", clock)
    assert (second in now_ids, now_n) == (False, 1)


def test_the_fallback_version_keeps_its_public_instant(db, sid):
    """The public clock's running maximum for share counts reads every version, set-aside ones included. That can
    only make a LATER version count later, never an earlier one earlier: the version the read falls back to
    keeps exactly the public instant it had before the undone version was written."""
    _write(
        db, sid, "shares_outstanding", 100.0, recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    db.commit()
    common = {"asof": _ASOF, "known_at": _KNOWN, "tenant_id": DATA_TENANT_ID, "clock": "public"}
    (alone,) = as_of(db, "fact_shares_outstanding", security_id=sid, **common)
    second = _write(
        db, sid, "shares_outstanding", 200.0, recorded_at=datetime(2026, 8, 2, tzinfo=timezone.utc)
    )
    correct = _decide(db, sid, "shares_outstanding", "correct", fact_id=second)
    _decide(db, sid, "shares_outstanding", "undo", revokes=correct)
    db.commit()
    (back,) = as_of(db, "fact_shares_outstanding", security_id=sid, **common)
    assert (back["id"], back["recorded_at"]) == (alone["id"], alone["recorded_at"])
