"""THE WORDS a user reads about a platform value (V2 W5a; the W5 plan, Q2 and section 6).

One module for every user-facing sentence about a share count or cash/burn: the label of each state and reason,
who set a value (a kind, never an email), the annual-figure line, the line for a newer filing awaiting the steward,
and the line for a value awaiting the steward. The user read (``platform_values.user_view`` and
``GET /platform-values/...``) uses it now; the server-written lines (the Filing Stream, the Evidence Ledger, the
scored read's notes: W5d) use it later, so every screen says the same thing.

Pure: no database, no clock (every date is a parameter). It lives under ``platform_values/``, NOT ``domain/``: the
editions fingerprint hashes ``domain/``, and a change of wording is not a rule change. Display only: nothing on the
call path reads it.
"""

from __future__ import annotations

from datetime import date

from domain.platform_values import (
    ANNUAL_COVER_SOURCE,
    FactType,
    Ratifier,
    ValueState,
    ratifier_kind,
)

SetBy = Ratifier  # "machine" | "steward" | "carried"

# Why a value is missing (the state's ``reason`` when it is ``no_value``), in the user's words. A reason not named
# here reads the plain "No value yet" (never its slug).
NO_VALUE_WORDS: dict[str, str] = {
    "not-extracted": "No value yet: the platform has not read this company's filings yet",
    "no-annual-filing": "No value yet: no 10-K, 10-Q, 20-F or 40-F on EDGAR",
    "cover-not-located": "No value yet: the cover count could not be read",
    "no-candidate": "No value yet: the latest filing gave no value of this kind",
    "dismissed": "No value yet: the steward found no usable value in the latest filing",
    "cash-generative": "Runway does not apply: the company generated cash",
    "financials-in-exhibit": "No value yet: the statements were in an exhibit the platform did not read",
    "exhibit-ambiguous": "No value yet: the statements were in more than one exhibit",
    "statements-not-located": "No value yet: the statements were not found in the filing",
    # defensive: a decision stands but the read returns no fact (the steward's page says "Report this")
    "not-shown": "No value yet",
}
NO_VALUE = "No value yet"

_SET_BY_WORDS: dict[SetBy, str] = {
    "machine": "from the filing",
    "steward": "verified by the steward",
    "carried": "verified (carried over from V1)",
}

_STATE_LABELS: dict[tuple[str, SetBy | None], str] = {
    (ValueState.FROM_FILING.value, "machine"): "From the filing",
    (ValueState.VERIFIED.value, "steward"): "Verified by the steward",
    (ValueState.VERIFIED.value, "carried"): "Verified (carried over from V1)",
}

_WHAT: dict[FactType, str] = {"shares_outstanding": "share count", "cash_burn": "cash and burn"}
_WHAT_WAITS: dict[FactType, str] = {
    "shares_outstanding": "Market cap is not shown until a count is verified.",
    "cash_burn": "Runway is not shown until the steward checks it.",
}


def day_words(d: date) -> str:
    """A day as a user reads it: ``Mar 31, 2026`` (no leading zero; the same on every platform)."""
    return f"{d:%b} {d.day}, {d.year}"


def set_by(ratified_by: str | None) -> SetBy:
    """Who set a stored value, as a kind: ``machine`` (``auto`` / NULL), ``steward`` (an email), ``carried`` (a V1
    row, ``operator``). The one vocabulary (``domain.platform_values.ratifier_kind``); never the email itself.
    """
    return ratifier_kind(ratified_by)


def set_by_words(ratified_by: str | None) -> str:
    """``from the filing`` / ``verified by the steward`` / ``verified (carried over from V1)``. Never an email."""
    return _SET_BY_WORDS[set_by(ratified_by)]


def no_value_words(reason: str | None) -> str:
    """Why a value is missing, in words; an unknown or absent reason reads ``No value yet``."""
    return NO_VALUE_WORDS.get(reason, NO_VALUE) if reason else NO_VALUE


def state_label(state: ValueState | str, reason: str | None, set_by: SetBy | None) -> str:
    """The first words of a value's status line: ``From the filing`` / ``Verified by the steward`` / ``Verified
    (carried over from V1)`` / ``Awaiting the steward`` / the no-value reason's words."""
    s = state.value if isinstance(state, ValueState) else str(state)
    if s == ValueState.AWAITING_STEWARD.value:
        return "Awaiting the steward"
    if s == ValueState.NO_VALUE.value:
        return no_value_words(reason)
    label = _STATE_LABELS.get((s, set_by))
    if label is not None:
        return label
    return "Verified" if s == ValueState.VERIFIED.value else "From the filing"


def annual_line(source: str | None, valid_from: date) -> str | None:
    """For an annual foreign filer's cover count (``source`` ``annual-cover``): ``annual figure from the 20-F or 40-F
    cover, as of <day>``, said quietly beside the count (it can be most of a year old). None for any other source.
    """
    if source != ANNUAL_COVER_SOURCE:
        return None
    return f"annual figure from the 20-F or 40-F cover, as of {day_words(valid_from)}"


def pending_line(event_date: date | None) -> str:
    """Beside a shown value when a newer filing's value awaits the steward. Names the filing's date, never its
    figure (approved B1 (b))."""
    if event_date is None:
        return "A newer filing is awaiting the steward"
    return f"A newer filing (dated {day_words(event_date)}) is awaiting the steward"


def awaiting_line(fact_type: FactType, event_date: date | None) -> str:
    """For a value awaiting the steward with nothing shown: what waits and which meter is blank. No figure (B1 (b)):
    the user reads the filing and its located passage instead."""
    when = f" dated {day_words(event_date)}" if event_date is not None else ""
    return (
        f"The steward has not checked the {_WHAT[fact_type]} read from the filing{when}. "
        f"{_WHAT_WAITS[fact_type]}"
    )


__all__ = [
    "ANNUAL_COVER_SOURCE",
    "NO_VALUE",
    "NO_VALUE_WORDS",
    "SetBy",
    "annual_line",
    "awaiting_line",
    "day_words",
    "no_value_words",
    "pending_line",
    "set_by",
    "set_by_words",
    "state_label",
]
