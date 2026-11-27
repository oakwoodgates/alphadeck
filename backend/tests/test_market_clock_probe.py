"""The suite-wide market-clock pin and the closure probe (F5, framework-completion plan §3.3).

``ALPHADECK_TEST_MARKET_NOW`` (an ISO instant with its offset) runs the whole suite as if the market clock read that
instant: ``tests/conftest.py``'s session fixture calls ``tests.market_clock_probe.pin_from_env``, which pins
``domain.market_time.market_now`` AND every reference that kept the original by name (the six product modules that
import it, a function default). ``scripts/closure_probe.py`` runs the suite at NYSE closure instants and at a control
a week earlier, and lists the failures only the closure run has. No database here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# The six product modules that import ``market_now`` BY NAME, imported here at collection, before any pin, as a real
# run imports them (imported inside a test they would bind whatever is pinned then, and prove nothing).
from app.routers import admin, scoreboard
from domain import market_time
from domain.trading_days import closure_name, is_scheduled_day
from pipeline import daily, fill_price_gaps, nightly_extraction, onboarding
from tests import market_clock_probe as probe

BY_NAME = (admin, scoreboard, daily, fill_price_gaps, nightly_extraction, onboarding)
_ET = ZoneInfo("America/New_York")
CHRISTMAS_NOON = datetime(2026, 12, 25, 12, 0, tzinfo=_ET)  # Friday, an NYSE full-day closure


@pytest.fixture
def _default_schedule(monkeypatch):
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    monkeypatch.delenv("ALPHADECK_CRON_AT", raising=False)
    market_time.get_settings.cache_clear()
    yield
    market_time.get_settings.cache_clear()


# --- the pin ----------------------------------------------------------------------------------------------------


def test_the_pin_moves_market_today_and_every_module_that_imported_market_now_by_name(
    monkeypatch, _default_schedule
):
    probe.pin_market_now_everywhere(CHRISTMAS_NOON, monkeypatch)
    assert market_time.market_today() == date(2026, 12, 25)
    for module in BY_NAME:
        got = module.market_now()
        assert got.date() == date(2026, 12, 25), module.__name__
        assert got.utcoffset() == timedelta(hours=-5), module.__name__  # aware, in the market zone
    # what every route reads through it: Christmas noon is a closure, so the night is Thursday 12-24 and the
    # action day the next session, Monday 12-28
    assert market_time.market_clock() == market_time.MarketClock(
        today=date(2026, 12, 25), night=date(2026, 12, 24), action_day=date(2026, 12, 28)
    )
    assert market_time.current_night() == date(2026, 12, 24)


def test_a_function_default_that_held_the_clock_reads_the_pin(monkeypatch):
    """``pipeline.onboarding.run_batch(now=market_now)`` bound the function when the module was defined."""
    probe.pin_market_now_everywhere(CHRISTMAS_NOON, monkeypatch)
    assert onboarding.run_batch.__kwdefaults__["now"]().date() == date(2026, 12, 25)


def test_the_pinned_clock_ticks_from_the_instant_in_any_zone(monkeypatch):
    probe.pin_market_now_everywhere(CHRISTMAS_NOON, monkeypatch)
    first = market_time.market_now()
    second = market_time.market_now(ZoneInfo("UTC"))
    assert CHRISTMAS_NOON <= first <= second < CHRISTMAS_NOON + timedelta(minutes=1)
    assert second.utcoffset() == timedelta(0)


def test_pinned_today_reads_the_pin(monkeypatch, request):
    probe.pin_market_now_everywhere(CHRISTMAS_NOON, monkeypatch)
    assert request.getfixturevalue("pinned_today") == date(2026, 12, 25)
    assert market_time.market_today() == date(2026, 12, 25)


def test_transaction_time_is_not_moved(monkeypatch):
    """The ``known_at`` cap's ``now`` is the real UTC instant (invariant 1), as under ``pinned_today``."""
    probe.pin_market_now_everywhere(CHRISTMAS_NOON, monkeypatch)
    before = datetime.now(timezone.utc)
    got = market_time.known_at_for_asof(date(2100, 1, 1))
    assert before <= got <= datetime.now(timezone.utc)


def test_a_test_that_freezes_the_modules_own_clock_still_wins(monkeypatch):
    """``tests/domain/test_market_time.py`` freezes ``market_time.datetime``; on the real day that wins, so under
    the pin it does too."""
    probe.pin_market_now_everywhere(CHRISTMAS_NOON, monkeypatch)
    frozen_at = datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc)

    class _Frozen:
        @staticmethod
        def now(tz=None):
            return frozen_at.astimezone(tz)

    monkeypatch.setattr(market_time, "datetime", _Frozen)
    assert market_time.market_today() == date(2026, 7, 17)
    assert daily.market_now().date() == date(2026, 7, 17)


