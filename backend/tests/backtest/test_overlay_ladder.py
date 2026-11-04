"""``backtest.sweep --overlay-ladder`` — one curve whose points are flat overlays, for dials that move together.

F4's unit and session count are refused one without the other (``CallConfig._bar_window_units``), so the
``--ladder dial=v1,v2`` front door cannot express a point. An overlay ladder is a JSON array of house-format
overlays; these tests pin that every point is validated before any work, that the committed F4
pre-registration files are what the docs say, and that the pass shares ONE baseline across their curves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.config_overlay import OverlayError, apply_overlay, load_overlay_ladder
from backtest.sweep import _ladders_from_args, build_parser, main
from domain.config import DEFAULT_CONFIG, config_hash

_OVERLAYS = Path(__file__).resolve().parents[2] / "backtest" / "overlays"
_F4 = {
    "breakout": ("f4-breakout-sessions.json", [5, 6, 7, 8, 9, 10]),
    "breakout_52w": ("f4-breakout-52w-sessions.json", [28, 30, 32, 34, 36]),
    "laggard": ("f4-laggard-sessions.json", [8, 9, 10, 11, 12, 13]),
}


def _write(tmp_path: Path, payload) -> Path:
    p = tmp_path / "ladder.json"
    p.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "payload, needle",
    [
        ("{not json", "not valid JSON"),
        ({"breakout_alpha_liveness_unit": "calendar"}, "non-empty JSON array"),
        ([], "non-empty JSON array"),
        ([{}], "point 0 must be a non-empty JSON object"),
        (["breakout_alpha_liveness_unit"], "point 0 must be a non-empty JSON object"),
        ([{"breakout_alpha_liveness_unit": "calendar"}, {"not_a_dial": 1}], "point 1"),
        ([{"breakout_alpha_liveness_unit": "sessions"}], "point 0"),  # a lone half
        (
            [
                {"breakout_alpha_liveness_unit": "calendar"},
                {"laggard_alpha_liveness_unit": "calendar"},  # also the production config
            ],
            "points 0 and 1 resolve to the same config",
        ),
    ],
)
def test_a_bad_ladder_is_refused_naming_the_point(tmp_path, payload, needle):
    with pytest.raises(OverlayError, match=needle):
        load_overlay_ladder(_write(tmp_path, payload))


@pytest.mark.parametrize("family", list(_F4))
def test_the_committed_f4_ladders_are_the_pre_registered_ones(family):
    fname, counts = _F4[family]
    points = load_overlay_ladder(_OVERLAYS / fname)
    # point 0 is the calendar baseline and IS the production config, so the sweep marks it `is_baseline`
    assert points[0] == {f"{family}_alpha_liveness_unit": "calendar"}
    assert config_hash(apply_overlay(points[0])) == config_hash(DEFAULT_CONFIG)
    assert points[1:] == [
        {f"{family}_alpha_liveness_unit": "sessions", f"{family}_alpha_liveness_sessions": n}
        for n in counts
    ]


def _args(*extra: str):
    return build_parser().parse_args(
        [
            "--start",
            "2025-09-01",
            "--end",
            "2026-09-14",
            "--hypothesis",
            "H",
            "--decision-rule",
            "D",
        ]
        + list(extra)
    )


def test_three_overlay_ladders_are_three_curves_sharing_one_baseline():
    args = _args(
        *[a for _, (f, _c) in _F4.items() for a in ("--overlay-ladder", str(_OVERLAYS / f))]
    )
    lads = _ladders_from_args(args)
    assert [lad.dial_names for lad in lads] == [
        ["breakout_alpha_liveness_sessions", "breakout_alpha_liveness_unit"],
        ["breakout_52w_alpha_liveness_sessions", "breakout_52w_alpha_liveness_unit"],
        ["laggard_alpha_liveness_sessions", "laggard_alpha_liveness_unit"],
    ]
    assert all(lad.hypothesis == "H" and lad.decision_rule == "D" for lad in lads)
    hashes = [config_hash(apply_overlay(v)) for lad in lads for v in lad.variants]
    # 1 shared baseline + 6 + 5 + 6 session points: the run_pass dedup measures the baseline ONCE
    assert len(set(hashes)) == 18
    assert hashes.count(config_hash(DEFAULT_CONFIG)) == 3


def test_an_overlay_ladder_shares_a_pass_with_a_dial_ladder():
    args = _args(
        "--ladder",
        "breakout_alpha_liveness_days=8,10,12",
        "--overlay-ladder",
        str(_OVERLAYS / _F4["breakout"][0]),
    )
    assert [lad.key for lad in _ladders_from_args(args)] == [
        "breakout_alpha_liveness_days",
        "breakout_alpha_liveness_sessions-breakout_alpha_liveness_unit",
    ]


def test_per_ladder_pre_registration_keys_on_the_first_dial():
    args = _args(
        "--overlay-ladder",
        str(_OVERLAYS / _F4["laggard"][0]),
        "--ladder-hypothesis",
        "laggard_alpha_liveness_sessions=the laggard's own text",
    )
    (lad,) = _ladders_from_args(args)
    assert lad.hypothesis == "the laggard's own text"
    with pytest.raises(OverlayError, match="no --ladder"):
        _ladders_from_args(
            _args(
                "--overlay-ladder",
                str(_OVERLAYS / _F4["laggard"][0]),
                "--ladder-hypothesis",
                "laggard_alpha_liveness_unit=keyed on the wrong dial",
            )
        )


def test_the_cli_refuses_an_overlay_ladder_mixed_with_a_grid(capsys):
    code = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-06-30",
            "--hypothesis",
            "h",
            "--decision-rule",
            "d",
            "--overlay-ladder",
            str(_OVERLAYS / _F4["breakout"][0]),
            "--dial",
            "activist_13d_liveness_days",
            "--values",
            "90,180",
        ]
    )
    assert code == 2
    assert "--overlay-ladder" in capsys.readouterr().err


def test_a_bad_overlay_ladder_fails_before_the_mirror_export(tmp_path, capsys):
    bad = _write(tmp_path, [{"breakout_alpha_liveness_sessions": 7}])  # a count under calendar
    code = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-06-30",
            "--hypothesis",
            "h",
            "--decision-rule",
            "d",
            "--overlay-ladder",
            str(bad),
        ]
    )
    assert code == 2
    assert "point 0" in capsys.readouterr().err
