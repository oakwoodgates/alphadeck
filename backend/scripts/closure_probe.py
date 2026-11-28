"""Run the backend suite at NYSE closure instants and at a control a week earlier: the failures only a closure has.

F5 (framework-completion plan §3.3). CI broke on Thanksgiving twice (#680, #690) on tests that took "today" or "the
night before" as a market session. ``ALPHADECK_TEST_MARKET_NOW`` pins the market clock for a whole pytest run
(``tests/market_clock_probe.py``, wired by ``tests/conftest.py``); this script runs the suite once per instant with
``--junitxml`` and compares each closure-side run with its control. A failure in both is noise from the pin itself
(the market day runs weeks ahead of transaction time: staleness ages, known-at caps); a failure only on the closure
side is a calendar bug to fix.

    cd backend && .venv/Scripts/python.exe scripts/closure_probe.py --out DIR [--pair CLOSURE,CONTROL ...]
        [--with-mlk] [--dry-run | --diff-only] [--max-run-min 20] [-- PYTEST_ARGS ...]

**Instants** (ISO with offset, each later than now): by default these pairs, noon ET unless stated:

    Fri 2026-12-25          vs Fri 2026-12-18         today is a closure (#680)
    Mon 2026-12-28          vs Mon 2026-12-21         the session after a Friday closure (#690)
    Thu 2026-12-24 23:00    vs Thu 2026-12-17 23:00   the night before a closure (the action day crosses it)
    Fri 2027-01-01          vs Fri 2026-12-18         a closure across a year boundary
    Mon 2027-01-04          vs Mon 2026-12-21         the session after it
    --with-mlk adds: Mon 2027-01-18 vs Mon 2027-01-11, Tue 2027-01-19 vs Tue 2027-01-12

``--pair`` (repeatable) replaces them. A control shared by two pairs runs once (8 runs by default, 12 with MLK).

**Runs**, one at a time, from ``backend/`` with this interpreter: ``python -m pytest PYTEST_ARGS
--junitxml=DIR/<label>.xml`` with the env var set, output to ``DIR/<label>.log`` (``<label>`` is the instant in ET,
``2026-12-25T1200``). PYTEST_ARGS default to ``-n 4`` (CI's). Prod's nightly (22:20-23:30 ET) shares the Postgres
server: a run that could reach it within ``--max-run-min`` does not start until 23:30. ``--dry-run`` prints the runs;
``--diff-only`` compares the junit files already in DIR (no run, no clock check).

**Output**: ``DIR/differential.txt`` (also printed) and ``DIR/differential.json``: per pair, the closure-only failures
(a failure or an error) with the first line of each message, the noise (failing in both), the control-only failures,
and the tests the closure run skipped but its control did not (a lost database connection skips a test, which hides
its failure).

**Exit**: 1 on any closure-only failure; 3 when there is none but a closure run skipped tests its control ran (not
comparable: re-run it); 2 on bad arguments or a missing junit file; else 0.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time as _time
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo

ENV = "ALPHADECK_TEST_MARKET_NOW"  # read by tests/market_clock_probe.py
BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_PYTEST_ARGS = ("-n", "4")
NIGHTLY_WINDOW = (time(22, 20), time(23, 30))  # prod's nightly pass, market time: no DB tests
_ET = ZoneInfo("America/New_York")


def _et(y: int, m: int, d: int, hh: int = 12) -> datetime:
    return datetime(y, m, d, hh, 0, tzinfo=_ET)


# (closure-side instant, its control: same weekday and time one or two weeks earlier, the shape it probes)
DEFAULT_PAIRS = (
    (_et(2026, 12, 25), _et(2026, 12, 18), "today is a closure (#680)"),
    (_et(2026, 12, 28), _et(2026, 12, 21), "the session after a Friday closure (#690)"),
    (_et(2026, 12, 24, 23), _et(2026, 12, 17, 23), "the night before a closure"),
    (_et(2027, 1, 1), _et(2026, 12, 18), "a closure across a year boundary"),
    (_et(2027, 1, 4), _et(2026, 12, 21), "the session after it"),
)
MLK_PAIRS = (
    (_et(2027, 1, 18), _et(2027, 1, 11), "a Monday closure"),
    (_et(2027, 1, 19), _et(2027, 1, 12), "the session after a Monday closure"),
)

Runner = Callable[..., int]


def label(instant: datetime) -> str:
    """The instant in market time, file-name safe: ``2026-12-25T1200``."""
    return instant.astimezone(_ET).strftime("%Y-%m-%dT%H%M")


def start_delay(now: datetime, max_run: timedelta) -> timedelta:
    """How long to wait before a run of up to ``max_run`` can start without reaching prod's nightly window."""
    local = now.astimezone(_ET)
    for day in (local.date(), local.date() + timedelta(days=1)):
        lo, hi = (datetime.combine(day, t, tzinfo=_ET) for t in NIGHTLY_WINDOW)
        if local < hi and local + max_run > lo:
            return hi - local
    return timedelta(0)


