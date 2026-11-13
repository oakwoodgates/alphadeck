from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pyarrow.parquet as pq

from backtest.manifest import mirror_hash
from db.public_clock import partition_cols
from db.session import DEFAULT_TENANT_ID
from replay.export import (
    AUX_TABLES,
    FACT_TABLES,
    HOLD_TABLES,
    IDENTITY_TABLE,
    SPLIT_TABLE,
    export_snapshot,
)

# THE MIRROR HASH MUST NOT DEPEND ON THE SCAN PLAN.
#
# A record-clock export used to read each fact table with no ORDER BY, so the rows reached the Parquet writer
# in whatever order Postgres's plan returned them, and the file's bytes (dictionary pages are built in
# first-seen order) and the manifest's mirror hash moved with the plan. CI caught it once on
# ``test_clock_wiring``: two exports of the same data quoted different hashes. The fixture below makes heap
# order, index order and ``(recorded_at, id)`` order three DIFFERENT sequences by construction, so a plan
# change is guaranteed to reorder an unordered read, and each test first proves the plan really did change
# (the teeth check) so it can never pass by accident.

# Every row lands in one transaction at one instant: the common real case (``now()`` is the transaction
# start, so one ingest transaction's rows share ``recorded_at``), and the one where only ``id`` breaks ties.
_REC = datetime(2026, 6, 2, 1, 0, tzinfo=timezone.utc)
# Insertion position i -> natural-key rank. Index order (by accession / valid_from / d) is then [1, 3, 0, 4, 2],
# heap order is [0, 1, 2, 3, 4], and id order (ids descend with i) is [4, 3, 2, 1, 0].
_PERM = (2, 0, 4, 1, 3)
_BASE = date(2026, 5, 1)


def _row_id(kind: int, i: int) -> uuid.UUID:
    """Explicit ids that DESCEND with insertion position, so id order is the reverse of heap order."""
    return uuid.UUID(int=(kind << 96) | (100 - i))


def _insider(sid: uuid.UUID, i: int) -> dict:
    rank = _PERM[i]
    vf = _BASE + timedelta(days=rank)
    return {
        "id": _row_id(1, i),
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "insider_name": "CEO",
        "insider_role": "CEO",
        "txn_code": "P",
        "usd": 1000.0 + rank,
        "accession": f"0000000000-26-{rank:06d}",
        "txn_seq": 0,
        "valid_from": vf,
        "accepted": datetime(vf.year, vf.month, vf.day, 21, 0, tzinfo=timezone.utc),
        "recorded_at": _REC,
    }


def _bar(sid: uuid.UUID, i: int) -> dict:
    rank = _PERM[i]
    d = _BASE + timedelta(days=rank)
    return {
        "id": _row_id(2, i),
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "d": d,
        "close": 10.0 + rank,
        "valid_from": d,
        "recorded_at": _REC,
    }


def _split(sid: uuid.UUID, i: int) -> dict:
    eff = date(2026, 3, 2) + timedelta(days=7 * _PERM[i])
    return {
        "id": _row_id(3, i),
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "key_date": eff,
        "effective_date": eff,
        "ratio_from": 10,
        "ratio_to": 1,
        "status": "confirmed",
        "confirmed_via": "auto:sec+polygon",
        "value_source": "both",
        "action": "backfill",
        "author": "test",
        "valid_from": eff,
        "recorded_at": _REC,
    }


def _insert(db: psycopg.Connection, table: str, rows: list[dict]) -> None:
    with db.cursor() as cur:
        for r in rows:
            cols = list(r)
            cur.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) "  # noqa: S608 -- trusted test literals
                f"VALUES ({', '.join('%s' for _ in cols)})",
                [r[c] for c in cols],
            )


def _seed(db: psycopg.Connection, sid: uuid.UUID, order: list[int]) -> None:
    """Insert the five insider rows and five bars in ``order`` (insertion positions), in ONE transaction."""
    _insert(db, "fact_insider_txn", [_insider(sid, i) for i in order])
    _insert(db, "fact_price_eod", [_bar(sid, i) for i in order])
    db.commit()


