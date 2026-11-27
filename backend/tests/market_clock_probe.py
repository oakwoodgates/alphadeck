"""The suite-wide market-clock pin (F5): run every test as if the market clock read one instant.

``ALPHADECK_TEST_MARKET_NOW=<ISO instant with its offset>`` (e.g. ``2026-12-25T12:00:00-05:00``) makes the session
fixture in ``tests/conftest.py`` call ``pin_from_env``: from the first test on, ``market_now()`` starts at that instant
and ticks with the run, and so do ``market_today()``, ``market_clock()``, ``current_night()``, ``pinned_today`` and
every route and job that reads them. Unset (CI, every normal run), nothing is patched. ``scripts/closure_probe.py``
runs the suite this way at NYSE closures and at a control a week earlier (framework-completion plan §3.3).

**What moves.** ``domain.market_time.market_now``, the one seam every market-day read goes through, AND every
reference that kept the original by name, which a patch of the module attribute alone misses: a module attribute
(``from domain.market_time import market_now`` in ``app/routers/admin.py``, ``app/routers/scoreboard.py``,
``pipeline/daily.py``, ``pipeline/fill_price_gaps.py``, ``pipeline/nightly_extraction.py``,
``pipeline/onboarding.py`` and the tests that do the same) and a function's default argument
(``pipeline.onboarding.run_batch(now=market_now)``). A reference kept anywhere else (a closure, a container) and a
subprocess a test starts keep the live clock.

**What does not.** Transaction time: ``datetime.now(timezone.utc)``, the database clock, the ``known_at`` cap's
``now`` (invariant 1, the same rule as ``pinned_today``). So the pinned instant must be LATER than the real one: a
fact a test records now would otherwise be recorded after the pinned day, from its future.

A test's own pin still wins for that test: ``monkeypatch.setattr(market_time, "market_now", ...)`` (``pinned_today``,
``pin_market_clock``) replaces this one, and a test that freezes the module's own ``datetime``
(``tests/domain/test_market_time.py``) is honored, as it would be on that real day.
"""

from __future__ import annotations

import sys
import time
import types
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timedelta, timezone

import pytest

from domain import market_time

ENV = "ALPHADECK_TEST_MARKET_NOW"

_REAL_DATETIME = datetime


def instant_from_env(environ: Mapping[str, str], *, now: datetime | None = None) -> datetime | None:
    """The instant ``ALPHADECK_TEST_MARKET_NOW`` asks for, or ``None`` when it is unset or blank.

    Refused (``ValueError``) unless it is an ISO instant WITH its offset (a naive time has no market date) and later
    than ``now`` (default: the real UTC instant; see the module docstring)."""
    raw = (environ.get(ENV) or "").strip()
    if not raw:
        return None
    try:
        instant = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV}={raw!r} is not an ISO instant ({exc})") from exc
    if instant.utcoffset() is None:
        raise ValueError(f"{ENV}={raw!r} has no offset: give one, e.g. 2026-12-25T12:00:00-05:00")
    real = now or datetime.now(timezone.utc)
    if instant <= real:
        raise ValueError(
            f"{ENV}={raw!r} is not later than now ({real.isoformat()}): transaction time is not pinned, so a "
            "fact recorded now would be from the pinned day's future"
        )
    return instant


def pin_market_now_everywhere(instant: datetime, mp: pytest.MonkeyPatch) -> list[str]:
    """Pin the market clock at ``instant`` (aware), ticking from it, wherever the current ``market_now`` is held.

    Replaces ``domain.market_time.market_now`` and every loaded module attribute and function default that IS the
    function it holds now (the original, or an outer pin: pins nest), through ``mp``, so ``mp.undo()`` restores
    each object. Returns what it patched (``module.attr`` / ``module.function(default=)``)."""
    if instant.utcoffset() is None:
        raise ValueError("the pinned instant must be aware")
    held = market_time.market_now
    start = time.monotonic()

    def market_now(tz=None):
        if market_time.datetime is not _REAL_DATETIME:
            return held(tz)  # a test froze the module's own clock: it wins, as it would on that day
        at = instant + timedelta(seconds=time.monotonic() - start)
        return at.astimezone(tz or market_time.market_tz())

    market_now.__doc__ = f"market_now pinned at {instant.isoformat()}, ticking ({ENV})."
    patched: list[str] = []
    seen: set[int] = set()
    for name, module in list(sys.modules.items()):
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        # a default can hold it only where the module could reach it: by name, or through ``market_time``
        reaches = False
        for attr, value in list(namespace.items()):
            if value is held:
                mp.setattr(module, attr, market_now)
                patched.append(f"{name}.{attr}")
                reaches = True
            elif value is market_time:
                reaches = True
        if not reaches:
            continue
        for value in list(namespace.values()):
            for fn in _functions(value, name):
                if id(fn) not in seen:
                    seen.add(id(fn))
                    patched += _pin_defaults(fn, held, market_now, mp)
    return patched


def pin_from_env(environ: Mapping[str, str], mp: pytest.MonkeyPatch) -> datetime | None:
    """The session fixture's body: pin at ``instant_from_env(environ)`` and return it, or patch nothing (``None``)."""
    instant = instant_from_env(environ)
    if instant is not None:
        pin_market_now_everywhere(instant, mp)
    return instant


def _functions(value: object, module_name: str) -> Iterator[types.FunctionType]:
    """The plain functions ``value`` defines in ``module_name``: itself, or a class's methods (static and class
    methods included). A re-export is skipped here; it is visited in the module that defines it."""
    if isinstance(value, types.FunctionType):
        if value.__module__ == module_name:
            yield value
    elif isinstance(value, type) and getattr(value, "__module__", None) == module_name:
        for member in list(vars(value).values()):
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            if isinstance(member, types.FunctionType):
                yield member


def _pin_defaults(
    fn: types.FunctionType, held: Callable, pinned: Callable, mp: pytest.MonkeyPatch
) -> list[str]:
    where = f"{fn.__module__}.{fn.__qualname__}"
    out: list[str] = []
    if fn.__defaults__ and any(v is held for v in fn.__defaults__):
        mp.setattr(fn, "__defaults__", tuple(pinned if v is held else v for v in fn.__defaults__))
        out.append(f"{where}(default=)")
    if fn.__kwdefaults__ and any(v is held for v in fn.__kwdefaults__.values()):
        kw = {k: (pinned if v is held else v) for k, v in fn.__kwdefaults__.items()}
        mp.setattr(fn, "__kwdefaults__", kw)
        out += [f"{where}({k}=)" for k, v in fn.__kwdefaults__.items() if v is pinned]
    return out
