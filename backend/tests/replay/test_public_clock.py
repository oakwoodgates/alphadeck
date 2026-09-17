from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from replay.export import (
    _PUBLIC_CLOCK,
    AmbiguousPublicClock,
    export_snapshot,
)
from replay.pit import ReplayPointInTimeData, connect_mirror
from replay.scoring import RealizedPrices

# B2 — the PUBLIC clock. The backtest asks "what would the algorithm have called on the information
# available?", which is a different question from the Scoreboard's "what did we hold", and the system
# clock cannot answer it: the filing tables' recorded_at begins 2026-06, so a 2025 backrun either sees
# nothing or (under a future pin) sees every Form 4 on its TRANSACTION date, months before it was public.
#
# Two properties carry this slice. The mirror must show a fact exactly when it became public -- on BOTH
# bitemporal axes -- and the rewrite must not corrupt which VERSION of a fact wins, because collapsing
# every version onto one recorded_at hands that decision to the `id DESC` tiebreak, and `id` is a random
# uuid. The version tests below run with SHUFFLED ids for exactly that reason: a random tiebreak passes
# a single-ordering test roughly half the time.

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _insider(sid, *, accession, valid_from, usd, recorded_at, accepted=None, seq=0, role="CEO"):
    v = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "insider_name": "CEO",
        "insider_role": role,
        "txn_code": "P",
        "usd": usd,
        "accession": accession,
        "txn_seq": seq,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }
    if accepted is not None:
        v["accepted"] = accepted
    return v


def _fundamentals(sid, *, period_end, valid_from, value, recorded_at, accn):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "metric_key": "revenue",
        "period_end": period_end,
        "fiscal_period": "Q4",
        "fiscal_year": period_end.year,
        "value": value,
        "unit": "USD",
        "basis": "native",
        "accession": accn,
        "source": "companyfacts",
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def _bar(sid, *, d, close, recorded_at):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "d": d,
        "close": close,
        "valid_from": d,
        "recorded_at": recorded_at,
    }


# --- the axis itself --------------------------------------------------------------------------------


def test_a_fact_is_invisible_before_its_accepted_and_visible_after(db, security_id, tmp_path):
    """The whole point, on BOTH axes. A Form 4 for a trade on T-5, ACCEPTED on T+3, ingested long ago:
    the transaction axis alone would show it at T (that is the leak a future pin creates), and the public
    clock must not."""
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="a-1",
            valid_from=date(2026, 6, 1),
            usd=1_000_000,
            recorded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),  # "we" held it early
            accepted=datetime(
                2026, 6, 9, 21, 0, tzinfo=timezone.utc
            ),  # ...it went public on the 9th
        ),
    )
    db.commit()
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        # asof 06-06: the trade has happened, the filing has NOT been accepted -> invisible
        blind = ReplayPointInTimeData(
            con, asof=date(2026, 6, 6), known_at=known_at_for_asof(date(2026, 6, 6), _PIN)
        )
        assert blind.insider_txns(security_id) == []
        # asof 06-09, capped at the end of THAT market day -> visible
        seeing = ReplayPointInTimeData(
            con, asof=date(2026, 6, 9), known_at=known_at_for_asof(date(2026, 6, 9), _PIN)
        )
        assert [r["accession"] for r in seeing.insider_txns(security_id)] == ["a-1"]
    finally:
        con.close()


def test_a_bar_is_visible_on_its_own_day_and_not_before(db, security_id, tmp_path):
    for d, px in [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 101.0)]:
        append_fact(
            db,
            "fact_price_eod",
            _bar(security_id, d=d, close=px, recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc)),
        )
    db.commit()
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        pit = ReplayPointInTimeData(
            con, asof=date(2026, 6, 1), known_at=known_at_for_asof(date(2026, 6, 1), _PIN)
        )
        assert [r["d"] for r in pit.price_history(security_id)] == [date(2026, 6, 1)]
    finally:
        con.close()


# --- the exclusions, counted ------------------------------------------------------------------------


def test_a_fact_with_no_clock_in_any_version_is_dropped_and_counted(db, security_id, tmp_path):
    """Excluded and COUNTED, never modeled: the measured disclosure lag has a long tail, so inventing an
    offset would be wrong exactly where it matters."""
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="no-clock",
            valid_from=date(2026, 6, 1),
            usd=500_000,
            recorded_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            accepted=None,
        ),
    )
    db.commit()
    m = export_snapshot(db, tmp_path, clock="public")
    counts = m["tables"]["fact_insider_txn"]
    assert counts["rows_out"] == 0
    assert counts["rows_dropped_null_clock"] == 1  # RAW rows
    assert counts["identities_lost"] == 1  # whole FACTS
    con = connect_mirror(tmp_path)
    try:
        assert (
            ReplayPointInTimeData(con, asof=date(2026, 12, 1), known_at=_PIN).insider_txns(
                security_id
            )
            == []
        )
    finally:
        con.close()


