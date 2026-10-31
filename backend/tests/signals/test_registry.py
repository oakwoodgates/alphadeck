from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from domain.config import DEFAULT_CONFIG
from pipeline.core import assemble_from_pit
from signals import (
    activist_stake,
    breakdown,
    breakout_52w,
    catalyst_conviction,
    corporate_catalyst,
    corporate_risk,
    dilution_clock,
    insider_conviction,
    insider_sell,
    registered_detectors,
    revenue_acceleration,
    share_creep,
    volume_breakout,
)
from signals.base import Detector
from signals.common import entry_signal_is_live
from signals.registry import register_detector
from tests.calls.factories import ASOF, SID, insider_event, make_thesis


def test_registry_contains_exactly_the_builtins_in_pipeline_order():
    detectors = registered_detectors()
    assert [detector.name for detector in detectors] == [
        "insider_conviction",
        "catalyst_conviction",
        "volume_breakout",
        "dilution_clock",
        "revenue_acceleration",  # §2.2 — appended after the original four
        "breakout_52w",  # §2.3 — appended after revenue_acceleration
        "breakdown_core",  # §2.5 — the two grade-aware structural de-arm RISK detectors
        "breakdown_flip",  # §3.3 — (existing detectors keep their order)
        "insider_sell",  # Band 03 S1 — the sell-cluster RISK detector (master switch OFF)
        "corporate_catalyst",  # Band 03 S3 — the 8-K item-tape pair (switches OFF)
        "corporate_risk",
        "share_creep",  # Band 03 S4 — the share-count-creep RISK detector (switch OFF)
        "activist_stake",  # Band 03 S5 — the SC 13D conviction trigger, appended LAST (switch OFF)
    ]
    assert [detector.detect for detector in detectors] == [
        insider_conviction.detect,
        catalyst_conviction.detect,
        volume_breakout.detect,
        dilution_clock.detect,
        revenue_acceleration.detect,
        breakout_52w.detect,
        breakdown.detect_core,
        breakdown.detect_flip,
        insider_sell.detect,
        corporate_catalyst.detect,
        corporate_risk.detect,
        share_creep.detect,
        activist_stake.detect,
    ]


def test_registry_rejects_duplicate_detector_names():
    duplicate = Detector(
        name="insider_conviction",
        detect=lambda pit, security_id, asof, cfg: None,
        horizons=lambda cfg: {},
    )
    with pytest.raises(ValueError, match="already registered"):
        register_detector(duplicate)


def test_detector_rejects_an_event_stamped_with_another_name():
    detector = Detector(
        name="expected_name",
        detect=lambda pit, security_id, asof, cfg: insider_event(),
        horizons=lambda cfg: {},
    )
    with pytest.raises(ValueError, match="emitted event stamped"):
        detector(SimpleNamespace(), SID, ASOF, DEFAULT_CONFIG)


def test_entry_liveness_needs_the_next_session_inside_the_window():
    """F3 (rule log ``2026-11-entry-window-next-session``): a trigger is live on night N iff the first session after
    N is on or before ``fire + liveness``. The window's own last night is NOT live when its next session is past the
    end (the call is recorded after the close, so nobody could act on it); the night before it is.
    """
    fire_date = ASOF  # Tue 2026-06-02; + 10 days = Fri 2026-06-12, a session
    end = fire_date + timedelta(days=10)
    assert entry_signal_is_live(fire_date, 10, fire_date)  # the fire night itself
    assert entry_signal_is_live(
        fire_date, 10, end - timedelta(days=1)
    )  # Thu: Fri's session is the last one
    assert not entry_signal_is_live(
        fire_date, 10, end
    )  # Fri: the next session (Mon) is past the end
    assert not entry_signal_is_live(fire_date, 10, end + timedelta(days=1))


def test_entry_liveness_a_weekend_or_holiday_end_closes_on_the_last_session_before_it():
    """A calendar-day window can end on a weekend or a market holiday: its last live night is the one whose next
    session is the window's last SESSION, so the night of that session already reads it closed."""
    # Tue 2026-06-02 + 4 = Sat 06-06: Thu is live (Fri's session left), Fri is not (Monday is past Saturday)
    assert entry_signal_is_live(date(2026, 6, 2), 4, date(2026, 6, 4))
    assert not entry_signal_is_live(date(2026, 6, 2), 4, date(2026, 6, 5))
    # Fri 2026-08-28 + 10 = Mon 09-07, Labor Day (no session): Thu 09-03 is live, Fri 09-04's next session is Tue
    assert entry_signal_is_live(date(2026, 8, 28), 10, date(2026, 9, 3))
    assert not entry_signal_is_live(date(2026, 8, 28), 10, date(2026, 9, 4))


def test_entry_liveness_never_stretches_a_window_too_short_for_a_session():
    """No window is stretched to reach a session: a Friday fire with one day of liveness (to Saturday) has no
    session in it, so it is not live even on its own fire night."""
    assert not entry_signal_is_live(date(2026, 6, 5), 1, date(2026, 6, 5))
    assert entry_signal_is_live(date(2026, 6, 4), 1, date(2026, 6, 4))  # Thu + 1 = Fri, a session


def test_pipeline_rejects_a_mismatched_explicit_asof():
    pit = SimpleNamespace(asof=ASOF - timedelta(days=1))
    with pytest.raises(ValueError, match="does not match point-in-time view"):
        assemble_from_pit(pit, make_thesis(), ASOF, DEFAULT_CONFIG)
