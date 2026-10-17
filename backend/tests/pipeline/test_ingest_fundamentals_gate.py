"""Q17 at the leg: ``pipeline.ingest_fundamentals`` builds a RECURRING client (so companyfacts is
filing-gated by construction — nothing for a caller to forget), a gated name still re-parses its cached
document, and a negative-cached 404 (X8) is reported as a KNOWN absence, quietly. DB-backed (``db``).
"""

from __future__ import annotations

import json
import os
import time
import uuid

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from pipeline import ingest_fundamentals as IF

_CIK = "0001234567"


def _add_master(db, *, ticker: str, cik: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01", "equity"),
        )
    db.commit()
    return sid


def _make_thesis(db, members) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "Gate thesis", "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _fundamentals_count(db) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) n FROM fact_fundamentals WHERE tenant_id = %s", (DEFAULT_TENANT_ID,)
        )
        return cur.fetchone()["n"]


def test_the_leg_builds_a_RECURRING_client_by_default(db, monkeypatch):
    """The cron calls ``ingest_fundamentals(conn, allow_live=True)`` with no client and no flag; the gate
    must come from the construction, so the default client carries the recurring dial."""
    seen: list[dict] = []

    class _Recording(EdgarClient):
        def __init__(self, **kwargs):
            seen.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(IF, "EdgarClient", _Recording)
    IF.ingest_fundamentals(db, allow_live=False)
    assert seen and seen[0]["cache_ttl_s"] == RECURRING_CACHE_TTL_S


def test_a_gated_name_still_stores_what_its_cached_document_holds(db, tmp_path):
    """Not re-pulling never means skipping: a name the gate leaves alone re-parses its CACHED companyfacts
    and appends any version not yet stored. Counted on the table, with ZERO fetches."""
    sid = _add_master(db, ticker="GATE", cik=_CIK)
    tid = _make_thesis(db, [("GATE", sid)])
    cf = {
        "cik": 1234567,
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "val": 100.0,
                                "accn": "0001234567-26-000010",
                                "fy": 2026,
                                "fp": "Q2",
                                "filed": "2026-08-05",
                            }
                        ]
                    }
                }
            }
        },
    }
    p = tmp_path / "companyfacts" / f"CIK{_CIK}.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(cf), encoding="utf-8")
    old = time.time() - 2 * 24 * 3600
    os.utime(p, (old, old))
    s = tmp_path / "submissions" / f"CIK{_CIK}.json"
    s.parent.mkdir(parents=True)
    s.write_text(
        json.dumps({"filings": {"recent": {"form": [], "accessionNumber": []}}}), encoding="utf-8"
    )
    client = EdgarClient(
        cache_dir=tmp_path, allow_live=True, user_agent="t", cache_ttl_s=RECURRING_CACHE_TTL_S
    )
    fetched: list[str] = []
    client._fetch = lambda url: fetched.append(url) or "{}"  # type: ignore[method-assign]

    before = _fundamentals_count(db)
    res = IF.ingest_fundamentals(db, thesis_id=tid, edgar_client=client)

    assert fetched == []  # gated: nothing filed since the fetch, inside the backstop
    assert [r.appended for r in res] == [1] and res[0].error is None
    assert _fundamentals_count(db) == before + 1


def test_a_negative_cached_404_is_a_KNOWN_absence(db, tmp_path):
    """X8 at the leg: the client answers a known 404 from its marker, and the leg marks that name
    ``known_absent`` (the cron prints it as one quiet count) — still recorded as the name's error, never
    dropped from the result."""
    sid = _add_master(db, ticker="NOCF", cik=_CIK)
    tid = _make_thesis(db, [("NOCF", sid)])
    marker = tmp_path / "companyfacts" / f"CIK{_CIK}.json.404"
    marker.parent.mkdir(parents=True)
    marker.write_text("https://data.sec.gov/x", encoding="utf-8")
    s = tmp_path / "submissions" / f"CIK{_CIK}.json"
    s.parent.mkdir(parents=True)
    s.write_text(
        json.dumps({"filings": {"recent": {"form": ["4"], "accessionNumber": ["a"]}}}),
        encoding="utf-8",
    )
    client = EdgarClient(
        cache_dir=tmp_path, allow_live=True, user_agent="t", cache_ttl_s=RECURRING_CACHE_TTL_S
    )
    fetched: list[str] = []
    client._fetch = lambda url: fetched.append(url) or "{}"  # type: ignore[method-assign]

    res = IF.ingest_fundamentals(db, thesis_id=tid, edgar_client=client)

    assert fetched == [] and client.live_fetches == 0
    assert len(res) == 1 and res[0].known_absent is True
    assert res[0].error and "404" in res[0].error
