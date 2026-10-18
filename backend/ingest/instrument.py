"""X5 — the INSTRUMENTATION SEAM the low-level clients report into: where a nightly pass spends its time
and its requests. MEASUREMENT ONLY — nothing here changes what is fetched, stored, recorded or paged.

The meter itself (``pipeline/pass_timings.PassTimer``) lives in the pipeline layer; this module holds only
the ``ContextVar`` it is made ACTIVE through and the two hooks a client calls, so ``ingest`` never imports
``pipeline`` (the layering stays one-way). With no active meter — the Workbench, a standalone CLI, the
suite — every hook is one ``ContextVar`` read and returns.

- ``timed(leg)`` wraps a call (or decorates a function): its wall time is that leg's.
- ``note(kind, n)`` adds ``n`` to a counter on the innermost open leg (the EDGAR client's cache hits and
  live fetches, the polite GET's requests per host, the rate limiter's wait).

FAIL-OPEN, the one rule: the hooks wrap the WORK, never the other way round. The meter's ``safe_*`` methods
swallow (and record) their own faults; the wrapped call runs, returns and raises exactly as it would
unwrapped.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# The ACTIVE pass meter (``pipeline.pass_timings.PassTimer``), or None. A ContextVar, not a module global: a
# Workbench request served by another thread of the same process never reports into a running pass.
ACTIVE: ContextVar[Any] = ContextVar("alphadeck_pass_meter", default=None)


def note(kind: str, n: float = 1) -> None:
    """Add ``n`` to counter ``kind`` on the active meter's innermost open leg; a no-op with no meter."""
    meter = ACTIVE.get()
    if meter is not None:
        meter.safe_note(kind, n)


def timed(leg: str) -> Callable[[F], F]:
    """``timed("form4")(fn)(...)`` — or ``@timed("form4")`` on a definition — times each call of ``fn`` as
    ``leg`` on the meter active AT CALL TIME (so a decorated module-level function is timed inside a pass and
    untouched outside one). The call's result and exceptions pass through unchanged."""

    def deco(fn: F) -> F:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            meter = ACTIVE.get()
            if meter is None:
                return fn(*args, **kwargs)
            frame = meter.safe_enter(leg)
            try:
                return fn(*args, **kwargs)
            finally:
                if frame is not None:
                    meter.safe_exit(frame)

        try:
            functools.update_wrapper(wrapper, fn)
        except Exception:  # noqa: BLE001 — cosmetic metadata never costs the call
            pass
        return wrapper  # type: ignore[return-value]

    return deco
