"""X5 — WHERE A NIGHTLY PASS SPENDS ITS TIME, ITS REQUESTS AND ITS MEMORY: the ``timings`` block on every run
artifact (``pipeline/cron_run_log.py``) and one summary line in the printed report. MEASUREMENT ONLY — it
never changes what is ingested, recorded, paged or scored, and nothing reads it back into the pass.

Why it exists: the cron is about to grow (more names, more theses) and move from a laptop to a server, and
two questions had no answer — where does a pass spend its time and memory, and how does that scale? Before
X5 the artifact carried the pass's start and finish and a per-thesis EDGAR fetch count, nothing per leg.

THE MODEL. One ``PassTimer`` per pass, made ACTIVE for the pass (``activate`` — a ``ContextVar``, the seam in
``ingest/instrument.py``), so the call sites need no new parameter and are a no-op wherever no pass runs:

- ``per_thesis(theses)`` wraps the per-thesis loop's iterable: each thesis is a SCOPE, and every leg timed
  while it is open is that thesis's.
- ``timed(leg)`` wraps a call: per thesis ``form4`` / ``form8k`` / ``schedule13`` / ``price`` /
  ``fund_shares`` (``ingest_thesis``, summed over the thesis's names), ``submissions`` (the EDGAR client's
  submissions-index reads, whichever leg made them), ``fundamentals`` (the Q17 refresh), ``call`` (the
  assemble), ``notify`` (a transition push) and ``record`` (``record_if_changed`` + its commit); per pass
  ``benchmarks``, ``baselines`` (the previous-artifact reads), ``digest`` (the Q13 newly-armed digest: its
  re-arm history read + the push, only on a pass with newly armed names), ``split_scan``, ``rename_probe``,
  ``successor_scan``, ``amendment_sweep`` (R8-B, before the loop), ``amendment_scan`` (R8-A), ``self_audit``,
  ``artifact_write``, ``health_page``,
  ``shell_sweep``, ``spac_radar``.
- ``note(kind, n)`` counts on the innermost open leg: ``sec_live`` / ``sec_cached`` / ``sec_cached_404``
  (the EDGAR client's network decisions — the cron's freeze detector ``edgar_fetches`` counts the same
  ``sec_live`` decisions), ``http:<host>`` / ``http_retry:<host>`` (every attempt of the polite GET: the
  Yahoo price pulls are ``http:query1.finance.yahoo.com`` / ``query2``), ``rate_wait_s`` (seconds slept by a
  proactive rate limiter).

EXCLUSIVE TIME. A leg's seconds are its SELF time: a leg timed inside another (a ``submissions`` read inside
the Form 4 leg) is subtracted from the outer one, so nothing counts twice — a thesis's legs sum to its
``total_s`` and the pass's legs sum to the pass's ``total_s``. Time no leg claimed is ``other``: per thesis
the master reads, the tape-edge / delisting reads, the feed-recency judgment and the prior-call read; at the
pass level everything between the legs.

FAIL-OPEN. Every timer operation runs in its own ``try``: a fault is recorded in ``errors`` (capped) and the
work runs exactly as it would have. Instrumentation never fails a pass or a thesis.

NEAR-ZERO OVERHEAD. Legs only — one ``perf_counter`` pair per leg per name, never per row or per filing; a
counter is a dict increment.

MEMORY. ``peak_rss_kb`` = ``getrusage(RUSAGE_SELF).ru_maxrss`` (KB on Linux) — the PASS process's peak: the
sidecar runs each pass as its own ``python -m pipeline.daily``, and the Admin "Run daily now" runs it in a
spawned child (``pipeline/bounded_pass.py``), so in both cases the process measured IS the pass.
``peak_rss_children_kb`` is ``RUSAGE_CHILDREN`` (the largest reaped child of the pass process; the pass
spawns none today). Each thesis records the peak and the CURRENT RSS (``/proc/self/statm``) at its end, so
growth across the loop is visible. ``resource`` and ``/proc`` do not exist on Windows: those fields are
``null`` there, never a crash.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Iterable, Iterator
from contextvars import Token
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, TypeVar

from ingest.instrument import ACTIVE, note, timed

__all__ = [
    "OTHER",
    "SCHEMA_VERSION",
    "PassTimer",
    "activate",
    "current",
    "current_rss_kb",
    "deactivate",
    "note",
    "peak_rss_kb",
    "per_thesis",
    "snapshot",
    "start",
    "summary_line",
    "timed",
]

_log = logging.getLogger("alphadeck.cron")

T = TypeVar("T")

SCHEMA_VERSION = 1
OTHER = "other"  # time (and counts) no named leg claimed
_MAX_ERRORS = 20


def peak_rss_kb(*, children: bool = False) -> int | None:
    """The process's PEAK resident set (``ru_maxrss``) in KB — ``RUSAGE_CHILDREN`` with ``children`` — or
    ``None`` where ``resource`` does not exist (Windows) or the read fails. Linux reports KB; macOS reports
    bytes, normalized here."""
    try:
        import resource  # POSIX only — absent on Windows

        who = resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
        value = int(resource.getrusage(who).ru_maxrss)
        return value // 1024 if sys.platform == "darwin" else value
    except Exception:  # noqa: BLE001 — a missing measurement is null, never a crash
        return None


def current_rss_kb() -> int | None:
    """The process's CURRENT resident set in KB (``/proc/self/statm`` x the page size — Linux), or ``None``
    elsewhere. Beside the monotone peak it tells a leak (current climbs) from a spike (current falls back).
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") // 1024
    except Exception:  # noqa: BLE001 — a missing measurement is null, never a crash
        return None


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 3)


