"""The per-user DAILY ALLOWANCE on user-triggered fetches (security hardening PR 1b, docs/AUTH.md).

A plain user's click may start a live fetch of server-sourced data (SEC, the price vendor, the LLM seams). Each such
path carries ``Depends(charge("<action>"))``: it counts one unit against the caller's allowance for the current
MARKET day (``domain.market_time.market_today``) and answers **429** with a fixed message naming the reset once the
day's ``Settings.allowance_<action>_per_day`` is spent. The allowances are ceilings against a runaway loop, set high
on purpose, not a rationing tool.

- A steward (``super_admin``) is never limited and never counted; nor is the system principal (the cron's own
  read-only warm-up, which has no user row).
- ``0`` = no limit on that action (nothing is counted).
- The count is ONE upsert statement (``INSERT ... ON CONFLICT DO UPDATE ... WHERE count + n <= limit``), so racing
  requests can never pass the limit between them; a refused request counts nothing.
- A request is counted when it is admitted, before its fetch: a fetch that then fails still spent its unit.
- A promote's fast lane spends ``price_pull`` units, one per new name (``consume(..., n=...)``); when they are not
  there, the promote's new names go to the onboarding queue instead (``app.routers.workbench.promote``).

Table ``user_action_count`` (migration 0084): keyed on the user, so it has no tenant scope.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException

from app.auth import CurrentUser, require_user
from app.deps import get_conn
from domain import market_time
from domain.settings import get_settings

Action = Literal["extract", "price_pull", "auto_confirm", "holdings", "chain_draft", "term_run"]

# What one unit of each action is called in the 429 message (plural).
LABELS: dict[str, str] = {
    "extract": "extracts",
    "price_pull": "price pulls",
    "auto_confirm": "auto-confirms",
    "holdings": "holdings reads",
    "chain_draft": "chain drafts",
    "term_run": "term or tier runs",
}

# The fixed start of every 429 this module answers (the frontend shows the whole message as it is).
LIMIT_PREFIX = "Daily allowance reached:"


def limit_for(action: Action) -> int:
    """The per-day limit for ``action`` (``Settings.allowance_<action>_per_day``); 0 = no limit."""
    return max(0, int(getattr(get_settings(), f"allowance_{action}_per_day")))


def market_day() -> date:
    """The day a count belongs to: today's calendar date in market time."""
    return market_time.market_today()


def exempt(user: CurrentUser) -> bool:
    """A steward is unbounded; the system principal is the server's own read-only caller (no user row)."""
    return user.is_super_admin or user.principal == "system"


def over_message(action: Action) -> str:
    """The fixed 429 message: the limit, the action, and when it resets."""
    return (
        f"{LIMIT_PREFIX} {limit_for(action):,} {LABELS[action]} a day. "
        f"It resets at midnight {market_time.market_tz().key} time."
    )


def consume(
    conn: psycopg.Connection,
    user_id: UUID,
    action: Action,
    n: int = 1,
    *,
    day: date | None = None,
) -> bool:
    """Take ``n`` units of ``action`` from ``user_id``'s allowance for ``day`` (default: today's market day) and
    COMMIT, or take nothing and return False when fewer than ``n`` are left. A limit of 0 takes nothing and returns
    True. One statement, so concurrent callers cannot pass the limit between them."""
    limit = limit_for(action)
    if limit == 0 or n <= 0:
        return True
    if n > limit:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_action_count (user_id, action, market_day, count) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (user_id, action, market_day) DO UPDATE "
            "SET count = user_action_count.count + EXCLUDED.count, updated_at = now() "
            "WHERE user_action_count.count + EXCLUDED.count <= %s "
            "RETURNING count",
            (user_id, action, day or market_day(), n, limit),
        )
        taken = cur.fetchone() is not None
    conn.commit()
    return taken


def used(
    conn: psycopg.Connection, user_id: UUID, action: Action, *, day: date | None = None
) -> int:
    """How many units of ``action`` ``user_id`` has taken on ``day`` (default: today's market day)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count FROM user_action_count WHERE user_id = %s AND action = %s AND market_day = %s",
            (user_id, action, day or market_day()),
        )
        row = cur.fetchone()
    return 0 if row is None else int(row["count"])


def charge(action: Action) -> Callable[..., None]:
    """The route dependency for one fetch path: count one unit for a plain user, else 429 with the fixed
    message. Exempt callers pass untouched. The returned callable carries ``allowance_action`` so a test can list
    which route spends which allowance."""

    def _charge(
        user: CurrentUser = Depends(require_user),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> None:
        if exempt(user):
            return
        if not consume(conn, user.id, action):
            raise HTTPException(status_code=429, detail=over_message(action))

    _charge.allowance_action = action  # type: ignore[attr-defined]
    _charge.__name__ = f"charge_{action}"
    return _charge


__all__ = [
    "LABELS",
    "LIMIT_PREFIX",
    "Action",
    "charge",
    "consume",
    "exempt",
    "limit_for",
    "market_day",
    "over_message",
    "used",
]
