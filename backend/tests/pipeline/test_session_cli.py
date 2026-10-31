"""F5 — the shell asks Python whether a date is a session: ``python -m pipeline.schedule is-session D``.

The CLI (exit 0 = a session, 1 = not) is exercised in-process and as a real subprocess. The sidecar's
``is_session`` (``scripts/daily_cron.sh``) is extracted verbatim and run under ``sh`` twice: against the REAL
CLI (a ``python`` on PATH that execs this interpreter, from ``backend/``) and against a stub ``python`` that
exits 0 / 1 / 2 / 127 — any exit that is not an answer must log ONE loud line and fall back to Mon-Fri, so
a broken check can never stop the nightly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import schedule

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = (BACKEND / "scripts" / "daily_cron.sh").read_text(encoding="utf-8")

needs_sh = pytest.mark.skipif(
    not (shutil.which("sh") and shutil.which("timeout")), reason="needs sh + timeout"
)


# --- the CLI ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "day, rc, says",
    [
        ("2026-11-27", 0, "a market session"),  # the half-day after Thanksgiving
        ("2026-11-26", 1, "a market holiday (Thanksgiving Day)"),
        ("2026-09-07", 1, "a market holiday (Labor Day)"),
        ("2026-10-31", 1, "a weekend"),
    ],
)
def test_the_cli_answers_with_its_exit_code(capsys, day, rc, says):
    assert schedule.main(["is-session", day]) == rc
    assert says in capsys.readouterr().out


def test_a_malformed_date_is_not_an_answer():
    with pytest.raises(SystemExit) as exc:
        schedule.main(["is-session", "next thursday"])
    assert exc.value.code == 2  # neither 0 nor 1: the shell's fail-safe takes over


def test_the_real_module_entry_point_as_the_shell_runs_it():
    """``python -m pipeline.schedule`` from ``backend/`` — the sidecar's exact invocation, a real process."""
    for day, rc in (("2026-11-25", 0), ("2026-11-26", 1)):
        r = subprocess.run(
            [sys.executable, "-m", "pipeline.schedule", "is-session", day],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert r.returncode == rc, r.stdout + r.stderr


def test_the_cli_imports_only_the_calendar():
    """No DB, no settings, no clock: the shell may call it before Postgres is up (the boot block's first
    question) and it must answer."""
    src = (BACKEND / "pipeline" / "schedule.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:from|import) ([\w.]+)", src, flags=re.M))
    assert imported == {
        "__future__",
        "argparse",
        "sys",
        "collections.abc",
        "datetime",
        "domain.trading_days",
    }


# --- the sidecar's is_session ----------------------------------------------------------------------------


def _function(name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", SCRIPT, flags=re.S | re.M)
    assert m, name
    return m.group(0)


def test_the_shell_keeps_no_calendar_of_its_own():
    code = [ln for ln in SCRIPT.splitlines() if not ln.strip().startswith("#")]
    body = "\n".join(code)
    assert "is_weekday" not in body and "next_weekday" not in body
    # the only `+%u` reads left are the fail-safe fallback and the two WEEKLY jobs' day-of-week (calendar
    # weeks, not sessions)
    u_lines = [ln.strip() for ln in code if "+%u" in ln]
    assert u_lines[0] == '[ "$(date -d "$1" +%u)" -le 5 ]', u_lines
    assert all("_nw_cand" in ln or "DOW" in ln for ln in u_lines[1:]), u_lines
    assert "python -m pipeline.schedule is-session" in _function("is_session")
    for caller in ("next_session", "last_expected_asof"):
        assert "is_session" in _function(caller)


def _run_is_session(
    tmp_path: Path, python_body: str, days: list[str]
) -> subprocess.CompletedProcess:
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "python").write_text(f"#!/bin/sh\n{python_body}\n", encoding="utf-8")
    (stub / "python").chmod(0o755)
    calls = "".join(
        f'if is_session {d}; then echo "{d}=session"; else echo "{d}=closed"; fi\n' for d in days
    )
    script = tmp_path / "check.sh"
    script.write_text("set -u\n" + _function("is_session") + calls, encoding="utf-8")
    sep = ";" if sys.platform == "win32" else ":"
    env = {**os.environ, "PATH": f"{stub}{sep}{os.environ['PATH']}"}
    return subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, timeout=120, env=env, cwd=BACKEND
    )


@needs_sh
def test_the_shell_reads_the_real_calendar(tmp_path):
    exe = Path(sys.executable).as_posix()
    r = _run_is_session(
        tmp_path,
        f'exec "{exe}" "$@"',
        ["2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28", "2026-09-07", "2026-09-08"],
    )
    assert r.stdout.split() == [
        "2026-11-25=session",
        "2026-11-26=closed",  # Thanksgiving: no pass
        "2026-11-27=session",  # the half-day
        "2026-11-28=closed",
        "2026-09-07=closed",  # Labor Day
        "2026-09-08=session",
    ], (
        r.stdout + r.stderr
    )
    assert "SESSION CHECK FAILED" not in r.stderr


@needs_sh
@pytest.mark.parametrize("rc", [2, 127])
def test_a_broken_check_falls_back_to_mon_fri_with_one_loud_line_each(tmp_path, rc):
    """Python broken (an import error, a missing interpreter): the answer is plain Mon-Fri — a holiday reads as
    a session, exactly the pre-F5 behavior — so the nightly never stops; each check logs ONE line to stderr.
    """
    r = _run_is_session(
        tmp_path,
        f'echo "Traceback (most recent call last):" >&2\necho "ImportError: boom" >&2\nexit {rc}',
        ["2026-11-26", "2026-11-28"],
    )
    assert r.stdout.split() == ["2026-11-26=session", "2026-11-28=closed"], r.stdout + r.stderr
    loud = [ln for ln in r.stderr.splitlines() if "SESSION CHECK FAILED" in ln]
    assert len(loud) == 2 and len(r.stderr.strip().splitlines()) == 2, r.stderr
    assert f"exit {rc}: ImportError: boom" in loud[0] and "falling back to Mon-Fri" in loud[0]
    assert "SESSION CHECK FAILED" not in r.stdout  # never captured by a caller's $(...)


@needs_sh
def test_the_answer_codes_pass_straight_through(tmp_path):
    for rc, word in ((0, "session"), (1, "closed")):
        sub = tmp_path / str(rc)
        sub.mkdir()
        r = _run_is_session(sub, f"exit {rc}", ["2026-10-31"])  # a Saturday: the stub's word wins
        assert r.stdout.split() == [f"2026-10-31={word}"] and r.stderr == "", r.stdout + r.stderr
