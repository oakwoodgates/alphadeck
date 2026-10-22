"""Loaders over ``tests/fixtures/splits/s5`` (split plan S5, the split-night hold) — REAL data only (see its
README): every stored version of each company's price bars around a real split night (or a real genuine big
move), with its real ``recorded_at``, and the company's split-list rows exactly as the list holds them. Values
are used as extracted; a helper only reshapes them.

Seeded into a test's own DB, the versions let the REAL public-clock machinery decide what a history view saw
that night (``db.public_clock``): a first version counts from its session settle, a vendor re-version from its
own ``recorded_at`` — unless a LISTED split re-scales the bar (S2), when every version counts from the settle
and the newest is read. So the same stored rows read as a mixed-scale tape without the list row and as one
scale with it: the list row is the only thing a golden varies.
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
from domain.thesis import BasketMember, Thesis
from repositories import split_repo, thesis_repo

FX = Path(__file__).resolve().parent / "fixtures" / "splits" / "s5"


@cache
def _cases() -> dict[str, Any]:
    with gzip.open(FX / "nights.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def case(tk: str) -> dict[str, Any]:
    return _cases()[tk]


def _dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def _day(v: str | None) -> date | None:
    return None if v is None else date.fromisoformat(v)


def split_rows(tk: str) -> list[dict[str, Any]]:
    """The company's split-list rows (newest version of each logical split) as typed dicts — the shape
    ``PointInTimeData.split_events`` returns."""
    out = []
    for s in case(tk)["splits"]:
        row: dict[str, Any] = dict(s)
        for k in (
            "key_date",
            "effective_date",
            "sec_filed",
            "sec_effective_date",
            "polygon_execution_date",
            "valid_from",
            "valid_to",
        ):
            row[k] = _day(s[k])
        for k in (
            "ratio_from",
            "ratio_to",
            "sec_ratio_from",
            "sec_ratio_to",
            "polygon_split_from",
            "polygon_split_to",
        ):
            row[k] = _dec(s[k])
        row["recorded_at"] = datetime.fromisoformat(s["recorded_at"])
        out.append(row)
    return out


def seed(db, tk: str, *, listed: bool) -> uuid.UUID:
    """Seed the company (a fresh master row under its real ticker / name / CIK), EVERY stored version of its
    bars with the real ``recorded_at``, and — when ``listed`` — its split-list rows as the list holds them
    (one appended version each, recorded now: on the public clock a split counts from its OWN publication,
    never from the day a row was written). Commits."""
    c = case(tk)
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, tk, c["name"], c["cik"], "2020-01-01"),
        )
        for d, o, h, lo, cl, v, vf, rec in c["versions"]:
            cur.execute(
                "INSERT INTO fact_price_eod (tenant_id, security_id, d, open, high, low, close, volume, "
                "valid_from, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    DEFAULT_TENANT_ID,
                    sid,
                    d,
                    _dec(o),
                    _dec(h),
                    _dec(lo),
                    _dec(cl),
                    _dec(v),
                    vf,
                    datetime.fromisoformat(rec),
                ),
            )
    if listed:
        for row in split_rows(tk):
            split_repo.append_version(
                db,
                tenant_id=DEFAULT_TENANT_ID,
                security_id=sid,
                key_date=row["key_date"],
                values={k: row.get(k) for k in split_repo.CONTENT_COLUMNS},
                action=row["action"],
                author=row["author"],
            )
    db.commit()
    return sid


def seed_thesis(
    db, members: list[tuple[str, uuid.UUID]], *, name: str = "split-night fixture"
) -> uuid.UUID:
    """A thesis whose basket is exactly these (ticker, security) members. Commits."""
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="split-night hold golden",
        ticker=members[0][0],
        basket=[BasketMember(ticker=tk, role="member", security_id=sid) for tk, sid in members],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis.id
