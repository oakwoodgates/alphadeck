"""The data-flag endpoints (split plan S3), end to end over a real DB holding REAL bars and split rows (the
``tests/fixtures/splits/s3`` extracts of prod: MVNC's tape and its needs-review 1-for-100 split, SKYT's tape that
stopped 2026-08-07). Display-only: the endpoints write nothing, and the call card beside them is unchanged.
"""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from repositories import split_repo
from tests.pipeline.test_data_flags import view

ASOF = "2026-10-22"
_CONTENT = set(split_repo.CONTENT_COLUMNS)


def _seed(db) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    tid = uuid.uuid4()
    ids: dict[str, uuid.UUID] = {}
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "data flags test", "n"),
        )
        for i, tk in enumerate(("MVNC", "SKYT")):
            v = view(tk, ASOF)
            sid = uuid.uuid4()
            ids[tk] = sid
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, tk, "2024-01-01"),
            )
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, tk, "—", sid),
            )
            cur.executemany(
                "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (DEFAULT_TENANT_ID, sid, b["d"], b["close"], b["volume"], b["d"])
                    for b in v["bars"]
                ],
            )
            for r in v["splits"]:
                split_repo.append_version(
                    db,
                    tenant_id=DEFAULT_TENANT_ID,
                    security_id=sid,
                    key_date=r["key_date"],
                    values={k: r.get(k) for k in _CONTENT if k in r}
                    | {"valid_from": r["polygon_execution_date"] or r["effective_date"]},
                    action="detect",
                    author="system:detect_splits",
                )
    db.commit()
    return tid, ids


def _count(db) -> tuple[int, ...]:
    with db.cursor() as cur:
        out = []
        for t in ("fact_price_eod", "security_split", "calls"):
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — literal table names
            out.append(cur.fetchone()["n"])
    return tuple(out)


def test_the_thesis_read_flags_the_real_issues_and_writes_nothing(client, db):
    tid, ids = _seed(db)
    before = _count(db)
    card_before = client.get(f"/theses/{tid}/call", params={"asof": ASOF}).json()
    r = client.get(f"/theses/{tid}/data-flags", params={"asof": ASOF})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thesis_id"] == str(tid) and body["names"] == 2
    by = {mm["ticker"]: mm for mm in body["members"]}
    mvnc, skyt = by["MVNC"], by["SKYT"]
    assert mvnc["marked"] and [f["kind"] for f in mvnc["flags"]] == ["split_needs_review"]
    review = mvnc["flags"][0]
    assert review["level"] == "in_doubt" and review["since"] == "2026-10-13"
    assert "ret_7d" in review["columns"] and "ret_1d" not in review["columns"]
    assert review["affects"] == ["price", "volume", "insider"] and not review["call_affected"]
    (split,) = mvnc["splits"]
    assert (split["label"], split["status"], split["factor"]) == (
        "1-for-100 reverse split",
        "needs_review",
        100.0,
    )
    assert skyt["marked"] and [f["kind"] for f in skyt["flags"]] == ["price_feed_stopped"]
    stopped = skyt["flags"][0]
    assert stopped["level"] == "known_wrong" and stopped["since"] == "2026-08-07"
    assert stopped["reason"].startswith("Price feed stopped: no price since 2026-08-07")
    assert "mktcap" in stopped["columns"]
    # display-only: nothing written, and the call card beside it is byte-identical
    assert _count(db) == before
    assert client.get(f"/theses/{tid}/call", params={"asof": ASOF}).json() == card_before


def test_the_union_read_lists_flagged_names_with_their_theses(client, db):
    tid, ids = _seed(db)
    r = client.get("/data-flags", params={"asof": ASOF})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thesis_id"] is None and body["names"] == 2
    assert [mm["ticker"] for mm in body["members"]] == ["MVNC", "SKYT"]
    assert all(
        mm["theses"] == [{"id": str(tid), "name": "data flags test"}] for mm in body["members"]
    )


def test_a_view_before_the_split_and_the_stop_flags_nothing(client, db):
    tid, _ = _seed(db)
    r = client.get(f"/theses/{tid}/data-flags", params={"asof": date(2026, 8, 5).isoformat()})
    assert r.status_code == 200, r.text
    # MVNC's split is not public yet and SKYT still traded: neither name has a flag or a split row
    assert r.json()["members"] == []
