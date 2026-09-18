from __future__ import annotations

import pytest

from backtest.dials import _PROPERTY_READS, partition, property_body_reads
from domain.config import DEFAULT_CONFIG, CallConfig

# B5b — THE DIAL PARTITION. It is the memoization key: a variant that moves only composition dials can
# reuse a cached event stream. A mis-classified dial does not fail loudly, it serves a STALE cache and the
# run reports it as a measurement -- which is why the partition is derived from the AST rather than typed
# out, and why these tests pin the derivation rather than the answer.


def test_the_partition_covers_every_config_field():
    """Nothing unaccounted. A NEW dial lands in one of the four sets automatically, so adding one can
    never leave the cache key silently incomplete."""
    p = partition()
    assert p.detector | p.assembler | p.both | p.unused == set(CallConfig.model_fields)
    assert not (p.detector & p.assembler)
    assert not (p.detector & p.both) and not (p.assembler & p.both)


def test_the_four_sets_are_disjoint_and_sum_to_the_model():
    p = partition()
    total = len(p.detector) + len(p.assembler) + len(p.both) + len(p.unused)
    assert total == len(CallConfig.model_fields)


def test_the_event_layer_key_is_detector_plus_both():
    """BOTH is included deliberately: over-invalidating a cache costs a re-run, under-invalidating serves
    a stale event stream and reports it as a result. Only one of those is recoverable."""
    p = partition()
    assert p.event_layer == p.detector | p.both
    assert not (p.event_layer & p.assembler)


# --- the indirection that makes a naive scan WRONG, not merely incomplete ---------------------------


def test_conviction_kinds_is_BOTH_because_of_the_property():
    """`signals/theme_conviction.py` reads `cfg.own_conviction_kinds`, a property over `conviction_kinds`.
    A scan for `.conviction_kinds` under signals/ finds NOTHING, so the naive answer files it as
    assembler-only -- and a cache keyed on that would reuse an event stream the dial had changed."""
    p = partition()
    assert "conviction_kinds" in p.both
    assert "conviction_kinds" in p.event_layer


def test_the_declared_property_reads_match_the_property_itself():
    """`_PROPERTY_READS` is the one hand-maintained thing here, so it is checked against the property's
    own SOURCE: if `own_conviction_kinds` ever stops reading `conviction_kinds`, this fails instead of the
    partition drifting."""
    for name, declared in _PROPERTY_READS.items():
        assert property_body_reads(name) == declared, name


def test_every_callconfig_property_is_declared():
    """A NEW property would be invisible to the field-level scan, so it has to be declared or fail here."""
    # CallConfig's OWN namespace, not dir(): BaseModel contributes `model_extra` / `model_fields_set`,
    # which are Pydantic machinery rather than dials and have nothing to declare.
    props = {n for n, v in vars(CallConfig).items() if isinstance(v, property)}
    assert props <= set(_PROPERTY_READS), props - set(_PROPERTY_READS)


# --- reconciliation with HD's observation-pinned classification -------------------------------------


@pytest.mark.parametrize(
    "dial,expected",
    [
        ("revenue_accel_grade", "detector"),
        ("breakdown_dearm_scope", "detector"),
        ("revenue_accel_key", "assembler"),
        ("computed_conviction_requires_core_confirmation", "assembler"),
    ],
)
def test_the_mechanical_rule_agrees_with_HD_s_observed_classification(dial, expected):
    """HD classified its four dials by OBSERVATION (does the event stream move, or only the card). This
    derives the same four from the source. The two must agree -- that is the reconciliation, and it is
    what makes either one trustworthy. Neither is duplicated: HD's test still observes, this one reads.
    """
    p = partition()
    got = "detector" if dial in p.detector else "assembler" if dial in p.assembler else "other"
    assert got == expected


# --- the shape of the answer, pinned so a change is review-visible ----------------------------------


def test_the_both_set_is_exactly_the_three_known_cases():
    """Small and stable, and each one is there for a stated reason: two kind-membership sets read by the
    theme broadcast as well as the assembler, and a severity threshold whose signals-side reads pick a
    copy string while its calls-side reads are the actual veto."""
    assert partition().both == {"conviction_kinds", "confirmation_kinds", "risk_block_severity"}


def test_the_unused_set_is_workbench_and_ingest_dials():
    """Not dead config -- dials read OUTSIDE the call path entirely (Workbench scoring pips, the DOE
    ingest grade rule) plus one documented convention string. Pinned so a dial falling out of the call
    path is a review-visible change rather than a silent one."""
    assert partition().unused == {
        "purity_pip_pct",
        "runway_pip_months",
        "catalyst_pip_multi_count",
        "catalyst_pip_dense_count",
        "dilution_pip_pct",
        "doe_core_min_obligation_usd",
        "cash_runway_basis",
    }


def test_a_representative_detector_dial_lands_in_detector():
    p = partition()
    for dial in (
        "insider_core_alpha_liveness_days",
        "breakout_volume_mult",
        "revenue_accel_min_yoy",
        "breakdown_dearm_enabled",
        # A2 — a cap on a published catalyst term. It changes what FIRES (a catalyst can age out under
        # it), so a cached event stream has to be keyed on it; `tests/signals/test_catalyst_conviction`
        # proves the behavior, this pins the classification.
        "catalyst_max_horizon_days",
    ):
        assert dial in p.detector, dial


def test_a_representative_assembler_dial_lands_in_assembler():
    p = partition()
    for dial in (
        "arming_requires_confirmation",
        "warming_min_entry_triggers",
        "single_detector_cap",
    ):
        assert dial in p.assembler, dial


def test_the_partition_is_stable_across_calls():
    """Cached, and the cache must not be the thing that makes it look stable."""
    a, b = partition(), partition()
    assert a == b and a.detector == b.detector


def test_the_config_is_not_mutated_by_deriving_the_partition():
    before = DEFAULT_CONFIG.model_dump_json()
    partition()
    assert DEFAULT_CONFIG.model_dump_json() == before
