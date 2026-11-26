"""S4 — the nightly split detection (``pipeline/detect_splits.py``) on a real DB, over REAL inputs: the 8-Ks
(and the items the nightly stored for them on prod), the 2026-10-22 Massive.com rows, and every stored version of
the bars around each split (``tests/split_s4_fixtures``). Cache-only: no network. Every idempotency claim
COUNTS THE TABLE — the as-of read dedups, so a duplicate append would hide behind it."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from domain.splits import (
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    VIA_AUTO,
    VIA_SEC_TAPE,
    PolygonEvidence,
    SecEvidence,
    decide,
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
from tests.split_fixtures import edgar_cache, v1_source
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
        allow_live=True,  # the fake Massive.com socket; the EDGAR client below is cache-only
        source=_polygon(tmp_path, today=asof) if polygon else None,
    )
    if not polygon:
        det.result.polygon = "off (no MASSIVE_API_KEY)"
    edgar = EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False)
    ds.run_over_union(db, det, edgar, tickers=tickers)
    return det


def _run_v1(db, tmp_path, *, asof: date) -> ds.SplitDetector:
    """A night on the v1 reader (W6-1): the real ``/stocks/v1/splits`` rows, each with Massive.com's label."""
    src, _ = v1_source(tmp_path, today=asof)
    det = ds.SplitDetector.start(asof=asof, allow_live=True, source=src)
    ds.run_over_union(db, det, EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False))
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
        "GAME 1-for-8 reverse split confirmed (8-K + Massive.com)",
        "NXL 1-for-30 reverse split confirmed (8-K + Massive.com)",
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
    assert lines[0].startswith("splits: Massive.com off (no MASSIVE_API_KEY)")
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
    )  # filed 08-27, no Massive.com, no tape yet
    (v,) = _latest(db, ids["NXL"])
    assert (v.status, v.value_source, v.key_date) == (STATUS_NEEDS_REVIEW, "sec", date(2026, 8, 31))
    _run(db, tmp_path, asof=date(2026, 8, 28))  # Massive.com lists it: the two sources agree
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
    det = _run(db, tmp_path, asof=date(2026, 8, 28), polygon=False)  # Massive.com down tonight
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
    # FDCT: S1 listed Massive.com's 07-10 row for review (no SEC filing in its window); the 8-K came 47 days later
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
            "note": "Massive.com only — no SEC filing read in the window states this split (0 filing(s) read).",
            "valid_from": date(2026, 7, 10),
        },
        action=split_repo.ACTION_BACKFILL,
        author=split_repo.AUTHOR_BACKFILL,
    )
    db.commit()
    _run(db, tmp_path, asof=date(2026, 8, 27))  # tonight's Massive.com pull no longer carries FDCT
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
        and block["appended"][0]["sources"] == "8-K + Massive.com"
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
    # the publication, not our stamp: the filing day at 06:00 ET, the first instant of its night (F6 —
    # a date-only instant sits at EDGAR's open, never inside the night before)
    assert row["recorded_at"] == datetime(2026, 8, 19, 6, 0, tzinfo=et)
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
    """ENHA (2026-10-23): the split is listed from Massive.com alone (10-28, needs review — the version prod holds,
    written by the backfill when the reader skipped Item 8.01). The nightly reads the 8.01 8-K and appends ONE
    version carrying its 1-for-10 and its first trading session (10-29), Massive.com's values kept for the
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
        "ENHA 1-for-10 reverse split needs review (8-K + Massive.com; updated)"
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


# --- D2 (platform fixes W2-3): a distribution is set aside automatically; the steward's restore is final ------
#
# SCCO's 2026-11-03 row as Massive.com lists it (a 0.0106-share stock dividend: 1 -> 1.0106), on a name with no CIK
# (no SEC side). Every write is counted on the TABLE.

SCCO_DAY = date(2026, 11, 3)
SCCO_ROW = {
    "execution_date": SCCO_DAY.isoformat(),
    "id": "E-scco-2026-11-03",
    "split_from": 1,
    "split_to": 1.0106,
    "ticker": "SCCO",
}


def _scco_source(tmp_path, *, today: date) -> PolygonSplitSource:
    body = json.dumps({"results": [SCCO_ROW], "status": "OK"})
    return PolygonSplitSource(
        api_key="test-key-not-real",
        cache_dir=tmp_path / f"polygon_scco_{today.isoformat()}",
        fetch=lambda url: body,
        today=lambda: today,
    )


def _detect_scco(db, tmp_path, *, asof: date) -> ds.SplitDetector:
    det = ds.SplitDetector.start(
        asof=asof, allow_live=True, source=_scco_source(tmp_path, today=asof)
    )
    ds.run_over_union(db, det, EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False))
    return det


def _backfill_scco(db, tmp_path, *, asof: date) -> bf.BackfillResult:
    return bf.backfill_splits(
        db,
        polygon=_scco_source(tmp_path, today=asof),
        edgar=EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False),
        since=date(2026, 10, 1),
        asof=asof,
        apply=True,
    )


def test_an_open_system_row_is_set_aside_once_and_a_restored_one_is_never_touched_again(
    db, tmp_path
):
    sid = seed_member(db, "SCCO", None, [])
    # the version the nightly wrote before D2: Massive.com only, waiting for review
    poly = PolygonEvidence(SCCO_ROW["id"], "SCCO", SCCO_DAY, Decimal(1), Decimal("1.0106"))
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=SCCO_DAY,
        values=split_repo.decision_values(decide(poly, [], asof=SCCO_DAY)),
        action=split_repo.ACTION_DETECT,
        author=split_repo.AUTHOR_DETECT,
    )
    db.commit()
    det = _detect_scco(db, tmp_path, asof=date(2026, 11, 5))
    assert [a.line for a in det.result.appended] == [
        "SCCO 1.0106-for-1 forward split set aside as a distribution (Massive.com; updated)"
    ]
    (v,) = _latest(db, sid)
    assert (v.status, v.author, v.value_source, v.confirmed_via) == (
        STATUS_REJECTED,
        split_repo.AUTHOR_DETECT,
        "polygon",
        None,
    )
    assert v.note.startswith(
        "Set aside automatically: a 1.0106-for-1 forward 'split' listed by Massive.com only"
    )
    assert split_repo.count_rows(db) == 2
    # re-decided ONCE: the next night appends nothing
    again = _detect_scco(db, tmp_path, asof=date(2026, 11, 6))
    assert again.result.appended == [] and split_repo.count_rows(db) == 2
    # the steward restores it: back to review, under the steward's name
    back = split_repo.restore(db, v.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert (back.status, back.author, back.action) == (STATUS_NEEDS_REVIEW, "operator", "restore")
    assert split_repo.count_rows(db) == 3
    # ... and neither writer ever touches it again: the nightly, twice, and the backfill
    for night in (date(2026, 11, 6), date(2026, 11, 9)):
        later = _detect_scco(db, tmp_path, asof=night)
        assert later.result.appended == [] and later.result.kept == []
        assert later.planned[0].action == ds.UNCHANGED  # the same values: nothing to tell
    result = _backfill_scco(db, tmp_path, asof=date(2026, 11, 9))
    (p,) = result.planned
    assert (p.decision.status, p.action, result.appended) == (STATUS_REJECTED, bf.OPERATOR_KEPT, 0)
    assert split_repo.count_rows(db) == 3
    (now,) = _latest(db, sid)
    assert (now.id, now.status) == (back.id, STATUS_NEEDS_REVIEW)


def test_a_distribution_born_set_aside_restores_to_review_and_stays_there(db, tmp_path):
    sid = seed_member(db, "SCCO", None, [])
    det = _detect_scco(db, tmp_path, asof=date(2026, 11, 5))
    assert [a.line for a in det.result.appended] == [
        "SCCO 1.0106-for-1 forward split set aside as a distribution (Massive.com)"
    ]
    (v,) = _latest(db, sid)
    assert split_repo.history(
        db, tenant_id=DEFAULT_TENANT_ID, security_id=sid, key_date=SCCO_DAY
    ) == [v]
    assert split_repo.is_auto_set_aside(v) and split_repo.prior_status(db, v) == STATUS_NEEDS_REVIEW
    back = split_repo.restore(db, v.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert back.status == STATUS_NEEDS_REVIEW and split_repo.allowed_actions(back) == [
        "confirm:polygon",
        "reject",
    ]
    assert _detect_scco(db, tmp_path, asof=date(2026, 11, 6)).result.appended == []
    assert _backfill_scco(db, tmp_path, asof=date(2026, 11, 6)).appended == 0
    assert split_repo.count_rows(db) == 2 and _latest(db, sid)[0].id == back.id


@pytest.fixture
def d2_off(monkeypatch):
    """The off switch, as an operator sets it (the env the Settings read), the cache cleared both ways."""
    monkeypatch.setenv("ALPHADECK_SPLIT_DISTRIBUTION_AUTO_SET_ASIDE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_with_the_setting_off_a_distribution_waits_for_review_as_before(db, tmp_path, d2_off):
    sid = seed_member(db, "SCCO", None, [])
    det = _detect_scco(db, tmp_path, asof=date(2026, 11, 5))
    assert det.distribution_max_factor is None
    assert [a.line for a in det.result.appended] == [
        "SCCO 1.0106-for-1 forward split needs review (Massive.com)"
    ]
    (v,) = _latest(db, sid)
    assert v.status == STATUS_NEEDS_REVIEW and v.note.startswith("Massive.com only")


# --- W2-7 (2026-11-25): a split already confirmed on the list, restated beside a new one -----------------------------
#
# GCTK's 8-K of 2026-10-22 (the REAL filing, ``tests/fixtures/splits``; its items from its SGML header) announces a
# 1-for-2 (first session 10-26) and restates the 1-for-15 of 08-28 / 08-31 that prod's list holds confirmed. The rows
# are prod's (the steward review's read of 2026-11-25): Massive.com's own ids, the confirmed version's evidence.

GCTK_CIK = "0001506983"
GCTK_8K = (
    "0001493152-26-048139",
    "8-K",
    date(2026, 10, 22),
    ["1.01", "2.03", "3.02", "7.01", "9.01"],
)
GCTK_DAY = date(2026, 10, 26)
GCTK_ROW = {
    "execution_date": GCTK_DAY.isoformat(),
    "id": "E15650bee4c5e40e44fa5b11023f901cd0fc2d4d63e6dd9da813a1655c136a7d6",
    "split_from": 2,
    "split_to": 1,
    "ticker": "GCTK",
}


def _detect_gctk(db, tmp_path, *, asof: date) -> ds.SplitDetector:
    body = json.dumps({"results": [GCTK_ROW], "status": "OK"})
    source = PolygonSplitSource(
        api_key="test-key-not-real",
        cache_dir=tmp_path / f"polygon_gctk_{asof.isoformat()}",
        fetch=lambda url: body,
        today=lambda: asof,
    )
    det = ds.SplitDetector.start(asof=asof, allow_live=True, source=source)
    ds.run_over_union(
        db, det, EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False), tickers=["GCTK"]
    )
    return det


def _gctk_1_for_15_confirmed(db, sid) -> None:
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=date(2026, 8, 31),
        values={
            "effective_date": date(2026, 8, 31),
            "ratio_from": Decimal(15),
            "ratio_to": Decimal(1),
            "status": STATUS_CONFIRMED,
            "confirmed_via": VIA_AUTO,
            "value_source": "both",
            "sec_accession": "0001493152-26-040332",
            "sec_form": "8-K",
            "sec_filed": date(2026, 8, 27),
            "sec_url": "https://www.sec.gov/Archives/edgar/data/1506983/000149315226040332/"
            "0001493152-26-040332-index.htm",
            "sec_effective_date": date(2026, 8, 31),
            "sec_date_kind": "trading",
            "sec_ratio_from": Decimal(15),
            "sec_ratio_to": Decimal(1),
            "polygon_id": "E664a48537aa0c93fda53354e4d4cd57e72dfafcf71fd28dae95af170704e4e0f",
            "polygon_ticker": "GCTK",
            "polygon_execution_date": date(2026, 8, 31),
            "polygon_split_from": Decimal(15),
            "polygon_split_to": Decimal(1),
            "note": "SEC 8-K 0001493152-26-040332 (filed 2026-08-27): 1-for-15 reverse split, first session "
            "2026-08-31 and Massive.com agree. 2 SEC filings state it; the earliest is kept.",
            "valid_from": date(2026, 8, 27),
        },
        action=split_repo.ACTION_BACKFILL,
        author=split_repo.AUTHOR_BACKFILL,
    )
    db.commit()


def test_gctk_the_nightly_confirms_its_new_split_once_the_old_one_is_on_the_list(db, tmp_path):
    """Without the 1-for-15 on the list the 8-K abstains on two ratios: the 1-for-2 waits for review and the 8-K is
    named unplaced (prod's 2026-10-29 reading). With it confirmed there, the 8-K is read knowing it: the 1-for-2 and
    its first session agree with Massive.com — confirmed, ONE new version (the extraction memo is keyed by what the
    filing was read knowing, so the next night re-reads it); the 1-for-15 is untouched; a re-run appends nothing.
    """
    sid = seed_member(db, "GCTK", GCTK_CIK, [GCTK_8K])
    asof = date(2026, 10, 29)
    first = _detect_gctk(db, tmp_path, asof=asof)
    (v,) = _latest(db, sid)
    assert (v.key_date, v.status, v.value_source) == (GCTK_DAY, STATUS_NEEDS_REVIEW, "polygon")
    assert "the extractor abstained — more than one ratio stated: 2->1, 15->1" in v.note
    (u,) = first.result.unplaced
    assert (u.accession, u.reason) == (GCTK_8K[0], "more than one ratio stated: 2->1, 15->1")
    _gctk_1_for_15_confirmed(db, sid)
    assert split_repo.count_rows(db) == 2
    second = _detect_gctk(db, tmp_path, asof=asof)
    assert [a.line for a in second.result.appended] == [
        "GCTK 1-for-2 reverse split confirmed (8-K + Massive.com; updated)"
    ]
    assert second.result.unplaced == []
    now = {x.key_date: x for x in _latest(db, sid)}
    v = now[GCTK_DAY]
    assert (v.status, v.confirmed_via, v.sec_accession) == (STATUS_CONFIRMED, VIA_AUTO, GCTK_8K[0])
    assert (v.sec_ratio_from, v.sec_ratio_to, v.sec_effective_date, v.sec_date_kind) == (
        Decimal(2),
        Decimal(1),
        GCTK_DAY,
        "trading",
    )
    assert now[date(2026, 8, 31)].status == STATUS_CONFIRMED  # the listed split, untouched
    assert split_repo.count_rows(db) == 3
    third = _detect_gctk(db, tmp_path, asof=asof)
    assert third.result.appended == [] and split_repo.count_rows(db) == 3


# --- W6-3b: a Massive.com fill is never split evidence (decision W6-c) -------------------------------------------


def _fill(d: date, close: str, volume: str):
    """One Massive.com fill (``gap_fill.FillBar``); the test stores it as the price leg does: the bar, then its
    ``price_bar_fill`` row (``record_fills``), in one transaction."""
    from fractions import Fraction

    from ingest.prices.gap_fill import REASON_SPLIT_WINDOW, FillBar

    return FillBar(
        d=d,
        open=None,
        high=None,
        low=None,
        close=Decimal(close),
        volume=Decimal(volume),
        raw_close=Decimal(close),
        raw_volume=Decimal(volume),
        vendor_symbol="SNDK",
        factor=Fraction(1),
        reason=REASON_SPLIT_WINDOW,
    )


def test_tape_at_skips_a_massive_fill_and_reads_the_vendor_s_later_print(db):
    """SNDK's real closes (the store's 11-19 = 94.395, Massive.com's raw 11-20 = 79.24 on 127,860,864.911014): with only
    the fill holding 11-20 the tape reads NO bar there (never evidence); once the vendor serves 11-20 (STATED: the
    same close, the volume as Yahoo rounds it) its print is the first-served one. The window marks the fill.
    """
    from ingest.prices.eod_loader import ingest_prices
    from ingest.prices.ingest_security import record_fills

    nov18, nov19, nov20 = date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "SNDK", "2025-01-01"),
        )
    bar = {"open": None, "high": None, "low": None}
    ingest_prices(
        db,
        sid,
        [
            {**bar, "d": nov18, "close": Decimal("97.0275"), "volume": Decimal(103506816)},
            {**bar, "d": nov19, "close": Decimal("94.395"), "volume": Decimal(119852712)},
        ],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    fill = _fill(nov20, "79.24", "127860864.911014")
    ingest_prices(db, sid, [fill.bar()], tenant_id=DEFAULT_TENANT_ID)
    record_fills(db, sid, [fill], tenant_id=DEFAULT_TENANT_ID)
    db.commit()

    assert ds.tape_at(db, tenant_id=DEFAULT_TENANT_ID, security_id=sid, session=nov20) is None
    win = ds.tape_window(db, tenant_id=DEFAULT_TENANT_ID, security_id=sid, around=nov20, sessions=3)
    assert [(b.d, b.close, b.filled) for b in win] == [
        (nov18, 97.0275, False),
        (nov19, 94.395, False),
        (nov20, 79.24, True),
    ]

    ingest_prices(
        db,
        sid,
        [{**bar, "d": nov20, "close": Decimal("79.24"), "volume": Decimal(127860900)}],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    tape = ds.tape_at(db, tenant_id=DEFAULT_TENANT_ID, security_id=sid, session=nov20)
    assert tape is not None
    assert (tape.prior, tape.prior_close, tape.close, tape.volume) == (
        nov19,
        94.395,
        79.24,
        127860900.0,  # the vendor's print, never the fill's 127,860,864.911014
    )
    win = ds.tape_window(db, tenant_id=DEFAULT_TENANT_ID, security_id=sid, around=nov20, sessions=3)
    assert [(b.d, b.volume, b.filled, b.restated) for b in win][-1] == (
        nov20,
        127860900.0,
        False,
        False,
    )


def test_tape_at_reads_past_a_fill_on_the_prior_session(db):
    """A fill on the session BEFORE: the prior bar is the vendor's older one (within the 14-day reach)."""
    from ingest.prices.eod_loader import ingest_prices
    from ingest.prices.ingest_security import record_fills

    nov19, nov20, nov23 = date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "SNDK", "2025-01-01"),
        )
    bar = {"open": None, "high": None, "low": None}
    ingest_prices(
        db,
        sid,
        [{**bar, "d": nov19, "close": Decimal("94.395"), "volume": Decimal(119852712)}],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    fill = _fill(nov20, "79.24", "127860864.911014")
    ingest_prices(db, sid, [fill.bar()], tenant_id=DEFAULT_TENANT_ID)
    record_fills(db, sid, [fill], tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    ingest_prices(
        db,
        sid,
        [{**bar, "d": nov23, "close": Decimal("87.71"), "volume": Decimal(114032600)}],
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    tape = ds.tape_at(db, tenant_id=DEFAULT_TENANT_ID, security_id=sid, session=nov23)
    assert tape is not None and (tape.prior, tape.prior_close) == (nov19, 94.395)


# --- W6-1: Massive.com's label is evidence on the list, never a reason to append ---------------------------------


def test_a_listed_split_without_a_label_meets_tonights_labelled_row_and_nothing_is_appended(
    db, tmp_path
):
    """GAME listed (confirmed) by the v3 reader, so its label is NULL; tonight the v1 reader serves the same row
    labelled ``reverse_split``: the plan is UNCHANGED and the TABLE does not grow — the stored row keeps NULL.
    """
    ids = _seed(db, ["GAME"])
    _run(db, tmp_path, asof=date(2026, 9, 1))
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.polygon_adjustment_type) == (STATUS_CONFIRMED, None)
    det = _run_v1(db, tmp_path, asof=date(2026, 9, 1))
    assert split_repo.count_rows(db) == 1 and det.result.appended == []
    (p,) = det.planned
    assert p.action == ds.UNCHANGED and p.decision.polygon.adjustment_type == "reverse_split"
    (v,) = _latest(db, ids["GAME"])
    assert v.polygon_adjustment_type is None


def test_an_open_split_re_versioned_for_another_reason_takes_the_label(db, tmp_path):
    """GAME announced by the v3 reader (no label); the same night on the v1 reader: the label alone appends nothing.
    On its first session the status moves (announced -> confirmed): that new version carries the label.
    """
    ids = _seed(db, ["GAME"])
    _run(db, tmp_path, asof=date(2026, 8, 20))
    det = _run_v1(db, tmp_path, asof=date(2026, 8, 20))
    assert split_repo.count_rows(db) == 1 and det.result.appended == []
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.polygon_adjustment_type) == (STATUS_ANNOUNCED, None)
    det = _run_v1(db, tmp_path, asof=date(2026, 8, 24))
    assert split_repo.count_rows(db) == 2 and det.result.appended[0].verb == "updated"
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.polygon_adjustment_type) == (STATUS_CONFIRMED, "reverse_split")