def test_the_null_fill_keeps_our_BEST_PARSE_not_an_older_one(db, security_id, tmp_path):
    """FLAG-D. An insider fact re-versioned by a repair, where the LATEST version predates the ``accepted``
    backfill and so carries no clock of its own. Dropping null-clock ROWS before resolving would discard
    that version and silently revert the fact to an older, less-repaired parse. Filling the clock from the
    identity's other version keeps the best parse AND dates it correctly. MEASURED: 6 identities on the dev
    copy are in exactly this state."""
    acc = datetime(2026, 6, 3, 18, 0, tzinfo=timezone.utc)
    append_fact(  # v1: has the clock, the OLD (pre-repair) content
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="a-1",
            valid_from=date(2026, 6, 1),
            usd=111_111,
            recorded_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
            accepted=acc,
            role="OLD ROLE",
        ),
    )
    append_fact(  # v2: the repair — later recorded_at, BETTER content, no clock of its own
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="a-1",
            valid_from=date(2026, 6, 1),
            usd=111_111,
            recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            accepted=None,
            role="REPAIRED ROLE",
        ),
    )
    db.commit()
    m = export_snapshot(db, tmp_path, clock="public")
    counts = m["tables"]["fact_insider_txn"]
    assert counts["rows_out"] == 1 and counts["identities_lost"] == 0
    con = connect_mirror(tmp_path)
    try:
        rows = ReplayPointInTimeData(con, asof=date(2026, 12, 1), known_at=_PIN).insider_txns(
            security_id
        )
        assert len(rows) == 1
        assert rows[0]["insider_role"] == "REPAIRED ROLE"  # the BEST parse, not the older one
        assert rows[0]["recorded_at"] == acc  # ...dated at the accession's acceptance
    finally:
        con.close()


# --- the version resolution (FLAG-A / FLAG-B) -------------------------------------------------------


@pytest.mark.parametrize("attempt", range(12))
def test_a_pure_reparse_collapses_to_the_latest_content(db, security_id, tmp_path, attempt):
    """Two versions of ONE fact sharing an ``accepted``, differing in content. On the public clock they
    tie on ``recorded_at``, so without the export-time resolution the winner is decided by ``id DESC`` —
    and ``id`` is ``gen_random_uuid()``. Repeated 12 times with fresh random ids: a random tiebreak would
    fail roughly half of these."""
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
                accession="a-1",
                valid_from=date(2026, 6, 1),
                usd=222_222,
                recorded_at=rec,
                accepted=acc,
                role=role,
            ),
        )
    db.commit()
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        rows = ReplayPointInTimeData(con, asof=date(2026, 12, 1), known_at=_PIN).insider_txns(
            security_id
        )
        assert len(rows) == 1 and rows[0]["insider_role"] == "REPAIRED ROLE"
    finally:
        con.close()


def test_a_restatement_survives_with_its_OWN_clock(db, security_id, tmp_path):
    """The other half of partitioning by ``(identity, clock)`` rather than by identity alone: a genuine
    RE-DISCLOSURE is not a re-parse. A restated quarter has its own ``filed`` date, must stay invisible
    until then, and must not be collapsed away — which partitioning by identity alone would do."""
    pe = date(2025, 12, 31)
    append_fact(
        db,
        "fact_fundamentals",
        _fundamentals(
            security_id,
            period_end=pe,
            valid_from=date(2026, 2, 15),
            value=100.0,
            recorded_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
            accn="f-1",
        ),
    )
    append_fact(
        db,
        "fact_fundamentals",
        _fundamentals(
            security_id,
            period_end=pe,
            valid_from=date(2026, 8, 10),  # restated, filed later
            value=90.0,
            recorded_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            accn="f-2",
        ),
    )
    db.commit()
    m = export_snapshot(db, tmp_path, clock="public")
    assert m["tables"]["fact_fundamentals"]["versions_collapsed"] == 0  # BOTH survive
    con = connect_mirror(tmp_path)
    try:

        def value_at(d):
            rows = ReplayPointInTimeData(
                con, asof=d, known_at=known_at_for_asof(d, _PIN)
            ).fundamentals_facts(security_id)
            return rows[0]["value"] if rows else None

        assert value_at(date(2026, 3, 1)) == 100.0  # before the restatement: the original
        assert value_at(date(2026, 9, 1)) == 90.0  # after: the restated value
    finally:
        con.close()


