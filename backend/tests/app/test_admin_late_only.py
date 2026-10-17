"""Admin's LATE-ONLY thesis-nights (operator decision 2026-10-17) — "covered" on Admin, a gap on the Scoreboard.

A late row (recorded at/after the next weekday's 06:00 ET EDGAR open) COVERS its night in Admin's per-thesis
coverage (the pass did record it), while the Scoreboard sets it aside (L1). So a (thesis, night) whose only
post-close rows are late read "covered" here and a gap there; ``record.late_only_nights`` names each such pair,
quietly — never an alarm, never the verdict.

THE SHAPE IS THE MEASURED 2026-10-12 NIGHT (prod, read-only): 12 theses; the 22:30 ET pass recorded 4 at
22:42-22:43 and died; the next afternoon's boot catch-up recorded 9 at 17:04-17:13 ET on 10-13 — a re-version
of one on-time thesis (AI Memory & Storage's shape: seq 1334 at 22:42, then seq 1367 at 17:04) and the 8
theses that had no on-time row. The other three on-time theses re-assembled identical cards, so the catch-up
appended nothing for them. Every row is written at its real stamp — an explicit INSERT of the cron's own card
(``daily.call_for_thesis``), because the calls log is immutable and the stamp must be supplied at insert time
(the ``tests/scoreboard/helpers.record_day`` precedent); the theses are created before the week, so they are
in each night's roster. Thesis names are synthetic.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from app.routers import admin
from db.session import DEFAULT_TENANT_ID
from pipeline import daily
from pipeline.backfill import run_backfill
from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult
from repositories.mappers import call_to_row
from scoreboard.late import late_nights

_NY = ZoneInfo("America/New_York")
_BORN = datetime(2026, 10, 1, 12, 0, tzinfo=_NY)
_FRI = date(2026, 10, 9)
_MON = date(2026, 10, 12)  # the night that died


def _et(m, d, hh, mm=0, ss=0) -> datetime:
    return datetime(2026, m, d, hh, mm, ss, tzinfo=_NY)


def _thesis(db, name: str) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", _BORN),
        )
    db.commit()
    return tid


def _row(db, tid: uuid.UUID, asof: date, recorded_at: datetime) -> None:
    """One call-of-record row, the cron's own card, at its REAL recorded_at."""
    row = call_to_row(daily.call_for_thesis(db, tid, asof, record=False), DEFAULT_TENANT_ID)
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, run_kind, recorded_at)
               VALUES (%(tenant_id)s, %(thesis_id)s, %(asof)s, %(state)s, %(verdict)s, %(card)s,
                       'cron', %(recorded_at)s)""",
            {**row, "card": Json(row["card"]), "recorded_at": recorded_at},
        )
    db.commit()


def _finished(asof: date, at: datetime, n: int) -> None:
    """The night's FINISHED run artifact (the real writer) — so the verdict is judged, not ``never_ran``."""
    path = write_cron_run_log(
        [
            ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=True, edgar_fetches=40)
            for _ in range(n)
        ],
        asof=asof,
        allow_live=True,
        started_at=at.astimezone(timezone.utc),
        finished_at=(at + timedelta(minutes=9)).astimezone(timezone.utc),
    )
    assert path is not None


