from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from db.session import DEFAULT_TENANT_ID
from replay.export import export_snapshot
from replay.pit import connect_mirror
from replay.schema import Episode
from replay.scoring import RealizedPrices, score_episode
from scoreboard.prices import PgRealizedPrices
from tests.scoreboard.helpers import bar

# The twin gate: the live Scoreboard's Postgres reader must agree row-for-row with replay's DuckDB
# reader over the same bars (same latest-version-per-day dedup, same tiebreak, same null-skip) — and
# ``score_episode`` must produce the IDENTICAL Outcome through either. (This file lives under
# tests/replay/ so the conftest's duckdb collect-guard applies in a lean venv.)


def _seed(db, sid):
    t1 = datetime(2026, 6, 2, 12, tzinfo=timezone.utc)
    bar(db, sid, date(2026, 6, 1), 100.0, recorded_at=t1)
    bar(db, sid, date(2026, 6, 1), 102.0, recorded_at=t1 + timedelta(hours=1))  # restated
    bar(db, sid, date(2026, 6, 3), None)  # no close — both readers must skip it
    bar(db, sid, date(2026, 6, 5), 111.0)
    bar(db, sid, date(2026, 6, 9), 108.0)
    bar(db, sid, date(2026, 6, 12), 120.0)


def test_pg_reader_matches_duckdb_reader_and_outcomes(db, security_id, tmp_path):
    _seed(db, security_id)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        duck = RealizedPrices(con)
        pg = PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=date(2099, 1, 1))

        probes = [date(2026, 5, 30), date(2026, 6, 1), date(2026, 6, 3), date(2026, 6, 10)]
        for d in probes:
            assert pg.first_close_on_or_after(security_id, d) == duck.first_close_on_or_after(
                security_id, d
            ), d
            assert pg.last_close_through(security_id, d) == duck.last_close_through(
                security_id, d
            ), d
        assert pg.closes_between(security_id, date(2026, 6, 1), date(2026, 6, 12)) == (
            duck.closes_between(security_id, date(2026, 6, 1), date(2026, 6, 12))
        )

        ep = Episode(
            thesis_id=uuid.uuid4(),
            security_id=security_id,
            is_headline=True,
            arm_date=date(2026, 6, 1),
            last_armed_date=date(2026, 6, 9),
            dearm_date=None,
            close_reason="window_end",
            warm_date=date(2026, 5, 30),
            exit_by=date(2026, 6, 12),
            arm_until=date(2026, 6, 5),
        )
        assert score_episode(ep, pg) == score_episode(ep, duck)
    finally:
        con.close()
