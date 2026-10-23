"""Editions S4 over the wire — the Scoreboard reads a stored EDITION: ``GET /scoreboard?edition=N``, the
``edition`` block, ``GET /scoreboard/editions``, the drawer's ``price-window?edition=``, and the default-edition
setting. An edition is read from its frozen ``edition_call`` pages ONLY: a ``calls`` row appended later never
moves it, and none of the record's set-aside readings apply (the lists are empty, the policies ``n/a``).

The edition here is stored through the repo's own writer with cards the assembler builds (the engine path is
``tests/editions/test_publish.py``'s), so the read is tested against controlled pages. Edition 0's
byte-for-byte guarantee is ``test_scoreboard_edition0_golden.py``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.settings import get_settings
from repositories import editions_repo
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

ASOF = "2026-07-15"
CUT = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)  # 22:00 ET on 07-14
ENGINE = {"fingerprint": "f" * 64, "config_hash": "c" * 64, "code_sha": "abc1234def"}


def _security(db, ticker: str, cik: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, '2026-01-01')",
            (sid, DEFAULT_TENANT_ID, ticker, cik),
        )
    db.commit()
    return sid


def _seed(db):
    """Two theses and one stored Edition 1. A joined 07-08 (estimated), warms, arms 07-09: a real arm. B
    joined 07-09 (estimated) and arms on that very night: G12 censors it. The RECORD (Edition 0) says
    something else entirely — A only ever warming, plus a reconstructed row — so a read that leaked the calls
    log into the edition would show."""
    sa, sb = _security(db, "EDA", "0000000201"), _security(db, "EDB", "0000000202")
    ta = persist_thesis(db, sa, thesis_id=uuid.uuid4())
    tb = persist_thesis(db, sb, thesis_id=uuid.uuid4())
    conv_a, conf_a = keys_fired(sa, date(2026, 7, 9), conv_liveness=120, conf_liveness=120)
    conv_b, conf_b = keys_fired(sb, date(2026, 7, 9), conv_liveness=120, conf_liveness=120)
    warm_a = insider_event(security_id=sa, liveness=120).model_copy(
        update={"asof": date(2026, 7, 8)}
    )
    pages = [
        assemble_call(ta, [warm_a], date(2026, 7, 8), DEFAULT_CONFIG),
        assemble_call(ta, [conv_a, conf_a], date(2026, 7, 9), DEFAULT_CONFIG),
        assemble_call(ta, [conv_a, conf_a], date(2026, 7, 10), DEFAULT_CONFIG),
        assemble_call(ta, [conv_a, conf_a], date(2026, 7, 13), DEFAULT_CONFIG),
        assemble_call(tb, [conv_b, conf_b], date(2026, 7, 9), DEFAULT_CONFIG),
        assemble_call(tb, [conv_b, conf_b], date(2026, 7, 13), DEFAULT_CONFIG),
    ]
    entries = [
        {
            "thesis_id": str(ta.id),
            "security_id": str(sa),
            "entry_date": "2026-07-08",
            "estimated": True,
            "method": "first_pull",
            "evidence": {"first_pull": "2026-07-08"},
        },
        {
            "thesis_id": str(tb.id),
            "security_id": str(sb),
            "entry_date": "2026-07-09",
            "estimated": True,
            "method": "own_batch+card",
            "evidence": {},
        },
    ]
    editions_repo.publish_edition(
        db,
        tenant_id=ta.tenant_id,
        number=1,
        label="first re-run",
        causes=["initial"],
        engine=ENGINE,
        data_known_by=CUT,
        pages=pages,
        entries=entries,
        computed_at=CUT,
    )
    db.commit()
    # the record: A warming only, B silent, plus a reconstructed night — none of it is the edition's
    record_day(db, ta, [warm_a], date(2026, 7, 9))
    record_day(db, ta, [conv_a, conf_a], date(2026, 7, 10), reconstructed=True)
    for d, c in ((date(2026, 7, 9), 100.0), (date(2026, 7, 13), 104.0), (date(2026, 7, 14), 105.0)):
        bar(db, sa, d, c)
        bar(db, sb, d, c / 2)
    return ta, tb, sa, sb, conv_a, conf_a


def _strip(body: dict) -> dict:
    body = dict(body)
    body.pop("generated_at")
    return body


def _eps(body: dict) -> dict[str, dict]:
    return {e["ticker"]: e for t in body["theses"] for e in t["episodes"]}


def _edition_counts(db) -> dict[str, int]:
    c = editions_repo.table_counts(db)
    db.commit()
    return c


def test_with_no_edition_stored_only_the_record_is_listed_and_is_the_default(client, db):
    r = client.get("/scoreboard/editions")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == 0  # the 0-editions fallback: the newest edition is the record itself
    assert [(e["number"], e["label"], e["cut"]) for e in body["editions"]] == [
        (0, "the original record", None)
    ]
    assert client.get("/scoreboard", params={"asof": ASOF, "edition": 1}).status_code == 404
    zero = client.get("/scoreboard", params={"asof": ASOF, "edition": 0}).json()
    assert zero["edition"]["number"] == 0 and zero["edition"]["n_entries_estimated"] is None
    default = client.get("/scoreboard", params={"asof": ASOF}).json()
    assert default["edition"]["number"] == 0 and _strip(default) == _strip(zero)


def test_an_edition_is_read_from_its_pages_with_its_own_block(client, db):
    ta, tb, sa, sb, *_ = _seed(db)
    before = _edition_counts(db)
    body = client.get("/scoreboard", params={"asof": ASOF, "edition": 1}).json()
    assert _edition_counts(db) == before  # the read writes nothing

    ed = body["edition"]
    assert (ed["number"], ed["label"], ed["causes"]) == (1, "first re-run", ["initial"])
    assert datetime.fromisoformat(ed["cut"]) == CUT
    assert (ed["first_night"], ed["last_night"]) == ("2026-07-08", "2026-07-13")
    assert (ed["n_pages"], ed["n_entries"], ed["n_entries_estimated"]) == (6, 2, 2)
    assert ed["engine_fingerprint"] == ENGINE["fingerprint"] and ed["code_sha"] == "abc1234def"

    s = body["summary"]
    assert (s["annotated_policy"], s["late_policy"]) == ("n/a", "n/a")
    assert s["annotated_nights"] == [] and s["late_nights"] == []
    assert s["reconstructed_nights"] == []  # the record HAS one (07-10); a re-run has none
    assert s["banner"].startswith("EDITION 1 · FIRST RE-RUN")
    assert s["record_began"] == "2026-07-08"
    assert s["n_ingest_flagged"] == 0

    eps = _eps(body)
    a, b = eps["EDA"], eps["EDB"]
    # A: armed 07-09, the night AFTER its estimated join — a real arm, from the edition's pages (the record
    # never armed A on an honest row)
    assert (a["arm_date"], a["censored_start"], a["status"]) == ("2026-07-09", False, "open")
    assert a["edition_join"] == {
        "entry_date": "2026-07-08",
        "estimated": True,
        "method": "first_pull",
        "armed_on_join_night": False,
        "note": "join date estimated · 2026-07-08 (first data pull)",
    }
    assert a["run_identity_note"] == (
        "Edition 1 · first re-run · engine ffffffff · policy cccccccc · code abc1234d · cut 2026-07-14"
    )
    assert (a["arm_run_kind"], a["arm_config_hash"]) == ("rerun", ENGINE["config_hash"])
    assert a["ingest_flagged"] is False and a["ingest_note"] is None
    assert a["annotation_note"] is None
    assert a["entry_close"] == 100.0  # scored on read against the realized tape
    # B: armed ON its estimated join night — the true arm date is unknowable (G12)
    assert b["censored_start"] is True and b["edition_join"]["armed_on_join_night"] is True
    assert "armed on its join night" in b["edition_join"]["note"]
    assert (
        b["edition_join"]["note"].split(" · armed")[0]
        == "join date estimated · 2026-07-09 (its thesis's own first batch, moved to the first "
        "recorded card naming it)"
    )
    assert s["n_censored"] == 1

    # Edition 0 is the record, with its own reading: A never armed honestly there
    zero = client.get("/scoreboard", params={"asof": ASOF, "edition": 0}).json()
    assert zero["edition"]["number"] == 0 and "EDA" not in _eps(zero)
    assert zero["summary"]["reconstructed_nights"] == ["2026-07-10"]
    assert zero["summary"]["annotated_policy"] == "excluded"


def test_an_edition_read_ignores_a_calls_row_appended_later(client, db):
    ta, tb, sa, sb, conv_a, conf_a = _seed(db)
    first = _strip(client.get("/scoreboard", params={"asof": ASOF, "edition": 1}).json())
    zero_before = _strip(client.get("/scoreboard", params={"asof": ASOF, "edition": 0}).json())
    # the nightly appends a NEW call of record that arms A on 07-14, and re-versions 07-09 (armed)
    record_day(db, ta, [conv_a, conf_a], date(2026, 7, 14))
    record_day(db, ta, [conv_a, conf_a], date(2026, 7, 9))
    counts = _edition_counts(db)
    again = _strip(client.get("/scoreboard", params={"asof": ASOF, "edition": 1}).json())
    zero_after = _strip(client.get("/scoreboard", params={"asof": ASOF, "edition": 0}).json())
    # the edition is frozen: its pages, not the log. The ONLY fields that move are the nightly FEED's
    # freshness (the calls log's edge — asof-independent by design, and the same on every view)
    feed = {"record_edge", "expected_asof", "days_behind", "stale"}
    moved = {k for k in first["summary"] if first["summary"][k] != again["summary"][k]}
    assert "record_edge" in moved and moved <= feed
    assert first["summary"]["record_edge"] == "2026-07-10"
    assert again["summary"]["record_edge"] == "2026-07-14"
    for body in (first, again):
        for k in feed:
            body["summary"].pop(k)
    assert again == first
    assert zero_after != zero_before and "EDA" in _eps(zero_after)  # the record did move
    assert _edition_counts(db) == counts


def _default_number(client) -> tuple[int, int]:
    """(the edition GET /scoreboard serves with no ``edition``, the list's ``default``) — they must agree."""
    served = client.get("/scoreboard", params={"asof": ASOF}).json()["edition"]["number"]
    return served, client.get("/scoreboard/editions").json()["default"]


def test_the_default_is_the_newest_published_edition_and_moves_when_a_new_one_is_cut(client, db):
    ta, tb, sa, sb, conv_a, conf_a = _seed(db)
    assert _default_number(client) == (
        1,
        1,
    )  # operator decision 2026-10-23: the newest is the default
    one = _strip(client.get("/scoreboard", params={"asof": ASOF, "edition": 1}).json())
    assert _strip(client.get("/scoreboard", params={"asof": ASOF}).json()) == one
    # Edition 0 stays one click away: an explicit edition=0 still reads the record
    zero = client.get("/scoreboard", params={"asof": ASOF, "edition": 0}).json()
    assert zero["edition"]["number"] == 0 and "EDA" not in _eps(zero)
    # cut Edition 2: the default follows it with no setting touched
    editions_repo.publish_edition(
        db,
        tenant_id=ta.tenant_id,
        number=2,
        label="second re-run",
        causes=["rule_change"],
        engine=ENGINE,
        data_known_by=datetime(2026, 7, 16, 2, 0, tzinfo=timezone.utc),
        pages=[assemble_call(ta, [conv_a, conf_a], date(2026, 7, 9), DEFAULT_CONFIG)],
        entries=[],
        computed_at=CUT,
    )
    db.commit()
    assert _default_number(client) == (2, 2)
    # the drawer's price window with no edition reads the newest too (a past as-of is on the public
    # clock either way, so it matches the explicit read)
    params = {
        "thesis_id": str(ta.id),
        "security_id": str(sa),
        "start": "2026-07-09",
        "end": "2026-11-06",
        "asof": ASOF,
    }
    bare = client.get("/scoreboard/price-window", params=params)
    named = client.get("/scoreboard/price-window", params={**params, "edition": 2})
    assert bare.status_code == named.status_code == 200 and bare.json() == named.json()


def test_the_setting_is_an_optional_override_and_an_unpublished_one_falls_back_to_the_newest(
    client, db, monkeypatch
):
    _seed(db)
    try:
        monkeypatch.setenv("ALPHADECK_SCOREBOARD_DEFAULT_EDITION", "0")
        get_settings.cache_clear()
        assert _default_number(client) == (0, 0)  # 0 pins the record
        monkeypatch.setenv("ALPHADECK_SCOREBOARD_DEFAULT_EDITION", "1")
        get_settings.cache_clear()
        assert _default_number(client) == (1, 1)
        monkeypatch.setenv("ALPHADECK_SCOREBOARD_DEFAULT_EDITION", "5")
        get_settings.cache_clear()
        assert _default_number(client) == (1, 1)  # never a 404: the newest stands in
    finally:
        monkeypatch.delenv("ALPHADECK_SCOREBOARD_DEFAULT_EDITION")
        get_settings.cache_clear()
    assert get_settings().scoreboard_default_edition is None  # unset = newest


def test_the_editions_list_names_the_record_then_each_stored_edition(client, db):
    _seed(db)
    body = client.get("/scoreboard/editions").json()
    assert body["default"] == 1  # the newest published edition
    zero, one = body["editions"]
    assert (zero["number"], zero["label"], zero["first_night"]) == (
        0,
        "the original record",
        "2026-07-09",
    )
    assert (one["number"], one["label"], one["n_entries_estimated"]) == (1, "first re-run", 2)
    assert datetime.fromisoformat(one["published_at"]) is not None


def test_the_drawer_price_window_takes_the_edition(client, db):
    ta, tb, sa, sb, *_ = _seed(db)
    params = {
        "thesis_id": str(ta.id),
        "security_id": str(sa),
        "start": "2026-07-09",
        "end": "2026-11-06",
        "asof": ASOF,
    }
    zero = client.get("/scoreboard/price-window", params={**params, "edition": 0})
    one = client.get("/scoreboard/price-window", params={**params, "edition": 1})
    assert zero.status_code == one.status_code == 200
    # a past as-of is already on the public clock (#433): the edition's drawer reads the same window
    assert one.json() == zero.json() and [b["d"] for b in one.json()["bars"]][:1] == ["2026-07-09"]
    missing = client.get("/scoreboard/price-window", params={**params, "edition": 9})
    assert missing.status_code == 404
