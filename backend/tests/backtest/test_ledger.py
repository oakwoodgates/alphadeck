from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from backtest.ledger import BacktestLedger, SecurityRef, build_ledger
from domain.call import TriggerRef
from domain.config import DEFAULT_CONFIG, config_hash
from domain.enums import Grade, Kind, State, Verdict
from replay.schema import CallSnapshot, Episode, MemberRow, Outcome
from scoreboard.replay_snapshot import ThesisMeta

# B6 -- the run's LEDGER, the per-thesis drill-down. PURE: no DB, no duckdb, no clock. Three properties
# carry it. It reuses the Scoreboard's own flattener (so the two surfaces cannot disagree about which
# episodes are eligible), it is ordered by NAME (a drill-down, never a leaderboard -- invariant #4), and
# it bakes the identity in at run time (an immutable artifact whose tickers re-resolve later is not
# immutable).

_PIN = datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc)
_HASH = config_hash(DEFAULT_CONFIG)


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.UUID(int=0xB0 + i) for i in range(n)]


def _snap(tid: uuid.UUID, sid: uuid.UUID, asof: date, *, exit_by: date) -> CallSnapshot:
    return CallSnapshot(
        thesis_id=tid,
        asof=asof,
        state=State.ARMED,
        verdict=Verdict.CORE_ENTRY,
        conviction_grade=Grade.CORE,
        armed_security_id=sid,
        exit_by=exit_by,
        members=[
            MemberRow(
                security_id=sid,
                tier="armed",
                verdict=Verdict.CORE_ENTRY,
                conviction_grade=Grade.CORE,
                entry_grade=Grade.CORE,
                exit_by=exit_by,
                triggers=[
                    TriggerRef(
                        label="2 insiders bought open-market",
                        kind=Kind.INSIDER,
                        grade=Grade.CORE,
                        security_id=sid,
                    )
                ],
            )
        ],
    )


def _episode(tid: uuid.UUID, sid: uuid.UUID, arm: date, exit_by: date) -> Episode:
    return Episode(
        thesis_id=tid,
        security_id=sid,
        is_headline=True,
        arm_date=arm,
        last_armed_date=arm,
        dearm_date=None,
        close_reason="window_end",
        exit_by=exit_by,
        entry_grade=Grade.CORE,
        key1_source="insider",
        co_arm_bucket="alone",
    )


def _outcome(ep: Episode, fwd: float) -> Outcome:
    return Outcome(
        thesis_id=ep.thesis_id,
        security_id=ep.security_id,
        is_headline=True,
        close_reason=ep.close_reason,
        arm_date=ep.arm_date,
        exit_by=ep.exit_by,
        forward_return=fwd,
        entry_close=100.0,
        exit_close=round(100.0 * (1 + fwd), 4),
    )


def _build(
    *,
    names: list[str],
    returns: list[float],
    dials: list[str] | None = None,
    clock: str = "record",
):
    """N theses, one armed member each, returns as given -- enough for ordering and identity."""
    tids = _ids(len(names))
    sids = [uuid.UUID(int=0xC0 + i) for i in range(len(names))]
    arm, exit_by = date(2026, 1, 5), date(2026, 3, 5)
    timeline = {tid: [_snap(tid, sid, arm, exit_by=exit_by)] for tid, sid in zip(tids, sids)}
    scored = []
    for tid, sid, ret in zip(tids, sids, returns):
        ep = _episode(tid, sid, arm, exit_by)
        scored.append((ep, _outcome(ep, ret)))
    ledger = build_ledger(
        timeline,
        scored,
        thesis_meta={
            tid: ThesisMeta(tenant_id=None, name=n, ticker=None, basket_size=1)
            for tid, n in zip(tids, names)
        },
        securities={
            sid: SecurityRef(ticker=f"T{i}", cik=f"000000000{i}", name=f"Co {i}")
            for i, sid in enumerate(sids)
        },
        window_start=date(2025, 7, 9),
        window_end=date(2026, 7, 9),
        pin=_PIN,
        generated_at=_PIN,
        matured_asof=date(2026, 7, 9),
        clock=clock,
        config_hash=_HASH,
        code_sha="a" * 40,
        dials_moved=dials or [],
    )
    return ledger, tids, sids


