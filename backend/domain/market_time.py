"""``market_today`` / ``market_now`` — the ONE home for "what day is it, in market time".

**Why this lives in ``domain/``.** The invariant's own words: *the trading day is a domain fact, not an
environment fact* (``INVARIANTS.md`` §6). ``date.today()`` reads the process's ambient timezone, so the
same code answered a different question depending on where it ran — and in production it did: a manual
``pipeline.daily`` in a UTC container after ~20:00 ET derived **tomorrow's** trading day and recorded a
materially different call (``asof`` is a LIVENESS PARAMETER, not a label — ``signals/common.py``'s
``entry_signal_is_live`` keeps a trigger only while the first session after the as-of is inside its window, F3,
so a signal whose window has no session left drops out and the derived countdowns shift). That is a domain answer changing with the deployment, which is what makes this a
``domain/`` concern rather than an ops one — it sits beside the other small shared helper, ``coerce.py``,
for the same reason: one home, so a new module has something to reach for instead of a fresh
``date.today()``. (The count was 9 sites when the gap was written up and 18 by the time it was fixed —
precisely because there was nothing to reach for.)

**What this is NOT — read this before assuming it.** ``market_today()`` is *today in market time*: the
calendar date in the configured exchange timezone, nothing more. It does **NO trading-calendar logic** —
**no weekend skip, no holiday calendar, no half-day handling**. It is **not** "the last trading day", and
on a Saturday it returns Saturday. The Mon-Fri + ``RUN_AT`` schedule math lives in ``pipeline/schedule.py``
(the shell's sleep-loop in ``scripts/daily_cron.sh`` is its other half); its pure core —
``is_scheduled_day``, ``parse_run_at``, ``last_expected_asof`` — is the sibling ``domain/trading_days.py``,
built ON TOP, as this paragraph asks, so the call path can share it — and this helper is untouched by it.
Teaching this helper to skip weekends/holidays would silently change every ``asof``, every bitemporal
``valid_from``, and every staleness read that calls it — a behavior change, deliberately out of scope. If you
need "the last *trading* day", build that on top; do not reach in here.

**The current night is built on top (F1, 2026-10-30): ``current_night()``.** The app's "today" for a VIEW is
not ``market_today()`` but the night the nightly pass most recently recorded or is recording:
``last_expected_asof(market_now(), RUN_AT)`` — today once ``RUN_AT`` (``Settings.cron_run_at``, 22:30) has
passed on a scheduled day, else the prior scheduled day (Tuesday 09:00 -> Monday; Tuesday 22:30 -> Tuesday;
Saturday -> Friday). It is the app's default as-of (served by ``GET /clock``), and the serve path's live test
(``serve_view``) is "on or after the current night". The **action day** is the first scheduled day after it
(``action_day``): the day the operator acts on that night's call; a COUNTDOWN to a future date counts from it on a
live view (``countdown_origin``, F2). ``market_today()`` itself is unchanged.

**A night ends at EDGAR's next open (F6, 2026-10-31, rule log ``2026-11-night-ends-at-edgar-open``).** Night
X's history read (``known_at_for_asof``) runs to the last instant before 06:00 market time on the first WEEKDAY
after X (``next_edgar_open``; a Friday night runs to Monday 06:00), not to midnight. It is the SAME line the
late-row rule draws (``pipeline/night_coverage``: a row recorded before it is the night's on-time record), so
one definition serves history views, late rows and the acceptance repair. Weekdays with holidays INCLUDED, on
purpose: EDGAR keeps SEC business days (it is open on Good Friday), not the NYSE's session calendar. EDGAR
accepts filings 06:00-22:00 on weekdays, so the FILINGS a night reads are practically the same set as with a
midnight end, and a stored time past midnight (the 10-23 acceptance drift) no longer moves a filing to the next
night. What does move is everything else stamped before the open: between midnight and 06:00 on a weeknight,
and for a Friday night the whole weekend — a vendor's re-version of a bar, an operator's ratification, basket
edit or logged decision, a first-held instant (MEASURED on a committed real fixture: GAME's Saturday 13:19 ET
re-version of its 08-27 bar now counts for Friday 08-28's view). ``edgar_night`` is the inverse: the night
whose read first holds an instant.

**What it is NOT, part two:** transaction time. ``datetime.now(timezone.utc)`` pins ``recorded_at`` (when
we learned a fact) and is correctly UTC — bitemporal record-keeping, not a trading day. Do not route those
through here; they are a different axis (``INVARIANTS.md`` §4).

**TESTS must use this too when they assert a production date contract** — and this bit on the very PR that
introduced the helper. A test that posts ``decision_date=date.today()`` or seeds a cache keyed on
``date.today()`` is asserting a DIFFERENT contract than the code implements: the route validates against
``market_today()``, so an ambient date reads as *future-dated* (422) and a cache seeded on the ambient day
is a structural miss. Ambient ``date.today()`` is fine in a test only when the test genuinely does not care
which day it is. The trap is that this failure is **time-of-day flaky**: the two clocks agree for most of
the day and diverge only after ~20:00 ET, so CI (which runs UTC) goes green at 10am and red at 9pm on the
same commit. Twelve tests failed exactly this way here, invisible on an ET dev box where the clocks never
diverge. **Verify a change like this with ``TZ=UTC pytest``**, not just the local run.

The zone is ``Settings.market_tz`` (env ``ALPHADECK_MARKET_TZ``, default ``America/New_York``), so the
answer is config, never ambient. The container ``TZ`` pin in ``docker-compose.yml`` remains as
defense-in-depth for anything that still reads a wall clock (logs, the cron sidecar's shell ``sleep``) —
it is no longer the mechanism.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from domain.settings import get_settings
from domain.trading_days import last_expected_asof, next_scheduled_day, parse_run_at

# EDGAR's filing day opens at 06:00 ET (it accepts 06:00-22:00): the end of a night (``next_edgar_open``).
EDGAR_OPEN = time(6, 0)


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


def cron_run_at() -> time:
    """The nightly pass's wall time in market time (``Settings.cron_run_at``, env ``ALPHADECK_CRON_AT`` — the
    one host var the cron sidecar and the backend both read). A malformed value raises ``ValueError``: a
    deploy error, loud, never a silently wrong night (``parse_run_at``'s discipline)."""
    return parse_run_at(get_settings().cron_run_at)


class MarketClock(NamedTuple):
    """One clock read's three days (``market_clock``): ``today`` = ``market_today()``; ``night`` = the current
    night; ``action_day`` = the first scheduled day after it. Always ``night <= today <= action_day``.
    """

    today: date
    night: date
    action_day: date


def action_day(night: date) -> date:
    """The day the operator acts on ``night``'s call: the first scheduled day after it (Monday for a Friday
    night; a market holiday is not a session, ``domain/trading_days``, F5). The same step the call path's liveness
    test takes (F3): a trigger is live on ``night`` only while this day is inside its window."""
    return next_scheduled_day(night)


def market_clock(now: datetime | None = None) -> MarketClock:
    """Today, the current night and the action day from ONE clock read, so the three can never straddle
    ``RUN_AT`` or midnight and disagree. The current night is ``last_expected_asof(now, RUN_AT)``: today once
    ``RUN_AT`` has passed on a scheduled day, else the prior scheduled day (module docstring). ``now`` is
    injectable for tests: an aware instant is read in market time, a naive one as a market wall time (never
    ``astimezone`` on a naive value — that reads the AMBIENT zone); the default is ``market_now()``.
    """
    if now is None:
        now = market_now()
    elif now.tzinfo is not None:
        now = now.astimezone(market_tz())
    night = last_expected_asof(now, cron_run_at())
    return MarketClock(today=now.date(), night=night, action_day=action_day(night))


def current_night(now: datetime | None = None) -> date:
    """The current night (``market_clock(now).night``): the app's default as-of and the serve path's live
    boundary. Tuesday 17:00 -> Monday; Tuesday 22:30 -> Tuesday; Saturday -> Friday."""
    return market_clock(now).night


def next_edgar_open(asof: date, tz: tzinfo | None = None) -> datetime:
    """06:00 market time on the first WEEKDAY after ``asof``: EDGAR's next open, where night ``asof`` ends.

    THE one definition (F6): the end of a night's history read (``known_at_for_asof``) and the end of its
    on-time window (``pipeline/night_coverage``, which re-exports this). Mon-Fri with holidays INCLUDED,
    deliberately not ``domain.trading_days.is_scheduled_day`` (the NYSE session calendar): EDGAR keeps SEC
    business days and accepts filings on Good Friday, so skipping NYSE closures here would stretch a
    Thursday night to Monday and let a Good Friday filing count for Thursday; on a federal holiday EDGAR is
    closed, so ending the night that morning drops nothing. A Friday night (and a Saturday or Sunday as-of)
    ends Monday 06:00. Pure: ``tz`` defaults to the configured market zone (``market_tz``)."""
    d = asof + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, EDGAR_OPEN, tzinfo=tz or market_tz())


