"""``pipeline.repair_catalyst_dates`` — re-date the stored DOE award catalysts that the feed dated by the work
period's start, and touch NOTHING else.

The rows under test are the six REAL committed awards (``seed_data/doe/award/*.json``), stored the way the
feed stored them BEFORE rule ``2026-11-catalysts-dated-by-record-date``: ``valid_from`` = the payload's
own ``period_of_performance.start_date`` and the pre-rule label. Three of the six were RECORDED (the
payload's ``date_signed``: the award's first action date in the federal record) after that start, so they
must be re-dated; three were recorded the same day, so they must be kept, untouched.

Every claim about a write is COUNTED AT THE TABLE (rows, ids and full row contents before and after), never
read back through the as-of view: that read dedups on the natural key, so a repair that deleted the wrong
version, or none, could still show a plausible-looking read.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.bitemporal import as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.enums import CatalystType, Grade
from domain.market_time import known_at_for_asof
from domain.scopes import DATA_TENANT_ID, tenants_for
from ingest.catalyst import ingest_catalyst
from ingest.doe.feed import discover, parse_award
from pipeline import repair_catalyst_dates as R
from pipeline import seed as seed_mod
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from pipeline.seed import LEU_ID, OKLO_ID, seed_nuclear

_OKLO_OTA = "ASST_NON_DENE0009589_089"
_LEU_GRANT = "ASST_NON_DENE0000530_089"
_LEU_GRANT_2010 = "ASST_NON_DESC0003997_089"
# award id -> (the work period's start = the date stored before the rule, the date the award was recorded =
# the rule's)
_MOVED = {
    _OKLO_OTA: (date(2026, 2, 9), date(2026, 5, 18)),
    _LEU_GRANT: (date(2012, 6, 1), date(2012, 11, 30)),
    _LEU_GRANT_2010: (date(2010, 3, 23), date(2010, 12, 21)),
}
_KEPT = {
    "CONT_AWD_89243223CNE000030_8900_-NONE-_-NONE-": date(2022, 11, 30),
    "CONT_AWD_89243225FNE400178_8900_89243225DNE000026_8900": date(2025, 4, 11),
    "CONT_AWD_89303519CNE000005_8900_-NONE-_-NONE-": date(2019, 5, 31),
}
_SECURITY = {"LEU": LEU_ID, "OKLO": OKLO_ID}
# after the OKLO award's work period starts; before it was recorded (and before it was announced, 03-17)
_BETWEEN = date(2026, 3, 1)
_URL = "https://www.usaspending.gov/award/"


def _store_as_before_the_rule(db) -> dict[str, uuid.UUID]:
    """Store the six real awards exactly as the pre-rule feed did: dated at the work period's START, with the
    pre-rule label (the current label minus the " · recorded …" note the rule adds; ``tests/ingest/
    test_doe_feed.py`` pins those pre-rule labels as literals measured from the old code). Returns
    ``{award id: stored row id}``."""
    seed_nuclear(db)
    db.commit()
    client = R.offline_client()
    stored: dict[str, uuid.UUID] = {}
    for gid, ticker in sorted(discover(client).items()):
        c = parse_award(client, gid, ticker)
        stored[gid] = ingest_catalyst(
            db,
            _SECURITY[ticker],
            catalyst_type=CatalystType.GOV_FUNDING,
            grade=c.grade,
            label=c.label.split(" · recorded ")[0],
            source="doe_usaspending",
            source_ref=c.source_ref,
            event_date=c.start_date,
            horizon_end=c.horizon_end,
        )
    db.commit()
    assert set(stored) == set(_MOVED) | set(_KEPT)
    return stored


def _table(db) -> list[dict]:
    """EVERY stored row, whole — the table itself, not a read through the as-of view."""
    with db.cursor() as cur:
        cur.execute("SELECT * FROM fact_catalyst ORDER BY id")
        rows = cur.fetchall()
    db.commit()
    return rows


def _by_award(db) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in _table(db):
        out.setdefault(R.award_id_of(r["source_ref"]), []).append(r)
    return out


def _journal(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT tool, action, rows_changed, deletes, counts FROM data_repair ORDER BY seq"
        )
        rows = cur.fetchall()
    db.commit()
    return rows


def _stamps(db) -> dict[uuid.UUID, int]:
    with db.cursor() as cur:
        cur.execute("SELECT security_id, count(*) AS n FROM security_change GROUP BY security_id")
        rows = cur.fetchall()
    db.commit()
    return {r["security_id"]: r["n"] for r in rows}


def _read(db, security_id, award: str, asof: date, clock: str) -> list[dict]:
    """The as-of read a card makes, for one award: the record clock at ``known_at`` = now (the live gate),
    the public clock capped at that night's end (a history view)."""
    rows = as_of(
        db,
        "fact_catalyst",
        security_id=security_id,
        asof=asof,
        known_at=db_now(db) if clock == "record" else known_at_for_asof(asof),
        tenant_id=tenants_for("fact_catalyst", data=DATA_TENANT_ID, workspace=DEFAULT_TENANT_ID),
        clock=clock,
    )
    db.commit()
    return [r for r in rows if r["source_ref"].endswith(award)]


