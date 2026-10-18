"""Q12 — the de-arm copy names the breakdown that actually de-armed the arm.

The old copy said "De-armed on a structural break … Re-arms only on a fresh confirmation". MEASURED on the
record (audit A1): all 30 recorded de-arm rows were FAST breakdowns (28 purely, 2 mixed with a structural
break), and 46 of 219 flip de-arms re-armed on the same breakout — both clauses were false. The
operator-approved copy (2026-10-18) is chosen by WHICH breakdowns de-armed the arm: the ``dearm_grade`` of
every de-arming breakdown, which ``_is_dearming`` only lets through when it matches its member's entry
grade — so it is the set of entry grades the broken-down members carried. {FLIP} -> the fast-breakdown
copy, {CORE} -> the structural-break copy, both -> the mixed copy.

REAL SHAPES. The flip, mixed and rule cases are recorded PROD de-arm rows (read-only, 2026-10-18): the
de-armed members' entry triggers and breakdown risks with the card's own kinds, grades and fire dates; the
liveness windows are the live dials. Each test first reproduces the RECORDED state / verdict / entry grade /
"Risk must clear" lines, then asserts the copy. The record's sub-veto risks (a capped insider-sell, an 8-K
3.02, a share-creep / dilution read — none in the recorded ``missing``, so none blocked) are left out: they
cannot reach the de-arm branch. The record holds NO pure structural-break de-arm, so the core copy is
proven by a CONTRACT test on hand-built events, and says so.
"""

from __future__ import annotations

import uuid
from datetime import date

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role, State, Verdict
from domain.signal import Provenance, SignalEvent
from domain.thesis import BasketMember, Thesis
from signals.breakdown import DETECTOR_CORE_NAME, DETECTOR_FLIP_NAME
from signals.revenue_acceleration import DETECTOR_NAME as REVENUE_ACCEL_DETECTOR
from tests.calls.factories import ASOF, breakdown_event, breakout_event, insider_event, make_thesis

_CFG = DEFAULT_CONFIG

# The approved strings, VERBATIM (em dashes included) — a wording change must be a deliberate edit here.
FLIP_COPY = (
    "De-armed on a fast breakdown — the close fell back below the 8-day breakout base, so the flip "
    "entry signal is no longer valid (a signal-validity event, not a sell). It re-arms if price "
    "reclaims that base while the breakout is still live, or on a fresh breakout."
)
CORE_COPY = (
    "De-armed on a structural break — the close fell below the 200-day base, so the core entry signal "
    "is no longer valid (a signal-validity event, not a sell). It re-arms if price reclaims the 200-day "
    "base while the entry triggers are live, or on a fresh confirmation."
)
MIXED_COPY = (
    "De-armed on a breakdown — armed names gave back their bases (see the counter-case); a "
    "signal-validity event, not a sell."
)

_LIVENESS = {
    ("insider", Grade.CORE): _CFG.insider_core_alpha_liveness_days,
    ("insider", Grade.FLIP): _CFG.insider_flip_alpha_liveness_days,
    ("catalyst", Grade.CORE): _CFG.revenue_accel_alpha_liveness_days,
    ("breakout", Grade.CORE): _CFG.breakout_alpha_liveness_days,
    ("breakout", Grade.FLIP): _CFG.breakout_alpha_liveness_days,
}
_KIND = {"insider": Kind.INSIDER, "catalyst": Kind.CATALYST, "breakout": Kind.TECHNICAL_BREAKOUT}
_DETECTOR = {
    "insider": "insider_conviction",
    "catalyst": REVENUE_ACCEL_DETECTOR,  # every recorded catalyst below is the revenue re-accel screen
    "breakout": "volume_breakout",
}


def _sid(ticker: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"q12:{ticker}")


def _thesis(*tickers: str) -> Thesis:
    return Thesis(
        id=uuid.uuid5(uuid.NAMESPACE_URL, "q12:" + ",".join(tickers)),
        name="Q12 real-shape thesis",
        narrative="Reconstructed from the recorded call-of-record.",
        basket=[BasketMember(ticker=t, role="member", security_id=_sid(t)) for t in tickers],
    )


def _entry(ticker: str, what: str, grade: Grade, fired: date) -> SignalEvent:
    return SignalEvent(
        detector=_DETECTOR[what],
        security_id=_sid(ticker),
        role=Role.ENTRY_TRIGGER,
        kind=_KIND[what],
        grade=grade,
        score=0.6,  # never read on a de-armed card (no setup strength, no ranking of a de-armed name)
        fired=True,
        label=f"{what} ({ticker})",
        alpha_liveness_days=_LIVENESS[(what, grade)],
        provenance=[Provenance(source="record", ref=f"{ticker}:{what}:{fired.isoformat()}")],
        asof=fired,
    )


