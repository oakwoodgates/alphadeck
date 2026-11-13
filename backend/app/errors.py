"""The API's ERROR CONTRACT (security hardening PR 2, docs/AUTH.md §Errors): what a failed request answers.

An error the code did not word itself never reaches a plain user's screen. Every failure answers a FIXED sentence
for its class plus a short REFERENCE id (``Reference 1a2b3c4d.``); the exception, with that id, goes to the log
(logger ``alphadeck.errors``), so a user who quotes the reference can be matched to the traceback. No SQL, column
value, file path or upstream (SEC, vendor, LLM) text is in the body.

A STEWARD (super admin) still sees the underlying error on screen (operator decision 2026-11-12): after the
sentence and the reference comes ``Detail: <ExceptionClass>: <message>``. The caller is known from
``request.state.caller`` (set by ``note_caller`` on every gated router) or passed to ``fail`` by the route; an
unknown caller (the open login route, or a failure before the gate ran) is treated as a plain user.

The global handlers (``install``):

- psycopg ``DataError`` (a NUL character, a number or date out of range) -> 422
- ``UniqueViolation`` (and any other integrity conflict) -> 409; ``CheckViolation`` / ``ForeignKeyViolation`` /
  ``NotNullViolation`` -> 422
- ``ProgramLimitExceeded`` (a value too large for an index row) -> 413
- ``QueryCanceled`` / ``LockNotAvailable`` (a timeout, ``db.session.connect_request``) -> 503 with ``Retry-After``;
  any other ``OperationalError`` (cannot connect, too many clients) -> 503 with ``Retry-After``
- any other exception -> 500
- FastAPI's request-validation 422 keeps its list shape (``loc`` / ``msg`` / ``type``) but never echoes the caller's
  ``input`` (which can be megabytes) or a documentation ``url``.

An explicit mapping in a route (``except ... raise HTTPException``) runs first and keeps winning; these handlers only
see what a route did not handle. A route that wants the same contract for an exception it catches (an upstream
fetch, a file read) raises ``fail(status, sentence, exc, user=...)``.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import CurrentUser, require_user

_log = logging.getLogger("alphadeck.errors")

# Seconds a 503 asks the client to wait before retrying.
RETRY_AFTER_S = 5

# The fixed sentence per class of failure. Each is a complete plain sentence: the frontend shows ``detail`` as is.
SENTENCES: dict[str, str] = {
    "data": (
        "The request has a value the database cannot store (a character, number or date out of range). "
        "Check it and try again."
    ),
    "unique": "That already exists, so it was not saved again.",
    "constraint": "The request breaks one of the data rules, so nothing was saved. Check it and try again.",
    "too_large": "A value in the request is too large to store. Shorten it and try again.",
    "busy": "The database took too long to answer. Try again in a moment.",
    "unavailable": "The database cannot be reached right now. Try again in a moment.",
    "server": "Something went wrong on the server. Try again, and quote the reference if it keeps happening.",
    # A background job's unexpected failure, shown on its poll (workbench/draft_jobs.py, workbench/ingest_jobs.py).
    "draft": "The draft failed on the server. Try again, and quote the reference if it keeps happening.",
    "fetch": "The data fetch failed on the server. Try again, and quote the reference if it keeps happening.",
    # Every password-check slot stayed taken (app.auth.password_check_slot, security PR 5): sign-in and
    # change-password answer 503 with Retry-After. No reference: nothing failed, the server is busy.
    "signin_busy": "Sign-in is busy. Try again in a moment.",
}


# A malformed ALPHADECK_CRON_AT, wherever a route reads the schedule (the value itself: a steward only, and the log).
SCHEDULE_SETTING_MALFORMED = "The server's nightly schedule setting is malformed, so the schedule cannot be read. A steward needs to fix it."


def new_reference() -> str:
    """A short random id (8 hex characters) that ties a response to its log line."""
    return secrets.token_hex(4)


def note_caller(request: Request, user: CurrentUser = Depends(require_user)) -> None:
    """Router dependency: remember the signed-in caller on the request, so a global handler knows whether to show
    the underlying error (a steward) or not (anyone else). It shares ``require_user``'s cached result.
    """
    request.state.caller = user


def _is_steward(user: CurrentUser | None) -> bool:
    return bool(user is not None and user.is_super_admin)


def caller_of(request: Request) -> CurrentUser | None:
    """The caller ``note_caller`` recorded, or None (the open login route, or a failure before the gate)."""
    return getattr(request.state, "caller", None)


def describe(exc: BaseException) -> str:
    """The underlying error as a steward sees it: class and message, on one line."""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def message(sentence: str, ref: str, exc: BaseException | str, *, steward: bool) -> str:
    """The response text: the fixed sentence, the reference, and (for a steward only) the underlying error.
    ``exc`` is the exception, or its description already stored by ``describe`` (a background job keeps the
    description and builds the text when it is polled, once the caller is known)."""
    out = f"{sentence} Reference {ref}."
    if steward:
        out += f" Detail: {describe(exc) if isinstance(exc, BaseException) else exc}"
    return out


def log_failure(
    status: int, sentence: str, ref: str, exc: BaseException, *, detail: str | None = None
) -> None:
    """Log ``exc`` with its traceback under ``ref`` (ERROR for a 5xx, WARNING otherwise). ``detail``, when given,
    is logged after it: the steward-only text the exception itself does not carry."""
    level = logging.ERROR if status >= 500 else logging.WARNING
    _log.log(
        level,
        "%s Reference %s. (%d %s)%s",
        sentence,
        ref,
        status,
        type(exc).__name__,
        f" Detail: {detail}" if detail else "",
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def fail(
    status: int,
    sentence: str,
    exc: BaseException,
    *,
    user: CurrentUser | None = None,
    request: Request | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    """The ONE helper a route uses for a failure it caught: log ``exc`` under a new reference id and return the
    ``HTTPException`` to raise (``raise fail(...) from exc``). The caller is ``user``, else the one recorded on
    ``request``; a steward's detail carries the underlying error after the sentence."""
    caller = user if user is not None else (caller_of(request) if request is not None else None)
    ref = new_reference()
    log_failure(status, sentence, ref, exc)
    return HTTPException(
        status_code=status,
        detail=message(sentence, ref, exc, steward=_is_steward(caller)),
        headers=headers,
    )


