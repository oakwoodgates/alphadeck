"""The ``/health`` payload + the boot-visibility guard for the SEC User-Agent.

Split out of ``app.main`` so the logic is a PURE, directly-unit-testable unit (no DB, no ``TestClient``) —
the same discipline as ``workbench.draft_jobs.assert_single_worker``. The route and the app lifespan are
thin callers of the two functions here.

Fail-OPEN by default (the config philosophy, spec §1): a missing ``ALPHADECK_USER_AGENT`` does NOT stop
boot — the live SEC/EDGAR pull is skipped and the rest of the app works. The guard makes the misconfig
VISIBLE at boot (a loud ERROR log + a ``degraded`` flag on ``/health``) instead of surfacing 11 filings
deep at the first EDGAR call — the exact worktree-``.env`` gap this hardens. Opt into fail-CLOSED with
``ALPHADECK_REQUIRE_UA=true`` (a prod-strict assert).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

_log = logging.getLogger("alphadeck.health")


class HealthResponse(BaseModel):
    """The ``/health`` body. ``status`` is ``"ok"`` or ``"degraded"``; ``degraded`` maps each degraded
    subsystem to a short reason (empty when healthy). The compose healthcheck only needs the HTTP 200 —
    ``degraded`` is a body flag an operator or agent can SEE, never a failed check (fail-open)."""

    status: str = "ok"
    degraded: dict[str, str] = Field(default_factory=dict)


def _user_agent_missing(user_agent: str | None) -> bool:
    """True when the SEC User-Agent is unset or blank (whitespace-only counts as missing)."""
    return user_agent is None or not user_agent.strip()


def compute_health(user_agent: str | None) -> HealthResponse:
    """The ``/health`` payload for the given User-Agent. Degraded (but still HTTP 200) when UA is missing."""
    degraded: dict[str, str] = {}
    if _user_agent_missing(user_agent):
        degraded["user_agent"] = "missing"
    return HealthResponse(status="degraded" if degraded else "ok", degraded=degraded)


def enforce_boot_visibility(user_agent: str | None, *, require_ua: bool) -> None:
    """Boot-time UA visibility. A missing/blank UA logs a loud ERROR so the misconfig shows at BOOT, not
    at the first EDGAR call. Default fail-OPEN (returns — the app still boots, the live pull is skipped).
    With ``require_ua`` (``ALPHADECK_REQUIRE_UA=true``) it is fail-CLOSED: raise so uvicorn exits non-zero.
    Pure — no env read here; the caller passes the resolved values, so this is directly testable."""
    if not _user_agent_missing(user_agent):
        return
    _log.error(
        "ALPHADECK_USER_AGENT is missing/empty — live SEC/EDGAR pulls will be SKIPPED (fail-open). "
        "Set it in the root .env (see .env.example). Set ALPHADECK_REQUIRE_UA=true to refuse boot instead."
    )
    if require_ua:
        raise RuntimeError(
            "ALPHADECK_REQUIRE_UA=true and ALPHADECK_USER_AGENT is missing/empty — refusing to boot."
        )
