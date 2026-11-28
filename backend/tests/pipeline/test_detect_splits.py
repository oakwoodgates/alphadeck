"""S4 — the nightly split detection (``pipeline/detect_splits.py``) on a real DB, over REAL inputs: the 8-Ks
(and the items the nightly stored for them on prod), the 2026-10-22 Massive.com rows, and every stored version of
the bars around each split (``tests/split_s4_fixtures``). Cache-only: no network. Every idempotency claim
COUNTS THE TABLE — the as-of read dedups, so a duplicate append would hide behind it."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
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
    VIA_OPERATOR,
    VIA_SEC_TAPE,
    CallOff,
    PolygonEvidence,
    SecEvidence,
    TapeEvidence,
    cluster_sec,
    decide,
    decide_sec_only,
    with_tape,
)
from ingest.edgar.client import EdgarClient
from ingest.edgar.form8k import filing_index_url
from ingest.polygon_splits import PolygonSplitSource
from pipeline import backfill_splits as bf
from pipeline import detect_splits as ds
from pipeline import ingest_thesis as IT
from pipeline import split_unplaced as su
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


# --- F1-1: an unplaced filing carries its evidence; ``--record`` keeps a long-window run's list -------------

NO_DATE = "no first-session or effective date stated"
UNPLACED_KEYS = {
    "security_id",
    "ticker",
    "accession",
    "form",
    "filed",
    "reason",
    "url",
    "document",
    "passage",
    "ratio_from",
    "ratio_to",
    "abstain_reason",
    "date_note",
}


def _meta_unplaced(**evidence) -> ds.Unplaced:
    """META's row on prod's 2026-11-25 artifact (``tests/fixtures/splits/unplaced``)."""
    return ds.Unplaced(
        uuid.UUID("826a470d-040b-4381-92c3-3c4e19cb81ad"),
        "META",
        "0001628280-26-066700",
        "8-K",
        date(2026, 10, 21),
        f"2-for-1 forward split stated, but {NO_DATE}",
        **evidence,
    )


def test_the_artifact_block_carries_each_unplaced_filings_evidence_the_passage_cut_at_600():
    passage = "stand-in passage text; only its length matters here. " * 20  # 1,080 characters
    det = ds.SplitDetection(
        evaluated=True,
        unplaced=[
            _meta_unplaced(
                url=filing_index_url("1326801", "0001628280-26-066700"),
                document="stand-in.htm",
                passage=passage,
                ratio_from=Decimal(1),
                ratio_to=Decimal(2),
                date_note=NO_DATE,
            ),
            _meta_unplaced(),  # nothing read beyond the reason: every evidence key null
        ],
    )
    full, bare = json.loads(json.dumps(det.payload()))["split_detect"]["unplaced"]
    assert set(full) == set(bare) == UNPLACED_KEYS
    assert ds.UNPLACED_PASSAGE_MAX == 600
    assert full["passage"] == passage[:600] and len(passage) > 600
    assert (full["ratio_from"], full["ratio_to"], full["date_note"]) == ("1", "2", NO_DATE)
    assert full["url"].endswith("/1326801/000162828026066700/0001628280-26-066700-index.htm")
    assert (full["document"], full["abstain_reason"]) == ("stand-in.htm", None)
    evidence = UNPLACED_KEYS - {"security_id", "ticker", "accession", "form", "filed", "reason"}
    assert {k for k in UNPLACED_KEYS if bare[k] is None} == evidence


class _Conn:
    """The CLI's connection, stood in: no database (the run itself is stubbed)."""

    read_only = False

    def close(self) -> None:
        pass


def _cli(
    monkeypatch, tmp_path, rows: list[ds.Unplaced], calloffs: list[ds.CallOffFiling] = ()
) -> list[_Conn]:
    """``detect_splits.main`` with Massive.com off, no DB and a run that lists ``rows`` as unplaced (and
    ``calloffs``); the record home is a tmp dir. Returns the connections the CLI opened."""
    opened: list[_Conn] = []

    def connect() -> _Conn:
        opened.append(_Conn())
        return opened[-1]

    def run_over_union(conn, detector, edgar, *, tickers=None):
        assert conn.read_only  # a dry run
        detector.result.checked += 1
        detector.result.unplaced.extend(rows)
        detector.result.calloffs.extend(calloffs)
        return []

    monkeypatch.setattr(ds, "default_split_source", lambda: None)
    monkeypatch.setattr(ds, "EdgarClient", lambda **kw: object())
    monkeypatch.setattr("db.session.connect", connect)
    monkeypatch.setattr(ds, "run_over_union", run_over_union)
    monkeypatch.setattr(su, "UNPLACED_DIR", tmp_path / "split_unplaced")
    return opened


