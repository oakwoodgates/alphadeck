"""Stored data is never held to a request limit (security hardening PR 3, the one hard constraint).

The request limits (``domain/text_types.py``) live on the REQUEST models only. A card whose stored rows exceed every
one of them (lengths, control characters, counts, dates), written straight through the repository writers with no
request in between, must still load everywhere stored data is read: ``thesis_repo.get``, the nightly's walk
(``list_all`` over every workspace, then the roster as of now), the API's reads and its call. If a limit had been put
on a domain model, this card would be unloadable everywhere.

"Resending old data": a promote is a full replace, so the editor resends the whole card. Resending this card
unchanged is refused with a 422 that names a field and its limit, and the stored card is left exactly as it was
(no bypass, no silent trim).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain import text_types as tt
from domain.enums import TermTier
from domain.scopes import WORKSPACE_ANY
from domain.thesis import (
    BasketMember,
    Catalyst,
    ExcludedName,
    KillCriterion,
    Segment,
    TermSetEntry,
    Thesis,
)
from repositories import thesis_repo

DATA = uuid.UUID("00000000-0000-0000-0000-000000000001")
CTRL = "\x01"  # a control character the database stores (a NUL it cannot)


def _long(n: int, ch: str = "x") -> str:
    return CTRL + ch * n


def _master_ids(db, n: int) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(n)]
    with db.cursor() as cur:
        cur.executemany(
            "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, '2026-01-01')",
            [(sid, DATA, f"OV{i}", f"Over limit {i}") for i, sid in enumerate(ids)],
        )
    return ids


@pytest.fixture
def over_limit_card(db) -> Thesis:
    """One card over every request limit, written through the repository writers (never a request)."""
    seg_labels = [
        _long(tt.LINE_MAX + 5, chr(ord("a") + i % 26)) + str(i) for i in range(tt.SEGMENTS_MAX + 1)
    ]
    segments = [Segment(label=s, descriptor=_long(tt.PROSE_MAX + 5)) for s in seg_labels]
    basket = [
        BasketMember(
            ticker=_long(tt.LINE_MAX + 5) + str(i),
            role=_long(tt.LINE_MAX + 5),
            detail=_long(tt.LINE_MAX + 5),
            segment=seg_labels[i % len(seg_labels)],
            thesis_fit=_long(tt.PROSE_MAX + 5),
            surfaced_terms=[_long(tt.TERM_MAX + 5)] * (tt.SURFACED_TERMS_MAX + 1) if i == 0 else [],
        )
        for i in range(tt.BASKET_MAX + 1)
    ]
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=_long(tt.LINE_MAX + 5),
        narrative=_long(tt.NARRATIVE_MAX + 5) + "\n",
        ticker=_long(tt.LINE_MAX + 5),
        basket=basket,
        segments=segments,
    )
    thesis_repo.upsert(db, thesis)
    thesis_repo.set_term_set(
        db,
        thesis.id,
        [
            TermSetEntry(term=_long(tt.TERM_MAX + 5) + str(i), tier=TermTier.SIGNAL)
            for i in range(tt.TERMS_MAX + 1)
        ],
    )
    thesis_repo.set_catalysts(
        db,
        thesis.id,
        [
            Catalyst(
                id=uuid.uuid4(),
                label=_long(tt.LINE_MAX + 5),
                kind=_long(tt.LINE_MAX + 5),
                when_label=_long(tt.LINE_MAX + 5),
                when_date=date(1901, 1, 1) if i % 2 else date(2999, 1, 1),
            )
            for i in range(tt.CATALYSTS_MAX + 1)
        ],
        tenant_id=DEFAULT_TENANT_ID,
    )
    thesis_repo.set_kill_criteria(
        db,
        thesis.id,
        [
            KillCriterion(id=uuid.uuid4(), text=_long(tt.PROSE_MAX + 5))
            for _ in range(tt.KILL_CRITERIA_MAX + 1)
        ],
        tenant_id=DEFAULT_TENANT_ID,
    )
    thesis_repo.set_exclusions(
        db,
        thesis.id,
        [
            ExcludedName(
                security_id=sid, ticker=_long(tt.LINE_MAX + 5), reason=_long(tt.PROSE_MAX + 5)
            )
            for sid in _master_ids(db, tt.EXCLUSIONS_MAX + 1)
        ],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    return thesis


def _stored(db, thesis_id: uuid.UUID) -> list[str]:
    """Content checksums of every row the card owns: the before/after proof that a refusal wrote nothing."""
    db.rollback()
    out = []
    with db.cursor() as cur:
        for table, key in (
            ("thesis", "id"),
            ("basket_member", "thesis_id"),
            ("basket_snapshot", "thesis_id"),
            ("catalyst", "thesis_id"),
            ("kill_criterion", "thesis_id"),
            ("thesis_exclusion", "thesis_id"),
        ):
            cur.execute(
                f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) AS h "  # noqa: S608
                f"FROM {table} t WHERE {key} = %s",
                (thesis_id,),
            )
            out.append(cur.fetchone()["h"])
    db.rollback()
    return out


def test_the_over_limit_card_loads_everywhere_stored_data_is_read(client, db, over_limit_card):
    # the repository
    got = thesis_repo.get(db, over_limit_card.id, workspace=DEFAULT_TENANT_ID)
    assert got is not None
    assert got.name == over_limit_card.name
    assert got.narrative.endswith("\n")  # stored as written: never trimmed on read
    assert len(got.basket) == tt.BASKET_MAX + 1
    assert len(got.basket[0].surfaced_terms) == tt.SURFACED_TERMS_MAX + 1
    assert len(got.term_set) == tt.TERMS_MAX + 1
    assert len(got.catalysts) == tt.CATALYSTS_MAX + 1
    assert {c.when_date for c in got.catalysts} == {date(1901, 1, 1), date(2999, 1, 1)}
    assert len(got.kill_criteria) == tt.KILL_CRITERIA_MAX + 1
    assert len(got.exclusions) == tt.EXCLUSIONS_MAX + 1
    # the segments come back with the three fixed links appended
    assert len(got.segments) >= tt.SEGMENTS_MAX + 1

    # the nightly's read path: the walk of every workspace, then the roster as of now
    walked = [
        t for t in thesis_repo.list_all(db, workspace=WORKSPACE_ANY) if t.id == over_limit_card.id
    ]
    assert len(walked) == 1 and len(walked[0].basket) == tt.BASKET_MAX + 1
    asof = thesis_repo.get_asof(db, over_limit_card.id, None, workspace=WORKSPACE_ANY)
    assert asof is not None and len(asof.basket) == tt.BASKET_MAX + 1

    # the API's reads and its call
    tid = over_limit_card.id
    r = client.get(f"/theses/{tid}")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["name"] == over_limit_card.name
    assert any(t["id"] == str(tid) for t in client.get("/theses").json())
    r = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})
    assert r.status_code == 200, r.text[:300]


def test_resending_the_over_limit_card_is_refused_by_name_and_changes_nothing(
    client, db, over_limit_card
):
    detail = client.get(f"/theses/{over_limit_card.id}").json()
    before = _stored(db, over_limit_card.id)

    r = client.post(
        "/workbench/theses",
        json={
            "id": detail["id"],
            "name": detail["name"],
            "narrative": detail["narrative"],
            "ticker": detail["ticker"],
            "basket": detail["basket"],
            "segments": detail["segments"],
        },
    )
    assert r.status_code == 422, r.text[:300]
    errors = r.json()["detail"]
    named = {tuple(e["loc"]) for e in errors}
    assert ("body", "name") in named
    by_loc = {tuple(e["loc"]): e["msg"] for e in errors}
    assert f"at most {tt.LINE_MAX} characters" in by_loc[("body", "name")]
    assert ("body", "basket") in named  # the row count, named by its field
    assert _stored(db, over_limit_card.id) == before

    r = client.put(f"/theses/{over_limit_card.id}/catalysts", json=detail["catalysts"])
    assert r.status_code == 422, r.text[:300]
    assert _stored(db, over_limit_card.id) == before
