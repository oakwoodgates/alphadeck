"""T4 on the Admin surface — ``/admin/status``'s split-basis panel and the run row's page line, read from
run artifacts written through the REAL writer (``write_cron_run_log``) into the conftest-redirected tmp home.

The operator's rule, the same one the stale-tape panel follows: a split-basis defect is a FEED problem
(flag only — nothing reaches the call), so it shows on the panel and on the run row that paged it, while the
one-word cron verdict stays about the CRON."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

from app.routers import admin
from db.session import DEFAULT_TENANT_ID
from pipeline import daily
from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult
from pipeline.split_basis import FLAP, ISLAND, SettledDefect, SplitDefect, SplitScan

_MON = date(2026, 7, 20)


def _tr(**kw) -> ThesisRunResult:
    return ThesisRunResult(thesis_id=uuid.uuid4(), name="T", **kw)


def _artifact(*, at: datetime, split: SplitScan | None, allow_live: bool = True):
    path = write_cron_run_log(
        [_tr(recorded=True, edgar_fetches=88)],
        asof=_MON,
        allow_live=allow_live,
        started_at=at,
        finished_at=at + timedelta(minutes=2),
        split=split,
    )
    assert path is not None
    return path


def _vwav(sid=None) -> SplitDefect:
    return SplitDefect(
        sid or uuid.uuid4(), "VWAV", ISLAND, (date(2026, 9, 15), date(2026, 9, 16)), 18.02, 2
    )


def test_status_split_panel_lists_the_inventory_and_what_PAGED(client, cron_runs_dir):
    known = SplitDefect(uuid.uuid4(), "MPU", FLAP, (date(2025, 8, 12),), 20.0, 274)
    new = _vwav()
    _artifact(
        at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc),
        split=SplitScan(evaluated=True, defects=(known, new), new=(new,), window_days=430),
    )

    panel = client.get("/admin/status").json()["split_defects"]

    assert panel["asof"] == _MON.isoformat() and panel["window_days"] == 430
    rows = {r["ticker"]: r for r in panel["defects"]}
    assert rows["VWAV"]["kind"] == "island" and rows["VWAV"]["dates"] == [
        "2026-09-15",
        "2026-09-16",
    ]
    assert rows["VWAV"]["factor"] == 18.02 and rows["VWAV"]["events"] == 2
    assert rows["MPU"]["kind"] == "flap" and rows["MPU"]["events"] == 274
    assert panel["newly_flagged"] == [new.label] and panel["error"] is None


def test_SETTLED_flaps_are_NAMED_on_the_panel_never_a_defect_never_on_the_run_row(
    client, cron_runs_dir
):
    """A settled flap rides its own quiet list (with how it was judged): not in ``defects`` (the loud
    block), not in ``newly_flagged`` (it never paged), and no line on the run row."""
    mpu = SplitDefect(uuid.uuid4(), "MPU", FLAP, (date(2026, 9, 14),), 20.0, 270)
    reason = (
        "the stored history changed basis and back, but every night's view reads a single basis"
    )
    _artifact(
        at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc),
        split=SplitScan(evaluated=True, settled=(SettledDefect(mpu, reason),), window_days=430),
    )

    body = client.get("/admin/status").json()
    panel = body["split_defects"]

    assert panel["defects"] == [] and panel["newly_flagged"] == []
    (row,) = panel["settled"]
    assert row["ticker"] == "MPU" and row["kind"] == "flap" and row["events"] == 270
    assert row["dates"] == ["2026-09-14"] and row["reason"] == reason
    run = client.get("/admin/runs").json()["runs"][0]
    assert run["split_defects_new"] == [] and not any("split" in p for p in run["problems"])


def test_an_artifact_from_BEFORE_the_settle_check_reads_nothing_settled(client, cron_runs_dir):
    path = _artifact(
        at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc),
        split=SplitScan(evaluated=True, defects=(_vwav(),), window_days=430),
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps({k: v for k, v in doc.items() if not k.startswith("split_settle")}),
        encoding="utf-8",
    )
    panel = client.get("/admin/status").json()["split_defects"]
    assert panel["settled"] == [] and [r["ticker"] for r in panel["defects"]] == ["VWAV"]


def test_a_NEW_split_defect_pages_on_the_run_row_but_the_cron_stays_HEALTHY(
    client, db, monkeypatch, cron_runs_dir
):
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'T', 'n')",
            (uuid.uuid4(), DEFAULT_TENANT_ID),
        )
    db.commit()
    daily.run_daily(db, asof=_MON, allow_live=True)  # edge = Monday (current)
    new = _vwav()
    _artifact(
        at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc),
        split=SplitScan(evaluated=True, defects=(new,), new=(new,), window_days=430),
    )
    monkeypatch.setattr(admin, "_now", lambda: datetime(2026, 7, 20, 23, 0))

    body = client.get("/admin/status").json()

    assert body["last_run"]["healthy"] is False  # the assessor DID page…
    assert body["last_run"]["split_defects_new"] == [new.label]
    assert any(
        "NEW split-basis defect" in p and "not an error" in p for p in body["last_run"]["problems"]
    )
    assert body["cron"]["status"] == "healthy"  # …and the verdict stays about the cron
    assert "split" not in body["cron"]["detail"]


def test_status_split_panel_is_NULL_until_a_pass_has_SCANNED(client, cron_runs_dir):
    _artifact(at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc), split=None)  # not evaluated
    _artifact(  # a cache-only pass never scans, and never counts even if it claimed to
        at=datetime(2026, 7, 20, 23, 0, tzinfo=timezone.utc),
        split=SplitScan(evaluated=True, defects=(_vwav(),)),
        allow_live=False,
    )
    assert client.get("/admin/status").json()["split_defects"] is None


def test_a_FAILED_scan_shows_its_error_over_the_last_good_inventory(client, cron_runs_dir):
    good = _vwav()
    _artifact(
        at=datetime(2026, 7, 17, 22, 30, tzinfo=timezone.utc),
        split=SplitScan(evaluated=True, defects=(good,), window_days=430),
    )
    _artifact(
        at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc),
        split=SplitScan(error="OperationalError: server closed the connection", window_days=430),
    )

    panel = client.get("/admin/status").json()["split_defects"]

    assert panel["error"] == "OperationalError: server closed the connection"
    assert [r["ticker"] for r in panel["defects"]] == ["VWAV"]  # the last good inventory…
    assert panel["ran_at"].startswith("2026-07-17")  # …and the pass it came from


def test_a_PRE_T4_artifact_still_parses_and_carries_no_split_line(client, cron_runs_dir):
    path = _artifact(at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc), split=None)
    doc = json.loads(path.read_text(encoding="utf-8"))
    pre_t4 = {k: v for k, v in doc.items() if not k.startswith("split_")}  # written before the keys
    assert len(pre_t4) < len(doc)
    path.write_text(json.dumps(pre_t4), encoding="utf-8")

    body = client.get("/admin/status").json()
    run = client.get("/admin/runs").json()["runs"][0]

    assert body["split_defects"] is None  # never scanned — the quiet state, not an alarm
    assert run["split_defects_new"] == [] and not any("split" in p for p in run["problems"])


def test_the_panel_reads_a_PARTIAL_catch_up_the_same_pass_the_next_diff_reads(
    client, cron_runs_dir
):
    """A --catch-up that SKIPPED a thesis (M2) scanned the whole basket union, so its split inventory is
    complete: the panel shows it (not the older full pass), exactly as ``previous_split_defects`` makes it
    the next pass's baseline — the panel and the page never disagree about which pass was last."""
    _artifact(
        at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc),
        split=SplitScan(evaluated=True, defects=(), window_days=430),
    )
    seen = _vwav()
    path = write_cron_run_log(
        [_tr(skipped="on-time call-of-record already recorded"), _tr(recorded=False)],
        asof=_MON,
        allow_live=True,
        started_at=datetime(2026, 7, 21, 13, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 21, 13, 5, tzinfo=timezone.utc),
        catch_up=True,
        split=SplitScan(evaluated=True, defects=(seen,), new=(seen,), window_days=430),
    )
    assert path is not None

    panel = client.get("/admin/status").json()["split_defects"]

    assert panel["ran_at"].startswith("2026-07-21") and panel["newly_flagged"] == [seen.label]
    assert [r["ticker"] for r in panel["defects"]] == ["VWAV"]