def test_record_writes_the_runs_unplaced_list_once(monkeypatch, tmp_path, capsys):
    row = _meta_unplaced(ratio_from=Decimal(1), ratio_to=Decimal(2), date_note=NO_DATE)
    _cli(monkeypatch, tmp_path, [row])
    ds.main(["--asof", "2026-11-25", "--sec-days", "800", "--polygon-days", "800", "--record"])
    (path,) = (tmp_path / "split_unplaced").iterdir()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == {
        "kind",
        "started_at",
        "finished_at",
        "asof",
        "sec_days",
        "polygon_days",
        "unplaced",
    }
    assert (doc["kind"], doc["asof"], doc["sec_days"], doc["polygon_days"]) == (
        "detect_splits",
        "2026-11-25",
        800,
        800,
    )
    assert doc["unplaced"] == [row.to_json()]
    started = datetime.fromisoformat(doc["started_at"])
    assert started <= datetime.fromisoformat(doc["finished_at"])
    assert path.name == started.strftime("%Y%m%dT%H%M%SZ") + ".json"
    out = capsys.readouterr().out
    assert "DRY-RUN (read-only connection; no database write) · RECORD" in out
    assert f"recorded 1 unplaced filing(s): {path}" in out
    # the one reader reads it as the check's list
    got = su.read_unplaced(records_dir=tmp_path / "split_unplaced")
    assert [(r["ticker"], r["source"], r["seen_on"]) for r in got.rows] == [
        ("META", "check", "2026-11-25")
    ]


def test_record_refuses_a_ticker_run_and_writes_nothing(monkeypatch, tmp_path, capsys):
    opened = _cli(monkeypatch, tmp_path, [_meta_unplaced()])
    with pytest.raises(SystemExit) as e:
        ds.main(["--asof", "2026-11-25", "--record", "--ticker", "META"])
    assert e.value.code == ds.EXIT_REFUSED == 2
    assert opened == [] and not (tmp_path / "split_unplaced").exists()
    assert "REFUSED: --record" in capsys.readouterr().out


def test_without_record_a_dry_run_writes_nothing(monkeypatch, tmp_path):
    opened = _cli(monkeypatch, tmp_path, [_meta_unplaced()])
    ds.main(["--asof", "2026-11-25"])
    assert len(opened) == 1 and not (tmp_path / "split_unplaced").exists()


# --- F10 P3: a filing read with call-off wording is one ``calloffs`` row on the artifact -----------------------

GAME_8K = "0001493152-26-039213"
# the run artifact's block as the nightly wrote it before call-offs: a night that reads none writes exactly this
BLOCK_KEYS = [
    "evaluated",
    "polygon",
    "polygon_rows",
    "polygon_live_pages",
    "polygon_unlabeled",
    "checked",
    "filings_read",
    "filings_unread",
    "appended",
    "unplaced",
    "kept",
    "errors",
    "duration_s",
]
CALLOFF_KEYS = [
    "security_id",
    "ticker",
    "accession",
    "form",
    "filed",
    "url",
    "kind",
    "ratio_from",
    "ratio_to",
    "document",
    "passage",
]
# a stand-in (GAME never called its split off): P4's reader sets the real ones
GAME_CALLOFF = CallOff(
    "decision",
    Decimal(8),
    Decimal(1),
    passage="stand-in call-off sentence.",
    document="stand-in.htm",
)


def _game(sid: uuid.UUID) -> bf.Member:
    return bf.Member(
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        ticker="GAME",
        price_symbol=None,
        cik=CIKS["GAME"],
    )


def _game_8k(accession: str = GAME_8K, *, calloff: CallOff | None = GAME_CALLOFF) -> SecEvidence:
    return SecEvidence(
        accession=accession,
        form="8-K",
        filed=date(2026, 8, 19),
        url=filing_index_url(CIKS["GAME"], accession),
        ratio_from=Decimal(8),
        ratio_to=Decimal(1),
        effective_date=date(2026, 8, 24),
        date_kind="trading",
        passage="the filing's own passage, the one its reading rests on",
        document="game-8k.htm",
        about_a_split=True,
        calloff=calloff,
    )


def _game_row(sid: uuid.UUID) -> dict:
    return {
        "security_id": str(sid),
        "ticker": "GAME",
        "accession": GAME_8K,
        "form": "8-K",
        "filed": "2026-08-19",
        "url": filing_index_url(CIKS["GAME"], GAME_8K),
        "kind": "decision",
        "ratio_from": "8",
        "ratio_to": "1",
        # the call-off's own document and passage, not the reading's
        "document": "stand-in.htm",
        "passage": "stand-in call-off sentence.",
    }


def test_a_calloff_row_is_the_filing_and_its_calloff_wording_the_passage_cut_at_600():
    sid = uuid.UUID(int=3)
    row = ds.CallOffFiling.of(_game(sid), _game_8k()).to_json()
    assert list(row) == CALLOFF_KEYS and row == _game_row(sid)
    vote = CallOff("failed_vote", passage="x" * 700)
    bare = ds.CallOffFiling.of(_game(sid), _game_8k(calloff=vote)).to_json()
    assert bare["passage"] == "x" * ds.UNPLACED_PASSAGE_MAX
    assert (bare["kind"], bare["ratio_from"], bare["ratio_to"], bare["document"]) == (
        "failed_vote",
        None,
        None,
        None,
    )
    empty = ds.CallOffFiling.of(_game(sid), _game_8k(calloff=CallOff("decision"))).to_json()
    assert empty["passage"] is None


