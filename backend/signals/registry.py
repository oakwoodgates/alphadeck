from __future__ import annotations

from signals.base import Detector

_REGISTERED: dict[str, Detector] = {}


def register_detector(detector: Detector) -> Detector:
    """Register one built-in detector, rejecting duplicate names instead of silently replacing one."""
    if detector.name in _REGISTERED:
        raise ValueError(f"detector already registered: {detector.name}")
    _REGISTERED[detector.name] = detector
    return detector


def registered_detectors() -> tuple[Detector, ...]:
    """The existing per-security detectors in deterministic registration order."""
    return tuple(_REGISTERED.values())
