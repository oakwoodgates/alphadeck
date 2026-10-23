"""S5 — WHERE the nightly page job runs: the cron sidecar's scheduling layer, after the nightly backup, bounded
and fail-open — never inside the call-of-record path. Static (no DB, no engine): the import graph, the cron
script and the compose build."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = (BACKEND / "scripts" / "daily_cron.sh").read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_the_nightly_pass_and_the_call_logic_import_nothing_from_editions():
    files = [BACKEND / "pipeline" / "daily.py", BACKEND / "pipeline" / "daily_job.py"]
    files += sorted((BACKEND / "calls").rglob("*.py"))
    offenders = [
        p.relative_to(BACKEND).as_posix()
        for p in files
        if any(m == "editions" or m.startswith("editions.") for m in _imports(p))
    ]
    assert offenders == []


def test_the_admin_run_daily_now_path_loads_no_editions_module():
    """``pipeline.daily_job`` is what the Admin "Run daily now" fires; loading it (and the bounded pass it
    runs) must never load the editions job — so the button can never append a page."""
    code = (
        "import sys\n"
        "import pipeline.daily_job, pipeline.bounded_pass, pipeline.daily, calls.assembler\n"
        "bad = sorted(m for m in sys.modules if m == 'editions' or m.startswith('editions.'))\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stdout + r.stderr


def _code_lines() -> list[str]:
    return [ln.strip() for ln in SCRIPT.splitlines() if not ln.strip().startswith("#")]


def test_the_sidecar_runs_it_once_per_wake_after_the_nightly_backup_bounded():
    code = _code_lines()
    runs = [ln for ln in code if "python -m editions.run" in ln]
    assert runs == ['timeout -k 60 "${EDITIONS_TIMEOUT_S}" python -m editions.run nightly --apply']
    calls = [i for i, ln in enumerate(code) if ln == 'editions_nightly "${target}"']
    assert len(calls) == 1  # once, in the nightly loop
    (at,) = calls
    loop = code.index("while :; do", code.index('if [ "${CRON_EXIT_AFTER_BOOT:-}" = 1 ]; then'))
    assert at > loop  # never in the boot block
    assert code[at - 1] == 'snapshot "${target}" "nightly"'  # right after the post-pass backup
    assert "editions" not in "\n".join(ln for ln in code if ln.startswith("run_pass"))


def test_compose_bakes_the_replay_extra_into_the_cron_image_only():
    compose = (BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    services = compose.split("\n  cron:\n", 1)
    backend_block = services[0].split("\n  backend:\n", 1)[1]
    cron_block = services[1].split("\n  frontend:\n", 1)[0]

    def code(block: str) -> str:
        return "\n".join(ln for ln in block.splitlines() if not ln.strip().startswith("#"))

    assert "PIP_EXTRAS: replay" in code(cron_block)
    assert "PIP_EXTRAS" not in code(backend_block)  # the API image stays lean
    # the kill switch + timeout defaults match the script's
    assert re.search(r"EDITIONS_NIGHTLY: \$\{ALPHADECK_EDITIONS_NIGHTLY:-1\}", cron_block)
    t = re.search(r"EDITIONS_TIMEOUT_S: \$\{ALPHADECK_EDITIONS_TIMEOUT_S:-(\d+)\}", cron_block)
    s = re.search(r'EDITIONS_TIMEOUT_S="\$\{EDITIONS_TIMEOUT_S:-(\d+)\}"', SCRIPT)
    # S7 raised it 1800 -> 3600 (a trial night re-runs the edition once or twice; MEASURED ~9 min on dev)
    assert t and s and t.group(1) == s.group(1) == "3600"


def _function(name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", SCRIPT, flags=re.S | re.M)
    assert m, name
    return m.group(0)


@pytest.mark.skipif(
    not (shutil.which("sh") and shutil.which("timeout")), reason="needs sh + timeout"
)
@pytest.mark.parametrize("rc, says", [(0, None), (75, "REFUSED"), (1, "FAILED (exit 1)")])
def test_the_hook_is_fail_open(tmp_path, rc, says):
    """Whatever the job exits with, the hook returns 0 (the loop goes on) and says what happened."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "python").write_text(f'#!/bin/sh\necho "stub $*"\nexit {rc}\n', encoding="utf-8")
    (stub / "python").chmod(0o755)
    body = (
        "EDITIONS_NIGHTLY=1\nEDITIONS_TIMEOUT_S=30\n"
        + _function("editions_nightly")
        + 'editions_nightly 2026-07-10\necho "hook-rc=$?"\n'
    )
    script = tmp_path / "hook.sh"
    script.write_text(body, encoding="utf-8")
    env = {"PATH": f"{stub}{':' if sys.platform != 'win32' else ';'}" + os.environ["PATH"]}
    r = subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, timeout=60, env={**os.environ, **env}
    )
    assert "stub -m editions.run nightly --apply" in r.stdout, r.stdout + r.stderr
    assert "hook-rc=0" in r.stdout
    if says:
        assert says in r.stdout