# --- the plan ----------------------------------------------------------------------------------------------


def test_the_payload_directory_is_the_one_the_seed_reads():
    """The repair re-parses the SAME committed payloads ``pipeline.seed.seed_doe_catalysts`` replays."""
    assert R.PAYLOAD_DIR == seed_mod._SEED_DATA / "doe"
    assert (R.PAYLOAD_DIR / "award" / f"{_OKLO_OTA}.json").is_file()
    assert R.offline_client().allow_live is False  # never the network


def test_award_id_of_inverts_the_feeds_source_ref():
    assert R.award_id_of(f"{_URL}{_OKLO_OTA}") == _OKLO_OTA
    assert R.award_id_of("https://elsewhere.example/a/b/CONT_AWD_X") == "CONT_AWD_X"  # another base


def test_scan_finds_the_three_mis_dated_keys_and_keeps_the_three_contracts(db):
    stored = _store_as_before_the_rule(db)
    plans = {p.award_id: p for p in R.scan(db)}
    db.rollback()
    assert set(plans) == set(stored)
    for gid, (start, recorded) in _MOVED.items():
        p = plans[gid]
        assert p.action == R.RE_DATE and p.stored_dates == [start] and p.rule_date == recorded
        assert p.to_delete == [stored[gid]] and p.needs_append
        assert (p.parsed.start_date, p.parsed.recorded_date) == (start, recorded)
    for gid, recorded in _KEPT.items():
        p = plans[gid]
        assert p.action == R.KEEP and p.stored_dates == [recorded] and p.rule_date == recorded
        assert p.to_delete == [] and not p.needs_append
    assert plans[_OKLO_OTA].ticker == "OKLO" and plans[_LEU_GRANT].ticker == "LEU"


# --- the CLI -----------------------------------------------------------------------------------------------


def test_the_dry_run_lists_three_re_date_and_three_keep_and_writes_nothing(db, capsys):
    _store_as_before_the_rule(db)
    before, stamps_before = _table(db), _stamps(db)
    assert len(before) == 6

    R.main([])
    out = capsys.readouterr().out
    assert "DRY-RUN (nothing written)" in out
    lines = [ln for ln in out.splitlines() if " -> " in ln]
    assert len(lines) == 6  # one line per key
    assert sum("-> re-date" in ln for ln in lines) == 3
    assert sum(ln.endswith("-> keep") for ln in lines) == 3
    # ticker, award id, stored date(s), the rule's date, the start date, the record date, the action
    assert (
        f"  OKLO {_OKLO_OTA}: stored 2026-02-09 · rule 2026-05-18 · start 2026-02-09 · "
        "recorded 2026-05-18 -> re-date (delete 1 version(s), append 1)"
    ) in lines
    assert (
        f"  LEU {_LEU_GRANT}: stored 2012-06-01 · rule 2012-11-30 · start 2012-06-01 · "
        "recorded 2012-11-30 -> re-date (delete 1 version(s), append 1)"
    ) in lines
    assert (
        f"  LEU {_LEU_GRANT_2010}: stored 2010-03-23 · rule 2010-12-21 · start 2010-03-23 · "
        "recorded 2010-12-21 -> re-date (delete 1 version(s), append 1)"
    ) in lines
    assert (
        "  LEU CONT_AWD_89243223CNE000030_8900_-NONE-_-NONE-: stored 2022-11-30 · rule 2022-11-30 · "
        "start 2022-11-30 · recorded 2022-11-30 -> keep"
    ) in lines
    assert "6 key(s): 3 re-date, 3 keep, 0 no payload, 0 no date" in out
    assert "would delete 3 row(s) and append 3" in out

    assert _table(db) == before  # COUNT THE TABLE: same rows, same ids, same contents
    assert _stamps(db) == stamps_before and _journal(db) == []