def test_every_filing_the_company_read_with_a_calloff_is_listed_once():
    """Placed or not, absorbed or not, whichever window read it: each accession once (a second reading of it,
    knowing a confirmed split, is the same filing). A filing in the pass's shared memo this company did not read
    is another company's."""
    m = _game(uuid.UUID(int=3))
    reader = bf.SecReader(object())  # never fetches here: the reads are set below
    plain = _game_8k("0001493152-26-000001", calloff=None)
    later = _game_8k("0001493152-26-000002")
    elsewhere = _game_8k("0001493152-26-000003")
    knowing = GAME_8K + ".known-0123456789"  # ``backfill_splits._known_tag``'s shape
    reader.evidence = {
        GAME_8K: _game_8k(),
        knowing: _game_8k(calloff=CallOff("failed_vote")),
        plain.accession: plain,
        later.accession: later,
        "0001493152-26-000004": None,  # unread
        elsewhere.accession: elsewhere,
    }
    reader.read_here = {GAME_8K, knowing, plain.accession, later.accession, "0001493152-26-000004"}
    got = ds.calloffs_read(m, reader)
    assert [(c.accession, c.kind) for c in got] == [
        ("0001493152-26-000002", "decision"),
        (GAME_8K, "decision"),
    ]
    assert ds.calloffs_read(m, bf.SecReader(object())) == []


def _dry(db, tmp_path, *, asof: date, night: str) -> ds.SplitDetector:
    """A dry-run night (nothing written), so two nights over one database decide alike; each pulls Massive.com
    into its own cache (``night``), so each makes the same one request."""
    source = _polygon(tmp_path / night, today=asof)
    det = ds.SplitDetector.start(asof=asof, allow_live=True, source=source, apply=False)
    ds.run_over_union(db, det, EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False))
    return det


def _block(det: ds.SplitDetector) -> dict:
    block = json.loads(json.dumps(det.result.payload()))["split_detect"]
    block["duration_s"] = 0.0  # the one value two runs never share
    return block


def test_a_detection_whose_filing_carries_a_calloff_writes_one_calloffs_row_and_nothing_else_moves(
    db, tmp_path, monkeypatch
):
    ids = _seed(db, ["GAME"])
    plain = _dry(db, tmp_path, asof=date(2026, 8, 25), night="plain")
    # no call-off read: the block the nightly always wrote, key for key
    assert list(_block(plain)) == BLOCK_KEYS and plain.result.calloffs == []
    assert plain.planned and plain.result.filings_read == 1
    real = bf.read_filing

    def read_with_calloff(client, cik, ref, **kw):
        s = real(client, cik, ref, **kw)
        return replace(s, calloff=GAME_CALLOFF) if ref.accession == GAME_8K else s

    monkeypatch.setattr(bf, "read_filing", read_with_calloff)
    det = _dry(db, tmp_path, asof=date(2026, 8, 25), night="calloff")
    block = _block(det)
    assert list(block) == BLOCK_KEYS[:10] + ["calloffs"] + BLOCK_KEYS[10:]
    assert block.pop("calloffs") == [_game_row(ids["GAME"])]
    # the call-off is no input to a decision yet: the plan, the report and the rest of the block are as they were
    assert block == _block(plain)
    assert [(p.action, p.reason, p.values) for p in det.planned] == [
        (p.action, p.reason, p.values) for p in plain.planned
    ]
    assert det.result.report_lines() == plain.result.report_lines()


