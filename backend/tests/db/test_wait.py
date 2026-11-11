"""``db.wait`` through its injected seams: no database, no real sleeping."""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from db.wait import DEFAULT_TIMEOUT_S, timeout_from_env, wait_for_db


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def _run(probe: Callable[[], None], timeout_s: float = 10) -> tuple[bool, list[str], Clock]:
    clock, lines = Clock(), []
    ok = wait_for_db(
        timeout_s=timeout_s,
        probe=probe,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        out=lines.append,
    )
    return ok, lines, clock


def test_ready_first_probe_is_silent() -> None:
    ok, lines, clock = _run(lambda: None)
    assert ok and lines == [] and clock.slept == []


def test_fails_then_ready_prints_two_lines() -> None:
    calls = {"n": 0}

    def probe() -> None:
        calls["n"] += 1
        if calls["n"] <= 3:
            raise psycopg.OperationalError("connection refused")

    ok, lines, _ = _run(probe)
    assert ok and calls["n"] == 4
    assert lines == [
        "db-wait: database not ready (OperationalError: connection refused); waiting up to 10s",
        "db-wait: database ready after 3s",
    ]


def test_never_ready_stops_at_the_bound() -> None:
    def probe() -> None:
        raise psycopg.OperationalError("the database system is starting up")

    ok, lines, clock = _run(probe, timeout_s=3.5)
    assert not ok and len(lines) == 2
    assert lines[1] == (
        "db-wait: database still not ready after 4s "
        "(OperationalError: the database system is starting up)"
    )
    assert clock.t == pytest.approx(3.5)  # never slept past the bound


def test_multiline_error_is_collapsed() -> None:
    def probe() -> None:
        raise psycopg.OperationalError("line one\n\tline   two\nline three")

    _, lines, _ = _run(probe, timeout_s=1)
    assert all("\n" not in x for x in lines)
    assert "OperationalError: line one line two line three" in lines[0]


def test_other_exceptions_propagate() -> None:
    def probe() -> None:
        raise ValueError("bad url")

    with pytest.raises(ValueError):
        _run(probe)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (None, DEFAULT_TIMEOUT_S),
        ("", DEFAULT_TIMEOUT_S),
        ("abc", DEFAULT_TIMEOUT_S),
        ("0", DEFAULT_TIMEOUT_S),
        ("45", 45),
    ],
)
def test_timeout_from_env(raw: str | None, want: int) -> None:
    env = {} if raw is None else {"ALPHADECK_DB_WAIT_S": raw}
    assert timeout_from_env(env) == want
