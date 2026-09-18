from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backtest import artifact, store
from backtest.run import MirrorClockMismatch, execute
from replay.export import MANIFEST_NAME, mirror_clock, read_mirror_manifest

# CW -- THE CLOCK IS A PROPERTY OF THE MIRROR, AND A RUN INHERITS IT.
#
# B2 built the public-clock export and the lockstep fact axis; neither was reachable from the run layer,
# and the tempting fix -- a `--clock` flag on the run and a `--known-at-mode` flag beside it -- is the one
# shape that can go wrong silently. Two flags can disagree with each other, and either can disagree with
# the bytes: a run labeled `public` over a record-clock tape is a result nobody can catch by reading the
# numbers. So the axis is read back off the mirror's own manifest, `known_at_mode` is DERIVED from it, and
# a supplied mirror handed a disagreeing clock refuses before doing any work.

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(2025, 5, 15)


def _mirror_manifest(path: Path, blob: dict) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / MANIFEST_NAME).write_text(json.dumps(blob), encoding="utf-8")
    return path


# --- reading the mirror back (pure) -------------------------------------------------------------------


def test_the_clock_is_read_off_the_mirrors_own_manifest(tmp_path):
    _mirror_manifest(tmp_path / "pub", {"clock": "public", "tables": {}, "excluded_tables": ["x"]})
    m = read_mirror_manifest(tmp_path / "pub")
    assert m is not None and m.clock == "public" and m.excluded_tables == ["x"]
    assert mirror_clock(tmp_path / "pub") == "public"


def test_a_mirror_that_predates_the_field_reads_as_the_system_clock(tmp_path):
    """Honest rather than unknown: a mirror written before the public mode existed WAS `recorded_at`."""
    _mirror_manifest(
        tmp_path / "old", {"tenant_id": "t", "tables": {"fact_price_eod": {"rows": 3}}}
    )
    assert mirror_clock(tmp_path / "old") == "record"


def test_a_missing_or_unreadable_manifest_is_the_system_clock_and_excludes_nothing(tmp_path):
    """Excluding NOTHING is the load-bearing half: it is what keeps a genuinely missing Parquet file
    failing LOUDLY in `connect_mirror` rather than yielding a mirror that silently answers nothing for
    that table."""
    assert read_mirror_manifest(tmp_path / "nope") is None
    assert mirror_clock(tmp_path / "nope") == "record"
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    assert read_mirror_manifest(bad) is None


def test_a_finished_runs_own_manifest_is_still_readable_as_its_mirrors(tmp_path):
    """The filename collision, handled rather than tripped over.

    A run that exports its OWN mirror writes both manifests into one directory under one name, run
    manifest last. Re-opening that directory as a mirror is legitimate, and the exclusion list has to
    survive -- which it does, because the run manifest records the same facts under `mirror`."""
    _mirror_manifest(
        tmp_path / "run",
        {
            "run_id": "20260917-abcd1234",
            "clock": "public",
            "mirror": {
                "hash": "c" * 64,
                "excluded_tables": ["fact_cash_burn"],
                "blind_detectors": [],
                "tables": {},
            },
        },
    )
    m = read_mirror_manifest(tmp_path / "run")
    assert m is not None
    assert m.clock == "public"
    assert m.excluded_tables == ["fact_cash_burn"]


# --- the refusal, before any work ---------------------------------------------------------------------


def test_a_disagreeing_clock_on_a_supplied_mirror_refuses_before_doing_work(tmp_path):
    """The mirror is a bare manifest -- no Parquet, no database. That the call still raises is the point:
    the refusal precedes the export, the DuckDB open and every use of the connection, so a refused run
    leaves no directory and no registry row behind."""
    mirror = _mirror_manifest(tmp_path / "m", {"clock": "public", "tables": {}})
    root = tmp_path / "store"
    with pytest.raises(MirrorClockMismatch) as exc:
        execute(
            None,  # type: ignore[arg-type] -- never touched; reaching it would be the failure
            start=_START,
            end=_END,
            pin=_PIN,
            mirror_dir=mirror,
            clock="record",
            root=root,
        )
    assert "public" in str(exc.value) and "record" in str(exc.value)
    assert store.list_runs(root) == []
    assert not (root / "runs").exists() or not any((root / "runs").iterdir())


def test_the_agreeing_clock_is_a_no_op_and_no_clock_at_all_inherits(tmp_path):
    """A sweep's points pass no clock; a careful caller may pass the matching one. Neither may raise --
    only a DISAGREEING one does, which is why this asserts the two accepted shapes explicitly. Both then
    fail LATER, on the real work (the mirror holds no Parquet), which is the correct failure."""
    mirror = _mirror_manifest(tmp_path / "m", {"clock": "public", "tables": {}})
    for clock in ("public", None):
        with pytest.raises(Exception) as exc:  # noqa: B017
            execute(
                None,  # type: ignore[arg-type]
                start=_START,
                end=_END,
                pin=_PIN,
                mirror_dir=mirror,
                clock=clock,
                root=tmp_path / f"store-{clock}",
            )
        assert not isinstance(exc.value, MirrorClockMismatch), clock


