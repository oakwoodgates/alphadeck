"""L1 (operator Q5 = A) — a LATE-recorded row never defines a Scoreboard episode; an on-time row beats a late one.

THE SHAPE IS THE MEASURED 2026-10-12 NIGHT, not a fitted one. The 22:30 ET pass recorded 4 theses at
22:42-22:43 and died; the next day's boot catch-up recorded 9 theses at 17:04-17:13 ET on 10-13 — ~18 h after
the night and ~11 h after its on-time window closed at the 06:00 ET EDGAR open — and for 4 theses the late row
superseded the honest on-time one in the per-as-of dedup (AI Memory & Storage: seq 1334 at 22:42 -> seq 1367
at 17:04). Each test writes rows at those real stamps through the Scoreboard helpers (the nightly shape is
``on_time``; a late catch-up passes its own stamp), then reads the record the way the Scoreboard does.

Every case also states the counterfactual — the include-late reading, which is exactly the record as it read
before L1 — so it is the filter that moves the answer, not the fixture.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from domain.settings import get_settings
from pipeline.night_coverage import next_edgar_open
from replay.episodes import derive_episodes
from replay.schema import CallSnapshot
from repositories import call_annotations_repo, calls_repo
from scoreboard.assemble import assemble_scoreboard
from scoreboard.late import judge_late_nights, late_call_ids, late_nights
from scoreboard.record import derive_thesis_record, scoreboard_records
from tests.scoreboard.helpers import keys_fired, on_time, persist_thesis, record_day

_NY = ZoneInfo("America/New_York")
_FRI = date(2026, 10, 9)
_MON = date(2026, 10, 12)  # the night that died
_TUE = date(2026, 10, 13)
_WED = date(2026, 10, 14)
_ON_TIME = datetime(2026, 10, 12, 22, 42, tzinfo=_NY)  # the honest row (AI Memory's seq 1334)
_LATE = datetime(2026, 10, 13, 17, 4, tzinfo=_NY)  # the boot catch-up's re-version (seq 1367)
_READ = date(2026, 10, 16)


def _et(y, m, d, hh, mm, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=_NY)


def _keys(security_id):
    return keys_fired(security_id, date(2026, 10, 8), conv_liveness=60, conf_liveness=60)


def _arms(record) -> list[tuple[date, date | None]]:
    return [(e.episode.arm_date, e.episode.dearm_date) for e in record.episodes]


def _include_late_episodes(db, thesis_id) -> list[tuple[date, date | None]]:
    """The record as it read BEFORE L1, independently of ``scoreboard/late.py``: the honest-row read
    (reconstructed set aside, nothing else), replay's ``derive_episodes`` as-is."""
    cards = list(reversed(calls_repo.latest_for_thesis(db, thesis_id, include_reconstructed=False)))
    return [
        (e.arm_date, e.dearm_date)
        for e in derive_episodes([CallSnapshot.from_card(c) for c in cards])
    ]


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test-local literal
        return cur.fetchone()["n"]


# --- the rule: an on-time row WINS over a later late re-version ------------------------------------------


def test_an_on_time_row_wins_and_the_episode_is_unchanged_by_the_late_re_version(db, security_id):
    """The 10-12 on-time row (warming) is superseded at 17:04 the next day by a catch-up re-version that
    ARMS (the shape of the measured night: the late card cited a Form 4 SEC accepted on 10-13). Under the
    default reading the record is EXACTLY what it was before the late row landed — same snapshots, same
    (zero) episodes; under the include reading the late row wins the dedup and opens an episode."""
    thesis = persist_thesis(db, security_id)
    conv, conf = _keys(security_id)
    record_day(db, thesis, [conv], _FRI)  # on time: warming
    record_day(db, thesis, [conv], _MON, recorded_at=_ON_TIME)  # on time: warming
    record_day(db, thesis, [conv], _TUE)  # on time: warming
    before, before_snaps = derive_thesis_record(db, thesis, _READ)

    record_day(db, thesis, [conv, conf], _MON, recorded_at=_LATE)  # the late re-version: "armed"

    after, after_snaps = derive_thesis_record(db, thesis, _READ)
    assert [s.model_dump() for s in after_snaps] == [s.model_dump() for s in before_snaps]
    assert [e.model_dump() for e in after.episodes] == [e.model_dump() for e in before.episodes]
    assert after.episodes == [] and after.current_state == "warming"
    # the counterfactual: the late row wins the per-as-of dedup and arms the night
    assert _include_late_episodes(db, thesis.id) == [(_MON, _TUE)]
    inc, _ = derive_thesis_record(db, thesis, _READ, exclude_late=False)
    assert _arms(inc) == [(_MON, _TUE)]