@dataclass
class Run:
    """One run's junit file: test id -> first message line of each failure or error, and the skipped ids."""

    label: str
    tests: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)


def read_junit(path: Path) -> Run:
    run = Run(label=path.stem)
    ids: set[str] = set()
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        tid = "::".join(p for p in (case.get("classname"), case.get("name")) if p)
        ids.add(tid)
        for tag in ("failure", "error"):
            el = case.find(tag)
            if el is not None and tid not in run.failed:
                text = (el.get("message") or el.text or tag).strip()
                run.failed[tid] = (text.splitlines() or [tag])[0][:300]
        if case.find("skipped") is not None:
            run.skipped.add(tid)
    run.tests = len(ids)
    return run


def compare(closure: Run, control: Run) -> dict:
    return {
        "closure": closure.label,
        "control": control.label,
        "closure_only": {
            t: closure.failed[t] for t in sorted(closure.failed.keys() - control.failed)
        },
        "noise": sorted(closure.failed.keys() & control.failed.keys()),
        "control_only": sorted(control.failed.keys() - closure.failed.keys()),
        "skipped_only_in_closure": sorted(closure.skipped - control.skipped),
    }


def _day_kind(d: date) -> str:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from domain.trading_days import why_not_a_session

    return why_not_a_session(d) or "a session"


def _counts(run: Run) -> str:
    return f"{run.tests} tests, {len(run.failed)} failed, {len(run.skipped)} skipped"


def report(
    pairs: Sequence[tuple[datetime, datetime, str]], runs: dict[str, Run]
) -> tuple[str, dict]:
    lines: list[str] = []
    out = {"pairs": []}
    union: dict[str, list[str]] = {}
    for closure, control, shape in pairs:
        a, b = runs[label(closure)], runs[label(control)]
        diff = compare(a, b)
        diff["shape"] = shape
        out["pairs"].append(diff)
        lines += [
            f"== {a.label} ({_day_kind(closure.astimezone(_ET).date())}) vs control {b.label}: {shape}",
            f"   closure: {_counts(a)} | control: {_counts(b)}",
            f"   closure-only failures ({len(diff['closure_only'])}):",
            *(f"     {t}  -- {msg}" for t, msg in diff["closure_only"].items()),
            f"   noise, failing in both ({len(diff['noise'])}):",
            *(f"     {t}" for t in diff["noise"]),
            f"   control-only failures ({len(diff['control_only'])}):",
            *(f"     {t}" for t in diff["control_only"]),
            f"   skipped in the closure run, not in its control ({len(diff['skipped_only_in_closure'])}):",
            *(f"     {t}" for t in diff["skipped_only_in_closure"]),
        ]
        for t in diff["closure_only"]:
            union.setdefault(t, []).append(a.label)
    out["closure_only"] = union
    lines.append(f"== closure-only, all pairs ({len(union)}):")
    lines += [f"   {t}  [{', '.join(where)}]" for t, where in sorted(union.items())]
    return "\n".join(lines) + "\n", out


def _run_subprocess(cmd: list[str], *, cwd: Path, env: dict, log: IO[str]) -> int:
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT).returncode