def test_record_keeps_the_runs_calloffs_and_the_one_reader_reads_them(monkeypatch, tmp_path):
    sid = uuid.UUID(int=3)
    calloff = ds.CallOffFiling.of(_game(sid), _game_8k())
    _cli(monkeypatch, tmp_path, [_meta_unplaced()], [calloff])
    ds.main(["--asof", "2026-11-25", "--sec-days", "800", "--polygon-days", "800", "--record"])
    (path,) = (tmp_path / "split_unplaced").iterdir()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["calloffs"] == [_game_row(sid)] and len(doc["unplaced"]) == 1
    got = su.read_unplaced(runs_dir=tmp_path / "no_runs", records_dir=tmp_path / "split_unplaced")
    assert got.calloffs == [{**_game_row(sid), "source": "check", "seen_on": "2026-11-25"}]


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
    days back. Without the catch-all filing-date screen the nightly would have LISTED a new split on 2025-12-31
    from it — MEASURED on prod's basket 2026-10-23, the one new list row the wider item set produced. Since
    2026-11-26 that date (a tax table's, in a sentence about "the combined entity's deferred tax assets") dates
    nothing even unscreened, so neither reading lists it, and the filing is named as not placed (#9: shown,
    never dropped)."""
    seed_member(db, "AIRE", AIRE_CIK, [AIRE_8KA])
    asof = date(2026, 10, 23)
    edgar = EdgarClient(cache_dir=edgar_cache(tmp_path), allow_live=False)
    with monkeypatch.context() as m:
        m.setattr(bf, "screen_of", lambda ref: bf.SCREEN_NONE)  # the reader without any screen
        bare = ds.SplitDetector.start(
            asof=asof, allow_live=True, source=_polygon(tmp_path, today=asof), apply=False
        )
        ds.run_over_union(db, bare, edgar, tickers=["AIRE"])
    assert bare.planned == []
    assert split_repo.count_rows(db) == 0  # a dry run
    det = _run(db, tmp_path, asof=asof, tickers=["AIRE"])
    assert det.planned == [] and det.result.appended == []
    assert split_repo.count_rows(db) == 0
    (u,) = det.result.unplaced
    assert (u.ticker, u.accession, u.form) == ("AIRE", AIRE_8KA[0], "8-K/A")
    assert u.reason == (
        "1-for-25 reverse split stated, but no first-session or effective date stated"
    )


def test_an_unplaced_filing_carries_its_link_passage_and_ratio(db, tmp_path):
    """F1-1: AIRE's 8-K/A (above) on the run artifact with what the extractor read from it, so the steward reads
    the filing's own words without opening it."""
    seed_member(db, "AIRE", AIRE_CIK, [AIRE_8KA])
    det = _run(db, tmp_path, asof=date(2026, 10, 23), tickers=["AIRE"])
    (row,) = json.loads(json.dumps(det.result.payload()))["split_detect"]["unplaced"]
    assert set(row) == UNPLACED_KEYS
    assert row["url"] == filing_index_url(AIRE_CIK, AIRE_8KA[0])
    assert (row["ratio_from"], row["ratio_to"]) == ("25", "1")
    assert (row["abstain_reason"], row["date_note"]) == (None, NO_DATE)
    assert row["document"] == "EX-99.3 ea030360501ex99-3.htm"
    assert "as adjusted for the Company\u2019s 1-for-25 reverse stock split" in row["passage"]
    assert len(row["passage"]) <= ds.UNPLACED_PASSAGE_MAX


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


# --- F8-1: a competing decision the same night is never dropped silently -----------------------------------------
#
# The 800-day check's plan on prod (2026-11-26, ``split-reader-misreads-evidence/prod-o2-rerun``) held three companies
# where a later decision met the row an earlier one had claimed that night, stated another first session, and was
# dropped as "already planned tonight" without a trace: CHRN (two 8-Ks of one 1-for-15 filed a day apart, first
# sessions 05-27 and 06-02), PTN (an 8-K/A trading 08-12, then an 8-K with the legal date 09-10) and CANG (Massive.com
# and a 6-K on 07-21 against a later 6-K's legal 07-20). Each filing below states what that plan's own lines say it
# states (the filings' text is not read here); the plan, not the reader, is under test.

F8_ASOF = date(2026, 11, 26)


def _f8_sec(
    accession: str, form: str, filed: date, ratio_from: int, effective: date, kind: str = "trading"
) -> SecEvidence:
    """What the extractor read from one split filing: a 1-for-``ratio_from`` reverse split on ``effective``."""
    return SecEvidence(
        accession,
        form,
        filed,
        "u",
        ratio_from=Decimal(ratio_from),
        ratio_to=Decimal(1),
        effective_date=effective,
        date_kind=kind,
        about_a_split=True,
    )


CHRN_8K = _f8_sec("0001437749-25-017680", "8-K", date(2025, 5, 20), 15, date(2025, 5, 27))
CHRN_8K_NEXT = _f8_sec("0001437749-25-018005", "8-K", date(2025, 5, 21), 15, date(2025, 6, 2))
PTN_8KA = _f8_sec("0001654954-25-009330", "8-K/A", date(2025, 8, 11), 50, date(2025, 8, 12))
PTN_8K = _f8_sec("0001654954-25-010576", "8-K", date(2025, 9, 10), 50, date(2025, 9, 10), "legal")
CANG_ROW = PolygonEvidence(
    "E-CANG-2026-07-21", "CANG", date(2026, 7, 21), Decimal(10), Decimal(1), "reverse_split"
)
CANG_6K = _f8_sec("0001104659-26-082390", "6-K", date(2026, 7, 10), 10, date(2026, 7, 21))
CANG_6K_LATER = _f8_sec(
    "0001493152-26-041303", "6-K", date(2026, 9, 3), 10, date(2026, 7, 20), "legal"
)


def _sec_only(*filings: SecEvidence) -> list:
    """The SEC-only decisions tonight's filings make, as ``detect_security`` makes them (tape aside)."""
    return [decide_sec_only(g, asof=F8_ASOF) for g in cluster_sec(list(filings))]


def _plan(member: bf.Member, decisions, stored=()) -> list[ds.Planned]:
    return ds.plan_against(
        ds.SecurityDetection(member=member, decisions=list(decisions)),
        list(stored),
        asof=F8_ASOF,
        distribution_max_factor=None,
        label_overrides_abstain=False,
    )


def _tonight(monkeypatch, decisions) -> None:
    """Tonight's evidence for the company checked is these decisions (the reads behind them are not the test)."""
    monkeypatch.setattr(
        ds,
        "detect_security",
        lambda conn, reader, m, rows, **kw: ds.SecurityDetection(
            member=m, decisions=list(decisions)
        ),
    )


def _check_dry(monkeypatch, member: bf.Member, decisions, stored=()) -> ds.SplitDetector:
    """One company's whole ``check`` with no database: the stored list as given, a dry run."""
    _tonight(monkeypatch, decisions)
    monkeypatch.setattr(split_repo, "latest_versions", lambda conn, **kw: list(stored))
    det = ds.SplitDetector(asof=F8_ASOF, polygon_rows=[], polygon_status="on", apply=False)
    det._former = {}
    sec = ds._MasterRow(member.security_id, member.ticker, None, None)
    assert det.check(None, None, sec, tenant_id=member.tenant_id) == 0
    return det


def _without_note(values: dict) -> dict:
    return {k: v for k, v in values.items() if k != "note"}


def test_chrn_the_second_8k_is_named_on_the_new_rows_note_and_on_kept(db, monkeypatch):
    """CHRN: two candidates (4 sessions apart, past ``cluster_sec``'s 2), one row (``match_stored``'s 45 days).
    The row is the first filing's, values unchanged; the second filing is on its note, on ``kept`` and counted
    in the report line. The next night appends nothing and still lists it."""
    sid = seed_member(db, "CHRN", None, [])
    first, second = _sec_only(CHRN_8K, CHRN_8K_NEXT)
    _tonight(monkeypatch, [first, second])
    edgar = None  # tonight's decisions are given: no filing is read
    chrn = ds._MasterRow(sid, "CHRN", None, None)
    det = ds.SplitDetector(asof=F8_ASOF, polygon_rows=[], polygon_status="on")
    assert det.check(db, edgar, chrn, tenant_id=DEFAULT_TENANT_ID) == 1
    db.commit()
    new, other = det.planned
    assert (new.action, new.reason) == (ds.APPEND, "new")
    assert (other.action, other.reason, other.decision.sec) == (
        ds.KEPT,
        ds.ANOTHER_FILING,
        CHRN_8K_NEXT,
    )
    named = "Another SEC filing (8-K 0001437749-25-018005, filed 2025-05-21) states first session 2025-06-02."
    assert new.decision == replace(first, note=f"{first.note} {named}")
    (row,) = _latest(db, sid)
    assert (row.key_date, row.effective_date, row.ratio_from, row.ratio_to, row.status) == (
        date(2025, 5, 27),
        date(2025, 5, 27),
        Decimal(15),
        Decimal(1),
        STATUS_NEEDS_REVIEW,
    )
    assert _without_note(row.model_dump()) == _without_note(
        {**row.model_dump(), **split_repo.decision_values(first)}
    )
    assert row.note == new.decision.note and det.result.appended[0].note.endswith(named)
    (kept,) = det.result.kept
    assert (kept["key_date"], kept["status"], kept["reason"]) == (
        "2025-05-27",
        STATUS_NEEDS_REVIEW,
        ds.ANOTHER_FILING,
    )
    assert "0001437749-25-018005" in kept["tonight"] and "2025-06-02" in kept["tonight"]
    (line,) = det.result.report_lines()
    assert "1 listed split(s) left as decided though tonight's evidence differs" in line
    # the next night: the listed row is not re-versioned (only its note would differ), the filing still listed
    again = ds.SplitDetector(asof=F8_ASOF, polygon_rows=[], polygon_status="on")
    assert again.check(db, edgar, chrn, tenant_id=DEFAULT_TENANT_ID) == 0
    db.commit()
    assert [p.action for p in again.planned] == [ds.UNCHANGED, ds.KEPT]
    assert [k["reason"] for k in again.result.kept] == [ds.ANOTHER_FILING]
    assert split_repo.count_rows(db) == 1


def test_ptn_the_later_8k_with_its_legal_date_is_named_on_the_8ka_row():
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "PTN", None, None)
    first, second = _sec_only(PTN_8KA, PTN_8K)
    new, other = _plan(m, [first, second])
    assert (new.action, other.action, other.reason) == (ds.APPEND, ds.KEPT, ds.ANOTHER_FILING)
    assert new.decision == replace(
        first,
        note=f"{first.note} Another SEC filing (8-K 0001654954-25-010576, filed 2025-09-10) states the "
        "effective date 2025-09-10.",
    )
    assert other.decision.sec == PTN_8K and other.stored.key_date == date(2025, 8, 12)


