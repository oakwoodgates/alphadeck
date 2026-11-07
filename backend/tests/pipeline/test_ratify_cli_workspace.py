"""The ratify CLIs write a ratified fact under a workspace, so they take it explicitly (auth PR 2, B4): there is no
deployment-wide default tenant any more (``ALPHADECK_TENANT_ID`` is retired). The parse refuses before any
database connection is opened."""

from __future__ import annotations

import pytest

from pipeline import ratify_catalyst, ratify_shares


def test_ratify_shares_requires_a_workspace(capsys):
    with pytest.raises(SystemExit) as exc:
        ratify_shares.main(
            [
                "--ticker",
                "XXXX",
                "--shares",
                "1000",
                "--source-url",
                "https://x.test",
                "--date",
                "2026-05-01",
            ]
        )
    assert exc.value.code == 2 and "--workspace" in capsys.readouterr().err


def test_ratify_catalyst_requires_a_workspace(capsys):
    with pytest.raises(SystemExit) as exc:
        ratify_catalyst.main(
            [
                "--ticker",
                "XXXX",
                "--type",
                "contract",
                "--grade",
                "core",
                "--label",
                "x",
                "--source-url",
                "https://x.test",
                "--date",
                "2026-05-01",
            ]
        )
    assert exc.value.code == 2 and "--workspace" in capsys.readouterr().err
