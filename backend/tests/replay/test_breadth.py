from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from domain.call import Provenance, TriggerRef
from domain.enums import Grade, Kind, State, Verdict
from replay.episodes import derive_episodes
from replay.key1 import co_arm_bucket, key1_source, key1_sources
from replay.run import arrow_schema
from replay.schema import CallSnapshot, Episode, MemberRow

# B3 — BREADTH on every arm episode. 82% of the arm episodes on the record arrived alongside a co-member
# the same thesis-night, so without these fields the 18% that armed alone are indistinguishable from a
# thirteen-name burst, and every pooled metric would treat one broadcast as thirteen independent
# observations. These tests pin the counting rules, which are the easy part to get subtly wrong.

_T0 = date(2026, 6, 1)


def _trig(kind: Kind, source: str, sid) -> TriggerRef:
    return TriggerRef(
        label="x",
        kind=kind,
        grade=Grade.CORE,
        event_date=_T0,
        security_id=sid,
        sources=[Provenance(source=source, ref="r-1")],
    )


def _member(sid, *, tier="armed", triggers=None, confirmation=Grade.CORE) -> MemberRow:
    return MemberRow(
        security_id=sid,
        tier=tier,
        verdict=Verdict.CORE_ENTRY if tier == "armed" else None,
        conviction_grade=Grade.CORE if tier == "armed" else None,
        confirmation_grade=confirmation,
        entry_grade=Grade.CORE if tier == "armed" else None,
        exit_by=_T0 + timedelta(days=180),
        arm_until=_T0 + timedelta(days=10),
        triggers=triggers or [],
    )


def _snap(tid, day_offset: int, members: list[MemberRow]) -> CallSnapshot:
    armed = [m for m in members if m.tier == "armed"]
    return CallSnapshot(
        thesis_id=tid,
        asof=_T0 + timedelta(days=day_offset),
        state=State.ARMED if armed else State.WARMING,
        verdict=Verdict.CORE_ENTRY if armed else Verdict.NOT_YET,
        armed_security_id=armed[0].security_id if armed else None,
        members=members,
    )


# --- the two counts ---------------------------------------------------------------------------------


def test_co_arm_count_counts_only_NEWLY_armed_members():
    """The decision made that night, not the loudness on screen. A member already armed yesterday is
    sticky, not a co-arm: counting it would report a fresh burst every day the first name stayed armed.
    """
    tid = uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    snaps = [
        _snap(tid, 0, [_member(a)]),  # a arms alone
        _snap(tid, 1, [_member(a), _member(b), _member(c)]),  # b and c arm together; a is sticky
    ]
    eps = {e.security_id: e for e in derive_episodes(snaps)}
    assert eps[a].co_arm_count == 0 and eps[a].co_arm_bucket == "alone"
    assert eps[b].co_arm_count == 1 and eps[c].co_arm_count == 1
    assert eps[b].co_arm_bucket == "2-3"


def test_armed_count_that_night_counts_everything_armed():
    """...and the other number keeps the loudness, which `co_arm_count` deliberately does not. On day 1
    only two names newly armed, but THREE were armed — one thesis, two readings, both recorded."""
    tid = uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    snaps = [
        _snap(tid, 0, [_member(a)]),
        _snap(tid, 1, [_member(a), _member(b), _member(c)]),
    ]
    eps = {e.security_id: e for e in derive_episodes(snaps)}
    assert eps[b].co_arm_count == 1  # newly armed WITH it
    assert eps[b].armed_count_that_night == 3  # armed in total, sticky included
    assert eps[a].armed_count_that_night == 1


def test_a_re_arm_is_a_new_episode_with_its_own_breadth():
    """Arm -> de-arm -> re-arm is two episodes, and the second one's breadth is its own night's."""
    tid = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    snaps = [
        _snap(tid, 0, [_member(a)]),  # a alone
        _snap(tid, 1, [_member(a, tier="watch")]),  # a de-arms
        _snap(tid, 2, [_member(a), _member(b)]),  # a re-arms, this time with b
    ]
    eps = sorted(derive_episodes(snaps), key=lambda e: (e.arm_date, e.security_id.int))
    a_eps = [e for e in eps if e.security_id == a]
    assert len(a_eps) == 2
    assert a_eps[0].co_arm_count == 0 and a_eps[0].co_arm_bucket == "alone"
    assert a_eps[1].co_arm_count == 1 and a_eps[1].co_arm_bucket == "2-3"


def test_the_first_replayed_session_counts_its_arms_as_new():
    """The sweep cannot see behind its own window, so everything armed on day one is new to it. Treating
    them as sticky would report a burst of zero co-arms on the one day the window guarantees no
    information — the same censoring `censored_start` already marks on the scored side."""
    tid = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    eps = {e.security_id: e for e in derive_episodes([_snap(tid, 0, [_member(a), _member(b)])])}
    assert eps[a].co_arm_count == 1 and eps[b].co_arm_count == 1


# --- the bucket -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "group,bucket",
    [(1, "alone"), (2, "2-3"), (3, "2-3"), (4, "4-7"), (7, "4-7"), (8, "8+"), (13, "8+")],
)
def test_the_bucket_is_keyed_on_the_GROUP_size(group, bucket):
    """The §4.3 buckets are contiguous with no gap at a single co-arm, which is only true if they key on
    the size of the group that armed together rather than on co-arms-excluding-self. Easy to get
    backwards; pinned at every boundary."""
    assert co_arm_bucket(group) == bucket


