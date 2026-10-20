"""Loaders over ``tests/fixtures/splits/s2`` (split plan S2) — REAL data only (see its README): the stored
price tapes of the split companies and their split-list rows (read-only from the dev database, which is seeded
one-way from prod), the vendor's own Yahoo pulls the split-tape diagnosis made on 2026-10-20, real Form 4 rows
and real 10-Q cover share counts. Values are used exactly as extracted; a helper only reshapes them.

The split rows carry their REAL statuses: MI / WNRS / ATRO still need review on the list. A test that needs one
of them in force says so by naming it in ``confirmed=`` — the stand-in for the operator's confirm, never a
value of its own (the ratio and date are the list's own).
"""

from __future__ import annotations

import gzip
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from db.session import DEFAULT_TENANT_ID
from domain.splits import STATUS_CONFIRMED, STATUS_NEEDS_REVIEW, ListedSplit
from ingest.prices.eod_loader import parse_yahoo_chart
from pipeline import split_repair as sr
from repositories import split_repo

FX = Path(__file__).resolve().parent / "fixtures" / "splits" / "s2"
EVIDENCE = (
    Path(__file__).resolve().parents[1] / "pipeline" / "split_evidence" / "polygon_raw_2026-10.json"
)
# the market day the tapes were extracted on (the dev DB's newest bar is 2026-10-19)
TODAY = date(2026, 10, 20)


@cache
def _tapes() -> dict[str, Any]:
    with gzip.open(FX / "tapes.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)["tapes"]


def _dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def tape(tk: str) -> dict[str, Any]:
    return _tapes()[tk]


def rows(tk: str) -> list[dict[str, Any]]:
    """The latest stored version per date, as price-writer rows (Decimal OHLCV)."""
    return [
        {
            "d": date.fromisoformat(d),
            "open": _dec(o),
            "high": _dec(h),
            "low": _dec(lo),
            "close": _dec(c),
            "volume": _dec(v),
        }
        for d, o, h, lo, c, v in tape(tk)["latest"]
    ]


def bars(tk: str) -> list[sr.Bar]:
    def f(x: Decimal | None) -> float | None:
        return None if x is None else float(x)

    return [
        sr.Bar(r["d"], f(r["open"]), f(r["high"]), f(r["low"]), f(r["close"]), f(r["volume"]))
        for r in rows(tk)
    ]


def versions(tk: str) -> list[tuple[dict[str, Any], datetime]]:
    """EVERY stored version (BTOC / OPTT only), oldest first: ``(row, recorded_at)``."""
    out = []
    for d, o, h, lo, c, v, rec in tape(tk)["versions"]:
        out.append(
            (
                {
                    "d": date.fromisoformat(d),
                    "open": _dec(o),
                    "high": _dec(h),
                    "low": _dec(lo),
                    "close": _dec(c),
                    "volume": _dec(v),
                },
                datetime.fromisoformat(rec),
            )
        )
    return out


def splits(tk: str, *, confirmed: tuple[str, ...] = ()) -> list[ListedSplit]:
    """The company's split-list rows; a needs-review split of a ticker named in ``confirmed`` reads as
    confirmed with the list's own values (the operator's confirm, stood in)."""
    out = []
    for s in tape(tk)["splits"]:
        status = s["status"]
        if tk in confirmed and status == STATUS_NEEDS_REVIEW:
            status = STATUS_CONFIRMED
        out.append(
            ListedSplit(
                key_date=date.fromisoformat(s["key_date"]),
                effective_date=date.fromisoformat(s["effective_date"]),
                ratio_from=Decimal(s["ratio_from"]),
                ratio_to=Decimal(s["ratio_to"]),
                status=status,
                sec_accession=s["sec_accession"],
            )
        )
    return out


def yahoo(sym: str) -> list[dict[str, Any]]:
    """The vendor's real 2026-10-20 pull (the diagnosis's ``fetch_eod`` mirror), parsed by the price leg."""
    with gzip.open(FX / f"yahoo_{sym}.json.gz", "rt", encoding="utf-8") as fh:
        return parse_yahoo_chart(json.load(fh))


@cache
def _evidence() -> dict[str, Any]:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))["prints"]


def spots(tk: str) -> dict[date, float]:
    """Polygon's real unadjusted closes for ``tk`` (the committed repair evidence)."""
    return {date.fromisoformat(d): float(v["close"]) for d, v in _evidence().get(tk, {}).items()}


def insider_rows(tk: str) -> list[dict[str, Any]]:
    blob = json.loads((FX / "insider.json").read_text(encoding="utf-8"))
    out = []
    for r in blob["rows"]:
        if r["ticker"] != tk:
            continue
        row = dict(r)
        row["valid_from"] = date.fromisoformat(r["valid_from"])
        for k in ("price", "shares", "usd"):
            row[k] = None if r[k] is None else Decimal(r[k])
        row["accepted"] = None if r["accepted"] is None else datetime.fromisoformat(r["accepted"])
        out.append(row)
    return out


def shares_rows(tk: str) -> list[dict[str, Any]]:
    blob = json.loads((FX / "shares.json").read_text(encoding="utf-8"))
    return [r for r in blob["rows"] if r["ticker"] == tk]


# --- DB seeding (a test's own per-worktree DB; the ``db`` fixture truncates it) -----------------------------


def seed_security(db, tk: str) -> uuid.UUID:
    """The company's master row, under its real id / ticker / name."""
    t = tape(tk)
    sid = uuid.UUID(t["security_id"])
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, price_symbol, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, t["ticker"], t["name"], t["price_symbol"], "2020-01-01"),
        )
    return sid


def seed_splits(db, sid: uuid.UUID, tk: str, *, confirmed: tuple[str, ...] = ()) -> None:
    """The company's split-list rows as S1's backfill wrote them (recorded_at = now); a ticker in
    ``confirmed`` then gets S1's own operator confirm (``split_repo.confirm`` with Polygon's values — the
    path the dev check uses), never a hand-made status."""
    for s in tape(tk)["splits"]:
        values = {
            "effective_date": date.fromisoformat(s["effective_date"]),
            "ratio_from": Decimal(s["ratio_from"]),
            "ratio_to": Decimal(s["ratio_to"]),
            "status": s["status"],
            "confirmed_via": s["confirmed_via"],
            "value_source": "polygon" if s["status"] == STATUS_NEEDS_REVIEW else "both",
            "sec_accession": s["sec_accession"],
            "sec_effective_date": (
                None
                if s["sec_effective_date"] is None
                else date.fromisoformat(s["sec_effective_date"])
            ),
            "sec_ratio_from": _dec(s["sec_ratio_from"]),
            "sec_ratio_to": _dec(s["sec_ratio_to"]),
            "polygon_execution_date": (
                None
                if s["polygon_execution_date"] is None
                else date.fromisoformat(s["polygon_execution_date"])
            ),
            "polygon_split_from": Decimal(s["ratio_from"]),
            "polygon_split_to": Decimal(s["ratio_to"]),
            "valid_from": date.fromisoformat(s["valid_from"]),
        }
        v = split_repo.append_version(
            db,
            tenant_id=DEFAULT_TENANT_ID,
            security_id=sid,
            key_date=date.fromisoformat(s["key_date"]),
            values=values,
            action=split_repo.ACTION_BACKFILL,
            author=split_repo.AUTHOR_BACKFILL,
        )
        if tk in confirmed and s["status"] == STATUS_NEEDS_REVIEW:
            split_repo.confirm(db, v.id, source="polygon", tenant_id=DEFAULT_TENANT_ID, today=TODAY)
