from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from repositories import thesis_repo
from scoreboard.record import derive_thesis_record, scoreboard_records
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import bar, keys_fired
from tests.scoreboard.helpers import persist_thesis as _thesis
from tests.scoreboard.helpers import record_day as _record_day

# The scoring source is the RECORD (the calls log), never a recompute — these tests write a
# controlled log via calls_repo.append at chosen as-ofs (the live shape: dense on cron days, gapped
# on weekends/downtime) and assert the derived episodes, the record-honesty flags, and that the
# whole path writes NOTHING.


def test_gappy_log_boundaries_rearm_and_open_edge(db, security_id):
    """A weekend/cron gap does not blur episode boundaries (membership changes always recorded a
    row); a re-arm is a SECOND episode; a run reaching the record edge is OPEN."""
    thesis = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=4)
    warm_only = [
        insider_event(security_id=security_id, liveness=60).model_copy(
            update={"asof": date(2026, 5, 29)}
        )
    ]

    _, conf2 = keys_fired(security_id, date(2026, 6, 10), conv_liveness=60, conf_liveness=4)

    _record_day(db, thesis, warm_only, date(2026, 5, 29))  # warming BEFORE the arm (not censored)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))  # armed
    _record_day(db, thesis, [conv, conf], date(2026, 6, 2))  # armed
    # gap: 06-03 .. 06-07 never recorded (weekend + a missed cron)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 8))  # confirmation lapsed -> warming
    _record_day(db, thesis, [conv, conf, conf2], date(2026, 6, 10))  # fresh breakout -> re-armed

    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 10), 110.0)
    bar(db, security_id, date(2026, 6, 12), 121.0)

    record, snaps = derive_thesis_record(db, thesis, date(2026, 6, 12))
    assert [s.asof for s in snaps][0] == date(2026, 5, 29)
    assert len(record.episodes) == 2

    first, second = record.episodes
    assert first.episode.arm_date == date(2026, 6, 1)
    assert first.episode.dearm_date == date(2026, 6, 8)  # exact: the change day was recorded
    assert first.episode.close_reason == "arm_until_lapsed"
    assert first.episode.warm_date == date(2026, 5, 29)
    assert first.status == "closed"
    assert first.censored_start is False  # the record saw the warming BEFORE this arm

    assert second.episode.arm_date == date(2026, 6, 10)
    assert second.status == "open" and second.episode.dearm_date is None
    assert second.episode.close_reason == "window_end"


def test_censored_start_marks_a_record_that_began_mid_arm(db, security_id):
    """Armed already on the thesis's FIRST recorded card -> the true arm date is unknowable from the
    record: marked censored, never reconstructed (no backfill)."""
    thesis = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=10)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 3))  # the record's first row, already armed

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    assert len(record.episodes) == 1
    assert record.episodes[0].censored_start is True
    assert record.episodes[0].episode.arm_date == date(2026, 6, 3)  # the record start, not the arm


def test_same_asof_supersede_latest_wins(db, security_id):
    """A fact-correction re-run at the SAME as-of supersedes (latest seq wins) — the superseded card
    never shapes an episode."""
    thesis = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=10)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))  # armed...
    _record_day(db, thesis, [conv], date(2026, 6, 1))  # ...corrected same-day: warming only

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    assert record.episodes == []  # the corrected record never armed
    assert record.current_state == "warming"


def test_asof_cap_no_future_leak(db, security_id):
    """A scrubbed-back asof sees neither later record rows nor later bars: the dearm recorded after
    asof is invisible (the episode reads OPEN), and a future price spike never becomes exit/peak."""
    thesis = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=30)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))
    _record_day(db, thesis, [conv], date(2026, 6, 15))  # the (future) dearm row

    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 5), 110.0)
    bar(db, security_id, date(2026, 6, 20), 200.0)  # the future spike that must not leak

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 10))
    (ep,) = record.episodes
    assert ep.status == "open"  # the 06-15 dearm is after asof: not yet part of the record
    out = ep.outcome
    assert out.entry_close == 100.0
    assert out.exit_close == 110.0 and out.exit_date == date(2026, 6, 5)  # last bar <= asof
    assert out.truncated is True  # exit_by ran past the available (capped) data
    assert out.peak_return == pytest.approx(0.10)  # the 200.0 bar never entered the window
    assert ep.matured is False  # exit_by (07-01) has not elapsed at 06-10


def test_maturity_judged_only_at_exit_by(db, security_id):
    """closed-but-immature (early de-arm, exit_by pending) and open-but-matured (record edge stale
    past exit_by) are both real live shapes; ``matured`` tracks the episode's OWN deadline only."""
    thesis = _thesis(db, security_id)
    # exit_by = 06-01 + 5d = 06-06; arm_until = 06-01 + 1d = 06-02
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=5, conf_liveness=1)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))
    _record_day(db, thesis, [conv], date(2026, 6, 3))  # early de-arm (confirmation lapsed)
    bar(db, security_id, date(2026, 6, 1), 100.0)

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 4))
    (ep,) = record.episodes
    assert ep.status == "closed" and ep.matured is False  # judged at 06-06, not at the de-arm

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 10))
    (ep,) = record.episodes
    assert ep.matured is True

    # open-but-matured: a second thesis whose record edge went stale while still armed
    thesis2 = _thesis(db, security_id, thesis_id=uuid.uuid4())
    conv2, conf2 = keys_fired(security_id, date(2026, 6, 1), conv_liveness=5, conf_liveness=30)
    _record_day(db, thesis2, [conv2, conf2], date(2026, 6, 1))  # then the cron went dark
    record2, _ = derive_thesis_record(db, thesis2, date(2026, 6, 10))
    (ep2,) = record2.episodes
    assert ep2.status == "open" and ep2.matured is True


