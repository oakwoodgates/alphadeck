"""F7 — filings dated by when they appeared on EDGAR (rule ``2026-11-filings-dated-by-acceptance``).

An 8-K / 13D/G / 10-Q / 10-K submitted after 17:30 ET carries the NEXT business day's ``filingDate`` (Regulation
S-T 13(a)(2)) although EDGAR disseminated it that evening. The trust rule (``resolve_public_acceptance``) admits the
evening-before roll and nothing earlier; an untrusted time keeps the filing date (late, never early). The DB half
pins the ingest legs: an evening 8-K / 13D/G / revenue filing is stored on the day it appeared, a re-run appends
nothing (count the table), and a filing stored before the rule is never re-dated by the ingest (the repair does).

Dates: 2026-10-26 is a Monday; EDT (UTC-4) until 2026-11-01.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.acceptance import (
    SOURCE_FILED,
    SOURCE_HEADER,
    SOURCE_LISTED,
    cached_listings,
    header_cache_key,
    listed_is_trusted_rolled,
    public_date,
    resolve_listed_filing,
    resolve_public_acceptance,
)
from ingest.edgar.form8k import ingest_form8k
from ingest.edgar.schedule13 import ingest_schedule13
from ingest.fundamentals import (
    QuarterPoint,
    carry_stored_acceptance,
    date_revenue_points,
    dated_points,
    store_quarters,
)

UTC = timezone.utc
_CIK = "0001234567"
_KNOWN = datetime(2100, 1, 1, tzinfo=UTC)

MON, TUE, WED = date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)
EVENING_MON = (
    "2026-10-26T22:05:00.000Z"  # 18:05 EDT Monday — the evening an after-17:30 8-K appears
)
LATE_EVENING_MON = "2026-10-27T01:30:00.000Z"  # 21:30 EDT Monday (Tuesday in UTC)
DRIFT_TUE = "2026-10-27T05:00:00.000Z"  # 01:00 EDT Tuesday — EDGAR is closed: the 10-23 list drift


def _utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# --- the trust rule (pure) ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "listed,filed,trusted",
    [
        ("2026-10-26T14:00:00.000Z", MON, True),  # 10:00 ET on its filing date
        (EVENING_MON, TUE, True),  # the roll: Monday evening, filed Tuesday
        (LATE_EVENING_MON, TUE, True),  # 21:30 ET — Tuesday in UTC, still Monday on EDGAR's clock
        (
            "2026-10-23T23:00:00.000Z",
            MON,
            True,
        ),  # Friday 19:00 ET -> filed Monday (a weekend between)
        ("2026-10-09T22:00:00.000Z", date(2026, 10, 13), True),  # Fri -> Tue over a Monday holiday
        ("2026-10-09T22:00:00.000Z", date(2026, 10, 14), False),  # Fri -> Wed: two weekdays between
        ("2026-10-26T20:00:00.000Z", TUE, False),  # 16:00 ET Monday would have been filed Monday
        ("2026-10-27T03:30:00.000Z", TUE, False),  # 23:30 ET Monday: EDGAR closes at 22:00
        (DRIFT_TUE, TUE, False),  # 01:00 ET on the filing date: EDGAR is closed
        # no SEC holiday calendar: ONE weekday between reads as a holiday (a Tuesday July 4th), two do not
        (EVENING_MON, WED, True),
        (EVENING_MON, date(2026, 10, 29), False),
        ("2026-10-27T22:05:00.000Z", MON, False),  # an acceptance AFTER its filing date
    ],
)
def test_the_trust_rule_admits_the_evening_before_roll_and_nothing_earlier(listed, filed, trusted):
    assert listed_is_trusted_rolled(_utc(listed), filed) is trusted


def test_an_untrusted_time_keeps_the_filing_date_never_a_guess():
    res = resolve_public_acceptance(DRIFT_TUE, "2026-10-27")
    assert res.at is None and res.source == SOURCE_FILED
    assert public_date(res.at, TUE) == TUE  # as before the rule: late, never early


def test_a_cached_header_that_passes_the_same_test_replaces_an_untrusted_listed_time():
    header = _utc(EVENING_MON)
    res = resolve_public_acceptance(DRIFT_TUE, "2026-10-27", lambda: header)
    assert (res.at, res.source) == (header, SOURCE_HEADER)
    # a header time that fails the test is not trusted either
    res = resolve_public_acceptance(DRIFT_TUE, "2026-10-27", lambda: _utc(DRIFT_TUE))
    assert res.at is None and res.source == SOURCE_FILED


def test_the_public_date_is_the_market_time_date_whatever_the_process_zone():
    """21:30 ET Monday is 01:30 UTC Tuesday: the row is dated MONDAY (market time), on any process TZ."""
    assert public_date(_utc(LATE_EVENING_MON), TUE) == MON
    assert public_date(_utc(EVENING_MON), TUE) == MON
    assert public_date(None, TUE) == TUE


def test_cached_listings_reads_the_index_and_cached_older_pages_from_disk(tmp_path: Path):
    subs = tmp_path / "submissions"
    subs.mkdir()
    cik = 1234567
    (subs / f"CIK{cik:010d}.json").write_text(
        json.dumps(
            {
                "filings": {
                    "recent": {
                        "accessionNumber": ["ACC-NEW"],
                        "filingDate": ["2026-10-27"],
                        "acceptanceDateTime": [EVENING_MON],
                    },
                    "files": [
                        {"name": f"CIK{cik:010d}-submissions-001.json"},
                        {"name": f"CIK{cik:010d}-submissions-002.json"},  # not cached: skipped
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    (subs / f"CIK{cik:010d}-submissions-001.json").write_text(
        json.dumps(
            {
                "accessionNumber": ["ACC-OLD"],
                "filingDate": ["2019-03-05"],
                "acceptanceDateTime": ["2019-03-04T23:10:00.000Z"],
            }
        ),
        encoding="utf-8",
    )
    got = cached_listings(tmp_path, cik)
    assert got == {
        "ACC-NEW": ("2026-10-27", EVENING_MON),
        "ACC-OLD": ("2019-03-05", "2019-03-04T23:10:00.000Z"),
    }
    assert (
        cached_listings(tmp_path, 7654321) == {}
    )  # nothing cached: nothing resolved (never a request)


def test_a_listing_must_carry_the_rows_own_filing_date():
    listings = {"ACC-1": ("2026-10-27", EVENING_MON)}
    assert resolve_listed_filing(listings, "ACC-1", TUE).source == SOURCE_LISTED
    # a row dated by ANOTHER filing (a derived Q4 dated by the later of two filings) is never re-dated
    assert resolve_listed_filing(listings, "ACC-1", WED).at is None
    assert resolve_listed_filing(listings, "ACC-MISSING", TUE).at is None


def test_a_cached_header_is_read_from_disk(tmp_path: Path):
    acc = "0001234567-26-000099"
    path = tmp_path / header_cache_key(acc)
    path.parent.mkdir(parents=True)
    path.write_text("<ACCEPTANCE-DATETIME>20261026180500", encoding="utf-8")
    res = resolve_listed_filing({acc: ("2026-10-27", DRIFT_TUE)}, acc, TUE, tmp_path)
    assert (res.at, res.source) == (_utc(EVENING_MON), SOURCE_HEADER)


# --- the 8-K leg (DB) ---------------------------------------------------------------------------------------


def _8k(accession, filed="2026-10-27", items=("8.01",), accepted=""):
    return {
        "accession": accession,
        "form": "8-K",
        "filed": filed,
        "items": list(items) if items is not None else None,
        "accepted": accepted,
    }


def _count(db, table) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) n FROM {table}")  # noqa: S608 — test constants
        return cur.fetchone()["n"]


def _read(db, table, sid, asof):
    return {
        r["accession"]: r
        for r in as_of(
            db, table, security_id=sid, asof=asof, known_at=_KNOWN, tenant_id=DEFAULT_TENANT_ID
        )
    }


def test_an_evening_8k_is_stored_on_the_night_it_appeared(db, security_id):
    res = ingest_form8k(db, security_id, _CIK, [_8k("ACC-EVE", accepted=EVENING_MON)])
    db.commit()
    assert (res.appended, res.dated_by_filing) == (1, 0)
    row = _read(db, "fact_corporate_event", security_id, MON)["ACC-EVE"]  # visible on MONDAY's read
    assert row["valid_from"] == MON and row["filed"] == TUE  # SEC's filing date is kept as filed
    assert row["accepted"] == _utc(EVENING_MON)
    # a re-run appends nothing (count the table — the read dedups)
    n = _count(db, "fact_corporate_event")
    ingest_form8k(db, security_id, _CIK, [_8k("ACC-EVE", accepted=EVENING_MON)])
    db.commit()
    assert _count(db, "fact_corporate_event") == n


def test_an_untrusted_time_keeps_the_8k_on_its_filing_date(db, security_id):
    res = ingest_form8k(db, security_id, _CIK, [_8k("ACC-DRIFT", accepted=DRIFT_TUE)])
    db.commit()
    assert res.dated_by_filing == 1
    assert "ACC-DRIFT" not in _read(db, "fact_corporate_event", security_id, MON)
    row = _read(db, "fact_corporate_event", security_id, TUE)["ACC-DRIFT"]
    assert row["valid_from"] == TUE and row["accepted"] is None


def test_a_stored_8k_is_never_re_dated_by_the_ingest(db, security_id):
    """A row stored before the rule (no listed time then) keeps its date on a re-run that now sees a trusted
    time: the acceptance is not part of the compare surface — the repair re-dates stored rows."""
    ingest_form8k(db, security_id, _CIK, [_8k("ACC-OLD")])
    db.commit()
    n = _count(db, "fact_corporate_event")
    res = ingest_form8k(db, security_id, _CIK, [_8k("ACC-OLD", accepted=EVENING_MON)])
    db.commit()
    assert (res.appended, res.reversioned) == (0, 0) and _count(db, "fact_corporate_event") == n
    assert _read(db, "fact_corporate_event", security_id, TUE)["ACC-OLD"]["valid_from"] == TUE


def test_a_re_version_keeps_a_known_acceptance_when_this_pass_resolves_none(db, security_id):
    ingest_form8k(db, security_id, _CIK, [_8k("ACC-R", items=None, accepted=EVENING_MON)])
    db.commit()
    # tonight the items resolve, but the listed time has drifted (untrusted): the stored time is carried
    res = ingest_form8k(db, security_id, _CIK, [_8k("ACC-R", items=("2.02",), accepted=DRIFT_TUE)])
    db.commit()
    assert res.reversioned == 1
    row = _read(db, "fact_corporate_event", security_id, MON)["ACC-R"]
    assert row["items"] == ["2.02"] and row["valid_from"] == MON
    assert row["accepted"] == _utc(EVENING_MON)


# --- the 13D/G leg (DB) -------------------------------------------------------------------------------------


def test_an_evening_13g_is_stored_on_the_night_it_appeared(db, security_id):
    """A classic-era 13G needs no identity fetch (``client`` unused), so the leg's dating is isolated."""
    f = {
        "accession": "ACC-13G",
        "form": "SC 13G",
        "filed": "2026-10-27",
        "primary_doc": "sc13g.htm",
        "accepted": EVENING_MON,
    }
    res = ingest_schedule13(db, security_id, _CIK, [f], None)
    db.commit()
    assert res.appended == 1
    row = _read(db, "fact_activist_stake", security_id, MON)["ACC-13G"]
    assert (row["valid_from"], row["filed"], row["accepted"]) == (MON, TUE, _utc(EVENING_MON))
    n = _count(db, "fact_activist_stake")
    ingest_schedule13(db, security_id, _CIK, [f], None)
    db.commit()
    assert _count(db, "fact_activist_stake") == n


