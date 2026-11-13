"""``/auth`` — sign in, sign out, who am I, change my password (auth PR 1b, docs/AUTH.md).

Mounted OPEN in ``app/main.py``: ``POST /auth/login`` is one of the two open routes (with ``GET /health``); every
other route here carries ``require_user`` on its own decorator, so the route-gate test sees it.
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app import auth, errors
from app.auth import CurrentUser, require_user
from app.client_address import client_address
from app.db_conn import get_conn
from app.schemas_api import AuthMeOut, ChangePasswordRequest, LoginRequest, MeOut, MeTokenOut
from domain.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_PAIR = "wrong email or password"
_TOO_MANY = "Too many failed sign-ins from your network. Try again in a minute."


def _busy() -> HTTPException:
    """Every password-check slot is taken (``auth.PasswordCheckBusy``): 503, try again shortly. Not a failed
    sign-in: it never counts toward the address's pause."""
    return HTTPException(
        status_code=503,
        detail=errors.SENTENCES["signin_busy"],
        headers={"Retry-After": str(errors.RETRY_AFTER_S)},
    )


def _me(user: CurrentUser) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,  # type: ignore[arg-type]
        workspace_id=user.workspace_id,
    )


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=s.session_idle_days * 86400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
    )


def _clear_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        auth.SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=s.cookie_secure
    )


@router.post("/login", response_model=MeOut)
def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    conn: psycopg.Connection = Depends(get_conn),
) -> MeOut:
    """Sign in with email + password: sets the HttpOnly session cookie and returns the user. 401 on a wrong pair
    (the same answer for an unknown email, a wrong password and a disabled account); 429 after too many failed
    sign-ins from this network address, whatever email was typed, until the short pause ends; 503 "Sign-in is
    busy" when every password-check slot stays taken (try again in a moment)."""
    key = auth.throttle_key(client_address(request.scope))
    if auth.is_locked_out(key):
        raise HTTPException(status_code=429, detail=_TOO_MANY)
    row = auth.find_user_by_email(conn, req.email)
    try:
        if row is None:
            auth._dummy_verify(req.password)
        else:
            ok = auth.verify_password(row["password_hash"], req.password)
    except auth.PasswordCheckBusy:
        raise _busy() from None
    if row is None:
        auth.record_failure(key)
        raise HTTPException(status_code=401, detail=_BAD_PAIR)
    if not ok or row["disabled_at"] is not None:
        auth.record_failure(key)
        raise HTTPException(status_code=401, detail=_BAD_PAIR)
    # a success leaves the address's failure count alone (D3): it ages out on its own
    token = auth.create_session(conn, row["id"])
    conn.commit()
    _set_cookie(response, token)
    return _me(auth._user_from_row(row, "session"))


@router.post("/logout", status_code=204, dependencies=[Depends(require_user)])
def logout(request: Request, conn: psycopg.Connection = Depends(get_conn)) -> Response:
    """Sign out: revokes this browser's session and clears the cookie."""
    cookie = request.cookies.get(auth.SESSION_COOKIE)
    if cookie:
        auth.revoke_session(conn, cookie)
        conn.commit()
    response = Response(status_code=204)
    _clear_cookie(response)
    return response


@router.get("/me", response_model=AuthMeOut, response_model_exclude_none=True)
def me(user: CurrentUser = Depends(require_user)) -> AuthMeOut:
    """The signed-in caller: id, email, display name, role, workspace. A bearer also gets `token`: which kind it is
    and its scopes (an app connected through the OAuth sign-in names its `client_id`). The hosted agent tools
    endpoint reads this to know what the caller may do."""
    token = None
    if user.principal in ("token", "oauth"):
        token = MeTokenOut(
            principal=user.principal,  # type: ignore[arg-type]
            client_id=user.client_id,
            scopes=sorted(user.scopes),
        )
    return AuthMeOut(**_me(user).model_dump(), token=token)


@router.post("/change-password", status_code=204)
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> Response:
    """Change my password: the current one must match and the new one needs at least 12 characters. Every OTHER
    session of mine is signed out; this one stays signed in. 503 "Sign-in is busy" when every password-check slot
    stays taken."""
    if user.principal == "system":
        raise HTTPException(status_code=403, detail="the system token has no password")
    if (
        user.principal == "oauth"
    ):  # the gate refuses it first (auth.oauth_may); this is the second line
        raise HTTPException(status_code=403, detail="a connected app cannot manage your account")
    row = auth.find_user_by_email(conn, user.email)
    try:
        if row is None or not auth.verify_password(row["password_hash"], req.current):
            raise HTTPException(status_code=400, detail="the current password is wrong")
        auth.set_password(conn, user.id, req.new)
    except auth.PasswordCheckBusy:
        raise _busy() from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.revoke_other_sessions(conn, user.id, request.cookies.get(auth.SESSION_COOKIE))
    conn.commit()
    return Response(status_code=204)