def _breakdown(ticker: str, dearm_grade: Grade, fired: date, label: str) -> SignalEvent:
    """A breakdown RISK exactly as the detector emits it: score = breakdown_severity, the recorded label."""
    return SignalEvent(
        detector=DETECTOR_CORE_NAME if dearm_grade is Grade.CORE else DETECTOR_FLIP_NAME,
        security_id=_sid(ticker),
        role=Role.RISK_SIGNAL,
        kind=Kind.BREAKDOWN,
        dearm_grade=dearm_grade,
        score=_CFG.breakdown_severity,
        fired=True,
        label=label,
        alpha_liveness_days=None,
        provenance=[Provenance(source="price", ref=f"price:{ticker}:{fired.isoformat()}")],
        asof=fired,
    )


def _blocks(card) -> list[str]:
    return sorted(m for m in card.missing if m.startswith("Risk must clear before arming: "))


# --- FLIP: HIMS — insider conviction (system), recorded 2026-09-23 (cron) --------------------------------

_HIMS_FAST = (
    "Fast breakdown: close 28.39 fell back below the 8-day breakout base 29.35 — the flip entry "
    "signal is no longer valid (a de-arm, not a sell)"
)
_HIMS = [
    _entry("HIMS", "insider", Grade.CORE, date(2026, 5, 26)),
    _entry("HIMS", "catalyst", Grade.CORE, date(2026, 8, 10)),
    _entry("HIMS", "breakout", Grade.FLIP, date(2026, 9, 22)),
    _breakdown("HIMS", Grade.FLIP, date(2026, 9, 23), _HIMS_FAST),
]


def test_real_flip_dearm_reads_the_fast_breakdown_copy():
    card = assemble_call(_thesis("HIMS"), _HIMS, date(2026, 9, 23), _CFG)
    # the recorded row: warming / not_yet, conviction core, confirmation flip, entry flip, one block
    assert card.state is State.WARMING and card.verdict is Verdict.NOT_YET
    assert card.conviction_grade is Grade.CORE and card.confirmation_grade is Grade.FLIP
    assert card.entry_grade is Grade.FLIP
    assert _blocks(card) == [f"Risk must clear before arming: {_HIMS_FAST}"]
    assert card.expression == FLIP_COPY


# --- MIXED: Nuclear Buildout, recorded 2026-09-01 (3 fast + 1 structural de-arm) --------------------------

_UUUU_FAST = (
    "Fast breakdown: close 14.22 fell back below the 8-day breakout base 15.14 — the flip entry "
    "signal is no longer valid (a de-arm, not a sell)"
)
_URG_FAST = (
    "Fast breakdown: close 1.34 fell back below the 8-day breakout base 1.52 — the flip entry signal "
    "is no longer valid (a de-arm, not a sell)"
)
_EU_FAST = (
    "Fast breakdown: close 1.19 fell back below the 8-day breakout base 1.38 — the flip entry signal "
    "is no longer valid (a de-arm, not a sell)"
)
_WSTRF_CORE = (
    "Structural break: close 0.48 closed below the 200-day base 0.48 — the core hold's entry signal "
    "is no longer valid (a de-arm, not a sell)"
)
_NUKE_FLIP_MEMBERS = [
    # UUUU — flip entry (momentum-only breakout); its old 06-03 structural break does NOT match its grade
    _entry("UUUU", "catalyst", Grade.CORE, date(2026, 5, 6)),
    _entry("UUUU", "insider", Grade.CORE, date(2026, 7, 8)),
    _entry("UUUU", "breakout", Grade.FLIP, date(2026, 8, 25)),
    _breakdown("UUUU", Grade.FLIP, date(2026, 9, 1), _UUUU_FAST),
    _breakdown("UUUU", Grade.CORE, date(2026, 6, 3), "Structural break: close 14.22 (UUUU)"),
    # URG — flip entry; its 08-28 structural break does NOT match its grade
    _entry("URG", "catalyst", Grade.CORE, date(2026, 3, 10)),
    _entry("URG", "insider", Grade.FLIP, date(2026, 8, 24)),
    _entry("URG", "breakout", Grade.FLIP, date(2026, 8, 27)),
    _breakdown("URG", Grade.FLIP, date(2026, 9, 1), _URG_FAST),
    _breakdown("URG", Grade.CORE, date(2026, 8, 28), "Structural break: close 1.34 (URG)"),
    # EU — flip entry
    _entry("EU", "insider", Grade.CORE, date(2026, 6, 12)),
    _entry("EU", "breakout", Grade.FLIP, date(2026, 8, 27)),
    _breakdown("EU", Grade.FLIP, date(2026, 9, 1), _EU_FAST),
]
_NUKE_WSTRF = [
    # WSTRF — CORE entry (volume-backed breakout); its fast breakdown does NOT match its grade
    _entry("WSTRF", "catalyst", Grade.CORE, date(2026, 8, 14)),
    _entry("WSTRF", "breakout", Grade.CORE, date(2026, 8, 27)),
    _breakdown("WSTRF", Grade.CORE, date(2026, 9, 1), _WSTRF_CORE),
    _breakdown("WSTRF", Grade.FLIP, date(2026, 9, 1), "Fast breakdown: close 0.48 (WSTRF)"),
]


