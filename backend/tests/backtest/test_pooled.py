from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

from backtest.nulls import EpisodeNulls, NullDraw
from backtest.pooled import SLICE_KEYS, build_report
from domain.enums import Grade, Verdict
from replay.metrics import MIN_N
from replay.schema import Episode

# B4 — the POOLED view. The unit is the ALGORITHM, never the thesis: pooling across theses tests the
# machinery, slicing outcomes BY thesis tests the idea, which is the leaderboard trap and a #4 violation.
# Q4 is enforced structurally rather than by convention, and the scanning test below is the enforcement.

_T0 = date(2026, 6, 1)


def _ep(tid, sid, **over) -> Episode:
    base = dict(
        thesis_id=tid,
        security_id=sid,
        is_headline=True,
        arm_date=_T0,
        last_armed_date=_T0,
        close_reason="window_end",
        verdict=Verdict.CORE_ENTRY,
        entry_grade=Grade.CORE,
        conviction_grade=Grade.CORE,
        confirmation_grade=Grade.CORE,
        exit_by=_T0 + timedelta(days=20),
        key1_source="insider",
        key1_sources=["insider"],
        co_arm_bucket="alone",
        co_arm_count=0,
        armed_count_that_night=1,
    )
    base.update(over)
    return Episode(**base)


def _nulls(ep, *, real=0.10, timing=(0.01, 0.02), name=(0.03,), excess=0.04) -> EpisodeNulls:
    def draw(kind, r):
        return NullDraw(
            kind=kind,
            thesis_id=ep.thesis_id,
            episode_security_id=ep.security_id,
            episode_arm_date=ep.arm_date,
            security_id=ep.security_id,
            entry_date=ep.arm_date,
            horizon_days=20,
            forward_return=r,
            excess_return=r,
        )

    return EpisodeNulls(
        thesis_id=ep.thesis_id,
        security_id=ep.security_id,
        arm_date=ep.arm_date,
        horizon_days=20,
        forward_return=real,
        excess_return=excess,
        timing=[draw("timing", r) for r in timing],
        name=[draw("name", r) for r in name],
    )


# --- Q4, structurally -------------------------------------------------------------------------------


def test_the_pooled_payload_carries_no_thesis_identifier_anywhere():
    """The enforcement, not a convention. Walks the SERIALIZED payload for any thesis id -- so a future
    field that happens to carry one fails here rather than quietly becoming a leaderboard."""
    tids = [uuid.uuid4() for _ in range(3)]
    eps = [_ep(t, uuid.uuid4()) for t in tids]
    report = build_report(eps, [_nulls(e) for e in eps], draws=2, seed="s")
    blob = report.model_dump_json()
    for t in tids:
        assert str(t) not in blob
    payload = json.loads(blob)

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert "thesis" not in k.lower(), k
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)


def test_the_slices_are_algorithm_dimensions_only():
    """Key-1 source x confirmation grade x co-arm bucket x close reason -- and nothing else. A slice key
    that named a thesis, a ticker or a sector would be a per-idea view wearing a pooled label."""
    tid = uuid.uuid4()
    eps = [_ep(tid, uuid.uuid4())]
    report = build_report(eps, [_nulls(e) for e in eps], draws=2, seed="s")
    assert set(SLICE_KEYS) == {
        "key1_source",
        "confirmation_grade",
        "co_arm_bucket",
        "close_reason",
    }
    for s in report.slices:
        assert set(s.key) == set(SLICE_KEYS)


# --- every metric carries its nulls -----------------------------------------------------------------


def test_every_metric_reports_both_nulls_and_the_excess():
    """An absolute number on a hindsight universe is not evidence; the gaps are."""
    tid = uuid.uuid4()
    eps = [_ep(tid, uuid.uuid4()) for _ in range(3)]
    report = build_report(eps, [_nulls(e) for e in eps], draws=2, seed="s")
    assert report.metrics
    for m in report.metrics:
        assert m.actual.n and m.vs_timing.n and m.vs_name.n and m.excess.n


def test_the_pooled_metric_pools_across_theses():
    """Three theses, one number: that IS the design."""
    eps = [_ep(uuid.uuid4(), uuid.uuid4()) for _ in range(3)]
    report = build_report(eps, [_nulls(e) for e in eps], draws=2, seed="s")
    assert report.metrics[0].actual.n == 3
    assert report.n_episodes == 3 and report.n_scoreable == 3


