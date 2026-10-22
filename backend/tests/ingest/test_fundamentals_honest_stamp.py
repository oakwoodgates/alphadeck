"""Migration 0057 — the fundamentals ingest stamps HONEST transaction time, and no call moves.

``fact_fundamentals.recorded_at`` used to be backdated to ``filed 00:00 UTC`` (MEASURED: all 120,876 prod rows);
the writer now stamps the database instant it stored the row — honest bookkeeping, never the history gate (the
public clock judges history; INVARIANTS.md §4). Those existing rows are kept as written. The read side keeps
every call where it was: a quarter dates by its FILING (``knowable_from``) and the pick is the latest FILING, so
a whole history written under ONE honest stamp reads exactly like the per-filing backdated stamps.

Every history is a REAL prod version history (``tests/fundamentals_fixtures.py``), stored either exactly as
prod holds it (``store_versions`` — the pre-0057 stamp) or through the real writer today
(``store_versions_live``). The pre-0057 READER is restated here as an independent oracle (the ``DISTINCT ON``
pick + D3a's ``max(valid_from, market date of recorded_at)`` over the trailing identical run) — direct
inputs, no fitted outcome. DB-backed (skips without Postgres).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from db.bitemporal import KNOWABLE_FROM, KNOWABLE_SOURCE, append_fact, as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.market_time import known_at_for_asof, market_tz
from ingest.fundamentals import fundamentals_row, store_quarters
from signals import revenue_acceleration as ra
from signals.base import PointInTimeData
from tests.fundamentals_fixtures import (
    CEG,
    FLR_2017Q1,
    GEV,
    KULR,
    LRHC,
    Version,
    legacy_stamp,
    quarter_points,
    store_versions,
    store_versions_live,
)

_FIXTURES: dict[str, list[Version]] = {
    "GEV": GEV,
    "CEG": CEG,
    "LRHC": LRHC,
    "KULR": KULR,
    "FLR": FLR_2017Q1,
}
_ASOF = date(2026, 10, 15)
_FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
# the columns that legitimately differ between two stores of one history (row identity + the stamp itself)
_STORE_COLS = ("id", "security_id", "recorded_at")


def _security(db: psycopg.Connection, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, f"{sid.int % 10**10:010d}", "2020-01-01"),
        )
    db.commit()
    return sid


def _read(db: psycopg.Connection, sid: uuid.UUID, asof: date, known_at: datetime) -> list[dict]:
    return as_of(
        db,
        "fact_fundamentals",
        security_id=sid,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
    )


def _strip(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in _STORE_COLS} for r in rows]


def _count(db: psycopg.Connection, sid: uuid.UUID) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_fundamentals WHERE security_id = %s", (sid,))
        return cur.fetchone()["n"]


def _stamps(db: psycopg.Connection, sid: uuid.UUID) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT period_end, valid_from, accession, recorded_at FROM fact_fundamentals "
            "WHERE security_id = %s ORDER BY period_end, valid_from",
            (sid,),
        )
        return cur.fetchall()


# --- the writer -----------------------------------------------------------------------------------------


def test_a_new_row_is_stamped_within_the_db_clock_window_of_the_run(db):
    """The real writer stamps every row it appends with ONE database instant taken during the run — never
    the filing date — and leaves ``valid_from`` at the filing (the knowability the call reads)."""
    sid = _security(db, "GEV")
    before = db_now(db)
    db.commit()
    result = store_quarters(db, sid, quarter_points(GEV))
    db.commit()
    after = db_now(db)
    db.commit()
    rows = _stamps(db, sid)
    assert result.appended == len(rows) == len(GEV)
    (stamp,) = {r["recorded_at"] for r in rows}  # one instant for the batch
    assert before <= stamp <= after
    assert sorted((r["period_end"], r["valid_from"], r["accession"]) for r in rows) == sorted(
        (pe, filed, accn) for pe, _v, filed, accn, *_ in GEV
    )
    assert not any(r["recorded_at"] == legacy_stamp(r["valid_from"]) for r in rows)


def test_a_rerun_appends_zero_and_a_duplicate_filing_is_refused(db):
    """Idempotency, COUNTING THE TABLE: a second pull of the same history appends nothing (the writer's
    ``(metric, period_end, filed)`` check). And with the stamp no longer a function of the filing, the
    table's own guard moved to the filing too (migration 0057): a second row for one (period, FILING) under
    a different stamp is refused loudly — never a silent extra version hiding behind a correct read.
    """
    sid = _security(db, "CEG")
    store_versions_live(db, sid, CEG)
    n = _count(db, sid)
    again = store_quarters(db, sid, quarter_points(CEG))
    db.commit()
    assert (again.appended, again.skipped) == (0, len(CEG))
    assert _count(db, sid) == n == len(CEG)
    q = quarter_points(CEG)[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        append_fact(
            db,
            "fact_fundamentals",
            {**fundamentals_row(sid, q), "recorded_at": _FAR - timedelta(days=1)},
        )
    db.rollback()
    assert _count(db, sid) == n


def test_the_version_key_is_the_filing_not_the_stamp(db):
    """The constraint set (0057, widened by 0060): the UNIQUE keys one ORIGINAL per (tenant, security, metric,
    period_end, FILING) — ``supersedes`` NULL, NULLS NOT DISTINCT — plus one re-parse per version it replaces
    (the revenue-label fix's chain; ``tests/ingest/test_revenue_label.py``), and no UNIQUE carries
    ``recorded_at`` any more (it would reject a history written under one honest stamp)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'fact_fundamentals'::regclass AND contype = 'u'"
        )
        uniques = {r["conname"]: r["def"] for r in cur.fetchall()}
    assert uniques == {
        "fact_fundamentals_filing_reparse_key": (
            "UNIQUE NULLS NOT DISTINCT "
            "(tenant_id, security_id, metric_key, period_end, valid_from, supersedes)"
        )
    }