def test_undo_restores_the_original_function_objects():
    before = market_time.market_now
    held = {m.__name__: m.market_now for m in BY_NAME}
    default = onboarding.run_batch.__kwdefaults__["now"]
    mp = pytest.MonkeyPatch()
    try:
        patched = probe.pin_market_now_everywhere(CHRISTMAS_NOON, mp)
        assert market_time.market_now is not before
        assert all(m.market_now is market_time.market_now for m in BY_NAME)
        assert onboarding.run_batch.__kwdefaults__["now"] is market_time.market_now
        assert {f"{m.__name__}.market_now" for m in BY_NAME} <= set(patched)
    finally:
        mp.undo()
    assert market_time.market_now is before
    assert {m.__name__: m.market_now for m in BY_NAME} == held
    assert all(held[m.__name__] is m.market_now for m in BY_NAME)
    assert onboarding.run_batch.__kwdefaults__["now"] is default


# --- the env var ------------------------------------------------------------------------------------------------

_NOW = datetime(2026, 11, 27, 14, 0, tzinfo=_ET)


def test_the_env_var_is_off_when_unset_or_blank():
    assert probe.instant_from_env({}, now=_NOW) is None
    assert probe.instant_from_env({probe.ENV: "  "}, now=_NOW) is None


def test_the_env_var_reads_an_aware_instant_later_than_now():
    got = probe.instant_from_env({probe.ENV: "2026-12-25T12:00:00-05:00"}, now=_NOW)
    assert got == CHRISTMAS_NOON and got.utcoffset() == timedelta(hours=-5)


@pytest.mark.parametrize(
    "raw, why",
    [
        ("2026-12-25T12:00:00", "offset"),  # naive: no market date
        ("christmas", "not an ISO instant"),
        (
            "2026-11-26T12:00:00-05:00",
            "later than now",
        ),  # a fact recorded now would be from the future
    ],
)
def test_the_env_var_refuses_a_naive_malformed_or_past_instant(raw, why):
    with pytest.raises(ValueError, match=why):
        probe.instant_from_env({probe.ENV: raw}, now=_NOW)


def test_pin_from_env_patches_nothing_when_unset(monkeypatch):
    before = market_time.market_now
    held = [m.market_now for m in BY_NAME]
    assert probe.pin_from_env({}, monkeypatch) is None
    assert market_time.market_now is before
    assert [m.market_now for m in BY_NAME] == held
    assert probe.pin_from_env({probe.ENV: "2099-12-25T12:00:00-05:00"}, monkeypatch) is not None
    assert all(m.market_now().date() == date(2099, 12, 25) for m in BY_NAME)


def test_the_session_pin_is_on_exactly_when_the_env_var_is_set():
    """The conftest session fixture's state in THIS run: unset (CI, every normal run), every by-name importer holds
    ``domain.market_time``'s own function; set, every one reads the pinned day."""
    raw = os.environ.get(probe.ENV, "").strip()
    holders = (market_time, *BY_NAME)
    assert all(m.market_now is market_time.market_now for m in holders)
    assert onboarding.run_batch.__kwdefaults__["now"] is market_time.market_now
    if not raw:
        assert market_time.market_now.__module__ == "domain.market_time"
        return
    want = datetime.fromisoformat(raw).astimezone(market_time.market_tz()).date()
    assert market_time.market_now.__module__ == "tests.market_clock_probe"
    assert market_time.market_today() == want
    for m in holders:
        assert m.market_now().date() == want, m.__name__


# --- the probe (scripts/closure_probe.py) ------------------------------------------------------------------------

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "closure_probe.py"


@pytest.fixture(scope="module")
def cp():
    spec = importlib.util.spec_from_file_location("closure_probe", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["closure_probe"] = mod  # its dataclasses need it
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("closure_probe", None)


XMAS, XMAS_CONTROL = "2026-12-25T12:00:00-05:00", "2026-12-18T12:00:00-05:00"
_FAIL = '<failure message="AssertionError: assert 1 == 2&#10;more">trace</failure>'
_ERROR = "<error message=\"failed on setup with 'ValueError: boom'\">trace</error>"
_SKIP = '<skipped type="pytest.skip" message="Postgres not reachable">skip</skipped>'


def _junit(path: Path, cases: dict[str, str]) -> None:
    """A pytest junit file: ``{"tests.mod::test_name": "pass" | "fail" | "error" | "skip"}``."""
    body = {"pass": "", "fail": _FAIL, "error": _ERROR, "skip": _SKIP}
    rows = []
    for tid, outcome in cases.items():
        cls, name = tid.split("::")
        rows.append(
            f'<testcase classname="{cls}" name="{name}" time="0.1">{body[outcome]}</testcase>'
        )
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="pytest" tests="{len(cases)}">{"".join(rows)}</testsuite></testsuites>',
        encoding="utf-8",
    )