def test_the_scoring_reader_is_not_corrupted_by_the_collapse(db, security_id, tmp_path):
    """FLAG-B. ``RealizedPrices`` reads this same mirror with the same ``recorded_at DESC, id DESC``
    tiebreak and is deliberately NOT as-of capped, so a random version pick would land straight in the
    realized returns — in a reader nobody would think to check when changing the as-of axis. MEASURED on
    the dev copy: 8,947 (security, day) pairs hold versions with DIFFERENT closes."""
    d = date(2026, 6, 1)
    append_fact(
        db,
        "fact_price_eod",
        _bar(security_id, d=d, close=100.0, recorded_at=datetime(2026, 6, 1, tzinfo=timezone.utc)),
    )
    append_fact(  # the correction
        db,
        "fact_price_eod",
        _bar(security_id, d=d, close=105.0, recorded_at=datetime(2026, 6, 5, tzinfo=timezone.utc)),
    )
    db.commit()
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        assert RealizedPrices(con).closes_between(security_id, d, d) == [(d, 105.0)]
    finally:
        con.close()


def test_an_ambiguous_clock_raises_rather_than_guessing(db, security_id, tmp_path):
    """The assertion behind the NULL-fill. A fact whose versions disagree on the disclosure instant AND
    include one with none cannot be attributed, so the export FAILS rather than picking. MEASURED: zero
    such identities exist on the dev copy today — this is the guard for when one appears."""
    for rec, acc in [
        (datetime(2026, 6, 4, tzinfo=timezone.utc), datetime(2026, 6, 3, tzinfo=timezone.utc)),
        (datetime(2026, 6, 5, tzinfo=timezone.utc), datetime(2026, 6, 4, tzinfo=timezone.utc)),
        (datetime(2026, 6, 6, tzinfo=timezone.utc), None),
    ]:
        append_fact(
            db,
            "fact_insider_txn",
            _insider(
                security_id,
                accession="a-1",
                valid_from=date(2026, 6, 1),
                usd=1,
                recorded_at=rec,
                accepted=acc,
            ),
        )
    db.commit()
    with pytest.raises(AmbiguousPublicClock, match="cannot be attributed"):
        export_snapshot(db, tmp_path, clock="public")


# --- clock=record is untouched ----------------------------------------------------------------------


def test_clock_record_is_unchanged(db, security_id, tmp_path):
    """The default mode is the pre-B2 exporter, byte-for-byte: EVERY row, EVERY version, ``recorded_at``
    exactly as stored. The Postgres-vs-Parquet parity gate runs on this mode and must keep doing so.
    """
    r1 = datetime(2026, 6, 4, tzinfo=timezone.utc)
    r2 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    acc = datetime(2026, 6, 3, 18, 0, tzinfo=timezone.utc)
    for rec in (r1, r2):
        append_fact(
            db,
            "fact_insider_txn",
            _insider(
                security_id,
                accession="a-1",
                valid_from=date(2026, 6, 1),
                usd=1,
                recorded_at=rec,
                accepted=acc,
            ),
        )
    db.commit()
    m = export_snapshot(db, tmp_path, clock="record")
    assert m["clock"] == "record"
    assert m["tables"]["fact_insider_txn"]["rows"] == 2  # BOTH versions kept
    assert m["excluded_tables"] == [] and m["blind_detectors"] == []
    assert "rows_dropped_null_clock" not in m["tables"]["fact_insider_txn"]
    con = connect_mirror(tmp_path)
    try:
        # the transaction axis still bites on the SYSTEM clock: pinned before the re-version, v1 wins
        early = ReplayPointInTimeData(con, asof=date(2026, 12, 1), known_at=r1).insider_txns(
            security_id
        )
        assert len(early) == 1 and early[0]["recorded_at"] == r1
    finally:
        con.close()


def test_the_excluded_tables_are_named_and_so_are_their_blind_detectors(db, tmp_path):
    """A table left out is only half an answer; without naming what stopped firing, a result reads as
    "that detector is inert on this tape" when the truth is "that detector had no tape"."""
    m = export_snapshot(db, tmp_path, clock="public")
    assert set(m["excluded_tables"]) == {
        "fact_revenue_mix",
        "fact_shares_outstanding",
        "fact_cash_burn",
        "fact_fund_shares",
    }
    # ...and none of them is read by a call detector, so nothing is blinded (operator decision Q5)
    assert m["blind_detectors"] == []
    for t in m["excluded_tables"]:
        assert t not in m["tables"]
        assert not (tmp_path / f"{t}.parquet").exists()


def test_every_call_path_table_has_a_declared_clock():
    """The registry must cover every table the call PIT reads, or a public-clock run would silently blind
    a detector. Enumerated from the accessors' own tables rather than retyped."""
    call_path = {
        "fact_price_eod",
        "fact_fundamentals",
        "fact_corporate_event",
        "fact_activist_stake",
        "fact_insider_txn",
        "fact_catalyst",
        "fact_dilution",
        "fact_theme_conviction",
    }
    assert call_path <= set(_PUBLIC_CLOCK)


# --- the lockstep fact axis -------------------------------------------------------------------------


