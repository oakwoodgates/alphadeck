from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, TriggerRef
from domain.enums import Grade, Kind, State, Verdict
from replay.schema import CallSnapshot, Episode, MemberRow
from repositories import thesis_repo
from scoreboard.record import (
    _dearm_detail,
    _risk_events,
    _transitions,
    derive_thesis_record,
    scoreboard_records,
)
from tests.calls.factories import (
    breakdown_event,
    breakout_event,
    dilution_event,
    insider_event,
    insider_sell_event,
)
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


# --- Slice C: dearm_detail (C1) + risk_events (C2) — composed from the recorded cards only ---

_SID = uuid.UUID(int=0xC1D)


def _warm_first(db, thesis, security_id, asof: date) -> None:
    """A warming row BEFORE the arm so the episode is not censored (the standing seed idiom)."""
    warm = [insider_event(security_id=security_id, liveness=60).model_copy(update={"asof": asof})]
    _record_day(db, thesis, warm, asof)


def _bare_card(
    asof: date,
    *,
    state: State = State.ARMED,
    risks: tuple[TriggerRef, ...] = (),
    missing: tuple[str, ...] = (),
) -> CallCard:
    """A minimal hand-built card for the PURE composer/walker branches the DB seeds can't reach
    (state fallbacks, None event dates) — the composition is a pure function over the card."""
    return CallCard(
        thesis_id=uuid.UUID(int=0xA1),
        asof=asof,
        state=state,
        verdict=Verdict.NOT_YET,
        expression="",
        key_conviction=KeyState(turned=False, label="Conviction"),
        key_confirmation=KeyState(turned=False, label="Confirmation"),
        risk_signals=list(risks),
        missing=list(missing),
    )


def _risk_ref(
    *,
    kind: Kind = Kind.INSIDER_SELL,
    event_date: date | None = None,
    security_id: uuid.UUID = _SID,
    label: str = "a risk",
) -> TriggerRef:
    return TriggerRef(label=label, kind=kind, event_date=event_date, security_id=security_id)


def _bare_episode(arm: date, last: date, dearm: date | None = None) -> Episode:
    return Episode(
        thesis_id=uuid.UUID(int=0xA1),
        security_id=_SID,
        is_headline=True,
        arm_date=arm,
        last_armed_date=last,
        dearm_date=dearm,
        close_reason="dearmed_other" if dearm else "window_end",
    )


def test_dearm_detail_risk_branch_and_dearm_day_risk_on_tape(db, security_id):
    """A breakdown de-arm (dearmed_other): the detail IS the member's risk label from the de-arm-day
    card, and the same risk rides risk_events (the de-arm day is INSIDE the walk — the risk that
    ended the run belongs on the tape)."""
    thesis = _thesis(db, security_id)
    _warm_first(db, thesis, security_id, date(2026, 5, 29))
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))  # armed (core entry)
    down = breakdown_event(dearm_grade=Grade.CORE, asof=date(2026, 6, 5), security_id=security_id)
    _record_day(db, thesis, [conv, conf, down], date(2026, 6, 5))  # the break de-arms

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 10))
    (ep,) = record.episodes
    assert ep.episode.close_reason == "dearmed_other"
    assert ep.dearm_detail == down.label
    assert [(r.kind, r.event_date) for r in ep.risk_events] == [(Kind.BREAKDOWN, date(2026, 6, 5))]


def test_dearm_detail_missing_branch(db, security_id):
    """No member risk on the de-arm-day card -> the first ``missing[]`` entry names the key that
    un-turned (the SPECIFIC why; the generic state phrase is the fallback behind it)."""
    thesis = _thesis(db, security_id)
    _warm_first(db, thesis, security_id, date(2026, 5, 29))
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))
    _record_day(db, thesis, [conv], date(2026, 6, 3))  # confirmation withdrawn pre-arm_until

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 10))
    (ep,) = record.episodes
    assert ep.episode.close_reason == "dearmed_other"
    assert ep.dearm_detail == "now missing: Volume-confirmed breakout (the confirmation key)"


def test_dearm_detail_self_explaining_token_stays_none(db, security_id):
    """A clock-lapse close (arm_until_lapsed) composes NOTHING — even with a risk on the de-arm-day
    card: the token self-explains, and a detail under every close would be noise (#7)."""
    thesis = _thesis(db, security_id)
    _warm_first(db, thesis, security_id, date(2026, 5, 29))
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=1)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))  # arm_until = 06-02
    risk = insider_sell_event().model_copy(
        update={"security_id": security_id, "asof": date(2026, 6, 8)}
    )
    _record_day(db, thesis, [conv, conf, risk], date(2026, 6, 8))  # conf lapsed -> de-arm

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 10))
    (ep,) = record.episodes
    assert ep.episode.close_reason == "arm_until_lapsed"
    assert ep.dearm_detail is None
    # ...while the de-arm-day risk still rides the tape (the two fields are independent)
    assert [r.kind for r in ep.risk_events] == [Kind.INSIDER_SELL]