def test_insufficient_n_is_flagged_rather_than_hidden():
    tid = uuid.uuid4()
    eps = [_ep(tid, uuid.uuid4())]
    report = build_report(eps, [_nulls(e) for e in eps], draws=2, seed="s")
    assert report.min_n == MIN_N
    assert report.metrics[0].insufficient_n is True  # n=1 < MIN_N
    assert all(s.insufficient_n for s in report.slices)


def test_an_unscoreable_episode_is_counted_but_not_averaged():
    """A name with no tape must not silently become a zero return."""
    tid = uuid.uuid4()
    good, bad = _ep(tid, uuid.uuid4()), _ep(tid, uuid.uuid4())
    report = build_report([good, bad], [_nulls(good), _nulls(bad, real=None)], draws=2, seed="s")
    assert report.n_episodes == 2 and report.n_scoreable == 1
    assert report.metrics[0].actual.n == 1


# --- the slices -------------------------------------------------------------------------------------


def test_episodes_land_in_the_slice_their_attributes_name():
    tid = uuid.uuid4()
    a = _ep(tid, uuid.uuid4(), key1_source="insider", co_arm_bucket="alone")
    b = _ep(tid, uuid.uuid4(), key1_source="revenue_accel", co_arm_bucket="8+")
    report = build_report([a, b], [_nulls(a), _nulls(b)], draws=1, seed="s")
    keys = {(s.key["key1_source"], s.key["co_arm_bucket"]) for s in report.slices}
    assert keys == {("insider", "alone"), ("revenue_accel", "8+")}
    assert all(s.n == 1 for s in report.slices)


def test_a_missing_attribute_slices_as_none_not_dropped():
    """An episode with no Key-1 source is still an episode; dropping it would quietly shrink the pool."""
    tid = uuid.uuid4()
    ep = _ep(tid, uuid.uuid4(), key1_source=None)
    report = build_report([ep], [_nulls(ep)], draws=1, seed="s")
    assert report.slices[0].key["key1_source"] == "none"
    assert report.n_scoreable == 1


# --- firing diagnostics (no outcome, no hindsight risk) ---------------------------------------------


def test_the_diagnostics_report_the_independence_fact_as_a_number():
    """82% of episodes on the record armed alongside a co-member. Effective n is far below the episode
    count, and the report says so rather than leaving it to be inferred."""
    tid = uuid.uuid4()
    alone = _ep(tid, uuid.uuid4(), co_arm_count=0, co_arm_bucket="alone")
    burst = [
        _ep(tid, uuid.uuid4(), co_arm_count=7, co_arm_bucket="8+", armed_count_that_night=8)
        for _ in range(3)
    ]
    eps = [alone, *burst]
    report = build_report(eps, [_nulls(e) for e in eps], draws=1, seed="s")
    d = report.diagnostics
    assert d.pct_armed_with_a_co_member == 0.75
    assert d.widest_single_session_group == 8
    assert d.co_arm_bucket_mix == {"8+": 3, "alone": 1}


def test_the_diagnostics_carry_no_outcome():
    """The half of the report with no hindsight risk -- counts and mixes only, which is what catches a
    broken detector without any claim about returns."""
    tid = uuid.uuid4()
    eps = [_ep(tid, uuid.uuid4())]
    d = build_report(eps, [_nulls(e) for e in eps], draws=1, seed="s").diagnostics
    blob = json.loads(d.model_dump_json())
    for k in blob:
        assert "return" not in k and "excess" not in k


def test_the_seed_and_K_ride_the_report():
    """A null is evidence, so it must be re-derivable: the report says how many draws and from what seed."""
    tid = uuid.uuid4()
    eps = [_ep(tid, uuid.uuid4())]
    report = build_report(eps, [_nulls(e) for e in eps], draws=37, seed="run-xyz")
    assert report.null_draws == 37 and report.null_seed == "run-xyz"


def test_the_banner_states_the_posture():
    tid = uuid.uuid4()
    eps = [_ep(tid, uuid.uuid4())]
    banner = build_report(eps, [_nulls(e) for e in eps], draws=1, seed="s").banner.lower()
    for token in ("pooled", "algorithm", "never the thesis", "not independent"):
        assert token in banner