def edgar_night(at: datetime, tz: tzinfo | None = None) -> date:
    """The night whose history read first holds the instant ``at``: the latest WEEKDAY ``d`` with ``d`` 06:00
    market time ``<= at`` — the inverse of ``next_edgar_open``, so ``at <= known_at_for_asof(n)`` iff
    ``edgar_night(at) <= n`` (for a ``now`` past both). A time inside EDGAR's hours (06:00-22:00 on a weekday)
    is its own market date; one between midnight and 06:00 belongs to the weekday before it, and a weekend time
    to the Friday. ``at`` must be aware (a naive instant has no market date)."""
    if at.tzinfo is None:
        raise ValueError("edgar_night needs an aware instant")
    local = at.astimezone(tz or market_tz())
    d = local.date()
    if local.time() < EDGAR_OPEN:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def view_day(known_at: datetime) -> date:
    """The market day a read bounded at ``known_at`` is a view OF — the day whose splits are in force for it
    (``domain.splits``: ``on_view_scale`` / ``tape_scale`` ``through=``) and the day a date-clocked row must be
    public by: ``edgar_night(known_at)``.

    Before F6 every reader took ``known_at``'s market DATE, which was the as-of itself for a history view
    (capped at midnight). Since F6 a night's cap runs past midnight (Friday's to Monday 05:59), so the date of
    ``known_at`` is the NEXT weekday, and a Friday view would have applied a split effective Monday — a
    lookahead. ``edgar_night`` maps the cap back to its own night (and a live ``now`` to tonight's: the same
    date in EDGAR's hours, the previous weekday between midnight and 06:00, Friday on a weekend — the same
    split reading, since a split's first session is a weekday)."""
    return edgar_night(known_at)