def test_apply_deletes_the_three_early_rows_appends_three_at_the_record_date_and_is_idempotent(
    db, capsys
):
    stored = _store_as_before_the_rule(db)
    before = {r["id"]: r for r in _table(db)}
    assert len(before) == 6
    stamps_before = _stamps(db)
    # THE LOOKAHEAD, before the repair: on 2026-03-01 both clocks read an award not recorded until 05-18
    for clock in ("record", "public"):
        (early,) = _read(db, OKLO_ID, _OKLO_OTA, _BETWEEN, clock)
        assert early["valid_from"] == date(2026, 2, 9)

    R.main(["--apply"])
    out = capsys.readouterr().out
    assert "6 key(s): 3 re-date, 3 keep, 0 no payload, 0 no date" in out
    assert "total_deleted=3 total_appended=3" in out and "fact_catalyst rows 6 -> 6" in out

    after = _by_award(db)
    assert (
        sum(len(v) for v in after.values()) == 6
    )  # 6 - 3 deleted + 3 appended, counted at the table
    for gid, (start, recorded) in _MOVED.items():
        (row,) = after[
            gid
        ]  # ONE version: the early-dated one is gone, one corrected version appended
        old = before[stored[gid]]
        assert row["id"] != old["id"] and row["valid_from"] == recorded
        assert row["label"] == f"{old['label']} · recorded {recorded}, work period from {start}"
        assert row["recorded_at"] > old["recorded_at"]  # recorded NOW, never backdated
        for col in ("tenant_id", "security_id", "source_ref", "source", "grade", "horizon_end"):
            assert row[col] == old[col], col  # the same logical fact, only its date and label moved
        assert row["catalyst_type"] == "gov_funding" and row["ratified_by"] is None
    for gid in _KEPT:
        assert after[gid] == [before[stored[gid]]]  # the SAME row, untouched

    # the lookahead is closed on both clocks, and the award is read from its record date
    for clock in ("record", "public"):
        assert _read(db, OKLO_ID, _OKLO_OTA, _BETWEEN, clock) == []
        (row,) = _read(db, OKLO_ID, _OKLO_OTA, date(2026, 5, 18), clock)
        assert row["valid_from"] == date(2026, 5, 18)
        assert _read(db, LEU_ID, _LEU_GRANT, date(2012, 11, 29), clock) == []
        assert len(_read(db, LEU_ID, _LEU_GRANT, date(2012, 11, 30), clock)) == 1

    # both companies' stored data changed -> both carry a new change stamp (the dashboards' cache)
    stamps = _stamps(db)
    assert stamps[OKLO_ID] > stamps_before[OKLO_ID] and stamps[LEU_ID] > stamps_before[LEU_ID]

    (j,) = _journal(db)  # ONE journal row for the apply
    assert (j["tool"], j["action"], j["rows_changed"], j["deletes"]) == (
        "repair_catalyst_dates",
        "apply",
        6,
        True,
    )
    assert j["counts"] == {
        "deleted": 3,
        "appended": 3,
        "keys": 6,
        "ratified_left": 0,
        "re-date": 3,
        "keep": 3,
        "no payload": 0,
        "no date": 0,
    }

    # a SECOND --apply changes nothing: same rows, same ids, same contents (and it journals its zero)
    settled = _table(db)
    R.main(["--apply"])
    out = capsys.readouterr().out
    assert "6 key(s): 0 re-date, 6 keep, 0 no payload, 0 no date" in out
    assert "total_deleted=0 total_appended=0" in out
    assert _table(db) == settled
    assert [(r["rows_changed"], r["deletes"]) for r in _journal(db)] == [(6, True), (0, True)]


