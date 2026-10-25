from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from db.bitemporal import _FACT_IDENTITY, append_fact
from db.public_clock import PUBLIC_TIME
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof, market_tz
from replay import export as export_mod
from replay.export import (
    _PUBLIC_CLOCK,
    export_snapshot,
)
from replay.pit import ReplayPointInTimeData, connect_mirror
from replay.scoring import RealizedPrices
from signals.base import PointInTimeData

# B2 — the PUBLIC clock. The backtest asks "what would the algorithm have called on the information
# available?", and the system clock cannot answer it: the filing tables' recorded_at begins 2026-06, so a
# 2025 backrun either sees nothing or (under a future pin) sees every Form 4 on its TRANSACTION date, months
# before it was public. Since 2026-10-21 the product's history views ask the same question (INVARIANTS §4),
# so the definition is ONE (``db.public_clock``) and the mirror is row-for-row the live public read
# (``test_the_public_mirror_is_row_for_row_the_live_public_read``).
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


def test_a_re_dated_acceptance_is_the_facts_clock_on_every_version(db, security_id, tmp_path):
    """The NULL-fill NAMES its winner (2026-10-24): the newest stored statement of the acceptance, on every
    version. The shape the acceptance-clock repair writes — a NULL pre-capture version, the backfilled time
    (a night late: SEC's list drifted), then the re-dated one — used to raise (two different non-NULL times
    beside a NULL); now the export keeps ONE row, dated at the re-dated time, and the mirror sees the fact on
    that night and not before."""
    right = datetime(2026, 6, 3, 23, 0, 20, tzinfo=timezone.utc)  # 19:00:20 EDT 06-03 (the header)
    late = datetime(
        2026, 6, 4, 4, 0, 20, tzinfo=timezone.utc
    )  # 00:00:20 EDT 06-04 (the drifted list)
    for rec, acc in [
        (datetime(2026, 6, 4, tzinfo=timezone.utc), None),
        (datetime(2026, 6, 5, tzinfo=timezone.utc), late),
        (datetime(2026, 10, 24, tzinfo=timezone.utc), right),
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
    m = export_snapshot(db, tmp_path, clock="public")
    counts = m["tables"]["fact_insider_txn"]
    assert (counts["rows_out"], counts["identities_lost"]) == (1, 0)
    con = connect_mirror(tmp_path)
    try:

        def seen(d: date) -> list:
            return ReplayPointInTimeData(
                con, asof=d, known_at=known_at_for_asof(d, now=_PIN)
            ).insider_txns(security_id)

        assert seen(date(2026, 6, 2)) == []
        (row,) = seen(date(2026, 6, 3))  # the filing's own night (it was a night late before)
        assert row["recorded_at"] == right
        (later,) = seen(date(2026, 6, 10))
        assert later["recorded_at"] == right  # every version carries the fact's one clock
    finally:
        con.close()


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


def test_every_fact_table_has_a_clock_so_nothing_is_excluded(db, tmp_path):
    """Since the 2026-10-21 rule every fact table carries a public clock (``db.public_clock`` — the operator-
    ratified Workbench tables and the fund samples at their ratification / sampling instant), so a public mirror
    excludes nothing and blinds nothing."""
    m = export_snapshot(db, tmp_path, clock="public")
    assert m["excluded_tables"] == [] and m["blind_detectors"] == []
    assert set(m["tables"]) == set(_FACT_IDENTITY)


def test_a_table_without_a_clock_is_excluded_whole_and_named(db, tmp_path, monkeypatch):
    """The exclusion mechanism, for the next table someone adds without a public clock: excluded WHOLE and
    named — and so are the detectors it blinds (a table left out is only half an answer; without naming what
    stopped firing, a result reads as "that detector is inert on this tape" when the truth is "that detector
    had no tape")."""
    missing = {"fact_revenue_mix", "fact_activist_stake"}
    monkeypatch.setattr(
        export_mod, "PUBLIC_TIME", {t: v for t, v in PUBLIC_TIME.items() if t not in missing}
    )
    m = export_snapshot(db, tmp_path, clock="public")
    assert set(m["excluded_tables"]) == missing
    assert m["blind_detectors"] == ["activist_stake"]  # the one detector that reads only that tape
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
    """A DATE clock (an 8-K's ``filed``) becomes an instant in MARKET time, not UTC — this repo's rule that a
    trading day is a domain fact. Inert on visibility (``valid_from <= asof`` already decides these rows),
    pinned so the next reader knows it was a decision."""
    d = date(2026, 6, 1)
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "form": "8-K",
            "items": ["8.01"],
            "accession": "8k-1",
            "filed": d,
            "source_ref": "https://www.sec.gov/x",
            "valid_from": d,
            "recorded_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        },
    )
    db.commit()
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    try:
        (row,) = ReplayPointInTimeData(con, asof=d, known_at=_PIN).corporate_event_facts(
            security_id
        )
        assert row["recorded_at"] == datetime(2026, 6, 1, tzinfo=market_tz())  # 00:00 ET
        assert row["ingested_at"] == datetime(2026, 9, 1, tzinfo=timezone.utc)  # the honest ingest
    finally:
        con.close()