# --- W6-2 (decision W6-a): Massive.com's stock-dividend label; the steward's CYCU rejection is never re-decided ------
#
# CYCU 2025-12-15 as Massive.com lists it (1 -> 1.008), labelled a stock dividend, and its proxy 8-K (an authorized
# range only: the extractor abstained). The steward REJECTED it on prod (an operator version): the label rule now
# agrees with the steward, and still the nightly appends nothing to it.

CYCU_DAY = date(2025, 12, 15)
CYCU_ROW = PolygonEvidence(
    "E-CYCU-2025-12-15", "CYCU", CYCU_DAY, Decimal(1), Decimal("1.008"), "stock_dividend"
)
CYCU_8K = SecEvidence(
    "0001493152-25-016033",
    "8-K",
    date(2025, 9, 29),
    "u",
    abstain_reason="only an authorized range of ratios (3:1) — no ratio set",
    about_a_split=True,
)


def _cycu_source(tmp_path, *, today: date) -> PolygonSplitSource:
    """CYCU's row on the v1 endpoint's shape (its label), behind a fake socket."""
    row = {
        "adjustment_type": "stock_dividend",
        "execution_date": CYCU_DAY.isoformat(),
        "id": CYCU_ROW.id,
        "split_from": 1.0,
        "split_to": 1.008,
        "ticker": "CYCU",
    }
    body = json.dumps({"results": [row], "status": "OK"})
    return PolygonSplitSource(
        api_key="test-key-not-real",
        cache_dir=tmp_path / f"polygon_cycu_{today.isoformat()}",
        fetch=lambda url: body,
        today=lambda: today,
    )


