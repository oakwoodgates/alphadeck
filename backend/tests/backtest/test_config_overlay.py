from __future__ import annotations

import json

import pytest

from backtest.config_overlay import OverlayError, apply_overlay, load_overlay, overlay_diff
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Kind

# Config as DATA. These tests are about the two ways an overlay can quietly lie: a dial that does not
# exist (a typo that changes nothing and reads as "that dial is inert") and a value the model would have
# rejected had the config been CONSTRUCTED rather than copied.


def test_overlay_applies_the_dials_it_names():
    cfg = apply_overlay({"insider_core_alpha_liveness_days": 90})
    assert cfg.insider_core_alpha_liveness_days == 90
    assert DEFAULT_CONFIG.insider_core_alpha_liveness_days != 90  # the base is untouched


def test_overlay_leaves_every_other_dial_alone():
    cfg = apply_overlay({"insider_core_alpha_liveness_days": 90})
    changed = {f for f in CallConfig.model_fields if getattr(cfg, f) != getattr(DEFAULT_CONFIG, f)}
    assert changed == {"insider_core_alpha_liveness_days"}


def test_overlay_rejects_an_unknown_dial_and_names_it():
    """A typo must fail LOUDLY. ``model_copy(update=...)`` would happily set the attribute, the run would
    complete, and the result would be read as evidence that the dial does nothing."""
    with pytest.raises(OverlayError, match="insider_core_min_distnct"):
        apply_overlay({"insider_core_min_distnct": 3})


def test_overlay_revalidates_the_result():
    """``model_copy(update=...)`` BYPASSES validation — it is a copy, not a construction. Constructing
    through ``model_validate`` instead is what makes an out-of-range dial fail before the run.

    Uses ``insider_10b5_1_buy_weight`` because, MEASURED, it is the ONLY one of the 83 dials that declares
    a bound (see ``test_only_one_dial_declares_a_bound`` below — the honest reach of this guard)."""
    with pytest.raises(OverlayError):
        apply_overlay({"insider_10b5_1_buy_weight": 5.0})  # declared ge=0, le=1


def test_overlay_coerces_a_json_value_to_its_declared_type():
    """The other half of constructing rather than copying: a JSON list must arrive as the declared
    ``frozenset[Kind]``, not as a list sitting where a frozenset is supposed to be. A copy would install
    the list verbatim and the detectors would do set algebra against it."""
    cfg = apply_overlay({"confirmation_kinds": ["technical_breakout"]})
    assert isinstance(cfg.confirmation_kinds, frozenset)
    # ``Kind`` is a StrEnum, so its members ARE strs — ``isinstance(k, str)`` proves nothing. Membership in
    # the enum is the real discriminator: a raw "technical_breakout" installed by a copy would fail it.
    assert all(isinstance(k, Kind) for k in cfg.confirmation_kinds)


def test_only_one_dial_declares_a_bound():
    """A deliberately uncomfortable test. Re-validation sounds like a strong guard; on this model it is
    mostly type coercion, because only ONE dial declares a value constraint. Pinned so the limitation is
    visible in the suite rather than assumed away — and so that ADDING a bound to a dial (which is the
    right place for one, since the live path benefits too) is a review-visible change here."""
    bounded = {n for n, f in CallConfig.model_fields.items() if f.metadata}
    assert bounded == {"insider_10b5_1_buy_weight"}


def test_overlay_accepts_a_set_valued_dial():
    """The frozenset dials (``conviction_kinds`` / ``confirmation_kinds``) must be settable from JSON,
    since they are the shape a kind-membership hypothesis moves."""
    cfg = apply_overlay({"confirmation_kinds": ["technical_breakout"]})
    assert {k.value for k in cfg.confirmation_kinds} == {"technical_breakout"}


def test_load_overlay_reads_a_file(tmp_path):
    p = tmp_path / "h5.json"
    p.write_text(json.dumps({"revenue_accel_alpha_liveness_days": 90}), encoding="utf-8")
    assert load_overlay(p).revenue_accel_alpha_liveness_days == 90


def test_load_overlay_rejects_non_json(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(OverlayError, match="not valid JSON"):
        load_overlay(p)


def test_load_overlay_rejects_a_non_object(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(OverlayError, match="must be a JSON object"):
        load_overlay(p)


def test_diff_is_empty_for_the_default_config():
    assert overlay_diff(DEFAULT_CONFIG) == {}


@pytest.mark.parametrize(
    "dials",
    [
        {"insider_core_alpha_liveness_days": 90},
        {"insider_flip_alpha_liveness_days": 30, "catalyst_default_horizon_days": 120},
        {
            "revenue_accel_alpha_liveness_days": 45,
            "activist_13d_liveness_days": 60,
            "breakdown_dearm_enabled": False,
            "warming_min_entry_triggers": 2,
            "share_creep_liveness_days": 200,
        },
    ],
)
def test_diff_names_exactly_the_dials_that_moved(dials):
    """The diff is the human-readable half of run identity: ``config_hash`` says WHETHER the dials differ,
    this says WHICH. It must name every moved dial and nothing else."""
    cfg = apply_overlay(dials)
    diff = overlay_diff(cfg)
    assert set(diff) == set(dials)
    for name, value in dials.items():
        assert diff[name]["run"] == value
        assert diff[name]["default"] != diff[name]["run"]


def test_diff_is_json_serializable():
    """It is written straight into the manifest, so it must survive ``json.dumps`` without a default=
    escape hatch — which is why both sides go through the canonical walk rather than the raw attribute.
    """
    cfg = apply_overlay({"confirmation_kinds": ["technical_breakout"], "breakout_min_return": 0.15})
    json.dumps(overlay_diff(cfg))  # must not raise


def test_diff_does_not_report_a_set_as_changed_when_it_is_not():
    """A ``frozenset`` dial's ITERATION ORDER is process-dependent. Comparing raw attributes would be a
    coin flip; the canonical walk sorts, so an unchanged set reads as unchanged."""
    same = apply_overlay({"confirmation_kinds": list(DEFAULT_CONFIG.confirmation_kinds)})
    assert overlay_diff(same) == {}
