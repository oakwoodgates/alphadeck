"""S4 — the nightly split detection (``pipeline/detect_splits.py``) on a real DB, over REAL inputs: the 8-Ks
(and the items the nightly stored for them on prod), the 2026-10-22 Polygon rows, and every stored version of
the bars around each split (``tests/split_s4_fixtures``). Cache-only: no network. Every idempotency claim
COUNTS THE TABLE — the as-of read dedups, so a duplicate append would hide behind it."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.splits import (
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_AUTO,
    VIA_SEC_TAPE,
)
from ingest.edgar.client import EdgarClient
from ingest.polygon_splits import PolygonSplitSource
from pipeline import backfill_splits as bf
from pipeline import detect_splits as ds
from pipeline import ingest_thesis as IT
from repositories import split_repo
from tests.pipeline.test_ingest_thesis import _make_thesis, _patch
from tests.split_801_fixtures import (
    AIRE_8KA,
    AIRE_CIK,
    ENHA_8K,
    ENHA_FIRST_SESSION,
    ENHA_POLYGON_DAY,
    OLD_ITEMS,
    enha_polygon,
    seed_enha,
    seed_member,
)
from tests.split_801_fixtures import SINCE as SINCE_8_01
from tests.split_fixtures import edgar_cache
from tests.split_s4_fixtures import CIKS, FILINGS, ROWS, tape_rows


def _seed(db, tickers) -> dict[str, uuid.UUID]:
    """The names in one live thesis, with the 8-K item rows and the stored bar versions prod holds for them."""
    ids: dict[str, uuid.UUID] = {}
    with db.cursor() as cur:
        for t in tickers:
            sid = uuid.uuid4()
            ids[t] = sid
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, t, CIKS[t], "2025-01-01"),
            )
            # the real stored versions, each with the stamp prod recorded it under (a fixture of real data)
            for r in tape_rows(t):
                cur.execute(
                    "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, "
                    "recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        DEFAULT_TENANT_ID,
                        sid,
                        r["d"],
                        r["close"],
                        r["volume"],
                        r["d"],
                        r["recorded_at"],
                    ),
                )
    db.commit()
    _make_thesis(db, [(t, ids[t]) for t in tickers])
    for acc, (t, form, filed, items) in FILINGS.items():
        if t in ids:
            append_fact(
                db,
                "fact_corporate_event",
                {
                    "tenant_id": DEFAULT_TENANT_ID,
                    "security_id": ids[t],
                    "form": form,
                    "items": items,
                    "accession": acc,
                    "filed": date.fromisoformat(filed),
                    "source_ref": f"https://www.sec.gov/Archives/edgar/data/{int(CIKS[t])}/{acc}",
                    "valid_from": date.fromisoformat(filed),
                },
            )
    db.commit()
    return ids


def _polygon(tmp_path, *, today: date) -> PolygonSplitSource:
    """The real 2026-10-22 rows behind a fake socket (no network; the request carries a date only)."""
    body = ROWS.read_text(encoding="utf-8")
    return PolygonSplitSource(
        api_key="test-key-not-real",
        cache_dir=tmp_path / f"polygon_{today.isoformat()}",
        fetch=lambda url: body,
        today=lambda: today,
    )


def _run(db, tmp_path, *, asof: date, polygon: bool = True, tickers=None) -> ds.SplitDetector:
    det = ds.SplitDetector.start(
        asof=asof,
        allow_live=True,  # the fake Polygon socket; the EDGAR client below is cache-only
        source=_polygon(tmp_path, today=asof) if polygon else None,
    )
    if not polygon:
        det.result.polygon = "off (no POLYGON_API_KEY)"
    edgar = EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False)
    ds.run_over_union(db, det, edgar, tickers=tickers)
    return det


def _latest(db, sid) -> list[split_repo.SplitVersion]:
    return split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, security_ids=[sid])


def test_a_split_both_sources_state_is_confirmed_and_a_rerun_appends_nothing(db, tmp_path):
    ids = _seed(db, ["GAME", "NXL"])
    det = _run(db, tmp_path, asof=date(2026, 9, 1))
    assert split_repo.count_rows(db) == 2
    for t, d0, k in [("GAME", date(2026, 8, 24), 8), ("NXL", date(2026, 8, 31), 30)]:
        (v,) = _latest(db, ids[t])
        assert (v.key_date, v.status, v.confirmed_via) == (d0, STATUS_CONFIRMED, VIA_AUTO), t
        assert (v.ratio_from, v.ratio_to) == (Decimal(k), Decimal(1))
        assert (v.author, v.action) == (split_repo.AUTHOR_DETECT, split_repo.ACTION_DETECT)
        assert v.sec_passage and v.polygon_id  # both sources' evidence on the row
    assert [a.line for a in det.result.appended] == [
        "GAME 1-for-8 reverse split confirmed (8-K + Polygon)",
        "NXL 1-for-30 reverse split confirmed (8-K + Polygon)",
    ]
    # the second night: the TABLE does not grow
    det2 = _run(db, tmp_path, asof=date(2026, 9, 1))
    assert split_repo.count_rows(db) == 2 and det2.result.appended == []


def test_with_polygon_off_the_sec_filing_and_the_tape_confirm_it(db, tmp_path):
    ids = _seed(db, ["GAME"])
    det = _run(db, tmp_path, asof=date(2026, 9, 5), polygon=False)
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.confirmed_via, v.value_source) == (STATUS_CONFIRMED, VIA_SEC_TAPE, "sec")
    assert v.polygon_id is None and "x7.5" in v.note
    lines = det.result.report_lines()
    assert lines[0].startswith("splits: Polygon off (no POLYGON_API_KEY)")
    assert "GAME 1-for-8 reverse split confirmed (8-K + tape)" in lines[1]


def test_a_split_filed_ahead_is_announced_then_confirmed_on_its_night(db, tmp_path):
    ids = _seed(db, ["GAME"])
    _run(db, tmp_path, asof=date(2026, 8, 20))  # the 8-K was filed 08-19 for 08-24
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.confirmed_via, v.effective_date) == (
        STATUS_ANNOUNCED,
        VIA_AUTO,
        date(2026, 8, 24),
    )
    assert v.valid_from == date(2026, 8, 19)
    det = _run(db, tmp_path, asof=date(2026, 8, 24))
    (v,) = _latest(db, ids["GAME"])
    assert v.status == STATUS_CONFIRMED and split_repo.count_rows(db) == 2
    assert det.result.appended[0].verb == "updated"
    _run(db, tmp_path, asof=date(2026, 8, 24))
    assert split_repo.count_rows(db) == 2


def test_one_source_alone_waits_for_review_and_is_upgraded_when_the_second_arrives(db, tmp_path):
    ids = _seed(db, ["NXL"])
    _run(
        db, tmp_path, asof=date(2026, 8, 28), polygon=False
    )  # filed 08-27, no Polygon, no tape yet
    (v,) = _latest(db, ids["NXL"])
    assert (v.status, v.value_source, v.key_date) == (STATUS_NEEDS_REVIEW, "sec", date(2026, 8, 31))
    _run(db, tmp_path, asof=date(2026, 8, 28))  # Polygon lists it: the two sources agree
    (v,) = _latest(db, ids["NXL"])
    assert (v.status, v.confirmed_via, v.key_date) == (
        STATUS_ANNOUNCED,
        VIA_AUTO,
        date(2026, 8, 31),
    )
    assert split_repo.count_rows(db) == 2  # ONE logical split, two versions


def test_a_missing_source_never_downgrades_a_listed_split(db, tmp_path):
    ids = _seed(db, ["NXL"])
    _run(db, tmp_path, asof=date(2026, 8, 28))
    before = split_repo.count_rows(db)
    det = _run(db, tmp_path, asof=date(2026, 8, 28), polygon=False)  # Polygon down tonight
    assert split_repo.count_rows(db) == before and det.result.appended == []
    (v,) = _latest(db, ids["NXL"])
    assert v.status == STATUS_ANNOUNCED


def test_the_operators_decision_is_never_touched(db, tmp_path):
    ids = _seed(db, ["GAME"])
    _run(db, tmp_path, asof=date(2026, 8, 20))
    (v,) = _latest(db, ids["GAME"])
    split_repo.reject(db, v.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    before = split_repo.count_rows(db)
    _run(db, tmp_path, asof=date(2026, 8, 25))
    assert split_repo.count_rows(db) == before
    assert _latest(db, ids["GAME"])[0].status == "rejected"


def test_a_late_filing_meets_the_polygon_row_the_list_already_holds(db, tmp_path):
    # FDCT: S1 listed Polygon's 07-10 row for review (no SEC filing in its window); the 8-K came 47 days later
    ids = _seed(db, ["FDCT"])
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=ids["FDCT"],
        key_date=date(2026, 7, 10),
        values={
            "effective_date": date(2026, 7, 10),
            "ratio_from": Decimal(100),
            "ratio_to": Decimal(1),
            "status": STATUS_NEEDS_REVIEW,
            "value_source": "polygon",
            "polygon_id": "E99b1775429ab5476fd2d56fcd034c826134b22464c86fb31a49ed2f7085c30ea",
            "polygon_ticker": "FDCT",
            "polygon_execution_date": date(2026, 7, 10),
            "polygon_split_from": Decimal(100),
            "polygon_split_to": Decimal(1),
            "note": "Polygon only — no SEC filing read in the window states this split (0 filing(s) read).",
            "valid_from": date(2026, 7, 10),
        },
        action=split_repo.ACTION_BACKFILL,
        author=split_repo.AUTHOR_BACKFILL,
    )
    db.commit()
    _run(db, tmp_path, asof=date(2026, 8, 27))  # tonight's Polygon pull no longer carries FDCT
    (v,) = _latest(db, ids["FDCT"])
    assert (v.key_date, v.status, v.confirmed_via) == (
        date(2026, 7, 10),
        STATUS_CONFIRMED,
        VIA_AUTO,
    )
    assert v.sec_accession == "0001493152-26-040184" and v.polygon_id.startswith("E99b")
    assert split_repo.count_rows(db) == 2


def test_the_artifact_block_and_a_quiet_night(db, tmp_path):
    _seed(db, ["GAME"])
    det = _run(db, tmp_path, asof=date(2026, 8, 25))
    block = json.loads(json.dumps(det.result.payload()))["split_detect"]
    assert block["evaluated"] and block["polygon"] == "on" and block["checked"] == 1
    assert (
        block["appended"][0]["ticker"] == "GAME"
        and block["appended"][0]["sources"] == "8-K + Polygon"
    )
    quiet = _run(db, tmp_path, asof=date(2026, 8, 25))
    assert quiet.result.report_lines() == [
        "splits: no new list rows · 1 companies, 1 filing(s) read"
    ]


def test_the_public_clock_reads_a_detected_split_from_its_own_publication(db, tmp_path):
    # S2's history read (``split_repo.as_of_rows(clock="public")``): every version of the split counts from
    # its first SEC filing (GAME's 8-K, 2026-08-19), never from the nights the detector wrote it (announced
    # 08-20, confirmed 08-24 — both stamped NOW, honest bookkeeping)
    ids = _seed(db, ["GAME"])
    _run(db, tmp_path, asof=date(2026, 8, 20))
    _run(db, tmp_path, asof=date(2026, 8, 24))
    sid = ids["GAME"]
    et = ZoneInfo("America/New_York")

    def public(at: datetime) -> list[dict]:
        return split_repo.as_of_rows(
            db, tenant_id=DEFAULT_TENANT_ID, security_ids=[sid], known_at=at, clock="public"
        )[sid]

    assert public(datetime(2026, 8, 18, 23, 0, tzinfo=et)) == []
    (row,) = public(datetime(2026, 8, 19, 12, 0, tzinfo=et))
    assert (row["status"], row["effective_date"]) == (STATUS_CONFIRMED, date(2026, 8, 24))
    assert row["recorded_at"] == datetime(2026, 8, 19, tzinfo=et)  # the publication, not our stamp
    assert row["ingested_at"] > row["recorded_at"]  # the honest stamp is kept beside it


# --- the hook inside the per-thesis ingest -------------------------------------------------------------


class _Recorder:
    """A stand-in detector recording what the stored tables held when the ingest called it."""

    def __init__(self, db, raise_with: Exception | None = None):
        self.db, self.raise_with, self.seen, self.faults = db, raise_with, [], []

    def check(self, conn, client, sec, *, tenant_id):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n FROM fact_corporate_event WHERE security_id = %s", (sec.id,)
            )
            events = cur.fetchone()["n"]
            cur.execute("SELECT count(*) n FROM fact_price_eod WHERE security_id = %s", (sec.id,))
            bars = cur.fetchone()["n"]
        self.seen.append((sec.ticker, events, bars))
        if self.raise_with is not None:
            raise self.raise_with

    def fault(self, sec, exc):
        self.faults.append((sec.ticker, str(exc)))


def test_the_ingest_checks_a_company_after_its_8k_leg_and_before_its_price_leg(
    db, security_id, monkeypatch
):
    _patch(monkeypatch, bar_dates=(date(2026, 6, 15),), form8ks=(("8K-1", "2026-06-10", "5.03"),))
    tid = _make_thesis(db, [("DEVCO", security_id)])
    rec = _Recorder(db)
    (r,) = IT.ingest_thesis(db, tid, allow_live=False, split_detector=rec)
    assert rec.seen == [("DEVCO", 1, 0)]  # tonight's 8-K stored, tonight's bars not yet
    assert r.error is None and r.price_bars_appended == 1


def test_a_detection_fault_never_costs_the_name_its_ingest(db, security_id, monkeypatch):
    _patch(monkeypatch, bar_dates=(date(2026, 6, 15),))
    tid = _make_thesis(db, [("DEVCO", security_id)])
    rec = _Recorder(db, raise_with=RuntimeError("boom"))
    (r,) = IT.ingest_thesis(db, tid, allow_live=False, split_detector=rec)
    assert rec.faults == [("DEVCO", "boom")]
    assert r.error is None and r.leg_errors == () and r.price_bars_appended == 1


def test_the_nightly_attaches_an_item_8_01_reading_to_a_listed_split(db, tmp_path, monkeypatch):
    """ENHA (2026-10-23): the split is listed from Polygon alone (10-28, needs review — the version prod holds,
    written by the backfill when the reader skipped Item 8.01). The nightly reads the 8.01 8-K and appends ONE
    version carrying its 1-for-10 and its first trading session (10-29), Polygon's values kept for the
    operator; the next night appends nothing (the TABLE is counted)."""
    sid = seed_enha(db)
    with monkeypatch.context() as m:
        m.setattr(bf, "SPLIT_8K_ITEMS", OLD_ITEMS)
        bf.backfill_splits(
            db,
            polygon=enha_polygon(tmp_path, today=date(2026, 10, 20)),
            edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
            since=SINCE_8_01,
            asof=date(2026, 10, 20),
            apply=True,
            tickers=["ENHA"],
        )
    assert split_repo.count_rows(db) == 1
    det = _run(db, tmp_path, asof=date(2026, 10, 23), tickers=["ENHA"])
    assert [a.line for a in det.result.appended] == [
        "ENHA 1-for-10 reverse split needs review (8-K + Polygon; updated)"
    ]
    assert split_repo.count_rows(db) == 2
    (v,) = _latest(db, sid)
    assert (v.author, v.action, v.status) == (
        split_repo.AUTHOR_DETECT,
        split_repo.ACTION_DETECT,
        STATUS_NEEDS_REVIEW,
    )
    assert (v.effective_date, v.sec_accession) == (ENHA_POLYGON_DAY, ENHA_8K)
    assert (v.sec_effective_date, v.sec_date_kind) == (ENHA_FIRST_SESSION, "trading")
    assert split_repo.confirm_options(v) == ["sec", "polygon"]
    det2 = _run(db, tmp_path, asof=date(2026, 10, 23), tickers=["ENHA"])
    assert det2.result.appended == [] and split_repo.count_rows(db) == 2
    # ENHA's other 8.01 / 7.01 8-Ks in the lookback state no split, and are not listed as unplaced either
    assert det.result.unplaced == [] and det.result.filings_read == 3


def test_a_catch_all_8k_restating_a_past_split_is_never_listed(db, tmp_path, monkeypatch):
    """AIRE (no split on the list): its Item 7.01 8-K/A of 2026-09-23 restates a 1-for-25 beside a date 266
    days back. Without the catch-all filing-date screen the nightly would LIST a new split on 2025-12-31 from it
    — MEASURED on prod's basket 2026-10-23, the one new list row the wider item set produced. With it, nothing
    is listed, and the filing is named as not placed (#9: shown, never dropped)."""
    seed_member(db, "AIRE", AIRE_CIK, [AIRE_8KA])
    asof = date(2026, 10, 23)
    edgar = EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False)
    with monkeypatch.context() as m:
        m.setattr(bf, "screen_of", lambda ref: bf.SCREEN_NONE)  # the reader without any screen
        bare = ds.SplitDetector.start(
            asof=asof, allow_live=True, source=_polygon(tmp_path, today=asof), apply=False
        )
        ds.run_over_union(db, bare, edgar, tickers=["AIRE"])
    (p,) = bare.planned
    assert (p.action, p.decision.key_date, p.decision.status) == (
        ds.APPEND,
        date(2025, 12, 31),
        STATUS_NEEDS_REVIEW,
    )
    assert split_repo.count_rows(db) == 0  # a dry run
    det = _run(db, tmp_path, asof=asof, tickers=["AIRE"])
    assert det.planned == [] and det.result.appended == []
    assert split_repo.count_rows(db) == 0
    (u,) = det.result.unplaced
    assert (u.ticker, u.accession, u.form) == ("AIRE", AIRE_8KA[0], "8-K/A")
    assert u.reason == (
        "1-for-25 reverse split stated, but only a past date stated (2025-12-31), more than 90 days before "
        "the filing"
    )