def day_clock_public_by(public: datetime, known_at: datetime) -> bool:
    """Is a DATE-clocked row public by a read bounded at ``known_at``? "Has that DAY's night begun" — its date
    ``<= view_day(known_at)``. A date-only public instant is now 06:00 of its date (``db.public_clock
    ._start_of_day``), so on a current store the bare compare ``public <= known_at`` already agrees; this gate
    is what keeps a mirror EXPORTED before that change (its split rows at 00:00, which F6's cap — to 06:00 on
    the next weekday — would reach a night early) honest too. A fact table needs none of this (its ``valid_from
    <= asof`` half guards the day); this is for a date-clocked read with no valid-time gate (the split list).
    """
    return public.astimezone(market_tz()).date() <= view_day(known_at)


def known_at_for_asof(asof: date, now: datetime | None = None) -> datetime:
    """The transaction-axis cap for a scrubbed-back read: ``min(now, the end of night asof)``, as UTC — the
    night ending at the last instant before ``next_edgar_open(asof)`` (06:00 market time on the next weekday).

    The two-axis no-lookahead a forward reader owes (invariant #1 / ``INVARIANTS.md`` §4). The as-of gate
    is ``valid_from <= asof AND recorded_at <= known_at``, and with ``known_at = now`` the second half never
    bites for a past ``asof`` — a scrub-back reads TODAY's knowledge of that date (the serve-path leak:
    TPCS bars dated August, ingested 09-01, arming an 08-25 recompute the record had logged as watching).
    Capping ``known_at`` at the night's end hides every fact that became knowable after that night, not just
    every fact dated after it. Shared by the Scoreboard drawer's dated overlays, the editions engine and the
    replay harness, and, via ``serve_known_at``, the Board/Cockpit/Workbench recomputes — one definition of
    "what was knowable by the end of that night".

    **The night ends at EDGAR's next open (F6, 2026-10-31, rule log ``2026-11-night-ends-at-edgar-open``).**
    Until F6 it ended at midnight market time. The late-row rule already ended a night at the next weekday's
    06:00 (``pipeline/night_coverage.on_time_window``: a row recorded before it is the night's on-time record),
    so the two now agree. EDGAR accepts filings 06:00-22:00, so between midnight and 06:00 almost nothing
    becomes public; what moves is a stored time past midnight (the 10-23 SEC acceptance drift stored filings
    +4/+5 h late, across midnight: it no longer moves them to the next night), an operator write or a pass
    that ran past midnight, and, for a Friday night, everything up to Monday 06:00. The cap is the last
    microsecond BEFORE the open (the gate is ``<=``), so an instant at exactly 06:00 is the next night's, the
    late rule's half-open window. **The date-only trap is shut twice:** a date-clocked fact's public instant
    is 06:00 market time of its date (``db.public_clock._start_of_day`` — the first instant of that date's
    night, never inside the night before), and every date-clocked fact table also sets ``valid_from`` to that
    public date, so the read's other half, ``valid_from <= asof``, keeps a Tuesday 8-K out of Monday's night
    whatever the cap.

    **The night ends in MARKET time, not UTC** — this module's whole point. The daily cron runs at ``RUN_AT``
    22:30 ET with ``asof = market_today()`` and ``known_at = now``, so the record for day X was computed at
    X 22:30 ET = X+1 02:30 UTC. A UTC cap sits BEFORE the cron's own run and blinds a past-asof recompute to
    the night's ingest (day X's bar, that evening's Form 4s) that the record for X saw — a systematic
    one-bar-short disagreement with the record, on every past date. The cap is >= every on-time nightly run's
    ``known_at`` for that asof, so the recompute's data view is a superset of the record's, never a subset.
    It also makes the live identity exact: for ``asof == market_today()`` the cap is always in the future,
    so ``min(now, cap) == now`` at every hour. Since the public-clock rule (2026-10-21) every HISTORY view —
    the serve recomputes, the drawer, ``pipeline.backfill``'s missed-night reconstruction — bounds the
    PUBLIC instant of each fact by this same cap (``serve_view``): "what was public by the end of that
    night". (The backfill's old record-clock ``next-run`` pin is retired.)

    ``now`` is injectable for tests; the default is the UTC instant (transaction time, correctly UTC — #4).
    Returns an aware UTC datetime either way.
    """
    now = now or datetime.now(timezone.utc)
    night_end = next_edgar_open(asof) - timedelta(microseconds=1)
    return min(now, night_end.astimezone(timezone.utc))


