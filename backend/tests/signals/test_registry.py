from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from domain.config import DEFAULT_CONFIG
from pipeline.core import assemble_from_pit
from signals import (
    catalyst_conviction,
    dilution_clock,
    insider_conviction,
    registered_detectors,
    volume_breakout,
)
from signals.base import Detector
from signals.common import entry_signal_is_live
from signals.registry import register_detector
from tests.calls.factories import ASOF, SID, insider_event, make_thesis


def test_registry_contains_exactly_the_four_builtins_in_pipeline_order():
    detectors = registered_detectors()
    assert [detector.name for detector in detectors] == [
        "insider_conviction",
        "catalyst_conviction",
        "volume_breakout",
        "dilution_clock",
    ]
    assert [detector.detect for detector in detectors] == [
        insider_conviction.detect,
        catalyst_conviction.detect,
        volume_breakout.detect,
        dilution_clock.detect,
    ]


def test_registry_rejects_duplicate_detector_names():
    duplicate = Detector(
        name="insider_conviction",
        detect=lambda pit, security_id, asof, cfg: None,
    )
    with pytest.raises(ValueError, match="already registered"):
        register_detector(duplicate)


def test_detector_rejects_an_event_stamped_with_another_name():
    detector = Detector(
        name="expected_name",
        detect=lambda pit, security_id, asof, cfg: insider_event(),
    )
    with pytest.raises(ValueError, match="emitted event stamped"):
        detector(SimpleNamespace(), SID, ASOF, DEFAULT_CONFIG)


def test_entry_liveness_is_inclusive_at_the_alpha_horizon():
    fire_date = ASOF
    assert entry_signal_is_live(fire_date, 10, fire_date + timedelta(days=10))
    assert not entry_signal_is_live(fire_date, 10, fire_date + timedelta(days=11))


def test_pipeline_rejects_a_mismatched_explicit_asof():
    pit = SimpleNamespace(asof=ASOF - timedelta(days=1))
    with pytest.raises(ValueError, match="does not match point-in-time view"):
        assemble_from_pit(pit, make_thesis(), ASOF, DEFAULT_CONFIG)
