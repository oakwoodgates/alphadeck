"""The fork-name guard in ``scripts/fork.sh`` and ``scripts/refresh-fork.sh``: a name starting with ``test`` is refused.

The hazard: a fork named ``testbed`` owns the database ``alphadeck_testbed``, which matches the test-database pattern
``alphadeck_test%`` that the cleanup drops. So both scripts refuse ``prod``, ``dev``, ``sig`` and any name that starts
with ``test`` (not a name that merely contains it).

Two layers: the case arm is read as TEXT (any host), and the real scripts run under bash in a throwaway directory
(skipped where no bash starts). The guard fails before any git or Docker call, so no run can touch a stack; the
fork.sh run passes no ``--slot`` so a name that gets past the guard stops at the slot check, and ``DOCKER_HOST``
points at a dead port in case anything slipped through.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.shell_support import NO_SHELL_REASON, find_shell, path_with

_REPO = Path(__file__).resolve().parents[3]
_FORK = _REPO / "scripts" / "fork.sh"
_REFRESH = _REPO / "scripts" / "refresh-fork.sh"

_BASH = find_shell("bash")
needs_bash = pytest.mark.skipif(_BASH is None, reason=NO_SHELL_REASON)

RESERVED = ["prod", "dev", "sig", "test", "testbed", "test_two", "test1"]
ALLOWED = ["bench", "latest", "mytest", "contest_2"]  # "test" inside the name, not at its start
REFUSAL = "is reserved (a real tier, or a name starting with 'test')"


def _run(root: Path, script: str, *args: str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOCKER_")}
    env.update(DOCKER_HOST="tcp://127.0.0.1:9", PATH=path_with(), PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [_BASH, script, *args],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return proc.returncode, proc.stdout


def _root_with(tmp_path: Path, script: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(script, tmp_path / "scripts" / script.name)
    return tmp_path


@pytest.mark.parametrize("script", [_FORK, _REFRESH], ids=lambda p: p.name)
def test_both_scripts_carry_the_same_test_prefix_rule(script):
    """Read as TEXT: the case arm has the prefix pattern (no bare ``test`` arm any more) and the same message in
    both scripts, so one cannot drift from the other."""
    text = script.read_text(encoding="utf-8")
    assert "prod|dev|sig|test*)" in text
    assert "prod|dev|sig|test)" not in text
    assert REFUSAL in text


@needs_bash
@pytest.mark.parametrize("name", RESERVED)
def test_fork_init_refuses_a_reserved_name_or_a_test_prefix(tmp_path, name):
    root = _root_with(tmp_path, _FORK)
    code, out = _run(root, "scripts/fork.sh", "init", name)
    assert code == 1, out
    assert f"FORK_NAME='{name}' {REFUSAL}" in out, out
    assert not (root / ".claude").exists() and not (root / ".env.fork").exists()


@needs_bash
@pytest.mark.parametrize("name", ALLOWED)
def test_fork_init_lets_a_name_with_test_inside_pass_the_guard(tmp_path, name):
    """Past the guard it stops at the next check (no ``--slot``), before any worktree or Docker step."""
    root = _root_with(tmp_path, _FORK)
    code, out = _run(root, "scripts/fork.sh", "init", name)
    assert code == 1, out
    assert "reserved" not in out, out
    assert "--slot must be a single digit 3-9" in out, out
    assert not (root / ".claude").exists()


@needs_bash
@pytest.mark.parametrize("name", RESERVED)
def test_refresh_fork_refuses_a_reserved_name_or_a_test_prefix(tmp_path, name):
    root = _root_with(tmp_path, _REFRESH)
    (root / ".env.fork").write_text(f"FORK_NAME={name}\n", encoding="utf-8")
    code, out = _run(root, "scripts/refresh-fork.sh", "--reset", "--dry-run")
    assert code == 1, out
    assert f"FORK_NAME='{name}' {REFUSAL}" in out, out
