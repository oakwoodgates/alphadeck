"""The request INPUT LIMITS, defined once (security hardening PR 3, docs/AUTH.md §Request limits).

Limits apply to what comes IN on a request and NEVER when stored data is read. The domain models (``Thesis``,
``BasketMember``, ``Segment``, ``ExtractedFact``, ...) are built from database rows and server-side extraction on
every load (the nightly, the replay harness, the backtest, every route), so none of these types may appear on a
domain model's own field: one stored value over a limit would make that card unloadable everywhere. They are used
only on the REQUEST models in ``app/schemas_api.py`` (and the request-side copies of the nested domain models there)
and on query parameters.

The rules every text class shares:

- leading and trailing whitespace is trimmed, and nothing else about the value is altered;
- a NUL or any other control character (Unicode category Cc: U+0000-U+001F, U+007F-U+009F) is refused; the prose
  classes (``Prose``, ``Narrative``, ``Excerpt``) allow tab, newline and carriage return, the one-line classes allow
  none;
- a value over its limit is refused (422 naming the field and the limit), never cut.

Numbers are finite (no NaN, no Infinity) and ranged; a request date sits in a window around today's market date.
Each limit is a named constant here, so raising one is a one-line change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any

from annotated_types import Ge, Gt, Le
from pydantic import AfterValidator, AllowInfNan, GetCoreSchemaHandler, StringConstraints
from pydantic_core import CoreSchema, core_schema

from domain import market_time
from securities.symbols import normalize_ticker

# --- the limits ----------------------------------------------------------------------------------------------------

# text, in characters (after the trim)
LINE_MAX = (
    200  # a one-line label: a name, a link label, a role, a ticker shown as a label, a token name
)
PROSE_MAX = (
    2_000  # short prose: a link descriptor, thesis fit, a kill criterion, a reason, a ratify note
)
NARRATIVE_MAX = 20_000  # the thesis narrative
TERM_MAX = 200  # a search term, a seed, a surfaced term
URL_MAX = 2_000  # a URL or a ``source_ref``
EMAIL_MAX = 254
PASSWORD_MIN = 12  # app.auth.MIN_PASSWORD_LEN: every stored password was set under it
PASSWORD_MAX = 1_024
SEARCH_Q_MAX = 100  # the security search ``q``
CIK_MAX_DIGITS = 10
EXCERPT_MAX = 4_000  # one located passage sent back to the flag explainer

# collections, in items
BASKET_MAX = 3_000  # basket rows per card
SEGMENTS_MAX = 100  # links per card
TERMS_MAX = 500  # terms per card
SEEDS_MAX = 100  # seeds per term run
SURFACED_TERMS_MAX = 200  # surfaced terms per basket row
CATALYSTS_MAX = 500
KILL_CRITERIA_MAX = 200
EXCLUSIONS_MAX = 5_000
IDENTITY_OVERRIDES_MAX = 3_000  # one per basket row at most
EXPLAIN_PASSAGES_MAX = 10
EXPLAIN_FLAGS_MAX = 20

# numbers
MIX_PCT_MIN, MIX_PCT_MAX = 0.0, 100.0
SHARES_MAX = 1e13  # largest stored share count: 6.1e10
ADS_RATIO_MIN, ADS_RATIO_MAX = 1, 1_000_000
MONEY_ABS_MAX = 1e15  # cash and quarterly burn, either sign (largest stored: 2.8e12)
DECISION_AMOUNT_MAX = 1e13  # a decision's shares and price

# dates
REQUEST_DATE_MIN = date(1990, 1, 1)
REQUEST_DATE_AHEAD_DAYS = 366  # after today's market date
ASOF_MIN = date(2000, 1, 1)
ASOF_AHEAD_DAYS = 7

# the triage-session autosave (opaque, never interpreted)
STATE_MAX_BYTES = 25 * 1024 * 1024  # the request body; the nginx ceiling on that location
STATE_MAX_DEPTH = 32


@dataclass(frozen=True)
class Bounded:
    """A marker in a type's metadata: this request type carries its own limit. The guard test
    (``tests/app/test_request_bounds_guard.py``) reads it; pydantic ignores it."""

    kind: str


# --- text ---------------------------------------------------------------------------------------------------------

_CONTROL_ANY = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_BUT_BREAKS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _one_line(value: str) -> str:
    if _CONTROL_ANY.search(value):
        raise ValueError(
            "must not contain a control character (a NUL, a tab, a line break or similar)"
        )
    return value


def _prose(value: str) -> str:
    if _CONTROL_BUT_BREAKS.search(value):
        raise ValueError(
            "must not contain a control character other than a tab or a line break (a NUL or similar)"
        )
    return value


def _line_type(max_length: int, kind: str) -> Any:
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=max_length),
        AfterValidator(_one_line),
        Bounded(kind),
    ]


def _prose_type(max_length: int, kind: str) -> Any:
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=max_length),
        AfterValidator(_prose),
        Bounded(kind),
    ]


Line = _line_type(LINE_MAX, "line")
Term = _line_type(TERM_MAX, "term")
UrlText = _line_type(URL_MAX, "url")
EmailIn = _line_type(EMAIL_MAX, "email")
SearchQuery = _line_type(SEARCH_Q_MAX, "search")
Prose = _prose_type(PROSE_MAX, "prose")
Narrative = _prose_type(NARRATIVE_MAX, "narrative")
Excerpt = _prose_type(EXCERPT_MAX, "excerpt")

# A password is counted as typed: never trimmed (a space is part of it) and never inspected beyond its length (it is
# only ever hashed). The bounds are checked by the request model, so a refused one never reaches the hash.
PasswordIn = Annotated[
    str,
    StringConstraints(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX),
    Bounded("password"),
]


def _cik(value: str) -> str:
    if not (value.isascii() and value.isdigit()):
        raise ValueError(f"a CIK is 1 to {CIK_MAX_DIGITS} digits")
    return value


CikIn = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=CIK_MAX_DIGITS),
    AfterValidator(_cik),
    Bounded("cik"),
]

# A ticker typed into a request that can reach the shared security master (security hardening PR 1a): stripped +
# uppercased, then held to ``securities.symbols`` (1-12 of A-Z 0-9 . -, starting with a letter or digit), else 422
# before the route runs.
TickerIn = Annotated[str, AfterValidator(normalize_ticker), Bounded("ticker")]

# --- numbers ------------------------------------------------------------------------------------------------------

_FINITE = AllowInfNan(False)
FiniteFloat = Annotated[float, _FINITE, Bounded("finite")]
MixPct = Annotated[float, _FINITE, Ge(MIX_PCT_MIN), Le(MIX_PCT_MAX), Bounded("mix_pct")]
Shares = Annotated[float, _FINITE, Gt(0), Le(SHARES_MAX), Bounded("shares")]
Money = Annotated[float, _FINITE, Ge(-MONEY_ABS_MAX), Le(MONEY_ABS_MAX), Bounded("money")]
DecisionAmount = Annotated[
    float, _FINITE, Ge(0), Le(DECISION_AMOUNT_MAX), Bounded("decision_amount")
]
AdsRatio = Annotated[int, Ge(ADS_RATIO_MIN), Le(ADS_RATIO_MAX), Bounded("ads_ratio")]

# --- dates --------------------------------------------------------------------------------------------------------


def _window(lo: date, ahead_days: int) -> Any:
    def check(value: date) -> date:
        hi = market_time.market_today() + timedelta(days=ahead_days)
        if not lo <= value <= hi:
            raise ValueError(
                f"must be on or after {lo.isoformat()} and no later than {hi.isoformat()} "
                f"({ahead_days} days after today's market date)"
            )
        return value

    return check


# A date on a request body: not before 1990-01-01, not more than 366 days after today's market date.
RequestDate = Annotated[
    date, AfterValidator(_window(REQUEST_DATE_MIN, REQUEST_DATE_AHEAD_DAYS)), Bounded("date")
]
_check_asof = _window(ASOF_MIN, ASOF_AHEAD_DAYS)


class AsofDate(date):
    """An ``asof`` (or other date) QUERY parameter on a read: not before 2000-01-01, not more than 7 days after
    today's market date. Earlier than any data the app holds; a cold read per distinct date is what the bound
    protects.

    A class rather than an ``Annotated`` alias because FastAPI keeps an ``Annotated`` type's validators only in the
    ``Annotated[..., Query()]`` form, and the routes declare ``asof: AsofDate = Query(...)`` (so each keeps its own
    description and its parameter order). Validation hands the route a plain ``date``; the schema is a plain
    ``format: date`` string, so the contract does not change."""

    BOUNDED = Bounded("asof")

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        return core_schema.no_info_after_validator_function(_check_asof, core_schema.date_schema())


# --- the opaque blob ----------------------------------------------------------------------------------------------


def json_depth(value: Any, *, stop_after: int | None = None) -> int:
    """The nesting depth of parsed JSON: 1 for a flat object or array, 0 for a scalar. Walks the containers in place
    with an explicit stack (no copy, no recursion); string contents are never inspected. ``stop_after`` returns as
    soon as the depth passes it."""
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        if depth > deepest:
            deepest = depth
            if stop_after is not None and deepest > stop_after:
                return deepest
        stack.extend((c, depth + 1) for c in children if isinstance(c, (dict, list)))
    return deepest


def _shallow_enough(value: dict[str, Any]) -> dict[str, Any]:
    if json_depth(value, stop_after=STATE_MAX_DEPTH) > STATE_MAX_DEPTH:
        raise ValueError(f"nesting is deeper than {STATE_MAX_DEPTH} levels")
    return value


# The triage-session ``state``: never interpreted, its strings never inspected; only its nesting is bounded here
# (its serialized size is bounded by the route on the raw request body).
OpaqueState = Annotated[dict[str, Any], AfterValidator(_shallow_enough), Bounded("opaque")]


__all__ = [
    "ADS_RATIO_MAX",
    "ADS_RATIO_MIN",
    "ASOF_AHEAD_DAYS",
    "ASOF_MIN",
    "BASKET_MAX",
    "CATALYSTS_MAX",
    "CIK_MAX_DIGITS",
    "DECISION_AMOUNT_MAX",
    "EMAIL_MAX",
    "EXCERPT_MAX",
    "EXCLUSIONS_MAX",
    "EXPLAIN_FLAGS_MAX",
    "EXPLAIN_PASSAGES_MAX",
    "IDENTITY_OVERRIDES_MAX",
    "KILL_CRITERIA_MAX",
    "LINE_MAX",
    "MIX_PCT_MAX",
    "MIX_PCT_MIN",
    "MONEY_ABS_MAX",
    "NARRATIVE_MAX",
    "PASSWORD_MAX",
    "PASSWORD_MIN",
    "PROSE_MAX",
    "REQUEST_DATE_AHEAD_DAYS",
    "REQUEST_DATE_MIN",
    "SEARCH_Q_MAX",
    "SEEDS_MAX",
    "SEGMENTS_MAX",
    "SHARES_MAX",
    "STATE_MAX_BYTES",
    "STATE_MAX_DEPTH",
    "SURFACED_TERMS_MAX",
    "TERMS_MAX",
    "TERM_MAX",
    "URL_MAX",
    "AdsRatio",
    "AsofDate",
    "Bounded",
    "CikIn",
    "DecisionAmount",
    "EmailIn",
    "Excerpt",
    "FiniteFloat",
    "Line",
    "MixPct",
    "Money",
    "Narrative",
    "OpaqueState",
    "PasswordIn",
    "Prose",
    "RequestDate",
    "SearchQuery",
    "Shares",
    "Term",
    "TickerIn",
    "UrlText",
    "json_depth",
]
