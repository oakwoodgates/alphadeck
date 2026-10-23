"""S5 — the NIGHTLY PAGE job (``python -m editions.run nightly``), COUNTED AT THE TABLE.

A published edition grows by one page per live thesis per session it has no page for: tonight's ``nightly``,
any missed session's ``catchup``; a holiday has none; a second run the same night appends nothing. A thesis or
company new since the cut joins from its own first night with an exact ``snapshot`` join date; a company
removed drops off from the night it left; an archived thesis gets no new page; a thesis with a member whose
history is still loading is HELD, and the next run's catch-up fills it. The job runs only after a finished
nightly pass, refuses while the call-of-record lock is held (a probe, never a take), and self-checks tonight's
page against tonight's recorded call — skipping a row another build recorded, paging a real mismatch.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import editions.engine as engine
import editions.nightly as nightly
from db.session import DEFAULT_TENANT_ID
from db.write_lock import FACT_GATE_ID
from domain.config import DEFAULT_CONFIG, config_hash
from editions.entry import Snapshot, ThesisRoster, estimate_entries
from editions.nightly import NightlyRefused, plan_pages, run_nightly
from editions.run import main, run_publish
from pipeline import edition_run_log
from pipeline.call_for_thesis import call_for_thesis
from pipeline.onboarding import ONBOARD_LOCK_ID
from pipeline.run_lock import (
    LOCK_ID_CALL_OF_RECORD,
    LOCK_ID_EDITIONS,
    acquire_editions_lock,
    acquire_pass_lock,
)
from repositories import calls_repo, editions_repo
from tests.editions.fixtures import member_rows, seed_big_buy, seed_tape
from tests.editions.test_engine import _entry, real_roster
from tests.scoreboard.helpers import persist_thesis

CUT_NIGHT = date(2026, 7, 7)  # Tue: the published edition's last night
END = date(2026, 7, 10)  # Fri: tonight
JOIN = date(2026, 6, 8)
BUY_STORED = datetime(2026, 6, 18, tzinfo=timezone.utc)  # stored the night after it became public
_NY = ZoneInfo("America/New_York")


def _counts(db) -> dict[str, int]:
    c = editions_repo.table_counts(db)
    db.commit()
    return c


def _fake_derive(monkeypatch, rosters, entries) -> None:
    """Pin the join-date rule's output (S1's rule is tested on its own) for the publish AND the nightly."""

    def fake(conn, *, known_by):
        return list(rosters), entries

    monkeypatch.setattr(engine, "derive_all", fake)
    monkeypatch.setattr(nightly, "derive_all", fake)


def _publish(db, monkeypatch, tmp_path, rosters, entries, *, cut_night=CUT_NIGHT):
    _fake_derive(monkeypatch, rosters, entries)
    run_publish(db, tmp_path / "publish", end=cut_night, apply=True)
    (ed,) = editions_repo.list_editions(db, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    return ed


def _published(db, security_id, monkeypatch, tmp_path, *, cut_night=CUT_NIGHT, tape=True):
    """One thesis, its tape and a big buy, Edition 1 published through ``cut_night``."""
    thesis = persist_thesis(db, security_id)
    if tape:
        seed_tape(db, security_id)
    seed_big_buy(db, security_id, recorded_at=BUY_STORED)
    db.commit()
    roster = real_roster(db, thesis.id)
    entries = {thesis.id: {security_id: _entry(thesis.id, security_id, JOIN)}}
    ed = _publish(db, monkeypatch, tmp_path, [roster], entries, cut_night=cut_night)
    return thesis, roster, entries, ed


def _pages(db, edition_id, thesis_id) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT asof, page_kind, data_known_by, engine_fingerprint FROM edition_call "
            "WHERE edition_id = %s AND thesis_id = %s ORDER BY asof",
            (edition_id, thesis_id),
        )
        rows = cur.fetchall()
    db.commit()
    return rows


def _new_pages(db, ed, thesis_id, after=CUT_NIGHT) -> list[tuple[date, str]]:
    return [(r["asof"], r["page_kind"]) for r in _pages(db, ed.id, thesis_id) if r["asof"] > after]


def _security(db, ticker: str, cik: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    db.commit()
    return sid


def _cron_artifact(
    tmp_path,
    asof: date,
    *,
    status: str = "finished",
    pass_kind: str | None = "nightly",
    mode: str = "live",
    theses=(),
    started: datetime | None = None,
) -> None:
    d = tmp_path / "cron_runs"
    d.mkdir(parents=True, exist_ok=True)
    started = started or datetime.now(timezone.utc)
    doc = {
        "status": status,
        "asof": asof.isoformat(),
        "mode": mode,
        "started_at": started.isoformat(),
        "theses": list(theses),
    }
    if pass_kind is not None:
        doc["pass_kind"] = pass_kind
    (d / f"{started:%Y%m%dT%H%M%SZ}.json").write_text(json.dumps(doc), encoding="utf-8")


def _artifacts(tmp_path) -> list[dict]:
    d = tmp_path / "edition_runs"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))]


def _evening(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 16, 0, tzinfo=_NY)


# --- the pages ---------------------------------------------------------------------------------------------


def test_missed_sessions_get_catchup_pages_and_tonight_a_nightly_page(
    db, security_id, tmp_path, monkeypatch
):
    thesis, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    before = _counts(db)
    _cron_artifact(tmp_path, END)  # tonight's FINISHED nightly pass: the gate names the night

    r = run_nightly(db, apply=True)
    after = _counts(db)
    assert r.status == "done" and r.end == END and r.gate.ok
    assert _new_pages(db, ed, thesis.id) == [
        (date(2026, 7, 8), "catchup"),
        (date(2026, 7, 9), "catchup"),
        (END, "nightly"),
    ]
    assert after["edition_call"] == before["edition_call"] + 3 == before["edition_call"] + r.n_pages
    assert after["edition_event"] == before["edition_event"] + 1  # one `paged` event
    assert after["edition_entry"] == before["edition_entry"]  # no company new to the edition
    assert after["calls"] == before["calls"]  # the record is only ever read
    assert after["edition"] == 1  # the same edition grew; nothing new was cut
    new = [p for p in _pages(db, ed.id, thesis.id) if p["asof"] > CUT_NIGHT]
    assert {p["data_known_by"] for p in new} == {r.known_by}  # each page carries its watermark
    assert {p["engine_fingerprint"] for p in new} == {r.engine["fingerprint"]}
    (art,) = _artifacts(tmp_path)
    assert art["mode"] == "apply" and art["status"] == "done" and art["n_pages"] == 3
    assert art["admin_line"].startswith(
        "Edition 1 · page for Jul 10 added · 2 catch-up pages added · 1 thesis"
    )
    assert edition_run_log.latest_edition_run()["admin_line"] == art["admin_line"]


def test_a_second_run_the_same_night_appends_nothing(db, security_id, tmp_path, monkeypatch):
    """COUNT THE TABLE: every edition table (and ``calls``) is the same size after the second run."""
    _published(db, security_id, monkeypatch, tmp_path)
    _cron_artifact(tmp_path, END)
    run_nightly(db, apply=True)
    once = _counts(db)
    again = run_nightly(db, apply=True)
    assert _counts(db) == once
    assert again.n_pages == 0 and again.entries_appended == 0 and again.ink.get("inked") == 0
    assert again.counts_before == again.counts_after == once
    assert "up to date through Jul 10" in again.admin_line()


def test_a_market_holiday_gets_no_page(db, security_id, tmp_path, monkeypatch):
    """2026-07-03 (Independence Day observed) has no bars on the tape, so no page — the sessions on either
    side are caught up, and tonight is the nightly page."""
    seed_tape(db, security_id, end=date(2026, 7, 2))
    seed_tape(db, security_id, start=date(2026, 7, 6))
    thesis, _, _, ed = _published(
        db, security_id, monkeypatch, tmp_path, cut_night=date(2026, 7, 1), tape=False
    )
    run_nightly(db, apply=True, end=date(2026, 7, 7))
    assert _new_pages(db, ed, thesis.id, after=date(2026, 7, 1)) == [
        (date(2026, 7, 2), "catchup"),
        (date(2026, 7, 6), "catchup"),
        (date(2026, 7, 7), "nightly"),
    ]


def test_a_catchup_too_big_for_one_run_finishes_oldest_first(
    db, security_id, tmp_path, monkeypatch
):
    thesis, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    first = run_nightly(db, apply=True, end=END, max_nights=2)
    assert _new_pages(db, ed, thesis.id) == [
        (date(2026, 7, 8), "catchup"),
        (date(2026, 7, 9), "catchup"),
    ]
    assert first.behind == [
        {"thesis_id": str(thesis.id), "name": thesis.name, "owed": 3, "this_run": 2}
    ]
    assert "1 thesis still catching up" in first.admin_line()
    second = run_nightly(db, apply=True, end=END, max_nights=2)
    assert second.behind == [] and _new_pages(db, ed, thesis.id)[-1] == (END, "nightly")


def test_a_dry_run_writes_nothing_and_the_admin_ignores_it(db, security_id, tmp_path, monkeypatch):
    _published(db, security_id, monkeypatch, tmp_path)
    start = _counts(db)
    own = db.read_only
    r = run_nightly(db, end=END)
    assert r.mode == "dry_run" and r.n_pages == 3  # what --apply would append
    assert _counts(db) == start
    (art,) = _artifacts(tmp_path)
    assert art["mode"] == "dry_run" and "(dry run)" in art["admin_line"]
    assert edition_run_log.latest_edition_run() is None  # the Admin reads applied runs only
    assert db.read_only == own  # the connection's own setting is restored


# --- the gate and the locks --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact, why",
    [
        (None, "no nightly pass has run yet"),
        (dict(pass_kind="onboard"), "not a nightly pass (pass_kind onboard)"),
        (dict(status="running"), "has not finished (status running)"),
        (dict(status="crashed"), "has not finished (status crashed)"),
        (dict(mode="no-live"), "cache-only"),
    ],
)
def test_only_a_finished_nightly_pass_triggers_it(db, tmp_path, artifact, why):
    if artifact is not None:
        _cron_artifact(tmp_path, END, **artifact)
    start = _counts(db)
    r = run_nightly(db, apply=True)
    assert r.status == "skipped" and why in r.reason
    assert _counts(db) == start
    (art,) = _artifacts(tmp_path)
    assert art["status"] == "skipped" and why in art["admin_line"]


def test_an_artifact_older_than_pass_kind_reads_as_the_nightly(db, tmp_path):
    _cron_artifact(tmp_path, END, pass_kind=None)
    assert nightly.pass_gate().ok


def test_it_refuses_while_the_call_of_record_lock_is_held(db, security_id, tmp_path, monkeypatch):
    _published(db, security_id, monkeypatch, tmp_path)
    start = _counts(db)
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(NightlyRefused, match="call-of-record lock"):
            run_nightly(db, apply=True, end=END)
        with pytest.raises(SystemExit) as exc:  # the CLI's own exit code
            main(["nightly", "--apply", "--end", END.isoformat()])
        assert exc.value.code == 75
    finally:
        held.release()
    assert _counts(db) == start and _artifacts(tmp_path) == []  # nothing written, no artifact
    # the probe never TOOK lock 1: with the pass gone the job runs at once
    assert run_nightly(db, apply=True, end=END).n_pages == 3


def test_it_refuses_while_another_editions_job_runs(db, security_id, tmp_path, monkeypatch):
    _published(db, security_id, monkeypatch, tmp_path)
    start = _counts(db)
    other = acquire_editions_lock()
    try:
        with pytest.raises(NightlyRefused, match="another editions page job"):
            run_nightly(db, apply=True, end=END)
    finally:
        other.release()
    assert _counts(db) == start


def test_the_editions_lock_id_collides_with_no_other_lock():
    """The plan named id 2, but that is the fact-writer gate (an exclusive hold there would stall every
    company writer): the editions lock is its own id in the namespace."""
    assert LOCK_ID_EDITIONS not in (LOCK_ID_CALL_OF_RECORD, FACT_GATE_ID, ONBOARD_LOCK_ID)


# --- the roster: new theses, new and removed companies, archived theses -------------------------------------


def test_a_thesis_created_after_the_cut_joins_from_its_first_night_with_snapshot_entries(
    db, security_id, tmp_path, monkeypatch
):
    thesis, roster, entries, ed = _published(db, security_id, monkeypatch, tmp_path)
    late_sid = _security(db, "NEWCO", "0007654321")
    seed_tape(db, late_sid)
    late = persist_thesis(db, late_sid)
    # created after the cut, its first promote on 07-08 16:00 ET — its join date by S1's own rule
    first = Snapshot(_evening(date(2026, 7, 8)), member_rows(late_sid))
    late_roster = ThesisRoster(
        thesis_id=late.id,
        tenant_id=DEFAULT_TENANT_ID,
        created_at=first.taken_at - timedelta(hours=1),
        archived_at=None,
        snapshots=(first,),
        live_security_ids=frozenset({late_sid}),
        seed=None,
    )
    late_entries = estimate_entries(late_roster, first_pulls={}, first_cards={})
    assert {e.method for e in late_entries.values()} == {"snapshot"}
    _fake_derive(monkeypatch, [roster, late_roster], {**entries, late.id: late_entries})

    before = _counts(db)
    run_nightly(db, apply=True, end=END)
    assert _new_pages(db, ed, late.id, after=date(2026, 1, 1)) == [  # no page before it existed
        (date(2026, 7, 8), "catchup"),
        (date(2026, 7, 9), "catchup"),
        (END, "nightly"),
    ]
    assert len(_new_pages(db, ed, thesis.id)) == 3  # the existing thesis is caught up too
    assert _counts(db)["edition_entry"] == before["edition_entry"] + 1
    (j,) = editions_repo.joins_for_thesis(db, ed.id, late.id).values()
    db.commit()
    assert (j.security_id, j.entry_date, j.estimated, j.method) == (
        late_sid,
        date(2026, 7, 8),
        False,
        "snapshot",
    )


def test_a_removed_company_drops_off_from_the_night_it_left(db, security_id, tmp_path, monkeypatch):
    other = _security(db, "GONECO", "0007654322")
    seed_tape(db, other)
    thesis, roster, entries, ed = _published(db, security_id, monkeypatch, tmp_path)
    # the roster as S1 reads it: the 0043 deploy seed held both companies; a promote on 07-09 16:00 ET
    # removed GONECO (its basket_snapshot is the roster from that night on)
    seed = Snapshot(
        datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc), member_rows(security_id, other)
    )
    removed = Snapshot(_evening(date(2026, 7, 9)), member_rows(security_id))
    both = dataclasses.replace(
        roster, snapshots=(seed, removed), seed=seed, live_security_ids=frozenset({security_id})
    )
    both_entries = {
        thesis.id: {
            security_id: _entry(thesis.id, security_id, JOIN),
            other: _entry(thesis.id, other, JOIN),
        }
    }
    _fake_derive(monkeypatch, [both], both_entries)
    kb = nightly.db_now(db)
    db.commit()
    plan = plan_pages(db, tmp_path / "plan-mirror", end=END, known_by=kb)
    (job,) = plan.jobs
    by = {n.asof: {m.security_id for m in n.basket} for n in job.nights}
    assert by == {
        date(2026, 7, 8): {security_id, other},
        date(2026, 7, 9): {security_id},  # dropped on the night it left
        END: {security_id},
    }


def test_an_archived_thesis_gets_no_new_page(db, security_id, tmp_path, monkeypatch):
    thesis, roster, entries, ed = _published(db, security_id, monkeypatch, tmp_path)
    archived = dataclasses.replace(roster, archived_at=_evening(date(2026, 7, 8)))
    _fake_derive(monkeypatch, [archived], entries)
    before = _counts(db)
    r = run_nightly(db, apply=True, end=END)
    assert _counts(db)["edition_call"] == before["edition_call"]
    assert [a["thesis_id"] for a in r.archived] == [str(thesis.id)]


# --- the hold -----------------------------------------------------------------------------------------------


def _onboarding(db, sid, status: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_onboarding (tenant_id, security_id, status, source) "
            "VALUES (%s, %s, %s, 'test') ON CONFLICT (tenant_id, security_id) "
            "DO UPDATE SET status = EXCLUDED.status",
            (DEFAULT_TENANT_ID, sid, status),
        )
    db.commit()


def test_a_member_still_loading_holds_the_thesis_and_the_catchup_fills_it(
    db, security_id, tmp_path, monkeypatch
):
    thesis, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    _onboarding(db, security_id, "in_progress")
    before = _counts(db)
    held = run_nightly(db, apply=True, end=date(2026, 7, 9))
    assert _counts(db)["edition_call"] == before["edition_call"]  # NO page while it loads
    (h,) = held.held
    assert h.thesis_id == thesis.id and h.nights == (date(2026, 7, 8), date(2026, 7, 9))
    assert h.names[0]["status"] == "in_progress" and h.names[0]["ticker"] == "DEVCO"
    assert "1 thesis held: history still loading" in held.admin_line()
    assert held.self_check["rows"] == [] or all(
        r["verdict"] == "held" for r in held.self_check["rows"]
    )

    _onboarding(db, security_id, "done")  # the history finished loading
    filled = run_nightly(db, apply=True, end=END)
    assert filled.held == []
    assert _new_pages(db, ed, thesis.id) == [
        (date(2026, 7, 8), "catchup"),
        (date(2026, 7, 9), "catchup"),
        (END, "nightly"),
    ]


def test_an_ordinary_ingest_fault_is_noted_not_held(db, security_id, tmp_path, monkeypatch):
    thesis, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    _cron_artifact(
        tmp_path,
        END,
        theses=[{"id": str(thesis.id), "name": thesis.name, "recorded": True, "names_errored": 1}],
    )
    r = run_nightly(db, apply=True)
    assert r.held == [] and len(_new_pages(db, ed, thesis.id)) == 3
    assert r.noted == [{"thesis_id": str(thesis.id), "name": thesis.name, "names_errored": 1}]


# --- the self-check -----------------------------------------------------------------------------------------


class _FakeNotifier:
    def __init__(self) -> None:
        self.events = []

    def notify_health(self, event) -> None:
        self.events.append(event)


def _recorded_tonight(db, thesis):
    """Tonight's call of record, as the cron writes it."""
    card = call_for_thesis(db, thesis.id, END, record=True, run_kind="cron")
    db.commit()
    return card