# --- end to end, on a real mirror ---------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_public_clock_run_says_public_and_lockstep_and_carries_the_exclusions(db, tmp_path):
    """The whole slice in one assertion set: the flag reaches the export, the manifest reports the axis it
    actually swept, `known_at_mode` follows the clock rather than a second flag, and the mirror's own
    accounting (B2's counts, the excluded tables and the detectors they blind) rides the run manifest --
    so a reader learns how big the hole in the tape was without going to find the mirror."""
    pytest.importorskip("duckdb")
    from pipeline.seed import seed_unh

    seed_unh(db)
    db.commit()
    outcome = execute(db, start=_START, end=_END, pin=_PIN, clock="public", root=tmp_path)
    m = outcome.manifest
    assert m.clock == "public"
    assert m.known_at_mode == "lockstep", "the fact axis must follow the clock, not a second flag"
    # B2 excludes any table with no declared public clock, and names them
    assert set(m.mirror.excluded_tables) == {
        "fact_revenue_mix",
        "fact_shares_outstanding",
        "fact_cash_burn",
        "fact_fund_shares",
    }
    assert m.mirror.blind_detectors == []  # none of those is read by a call detector
    assert m.mirror.hash and len(m.mirror.hash) == 64
    # ...and the numbers that say how much of the tape survived the rewrite
    counts = m.mirror.tables["fact_insider_txn"]
    assert counts.rows_in >= counts.rows_out
    assert all(t not in m.mirror.tables for t in m.mirror.excluded_tables)


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_record_run_is_unchanged_and_the_flag_is_a_no_op(db, tmp_path):
    """`clock=record` must be today's run, byte for byte. The default and the explicit flag are run
    against each other rather than against a stored expectation: an outcome snapshot would only pin
    whatever this fixture happens to produce, while equality pins that the argument changed nothing.
    """
    pytest.importorskip("duckdb")
    from pipeline.seed import seed_unh

    seed_unh(db)
    db.commit()
    default = execute(db, start=_START, end=_END, pin=_PIN, root=tmp_path)
    explicit = execute(db, start=_START, end=_END, pin=_PIN, clock="record", root=tmp_path)
    assert default.manifest.clock == explicit.manifest.clock == "record"
    assert default.manifest.known_at_mode == explicit.manifest.known_at_mode == "pin"
    assert (default.path / "episodes.parquet").read_bytes() == (
        explicit.path / "episodes.parquet"
    ).read_bytes()
    assert default.manifest.mirror.hash == explicit.manifest.mirror.hash
    # the record export is a straight copy, so its per-table accounting is the identity -- stated, so a
    # record run's manifest is as informative about the tape's size as a public run's
    counts = default.manifest.mirror.tables["fact_price_eod"]
    assert counts.rows_in == counts.rows_out
    assert counts.rows_dropped_null_clock == 0 and counts.identities_lost == 0


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_every_point_of_a_sweep_cites_one_mirror_on_one_clock(db, tmp_path):
    """The points are run with NO clock argument at all -- they inherit. That is what makes "one tape, one
    axis, N dial settings" structural instead of something each call site has to repeat correctly, and it
    is the property a delta between two points depends on."""
    pytest.importorskip("duckdb")
    from backtest.manifest import read_manifest
    from backtest.sweep import run_sweep
    from pipeline.seed import seed_unh

    seed_unh(db)
    db.commit()
    report = run_sweep(
        db,
        grid={"insider_core_alpha_liveness_days": [90, 180]},
        windows=[(_START, _END)],
        pin=_PIN,
        hypothesis="CW smoke",
        decision_rule="plateau, not argmax",
        clock="public",
        root=tmp_path,
    )
    assert report.clock == "public"
    assert len(report.points) == 2
    seen = set()
    for p in report.points:
        d = store.run_dir(p.run_ids[0], tmp_path)
        assert d is not None
        m = read_manifest(d)
        assert m is not None
        seen.add((m.clock, m.known_at_mode, m.mirror.hash))
        # ...and the axis reaches the LEDGER's banner too -- the surface sentence a reader quotes from.
        # This is the regression for a silent merge: B6 wrote `build_ledger(clock=clock)` when `clock` was
        # a local constant, CW turned it into a nullable "inherit" argument, and git merged both cleanly
        # because the edits never shared a line. A sweep point passes no clock, so the banner would have
        # read None here while the manifest beside it read `public`.
        ledger = artifact.read_ledger(d)
        assert ledger is not None
        assert "clock public" in ledger.snapshot.banner
        assert "became PUBLIC" in ledger.snapshot.banner
    assert seen == {("public", "lockstep", report.mirror_hash)}


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_two_sweeps_on_different_clocks_do_not_overwrite_each_others_tape(db, tmp_path):
    """Same window, same pin, ONE hypothesis, different axis — the exact shape the first pre-registered
    pass will run back to back, and the shape that used to break in two ways.

    The mirror directory is named for the clock, so the second sweep exports BESIDE the first rather than
    over it; otherwise the first sweep's points would go on citing a mirror hash that no longer describes
    the tape they swept. And the run id now carries the clock, so the two passes do not collide inside the
    timestamp's one-second resolution — on a window this short a point finishes well inside one second,
    which is how this was found (the earlier draft of this test died on a bare `FileExistsError` from
    `create_run_dir`). One hypothesis on purpose: passing two would hide both bugs again."""
    pytest.importorskip("duckdb")
    from backtest.sweep import run_sweep

    seed = dict(
        grid={"insider_core_alpha_liveness_days": [180]},
        windows=[(_START, _END)],
        pin=_PIN,
        hypothesis="CW smoke",
        decision_rule="plateau, not argmax",
        root=tmp_path,
    )
    rec = run_sweep(db, clock="record", **seed)
    pub = run_sweep(db, clock="public", **seed)
    assert rec.mirror_hash != pub.mirror_hash
    mirrors = sorted(p.name for p in (tmp_path / "mirrors").iterdir())
    assert len(mirrors) == 2
    assert mirrors[0].endswith("public") and mirrors[1].endswith("record")
    # ...and the four runs are four addressable trials, not two runs and two crashes
    assert len(store.list_runs(tmp_path)) == 2
    ids = {r.run_id for r in store.list_runs(tmp_path)}
    assert len({i for i in ids if "-record-" in i}) == 1
    assert len({i for i in ids if "-public-" in i}) == 1


