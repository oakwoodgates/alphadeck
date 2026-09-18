from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backtest import store
from backtest.sweep import Ladder, build_parser, main
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash, short_hash

# S2 — THE ONE-PASS MULTI-LADDER MODE, and --resume.
#
# Six separate sweeps each re-ran their own baseline: 45 redundant runs and five redundant exports, about
# 1.1 h of a 6.5 h pass. A pass runs every distinct config ONCE per window and hands each ladder its own
# curve off the shared results — the production default appears in every ladder, so deduplicating by
# config_hash ACROSS ladders is where the saving comes from, as a consequence of the dedup rather than as
# a special case.
#
# The analysis is the same code as a single sweep's, deliberately: `run_sweep` is now `run_pass` with one
# ladder, so "a one-pass curve equals the sweep it replaces" is nearly true by construction — and pinned
# by a test anyway, because "nearly" is where the interesting bugs live.

_W1 = (date(2026, 1, 1), date(2026, 2, 11))
_W2 = (date(2026, 2, 12), date(2026, 3, 25))
_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_NOW = datetime(2026, 9, 18, 3, 15, 0, tzinfo=timezone.utc)
_DEFAULT = DEFAULT_CONFIG.insider_core_alpha_liveness_days
_DEFAULT_2 = DEFAULT_CONFIG.activist_13d_liveness_days


@pytest.fixture
def pass_fixture(tmp_path, monkeypatch):
    """A stubbed launcher over hand-written run directories.

    What is under test is which JOBS a pass launches and how their results are assembled — neither needs
    the engine that would produce them, and paying for a replay would make these tests too slow to run on
    every commit."""
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    import backtest.sweep as sweep_mod

    launched: list = []

    def write_run(root, run_id: str, rows: list[tuple[str, float]]) -> None:
        d = store.runs_root(root) / run_id
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(
                [{"arm_date": a, "forward_return": r} for a, r in rows],
                schema=pa.schema([("arm_date", pa.string()), ("forward_return", pa.float64())]),
            ),
            d / "outcomes.parquet",
        )

    def fake_launch(jobs, concurrency):
        """Stands in for a window job, and must be FAITHFUL in the two ways `--resume` depends on: it
        writes into the job's OWN root, and it REGISTERS the run the way `execute` does as its last act.
        Resume reads the registry, so a stub that skipped registration would make every resume look like
        a fresh pass and the tests would pass while proving nothing."""
        launched.extend(jobs)
        ids = []
        for job in jobs:
            root = Path(job.root)
            cfg = CallConfig.model_validate_json(job.cfg_json)
            h = config_hash(cfg)
            rid = f"run-{short_hash(h)}-{job.start}"  # unique per (config, window) -- the job's own key
            # a deterministic, config-dependent "result" so the curves are not all identical
            bump = 0.01 * (int(h[:4], 16) % 7)
            write_run(root, rid, [(job.start, 0.05 + bump), (job.end, 0.03 + bump)])
            store.register_run(
                store.RunSummary(
                    run_id=rid,
                    pass_id=job.pass_id,
                    created_at="2026-09-18T03:15:00+00:00",
                    config_short=short_hash(h) or "",
                    config_hash=h,
                    clock="public",
                    known_at_mode="lockstep",
                    window_start=job.start,
                    window_end=job.end,
                ),
                root,
            )
            ids.append(rid)
        return ids

    monkeypatch.setattr(sweep_mod, "_launch", fake_launch)
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    # `sweep_mod.mirror_hash`, NOT `sweep_mod.mf.mirror_hash`: `run_pass` imported the name directly,
    # so patching it on the manifest module would change an attribute nothing on this path reads.
    monkeypatch.setattr(sweep_mod, "mirror_hash", lambda p: "c" * 64)
    return sweep_mod, tmp_path, launched, write_run


def _ladder(dial: str, values: list, h: str = "H5", d: str = "plateau, not argmax") -> Ladder:
    return Ladder([dial], [{dial: v} for v in values], h, d)


# --- one pass, one baseline ------------------------------------------------------------------------------


