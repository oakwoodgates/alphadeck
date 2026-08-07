"""market_time — "today" is a DOMAIN fact, not an ambient one (INVARIANTS.md §6).

The production bug this closes: a manual ``pipeline.daily`` in a UTC container at ~02:xx UTC computed
``date.today() = 2026-07-18`` and recorded TOMORROW's ``asof`` — and ``asof`` is a LIVENESS PARAMETER, not
a label (``calls/assembler``'s inclusive ``entry_signal_is_live``), so that is a materially DIFFERENT call:
a signal whose window closed that day drops out and the derived countdowns shift.
(``POSTMORTEM_CRON_FREEZE_2026-07.md`` — "The TZ forensic".)

Two kinds of test here:
- the CONTRACT tests — a pinned UTC-evening instant yields the ET (previous) date; the zone is config,
  not ambient; a bad zone fails LOUD; and the helper does NO trading-calendar logic (a Saturday stays a
  Saturday — pinned so nobody quietly teaches it to skip weekends);
- the REGRESSION GUARD — a repo scan that fails on any NEW bare ``date.today()`` / ``datetime.now()``.
  That guard is the point: the gap was written up at 9 call sites and had grown to 18 by the time it was
  fixed, because there was no shared helper to reach for and nothing that noticed.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from domain import market_time
from domain.market_time import market_now, market_today, market_tz
from domain.settings import get_settings

# 2026-07-18 01:30 UTC IS 2026-07-17 21:30 in New York — the exact shape of the production mislabel
# (an evening ET run that a UTC process dates to tomorrow).
_UTC_EVENING = datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings() is a cached singleton — clear it around every test so an ALPHADECK_MARKET_TZ
    override in one test can never leak into another (the test_settings.py discipline)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _pin(monkeypatch, instant: datetime) -> None:
    """Freeze the module's clock at a real INSTANT, converted into whatever zone the code asks for — so
    the test exercises the actual ``datetime.now(tz)`` path rather than stubbing the answer. The ``tz is
    not None`` assert is load-bearing: ``astimezone(None)`` silently converts to the AMBIENT zone, which
    is exactly the bug, so a regression that dropped the explicit tz would otherwise pass here."""

    class _Frozen:
        @staticmethod
        def now(tz=None):
            assert tz is not None, "market_now must pass an EXPLICIT tz, never the ambient clock"
            return instant.astimezone(tz)

    monkeypatch.setattr(market_time, "datetime", _Frozen)


# --- the contract: market time, not ambient time ---


def test_a_utc_evening_instant_is_still_the_PREVIOUS_day_in_market_time(monkeypatch):
    """The whole bug in one assertion: at 2026-07-18 01:30 UTC an ambient-UTC process reads "today" as
    the 18th, while the market is still on the 17th. market_today() must say the 17th."""
    _pin(monkeypatch, _UTC_EVENING)
    assert _UTC_EVENING.date() == date(2026, 7, 18)  # what date.today() answered in the container
    assert market_today() == date(2026, 7, 17)  # what the trading day actually was


def test_market_now_is_timezone_AWARE_and_in_the_market_zone(monkeypatch):
    """Aware on purpose — a naive datetime is the ambient-TZ bug wearing a different hat."""
    _pin(monkeypatch, _UTC_EVENING)
    now = market_now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None
    assert now.hour == 21 and now.minute == 30  # 01:30Z rendered in New York
    assert now.date() == market_today()  # the two helpers agree by construction


def test_the_zone_is_a_real_zone_not_a_fixed_offset(monkeypatch):
    """DST is honored: the same wall reading is -4h in July (EDT) and -5h in January (EST). A hardcoded
    offset would pass every summer test and be an hour wrong for four months."""
    _pin(monkeypatch, datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc))
    assert market_now().utcoffset().total_seconds() == -4 * 3600
    _pin(monkeypatch, datetime(2026, 1, 18, 1, 30, tzinfo=timezone.utc))
    assert market_now().utcoffset().total_seconds() == -5 * 3600


def test_the_zone_comes_from_settings_not_the_environment(monkeypatch):
    """ALPHADECK_MARKET_TZ is the answer's source — config, never the process TZ. Pinned at an instant
    where the two zones genuinely disagree about the date."""
    monkeypatch.setenv("ALPHADECK_MARKET_TZ", "Asia/Tokyo")
    get_settings.cache_clear()
    _pin(monkeypatch, _UTC_EVENING)
    assert market_tz() == ZoneInfo("Asia/Tokyo")
    assert market_today() == date(2026, 7, 18)  # Tokyo is already on the 18th; New York is not


def test_the_default_zone_is_new_york(monkeypatch):
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    get_settings.cache_clear()
    assert market_tz() == ZoneInfo("America/New_York")


def test_an_explicit_tz_argument_wins_over_settings(monkeypatch):
    """The optional tz parameter is the injection seam (a caller/test may pass its own zone) — it must
    override the configured default, not merely coexist with it."""
    _pin(monkeypatch, _UTC_EVENING)
    assert market_today(ZoneInfo("Asia/Tokyo")) == date(2026, 7, 18)
    assert market_today() == date(2026, 7, 17)


def test_a_malformed_zone_fails_LOUD(monkeypatch):
    """parse_run_at's discipline: a bad deploy value is an error, never a silent UTC fallback (a silent
    fallback here would reintroduce the exact bug the module exists to remove). The message must name
    the tzdata dependency — the likeliest cause on a fresh environment."""
    monkeypatch.setenv("ALPHADECK_MARKET_TZ", "Mars/Olympus_Mons")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="tzdata"):
        market_today()


def test_it_does_NO_trading_calendar_logic(monkeypatch):
    """market_today() is "today in market time", NOT "the last trading day": no weekend skip, no holiday
    calendar. 2026-07-18 is a SATURDAY and must come back as itself. Pinned deliberately — teaching this
    helper to skip weekends would silently move every asof, every bitemporal valid_from, and every
    staleness read that calls it. The Mon-Fri schedule math lives in pipeline/schedule.py."""
    saturday = datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc)  # 12:00 EDT, a Saturday
    _pin(monkeypatch, saturday)
    assert market_today() == date(2026, 7, 18)
    assert market_today().weekday() == 5  # Saturday, returned as-is


# --- the regression guard: no NEW ambient clock reads ---

_BACKEND = Path(__file__).resolve().parents[2]

# The ONE module allowed to touch the raw clock — it is the shared helper everything else routes
# through. (Under the AST rule below it would not currently need the exemption: its own call is
# `datetime.now(tz)`, which carries an explicit zone. The entry stands so the module stays legal if a
# future edit needs a bare read here, and so the allowlist has exactly one, obvious member.)
_ALLOWED = {"domain/market_time.py"}

# Directories a source scan must never walk into: the tests themselves (a test may legitimately
# construct a naive clock), plus venvs / caches / data.
_SKIP_DIRS = {"tests", "data", "seed_data", "node_modules", "build", "dist"}

_WHY = (
    "Use domain.market_time.market_today() / market_now() instead.\n"
    "  WHY: date.today() and datetime.now() read the PROCESS's ambient timezone, so the same code\n"
    "  answers a different question depending on where it runs. The trading day is a DOMAIN fact, not\n"
    "  an environment fact (INVARIANTS.md #6). This is not hypothetical: a UTC container after ~20:00\n"
    "  ET derived TOMORROW's trading day and recorded a materially DIFFERENT call — asof is a liveness\n"
    "  PARAMETER, not a label, so a signal whose window closed that day silently dropped out.\n"
    "  NOT covered by this rule: datetime.now(timezone.utc). That is TRANSACTION time (recorded_at) and\n"
    "  is correctly UTC — keep it (INVARIANTS.md #4).\n"
    "  Do NOT satisfy this test by extending _ALLOWED; the allowlist is for the helper itself."
)


def _source_files() -> list[Path]:
    """Every non-test backend source file, by the same walk in every environment (a stray .venv in the
    working checkout must not be scanned — the main checkout has one, this worktree does not)."""
    out = []
    for p in sorted(_BACKEND.rglob("*.py")):
        parts = p.relative_to(_BACKEND).parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in parts):
            continue
        if any(part.endswith(".egg-info") or part == "__pycache__" for part in parts):
            continue
        out.append(p)
    return out


def _ambient_clock_calls(path: Path) -> list[str]:
    """AST, not a regex: a lexical scan would flag every prose mention of ``date.today()`` in a docstring
    (this file included) and push the author toward an allowlist entry — the exact wrong lesson. Flags
    ``date.today()`` / ``datetime.today()`` (always ambient) and a NO-ARG ``datetime.now()`` (an argument
    means an explicit zone, e.g. the correct ``datetime.now(timezone.utc)``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        recv = node.func.value
        # `date.today()` and `datetime.date.today()` both reduce to the rightmost receiver name.
        name = getattr(recv, "id", None) or getattr(recv, "attr", None)
        if name not in ("date", "datetime"):
            continue
        if node.func.attr == "today":
            hits.append(f"line {node.lineno}: {name}.today()")
        elif node.func.attr == "now" and not node.args and not node.keywords:
            hits.append(f"line {node.lineno}: {name}.now()  [no timezone]")
    return hits


def test_no_backend_module_reads_the_ambient_clock():
    found: list[str] = []
    for path in _source_files():
        rel = path.relative_to(_BACKEND).as_posix()
        if rel in _ALLOWED:
            continue
        found += [f"{rel} {h}" for h in _ambient_clock_calls(path)]
    assert not found, "ambient clock read(s) found:\n  " + "\n  ".join(found) + "\n" + _WHY


def test_the_guard_actually_detects_a_violation(tmp_path):
    """The guard's own guard — a scan that silently matches nothing is worse than no scan (it reads
    green forever). Prove each pattern is caught, and that the legitimate UTC form is NOT."""
    good = tmp_path / "good.py"
    good.write_text(
        "from datetime import datetime, timezone\n"
        "from domain.market_time import market_today\n"
        "recorded_at = datetime.now(timezone.utc)\n"  # transaction time — correct, must NOT flag
        "asof = market_today()\n",
        encoding="utf-8",
    )
    assert _ambient_clock_calls(good) == []

    bad = tmp_path / "bad.py"
    bad.write_text(
        "from datetime import date, datetime\n"
        "asof = date.today()\n"
        "wall = datetime.now()\n"
        "'''a docstring that merely MENTIONS date.today() must not count'''\n",
        encoding="utf-8",
    )
    assert len(_ambient_clock_calls(bad)) == 2


def test_the_scan_actually_walks_the_backend():
    """A path-shape regression (wrong parents[] depth, an over-broad skip) would empty the file list and
    make the guard above vacuously green. Anchor it on modules that must always be in scope."""
    rel = {p.relative_to(_BACKEND).as_posix() for p in _source_files()}
    assert len(rel) > 100
    for anchor in ("pipeline/daily.py", "securities/master.py", "app/routers/theses.py"):
        assert anchor in rel
    assert not any(r.startswith("tests/") for r in rel)
