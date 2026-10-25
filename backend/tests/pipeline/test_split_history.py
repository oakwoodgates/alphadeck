"""``pipeline.split_history`` — a company that joins a basket gets its split history (2026-10-24), end to end on
the real test Postgres over REAL inputs (``tests/fixtures/splits``): ENHA's 8-Ks + submissions index and the
2026-10-22 Massive.com rows behind a fake socket (no network), a cache-only EDGAR client. Every idempotency claim
COUNTS THE TABLES (``security_split``, ``security_split_history``, ``data_repair``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID, connect, name_session
from db.write_lock import acquire_company_lock
from domain.settings import get_settings
from domain.splits import STATUS_NEEDS_REVIEW
from ingest.edgar.client import EdgarClient
from pipeline import backfill_splits as bf
from pipeline import onboarding, split_history
from repositories import split_history_repo, split_repo
from tests.split_801_fixtures import ENHA_POLYGON_DAY, enha_polygon, seed_enha, seed_member
from tests.split_fixtures import edgar_cache

ASOF = date(2026, 10, 23)
GAME_CIK = "0001714562"  # GAME: Massive.com lists its 1-for-8 of 2026-08-24; no submissions index in the fixtures
_NY = ZoneInfo("America/New_York")
MIGRATION = (
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "0067_security_split_history.sql"
)


def _count(db, table: str) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — a test literal
        n = int(cur.fetchone()["n"])
    db.rollback()
    return n


def _fill(db, tmp_path, **kw):
    kw.setdefault("polygon", enha_polygon(tmp_path, today=ASOF))
    kw.setdefault("edgar", EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False))
    kw.setdefault("asof", ASOF)
    return split_history.fill(db, allow_live=True, **kw)


def test_a_joining_company_gets_its_split_history_once(db, tmp_path):
    """ENHA joins a basket with no split on the list: the fill decides Massive.com's 1-for-10 of 2026-10-28 by S1's
    rule (its 8.01 8-K states the first session 10-29 — a session apart: review), appends it as the backfill's,
    marks ENHA filled and journals the run. A company Massive.com lists no split for is marked too, at no request.
    A second run has nothing to do: the TABLES do not grow."""
    enha = seed_enha(db)
    other = seed_member(db, "QUIET", "0000000001", [])
    assert {m.security_id for m in split_history.needing(db)} == {enha, other}
    out = _fill(db, tmp_path)
    assert out.status == "done" and out.applied
    assert out.appended == 1 and len(out.companies) == 2
    (v,) = split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[enha])
    assert (v.key_date, v.status, v.author) == (
        ENHA_POLYGON_DAY,
        STATUS_NEEDS_REVIEW,
        split_repo.AUTHOR_BACKFILL,
    )
    marks = split_history_repo.get(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[enha, other])
    assert {
        sid: (m.source, m.complete, m.polygon_splits, m.appended) for sid, m in marks.items()
    } == {
        enha: (split_history_repo.SOURCE_JOB, True, 1, 1),
        other: (split_history_repo.SOURCE_JOB, True, 0, 0),
    }
    assert (_count(db, "security_split"), _count(db, "security_split_history")) == (1, 2)
    assert _count(db, "data_repair") == 1  # the S1 journal, as the CLI writes it
    with db.cursor() as cur:
        cur.execute("SELECT tool, rows_changed FROM data_repair")
        assert cur.fetchone() == {"tool": "backfill_splits", "rows_changed": 1}
    db.rollback()
    again = _fill(db, tmp_path)
    assert (again.status, again.needing, again.appended) == ("idle", 0, 0)
    assert (_count(db, "security_split"), _count(db, "security_split_history")) == (1, 2)
    assert _count(db, "data_repair") == 1


def test_a_split_already_listed_is_never_listed_twice(db, tmp_path):
    """A joiner whose split the nightly (or the operator) already listed: the fill finds it on the list
    (``match_stored``) and leaves it exactly as it is — it only marks the company."""
    enha = seed_enha(db)
    bf.backfill_splits(
        db,
        polygon=enha_polygon(tmp_path, today=ASOF),
        edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        since=date(2026, 7, 24),  # a partial history: it marks nothing
        asof=ASOF,
        apply=True,
        allow_live=True,
    )
    n = _count(db, "security_split")
    assert n == 1 and _count(db, "security_split_history") == 0
    out = _fill(db, tmp_path)
    assert out.appended == 0 and _count(db, "security_split") == n
    assert split_history_repo.get(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[enha])[
        enha
    ].complete


def test_a_marked_company_is_never_filled_again(db, tmp_path):
    enha = seed_enha(db)
    split_history_repo.mark(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=enha,
        since=bf.DEFAULT_SINCE,
        source=split_history_repo.SOURCE_SEED,
        complete=True,
    )
    db.commit()
    assert split_history.needing(db) == []
    out = _fill(db, tmp_path)
    assert out.status == "idle" and _count(db, "security_split") == 0


def test_no_polygon_key_fills_and_marks_nothing(db, tmp_path):
    seed_enha(db)
    out = _fill(db, tmp_path, polygon=None)
    assert out.status == "off" and out.needing == 1
    assert (_count(db, "security_split"), _count(db, "security_split_history")) == (0, 0)


def test_an_unreadable_index_is_retried_then_left_marked(db, tmp_path):
    """GAME: Massive.com lists its split, but its SEC filing index cannot be read here. The split is still listed
    (Massive.com only, for review — visible, #9) and the company is marked INCOMPLETE; the next runs retry it, at
    most ``MAX_ATTEMPTS`` times — then it is no longer picked up (its note says why)."""
    game = seed_member(db, "GAME", GAME_CIK, [])
    out = _fill(db, tmp_path)
    (c,) = out.companies
    assert (c.outcome, c.appended) == ("incomplete", 1)
    assert "SEC filing index could not be read" in c.note
    m = split_history_repo.get(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[game])[game]
    assert (m.complete, m.attempts) == (False, 1)
    for attempt in range(2, split_history.MAX_ATTEMPTS + 1):
        _fill(db, tmp_path)
        m = split_history_repo.get(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[game])[game]
        assert m.attempts == attempt
    assert split_history.needing(db) == []
    # one split, one version: the retries re-planned it UNCHANGED (the table is counted)
    assert _count(db, "security_split") == 1


def test_a_company_another_writer_holds_waits_for_the_next_run(db, tmp_path):
    enha = seed_enha(db)
    other = connect()
    try:
        name_session(other, "alphadeck-writer:workbench-ingest")
        with acquire_company_lock(other, enha, wait_s=0):
            out = _fill(db, tmp_path)
            (c,) = out.companies
            assert c.outcome == "busy"
            assert _count(db, "security_split_history") == 0 and _count(db, "security_split") == 0
    finally:
        other.close()
    _fill(db, tmp_path)
    assert _count(db, "security_split_history") == 1


def test_the_dry_run_writes_nothing(db, tmp_path):
    seed_enha(db)
    out = _fill(db, tmp_path, apply=False)
    (c,) = out.companies
    assert (c.outcome, c.appended) == ("planned", 1)
    assert "would append 1" in out.summary()
    assert (_count(db, "security_split"), _count(db, "security_split_history")) == (0, 0)
    assert _count(db, "data_repair") == 0


def test_the_onboarding_job_fills_it_even_with_nothing_queued(db, tmp_path):
    """The seam: every staged-onboarding batch fills split history BEFORE its queue — a fast-lane promote leaves
    nothing queued, and its names still get their history."""
    seed_enha(db)
    out = onboarding.run_batch(
        now=lambda: datetime(2026, 10, 23, 12, 0, tzinfo=_NY),
        edgar_client=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        split_source=enha_polygon(tmp_path, today=ASOF),
        runs_dir=tmp_path / "runs",
    )
    assert out.refused and out.stopped == "nothing_queued"
    assert out.split_history["status"] == "done" and out.split_history["appended"] == 1
    assert "split history:" in out.split_history_line
    assert (_count(db, "security_split"), _count(db, "security_split_history")) == (1, 1)


@pytest.fixture
def nightly_window(monkeypatch):
    """The real 21:30-23:30 ET window (the suite turns it off — ``tests/conftest.py``)."""
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_BEFORE_MIN", "60")
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_AFTER_MIN", "60")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_onboarding_job_never_fills_inside_the_nightly_window(db, tmp_path, nightly_window):
    seed_enha(db)
    out = onboarding.run_batch(
        now=lambda: datetime(2026, 10, 23, 22, 0, tzinfo=_NY),
        edgar_client=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        split_source=enha_polygon(tmp_path, today=ASOF),
        runs_dir=tmp_path / "runs",
    )
    assert out.refused and out.stopped == "window" and out.split_history is None
    assert _count(db, "security_split_history") == 0


def _snapshot(db, thesis_id, members, taken_at) -> None:
    from psycopg.types.json import Json

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
            "VALUES (%s, %s, %s, %s, %s, 'test')",
            (
                uuid.uuid4(),
                DEFAULT_TENANT_ID,
                thesis_id,
                taken_at,
                Json([{"security_id": str(s), "ticker": t} for t, s in members]),
            ),
        )
    db.commit()


def test_the_migration_seeds_what_the_s1_backfill_covered(db, tmp_path):
    """0067's seed (re-run here — it is idempotent): a company in a live basket at the S1 backfill's apply
    instant (its basket snapshot then) and a company with any backfill version are marked; a company that
    joined after it is not — the job fills that one."""
    early = seed_member(db, "EARLY", "0000000002", [])
    with db.cursor() as cur:
        cur.execute("SELECT thesis_id FROM basket_member WHERE security_id = %s", (early,))
        tid = cur.fetchone()["thesis_id"]
    db.rollback()
    _snapshot(db, tid, [("EARLY", early)], datetime(2026, 1, 2, tzinfo=_NY))
    enha = seed_enha(db)
    bf.backfill_splits(  # the S1 backfill: writes ENHA's version (its apply instant is the cutoff)
        db,
        polygon=enha_polygon(tmp_path, today=ASOF),
        edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        since=date(2026, 7, 24),
        asof=ASOF,
        apply=True,
        allow_live=True,
    )
    late = seed_member(db, "LATE", "0000000003", [])
    _snapshot(db, tid, [("EARLY", early), ("LATE", late)], datetime(2099, 1, 1, tzinfo=_NY))
    db.execute("DELETE FROM security_split_history")
    db.commit()
    db.execute(MIGRATION.read_text(encoding="utf-8"))
    db.execute(MIGRATION.read_text(encoding="utf-8"))  # idempotent
    db.commit()
    seeded = split_history_repo.get(
        db, tenant_id=DEFAULT_TENANT_ID, security_ids=[early, enha, late]
    )
    assert set(seeded) == {early, enha}
    assert {m.source for m in seeded.values()} == {split_history_repo.SOURCE_SEED}
    assert {m.security_id for m in split_history.needing(db)} == {late}
