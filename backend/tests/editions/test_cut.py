"""S7 — THE AUTOMATIC NEW-EDITION CUT, inside the nightly editions job, COUNTED AT THE TABLE.

Only two things start a trial: the rules changed (the engine fingerprint differs from the last one checked for
the current edition) or a data repair was journaled since its cut. No event: no trial, no rows. A rules trial
re-runs the edition's re-run nights at its own cut (the data held fixed): equal output records
``trial_no_change`` once (never re-tried nightly); different output cuts a new edition labeled by the declared
rule — or "unexplained change", published marked, with a page. A repair trial compares the new edition N with
every page the edition holds. A cut closes the old edition (frozen, inked ``running_at_close``), and the
default view moves to the newest on its own. At most one cut per night.

Every run here uses ONE worker (``trial_workers=1``): the trial then runs in-process, where a test's stand-in
for a rule change (a patched assembly) reaches it.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import editions.cut as cut
import editions.engine as engine
import editions.nightly as nightly
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain import rule_log
from domain.rule_log import RuleEntry
from editions.nightly import run_nightly
from repositories import data_repair_repo, editions_repo
from tests.editions.fixtures import seed_big_buy
from tests.editions.test_nightly import END, _counts, _published

_REAL_ONE_PER_NIGHT = cut.one_per_night_refusal  # kept before the autouse fixture replaces it

RULE = RuleEntry(
    id="2026-10-test-window",
    date=date(2026, 10, 23),
    label="insider window widened",
    description="A test rule: the counter-case gains a sentence.",
)


class _Pager:
    """Records every notifier call (the quiet edition line and the health page)."""

    def __init__(self) -> None:
        self.health: list = []
        self.editions: list = []

    def notify_health(self, event) -> None:
        self.health.append(event)

    def notify_edition(self, event) -> None:
        self.editions.append(event)


@pytest.fixture(autouse=True)
def _not_the_same_night(monkeypatch):
    """Every edition in these tests is published TODAY, which the one-cut-per-night rule would refuse — the
    rule has its own test below (with the real function)."""
    monkeypatch.setattr(cut, "one_per_night_refusal", lambda edition, now: None)


def _new_fingerprint(monkeypatch, tag: str = "rules") -> dict:
    """A deploy that changed the rules: the running engine's fingerprint differs from the edition's."""
    real = nightly.engine_fingerprint(nightly.DEFAULT_CONFIG)
    changed = {**real, "fingerprint": (tag * 64)[:64]}
    monkeypatch.setattr(nightly, "engine_fingerprint", lambda cfg: dict(changed))
    return changed


def _change_the_output(monkeypatch) -> None:
    """A rule change that moves every card: the counter-case gains a sentence."""
    real = engine.assemble_from_pit

    def changed(pit, thesis, night, cfg):
        card = real(pit, thesis, night, cfg)
        return card.model_copy(update={"counter_case": card.counter_case + " A new rule."})

    monkeypatch.setattr(engine, "assemble_from_pit", changed)


def _spy_trials(monkeypatch) -> list:
    calls: list = []
    real_rules, real_new = cut.rules_trial, cut.new_run

    def rules(*a, **k):
        calls.append("rules")
        return real_rules(*a, **k)

    def new(*a, **k):
        calls.append("new")
        return real_new(*a, **k)

    monkeypatch.setattr(cut, "rules_trial", rules)
    monkeypatch.setattr(cut, "new_run", new)
    return calls


def _nightly(db, tmp_path, **kw):
    return run_nightly(
        db,
        apply=True,
        end=END,
        trial_workers=1,
        cut_runs_dir=tmp_path / "cut_runs",
        notifier=kw.pop("notifier", _Pager()),
        **kw,
    )


def _events(db, edition_id, kind):
    out = editions_repo.events(db, edition_id, kind=kind)
    db.commit()
    return out


def _pages_of(db, edition_id, kind: str | None = None) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM edition_call WHERE edition_id = %s "
            "AND (%s::text IS NULL OR page_kind = %s)",
            (edition_id, kind, kind),
        )
        n = cur.fetchone()["n"]
    db.commit()
    return n


# --- no event, no trial ---------------------------------------------------------------------------------------


def test_no_rule_change_and_no_repair_runs_no_trial_and_writes_no_trial_rows(
    db, security_id, tmp_path, monkeypatch
):
    _, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    trials = _spy_trials(monkeypatch)
    before = _counts(db)
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "no_event" and trials == []
    after = _counts(db)
    assert after["edition"] == before["edition"] == 1
    assert _events(db, ed.id, "trial_no_change") == []
    assert after["edition_cause"] == before["edition_cause"] == 0
    # tonight's pages go on as usual (the S5 job is untouched by an eventless night)
    assert r.n_pages == 3 and "trial" not in r.admin_line()


def test_a_zero_change_repair_is_journaled_but_starts_no_trial(
    db, security_id, tmp_path, monkeypatch
):
    _published(db, security_id, monkeypatch, tmp_path)
    data_repair_repo.journal(db, tool="repair_test", summary="found nothing to do", rows_changed=0)
    trials = _spy_trials(monkeypatch)
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "no_event" and trials == []


# --- a rules change ------------------------------------------------------------------------------------------


def test_a_fingerprint_change_with_the_same_output_records_no_change_once(
    db, security_id, tmp_path, monkeypatch
):
    """The deploy changed the fingerprint but no card: no edition, one ``trial_no_change`` carrying the new
    fingerprint — and the next night runs NO trial."""
    # this scenario is "no rule was declared": pin the log empty so it does not depend on the shipped entries
    monkeypatch.setattr(rule_log, "RULE_LOG", ())
    _, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    changed = _new_fingerprint(monkeypatch)
    trials = _spy_trials(monkeypatch)
    before = _counts(db)
    rerun = _pages_of(db, ed.id, "rerun")
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "no_change" and trials == ["rules"]
    assert r.cut["rules_trial"]["pages"]["compared"] == rerun > 0  # every re-run page, re-tested
    assert r.cut["rules_trial"]["pages"]["pages_changed"] == 0
    assert r.cut["rules_trial"]["bodies"] == "frozen"  # the publish froze the thesis bodies
    after = _counts(db)
    assert after["edition"] == before["edition"] == 1  # no new edition
    (ev,) = _events(db, ed.id, "trial_no_change")
    assert ev.detail["engine"]["fingerprint"] == changed["fingerprint"]
    assert ev.detail["rule_log"] == []
    assert r.n_pages == 3  # then tonight's pages, as usual
    assert "trial: the rules changed no page — no new edition" in r.admin_line()

    again = _nightly(db, tmp_path)  # the next night: the same fingerprint was checked
    assert again.cut["status"] == "no_event" and trials == ["rules"]
    assert len(_events(db, ed.id, "trial_no_change")) == 1


def test_an_older_fingerprint_version_counts_as_a_change_and_is_tried_once(
    db, security_id, tmp_path, monkeypatch
):
    """An edition stamped by fingerprint version 1 (before S7) cannot be compared: the first run tries it and
    records the version-2 fingerprint, so the next run does not."""
    _, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    real = nightly.engine_fingerprint(nightly.DEFAULT_CONFIG)
    old = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1)
    db.commit()
    legacy = dataclasses.replace(
        old, reason={**old.reason, "engine": {"fingerprint": real["fingerprint"]}}
    )
    ev = cut.detect(db, legacy, engine=real)
    db.commit()
    assert ev.rules and "fingerprint version 1" in (ev.rules_why or "")
    # the published edition itself carries version 2: nothing to try
    assert not cut.detect(db, old, engine=real).rules
    db.commit()


def test_a_declared_rule_that_moves_history_cuts_a_labeled_edition_and_closes_the_old_one(
    db, security_id, tmp_path, monkeypatch
):
    thesis, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    _change_the_output(monkeypatch)
    monkeypatch.setattr(rule_log, "RULE_LOG", (RULE,))
    pager = _Pager()
    old_pages = _pages_of(db, ed.id)
    before = _counts(db)

    r = _nightly(db, tmp_path, notifier=pager)
    assert r.cut["status"] == "cut", r.cut
    after = _counts(db)
    assert after["edition"] == 2 and after["calls"] == before["calls"]
    one, two = editions_repo.list_editions(db, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert two.number == 2 and two.parent_id == one.id and two.causes == ("rule_change",)
    assert (
        two.label
        == "rule change: insider window widened · includes data that arrived since Edition 1"
    )
    assert two.reason["rule_log"] == [RULE.id]
    assert two.reason["trial"]["rules"] is True and two.reason["trial"]["unexplained"] is False
    assert two.data_known_by == r.known_by and two.last_night == END
    # the old edition is CLOSED (an appended event), frozen and inked
    assert one.closed_at is not None
    (closed,) = _events(db, one.id, "closed")
    assert closed.detail["by_edition"] == 2
    assert _pages_of(db, one.id) == old_pages  # tonight's page went on the NEW edition only
    # the default view moved to the newest edition on its own (the API reads ``newest``; tests/app pins it)
    assert editions_repo.current_edition(db, tenant_id=DEFAULT_TENANT_ID).number == 2
    assert editions_repo.newest(db, tenant_id=DEFAULT_TENANT_ID).number == 2
    notice = editions_repo.cut_notice(
        db, tenant_id=DEFAULT_TENANT_ID, now=datetime.now(timezone.utc)
    )
    db.commit()
    assert notice is not None and (notice.number, notice.previous) == (2, 1)
    assert notice.line().startswith("Edition 2 · rule change: insider window widened")
    # the old edition's open episodes are inked as it closes (none open in this fixture: counted, not assumed)
    assert r.cut["new_edition"] == 2
    # one QUIET line, no page
    assert pager.health == [] and len(pager.editions) == 1
    assert "🚨" not in pager.editions[0].label and "Edition 2" in pager.editions[0].label
    assert r.admin_line().startswith("Edition 2 cut · rule change: insider window widened")
    # a join date carried over from the old edition, the thesis body frozen with the new one
    assert two.n_entries == one.n_entries
    assert editions_repo.frozen_bodies(db, two.id) is not None
    db.commit()


def test_the_trial_stores_what_it_proved_and_what_changed_reads_it_back(
    db, security_id, tmp_path, monkeypatch
):
    """A rule change that ARMS the company from 06-22 on: the rules trial proves those company-nights and the
    episode, stores them with the new edition, and the "what changed" comparison of the pair names the
    cause on every change."""
    from scoreboard.diff import edition_diff

    _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    monkeypatch.setattr(rule_log, "RULE_LOG", (RULE,))
    real = engine.assemble_from_pit

    from domain.call import MemberCall
    from domain.enums import Grade, State, Verdict

    def arms(pit, thesis, night, cfg):
        card = real(pit, thesis, night, cfg)
        if night < date(2026, 6, 22):
            return card
        member = MemberCall(
            security_id=security_id,
            verdict=Verdict.CORE_ENTRY,
            conviction_grade=Grade.CORE,
            entry_grade=Grade.CORE,
            exit_by=date(2026, 9, 1),
        )
        return card.model_copy(
            update={"armed_members": [member], "state": State.ARMED, "verdict": Verdict.CORE_ENTRY}
        )

    monkeypatch.setattr(engine, "assemble_from_pit", arms)
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "cut", r.cut
    one = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1)
    two = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=2)
    db.commit()
    stored = _counts(db)["edition_cause"]
    assert stored > 0 and two.reason["trial"]["n_proven"] == stored
    assert two.reason["trial"]["n_company_nights_changed"] > 0
    d = edition_diff(db, before=one, after=two, through=one.last_night)
    db.commit()
    assert d.causes is True
    (th,) = [t for t in d.theses if t.companies]
    (co,) = th.companies
    assert co.nights and {n.cause for n in co.nights} == {"rule_change"}
    assert co.episodes and {e.cause for e in co.episodes} == {"rule_change"}
    # the earlier pair (the record -> Edition 2) has no single trial: no cause anywhere
    zero = edition_diff(db, before=None, after=two, through=one.last_night)
    db.commit()
    assert zero.causes is False
    assert {n.cause for t in zero.theses for c in t.companies for n in c.nights} <= {None}


def test_an_undeclared_change_is_published_marked_unexplained_and_pages(
    db, security_id, tmp_path, monkeypatch
):
    _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    _change_the_output(monkeypatch)
    pager = _Pager()
    r = _nightly(db, tmp_path, notifier=pager)
    assert r.cut["status"] == "cut" and r.cut["unexplained"] is True
    two = editions_repo.newest(db, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert two.label.startswith("unexplained change · includes data that arrived since Edition 1")
    assert two.causes == ("code_drift",)
    (page,) = pager.health  # the page, through notify_health — never the quiet line
    assert pager.editions == []
    assert "UNEXPLAINED" in page.label and "Edition 2" in page.label
    assert r.payload()["attention"] is True


# --- a data repair --------------------------------------------------------------------------------------------


def test_an_appended_repair_that_moves_history_cuts_a_data_repair_edition(
    db, security_id, tmp_path, monkeypatch
):
    """A repair's rows are stamped after the edition's cut, so only N (at now) sees them: N differs on a
    night the edition covers -> a "data repair" edition, the late data included and said so."""
    _, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    trials = _spy_trials(monkeypatch)
    # the repair: a second big buy, public long ago, appended now (an append-only repair)
    seed_big_buy(
        db,
        security_id,
        accession="0000000000-26-000099",
        valid_from=date(2026, 6, 22),
        accepted=datetime(2026, 6, 23, 21, 0, tzinfo=timezone.utc),
        recorded_at=db_now(db),
    )
    db.commit()
    data_repair_repo.journal(
        db, tool="repair_test", summary="appended a missed insider filing", rows_changed=1
    )
    r = _nightly(db, tmp_path)
    assert trials == ["new"]  # no rules trial: the fingerprint did not change
    assert r.cut["status"] == "cut", r.cut
    assert r.cut["new_run"]["versus_previous"]["pages_changed"] > 0
    two = editions_repo.newest(db, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert two.causes == ("data_repair",)
    assert two.label == (
        "data repair: appended a missed insider filing · includes data that arrived since Edition 1"
    )
    assert two.reason["trial"]["repair"] is True and two.reason["trial"]["rules"] is False
    # a repair proves nothing per company-night: no stored cause rows (the diff reads "data_repair")
    assert _counts(db)["edition_cause"] == 0
    assert editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1).closed_at
    db.commit()


def test_a_repair_that_changes_no_history_cuts_nothing_and_is_not_retried(
    db, security_id, tmp_path, monkeypatch
):
    _, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    data_repair_repo.journal(
        db, tool="repair_test", summary="re-versioned rows no call reads", rows_changed=4
    )
    trials = _spy_trials(monkeypatch)
    before = _counts(db)
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "no_change" and trials == ["new"]
    assert _counts(db)["edition"] == before["edition"] == 1
    (ev,) = _events(db, ed.id, "trial_no_change")
    assert [x["summary"] for x in ev.detail["repairs"]] == ["re-versioned rows no call reads"]
    runs = tmp_path / "cut_runs"
    assert (list(runs.glob("*-cut")) if runs.exists() else []) == []  # N was a comparison: not kept
    again = _nightly(db, tmp_path)
    assert again.cut["status"] == "no_event" and trials == ["new"]


# --- the guards -----------------------------------------------------------------------------------------------


def test_at_most_one_cut_per_night(db, security_id, tmp_path, monkeypatch):
    _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    _change_the_output(monkeypatch)
    assert _nightly(db, tmp_path).cut["status"] == "cut"
    # a second event the same night — the real one-per-night rule now applies
    monkeypatch.setattr(cut, "one_per_night_refusal", _REAL_ONE_PER_NIGHT)
    data_repair_repo.journal(db, tool="repair_test", summary="another repair", rows_changed=2)
    before = _counts(db)
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "refused" and "at most one new edition per night" in r.cut["reason"]
    assert _counts(db)["edition"] == before["edition"] == 2
    assert "no trial tonight" in r.admin_line()


def test_the_one_per_night_rule_reads_the_market_day():
    ed = editions_repo.Edition(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        number=1,
        parent_id=None,
        label="x",
        causes=("initial",),
        reason={},
        engine_fingerprint="f",
        config_hash="c",
        code_sha=None,
        data_known_by=datetime(2026, 10, 22, 23, 0, tzinfo=timezone.utc),
        first_night=date(2026, 6, 1),
        # 23:30 ET on Oct 22 (03:30 UTC on Oct 23)
        created_at=datetime(2026, 10, 23, 3, 30, tzinfo=timezone.utc),
    )
    # the same market day (Oct 22 ET), even though UTC has rolled over
    assert _REAL_ONE_PER_NIGHT(ed, datetime(2026, 10, 23, 3, 50, tzinfo=timezone.utc))
    # the next market day
    assert _REAL_ONE_PER_NIGHT(ed, datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)) is None


def test_a_closed_edition_is_frozen(db, security_id, tmp_path, monkeypatch):
    """COUNT ITS PAGES: after the cut, and after another night, the closed edition holds exactly what it held."""
    _, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    _change_the_output(monkeypatch)
    pages = _pages_of(db, ed.id)
    assert _nightly(db, tmp_path).cut["status"] == "cut"
    assert _pages_of(db, ed.id) == pages
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM edition_outcome WHERE edition_id = %s", (ed.id,))
        inked = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM edition_event WHERE edition_id = %s", (ed.id,))
        events = cur.fetchone()["n"]
    db.commit()
    later = run_nightly(
        db, apply=True, end=END + timedelta(days=3), trial_workers=1, notifier=_Pager()
    )
    assert later.edition.number == 2  # the new edition grows; the closed one never does
    assert _pages_of(db, ed.id) == pages
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM edition_outcome WHERE edition_id = %s", (ed.id,))
        assert cur.fetchone()["n"] == inked
        cur.execute("SELECT count(*) AS n FROM edition_event WHERE edition_id = %s", (ed.id,))
        assert cur.fetchone()["n"] == events
    db.commit()


def test_a_pass_that_takes_the_call_of_record_lock_before_the_write_costs_the_cut(
    db, security_id, tmp_path, monkeypatch
):
    """The lock probe is asked again just before the write: a pass that started mid-trial wins, nothing is
    written (no edition, no ``trial_no_change``), and the run directory N wrote is not kept."""
    _, _, _, ed = _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    _change_the_output(monkeypatch)
    engine_now = nightly.engine_fingerprint(nightly.DEFAULT_CONFIG)
    before = _counts(db)
    with pytest.raises(cut.CutRefused, match="call-of-record lock"):
        cut.run_cut(
            db,
            apply=True,
            known_by=db_now(db),
            end=END,
            work_dir=tmp_path / "work",
            runs_dir=tmp_path / "cut_runs",
            workers=1,
            notifier=_Pager(),
            lock_probe=lambda: "cron",
            engine=engine_now,
        )
    db.rollback()
    assert _counts(db) == before and _events(db, ed.id, "trial_no_change") == []
    assert list((tmp_path / "cut_runs").glob("*-cut")) == []


def test_the_cuts_one_write_closes_and_inks_the_old_edition_or_writes_nothing(
    db, security_id, tmp_path
):
    """``store_edition(close=E)`` — the cut's ONE transaction: the new edition (parent E), its proven causes,
    E's ``closed`` event and E's ink — an episode still RUNNING at E's last night inked ``running_at_close``.
    A fault anywhere leaves nothing (E is not closed, nothing appended)."""
    from editions.run import store_edition
    from tests.editions.test_ink import _edition

    long_sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, 'LONGCO', '0007654399', '2026-01-01')",
            (long_sid, DEFAULT_TENANT_ID),
        )
    db.commit()
    thesis, long_thesis, ed = _edition(db, security_id, long_sid=long_sid)
    pages = editions_repo.pages_for_thesis(db, ed.id, long_thesis.id, upto=date.max)
    db.commit()
    now = db_now(db)
    db.commit()
    meta = {
        "known_by": now.isoformat(),
        "created_at": now.isoformat(),
        "engine": {"fingerprint": "fp-2", "config_hash": "cfg-2", "code_sha": None},
    }
    plan = {
        "number": 2,
        "label": "rule change: x · includes data that arrived since Edition 1",
        "causes": ["rule_change"],
        "pages": pages,
        "entries": [],
        "n_pages": len(pages),
    }
    bad = [
        {"thesis_id": long_thesis.id, "security_id": long_sid, "night": pages[0].asof, "cause": "x"}
    ]
    before = _counts(db)
    with pytest.raises(ValueError):
        store_edition(
            db,
            tenant_id=DEFAULT_TENANT_ID,
            plan=plan,
            meta=meta,
            out=tmp_path,
            parent=ed,
            cause_rows=bad,
            close=ed,
        )
    assert _counts(db) == before  # all or nothing
    good = [{**bad[0], "cause": "rule_change"}]
    new_id = store_edition(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        plan=plan,
        meta=meta,
        out=tmp_path,
        parent=ed,
        cause_rows=good,
        close=ed,
    )
    after = _counts(db)
    assert after["edition"] == before["edition"] + 1 and after["edition_cause"] == 1
    one = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1)
    two = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=2)
    assert two.id == new_id and two.parent_id == one.id and one.closed_at is not None
    inked = editions_repo.outcomes_for_thesis(db, one.id, long_thesis.id)
    db.commit()
    assert {o.kind for o in inked.values()} == {
        "running_at_close"
    }  # its window runs past the last night
    assert after["edition_outcome"] == before["edition_outcome"] + len(inked) + len(
        editions_repo.outcomes_for_thesis(db, one.id, thesis.id)
    )
    db.commit()


def test_a_cut_waits_while_a_member_is_still_loading(db, security_id, tmp_path, monkeypatch):
    from tests.editions.test_nightly import _onboarding

    _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    _change_the_output(monkeypatch)
    _onboarding(db, security_id, "in_progress")
    before = _counts(db)
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "deferred" and "history still loading" in r.cut["reason"]
    assert _counts(db)["edition"] == before["edition"] == 1
    assert "a new edition is due but waits" in r.admin_line()


def test_a_trial_fault_is_fail_open(db, security_id, tmp_path, monkeypatch):
    _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("mirror unreadable")

    monkeypatch.setattr(cut, "rules_trial", boom)
    r = _nightly(db, tmp_path)
    assert r.cut["status"] == "failed" and "mirror unreadable" in r.cut["reason"]
    assert r.status == "done" and r.n_pages == 3  # tonight's pages still went on the edition
    assert r.payload()["attention"] is True


def test_a_dry_run_cut_writes_nothing(db, security_id, tmp_path, monkeypatch):
    _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    _change_the_output(monkeypatch)
    before = _counts(db)
    r = run_nightly(db, end=END, trial_workers=1, notifier=_Pager())
    assert r.cut["status"] == "cut" and r.cut["mode"] == "dry_run"
    assert _counts(db) == before
    assert "would be cut (dry run)" in r.cut["part"]


def test_the_manual_cli_is_a_dry_run_by_default(db, security_id, tmp_path, monkeypatch, capsys):
    from editions.run import main

    _published(db, security_id, monkeypatch, tmp_path)
    _new_fingerprint(monkeypatch)
    monkeypatch.setattr(cut, "engine_fingerprint", nightly.engine_fingerprint)
    monkeypatch.chdir(tmp_path)
    before = _counts(db)
    main(["cut", "--workers", "1", "--end", END.isoformat()])
    out = capsys.readouterr().out
    assert "trial (dry run): the rules changed no page" in out
    assert _counts(db) == before