def _live_boundary(today: date | None, night: date | None) -> date:
    """The first as-of a serve read treats as LIVE: the injected ``night``, else an injected ``today`` (a test
    that pins only the day pins the boundary to it — the pre-F1 rule, ``asof >= market_today()``), else the
    current night from one clock read. Since ``current_night() <= market_today()`` always, every as-of the old
    rule called live is still live."""
    if night is not None:
        return night
    if today is not None:
        return today
    return current_night()


def serve_known_at(
    asof: date, today: date | None = None, *, night: date | None = None
) -> datetime | None:
    """The ``known_at`` a SERVE-path recompute (``/call``, ``/display-signals``, ``/scored``) threads into
    its ``PointInTimeData`` for ``asof``: ``None`` for a live view, the night's cap (``known_at_for_asof``)
    for a scrubbed-back one.

    **Live = on or after the current night (F1, 2026-10-30).** The app's default as-of is the current night
    (``current_night``: the night the nightly pass last recorded, or is recording), so at 09:00 Tuesday the
    default view is Monday's night, read LIVE — the call the night's record and its alert showed — not a
    public-clock recompute of a day that has not finished its pass. Before F1 the test was
    ``asof >= market_today()``; every as-of that rule called live is still live (``current_night() <=
    market_today()``), so the read for ``asof >= market_today()`` is byte-identical. Only an as-of in
    ``[current night, today)`` moves: from a history view to the live read.

    ``None`` — not ``now`` — for a live view, on purpose: the live path stays the exact code it was. ``None``
    lets the PIT default to the host's UTC instant for the fact reads (unchanged) and lets ``decisions_repo``
    bound the operator log with the DATABASE clock (``clock_timestamp()``) — the one-clock rule that closed
    the two-clock flake (a take appended a millisecond ago reading as not-yet-recorded when the DB clock ran
    fractionally ahead of the host). Passing a host-clock ``now`` here would reopen it. A future ``asof`` is a
    live read too (nothing recorded after now exists to hide).

    A past ``asof`` (before the current night) gets ``known_at_for_asof(asof)`` — the cap that makes the
    scrub-back honest to the as-of. ``night`` (the live boundary) and ``today`` (the pre-F1 boundary, kept as
    an injection seam) are for tests; the default is one ``current_night()`` read (``_live_boundary``).

    **Which axis that cap bounds is ``serve_view``'s half** (operator decision 2026-10-21): a past view reads
    on the PUBLIC clock — a fact counts from when it became public, not from when our database ingested it.
    """
    if asof >= _live_boundary(today, night):
        return None
    return known_at_for_asof(asof)