def test_a_bar_lands_at_its_settle_and_a_correction_at_its_own_publication(
    db, security_id, tmp_path
):
    """A bar's FIRST version is public at its session's settle (``d`` 18:00 ET — when a close is final),
    however late we ingested it; a vendor RE-version carries no publication time of its own, so it counts
    from its own ``recorded_at`` — never back-dated to the bar's day. The scoring reader (not as-of capped)
    still reads the corrected close."""
    d = date(2026, 6, 1)
    append_fact(
        db,
        "fact_price_eod",
        _bar(security_id, d=d, close=100.0, recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc)),
    )
    fix = datetime(2026, 9, 10, 2, 30, tzinfo=timezone.utc)
    append_fact(db, "fact_price_eod", _bar(security_id, d=d, close=105.0, recorded_at=fix))
    db.commit()
    m = export_snapshot(db, tmp_path, clock="public")
    assert m["tables"]["fact_price_eod"]["versions_collapsed"] == 0  # both publications survive
    con = connect_mirror(tmp_path)
    try:

        def close_at(asof):
            rows = ReplayPointInTimeData(
                con, asof=asof, known_at=known_at_for_asof(asof, _PIN)
            ).price_history(security_id)
            return [(r["close"], r["recorded_at"]) for r in rows]

        settle = datetime(2026, 6, 1, 18, 0, tzinfo=market_tz())
        assert close_at(d) == [(100.0, settle)]
        assert close_at(date(2026, 9, 8)) == [(100.0, settle)]  # the fix is not public yet
        assert close_at(date(2026, 9, 9)) == [(105.0, fix)]  # 09-09 22:30 ET: public that night
        assert RealizedPrices(con).closes_between(security_id, d, d) == [(d, 105.0)]
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


# --- ONE definition: the public mirror is the live public read, row for row ---------------------------------


def _seed_public_parity(db) -> list:
    """Real seed theses + every public-clock shape: Form 4s with acceptances (one ingested a year late, one
    re-parsed with the same acceptance), a corrected bar, a restated + re-reported fundamentals quarter, an 8-K
    resolved later, a 13D resolved later — reusing the record-clock parity gate's own seeders."""
    from pipeline.seed import (
        HIMS_SECURITY_ID,
        LEU_ID,
        NNE_ID,
        OKLO_ID,
        SMR_ID,
        UNH_SECURITY_ID,
        seed_hims,
        seed_leu_catalyst,
        seed_nuclear,
        seed_nuclear_catalyst,
        seed_nuclear_theme_conviction,
        seed_unh,
    )
    from tests.replay.test_pit_parity import (
        _seed_activist_stakes,
        _seed_corporate_events,
        _seed_fundamentals,
    )

    seed_hims(db)
    seed_unh(db)
    seed_nuclear(db)
    seed_nuclear_catalyst(db)
    seed_leu_catalyst(db)
    seed_nuclear_theme_conviction(db)
    _seed_fundamentals(db)
    _seed_corporate_events(db)
    _seed_activist_stakes(db)
    sid = HIMS_SECURITY_ID
    late = datetime(2026, 7, 1, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            sid,
            accession="pp-late",
            valid_from=date(2025, 8, 1),
            usd=1_000_000,
            recorded_at=late,  # ingested ~11 months after it was public
            accepted=datetime(2025, 8, 4, 18, 30, tzinfo=timezone.utc),
        ),
    )
    for rec, role in [(datetime(2025, 8, 6, tzinfo=timezone.utc), "OLD"), (late, "REPARSED")]:
        append_fact(
            db,
            "fact_insider_txn",
            _insider(
                sid,
                accession="pp-reparse",
                valid_from=date(2025, 8, 5),
                usd=2_000_000,
                recorded_at=rec,
                accepted=datetime(2025, 8, 5, 20, 0, tzinfo=timezone.utc),
                role=role,
            ),
        )
    d = date(2025, 8, 1)
    with db.cursor() as cur:
        cur.execute("SELECT close FROM fact_price_eod WHERE security_id = %s AND d = %s", (sid, d))
        row = cur.fetchone()
    if row is not None:  # a vendor correction of a real seed bar, published a month later
        fixed = _bar(
            sid,
            d=d,
            close=float(row["close"]) + 1.0,
            recorded_at=datetime(2025, 9, 2, tzinfo=timezone.utc),
        )
        append_fact(db, "fact_price_eod", fixed)
    db.commit()
    return [HIMS_SECURITY_ID, UNH_SECURITY_ID, SMR_ID, OKLO_ID, NNE_ID, LEU_ID]