def test_the_baseline_is_run_ONCE_FOR_THE_PASS_and_shared_by_every_curve(pass_fixture, db):
    """The saving, and the honesty that has to travel with it. Two ladders, each containing the production
    default: the default is one config, so it is launched once per window and both curves cite the same
    baseline runs. Both baseline points are marked `runs_shared`, because one measurement cited twice is
    not two."""
    sweep_mod, root, launched, _ = pass_fixture
    reports = sweep_mod.run_pass(
        db,
        ladders=[
            _ladder("insider_core_alpha_liveness_days", [_DEFAULT, 90]),
            _ladder("activist_13d_liveness_days", [_DEFAULT_2, 90]),
        ],
        windows=[_W1, _W2],
        pin=_PIN,
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
        now=_NOW,
    )
    assert len(reports) == 2
    # 3 distinct configs (the shared default + one per ladder) x 2 windows = 6 jobs, not 8
    assert len(launched) == 6
    base = [next(p for p in r.points if p.is_baseline) for r in reports]
    assert base[0].run_ids == base[1].run_ids  # the SAME runs, cited by both curves
    assert all(p.runs_shared for p in base)
    # ...and one pass id on every curve and every job
    assert len({r.pass_id for r in reports}) == 1
    assert {j.pass_id for j in launched} == {reports[0].pass_id}


def test_every_curve_names_its_siblings(pass_fixture, db):
    """A reader holding one curve has to be able to tell it was measured beside others, against the same
    baseline over the same tape."""
    sweep_mod, root, _, _ = pass_fixture
    reports = sweep_mod.run_pass(
        db,
        ladders=[
            _ladder("insider_core_alpha_liveness_days", [_DEFAULT, 90]),
            _ladder("activist_13d_liveness_days", [_DEFAULT_2, 90]),
        ],
        windows=[_W1, _W2],
        pin=_PIN,
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
        now=_NOW,
    )
    for r in reports:
        assert r.pass_curves == [
            "insider_core_alpha_liveness_days",
            "activist_13d_liveness_days",
        ]
    assert {r.mirror_hash for r in reports} == {"c" * 64}  # one tape


def test_each_curve_carries_its_OWN_pre_registration(pass_fixture, db):
    """The runs carry the PASS's text and must: the baseline run belongs to every curve at once and cannot
    carry two different hypotheses. Each curve carries its own, which is the artifact a dial's result is
    quoted from."""
    sweep_mod, root, launched, _ = pass_fixture
    reports = sweep_mod.run_pass(
        db,
        ladders=[
            _ladder("insider_core_alpha_liveness_days", [_DEFAULT, 90], h="H5 core", d="rule A"),
            _ladder("activist_13d_liveness_days", [_DEFAULT_2, 90], h="H5 activist", d="rule B"),
        ],
        windows=[_W1, _W2],
        pin=_PIN,
        hypothesis="the pass hypothesis",
        decision_rule="the pass rule",
        root=root,
        now=_NOW,
    )
    assert [r.hypothesis for r in reports] == ["H5 core", "H5 activist"]
    assert [r.decision_rule for r in reports] == ["rule A", "rule B"]
    assert {j.hypothesis for j in launched} == {"the pass hypothesis"}


# --- identity with the sweeps it replaces -----------------------------------------------------------------


def _curve_shape(r) -> str:
    """A curve minus the four things that legitimately differ between a one-pass curve and the separate
    sweep it replaces: the run ids, the pass id, `runs_shared` and `pass_curves`.

    The last two differ BY DESIGN and are the point of the slice — in a pass the production baseline is
    one measurement cited by every curve, and each curve knows its siblings; alone, neither is true. They
    are excluded here and asserted EXPLICITLY below, rather than quietly smoothed away, because an
    exclusion nobody checks is how a real difference gets hidden behind a passing identity test."""
    blob = json.loads(r.model_dump_json())
    blob.pop("pass_id")
    blob.pop("pass_curves")
    for p in blob["points"]:
        p.pop("run_ids")
        p.pop("runs_shared")
    return json.dumps(blob, sort_keys=True)


