"""Shared helpers for the split-list tests (split plan S1) — every input is a REAL filing / response from
``tests/fixtures/splits`` (see its README); nothing here invents a filing, a ratio or a date.

- ``filing`` / ``documents`` — a real SEC submission (gunzipped), and its documents.
- ``polygon_pages`` / ``polygon_rows`` / ``polygon_row`` — the real Polygon split-reference pages.
- ``edgar_cache`` — a tmp EDGAR cache dir holding the real filings + submissions indexes under the keys the
  EDGAR client reads (``forms/<acc>/<acc>.txt``, ``submissions/CIK<10>.json``).
- ``polygon_source`` — a ``PolygonSplitSource`` whose socket is a fake serving the real pages (no network).
- ``seed_union`` — the basket the pipeline/API tests run on: real tickers / CIKs, and the 8-K item rows the
  nightly would have stored for them (each filing's REAL items and filed date, from its SGML header).
"""

from __future__ import annotations

import gzip
import uuid
from datetime import date
from pathlib import Path

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.splits import PolygonEvidence
from ingest.edgar.split_filing import FilingDocument, submission_documents
from ingest.polygon_splits import PolygonSplitSource, parse_splits_page

FX = Path(__file__).resolve().parent / "fixtures" / "splits"
PAGES = [FX / "polygon" / f"page{i}.json" for i in (1, 2, 3)]


def filing(name: str) -> str:
    """A fixture filing's text (``<accession>.txt`` or one of the two single documents)."""
    return gzip.decompress((FX / "sec" / f"{name}.gz").read_bytes()).decode("utf-8", "replace")


def documents(name: str) -> list[FilingDocument]:
    raw = filing(name)
    if name == "MI_ex991_0930.htm":
        return [FilingDocument(type="EX-99.1", filename=name, raw=raw)]
    return submission_documents(raw)


def polygon_pages() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in PAGES]


# the real rows (from the dev backfill's 2024-10-01 pull) for the filings added from that run
DEV_ROWS = FX / "polygon" / "rows_2024-10.json"


def polygon_rows() -> list[PolygonEvidence]:
    """Every real Polygon row in the fixtures: the three 2025-08 pages, then the 2024-10 extract."""
    out: list[PolygonEvidence] = []
    for text in [*polygon_pages(), DEV_ROWS.read_text(encoding="utf-8")]:
        rows, _, _ = parse_splits_page(text)
        out.extend(rows)
    return out


def polygon_row(ticker: str, execution_date: str) -> PolygonEvidence:
    d = date.fromisoformat(execution_date)
    return next(r for r in polygon_rows() if r.ticker == ticker and r.execution_date == d)


def polygon_source(
    tmp_path: Path, *, today: date = date(2026, 10, 20)
) -> tuple[PolygonSplitSource, list]:
    """``(source, requested_urls)`` — the fake socket serves page 1 for the first request and follows the
    real ``next_url`` cursors after it."""
    texts = polygon_pages()
    nexts = {}
    for i, t in enumerate(texts[:-1]):
        _, nxt, _ = parse_splits_page(t)
        nexts[nxt] = texts[i + 1]
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return nexts.get(url, texts[0])

    src = PolygonSplitSource(
        api_key="test-key-not-real",
        cache_dir=tmp_path / "polygon_cache",
        fetch=fetch,
        today=lambda: today,
    )
    return src, calls


# --- the EDGAR cache --------------------------------------------------------------------------------------------


def edgar_cache(tmp_path: Path) -> Path:
    root = tmp_path / "edgar_cache"
    for gz in (FX / "sec").glob("*.txt.gz"):
        acc = gz.name[: -len(".txt.gz")]
        dest = root / "forms" / acc / f"{acc}.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(gzip.decompress(gz.read_bytes()))
    sub = root / "submissions"
    sub.mkdir(parents=True, exist_ok=True)
    for js in (FX / "submissions").glob("*.json"):
        (sub / js.name).write_bytes(js.read_bytes())
    return root


# --- the basket -------------------------------------------------------------------------------------------------

# ticker -> (cik, price_symbol, the 8-K item rows the nightly stores: (accession, filed, items))
UNION = {
    "BTOC": ("0001972529", None, [("0001213900-26-108468", "2026-10-09", ["5.03", "9.01"])]),
    "LRHC": (
        "0001879403",
        None,
        [("0001213900-26-097860", "2026-09-08", ["3.03", "5.03", "7.01", "9.01"])],
    ),
    "OPAD": (
        "0001825024",
        None,
        [("0001193125-26-263723", "2026-06-09", ["5.03", "5.07", "9.01"])],
    ),
    "GGRP": (
        "0001854445",
        "BTLN",
        [("0001493152-26-043847", "2026-09-23", ["3.03", "5.03", "7.01", "9.01"])],
    ),
    # a 6-K filer: its window is read from the REAL submissions index in the fixture cache
    "MI": ("0001958713", None, []),
}


def seed_union(db) -> dict[str, uuid.UUID]:
    """The members above in one live thesis, plus their stored 8-K item rows. Commits."""
    ids: dict[str, uuid.UUID] = {}
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "split list test", "n"),
        )
        for i, (ticker, (cik, price_symbol, _)) in enumerate(UNION.items()):
            sid = uuid.uuid4()
            ids[ticker] = sid
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, price_symbol, valid_from) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, ticker, cik, price_symbol, "2025-01-01"),
            )
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    for ticker, (cik, _, events) in UNION.items():
        for acc, filed, items in events:
            append_fact(
                db,
                "fact_corporate_event",
                {
                    "tenant_id": DEFAULT_TENANT_ID,
                    "security_id": ids[ticker],
                    "form": "8-K",
                    "items": items,
                    "accession": acc,
                    "filed": date.fromisoformat(filed),
                    "source_ref": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{acc.replace('-', '')}/{acc}-index.htm",
                    "valid_from": date.fromisoformat(filed),
                },
            )
    db.commit()
    return ids
