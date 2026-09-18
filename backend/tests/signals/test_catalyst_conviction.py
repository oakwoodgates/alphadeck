from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

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


# --- A2: the CAP on a published term --------------------------------------------------------------------
#
# `catalyst_max_horizon_days` is DORMANT (None = no cap = today, byte-identically), on the
# `insider_10b5_1_buy_weight` precedent: land the dial at today's value, measure it on the lab, flip only
# on a pre-registered pass plus the operator's sign-off.
#
# It exists because the dial that looked like the catalyst's horizon lever is not one. Phase 1 swept
# `catalyst_default_horizon_days` across 90/180/365/540/730 over a year of tape and every point was
# BYTE-IDENTICAL -- pooled, and on its own `key1_source=ratified_catalyst` slice too (46 episodes, the
# same -38.465% median at all five settings). That dial is a FALLBACK, read only when a fact publishes no
# `horizon_end`, and every catalyst fact on the tape publishes one. The real instance is below.

_DOE_EVENT = date(2026, 2, 9)  # OKLO's DOE Reactor Pilot OTA (DENE0009589), as seeded
_DOE_TERM = date(2029, 7, 1)  # its period of performance
_DOE_DAYS = (_DOE_TERM - _DOE_EVENT).days  # 1,238 days of signal validity off ONE event


def test_the_real_DOE_fact_buys_1238_days_and_the_cap_is_what_can_shorten_it():
    """The instance, stated as a number: a single 2026 OTA keeps a catalyst live into 2029 under today's
    config. Whether an edge survives that long is exactly the question a timing platform should be able
    to ask, and before this dial nothing could ask it."""
    assert _DOE_DAYS == 1238
    uncapped = catalyst_conviction.liveness(
        _cat(d=_DOE_EVENT, horizon_end=_DOE_TERM), DEFAULT_CONFIG
    )
    assert uncapped == _DOE_DAYS  # None = no cap = today

    capped = catalyst_conviction.liveness(
        _cat(d=_DOE_EVENT, horizon_end=_DOE_TERM),
        DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": 365}),
    )
    assert capped == 365  # the cap BINDS


def test_the_cap_changes_what_FIRES_not_only_what_the_card_says():
    """A detector dial, proved as one: 200 days after the OTA the catalyst is still live uncapped and is
    GONE at a 90-day cap. The event stream moves, which is why the dial partition must key a cached
    stream on it."""
    asof = _DOE_EVENT + timedelta(days=200)
    fact = [_cat(grade="flip", d=_DOE_EVENT, horizon_end=_DOE_TERM)]

    live = catalyst_conviction.score(fact, SID, asof, DEFAULT_CONFIG)
    assert live is not None and live.alpha_liveness_days == _DOE_DAYS

    capped = catalyst_conviction.score(
        fact, SID, asof, DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": 90})
    )
    assert capped is None  # aged out under the cap -- nothing fires, nothing to compose


def test_a_cap_LONGER_than_the_term_does_nothing():
    """A cap is a ceiling, never a floor: it can only ever shorten a published term, so a wide setting
    reads exactly as no cap at all."""
    fact = _cat(d=_DOE_EVENT, horizon_end=_DOE_TERM)
    wide = DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": 2000})
    assert catalyst_conviction.liveness(fact, wide) == _DOE_DAYS


def test_the_cap_does_NOT_touch_the_no_term_fallback():
    """THE ASYMMETRY, pinned so it reads as deliberate rather than as an oversight. The fallback answers
    "we do not know this agreement's term"; the cap answers "we do not believe the edge outlives N
    days". They are different statements about different facts and each has its own dial — capping both
    with one number would conflate them and make a sweep of either uninterpretable."""
    cfg = DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": 90})
    assert catalyst_conviction.liveness(_cat(horizon_end=None), cfg) == (
        cfg.catalyst_default_horizon_days
    )


def test_a_nonsense_cap_floors_at_one_day_rather_than_silencing_the_detector():
    """A zero or negative cap would otherwise produce a liveness no live-window can contain, and the
    catalyst would read as never having fired instead of the cap reading as nonsense."""
    for bad in (0, -5):
        cfg = DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": bad})
        assert catalyst_conviction.liveness(_cat(d=_DOE_EVENT, horizon_end=_DOE_TERM), cfg) == 1


# --- DORMANT: None IS today ---------------------------------------------------------------------------------


def test_the_default_is_NO_CAP():
    """Flipping this default is a LIVE behavior change and must fail here rather than ship quietly inside
    a backtest slice."""
    assert DEFAULT_CONFIG.catalyst_max_horizon_days is None


def test_spelling_the_default_out_produces_a_byte_identical_EVENT():
    """The dormancy claim at the layer this dial acts on. (The broader claim is carried by the rest of
    the suite continuing to pass unmodified: this slice edits no golden and no existing assertion.)
    """
    explicit = DEFAULT_CONFIG.model_copy(update={"catalyst_max_horizon_days": None})
    facts = [_cat(d=_DOE_EVENT, horizon_end=_DOE_TERM), _cat()]
    a = catalyst_conviction.score(facts, SID, ASOF, DEFAULT_CONFIG)
    b = catalyst_conviction.score(facts, SID, ASOF, explicit)
    assert a is not None and a.model_dump_json() == b.model_dump_json()


def test_the_no_cap_point_IS_the_production_config_not_a_lookalike():
    """What makes the ladder's `null` point a real baseline rather than a fifth variant: it resolves to
    the production config itself, same fingerprint, so a sweep measures it ONCE and every other point is
    a delta against today."""
    from backtest.config_overlay import apply_overlay
    from domain.config import config_hash

    assert apply_overlay({"catalyst_max_horizon_days": None}) == DEFAULT_CONFIG
    assert config_hash(apply_overlay({"catalyst_max_horizon_days": None})) == config_hash(
        DEFAULT_CONFIG
    )
    assert config_hash(apply_overlay({"catalyst_max_horizon_days": 365})) != config_hash(
        DEFAULT_CONFIG
    )


def test_the_ladder_spelling_of_no_cap_is_JSON_null_and_a_typo_is_refused():
    """The launch line has to be exact. `--ladder catalyst_max_horizon_days=90,180,365,730,null` parses
    to a real `None`; the Python spelling `None` parses as the STRING "None" and is then refused by the
    overlay rather than silently becoming a variant."""
    from backtest.config_overlay import OverlayError, apply_overlay
    from backtest.sweep import parse_values

    assert parse_values("90,180,365,730,null") == [90, 180, 365, 730, None]
    assert parse_values("730,None") == [730, "None"]  # NOT a None
    with pytest.raises(OverlayError):
        apply_overlay({"catalyst_max_horizon_days": "None"})


def test_the_cap_is_a_DETECTOR_dial_so_a_cached_event_stream_is_keyed_on_it():
    """Derived from the source, not declared: the dial is read under `signals/`, so the partition files
    it as DETECTOR and the backtest's event cache must invalidate on it. Serving a cached stream across
    this dial would report the uncapped tape as the capped one's result."""
    from backtest.dials import partition

    p = partition()
    assert "catalyst_max_horizon_days" in p.detector
    assert "catalyst_max_horizon_days" in p.event_layer


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
