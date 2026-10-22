"""The PUBLIC clock as-of read (``clock="public"``) — history judged on when each fact became PUBLIC, not on
when this system ingested it (operator decision 2026-10-21; ``docs/INVARIANTS.md`` §4, ``db/public_clock.py``).

What these pin, each on the read the history views use (``db.bitemporal`` + ``signals.base.PointInTimeData``):

- a fact ingested LATE but public before the as-of is VISIBLE (a Form 4 accepted before the as-of, a bar
  settled before it) — the record clock hid it, and that only replayed our own outage;
- a correction published AFTER the as-of is NOT visible (a vendor re-version counts from its own
  publication — its ``recorded_at``, since it carries no publication time of its own);
- a Form 4 counts from its SEC acceptance, never its transaction date, and a bar from its session SETTLE;
- a fact with no acceptance on any version is never dropped and never early (from when we first held it);
- a re-parse of one filing shares that filing's public instant, so our best parse is what a past view reads;
- with every version visible the public pick IS the record pick (the live coincidence, by construction);
- the basket read equals the per-security read, on the public clock too.

DB-backed (skips without Postgres). Fixed instants throughout — never an ambient "now" in an assertion.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from db.bitemporal import _FACT_IDENTITY, append_fact, as_of, as_of_many
from db.public_clock import INGESTED_COL, PUBLIC_TIME, SCOPE_COL, STORE_THROUGH_COL
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof, market_tz
from signals.base import PointInTimeData

T = DEFAULT_TENANT_ID
FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)


def _eod(d: date) -> datetime:
    return known_at_for_asof(d, now=FAR)


def _et(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=market_tz())


def _insider(sid, *, accession, valid_from, recorded_at, accepted=None, role="CEO", usd=1_000_000):
    v = {
        "tenant_id": T,
        "security_id": sid,
        "insider_name": "A Director",
        "insider_role": role,
        "txn_code": "P",
        "shares": 10_000,
        "price": 100.0,
        "usd": usd,
        "accession": accession,
        "txn_seq": 0,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }
    if accepted is not None:
        v["accepted"] = accepted
    return v


def _bar(sid, d, close, recorded_at):
    return {
        "tenant_id": T,
        "security_id": sid,
        "d": d,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000,
        "valid_from": d,
        "recorded_at": recorded_at,
    }


def _read(db, table, sid, asof, known_at, clock):
    return as_of(db, table, security_id=sid, asof=asof, known_at=known_at, tenant_id=T, clock=clock)


# --- the registry ------------------------------------------------------------------------------------


def test_every_fact_table_has_exactly_one_public_clock():
    """ONE definition per table: the history read and the backtest export read the same registry, so a
    new fact table without a public clock fails here before it can silently read on the record clock.
    """
    assert set(PUBLIC_TIME) == set(_FACT_IDENTITY)
    assert set(SCOPE_COL) == set(_FACT_IDENTITY)


# --- late ingest is not hidden; a later publication is -----------------------------------------------


def test_a_form4_ingested_late_but_public_before_the_asof_is_visible(db, security_id):
    """THE RULE'S CASE: a Form 4 accepted 06-03, ingested 09-01 (an outage / a late onboarding). A 06-05
    view on the public clock SEES it (it was public); the record clock hid it, which only replayed our own
    ingest history. Before its acceptance it is invisible — the transaction date (06-01) is not the clock.
    """
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="late-1",
            valid_from=date(2026, 6, 1),
            accepted=datetime(2026, 6, 3, 21, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
    )
    db.commit()
    asof = date(2026, 6, 5)
    public = _read(db, "fact_insider_txn", security_id, asof, _eod(asof), "public")
    assert [r["accession"] for r in public] == ["late-1"]
    # the row carries the axis it was judged on, and our honest ingest instant beside it
    assert public[0]["recorded_at"] == datetime(2026, 6, 3, 21, 0, tzinfo=timezone.utc)
    assert public[0][INGESTED_COL] == datetime(2026, 9, 1, tzinfo=timezone.utc)
    # traded 06-01, accepted 06-03 at 17:00 ET: a 06-02 view cannot see it, nor 06-03 before 17:00 ET
    assert (
        _read(
            db, "fact_insider_txn", security_id, date(2026, 6, 2), _eod(date(2026, 6, 2)), "public"
        )
        == []
    )
    before = _et(date(2026, 6, 3), 16, 59)
    assert _read(db, "fact_insider_txn", security_id, date(2026, 6, 3), before, "public") == []


def test_a_correction_published_after_the_asof_is_not_visible(db, security_id):
    """A bar first ingested LATE (09-01) at 100, then corrected by the vendor on 09-10 to 105. The first
    version counts from its session's settle (06-01 18:00 ET), so a 06-05 view sees 100; the correction
    carries no publication time of its own, so it counts from its own ``recorded_at`` — a 09-09 view still
    reads 100, a 09-10 view reads 105. A correction never back-dates to the bar's own day."""
    d = date(2026, 6, 1)
    append_fact(
        db, "fact_price_eod", _bar(security_id, d, 100.0, datetime(2026, 9, 1, tzinfo=timezone.utc))
    )
    append_fact(db, "fact_price_eod", _bar(security_id, d, 105.0, _et(date(2026, 9, 10), 9)))
    db.commit()

    def close_at(asof):
        rows = _read(db, "fact_price_eod", security_id, asof, _eod(asof), "public")
        return [float(r["close"]) for r in rows]

    assert close_at(date(2026, 6, 5)) == [100.0]
    assert close_at(date(2026, 9, 9)) == [100.0]
    assert close_at(date(2026, 9, 10)) == [105.0]
    first = _read(
        db, "fact_price_eod", security_id, date(2026, 6, 5), _eod(date(2026, 6, 5)), "public"
    )[0]
    assert first["recorded_at"] == _et(d, 18)  # the settle instant (Settings.price_settle_time)
    assert first[INGESTED_COL] == datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_a_bar_counts_from_its_settle_not_the_start_of_its_day(db, security_id):
    d = date(2026, 6, 1)
    append_fact(
        db, "fact_price_eod", _bar(security_id, d, 50.0, datetime(2026, 9, 1, tzinfo=timezone.utc))
    )
    db.commit()
    assert _read(db, "fact_price_eod", security_id, d, _et(d, 17, 59), "public") == []
    assert len(_read(db, "fact_price_eod", security_id, d, _et(d, 18), "public")) == 1


