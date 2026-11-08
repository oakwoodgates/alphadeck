"""``/account`` — the signed-in user's own settings (the Settings page, docs/AUTH.md §Settings page).

Mounted with ``require_user`` on the router (``app/main.py``), so every route here answers 401 before validation
and the route-gate walk in ``tests/app/test_auth_gate.py`` covers it. Every route acts on the CALLER's own rows
only: there is no user id in any path. The system principal is refused by the gate (``auth.system_may``) and, as
a second line, by each writer here.

* ``PUT /account/profile`` edits ``app_user.display_name`` / ``email`` in place (``auth.update_profile``).
* ``/account/notifications/slack-webhook`` sets, reads (whether set, never the URL), clears and tests the user's
  own Slack webhook (``user_meta.slack_webhook_url``, migration 0079), the destination the nightly pushes the
  user's cards to (docs/FEED_LOOP.md step 3).
* ``GET /account/agents`` says how an AI client reaches the caller's workspace (the hosted connector URL when
  ``ALPHADECK_PUBLIC_ORIGIN`` is set); ``/account/tokens`` lists, creates (the token shown ONCE) and revokes the
  caller's own personal access tokens (docs/AGENT_TOOLS.md §Hosted endpoint). Another user's token is 404.
"""

from __future__ import annotations

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response

from app import auth
from app.auth import CurrentUser, require_user
from app.db_conn import get_conn
from app.schemas_api import (
    AgentsOut,
    MeOut,
    ProfileUpdate,
    SlackTestOut,
    SlackWebhookIn,
    SlackWebhookOut,
    TokenCreatedOut,
    TokenCreateIn,
    TokenListOut,
    TokenOut,
)
from domain.settings import get_settings
from notify import post_text
from repositories import user_meta_repo

router = APIRouter(prefix="/account", tags=["account"])

SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/"
TEST_TIMEOUT_S = 10.0
MAX_TOKEN_NAME = 60
MAX_LIVE_TOKENS = 20


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


@router.get("/agents", response_model=AgentsOut)
def get_agents(user: CurrentUser = Depends(require_user)) -> AgentsOut:
    """How an AI client reaches my workspace here: whether this install has a public address and, if so, the
    connector URL to give a client (the agent tools server signs in with my own access token)."""
    _refuse_system(user)
    origin = get_settings().public_origin
    url = None
    if origin:
        from agent_tools.hosted import (
            connector_url,  # only when hosted: the mount imported it already
        )

        url = connector_url(origin)
    return AgentsOut(
        hosted=bool(origin), connector_url=url, steward=user.is_super_admin, stdio_server=True
    )


def _token_out(row: dict) -> TokenOut:
    return TokenOut(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


@router.get("/tokens", response_model=TokenListOut)
def list_tokens(
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> TokenListOut:
    """My access tokens (never the token itself): live ones first, then revoked ones, newest first."""
    _refuse_system(user)
    rows = auth.list_api_tokens(conn, user.id)
    conn.commit()  # end the read transaction
    return TokenListOut(tokens=[_token_out(r) for r in rows])


@router.post("/tokens", response_model=TokenCreatedOut, status_code=201)
def create_token(
    req: TokenCreateIn,
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> TokenCreatedOut:
    """Create a named access token for an AI client or a script. The token is in this reply ONLY, never again.
    The name is trimmed and must be 1 to 60 characters (400 otherwise); at most 20 live tokens per user (409).
    """
    _refuse_system(user)
    name = req.name.strip()
    if not 1 <= len(name) <= MAX_TOKEN_NAME:
        raise HTTPException(
            status_code=400, detail=f"a token name needs 1 to {MAX_TOKEN_NAME} characters"
        )
    if auth.count_live_api_tokens(conn, user.id) >= MAX_LIVE_TOKENS:
        conn.rollback()
        raise HTTPException(status_code=409, detail="too many tokens; revoke one first")
    token_id, token = auth.create_api_token(conn, user.id, name)
    row = auth.get_api_token(conn, token_id)
    conn.commit()
    return TokenCreatedOut(id=token_id, name=name, created_at=row["created_at"], token=token)


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: UUID,
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> Response:
    """Revoke one of my access tokens: it stops working on its next request. Revoking a revoked token is a no-op
    (204). A token that is not mine, or no token at all, answers 404 alike."""
    _refuse_system(user)
    row = auth.get_api_token(conn, token_id)
    if row is None or row["user_id"] != user.id:
        conn.rollback()
        raise HTTPException(status_code=404, detail="no such token")
    auth.revoke_api_token(conn, token_id)
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
