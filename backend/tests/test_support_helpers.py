"""Meta-tests for the test-infrastructure helpers (``tests/shell_support.py``, ``tests/thread_support.py``)."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from tests import shell_support
from tests.thread_support import join_all


def test_off_windows_the_shell_is_plain_which_and_nothing_is_skipped_for_the_platform(monkeypatch):
    """Linux and CI: ``find_shell`` is ``shutil.which`` untouched, so the shell tests run exactly as before."""
    monkeypatch.setattr(shell_support.sys, "platform", "linux")
    assert shell_support.shell_dir.__wrapped__() is None  # type: ignore[attr-defined]
    for name in ("bash", "sh"):
        assert shell_support.find_shell(name) == shutil.which(name)
    assert shell_support.has_timeout() == (shutil.which("timeout") is not None)


def test_path_with_puts_the_stub_first_and_keeps_the_rest(monkeypatch, tmp_path):
    monkeypatch.setattr(shell_support, "shell_dir", lambda: None)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert shell_support.path_with(tmp_path) == f"{tmp_path}{shell_support.os.pathsep}/usr/bin"
    assert shell_support.path_with() == "/usr/bin"


def test_on_windows_the_packaged_unlaunchable_git_is_skipped(monkeypatch, tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    for d in (good, bad):
        d.mkdir()
        (d / "timeout.exe").write_text("")
    monkeypatch.setattr(shell_support.sys, "platform", "win32")
    monkeypatch.setattr(shell_support, "_windows_git_bin_dirs", lambda: [bad, good])
    monkeypatch.setattr(shell_support, "_launches", lambda exe: Path(exe).parent == good)
    shell_support.shell_dir.cache_clear()
    try:
        assert shell_support.shell_dir() == good
        assert shell_support.find_shell("sh") == str(good / "sh.exe")
        assert shell_support.path_with().split(shell_support.os.pathsep)[0] == str(good)
    finally:
        shell_support.shell_dir.cache_clear()


def test_join_all_fails_with_a_message_instead_of_hanging():
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, name="never-arrives", daemon=True)
    stuck.start()
    try:
        with pytest.raises(
            AssertionError, match=r"1 of 1 racers still running after 1s.*never-arrives"
        ):
            join_all([stuck], timeout_s=1, what="racers")
    finally:
        release.set()
        stuck.join(5)