def test_the_detector_reads_the_label_setting_once_a_pass(monkeypatch, tmp_path):
    var = "ALPHADECK_SPLIT_DISTRIBUTION_LABEL_OVERRIDES_ABSTAIN"
    monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    try:
        on = ds.SplitDetector.start(
            asof=CYCU_DAY, allow_live=True, source=_cycu_source(tmp_path, today=CYCU_DAY)
        )
        assert on.label_overrides_abstain is True
        monkeypatch.setenv(var, "false")
        get_settings.cache_clear()
        off = ds.SplitDetector.start(
            asof=CYCU_DAY, allow_live=True, source=_cycu_source(tmp_path, today=CYCU_DAY)
        )
        assert off.label_overrides_abstain is False
    finally:
        get_settings.cache_clear()


def test_cycus_operator_rejection_is_never_re_decided(db, tmp_path):
    sid = seed_member(db, "CYCU", None, [])
    cap = Decimal("1.5")
    # the version the backfill listed before W6-2: waiting for review, the proxy 8-K as its evidence
    listed = split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=CYCU_DAY,
        values=split_repo.decision_values(
            decide(CYCU_ROW, [CYCU_8K], asof=CYCU_DAY, distribution_max_factor=cap)
        ),
        action=split_repo.ACTION_BACKFILL,
        author=split_repo.AUTHOR_BACKFILL,
    )
    db.commit()
    assert listed.status == STATUS_NEEDS_REVIEW
    rejected = split_repo.reject(db, listed.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    # tonight's verdict with the label rule: set aside — the steward's own answer, re-decided by no one
    tonight = decide(
        CYCU_ROW,
        [CYCU_8K],
        asof=CYCU_DAY,
        distribution_max_factor=cap,
        label_overrides_abstain=True,
    )
    assert tonight.status == STATUS_REJECTED and tonight.sec == CYCU_8K
    m = bf.Member(DEFAULT_TENANT_ID, sid, "CYCU", None, None)
    (p,) = ds.plan_against(
        ds.SecurityDetection(member=m, decisions=[tonight]),
        [rejected],
        asof=CYCU_DAY,
        distribution_max_factor=cap,
        label_overrides_abstain=True,
    )
    assert (p.action, p.stored.id) == (
        ds.UNCHANGED,
        rejected.id,
    )  # the same values: nothing to tell
    assert p.reason == "the operator's decision stands"
    # the whole nightly, twice: the TABLE does not grow
    for night in (date(2025, 12, 16), date(2025, 12, 17)):
        det = ds.SplitDetector.start(
            asof=night, allow_live=True, source=_cycu_source(tmp_path, today=night)
        )
        ds.run_over_union(db, det, EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False))
        assert det.result.appended == [] and det.planned[0].action == ds.UNCHANGED
    assert split_repo.count_rows(db) == 2
    (now,) = _latest(db, sid)
    assert (now.id, now.status, now.author) == (rejected.id, STATUS_REJECTED, "operator")
