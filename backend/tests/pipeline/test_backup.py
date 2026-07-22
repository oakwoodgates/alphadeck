"""The DB-snapshot runner (Slice 4) — PURE over a tmp dir with an injected ``dump_runner`` (no binary,
no DB), plus ONE double-gated test that exercises the real ``pg_dump``. The retention rules are the
load-bearing part: keep-last-N UNLABELED, labeled EXEMPT, prune ONLY after a successful dump, atomic
publish so a crashed dump never lists."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import backup


def _fake_dump(url: str, dest: Path, *, timeout_s: float) -> None:
    """A stand-in for ``pg_dump`` that writes a dummy ``.sql`` — the testability hinge, so no unit test
    needs the binary. It is handed the ``.tmp`` path (run_backup renames it into place atomically).
    """
    dest.write_text("-- dummy dump\nSELECT 1;\n", encoding="utf-8")


def _seed(dir_: Path, *stamps: str) -> None:
    """Drop unlabeled dump files at fixed timestamps (content is irrelevant to the pure logic)."""
    for ts in stamps:
        (dir_ / f"alphadeck-{ts}.sql").write_text("x", encoding="utf-8")


# --- _slugify -------------------------------------------------------------------------------------


def test_slugify_lowers_hyphenates_and_drops_punctuation():
    assert backup._slugify("Pre Shares Backfill") == "pre-shares-backfill"
    assert backup._slugify("  weird!! name @#$  ") == "weird-name"


def test_slugify_empty_or_punctuation_only_is_empty():
    assert backup._slugify("") == ""
    assert backup._slugify("!!!") == ""


def test_slugify_truncates_to_40():
    assert len(backup._slugify("x" * 100)) <= 40


# --- run_backup: filename shape + atomic publish --------------------------------------------------


def test_run_backup_unlabeled_shape_and_atomic_publish(tmp_path):
    now = datetime(2026, 7, 21, 14, 30, 0, tzinfo=timezone.utc)
    r = backup.run_backup(
        base_dir=tmp_path, database_url="postgresql://t", now=now, dump_runner=_fake_dump
    )
    assert r.name == "alphadeck-20260721T143000.sql"
    assert r.labeled is False
    assert r.bytes > 0 and r.created_at == now
    assert r.path.exists()
    assert not (tmp_path / f"{r.name}.tmp").exists()  # os.replace cleaned the tmp — no leftover

    infos = backup.list_backups(base_dir=tmp_path)
    assert [i.name for i in infos] == [r.name]
    assert infos[0].labeled is False and infos[0].bytes > 0


def test_run_backup_labeled_shape_carries_the_slug(tmp_path):
    now = datetime(2026, 7, 21, 2, 57, 57, tzinfo=timezone.utc)
    r = backup.run_backup(
        base_dir=tmp_path,
        database_url="postgresql://t",
        now=now,
        label="Pre Shares Backfill",
        dump_runner=_fake_dump,
    )
    assert r.name == "alphadeck-20260721T025757-pre-shares-backfill.sql"
    assert r.labeled is True
    info = backup.list_backups(base_dir=tmp_path)[0]
    assert info.labeled is True and info.created_at == now


def test_run_backup_prunes_after_a_successful_dump(tmp_path):
    _seed(tmp_path, "20260101T000000", "20260102T000000", "20260103T000000")
    now = datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc)
    r = backup.run_backup(
        base_dir=tmp_path, database_url="postgresql://t", now=now, keep=2, dump_runner=_fake_dump
    )
    names = {i.name for i in backup.list_backups(base_dir=tmp_path)}
    # 4 unlabeled, keep=2 -> the new dump + the newest seed survive; the 2 oldest seeds are pruned
    assert r.name in names and "alphadeck-20260103T000000.sql" in names
    assert "alphadeck-20260101T000000.sql" not in names
    assert "alphadeck-20260102T000000.sql" not in names
    assert set(r.pruned) == {"alphadeck-20260101T000000.sql", "alphadeck-20260102T000000.sql"}


def test_run_backup_dump_failure_publishes_nothing_and_never_prunes(tmp_path):
    """A raising dump propagates, publishes NO ``.sql`` (a crashed dump never lists), and — critically —
    NEVER prunes: a failed dump must not shrink the safety net."""
    _seed(tmp_path, "20260101T000000", "20260102T000000", "20260103T000000")

    def _boom(url: str, dest: Path, *, timeout_s: float) -> None:
        raise RuntimeError("pg_dump exploded")

    now = datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="exploded"):
        backup.run_backup(
            base_dir=tmp_path, database_url="postgresql://t", now=now, keep=1, dump_runner=_boom
        )
    infos = backup.list_backups(base_dir=tmp_path)
    # no NEW .sql published, and all 3 seeds survive despite keep=1 (prune never ran)
    assert {i.name for i in infos} == {
        "alphadeck-20260101T000000.sql",
        "alphadeck-20260102T000000.sql",
        "alphadeck-20260103T000000.sql",
    }


# --- _prune: keep-last-N, labeled EXEMPT ----------------------------------------------------------


def test_prune_keeps_newest_unlabeled_and_exempts_labeled(tmp_path):
    _seed(tmp_path, "20260101T000000", "20260102T000000", "20260103T000000")
    # a labeled dump OLDER than every unlabeled one — it must survive regardless (a deliberate recovery
    # point is never auto-deleted)
    (tmp_path / "alphadeck-20250101T000000-pre-migration.sql").write_text("x", encoding="utf-8")
    deleted = backup._prune(tmp_path, keep=1)
    assert set(deleted) == {"alphadeck-20260101T000000.sql", "alphadeck-20260102T000000.sql"}
    remaining = {p.name for p in tmp_path.glob("*.sql")}
    assert remaining == {
        "alphadeck-20260103T000000.sql",  # newest unlabeled kept
        "alphadeck-20250101T000000-pre-migration.sql",  # labeled EXEMPT even though it's oldest
    }


def test_prune_keep_ge_count_deletes_nothing(tmp_path):
    _seed(tmp_path, "20260101T000000", "20260102T000000")
    assert backup._prune(tmp_path, keep=7) == []
    assert len(list(tmp_path.glob("*.sql"))) == 2


def test_prune_never_touches_an_unparseable_name(tmp_path):
    _seed(tmp_path, "20260101T000000", "20260102T000000")
    (tmp_path / "alphadeck-NOTATIME.sql").write_text("x", encoding="utf-8")  # foreign file
    backup._prune(tmp_path, keep=0)  # even keep=0 must not delete what it can't identify
    assert (tmp_path / "alphadeck-NOTATIME.sql").exists()


# --- list_backups: newest-first, skip-unreadable fail-open ----------------------------------------


def test_list_backups_newest_first_and_skips_junk(tmp_path):
    _seed(tmp_path, "20260101T000000", "20260102T000000")
    (tmp_path / "alphadeck-20260103T000000-labeled.sql").write_text("x", encoding="utf-8")
    # junk that must be skipped fail-open: a bad-prefix .sql and a bad-timestamp .sql (a non-.sql never
    # matches the glob)
    (tmp_path / "random.sql").write_text("x", encoding="utf-8")
    (tmp_path / "alphadeck-NOTATIME.sql").write_text("x", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    infos = backup.list_backups(base_dir=tmp_path)
    assert [i.name for i in infos] == [
        "alphadeck-20260103T000000-labeled.sql",
        "alphadeck-20260102T000000.sql",
        "alphadeck-20260101T000000.sql",
    ]
    assert infos[0].labeled is True and infos[1].labeled is False


def test_list_backups_missing_dir_is_empty(tmp_path):
    assert backup.list_backups(base_dir=tmp_path / "does-not-exist") == []


# --- the ONE double-gated real-pg_dump test (binary + DB) -----------------------------------------


def _table_counts(db) -> dict[str, int]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        names = [r["table_name"] for r in cur.fetchall()]
        counts: dict[str, int] = {}
        for n in names:
            cur.execute(f'SELECT COUNT(*) AS n FROM "{n}"')
            counts[n] = cur.fetchone()["n"]
        return counts


@pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump binary not installed")
def test_real_pg_dump_produces_a_dump_and_mutates_nothing(tmp_path, db):
    """The only test that needs the real binary AND the DB (``db`` -> alphadeck_test): the real
    ``_pg_dump`` writes a genuine ``-- PostgreSQL database dump`` and the read-only dump changes no table
    count. Skips where ``pg_dump`` is absent (the dev host / CI without the image)."""
    before = _table_counts(db)
    r = backup.run_backup(
        base_dir=tmp_path,
        now=datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc),
        dump_runner=backup._pg_dump,  # the REAL subprocess
    )
    content = r.path.read_text(encoding="utf-8", errors="replace")
    assert "PostgreSQL database dump" in content
    assert _table_counts(db) == before  # a pg_dump is read-only — it mutates no row