def test_dearm_detail_composer_branches_unit():
    """The pure composer, branch by branch: risks lead (first 2 joined), then missing[0], then the
    state fallback, then the member-only-de-arm phrase, then None (no card = a record gap)."""
    sid = _SID
    risks = tuple(
        _risk_ref(kind=k, event_date=date(2026, 6, 5), label=f"risk {i}")
        for i, k in enumerate((Kind.INSIDER_SELL, Kind.DILUTION_RISK, Kind.CORPORATE_RISK))
    )
    d = date(2026, 6, 5)
    assert _dearm_detail(_bare_card(d, risks=risks), sid) == "risk 0 · risk 1"  # first 2 only
    # a risk on ANOTHER member never explains THIS member's de-arm
    other = (_risk_ref(security_id=uuid.uuid4(), event_date=d, label="not mine"),)
    assert _dearm_detail(_bare_card(d, risks=other, missing=("Conviction trigger",)), sid) == (
        "now missing: Conviction trigger"
    )
    assert _dearm_detail(_bare_card(d, state=State.WARMING), sid) == "thesis fell back to Warming"
    assert _dearm_detail(_bare_card(d, state=State.INCUBATING), sid) == (
        "thesis fell back to Incubating"
    )
    assert _dearm_detail(_bare_card(d, state=State.ARMED), sid) == (
        "left the armed set (thesis still Armed)"
    )
    assert _dearm_detail(None, sid) is None


def test_risk_events_dedupe_collapses_refires_distinct_kinds_survive(db, security_id):
    """A live risk re-fires on every daily card: (kind, event_date) collapses it to ONE row —
    while two DIFFERENT kinds sharing the day stay distinct (#9). Open episode: the walk runs to
    the record edge (last_armed_date)."""
    thesis = _thesis(db, security_id)
    _warm_first(db, thesis, security_id, date(2026, 5, 29))
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    sell = insider_sell_event().model_copy(
        update={"security_id": security_id, "asof": date(2026, 6, 1)}
    )
    # sub-veto score: a 0.80 dilution would BLOCK the arm (risk_block_severity) — this one rides
    dil = dilution_event(score=0.5).model_copy(
        update={"security_id": security_id, "asof": date(2026, 6, 1)}
    )
    for asof in (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)):
        _record_day(db, thesis, [conv, conf, sell, dil], asof)  # armed + both risks, 3 days

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    (ep,) = record.episodes
    assert ep.status == "open"
    # COUNT THE LIST, not just the read: 3 daily re-fires -> exactly one row per kind
    assert [(r.kind, r.event_date) for r in ep.risk_events] == [
        (Kind.INSIDER_SELL, date(2026, 6, 1)),
        (Kind.DILUTION_RISK, date(2026, 6, 1)),
    ]


def test_risk_events_arm_day_included_other_member_excluded(db, security_id):
    """The walk interval is CLOSED at the arm day (a risk live AT arm haircut the arm's own setup
    strength — pinning the interval ruling); a risk attributed to another member never rides this
    episode's tape."""
    thesis = _thesis(db, security_id)
    _warm_first(db, thesis, security_id, date(2026, 5, 29))
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    mine = insider_sell_event().model_copy(
        update={"security_id": security_id, "asof": date(2026, 6, 1)}
    )
    theirs = insider_sell_event().model_copy(
        update={"security_id": uuid.uuid4(), "asof": date(2026, 6, 1)}
    )
    _record_day(db, thesis, [conv, conf, mine, theirs], date(2026, 6, 1))  # arm day only
    _record_day(db, thesis, [conv, conf], date(2026, 6, 2))  # both risks gone next card

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    (ep,) = record.episodes
    assert [(r.kind, r.event_date) for r in ep.risk_events] == [
        (Kind.INSIDER_SELL, date(2026, 6, 1))
    ]
    assert all(r.security_id == security_id for r in ep.risk_events)


def test_risk_events_ordered_by_first_appearance(db, security_id):
    """Mid-run risks enter in chronological first-appearance order (the tape reads forward)."""
    thesis = _thesis(db, security_id)
    _warm_first(db, thesis, security_id, date(2026, 5, 29))
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    dil = dilution_event(score=0.5).model_copy(
        update={"security_id": security_id, "asof": date(2026, 6, 3)}
    )
    sell = insider_sell_event().model_copy(
        update={"security_id": security_id, "asof": date(2026, 6, 4)}
    )
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))
    _record_day(db, thesis, [conv, conf, dil], date(2026, 6, 3))
    _record_day(db, thesis, [conv, conf, dil, sell], date(2026, 6, 4))

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    (ep,) = record.episodes
    assert [r.kind for r in ep.risk_events] == [Kind.DILUTION_RISK, Kind.INSIDER_SELL]