def _ids(db: psycopg.Connection, table: str) -> list[str]:
    """The unordered read's own sequence -- what the exporter's fact read returned before the fix."""
    with db.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {table} WHERE tenant_id = ANY(%s)",  # noqa: S608 -- trusted literal
            ([DEFAULT_TENANT_ID],),
        )
        return [str(r["id"]) for r in cur.fetchall()]


# The two plans, each FORCED rather than left to the planner: on a freshly truncated table the default plan
# is not predictable (MEASURED on this suite's Postgres 16: the default already picked an index scan, so
# turning seq scans off changed nothing). Bitmap scans are off in both: a bitmap heap scan returns heap order.
_PLANS: dict[str, tuple[str, ...]] = {
    "seq": ("enable_indexscan", "enable_indexonlyscan", "enable_bitmapscan"),
    "index": ("enable_seqscan", "enable_bitmapscan"),
}


@contextmanager
def _plan(db: psycopg.Connection, kind: str) -> Iterator[None]:
    """Force a sequential scan (heap order) or an index scan (index order) for the block."""
    with db.cursor() as cur:
        for setting in _PLANS[kind]:
            cur.execute(f"SET {setting} = off")
    try:
        yield
    finally:
        with db.cursor() as cur:
            for setting in _PLANS[kind]:
                cur.execute(f"RESET {setting}")


def _digests(folder: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.parquet"))
    }


def _assert_same_mirror(a: Path, b: Path) -> None:
    assert _digests(a) == _digests(b)
    assert mirror_hash(a) == mirror_hash(b)


# --- step 1: the bytes do not depend on the plan or on the physical row order -------------------------


def test_record_export_ignores_the_scan_plan(db, security_id, tmp_path):
    _seed(db, security_id, list(range(5)))
    with _plan(db, "seq"):
        heap_order = _ids(db, "fact_insider_txn")
        export_snapshot(db, tmp_path / "a", workspace=DEFAULT_TENANT_ID)
    with _plan(db, "index"):
        index_order = _ids(db, "fact_insider_txn")
        export_snapshot(db, tmp_path / "b", workspace=DEFAULT_TENANT_ID)
    assert sorted(heap_order) == sorted(index_order)
    assert heap_order != index_order, "setup did not force a different plan"
    _assert_same_mirror(tmp_path / "a", tmp_path / "b")


def test_record_export_ignores_physical_row_order(db, security_id, tmp_path):
    """Both exports on a sequential scan, so the only thing that differs is the heap's physical order."""
    _seed(db, security_id, list(range(5)))
    with _plan(db, "seq"):
        before = _ids(db, "fact_insider_txn")
        export_snapshot(db, tmp_path / "a", workspace=DEFAULT_TENANT_ID)
    # Same data, rewritten in reverse: the same ids, the same recorded_at, the same values.
    with db.cursor() as cur:
        cur.execute("DELETE FROM fact_insider_txn")
        cur.execute("DELETE FROM fact_price_eod")
    _seed(db, security_id, list(reversed(range(5))))
    with _plan(db, "seq"):
        after = _ids(db, "fact_insider_txn")
        export_snapshot(db, tmp_path / "b", workspace=DEFAULT_TENANT_ID)
    assert sorted(before) == sorted(after)
    assert before != after, "setup did not change the physical row order"
    _assert_same_mirror(tmp_path / "a", tmp_path / "b")


def test_public_export_ignores_the_scan_plan(db, security_id, tmp_path):
    """The public path is ALREADY total (one row per (partition, public instant), sorted on exactly that):
    pinned here so it cannot regress, and so a change to it shows up as a test, not as a moved archive hash.
    """
    _seed(db, security_id, list(range(5)))
    with _plan(db, "seq"):
        heap_order = _ids(db, "fact_insider_txn")
        export_snapshot(db, tmp_path / "a", workspace=DEFAULT_TENANT_ID, clock="public")
    with _plan(db, "index"):
        index_order = _ids(db, "fact_insider_txn")
        export_snapshot(db, tmp_path / "b", workspace=DEFAULT_TENANT_ID, clock="public")
    assert heap_order != index_order, "setup did not force a different plan"
    _assert_same_mirror(tmp_path / "a", tmp_path / "b")