def test_cang_a_later_6k_against_the_operators_row_is_on_kept_and_nothing_is_appended(monkeypatch):
    """CANG: tonight's first decision (Massive.com and the July 6-K, 07-21) meets the operator's row and is kept;
    the September 6-K's legal 07-20 meets the same row. Prod's row was not read: the plan's lines say it is the
    operator's, holds other values than tonight's 07-21 (that decision was KEPT) and carries no Massive.com row (the
    6-K was not re-read against one); 07-20 here. The later 6-K is weighed against tonight's first decision, which
    claimed the row: against the row's own 07-20 it would be silent."""
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "CANG", None, None)
    first = decide(CANG_ROW, [CANG_6K], asof=F8_ASOF)
    (second,) = _sec_only(CANG_6K_LATER)
    assert (first.status, first.effective_date, second.effective_date) == (
        STATUS_CONFIRMED,
        date(2026, 7, 21),
        date(2026, 7, 20),
    )
    operators = ds._as_version(
        m, replace(second, status=STATUS_CONFIRMED, confirmed_via=VIA_OPERATOR)
    ).model_copy(update={"author": split_repo.AUTHOR_OPERATOR, "action": split_repo.ACTION_CONFIRM})
    det = _check_dry(monkeypatch, m, [first, second], stored=[operators])
    assert [(p.action, p.reason) for p in det.planned] == [
        (ds.KEPT, "the operator's decision stands"),
        (ds.KEPT, ds.ANOTHER_FILING),
    ]
    assert [p.decision.sec for p in det.planned] == [CANG_6K, CANG_6K_LATER]
    assert [k["reason"] for k in det.result.kept] == [
        "the operator's decision stands",
        ds.ANOTHER_FILING,
    ]
    assert "0001493152-26-041303" in det.result.kept[1]["tonight"]


def test_a_same_value_duplicate_stays_silent(monkeypatch):
    """A second reading of the same split (same ratio, same first session) is no news: not on ``kept``, no note."""
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "CHRN", None, None)
    (first,) = _sec_only(CHRN_8K)
    restated = _f8_sec("0000000000-25-000003", "8-K/A", date(2025, 5, 22), 15, date(2025, 5, 27))
    (again,) = _sec_only(restated)
    det = _check_dry(monkeypatch, m, [first, again])
    new, dup = det.planned
    assert (new.action, new.decision) == (ds.APPEND, first)
    assert (dup.action, dup.reason) == (ds.KEPT, ds.ALREADY_PLANNED) and det.result.kept == []


