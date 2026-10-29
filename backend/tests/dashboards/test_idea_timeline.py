"""Idea Timeline (``docs/dashboards/idea-timeline.md``): the pure compute on hand-built rows, the router's reads
(pure, with every read stubbed), and DB-backed reads through the real SELECTs proving an archived card stays
off the page and a revoked exclude is no longer active.

A "now" view (``asofAware: false``): the time axis IS the history, so the checklist's past-as-of case does not
apply: the endpoint takes no as-of and reads the recorded calls as they stand (the plan's known limits).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import idea_timeline as router_mod
from dashboards.idea_timeline import (
    FUTURE_CAP_DAYS,
    AnnotationRow,
    CallRow,
    Clocks,
    DecisionRow,
    IdeaTimeline,
    active_excludes,
    idea_timeline,
)
from db.session import DEFAULT_TENANT_ID
from domain.thesis import BasketMember, Catalyst, Thesis
from repositories import thesis_repo

TODAY = date(2026, 10, 29)

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def card(name: str, *, tid: UUID | None = None, catalysts=(), basket=()) -> Thesis:
    return Thesis(
        id=tid or uuid.uuid4(),
        name=name,
        narrative="x",
        catalysts=list(catalysts),
        basket=list(basket),
    )


_seq = iter(range(1, 1_000_000))


def call(
    tid: UUID,
    asof: date,
    state: str,
    verdict: str = "watching",
    *,
    reconstructed: bool = False,
    cid: UUID | None = None,
) -> CallRow:
    return CallRow(
        id=cid or uuid.uuid4(),
        seq=next(_seq),
        thesis_id=tid,
        asof=asof,
        state=state,
        verdict=verdict,
        reconstructed=reconstructed,
    )


def d(month: int, day: int) -> date:
    return date(2026, month, day)


def run(theses, calls=(), annotations=(), decisions=(), clocks=None, today=TODAY) -> IdeaTimeline:
    return idea_timeline(theses, calls, annotations, decisions, clocks or {}, today=today)


def only_row(result: IdeaTimeline):
    assert len(result.rows) == 1
    return result.rows[0]


def spans(row):
    return [(s.state, s.start, s.end, s.days) for s in row.segments]


# ---------------------------------------------------------------------------------------------------------
# segments: runs of one recorded state
# ---------------------------------------------------------------------------------------------------------


def test_consecutive_nights_in_one_state_become_one_segment_and_a_change_starts_the_next():
    t = card("Grid")
    calls = [
        call(t.id, d(10, 1), "incubating"),
        call(t.id, d(10, 2), "incubating"),
        call(t.id, d(10, 5), "warming", "not_yet"),
        call(t.id, d(10, 6), "warming"),
        call(t.id, d(10, 7), "armed", "starter_entry"),
        call(t.id, d(10, 20), "warming"),
    ]
    row = only_row(run([t], calls))

    assert spans(row) == [
        ("incubating", d(10, 1), d(10, 5), 4),
        ("warming", d(10, 5), d(10, 7), 2),
        ("armed", d(10, 7), d(10, 20), 13),
        ("warming", d(10, 20), TODAY, 9),  # the current run holds until today
    ]
    assert [s.current for s in row.segments] == [False, False, False, True]
    assert [s.nights for s in row.segments] == [2, 2, 1, 1]
    # the verdict is the run's FIRST night's
    assert row.segments[1].verdict == "not_yet"
    assert row.segments[2].verdict == "starter_entry"


def test_a_state_carries_through_nights_with_no_row():
    t = card("Grid")
    calls = [
        call(t.id, d(9, 1), "armed"),
        call(t.id, d(9, 30), "armed"),
        call(t.id, d(10, 10), "warming"),
    ]
    row = only_row(run([t], calls))
    # no rows between Sep 1 and Sep 30: armed holds through them, one segment of two recorded nights
    assert spans(row) == [
        ("armed", d(9, 1), d(10, 10), 39),
        ("warming", d(10, 10), TODAY, 19),
    ]
    assert row.segments[0].nights == 2


def test_the_last_row_of_a_night_wins_and_the_night_keeps_its_row_count():
    t = card("Grid")
    calls = [
        call(t.id, d(10, 1), "incubating"),
        call(t.id, d(10, 2), "warming"),  # superseded the same night
        call(t.id, d(10, 2), "armed"),
        call(t.id, d(10, 2), "armed", "core_entry"),  # the last one (highest seq) is read
        call(t.id, d(10, 3), "armed"),
    ]
    result = run([t], calls)
    row = only_row(result)

    assert spans(row) == [
        ("incubating", d(10, 1), d(10, 2), 1),
        ("armed", d(10, 2), TODAY, 27),
    ]
    armed = row.segments[1]
    assert armed.verdict == "core_entry"
    assert [(m.asof, m.rows) for m in armed.multi_row_nights] == [(d(10, 2), 3)]
    assert row.segments[0].multi_row_nights == ()
    assert (row.recorded_calls, row.nights) == (5, 3)
    assert (result.recorded_calls, result.nights, result.multi_row_nights) == (5, 3, 1)


def test_the_last_row_wins_by_seq_not_by_input_order():
    t = card("Grid")
    late = call(t.id, d(10, 2), "armed")
    early = CallRow(
        id=uuid.uuid4(),
        seq=late.seq - 1_000,
        thesis_id=t.id,
        asof=d(10, 2),
        state="warming",
        verdict="watching",
        reconstructed=False,
    )
    row = only_row(run([t], [late, early]))
    assert row.current_state == "armed"


# ---------------------------------------------------------------------------------------------------------
# the left-side summaries
# ---------------------------------------------------------------------------------------------------------


def test_days_in_state_days_armed_and_entries_into_armed():
    t = card("Grid")
    calls = [
        call(t.id, d(9, 1), "armed"),  # the first recorded night armed: an entry
        call(t.id, d(9, 11), "warming"),
        call(t.id, d(9, 21), "armed"),
        call(t.id, d(9, 22), "armed"),
        call(t.id, d(10, 1), "incubating"),
        call(t.id, d(10, 19), "armed", "starter_entry"),
    ]
    row = only_row(run([t], calls))

    assert row.current_state == "armed"
    assert row.current_verdict == "starter_entry"
    assert row.days_in_state == 10  # Oct 19 -> Oct 29
    assert row.times_armed == 3
    assert row.total_days_armed == 10 + 10 + 10  # Sep 1-11, Sep 21-Oct 1, Oct 19-today
    assert (row.first_recorded, row.last_recorded) == (d(9, 1), d(10, 19))


def test_a_card_armed_tonight_reads_zero_days_in_state():
    t = card("Grid")
    row = only_row(run([t], [call(t.id, d(10, 28), "warming"), call(t.id, TODAY, "armed")]))
    assert spans(row)[-1] == ("armed", TODAY, TODAY, 0)
    assert (row.days_in_state, row.times_armed, row.total_days_armed) == (0, 1, 0)


def test_a_never_armed_card_has_no_armed_days():
    t = card("Grid")
    row = only_row(run([t], [call(t.id, d(10, 1), "incubating")]))
    assert (row.times_armed, row.total_days_armed, row.days_in_state) == (0, 0, 28)


# ---------------------------------------------------------------------------------------------------------
# reconstructed + set-aside nights
# ---------------------------------------------------------------------------------------------------------


def test_reconstructed_nights_hatch_the_span_they_hold_merged_when_consecutive():
    t = card("Grid")
    calls = [
        call(t.id, d(10, 1), "armed"),
        call(t.id, d(10, 2), "armed", reconstructed=True),
        call(t.id, d(10, 3), "armed", reconstructed=True),
        call(t.id, d(10, 6), "armed"),
        call(t.id, d(10, 8), "armed", reconstructed=True),
    ]
    result = run([t], calls)
    (seg,) = only_row(result).segments

    assert [(h.kind, h.start, h.end, h.nights, h.reason) for h in seg.hatches] == [
        ("reconstructed", d(10, 2), d(10, 6), 2, None),
        ("reconstructed", d(10, 8), TODAY, 1, None),  # the last night holds until today
    ]
    assert result.reconstructed_nights == 3


def test_the_flag_is_read_off_the_nights_winning_row():
    t = card("Grid")
    calls = [
        call(t.id, d(10, 1), "armed", reconstructed=True),
        call(t.id, d(10, 1), "armed"),  # an honest row wins the night
        call(t.id, d(10, 2), "armed"),
        call(t.id, d(10, 2), "armed", reconstructed=True),  # a reconstruction wins this one
    ]
    (seg,) = only_row(run([t], calls)).segments
    assert [(h.kind, h.start, h.end) for h in seg.hatches] == [("reconstructed", d(10, 2), TODAY)]


def test_an_active_exclude_hatches_its_night_with_its_reason():
    t = card("Grid")
    phantom = call(t.id, d(10, 2), "armed")
    calls = [call(t.id, d(10, 1), "warming"), phantom, call(t.id, d(10, 3), "warming")]
    ann = [AnnotationRow(uuid.uuid4(), phantom.id, "exclude", "stale_comparative_revenue", None)]
    result = run([t], calls, ann)
    armed = only_row(result).segments[1]

    assert [(h.kind, h.start, h.end, h.reason) for h in armed.hatches] == [
        ("excluded", d(10, 2), d(10, 3), "stale_comparative_revenue")
    ]
    assert result.excluded_nights == 1


def test_a_revoked_exclude_is_not_active():
    a, b = uuid.uuid4(), uuid.uuid4()
    kept, gone = uuid.uuid4(), uuid.uuid4()
    anns = [
        AnnotationRow(kept, a, "exclude", "stale_comparative_revenue", None),
        AnnotationRow(gone, b, "exclude", "stale_comparative_revenue", None),
        AnnotationRow(uuid.uuid4(), b, "revoke", "operator changed their mind", gone),
    ]
    assert active_excludes(anns) == {a: "stale_comparative_revenue"}

    t = card("Grid")
    revoked = call(t.id, d(10, 2), "armed", cid=b)
    (seg,) = only_row(run([t], [revoked], anns)).segments
    assert seg.hatches == ()


def test_several_active_excludes_on_one_row_list_each_reason_once():
    cid = uuid.uuid4()
    anns = [
        AnnotationRow(uuid.uuid4(), cid, "exclude", "rule_a", None),
        AnnotationRow(uuid.uuid4(), cid, "exclude", "rule_b", None),
        AnnotationRow(uuid.uuid4(), cid, "exclude", "rule_a", None),
    ]
    assert active_excludes(anns) == {cid: "rule_a; rule_b"}


def test_different_exclude_reasons_are_separate_hatches():
    t = card("Grid")
    n1, n2 = call(t.id, d(10, 1), "armed"), call(t.id, d(10, 2), "armed")
    anns = [
        AnnotationRow(uuid.uuid4(), n1.id, "exclude", "rule_a", None),
        AnnotationRow(uuid.uuid4(), n2.id, "exclude", "rule_b", None),
    ]
    (seg,) = only_row(run([t], [n1, n2], anns)).segments
    assert [(h.start, h.end, h.reason) for h in seg.hatches] == [
        (d(10, 1), d(10, 2), "rule_a"),
        (d(10, 2), TODAY, "rule_b"),
    ]


# ---------------------------------------------------------------------------------------------------------
# cards: archived ones absent, a brand-new one kept
# ---------------------------------------------------------------------------------------------------------


def test_only_the_given_live_cards_have_rows_and_other_cards_calls_are_ignored():
    live = card("Live")
    archived_id = uuid.uuid4()
    calls = [call(live.id, d(10, 1), "armed"), call(archived_id, d(7, 1), "armed")]
    result = run([live], calls)
    assert [r.thesis_name for r in result.rows] == ["Live"]
    assert result.recorded_calls == 1
    assert result.axis_start == d(10, 1)  # the archived card's July row never widens the axis


def test_a_brand_new_card_with_no_calls_keeps_its_row_with_no_segments():
    new = card("Brand New")
    old = card("Old")
    result = run([new, old], [call(old.id, d(10, 1), "warming")])

    assert [r.thesis_name for r in result.rows] == ["Brand New", "Old"]
    row = result.rows[0]
    assert row.segments == ()
    assert (row.current_state, row.current_verdict, row.days_in_state) == (None, None, None)
    assert (row.first_recorded, row.last_recorded, row.recorded_calls) == (None, None, 0)
    assert (row.times_armed, row.total_days_armed) == (0, 0)
    assert result.cards_without_calls == 1


# ---------------------------------------------------------------------------------------------------------
# markers: catalysts, decisions, clocks
# ---------------------------------------------------------------------------------------------------------


def test_dated_catalysts_are_markers_and_undated_ones_are_counted():
    t = card(
        "Grid",
        catalysts=[
            Catalyst(id=uuid.uuid4(), label="PDUFA", kind="regulatory", when_date=d(11, 15)),
            Catalyst(id=uuid.uuid4(), label="Q3 print", when_date=d(10, 2)),
            Catalyst(id=uuid.uuid4(), label="Today's vote", when_date=TODAY),
            Catalyst(id=uuid.uuid4(), label="Some summer", when_label="summer"),
            Catalyst(id=uuid.uuid4(), label="Later"),
        ],
    )
    row = only_row(run([t]))

    assert [(c.label, c.when_date, c.past) for c in row.catalysts] == [
        ("Q3 print", d(10, 2), True),  # by date; muted when past
        ("Today's vote", TODAY, False),  # today is not past
        ("PDUFA", d(11, 15), False),
    ]
    assert row.catalysts[2].kind == "regulatory"
    assert row.undated_catalysts == 2


def test_decisions_mark_take_close_pass_and_a_voided_one_stays_marked_voided():
    hims = uuid.uuid4()
    t = card("Telehealth", basket=[BasketMember(ticker="HIMS", role="x", security_id=hims)])
    take, mistake, gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    decisions = [
        DecisionRow(take, 1, t.id, hims, "take", d(10, 2), "starter", None),
        DecisionRow(mistake, 2, t.id, hims, "close", d(10, 5), None, None),
        DecisionRow(uuid.uuid4(), 3, t.id, None, "void", d(10, 6), "fat finger", mistake),
        DecisionRow(uuid.uuid4(), 4, t.id, None, "pass", d(10, 1), "too early", None),
        DecisionRow(uuid.uuid4(), 5, t.id, gone, "close", d(10, 20), None, None),
    ]
    row = only_row(run([t], decisions=decisions))

    assert [(m.action, m.decision_date, m.ticker, m.voided) for m in row.decisions] == [
        ("pass", d(10, 1), None, False),  # card-level
        ("take", d(10, 2), "HIMS", False),
        ("close", d(10, 5), "HIMS", True),  # voided: stays visible, marked
        ("close", d(10, 20), None, False),  # a name no longer on the basket: no ticker, its id kept
    ]
    assert row.decisions[3].security_id == gone
    assert row.decisions[1].reason == "starter"
    # the void row draws nothing of its own
    assert all(m.action != "void" for m in row.decisions)


def test_no_decisions_renders_no_decision_marks():
    row = only_row(run([card("Grid")]))
    assert row.decisions == ()


def test_the_clocks_ride_the_row_and_stay_empty_when_unset():
    a, b = card("A"), card("B")
    result = run([a, b], clocks={a.id: Clocks(exit_by=d(11, 20), arm_until=d(11, 5))})
    assert (result.rows[0].exit_by, result.rows[0].arm_until) == (d(11, 20), d(11, 5))
    assert (result.rows[1].exit_by, result.rows[1].arm_until) == (None, None)


# ---------------------------------------------------------------------------------------------------------
# the axis
# ---------------------------------------------------------------------------------------------------------


def test_the_axis_runs_from_the_first_recorded_night_to_the_latest_future_marker():
    a = card("A", catalysts=[Catalyst(id=uuid.uuid4(), label="vote", when_date=d(11, 10))])
    b = card("B")
    result = run(
        [a, b],
        [call(a.id, d(9, 1), "warming"), call(b.id, d(7, 10), "incubating")],
        clocks={b.id: Clocks(exit_by=d(11, 30), arm_until=None)},
    )
    assert (result.axis_start, result.axis_end) == (d(7, 10), d(11, 30))
    assert (result.first_recorded, result.last_recorded) == (d(7, 10), d(9, 1))
    assert result.future_cap == TODAY + timedelta(days=FUTURE_CAP_DAYS)


def test_the_axis_end_is_capped_at_today_plus_90_days():
    near = card("Near", catalysts=[Catalyst(id=uuid.uuid4(), label="vote", when_date=d(12, 1))])
    on_cap = card(
        "On cap",
        catalysts=[
            Catalyst(id=uuid.uuid4(), label="readout", when_date=TODAY + timedelta(days=90)),
            Catalyst(id=uuid.uuid4(), label="far", when_date=date(2027, 6, 1)),
        ],
    )
    result = run([near, on_cap], [call(near.id, d(10, 1), "warming")])
    assert result.future_cap == TODAY + timedelta(days=90)
    assert result.axis_end == result.future_cap
    # the far marker stays in the payload (the page counts it as past the axis end)
    assert result.rows[1].catalysts[-1].when_date == date(2027, 6, 1)


def test_a_marker_past_the_cap_never_stretches_the_axis():
    far = card(
        "Far",
        catalysts=[
            Catalyst(id=uuid.uuid4(), label="vote", when_date=d(11, 20)),
            Catalyst(id=uuid.uuid4(), label="readout", when_date=date(2027, 6, 1)),
        ],
    )
    result = run([far], [call(far.id, d(10, 1), "warming")])
    assert result.axis_end == d(11, 20)  # the latest marker the axis shows
    assert only_row(result).catalysts[-1].when_date == date(2027, 6, 1)


def test_with_no_future_marker_the_axis_ends_today():
    t = card("Grid", catalysts=[Catalyst(id=uuid.uuid4(), label="old", when_date=d(8, 1))])
    result = run([t], [call(t.id, d(9, 1), "warming")])
    assert (result.axis_start, result.axis_end) == (d(9, 1), TODAY)


def test_an_empty_board():
    result = run([])
    assert (result.live_cards, result.recorded_calls, result.nights) == (0, 0, 0)
    assert (result.axis_start, result.axis_end) == (TODAY, TODAY)
    assert result.rows == ()


# ---------------------------------------------------------------------------------------------------------
# the router (pure: every read stubbed; it only reads and calls the compute)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    t = card("Grid", catalysts=[Catalyst(id=uuid.uuid4(), label="vote", when_date=d(11, 10))])
    seen: dict = {}

    def fake_list_all(conn, **kwargs):
        seen["list_all_kwargs"] = kwargs
        return [t]

    def reader(name, value):
        def fake(conn, ids):
            seen[name] = list(ids)
            return value

        return fake

    monkeypatch.setattr(router_mod.thesis_repo, "list_all", fake_list_all)
    monkeypatch.setattr(
        router_mod,
        "read_calls",
        reader("calls", [call(t.id, d(10, 1), "warming"), call(t.id, d(10, 20), "armed")]),
    )
    monkeypatch.setattr(router_mod, "read_annotations", reader("annotations", []))
    monkeypatch.setattr(router_mod, "read_decisions", reader("decisions", []))
    monkeypatch.setattr(
        router_mod, "read_clocks", reader("clocks", {t.id: Clocks(d(11, 20), d(11, 5))})
    )
    monkeypatch.setattr(router_mod, "market_today", lambda: TODAY)
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen, t
    app.dependency_overrides.clear()


def test_the_router_reads_the_default_live_list_and_serves_the_compute(stub_client):
    client, seen, t = stub_client
    r = client.get("/dashboards/idea-timeline")
    assert r.status_code == 200
    body = r.json()

    # list_all's DEFAULT excludes archived cards; the router must never widen it
    assert seen["list_all_kwargs"] == {}
    assert seen["calls"] == seen["annotations"] == seen["decisions"] == seen["clocks"] == [t.id]
    assert body["today"] == "2026-10-29"
    assert (body["axis_start"], body["axis_end"]) == ("2026-10-01", "2026-11-20")
    (row,) = body["rows"]
    assert row["current_state"] == "armed" and row["days_in_state"] == 9
    assert [(s["state"], s["start"], s["end"]) for s in row["segments"]] == [
        ("warming", "2026-10-01", "2026-10-20"),
        ("armed", "2026-10-20", "2026-10-29"),
    ]
    assert (row["exit_by"], row["arm_until"]) == ("2026-11-20", "2026-11-05")
    assert row["catalysts"][0]["label"] == "vote" and row["decisions"] == []


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real SELECTs (skip without Postgres)
# ---------------------------------------------------------------------------------------------------------


def _insert_call(db, tid: UUID, asof: date, state: str, card_json: str = "{}", **kw) -> UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, reconstructed) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
            (
                DEFAULT_TENANT_ID,
                tid,
                asof,
                state,
                kw.get("verdict", "watching"),
                card_json,
                kw.get("reconstructed", False),
            ),
        )
        return cur.fetchone()["id"]


def _insert_annotation(db, call_id: UUID, action: str, reason: str, revokes=None) -> UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO call_annotation (tenant_id, call_id, thesis_id, asof, action, reason, rule, "
            "revokes) SELECT tenant_id, id, thesis_id, asof, %s, %s, 'test@x', %s FROM calls "
            "WHERE id = %s RETURNING id",
            (action, reason, revokes, call_id),
        )
        return cur.fetchone()["id"]


def test_the_real_reads_skip_archived_cards_and_honor_revokes_and_voids(db, monkeypatch):
    live = card("Live")
    parked = card("Parked")
    for t in (live, parked):
        thesis_repo.upsert(db, t.model_copy(update={"tenant_id": DEFAULT_TENANT_ID}))
    thesis_repo.set_archived(db, parked.id, True)

    _insert_call(db, live.id, d(10, 1), "warming")
    _insert_call(db, live.id, d(10, 2), "warming", reconstructed=True)
    kept = _insert_call(db, live.id, d(10, 5), "armed", verdict="starter_entry")
    _insert_call(db, live.id, d(10, 6), "armed")
    # the night's (and the card's) latest row: it wins the night and the clocks are read off it
    revoked = _insert_call(
        db, live.id, d(10, 6), "armed", '{"exit_by": "2026-11-20", "arm_until": null}'
    )
    _insert_call(db, parked.id, d(7, 1), "armed")
    _insert_annotation(db, kept, "exclude", "stale_comparative_revenue")
    gone = _insert_annotation(db, revoked, "exclude", "stale_comparative_revenue")
    _insert_annotation(db, revoked, "revoke", "operator reversed it", gone)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_decision (tenant_id, thesis_id, action, decision_date) "
            "VALUES (%s, %s, 'take', %s) RETURNING id",
            (DEFAULT_TENANT_ID, live.id, d(10, 7)),
        )
        take = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO operator_decision (tenant_id, thesis_id, action, decision_date, voids) "
            "VALUES (%s, %s, 'void', %s, %s)",
            (DEFAULT_TENANT_ID, live.id, d(10, 8), take),
        )
    db.commit()

    monkeypatch.setattr(router_mod, "market_today", lambda: TODAY)
    app.dependency_overrides[get_conn] = lambda: db
    try:
        body = TestClient(app).get("/dashboards/idea-timeline").json()
    finally:
        app.dependency_overrides.clear()

    assert body["live_cards"] == 1
    (row,) = body["rows"]
    assert row["thesis_name"] == "Live"
    assert body["axis_start"] == "2026-10-01"  # the archived card's July row is never read
    assert (body["recorded_calls"], body["nights"], body["multi_row_nights"]) == (5, 4, 1)
    warming, armed = row["segments"]
    assert [(h["kind"], h["start"], h["end"]) for h in warming["hatches"]] == [
        ("reconstructed", "2026-10-02", "2026-10-05")
    ]
    # only the un-revoked exclude is active
    assert [(h["kind"], h["start"], h["end"], h["reason"]) for h in armed["hatches"]] == [
        ("excluded", "2026-10-05", "2026-10-06", "stale_comparative_revenue")
    ]
    assert armed["multi_row_nights"] == [{"asof": "2026-10-06", "rows": 2}]
    assert (row["exit_by"], row["arm_until"]) == ("2026-11-20", None)
    assert [(m["action"], m["voided"]) for m in row["decisions"]] == [("take", True)]