def test_the_bucket_never_reports_a_group_of_zero():
    """An episode exists, so at least one member armed."""
    assert co_arm_bucket(0) == "alone"


# --- the Key-1 source -------------------------------------------------------------------------------


def test_the_three_catalyst_detectors_are_told_apart_by_provenance_source():
    """The whole reason this field is not keyed on `Kind`: three detectors emit `Kind.CATALYST` making
    three different claims about the world, and H1 is the difference between them."""
    sid = uuid.uuid4()
    assert key1_source([_trig(Kind.CATALYST, "xbrl", sid)]) == "revenue_accel"
    assert key1_source([_trig(Kind.CATALYST, "8-k", sid)]) == "corporate_catalyst"
    assert key1_source([_trig(Kind.CATALYST, "doe_usaspending", sid)]) == "ratified_catalyst"
    assert key1_source([_trig(Kind.CATALYST, "ratified", sid)]) == "ratified_catalyst"


def test_the_named_actor_kinds_map_straight_through():
    sid = uuid.uuid4()
    assert key1_source([_trig(Kind.INSIDER, "form4", sid)]) == "insider"
    assert key1_source([_trig(Kind.ACTIVIST_STAKE, "13d", sid)]) == "activist"
    assert key1_source([_trig(Kind.THEME_CONVICTION, "ratified", sid)]) == "theme"


def test_a_confirmation_trigger_is_not_a_key1_source():
    sid = uuid.uuid4()
    assert key1_source([_trig(Kind.TECHNICAL_BREAKOUT, "price", sid)]) is None
    assert key1_sources([_trig(Kind.LAGGARD, "price", sid)]) == []


def test_both_sources_are_kept_and_the_strongest_claim_wins_the_slice():
    """70 armed member-nights on the record carry an insider buy AND a catalyst. Collapsing them to one
    label would lose that; binning them as "insider" is what makes §4.2's "carrying revenue
    re-acceleration and NO insider buy" answerable."""
    sid = uuid.uuid4()
    triggers = [_trig(Kind.CATALYST, "xbrl", sid), _trig(Kind.INSIDER, "form4", sid)]
    assert key1_source(triggers) == "insider"
    assert key1_sources(triggers) == ["insider", "revenue_accel"]


def test_the_episode_carries_the_source_from_the_ARM_DATE_evidence():
    tid, sid = uuid.uuid4(), uuid.uuid4()
    snaps = [_snap(tid, 0, [_member(sid, triggers=[_trig(Kind.CATALYST, "xbrl", sid)])])]
    ep = derive_episodes(snaps)[0]
    assert ep.key1_source == "revenue_accel" and ep.key1_sources == ["revenue_accel"]


def test_the_confirmation_grade_is_copied_from_the_arm_snapshot_not_recomputed():
    tid, sid = uuid.uuid4(), uuid.uuid4()
    snaps = [_snap(tid, 0, [_member(sid, confirmation=Grade.FLIP)])]
    ep = derive_episodes(snaps)[0]
    assert ep.confirmation_grade is Grade.FLIP
    assert ep.entry_grade is Grade.CORE  # the pre-existing field is untouched


# --- the declared artifact schema -------------------------------------------------------------------


def test_the_arrow_schema_covers_every_episode_field():
    """`replay/run.py` declares the artifact's schema from the model so an empty run writes the same
    shape as a populated one. Adding a field must therefore land in the schema, not just the model.
    """
    assert arrow_schema(Episode).names == list(Episode.model_fields)


def test_the_new_list_field_is_declared_as_a_list():
    schema = arrow_schema(Episode)
    assert str(schema.field("key1_sources").type) == "list<item: string>"
    assert str(schema.field("co_arm_count").type) == "int64"
    assert str(schema.field("co_arm_bucket").type) == "string"


def test_an_empty_run_still_writes_the_full_breadth_schema(tmp_path):
    """The F3 rule survives the new fields: a zero-episode run leaves an EMPTY file carrying the whole
    schema, never nothing (which would leave a previous run's episodes beside a fresh manifest)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "episodes.parquet"
    pq.write_table(pa.Table.from_pylist([], schema=arrow_schema(Episode)), path)
    back = pq.read_table(path)
    assert back.num_rows == 0
    assert back.schema.names == list(Episode.model_fields)


def test_breadth_survives_the_json_round_trip_the_artifact_uses():
    """The artifact writes `model_dump(mode="json")`; the fields must survive it with their declared
    types (a str-enum confirmation grade becomes its wire value, the list stays a list)."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    snaps = [
        _snap(
            tid,
            0,
            [_member(sid, triggers=[_trig(Kind.INSIDER, "form4", sid)], confirmation=Grade.FLIP)],
        )
    ]
    dumped = derive_episodes(snaps)[0].model_dump(mode="json")
    assert dumped["key1_sources"] == ["insider"]
    assert dumped["confirmation_grade"] == "flip"
    assert dumped["co_arm_bucket"] == "alone"
    assert Episode.model_validate(dumped).key1_source == "insider"
