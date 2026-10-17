"""The DB-snapshot runner (Slice 4) — PURE over a tmp dir with an injected ``dump_runner`` (no binary,
no DB), plus ONE double-gated test that exercises the real ``pg_dump``. The retention rules are the
load-bearing part: keep-last-N UNLABELED, labeled EXEMPT, prune ONLY after a successful dump, atomic
publish so a crashed dump never lists."""

from __future__ import annotations

import shutil
import subprocess
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from domain.settings import get_settings
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


# --- Q16: labeled retention — "90 days, or the last 20" -------------------------------------------

_Q16_NOW = datetime(2026, 10, 17, 5, 0, 0, tzinfo=timezone.utc)


def _labeled(dir_: Path, days_ago: float, label: str, *, body: str = "x") -> str:
    """Drop one LABELED dump ``days_ago`` before ``_Q16_NOW`` (the real ``alphadeck-<ts>-<label>.sql``
    shape) and return its name."""
    name = f"alphadeck-{(_Q16_NOW - timedelta(days=days_ago)).strftime(backup._TS)}-{label}.sql"
    (dir_ / name).write_text(body, encoding="utf-8")
    return name


def test_a_labeled_dump_goes_only_when_old_AND_outside_the_newest_20(tmp_path):
    """The rule is a conjunction. 25 labeled dumps, one a day from 100 days back: the newest 20 are kept
    whatever their age, and of the 5 beyond them only the ones older than 90 days are selected."""
    names = [_labeled(tmp_path, 100 - i, f"pre-deploy-{i}") for i in range(25)]
    # names[0] is 100 days old ... names[24] is 76 days old; newest-first, names[4] is the 21st newest
    plan = backup.plan_prune(
        tmp_path, keep=7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW
    )
    selected = [c.name for c in plan]
    # beyond the newest 20: names[0..4] (100, 99, 98, 97, 96 days old) — all older than 90 days
    assert selected == names[0:5]
    assert all(c.labeled for c in plan)
    assert "100 days old" in plan[0].reason and "newest 20 labeled" in plan[0].reason


def test_an_OLD_labeled_dump_inside_the_newest_20_is_kept(tmp_path):
    """Age alone never deletes: with only 3 labeled dumps, even a 400-day-old one is among the last 20."""
    _labeled(tmp_path, 400, "pre-shares-backfill")
    _labeled(tmp_path, 200, "pre-refresh")
    _labeled(tmp_path, 1, "pre-deploy")
    assert (
        backup.plan_prune(tmp_path, keep=7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW)
        == []
    )


def test_a_YOUNG_labeled_dump_outside_the_newest_20_is_kept(tmp_path):
    """Rank alone never deletes: 30 labeled dumps all younger than 90 days are all kept."""
    for i in range(30):
        _labeled(tmp_path, 60 - i, f"pre-deploy-{i}")
    assert (
        backup.plan_prune(tmp_path, keep=7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW)
        == []
    )


def test_exactly_90_days_old_is_not_older_than_90(tmp_path):
    for i in range(20):
        _labeled(tmp_path, 1 + i * 0.01, f"recent-{i}")
    edge = _labeled(tmp_path, 90, "edge")
    over = _labeled(tmp_path, 90.01, "over")
    plan = backup.plan_prune(
        tmp_path, keep=7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW
    )
    assert [c.name for c in plan] == [over]
    assert edge not in [c.name for c in plan]


@pytest.mark.parametrize("labeled_keep,max_age", [(0, 90), (20, 0), (-1, 90), (20, -5)])
def test_a_nonpositive_dial_turns_labeled_pruning_OFF(tmp_path, labeled_keep, max_age):
    """A bad setting can only keep MORE: either dial <= 0 is the pre-Q16 keep-forever."""
    for i in range(30):
        _labeled(tmp_path, 500 - i, f"ancient-{i}")
    assert (
        backup.plan_prune(
            tmp_path,
            keep=7,
            labeled_keep=labeled_keep,
            labeled_max_age_days=max_age,
            now=_Q16_NOW,
        )
        == []
    )


