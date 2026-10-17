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
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.routers import theses as theses_router
from domain import market_time
from domain.market_time import (
    known_at_for_asof,
    market_now,
    market_today,
    market_tz,
    serve_known_at,
)
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


# --- the durability guard: the option-(b) discipline, enforced (INVARIANTS.md §6) ---
#
# The production guard above deliberately SKIPS tests/ (a test may legitimately construct a naive clock).
# But the fix has a SECOND half: a test that asserts a PRODUCTION date contract must read the same
# ``market_today()`` the code does, or it drifts on any runner whose zone leads market time. CI runs UTC and
# the two dates diverge only after ~20:00 ET, so such a test reads green at 10am and red at 9pm on the SAME
# commit — twelve tests failed exactly this way on the PR that first introduced this helper. This guard
# scans the tests tree the production guard skips, so a NEW ambient-clock test becomes an explicit,
# review-visible ``_TEST_ALLOWED`` decision instead of a silent, time-of-day-flaky failure.

_TESTS = _BACKEND / "tests"

# Test files allowed a bare ``date.today()`` / no-arg ``datetime.now()``. EMPTY by design: after the
# option-(b) migration every production-date-contract test reads ``market_today()``, so test and code agree
# by construction. Add an entry ONLY when a test genuinely does not care which calendar day it is, with a
# one-line reason — never to silence a real cross-clock flake (that is the option-(b) swap's job).
_TEST_ALLOWED: set[str] = set()


def _test_files() -> list[Path]:
    """Every ``.py`` under ``tests/`` — the mirror of ``_source_files()``, pointed at the tree it skips."""
    out = []
    for p in sorted(_TESTS.rglob("*.py")):
        if any(part == "__pycache__" for part in p.relative_to(_TESTS).parts):
            continue
        out.append(p)
    return out


def test_no_test_module_reads_the_ambient_clock():
    """No test reads the ambient clock either (option (b), INVARIANTS.md #6): a production-date-contract
    test must read ``market_today()`` so it can never disagree with the route it exercises. The AST helper
    ignores the ``date.today()`` / ``datetime.now()`` that appear in THIS file's prose and string fixtures
    (they are not Calls), so the allowlist stays honestly empty."""
    found: list[str] = []
    for path in _test_files():
        rel = path.relative_to(_BACKEND).as_posix()
        if rel in _TEST_ALLOWED:
            continue
        found += [f"{rel} {h}" for h in _ambient_clock_calls(path)]
    assert not found, (
        "ambient clock read(s) in tests:\n  "
        + "\n  ".join(found)
        + "\n  Use domain.market_time.market_today() / market_now() so the test and the production path\n"
        "  agree by construction (option (b), INVARIANTS.md #6). Add to _TEST_ALLOWED (with a one-line\n"
        "  reason) ONLY if the test genuinely does not care which calendar day it is."
    )


def test_the_test_scan_actually_walks_the_tests_tree():
    """Vacuity guard for the durability scan (the same lesson as the source-scan anchor): a wrong path
    would empty the file list and read green forever. Anchor on test modules that must always be present.
    """
    rel = {p.relative_to(_BACKEND).as_posix() for p in _test_files()}
    assert len(rel) > 20
    for anchor in ("tests/app/test_decisions_api.py", "tests/domain/test_market_time.py"):
        assert anchor in rel
    assert all(r.startswith("tests/") for r in rel)


# --- the import-time guard: no test captures "today" when its module is IMPORTED ---
#
# The option-(b) swap made test dates read the production clock, but three modules read it ONCE, at import:
# ``TODAY = market_today()``. Collection imports every module before the first test runs, so under ``-n 6``
# that value can be minutes old by the time a test uses it, while the code under test reads
# ``market_today()`` at REQUEST time. A full run started 2026-10-16 23:59:11 ET crossed midnight and failed
# 8 tests in test_decisions_api / test_thesis_lists_api: the test said the 16th and the app (correctly)
# said the 17th. The same shape as the option-(b) flake, on the time axis instead of the zone axis, and just
# as invisible on most runs. This scan makes a NEW import-time read a failing test instead of a midnight.
#
# Scope: the day/instant reads (``market_today()``, ``market_now()``, ``date.today()``, ``datetime.now()``
# with or without a zone, ``.utcnow()``, ``datetime.today()``). NOT ``known_at_for_asof(<fixed date>)``: for
# a past date it is ``min(now, end of that day)`` = the end of that day, clock-free (the pinned historical
# dates in tests/replay/test_pit_first_knowability.py).

_IMPORT_TIME_WHY = (
    "Read the clock INSIDE the test, or take the ``pinned_today`` fixture (tests/conftest.py), which pins\n"
    "  the day for the test AND the app it calls. A value read at import is read at COLLECTION, which can\n"
    "  be minutes before the test runs, and goes stale the moment a run crosses midnight ET."
)


