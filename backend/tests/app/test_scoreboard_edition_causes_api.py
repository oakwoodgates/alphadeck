"""Editions S7 over the wire — the automatic cut's CAUSES on "what changed", and its NOTICE.

``GET /scoreboard/editions/diff?from=1&to=2``, where Edition 2 was cut from Edition 1 (its parent, with the
trial's summary and its proven ``edition_cause`` rows): a change in the proven set carries its stored cause, a
change on a night the rules trial re-tested carries ``late_data``, one on a night it did not re-test (Edition
1's nightly page) ``not_separated`` — and ``causes`` is true. Any other pair (``from=0``) has no single trial:
every cause null, ``causes`` false. ``GET /scoreboard/editions`` carries the seven-day notice, and the Admin
status its one line; Edition 1 (published, not cut) has none.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from repositories import editions_repo
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import keys_fired, persist_thesis, record_day

ENGINE = {"fingerprint": "f" * 64, "config_hash": "c" * 64, "code_sha": None}
CUT_1 = datetime(2026, 7, 11, 2, 0, tzinfo=timezone.utc)


def _security(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, 'CAU', '0000000401', 'Cause Corp', '2026-01-01')",
            (sid, DEFAULT_TENANT_ID),
        )
    db.commit()
    return sid


def _seed(db, *, with_trial: bool = True):
    """Edition 1 arms the company 07-09 .. 07-13 (07-08..07-10 its re-run pages, 07-13 a nightly page);
    Edition 2 — cut from it — never arms it. The trial re-tested 07-08..07-10 and proved 07-09 (and the
    07-09 episode)."""
    sid = _security(db)
    t = persist_thesis(db, sid, thesis_id=uuid.uuid4())
    conv, conf = keys_fired(sid, date(2026, 7, 9), conv_liveness=120, conf_liveness=120)
    warm = insider_event(security_id=sid, liveness=120).model_copy(
        update={"asof": date(2026, 7, 8)}
    )
    record_day(db, t, [warm], date(2026, 7, 8))
    nights = [date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 10)]
    e1_pages = [assemble_call(t, [warm], nights[0], DEFAULT_CONFIG)] + [
        assemble_call(t, [conv, conf], n, DEFAULT_CONFIG) for n in nights[1:]
    ]
    editions_repo.publish_edition(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        number=1,
        label="first re-run",
        causes=["initial"],
        engine=ENGINE,
        data_known_by=CUT_1,
        pages=e1_pages,
        entries=[],
        computed_at=CUT_1,
    )
    e1 = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1)
    editions_repo.append_pages(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        edition_id=e1.id,
        pages=[(assemble_call(t, [conv, conf], date(2026, 7, 13), DEFAULT_CONFIG), "nightly")],
        data_known_by=CUT_1 + timedelta(days=3),
        engine_fingerprint=ENGINE["fingerprint"],
        computed_at=CUT_1 + timedelta(days=3),
    )
    now = datetime.now(timezone.utc)
    e2_pages = [assemble_call(t, [warm], n, DEFAULT_CONFIG) for n in (*nights, date(2026, 7, 13))]
    trial = {
        "parent": str(e1.id),
        "previous": 1,
        "rules": True,
        "repair": False,
        "unexplained": False,
        "tested": {str(t.id): ["2026-07-08", "2026-07-10"]},
        "n_company_nights_changed": 3,
        "n_pages_changed": 4,
    }
    e2_id = editions_repo.publish_edition(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        number=2,
        label="rule change: tighter window · includes data that arrived since Edition 1",
        causes=["rule_change"],
        engine={**ENGINE, "fingerprint": "e" * 64},
        data_known_by=now,
        pages=e2_pages,
        entries=[],
        computed_at=now,
        parent_id=e1.id,
        reason={"trial": trial} if with_trial else {},
    )
    editions_repo.append_causes(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        edition_id=e2_id,
        rows=[
            {
                "thesis_id": t.id,
                "security_id": sid,
                "night": date(2026, 7, 9),
                "cause": "rule_change",
            },
            {
                "thesis_id": t.id,
                "security_id": sid,
                "arm_date": date(2026, 7, 9),
                "cause": "rule_change",
            },
        ],
    )
    editions_repo.append_event(db, tenant_id=DEFAULT_TENANT_ID, edition_id=e1.id, kind="closed")
    db.commit()
    return t, sid


def test_the_cut_pair_carries_causes_and_every_other_change_says_what_the_trial_implies(client, db):
    t, sid = _seed(db)
    body = client.get("/scoreboard/editions/diff", params={"from": 1, "to": 2}).json()
    assert body["causes"] is True
    [th] = body["theses"]
    [co] = th["companies"]
    by = {n["night"]: n["cause"] for n in co["nights"]}
    assert by == {
        "2026-07-09": "rule_change",  # proven
        "2026-07-10": "late_data",  # re-tested, not proven: the data arrived after Edition 1's cut
        "2026-07-13": "not_separated",  # Edition 1's nightly page: not re-tested
    }
    [ep] = co["episodes"]
    assert (ep["change"], ep["cause"]) == ("vanished", "rule_change")
    assert [(r["first"], r["cause"]) for r in co["runs"]] == [
        ("2026-07-09", "rule_change"),
        ("2026-07-10", "late_data"),
        ("2026-07-13", "not_separated"),
    ]


def test_any_other_pair_has_no_cause(client, db):
    _seed(db)
    body = client.get("/scoreboard/editions/diff", params={"from": 0, "to": 2}).json()
    assert body["causes"] is False
    for th in body["theses"]:
        for co in th["companies"]:
            assert {n["cause"] for n in co["nights"]} <= {None}
            assert {e["cause"] for e in co["episodes"]} <= {None}


def test_the_notice_and_the_admin_line_follow_a_cut(client, db):
    _seed(db)
    body = client.get("/scoreboard/editions").json()
    assert body["default"] == 2  # the newest edition, on its own
    n = body["notice"]
    assert (n["number"], n["previous"], n["n_company_nights_changed"], n["unexplained"]) == (
        2,
        1,
        3,
        False,
    )
    assert n["line"] == (
        "Edition 2 · rule change: tighter window · includes data that arrived since Edition 1 · "
        "3 company-nights changed"
    )
    admin = client.get("/admin/status").json()["editions_cut"]
    assert admin["line"] == n["line"] and admin["number"] == 2 and admin["attention"] is False


def test_no_notice_for_a_published_edition_or_an_old_cut(client, db, monkeypatch):
    t, _ = _seed(db, with_trial=False)  # Edition 2 without a trial: not the automatic cut's
    assert client.get("/scoreboard/editions").json()["notice"] is None
    assert client.get("/admin/status").json()["editions_cut"] is None
    # seven days on, a cut's notice is gone (the window is the repo's, read with a moved clock)
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert editions_repo.cut_notice(db, tenant_id=DEFAULT_TENANT_ID, now=later) is None
    db.commit()