def test_a_future_dated_labeled_dump_is_never_old(tmp_path):
    """The clock went backwards: a dump 'from the future' has a negative age and is kept."""
    for i in range(20):
        _labeled(tmp_path, -10 - i, f"future-{i}")
    old = _labeled(tmp_path, 120, "old")
    plan = backup.plan_prune(
        tmp_path, keep=7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW
    )
    assert [c.name for c in plan] == [old]  # only the genuinely old one beyond the 20


def test_the_unlabeled_rule_is_unchanged_by_q16(tmp_path):
    """Q16 touches labeled dumps only: unlabeled stay keep-newest-N, whatever their age."""
    _seed(tmp_path, *(f"2026{m:02d}01T000000" for m in range(1, 10)))  # 9 unlabeled, Jan..Sep
    plan = backup.plan_prune(
        tmp_path, keep=7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW
    )
    assert [c.name for c in plan] == [
        "alphadeck-20260101T000000.sql",
        "alphadeck-20260201T000000.sql",
    ]
    assert not any(c.labeled for c in plan)


def test_prune_deletes_exactly_the_plan_and_logs_each_file_by_name(tmp_path, caplog):
    """The real prune applies the SAME plan the dry run prints, and logs every removed file by name. Counted
    on the directory, before and after — not just on the returned list."""
    old = [_labeled(tmp_path, 200 - i, f"pre-deploy-{i}") for i in range(3)]
    for i in range(20):
        _labeled(tmp_path, 10 + i * 0.1, f"recent-{i}")
    before = sorted(p.name for p in tmp_path.glob("*.sql"))
    assert len(before) == 23
    planned = [
        c.name
        for c in backup.plan_prune(
            tmp_path, keep=7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW
        )
    ]

    with caplog.at_level("WARNING", logger="alphadeck.backup"):
        deleted = backup._prune(tmp_path, 7, labeled_keep=20, labeled_max_age_days=90, now=_Q16_NOW)

    after = sorted(p.name for p in tmp_path.glob("*.sql"))
    assert deleted == planned == old
    assert after == sorted(set(before) - set(old)) and len(after) == 20
    for name in old:
        assert any(name in r.getMessage() for r in caplog.records)


def test_run_backup_applies_the_labeled_rule_from_settings(tmp_path, monkeypatch):
    """End to end through ``run_backup``: the settings drive the labeled rule after a successful dump."""
    monkeypatch.setenv("ALPHADECK_BACKUP_LABELED_KEEP", "2")
    monkeypatch.setenv("ALPHADECK_BACKUP_LABELED_MAX_AGE_DAYS", "30")
    get_settings.cache_clear()
    try:
        gone = _labeled(tmp_path, 45, "pre-old")
        kept_old_in_window = _labeled(tmp_path, 40, "pre-older-but-2nd-newest")
        kept_young = _labeled(tmp_path, 5, "pre-young")
        r = backup.run_backup(
            base_dir=tmp_path,
            database_url="postgresql://t",
            now=_Q16_NOW,
            dump_runner=_fake_dump,
        )
        names = {p.name for p in tmp_path.glob("*.sql")}
        assert r.pruned == [gone]
        assert names == {r.name, kept_old_in_window, kept_young}
    finally:
        get_settings.cache_clear()


def test_the_dump_failing_still_never_prunes_a_labeled_dump(tmp_path):
    for i in range(25):
        _labeled(tmp_path, 300 - i, f"ancient-{i}")

    def _boom(url: str, dest: Path, *, timeout_s: float) -> None:
        raise RuntimeError("pg_dump exploded")

    with pytest.raises(RuntimeError):
        backup.run_backup(
            base_dir=tmp_path, database_url="postgresql://t", now=_Q16_NOW, dump_runner=_boom
        )
    assert len(list(tmp_path.glob("*.sql"))) == 25