def _clock_call(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name) and f.id in ("market_today", "market_now"):
        return f"{f.id}()"
    if not isinstance(f, ast.Attribute):
        return None
    if f.attr in ("market_today", "market_now"):
        return f"{f.attr}()"
    recv = getattr(f.value, "id", None) or getattr(f.value, "attr", None)
    if (recv, f.attr) in {
        ("date", "today"),
        ("datetime", "today"),
        ("datetime", "now"),
        ("datetime", "utcnow"),
    }:
        return f"{recv}.{f.attr}()"
    return None


class _ImportTimeClockReads(ast.NodeVisitor):
    """Collects clock reads that run when the module is IMPORTED: the module body, class bodies, decorator
    expressions and default-argument values (all evaluated at definition time). A function or lambda BODY
    is never entered: it runs per call, which is exactly where a test should read the clock."""

    def __init__(self) -> None:
        self.hits: list[str] = []

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        if not isinstance(node, ast.Lambda):
            for deco in node.decorator_list:
                self.visit(deco)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _definition

    def visit_Call(self, node: ast.Call) -> None:
        what = _clock_call(node)
        if what:
            self.hits.append(f"line {node.lineno}: {what}")
        self.generic_visit(node)


def _import_time_clock_reads(path: Path) -> list[str]:
    scan = _ImportTimeClockReads()
    scan.visit(ast.parse(path.read_text(encoding="utf-8")))
    return scan.hits


def test_no_test_module_reads_the_clock_at_import():
    found: list[str] = []
    for path in _test_files():
        rel = path.relative_to(_BACKEND).as_posix()
        found += [f"{rel} {h}" for h in _import_time_clock_reads(path)]
    assert not found, (
        "clock read(s) at IMPORT time in tests:\n  "
        + "\n  ".join(found)
        + "\n  "
        + _IMPORT_TIME_WHY
    )


def test_the_import_time_guard_actually_detects_a_violation(tmp_path):
    """The guard's own guard: every import-time shape is caught, and a read inside a test or helper BODY
    (per call, the correct place) is not."""
    good = tmp_path / "good.py"
    good.write_text(
        "from datetime import date, datetime, timezone\n"
        "from domain.market_time import known_at_for_asof, market_today\n"
        "FIXED = date(2026, 10, 16)\n"  # a fixed date is not a clock read
        "PINS = [known_at_for_asof(date(2025, 6, 2))]\n"  # out of scope (clock-free for a past date)
        "def test_x(pinned_today):\n"
        "    today = market_today()\n"
        "    now = datetime.now(timezone.utc)\n"
        "    later = lambda: date.today()\n"
        "def _helper(asof=None):\n"
        "    return asof or market_today()\n"
        "class TestY:\n"
        "    def test_z(self):\n"
        "        return market_today()\n",
        encoding="utf-8",
    )
    assert _import_time_clock_reads(good) == []

    bad = tmp_path / "bad.py"
    bad.write_text(
        "import datetime as dt\n"
        "import pytest\n"
        "from datetime import date, datetime, timezone\n"
        "from domain import market_time\n"
        "from domain.market_time import market_now, market_today\n"
        "TODAY = market_today()\n"  # the 2026-10-16/17 shape
        "NOW = market_now()\n"
        "STAMP = datetime.now(timezone.utc)\n"
        "AMBIENT = dt.date.today()\n"
        "VIA_MODULE = market_time.market_today()\n"
        "def _call(asof=market_today()):\n"  # a default value runs at definition time
        "    return asof\n"
        "@pytest.mark.parametrize('d', [date.today()])\n"  # so does a decorator
        "def test_p(d):\n"
        "    pass\n"
        "class TestK:\n"
        "    WHEN = datetime.utcnow()\n",  # and a class body
        encoding="utf-8",
    )
    assert len(_import_time_clock_reads(bad)) == 8


def test_pinned_today_pins_the_route_too_and_holds_across_midnight(monkeypatch, pinned_today):
    """The fixture's contract (tests/conftest.py): the day the TEST holds is the day the ROUTE reads, even
    when the wall clock crosses midnight ET mid-test. ``app.routers.theses`` imports ``market_today`` BY
    NAME, like every route, so a pin that patched only ``domain.market_time.market_today`` would miss it;
    the fixture pins ``market_now``, which every ``market_today()`` goes through. (The route module is
    imported at the TOP of this file, before any fixture runs, as collection imports it in a real run:
    imported inside the test it would bind whatever the fixture had patched, and prove nothing.)"""
    assert theses_router.market_today() == market_today() == pinned_today
    # the wall clock crosses midnight ET while the test runs
    _pin(
        monkeypatch, datetime.combine(pinned_today + timedelta(days=1), time(0, 0, 1), market_tz())
    )
    assert theses_router.market_today() == pinned_today