def test_the_self_check_passes_at_the_live_edge(db, security_id, tmp_path, monkeypatch):
    thesis, _, _, _ = _published(db, security_id, monkeypatch, tmp_path)
    _recorded_tonight(db, thesis)
    pager = _FakeNotifier()
    r = run_nightly(db, apply=True, end=END, notifier=pager)
    assert r.self_check["status"] == "passed", r.self_check["rows"]
    assert pager.events == [] and "self-check passed" in r.admin_line()


def test_a_real_mismatch_pages_without_naming_the_thesis(db, security_id, tmp_path, monkeypatch):
    thesis, _, _, _ = _published(db, security_id, monkeypatch, tmp_path)
    card = _recorded_tonight(db, thesis)
    tampered = card.model_copy(update={"counter_case": "a different counter-case"})
    calls_repo.append(db, tampered, thesis.tenant_id, config_hash=config_hash(DEFAULT_CONFIG))
    db.commit()
    pager = _FakeNotifier()
    r = run_nightly(db, apply=True, end=END, notifier=pager)
    assert r.self_check["status"] == "mismatch" and r.self_check["paged"] is True
    (row,) = r.self_check["rows"]
    assert row["verdict"] == "mismatch" and row["differs"] == ["counter_case"]
    (event,) = pager.events
    assert "1 of 1 theses" in event.label and thesis.name not in event.label
    (art,) = _artifacts(tmp_path)
    assert art["attention"] is True
    assert "self-check: 1 thesis differs from the recorded call" in art["admin_line"]


