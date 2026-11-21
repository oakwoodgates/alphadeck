"""``GET /record/start`` (V2 W8a): the first night on the signed-in user's OWN workspace's record.

Real sign-ins on three provisioned workspaces (the ``world`` fixture of ``test_workspace_isolation``: A records a
call on its own card, B and the steward S record nothing, none of them is the data tenant). The answer is
``{"first_night": <the workspace's first honest night> | null}``: null is the honest "no record yet", never an
error and never a zero date. One workspace's nights never reach another's answer: an EARLIER night recorded in
B, and an even earlier one in the data tenant, leave A's answer where it was; a reconstructed row (a backfill's,
never the record of what the system said that night) is not the start.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from pipeline.call_for_thesis import call_for_thesis
from tests.app import test_workspace_isolation as isolation
from tests.scoreboard.helpers import persist_thesis

# the isolation proof's three real workspaces and sign-ins (A recorded one call; B and the steward S none)
world = isolation.world
_promote = isolation._promote


def _start(c: TestClient) -> dict:
    r = c.get("/record/start")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_workspace_with_no_record_reads_null_and_the_route_needs_a_sign_in(world):
    assert _start(world["b"]) == {"first_night": None}
    assert _start(world["s"]) == {"first_night": None}  # a steward reads its own workspace too
    anonymous = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert anonymous.get("/record/start").status_code == 401


def test_each_workspace_reads_its_own_first_night_and_never_another_s(db, world):
    asof = world["asof"]
    assert _start(world["a"]) == {"first_night": asof.isoformat()}

    # B starts its record a week EARLIER than A, on its own card
    r = _promote(world["b"], f"B card {uuid.uuid4().hex[:8]}", world["sid"])
    assert r.status_code == 200, r.text
    b_id = uuid.UUID(r.json()["id"])
    call_for_thesis(db, b_id, asof - timedelta(days=7), record=True, workspace=world["w_b"])
    # the data tenant holds an even earlier night (a row no workspace owns)
    data_card = persist_thesis(db, world["sid"])
    call_for_thesis(db, data_card.id, asof - timedelta(days=14), record=True)
    db.commit()

    assert _start(world["a"]) == {
        "first_night": asof.isoformat()
    }  # A's own, untouched by B or DATA
    assert _start(world["b"]) == {"first_night": (asof - timedelta(days=7)).isoformat()}
    assert _start(world["s"]) == {"first_night": None}


def test_a_reconstructed_row_is_not_the_start_of_the_record(db, world):
    asof = world["asof"]
    with db.cursor() as cur:  # a backfill's row for an earlier night, on A's card, in A's workspace
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, reconstructed) "
            "SELECT tenant_id, thesis_id, %s, state, verdict, card, true FROM calls "
            "WHERE thesis_id = %s LIMIT 1",
            (asof - timedelta(days=30), world["a_id"]),
        )
    db.commit()
    assert _start(world["a"]) == {"first_night": asof.isoformat()}