# --- known_at_for_asof + serve_known_at: the transaction-axis cap the serve path threads (invariant #1) ---
#
# The as-of gate is ``valid_from <= asof AND recorded_at <= known_at``. With ``known_at = now`` the second
# half never bites for a past asof, so a scrubbed-back /call read TODAY's knowledge of that date (the
# serve-path leak: TPCS bars dated August, ingested 09-01, arming an 08-25 recompute the record had logged
# as watching). These pin the cap that closes it — and that the day ends in MARKET time, the cron's own
# convention (RUN_AT 22:30 ET, asof = market_today()), never UTC. ``now=`` / ``today=`` are the injection
# seams; ``_pin`` is not used for the past branch because its ``_Frozen`` clock has no ``combine``.

_NOW_LATE_JULY = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def test_known_at_caps_a_past_asof_at_the_end_of_its_MARKET_day(monkeypatch):
    """A scrubbed-back as-of caps the transaction axis at that day's end IN MARKET TIME, rendered in UTC:
    2026-06-10 ends at 23:59:59.999999 EDT = 2026-06-11 03:59:59.999999Z. A UTC end-of-day
    (2026-06-10 23:59:59Z = 19:59 EDT) would sit BEFORE the 22:30 ET cron run that records the 10th, so a
    fact the record for 06-10 saw (that night's bar, that evening's Form 4) would be invisible to a 06-10
    recompute — a systematic one-bar-short disagreement with the record on every past date."""
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    get_settings.cache_clear()
    got = known_at_for_asof(date(2026, 6, 10), now=_NOW_LATE_JULY)
    assert got == datetime(2026, 6, 11, 3, 59, 59, 999999, tzinfo=timezone.utc)
    assert got.tzinfo is timezone.utc  # an instant, normalized — never a wall time
    # a winter date is -5h (EST): the zone is real, not a fixed offset
    assert known_at_for_asof(date(2026, 1, 10), now=_NOW_LATE_JULY) == datetime(
        2026, 1, 11, 4, 59, 59, 999999, tzinfo=timezone.utc
    )


def test_known_at_is_now_for_a_live_or_future_asof(monkeypatch):
    """A live/future as-of reads at now (everything recorded by this moment), never a future EOD."""
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    get_settings.cache_clear()
    now = _NOW_LATE_JULY
    assert known_at_for_asof(date(2026, 8, 1), now=now) == now  # future EOD -> now wins
    assert known_at_for_asof(date(2026, 7, 24), now=now) == now  # today, before EOD -> now wins


def test_known_at_is_STILL_now_for_today_after_20_00_ET(monkeypatch):
    """The live identity holds at every hour of the day: at 01:30Z (21:30 EDT on the 17th) the market day
    is the 17th and its end (03:59:59Z on the 18th) is still ahead of now, so min(now, cap) == now. Under
    a UTC end-of-day the cap (2026-07-17 23:59:59Z) had ALREADY passed and a live Board after 20:00 ET
    would have read up to four hours stale — the same time-of-day window market_today() exists for.
    """
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    get_settings.cache_clear()
    assert known_at_for_asof(date(2026, 7, 17), now=_UTC_EVENING) == _UTC_EVENING


def test_the_cap_follows_the_configured_zone(monkeypatch):
    """The day's end is the CONFIGURED market zone's — never the process's, and not New York's by fiat."""
    monkeypatch.setenv("ALPHADECK_MARKET_TZ", "Asia/Tokyo")
    get_settings.cache_clear()
    # Tokyo is +9 (no DST): 2026-06-10 ends at 2026-06-10 14:59:59.999999Z
    assert known_at_for_asof(date(2026, 6, 10), now=_NOW_LATE_JULY) == datetime(
        2026, 6, 10, 14, 59, 59, 999999, tzinfo=timezone.utc
    )


def test_serve_known_at_is_None_for_a_live_or_future_asof_and_the_cap_for_a_past_one(monkeypatch):
    """The serve seam: a live view threads None — the UNCHANGED live read (the PIT's own UTC now for the
    fact reads, the DATABASE clock for the decisions log: the one-clock rule in decisions_repo) — and a
    past view threads the market-day cap. None, not now: a host-clock now on the live path would reopen
    the two-clock flake the decisions read closed."""
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    get_settings.cache_clear()
    today = date(2026, 7, 24)
    assert serve_known_at(today, today=today) is None
    assert serve_known_at(date(2026, 8, 1), today=today) is None  # a future asof is live too
    assert serve_known_at(date(2026, 6, 10), today=today) == datetime(
        2026, 6, 11, 3, 59, 59, 999999, tzinfo=timezone.utc
    )


def test_serve_known_at_defaults_today_to_market_today_not_the_ambient_date(monkeypatch):
    """At 01:30Z on the 18th an ambient-UTC 'today' is the 18th, so asof=07-17 would look PAST and get
    capped — but the market is still on the 17th, so the 17th is LIVE (None). The seam must decide
    live-vs-past with market_today(): the same evening window that once mislabeled the cron's asof.
    """
    _pin(monkeypatch, _UTC_EVENING)
    assert market_today() == date(2026, 7, 17)
    assert serve_known_at(date(2026, 7, 17)) is None
    assert serve_known_at(date(2026, 7, 18)) is None  # tomorrow in market time: future = live
