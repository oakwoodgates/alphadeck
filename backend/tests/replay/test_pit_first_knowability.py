"""D3a in REPLAY — the DuckDB/Parquet mirror applies the IDENTICAL first-knowability rule (the parity gate).

Both engines feed every visible version through the SAME pure ``db.bitemporal.first_knowable_versions``, so
parity is by construction — and these tests hold it to that, row-for-row with the annotation, on REAL version
histories (``tests/fundamentals_fixtures.py``: GEV's re-reported comparative, CEG's phantom, LRHC's shared
filing, KULR's restatement, Fluor's flip-flop), across transaction-time pins BETWEEN filings as well as after
them, on the scoped AND the basket-prefetched mirror reads, and down to the detector's event.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from db.bitemporal import _FACT_IDENTITY, KNOWABLE_FROM, KNOWABLE_SOURCE
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.market_time import known_at_for_asof
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals import revenue_acceleration as ra
from signals.base import PointInTimeData
from tests.fundamentals_fixtures import (
    CEG,
    FLR_2017Q1,
    GEV,
    KULR,
    LRHC,
    store_versions,
    store_versions_live,
)
from tests.replay.test_pit_parity import _match

_FIXTURES = {"GEV": GEV, "CEG": CEG, "LRHC": LRHC, "KULR": KULR, "FLR": FLR_2017Q1}
_IDENT = _FACT_IDENTITY["fact_fundamentals"]

# (asof, known_at): between GEV's two Q1-2025 filings (lockstep, then event time AHEAD of transaction
# time), after them, the 2026-10-15 night, LRHC's shared-filing window, Fluor between two versions, and the
# far-future pin the parity gate already sweeps.
_PINS = [
    (date(2025, 6, 2), known_at_for_asof(date(2025, 6, 2))),
    (date(2026, 10, 1), known_at_for_asof(date(2025, 6, 2))),
    (date(2026, 10, 1), known_at_for_asof(date(2026, 10, 1))),
    (date(2026, 10, 15), known_at_for_asof(date(2026, 10, 15))),
    (date(2026, 7, 1), known_at_for_asof(date(2026, 7, 1))),
    (date(2018, 6, 1), known_at_for_asof(date(2018, 6, 1))),
    (date(2026, 10, 15), datetime(2100, 1, 1, tzinfo=timezone.utc)),
]


@pytest.fixture
def mirror(db, tmp_path):
    """Every fixture on its own security, stored exactly as prod holds it (the pre-0057 stamp), exported,
    opened."""
    sids: dict[str, uuid.UUID] = {}
    with db.cursor() as cur:
        for name in _FIXTURES:
            sid = uuid.uuid4()
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, name, f"{sid.int % 10**10:010d}", "2020-01-01"),
            )
            sids[name] = sid
    db.commit()
    for name, versions in _FIXTURES.items():
        store_versions(db, sids[name], versions)
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        yield sids, con
    finally:
        con.close()


def test_mirror_first_knowability_matches_postgres_row_for_row(db, mirror):
    """THE PARITY GATE for D3a: at every pin, for every fixture, the live ``PointInTimeData`` rows equal the
    mirror's — the scoped read AND the basket prefetch — with ``knowable_from`` / ``knowable_accession``
    compared like every other column. Counts first (a prefetch keyed on the wrong type serves empties).
    """
    sids, con = mirror
    basket = list(sids.values())
    for asof, known_at in _PINS:
        live_pit = PointInTimeData(db, asof=asof, known_at=known_at, basket=basket)
        scoped = ReplayPointInTimeData(con, asof=asof, known_at=known_at)
        batched = ReplayPointInTimeData(con, asof=asof, known_at=known_at, basket=basket)
        for name, sid in sids.items():
            live = live_pit.fundamentals_facts(sid)
            rep_s, rep_b = scoped.fundamentals_facts(sid), batched.fundamentals_facts(sid)
            assert len(live) == len(rep_s) == len(rep_b), (name, asof, known_at)
            assert all(KNOWABLE_FROM in r and KNOWABLE_SOURCE in r for r in live + rep_s + rep_b)
            _match(live, rep_s, _IDENT)
            _match(live, rep_b, _IDENT)
            assert rep_s == rep_b, (name, asof, known_at)


def test_detector_events_are_identical_live_and_in_replay(db, mirror):
    """Down to the event: the revenue detector over the mirror emits exactly what it emits over Postgres
    at every pin (or declines on both)."""
    sids, con = mirror
    for asof, known_at in _PINS:
        for name, sid in sids.items():
            live = ra.detect(
                PointInTimeData(db, asof=asof, known_at=known_at), sid, asof, DEFAULT_CONFIG
            )
            rep = ra.detect(
                ReplayPointInTimeData(con, asof=asof, known_at=known_at), sid, asof, DEFAULT_CONFIG
            )
            assert (live is None) == (rep is None), (name, asof, known_at)
            if live is not None:
                assert live.model_dump(mode="json") == rep.model_dump(mode="json"), (name, asof)


def test_mirror_hides_the_comparative_between_filings_and_dates_at_the_first_filing(mirror):
    """The mirror analog of the Postgres PIT test: pinned between GEV's 2025-04-23 filing and its
    2026-04-22 comparative, the comparative is invisible and Q1-2025 dates at 2025-04-23; after it, the
    comparative is the picked version but the date does not move."""
    sids, con = mirror
    gev = sids["GEV"]
    between = known_at_for_asof(date(2025, 6, 2))
    rows = {
        r["period_end"]: r
        for r in ReplayPointInTimeData(
            con, asof=date(2026, 10, 1), known_at=between
        ).fundamentals_facts(gev)
    }
    q = rows[date(2025, 3, 31)]
    assert q["accession"] == "0001996810-25-000073" and q[KNOWABLE_FROM] == date(2025, 4, 23)
    assert date(2026, 3, 31) not in rows
    after = known_at_for_asof(date(2026, 10, 1))
    rows = {
        r["period_end"]: r
        for r in ReplayPointInTimeData(
            con, asof=date(2026, 10, 1), known_at=after
        ).fundamentals_facts(gev)
    }
    q = rows[date(2025, 3, 31)]
    assert q["accession"] == "0001996810-26-000064" and q["valid_from"] == date(2026, 4, 22)
    assert q[KNOWABLE_FROM] == date(2025, 4, 23)
    assert q[KNOWABLE_SOURCE] == "0001996810-25-000073"


def test_replay_gev_cites_q1_2026_and_ceg_does_not_rearm_on_its_comparative(mirror):
    """The two live cases, in replay: GEV at 2026-10-01 fires on its genuine Q1-2026 (dated 2026-04-22),
    and CEG at 2026-10-15 has no live revenue trigger — its Q1-2025 inflection dates at 2025-05-06.
    """
    sids, con = mirror
    a = date(2026, 10, 1)
    ev = ra.detect(
        ReplayPointInTimeData(con, asof=a, known_at=known_at_for_asof(a)),
        sids["GEV"],
        a,
        DEFAULT_CONFIG,
    )
    assert ev is not None and ev.asof == date(2026, 4, 22) and "ending 2026-03-31" in ev.label
    n = date(2026, 10, 15)
    pit = ReplayPointInTimeData(con, asof=n, known_at=known_at_for_asof(n))
    assert ra.detect(pit, sids["CEG"], n, DEFAULT_CONFIG) is None


# --- migration 0057: the honest stamp, in replay ---------------------------------------------------------


@pytest.fixture
def honest(db):
    """Every fixture stored TWICE — as prod holds it (the pre-0057 backdated stamp) and through today's
    writer (one honest stamp per history, a name onboarded tonight). Returns the two id maps and each
    honest history's stamp, keyed by its security."""
    legacy: dict[str, uuid.UUID] = {}
    live: dict[str, uuid.UUID] = {}
    with db.cursor() as cur:
        for name in _FIXTURES:
            for target, tag in ((legacy, ""), (live, "-live")):
                sid = uuid.uuid4()
                cur.execute(
                    "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (sid, DEFAULT_TENANT_ID, name + tag, f"{sid.int % 10**10:010d}", "2020-01-01"),
                )
                target[name] = sid
    db.commit()
    stamps: dict[uuid.UUID, datetime] = {}
    for name, versions in _FIXTURES.items():
        store_versions(db, legacy[name], versions)
        stamps[live[name]] = store_versions_live(db, live[name], versions)
    return legacy, live, stamps