# --- step 2: the order contract, file by file ---------------------------------------------------------
#
# Every file a mirror can hold, by the order it is written in. The resolver reads the aux, hold and split
# files in FILE order (``replay.pit``), so their order is part of their contract, not a cosmetic.

_RECORD_ORDERED = set(FACT_TABLES) | set(AUX_TABLES) | set(HOLD_TABLES) | {SPLIT_TABLE}
_RECORD_ORDERED -= {"security_cik"}  # identity pairs, not versions: sorted on (security_id, cik)


def _seed_contract(db: psycopg.Connection, sid: uuid.UUID) -> None:
    _insert(db, "fact_insider_txn", [_insider(sid, i) for i in range(5)])
    _insert(db, "fact_price_eod", [_bar(sid, i) for i in range(5)])
    _insert(db, "security_split", [_split(sid, i) for i in range(2)])
    db.commit()


def _strictly_increasing(keys: list[tuple]) -> bool:
    return all(a < b for a, b in zip(keys, keys[1:], strict=False))


def _check_record_file(path: Path) -> None:
    t = pq.read_table(path).to_pylist()
    name = path.stem
    if name == "security_cik":
        keys = [(r["security_id"], r["cik"]) for r in t]
    elif name == IDENTITY_TABLE:
        keys = [(r["security_id"],) for r in t]
    else:
        assert name in _RECORD_ORDERED, name
        keys = [(r["recorded_at"], r["id"]) for r in t]
    assert _strictly_increasing(keys), f"{name} is not strictly increasing on its order key: {keys}"


def test_every_record_file_is_sorted_by_recorded_at_then_id(db, security_id, tmp_path):
    """Strictly: sorted AND unique on ``(recorded_at, id)`` (a lowercase uuid string compares exactly as
    Postgres compares the uuid). ``security_cik`` is identity, sorted on ``(security_id, cik)``."""
    _seed_contract(db, security_id)
    export_snapshot(db, tmp_path, workspace=DEFAULT_TENANT_ID, identity=True)
    files = sorted(tmp_path.glob("*.parquet"))
    assert pq.read_table(tmp_path / "fact_insider_txn.parquet").num_rows == 5
    assert pq.read_table(tmp_path / f"{SPLIT_TABLE}.parquet").num_rows == 2
    for path in files:
        _check_record_file(path)


def _check_public_file(path: Path) -> None:
    t = pq.read_table(path).to_pylist()
    name = path.stem
    if name in FACT_TABLES:
        # one tenant here, so ``tenant_id`` stays in the key (``export._read_table_public`` drops it only for
        # a mixed union). Unique, not Python-sorted: the text keys follow the database's collation.
        key = partition_cols(name) + ["recorded_at"]
        keys = [tuple(r[c] for c in key) for r in t]
        assert len(keys) == len(set(keys)), f"{name} repeats a sort key"
    elif name == SPLIT_TABLE:
        keys = [(r["tenant_id"], r["security_id"], r["key_date"]) for r in t]
        assert len(keys) == len(set(keys)), f"{name} repeats a split"
    else:
        _check_record_file(path)  # the aux and hold files keep their honest (recorded_at, id) order


def test_every_public_fact_file_is_unique_on_its_sort_key(db, security_id, tmp_path):
    _seed_contract(db, security_id)
    export_snapshot(db, tmp_path, workspace=DEFAULT_TENANT_ID, clock="public", identity=True)
    assert pq.read_table(tmp_path / "fact_insider_txn.parquet").num_rows == 5
    for path in sorted(tmp_path.glob("*.parquet")):
        _check_public_file(path)


def test_every_export_file_is_covered(db, security_id, tmp_path):
    """The two checks above cover every file an export writes, on both clocks, so a new table cannot
    skip the contract by falling through a list nobody updated."""
    _seed_contract(db, security_id)
    known = _RECORD_ORDERED | {"security_cik", IDENTITY_TABLE}
    for clock in ("record", "public"):
        out = tmp_path / clock
        export_snapshot(db, out, workspace=DEFAULT_TENANT_ID, clock=clock, identity=True)
        written = {p.stem for p in out.glob("*.parquet")}
        assert written == known, (clock, written ^ known)