def _diff(cp, out: Path, closure: dict, control: dict) -> int:
    _junit(out / "2026-12-25T1200.xml", closure)
    _junit(out / "2026-12-18T1200.xml", control)
    return cp.main(["--out", str(out), "--diff-only", "--pair", f"{XMAS},{XMAS_CONTROL}"])


def test_the_probe_reads_the_same_env_var(cp):
    assert cp.ENV == probe.ENV


def test_the_default_pairs_are_the_plans_closures_against_sessions_a_week_or_two_earlier(cp):
    closures = [c.date() for c, _, _ in cp.DEFAULT_PAIRS]
    assert closures == [
        date(2026, 12, 25),
        date(2026, 12, 28),
        date(2026, 12, 24),
        date(2027, 1, 1),
        date(2027, 1, 4),
    ]
    assert closure_name(date(2026, 12, 25)) and closure_name(date(2027, 1, 1))
    assert cp.DEFAULT_PAIRS[2][0].hour == 23  # the night before a closure, after the 22:30 pass
    for closure, control, _shape in (*cp.DEFAULT_PAIRS, *cp.MLK_PAIRS):
        assert control < closure and is_scheduled_day(control.date())
        assert (control.weekday(), control.time()) == (closure.weekday(), closure.time())
        assert closure.utcoffset() == control.utcoffset() == timedelta(hours=-5)
    assert closure_name(cp.MLK_PAIRS[0][0].date()) == "Martin Luther King, Jr. Day"


def test_a_failure_only_on_the_closure_side_fails_the_probe(cp, tmp_path, capsys):
    rc = _diff(
        cp,
        tmp_path,
        {"tests.a::test_seed": "fail", "tests.b::test_stale": "fail", "tests.c::test_x": "pass"},
        {"tests.a::test_seed": "pass", "tests.b::test_stale": "fail", "tests.c::test_x": "fail"},
    )
    assert rc == 1
    pair = json.loads((tmp_path / "differential.json").read_text(encoding="utf-8"))["pairs"][0]
    assert pair["closure_only"] == {"tests.a::test_seed": "AssertionError: assert 1 == 2"}
    assert pair["noise"] == ["tests.b::test_stale"]
    assert pair["control_only"] == ["tests.c::test_x"]
    report = (tmp_path / "differential.txt").read_text(encoding="utf-8")
    assert report in capsys.readouterr().out
    assert "tests.a::test_seed" in report and "AssertionError: assert 1 == 2" in report


def test_a_failure_in_both_runs_is_listed_as_noise_and_passes(cp, tmp_path):
    rc = _diff(
        cp,
        tmp_path,
        {"tests.b::test_stale": "fail", "tests.c::test_x": "pass"},
        {"tests.b::test_stale": "error", "tests.c::test_x": "pass"},
    )
    assert rc == 0
    pair = json.loads((tmp_path / "differential.json").read_text(encoding="utf-8"))["pairs"][0]
    assert pair["closure_only"] == {} and pair["noise"] == ["tests.b::test_stale"]
    assert "tests.b::test_stale" in (tmp_path / "differential.txt").read_text(encoding="utf-8")


def test_a_setup_error_on_the_closure_side_counts_as_a_failure(cp, tmp_path):
    assert _diff(cp, tmp_path, {"tests.a::test_seed": "error"}, {"tests.a::test_seed": "pass"}) == 1


def test_a_closure_run_that_skipped_what_its_control_ran_is_not_comparable(cp, tmp_path):
    """A lost database connection skips a test, which would hide its failure: exit 3, the skips listed."""
    rc = _diff(cp, tmp_path, {"tests.a::test_seed": "skip"}, {"tests.a::test_seed": "pass"})
    assert rc == 3
    pair = json.loads((tmp_path / "differential.json").read_text(encoding="utf-8"))["pairs"][0]
    assert pair["skipped_only_in_closure"] == ["tests.a::test_seed"]


def test_a_missing_junit_file_is_an_error(cp, tmp_path):
    _junit(tmp_path / "2026-12-25T1200.xml", {"tests.a::test_seed": "pass"})
    rc = cp.main(["--out", str(tmp_path), "--diff-only", "--pair", f"{XMAS},{XMAS_CONTROL}"])
    assert rc == 2