def test_a_two_ladder_pass_matches_two_separate_sweeps(pass_fixture, db):
    """The claim that makes the saving safe to take: running two ladders in one pass changes what it
    COSTS, never what it says. Compared modulo run ids and the pass id, which differ by construction.
    """
    sweep_mod, root, _, _ = pass_fixture
    lads = [
        _ladder("insider_core_alpha_liveness_days", [_DEFAULT, 90], h="H5 core", d="rule A"),
        _ladder("activist_13d_liveness_days", [_DEFAULT_2, 90], h="H5 activist", d="rule B"),
    ]
    common = dict(
        windows=[_W1, _W2],
        pin=_PIN,
        hypothesis="the pass hypothesis",
        decision_rule="the pass rule",
        now=_NOW,
    )
    together = sweep_mod.run_pass(db, ladders=lads, root=root / "one", **common)
    apart = [
        sweep_mod.run_pass(db, ladders=[lad], root=root / f"sep{i}", **common)[0]
        for i, lad in enumerate(lads)
    ]
    for a, b in zip(together, apart, strict=True):
        assert _curve_shape(a) == _curve_shape(b)
    # ...and the TWO honest differences, asserted rather than smoothed away.
    # (1) in a pass the baseline is one measurement cited by both curves, so it is marked shared; run
    #     alone it is shared with nothing.
    assert all(next(p for p in r.points if p.is_baseline).runs_shared for r in together)
    assert not any(next(p for p in r.points if p.is_baseline).runs_shared for r in apart)
    # (2) a pass's curves name their siblings; a lone sweep names only itself.
    assert all(len(r.pass_curves) == 2 for r in together)
    assert all(len(r.pass_curves) == 1 for r in apart)


# --- resume -----------------------------------------------------------------------------------------------


def test_resume_relaunches_ONLY_the_pairs_that_are_missing(pass_fixture, db):
    """What turns "note the dead pass id and re-run 45 jobs" into "re-run the ones that died"."""
    sweep_mod, root, launched, _ = pass_fixture
    lads = [_ladder("insider_core_alpha_liveness_days", [_DEFAULT, 90])]
    common = dict(
        windows=[_W1, _W2],
        pin=_PIN,
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
        now=_NOW,
    )
    first = sweep_mod.run_pass(db, ladders=lads, **common)[0]
    assert len(launched) == 4  # 2 configs x 2 windows
    launched.clear()

    # kill one run the way a dead job does: the registry row stays, the outcomes do not
    victim = first.points[-1].run_ids[-1]
    (store.run_dir(victim, root) / "outcomes.parquet").unlink()

    again = sweep_mod.run_pass(db, ladders=lads, resume=first.pass_id, **common)[0]
    assert len(launched) == 1, [j.start for j in launched]  # exactly the dead pair
    assert again.pass_id == first.pass_id  # ...and it continues the pass rather than starting one
    # the three survivors are CITED, not re-run
    kept = set(first.points[0].run_ids) | {first.points[-1].run_ids[0]}
    assert kept <= {rid for p in again.points for rid in p.run_ids}


def test_a_registered_run_with_no_outcomes_does_not_count_as_done(pass_fixture, db):
    """A job killed mid-write can leave a directory and a row. Reusing it would pool a point over a
    truncated run, so readable outcomes -- not registration alone -- is what "done" means."""
    sweep_mod, root, launched, write_run = pass_fixture
    lads = [_ladder("insider_core_alpha_liveness_days", [_DEFAULT])]
    common = dict(
        windows=[_W1],
        pin=_PIN,
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
        now=_NOW,
    )
    first = sweep_mod.run_pass(db, ladders=lads, **common)[0]
    launched.clear()
    d = store.run_dir(first.points[0].run_ids[0], root)
    (d / "outcomes.parquet").unlink()
    sweep_mod.run_pass(db, ladders=lads, resume=first.pass_id, **common)
    assert len(launched) == 1


