"""THE SETTLE GUARD (D2) — no in-progress session bar is ever stored as a close.

A daily EOD bar dated ``d`` is a CLOSE only once ``d``'s session has closed and the vendor's bar has
settled. Before that, the vendor's "today" bar is the session so far: an Admin "Run daily now" in market
hours, or a boot catch-up after a daytime cold start (one fired at 12:07 ET on 2026-10-14), used to store
it as if it were final — 1,065 partial bars between 2026-07-16 and 08-27, MEASURED on prod, and the
benchmark leg froze two of them as the only version it ever held (SPY 2026-08-25 at 765.27 against a
final 765.91). So every price write (the thesis price leg, the SPY/IWM leg, the Workbench's per-name
pull) filters its fresh bars through ``withhold_unsettled`` first.

**The rule is one comparison:** a bar dated ``d`` is settled iff ``now >= d at the settle time`` in market
time (``Settings.price_settle_time``, default 18:00 — the evidence is on the setting). That single test
covers every case without a trading calendar:

- a PRIOR day's bar (``d < market_today``) is always settled — its settle instant is behind us — so a
  post-midnight catch-up (market_today is the next day, pre-open) still stores the previous session;
- TODAY's bar is held until the settle time, then stored (the 22:30 cron is 4.5 h past it);
- a FUTURE-dated bar is never settled (a vendor timestamp can never make tomorrow's bar a close);
- a HOLIDAY normally has no vendor bar, so there is nothing to withhold; a HALF-DAY bar (13:00 close) is held
  until the same 18:00 — conservative, never a loss.

**A bar dated a day with NO session is never a close (F5, 2026-10-31).** The vendor does print OTC bars on
closed days — MEASURED on dev: 10 bars dated Labor Day 2026-09-07, all OTC names, each a flat carry (open =
high = low, close = the prior close, a token volume). There was no session, so the bar is not a fact about
the market. ``withhold_closed_days`` drops every fresh bar whose date is not a session
(``domain.trading_days.is_scheduled_day``: a weekend or an NYSE full-day closure) from every write path that
follows, right after ``withhold_unsettled`` and at the same three callers. UNLIKE the settle guard it is not a
delay: the bar is never written by any pass. The count rides the leg's receipt
(``BarsResult.closed_day`` / the benchmark leg's ``bars_closed_day``) so the pass prints ONE quiet line.
A bar already stored for a market holiday is left in place (nothing is deleted) and is ignored on READ, in
one place: ``signals.base.window_prices``, the post-process every point-in-time price view shares.

**Withholding never loses a bar.** The incremental tail is "newer than the latest STORED bar", and a
withheld bar is never stored, so the next settled pass appends it as an ordinary new bar. The count is
reported (``BarsResult.withheld`` and the benchmark leg's result) so the pass prints ONE quiet line, never
one per name.

``now`` is a parameter (time is never ambient in a decision): callers pass the UTC instant they are
ingesting at, and it must be timezone-AWARE — a naive datetime is exactly the ambient-timezone bug
``domain/market_time.py`` exists to kill, so it is refused loudly rather than guessed at.
"""

from __future__ import annotations

from datetime import date, datetime, time

from domain.market_time import market_tz
from domain.settings import get_settings
from domain.trading_days import is_scheduled_day


def settle_instant(d: date, *, settle_at: time | None = None) -> datetime:
    """The aware instant at which a bar dated ``d`` becomes a close: ``d`` at the settle time, market tz."""
    at = settle_at or get_settings().price_settle_time
    return datetime.combine(d, at, tzinfo=market_tz())


def is_settled(d: date, *, now: datetime, settle_at: time | None = None) -> bool:
    """Has the session dated ``d`` closed and settled by ``now``? See the module docstring for why this
    one comparison handles prior days, today, future dates, holidays and half-days."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(
            "the settle guard needs a timezone-AWARE `now` (a naive datetime would read the ambient zone)"
        )
    return now >= settle_instant(d, settle_at=settle_at)


def withhold_unsettled(
    rows: list[dict], *, now: datetime, settle_at: time | None = None
) -> tuple[list[dict], int]:
    """Split fresh vendor bars into ``(settled bars, count withheld)``, preserving order. The withheld
    bars are dropped from EVERY write path that follows — the incremental tail AND the restatement
    compare — so an in-progress value can neither be appended nor re-version a stored bar."""
    settled = [r for r in rows if is_settled(r["d"], now=now, settle_at=settle_at)]
    return settled, len(rows) - len(settled)


def withhold_closed_days(rows: list[dict]) -> tuple[list[dict], int]:
    """Split fresh vendor bars into ``(bars dated a session, count dated a day with no session)``, preserving
    order. A bar dated a weekend or an NYSE full-day closure (``is_scheduled_day``) is dropped from every write
    path that follows — never appended, never allowed to re-version a stored bar — and, unlike an unsettled
    bar, no later pass writes it either (see the module docstring). Pure: no clock, the bar's own date only.
    """
    kept = [r for r in rows if is_scheduled_day(r["d"])]
    return kept, len(rows) - len(kept)
