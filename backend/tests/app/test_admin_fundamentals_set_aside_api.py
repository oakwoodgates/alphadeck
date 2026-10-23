"""The Admin status's one quiet line for migration 0062: how many stored fundamentals versions are set aside.

REAL data: LFMD's stored pre-#417 revenue (``tests/pipeline/test_set_aside_fundamentals.py`` explains the row),
set aside by the CLI's own ``scan`` + ``apply``, then revoked. DB-backed (skips without Postgres).
"""

from __future__ import annotations

import math

from ingest.edgar.client import EdgarClient
from pipeline import set_aside_fundamentals as SA
from tests.fundamentals_fixtures import companyfacts_cache
from tests.ingest.test_revenue_label import _security, _store_as_prod_held_it


def test_the_status_counts_the_active_set_asides(client, db, tmp_path):
    assert client.get("/admin/status").json()["fundamentals_set_aside"] == 0
    sec = _security(db, "LFMD")
    _store_as_prod_held_it(db, sec, "LFMD")
    edgar = EdgarClient(
        cache_dir=companyfacts_cache(tmp_path / "edgar", "LFMD"),
        allow_live=False,
        cache_ttl_s=math.inf,
    )
    res = SA.scan(db, client=edgar)
    db.rollback()
    SA.apply(db, res)
    assert client.get("/admin/status").json()["fundamentals_set_aside"] == 1
    SA.revoke(db, [sid for sid, _ in res.appended], reason="test", apply=True)
    assert client.get("/admin/status").json()["fundamentals_set_aside"] == 0