# --- the revenue rows (pure + DB) ---------------------------------------------------------------------------


def _q(value, filed, accession="ACC-Q", metric="revenue", period=date(2026, 9, 30)) -> QuarterPoint:
    return QuarterPoint(
        metric_key=metric,
        period_end=period,
        value=value,
        filed=filed,
        accession=accession,
        basis="native",
        fiscal_period="Q3",
        fiscal_year=2026,
    )


def test_only_revenue_points_whose_listing_carries_their_filing_date_are_dated():
    listings = {"ACC-Q": ("2026-10-27", EVENING_MON), "ACC-K": ("2026-10-28", EVENING_MON)}
    rev, share, derived = date_revenue_points(
        [
            _q(100.0, TUE),
            _q(
                5.0, TUE, metric="shares_out_xbrl"
            ),  # a share series: not re-dated (the rule names revenue)
            _q(9.0, TUE, accession="ACC-K"),  # dated by another filing: its listing says Wednesday
        ],
        listings,
    )
    assert rev.accepted == _utc(EVENING_MON) and rev.valid_from == MON
    assert share.accepted is None and share.valid_from == TUE
    assert derived.accepted is None and derived.valid_from == TUE


def test_a_stored_filing_keeps_its_stored_dating():
    dated = date_revenue_points([_q(100.0, TUE)], {"ACC-Q": ("2026-10-27", EVENING_MON)})
    carried = carry_stored_acceptance(dated, {("revenue", date(2026, 9, 30), "ACC-Q"): None})
    assert carried[0].accepted is None and carried[0].valid_from == TUE  # stored before the rule
    assert carry_stored_acceptance(dated, {})[0].valid_from == MON  # a new filing: its acceptance


