from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import CatalystType, Grade, Kind, Role
from signals import catalyst_conviction

ASOF = date(2026, 6, 5)
SID = uuid4()


def _cat(
    grade="core", ctype="contract", d=date(2026, 5, 15), ref="https://x/ppa", label="20-yr PPA"
):
    return {
        "grade": grade,
        "catalyst_type": ctype,
        "valid_from": d,
        "source": "ratified",
        "source_ref": ref,
        "label": label,
    }


def test_core_catalyst_fires_conviction():
    ev = catalyst_conviction.score([_cat()], SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.role is Role.ENTRY_TRIGGER and ev.kind is Kind.CATALYST
    assert ev.grade is Grade.CORE and ev.type is CatalystType.CONTRACT
    assert ev.alpha_liveness_days == DEFAULT_CONFIG.catalyst_core_alpha_liveness_days
    assert ev.asof == date(2026, 5, 15)  # dated at the catalyst event, not the query asof
    assert ev.provenance[0].source == "ratified" and ev.provenance[0].ref == "https://x/ppa"


def test_flip_catalyst_is_short_lived():
    ev = catalyst_conviction.score([_cat(grade="flip", ctype="promoter_attention")], SID, ASOF)
    assert ev is not None and ev.grade is Grade.FLIP
    assert ev.alpha_liveness_days == DEFAULT_CONFIG.catalyst_flip_alpha_liveness_days


def test_core_catalyst_lives_for_a_year_flip_does_not():
    old = ASOF - timedelta(days=300)  # 300d before the query asof
    assert catalyst_conviction.score([_cat(d=old)], SID, ASOF) is not None  # core 365 -> still live
    assert (
        catalyst_conviction.score([_cat(grade="flip", d=old)], SID, ASOF) is None
    )  # flip 30 -> gone


def test_decayed_core_catalyst_drops():
    old = ASOF - timedelta(days=400)  # past the 365d core window
    assert catalyst_conviction.score([_cat(d=old)], SID, ASOF) is None


def test_picks_binding_over_a_more_recent_provisional():
    flip = _cat(grade="flip", d=date(2026, 6, 1), ref="https://x/mou")
    core = _cat(grade="core", d=date(2026, 5, 1), ref="https://x/ppa")
    ev = catalyst_conviction.score([flip, core], SID, ASOF, DEFAULT_CONFIG)
    assert (
        ev.grade is Grade.CORE and ev.provenance[0].ref == "https://x/ppa"
    )  # core beats a newer flip


def test_no_catalyst_no_event():
    assert catalyst_conviction.score([], SID, ASOF) is None
