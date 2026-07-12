from __future__ import annotations

from datetime import date

import pytest

from repositories import decisions_repo
from scoreboard.record import derive_thesis_record
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

# The operator track: the decision log joined to the episodes it answered. Voids resolve first;
# prices carry inferred flags (a logged fill wins, a close fallback is flagged, a thesis-level row
# stays unpriced); a take inside the armed window answers the episode; a take against a not-armed
# stance is an off-record OVERRIDE span carrying the stance frozen at logging time.


def _armed_thesis(db, security_id, *, fire=date(2026, 6, 1)):
    """One thesis armed from ``fire``, still armed at the record edge (a long window)."""
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, fire, conv_liveness=120, conf_liveness=120)
    record_day(db, thesis, [conv, conf], fire)
    return thesis


def _decide(db, thesis, action, d, **kw):
    row = decisions_repo.append(
        db, thesis_id=thesis.id, action=action, decision_date=d, tenant_id=thesis.tenant_id, **kw
    )
    db.commit()
    return row


def test_take_close_span_realized_at_logged_prices(db, security_id):
    thesis = _armed_thesis(db, security_id)
    _decide(db, thesis, "take", date(2026, 6, 2), security_id=security_id, price=100.0, shares=10)
    _decide(db, thesis, "close", date(2026, 6, 9), security_id=security_id, price=112.0)

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    (ep,) = record.episodes
    op = ep.operator
    assert op is not None and op.action == "took"
    assert op.entry_price == 100.0 and op.entry_inferred is False
    assert op.exit_price == 112.0 and op.exit_inferred is False
    assert op.exit_date == date(2026, 6, 9) and op.running is False
    assert op.operator_return == pytest.approx(0.12)
    assert record.n_takes == 1 and record.n_passes == 0 and record.operator_spans == []


def test_open_take_runs_to_asof_with_inferred_exit(db, security_id):
    thesis = _armed_thesis(db, security_id)
    _decide(db, thesis, "take", date(2026, 6, 2), security_id=security_id, price=100.0)
    bar(db, security_id, date(2026, 6, 10), 105.0)
    bar(db, security_id, date(2026, 6, 20), 130.0)  # beyond asof: must not leak

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    op = record.episodes[0].operator
    assert op.running is True and op.exit_date is None
    assert op.exit_price == 105.0 and op.exit_inferred is True  # last close <= asof
    assert op.operator_return == pytest.approx(0.05)


def test_priceless_take_infers_entry_from_the_close(db, security_id):
    thesis = _armed_thesis(db, security_id)
    _decide(db, thesis, "take", date(2026, 6, 2), security_id=security_id)  # no fill price logged
    bar(db, security_id, date(2026, 6, 3), 102.0)  # first close on/after the take (entry parity)
    bar(db, security_id, date(2026, 6, 10), 107.1)

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    op = record.episodes[0].operator
    assert op.entry_price == 102.0 and op.entry_inferred is True
    assert op.operator_return == pytest.approx(107.1 / 102.0 - 1)


def test_voided_take_is_excluded_and_counted(db, security_id):
    thesis = _armed_thesis(db, security_id)
    take = _decide(db, thesis, "take", date(2026, 6, 2), security_id=security_id, price=100.0)
    _decide(db, thesis, "void", date(2026, 6, 3), voids=take["id"])

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    assert record.episodes[0].operator is None  # the honest capture gap, not a phantom take
    assert record.n_takes == 0 and record.n_voided == 1
    assert record.operator_spans == []


def test_pass_attaches_to_the_member_and_thesis_level_to_the_headline(db, security_id):
    thesis = _armed_thesis(db, security_id)
    _decide(db, thesis, "pass", date(2026, 6, 3), security_id=security_id, reason="too extended")

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    op = record.episodes[0].operator
    assert op is not None and op.action == "passed" and op.reason == "too extended"
    assert op.operator_return is None  # a pass buys nothing; the episode's outcome sits beside it
    assert record.n_passes == 1

    # a second armed thesis, passed at THESIS level: lands on the headline episode
    thesis2 = _armed_thesis(db, persist_second_security(db))
    _decide(db, thesis2, "pass", date(2026, 6, 3))  # security_id None
    record2, _ = derive_thesis_record(db, thesis2, date(2026, 6, 15))
    op2 = record2.episodes[0].operator
    assert op2 is not None and op2.action == "passed" and op2.thesis_level is True


def persist_second_security(db):
    import uuid

    from db.session import DEFAULT_TENANT_ID

    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "OTHRCO", "0007654321", "2026-01-01"),
        )
    db.commit()
    return sid


def test_override_take_rides_off_record_with_frozen_stance(db, security_id):
    """A take while the platform said watching answers NO episode: an operator_span with
    override=True and the stance the take row froze at logging time."""
    thesis = persist_thesis(db, security_id)  # incubating — no armed window at all
    record_day(db, thesis, [], date(2026, 6, 1))
    _decide(
        db,
        thesis,
        "take",
        date(2026, 6, 2),
        security_id=security_id,
        price=125.0,
        call_state="incubating",
        call_verdict="watching",
    )
    bar(db, security_id, date(2026, 6, 10), 150.0)

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    assert record.episodes == []
    (span,) = record.operator_spans
    assert span.override is True
    assert span.call_state_at_take == "incubating" and span.call_verdict_at_take == "watching"
    assert span.running is True and span.operator_return == pytest.approx(0.20)
    assert record.n_overrides == 1


def test_take_before_the_arm_is_off_record_not_episode_attached(db, security_id):
    """A take DATED before the arm window opened never attaches to the episode (the window is
    [arm_date, dearm/asof]) — it rides off-record with whatever stance it froze."""
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 5), conv_liveness=120, conf_liveness=120)
    record_day(db, thesis, [conv], date(2026, 6, 1))  # warming first
    record_day(db, thesis, [conv, conf], date(2026, 6, 5))  # armed later
    _decide(
        db,
        thesis,
        "take",
        date(2026, 6, 2),
        security_id=security_id,
        price=90.0,
        call_state="warming",
        call_verdict="not_yet",
    )

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    (ep,) = record.episodes
    assert ep.operator is None
    (span,) = record.operator_spans
    assert span.override is True and span.call_state_at_take == "warming"


def test_decisions_after_asof_are_invisible(db, security_id):
    thesis = _armed_thesis(db, security_id)
    _decide(db, thesis, "take", date(2026, 6, 20), security_id=security_id, price=100.0)

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    assert record.episodes[0].operator is None
    assert record.n_takes == 0 and record.operator_spans == []


def test_thesis_level_take_stays_unpriced_but_visible(db, security_id):
    """A take logged without a name (thesis-level) is never guessed onto one: it rides off-record
    (episodes join by name), unpriced, visible."""
    thesis = _armed_thesis(db, security_id)
    _decide(db, thesis, "take", date(2026, 6, 2), price=100.0, call_state="armed")

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    assert record.episodes[0].operator is None
    (span,) = record.operator_spans
    assert span.thesis_level is True and span.security_id is None
    assert span.operator_return is None and span.entry_price is None
    assert span.override is False  # the stance WAS armed; off-record only because unattributed


def test_close_while_flat_surfaces_an_anomaly(db, security_id):
    thesis = _armed_thesis(db, security_id)
    _decide(db, thesis, "close", date(2026, 6, 3), security_id=security_id, price=100.0)

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 15))
    assert record.decision_anomaly is not None and "flat" in record.decision_anomaly
