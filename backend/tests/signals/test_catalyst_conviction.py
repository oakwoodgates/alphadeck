from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import CatalystType, Grade, Kind, Role
from signals import catalyst_conviction

ASOF = date(2026, 6, 5)
SID = uuid4()


def _cat(
    grade="core",
    ctype="contract",
    d=date(2026, 5, 15),
    ref="https://x/ppa",
    label="20-yr PPA",
    horizon_end=None,
):
    return {
        "grade": grade,
        "catalyst_type": ctype,
        "valid_from": d,
        "source": "ratified",
        "source_ref": ref,
        "label": label,
        "horizon_end": horizon_end,
    }


def test_core_catalyst_fires_with_the_default_horizon():
    ev = catalyst_conviction.score([_cat()], SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.role is Role.ENTRY_TRIGGER and ev.kind is Kind.CATALYST
    assert ev.grade is Grade.CORE and ev.type is CatalystType.CONTRACT
    assert (
        ev.alpha_liveness_days == DEFAULT_CONFIG.catalyst_default_horizon_days
    )  # no term -> default
    assert ev.asof == date(2026, 5, 15)  # dated at the catalyst event, not the query asof
    assert ev.provenance[0].source == "ratified" and ev.provenance[0].ref == "https://x/ppa"


def test_liveness_is_the_agreement_term_when_published():
    # a DOE OTA running to 2029 -> liveness = the period of performance, not a flat default
    ev = catalyst_conviction.score(
        [_cat(d=date(2026, 2, 9), horizon_end=date(2029, 7, 1))], SID, ASOF, DEFAULT_CONFIG
    )
    assert ev is not None
    assert ev.alpha_liveness_days == (date(2029, 7, 1) - date(2026, 2, 9)).days


def test_grade_does_not_affect_liveness():
    # THE decoupling: a flip and a core catalyst with the SAME term carry the SAME liveness; only the
    # grade (entry size) differs. (Insider stays grade-coupled — this decoupling is catalyst-only.)
    term = date(2029, 7, 1)
    flip = catalyst_conviction.score(
        [_cat(grade="flip", d=date(2026, 2, 9), horizon_end=term)], SID, ASOF
    )
    core = catalyst_conviction.score(
        [_cat(grade="core", d=date(2026, 2, 9), horizon_end=term)], SID, ASOF
    )
    assert flip.alpha_liveness_days == core.alpha_liveness_days  # liveness decoupled from grade
    assert flip.grade is Grade.FLIP and core.grade is Grade.CORE  # grade still distinguishes them


def test_a_provisional_catalyst_stays_live_for_its_horizon():
    # the OKLO fix: a FLIP catalyst 100d before the query is still live when its horizon is long — it
    # would have decayed under the old flat 30d flip window, missing its later breakout.
    ev = catalyst_conviction.score(
        [_cat(grade="flip", d=ASOF - timedelta(days=100), horizon_end=ASOF + timedelta(days=900))],
        SID,
        ASOF,
        DEFAULT_CONFIG,
    )
    assert ev is not None and ev.grade is Grade.FLIP


def test_catalyst_decays_past_its_horizon():
    ev = catalyst_conviction.score(  # horizon ended in early 2025, well before the query
        [_cat(d=date(2024, 1, 1), horizon_end=date(2025, 1, 1))], SID, ASOF
    )
    assert ev is None


def test_default_horizon_decays_an_old_termless_catalyst():
    old = ASOF - timedelta(days=DEFAULT_CONFIG.catalyst_default_horizon_days + 10)
    assert catalyst_conviction.score([_cat(d=old)], SID, ASOF) is None  # past the default horizon


def test_picks_binding_over_a_more_recent_provisional():
    flip = _cat(grade="flip", d=date(2026, 6, 1), ref="https://x/mou")
    core = _cat(grade="core", d=date(2026, 5, 1), ref="https://x/ppa")
    ev = catalyst_conviction.score([flip, core], SID, ASOF, DEFAULT_CONFIG)
    assert (
        ev.grade is Grade.CORE and ev.provenance[0].ref == "https://x/ppa"
    )  # core beats a newer flip


def test_no_catalyst_no_event():
    assert catalyst_conviction.score([], SID, ASOF) is None
