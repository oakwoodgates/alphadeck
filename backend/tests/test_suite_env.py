"""The suite never runs with a live Massive.com key (W6-4).

``tests/conftest.py`` removes ``MASSIVE_API_KEY`` and every ``ALPHADECK_POLYGON_*`` override (the vendor host, the
split path) from the test process before any test runs. With the key set on the host, a test that runs the daily
pass (the split detector, the tape cross-check, the reference reads) would otherwise send live requests on the
one free-plan key prod's jobs share. A test that needs the key sets a fake one itself (``monkeypatch.setenv``).
"""

from __future__ import annotations

import os

from domain.settings import Settings


def test_no_test_process_sees_the_massive_key_or_a_polygon_override():
    assert "MASSIVE_API_KEY" not in os.environ
    assert [n for n in os.environ if n.upper().startswith("ALPHADECK_POLYGON_")] == []
    fresh = Settings()  # what any reader building its settings now would get
    assert fresh.polygon_api_key is None
    assert fresh.polygon_base == "https://api.polygon.io"
