"""``market_today`` / ``market_now`` — the ONE home for "what day is it, in market time".

**Why this lives in ``domain/``.** The invariant's own words: *the trading day is a domain fact, not an
environment fact* (``INVARIANTS.md`` §6). ``date.today()`` reads the process's ambient timezone, so the
same code answered a different question depending on where it ran — and in production it did: a manual
``pipeline.daily`` in a UTC container after ~20:00 ET derived **tomorrow's** trading day and recorded a
materially different call (``asof`` is a LIVENESS PARAMETER, not a label — ``calls/assembler.py``'s
``entry_signal_is_live`` is inclusive, so a signal whose window closed that day drops out and the derived
countdowns shift). That is a domain answer changing with the deployment, which is what makes this a
``domain/`` concern rather than an ops one — it sits beside the other small shared helper, ``coerce.py``,
for the same reason: one home, so a new module has something to reach for instead of a fresh
``date.today()``. (The count was 9 sites when the gap was written up and 18 by the time it was fixed —
precisely because there was nothing to reach for.)

**What this is NOT — read this before assuming it.** ``market_today()`` is *today in market time*: the
calendar date in the configured exchange timezone, nothing more. It does **NO trading-calendar logic** —
**no weekend skip, no holiday calendar, no half-day handling**. It is **not** "the last trading day", and
on a Saturday it returns Saturday. The Mon-Fri + ``RUN_AT`` schedule math lives in ``pipeline/schedule.py``
and **stays there** (the shell's sleep-loop in ``scripts/daily_cron.sh`` is its other half). Teaching this
helper to skip weekends/holidays would silently change every ``asof``, every bitemporal ``valid_from``, and
every staleness read that calls it — a behavior change, deliberately out of scope. If you need "the last
*trading* day", build that on top; do not reach in here.

**What it is NOT, part two:** transaction time. ``datetime.now(timezone.utc)`` pins ``recorded_at`` (when
we learned a fact) and is correctly UTC — bitemporal record-keeping, not a trading day. Do not route those
through here; they are a different axis (``INVARIANTS.md`` §4).

The zone is ``Settings.market_tz`` (env ``ALPHADECK_MARKET_TZ``, default ``America/New_York``), so the
answer is config, never ambient. The container ``TZ`` pin in ``docker-compose.yml`` remains as
defense-in-depth for anything that still reads a wall clock (logs, the cron sidecar's shell ``sleep``) —
it is no longer the mechanism.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from domain.settings import get_settings


def market_tz() -> ZoneInfo:
    """The configured trading-day zone (``ALPHADECK_MARKET_TZ``; default ``America/New_York``).

    Fails **LOUD**, never a silent UTC fallback — the whole point of this module is that a wrong zone
    shifts real answers invisibly (``parse_run_at``'s discipline: a malformed deploy value is a deploy
    error). The most likely cause on a fresh environment is a missing tz database rather than a typo, so
    the message names it: ``tzdata`` is a declared dependency in ``pyproject.toml`` for exactly this
    (Windows ships no system zoneinfo — ``zoneinfo.TZPATH`` is empty there).
    """
    name = get_settings().market_tz
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:  # includes "no tz database on this system"
        raise RuntimeError(
            f"ALPHADECK_MARKET_TZ={name!r} could not be resolved ({exc}). Check the zone name, and "
            "that the `tzdata` dependency is installed (Windows ships no system tz database — "
            "`pip install -e .` from backend/ brings it in)."
        ) from exc


def market_now(tz: ZoneInfo | None = None) -> datetime:
    """Now, as a **timezone-AWARE** datetime in market time. The wall-clock sibling of ``market_today``.

    Aware on purpose: a naive datetime is exactly the ambient-timezone bug this module exists to kill.
    Callers doing wall-time comparisons should note that ``.time()`` drops the tzinfo (returning a naive
    ``time``), which is what keeps a comparison against a naive ``RUN_AT`` working — see
    ``pipeline/schedule.last_expected_asof``.
    """
    return datetime.now(tz or market_tz())


def market_today(tz: ZoneInfo | None = None) -> date:
    """**Today's calendar date in market time** — the drop-in for ``date.today()`` everywhere the answer
    means a trading day (a liveness ``asof``, a no-lookahead clamp, a bitemporal ``valid_from``, "as of
    when am I judging this filing").

    NOT a trading-calendar read: no weekend skip, no holidays — see the module docstring.
    """
    return market_now(tz).date()