def test_the_ledger_reuses_the_scoreboards_own_episode_shape():
    """Not a parallel model: the rows ARE ScoredEpisodes, so the /backtest surface renders through the
    same components the Scoreboard's replay panel uses and the two cannot drift."""
    ledger, _, _ = _build(names=["Alpha", "Beta"], returns=[0.1, 0.2])
    eps = [e for t in ledger.snapshot.theses for e in t.episodes]
    assert len(eps) == 2
    one = eps[0]
    assert one.status == "open" and one.matured is True
    # the WHY rides from the arm-date snapshot, exactly as the record reads it from the arm-date card
    assert [t.kind for t in one.triggers_at_arm] == [Kind.INSIDER]
    # a replay carries no recorded run identity and must never claim one
    assert one.arm_config_hash is None and one.arm_run_kind is None


def test_the_theses_come_back_in_name_order_not_outcome_order():
    """A drill-down, never a leaderboard (#4). The worst performer is named first here purely because
    its name sorts first -- an ordering arbitrary with respect to performance is the point."""
    ledger, _, _ = _build(names=["Alpha", "Zulu"], returns=[-0.5, 0.9])
    assert [t.name for t in ledger.snapshot.theses] == ["Alpha", "Zulu"]
    ledger, _, _ = _build(names=["Zulu", "Alpha"], returns=[0.9, -0.5])
    assert [t.name for t in ledger.snapshot.theses] == ["Alpha", "Zulu"]


def test_a_thesis_that_armed_nothing_still_appears():
    """Recall is sacred, and it applies to a research surface too: a thesis that armed nothing is a
    RESULT, and dropping it would make an empty run indistinguishable from a missing one (#9)."""
    tid = uuid.UUID(int=0xD1)
    ledger = build_ledger(
        {tid: []},
        [],
        thesis_meta={
            tid: ThesisMeta(tenant_id=None, name="Armed nothing", ticker=None, basket_size=3)
        },
        securities={},
        window_start=date(2025, 7, 9),
        window_end=date(2026, 7, 9),
        pin=_PIN,
        generated_at=_PIN,
        matured_asof=date(2026, 7, 9),
        clock="record",
        config_hash=_HASH,
        code_sha=None,
        dials_moved=[],
    )
    assert [t.name for t in ledger.snapshot.theses] == ["Armed nothing"]
    assert ledger.snapshot.n_episodes == 0


def test_the_identity_is_baked_in_at_run_time():
    """The artifact carries the names the RUN resolved. Keyed by string because JSON has no UUID key
    type, and the round trip has to survive that rather than depend on a clever parser."""
    ledger, _, sids = _build(names=["Alpha", "Beta"], returns=[0.1, 0.2])
    again = BacktestLedger.model_validate_json(ledger.model_dump_json())
    assert again.securities[str(sids[0])].ticker == "T0"
    assert again.securities[str(sids[0])].cik == "0000000000"
    assert again.securities[str(sids[0])].name == "Co 0"


def test_the_banner_states_this_runs_own_dials_not_todays():
    """The replay panel's banner opens "today's code + dials", which is FALSE of any run that moved a
    dial. A run states its own, and says which clock its facts entered on."""
    ledger, _, _ = _build(
        names=["Alpha"], returns=[0.1], dials=["insider_core_alpha_liveness_days"]
    )
    b = ledger.snapshot.banner
    assert "today's code + dials" not in b
    assert "insider_core_alpha_liveness_days" in b
    assert "drill-down" in b and "NOT a ranking" in b
    assert "never the record" in b


def test_the_banner_names_the_clock_axis_in_words():
    """A bare "clock public" is jargon -- the sentence has to say what the axis MEANS, because telling
    the two apart is the entire point of the public clock."""
    rec, _, _ = _build(names=["Alpha"], returns=[0.1], clock="record")
    pub, _, _ = _build(names=["Alpha"], returns=[0.1], clock="public")
    assert "this system recorded them" in rec.snapshot.banner
    assert "became PUBLIC" in pub.snapshot.banner
