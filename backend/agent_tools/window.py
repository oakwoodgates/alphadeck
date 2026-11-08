"""The nightly window: 22:15 to 23:30 market time, while the nightly pass writes the record.

The same rule the ``alphadeck-agent`` skill states for its readers ("no prod read 22:15 to 23:30 ET"). Pure: the
time is a parameter. A hosted reply inside the window carries ``"nightly_window": true`` (a note, never a refusal).
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

WINDOW_START = time(22, 15)
WINDOW_END = time(23, 30)


def in_nightly_window(now: datetime, market_tz: str = "America/New_York") -> bool:
    """True when ``now`` (timezone-aware) falls in [22:15, 23:30) in ``market_tz``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(ZoneInfo(market_tz)).time()
    return WINDOW_START <= local < WINDOW_END