@pytest.mark.parametrize("basket", [True, False], ids=["prefetch", "scoped"])
def test_the_public_mirror_is_row_for_row_the_live_public_read(db, tmp_path, basket):
    """THE PARITY GATE for the public clock: across every accessor, every seeded scope and a sweep of as-ofs
    at the lockstep cap (the end of each market day — exactly what the history views and a public backtest
    read), ``PointInTimeData(clock="public")`` over Postgres equals ``ReplayPointInTimeData`` over a
    ``clock="public"`` mirror, row for row — including ``recorded_at`` (the public instant) and
    ``ingested_at`` (the honest one). The two engines run ONE definition (``db.public_clock``)."""
    from pipeline.seed import HIMS_THESIS_ID, NUCLEAR_THESIS_ID, UNH_THESIS_ID
    from tests.replay.test_pit_parity import _match

    secs = _seed_public_parity(db)
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    accessors = (
        ("insider_txns", "fact_insider_txn"),
        ("price_history", "fact_price_eod"),
        ("dilution_facts", "fact_dilution"),
        ("catalyst_facts", "fact_catalyst"),
        ("fundamentals_facts", "fact_fundamentals"),
        ("corporate_event_facts", "fact_corporate_event"),
        ("activist_stake_facts", "fact_activist_stake"),
    )
    checked = 0
    try:
        for asof in (
            date(2025, 8, 4),
            date(2025, 8, 15),
            date(2025, 9, 3),
            date(2026, 4, 1),
            date(2026, 6, 5),
        ):
            k = known_at_for_asof(asof, _PIN)
            live = PointInTimeData(
                db, asof=asof, known_at=k, basket=secs if basket else None, clock="public"
            )
            rep = ReplayPointInTimeData(con, asof=asof, known_at=k, basket=secs if basket else None)
            for sid in secs:
                for accessor, table in accessors:
                    lr, rr = getattr(live, accessor)(sid), getattr(rep, accessor)(sid)
                    _match(lr, rr, _FACT_IDENTITY[table])
                    assert len(lr) == len(rr)
                    checked += len(lr)
            for tid in (HIMS_THESIS_ID, UNH_THESIS_ID, NUCLEAR_THESIS_ID):
                _match(
                    live.theme_conviction_facts(tid),
                    rep.theme_conviction_facts(tid),
                    _FACT_IDENTITY["fact_theme_conviction"],
                )
    finally:
        con.close()
    assert checked > 1000  # not vacuous: the seeded tapes really were compared


def test_an_undatable_form4_is_shown_by_the_history_read_and_counted_out_by_the_export(
    db, security_id, tmp_path
):
    """The ONE named asymmetry between the two consumers of the shared definition: a Form 4 with no
    acceptance on any version. The history read counts it from the first time we held it (an upper bound on
    its publication — never early, never dropped: #9); the backtest export keeps its contract of excluding
    and COUNTING it (``rows_dropped_null_clock``)."""
    first = datetime(2026, 6, 2, 2, 30, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="undatable",
            valid_from=date(2026, 6, 1),
            usd=1,
            recorded_at=first,
        ),
    )
    db.commit()
    asof = date(2026, 6, 1)
    (row,) = PointInTimeData(
        db, asof=asof, known_at=known_at_for_asof(asof, _PIN), clock="public"
    ).insider_txns(security_id)
    assert row["recorded_at"] == first
    m = export_snapshot(db, tmp_path, clock="public")
    assert m["tables"]["fact_insider_txn"]["rows_dropped_null_clock"] == 1
