from __future__ import annotations

import ast
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from signals import registered_detectors
from signals.display import (
    etf_flow,
    insider_flow,
    range52w,
    registered_display_members,
    relative_strength,
    rvol,
    sma,
    trailing_returns,
    vcp,
    volume_regime,
)
from signals.display.base import DisplayBasis, DisplayMember, DisplaySignal
from signals.display.registry import register_display_member

_SID = uuid.uuid4()
_ASOF = date(2026, 7, 1)


def test_registry_contains_exactly_the_members_in_display_order():
    members = registered_display_members()
    assert [m.name for m in members] == [
        "sma_position",
        "trailing_returns",
        "range_52w",
        "volume_regime",
        "rvol",
        "relative_strength",
        "insider_flow_90d",
        "etf_flow",
        "vcp",  # §3.2 base-tightening / VCP — appended LAST (existing members keep their render order)
    ]
    assert [m.compute for m in members] == [
        sma.display,
        trailing_returns.display,
        range52w.display,
        volume_regime.display,
        rvol.display,
        relative_strength.display,
        insider_flow.display,
        etf_flow.display,
        vcp.display,
    ]


def test_registry_rejects_duplicate_member_names():
    duplicate = DisplayMember(name="sma_position", compute=lambda pit, security_id, asof: None)
    with pytest.raises(ValueError, match="already registered"):
        register_display_member(duplicate)


def test_member_rejects_a_signal_stamped_with_another_kind():
    stray = DisplaySignal(
        kind="someone_else", label="x", basis=DisplayBasis(source="fact_price_eod")
    )
    member = DisplayMember(name="expected_name", compute=lambda pit, security_id, asof: stray)
    with pytest.raises(ValueError, match="emitted a signal stamped"):
        member(SimpleNamespace(), _SID, _ASOF)


def test_detector_registry_is_untouched_by_the_display_seam():
    """The two registries never bleed: importing signals.display must leave the call path's detector
    set exactly as the call side pins it (tests/signals/test_registry.py — the one source of the
    expected list; this copy exists to prove the DISPLAY import added nothing)."""
    assert [d.name for d in registered_detectors()] == [
        "insider_conviction",
        "catalyst_conviction",
        "volume_breakout",
        "dilution_clock",
        "revenue_acceleration",
        "breakout_52w",
        "breakdown_core",
        "breakdown_flip",
        "insider_sell",  # Band 03 S1 — a CALL-path risk detector (appended there, not by display)
        "corporate_catalyst",  # Band 03 S3 — the 8-K item-tape pair (call path, switches OFF)
        "corporate_risk",
        "share_creep",  # Band 03 S4 — the share-count-creep risk detector (call path, switch OFF)
        "activist_stake",  # Band 03 S5 — the 13D activist-stake conviction detector (call path, switch OFF)
    ]


# The call machinery, DB drivers, and writers a display module must have NO import path to — the
# structural bound (the explain-seam idiom): a display member physically cannot emit a SignalEvent,
# read a CallConfig dial, open a connection, or persist anything.
_FORBIDDEN_IMPORTS = (
    "domain.signal",
    "domain.config",
    "signals.base",
    "signals.registry",
    "signals.common",
    "calls",
    "pipeline",
    "repositories",
    "db",
    "psycopg",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_display_package_cannot_touch_the_call_path():
    pkg = Path(sma.__file__).parent
    files = sorted(pkg.glob("*.py"))
    assert files, "display package sources not found"
    for src in files:
        for mod in _imported_modules(src):
            for forbidden in _FORBIDDEN_IMPORTS:
                assert mod != forbidden and not mod.startswith(
                    forbidden + "."
                ), f"{src.name} imports {mod!r} — the display seam is read-only, off the call path"
    # And the payload carries none of SignalEvent's call fields: nothing downstream can mistake a
    # display signal for something that fired.
    assert not set(DisplaySignal.model_fields) & {
        "role",
        "fired",
        "grade",
        "score",
        "alpha_liveness_days",
    }