def test_a_form4_with_no_acceptance_counts_from_when_we_first_held_it(db, security_id):
    """No version carries ``accepted`` (0.66% of insider facts on prod, MEASURED 2026-10-21): the serve
    path neither drops it (#9) nor dates it early — it counts from the FIRST time we held it, an upper bound
    on its publication (a filing we held was public by then)."""
    first = datetime(2026, 6, 10, 2, 30, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _insider(security_id, accession="nc", valid_from=date(2026, 6, 1), recorded_at=first),
    )
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="nc",
            valid_from=date(2026, 6, 1),
            recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            role="REPARSED",
        ),
    )
    db.commit()
    assert (
        _read(
            db, "fact_insider_txn", security_id, date(2026, 6, 8), _eod(date(2026, 6, 8)), "public"
        )
        == []
    )
    (row,) = _read(
        db, "fact_insider_txn", security_id, date(2026, 6, 9), _eod(date(2026, 6, 9)), "public"
    )
    assert row["recorded_at"] == first  # 06-09 22:30 ET, the night we first held it
    assert row["insider_role"] == "REPARSED"  # ...and the best parse of it


def test_a_reparse_shares_its_filings_public_instant_so_the_best_parse_is_read(db, security_id):
    """Our own repair of a public filing (a later ``recorded_at``, the same acceptance) is today's reading of
    what was public — on the public clock it counts from the filing, so a past view reads the BEST parse, not
    the state our database happened to hold that day (no record-clock replay)."""
    acc = datetime(2026, 6, 3, 18, 0, tzinfo=timezone.utc)
    for rec, role in [
        (datetime(2026, 6, 4, tzinfo=timezone.utc), "OLD ROLE"),
        (datetime(2026, 8, 1, tzinfo=timezone.utc), "REPAIRED ROLE"),
    ]:
        append_fact(
            db,
            "fact_insider_txn",
            _insider(
                security_id,
                accession="rp",
                valid_from=date(2026, 6, 1),
                recorded_at=rec,
                accepted=acc,
                role=role,
            ),
        )
    db.commit()
    asof = date(2026, 6, 5)
    (pub,) = _read(db, "fact_insider_txn", security_id, asof, _eod(asof), "public")
    assert pub["insider_role"] == "REPAIRED ROLE"


