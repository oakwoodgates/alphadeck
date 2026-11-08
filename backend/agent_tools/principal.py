"""Who is calling the tools: the scopes a request holds, set per request (hosted) or once at start (stdio).

Pure: no I/O, no tenant. A request's DATA scope is the API's business (its login door picks the caller's workspace);
this module only says which TOOLS a caller may list and call. Every tool today is ``read``; ``steward`` is the scope
a later phase's steward tools will need (held by a ``super_admin`` caller).

``CURRENT_PRINCIPAL`` is a ``ContextVar``: each request runs in its own task context, so two callers' requests
interleaved on one server never see each other's principal (tests/agent_tools/test_hosted.py proves it).
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

READ = "read"
STEWARD = "steward"
KNOWN_SCOPES = frozenset({READ, STEWARD})


@dataclass(frozen=True)
class Principal:
    """The caller as the tool registry sees it: its scopes, and who it is (for logs; never a data filter)."""

    scopes: frozenset[str]
    subject: str | None
    client_id: str | None


def scopes_for_role(role: str | None) -> frozenset[str]:
    """``{"read"}`` for every user; plus ``"steward"`` for a ``super_admin``."""
    if role == "super_admin":
        return frozenset({READ, STEWARD})
    return frozenset({READ})


DEFAULT_PRINCIPAL = Principal(frozenset({READ}), None, None)
CURRENT_PRINCIPAL: ContextVar[Principal | None] = ContextVar("alphadeck_principal", default=None)


def current() -> Principal:
    """The principal of the running request, else the plain default."""
    return CURRENT_PRINCIPAL.get() or DEFAULT_PRINCIPAL