def _cache_with_listing(
    tmp_path: Path, cik: str, accession: str, filed: str, accepted: str
) -> Path:
    subs = tmp_path / "submissions"
    subs.mkdir(parents=True, exist_ok=True)
    (subs / f"CIK{int(cik):010d}.json").write_text(
        json.dumps(
            {
                "filings": {
                    "recent": {
                        "accessionNumber": [accession],
                        "filingDate": [filed],
                        "acceptanceDateTime": [accepted],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_an_evening_10q_revenue_row_is_stored_on_the_day_it_appeared(db, security_id, tmp_path):
    cache = _cache_with_listing(tmp_path, _CIK, "ACC-Q", "2026-10-27", EVENING_MON)
    points = dated_points(db, security_id, _CIK, [_q(100.0, TUE)], cache_dir=cache)
    assert store_quarters(db, security_id, points).appended == 1
    db.commit()
    rows = as_of(
        db,
        "fact_fundamentals",
        security_id=security_id,
        asof=MON,
        known_at=_KNOWN,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert [(r["valid_from"], r["knowable_from"], r["accepted"]) for r in rows] == [
        (MON, MON, _utc(EVENING_MON))
    ]
    # a re-run plans nothing (count the table)
    n = _count(db, "fact_fundamentals")
    again = dated_points(db, security_id, _CIK, [_q(100.0, TUE)], cache_dir=cache)
    assert store_quarters(db, security_id, again).appended == 0
    assert _count(db, "fact_fundamentals") == n


def test_a_revenue_filing_stored_before_the_rule_gets_no_second_dating(db, security_id, tmp_path):
    """Stored by its filing date (no cached listing then); a later pass with the listing cached appends NOTHING —
    a second dating of one filing would be read as two filings (the repair re-dates it, with a set-aside).
    """
    assert store_quarters(db, security_id, [_q(100.0, TUE)]).appended == 1
    db.commit()
    cache = _cache_with_listing(tmp_path, _CIK, "ACC-Q", "2026-10-27", EVENING_MON)
    n = _count(db, "fact_fundamentals")
    points = dated_points(db, security_id, _CIK, [_q(100.0, TUE)], cache_dir=cache)
    assert points[0].valid_from == TUE
    assert store_quarters(db, security_id, points).appended == 0
    assert _count(db, "fact_fundamentals") == n


def test_migration_0072_adds_the_nullable_columns(db):
    with db.cursor() as cur:
        for table, col in (
            ("fact_corporate_event", "accepted"),
            ("fact_activist_stake", "accepted"),
            ("fact_fundamentals", "accepted"),
            ("fact_shares_outstanding", "filing_accepted"),
            ("fact_shares_outstanding", "filing_accession"),
        ):
            cur.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, col),
            )
            row = cur.fetchone()
            assert row is not None and row["is_nullable"] == "YES", (table, col)