# --- the CLI contract: --clock must REACH execute ---------------------------------------------------------


def test_the_cli_refuses_a_clock_that_disagrees_with_a_supplied_mirror(tmp_path, capsys):
    """THE BUG THIS REPLACES. `main()` used to drop `--clock` whenever `--mirror-dir` was given, because
    the parser's own default ("record") is indistinguishable from the operator TYPING "record" -- so
    `--mirror-dir <public mirror> --clock record` ran happily on the public clock while the help text
    promised a disagreeing clock was refused. The default now lives in `execute` and nowhere else, which
    is what lets the flag arrive and be checked.

    The mirror is a bare manifest with no Parquet: the refusal has to come BEFORE any work, so reaching
    the export or the DuckDB open would itself be the failure."""
    from backtest.run import main

    mirror = _mirror_manifest(tmp_path / "m", {"clock": "public", "tables": {}})
    with pytest.raises(MirrorClockMismatch) as exc:
        main(
            [
                "--start",
                str(_START),
                "--end",
                str(_END),
                "--pin",
                _PIN.isoformat(),
                "--mirror-dir",
                str(mirror),
                "--clock",
                "record",
                "--out-root",
                str(tmp_path / "store"),
            ]
        )
    assert "public" in str(exc.value) and "record" in str(exc.value)
    assert store.list_runs(tmp_path / "store") == []


def test_the_cli_default_is_absent_so_a_mirror_can_be_inherited_from():
    """A parser default of "record" cannot be told apart from the operator typing it, and that is exactly
    what made the flag un-refusable. `execute` resolves None as "inherit the mirror's clock, else record",
    so the DEFAULT lives in one place and the CLI's job is only to pass what it was given."""
    from backtest.run import build_parser

    base = ["--start", "2025-01-01", "--end", "2025-02-01"]
    assert build_parser().parse_args(base).clock is None
    assert build_parser().parse_args([*base, "--clock", "public"]).clock == "public"


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_cli_with_no_mirror_and_no_flag_still_runs_on_the_record_clock(db, tmp_path):
    """The default path, unchanged: no mirror and no flag is a record-clock run with a pinned known_at."""
    pytest.importorskip("duckdb")
    from backtest.run import main
    from pipeline.seed import seed_unh

    seed_unh(db)
    db.commit()
    assert (
        main(
            [
                "--start",
                str(_START),
                "--end",
                str(_END),
                "--pin",
                _PIN.isoformat(),
                "--out-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    rows = store.list_runs(tmp_path)
    assert len(rows) == 1 and rows[0].clock == "record" and rows[0].known_at_mode == "pin"
