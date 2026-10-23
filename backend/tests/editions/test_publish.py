"""S3 — ``python -m editions.run publish``: Edition 1 stored from a real engine run, COUNTED AT THE TABLE.

The dry-run writes no row; ``--apply`` writes one edition with its join dates, one page per thesis-night and a
``published`` event; a second publish is refused and appends nothing; and the ``calls`` log is counted before
and after every step and never moves. The stored pages are the engine's own cards, byte for byte.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import editions.engine as engine
import editions.run as run
from db.session import DEFAULT_TENANT_ID
from editions.run import PublishRefused, load_run, main, run_publish
from pipeline.call_for_thesis import call_for_thesis
from repositories import editions_repo
from repositories.calls_repo import _canonical
from tests.editions.fixtures import seed_big_buy, seed_tape
from tests.editions.test_engine import _entry, real_roster
from tests.scoreboard.helpers import persist_thesis

NIGHT = date(2026, 7, 10)
JOIN = date(2026, 6, 8)


def _seed(db, security_id, monkeypatch):
    """One thesis, its tape and a big buy, one call of record on ``NIGHT``; the join date pinned (S1's rule is
    tested on its own) by handing ``prepare`` this roster and entry."""
    thesis = persist_thesis(db, security_id)
    seed_tape(db, security_id)
    seed_big_buy(db, security_id)
    db.commit()
    call_for_thesis(db, thesis.id, NIGHT, record=True, run_kind="cron")
    db.commit()
    roster = real_roster(db, thesis.id)
    entries = {thesis.id: {security_id: _entry(thesis.id, security_id, JOIN)}}
    monkeypatch.setattr(engine, "derive_all", lambda conn, *, known_by: ([roster], entries))
    return thesis


def _counts(db) -> dict[str, int]:
    c = editions_repo.table_counts(db)
    db.commit()
    return c


def test_dry_run_writes_nothing_and_apply_writes_one_edition(
    db, security_id, tmp_path, monkeypatch
):
    thesis = _seed(db, security_id, monkeypatch)
    start = _counts(db)
    assert start["calls"] == 1 and start["edition"] == 0

    dry = run_publish(db, tmp_path / "dry", end=NIGHT)
    assert dry["applied"] is False and dry["n_pages"] > 0
    assert _counts(db) == start  # a dry run writes no row anywhere

    s = run_publish(db, tmp_path / "apply", end=NIGHT, apply=True)
    after = _counts(db)
    assert s["applied"] is True
    assert s["counts_before"] == start and s["counts_after"] == after
    assert after["calls"] == start["calls"]  # the record is only ever read
    assert after["edition"] == 1 and after["edition_event"] == 1
    assert after["edition_entry"] == 1
    assert after["edition_call"] == s["n_pages"] == dry["n_pages"]

    (ed,) = editions_repo.list_editions(db, tenant_id=thesis.tenant_id)
    assert (ed.number, ed.label, ed.causes) == (1, "first re-run", ("initial",))
    assert ed.first_night == JOIN and ed.last_night == NIGHT
    assert ed.data_known_by == datetime.fromisoformat(s["data_known_by"])
    assert ed.engine_fingerprint == s["engine"]["fingerprint"]
    assert ed.config_hash == s["engine"]["config_hash"]
    assert (ed.n_entries, ed.n_entries_estimated) == (1, 1)

    # the stored pages ARE the engine's cards (the run directory the publish kept)
    _, cards, _ = load_run(tmp_path / "apply")
    stored = editions_repo.pages_for_thesis(db, ed.id, thesis.id, upto=NIGHT)
    assert [_canonical(c) for c in stored] == [_canonical(c) for c in cards[thesis.id]]
    assert [c.model_dump_json() for c in stored] == [c.model_dump_json() for c in cards[thesis.id]]
    (j,) = editions_repo.joins_for_thesis(db, ed.id, thesis.id).values()
    assert (j.security_id, j.entry_date, j.estimated, j.method) == (
        security_id,
        JOIN,
        True,
        "first_pull",
    )
    db.commit()


def test_a_second_publish_is_refused_and_appends_nothing(db, security_id, tmp_path, monkeypatch):
    _seed(db, security_id, monkeypatch)
    run_publish(db, tmp_path / "one", end=NIGHT, apply=True)
    once = _counts(db)
    ran = []
    monkeypatch.setattr(run, "run_trial", lambda *a, **k: ran.append(1))
    with pytest.raises(PublishRefused, match="already published"):
        run_publish(db, tmp_path / "two", end=NIGHT, apply=True)
    assert _counts(db) == once  # count the TABLE: nothing appended
    assert ran == [] and not (tmp_path / "two").exists()  # refused before the engine ran


def test_the_write_transaction_refuses_a_publish_that_raced_in(
    db, security_id, tmp_path, monkeypatch
):
    """A publish that landed between the pre-check and the write is caught inside the write transaction."""
    thesis = _seed(db, security_id, monkeypatch)
    real_trial = run.run_trial

    def trial_then_race(conn, out, **kw):
        meta = real_trial(conn, out, **kw)
        _, cards, _ = load_run(out)
        conn.commit()
        conn.read_only = False
        editions_repo.publish_edition(  # another process publishes first
            conn,
            tenant_id=thesis.tenant_id,
            number=1,
            label="racer",
            causes=["initial"],
            engine=meta["engine"],
            data_known_by=datetime(2026, 7, 11, tzinfo=timezone.utc),
            pages=cards[thesis.id][:1],
            entries=[],
            computed_at=datetime.now(timezone.utc),
        )
        conn.commit()
        conn.read_only = True
        return meta

    monkeypatch.setattr(run, "run_trial", trial_then_race)
    with pytest.raises(PublishRefused, match="already published"):
        run_publish(db, tmp_path / "slow", end=NIGHT, apply=True)
    c = _counts(db)
    assert (c["edition"], c["edition_event"]) == (1, 1)  # only the racer's edition


def test_the_cli_is_a_dry_run_by_default_and_exits_2_on_a_refusal(
    db, security_id, tmp_path, monkeypatch, capsys
):
    _seed(db, security_id, monkeypatch)
    start = _counts(db)
    main(["publish", "--out", str(tmp_path / "cli-dry"), "--end", NIGHT.isoformat()])
    assert "DRY RUN" in capsys.readouterr().out
    assert _counts(db) == start
    main(["publish", "--apply", "--out", str(tmp_path / "cli"), "--end", NIGHT.isoformat()])
    out = capsys.readouterr().out
    assert "PUBLISHED" in out and "calls" in out
    once = _counts(db)
    with pytest.raises(SystemExit) as exc:
        main(["publish", "--apply", "--out", str(tmp_path / "cli2"), "--end", NIGHT.isoformat()])
    assert exc.value.code == 2 and "REFUSED" in capsys.readouterr().out
    assert _counts(db) == once and once["calls"] == start["calls"]


def test_publish_rejects_an_empty_or_malformed_edition(db):
    cut = datetime(2026, 10, 22, tzinfo=timezone.utc)
    engine_fp = {"fingerprint": "fp", "config_hash": "cfg", "code_sha": None}
    common = dict(
        tenant_id=DEFAULT_TENANT_ID,
        label="x",
        engine=engine_fp,
        data_known_by=cut,
        entries=[],
        computed_at=cut,
    )
    with pytest.raises(ValueError, match="at least one page"):
        editions_repo.publish_edition(db, number=1, causes=["initial"], pages=[], **common)
    with pytest.raises(ValueError, match="start at 1"):
        editions_repo.publish_edition(db, number=0, causes=["initial"], pages=[], **common)
    with pytest.raises(ValueError, match="causes"):
        editions_repo.publish_edition(db, number=1, causes=["whim"], pages=[], **common)
    db.rollback()
    assert _counts(db)["edition"] == 0
