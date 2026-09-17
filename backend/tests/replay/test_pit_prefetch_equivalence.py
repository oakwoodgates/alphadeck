from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from domain.config import DEFAULT_CONFIG
from pipeline.core import assemble_from_pit
from pipeline.seed import UNH_THESIS_ID, seed_unh
from replay.export import export_snapshot
from replay.harness import replay_thesis, trading_sessions
from replay.pit import ReplayPointInTimeData, connect_mirror
from replay.schema import CallSnapshot
from repositories import thesis_repo

# B5a — the acceptance test for the PIT port: a real sweep through the NEW harness (memo + basket
# prefetch + registry bounds) must be BYTE-IDENTICAL to the same sweep read the pre-B5a way (a plain
# per-security, unbounded, un-memoized PIT per session).
#
# This is the only test that can fail for the right reason if the prefetch, the floor, or the version
# pick is subtly wrong on real data: the unit tests next door build their own tape, this one runs the
# REAL detectors over the seeded UNH arc and compares the assembled calls, not the rows.

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(2026, 6, 1)


def _replay_the_old_way(con, thesis, *, start, end, known_at) -> list[CallSnapshot]:
    """The pre-B5a loop, reproduced verbatim: one plain PIT per session, no basket, no bounds."""
    sids = [m.security_id for m in thesis.basket if m.security_id is not None]
    out: list[CallSnapshot] = []
    for t in trading_sessions(con, sids, start, end, thesis.tenant_id):
        pit = ReplayPointInTimeData(con, asof=t, known_at=known_at, tenant_id=thesis.tenant_id)
        out.append(CallSnapshot.from_card(assemble_from_pit(pit, thesis, t, DEFAULT_CONFIG)))
    return out


@pytest.mark.slow  # one UNH sweep, run twice — the sibling of test_unh_arc_replays_*
@pytest.mark.timeout(300)
def test_prefetched_sweep_is_byte_identical_to_the_plain_sweep(db, tmp_path):
    """The whole claim of B5a in one assertion: 25x fewer queries, the SAME calls."""
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        thesis = thesis_repo.get(db, UNH_THESIS_ID)
        fast = replay_thesis(con, thesis, start=_START, end=_END, known_at=_PIN)
        plain = _replay_the_old_way(con, thesis, start=_START, end=_END, known_at=_PIN)
        assert fast, "the UNH window should have trading sessions"
        assert len(fast) == len(plain)
        assert [s.model_dump_json() for s in fast] == [s.model_dump_json() for s in plain]
    finally:
        con.close()


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_prefetched_sweep_costs_one_query_per_table_per_session(db, tmp_path):
    """The cost claim, pinned on a real sweep: the per-session query count is bounded by the number of
    fact tables the call path reads, NOT by basket size x accessor calls. Pre-B5a this was ~15 queries
    per member-session (MEASURED 2,935/session on a 196-name basket); a regression here means someone
    dropped the basket or the memo on the floor."""
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    calls = {"n": 0}
    orig = type(con).execute

    def counting(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    try:
        thesis = thesis_repo.get(db, UNH_THESIS_ID)
        sids = [m.security_id for m in thesis.basket if m.security_id is not None]
        n_sessions = len(trading_sessions(con, sids, _START, _END, thesis.tenant_id))
        type(con).execute = counting
        replay_thesis(con, thesis, start=_START, end=_END, known_at=_PIN)
        type(con).execute = orig
        # 8 fact accessors + the one trading_sessions query; the ceiling is generous on purpose — the
        # point is that it does not scale with basket size.
        assert (
            calls["n"] <= 9 * n_sessions + 1
        ), f"{calls['n']} queries over {n_sessions} sessions — the prefetch is not engaging"
    finally:
        type(con).execute = orig
        con.close()
