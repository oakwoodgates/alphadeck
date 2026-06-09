from __future__ import annotations

from datetime import date, datetime, timezone

from domain.enums import State, Verdict
from pipeline.seed import UNH_SECURITY_ID, UNH_THESIS_ID, seed_unh
from replay.export import export_snapshot
from replay.harness import replay_thesis
from replay.pit import connect_mirror
from repositories import thesis_repo

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_unh_arc_replays_warm_to_arm_to_ageout(db, tmp_path):
    """The flagship end-to-end replay, on the one richly-covered arc (UNH: the mid-May-2025 CEO-led insider
    cluster -> the August-2025 volume-backed breakout -> aged out by 2026). Run the REAL pipeline day by day
    over the mirror and assert the arc shape — the same warm -> arm -> age-out the live test asserts, now via
    the replay path with zero forward knowledge."""
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        thesis = thesis_repo.get(db, UNH_THESIS_ID)
        snaps = replay_thesis(
            con, thesis, start=date(2025, 4, 1), end=date(2026, 6, 1), known_at=_PIN
        )
        assert snaps, "the UNH window should have trading sessions"
        assert snaps == sorted(snaps, key=lambda s: s.asof)  # the timeline is ordered by as-of

        armed = [s for s in snaps if s.state is State.ARMED]
        assert armed, "UNH should ARM at the August breakout"
        # the CEO-led cluster is CORE conviction + the volume-backed breakout -> a real core_entry on UNH
        assert any(s.verdict is Verdict.CORE_ENTRY for s in armed)
        assert all(s.armed_security_id == UNH_SECURITY_ID for s in armed)  # single-name headline

        first_arm = min(s.asof for s in armed)
        assert (
            date(2025, 6, 1) <= first_arm <= date(2025, 11, 1)
        )  # summer-2025 arm (post-cluster breakout)
        # it WARMED before it armed (conviction in, awaiting confirmation) ...
        assert any(s.state is State.WARMING and s.asof < first_arm for s in snaps)
        # ... and aged out by the end of the window (the core conviction horizon lapsed)
        assert snaps[-1].state is not State.ARMED

        # no forward knowledge ever entered a call: every snapshot's hold clock is on/after its as-of
        assert all(s.exit_by is None or s.exit_by >= s.asof for s in armed)
    finally:
        con.close()
