"""The ratify CLIs and the two scopes (V2 W1a and W2c, docs/TENANT_SCOPES.md).

Purity is the workspace's own: ``pipeline.ratify_revenue_mix`` takes ``--workspace``, required, and refuses before
any database connection is opened when it is missing. Share counts and cash/burn are platform values set only by the
steward's write (``platform_values.steward``, V2 W2c): ``pipeline.ratify_shares`` and ``pipeline.ratify_cash_burn``
are deleted (approved decision D4 (a)), and so is ``pipeline.ratify_catalyst`` (company catalysts move to the
thesis's catalyst list in V2).
"""

from __future__ import annotations

import importlib.util

import pytest

from pipeline import ratify_revenue_mix


def test_ratify_revenue_mix_requires_a_workspace(capsys):
    with pytest.raises(SystemExit) as exc:
        ratify_revenue_mix.main(
            [
                "--ticker",
                "XXXX",
                "--segment",
                "core",
                "--pct",
                "50",
                "--source-url",
                "https://x.test",
                "--date",
                "2026-05-01",
            ]
        )
    assert exc.value.code == 2 and "--workspace" in capsys.readouterr().err


@pytest.mark.parametrize(
    "module", ["pipeline.ratify_shares", "pipeline.ratify_cash_burn", "pipeline.ratify_catalyst"]
)
def test_the_platform_value_ratify_clis_are_gone(module):
    """No shell path writes a share count, cash/burn or a company catalyst: the steward's write is the one way."""
    assert importlib.util.find_spec(module) is None