def test_archived_included_by_default_and_excludable(db, security_id):
    """Archiving stops accrual; it never erases the record. Default include, explicit exclude."""
    thesis = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))
    thesis_repo.set_archived(db, thesis.id, True)
    db.commit()

    result, _, _ = scoreboard_records(db, date(2026, 6, 5))
    (rec,) = [t for t in result.theses if t.thesis_id == thesis.id]
    assert rec.archived is True and len(rec.episodes) == 1

    result, _, _ = scoreboard_records(db, date(2026, 6, 5), include_archived=False)
    assert [t for t in result.theses if t.thesis_id == thesis.id] == []


def test_unreadable_card_is_fault_isolated(db, security_id):
    """The log outlives schema changes (DomainModel is extra=forbid): one unreadable historical card
    surfaces as that thesis's visible error — siblings score unaffected, nothing raises."""
    bad = _thesis(db, security_id)
    good = _thesis(db, security_id, thesis_id=uuid.uuid4())
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    _record_day(db, good, [conv, conf], date(2026, 6, 1))
    with db.cursor() as cur:  # a card recorded under some other (older/newer) CallCard schema
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card) "
            "VALUES (%s, %s, %s, 'armed', 'core_entry', '{\"bogus_key\": 1}'::jsonb)",
            (DEFAULT_TENANT_ID, bad.id, date(2026, 6, 1)),
        )
    db.commit()

    result, _, _ = scoreboard_records(db, date(2026, 6, 5))
    by_id = {t.thesis_id: t for t in result.theses}
    assert by_id[bad.id].error is not None and by_id[bad.id].episodes == []
    assert by_id[good.id].error is None and len(by_id[good.id].episodes) == 1


def test_scoreboard_writes_nothing(db, security_id):
    """Compute-on-read: COUNT THE TABLES before and after — the whole derivation appends no call,
    no decision, no fact (the read-path twin of the idempotency discipline)."""
    thesis = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))
    bar(db, security_id, date(2026, 6, 1), 100.0)

    def counts():
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM calls) AS c,"
                " (SELECT count(*) FROM operator_decision) AS d,"
                " (SELECT count(*) FROM fact_price_eod) AS p"
            )
            r = cur.fetchone()
            return (r["c"], r["d"], r["p"])

    before = counts()
    scoreboard_records(db, date(2026, 6, 5))
    assert counts() == before


def test_triggers_at_arm_carry_the_why(db, security_id):
    """Every episode carries the arm-date card's member trigger evidence (invariant #6 — if you
    can't show the work, don't surface the result)."""
    thesis = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    (ep,) = record.episodes
    labels = [t.label for t in ep.triggers_at_arm]
    assert any("insider" in label.lower() or "bought" in label.lower() for label in labels)


def test_freeze_era_arm_is_flagged_visible_and_counted(db, security_id):
    """2d spot check at the record level: an arm inside the 2026-07 freeze window flags (B1, with
    its note; the legacy stamp stays raw None) while a pre-freeze arm stays clean; the rollup count
    rides the result and the ledger keeps BOTH episodes (recall-is-sacred cousin)."""
    frozen = _thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 7, 10), conv_liveness=30, conf_liveness=10)
    _record_day(db, frozen, [conv, conf], date(2026, 7, 10))

    clean = _thesis(db, security_id, thesis_id=uuid.uuid4())
    conv2, conf2 = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    _record_day(db, clean, [conv2, conf2], date(2026, 6, 1))

    result, _, _ = scoreboard_records(db, date(2026, 7, 15))
    by_id = {t.thesis_id: t for t in result.theses}
    (flagged,) = by_id[frozen.id].episodes
    assert flagged.freeze_era is True and flagged.ingest_flagged is True
    assert flagged.arm_ingest_fresh is None  # legacy append: raw, never coerced to a judgement
    assert "freeze window" in (flagged.ingest_note or "")
    (ok,) = by_id[clean.id].episodes
    assert ok.freeze_era is False and ok.ingest_flagged is False and ok.ingest_note is None
    assert result.n_ingest_flagged == 1 and result.n_episodes == 2


def test_zero_episode_thesis_reports_coverage_and_warming(db, security_id):
    """A never-armed thesis still reports its record span and the accruing warming window — the
    honest launch state is a first-class render, not an empty error."""
    thesis = _thesis(db, security_id)
    warm = [
        insider_event(security_id=security_id, liveness=60).model_copy(
            update={"asof": date(2026, 6, 1)}
        )
    ]
    _record_day(db, thesis, warm, date(2026, 6, 1))
    _record_day(db, thesis, warm, date(2026, 6, 2))

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    assert record.episodes == []
    assert record.first_call_asof == date(2026, 6, 1)
    assert record.last_call_asof == date(2026, 6, 2)
    assert record.current_state == "warming"
    assert record.warming_since == date(2026, 6, 1)
