"""The cron sidecar runs the nightly sign-in prune (security PR 5 step 8): text checks on
``backend/scripts/daily_cron.sh``, like the other script checks (the script needs bash and Docker to run).

``prune_expired`` runs ``python -m db.housekeeping --prune`` inside a ``timeout``, is FAIL-OPEN (it always returns
0: a failure or a timeout is one log line, and the loop goes on), and sits right before ``prune_change_log``: the
change-log prune stays right before the dashboards' warm-up (``tests/pipeline/test_warm_dashboards.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

_SCRIPT = (Path(__file__).resolve().parents[2] / "scripts" / "daily_cron.sh").read_text(
    encoding="utf-8"
)


def _function(name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", _SCRIPT, re.M | re.S)
    assert m is not None, f"{name}() not found in daily_cron.sh"
    return m.group(1)


def _code() -> list[str]:
    return [
        ln.strip() for ln in _SCRIPT.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]


def test_prune_expired_runs_the_housekeeping_cli_inside_a_timeout():
    body = _function("prune_expired")
    assert re.search(r"^\s+timeout -k 10 300 python -m db\.housekeeping --prune\s*$", body, re.M)
    assert [ln for ln in _code() if "db.housekeeping" in ln] == [
        "timeout -k 10 300 python -m db.housekeeping --prune"
    ]


def test_prune_expired_is_fail_open():
    body = _function("prune_expired")
    lines = [ln.strip() for ln in body.strip().splitlines()]
    assert lines[-1] == "return 0"
    assert "124)" in body and "*)" in body  # a timeout and any other failure are logged, not fatal
    assert not re.search(r"^\s*(exit|set -e)\b", body, re.M)


def test_prune_expired_runs_once_right_before_the_change_log_prune():
    code = _code()
    at = [i for i, ln in enumerate(code) if ln == "prune_expired"]
    assert len(at) == 1
    assert code[at[0] + 1] == "prune_change_log"