def test_risk_events_none_event_date_stamped_first_appearance_unit():
    """The pure walker on a legacy dateless risk: (kind, None) keys its daily re-fires as ONE live
    risk, stamped with the FIRST card-asof it appeared on (a recorded fact — when the record first
    said it, NOT the market event date)."""
    cards = {
        d: _bare_card(d, risks=(_risk_ref(event_date=None),))
        for d in (date(2026, 6, 2), date(2026, 6, 3))
    }
    ep = _bare_episode(date(2026, 6, 1), date(2026, 6, 4))
    out = _risk_events(cards, ep)
    assert [(r.kind, r.event_date) for r in out] == [(Kind.INSIDER_SELL, date(2026, 6, 2))]
    # ...and the stamp is a COPY: the recorded card's own ref keeps its None (never mutated)
    assert cards[date(2026, 6, 2)].risk_signals[0].event_date is None


def test_risk_events_walk_stops_at_dearm_unit():
    """A closed episode's walk ends AT the de-arm card — a later card's risk (a re-warm story)
    never leaks into this run's tape."""
    ref = _risk_ref(event_date=date(2026, 6, 8))
    cards = {
        date(2026, 6, 3): _bare_card(date(2026, 6, 3), state=State.WARMING),
        date(2026, 6, 8): _bare_card(date(2026, 6, 8), state=State.WARMING, risks=(ref,)),
    }
    ep = _bare_episode(date(2026, 6, 1), date(2026, 6, 2), dearm=date(2026, 6, 3))
    assert _risk_events(cards, ep) == []


# --- Slice C3: transitions — the un-numbered record trail (verdict/grade diffs, never confidence) ---


def _member_row(
    *,
    verdict: Verdict | None = Verdict.CORE_ENTRY,
    entry_grade: Grade | None = Grade.CORE,
    conviction_grade: Grade | None = Grade.CORE,
    confidence: float | None = 0.8,
) -> MemberRow:
    return MemberRow(
        security_id=_SID,
        tier="armed",
        verdict=verdict,
        entry_grade=entry_grade,
        conviction_grade=conviction_grade,
        confidence=confidence,
    )


def _armed_snap(asof: date, row: MemberRow) -> CallSnapshot:
    return CallSnapshot(
        thesis_id=uuid.UUID(int=0xA1),
        asof=asof,
        state=State.ARMED,
        verdict=row.verdict or Verdict.NOT_YET,
        armed_security_id=_SID,
        members=[row],
    )


def test_transitions_differ_unit():
    """The pure differ: the first card is the baseline (no emission); a multi-field change on one
    day emits one row PER field; a None → value change reads from_value=None; the daily confidence
    wobble emits NOTHING; snaps outside [arm_date, last_armed_date] never contribute."""
    snaps = [
        _armed_snap(date(2026, 5, 30), _member_row(verdict=Verdict.STARTER_ENTRY)),  # pre-arm: out
        _armed_snap(date(2026, 6, 1), _member_row(conviction_grade=None)),  # baseline
        _armed_snap(
            date(2026, 6, 2), _member_row(conviction_grade=None, confidence=0.55)
        ),  # wobble
        _armed_snap(  # verdict + entry_grade same day; conviction None -> core
            date(2026, 6, 4),
            _member_row(verdict=Verdict.STARTER_ENTRY, entry_grade=Grade.FLIP, confidence=0.55),
        ),
        _armed_snap(date(2026, 6, 8), _member_row(verdict=Verdict.MANAGING)),  # past the run: out
    ]
    ep = _bare_episode(date(2026, 6, 1), date(2026, 6, 4))
    out = _transitions(snaps, ep)
    assert [(t.asof, t.field, t.from_value, t.to_value) for t in out] == [
        (date(2026, 6, 4), "verdict", "core_entry", "starter_entry"),
        (date(2026, 6, 4), "entry_grade", "core", "flip"),
        (date(2026, 6, 4), "conviction_grade", None, "core"),
    ]

    # no change at all -> an empty trail (the quiet default, #7)
    flat = [_armed_snap(d, _member_row()) for d in (date(2026, 6, 1), date(2026, 6, 2))]
    assert _transitions(flat, _bare_episode(date(2026, 6, 1), date(2026, 6, 2))) == []


def test_transitions_recorded_run_captures_a_grade_flip(db, security_id):
    """Integration through the record: a confirmation re-fire at FLIP grade mid-run moves the
    member's entry grade core -> flip on the card that first said it; the arm card stays the
    baseline (no 06-01 rows)."""
    thesis = _thesis(db, security_id)
    _warm_first(db, thesis, security_id, date(2026, 5, 29))
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=60, conf_liveness=30)
    _record_day(db, thesis, [conv, conf], date(2026, 6, 1))  # armed core/core
    conf_flip = breakout_event(grade=Grade.FLIP, liveness=10, security_id=security_id).model_copy(
        update={"asof": date(2026, 6, 3)}
    )
    _record_day(db, thesis, [conv, conf_flip], date(2026, 6, 3))  # confirmation now flip-grade

    record, _ = derive_thesis_record(db, thesis, date(2026, 6, 5))
    (ep,) = record.episodes
    eg = next(t for t in ep.transitions if t.field == "entry_grade")
    assert (eg.asof, eg.from_value, eg.to_value) == (date(2026, 6, 3), "core", "flip")
    assert all(t.asof != date(2026, 6, 1) for t in ep.transitions)  # the baseline emits nothing


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
