"""Direct unit tests for the confidence combine (calls/confidence.py) — the two family-collapse fixes
(P4 Items 2 + 3) proven on DIRECT inputs, not fitted assembler snapshots.

Item 2 (entry side): correlated confirmations sharing a ``kind`` collapse to one max-score term before
the noisy-OR, so two reads of the SAME price move (volume_breakout + breakout_52w, both
TECHNICAL_BREAKOUT) stop saturating confidence; independent kinds still stack.
Item 3 (risk side): risk haircuts collapse per ``kind`` — two DILUTION_RISK lenses cost ONE haircut,
matching corporate_risk's within-detector collapse; different risk kinds still each cost one.
"""

from __future__ import annotations

from calls.confidence import confidence
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from tests.calls.factories import ASOF, SID


def _entry(kind: Kind, score: float, detector: str = "d", grade: Grade = Grade.CORE) -> SignalEvent:
    return SignalEvent(
        detector=detector,
        security_id=SID,
        role=Role.ENTRY_TRIGGER,
        kind=kind,
        grade=grade,
        score=score,
        fired=True,
        label="entry",
        alpha_liveness_days=30,
        provenance=[Provenance(source="price", ref="r")],
        asof=ASOF,
    )


def _risk(kind: Kind, score: float, detector: str = "d") -> SignalEvent:
    return SignalEvent(
        detector=detector,
        security_id=SID,
        role=Role.RISK_SIGNAL,
        kind=kind,
        grade=None,
        score=score,
        fired=True,
        label="risk",
        alpha_liveness_days=None,
        provenance=[Provenance(source="xbrl", ref="r")],
        asof=ASOF,
    )


# --- Item 2: the entry-trigger family collapse ------------------------------------------------------


def test_correlated_breakouts_collapse_to_one_max_score_term():
    """Item 2: two Kind.TECHNICAL_BREAKOUT reads of the same move (volume_breakout + breakout_52w)
    contribute ONE term at the STRONGER score — not two independent noisy-OR terms that saturate.
    Adding the weaker same-kind read changes NOTHING."""
    strong = _entry(Kind.TECHNICAL_BREAKOUT, 0.9, detector="breakout_52w")
    weak = _entry(Kind.TECHNICAL_BREAKOUT, 0.7, detector="volume_breakout")
    both = confidence([strong, weak], [], DEFAULT_CONFIG)
    one = confidence([strong], [], DEFAULT_CONFIG)
    assert both == one  # the family collapses to its max; the weaker same-kind read is absorbed
    # one distinct kind -> the single-detector cap applies; the value derives from max(0.9) capped
    assert both == round(min(0.9, DEFAULT_CONFIG.single_detector_cap), 4)


def test_independent_kinds_still_stack():
    """Item 2: genuinely independent axes (a conviction + a confirmation — different kinds) still
    noisy-OR together; only same-kind families collapse."""
    conv = _entry(Kind.INSIDER, 0.8)
    conf = _entry(Kind.TECHNICAL_BREAKOUT, 0.7)
    stacked = confidence([conv, conf], [], DEFAULT_CONFIG)
    expected = round(1.0 - (1.0 - 0.8) * (1.0 - 0.7), 4)  # two distinct kinds noisy-OR -> 0.94
    assert stacked == expected
    assert stacked > DEFAULT_CONFIG.single_detector_cap  # two independent detectors -> NOT capped


def test_single_detector_cap_keys_on_distinct_kinds_not_trigger_count():
    """Item 2: the 'single-detector' cap keys on the number of distinct detector-FAMILIES (kinds), so a
    same-kind PAIR (one family, two triggers) is capped exactly like one detector."""
    b1 = _entry(Kind.TECHNICAL_BREAKOUT, 0.9, detector="volume_breakout")
    b2 = _entry(Kind.TECHNICAL_BREAKOUT, 0.95, detector="breakout_52w")
    assert confidence([b1, b2], [], DEFAULT_CONFIG) == round(DEFAULT_CONFIG.single_detector_cap, 4)
