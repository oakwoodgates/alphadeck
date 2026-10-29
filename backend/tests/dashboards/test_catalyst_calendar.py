"""Catalyst Calendar (``docs/dashboards/catalyst-calendar.md``): the pure compute on hand-built cards (the bucket
edges, today vs yesterday, the deadlines, a card with no recorded call, the undated groups, the order), the
router's read (pure, with the read stubbed), and a DB-backed read through the real SELECT proving an archived
card stays off the page and each card's LATEST call row supplies its state and deadlines.

A "now" view (``asofAware: false``): catalysts are edited in place with no history, so the checklist's past-as-of
case does not apply: the endpoint takes no as-of (the plan's known limits).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import catalyst_calendar as router_mod
from dashboards.catalyst_calendar import (
    CatalystCalendar,
    LatestCall,
    bucket_bounds,
    catalyst_calendar,
)
from db.session import DEFAULT_TENANT_ID
from domain.thesis import Catalyst, Thesis
from repositories import thesis_repo

TODAY = date(2026, 10, 29)  # a Thursday: this week runs through Sunday Nov 1

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def cat(
    label: str,
    when: date | None = None,
    *,
    kind: str | None = "regulatory",
    when_label: str | None = None,
) -> Catalyst:
    return Catalyst(id=uuid.uuid4(), label=label, kind=kind, when_date=when, when_label=when_label)


def card(name: str, *catalysts: Catalyst, tid: UUID | None = None) -> Thesis:
    return Thesis(id=tid or uuid.uuid4(), name=name, narrative="x", catalysts=list(catalysts))


def latest(
    state: str = "warming", *, arm_until: date | None = None, exit_by: date | None = None
) -> LatestCall:
    return LatestCall(state=state, arm_until=arm_until, exit_by=exit_by)


def days(n: int) -> date:
    return TODAY + timedelta(days=n)


def run(theses, calls=None, today=TODAY) -> CatalystCalendar:
    return catalyst_calendar(theses, calls or {}, today=today)


def where(result: CatalystCalendar) -> dict[str, str]:
    """``label -> bucket key`` for every upcoming item."""
    return {i.label: b.key for b in result.buckets for i in b.items}


def upcoming(result: CatalystCalendar):
    return [i for b in result.buckets for i in b.items]


# ---------------------------------------------------------------------------------------------------------
# the buckets
# ---------------------------------------------------------------------------------------------------------


def test_the_five_buckets_tile_the_calendar_with_monday_starting_a_week():
    assert [(k, s, e) for k, _, s, e in bucket_bounds(TODAY)] == [
        ("this_week", TODAY, date(2026, 11, 1)),
        ("next_week", date(2026, 11, 2), date(2026, 11, 8)),
        ("next_30_days", date(2026, 11, 9), days(30)),
        ("days_31_90", days(31), days(90)),
        ("later", days(91), None),
    ]
    assert date(2026, 11, 1).weekday() == 6 and date(2026, 11, 2).weekday() == 0


def test_a_sunday_item_is_this_week_and_the_next_monday_is_next_week():
    t = card(
        "Grid",
        cat("sunday", date(2026, 11, 1)),
        cat("monday", date(2026, 11, 2)),
        cat("next sunday", date(2026, 11, 8)),
        cat("the monday after", date(2026, 11, 9)),
    )
    assert where(run([t])) == {
        "sunday": "this_week",
        "monday": "next_week",
        "next sunday": "next_week",
        "the monday after": "next_30_days",
    }


def test_day_30_is_the_next_30_days_and_day_31_is_31_to_90():
    t = card("Grid", cat("day 30", days(30)), cat("day 31", days(31)))
    assert where(run([t])) == {"day 30": "next_30_days", "day 31": "days_31_90"}


def test_day_90_is_31_to_90_and_day_91_is_later():
    t = card("Grid", cat("day 90", days(90)), cat("day 91", days(91)), cat("far", date(2028, 1, 1)))
    assert where(run([t])) == {"day 90": "days_31_90", "day 91": "later", "far": "later"}


def test_on_a_sunday_this_week_is_today_alone_and_on_a_monday_it_is_seven_days():
    sunday, monday = date(2026, 11, 1), date(2026, 11, 2)
    assert bucket_bounds(sunday)[0][2:] == (sunday, sunday)
    assert bucket_bounds(sunday)[1][2:] == (date(2026, 11, 2), date(2026, 11, 8))
    assert bucket_bounds(monday)[0][2:] == (monday, date(2026, 11, 8))
    t = card("Grid", cat("tomorrow", date(2026, 11, 2)))
    assert where(run([t], today=sunday)) == {"tomorrow": "next_week"}


def test_today_counts_as_upcoming_and_yesterday_as_passed():
    t = card("Grid", cat("today", TODAY), cat("yesterday", days(-1)))
    result = run([t])

    (item,) = upcoming(result)
    assert (item.label, item.days) == ("today", 0)
    assert result.buckets[0].items == (item,)
    (gone,) = result.passed
    assert (gone.label, gone.on, gone.days) == ("yesterday", days(-1), -1)
    assert (result.upcoming_catalysts, result.passed_catalysts) == (1, 1)


def test_the_payload_always_carries_all_five_buckets_even_empty():
    result = run([card("Grid")])
    assert [b.key for b in result.buckets] == [
        "this_week",
        "next_week",
        "next_30_days",
        "days_31_90",
        "later",
    ]
    assert all(b.items == () for b in result.buckets)
    assert [b.label for b in result.buckets] == [
        "This week",
        "Next week",
        "Rest of the next 30 days",
        "31–90 days",
        "Later",
    ]


# ---------------------------------------------------------------------------------------------------------
# the card deadlines
# ---------------------------------------------------------------------------------------------------------


def test_deadlines_appear_only_on_or_after_today_and_are_tagged():
    on_time = card("On time")
    lapsed = card("Lapsed")
    calls = {
        on_time.id: latest("armed", arm_until=TODAY, exit_by=days(40)),
        lapsed.id: latest("warming", arm_until=days(-1), exit_by=days(-3)),
    }
    result = run([on_time, lapsed], calls)

    items = upcoming(result)
    assert [(i.item, i.label, i.on, i.days, i.thesis_name) for i in items] == [
        ("entry_window", "entry window ends", TODAY, 0, "On time"),
        ("exit_by", "exit-by", days(40), 40, "On time"),
    ]
    # tagged by ``item``: never a catalyst's kind, label or id
    assert all(i.kind is None and i.catalyst_id is None and i.when_label is None for i in items)
    assert where(result) == {"entry window ends": "this_week", "exit-by": "days_31_90"}
    # a passed deadline is the call's own clock, never a catalyst to prune
    assert result.passed == ()
    assert (result.deadlines, result.upcoming_catalysts, result.catalysts) == (2, 0, 0)


def test_a_deadline_carries_the_cards_latest_state():
    t = card("Grid")
    (item,) = upcoming(run([t], {t.id: latest("armed", arm_until=days(2))}))
    assert (item.state, item.thesis_id) == ("armed", t.id)


def test_a_card_with_no_recorded_call_is_still_listed_with_no_state_and_no_deadlines():
    fresh = card("Fresh", cat("vote", days(3)), cat("readout", days(-2)), cat("someday"))
    result = run([fresh])

    (item,) = upcoming(result)
    assert (item.thesis_name, item.state) == ("Fresh", None)  # the page shows None as "—"
    assert result.passed[0].state is None
    assert result.undated_groups[0].state is None
    assert result.deadlines == 0
    assert (result.live_cards, result.cards_without_calls) == (1, 1)


# ---------------------------------------------------------------------------------------------------------
# the order
# ---------------------------------------------------------------------------------------------------------


def test_same_day_items_are_ordered_by_card_name_then_catalysts_before_deadlines():
    beta = card("beta", cat("b-vote", days(2)))
    alpha = card("Alpha", cat("z-readout", days(2)), cat("a-vote", days(2)))
    gamma = card("gamma", cat("g-vote", days(1)))
    calls = {alpha.id: latest("armed", arm_until=days(2), exit_by=days(2))}
    result = run([gamma, beta, alpha], calls)

    assert [(i.on, i.thesis_name, i.label) for i in upcoming(result)] == [
        (days(1), "gamma", "g-vote"),
        (days(2), "Alpha", "a-vote"),
        (days(2), "Alpha", "z-readout"),
        (days(2), "Alpha", "entry window ends"),
        (days(2), "Alpha", "exit-by"),
        (days(2), "beta", "b-vote"),
    ]


def test_passed_catalysts_are_newest_first_with_how_many_days_ago():
    a = card("Alpha", cat("old", days(-40)), cat("recent", days(-1)))
    b = card("Beta", cat("mid", days(-7)), cat("also recent", days(-1)))
    result = run([b, a])
    assert [(i.label, i.thesis_name, i.days) for i in result.passed] == [
        ("recent", "Alpha", -1),
        ("also recent", "Beta", -1),
        ("mid", "Beta", -7),
        ("old", "Alpha", -40),
    ]
    assert all(i.item == "catalyst" for i in result.passed)


# ---------------------------------------------------------------------------------------------------------
# the undated groups
# ---------------------------------------------------------------------------------------------------------


def test_undated_catalysts_are_grouped_per_card_most_first_in_the_cards_own_order():
    one = card("One", cat("lone", when_label="~Q3"), cat("dated", days(5)))
    many = card(
        "many",
        cat("z first", when_label="2H27", kind=None),
        cat("a second"),
        cat("m third", when_label="~3wk", kind="clinical_readout"),
    )
    tied = card("Many too", cat("x"), cat("y"), cat("w"))
    none = card("None undated", cat("dated", days(1)))
    result = run([one, many, tied, none])

    assert [(g.thesis_name, g.count) for g in result.undated_groups] == [
        ("many", 3),  # a count tie reads by card name
        ("Many too", 3),
        ("One", 1),
    ]
    assert [(c.label, c.kind, c.when_label) for c in result.undated_groups[0].catalysts] == [
        ("z first", None, "2H27"),
        ("a second", "regulatory", None),
        ("m third", "clinical_readout", "~3wk"),
    ]
    # an undated catalyst never reaches the calendar, whatever its when_label says
    assert {i.label for i in upcoming(result)} == {"dated"}
    assert result.undated == 7


def test_an_empty_kind_reads_as_no_kind():
    t = card("Grid", cat("vote", days(1), kind=""), cat("someday", kind=""))
    result = run([t])
    assert upcoming(result)[0].kind is None
    assert result.undated_groups[0].catalysts[0].kind is None


# ---------------------------------------------------------------------------------------------------------
# which cards: only the live ones given
# ---------------------------------------------------------------------------------------------------------


def test_only_the_given_live_cards_count_and_an_archived_cards_call_is_ignored():
    live = card("Live", cat("vote", days(1)))
    archived_id = (
        uuid.uuid4()
    )  # list_all's default left it out; its latest call row still came back
    calls = {
        live.id: latest("warming"),
        archived_id: latest("armed", arm_until=days(1), exit_by=days(2)),
    }
    result = run([live], calls)
    assert {i.thesis_id for i in upcoming(result)} == {live.id}
    assert (result.live_cards, result.cards_without_calls, result.deadlines) == (1, 0, 0)


def test_the_counts_on_the_quiet_line():
    a = card("A", cat("up", days(3)), cat("past", days(-3)), cat("undated"))
    b = card("B")
    c = card("C", cat("later", days(200)))
    result = run([a, b, c], {a.id: latest("armed", arm_until=days(1), exit_by=days(-1))})
    assert (
        result.today,
        result.live_cards,
        result.cards_without_calls,
        result.cards_without_catalysts,
        result.catalysts,
        result.dated,
        result.upcoming_catalysts,
        result.passed_catalysts,
        result.undated,
        result.deadlines,
    ) == (TODAY, 3, 2, 1, 4, 3, 2, 1, 1, 1)


def test_an_empty_board():
    result = run([])
    assert (result.live_cards, result.catalysts, result.deadlines) == (0, 0, 0)
    assert result.passed == () and result.undated_groups == ()


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the read stubbed; it only reads and calls the compute)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    t = card(
        "Grid", cat("vote", days(12), when_label="mid-Nov"), cat("readout", days(-4)), cat("?")
    )
    seen: dict = {}

    def fake_list_all(conn, **kwargs):
        seen["list_all_kwargs"] = kwargs
        return [t]

    def fake_read(conn, ids):
        seen["ids"] = list(ids)
        return {t.id: latest("armed", arm_until=days(1), exit_by=days(60))}

    monkeypatch.setattr(router_mod.thesis_repo, "list_all", fake_list_all)
    monkeypatch.setattr(router_mod, "read_latest_calls", fake_read)
    monkeypatch.setattr(router_mod, "market_today", lambda: TODAY)
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen, t
    app.dependency_overrides.clear()


def test_the_router_reads_the_default_live_list_and_serves_the_compute(stub_client):
    client, seen, t = stub_client
    r = client.get("/dashboards/catalyst-calendar")
    assert r.status_code == 200
    body = r.json()

    # list_all's DEFAULT excludes archived cards; the router must never widen it
    assert seen["list_all_kwargs"] == {}
    assert seen["ids"] == [t.id]
    assert body["today"] == "2026-10-29"
    assert {b["key"]: [(i["item"], i["on"]) for i in b["items"]] for b in body["buckets"]} == {
        "this_week": [("entry_window", "2026-10-30")],
        "next_week": [],
        "next_30_days": [("catalyst", "2026-11-10")],
        "days_31_90": [("exit_by", "2026-12-28")],
        "later": [],
    }
    vote = body["buckets"][2]["items"][0]
    assert (vote["label"], vote["when_label"], vote["state"], vote["days"]) == (
        "vote",
        "mid-Nov",
        "armed",
        12,
    )
    assert [(p["label"], p["days"]) for p in body["passed"]] == [("readout", -4)]
    assert [(g["thesis_name"], g["count"]) for g in body["undated_groups"]] == [("Grid", 1)]
    assert (body["catalysts"], body["dated"], body["undated"], body["deadlines"]) == (3, 2, 1, 2)


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real read (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------


def _insert_call(db, tid: UUID, asof: date, state: str, card_json: str = "{}") -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card) "
            "VALUES (%s, %s, %s, %s, 'watching', %s::jsonb)",
            (DEFAULT_TENANT_ID, tid, asof, state, card_json),
        )


def test_the_real_read_skips_archived_cards_and_reads_each_cards_latest_call(db, monkeypatch):
    live = card("Live", cat("vote", days(5)), cat("readout", days(-2)), cat("someday"))
    fresh = card("Fresh", cat("launch", days(5)))
    parked = card("Parked", cat("parked vote", days(5)))
    for t in (live, fresh, parked):
        thesis_repo.upsert(db, t.model_copy(update={"tenant_id": DEFAULT_TENANT_ID}))
        thesis_repo.set_catalysts(db, t.id, t.catalysts, tenant_id=DEFAULT_TENANT_ID)
    thesis_repo.set_archived(db, parked.id, True)

    _insert_call(db, live.id, date(2026, 10, 1), "armed", '{"arm_until": "2026-10-30"}')
    # the night's (and the card's) latest row by seq: its state and clocks win
    _insert_call(db, live.id, date(2026, 10, 27), "armed", '{"arm_until": "2026-10-31"}')
    _insert_call(
        db,
        live.id,
        date(2026, 10, 27),
        "warming",
        '{"arm_until": null, "exit_by": "2026-11-20"}',
    )
    _insert_call(db, parked.id, date(2026, 10, 27), "armed", '{"exit_by": "2026-11-02"}')
    db.commit()

    monkeypatch.setattr(router_mod, "market_today", lambda: TODAY)
    app.dependency_overrides[get_conn] = lambda: db
    try:
        body = TestClient(app).get("/dashboards/catalyst-calendar").json()
    finally:
        app.dependency_overrides.clear()

    assert (body["live_cards"], body["cards_without_calls"]) == (2, 1)
    items = [i for b in body["buckets"] for i in b["items"]]
    assert [(i["thesis_name"], i["item"], i["label"], i["on"], i["state"]) for i in items] == [
        ("Fresh", "catalyst", "launch", "2026-11-03", None),
        ("Live", "catalyst", "vote", "2026-11-03", "warming"),
        ("Live", "exit_by", "exit-by", "2026-11-20", "warming"),
    ]
    assert [(p["thesis_name"], p["label"]) for p in body["passed"]] == [("Live", "readout")]
    assert [(g["thesis_name"], g["count"]) for g in body["undated_groups"]] == [("Live", 1)]
    assert "Parked" not in str(body)