def test_a_ratified_row_is_never_touched_and_is_listed(db, capsys):
    """The operator's rows are out of scope whichever way they are marked: ``source = 'ratified'``, or a
    feed-sourced row carrying a ``ratified_by``. One that sits on a re-dated award at another date is LISTED
    (it is still read from its own date), never changed."""
    _store_as_before_the_rule(db)
    # the operator's ratification of the OKLO award, at the date ``seed_nuclear_catalyst`` gave it before
    # this change (each in its own transaction: the table is unique on (tenant, source_ref, recorded_at))
    on_award = ingest_catalyst(
        db,
        OKLO_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=Grade.FLIP,
        label="DOE Reactor Pilot Program OTA (DENE0009589) — ratified",
        source="ratified",
        source_ref=f"{_URL}{_OKLO_OTA}",
        event_date=date(2026, 2, 9),
        horizon_end=date(2029, 7, 1),
        ratified_by="operator",
    )
    db.commit()
    feed_row_ratified = ingest_catalyst(
        db,
        LEU_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=Grade.FLIP,
        label="DOE grant (DENE0000530) — the operator ratified the feed's row",
        source="doe_usaspending",
        source_ref=f"{_URL}{_LEU_GRANT}",
        event_date=date(2012, 6, 1),
        horizon_end=date(2014, 4, 15),
        ratified_by="operator",
    )
    db.commit()
    unrelated = ingest_catalyst(
        db,
        LEU_ID,
        catalyst_type=CatalystType.CONTRACT,
        grade=Grade.CORE,
        label="a ratified catalyst on no DOE award",
        source="ratified",
        source_ref="https://example.com/ppa",
        event_date=date(2026, 4, 1),
        ratified_by="operator",
    )
    db.commit()
    ratified_ids = {on_award, feed_row_ratified, unrelated}
    before = {r["id"]: r for r in _table(db) if r["id"] in ratified_ids}
    assert len(before) == 3

    R.main(["--apply"])
    out = capsys.readouterr().out
    assert "total_deleted=3 total_appended=3" in out  # the three FEED rows, exactly as without them
    notes = [ln for ln in out.splitlines() if ln.startswith("  NOTE ")]
    assert (
        len(notes) == 2
    )  # the two on re-dated awards; the unrelated one is not this tool's business
    assert any(f"OKLO {_OKLO_OTA}" in n and "dated 2026-02-09" in n for n in notes)
    assert any(f"LEU {_LEU_GRANT}" in n and "dated 2012-06-01" in n for n in notes)
    assert all("never touched" in n for n in notes)

    after = {r["id"]: r for r in _table(db) if r["id"] in ratified_ids}
    assert after == before  # all three rows byte-identical: same ids, same dates, same contents
    assert len(_table(db)) == 9  # 6 feed rows (3 replaced) + the 3 ratified rows
    assert _journal(db)[0]["counts"]["ratified_left"] == 2
    # ...and the flag is honest: the operator's early-dated row is still read on 2026-03-01
    (still,) = _read(db, OKLO_ID, _OKLO_OTA, _BETWEEN, "public")
    assert still["id"] == on_award and still["source"] == "ratified"