def test_a_resumed_curve_is_the_curve_an_uninterrupted_pass_would_have_produced(pass_fixture, db):
    """The seed is the pass id, so a resumed pass draws the same nulls as the one it continues — and the
    re-run pair is scored from the same inputs. The assembled curve must therefore be the same one.
    """
    sweep_mod, root, launched, _ = pass_fixture
    lads = [_ladder("insider_core_alpha_liveness_days", [_DEFAULT, 90])]
    common = dict(
        windows=[_W1, _W2],
        pin=_PIN,
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        now=_NOW,
    )
    whole = sweep_mod.run_pass(db, ladders=lads, root=root / "whole", **common)[0]

    broken = sweep_mod.run_pass(db, ladders=lads, root=root / "broken", **common)[0]
    victim = broken.points[-1].run_ids[-1]
    (store.run_dir(victim, root / "broken") / "outcomes.parquet").unlink()
    resumed = sweep_mod.run_pass(
        db, ladders=lads, root=root / "broken", resume=broken.pass_id, **common
    )[0]
    assert _curve_shape(resumed) == _curve_shape(whole)


def test_resuming_a_pass_id_nothing_ran_under_is_just_a_fresh_pass(pass_fixture, db):
    """Not an error: the id names a pass with no completed pairs, so every pair is missing and every one
    runs. That is what a resume of a pass that died on its first job looks like."""
    sweep_mod, root, launched, _ = pass_fixture
    sweep_mod.run_pass(
        db,
        ladders=[_ladder("insider_core_alpha_liveness_days", [_DEFAULT, 90])],
        windows=[_W1],
        pin=_PIN,
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
        now=_NOW,
        resume="20260918T031500Z-public-nothing-ran-here",
    )
    assert len(launched) == 2


# --- the CLI ------------------------------------------------------------------------------------------------


def test_the_cli_builds_one_ladder_per_dial_with_per_ladder_overrides():
    from backtest.sweep import _ladders_from_args

    args = build_parser().parse_args(
        [
            "--start",
            "2025-09-01",
            "--end",
            "2026-09-14",
            "--hypothesis",
            "pass H",
            "--decision-rule",
            "pass D",
            "--ladder",
            "insider_core_alpha_liveness_days=60,90,180",
            "--ladder",
            "activist_13d_liveness_days=90,180",
            "--ladder-hypothesis",
            "activist_13d_liveness_days=H5 activist",
        ]
    )
    lads = _ladders_from_args(args)
    assert [lad.key for lad in lads] == [
        "insider_core_alpha_liveness_days",
        "activist_13d_liveness_days",
    ]
    assert [len(lad.variants) for lad in lads] == [3, 2]
    assert [lad.hypothesis for lad in lads] == ["pass H", "H5 activist"]
    assert [lad.decision_rule for lad in lads] == ["pass D", "pass D"]


def test_the_cli_refuses_ladders_mixed_with_a_grid(capsys):
    """One is N curves, the other is one. Silently picking would make the artifact a guess."""
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
            "--ladder",
            "insider_core_alpha_liveness_days=90,180",
            "--dial",
            "activist_13d_liveness_days",
            "--values",
            "90,180",
        ]
    )
    assert code == 2
    assert "--ladder" in capsys.readouterr().err


def test_the_cli_refuses_pre_registration_for_a_dial_with_no_ladder(capsys):
    """A typo in a dial name would otherwise silently drop that curve's own hypothesis and substitute the
    pass's, which is the one thing a pre-registration must not do quietly."""
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
            "--ladder",
            "insider_core_alpha_liveness_days=90,180",
            "--ladder-hypothesis",
            "insider_core_alpha_livenes_days=typo",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "no --ladder" in err and "insider_core_alpha_livenes_days" in err


def test_an_unknown_ladder_dial_fails_before_the_mirror_export(capsys):
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
            "--ladder",
            "not_a_dial=1,2",
        ]
    )
    assert code == 2
    assert "not_a_dial" in capsys.readouterr().err


def test_run_sweep_is_still_the_single_curve_front_door(pass_fixture, db):
    """The grid path is unchanged and still returns ONE report — H3's three arms live on two dials and
    need the cartesian set, not a ladder per dial."""
    sweep_mod, root, _, _ = pass_fixture
    report = sweep_mod.run_sweep(
        db,
        grid={"insider_core_alpha_liveness_days": [_DEFAULT, 90]},
        windows=[_W1],
        pin=_PIN,
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
        now=_NOW,
    )
    assert isinstance(report, sweep_mod.SweepReport)
    assert report.pass_curves == ["insider_core_alpha_liveness_days"]
    assert len(report.points) == 2
