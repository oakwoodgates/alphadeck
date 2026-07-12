from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from domain.call import TriggerRef
from domain.enums import Grade, Kind, State, Verdict
from replay.schema import CallSnapshot, Episode, MemberRow, Outcome
from scoreboard.artifact import read_snapshot, write_snapshot
from scoreboard.replay_snapshot import ThesisMeta, build_snapshot

# The replay-panel flattener — PURE (no DB, no duckdb, no clock): the honesty flags mirror the
# forward record (censored_start on the window's first day; matured against the data edge;
# metrics over matured + non-censored only), the WHY comes from the arm-date snapshot's MemberRow
# triggers, and pushing the window over the record is LOUD in the artifact, never silent.

_TID = uuid.UUID(int=0xA1)
_SID = uuid.UUID(int=0xA2)
_PIN = datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc)

_TRIGGER = TriggerRef(
    label="2 insiders bought open-market", kind=Kind.INSIDER, grade=Grade.CORE, security_id=_SID
)


def _snap(asof: date, *, armed: bool, exit_by: date | None = None) -> CallSnapshot:
    members = (
        [
            MemberRow(
                security_id=_SID,
                tier="armed",
                verdict=Verdict.CORE_ENTRY,
                conviction_grade=Grade.CORE,
                entry_grade=Grade.CORE,
                exit_by=exit_by,
                triggers=[_TRIGGER],
            )
        ]
        if armed
        else []
    )
    return CallSnapshot(
        thesis_id=_TID,
        asof=asof,
        state=State.ARMED if armed else State.WARMING,
        verdict=Verdict.CORE_ENTRY if armed else Verdict.NOT_YET,
        conviction_grade=Grade.CORE,
        armed_security_id=_SID if armed else None,
        exit_by=exit_by,
        members=members,
    )


def _episode(arm: date, *, dearm: date | None, exit_by: date) -> Episode:
    return Episode(
        thesis_id=_TID,
        security_id=_SID,
        is_headline=True,
        arm_date=arm,
        last_armed_date=dearm or arm,
        dearm_date=dearm,
        close_reason="conviction_aged_out" if dearm else "window_end",
        exit_by=exit_by,
        entry_grade=Grade.CORE,
    )


def _outcome(ep: Episode, fwd: float | None) -> Outcome:
    return Outcome(
        thesis_id=ep.thesis_id,
        security_id=ep.security_id,
        is_headline=True,
        close_reason=ep.close_reason,
        arm_date=ep.arm_date,
        exit_by=ep.exit_by,
        forward_return=fwd,
        entry_close=100.0,
        exit_close=None if fwd is None else round(100.0 * (1 + fwd), 4),
    )


def _build(scored, timeline, *, window_end=date(2026, 7, 9), record_began=date(2026, 7, 10)):
    return build_snapshot(
        timeline,
        scored,
        thesis_meta={_TID: ThesisMeta(tenant_id=None, name="T", ticker="DEVCO", basket_size=1)},
        window_start=date(2025, 7, 9),
        window_end=window_end,
        pin=_PIN,
        generated_at=_PIN,
        matured_asof=date(2026, 7, 12),
        record_began=record_began,
    )


def test_flags_censoring_maturity_status_and_triggers():
    """First-replayed-day arm = censored; exit_by past the data edge = immature; an un-dearmed run
    = open; the WHY rides from the arm-date snapshot's MemberRow."""
    timeline = {
        _TID: [
            _snap(date(2026, 6, 1), armed=True, exit_by=date(2026, 6, 20)),  # censored arm
            _snap(date(2026, 6, 25), armed=False),
            _snap(date(2026, 7, 1), armed=True, exit_by=date(2026, 12, 1)),  # fresh, open, immature
        ]
    }
    ep1 = _episode(date(2026, 6, 1), dearm=date(2026, 6, 25), exit_by=date(2026, 6, 20))
    ep2 = _episode(date(2026, 7, 1), dearm=None, exit_by=date(2026, 12, 1))
    snap = _build([(ep1, _outcome(ep1, 0.10)), (ep2, _outcome(ep2, 0.02))], timeline)

    (t,) = snap.theses
    first, second = t.episodes
    assert first.censored_start is True and first.matured is True and first.status == "closed"
    assert second.censored_start is False and second.matured is False and second.status == "open"
    assert [tr.label for tr in first.triggers_at_arm] == ["2 insiders bought open-market"]
    assert snap.n_episodes == 2 and snap.n_censored == 1


def test_metrics_judge_only_matured_non_censored():
    """A censored matured episode and an immature open one both stay OUT of the metric inputs —
    the same eligibility rule as the live summary (the strips must be comparable)."""
    timeline = {
        _TID: [
            _snap(date(2026, 6, 1), armed=True, exit_by=date(2026, 6, 20)),  # censored
            _snap(date(2026, 6, 25), armed=False),
            _snap(date(2026, 6, 28), armed=True, exit_by=date(2026, 7, 5)),  # eligible
            _snap(date(2026, 7, 6), armed=False),
            _snap(date(2026, 7, 8), armed=True, exit_by=date(2026, 12, 1)),  # immature
        ]
    }
    eps = [
        _episode(date(2026, 6, 1), dearm=date(2026, 6, 25), exit_by=date(2026, 6, 20)),
        _episode(date(2026, 6, 28), dearm=date(2026, 7, 6), exit_by=date(2026, 7, 5)),
        _episode(date(2026, 7, 8), dearm=None, exit_by=date(2026, 12, 1)),
    ]
    snap = _build([(e, _outcome(e, r)) for e, r in zip(eps, [0.5, 0.07, 0.01])], timeline)

    assert snap.n_eligible == 1
    arm_timing = next(m for m in snap.metrics if m.name == "arm_timing_forward_return")
    assert arm_timing.n == 1  # only the eligible one — the +50% censored outcome never leaks in
    assert arm_timing.summary["median"] == 0.07


def test_window_overlap_is_loud_never_silent():
    snap = _build([], {_TID: []}, window_end=date(2026, 7, 15), record_began=date(2026, 7, 10))
    assert snap.window_overlaps_record is True
    assert "overlaps the forward record" in snap.banner

    clean = _build([], {_TID: []})
    assert clean.window_overlaps_record is False
    assert "overlaps" not in clean.banner
    assert "NOT the record" in clean.banner  # the recompute caveat always rides


def test_artifact_round_trip_and_unreadable_is_absence(tmp_path):
    snap = _build([], {_TID: []})
    path = write_snapshot(snap, base_dir=tmp_path)
    assert path.name == "latest.json"
    loaded = read_snapshot(base_dir=tmp_path)
    assert loaded == snap  # full pydantic round-trip

    path.write_text("{not json", encoding="utf-8")
    assert read_snapshot(base_dir=tmp_path) is None  # absence, never a raise
    assert read_snapshot(base_dir=tmp_path / "missing") is None
