"""V2 W6b: the WIPE GUARD. A calendar row whose counting entry still counts cannot be removed by any list writer.

Every writer of the ``catalyst`` table goes through ``thesis_repo.set_catalysts`` (the route, the SPAC Radar's
"+ vote catalyst" through the route, the seed's boot write), so the guard lives there: a list that would drop the
row of an entry whose newest version counts is REFUSED (the route: 409 and a sentence naming the entry), and NOTHING
is written. Once the entry is switched off the row can be removed; its versions stay in the store (history is never
deleted). A label edit on a counting row appends one label-only version. A promote touches neither table.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

import pytest

from domain.thesis import Catalyst, Thesis
from pipeline import seed
from repositories import thesis_catalyst_repo, thesis_repo
from tests.thesis_catalyst_fixtures import card


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        return cur.fetchone()["n"]


def _counts(db) -> tuple[int, int]:
    return _count(db, "catalyst"), _count(db, "fact_thesis_catalyst")


def _switch_on(db, tid, entry, sids) -> None:
    thesis_catalyst_repo.append_version(
        db,
        tid,
        entry,
        counts=True,
        grade="core",
        security_ids=[sids[0]],
        citation="https://example.com/doe",
        relevant_until=None,
        label="DOE loan",
        user_id=None,
    )
    db.commit()


def test_a_save_that_omits_a_counting_row_is_refused_and_writes_nothing(client, db, pinned_today):
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    before = _counts(db)
    r = client.put(f"/theses/{tid}/catalysts", json=[{"label": "something else"}])
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "“DOE loan” counts toward the call. Turn that off before removing it."
    )
    assert _counts(db) == before
    assert [
        c.id for c in thesis_repo.get(db, tid, workspace=thesis_repo_ws(db, tid)).catalysts
    ] == [entry]


def thesis_repo_ws(db, tid):
    with db.cursor() as cur:
        cur.execute("SELECT tenant_id FROM thesis WHERE id = %s", (tid,))
        return cur.fetchone()["tenant_id"]


def test_an_old_client_without_ids_gets_409_not_a_silent_loss(client, db, pinned_today):
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    before = _counts(db)
    # exactly what the pre-W6b editors sent: the same rows, no ids
    r = client.put(
        f"/theses/{tid}/catalysts",
        json=[{"label": "DOE loan", "kind": "regulatory", "when_date": "2026-03-01"}],
    )
    assert r.status_code == 409, r.text
    assert _counts(db) == before


def test_a_save_that_keeps_the_counting_row_passes_and_appends_nothing(client, db, pinned_today):
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    before = _counts(db)
    r = client.put(
        f"/theses/{tid}/catalysts",
        json=[
            {
                "id": str(entry),
                "label": "DOE loan",
                "kind": "regulatory",
                "when_date": "2026-03-01",
            },
            {"label": "a second row"},
        ],
    )
    assert r.status_code == 200, r.text
    assert _counts(db) == (before[0] + 1, before[1])  # one new calendar row, no new version


def test_a_label_edit_on_a_counting_row_appends_one_label_only_version(client, db, pinned_today):
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    (first,) = thesis_catalyst_repo.current(db, tid)
    before = _counts(db)
    body = [{"id": str(entry), "label": "DOE loan guarantee", "kind": "regulatory"}]
    r = client.put(f"/theses/{tid}/catalysts", json=body)
    assert r.status_code == 200, r.text
    assert _counts(db) == (before[0], before[1] + 1)
    (now,) = thesis_catalyst_repo.current(db, tid)
    assert now["label"] == "DOE loan guarantee" and now["counts"]
    assert now["grade"] == first["grade"] and now["valid_from"] == first["valid_from"]
    assert now["supersedes"] == first["id"]
    # the same save again: store-on-diff, nothing appended
    r = client.put(f"/theses/{tid}/catalysts", json=body)
    assert r.status_code == 200, r.text
    assert _counts(db) == (before[0], before[1] + 1)


def test_once_switched_off_the_row_can_be_removed_and_its_history_stays(client, db, pinned_today):
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    r = client.put(f"/theses/{tid}/catalysts/{entry}/counting", json={"counts": False})
    assert r.status_code == 200, r.text
    versions = _count(db, "fact_thesis_catalyst")
    assert versions == 2
    r = client.put(f"/theses/{tid}/catalysts", json=[])
    assert r.status_code == 200, r.text
    assert _counts(db) == (0, 2)  # the row is gone; both versions stay


def test_the_repository_guard_covers_every_caller(db):
    """The guard is in ``set_catalysts`` itself, so a direct caller (the seed) cannot drop a counting row."""
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    tenant = thesis_repo_ws(db, tid)
    before = _counts(db)
    with pytest.raises(thesis_repo.CountingCatalystRemoval) as e:
        thesis_repo.set_catalysts(db, tid, [], tenant_id=tenant)
    assert "DOE loan" in str(e.value)
    db.rollback()
    assert _counts(db) == before


def test_the_seed_rewrite_with_the_row_present_passes(db):
    """``seed._persist_thesis`` re-writes a card's list on every seed; with its row (the same id) present it passes
    and appends nothing."""
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    thesis = thesis_repo.get(db, tid, workspace=thesis_repo_ws(db, tid))
    before = _counts(db)
    seed._persist_thesis(db, thesis)
    db.commit()
    assert _counts(db) == before
    # the seed's list WITHOUT the row is refused like any other writer
    stripped = thesis.model_copy(
        update={"catalysts": [Catalyst(id=uuid.uuid4(), label="other", when_date=date(2026, 3, 1))]}
    )
    with pytest.raises(thesis_repo.CountingCatalystRemoval):
        seed._persist_thesis(db, stripped)
    db.rollback()
    assert _counts(db) == before


def test_a_promote_touches_neither_table(db):
    tid, sids, entry = card(db)
    _switch_on(db, tid, entry, sids)
    thesis: Thesis = thesis_repo.get(db, tid, workspace=thesis_repo_ws(db, tid))
    before = _counts(db)
    thesis_repo.upsert(db, thesis.model_copy(update={"narrative": "edited", "catalysts": []}))
    db.commit()
    assert _counts(db) == before
    assert thesis_catalyst_repo.counting_entry_ids(db, tid) == {entry}


def test_set_catalysts_is_the_only_writer_of_the_calendar_table():
    """The guard holds only if nothing bypasses ``set_catalysts``: a writer that deleted ``catalyst`` rows directly
    would orphan a counting entry (it would keep its versions with no row to switch it off). Source scan, no
    database: only ``repositories/thesis_repo.py`` may hold a write statement against the table."""
    write = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(\s+TABLE)?)\s+catalyst\b", re.I
    )
    backend = Path(__file__).resolve().parents[2]
    writers = sorted(
        path.relative_to(backend).as_posix()
        for path in backend.rglob("*.py")
        if not {"tests", ".venv", "build", "node_modules", "__pycache__"}
        & set(path.relative_to(backend).parts)
        and write.search(path.read_text(encoding="utf-8"))
    )
    assert writers == ["repositories/thesis_repo.py"]
