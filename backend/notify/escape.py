"""Text sent to Slack goes through here (security hardening PR 4a).

Slack reads ``<!channel>``, ``<!here>``, ``<@U123>`` and ``<https://x|label>`` as control syntax in a message's
``text``. Its documented escaping is to replace ``&``, ``<`` and ``>`` with ``&amp;``, ``&lt;`` and ``&gt;``;
the text then shows as typed and pings or links nobody.

How the pushes use it. No push template uses angle-bracket markup on purpose (the deliberate formatting is the
line break between a push's lines and literal emoji), so the markup is escaped ONCE, at the chokepoint every
POST passes (``notify.post_text`` and ``notify.page``'s fallback POST, via ``slack_message``), which no future
template can bypass. The line breaks ARE deliberate, so the chokepoint keeps them; each template therefore
flattens every value it interpolates with ``one_line``, so a newline inside a thesis or company name cannot
forge a line of its own. The log notifier flattens its lines the same way (log injection).
"""

from __future__ import annotations

import re
from typing import Any

# A value interpolated into a push (a thesis name, a company name, a term). Generous: a cap, not a format.
MAX_VALUE_LENGTH = 300
# The whole message. Slack truncates near 40,000 characters; a page this long is already a wall.
MAX_MESSAGE_LENGTH = 12_000

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")
_ELLIPSIS = "…"


def _cap(text: str, limit: int | None) -> str:
    if limit is not None and len(text) > limit:
        return text[: limit - 1] + _ELLIPSIS
    return text


def one_line(value: Any, limit: int | None = MAX_VALUE_LENGTH) -> str:
    """``value`` as one line: each run of newlines and other control characters becomes one space, then the
    length is capped (``None`` = no cap). No markup escaping (the chokepoint does that)."""
    text = "" if value is None else str(value)
    return _cap(_CONTROL.sub(" ", text), limit)


def _escape_markup(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_escape(value: Any, limit: int | None = MAX_VALUE_LENGTH) -> str:
    """One value made inert for Slack: one line (``one_line``), capped, then ``&``, ``<``, ``>`` escaped. The
    cap applies before the escaping, so an entity is never cut in half."""
    return _escape_markup(one_line(value, limit))


def slack_message(text: str) -> str:
    """A whole push made inert at the chokepoint: capped to ``MAX_MESSAGE_LENGTH``, then each line escaped with
    ``slack_escape`` (so every other control character is a space), the template's line breaks kept.
    """
    capped = _cap(text, MAX_MESSAGE_LENGTH)
    return "\n".join(slack_escape(line, limit=None) for line in re.split(r"\r?\n", capped))
