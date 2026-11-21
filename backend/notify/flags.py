"""The steward's Slack message for a user's FLAG on a platform value (V2 W5b; the W5 plan's Q5, decisions B4 and
W-c; ``docs/PLATFORM_VALUES.md`` §Flags).

One message per raised flag (none on a withdrawal, a close or a reopen), sent AFTER the row is committed and after
the user's answer (the route's background task), so Slack never holds a click and never decides it:

    Flag from a user: ACME (Acme Corp), share count
    Current when flagged: 52,113,000 shares, from the filing, dated 2026-09-30
    Note: the cover lists two classes; this looks like class A only
    Open: https://deck.example.com/admin/facts/<security id>

It says "a user": NEVER an email, a user id, a workspace, a card count or a queue rank (the steward reads who on
the company page, decision B4 (a)); a steward's email in ``ratified_by`` is printed as "verified by the steward". A
value awaiting the steward prints no number (decision B1 (b)), and a cash value prints none either (its currency
rule lives on the steward's page). Every interpolated value is flattened with ``one_line`` (the note is cut at its
default cap; the steward reads the whole note on the page), and the chokepoint (``notify.post_text``) escapes
``& < >``, so a note cannot ping a channel, link anywhere or forge a line.

Where: the steward's flag webhook (``ALPHADECK_STEWARD_SLACK_WEBHOOK_URL``), else the env webhook
(``SLACK_WEBHOOK_URL``), else one log line. A 5-second timeout; a failure or an exception is logged without the URL
(a credential) and never raised.
"""

from __future__ import annotations

import logging
from uuid import UUID

import notify
from domain.platform_values import DerivedState, ValueState
from domain.settings import get_settings
from notify.escape import one_line

_log = logging.getLogger("alphadeck.flags")

# Slack must answer within this, or the message is given up (logged); the user's answer was already sent.
FLAG_TIMEOUT_S = 5.0

_VALUE_WORDS = {"shares_outstanding": "share count", "cash_burn": "cash and burn"}
_SET_BY = {
    "machine": "from the filing",
    "steward": "verified by the steward",
    "carried": "verified (carried over from V1)",
}
_REASON_WORDS = {"not-extracted": "not extracted", "dismissed": "dismissed by the steward"}


def _company(ticker: str | None, name: str | None) -> str:
    t, n = one_line(ticker).strip(), one_line(name).strip()
    if t and n:
        return f"{t} ({n})"
    return t or n or "a company"


def _current(value_type: str, st: DerivedState) -> str:
    """What was current when the flag was raised, in words."""
    if st.state in (ValueState.FROM_FILING, ValueState.VERIFIED) and st.value is not None:
        v = st.value
        parts: list[str] = []
        if value_type == "shares_outstanding" and v.get("shares") is not None:
            parts.append(f"{float(v['shares']):,.0f} shares")
        parts.append(_SET_BY.get(st.ratifier or "machine", "from the filing"))
        parts.append(f"dated {v['valid_from']}")
        text = ", ".join(parts)
        if st.pending is not None and st.pending.get("event_date") is not None:
            text += f"; a newer filing dated {st.pending['event_date']} awaits the steward"
        return text
    if st.state is ValueState.AWAITING_STEWARD:
        when = (st.awaiting or {}).get("event_date")
        return f"awaiting the steward (a filing dated {when})" if when else "awaiting the steward"
    reason = st.reason or "not-extracted"
    return f"no value yet ({one_line(_REASON_WORDS.get(reason, reason))})"


def flag_message(
    *,
    security_id: UUID,
    ticker: str | None,
    name: str | None,
    value_type: str,
    state: DerivedState,
    note: str | None,
    origin: str | None,
) -> str:
    """The message for one raised flag (see the module docstring). Plain text; the chokepoint escapes it."""
    lines = [
        f"Flag from a user: {_company(ticker, name)}, {_VALUE_WORDS.get(value_type, one_line(value_type))}",
        f"Current when flagged: {_current(value_type, state)}",
    ]
    if note and note.strip():
        lines.append(f"Note: {one_line(note.strip())}")
    if origin:
        lines.append(f"Open: {origin.rstrip('/')}/admin/facts/{security_id}")
    else:
        lines.append(
            f"Open: Admin > Shared facts > {one_line(ticker).strip() or _company(None, name)}"
        )
    return "\n".join(lines)


def flag_webhook() -> str | None:
    """The steward's flag webhook, else the env webhook, else None."""
    s = get_settings()
    for url in (s.steward_slack_webhook_url, s.slack_webhook_url):
        if url and url.strip():
            return url.strip()
    return None


def post_flag(text: str) -> None:
    """Send one flag message. NEVER raises: no webhook = one log line (without the note); a failure or an exception
    = one warning naming what failed, never the URL."""
    try:
        url = flag_webhook()
        if url is None:
            _log.info(
                "a user raised a flag; no steward or env Slack webhook is set, so it was not posted"
            )
            return
        delivered, detail = notify.post_text(url, text, timeout=FLAG_TIMEOUT_S)
        if not delivered:
            _log.warning("a flag's Slack message was not delivered: %s", one_line(detail))
    except Exception as exc:  # noqa: BLE001 — best-effort delivery: the flag is already stored
        _log.warning("a flag's Slack message failed (%s)", type(exc).__name__)


__all__ = ["FLAG_TIMEOUT_S", "flag_message", "flag_webhook", "post_flag"]