def test_real_mixed_dearm_reads_the_mixed_copy():
    card = assemble_call(
        _thesis("UUUU", "URG", "EU", "WSTRF"),
        _NUKE_FLIP_MEMBERS + _NUKE_WSTRF,
        date(2026, 9, 1),
        _CFG,
    )
    # the recorded row: warming / not_yet, nothing armed, card-level entry grade core, four blocks
    assert card.state is State.WARMING and card.verdict is Verdict.NOT_YET
    assert card.armed_members == [] and card.entry_grade is Grade.CORE
    assert _blocks(card) == sorted(
        f"Risk must clear before arming: {x}"
        for x in (_UUUU_FAST, _URG_FAST, _EU_FAST, _WSTRF_CORE)
    )
    assert card.expression == MIXED_COPY


def test_only_breakdowns_that_actually_dearm_pick_the_copy():
    """The same recorded night WITHOUT WSTRF: UUUU and URG still carry STRUCTURAL breaks on their tape,
    but those don't match their flip entries, so they de-arm nothing — the copy is the flip one."""
    card = assemble_call(_thesis("UUUU", "URG", "EU"), _NUKE_FLIP_MEMBERS, date(2026, 9, 1), _CFG)
    assert card.state is State.WARMING
    assert any(r.label.startswith("Structural break") for r in card.risk_signals)  # still surfaced
    assert card.expression == FLIP_COPY


# --- THE RULE: Nuclear Buildout, recorded 2026-09-23 — a fast de-arm on a card graded CORE ----------------

_FCEL_FAST = (
    "Fast breakdown: close 17.18 fell back below the 8-day breakout base 17.76 — the flip entry "
    "signal is no longer valid (a de-arm, not a sell)"
)


def test_the_cards_own_entry_grade_cannot_pick_the_copy():
    """A de-armed card grades across the whole live basket, so its entry_grade can say CORE while the
    only de-arm was a FAST breakdown — 16 of the 28 pure fast-breakdown rows on the record did. Here:
    FCEL (flip entry) broke down; J (a core conviction, no confirmation) and SAIH (a core breakout, no
    conviction) are real co-members on that night that lift the card-level grade to core."""
    events = [
        _entry("FCEL", "insider", Grade.FLIP, date(2026, 9, 14)),
        _entry("FCEL", "breakout", Grade.FLIP, date(2026, 9, 18)),
        _breakdown("FCEL", Grade.FLIP, date(2026, 9, 23), _FCEL_FAST),
        _entry("J", "insider", Grade.CORE, date(2026, 5, 15)),
        _entry("SAIH", "breakout", Grade.CORE, date(2026, 9, 16)),
        _entry("SAIH", "breakout", Grade.FLIP, date(2026, 9, 23)),
    ]
    card = assemble_call(_thesis("FCEL", "J", "SAIH"), events, date(2026, 9, 23), _CFG)
    assert card.state is State.WARMING and card.verdict is Verdict.NOT_YET  # as recorded
    assert card.entry_grade is Grade.CORE  # as recorded — the basket-wide grade
    assert _blocks(card) == [f"Risk must clear before arming: {_FCEL_FAST}"]  # as recorded
    assert (
        card.expression == FLIP_COPY
    )  # the breakdown that de-armed it decides, not the card grade


# --- CORE: a CONTRACT test — the record holds no pure structural-break de-arm ----------------------------


def test_core_dearm_reads_the_structural_break_copy_contract():
    """No recorded de-arm was a pure structural break (MEASURED: 28 fast, 2 mixed, 0 core), so this is
    a contract test on hand-built events: a core hold (core conviction + volume-backed breakout) that a
    post-dating 200-day break de-arms reads the structural-break copy."""
    cfg = _CFG.model_copy(update={"breakdown_dearm_enabled": True})
    card = assemble_call(
        make_thesis(),
        [
            insider_event(),
            breakout_event(grade=Grade.CORE),
            breakdown_event(dearm_grade=Grade.CORE),
        ],
        ASOF,
        cfg,
    )
    assert card.state is State.WARMING and card.armed_members == []
    assert card.expression == CORE_COPY


def test_a_grade_blind_block_keeps_the_withheld_copy():
    """Not a de-arm (dilution has no dearm_grade): the risk/timing withhold copy is untouched by Q12."""
    from tests.calls.factories import dilution_event

    card = assemble_call(
        make_thesis(), [insider_event(), breakout_event(), dilution_event()], ASOF, _CFG
    )
    assert card.expression.startswith("Entry withheld on risk/timing")
