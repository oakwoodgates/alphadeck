"""A2b — ONE TAPE, MANY PASSES, and a mirror nobody can overwrite.

Two failures caught the day before phase 1b would have hit both.

**The overwrite.** A pass's mirror path is derived from `(pin, window, clock)`, so a second pass
registered on the same experiment resolves to the SAME directory and silently re-exported over the first.
Nothing about the second pass looks wrong. The damage lands on the FIRST: its 225 run manifests then cite
a hash the directory no longer has, so its numbers cannot be re-derived from their own tape and
`backtest.rebench` refuses them. A fresh pass now REFUSES rather than overwriting, and names the flag that
reuses the tape instead.

**The reuse.** Refusing is only half an answer — the operator still wants the second pass on the first
one's bytes, which is the better experiment anyway (one tape, two passes: comparable artifact for
artifact rather than merely through a shared pin). `--mirror-dir` sweeps an existing frozen mirror, skips
the export, inherits the mirror's clock, and records its hash on every run and every curve.

The clock is a property of the TAPE (CW), so an explicit `--clock` that disagrees with the supplied mirror
is refused: a record sweep and a public sweep are two experiments and are never one curve.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backtest import manifest as mf
from backtest import store
from backtest.sweep import MirrorExists, MirrorReuseError

_W = (date(2026, 1, 1), date(2026, 2, 11))
_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_DIAL = "insider_core_alpha_liveness_days"


@pytest.fixture
def swept(tmp_path, monkeypatch):
    """A stubbed pass: hand-written run artifacts, a stubbed launcher, and a REAL export stub that writes
    a mirror manifest — because what is under test is which tape a pass decides to sweep, not the
    export."""
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    import backtest.sweep as sweep_mod

    def _write_run(run_id: str, rows: list[tuple[str, float]]) -> None:
        d = store.runs_root(tmp_path) / run_id
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(
                [{"arm_date": a, "forward_return": r} for a, r in rows],
                schema=pa.schema([("arm_date", pa.string()), ("forward_return", pa.float64())]),
            ),
            d / "outcomes.parquet",
        )

    exported: list[Path] = []

    def _fake_export(_conn, out, **_kw):
        exported.append(Path(out))
        return {}

    monkeypatch.setattr(sweep_mod, "export_snapshot", _fake_export)
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)
    monkeypatch.setattr(sweep_mod, "mirror_hash", lambda p: "c" * 64)
    yield _write_run, exported, monkeypatch, sweep_mod, tmp_path


def _make_mirror(root: Path, name: str, *, clock: str = "public") -> Path:
    """A frozen mirror on disk: one Parquet file and the exporter's own manifest."""
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = root / "mirrors" / name
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist([{"x": 1}], schema=pa.schema([("x", pa.int64())])),
        d / "fact_price_eod.parquet",
    )
    (d / "manifest.json").write_text(
        json.dumps({"clock": clock, "tables": {}, "excluded_tables": []}), encoding="utf-8"
    )
    return d


def _sweep(sweep_mod, db, root, **kw):
    return sweep_mod.run_sweep(
        db,
        grid={_DIAL: [180, 90]},
        windows=[_W],
        pin=_PIN,
        hypothesis="A2b",
        decision_rule="one tape, many passes",
        root=root,
        **kw,
    )


# --- the refusal ------------------------------------------------------------------------------------------


def test_a_fresh_pass_REFUSES_to_overwrite_an_existing_mirror(swept, db):
    """The phase-1b near miss, as a test. The second pass is the one that would have looked fine."""
    _write_run, exported, monkeypatch, sweep_mod, root = swept
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    _write_run("b", [("2026-01-10", 0.05)])
    # the mirror a pass with THIS pin/window/clock resolves to, already on disk
    _make_mirror(root, f"{_PIN.strftime('%Y%m%dT%H%M%SZ')}-{_W[0]}-{_W[1]}-record", clock="record")

    with pytest.raises(MirrorExists) as exc:
        _sweep(sweep_mod, db, root)
    assert "--mirror-dir" in str(exc.value)  # it names the way out
    assert "--pin" in str(exc.value)  # ...and the other way out
    assert exported == [], "the refusal must happen BEFORE anything is written"


def test_the_refusal_does_not_fire_on_a_resume(swept, db):
    """A resume is the one case that SHOULD find a mirror there — it is the same pass continuing, and
    `_resume_mirror` already verifies the tape against the runs that finished."""
    _write_run, exported, monkeypatch, sweep_mod, root = swept
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    _write_run("b", [("2026-01-10", 0.05)])
    _make_mirror(root, f"{_PIN.strftime('%Y%m%dT%H%M%SZ')}-{_W[0]}-{_W[1]}-record", clock="record")
    rep = _sweep(sweep_mod, db, root, resume="some-pass-id")
    assert rep.pass_id == "some-pass-id"
    assert exported == [], "a resume must never re-export"


# --- the reuse --------------------------------------------------------------------------------------------