def test_a_listed_open_row_is_never_re_versioned_for_a_competing_filing(monkeypatch):
    """CHRN's row as listed before F8-1 (open, the system's, no sentence): tonight's two filings append nothing."""
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "CHRN", None, None)
    first, second = _sec_only(CHRN_8K, CHRN_8K_NEXT)
    det = _check_dry(monkeypatch, m, [first, second], stored=[ds._as_version(m, first)])
    assert [(p.action, p.reason) for p in det.planned] == [
        (ds.UNCHANGED, ""),
        (ds.KEPT, ds.ANOTHER_FILING),
    ]
    assert det.planned[0].decision == first
    (kept,) = det.result.kept
    assert (kept["key_date"], kept["reason"]) == ("2025-05-27", ds.ANOTHER_FILING)


def test_a_competing_ratio_or_massive_row_is_named_in_its_own_words():
    """Another ratio on the same first session (met by key date) names it; a second Massive.com row of the same
    ratio a few sessions on (met by the 45-day window) is named as Massive.com's."""
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "TEST", None, None)
    one, other_ratio = _sec_only(
        _f8_sec("0000000000-26-000001", "8-K", date(2026, 2, 20), 20, date(2026, 3, 2)),
        _f8_sec("0000000000-26-000002", "8-K/A", date(2026, 2, 23), 25, date(2026, 3, 2)),
    )
    new, kept = _plan(m, [one, other_ratio])
    assert (kept.action, kept.reason) == (ds.KEPT, ds.ANOTHER_FILING)
    assert new.decision.note == (
        f"{one.note} Another SEC filing (8-K/A 0000000000-26-000002, filed 2026-02-23) states a 1-for-25 reverse "
        "split, first session 2026-03-02."
    )
    row_a = PolygonEvidence("E-stand-in-a", "TEST", date(2026, 3, 2), Decimal(20), Decimal(1))
    row_b = PolygonEvidence("E-stand-in-b", "TEST", date(2026, 3, 5), Decimal(20), Decimal(1))
    a, b = decide(row_a, [], asof=F8_ASOF), decide(row_b, [], asof=F8_ASOF)
    new, kept = _plan(m, [a, b])
    assert (kept.action, kept.reason) == (ds.KEPT, ds.ANOTHER_FILING)
    assert new.decision == replace(
        a, note=f"{a.note} Another Massive.com row states first session 2026-03-05."
    )


# --- F8-2 (dark, decision D-F8b): the restate rule through the detector -------------------------------------------
#
# ``split_sec_restate_days`` (0 = off, shipped): read once a pass beside D2's bound and passed to ``detect_security``,
# which passes it to the pure rule (``cluster_sec`` / ``decide_sec_only``) and never puts a candidate whose filings
# state dates more than ``SESSION_TOLERANCE`` sessions apart to the tape: the tape never picks between the company's
# own dates.


class _Filings:
    """A ``SecReader`` stand-in: tonight's split filings in the SEC window are these readings (no fetch)."""

    def __init__(self, *filings: SecEvidence) -> None:
        self.filings = list(filings)
        self.evidence = {s.accession: s for s in filings}
        self.read_here = [s.accession for s in filings]
        self.submissions: dict = {}

    def refs(self, conn, m, since, asof):
        return [s.accession for s in self.filings]

    def read(self, m, refs, *, confirmed=()):
        return list(self.filings), 0


def _tape(session: date) -> TapeEvidence:
    """CHRN's first-served tape shape (no step at the ratio): the reading only adds to a note."""
    return TapeEvidence(
        session=session,
        prior=date(2025, 5, 23),
        prior_close=4.2,
        close=4.44,
        prior_volume=57_553.0,
        volume=95_307.0,
        prior_served_before=False,
    )


def _detect(monkeypatch, *filings: SecEvidence, restate_days: int):
    """``detect_security`` over these filings (no Massive.com row): its decisions and the sessions it read."""
    read: list[date] = []

    def tape_at(conn, *, tenant_id, security_id, session):
        read.append(session)
        return _tape(session)

    monkeypatch.setattr(ds, "tape_at", tape_at)
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "CHRN", None, None)
    det = ds.detect_security(
        None,
        _Filings(*filings),
        m,
        [],
        asof=F8_ASOF,
        sec_since=date(2025, 1, 1),
        distribution_max_factor=None,
        label_overrides_abstain=False,
        confirmed=(),
        restate_days=restate_days,
    )
    return m, det, read


def test_at_0_the_detector_decides_chrn_exactly_as_before(monkeypatch):
    """Two candidates, each put to the tape (its reading on the note), F8-1's plan: one row, the other on kept."""
    m, det, read = _detect(monkeypatch, CHRN_8K, CHRN_8K_NEXT, restate_days=0)
    first, second = _sec_only(CHRN_8K, CHRN_8K_NEXT)
    assert det.decisions == [
        with_tape(first, _tape(date(2025, 5, 27))),
        with_tape(second, _tape(date(2025, 6, 2))),
    ]
    assert read == [date(2025, 5, 27), date(2025, 6, 2)]
    assert [(p.action, p.reason) for p in _plan(m, det.decisions)] == [
        (ds.APPEND, "new"),
        (ds.KEPT, ds.ANOTHER_FILING),
    ]