def test_the_on_time_rows_stamps_caption_the_episode_never_the_late_rows(db, security_id):
    """All four winning-row peer reads drop the SAME late ids: the arm's ingest stamp and run identity are
    the ON-TIME row's, never the catch-up's (a caption describing a different row than the one scored is
    worse than none — the 0044 must-match rule, extended to L1)."""
    thesis = persist_thesis(db, security_id)
    conv, conf = _keys(security_id)
    record_day(db, thesis, [conv], _FRI)
    record_day(
        db,
        thesis,
        [conv, conf],
        _MON,
        recorded_at=_ON_TIME,
        ingest_fresh=False,
        ingest_errors=1,
        config_hash="a1b2c3d4e5f60718",
        code_sha="9f8e7d6c5b4a3210",
        run_kind="cron",
    )
    record_day(
        db,
        thesis,
        [conv, conf],
        _MON,
        recorded_at=_LATE,
        ingest_fresh=True,
        ingest_errors=0,
        config_hash="ffffffff00000000",
        code_sha="0000000000000000",
        run_kind="cron",
    )

    (ep,) = derive_thesis_record(db, thesis, _READ)[0].episodes
    assert ep.episode.arm_date == _MON
    assert ep.arm_ingest_fresh is False and ep.ingest_flagged is True  # the on-time row's stamp
    assert (ep.arm_config_hash, ep.arm_code_sha) == ("a1b2c3d4e5f60718", "9f8e7d6c5b4a3210")
    # the include reading reads the late row's stamps — the same arm, a different row
    (inc,) = derive_thesis_record(db, thesis, _READ, exclude_late=False)[0].episodes
    assert inc.arm_ingest_fresh is True and inc.arm_config_hash == "ffffffff00000000"


# --- a late-ONLY night reads like a cron gap ------------------------------------------------------------


def test_a_late_only_arm_opens_nothing(db, security_id):
    """The night's ONLY row is the catch-up's (the thesis never got an on-time row — 8 of the 9 measured
    theses): it reads as a gap, so the arm it claims never happened on the record."""
    thesis = persist_thesis(db, security_id)
    conv, conf = _keys(security_id)
    record_day(db, thesis, [conv], _FRI)  # on time: warming
    record_day(db, thesis, [conv, conf], _MON, recorded_at=_LATE)  # late-only: "armed"
    record_day(db, thesis, [conv], _TUE)  # on time: warming

    record, snaps = derive_thesis_record(db, thesis, _READ)
    assert [s.asof for s in snaps] == [_FRI, _TUE]  # the record last spoke on 10-09, then 10-13
    assert record.episodes == []
    assert _include_late_episodes(db, thesis.id) == [(_MON, _TUE)]


def test_a_late_only_de_arm_closes_nothing(db, security_id):
    """The mirror: a late-only "de-armed" night inside a run never closes it — ONE continuous episode."""
    thesis = persist_thesis(db, security_id)
    conv, conf = _keys(security_id)
    record_day(db, thesis, [conv], date(2026, 10, 8))  # warming: the arm below is not censored
    record_day(db, thesis, [conv, conf], _FRI)  # on time: armed
    record_day(db, thesis, [conv], _MON, recorded_at=_LATE)  # late-only: "de-armed"
    record_day(db, thesis, [conv, conf], _TUE)  # on time: still armed

    record, _ = derive_thesis_record(db, thesis, _READ)
    (ep,) = record.episodes
    assert (ep.episode.arm_date, ep.episode.dearm_date) == (_FRI, None)
    assert ep.episode.last_armed_date == _TUE and ep.status == "open"
    assert _include_late_episodes(db, thesis.id) == [(_FRI, _MON), (_TUE, None)]


# --- the boundary: 05:59 ET the next weekday is on time, 06:00 is late; a Friday runs to Monday --------


@pytest.mark.parametrize(
    ("asof", "stamp", "late"),
    [
        (_MON, _et(2026, 10, 13, 5, 59, 59), False),  # the last second of the window
        (_MON, _et(2026, 10, 13, 6, 0), True),  # the EDGAR open itself
        (_FRI, _et(2026, 10, 10, 12, 0), False),  # Saturday noon: Friday's window is still open
        (_FRI, _et(2026, 10, 11, 23, 0), False),  # Sunday night
        (_FRI, _et(2026, 10, 12, 5, 59), False),  # Monday 05:59
        (_FRI, _et(2026, 10, 12, 6, 0), True),  # Monday 06:00
        # DST ends Sunday 2026-11-01: Friday 10-30's window ends Monday 11-02 06:00 EST = 11:00 UTC
        (date(2026, 10, 30), datetime(2026, 11, 2, 10, 59, tzinfo=timezone.utc), False),
        (date(2026, 10, 30), datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc), True),
    ],
)
def test_the_late_boundary_on_real_rows(db, security_id, asof, stamp, late):
    thesis = persist_thesis(db, security_id)
    conv, _ = _keys(security_id)
    call_id = record_day(db, thesis, [conv], asof, recorded_at=stamp)
    assert (call_id in late_call_ids(db, thesis.id)) is late
    # and the record path agrees: a late-only night is absent from the timeline, an on-time one present
    _, snaps = derive_thesis_record(db, thesis, date(2026, 11, 6))
    assert [s.asof for s in snaps] == ([] if late else [asof])


