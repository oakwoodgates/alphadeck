"""The Admin "Run daily now" pass is BOUNDED like the sidecar's (``pipeline/bounded_pass.py``).

Proved with a tiny timeout and STUB pass bodies (``bounded_pass_stubs.py`` — the work is stubbed, never a
real 90-minute run), in a REAL spawned child: the call-of-record lock the stub takes is the real advisory
lock on the test DB, and the start artifact it writes is the real M1 file. So the assertions are on the real
things — the lock is held while the pass runs and free once it is stopped, the artifact reads ``crashed``
with the reason, one crash page goes out — never on a mock of them.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from domain.settings import get_settings
from pipeline import bounded_pass, cron_run_log
from pipeline.bounded_pass import (
    PassDied,
    PassFailed,
    PassTimedOut,
    pass_payload,
    run_admin_pass,
    run_bounded,
)
from pipeline.run_lock import PassAlreadyRunning, probe_pass_lock
from tests.pipeline import bounded_pass_stubs as stubs


def _pager():
    pages: list = []
    return pages, SimpleNamespace(notify_health=pages.append)


def _until(predicate, within_s: float) -> bool:
    """Poll ``predicate`` for up to ``within_s`` — for a state another PROCESS changes (the child taking
    the lock; Postgres ending a dead client's session), which this one can only observe."""
    end = monotonic() + within_s
    while monotonic() < end:
        if predicate():
            return True
        sleep(0.2)
    return predicate()


def _stopped_run(stub, tmp_path, *, timeout_s: float, grace_s: float):
    """Run ``stub`` bounded, in a thread, and return (the error it ended with, whether the child was seen
    HOLDING the lock while it ran, the pages sent)."""
    pages, notifier = _pager()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(
            run_bounded,
            stub,
            (str(tmp_path),),
            timeout_s=timeout_s,
            grace_s=grace_s,
            runs_dir=tmp_path,
            notifier=notifier,
        )
        held = _until(lambda: probe_pass_lock() is not None, within_s=timeout_s)
        err = fut.exception(timeout=timeout_s + grace_s + 60)
    return err, held, pages


def test_a_pass_past_the_timeout_is_STOPPED_its_lock_RELEASED_its_artifact_CRASHED_and_it_PAGES(
    db, tmp_path
):
    """The hung pass (it holds the real run lock and sleeps): at the timeout it is stopped the sidecar's
    way, the lock it held is free again (Postgres ended its session with the process), its start artifact
    reads ``crashed`` with the reason, ONE crash page goes out, and the job's error says what happened.
    """
    t0 = monotonic()
    err, held, pages = _stopped_run(stubs.hang_holding_the_lock, tmp_path, timeout_s=12, grace_s=5)

    assert held  # the child really held the call-of-record lock while it ran
    assert isinstance(err, PassTimedOut)
    assert "ran past the run timeout (12 s) and was stopped" in str(err)
    assert "its run artifact is marked crashed and the run lock was released" in str(err)
    assert monotonic() - t0 < 12 + 5 + 30  # bounded: never the stub's hour-long sleep
    assert _until(lambda: probe_pass_lock() is None, within_s=15)  # the lock was released
    (doc,) = cron_run_log.list_run_logs(base_dir=tmp_path)
    assert doc["status"] == "crashed" and doc["run_kind"] == "manual"
    assert doc["error"].startswith("PassTimedOut: the pass ran past the run timeout (12 s)")
    assert len(pages) == 1 and pages[0].crashed.startswith("PassTimedOut:")
    assert pages[0].asof == stubs.ASOF


def test_a_pass_stopped_BEFORE_it_started_says_so_and_marks_nothing(tmp_path):
    """A pass stuck before it held the lock (MEASURED: the real body, connecting to an unreachable DB with
    no connect timeout, hangs there) is stopped all the same; there is no artifact to mark, and the error
    says so rather than claiming one was marked."""
    pages, notifier = _pager()
    with pytest.raises(PassTimedOut, match="it had not started yet"):
        run_bounded(
            stubs.hang_before_starting,
            (str(tmp_path),),
            timeout_s=8,
            grace_s=5,
            runs_dir=tmp_path,
            notifier=notifier,
        )
    assert cron_run_log.list_run_logs(base_dir=tmp_path) == []
    assert len(pages) == 1  # still one page: a manual pass that never got going is news too


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM can be ignored only on POSIX")
def test_a_pass_that_IGNORES_SIGTERM_is_KILLED_after_the_grace(db, tmp_path):
    """``timeout -k``'s second signal: a process the SIGTERM does not stop is SIGKILLed after the grace."""
    err, held, _ = _stopped_run(stubs.hang_ignoring_sigterm, tmp_path, timeout_s=12, grace_s=2)
    assert held and isinstance(err, PassTimedOut)
    assert _until(lambda: probe_pass_lock() is None, within_s=15)
    (doc,) = cron_run_log.list_run_logs(base_dir=tmp_path)
    assert doc["status"] == "crashed"


def test_a_pass_process_that_DIES_is_marked_crashed_pages_and_frees_the_lock(db, tmp_path):
    """Killed from outside after it started (the OOM shape): no Python ran its crash path, so the parent
    marks the artifact with the exit code and pages; the lock went with the process."""
    pages, notifier = _pager()
    with pytest.raises(PassDied, match=r"exited \(code 3\) before it finished"):
        run_bounded(
            stubs.die_mid_pass,
            (str(tmp_path),),
            timeout_s=120,
            grace_s=5,
            runs_dir=tmp_path,
            notifier=notifier,
        )
    assert _until(lambda: probe_pass_lock() is None, within_s=15)
    (doc,) = cron_run_log.list_run_logs(base_dir=tmp_path)
    assert doc["status"] == "crashed" and doc["error"].startswith("PassDied:")
    assert len(pages) == 1


def test_a_pass_that_FINISHES_returns_its_payload_and_marks_nothing(tmp_path):
    pages, notifier = _pager()
    got = run_bounded(
        stubs.finish,
        (str(tmp_path),),
        timeout_s=120,
        runs_dir=tmp_path,
        notifier=notifier,
    )
    assert got == {"mode": "live", "theses": 1, "runs_dir": str(tmp_path)}
    assert pages == [] and cron_run_log.list_run_logs(base_dir=tmp_path) == []


def test_a_pass_that_RAISES_is_reported_without_a_SECOND_page(tmp_path):
    """A pass that raises inside its own process already paged and marked itself (``run_daily_pass``'s
    H3 path) — the parent reports the error and adds nothing."""
    pages, notifier = _pager()
    with pytest.raises(PassFailed, match="RuntimeError: db exploded"):
        run_bounded(
            stubs.raise_inside,
            (str(tmp_path),),
            timeout_s=120,
            runs_dir=tmp_path,
            notifier=notifier,
        )
    assert pages == []


def test_a_pass_REFUSED_by_another_holder_of_the_lock_stays_PassAlreadyRunning(tmp_path):
    with pytest.raises(PassAlreadyRunning, match="alphadeck-pass:cron"):
        run_bounded(stubs.refused, (str(tmp_path),), timeout_s=120, runs_dir=tmp_path)


def test_the_Admin_pass_is_bounded_by_the_ONE_run_timeout(monkeypatch):
    """One value: the Admin bound is ``Settings.cron_run_timeout_s`` — the backend's mirror of the
    sidecar's ``RUN_TIMEOUT_S`` (both fed by ALPHADECK_CRON_RUN_TIMEOUT_S; pinned equal in
    ``test_killed_runs``) — and the body is the real pass (``pass_payload``)."""
    seen = []
    monkeypatch.setattr(
        bounded_pass, "run_bounded", lambda body, **kw: seen.append((body, kw)) or {"ok": 1}
    )
    assert run_admin_pass() == {"ok": 1}
    assert seen == [(pass_payload, {"timeout_s": float(get_settings().cron_run_timeout_s)})]
    run_admin_pass(timeout_s=3.0)
    assert seen[-1][1] == {"timeout_s": 3.0}