def test_a_supplied_mirror_is_SWEPT_not_re_exported(swept, db):
    _write_run, exported, monkeypatch, sweep_mod, root = swept
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    _write_run("b", [("2026-01-10", 0.05)])
    mirror = _make_mirror(root, "an-earlier-passes-tape", clock="public")

    rep = _sweep(sweep_mod, db, root, mirror_dir=mirror)
    assert exported == [], "the export is skipped entirely"
    assert rep.mirror_reused is True
    assert rep.clock == "public", "the clock is inherited from the tape"


def test_the_reused_mirrors_hash_is_what_the_curve_records(swept, db, monkeypatch):
    """The provenance claim: a later pass cites the SAME hash the earlier pass's manifests cite, which is
    what makes 'one tape, two passes' checkable rather than asserted."""
    _write_run, exported, mp, sweep_mod, root = swept
    mp.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    _write_run("b", [("2026-01-10", 0.05)])
    mirror = _make_mirror(root, "an-earlier-passes-tape", clock="public")
    mp.setattr(sweep_mod, "mirror_hash", lambda p: "beef" * 16)
    mp.setattr(sweep_mod.mf, "mirror_hash", lambda p: "beef" * 16)

    rep = _sweep(sweep_mod, db, root, mirror_dir=mirror)
    assert rep.mirror_hash == "beef" * 16


def test_a_directory_that_is_not_a_mirror_is_refused(swept, db, tmp_path):
    """Exporting into it would produce a fresh snapshot wearing the name of a reused one — the failure
    this flag exists to prevent, arrived at from the other side."""
    _write_run, exported, monkeypatch, sweep_mod, root = swept
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    empty = tmp_path / "not-a-mirror"
    empty.mkdir()
    with pytest.raises(MirrorReuseError, match="no mirror manifest"):
        _sweep(sweep_mod, db, root, mirror_dir=empty)
    assert exported == []


def test_a_clock_that_DISAGREES_with_the_supplied_mirror_is_refused(swept, db):
    """CW's rule, enforced on the reuse path: the clock belongs to the TAPE. Honoring the flag would put
    two fact axes under one curve; honoring the mirror silently would drop a flag the operator typed.
    """
    _write_run, exported, monkeypatch, sweep_mod, root = swept
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    mirror = _make_mirror(root, "a-public-tape", clock="public")
    with pytest.raises(MirrorReuseError, match="disagrees"):
        _sweep(sweep_mod, db, root, mirror_dir=mirror, clock="record")
    assert exported == []


def test_an_AGREEING_clock_is_accepted_rather_than_treated_as_a_conflict(swept, db):
    """Spelling out the tape's own clock is not a disagreement — a pre-registered launch line names it
    deliberately, and refusing that would punish the careful operator."""
    _write_run, exported, monkeypatch, sweep_mod, root = swept
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    _write_run("b", [("2026-01-10", 0.05)])
    mirror = _make_mirror(root, "a-public-tape", clock="public")
    rep = _sweep(sweep_mod, db, root, mirror_dir=mirror, clock="public")
    assert rep.clock == "public" and rep.mirror_reused is True


def test_no_mirror_dir_means_the_pass_exports_its_own_and_says_so(swept, db):
    """The unchanged path, pinned: nothing about a normal pass moves."""
    _write_run, exported, monkeypatch, sweep_mod, root = swept
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["a", "b"])
    _write_run("a", [("2026-01-10", 0.0)])
    _write_run("b", [("2026-01-10", 0.05)])
    rep = _sweep(sweep_mod, db, root)
    assert len(exported) == 1 and rep.mirror_reused is False
    assert rep.clock == "record"  # the default when no tape says otherwise


def test_the_cli_takes_the_flag_and_defaults_the_clock_to_NONE():
    """The S1 lesson: a parser default of "record" is indistinguishable from the operator typing it, so
    `--clock` would be silently dropped against a public mirror. `None` keeps the two apart."""
    from backtest.sweep import build_parser

    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-02-11",
            "--hypothesis",
            "h",
            "--decision-rule",
            "r",
        ]
    )
    assert args.clock is None and args.mirror_dir is None
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-02-11",
            "--hypothesis",
            "h",
            "--decision-rule",
            "r",
            "--mirror-dir",
            "/tmp/x",
            "--clock",
            "public",
        ]
    )
    assert args.mirror_dir == "/tmp/x" and args.clock == "public"


# --- phase 1's own tape, on disk ---------------------------------------------------------------------------


def test_the_mirror_manifest_is_what_marks_a_directory_as_a_frozen_tape(tmp_path):
    """Both halves key on the same fact — `read_mirror_manifest` — so the refusal and the reuse can never
    disagree about what a mirror IS."""
    from replay.export import read_mirror_manifest

    d = _make_mirror(tmp_path, "m", clock="public")
    assert read_mirror_manifest(d) is not None
    assert mf.mirror_hash(d)  # and it is content-addressable
    bare = tmp_path / "bare"
    bare.mkdir()
    assert read_mirror_manifest(bare) is None
