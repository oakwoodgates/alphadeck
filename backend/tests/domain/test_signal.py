from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.enums import Grade, Kind, Role
from domain.signal import Provenance, SignalEvent


def _entry(**updates) -> SignalEvent:
    values = {
        "detector": "test",
        "security_id": uuid4(),
        "role": Role.ENTRY_TRIGGER,
        "kind": Kind.INSIDER,
        "grade": Grade.CORE,
        "score": 0.8,
        "fired": True,
        "label": "test signal",
        "alpha_liveness_days": 30,
        "provenance": [Provenance(source="test", ref="fact-1")],
        "asof": date(2026, 6, 1),
    }
    values.update(updates)
    return SignalEvent(**values)


def test_fired_signal_requires_provenance():
    with pytest.raises(ValidationError, match="must carry provenance"):
        _entry(provenance=[])


def test_fired_risk_signal_also_requires_provenance():
    with pytest.raises(ValidationError, match="must carry provenance"):
        _entry(
            role=Role.RISK_SIGNAL,
            kind=Kind.DILUTION_RISK,
            grade=None,
            alpha_liveness_days=None,
            provenance=[],
        )


def test_fired_entry_trigger_requires_alpha_liveness():
    with pytest.raises(ValidationError, match="must carry alpha_liveness_days"):
        _entry(alpha_liveness_days=None)


def test_fired_risk_signal_may_be_maturity_gated_without_alpha_liveness():
    event = _entry(
        role=Role.RISK_SIGNAL,
        kind=Kind.DILUTION_RISK,
        grade=None,
        alpha_liveness_days=None,
    )
    assert event.alpha_liveness_days is None