def test_honest_stamps_hold_replay_parity_at_every_pin(db, honest, tmp_path):
    """THE PARITY GATE, under the honest stamp: on a record-clock mirror of BOTH stores, the live
    ``PointInTimeData`` rows equal the mirror's — scoped and prefetched, annotation included — at every
    historical pin (where the honest rows are not held yet: both engines return nothing for them), just
    before and at each honest history's stamp, and far ahead."""
    legacy, live, stamps = honest
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        sids = [*legacy.values(), *live.values()]
        pins = list(_PINS)
        for stamp in stamps.values():
            pins += [
                (date(2026, 10, 15), stamp - timedelta(microseconds=1)),
                (date(2026, 10, 15), stamp),
            ]
        for asof, known_at in pins:
            live_pit = PointInTimeData(db, asof=asof, known_at=known_at, basket=sids)
            scoped = ReplayPointInTimeData(con, asof=asof, known_at=known_at)
            batched = ReplayPointInTimeData(con, asof=asof, known_at=known_at, basket=sids)
            for sid in sids:
                pg = live_pit.fundamentals_facts(sid)
                rep_s, rep_b = scoped.fundamentals_facts(sid), batched.fundamentals_facts(sid)
                assert len(pg) == len(rep_s) == len(rep_b), (sid, asof, known_at)
                _match(pg, rep_s, _IDENT)
                _match(pg, rep_b, _IDENT)
                if sid in stamps:
                    held = known_at >= stamps[sid]
                    assert bool(pg) == held, "an honest row is visible exactly from its stamp on"
    finally:
        con.close()


