"""One place per concern: only ``app/client_address.py`` reads the caller's network address (security PR 5).

A second reader would be a second, unguarded answer to "who is calling" (one that trusts the wrong header, or keys
every request behind nginx on nginx). A text scan of ``backend/app``.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"
_READS = re.compile(
    r"""request\.client\b|scope\[\s*["']client["']\s*\]|scope\.get\(\s*["']client["']"""
)


def test_only_client_address_reads_the_callers_address():
    offenders = [
        f"{p.relative_to(_APP)}:{n}"
        for p in sorted(_APP.rglob("*.py"))
        if p.name != "client_address.py"
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _READS.search(line)
    ]
    assert offenders == []


def test_the_scan_sees_each_form():
    for line in ("request.client.host", 'scope["client"]', "scope.get('client')"):
        assert _READS.search(line), line
    assert (_APP / "client_address.py").is_file()