def test_at_30_chrn_is_one_new_row_on_the_later_8ks_first_session_never_put_to_the_tape(
    monkeypatch,
):
    m, det, read = _detect(monkeypatch, CHRN_8K, CHRN_8K_NEXT, restate_days=30)
    (d,) = det.decisions
    assert d == decide_sec_only([CHRN_8K, CHRN_8K_NEXT], asof=F8_ASOF, restate_days=30)
    assert (d.effective_date, d.sec, d.status) == (
        date(2025, 6, 2),
        CHRN_8K_NEXT,
        STATUS_NEEDS_REVIEW,
    )
    assert read == [] and "Tape as first served" not in d.note
    (new,) = _plan(m, det.decisions)
    assert (new.action, new.reason, new.decision) == (ds.APPEND, "new", d)


def test_at_30_a_single_filing_or_one_date_is_still_put_to_the_tape(monkeypatch):
    """Only a candidate whose filings disagree on the date skips the tape: one filing, or a restatement of the same
    date, reads it as before."""
    same_date = _f8_sec("0000000000-25-000003", "8-K/A", date(2025, 5, 22), 15, date(2025, 5, 27))
    _, det, read = _detect(monkeypatch, CHRN_8K, same_date, restate_days=30)
    (d,) = det.decisions
    assert d == with_tape(
        decide_sec_only([CHRN_8K, same_date], asof=F8_ASOF), _tape(date(2025, 5, 27))
    )
    assert read == [date(2025, 5, 27)]


def test_at_30_the_listed_chrn_row_moves_to_the_later_first_session(monkeypatch):
    """CHRN as listed before (the 05-27 row, open, the system's): with the rule on, tonight's one decision is an
    update of that row to 06-02 (its key date kept), nothing left on ``kept``. At 0 it stays UNCHANGED (F8-1).
    """
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "CHRN", None, None)
    first, _ = _sec_only(CHRN_8K, CHRN_8K_NEXT)
    listed = ds._as_version(m, first)
    (d,) = _detect(monkeypatch, CHRN_8K, CHRN_8K_NEXT, restate_days=30)[1].decisions
    det = _check_dry(monkeypatch, m, [d], stored=[listed])
    (p,) = det.planned
    assert (p.action, p.reason, p.stored) == (ds.APPEND, "updated", listed)
    assert (p.decision.key_date, p.decision.effective_date) == (date(2025, 5, 27), date(2025, 6, 2))
    assert det.result.kept == []


@pytest.fixture
def restate_30(monkeypatch):
    """The rule turned on as an operator would (the env the Settings read), the cache cleared both ways."""
    monkeypatch.setenv("ALPHADECK_SPLIT_SEC_RESTATE_DAYS", "30")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _count_reads(monkeypatch) -> list[int]:
    reads: list[int] = []
    real = ds.sec_restate_days

    def counted() -> int:
        reads.append(1)
        return real()

    monkeypatch.setattr(ds, "sec_restate_days", counted)
    return reads


def _check_two(monkeypatch) -> tuple[ds.SplitDetector, list[int]]:
    """A dry pass over two companies; what each ``detect_security`` call was given for the rule."""
    given: list[int] = []

    def detect(conn, reader, m, rows, **kw):
        given.append(kw["restate_days"])
        return ds.SecurityDetection(member=m)

    monkeypatch.setattr(ds, "detect_security", detect)
    monkeypatch.setattr(split_repo, "latest_versions", lambda conn, **kw: [])
    det = ds.SplitDetector(asof=F8_ASOF, polygon_rows=[], polygon_status="on", apply=False)
    det._former = {}
    for t in ("AAA", "BBB"):
        sec = ds._MasterRow(uuid.uuid4(), t, None, None)
        assert det.check(None, None, sec, tenant_id=DEFAULT_TENANT_ID) == 0
    return det, given


def test_the_restate_setting_is_read_once_a_pass_and_given_to_every_company(
    monkeypatch, restate_30
):
    reads = _count_reads(monkeypatch)
    det, given = _check_two(monkeypatch)
    assert (det.sec_restate_days, len(reads), given) == (30, 1, [30, 30])


def test_the_restate_setting_ships_off(monkeypatch):
    monkeypatch.delenv("ALPHADECK_SPLIT_SEC_RESTATE_DAYS", raising=False)
    get_settings.cache_clear()
    try:
        assert ds.sec_restate_days() == 0
        det, given = _check_two(monkeypatch)
        assert (det.sec_restate_days, given) == (0, [0, 0])
    finally:
        get_settings.cache_clear()


# --- F3b (dark, framework completion): a non-session legal date moves to the next session, through the detector ----
#
# ``split_legal_date_next_session`` (False = off, shipped): read once a pass beside the other split settings and passed
# to ``detect_security``, which passes it to ``decide_sec_only``. The tape is read on the decision's first session: on,
# Monday's bar for SKYA's Sunday-legal 8-K (as prod's plan reads it, filed 2025-04-30, 1-for-300, effective 04-27).

SKYA_8K = _f8_sec("0001641172-25-007892", "8-K", date(2025, 4, 30), 300, date(2025, 4, 27), "legal")


def _detect_next(monkeypatch, *filings: SecEvidence, next_session: bool):
    """``detect_security`` over these filings (no Massive.com row), the F3b setting as given."""
    read: list[date] = []

    def tape_at(conn, *, tenant_id, security_id, session):
        read.append(session)
        return _tape(session)

    monkeypatch.setattr(ds, "tape_at", tape_at)
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "SKYA", None, None)
    det = ds.detect_security(
        None,
        _Filings(*filings),
        m,
        [],
        asof=F8_ASOF,
        sec_since=date(2025, 1, 1),
        distribution_max_factor=None,
        label_overrides_abstain=False,
        confirmed=(),
        next_session=next_session,
    )
    return m, det, read


