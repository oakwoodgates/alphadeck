"""``GET /clock`` — the server's days, so the app's "today" is never the browser's (F1, 2026-10-30).

The SPA's default as-of used to be the browser's local calendar date. At 09:00 Tuesday that recomputed a
"Tuesday" call from Monday's facts — a different call from the one Monday night recorded and alerted on (an
entry trigger's liveness and an arm's lapse both read the as-of). The default as-of is now the CURRENT NIGHT
(``domain.market_time.current_night``: the night the nightly pass last recorded or is recording), served here,
and the serve path reads that night live (``serve_view``). ``market_today`` is the date a decision or a ratified
fact is dated with; ``action_day`` is the day the operator acts on the night's call.

Read-only and cheap: no database, one clock read (``market_clock``) so the three days never disagree.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from domain.market_time import market_clock, market_tz

router = APIRouter(tags=["meta"])


class ClockOut(BaseModel):
    """The server's days, from one clock read in the market's zone."""

    current_night: date = Field(
        description="the night the nightly pass last recorded or is recording: today once the pass time has "
        "passed on a weekday, else the weekday before; the app's default as-of"
    )
    action_day: date = Field(
        description="the first weekday after the current night: the day the operator acts on its call"
    )
    market_today: date = Field(description="today's calendar date in the market's zone")
    market_tz: str = Field(description="the market's zone, e.g. America/New_York")


@router.get("/clock", response_model=ClockOut)
def get_clock() -> ClockOut:
    """The server's current night, action day and today in the market's zone. The app's default as-of is the
    current night, never the browser's date. Reads no data."""
    try:
        c = market_clock()
    except ValueError as exc:  # a malformed ALPHADECK_CRON_AT: a deploy error, loud
        raise HTTPException(
            status_code=500, detail=f"ALPHADECK_CRON_AT is malformed: {exc}"
        ) from exc
    return ClockOut(
        current_night=c.night,
        action_day=c.action_day,
        market_today=c.today,
        market_tz=market_tz().key,
    )
