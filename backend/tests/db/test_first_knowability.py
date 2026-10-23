"""D3a — FIRST KNOWABILITY on the fundamentals as-of read, at FIXED transaction times, on REAL filing shapes.

The bug: every 10-Q re-reports the year-ago quarter as its comparative, the ingest versions it at the later
filing, and the latest-version pick handed the revenue detector that later date — so a year-old inflection
re-fired as fresh for another 180 days (tonight's CEG arm; GEV's 2026-10-01 citation). The rule
(operator-approved 2026-10-16): a fact dates at the EARLIEST knowable date of the trailing run of
value-identical versions ending at the picked one, counting only versions the read can see.

Every fixture is a real prod version history (``tests/fundamentals_fixtures.py``) stored exactly as prod holds
it (the ingest's own row values under the pre-0057 per-filing stamp — the honest-stamp twin is
``tests/ingest/test_fundamentals_honest_stamp.py``). The few pure contract tests at the bottom state
``version_knowable_date`` / ``first_knowable_versions`` with direct inputs; they fabricate no outcome.
DB-backed (skips without Postgres).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from db.bitemporal import (
    _FACT_IDENTITY,
    KNOWABLE_FROM,
    KNOWABLE_SOURCE,
    as_of,
    as_of_many,
    first_knowable_versions,
    version_knowable_date,
)
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.market_time import known_at_for_asof
from signals import revenue_acceleration as ra
from signals.base import PointInTimeData
from signals.common import entry_signal_is_live
from tests.fundamentals_fixtures import (
    CEG,
    FLR_2017Q1,
    GEV,
    KULR,
    LRHC,
    Version,
    first_filed,
    legacy_stamp,
    store_versions,
)

# Rule (c) (operator sign-off 2026-10-22) also keeps a year-old quarter from being the cited one; these D3a
# proofs hold it OFF (``None`` = no cap, the rule before it) where they isolate D3a's own effect, and say what
# rule (c) adds beside them.
_PRE_RULE_C = DEFAULT_CONFIG.model_copy(update={"revenue_accel_max_quarter_age_days": None})


def _security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, f"{sid.int % 10**10:010d}", "2020-01-01"),
        )
    db.commit()
    return sid


def _revenue(db, sid, asof: date, known_at: datetime) -> dict[date, dict]:
    rows = PointInTimeData(db, asof=asof, known_at=known_at).fundamentals_facts(sid)
    return {r["period_end"]: r for r in rows if r["metric_key"] == "revenue"}


def _stamp(filed: date) -> datetime:
    """The stamp every pre-0057 prod row carries: ``recorded_at = filed 00:00 UTC``."""
    return legacy_stamp(filed)


# --- GEV: the two-filings, one-identical-value shape ---------------------------------------------------


def test_between_the_two_filings_the_comparative_is_invisible_and_the_period_dates_at_its_first_filing(
    db,
):
    """``known_at`` between the 2025-04-23 first filing and the 2026-04-22 comparative: the comparative was
    not yet recorded, so it is invisible (even with event time far ahead), and Q1-2025 dates at 2025-04-23.
    """
    sid = _security(db, "GEV")
    store_versions(db, sid, GEV)
    known_at = known_at_for_asof(date(2025, 6, 2))
    for asof in (date(2025, 6, 2), date(2026, 10, 1)):  # lockstep, and event time AHEAD of known_at
        rows = _revenue(db, sid, asof, known_at)
        q = rows[date(2025, 3, 31)]
        assert q["accession"] == "0001996810-25-000073" and q["valid_from"] == date(2025, 4, 23)
        assert q[KNOWABLE_FROM] == date(2025, 4, 23)
        assert q[KNOWABLE_SOURCE] == "0001996810-25-000073"
        assert date(2026, 3, 31) not in rows  # Q1-2026 was filed (and recorded) after known_at
        assert all(r["recorded_at"] <= known_at for r in rows.values())


def test_after_the_second_filing_the_comparative_is_picked_but_still_dated_at_the_first(db):
    """``known_at`` after 2026-04-22: the as-of pick is UNCHANGED (the latest version — the comparative,
    accession and valid_from intact), but its knowability stays the 2025-04-23 first filing. GEV restated
    nothing, so EVERY period dates at its earliest filing."""
    sid = _security(db, "GEV")
    store_versions(db, sid, GEV)
    asof = date(2026, 10, 1)
    rows = _revenue(db, sid, asof, known_at_for_asof(asof))
    q = rows[date(2025, 3, 31)]
    assert q["accession"] == "0001996810-26-000064" and q["valid_from"] == date(2026, 4, 22)
    assert q[KNOWABLE_FROM] == date(2025, 4, 23)
    assert q[KNOWABLE_SOURCE] == "0001996810-25-000073"
    assert {pe: (r[KNOWABLE_FROM], r[KNOWABLE_SOURCE]) for pe, r in rows.items()} == first_filed(
        GEV
    )
    # ...so the year-old Q1-2025 inflection is NOT live at 2026-10-01 (it was, dated at the comparative)
    lv = DEFAULT_CONFIG.revenue_accel_alpha_liveness_days
    assert not entry_signal_is_live(q[KNOWABLE_FROM], lv, asof)
    assert entry_signal_is_live(q["valid_from"], lv, asof)  # the old anchor — the phantom


def test_gev_2026_10_01_fires_on_its_genuine_q1_2026_with_the_citation_fixed(db):
    """The 2026-10-01 card cited "Q1 ending 2025-03-31" dated 2026-04-22. GEV's Q1-2026 is a genuine
    inflection (YoY +16% after +4% in Q4-2025) first filed 2026-04-22, so the call stays — on the right
    quarter, at the right filing."""
    sid = _security(db, "GEV")
    store_versions(db, sid, GEV)
    asof = date(2026, 10, 1)
    ev = ra.detect(
        PointInTimeData(db, asof=asof, known_at=known_at_for_asof(asof)), sid, asof, DEFAULT_CONFIG
    )
    assert ev is not None
    assert ev.asof == date(2026, 4, 22)
    assert "ending 2026-03-31" in ev.label
    assert ev.provenance[0].ref == "0001996810-26-000064"
    assert ev.provenance[0].detail["period_end"] == "2026-03-31"
    assert ev.provenance[0].detail["yoy_growth"] == round(9339 / 8032 - 1, 4)


# --- CEG: tonight's phantom arm ------------------------------------------------------------------------


def test_ceg_year_old_inflection_is_not_live_after_its_comparative_refiles(db):
    """CEG's Q1-2025 re-acceleration (first filed 2025-05-06) is a real fire in its own window. At
    2026-10-15 the only thing keeping it "live" was its 2026-05-11 comparative; dated at first knowability
    it lapsed in 2025, and Q4-2025 (2026-02-24) lapsed 2026-08-23 — no revenue trigger at all."""
    sid = _security(db, "CEG")
    store_versions(db, sid, CEG)
    early = date(2025, 6, 2)
    ev = ra.detect(
        PointInTimeData(db, asof=early, known_at=known_at_for_asof(early)),
        sid,
        early,
        DEFAULT_CONFIG,
    )
    assert ev is not None and ev.asof == date(2025, 5, 6) and "ending 2025-03-31" in ev.label
    assert ev.provenance[0].ref == "0001868275-25-000044"

    night = date(2026, 10, 15)
    pit = PointInTimeData(db, asof=night, known_at=known_at_for_asof(night))
    assert ra.detect(pit, sid, night, DEFAULT_CONFIG) is None
    # the regression proof: the SAME as-of rows dated by the picked version's valid_from (the pre-D3a
    # read) re-fire the year-old quarter — the exact phantom on the 2026-10-15 record (rule (c) held off:
    # this isolates D3a)
    stale = [
        {**r, KNOWABLE_FROM: r["valid_from"], KNOWABLE_SOURCE: r["accession"]}
        for r in pit.fundamentals_facts(sid)
    ]
    phantom = ra.score(stale, sid, night, _PRE_RULE_C)
    assert phantom is not None and phantom.asof == date(2026, 5, 11)
    assert "ending 2025-03-31" in phantom.label
    # ...and rule (c) is a second, independent guard against it: Q1-2025 dated 2026-05-11 is 406 days past its
    # period end, so even the pre-D3a dating cannot make it the cited quarter
    assert ra.score(stale, sid, night, DEFAULT_CONFIG) is None


# --- genuine changes are new knowledge -----------------------------------------------------------------


def test_a_genuine_restatement_is_new_knowledge_at_its_own_filing(db):
    """KULR restated its Q1-2026 revenue ($4,846,430 -> $3,946,430) in the 2026-08-13 10-Q: a VALUE
    change, so the restated figure is knowable only from 2026-08-13 — and before that, the original stands.
    """
    sid = _security(db, "KULR")
    store_versions(db, sid, KULR)
    before = _revenue(db, sid, date(2026, 6, 1), known_at_for_asof(date(2026, 6, 1)))
    q = before[date(2026, 3, 31)]
    assert float(q["value"]) == 4_846_430 and q[KNOWABLE_FROM] == date(2026, 5, 14)
    after = _revenue(db, sid, date(2026, 8, 14), known_at_for_asof(date(2026, 8, 14)))
    q = after[date(2026, 3, 31)]
    assert float(q["value"]) == 3_946_430
    assert q[KNOWABLE_FROM] == date(2026, 8, 13)
    assert q[KNOWABLE_SOURCE] == "0001104659-26-095924"


def test_a_reverted_value_starts_a_new_run(db):
    """Fluor's Q1-2017 revenue alternates between an exact and a rounded figure across five filings. The run
    is the TRAILING stretch of identical values: an earlier appearance of the same number, interrupted by a
    different one, does not date the current value."""
    sid = _security(db, "FLR")
    store_versions(db, sid, FLR_2017Q1)
    pe = date(2017, 3, 31)
    cases = [  # (known_at's market day, current value, its run's first filing)
        (date(2018, 3, 1), 4_835_900_000, date(2018, 2, 20)),  # rounded, first appearance
        (date(2018, 6, 1), 4_835_905_000, date(2018, 5, 3)),  # exact again — NOT 2017-05-04
        (
            date(2019, 3, 1),
            4_835_900_000,
            date(2018, 8, 3),
        ),  # rounded again, run of two — NOT 2018-02-20
    ]
    for day, value, first in cases:
        q = _revenue(db, sid, day, known_at_for_asof(day))[pe]
        assert float(q["value"]) == value, day
        assert q[KNOWABLE_FROM] == first, day


# --- tie-break (d) -------------------------------------------------------------------------------------


def test_the_tie_break_cites_the_newest_period_of_a_shared_filing(db):
    """LRHC's 2026-06-04 10-K restated 2024-Q2 and 2025-Q1 and first reported 2025-Q4 — three live
    inflections sharing one knowability date. ``max`` on the date alone kept the OLDEST (2024-Q2); the
    filing's newest period is the one cited (tie-break (d), stated with rule (c) held off). Under rule (c) none
    of the three may be cited — each period ended more than 135 days before 2026-06-04 (704 d, 430 d, and a
    late filer's 155 d) — so the detector declines: the real late-filer + restated-comparative instance.
    """
    sid = _security(db, "LRHC")
    store_versions(db, sid, LRHC)
    asof = date(2026, 7, 1)
    pit = PointInTimeData(db, asof=asof, known_at=known_at_for_asof(asof))
    quarters = ra._quarters(pit.fundamentals_facts(sid))
    lv = DEFAULT_CONFIG.revenue_accel_alpha_liveness_days
    hits = [
        q
        for q in quarters
        if ra._inflection(quarters, q, DEFAULT_CONFIG) is not None
        and entry_signal_is_live(q.filed, lv, asof)
    ]
    assert [(q.period_end, q.filed) for q in hits] == [
        (date(2024, 6, 30), date(2026, 6, 4)),
        (date(2025, 3, 31), date(2026, 6, 4)),
        (date(2025, 12, 31), date(2026, 6, 4)),
    ], "the real fixture must hold the tie it claims"
    ev = ra.detect(pit, sid, asof, _PRE_RULE_C)
    assert ev is not None and "ending 2025-12-31" in ev.label
    assert ev.asof == date(2026, 6, 4) and ev.provenance[0].ref == "0001213900-26-065276"
    # rule (c): every tied quarter is past 135 days at its knowability, so none fires
    assert [(q.filed - q.period_end).days for q in hits] == [704, 430, 155]
    assert ra.detect(pit, sid, asof, DEFAULT_CONFIG) is None


# --- no version recorded after known_at influences anything --------------------------------------------


def _expected(versions: list[Version], known_at: datetime) -> dict[date, tuple[float, date, str]]:
    """An INDEPENDENT re-statement of the rule over the raw fixture: per period, only versions recorded at
    or before ``known_at``; pick the latest; walk back the identical values; date at the run's first.
    """
    by_pe: dict[date, list[Version]] = {}
    for v in sorted(versions, key=lambda v: v[2]):
        if _stamp(v[2]) <= known_at:
            by_pe.setdefault(v[0], []).append(v)
    out = {}
    for pe, vs in by_pe.items():
        i = len(vs) - 1
        while i > 0 and vs[i - 1][1] == vs[-1][1]:
            i -= 1
        out[pe] = (float(vs[-1][1]), vs[i][2], vs[i][3])
    return out


@pytest.mark.parametrize("name,versions", [("GEV", GEV), ("CEG", CEG), ("LRHC", LRHC)])
def test_nothing_recorded_after_known_at_moves_the_pick_or_the_annotation(db, name, versions):
    """Sweep ``known_at`` across the day before and the day of EVERY filing in the history (event time held
    far ahead, so only the transaction axis gates): the read's value and annotation equal an independent
    computation over ONLY the versions recorded by then, and every returned row was recorded by then.
    """
    sid = _security(db, name)
    store_versions(db, sid, versions)
    asof = date(2026, 10, 15)
    for filed in sorted({v[2] for v in versions}):
        for day in (filed - timedelta(days=1), filed):
            known_at = known_at_for_asof(day)
            rows = _revenue(db, sid, asof, known_at)
            got = {
                pe: (float(r["value"]), r[KNOWABLE_FROM], r[KNOWABLE_SOURCE])
                for pe, r in rows.items()
            }
            assert got == _expected(versions, known_at), (name, day)
            for r in rows.values():
                assert r["recorded_at"] <= known_at
                assert r[KNOWABLE_FROM] <= r["valid_from"] <= asof


# --- the batched read and the pick ---------------------------------------------------------------------


def test_as_of_many_equals_as_of_and_the_pick_is_the_distinct_on_row(db):
    """The batch read is row-for-row the scoped read (annotation included) at a between-filings pin, an
    after pin and under a floor; and stripped of the two keys every row IS the ``DISTINCT ON`` row the read
    returned before D3a — the pick did not move."""
    gev, ceg = _security(db, "GEV"), _security(db, "CEG")
    store_versions(db, gev, GEV)
    store_versions(db, ceg, CEG)
    asof = date(2026, 10, 15)
    for known_at in (known_at_for_asof(date(2025, 6, 2)), known_at_for_asof(asof)):
        batch = as_of_many(
            db,
            "fact_fundamentals",
            security_ids=[gev, ceg],
            asof=asof,
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
        )
        floored = as_of_many(
            db,
            "fact_fundamentals",
            security_ids=[gev, ceg],
            asof=asof,
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
            valid_from_lower=date(2025, 1, 1),
        )
        for sid in (gev, ceg):
            scoped = as_of(
                db,
                "fact_fundamentals",
                security_id=sid,
                asof=asof,
                known_at=known_at,
                tenant_id=DEFAULT_TENANT_ID,
            )
            assert batch[sid] == scoped
            assert floored[sid] == [r for r in scoped if r["valid_from"] >= date(2025, 1, 1)]
            with db.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ON (security_id, metric_key, period_end) * FROM fact_fundamentals "
                    "WHERE tenant_id = %s AND security_id = %s AND valid_from <= %s AND recorded_at <= %s "
                    "ORDER BY security_id, metric_key, period_end, recorded_at DESC, id DESC",
                    (DEFAULT_TENANT_ID, sid, asof, known_at),
                )
                legacy = cur.fetchall()
            assert [
                {k: v for k, v in r.items() if k not in (KNOWABLE_FROM, KNOWABLE_SOURCE)}
                for r in scoped
            ] == legacy


# --- pure contract tests (direct inputs, no DB) --------------------------------------------------------


def _v(value, valid_from, recorded_at, *, accn="a", vid=None, pe=date(2025, 3, 31)):
    return {
        "security_id": "s",
        "metric_key": "revenue",
        "period_end": pe,
        "value": value,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
        "accession": accn,
        "id": vid or str(uuid.uuid4()),
    }


def test_version_knowable_date_is_the_filing_whatever_the_stamp():
    """A version dates at its PUBLIC date (``valid_from`` = the filing), never at the instant this system
    recorded it (migration 0057). The pre-0057 backdated stamp, an honest same-evening stamp (02:30 UTC =
    22:30 ET), a late ingest after an outage and a name onboarded years after the filing all date at the
    filing — WHETHER the version is held is the gate's question, answered before this one is asked.
    """
    filed = date(2026, 4, 22)
    for recorded_at in (
        _stamp(filed),  # the pre-0057 stamp every existing prod row carries
        datetime(2026, 4, 23, 2, 30, tzinfo=timezone.utc),  # the nightly pass, the same evening
        datetime(2026, 5, 1, 3, 0, tzinfo=timezone.utc),  # held a week late (2026-04-30 23:00 ET)
        datetime(2028, 1, 5, 3, 0, tzinfo=timezone.utc),  # onboarded years later
    ):
        assert version_knowable_date(_v(1, filed, recorded_at)) == filed


def _pick(rows: list[dict]) -> list[dict]:
    """The pure function over tuple rows, the way both engines' cursors hand them over."""
    cols = list(rows[0])
    return first_knowable_versions(
        "fact_fundamentals",
        cols,
        [tuple(r[c] for c in cols) for r in rows],
        _FACT_IDENTITY["fact_fundamentals"],
    )


def test_a_late_held_first_version_still_dates_at_its_filing():
    """A first version ingested late (held from 2025-04-30, filed 2025-04-23) and its identical comparative:
    the run dates at the FILING (2025-04-23) — the number was public then, and dating it at the day this
    system held it would move the signal. (Before migration 0057 this read 2025-04-30: D3a's
    ``max(valid_from, market date of recorded_at)`` term, inert on every backdated row, would have turned
    live under the honest stamp — so 0057 retired it.) WHETHER the late version counts at all is the gate's:
    an input without it (a read pinned before it was held) dates the run at the comparative alone.
    """
    v1 = _v(8032, date(2025, 4, 23), datetime(2025, 5, 1, 3, tzinfo=timezone.utc), accn="first")
    v2 = _v(8032, date(2026, 4, 22), _stamp(date(2026, 4, 22)), accn="comparative")
    (row,) = _pick([v1, v2])
    assert row == {
        **v2,
        KNOWABLE_FROM: date(2025, 4, 23),
        KNOWABLE_SOURCE: "first",
    }  # pick untouched
    (alone,) = _pick([v2])
    assert alone == {**v2, KNOWABLE_FROM: date(2026, 4, 22), KNOWABLE_SOURCE: "comparative"}


def test_the_pick_is_the_latest_filing_not_the_latest_record():
    """An OLDER filing recorded AFTER a newer one (a concept switch surfacing an old row, an onboarding pull
    landing after the nightly) never outranks the newer filing: the pick is the latest ``valid_from`` held —
    what the pre-0057 stamp (a function of the filing) always picked. The run walk follows the same order.
    """
    newer = _v(9339, date(2026, 4, 22), _stamp(date(2026, 4, 22)), accn="newer")
    older = _v(
        9000, date(2025, 4, 23), datetime(2026, 10, 21, 3, tzinfo=timezone.utc), accn="older"
    )
    for order in ([newer, older], [older, newer]):
        (row,) = _pick(order)
        assert row == {**newer, KNOWABLE_FROM: date(2026, 4, 22), KNOWABLE_SOURCE: "newer"}


@pytest.mark.parametrize(
    "versions", [GEV, CEG, LRHC, KULR, FLR_2017Q1], ids=["GEV", "CEG", "LRHC", "KULR", "FLR"]
)
def test_one_shared_stamp_picks_and_dates_exactly_like_the_backdated_stamps(versions):
    """A name's onboarding pull writes its whole history in ONE transaction — one honest stamp on every
    version. Over each real history (with the ids DESCENDING by filing, so an order that fell through to the
    uuid would pick the OLDEST version) the read returns the SAME pick and the SAME annotation as over the
    per-filing pre-0057 stamps; only ``recorded_at`` differs."""
    stamp = datetime(2026, 10, 21, 2, 30, tzinfo=timezone.utc)
    legacy, shared = [], []
    for n, (pe, value, filed, accn, *_rest) in enumerate(versions):
        vid = f"{len(versions) - n:08x}-0000-0000-0000-000000000000"
        legacy.append(_v(value, filed, _stamp(filed), accn=accn, vid=vid, pe=pe))
        shared.append(_v(value, filed, stamp, accn=accn, vid=vid, pe=pe))

    def strip(rows: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "recorded_at"} for r in rows]

    assert strip(_pick(shared)) == strip(_pick(legacy))


