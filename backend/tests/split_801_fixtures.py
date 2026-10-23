"""Shared helpers for the Item 8.01 / 7.01 split-reader tests (2026-10-23) — every input is REAL
(``tests/fixtures/splits``, see its README): ENHA's 8-Ks, its submissions index as data.sec.gov served it, and
the 2026-10-22 Polygon rows (which list ENHA's 1-for-10 on 2026-10-28, one session before the first session
its 8-K states).

- ``seed_enha`` — ENHA in one live thesis, with the 8-K rows the nightly stores for it (each filing's REAL
  items and filed date, read from the fixture submissions index — nothing typed by hand); ``seed_member`` the
  same for any company (AIRE: its one item row as prod's nightly stored it).
- ``enha_polygon`` — the 2026-10-22 rows behind a fake socket (no network; the request carries a date only).
- ``OLD_ITEMS`` — the 8-K items the reader read before 2026-10-23, to reproduce the version prod holds.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.submissions import parse_item_codes
from ingest.polygon_splits import PolygonSplitSource
from tests.split_fixtures import FX

ENHA_CIK = "0001956439"
ENHA_8K = "0001628280-26-066196"  # Item 8.01, filed 2026-10-19: 1-for-10, split-adjusted trading 2026-10-29
ENHA_POLYGON_DAY = date(2026, 10, 28)
ENHA_FIRST_SESSION = date(2026, 10, 29)
OLD_ITEMS = frozenset({"3.03", "5.03"})
ROWS = FX / "polygon" / "rows_2026-10-22.json"
SINCE = date(2026, 7, 24)  # the 2026-10-22 pull's own query


def enha_8ks(
    lo: date = date(2026, 6, 30), hi: date = date(2026, 11, 7)
) -> list[tuple[str, date, list]]:
    """``(accession, filed, items)`` of every 8-K ENHA's REAL submissions index lists in ``[lo, hi]`` (the
    backfill window of the 2026-10-28 Polygon row)."""
    subs = json.loads((FX / "submissions" / f"CIK{ENHA_CIK}.json").read_text(encoding="utf-8"))
    r = subs["filings"]["recent"]
    out = []
    for i, form in enumerate(r["form"]):
        filed = date.fromisoformat(r["filingDate"][i])
        if form == "8-K" and lo <= filed <= hi:
            out.append((r["accessionNumber"][i], filed, parse_item_codes(r["items"][i]) or []))
    return out


# AIRE (reAlpha Tech) — its Item 7.01 8-K/A of 2026-09-23 restates a PAST 1-for-25 beside a date 266 days back;
# the item row as prod's nightly stored it. It has no split on the list.
AIRE_CIK = "0001859199"
AIRE_8KA = ("0001213900-26-102384", "8-K/A", date(2026, 9, 23), ["7.01", "9.01"])


def seed_member(db, ticker: str, cik: str, events: list[tuple[str, str, date, list]]) -> uuid.UUID:
    """One company in one live thesis, plus its stored 8-K item rows ``(accession, form, filed, items)``.
    Commits; returns its security id."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2025-01-01"),
        )
        tid = uuid.uuid4()
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "split list 8.01 test", "n"),
        )
        cur.execute(
            "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), DEFAULT_TENANT_ID, tid, 0, ticker, "—", sid),
        )
    for acc, form, filed, items in events:
        append_fact(
            db,
            "fact_corporate_event",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                "form": form,
                "items": items,
                "accession": acc,
                "filed": filed,
                "source_ref": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{acc.replace('-', '')}/{acc}-index.htm",
                "valid_from": filed,
            },
        )
    db.commit()
    return sid


def seed_enha(db) -> uuid.UUID:
    """ENHA in one live thesis, plus its stored 8-K item rows. Commits; returns its security id."""
    return seed_member(db, "ENHA", ENHA_CIK, [(a, "8-K", f, i) for a, f, i in enha_8ks()])


def enha_polygon(tmp_path: Path, *, today: date) -> PolygonSplitSource:
    body = ROWS.read_text(encoding="utf-8")
    return PolygonSplitSource(
        api_key="test-key-not-real",
        cache_dir=tmp_path / f"polygon_{today.isoformat()}",
        fetch=lambda url: body,
        today=lambda: today,
    )
