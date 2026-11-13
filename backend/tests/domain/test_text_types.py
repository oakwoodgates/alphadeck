"""The request-side bounded types (security hardening PR 3, ``domain/text_types.py``).

Each class of field is checked at its limit (accepted) and one past it (refused), plus the text rules: leading and
trailing whitespace is trimmed and nothing else is altered; a NUL or any other control character is refused; the
prose classes allow tab, newline and carriage return. Numbers are finite and ranged; request dates sit in a window
around today's market date.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from domain import text_types as tt
from domain.market_time import market_today


def _ok(tp: Any, value: Any) -> Any:
    return TypeAdapter(tp).validate_python(value)


def _refused(tp: Any, value: Any) -> str:
    with pytest.raises(ValidationError) as exc:
        TypeAdapter(tp).validate_python(value)
    return str(exc.value)


# --- text ---------------------------------------------------------------------------------------------------------

TEXT_CLASSES = [
    (tt.Line, tt.LINE_MAX),
    (tt.Prose, tt.PROSE_MAX),
    (tt.Narrative, tt.NARRATIVE_MAX),
    (tt.Term, tt.TERM_MAX),
    (tt.UrlText, tt.URL_MAX),
    (tt.EmailIn, tt.EMAIL_MAX),
    (tt.SearchQuery, tt.SEARCH_Q_MAX),
    (tt.Excerpt, tt.EXCERPT_MAX),
]


@pytest.mark.parametrize(("tp", "limit"), TEXT_CLASSES)
def test_text_at_its_limit_is_accepted_and_one_past_is_refused(tp, limit):
    assert _ok(tp, "a" * limit) == "a" * limit
    msg = _refused(tp, "a" * (limit + 1))
    assert f"at most {limit} characters" in msg


@pytest.mark.parametrize(("tp", "limit"), TEXT_CLASSES)
def test_text_is_trimmed_and_otherwise_unaltered(tp, limit):
    # the limit is counted after the trim: surrounding whitespace never pushes a value over
    assert _ok(tp, "  " + "a" * limit + " \n") == "a" * limit
    assert _ok(tp, "  Mixed Case é ✓  ") == "Mixed Case é ✓"


@pytest.mark.parametrize(("tp", "_limit"), TEXT_CLASSES)
@pytest.mark.parametrize("bad", ["a\x00b", "a\x01b", "a\x1b[31mb", "a\x7fb", "a\x85b", "a\x9fb"])
def test_text_refuses_nul_and_control_characters(tp, _limit, bad):
    assert "control character" in _refused(tp, bad)


@pytest.mark.parametrize("tp", [tt.Line, tt.Term, tt.UrlText, tt.EmailIn, tt.SearchQuery])
@pytest.mark.parametrize("brk", ["\t", "\n", "\r"])
def test_one_line_classes_refuse_line_breaks_and_tabs_inside(tp, brk):
    assert "control character" in _refused(tp, f"a{brk}b")


@pytest.mark.parametrize("tp", [tt.Prose, tt.Narrative, tt.Excerpt])
def test_prose_classes_allow_tab_newline_and_carriage_return(tp):
    assert _ok(tp, "line one\r\n\tline two") == "line one\r\n\tline two"


def test_password_is_counted_as_typed_and_never_trimmed():
    assert _ok(tt.PasswordIn, "  twelve chars ") == "  twelve chars "
    assert _ok(tt.PasswordIn, "x" * tt.PASSWORD_MAX) == "x" * tt.PASSWORD_MAX
    assert "at most 1024 characters" in _refused(tt.PasswordIn, "x" * (tt.PASSWORD_MAX + 1))
    assert "at least 12 characters" in _refused(tt.PasswordIn, "x" * (tt.PASSWORD_MIN - 1))


def test_cik_is_digits_only_and_at_most_ten():
    assert _ok(tt.CikIn, " 0000320193 ") == "0000320193"
    assert _ok(tt.CikIn, "320193") == "320193"
    for bad in ("00003201931", "32O193", "", "-1", "1 2"):
        _refused(tt.CikIn, bad)


def test_ticker_keeps_pr1a_shape_and_is_re_exported():
    from app.schemas_api import TickerIn as FromSchemas

    assert FromSchemas is tt.TickerIn
    assert _ok(tt.TickerIn, " brk-b ") == "BRK-B"
    _refused(tt.TickerIn, "A" * 13)
    _refused(tt.TickerIn, "../etc")


# --- numbers ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tp",
    [tt.FiniteFloat, tt.MixPct, tt.Shares, tt.Money, tt.DecisionAmount],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_every_number_is_finite(tp, bad):
    assert "finite" in _refused(tp, bad)


@pytest.mark.parametrize(
    ("tp", "lo_ok", "hi_ok", "lo_bad", "hi_bad"),
    [
        (tt.MixPct, 0, 100, -0.01, 100.01),
        (tt.Shares, 0.5, 1e13, 0, 1.0001e13),
        (tt.Money, -1e15, 1e15, -1.0001e15, 1.0001e15),
        (tt.DecisionAmount, 0, 1e13, -0.01, 1.0001e13),
        (tt.AdsRatio, 1, 1_000_000, 0, 1_000_001),
    ],
)
def test_number_ranges(tp, lo_ok, hi_ok, lo_bad, hi_bad):
    assert _ok(tp, lo_ok) == lo_ok
    assert _ok(tp, hi_ok) == hi_ok
    _refused(tp, lo_bad)
    _refused(tp, hi_bad)


# --- dates --------------------------------------------------------------------------------------------------------


def test_request_date_window():
    today = market_today()
    assert _ok(tt.RequestDate, date(1990, 1, 1)) == date(1990, 1, 1)
    assert _ok(tt.RequestDate, today + timedelta(days=366)) == today + timedelta(days=366)
    assert "1990-01-01" in _refused(tt.RequestDate, date(1989, 12, 31))
    _refused(tt.RequestDate, today + timedelta(days=367))
    _refused(tt.RequestDate, "0001-01-01")
    _refused(tt.RequestDate, "9999-12-31")


def test_asof_window():
    today = market_today()
    assert _ok(tt.AsofDate, date(2000, 1, 1)) == date(2000, 1, 1)
    assert _ok(tt.AsofDate, today + timedelta(days=7)) == today + timedelta(days=7)
    assert "2000-01-01" in _refused(tt.AsofDate, date(1999, 12, 31))
    _refused(tt.AsofDate, today + timedelta(days=8))


def test_the_window_follows_the_market_clock(monkeypatch):
    from domain import market_time

    monkeypatch.setattr(market_time, "market_today", lambda tz=None: date(2030, 6, 1))
    assert _ok(tt.RequestDate, date(2031, 6, 1)) == date(2031, 6, 1)
    _refused(tt.RequestDate, date(2031, 6, 3))


# --- the opaque blob -----------------------------------------------------------------------------------------------


def _nest(depth: int) -> dict:
    out: dict = {}
    for _ in range(depth - 1):
        out = {"k": out}
    return out


def test_json_depth_counts_containers_without_copying():
    assert tt.json_depth({}) == 1
    assert tt.json_depth({"a": [1, {"b": "x"}]}) == 3
    assert tt.json_depth(_nest(tt.STATE_MAX_DEPTH)) == tt.STATE_MAX_DEPTH
    assert tt.json_depth({"a": "[[[[{{{{"}) == 1  # string contents are never inspected


def test_opaque_state_depth_limit():
    assert _ok(tt.OpaqueState, _nest(tt.STATE_MAX_DEPTH)) == _nest(tt.STATE_MAX_DEPTH)
    assert "nesting" in _refused(tt.OpaqueState, _nest(tt.STATE_MAX_DEPTH + 1))
