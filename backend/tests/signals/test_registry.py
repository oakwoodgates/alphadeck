from __future__ import annotations

from datetime import timedelta
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