class _Runner:
    """Records each run and writes a passing junit file where the probe asked for it."""

    def __init__(self):
        self.calls: list[tuple[list[str], Path, dict]] = []

    def __call__(self, cmd, *, cwd, env, log):
        self.calls.append((cmd, cwd, env))
        junit = next(a for a in cmd if a.startswith("--junitxml=")).split("=", 1)[1]
        _junit(Path(junit), {"tests.c::test_x": "pass"})
        log.write("1 passed\n")
        return 0


def _clock(at: datetime):
    return lambda: at


def test_each_instant_runs_once_with_the_pin_in_its_environment(cp, tmp_path):
    runner = _Runner()
    rc = cp.main(["--out", str(tmp_path)], runner=runner, clock=_clock(_NOW), sleep=None)
    assert rc == 0
    instants = [env[cp.ENV] for _, _, env in runner.calls]
    # five closure-side instants and three controls (12-18 and 12-21 serve two pairs each)
    assert len(instants) == len(set(instants)) == 8
    assert instants[:2] == [XMAS, XMAS_CONTROL]
    for cmd, cwd, env in runner.calls:
        assert cwd == cp.BACKEND
        assert cmd[:3] == [sys.executable, "-m", "pytest"] and cmd[3:5] == ["-n", "4"]
        label = cp.label(datetime.fromisoformat(env[cp.ENV]))
        assert cmd[-1] == f"--junitxml={tmp_path / (label + '.xml')}"
        assert (tmp_path / f"{label}.log").read_text(encoding="utf-8") == "1 passed\n"


def test_with_mlk_adds_the_monday_closure_and_the_day_after(cp, tmp_path):
    runner = _Runner()
    argv = ["--out", str(tmp_path), "--with-mlk"]
    assert cp.main(argv, runner=runner, clock=_clock(_NOW), sleep=None) == 0
    assert len(runner.calls) == 12


def test_args_after_the_double_dash_replace_the_default_pytest_args(cp, tmp_path):
    runner = _Runner()
    argv = ["--out", str(tmp_path), "--pair", f"{XMAS},{XMAS_CONTROL}", "--", "tests/app", "-x"]
    assert cp.main(argv, runner=runner, clock=_clock(_NOW), sleep=None) == 0
    assert [c[3:5] for c, _, _ in runner.calls] == [["tests/app", "-x"]] * 2


def test_a_junitxml_argument_or_a_past_instant_is_refused_before_any_run(cp, tmp_path):
    runner = _Runner()
    pair = ["--pair", f"{XMAS},{XMAS_CONTROL}"]
    assert cp.main(["--out", str(tmp_path), *pair, "--", "--junitxml=x.xml"], runner=runner) == 2
    after_christmas = _clock(datetime(2026, 12, 26, tzinfo=_ET))
    assert cp.main(["--out", str(tmp_path), *pair], runner=runner, clock=after_christmas) == 2
    assert runner.calls == []


def test_a_dry_run_prints_the_runs_and_starts_none(cp, tmp_path, capsys):
    runner = _Runner()
    out = tmp_path / "probe"
    argv = ["--out", str(out), "--pair", f"{XMAS},{XMAS_CONTROL}", "--dry-run"]
    assert cp.main(argv, runner=runner, clock=_clock(_NOW)) == 0
    printed = capsys.readouterr().out
    assert f"{cp.ENV}={XMAS}" in printed and f"{cp.ENV}={XMAS_CONTROL}" in printed
    assert runner.calls == [] and not out.exists()


@pytest.mark.parametrize(
    "hh, mm, wait_min",
    [(21, 0, 0), (21, 59, 0), (22, 5, 85), (22, 45, 45), (23, 30, 0), (23, 45, 0)],
)
def test_no_run_starts_when_it_could_reach_prods_nightly_window(cp, hh, mm, wait_min):
    """22:20-23:30 ET: prod's nightly shares the Postgres server. With 20-minute runs, 22:00 is the last start."""
    at = datetime(2026, 12, 1, hh, mm, tzinfo=_ET)
    assert cp.start_delay(at, timedelta(minutes=20)) == timedelta(minutes=wait_min)


def test_the_probe_waits_out_the_window_before_a_run(cp, tmp_path):
    t = [datetime(2026, 12, 1, 22, 30, tzinfo=_ET)]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        t[0] += timedelta(seconds=seconds)

    runner = _Runner()
    argv = ["--out", str(tmp_path), "--pair", f"{XMAS},{XMAS_CONTROL}"]
    assert cp.main(argv, runner=runner, clock=lambda: t[0], sleep=sleep) == 0
    assert slept == [3600.0] and len(runner.calls) == 2
