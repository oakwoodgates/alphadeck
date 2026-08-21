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


# --- Item 3: the risk-haircut per-kind collapse -----------------------------------------------------

# Two INDEPENDENT entry kinds so the base confidence is uncapped (0.94) — isolates the haircut delta.
_CONV = _entry(Kind.INSIDER, 0.8)
_CONF = _entry(Kind.TECHNICAL_BREAKOUT, 0.7)


def test_two_dilution_lenses_cost_one_haircut():
    """Item 3: two Kind.DILUTION_RISK events (dilution_clock's POTENTIAL overhang + share_creep's
    REALIZED issuance — two lenses on ONE phenomenon) cost ONE haircut, at the STRONGER score, matching
    corporate_risk's within-detector collapse. The weaker same-kind lens adds no extra haircut."""
    potential = _risk(Kind.DILUTION_RISK, 0.5, detector="dilution_clock")
    realized = _risk(Kind.DILUTION_RISK, 0.3, detector="share_creep")
    both = confidence([_CONV, _CONF], [potential, realized], DEFAULT_CONFIG)
    one = confidence([_CONV, _CONF], [potential], DEFAULT_CONFIG)  # just the stronger lens
    assert both == one  # the weaker same-kind lens adds NO extra haircut
    # the single haircut is the STRONGER lens's score (0.5), never the sum (0.5 + 0.3)
    clean = confidence([_CONV, _CONF], [], DEFAULT_CONFIG)
    assert round(clean - both, 4) == round(DEFAULT_CONFIG.risk_penalty_per_signal * 0.5, 4)


def test_distinct_risk_kinds_each_cost_a_haircut():
    """Item 3: only a CORRELATED family collapses — a dilution risk + a corporate risk are different
    kinds (genuinely different phenomena) and still each cost their own haircut."""
    dilution = _risk(Kind.DILUTION_RISK, 0.5, detector="dilution_clock")
    corporate = _risk(Kind.CORPORATE_RISK, 0.5, detector="corporate_risk")
    clean = confidence([_CONV, _CONF], [], DEFAULT_CONFIG)
    two_kinds = confidence([_CONV, _CONF], [dilution, corporate], DEFAULT_CONFIG)
    expected_cut = round(
        DEFAULT_CONFIG.risk_penalty_per_signal * 0.5 * 2, 4
    )  # two haircuts, 0.5 each
    assert round(clean - two_kinds, 4) == expected_cut