def test_a_daytime_row_is_not_late_and_a_reconstructed_row_is_never_judged_late(db, security_id):
    """Only the window's END makes a row late: a pre-RUN_AT daytime row (a 09:15 "Run daily now") is not
    late, and a ``pipeline.backfill`` row is never judged late — it is set aside on its own marker.
    """
    thesis = persist_thesis(db, security_id)
    conv, _ = _keys(security_id)
    daytime = record_day(db, thesis, [conv], _MON, recorded_at=_et(2026, 10, 12, 9, 15))
    recon = record_day(db, thesis, [conv], _TUE, reconstructed=True, recorded_at=_LATE)
    assert {s["id"] for s in calls_repo.call_stamps(db, thesis_id=thesis.id)} == {daytime, recon}
    assert late_call_ids(db, thesis.id) == frozenset()


# --- reversible: the setting OFF and the param ON read exactly as before L1 ------------------------------


def _mixed_log(db, security_id):
    thesis = persist_thesis(db, security_id)
    conv, conf = _keys(security_id)
    record_day(db, thesis, [conv], date(2026, 10, 8))
    record_day(db, thesis, [conv, conf], _FRI)  # armed, on time
    record_day(db, thesis, [conv], _MON, recorded_at=_ON_TIME)  # on time: de-armed
    record_day(db, thesis, [conv, conf], _MON, recorded_at=_LATE)  # late re-version: armed
    record_day(db, thesis, [conv, conf], _TUE, recorded_at=_et(2026, 10, 15, 8, 0))  # late-only
    record_day(db, thesis, [conv], _WED)  # on time: de-armed
    return thesis


def test_the_setting_off_and_the_param_are_identical_to_the_record_before_L1(
    db, security_id, monkeypatch
):
    thesis = _mixed_log(db, security_id)
    pre_l1 = _include_late_episodes(db, thesis.id)
    assert pre_l1 == [(_FRI, _WED)]  # the late rows bridge 10-12 and 10-13

    param = assemble_scoreboard(db, asof=_READ, exclude_late=False)
    monkeypatch.setenv("ALPHADECK_SCOREBOARD_EXCLUDE_LATE", "false")
    get_settings.cache_clear()
    try:
        off = assemble_scoreboard(db, asof=_READ)
    finally:
        monkeypatch.delenv("ALPHADECK_SCOREBOARD_EXCLUDE_LATE")
        get_settings.cache_clear()
    assert param.summary.late_policy == off.summary.late_policy == "included"
    assert off.model_dump() == param.model_dump()  # the env path and the param path: one reading
    assert _arms(param.theses[0]) == pre_l1

    default = assemble_scoreboard(db, asof=_READ)
    assert default.summary.late_policy == "excluded"
    # set aside: 10-12 scores its on-time de-arm, 10-13 reads as a gap -> the run closes on 10-12
    assert _arms(default.theses[0]) == [(_FRI, _MON)]
    # the banner names both late nights under BOTH readings
    assert default.summary.late_nights == param.summary.late_nights


# --- the banner list ------------------------------------------------------------------------------------


def test_late_nights_names_each_night_its_stamp_and_what_is_scored_instead(db, security_id):
    thesis = _mixed_log(db, security_id)
    nights = late_nights(db, upto=_READ)
    assert [(n.thesis_name, n.asof, n.n_rows) for n in nights] == [
        (thesis.name, _MON, 1),
        (thesis.name, _TUE, 1),
    ]
    mon, tue = nights
    assert mon.recorded_at == _LATE and mon.edgar_open == _et(2026, 10, 13, 6, 0)
    assert mon.earlier_recorded_at == _ON_TIME  # the on-time row the default reading scores
    assert tue.earlier_recorded_at is None  # nothing else that night: it reads like a cron gap
    assert mon.reason == "recorded after the next EDGAR open"
    # capped at the view's as-of: a scrubbed-back view names only the nights it can see
    assert [n.asof for n in late_nights(db, upto=_MON)] == [_MON]
    assert late_nights(db, upto=_FRI) == []