def classify(exc: BaseException) -> tuple[int, str]:
    """The (status, sentence key) a database error answers. Order matters: the specific classes are subclasses of
    ``OperationalError`` / ``IntegrityError`` in psycopg."""
    e = psycopg.errors
    if isinstance(exc, e.UniqueViolation):
        return 409, "unique"
    if isinstance(exc, (e.CheckViolation, e.ForeignKeyViolation, e.NotNullViolation)):
        return 422, "constraint"
    if isinstance(exc, psycopg.IntegrityError):
        return 409, "constraint"
    if isinstance(exc, psycopg.DataError):
        return 422, "data"
    if isinstance(exc, e.ProgramLimitExceeded):
        return 413, "too_large"
    if isinstance(exc, (e.QueryCanceled, e.LockNotAvailable)):
        return 503, "busy"
    if isinstance(exc, psycopg.OperationalError):
        return 503, "unavailable"
    return 500, "server"


def _respond(request: Request, exc: BaseException, status: int, key: str) -> JSONResponse:
    sentence = SENTENCES[key]
    ref = new_reference()
    log_failure(status, sentence, ref, exc)
    headers = {"Retry-After": str(RETRY_AFTER_S)} if status == 503 else None
    return JSONResponse(
        status_code=status,
        content={"detail": message(sentence, ref, exc, steward=_is_steward(caller_of(request)))},
        headers=headers,
    )


async def database_error(request: Request, exc: Exception) -> JSONResponse:
    status, key = classify(exc)
    return _respond(request, exc, status, key)


async def server_error(request: Request, exc: Exception) -> JSONResponse:
    """Any exception nothing else handled. Starlette's server-error layer runs this, sends the 500, then re-raises
    so the server's own log still records the crash."""
    return _respond(request, exc, 500, "server")


def _without_input(err: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in err.items() if k not in ("input", "url")}


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422 for a request that does not match its model, minus the echoed ``input``."""
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder([_without_input(e) for e in exc.errors()])},
    )


def install(app: FastAPI) -> None:
    """Register the handlers on ``app``."""
    app.add_exception_handler(RequestValidationError, validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(psycopg.Error, database_error)
    app.add_exception_handler(Exception, server_error)


__all__ = [
    "RETRY_AFTER_S",
    "SCHEDULE_SETTING_MALFORMED",
    "SENTENCES",
    "caller_of",
    "classify",
    "describe",
    "fail",
    "install",
    "log_failure",
    "message",
    "new_reference",
    "note_caller",
]