# --- the live coincidence + the basket read ------------------------------------------------------------


def _mixed_history(db, sid) -> None:
    """Several versions on every kind of public clock: a re-versioned bar, a re-parsed Form 4, an
    undatable Form 4, an 8-K resolved later, a restated fundamentals quarter."""
    append_fact(
        db,
        "fact_price_eod",
        _bar(sid, date(2026, 6, 1), 10.0, datetime(2026, 6, 2, tzinfo=timezone.utc)),
    )
    append_fact(
        db,
        "fact_price_eod",
        _bar(sid, date(2026, 6, 1), 10.5, datetime(2026, 6, 9, tzinfo=timezone.utc)),
    )
    append_fact(
        db,
        "fact_price_eod",
        _bar(sid, date(2026, 6, 2), 11.0, datetime(2026, 9, 1, tzinfo=timezone.utc)),
    )
    acc = datetime(2026, 6, 3, 18, 0, tzinfo=timezone.utc)
    for rec, role in [
        (datetime(2026, 6, 4, tzinfo=timezone.utc), "A"),
        (datetime(2026, 8, 1, tzinfo=timezone.utc), "B"),
    ]:
        append_fact(
            db,
            "fact_insider_txn",
            _insider(
                sid,
                accession="mx-1",
                valid_from=date(2026, 6, 1),
                recorded_at=rec,
                accepted=acc,
                role=role,
            ),
        )
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            sid,
            accession="mx-2",
            valid_from=date(2026, 6, 2),
            recorded_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        ),
    )
    for items, rec in [
        (None, datetime(2026, 6, 2, tzinfo=timezone.utc)),
        (["2.02"], datetime(2026, 6, 20, tzinfo=timezone.utc)),
    ]:
        append_fact(
            db,
            "fact_corporate_event",
            {
                "tenant_id": T,
                "security_id": sid,
                "form": "8-K",
                "items": items,
                "accession": "mx-8k",
                "filed": date(2026, 6, 1),
                "source_ref": "https://www.sec.gov/x",
                "valid_from": date(2026, 6, 1),
                "recorded_at": rec,
            },
        )
    for value, filed in [
        (100.0, date(2026, 5, 1)),
        (90.0, date(2026, 6, 2)),
        (90.0, date(2026, 6, 3)),
    ]:
        append_fact(
            db,
            "fact_fundamentals",
            {
                "tenant_id": T,
                "security_id": sid,
                "metric_key": "revenue",
                "period_end": date(2026, 3, 31),
                "fiscal_period": "Q1",
                "fiscal_year": 2026,
                "value": value,
                "unit": "USD",
                "basis": "native",
                "accession": f"f-{filed.isoformat()}",
                "source": "companyfacts",
                "valid_from": filed,
                "recorded_at": datetime.combine(filed, time.min, tzinfo=timezone.utc),
            },
        )
    db.commit()