def test_off_the_detector_plans_skyas_sunday_legal_date_as_stated(monkeypatch):
    _, det, read = _detect_next(monkeypatch, SKYA_8K, next_session=False)
    (d,) = det.decisions
    assert d == with_tape(decide_sec_only([SKYA_8K], asof=F8_ASOF), _tape(date(2025, 4, 27)))
    assert (d.key_date, d.effective_date) == (date(2025, 4, 27), date(2025, 4, 27))
    assert read == [date(2025, 4, 27)]


def test_on_the_detector_plans_skya_on_mondays_session_and_reads_its_tape(monkeypatch):
    m, det, read = _detect_next(monkeypatch, SKYA_8K, next_session=True)
    (d,) = det.decisions
    on = decide_sec_only([SKYA_8K], asof=F8_ASOF, next_session=True)
    assert d == with_tape(on, _tape(date(2025, 4, 28)))
    assert (d.key_date, d.effective_date, d.sec, d.status) == (
        date(2025, 4, 28),
        date(2025, 4, 28),
        SKYA_8K,
        STATUS_NEEDS_REVIEW,
    )
    assert read == [date(2025, 4, 28)]
    assert "the first session is the next one, 2025-04-28, at the earliest." in d.note
    assert "Tape as first served" in d.note
    (new,) = _plan(m, det.decisions)
    assert (new.action, new.reason, new.decision) == (ds.APPEND, "new", d)


def test_on_the_listed_sunday_row_is_updated_to_mondays_session(monkeypatch):
    """SKYA as listed off (the Sunday row, open, the system's): on, tonight's decision updates that row (``match_stored``:
    the same ratio within 45 days) to the Monday, its key date kept. Off, it stays UNCHANGED."""
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "SKYA", None, None)
    (off,) = _detect_next(monkeypatch, SKYA_8K, next_session=False)[1].decisions
    listed = ds._as_version(m, off)
    (on,) = _detect_next(monkeypatch, SKYA_8K, next_session=True)[1].decisions
    det = _check_dry(monkeypatch, m, [on], stored=[listed])
    (p,) = det.planned
    assert (p.action, p.reason, p.stored) == (ds.APPEND, "updated", listed)
    assert (p.decision.key_date, p.decision.effective_date) == (
        date(2025, 4, 27),
        date(2025, 4, 28),
    )
    det = _check_dry(monkeypatch, m, [off], stored=[listed])
    assert [p.action for p in det.planned] == [ds.UNCHANGED]


def test_on_a_trading_filing_is_planned_exactly_as_off(monkeypatch):
    """A trading date is never moved, even on a non-session: the same decision and the same tape read."""
    sunday_trading = _f8_sec("0000000000-26-000061", "8-K", date(2026, 2, 20), 10, date(2026, 3, 1))
    for filings in ((CHRN_8K, CHRN_8K_NEXT), (sunday_trading,), (PTN_8KA, PTN_8K)):
        _, off, off_read = _detect_next(monkeypatch, *filings, next_session=False)
        _, on, on_read = _detect_next(monkeypatch, *filings, next_session=True)
        assert (on.decisions, on_read) == (off.decisions, off_read)


@pytest.fixture
def next_session_on(monkeypatch):
    """The rule turned on as an operator would (the env the Settings read), the cache cleared both ways."""
    monkeypatch.setenv("ALPHADECK_SPLIT_LEGAL_DATE_NEXT_SESSION", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _check_two_next(monkeypatch) -> tuple[ds.SplitDetector, list[int], list[bool]]:
    """A dry pass over two companies; how often the setting was read, and what each ``detect_security`` was given."""
    reads: list[int] = []
    real = ds.legal_date_next_session

    def counted() -> bool:
        reads.append(1)
        return real()

    monkeypatch.setattr(ds, "legal_date_next_session", counted)
    given: list[bool] = []

    def detect(conn, reader, m, rows, **kw):
        given.append(kw["next_session"])
        return ds.SecurityDetection(member=m)

    monkeypatch.setattr(ds, "detect_security", detect)
    monkeypatch.setattr(split_repo, "latest_versions", lambda conn, **kw: [])
    det = ds.SplitDetector(asof=F8_ASOF, polygon_rows=[], polygon_status="on", apply=False)
    det._former = {}
    for t in ("AAA", "BBB"):
        sec = ds._MasterRow(uuid.uuid4(), t, None, None)
        assert det.check(None, None, sec, tenant_id=DEFAULT_TENANT_ID) == 0
    return det, reads, given


def test_the_next_session_setting_is_read_once_a_pass_and_given_to_every_company(
    monkeypatch, next_session_on
):
    det, reads, given = _check_two_next(monkeypatch)
    assert (det.legal_date_next_session, len(reads), given) == (True, 1, [True, True])


def test_the_next_session_setting_ships_off(monkeypatch):
    monkeypatch.delenv("ALPHADECK_SPLIT_LEGAL_DATE_NEXT_SESSION", raising=False)
    get_settings.cache_clear()
    try:
        assert ds.legal_date_next_session() is False
        det, _, given = _check_two_next(monkeypatch)
        assert (det.legal_date_next_session, given) == (False, [False, False])
    finally:
        get_settings.cache_clear()