def test_cli_prune_dry_run_lists_exactly_and_removes_nothing(tmp_path, monkeypatch, capsys):
    """``--prune-dry-run`` dumps nothing and deletes nothing; it names every file the prune would remove,
    with its bytes, and the total freed. ``--dir`` points it at a directory (the host copy of the bind).
    """
    monkeypatch.setattr(backup, "_pg_dump", _fake_dump)
    now = datetime.now(timezone.utc)
    old = f"alphadeck-{(now - timedelta(days=120)).strftime(backup._TS)}-pre-shares-backfill.sql"
    (tmp_path / old).write_text("x" * 1234, encoding="utf-8")
    for i in range(20):
        name = f"alphadeck-{(now - timedelta(days=1 + i)).strftime(backup._TS)}-pre-deploy-{i}.sql"
        (tmp_path / name).write_text("y", encoding="utf-8")
    _seed(tmp_path, *((now - timedelta(days=d)).strftime(backup._TS) for d in range(1, 8)))
    before = sorted((p.name, p.stat().st_size) for p in tmp_path.iterdir())

    backup.main(["--prune-dry-run", "--dir", str(tmp_path)])

    assert sorted((p.name, p.stat().st_size) for p in tmp_path.iterdir()) == before
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "nothing is removed" in out
    assert f"{old}  1234 bytes" in out
    assert "a prune now would remove 1 file(s)" in out
    assert "bytes freed: 1234" in out
    # the nightly that triggers the prune also rolls the unlabeled window — named, so the count is exact
    oldest_kept_unlabeled = f"alphadeck-{(now - timedelta(days=7)).strftime(backup._TS)}.sql"
    assert f"also removes {oldest_kept_unlabeled}" in out


def test_cli_prune_dry_run_is_exclusive_with_a_dump_mode(tmp_path):
    with pytest.raises(SystemExit) as exc:
        backup.main(["--prune-dry-run", "--label", "pre-migration", "--dir", str(tmp_path)])
    assert exc.value.code == 2
    assert list(tmp_path.glob("*.sql")) == []


# --- credentials never reach the log (2026-10-17) -------------------------------------------------

_SECRET = "s3cr3t-P@ss:w/rd"  # '@', ':' and '/' force the percent-encoded URI form too
_SECRET_URL = (
    "postgresql://alphadeck:" + quote(_SECRET, safe="") + "@postgres:5432/alphadeck?sslmode=disable"
)


def _record_run(monkeypatch, *, raise_exc: BaseException | None = None):
    """Swap ``subprocess.run`` for a recorder: it keeps argv + env, then succeeds or raises ``raise_exc``
    exactly as the real call would (``CalledProcessError`` / ``TimeoutExpired`` carry the argv)."""
    seen: dict = {}

    def _run(argv, **kw):
        seen["argv"], seen["env"] = list(argv), kw.get("env")
        if raise_exc is not None:
            if isinstance(raise_exc, subprocess.CalledProcessError):
                raise subprocess.CalledProcessError(
                    raise_exc.returncode, argv, output="", stderr=raise_exc.stderr
                )
            if isinstance(raise_exc, subprocess.TimeoutExpired):
                raise subprocess.TimeoutExpired(argv, raise_exc.timeout)
            raise raise_exc
        Path(argv[argv.index("--file") + 1]).write_text("-- dump\n", encoding="utf-8")

    monkeypatch.setattr(backup.subprocess, "run", _run)
    return seen


def _all_text(exc: BaseException) -> str:
    """Everything a traceback of ``exc`` would print — message AND any chained cause/context it shows."""
    return "".join(traceback.format_exception(exc))


def test_the_password_rides_PGPASSWORD_never_the_command_line(tmp_path, monkeypatch):
    """What ``ps`` would show while the dump runs is argv: the URL there carries user/host/db and NOT the
    password, which reaches pg_dump through its environment (decoded)."""
    seen = _record_run(monkeypatch)
    backup._pg_dump(_SECRET_URL, tmp_path / "x.sql", timeout_s=5)
    argv = " ".join(seen["argv"])
    assert _SECRET not in argv and quote(_SECRET, safe="") not in argv
    assert "postgresql://alphadeck@postgres:5432/alphadeck?sslmode=disable" in seen["argv"]
    assert seen["env"]["PGPASSWORD"] == _SECRET