def _strip(rows):
    """Everything but the transaction axis (which the two clocks name differently by design) — and the
    public read's ``split_store_through`` annotation (the scale a bar's stored value is on, S2)."""
    return [
        {k: v for k, v in r.items() if k not in ("recorded_at", INGESTED_COL, STORE_THROUGH_COL)}
        for r in rows
    ]


def test_with_every_version_visible_the_public_pick_IS_the_record_pick(db, security_id):
    """The live coincidence, proved on a mixed history: at a ``known_at`` after everything, every table's
    public read picks the same version as the record read (a version's public instant never decreases as its
    ``recorded_at`` increases). The LIVE path stays on the record clock regardless; this is why the two
    agree there."""
    _mixed_history(db, security_id)
    for table in (
        "fact_price_eod",
        "fact_insider_txn",
        "fact_corporate_event",
        "fact_fundamentals",
    ):
        pub = _read(db, table, security_id, date(2026, 10, 1), FAR, "public")
        rec = _read(db, table, security_id, date(2026, 10, 1), FAR, "record")
        assert pub and _strip(pub) == _strip(rec), table


def test_the_basket_read_equals_the_per_security_read_on_the_public_clock(db, security_id):
    _mixed_history(db, security_id)
    for asof in (date(2026, 6, 1), date(2026, 6, 3), date(2026, 6, 5), date(2026, 9, 2)):
        for table in (t for t in _FACT_IDENTITY if t != "fact_theme_conviction"):
            for lower in (None, asof - timedelta(days=10)):
                many = as_of_many(
                    db,
                    table,
                    security_ids=[security_id],
                    asof=asof,
                    known_at=_eod(asof),
                    tenant_id=T,
                    valid_from_lower=lower,
                    clock="public",
                )[security_id]
                one = _read(db, table, security_id, asof, _eod(asof), "public")
                if lower is not None:
                    one = [r for r in one if r["valid_from"] >= lower]
                assert many == one, (table, asof, lower)


def test_first_knowability_reads_the_public_clock(db, security_id):
    """D3a on the public clock: a re-reported, UNCHANGED number is still not new knowledge — the knowable
    date is the earliest FILING of the trailing value-identical run (90 first filed 06-02, re-reported
    06-03), and a restatement counts from its OWN filing (before 06-02 the read is the original 100).
    """
    _mixed_history(db, security_id)
    (early,) = _read(
        db, "fact_fundamentals", security_id, date(2026, 6, 1), _eod(date(2026, 6, 1)), "public"
    )
    assert float(early["value"]) == 100.0 and early["knowable_from"] == date(2026, 5, 1)
    (late,) = _read(
        db, "fact_fundamentals", security_id, date(2026, 6, 5), _eod(date(2026, 6, 5)), "public"
    )
    assert float(late["value"]) == 90.0
    assert (
        late["knowable_from"] == date(2026, 6, 2) and late["knowable_accession"] == "f-2026-06-02"
    )


def test_the_pit_threads_the_clock_to_every_read(db, security_id):
    """``PointInTimeData(clock="public")`` — the view the history routes and the backfill build — reads every
    table on the public clock, prefetched or per-security alike."""
    _mixed_history(db, security_id)
    asof = date(2026, 6, 5)
    for basket in (None, [security_id]):
        pit = PointInTimeData(db, asof=asof, known_at=_eod(asof), basket=basket, clock="public")
        # 06-01: the 10.0 first version (the 10.5 correction is public only from 06-09); 06-02: ingested
        # 09-01 but settled 06-02 — public, so visible
        assert [float(r["close"]) for r in pit.price_history(security_id)] == [10.0, 11.0]
        # mx-1's best parse (public at its acceptance); mx-2 has no acceptance and was first held 06-06 00:00
        # UTC = 06-05 20:00 ET — inside the 06-05 market day, so the night held it
        assert [r["insider_role"] for r in pit.insider_txns(security_id)] == ["B", "CEO"]
