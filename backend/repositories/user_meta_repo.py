"""The ``user_meta`` table (migration 0079, docs/AUTH.md §Settings page): per-USER preferences, 1:1 with ``app_user``.

One typed column per setting, NULL = unset, a missing row = every setting unset. Settings state, not a fact: no
``tenant_id`` (keyed on the user), no bitemporal columns, plain upserts. The caller owns the transaction (commits).

The Slack webhook URL is a credential: no reader here returns it to a route; ``get`` hands the API only whether it
is set (``SlackWebhookOut``), and the one reader that returns URLs is the nightly's ``webhooks_by_workspace``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg


@dataclass(frozen=True)
class UserMeta:
    """One user's settings row."""

    slack_webhook_url: str | None
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceDestinations:
    """Where one workspace's pushes go (docs/FEED_LOOP.md step 3): every ENABLED user's webhook, deduplicated and
    sorted, and whether the workspace holds an enabled super admin (the env fallback, ``notify.notifier_for_workspace``).
    """

    urls: tuple[str, ...]
    has_super_admin: bool


def get(conn: psycopg.Connection, user_id: UUID) -> UserMeta | None:
    """The user's settings row, or None when they have never saved one."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT slack_webhook_url, updated_at FROM user_meta WHERE user_id = %s", (user_id,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    return UserMeta(slack_webhook_url=row["slack_webhook_url"], updated_at=row["updated_at"])


def set_slack_webhook(conn: psycopg.Connection, user_id: UUID, url: str | None) -> None:
    """Set (or, with ``None``, clear) the user's Slack webhook: an upsert that stamps ``updated_at``. Clearing
    keeps the row. The caller validates the URL and commits."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_meta (user_id, slack_webhook_url) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET slack_webhook_url = EXCLUDED.slack_webhook_url, "
            "updated_at = now()",
            (user_id, url),
        )


def webhooks_by_workspace(conn: psycopg.Connection) -> dict[UUID, WorkspaceDestinations]:
    """Every ``kind='workspace'`` tenant -> its destinations, in ONE query (the nightly's pass-start read).

    A disabled user adds no URL and no super-admin flag; a user with no ``user_meta`` row adds nothing; a workspace
    with no users still appears (empty URLs, no super admin)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.id, "
            "array_remove(array_agg(DISTINCT m.slack_webhook_url) "
            "FILTER (WHERE u.disabled_at IS NULL), NULL) AS urls, "
            "coalesce(bool_or(u.role = 'super_admin' AND u.disabled_at IS NULL), false) "
            "AS has_super_admin "
            "FROM tenant t "
            "LEFT JOIN app_user u ON u.workspace_id = t.id "
            "LEFT JOIN user_meta m ON m.user_id = u.id "
            "WHERE t.kind = 'workspace' "
            "GROUP BY t.id"
        )
        rows = cur.fetchall()
    return {
        r["id"]: WorkspaceDestinations(
            urls=tuple(sorted(r["urls"] or ())), has_super_admin=bool(r["has_super_admin"])
        )
        for r in rows
    }