def test_a_recorded_at_tie_picks_the_greater_id_like_distinct_on():
    """Two versions of the SAME filing recorded at the same instant (the writer never stores that — a filing's
    re-parse is a later pass's, migration 0060; the pure function still decides it deterministically): the greater id wins (``recorded_at
    DESC, id DESC``) whatever order the rows arrive in."""
    t = _stamp(date(2026, 4, 22))
    lo = _v(100, date(2026, 4, 22), t, accn="lo", vid="00000000-0000-0000-0000-000000000001")
    hi = _v(200, date(2026, 4, 22), t, accn="hi", vid="ffffffff-0000-0000-0000-000000000001")
    for order in ([lo, hi], [hi, lo]):
        (row,) = _pick(order)
        assert row["accession"] == "hi" and row[KNOWABLE_SOURCE] == "hi"


def test_a_filing_counts_once_in_the_run_by_its_latest_version():
    """The revenue-label supersession's shape (the review's A3; real instances on the dev copy: 55 periods in
    10 companies — MTRN's in ``tests/ingest/test_revenue_label.py``): an old tag's X on two filings, stored
    with the pre-0057 stamps, and the current tag's Y re-parsing BOTH under one later honest stamp (one run).
    Each filing takes part in the run by its latest version only, so Y — public since F1 — dates at F1, not
    at the F2 comparative the superseded X between them used to break the run at. The pick is unchanged.
    """
    f1, f2 = date(2024, 8, 5), date(2025, 8, 4)
    run = datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc)  # the re-ingest's one honest stamp
    rows = [
        _v(100, f1, _stamp(f1), accn="f1"),
        _v(90, f1, run, accn="f1"),
        _v(100, f2, _stamp(f2), accn="f2"),
        _v(90, f2, run, accn="f2"),
    ]
    (row,) = _pick(rows)
    assert row["value"] == 90 and row["valid_from"] == f2  # the pick: the latest version overall
    assert row[KNOWABLE_FROM] == f1 and row[KNOWABLE_SOURCE] == "f1"
    # a genuine change across filings is still new knowledge: Y on F1, then Z on F2 dates at F2
    rows[3] = _v(80, f2, run, accn="f2")
    (row,) = _pick(rows)
    assert row["value"] == 80 and row[KNOWABLE_FROM] == f2