def test_a_FAILED_pg_dump_never_puts_the_password_in_the_error_or_the_log(
    tmp_path, monkeypatch, caplog
):
    """A forced failure through the ADMIN job path (the one that logs a traceback): neither the job's error
    text, nor the raised exception with its whole traceback, nor any captured log line holds the password —
    even when pg_dump's stderr echoes the full connection URL back."""
    from pipeline import backup_job

    seen = _record_run(
        monkeypatch,
        raise_exc=subprocess.CalledProcessError(
            1,
            [],
            stderr=f'pg_dump: error: connection to server failed for "{_SECRET_URL}": FATAL: password '
            f'authentication failed for user "alphadeck" (password={_SECRET})',
        ),
    )
    with pytest.raises(RuntimeError) as exc:
        backup.run_backup(base_dir=tmp_path, database_url=_SECRET_URL, now=_NOW)
    text = _all_text(exc.value)
    assert "pg_dump failed (exit 1)" in text and "password authentication failed" in text
    assert _SECRET not in text and quote(_SECRET, safe="") not in text
    assert _SECRET not in " ".join(seen["argv"])

    backup_job.reset_state()
    with caplog.at_level("DEBUG"):
        jid = backup_job.start_backup_job(
            lambda: backup.run_backup(base_dir=tmp_path, database_url=_SECRET_URL, now=_NOW),
            executor=lambda job, run: backup_job._run_job(job, run),
        )
    job = backup_job.get_job(jid)
    assert job is not None and job.status == "failed" and "pg_dump failed" in job.error
    assert _SECRET not in job.error and quote(_SECRET, safe="") not in job.error
    logged = caplog.text + "".join(
        "".join(traceback.format_exception(*r.exc_info)) for r in caplog.records if r.exc_info
    )
    assert "backup job" in logged and _SECRET not in logged
    assert quote(_SECRET, safe="") not in logged
    backup_job.reset_state()


def test_a_TIMED_OUT_pg_dump_never_puts_the_password_in_the_error(tmp_path, monkeypatch):
    _record_run(monkeypatch, raise_exc=subprocess.TimeoutExpired([], 5))
    with pytest.raises(RuntimeError) as exc:
        backup._pg_dump(_SECRET_URL, tmp_path / "x.sql", timeout_s=5)
    text = _all_text(exc.value)
    assert "timed out after 5 s" in text and _SECRET not in text


@pytest.mark.parametrize(
    "url,want_safe,want_pw",
    [
        ("postgresql://u:pw@h:5544/db", "postgresql://u@h:5544/db", "pw"),
        (
            "postgresql://u@h/db?password=pw&sslmode=require",
            "postgresql://u@h/db?sslmode=require",
            "pw",
        ),
        ("postgresql://u@h/db", "postgresql://u@h/db", None),
        ("host=h user=u password='a b' dbname=db", "host=h user=u  dbname=db", "a b"),
        ("host=h dbname=db", "host=h dbname=db", None),
    ],
)
def test_the_password_is_split_out_of_every_connection_string_shape(url, want_safe, want_pw):
    safe, pw = backup._dump_target(url)
    assert (safe, pw) == (want_safe, want_pw)
    assert want_pw is None or want_pw not in safe


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


# --- M3: the age gate (fresh_snapshot + --if-older-than-h) ----------------------------------------


_NOW = datetime(2026, 10, 16, 3, 0, 0, tzinfo=timezone.utc)


def _stamp(hours_ago: float, *, now: datetime = _NOW) -> str:
    return (now - timedelta(hours=hours_ago)).strftime(backup._TS)


def test_fresh_snapshot_none_when_there_is_no_snapshot(tmp_path):
    assert backup.fresh_snapshot(20, base_dir=tmp_path, now=_NOW) is None
    assert backup.fresh_snapshot(20, base_dir=tmp_path / "missing", now=_NOW) is None


def test_fresh_snapshot_returns_a_young_unlabeled_dump(tmp_path):
    _seed(tmp_path, _stamp(30), _stamp(2))
    fresh = backup.fresh_snapshot(20, base_dir=tmp_path, now=_NOW)
    assert fresh is not None and fresh.name == f"alphadeck-{_stamp(2)}.sql"


def test_fresh_snapshot_none_when_the_newest_unlabeled_is_old(tmp_path):
    _seed(tmp_path, _stamp(30))
    assert backup.fresh_snapshot(20, base_dir=tmp_path, now=_NOW) is None


def test_fresh_snapshot_at_exactly_the_threshold_is_due(tmp_path):
    _seed(tmp_path, _stamp(20))
    assert backup.fresh_snapshot(20, base_dir=tmp_path, now=_NOW) is None


