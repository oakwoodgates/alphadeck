"""Editions S6 over the wire — ``GET /scoreboard/editions/diff?from&to``: "what changed" from one edition to a
later one, computed on read. Edition 0 is read through its HONEST default read (reconstructed rows never; late
rows under the default policy), the comparison is capped at the earlier of the two editions' last nights, the
read writes nothing, and every change's ``cause`` is null until S7. Also: the retired replay-panel route is
gone (S8). The pure pairing rules are ``tests/scoreboard/test_edition_diff.py``'s.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from repositories import editions_repo
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

ENGINE = {"fingerprint": "f" * 64, "config_hash": "c" * 64, "code_sha": "abc1234def"}
CUT_1 = datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc)
CUT_2 = datetime(2026, 7, 16, 2, 0, tzinfo=timezone.utc)
_NY = ZoneInfo("America/New_York")


def _security(db, ticker: str, cik: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, '2026-01-01')",
            (sid, DEFAULT_TENANT_ID, ticker, cik, f"{ticker} Corp"),
        )
    db.commit()
    return sid


def _publish(db, thesis, pages, *, number: int, cut: datetime, entries=()) -> None:
    editions_repo.publish_edition(
        db,
        tenant_id=thesis.tenant_id,
        number=number,
        label="first re-run" if number == 1 else "second re-run",
        causes=["initial"] if number == 1 else ["rule_change"],
        engine=ENGINE,
        data_known_by=cut,
        pages=pages,
        entries=list(entries),
        computed_at=cut,
    )
    db.commit()


def _seed(db):
    """One thesis, one company. The RECORD (Edition 0) only ever warms on an honest row: its 07-10 arm is a
    reconstructed row and its 07-13 arm a LATE row (recorded the next afternoon, after an on-time warm row),
    both set aside by the default read. Edition 1 arms the company 07-09 through 07-13."""
    sid = _security(db, "DFA", "0000000301")
    t = persist_thesis(db, sid, thesis_id=uuid.uuid4())
    conv, conf = keys_fired(sid, date(2026, 7, 9), conv_liveness=120, conf_liveness=120)
    warm = insider_event(security_id=sid, liveness=120).model_copy(
        update={"asof": date(2026, 7, 8)}
    )
    record_day(db, t, [warm], date(2026, 7, 8))
    record_day(db, t, [warm], date(2026, 7, 9))
    record_day(db, t, [conv, conf], date(2026, 7, 10), reconstructed=True)
    record_day(db, t, [warm], date(2026, 7, 13))
    record_day(
        db, t, [conv, conf], date(2026, 7, 13), recorded_at=datetime(2026, 7, 14, 12, 0, tzinfo=_NY)
    )
    pages = [
        assemble_call(t, [warm], date(2026, 7, 8), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf], date(2026, 7, 9), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf], date(2026, 7, 10), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf], date(2026, 7, 13), DEFAULT_CONFIG),
    ]
    _publish(db, t, pages, number=1, cut=CUT_1)
    for d, c in ((date(2026, 7, 9), 100.0), (date(2026, 7, 13), 104.0), (date(2026, 7, 14), 105.0)):
        bar(db, sid, d, c)
    return t, sid, conv, conf, warm


def _counts(db) -> dict[str, int]:
    c = editions_repo.table_counts(db)
    db.commit()
    return c


def test_the_diff_reads_edition_0_honestly_and_lists_the_change_thesis_then_company(client, db):
    t, sid, *_ = _seed(db)
    before = _counts(db)
    r = client.get("/scoreboard/editions/diff", params={"from": 0, "to": 1})
    assert r.status_code == 200, r.text
    assert _counts(db) == before  # computed on read: nothing written, the calls log untouched
    body = r.json()

    assert (body["before"]["number"], body["before"]["label"]) == (0, "the original record")
    assert (body["after"]["number"], body["after"]["label"]) == (1, "first re-run")
    assert (body["through"], body["first_night"]) == ("2026-07-13", "2026-07-08")

    [th] = body["theses"]
    assert (th["thesis_id"], th["name"], th["error"]) == (str(t.id), t.name, None)
    assert (th["first_night"], th["last_night"], th["n_nights"]) == ("2026-07-08", "2026-07-13", 4)
    assert th["before_only"] == [] and th["after_only"] == [] and th["n_outside"] == 0
    assert th["n_state_nights"] == 3  # the record warms where the re-run is armed
    [co] = th["companies"]
    assert (co["security_id"], co["ticker"], co["company_name"]) == (str(sid), "DFA", "DFA Corp")

    # the honest read: the record's reconstructed 07-10 arm and its late 07-13 arm are both set aside, so
    # the record never arms the company — on 07-10 its 07-09 warm page stands (a set-aside night reads like
    # a gap), and on 07-13 its ON-TIME warm row wins over the later late one
    nights = {n["night"]: n for n in co["nights"]}
    assert sorted(nights) == ["2026-07-09", "2026-07-10", "2026-07-13"]
    assert all(n["changed"] == ["armed"] and n["cause"] is None for n in nights.values())
    assert all(
        n["before"]["armed"] is False and n["after"]["armed"] is True for n in nights.values()
    )
    assert nights["2026-07-10"]["before"]["page"] == "2026-07-09"
    assert nights["2026-07-13"]["before"]["page"] == "2026-07-13"
    assert nights["2026-07-09"]["after"]["verdict"] is not None
    assert nights["2026-07-09"]["after"]["exit_by"] == "2026-11-06"
    [run] = co["runs"]
    assert (run["first"], run["last"], run["n_nights"], run["changed"]) == (
        "2026-07-09",
        "2026-07-13",
        3,
        ["armed"],
    )

    # the re-run's arm APPEARED (a raw read of the log would have paired it with the reconstructed 07-10
    # arm as "moved"), scored arm -> exit-by on closes through the last night (07-14's bar is past it)
    [ep] = co["episodes"]
    assert (ep["change"], ep["before"], ep["cause"]) == ("appeared", None, None)
    assert ep["after"]["arm_date"] == "2026-07-09" and ep["after"]["dearm_date"] is None
    assert ep["after"]["forward_return"] == pytest.approx(0.04)

    c = body["counts"]
    assert (c["n_theses"], c["n_theses_changed"], c["n_companies_changed"]) == (1, 1, 1)
    assert (c["n_company_nights_changed"], c["n_armed_after_only"], c["n_armed_before_only"]) == (
        3,
        3,
        0,
    )
    assert (c["n_episodes_appeared"], c["n_episodes_vanished"], c["n_episodes_moved"]) == (1, 0, 0)
    assert c["n_thesis_nights"] == 4 and c["n_state_nights"] == 3


def test_the_comparison_is_capped_at_the_earlier_editions_last_night(client, db):
    t, sid, conv, conf, warm = _seed(db)
    # Edition 2 runs two nights past Edition 1's last night (07-13) and arms a second company there only
    other = _security(db, "DFB", "0000000302")
    c2, f2 = keys_fired(other, date(2026, 7, 15), conv_liveness=120, conf_liveness=120)
    pages = [
        assemble_call(t, [warm], date(2026, 7, 8), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf], date(2026, 7, 9), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf], date(2026, 7, 10), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf], date(2026, 7, 13), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf], date(2026, 7, 14), DEFAULT_CONFIG),
        assemble_call(t, [conv, conf, c2, f2], date(2026, 7, 15), DEFAULT_CONFIG),
    ]
    _publish(db, t, pages, number=2, cut=CUT_2)

    body = client.get("/scoreboard/editions/diff", params={"from": 1, "to": 2}).json()
    assert body["through"] == "2026-07-13"  # Edition 1's last night, not Edition 2's 07-15
    [th] = body["theses"]
    assert th["last_night"] == "2026-07-13" and th["after_only"] == []  # 07-14/15 never read
    assert th["companies"] == [] and body["counts"]["n_company_nights_changed"] == 0
    assert th["n_matched"] == 1  # the same arm, unchanged, counted not listed

    # the earlier edition's last night binds from Edition 0 too, and a later edition that has not reached
    # it yet is never charged with nights it has no page for: the earlier of the two last nights
    record_day(db, t, [warm], date(2026, 7, 14))
    zero = client.get("/scoreboard/editions/diff", params={"from": 0, "to": 1}).json()
    assert zero["before"]["last_night"] == "2026-07-14" and zero["through"] == "2026-07-13"


def test_the_diff_refuses_a_missing_or_backwards_pair(client, db):
    _seed(db)
    assert client.get("/scoreboard/editions/diff", params={"from": 0, "to": 3}).status_code == 404
    assert client.get("/scoreboard/editions/diff", params={"from": 1, "to": 1}).status_code == 422
    assert client.get("/scoreboard/editions/diff", params={"from": 0, "to": 0}).status_code == 422
    assert client.get("/scoreboard/editions/diff", params={"to": 1}).status_code == 422


def test_the_diff_with_an_empty_record_compares_nothing_and_says_so(client, db):
    sid = _security(db, "DFC", "0000000303")
    t = persist_thesis(db, sid, thesis_id=uuid.uuid4())
    conv, conf = keys_fired(sid, date(2026, 7, 9), conv_liveness=120, conf_liveness=120)
    _publish(
        db,
        t,
        [assemble_call(t, [conv, conf], date(2026, 7, 9), DEFAULT_CONFIG)],
        number=1,
        cut=CUT_1,
    )
    body = client.get("/scoreboard/editions/diff", params={"from": 0, "to": 1}).json()
    # the record has no night at all: there is nothing both editions cover
    assert body["before"]["last_night"] is None and body["through"] is None
    assert body["theses"] == [] and body["counts"]["n_theses"] == 0


def test_the_replay_panel_route_is_retired_and_the_diff_is_in_the_contract(client, db):
    assert client.get("/scoreboard/replay").status_code == 404
    paths = client.get("/openapi.json").json()["paths"]
    assert "/scoreboard/replay" not in paths
    assert "/scoreboard/editions/diff" in paths
    assert {"/backtest/runs", "/backtest/runs/{run_id}"} <= set(paths)  # /backtest is untouched