@pytest.mark.parametrize("clock", ["record", "public"])
def test_a_backtest_reads_honest_rows_exactly_like_backdated_ones(db, honest, tmp_path, clock):
    """Replay is unmoved. Under the determinism pin (``known_at`` after every write) on a record-clock
    mirror — and at EVERY pin, lockstep included, on a public-clock mirror (which clocks fundamentals at
    their filing whatever the stamp) — a history written under one honest stamp reads row-for-row and
    event-for-event like the same history under the backdated stamps."""
    legacy, live, _stamps = honest
    export_snapshot(db, tmp_path, clock=clock)
    con = connect_mirror(tmp_path)
    try:
        pins = _PINS if clock == "public" else [p for p in _PINS if p[1].year == 2100]
        for asof, known_at in pins:
            pit = ReplayPointInTimeData(con, asof=asof, known_at=known_at)
            for name in _FIXTURES:
                a, b = pit.fundamentals_facts(legacy[name]), pit.fundamentals_facts(live[name])
                # the bookkeeping stamps differ by construction: ``recorded_at`` on a record mirror, and on a
                # public mirror the honest ingest instant it carries as ``ingested_at`` (``db.public_clock``)
                strip = ("id", "security_id", "recorded_at", "ingested_at")
                assert [{k: v for k, v in r.items() if k not in strip} for r in a] == [
                    {k: v for k, v in r.items() if k not in strip} for r in b
                ], (clock, name, asof)
                ea = ra.detect(pit, legacy[name], asof, DEFAULT_CONFIG)
                eb = ra.detect(pit, live[name], asof, DEFAULT_CONFIG)
                assert (ea is None) == (eb is None), (clock, name, asof)
                if ea is not None:
                    da, db_ = ea.model_dump(mode="json"), eb.model_dump(mode="json")
                    da.pop("security_id"), db_.pop("security_id")
                    assert da == db_, (clock, name, asof)
    finally:
        con.close()
