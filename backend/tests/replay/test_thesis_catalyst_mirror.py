"""V2 W6a: the counting-catalyst store in the replay mirror — row-for-row parity with the live read on the record
and the public clock (``security_ids`` included), a total export order (two exports of the same rows are the same
bytes), and a mirror frozen before the table existed still opens (the table reads empty).

``security_ids`` (a Postgres ``uuid[]``) is exported as a JSON array of uuid strings in its stored order (the
writer stores it sorted) and decoded back to a list by the mirror reader; ``thesis_catalyst_repo.members_of``
reads either shape as the same ``frozenset[UUID]``.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest.manifest import mirror_hash
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from replay.export import MANIFEST_NAME, export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories import thesis_catalyst_repo as repo
from tests.thesis_catalyst_fixtures import card, insert_version

ET = ZoneInfo("America/New_York")
_PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_FILE = "fact_thesis_catalyst.parquet"
NIGHTS = (date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5), date(2026, 6, 8))


def _seed(db) -> uuid.UUID:
    tid, sids, entry = card(db, n_members=3)
    two = sorted(sids[:2])
    other = uuid.uuid4()
    insert_version(
        db, tid, entry, at=datetime(2026, 6, 3, 10, tzinfo=ET), valid_from=date(2026, 6, 3)
    )
    # a grade + members change saved before Thursday's open: still Wednesday's night
    insert_version(
        db,
        tid,
        entry,
        at=datetime(2026, 6, 4, 3, tzinfo=ET),
        valid_from=date(2026, 6, 3),
        grade="core",
        security_ids=two,
    )
    # a label edit after Thursday's open, carrying the entry's night (N5)
    insert_version(
        db,
        tid,
        entry,
        at=datetime(2026, 6, 4, 9, tzinfo=ET),
        valid_from=date(2026, 6, 3),
        grade="core",
        security_ids=two,
        label="relabeled",
    )
    # switched off on Friday
    insert_version(
        db,
        tid,
        entry,
        at=datetime(2026, 6, 5, 11, tzinfo=ET),
        valid_from=date(2026, 6, 5),
        counts=False,
        grade="core",
        security_ids=two,
    )
    # a second entry, two versions in ONE transaction instant would be refused (UNIQUE), so its own instants
    insert_version(
        db, tid, other, at=datetime(2026, 6, 4, 10, tzinfo=ET), valid_from=date(2026, 6, 4)
    )
    db.commit()
    return tid


def _norm(rows: list[dict]) -> list[tuple]:
    out = []
    for r in rows:
        out.append(
            (
                str(r["entry_id"]),
                r["counts"],
                repo.members_of(r["security_ids"]),
                r["grade"],
                r["label"],
                r["citation"],
                r["valid_from"],
                r["recorded_at"].astimezone(timezone.utc),
                r["source"],
            )
        )
    return sorted(out)


def test_the_mirror_reads_what_the_live_store_reads_on_both_clocks(db, tmp_path):
    tid = _seed(db)
    for clock in ("record", "public"):
        export_snapshot(db, tmp_path / clock, workspace=DEFAULT_TENANT_ID, clock=clock)
        con = connect_mirror(tmp_path / clock)
        try:
            for night in NIGHTS:
                known_at = known_at_for_asof(night, _PIN) if clock == "public" else _PIN
                live = repo.versions_asof(
                    db, tid, asof=night, known_at=known_at, workspace=DEFAULT_TENANT_ID, clock=clock
                )
                rep = ReplayPointInTimeData(
                    con, asof=night, known_at=known_at, workspace=DEFAULT_TENANT_ID
                ).thesis_catalyst_facts(tid)
                assert _norm(live) == _norm(rep), (clock, night)
                assert [r["security_ids"] for r in repo.counting(live)] == [
                    r["security_ids"] for r in repo.counting(rep)
                ]
        finally:
            con.close()
    # the history the parity covers is not empty: Wednesday's public view sees the 03:00 core version
    wed = repo.versions_asof(
        db,
        tid,
        asof=date(2026, 6, 3),
        known_at=known_at_for_asof(date(2026, 6, 3), _PIN),
        workspace=DEFAULT_TENANT_ID,
        clock="public",
    )
    assert [(r["grade"], r["label"]) for r in wed] == [("core", "DOE loan")]


def test_two_exports_of_the_same_rows_are_the_same_bytes(db, tmp_path):
    _seed(db)
    for clock in ("record", "public"):
        a, b = tmp_path / f"{clock}-a", tmp_path / f"{clock}-b"
        export_snapshot(db, a, workspace=DEFAULT_TENANT_ID, clock=clock)
        export_snapshot(db, b, workspace=DEFAULT_TENANT_ID, clock=clock)
        assert (a / _FILE).read_bytes() == (b / _FILE).read_bytes()
        assert mirror_hash(a) == mirror_hash(b)
    # timestamps as integer instants: pyarrow's timestamp-to-Python conversion raises once replay.pit is loaded
    t = pq.read_table(tmp_path / "record-a" / _FILE)
    recorded = t.column("recorded_at").cast(pa.int64()).to_pylist()
    keys = list(zip(recorded, t.column("id").to_pylist(), strict=True))
    assert (
        keys == sorted(keys) and len(set(keys)) == len(keys) == 5
    )  # the (recorded_at, id) total order
    grades = t.column("grade").to_pylist()
    members = t.column("security_ids").to_pylist()
    two = [json.loads(m) for g, m in zip(grades, members, strict=True) if g == "core"]
    assert two and all(v == sorted(v) and len(v) == 2 for v in two)  # a JSON array, stored order


def test_a_mirror_frozen_before_the_table_existed_still_opens(db, tmp_path):
    tid = _seed(db)
    for clock in ("record", "public"):
        out = tmp_path / clock
        export_snapshot(db, out, workspace=DEFAULT_TENANT_ID, clock=clock)
        # make it a pre-W6 mirror: no file, and the manifest does not list the table
        (out / _FILE).unlink()
        manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
        del manifest["tables"]["fact_thesis_catalyst"]
        (out / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        con = connect_mirror(out)
        try:
            pit = ReplayPointInTimeData(
                con, asof=date(2026, 6, 8), known_at=_PIN, workspace=DEFAULT_TENANT_ID
            )
            assert pit.thesis_catalyst_facts(tid) == []
            assert pit.theme_conviction_facts(tid) == []  # the rest of the mirror reads as before
        finally:
            con.close()


def test_a_listed_table_with_a_lost_file_still_fails_loudly(db, tmp_path):
    _seed(db)
    export_snapshot(db, tmp_path / "m", workspace=DEFAULT_TENANT_ID)
    (tmp_path / "m" / _FILE).unlink()
    with pytest.raises(Exception, match="fact_thesis_catalyst"):
        connect_mirror(tmp_path / "m")
    # and with no manifest at all nothing is assumed: still loud
    shutil.copytree(tmp_path / "m", tmp_path / "n")
    (tmp_path / "n" / MANIFEST_NAME).unlink()
    with pytest.raises(Exception, match="fact_thesis_catalyst"):
        connect_mirror(tmp_path / "n")