def _parse_pair(text: str) -> tuple[datetime, datetime, str]:
    try:
        a, b = (datetime.fromisoformat(s.strip()) for s in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--pair {text!r}: CLOSURE,CONTROL ISO instants") from exc
    if a.utcoffset() is None or b.utcoffset() is None:
        raise argparse.ArgumentTypeError(f"--pair {text!r}: each instant needs its offset")
    return a, b, "custom"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="closure_probe.py", description=__doc__.split("\n\n")[0], add_help=True
    )
    p.add_argument("--out", required=True, type=Path, help="directory for junit, logs and the diff")
    p.add_argument("--pair", action="append", type=_parse_pair, help="CLOSURE,CONTROL (repeatable)")
    p.add_argument("--with-mlk", action="store_true", help="add the 2027-01-18/19 pairs")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the runs, start none")
    mode.add_argument("--diff-only", action="store_true", help="compare the junit files in --out")
    p.add_argument(
        "--max-run-min", type=float, default=20.0, help="one run's longest expected time"
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    own, pytest_args = (
        (argv[: argv.index("--")], argv[argv.index("--") + 1 :]) if "--" in argv else (argv, [])
    )
    try:
        args = _parser().parse_args(own)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    pytest_args = pytest_args or list(DEFAULT_PYTEST_ARGS)
    if any(a.startswith(("--junitxml", "--junit-xml")) for a in pytest_args):
        print("closure_probe: the probe sets --junitxml itself", file=sys.stderr)
        return 2
    pairs = list(args.pair or DEFAULT_PAIRS) + (list(MLK_PAIRS) if args.with_mlk else [])
    instants: list[datetime] = []
    for closure, control, _ in pairs:
        for instant in (closure, control):
            if label(instant) not in {label(i) for i in instants}:
                instants.append(instant)
    out: Path = args.out
    runner = runner or _run_subprocess
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleep = sleep or _time.sleep

    if not args.diff_only:
        now = clock()
        past = [i.isoformat() for i in instants if i <= now]
        if past:
            print(f"closure_probe: not later than now ({now.isoformat()}): {past}", file=sys.stderr)
            return 2
        if args.dry_run:
            print(f"cd {BACKEND}")
            for i in instants:
                cmd = [
                    sys.executable,
                    "-m",
                    "pytest",
                    *pytest_args,
                    f"--junitxml={out / label(i)}.xml",
                ]
                print(f"{ENV}={i.isoformat()} {' '.join(cmd)}")
            return 0
        out.mkdir(parents=True, exist_ok=True)
        max_run = timedelta(minutes=args.max_run_min)
        for n, instant in enumerate(instants, 1):
            while (delay := start_delay(clock(), max_run)) > timedelta(0):
                print(
                    f"waiting {delay} for prod's nightly window (22:20-23:30 ET) to pass",
                    flush=True,
                )
                sleep(delay.total_seconds())
            lab = label(instant)
            junit = out / f"{lab}.xml"
            junit.unlink(missing_ok=True)
            cmd = [sys.executable, "-m", "pytest", *pytest_args, f"--junitxml={junit}"]
            env = {**os.environ, ENV: instant.isoformat()}
            print(f"[{n}/{len(instants)}] {lab}: {' '.join(cmd[1:])}", flush=True)
            started = _time.monotonic()
            with open(out / f"{lab}.log", "w", encoding="utf-8") as log:
                rc = runner(cmd, cwd=BACKEND, env=env, log=log)
            print(f"   exit {rc} in {_time.monotonic() - started:.0f}s", flush=True)

    runs: dict[str, Run] = {}
    for instant in instants:
        junit = out / f"{label(instant)}.xml"
        if not junit.is_file():
            print(
                f"closure_probe: missing {junit} (the run crashed, or not run yet)", file=sys.stderr
            )
            return 2
        runs[label(instant)] = read_junit(junit)
    text, data = report(pairs, runs)
    (out / "differential.txt").write_text(text, encoding="utf-8")
    (out / "differential.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(text, end="")
    if data["closure_only"]:
        return 1
    return 3 if any(p["skipped_only_in_closure"] for p in data["pairs"]) else 0


if __name__ == "__main__":
    sys.exit(main())