def _table_counts(db) -> dict[str, int]:
    """COUNT every public base table — nothing-written is proved on the table, never on a read."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        names = [r["table_name"] for r in cur.fetchall()]
        counts: dict[str, int] = {}
        for n in names:
            cur.execute(f'SELECT COUNT(*) AS n FROM "{n}"')  # names come from the catalog, quoted
            counts[n] = cur.fetchone()["n"]
        return counts


def test_the_10_12_night_names_exactly_the_8_late_only_theses_and_stays_quiet(
    client, db, monkeypatch
):
    """The measured night: 4 on-time rows (22:42-22:43), then 9 late catch-up rows (10-13 17:04-17:13), one
    of them re-versioning an on-time thesis. Admin's coverage reads the night fully covered (12 of 12 — the
    late rows cover, unchanged) and the verdict healthy; ``late_only_nights`` names EXACTLY the 8 theses
    whose night is a gap on the Scoreboard — not the re-versioned one (its on-time row is scored), not the
    three whose catch-up appended nothing.
    """
    re_versioned = _thesis(db, "OnTime-ReVersioned")  # AI Memory & Storage's shape
    quiet = [_thesis(db, f"OnTime-{i}") for i in range(3)]
    late_only = [_thesis(db, f"LateOnly-{i}") for i in range(8)]
    names = {t: f"LateOnly-{i}" for i, t in enumerate(late_only)}

    for i, tid in enumerate([re_versioned, *quiet]):  # the 22:30 pass, before it died
        _row(db, tid, _MON, _et(10, 12, 22, 42 + i // 2, 10 * i))
    _row(db, re_versioned, _MON, _et(10, 13, 17, 4))  # the catch-up's re-version
    for i, tid in enumerate(late_only):  # the catch-up's late-only rows, 17:05-17:12
        _row(db, tid, _MON, _et(10, 13, 17, 5 + i))
    _finished(_MON, _et(10, 12, 22, 30), 12)
    # Tuesday 18:00: after the catch-up, before Tuesday's own 22:30 pass — the last expected night is 10-12
    monkeypatch.setattr(admin, "_now", lambda: _et(10, 13, 18, 0))

    before = _table_counts(db)
    body = client.get("/admin/status").json()
    assert _table_counts(db) == before  # nothing written — counted on every table

    rec = body["record"]
    assert [(n["thesis_name"], n["asof"]) for n in rec["late_only_nights"]] == [
        (f"LateOnly-{i}", "2026-10-12") for i in range(8)
    ]
    by_id = {n["thesis_id"]: n for n in rec["late_only_nights"]}
    assert set(by_id) == {str(t) for t in late_only}
    assert str(re_versioned) not in by_id  # an on-time row stands beside its late one
    assert not ({str(t) for t in quiet} & set(by_id))
    for tid, n in zip(late_only, rec["late_only_nights"], strict=True):
        assert n["thesis_name"] == names[tid]
        # served in MARKET time: the stamp the operator saw on the night, and the EDGAR open it missed
        assert datetime.fromisoformat(n["recorded_at"]).utcoffset() == timedelta(hours=-4)
        assert n["edgar_open"] == "2026-10-13T06:00:00-04:00"
        assert n["n_rows"] == 1
    assert rec["late_only_nights"][0]["recorded_at"] == "2026-10-13T17:05:00-04:00"
    assert rec["late_only_nights"][-1]["recorded_at"] == "2026-10-13T17:12:00-04:00"

    # INFORMATIONAL: the coverage still reads the night recorded, and nothing turns loud
    assert rec["partial_nights"] == [] and rec["missed_asofs"] == []
    assert rec["reconstructed_nights"] == []
    assert rec["reason"] == "current — no scheduled run is missing"
    assert body["cron"]["status"] == "healthy", body["cron"]
    assert body["cron"]["alarms"] == []
    assert "late" not in body["cron"]["detail"]

    # ONE definition: Admin's list is exactly the Scoreboard banner's late nights that read as a GAP there
    banner = late_nights(db, upto=date(2026, 10, 16))
    assert (
        len(banner) == 9
    )  # the re-versioned thesis's night is late too — but its on-time row is scored
    assert [str(n.thesis_id) for n in banner if n.earlier_recorded_at is None] == [
        n["thesis_id"] for n in rec["late_only_nights"]
    ]


def test_a_late_row_beside_a_DAYTIME_row_is_not_listed_the_scoreboard_scores_that_row(
    client, db, monkeypatch
):
    """A thesis whose 10-12 rows are a 09:15 "Run daily now" (pre-RUN_AT, the prior session's bars) and a
    late catch-up: Admin covers the night with the late row, and the Scoreboard — setting the late row aside —
    scores the DAYTIME row, so there is no gap to name; listing it "set aside on the Scoreboard" would be
    untrue, and noise. A plain late-only thesis beside it IS listed."""
    daytime = _thesis(db, "Daytime+Late")
    late = _thesis(db, "LateOnly")
    _row(db, daytime, _MON, _et(10, 12, 9, 15))
    _row(db, daytime, _MON, _et(10, 13, 17, 4))
    _row(db, late, _MON, _et(10, 13, 17, 5))
    _finished(_MON, _et(10, 12, 22, 30), 2)
    monkeypatch.setattr(admin, "_now", lambda: _et(10, 13, 18, 0))

    before = _table_counts(db)
    body = client.get("/admin/status").json()
    assert _table_counts(db) == before

    rec = body["record"]
    assert [n["thesis_id"] for n in rec["late_only_nights"]] == [str(late)]
    assert rec["partial_nights"] == []  # both theses are covered (by their late rows), unchanged
    # the banner lists both late nights; the daytime one names the row the Scoreboard scores instead
    by_thesis = {n.thesis_id: n for n in late_nights(db, upto=_MON)}
    assert by_thesis[daytime].earlier_recorded_at == _et(10, 12, 9, 15)
    assert by_thesis[late].earlier_recorded_at is None


def test_the_boundaries_FRIDAY_runs_to_MONDAY_06_00_and_a_reconstructed_only_night_is_not_late_only(
    client, db, monkeypatch
):
    """Friday 10-09's on-time window runs to MONDAY 06:00 ET (the trigger's Mon-Fri calendar):
    - a weekend catch-up (Saturday noon) and one at Monday 05:59:59 are ON TIME — not listed;
    - a row at Monday 06:00:00 sharp is LATE — listed, with Monday's EDGAR open as the window it missed;
    - a thesis whose night `pipeline.backfill` reconstructed (the real backfill, `reconstructed = true`) is
      NOT late-only — it is its own marker, the quiet ``reconstructed_nights`` count."""
    saturday = _thesis(db, "Saturday")
    edge = _thesis(db, "Mon-05:59:59")
    late = _thesis(db, "Mon-06:00")
    backfilled = _thesis(db, "Backfilled")
    _row(db, saturday, _FRI, _et(10, 10, 12, 0))
    _row(db, edge, _FRI, _et(10, 12, 5, 59, 59))
    _row(db, late, _FRI, _et(10, 12, 6, 0))
    run_backfill(
        db, asof=_FRI, known_at=_et(10, 12, 9, 0).astimezone(timezone.utc), thesis_id=backfilled
    )
    _finished(_FRI, _et(10, 9, 22, 30), 4)
    monkeypatch.setattr(admin, "_now", lambda: _et(10, 12, 12, 0))  # Monday noon: expected = Friday

    before = _table_counts(db)
    body = client.get("/admin/status").json()
    assert _table_counts(db) == before

    rec = body["record"]
    [only] = rec["late_only_nights"]
    assert (only["thesis_id"], only["asof"]) == (str(late), "2026-10-09")
    assert only["recorded_at"] == "2026-10-12T06:00:00-04:00"
    assert only["edgar_open"] == "2026-10-12T06:00:00-04:00"
    # the backfilled thesis is the quiet reconstructed marker, never the late one
    assert rec["reconstructed_nights"] == [
        {"asof": "2026-10-09", "reconstructed": 1, "expected": 4}
    ]
    assert rec["partial_nights"] == []
    assert body["cron"]["status"] == "healthy", body["cron"]
    assert body["cron"]["alarms"] == []


def test_a_clean_window_lists_no_late_only_night(client, db, monkeypatch):
    """Every row on time: the field is empty (the page then renders nothing — honest loudness)."""
    theses = [_thesis(db, f"Clean-{i}") for i in range(2)]
    for i, tid in enumerate(theses):
        _row(db, tid, _MON, _et(10, 12, 22, 42, i))
    _finished(_MON, _et(10, 12, 22, 30), 2)
    monkeypatch.setattr(admin, "_now", lambda: _et(10, 13, 9, 0))
    body = client.get("/admin/status").json()
    assert body["record"]["late_only_nights"] == []
    assert body["cron"]["status"] == "healthy", body["cron"]
