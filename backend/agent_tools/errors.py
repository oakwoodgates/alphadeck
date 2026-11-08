"""The one failure shape every tool reports: ``{"code", "message", "retryable"}`` as compact JSON.

A ``ToolFailure`` IS an SDK ``ToolError`` (the SDK answers it with ``isError: true``); the server unwraps it so the
model reads exactly this JSON, never an exception's text, a path or a payload.
"""

from __future__ import annotations

import json

from mcp.server.mcpserver.exceptions import ToolError

CODES = frozenset(
    {
        "invalid_arguments",
        "unknown_thesis",
        "ambiguous_thesis",
        "unknown_ticker",
        "backend_unavailable",
        "api_timeout",
        "api_error",
        "api_rejected",
        "output_too_large",
        "internal_error",
        "forbidden",
    }
)
RETRYABLE = frozenset({"backend_unavailable", "api_timeout", "api_error"})


def compact(payload: object) -> str:
    """The reply encoding: compact JSON, ASCII only."""
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class ToolFailure(ToolError):
    """An anticipated failure. ``extra`` carries small structured context (candidates for an ambiguous name)."""

    def __init__(self, code: str, message: str, *, extra: dict | None = None) -> None:
        if code not in CODES:
            raise ValueError(f"unknown failure code {code!r}")
        self.code = code
        self.message = message
        self.retryable = code in RETRYABLE
        self.extra = extra or {}
        super().__init__(compact(self.payload()))

    def payload(self) -> dict:
        body: dict = {"code": self.code, "message": self.message, "retryable": self.retryable}
        body.update(self.extra)
        return body