def test_every_early_dated_version_goes_and_a_key_already_dated_right_gets_no_second_version(db):
    """Two shapes the six clean rows do not have. (1) A key with SEVERAL early-dated versions (the boot seed
    appended one per start before it learned to skip a stored award): every one of them is read by a view
    before the record date, so every one goes. (2) A key that already carries a version at the rule's date (a
    feed run under the rule appended it): only its early-dated version goes; nothing is appended."""
    stored = _store_as_before_the_rule(db)
    client = R.offline_client()
    oklo = parse_award(client, _OKLO_OTA, "OKLO")
    second_early = ingest_catalyst(
        db,
        OKLO_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=oklo.grade,
        label=oklo.label.split(" · recorded ")[0],
        source="doe_usaspending",
        source_ref=oklo.source_ref,
        event_date=oklo.start_date,
        horizon_end=oklo.horizon_end,
    )
    grant = parse_award(client, _LEU_GRANT, "LEU")
    already_right = ingest_catalyst(
        db,
        LEU_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=grant.grade,
        label=grant.label,
        source="doe_usaspending",
        source_ref=grant.source_ref,
        event_date=grant.event_date,
        horizon_end=grant.horizon_end,
    )
    db.commit()
    assert len(_table(db)) == 8

    plans = R.scan(db)
    by = {p.award_id: p for p in plans}
    assert sorted(by[_OKLO_OTA].to_delete) == sorted([stored[_OKLO_OTA], second_early])
    assert by[_OKLO_OTA].needs_append
    assert by[_LEU_GRANT].to_delete == [stored[_LEU_GRANT]] and not by[_LEU_GRANT].needs_append
    assert by[_LEU_GRANT].stored_dates == [date(2012, 6, 1), date(2012, 11, 30)]

    result = R.apply(db, plans)
    assert (result.deleted, result.appended) == (
        4,
        2,
    )  # OKLO x2 + both LEU grants; OKLO + the 2010 grant
    assert (result.rows_before, result.rows_after) == (8, 6)
    after = _by_award(db)
    assert sum(len(v) for v in after.values()) == 6
    assert [r["id"] for r in after[_LEU_GRANT]] == [
        already_right
    ]  # kept, and no second version beside it
    (row,) = after[_OKLO_OTA]
    assert row["valid_from"] == date(2026, 5, 18)
    assert row["id"] not in (stored[_OKLO_OTA], second_early)


def test_a_key_with_no_payload_on_disk_is_listed_and_left_as_stored(db, capsys):
    """INPUT-CONTRACT (the award id is made up, so no payload exists for it): with nothing on disk to
    re-parse, the tool cannot know the rule's date, so it lists the key and changes nothing. It never
    fetches."""
    seed_nuclear(db)
    db.commit()
    orphan = ingest_catalyst(
        db,
        LEU_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=Grade.FLIP,
        label="DOE grant (NOT_ON_DISK)",
        source="doe_usaspending",
        source_ref=f"{_URL}ASST_NON_NOT_ON_DISK_089",
        event_date=date(2020, 1, 1),
        horizon_end=date(2021, 1, 1),
    )
    db.commit()
    before = _table(db)

    R.main(["--apply"])
    out = capsys.readouterr().out
    assert (
        "  LEU ASST_NON_NOT_ON_DISK_089: stored 2020-01-01 · rule n/a · start n/a · recorded n/a "
        "-> no payload, left as stored"
    ) in out
    assert "1 key(s): 0 re-date, 0 keep, 1 no payload, 0 no date" in out
    assert "total_deleted=0 total_appended=0" in out
    assert _table(db) == before and [r["id"] for r in before] == [orphan]


def test_a_count_mismatch_rolls_everything_back_and_exits_non_zero(db, monkeypatch, capsys):
    """The verification, on a real mismatch: an append that writes nothing leaves the table three rows short
    of the plan. The whole transaction — the three DELETEs included — is rolled back, the run exits 1 and
    nothing is journaled."""
    _store_as_before_the_rule(db)
    before, stamps_before = _table(db), _stamps(db)
    monkeypatch.setattr(R, "ingest_catalyst", lambda *a, **k: uuid.uuid4())

    with pytest.raises(SystemExit) as exc:
        R.main(["--apply"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ABORTED" in out and "ROLLED BACK, nothing written" in out
    assert "fact_catalyst went 6 -> 3, expected 6" in out
    assert _table(db) == before  # the deletes did not survive: same rows, same ids
    assert _stamps(db) == stamps_before and _journal(db) == []


def test_apply_exits_75_while_the_call_of_record_lock_is_held(db, capsys):
    _store_as_before_the_rule(db)
    before = _table(db)
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            R.main(["--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        assert "ALREADY RUNNING" in capsys.readouterr().out
        R.main([])  # a dry run takes no lock and still runs
        assert "DRY-RUN" in capsys.readouterr().out
    finally:
        held.release()
    assert _table(db) == before and _journal(db) == []


def test_the_cli_refuses_to_run_without_an_explicit_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(SystemExit) as exc:
        R.main([])
    assert "DATABASE_URL is not set" in str(exc.value)
