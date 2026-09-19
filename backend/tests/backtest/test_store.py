from __future__ import annotations

import json

import pytest

from backtest import manifest as mf
from backtest import store

# The run store. Two properties carry the whole design: a run directory is created fresh and NEVER reused,
# and the registry is rewritten atomically. Both are integrity rules, not tidiness — a reused directory
# means a run id in a PR description no longer identifies one set of numbers, and a truncated registry
# reads as runs having vanished.


def _summary(
    run_id: str, created_at: str = "2026-09-18T04:12:07+00:00", **over
) -> store.RunSummary:
    base = dict(
        run_id=run_id,
        created_at=created_at,
        config_short="abc12345",
        config_hash="a" * 64,
        clock="record",
        known_at_mode="pin",
        window_start="2025-09-01",
        window_end="2026-09-14",
    )
    base.update(over)
    return store.RunSummary(**base)


def test_a_run_directory_is_created_fresh(tmp_path):
    path = store.create_run_dir("run-1", tmp_path)
    assert path.is_dir() and path.name == "run-1"
    assert path.parent.name == store.RUNS_DIRNAME


def test_a_run_directory_is_never_reused(tmp_path):
    """Immutability, enforced rather than trusted: the second create RAISES instead of overwriting an
    artifact somebody may already have cited. Still a ``FileExistsError`` — ``RunDirExists`` subclasses it,
    so anything that already caught the builtin is unaffected."""
    store.create_run_dir("run-1", tmp_path)
    with pytest.raises(FileExistsError):
        store.create_run_dir("run-1", tmp_path)


def test_a_collision_SAYS_WHAT_COLLIDED(tmp_path):
    """A bare ``FileExistsError`` naming a temp path says something collided but not what, and the answer
    — every component of the run id agreed, inside one second — is not guessable from the path. It cost a
    real debugging detour, so the message now names the id, the location, the composition and the fix.
    """
    store.create_run_dir("20260918T041207Z-public-h5-abcd1234", tmp_path)
    with pytest.raises(store.RunDirExists) as exc:
        store.create_run_dir("20260918T041207Z-public-h5-abcd1234", tmp_path)
    msg = str(exc.value)
    assert "20260918T041207Z-public-h5-abcd1234" in msg  # WHICH run
    assert str(tmp_path) in msg  # WHERE
    for part in mf.RUN_ID_PARTS:  # WHAT had to agree — read from the one definition, never retyped
        assert part in msg
    assert "refuses rather than overwriting" in msg  # WHY it did not just proceed
    assert "wait a second" in msg  # and what to DO


def test_an_absent_registry_reads_as_empty_not_an_error(tmp_path):
    """No runs on this stack is a legitimate state (it is prod's state), not an outage."""
    assert store.list_runs(tmp_path) == []


def test_a_corrupt_registry_reads_as_empty_not_an_error(tmp_path):
    store.index_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    store.index_path(tmp_path).write_text("{ not json", encoding="utf-8")
    assert store.list_runs(tmp_path) == []


def test_registering_lists_newest_first(tmp_path):
    store.register_run(_summary("run-a", "2026-09-18T01:00:00+00:00"), tmp_path)
    store.register_run(_summary("run-c", "2026-09-18T03:00:00+00:00"), tmp_path)
    store.register_run(_summary("run-b", "2026-09-18T02:00:00+00:00"), tmp_path)
    assert [r.run_id for r in store.list_runs(tmp_path)] == ["run-c", "run-b", "run-a"]


def test_re_registering_the_same_run_does_not_duplicate_a_trial(tmp_path):
    """Counting trials is the registry's job; a re-registration must be idempotent or the count inflates."""
    store.register_run(_summary("run-a"), tmp_path)
    store.register_run(_summary("run-a", n_episodes=42), tmp_path)
    runs = store.list_runs(tmp_path)
    assert len(runs) == 1 and runs[0].n_episodes == 42


def test_the_registry_is_written_atomically_leaving_no_temp_file(tmp_path):
    store.register_run(_summary("run-a"), tmp_path)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    assert json.loads(store.index_path(tmp_path).read_text(encoding="utf-8"))["runs"]


def test_the_registry_records_which_dials_a_run_moved(tmp_path):
    """ "Count your trials" has to be answerable per DIAL, not just per run — otherwise nobody can say how
    many times a given dial has been swept before this result."""
    store.register_run(
        _summary("run-a", dials_moved=["insider_core_alpha_liveness_days"]), tmp_path
    )
    store.register_run(
        _summary(
            "run-b", dials_moved=["insider_core_alpha_liveness_days", "breakdown_dearm_enabled"]
        ),
        tmp_path,
    )
    touched = [
        r for r in store.list_runs(tmp_path) if "insider_core_alpha_liveness_days" in r.dials_moved
    ]
    assert len(touched) == 2


def test_the_registry_records_each_moved_dials_VALUE_and_old_rows_still_load(tmp_path):
    """B — the SIBLING of `dials_moved`. The row also names the VALUE each moved dial took, so the picker
    can label a run's arm off the row alone. Two properties: a fresh row carries its values, and a row
    written BEFORE the field existed (every index.json already on disk) still loads — the default fills
    in rather than the read failing."""
    # a legacy row: the JSON has no `dial_values` key at all
    store.index_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    store.index_path(tmp_path).write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "run_id": "legacy",
                        "created_at": "2026-09-18T04:12:07+00:00",
                        "config_short": "abc12345",
                        "config_hash": "a" * 64,
                        "clock": "record",
                        "known_at_mode": "pin",
                        "window_start": "2025-09-01",
                        "window_end": "2026-09-14",
                        "dials_moved": ["insider_core_alpha_liveness_days"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    legacy = store.list_runs(tmp_path)[0]
    assert legacy.dials_moved == ["insider_core_alpha_liveness_days"]
    assert legacy.dial_values == {}  # the default fills in for a row that predates the field

    # a fresh row carries its values through the register/read round trip
    store.register_run(
        _summary(
            "fresh",
            dials_moved=["insider_core_alpha_liveness_days"],
            dial_values={"insider_core_alpha_liveness_days": 90},
        ),
        tmp_path,
    )
    fresh = next(r for r in store.list_runs(tmp_path) if r.run_id == "fresh")
    assert fresh.dial_values == {"insider_core_alpha_liveness_days": 90}


def test_run_dir_resolves_a_registered_run(tmp_path):
    store.create_run_dir("run-a", tmp_path)
    assert store.run_dir("run-a", tmp_path) is not None


def test_run_dir_is_none_for_an_unknown_run(tmp_path):
    assert store.run_dir("nope", tmp_path) is None


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", ".hidden", ""])
def test_run_dir_refuses_to_walk_out_of_the_store(tmp_path, bad):
    """A run id reaches this from a URL in B6. It is a path component, so it must never be joined blindly."""
    assert store.run_dir(bad, tmp_path) is None


def test_write_metrics_accepts_a_plain_mapping(tmp_path):
    run = store.create_run_dir("run-a", tmp_path)
    path = store.write_metrics(run, {"n_episodes": 3})
    assert json.loads(path.read_text(encoding="utf-8")) == {"n_episodes": 3}