def test_judge_late_nights_pure_contract():
    """Stated with direct inputs: two late rows on one night count once (the FIRST late stamp), the winner
    under the excluding reading is the highest-seq non-late honest row (a reconstruction never wins).
    """
    import uuid

    tid = uuid.uuid4()

    def row(seq, stamp, reconstructed=False):
        return {
            "id": uuid.uuid4(),
            "seq": seq,
            "thesis_id": tid,
            "thesis_name": "AI Memory & Storage",
            "asof": _MON,
            "reconstructed": reconstructed,
            "recorded_at": stamp,
        }

    daytime = _et(2026, 10, 12, 9, 15)
    stamps = [
        row(1, daytime),
        row(2, _ON_TIME),
        row(3, _et(2026, 10, 13, 8, 0), reconstructed=True),
        row(4, _LATE),
        row(5, _et(2026, 10, 13, 17, 10)),
    ]
    (n,) = judge_late_nights(stamps, tz=_NY)
    assert (n.recorded_at, n.n_rows, n.earlier_recorded_at) == (_LATE, 2, _ON_TIME)
    assert n.edgar_open == next_edgar_open(_MON, _NY)
    assert judge_late_nights(stamps[:3], tz=_NY) == []  # no late row: nothing to list


# --- composition: reconstructed (always) AND annotated (its policy) AND late (this one) -----------------


def test_the_three_filters_compose_by_AND(db, security_id):
    """10-12 holds an on-time row annotated ``exclude`` (a phantom arm) AND a late re-version; 10-13 holds a
    reconstruction AND a late row. A row is scored only if it is not reconstructed, not annotated (under that
    policy) and not late (under this one) — each reading toggles exactly its own rows."""
    thesis = persist_thesis(db, security_id)
    conv, conf = _keys(security_id)
    record_day(db, thesis, [conv], _FRI)  # on time: warming
    phantom = record_day(db, thesis, [conv, conf], _MON, recorded_at=_ON_TIME)  # on time: "armed"
    record_day(db, thesis, [conv], _MON, recorded_at=_LATE)  # late: warming
    record_day(db, thesis, [conv, conf], _TUE, reconstructed=True)  # a backfill: "armed"
    record_day(db, thesis, [conv, conf], _TUE, recorded_at=_et(2026, 10, 14, 9, 0))  # late: armed
    record_day(db, thesis, [conv], _WED)  # on time: warming
    call_annotations_repo.append_annotation(
        db, phantom, action="exclude", reason="stale_comparative_revenue", rule="d3a@t"
    )
    db.commit()

    def arms(**kw):
        rec, snaps = derive_thesis_record(db, thesis, _READ, **kw)
        return _arms(rec), [s.asof for s in snaps]

    # default: the annotated on-time row AND both late rows set aside -> 10-12 and 10-13 are gaps
    assert arms() == ([], [_FRI, _WED])
    # include annotated only: the phantom on-time row is scored (the late re-version still set aside)
    assert arms(exclude_annotated=False) == ([(_MON, _WED)], [_FRI, _MON, _WED])
    # include late only: the late rows win their nights (warming on 10-12; armed on 10-13)
    assert arms(exclude_late=False) == ([(_TUE, _WED)], [_FRI, _MON, _TUE, _WED])
    # both included: latest-append wins on 10-12 (the late warming), 10-13 is the late arm
    assert arms(exclude_annotated=False, exclude_late=False) == arms(exclude_late=False)
    # a reconstruction is never scored under ANY reading — the 10-13 arm above is the late row's
    assert _include_late_episodes(db, thesis.id) == [(_TUE, _WED)]

    summary = assemble_scoreboard(db, asof=_READ).summary
    # 10-13 is NOT a reconstructed-only night (a late honest row shares it) -> named in the LATE list.
    # 10-12's earlier_recorded_at is the on-time row ignoring the ANNOTATED filter (that row is itself set
    # aside under the annotated reading, and named in annotated_nights) — the late judgment is its own.
    assert summary.reconstructed_nights == []
    assert [(n.asof, n.earlier_recorded_at) for n in summary.late_nights] == [
        (_MON, _ON_TIME),
        (_TUE, None),
    ]
    assert [n.asof for n in summary.annotated_nights] == [_MON]


# --- the read writes nothing ---------------------------------------------------------------------------


def test_every_reading_writes_nothing(db, security_id):
    _mixed_log(db, security_id)
    before = (_count(db, "calls"), _count(db, "call_annotation"))
    for kw in ({}, {"exclude_late": False}, {"exclude_late": True}):
        assemble_scoreboard(db, asof=_READ, **kw)
        scoreboard_records(db, _READ, **kw)
    assert (_count(db, "calls"), _count(db, "call_annotation")) == before


def test_on_time_helper_is_inside_the_window():
    """The fixture's nightly stamp is genuinely on time (so the rest of the Scoreboard suite reads it)."""
    assert on_time(_MON) < next_edgar_open(_MON, _NY)
    assert on_time(_FRI) < next_edgar_open(_FRI, _NY)
