from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

import scoreboard.artifact as artifact
from db.session import DEFAULT_TENANT_ID
from domain.call import TriggerRef
from domain.enums import Grade, Kind
from replay.schema import Episode, Outcome
from scoreboard.artifact import write_snapshot
from scoreboard.schema import ReplaySnapshot, ReplayThesisHistory, ScoredEpisode

# GET /scoreboard/replay — the historical panel served from the artifact: absence is available:false
# (never a 500), presence maps through the SAME episode wire shape as the live ledger with tickers
# resolved at serve time, and the GET writes nothing.


@pytest.fixture
def artifact_home(tmp_path, monkeypatch):
    """Redirect the artifact home for the test (the draft_run_log monkeypatch idiom)."""
    home = tmp_path / "scoreboard_replay"
    monkeypatch.setattr(artifact, "_DEFAULT_HOME", home)
    return home


def _snapshot(security_id) -> ReplaySnapshot:
    ep = Episode(
        thesis_id=uuid.UUID(int=0xB1),
        security_id=security_id,
        is_headline=True,
        arm_date=date(2026, 6, 1),
        last_armed_date=date(2026, 6, 18),
        dearm_date=date(2026, 6, 22),
        close_reason="conviction_aged_out",
        exit_by=date(2026, 6, 19),
        entry_grade=Grade.CORE,
    )
    out = Outcome(
        thesis_id=ep.thesis_id,
        security_id=security_id,
        is_headline=True,
        close_reason=ep.close_reason,
        arm_date=ep.arm_date,
        exit_by=ep.exit_by,
        entry_close=100.0,
        exit_close=112.0,
        exit_date=date(2026, 6, 19),
        forward_return=0.12,
    )
    scored = ScoredEpisode(
        episode=ep,
        outcome=out,
        status="closed",
        matured=True,
        censored_start=False,
        triggers_at_arm=[
            TriggerRef(
                label="cluster buy",
                kind=Kind.INSIDER,
                grade=Grade.CORE,
                security_id=security_id,
            )
        ],
    )
    return ReplaySnapshot(
        generated_at=datetime(2026, 7, 12, tzinfo=timezone.utc).isoformat(),
        window_start=date(2025, 7, 9),
        window_end=date(2026, 7, 9),
        known_at_pin=datetime(2026, 7, 12, tzinfo=timezone.utc).isoformat(),
        record_began=date(2026, 7, 10),
        banner="REPLAYED — NOT the record.",
        min_n=5,
        n_theses=1,
        n_episodes=1,
        n_censored=0,
        n_eligible=1,
        theses=[
            ReplayThesisHistory(
                thesis_id=ep.thesis_id,
                tenant_id=DEFAULT_TENANT_ID,
                name="Historical thesis",
                ticker="DEVCO",
                basket_size=1,
                episodes=[scored],
            )
        ],
    )


def test_no_artifact_is_available_false_not_an_error(client, artifact_home):
    r = client.get("/scoreboard/replay")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["theses"] == []


def test_artifact_served_with_resolved_tickers_and_why(client, db, security_id, artifact_home):
    write_snapshot(_snapshot(security_id), base_dir=artifact_home)

    body = client.get("/scoreboard/replay").json()
    assert body["available"] is True
    assert body["window_end"] == "2026-07-09" and body["record_began"] == "2026-07-10"
    assert "NOT the record" in body["banner"]
    (t,) = body["theses"]
    (ep,) = t["episodes"]
    assert ep["ticker"] == "DEVCO"  # resolved at serve time from the master
    assert ep["status"] == "closed" and ep["matured"] is True
    assert ep["forward_return"] == 0.12
    assert ep["operator"] is None  # platform track only — history predates decision capture
    assert ep["triggers_at_arm"][0]["label"] == "cluster buy"


def test_replay_get_writes_nothing(client, db, security_id, artifact_home):
    write_snapshot(_snapshot(security_id), base_dir=artifact_home)

    def counts():
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM calls) AS c,"
                " (SELECT count(*) FROM operator_decision) AS d"
            )
            r = cur.fetchone()
            return (r["c"], r["d"])

    before = counts()
    assert client.get("/scoreboard/replay").status_code == 200
    assert counts() == before