# --- the read: the call does not move ----------------------------------------------------------------


@pytest.mark.parametrize("name", list(_FIXTURES))
def test_one_honest_stamp_reads_exactly_like_the_backdated_stamps(db, name):
    """THE CALL-PRESERVATION GATE. One real history stored twice — as prod holds it (per-filing backdated
    stamps) and through today's writer (one honest stamp, a name onboarded tonight). Read live (``known_at``
    after both) at every filing date of the history and at 2026-10-15: the rows are identical in every
    column but row identity and the stamp — the same picked version, the same ``knowable_from`` /
    ``knowable_accession`` — and the revenue detector fires (or declines) identically."""
    versions = _FIXTURES[name]
    legacy, live = _security(db, name), _security(db, f"{name}-live")
    store_versions(db, legacy, versions)
    store_versions_live(db, live, versions)
    known_at = db_now(db)
    db.commit()
    asofs = sorted({filed for _pe, _v, filed, *_ in versions} | {_ASOF})
    fired = 0
    for asof in asofs:
        assert _strip(_read(db, live, asof, known_at)) == _strip(_read(db, legacy, asof, known_at))
        pit = PointInTimeData(db, asof=asof, known_at=known_at)
        ev_legacy = ra.detect(pit, legacy, asof, DEFAULT_CONFIG)
        ev_live = ra.detect(pit, live, asof, DEFAULT_CONFIG)
        assert (ev_legacy is None) == (ev_live is None), (name, asof)
        if ev_legacy is not None:
            fired += 1
            assert _event(ev_live) == _event(ev_legacy), (name, asof)
    if name in ("GEV", "LRHC"):  # histories with a live inflection somewhere in the sweep
        assert fired, "the sweep must reach a firing date, or it compares only declines"


def _event(ev: Any) -> dict:
    return {k: v for k, v in ev.model_dump(mode="json").items() if k != "security_id"}


def test_a_read_pinned_before_the_stamp_sees_nothing_the_system_had_not_stored(db):
    """The stamp is honest bookkeeping: a history written tonight carries tonight's store instant, so the
    store-time gate admits it from that instant on and not before (the pre-0057 stamp claimed 2025). History
    itself is judged on the public clock (``valid_from``), which this stamp never touches."""
    sid = _security(db, "GEV")
    stamp = store_versions_live(db, sid, GEV)
    assert _read(db, sid, _ASOF, stamp - timedelta(microseconds=1)) == []
    assert _read(db, sid, _ASOF, known_at_for_asof(date(2025, 6, 2))) == []
    assert len(_read(db, sid, _ASOF, stamp)) == len({pe for pe, *_ in GEV})