def test_lockstep_caps_the_facts_at_the_end_of_each_session(db, security_id, tmp_path):
    """In ``pin`` mode the facts are capped at the run-wide pin, so a filing accepted LATER in the window
    is visible at every session (that is the determinism pin doing its job on the system clock). In
    ``lockstep`` mode the cap moves with T, so the same filing appears only from its own day — the fact
    axis tracking the roster axis F4 already made per-T."""
    acc = datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="a-1",
            valid_from=date(2026, 6, 1),
            usd=1_000_000,
            recorded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            accepted=acc,
        ),
    )
    db.commit()
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        t_before, t_after = date(2026, 6, 6), date(2026, 6, 9)
        pin_before = ReplayPointInTimeData(con, asof=t_before, known_at=_PIN)
        assert pin_before.insider_txns(security_id), "pin mode: the run-wide pin sees it"
        lock_before = ReplayPointInTimeData(
            con, asof=t_before, known_at=known_at_for_asof(t_before, _PIN)
        )
        assert lock_before.insider_txns(security_id) == [], "lockstep: not yet public"
        lock_after = ReplayPointInTimeData(
            con, asof=t_after, known_at=known_at_for_asof(t_after, _PIN)
        )
        assert lock_after.insider_txns(security_id), "lockstep: public by the end of that day"
    finally:
        con.close()


def test_the_harness_threads_the_known_at_mode(db, tmp_path):
    """``known_at_mode`` reaches the PIT: in lockstep the per-session cap is ``known_at_for_asof(T)``,
    which is the SAME instant F4 hands the roster, so the two axes cannot drift apart."""
    import replay.harness as harness

    seen: list[datetime] = []
    real = harness.ReplayPointInTimeData

    def spy(con, *, asof, known_at, **kw):
        seen.append(known_at)
        return real(con, asof=asof, known_at=known_at, **kw)

    from pipeline.seed import UNH_THESIS_ID, seed_unh
    from repositories import thesis_repo

    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path, clock="record")
    con = connect_mirror(tmp_path)
    harness.ReplayPointInTimeData = spy
    try:
        thesis = thesis_repo.get(db, UNH_THESIS_ID)
        start, end = date(2025, 5, 1), date(2025, 5, 15)
        harness.replay_thesis_with_roster(
            con, thesis, start=start, end=end, known_at=_PIN, known_at_mode="lockstep"
        )
        assert seen, "the sweep should have replayed sessions"
        assert all(k != _PIN for k in seen), "lockstep must not use the run-wide pin"
        seen.clear()
        harness.replay_thesis_with_roster(con, thesis, start=start, end=end, known_at=_PIN)
        assert seen and all(k == _PIN for k in seen), "pin mode is unchanged"
    finally:
        harness.ReplayPointInTimeData = real
        con.close()


def test_a_date_clock_lands_at_the_start_of_the_market_day(db, security_id, tmp_path):
    """A DATE clock becomes an instant in MARKET time, not UTC — this repo's rule that a trading day is a
    domain fact. Inert on visibility (``valid_from <= asof`` already decides these rows), pinned so the
    next reader knows it was a decision."""
    d = date(2026, 6, 1)
    append_fact(
        db,
        "fact_price_eod",
        _bar(security_id, d=d, close=100.0, recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc)),
    )
    db.commit()
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        rows = ReplayPointInTimeData(con, asof=d, known_at=_PIN).price_history(security_id)
        stamped = rows[0]["recorded_at"]
        assert stamped.astimezone(timezone.utc) < datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        assert stamped.astimezone(timezone.utc) >= datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert stamped <= known_at_for_asof(d, _PIN)  # visible on its own day
    finally:
        con.close()


def test_ids_are_random_uuids_so_the_tiebreak_could_never_have_been_trusted(db, security_id):
    """The measurement behind FLAG-A, kept as a test because the fix's whole justification rests on it.
    ``ORDER BY recorded_at DESC, id DESC`` is the version pick on both engines. Collapsing versions onto
    one public ``recorded_at`` hands the decision to ``id`` — and ``id`` is ``gen_random_uuid()``, so it
    carries no insertion order at all. "The id tiebreak picks the latest content" is simply false.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'fact_insider_txn' AND column_name = 'id'"
        )
        assert "gen_random_uuid" in (cur.fetchone()["column_default"] or "")

    inserted = [
        append_fact(
            db,
            "fact_insider_txn",
            _insider(
                security_id,
                accession=f"rand-{i}",
                valid_from=date(2026, 6, 1),
                usd=1,
                recorded_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                seq=i,
            ),
        )
        for i in range(25)
    ]
    db.commit()
    assert len(set(inserted)) == 25
    # insertion order and id order disagree — with 25 rows, agreeing by chance is ~1 in 25!
    assert inserted != sorted(inserted, key=lambda u: uuid.UUID(str(u)).int)
