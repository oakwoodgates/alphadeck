"""``/oauth/consent`` — the user's Allow / Deny for an app's sign-in request (docs/CONNECTORS.md).

``/authorize`` (the MCP SDK's handler over ``app.oauth.provider``) stores a pending consent and sends the browser to
the SPA's consent page, which reads it here and posts the decision here. Mounted with ``require_user`` on the
router (``app/main.py``), and BOTH routes accept the browser SESSION principal only: a bearer (a personal token,
an OAuth token, the system token) can never approve a consent, so no app can approve its own request.

A consent is not bound to a user until it is decided: whoever is signed in decides for THEIR account, and the row
records who. Allow grants exactly the requested scopes this user's role may grant (never ``steward`` to a plain
user). Expired (ten minutes), already decided, or a disabled client: 410 on read; a second decision is 409.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser, require_user
from app.db_conn import get_conn
from app.oauth import store
from app.schemas_api import ConsentDecisionIn, ConsentDecisionOut, ConsentOut, ConsentScopeOut
from domain.settings import get_settings

router = APIRouter(prefix="/oauth/consent", tags=["oauth"])

_GONE = "this sign-in request has expired or was already answered"


def _session_only(user: CurrentUser) -> None:
    if user.principal != "session":
        raise HTTPException(
            status_code=403, detail="only a signed-in browser can answer a sign-in request"
        )


@router.get("/{consent_id}", response_model=ConsentOut)
def get_consent(
    consent_id: UUID,
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> ConsentOut:
    """An app's pending sign-in request, for the consent page: the app's name, whether Alpha Deck ships with it,
    each scope it asked for with whether my account can grant it, and the host Allow or Deny sends me back to.
    Browser session only (403 for any bearer); 404 for an unknown id; 410 when it expired, was answered, or its
    app was disabled."""
    _session_only(user)
    row = store.get_consent(conn, consent_id)
    conn.commit()  # end the read transaction
    if row is None:
        raise HTTPException(status_code=404, detail="no such sign-in request")
    if row["decided_at"] is not None or store.consent_is_gone(row):
        raise HTTPException(status_code=410, detail=_GONE)
    may = store.grantable(user.role)
    return ConsentOut(
        client_name=row["client_name"] or row["client_id"],
        client_known=row["registration"] == "static",
        scopes=[
            ConsentScopeOut(name=s, label=store.SCOPE_LABELS.get(s, s), grantable=s in may)
            for s in row["scopes_requested"]
        ],
        redirect_host=urlsplit(row["redirect_uri"]).hostname or row["redirect_uri"],
        expires_at=row["expires_at"],
    )


@router.post("/{consent_id}", response_model=ConsentDecisionOut)
def decide_consent(
    consent_id: UUID,
    req: ConsentDecisionIn,
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> ConsentDecisionOut:
    """Allow or deny an app's sign-in request; returns where to send the browser (the app's redirect URI with a
    one-time code, or `access_denied`). Allow grants only the scopes my account can grant. Browser session only
    (403 for any bearer); 404 unknown; 409 when it was already answered; 410 when it expired or its app was
    disabled."""
    _session_only(user)
    origin = get_settings().public_origin
    if not origin:  # no authorization server on this install: nothing could have asked
        raise HTTPException(status_code=404, detail="no such sign-in request")
    try:
        redirect_to = store.decide_consent(
            conn,
            consent_id,
            user_id=user.id,
            role=user.role,
            decision=req.decision,
            issuer=store.issuer_for(origin),
        )
    except LookupError:
        conn.rollback()
        raise HTTPException(status_code=404, detail="no such sign-in request") from None
    except store.ConsentDecided:
        conn.rollback()
        raise HTTPException(
            status_code=409, detail="this sign-in request was already answered"
        ) from None
    except store.ConsentGone:
        conn.rollback()
        raise HTTPException(status_code=410, detail=_GONE) from None
    conn.commit()
    return ConsentDecisionOut(redirect_to=redirect_to)