def test_a_fresh_LABELED_dump_never_satisfies_the_gate(tmp_path):
    """A labeled dump is a deliberate recovery point outside the rolling window, not the nightly: a
    pre-migration snapshot an hour ago must not stand in for the rolling dump."""
    _seed(tmp_path, _stamp(30))
    (tmp_path / f"alphadeck-{_stamp(1)}-pre-migration.sql").write_text("x", encoding="utf-8")
    assert backup.fresh_snapshot(20, base_dir=tmp_path, now=_NOW) is None


def test_a_future_dated_snapshot_fails_toward_dumping(tmp_path):
    """The clock went backwards: a dump 'from the future' is not proof of a fresh one."""
    _seed(tmp_path, _stamp(-5))
    assert backup.fresh_snapshot(20, base_dir=tmp_path, now=_NOW) is None


@pytest.fixture
def _cli_dir(tmp_path, monkeypatch):
    """Point the CLI's default dir at tmp_path and swap pg_dump for the fake — the CLI path end to end."""
    monkeypatch.setattr(backup, "_DEFAULT_BACKUPS", tmp_path)
    monkeypatch.setattr(backup, "_pg_dump", _fake_dump)
    return tmp_path


def test_cli_gate_skips_when_a_fresh_dump_exists(_cli_dir, capsys):
    now = datetime.now(timezone.utc)
    _seed(_cli_dir, _stamp(2, now=now), _stamp(30, now=now))
    before = sorted(p.name for p in _cli_dir.glob("*.sql"))

    backup.main(["--if-older-than-h", "20"])

    assert sorted(p.name for p in _cli_dir.glob("*.sql")) == before  # no dump, no prune
    out = capsys.readouterr().out
    assert "backup: skipped" in out and f"alphadeck-{_stamp(2, now=now)}.sql" in out


def test_cli_gate_dumps_when_the_newest_is_old(_cli_dir, capsys):
    now = datetime.now(timezone.utc)
    _seed(_cli_dir, _stamp(30, now=now))

    backup.main(["--if-older-than-h", "20"])

    names = [i.name for i in backup.list_backups(base_dir=_cli_dir)]
    assert len(names) == 2 and names[1] == f"alphadeck-{_stamp(30, now=now)}.sql"
    out = capsys.readouterr().out
    assert "backup: due" in out and f"backup: wrote {names[0]}" in out


def test_cli_gate_dumps_when_there_is_no_snapshot_at_all(_cli_dir):
    backup.main(["--if-older-than-h", "20"])
    assert len(backup.list_backups(base_dir=_cli_dir)) == 1


def test_a_gated_dump_retains_exactly_like_an_ungated_one(_cli_dir, monkeypatch):
    """Retention is untouched: a gated dump IS a run_backup, so it prunes to the same keep-window."""
    monkeypatch.setenv("ALPHADECK_BACKUP_KEEP", "3")
    get_settings.cache_clear()
    try:
        now = datetime.now(timezone.utc)
        _seed(_cli_dir, *(_stamp(24 * d, now=now) for d in range(1, 6)))  # 5 old unlabeled

        backup.main(["--if-older-than-h", "20"])

        unlabeled = [i for i in backup.list_backups(base_dir=_cli_dir) if not i.labeled]
        assert len(unlabeled) == 3  # the new dump + the two newest seeds
    finally:
        get_settings.cache_clear()


def test_cli_gate_and_label_are_mutually_exclusive(_cli_dir):
    with pytest.raises(SystemExit) as exc:
        backup.main(["--label", "pre-migration", "--if-older-than-h", "20"])
    assert exc.value.code == 2
    assert list(_cli_dir.glob("*.sql")) == []


def test_cli_gate_dump_failure_still_raises(_cli_dir, monkeypatch):
    """The shell pages on a non-zero exit, so a failed gated dump must still fail LOUD (and never prune)."""
    now = datetime.now(timezone.utc)
    _seed(_cli_dir, _stamp(30, now=now))

    def _boom(url: str, dest: Path, *, timeout_s: float) -> None:
        raise RuntimeError("pg_dump exploded")

    monkeypatch.setattr(backup, "_pg_dump", _boom)
    with pytest.raises(RuntimeError, match="exploded"):
        backup.main(["--if-older-than-h", "20"])
    assert [p.name for p in _cli_dir.glob("*.sql")] == [f"alphadeck-{_stamp(30, now=now)}.sql"]


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