def test_a_new_comparative_on_an_old_history_moves_only_its_visibility(db):
    """The nightly shape: GEV's pre-0057 rows are on file (backdated) and the 2026-04-22 10-Q (Q1-2026 +
    Q1-2025 re-reported as its comparative) arrives through the honest writer. After the stamp the read
    equals the all-backdated history (the comparative is picked; Q1-2025 still dates at its 2025-04-23
    filing); before the stamp it equals the all-backdated history as it stood before that 10-Q was on file.
    The stamp moved WHEN this system held the 10-Q — nothing else."""
    tenq = date(2026, 4, 22)
    asof = date(2026, 6, 1)  # after the 10-Q, before GEV's next filing (2026-07-22)
    mixed, legacy = _security(db, "GEV-mixed"), _security(db, "GEV")
    store_versions(db, mixed, [v for v in GEV if v[2] < tenq])
    stamp = store_versions_live(db, mixed, [v for v in GEV if v[2] == tenq])
    store_versions(db, legacy, GEV)
    after = _read(db, mixed, asof, stamp)
    assert _strip(after) == _strip(_read(db, legacy, asof, stamp))
    q1_2025 = next(r for r in after if r["period_end"] == date(2025, 3, 31))
    assert q1_2025["valid_from"] == tenq and q1_2025[KNOWABLE_FROM] == date(2025, 4, 23)
    assert q1_2025[KNOWABLE_SOURCE] == "0001996810-25-000073"
    assert any(r["period_end"] == date(2026, 3, 31) for r in after)  # Q1-2026, first reported
    before = _read(db, mixed, asof, stamp - timedelta(microseconds=1))
    not_yet = legacy_stamp(tenq) - timedelta(microseconds=1)  # the backdated rows, before the 10-Q
    assert _strip(before) == _strip(_read(db, legacy, asof, not_yet))
    assert not any(r["period_end"] == date(2026, 3, 31) for r in before)


# --- existing rows: exactly as before ----------------------------------------------------------------


def _pre_0057_read(
    db: psycopg.Connection, sid: uuid.UUID, asof: date, known_at: datetime
) -> list[dict[str, Any]]:
    """The pre-0057 READER, restated independently: the ``DISTINCT ON ... recorded_at DESC, id DESC`` pick,
    annotated with the earliest ``max(valid_from, market date of recorded_at)`` over the trailing run of
    value-identical versions in ``recorded_at, id`` order (a tie keeping the earliest-recorded)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (security_id, metric_key, period_end) * FROM fact_fundamentals "
            "WHERE tenant_id = %s AND security_id = %s AND valid_from <= %s AND recorded_at <= %s "
            "ORDER BY security_id, metric_key, period_end, recorded_at DESC, id DESC",
            (DEFAULT_TENANT_ID, sid, asof, known_at),
        )
        picks = cur.fetchall()
        cur.execute(
            "SELECT * FROM fact_fundamentals WHERE tenant_id = %s AND security_id = %s "
            "AND valid_from <= %s AND recorded_at <= %s ORDER BY recorded_at, id",
            (DEFAULT_TENANT_ID, sid, asof, known_at),
        )
        history = cur.fetchall()
    tz = market_tz()
    out = []
    for pick in picks:
        ident = (pick["metric_key"], pick["period_end"])
        vs = [v for v in history if (v["metric_key"], v["period_end"]) == ident]
        assert vs[-1]["id"] == pick["id"]
        run = [vs[-1]]
        for v in reversed(vs[:-1]):
            if float(v["value"]) != float(pick["value"]):
                break
            run.insert(0, v)
        first = min(run, key=lambda v: max(v["valid_from"], v["recorded_at"].astimezone(tz).date()))
        out.append(
            {
                **pick,
                KNOWABLE_FROM: max(first["valid_from"], first["recorded_at"].astimezone(tz).date()),
                KNOWABLE_SOURCE: first["accession"],
            }
        )
    return out


@pytest.mark.parametrize("name", list(_FIXTURES))
def test_existing_rows_read_exactly_as_before_at_every_pin(db, name):
    """No rewrite of history, and no re-reading of it either: over the rows prod holds today (the
    pre-0057 stamp), the new reader equals the pre-0057 reader row-for-row — pick, every column and the
    annotation — at ``known_at`` pinned the day before and the day of EVERY filing in the history (event
    time on that day and held ahead), and far in the future."""
    sid = _security(db, name)
    versions = _FIXTURES[name]
    store_versions(db, sid, versions)
    pins = [(_ASOF, _FAR)]
    for filed in sorted({v[2] for v in versions}):
        for day in (filed - timedelta(days=1), filed):
            pins += [(day, known_at_for_asof(day)), (_ASOF, known_at_for_asof(day))]
    for asof, known_at in pins:
        assert _read(db, sid, asof, known_at) == _pre_0057_read(db, sid, asof, known_at), (
            name,
            asof,
            known_at,
        )
