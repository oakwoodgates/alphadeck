"""Find a POSIX shell the tests can really launch: the one place that decides it.

Several suites run ``bash`` / ``sh`` scripts through ``subprocess`` (``scripts/deploy.sh``, the cron sidecar's
hooks). Off Windows that is plain ``shutil.which``. On Windows the shell on ``PATH`` is often NOT launchable
from Python: the ``bash.exe`` / ``sh.exe`` of a Git packaged inside a Microsoft Store (MSIX) app cannot be
started by another process (``PermissionError: [WinError 5] Access is denied`` from ``CreateProcess``), and
``System32\\bash.exe`` is WSL, another filesystem. So on Windows we use Git for Windows' ``usr/bin`` only, and
only a copy that a probe launch proves startable. If none is, the dependent tests skip with ``NO_SHELL_REASON``
(the single reason string) instead of failing.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NO_SHELL_REASON = (
    "needs a launchable POSIX shell (bash/sh + timeout): on Windows, Git for Windows' usr/bin that "
    "a probe launch can start (not WSL, not a Microsoft Store packaged Git)"
)


def _windows_git_bin_dirs() -> list[Path]:
    """Candidate Git-for-Windows ``usr/bin`` directories, the standard installs first."""
    roots: list[Path] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]) / "Git")
    roots.append(Path("C:/Program Files/Git"))  # the default install, if the environment names none
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Git")
    git = shutil.which("git")
    if git:  # a Git elsewhere (e.g. scoop): the root above its cmd/ or mingw64/bin/
        roots += list(Path(git).resolve().parents)
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        d = root / "usr" / "bin"
        if d not in seen and d.is_dir():
            seen.add(d)
            out.append(d)
    return out


def _launches(exe: Path) -> bool:
    try:
        r = subprocess.run([str(exe), "-c", "exit 0"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


@functools.lru_cache(maxsize=None)
def shell_dir() -> Path | None:
    """On Windows: the ``usr/bin`` of a Git whose ``bash.exe`` and ``timeout.exe`` start; elsewhere ``None``."""
    if sys.platform != "win32":
        return None
    for d in _windows_git_bin_dirs():
        if (d / "timeout.exe").is_file() and _launches(d / "bash.exe"):
            return d
    return None


def find_shell(name: str = "bash") -> str | None:
    """Path of a launchable ``bash`` / ``sh``, or ``None`` (the dependent tests then skip)."""
    if sys.platform != "win32":
        return shutil.which(name)
    d = shell_dir()
    return str(d / f"{name}.exe") if d is not None else None


def has_timeout() -> bool:
    """Whether the coreutils ``timeout`` is on hand (on Windows ``timeout.exe`` in System32 is another tool)."""
    if sys.platform != "win32":
        return shutil.which("timeout") is not None
    return shell_dir() is not None


def path_with(stub: Path | str | None = None) -> str:
    """``PATH`` for a shell child: the stub dir (if any) first, then (Windows) the launchable shell's dir, then
    ``PATH`` — so the child's ``date`` / ``timeout`` are the launchable Git's, not an unrunnable packaged copy.
    """
    d = shell_dir()
    parts = ([str(stub)] if stub else []) + ([str(d)] if d is not None else [])
    parts.append(os.environ.get("PATH", ""))
    return os.pathsep.join(parts)


def sh_path() -> str:
    """The ``sh`` to launch (the bare name if none was found, which only matters without ``needs_sh``)."""
    return find_shell("sh") or "sh"


needs_sh = pytest.mark.skipif(find_shell("sh") is None or not has_timeout(), reason=NO_SHELL_REASON)