def serve_view(
    asof: date, today: date | None = None, *, night: date | None = None
) -> tuple[datetime | None, Literal["record", "public"]]:
    """``(known_at, clock)`` for a HISTORY-capable read at ``asof`` — the ONE place a serve path (and the
    other history views: ``pipeline.run`` for a past asof) decides both halves together, so they can never
    disagree (one clock read, never two straddling ``RUN_AT`` or midnight).

    - live (``asof >=`` the current night, F1): ``(None, "record")`` — the unchanged live read
      (``serve_known_at``). The current night itself is live, so the app's default view is never a recompute.
    - past (before the current night): ``(known_at_for_asof(asof), "public")`` — a HISTORY view, judged on the
      PUBLIC clock (operator decision 2026-10-21, ``docs/INVARIANTS.md`` §4): each fact version counts from
      when it became public (``db.public_clock``) up to the end of that night (06:00 on the next weekday,
      F6) — so a scrub-back shows how today's rules judge what was public then, not which rows our database
      happened to hold (our outages, our onboarding order). The roster and the position (operator
      decisions) stay as of that same instant.
    """
    known_at = serve_known_at(asof, today, night=night)
    return known_at, ("record" if known_at is None else "public")


def countdown_origin(asof: date, clock: MarketClock | None = None) -> date:
    """The day a COUNTDOWN to a future date counts from (F2, 2026-10-30): the action day on a live view, else the
    as-of itself.

    The as-of is the night, for the data; the action day (the first scheduled day after it) is the day the operator
    acts on that night's call, so it is the day the WORDS count from. A catalyst dated Tuesday reads "today" on
    Tuesday morning (the view of Monday's night), and one dated Monday reads "passed". Without this, F1 alone would
    shift every countdown by one: Tuesday's catalyst would read "in 1 day" on the Tuesday morning it happens.

    **Live = on or after the current night** (the same test as ``serve_view``): the origin is ``clock.action_day``.
    A past view (before the current night) keeps its as-of, unchanged. A date equal to the origin reads "today"; a
    date before it reads "passed" (the caller's words: "lapsed" for an entry window).

    **Countdowns only.** The AGE of a past event (a filing's "Nd", "N days before the as-of", the days a card has
    sat in a state, ``days_after_night``, "filed N days after the trade") describes the record and still counts from
    the night. ``sessions_left`` is the call path's own count (sessions after the night) and is not re-based.

    ``clock`` is the request's one ``market_clock()`` read (the same one ``serve_view`` got its night from, so the
    two cannot straddle ``RUN_AT``); ``None`` reads the clock here."""
    c = clock or market_clock()
    return c.action_day if asof >= c.night else asof


def serve_decisions_asof(asof: date, clock: MarketClock | None = None) -> date:
    """The valid-time bound a SERVE-path CALL reads the operator's decisions log at (``decision_date <=`` it):
    the action day for a live view of a night before today, else ``asof`` itself.

    Why (F1): the default view is the current night, but the operator acts on the ACTION day — a fill logged
    this morning is dated today. Read at the night, it would stay invisible to the Board until the 22:30 pass
    (the card reading Armed all day after the entry) while the thesis-detail route, which reads the position
    at ``market_today()``, already shows it. So a live view of a night before today reads decisions up to the
    action day. DECISIONS ONLY: the market facts (and an operator-ratified fact, which is a market fact) still
    follow the night. Unchanged everywhere else: a past view (before the current night) reads at its as-of,
    and so does an as-of on or after today — byte-identical to before (the action day is never before today,
    and no decision can be dated after today). The nightly record path never calls this.

    ``clock`` is the request's one ``market_clock()`` read (pass the same one ``serve_view`` got its night
    from, so the two halves cannot straddle ``RUN_AT``); ``None`` reads the clock here."""
    c = clock or market_clock()
    if c.night <= asof < c.today:
        return c.action_day
    return asof