def test_a_row_another_build_recorded_is_skipped_not_paged(db, security_id, tmp_path, monkeypatch):
    thesis, _, _, _ = _published(db, security_id, monkeypatch, tmp_path)
    card = _recorded_tonight(db, thesis)
    tampered = card.model_copy(update={"counter_case": "what the old build said"})
    calls_repo.append(
        db,
        tampered,
        thesis.tenant_id,
        config_hash=config_hash(DEFAULT_CONFIG),
        code_sha="aaaaaaaaaaaa",
    )
    db.commit()
    monkeypatch.setattr(nightly, "get_settings", lambda: SimpleNamespace(image_sha="bbbbbbbbbbbb"))
    pager = _FakeNotifier()
    r = run_nightly(db, apply=True, end=END, notifier=pager)
    (row,) = r.self_check["rows"]
    assert row["verdict"] == "skipped" and "another build" in row["why"]
    assert r.self_check["status"] == "skipped" and pager.events == []


def test_a_dry_run_never_pages(db, security_id, tmp_path, monkeypatch):
    thesis, _, _, _ = _published(db, security_id, monkeypatch, tmp_path)
    card = _recorded_tonight(db, thesis)
    calls_repo.append(
        db,
        card.model_copy(update={"counter_case": "x"}),
        thesis.tenant_id,
        config_hash=config_hash(DEFAULT_CONFIG),
    )
    db.commit()
    pager = _FakeNotifier()
    r = run_nightly(db, end=END, notifier=pager)
    assert r.self_check["status"] == "mismatch" and pager.events == []
