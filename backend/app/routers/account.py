"""``/account`` — the signed-in user's own settings (the Settings page, docs/AUTH.md §Settings page).

Mounted with ``require_user`` on the router (``app/main.py``), so every route here answers 401 before validation
and the route-gate walk in ``tests/app/test_auth_gate.py`` covers it. Every route acts on the CALLER's own rows
only: there is no user id in any path. The system principal is refused by the gate (``auth.system_may``) and, as
a second line, by each writer here.

* ``PUT /account/profile`` edits ``app_user.display_name`` / ``email`` in place (``auth.update_profile``).
* ``/account/notifications/slack-webhook`` sets, reads (whether set, never the URL), clears and tests the user's
  own Slack webhook (``user_meta.slack_webhook_url``, migration 0079), the destination the nightly pushes the
  user's cards to (docs/FEED_LOOP.md step 3).
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response

from app import auth
from app.auth import CurrentUser, require_user
from app.db_conn import get_conn
from app.schemas_api import MeOut, ProfileUpdate, SlackTestOut, SlackWebhookIn, SlackWebhookOut
from notify import post_text
from repositories import user_meta_repo

router = APIRouter(prefix="/account", tags=["account"])

SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/"
TEST_TIMEOUT_S = 10.0


def _refuse_system(user: CurrentUser) -> None:
    if user.principal == "system":
        raise HTTPException(status_code=403, detail="the system token has no account")


@router.put("/profile", response_model=MeOut)
def update_profile(
    req: ProfileUpdate,
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> MeOut:
    """Change my display name and email. The name must not be empty and the email needs an `@` (400 otherwise);
    an email another user already has, in any case, answers 409. Changing only the case of my own email is
    allowed. My sessions and tokens stay signed in."""
    _refuse_system(user)
    try:
        row = auth.update_profile(conn, user.id, display_name=req.display_name, email=req.email)
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail="that email is already in use") from exc
    conn.commit()
    return MeOut(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        workspace_id=row["workspace_id"],
    )


@router.get("/notifications/slack-webhook", response_model=SlackWebhookOut)
def get_slack_webhook(
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> SlackWebhookOut:
    """Whether I have a Slack webhook set, and when I last saved it. The URL is never returned (it is a
    credential)."""
    meta = user_meta_repo.get(conn, user.id)
    conn.commit()  # end the read transaction
    if meta is None or meta.slack_webhook_url is None:
        return SlackWebhookOut(set=False, updated_at=None)
    return SlackWebhookOut(set=True, updated_at=meta.updated_at)


@router.put("/notifications/slack-webhook", status_code=204)
def set_slack_webhook(
    req: SlackWebhookIn,
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> Response:
    """Save my Slack incoming-webhook URL: armed calls and the nightly digest for my cards are pushed there. It
    must start with `https://hooks.slack.com/` (400 otherwise). Replaces any URL saved before."""
    _refuse_system(user)
    url = req.url.strip()
    if not url.startswith(SLACK_WEBHOOK_PREFIX) or len(url) <= len(SLACK_WEBHOOK_PREFIX):
        raise HTTPException(
            status_code=400, detail=f"a Slack webhook URL starts with {SLACK_WEBHOOK_PREFIX}"
        )
    user_meta_repo.set_slack_webhook(conn, user.id, url)
    conn.commit()
    return Response(status_code=204)


@router.delete("/notifications/slack-webhook", status_code=204)
def clear_slack_webhook(
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> Response:
    """Remove my Slack webhook: my cards' pushes are logged only. A no-op when none is set."""
    _refuse_system(user)
    if user_meta_repo.get(conn, user.id) is not None:
        user_meta_repo.set_slack_webhook(conn, user.id, None)
    conn.commit()
    return Response(status_code=204)


@router.post("/notifications/slack-webhook/test", response_model=SlackTestOut)
def test_slack_webhook(
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> SlackTestOut:
    """Send one plain test line to my saved Slack webhook and report whether Slack accepted it (`delivered`) with
    a short `detail`. 404 when no webhook is saved. A delivery failure is an answer, never an error.
    """
    _refuse_system(user)
    meta = user_meta_repo.get(conn, user.id)
    conn.commit()  # end the read transaction before the outbound POST
    if meta is None or meta.slack_webhook_url is None:
        raise HTTPException(status_code=404, detail="no Slack webhook is saved")
    delivered, detail = post_text(
        meta.slack_webhook_url,
        f"Alpha Deck test message for {user.display_name}",
        timeout=TEST_TIMEOUT_S,
    )
    return SlackTestOut(delivered=delivered, detail=detail)
