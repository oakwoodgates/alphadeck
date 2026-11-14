"""The ratify CLIs and the two scopes (V2 W1a, docs/TENANT_SCOPES.md).

Share counts and cash/burn are platform data: ``pipeline.ratify_shares`` and ``pipeline.ratify_cash_burn`` take no
workspace and write the data tenant. Purity is the workspace's own: ``pipeline.ratify_revenue_mix`` takes
``--workspace``, required, and refuses before any database connection is opened when it is missing.
(``pipeline.ratify_catalyst`` is deleted: company catalysts move to the thesis's catalyst list in V2.)
"""

from __future__ import annotations

import pytest

from domain.scopes import DATA_TENANT_ID
from pipeline import ratify_cash_burn, ratify_revenue_mix, ratify_shares


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


def _rows(db, table: str, security_id):
    db.rollback()  # see the CLI's own committed write, not this connection's old snapshot
    with db.cursor() as cur:
        cur.execute(
            f"SELECT tenant_id, ratified_by FROM {table} WHERE security_id = %s",  # noqa: S608
            (security_id,),
        )
        return [(r["tenant_id"], r["ratified_by"]) for r in cur.fetchall()]


def test_ratify_shares_writes_the_data_tenant(db, security_id):
    ratify_shares.main(
        [
            "--ticker",
            "DEVCO",
            "--shares",
            "1000",
            "--source-url",
            "https://x.test/10q",
            "--date",
            "2026-05-01",
        ]
    )
    assert _rows(db, "fact_shares_outstanding", security_id) == [(DATA_TENANT_ID, "operator")]


def test_ratify_cash_burn_writes_the_data_tenant(db, security_id):
    ratify_cash_burn.main(
        [
            "--ticker",
            "DEVCO",
            "--cash",
            "1000",
            "--quarterly-burn",
            "10",
            "--source-url",
            "https://x.test/10q",
            "--date",
            "2026-05-01",
        ]
    )
    assert _rows(db, "fact_cash_burn", security_id) == [(DATA_TENANT_ID, "operator")]


@pytest.mark.parametrize("module", [ratify_shares, ratify_cash_burn])
def test_the_platform_ratify_clis_take_no_tenant(module, capsys):
    """A platform fact has one home; neither CLI offers a flag that could aim it elsewhere."""
    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--workspace" not in out and "--tenant-id" not in out