def _num(v: float) -> float:
    return round(v, 3) if isinstance(v, float) else v


@dataclass(eq=False)
class _Thesis:
    id: str
    name: str
    legs: dict[str, float] = field(default_factory=dict)
    counts: dict[str, dict[str, float]] = field(default_factory=dict)
    total_s: float | None = None
    rss_kb: int | None = None
    peak_rss_kb: int | None = None


@dataclass(eq=False)  # identity, not field equality: two frames are never "the same" frame
class _Frame:
    leg: str
    t0: float
    thesis: _Thesis | None
    child_s: float = 0.0


def _legs_out(legs: dict[str, float]) -> dict[str, float]:
    """Seconds per leg, slowest first (ties by name) — the order an operator reads it in."""
    return {k: round(v, 3) for k, v in sorted(legs.items(), key=lambda kv: (-kv[1], kv[0]))}


def _counts_out(counts: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    return {
        leg: {k: _num(v) for k, v in sorted(kinds.items())}
        for leg, kinds in sorted(counts.items())
        if kinds
    }


def _merge_counts(into: dict[str, dict[str, float]], src: dict[str, dict[str, float]]) -> None:
    for leg, kinds in src.items():
        bucket = into.setdefault(leg, {})
        for k, v in kinds.items():
            bucket[k] = bucket.get(k, 0) + v


class PassTimer:
    """One pass's meter. Its methods without the ``safe_`` prefix may raise; everything the pass calls goes
    through a fail-open wrapper (``safe_enter`` / ``safe_exit`` / ``safe_note`` here, the module functions
    below), which records the fault in ``errors`` and lets the work run untouched."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or perf_counter
        self._stack: list[_Frame] = []
        self._legs: dict[str, float] = {}  # self time of legs run outside any thesis
        self._counts: dict[str, dict[str, float]] = {}
        self._theses: list[_Thesis] = []
        self._thesis: _Thesis | None = None
        self.errors: list[str] = []
        # called with a fresh snapshot each time a thesis ends (the running artifact's checkpoint)
        self.on_thesis_end: Callable[[dict], None] | None = None
        self._t0: float | None = None
        try:
            self._t0 = self._clock()
        except Exception as e:  # noqa: BLE001 — fail-open: the pass total reads null
            self.fault("start", e)

    # --- fault recording -------------------------------------------------------------------------------

    def fault(self, what: str, exc: BaseException) -> None:
        """Record one instrumentation fault (capped at ``_MAX_ERRORS`` lines). Never raises."""
        try:
            if len(self.errors) < _MAX_ERRORS:
                msg = " ".join(str(exc).split())[:160]
                self.errors.append(f"{what}: {type(exc).__name__}{': ' + msg if msg else ''}")
            elif len(self.errors) == _MAX_ERRORS:
                self.errors.append("... further timing errors not recorded")
        except Exception:  # noqa: BLE001 — the error log itself can never raise
            pass

    # --- the frame stack (may raise) -------------------------------------------------------------------

    def enter(self, leg: str) -> _Frame:
        frame = _Frame(leg=leg, t0=self._clock(), thesis=self._thesis)
        self._stack.append(frame)
        return frame

    def exit(self, frame: _Frame) -> float:
        """Close ``frame``: add its SELF time to its leg and its whole elapsed time to the enclosing frame's
        children. Returns the elapsed seconds."""
        t1 = self._clock()
        if frame not in self._stack:
            raise RuntimeError(f"leg {frame.leg!r} closed twice or never opened")
        while self._stack and self._stack[-1] is not frame:
            self._stack.pop()  # a frame a fault left open — dropped, never attributed
        self._stack.pop()
        elapsed = max(t1 - frame.t0, 0.0)
        own = max(elapsed - frame.child_s, 0.0)
        bucket = frame.thesis.legs if frame.thesis is not None else self._legs
        bucket[frame.leg] = bucket.get(frame.leg, 0.0) + own
        if self._stack:
            self._stack[-1].child_s += elapsed
        return elapsed

    def begin_thesis(self, thesis_id: object, name: object) -> _Frame:
        row = _Thesis(id=str(thesis_id), name=str(name))
        self._theses.append(row)
        self._thesis = row
        return self.enter(OTHER)  # the thesis's root frame: its self time is the thesis's `other`

    def end_thesis(self, frame: _Frame | None) -> None:
        row = self._thesis if frame is None else frame.thesis
        self._thesis = None
        if frame is not None:
            elapsed = self.exit(frame)
            if row is not None:
                row.total_s = elapsed
        if row is not None:
            row.rss_kb = current_rss_kb()
            row.peak_rss_kb = peak_rss_kb()

    def note(self, kind: str, n: float = 1) -> None:
        frame = self._stack[-1] if self._stack else None
        if frame is None:
            bucket = self._counts.setdefault(OTHER, {})
        elif frame.thesis is not None:
            bucket = frame.thesis.counts.setdefault(frame.leg, {})
        else:
            bucket = self._counts.setdefault(frame.leg, {})
        bucket[kind] = bucket.get(kind, 0) + n

    def snapshot(self) -> dict:
        """The ``timings`` block as of NOW (see the module docstring for every field)."""
        total: float | None = None
        if self._t0 is not None:
            try:
                total = max(self._clock() - self._t0, 0.0)
            except Exception as e:  # noqa: BLE001 — fail-open: the total reads null
                self.fault("snapshot clock", e)
        legs: dict[str, float] = dict(self._legs)
        counts: dict[str, dict[str, float]] = {}
        _merge_counts(counts, self._counts)
        for row in self._theses:
            for leg, s in row.legs.items():
                legs[leg] = legs.get(leg, 0.0) + s
            _merge_counts(counts, row.counts)
        if total is not None:
            legs[OTHER] = legs.get(OTHER, 0.0) + max(total - sum(legs.values()), 0.0)
        return {
            "v": SCHEMA_VERSION,
            "total_s": _r(total),
            "legs": _legs_out(legs),
            "counts": _counts_out(counts),
            "theses": [
                {
                    "id": row.id,
                    "name": row.name,
                    "total_s": _r(row.total_s),
                    "legs": _legs_out(row.legs),
                    "counts": _counts_out(row.counts),
                    "rss_kb": row.rss_kb,
                    "peak_rss_kb": row.peak_rss_kb,
                }
                for row in self._theses
            ],
            "peak_rss_kb": peak_rss_kb(),
            "peak_rss_children_kb": peak_rss_kb(children=True),
            "rss_kb": current_rss_kb(),
            "errors": list(self.errors),
        }

    # --- the fail-open surface the hooks call ----------------------------------------------------------

    def safe_enter(self, leg: str) -> _Frame | None:
        try:
            return self.enter(leg)
        except Exception as e:  # noqa: BLE001 — fail-open: the leg runs untimed
            self.fault(f"enter {leg}", e)
            return None

    def safe_exit(self, frame: _Frame) -> None:
        try:
            self.exit(frame)
        except Exception as e:  # noqa: BLE001 — fail-open: the leg's result stands
            self.fault(f"exit {getattr(frame, 'leg', '?')}", e)

    def safe_note(self, kind: str, n: float = 1) -> None:
        try:
            self.note(kind, n)
        except Exception as e:  # noqa: BLE001 — fail-open: one count lost
            self.fault(f"note {kind}", e)


# --- the module surface the pass calls (all fail-open) ------------------------------------------------


def start(clock: Callable[[], float] | None = None) -> PassTimer | None:
    """A new meter for one pass, or ``None`` if even that failed (the pass then runs unmeasured)."""
    try:
        return PassTimer(clock)
    except Exception:  # noqa: BLE001 — measurement never costs the pass
        _log.exception("pass timer could not start (fail-open — the pass runs unmeasured)")
        return None


def current() -> PassTimer | None:
    """The meter active in this context, or ``None``."""
    return ACTIVE.get()


def activate(timer: PassTimer | None) -> Token | None:
    """Make ``timer`` the active meter for this context; returns the token ``deactivate`` takes."""
    if timer is None:
        return None
    try:
        return ACTIVE.set(timer)
    except Exception:  # noqa: BLE001
        return None


def deactivate(token: Token | None) -> None:
    """Undo ``activate``. Never raises."""
    if token is None:
        return
    try:
        ACTIVE.reset(token)
    except Exception:  # noqa: BLE001 — a foreign-context token: clear rather than raise
        try:
            ACTIVE.set(None)
        except Exception:  # noqa: BLE001
            pass


def per_thesis(theses: Iterable[T]) -> Iterator[T]:
    """Yield every item of ``theses`` unchanged, each inside its own thesis scope on the active meter (one
    ``id`` + ``name`` per item, read with ``getattr``). The scope closes when the loop asks for the next
    item, leaves (``continue`` / ``break`` / an exception) or ends — and only then is the thesis's peak RSS
    read and the checkpoint hook (``on_thesis_end``) fed. With no active meter it is a plain pass-through.
    """
    timer = ACTIVE.get()
    if timer is None:
        yield from theses
        return
    for item in theses:
        frame: _Frame | None = None
        try:
            frame = timer.begin_thesis(getattr(item, "id", None), getattr(item, "name", None))
        except Exception as e:  # noqa: BLE001 — fail-open: this thesis runs unscoped
            timer.fault("thesis start", e)
        try:
            yield item
        finally:
            try:
                timer.end_thesis(frame)
            except Exception as e:  # noqa: BLE001
                timer.fault("thesis end", e)
            hook = timer.on_thesis_end
            if hook is not None:
                try:
                    hook(timer.snapshot())
                except Exception as e:  # noqa: BLE001 — a checkpoint never costs the pass
                    timer.fault("checkpoint", e)


def snapshot(timer: PassTimer | None = None) -> dict | None:
    """The ``timings`` block of ``timer`` (default: the active meter) as of now, or ``None`` with no meter.
    Never raises: a snapshot that itself fails returns the empty shape carrying the errors."""
    t = timer if timer is not None else ACTIVE.get()
    if t is None:
        return None
    try:
        return t.snapshot()
    except Exception as e:  # noqa: BLE001
        t.fault("snapshot", e)
        return {
            "v": SCHEMA_VERSION,
            "total_s": None,
            "legs": {},
            "counts": {},
            "theses": [],
            "peak_rss_kb": None,
            "peak_rss_children_kb": None,
            "rss_kb": None,
            "errors": list(t.errors),
        }


def _fmt_s(v: Any) -> str:
    return f"{v:.1f} s" if isinstance(v, (int, float)) else "n/a"


def summary_line(timings: Any) -> str | None:
    """The printed report's ONE timings line — the total, the three slowest legs, the slowest thesis and the
    peak RSS — or ``None`` when the pass carried no timings. Lowercase on purpose: information, not news.
    """
    try:
        if not isinstance(timings, dict):
            return None
        legs = timings.get("legs") if isinstance(timings.get("legs"), dict) else {}
        top = sorted(
            ((k, v) for k, v in legs.items() if isinstance(v, (int, float))),
            key=lambda kv: (-kv[1], kv[0]),
        )[:3]
        theses = [
            t
            for t in timings.get("theses") or []
            if isinstance(t, dict) and isinstance(t.get("total_s"), (int, float))
        ]
        slowest = max(theses, key=lambda t: t["total_s"], default=None)
        peak = timings.get("peak_rss_kb")
        parts = [
            f"timings: {_fmt_s(timings.get('total_s'))} total",
            "top legs " + (", ".join(f"{k} {_fmt_s(v)}" for k, v in top) if top else "n/a"),
            (
                f"slowest thesis {slowest.get('name')} {_fmt_s(slowest['total_s'])}"
                if slowest is not None
                else "slowest thesis n/a"
            ),
            f"peak RSS {peak / 1024:.0f} MB" if isinstance(peak, (int, float)) else "peak RSS n/a",
        ]
        errors = timings.get("errors")
        if isinstance(errors, list) and errors:
            parts.append(f"{len(errors)} timing error(s) — see the run artifact")
        return " · ".join(parts)
    except Exception:  # noqa: BLE001 — the line is best-effort
        return None
