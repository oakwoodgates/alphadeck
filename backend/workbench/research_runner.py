"""Cost-safety for the narrative→chain RESEARCH pass: a single-process IN-FLIGHT guard + a TTL cache around the
(slow, expensive) Opus web-search call.

The first live test of the research drafter cost $8.29 and returned nothing — a 120s timeout fell into a stack of
retries that re-launched the call ~5-9x. The timeout is fixed elsewhere (the dials + proxies); THIS module makes a
re-launch impossible and a re-draft free:

- **In-flight guard** — at most one research pass per thesis at a time. A second concurrent draft for the same
  thesis raises ``ResearchInFlight`` (the endpoint -> HTTP 409). Two correctness properties, both tested:
  - the check-and-claim is **ATOMIC** (ONE lock acquisition), so two threads can't both pass the check before
    either claims and thereby launch two Opus calls;
  - the key is released in a **``finally``**, so a timeout/raise frees it — a failed draft never strands a thesis
    permanently in-flight (a "bricked" thesis that 409s forever).
- **TTL cache** — the research synthesis, keyed by ``(thesis_id, narrative-hash)``, so a re-open / re-draft of the
  SAME narrative doesn't re-spend. ``ttl_s == 0`` disables it (always fresh — the convergence gate-2 mode). Only a
  NON-None result is cached, so a failed/empty research isn't stranded on recall-only for the whole TTL.

Single-process is authoritative: uvicorn runs ONE worker (``Dockerfile`` CMD, no ``--workers``) and the draft
endpoint is a sync ``def`` (Starlette threadpool), so concurrent drafts are THREADS in one process — a module-level
registry + a ``threading.Lock`` is correct. (If ``--workers>1`` is ever added, this needs a shared store.)

The cached text is discovery CONTEXT, not a fact or signal — it never enters the spine, is never scored, and
re-derives after the TTL. It is NOT the read-serving signal/score cache that Option B forbids.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from time import monotonic
from uuid import UUID

_lock = threading.Lock()
_inflight: set[str] = set()  # thesis_ids with a research pass running RIGHT NOW
_cache: dict[tuple[str, str], tuple[float, str]] = (
    {}
)  # (thesis_id, narrative-hash) -> (stored_at, synthesis)


class ResearchInFlight(RuntimeError):
    """A research pass for this thesis is already running — the endpoint maps it to HTTP 409. A double-click or a
    stray retry must NEVER launch a parallel (expensive) Opus call."""


def _narrative_hash(narrative: str) -> str:
    return hashlib.sha256(narrative.strip().encode("utf-8")).hexdigest()


def reset_state() -> None:
    """Clear the in-process registry + cache. For TESTS only (the state persists across tests in one process,
    like ``get_settings``'s singleton) — never called on the request path."""
    with _lock:
        _inflight.clear()
        _cache.clear()


def run_research(
    thesis_id: UUID,
    narrative: str,
    *,
    ttl_s: float,
    run: Callable[[], str | None],
) -> str | None:
    """Run the research pass for ``thesis_id`` behind the in-flight guard + the TTL cache.

    Returns the synthesis (cached or freshly produced by ``run``), or ``None`` if ``run`` produced none. Raises
    ``ResearchInFlight`` if a pass for this thesis is already running. ``run`` (the slow Opus call) executes
    OUTSIDE the lock.
    """
    tid = str(thesis_id)
    ckey = (tid, _narrative_hash(narrative))

    # ATOMIC: the cache-check AND the in-flight check-and-claim happen in ONE lock acquisition — never two — so
    # two concurrent drafts for the same thesis can't both pass the check and launch two Opus calls.
    with _lock:
        if ttl_s > 0:
            hit = _cache.get(ckey)
            if hit is not None and (monotonic() - hit[0]) < ttl_s:
                return hit[1]
        if tid in _inflight:
            raise ResearchInFlight(tid)
        _inflight.add(tid)

    try:
        result = run()  # the slow research call — OUTSIDE the lock
        if (
            ttl_s > 0 and result is not None
        ):  # cache only a real synthesis; never a failed/empty result
            with _lock:
                _cache[ckey] = (monotonic(), result)
        return result
    finally:
        # ALWAYS free the key — even when run() times out or raises — so a failed draft can't leave the thesis
        # permanently in-flight (every future draft would 409 forever).
        with _lock:
            _inflight.discard(tid)
