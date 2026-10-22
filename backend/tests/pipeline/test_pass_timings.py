"""X5 — the pass meter (``pipeline/pass_timings.py``): per-leg wall time, request counts and peak RSS on the
run artifact. MEASUREMENT ONLY, so the headline tests prove two things: the meter NEVER changes the pass (a
meter that raises costs nothing — the calls table counts the same, the artifact still finishes), and what it
reports is the shape the REAL code produces (the real EDGAR client's cache decisions, the real ``run_daily``
loop, the real artifact writer). Durations come from an injected clock, so every number asserted is exact —
the stub legs ADVANCE the clock; nothing is fitted to an outcome.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.http import RateLimiter, polite_get
from pipeline import cron_run_log, daily, pass_timings
from pipeline import ingest_thesis as IT
from pipeline.pass_timings import per_thesis, timed
from repositories import calls_repo
from tests.pipeline import catchup_pass_stubs as harness

_ASOF = date(2026, 6, 10)
_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)


class _Clock:
    """A deterministic ``perf_counter``: it moves only when a stub leg says how long that leg took."""

    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, s: float) -> None:
        self.t += s


class _Metered:
    """``with _Metered(clock) as timer:`` — a pass meter ACTIVE for the block (what ``run_daily_pass`` does)."""

    def __init__(self, clock=None) -> None:
        self.timer = pass_timings.PassTimer(clock)

    def __enter__(self) -> pass_timings.PassTimer:
        self._token = pass_timings.activate(self.timer)
        return self.timer

    def __exit__(self, *exc) -> None:
        pass_timings.deactivate(self._token)


def _theses(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=f"id-{n}", name=n) for n in names]


# --- the model: exclusive time, scopes, counts ----------------------------------------------------------


def test_legs_are_EXCLUSIVE_scoped_per_thesis_and_sum_to_the_totals():
    """A leg timed INSIDE another (the submissions index read inside the Form 4 leg) is subtracted from the
    outer one, a thesis's legs are its own, and unclaimed time is `other` — so a thesis's legs sum to its
    total and the pass's legs sum to the pass total (nothing counted twice, nothing lost)."""
    clock = _Clock()

    def form4(index_s: float) -> None:
        clock.tick(2)
        timed("submissions")(clock.tick)(index_s)  # nested: subtracted from form4

    with _Metered(clock):
        timed("benchmarks")(clock.tick)(3)  # a pass-level leg
        for t in per_thesis(_theses("Alpha", "Beta")):
            clock.tick(1)  # unclaimed inside the thesis -> its `other`
            timed("form4")(form4)(5)
            if t.name == "Beta":
                timed("call")(clock.tick)(4)
        clock.tick(0.5)  # a pass-level gap -> `other`
        snap = pass_timings.snapshot()

    alpha, beta = snap["theses"]
    assert alpha["name"] == "Alpha" and alpha["id"] == "id-Alpha"
    assert alpha["legs"] == {"submissions": 5.0, "form4": 2.0, "other": 1.0}
    assert alpha["total_s"] == 8.0
    assert beta["legs"] == {"submissions": 5.0, "call": 4.0, "form4": 2.0, "other": 1.0}
    assert beta["total_s"] == 12.0
    assert snap["total_s"] == 23.5
    assert snap["legs"] == {
        "submissions": 10.0,
        "call": 4.0,
        "form4": 4.0,
        "benchmarks": 3.0,
        "other": 2.5,
    }
    assert list(snap["legs"]) == [
        "submissions",
        "call",
        "form4",
        "benchmarks",
        "other",
    ]  # slowest first
    assert sum(snap["legs"].values()) == snap["total_s"]
    assert sum(beta["legs"].values()) == beta["total_s"]
    assert snap["errors"] == [] and snap["v"] == pass_timings.SCHEMA_VERSION


def test_counts_land_on_the_INNERMOST_open_leg_and_merge_across_theses():
    with _Metered(_Clock()):
        pass_timings.note("sec_live")  # outside any leg -> pass-level `other`
        for _ in per_thesis(_theses("A", "B")):

            def form4() -> None:
                pass_timings.note("sec_live")
                timed("submissions")(pass_timings.note)("sec_cached")

            timed("form4")(form4)()
            pass_timings.note("rate_wait_s", 0.25)  # inside the thesis, no leg -> its `other`
        snap = pass_timings.snapshot()

    assert snap["theses"][0]["counts"] == {
        "form4": {"sec_live": 1},
        "other": {"rate_wait_s": 0.25},
        "submissions": {"sec_cached": 1},
    }
    assert snap["counts"] == {
        "form4": {"sec_live": 2},
        "other": {"rate_wait_s": 0.5, "sec_live": 1},
        "submissions": {"sec_cached": 2},
    }


def test_outside_a_pass_every_hook_is_a_NO_OP_and_the_call_is_untouched():
    assert pass_timings.current() is None
    assert timed("form4")(lambda x: x * 2)(21) == 42
    pass_timings.note("sec_live")  # nothing to count into — and no raise
    assert list(per_thesis([1, 2, 3])) == [1, 2, 3]
    assert pass_timings.snapshot() is None
    assert pass_timings.summary_line(None) is None


def test_a_leg_that_RAISES_still_raises_unchanged_and_is_still_timed():
    clock = _Clock()

    def boom() -> None:
        clock.tick(2)
        raise ValueError("the leg's own failure")

    with _Metered(clock):
        with pytest.raises(ValueError, match="the leg's own failure"):
            timed("price")(boom)()
        snap = pass_timings.snapshot()
    assert snap["legs"]["price"] == 2.0 and snap["errors"] == []


def test_a_thesis_scope_closes_on_continue_break_and_an_exception():
    clock = _Clock()
    with _Metered(clock):
        for t in per_thesis(_theses("A", "B", "C")):
            clock.tick(1)
            if t.name == "A":
                continue
            if t.name == "B":
                break
        with pytest.raises(RuntimeError):
            for _ in per_thesis(_theses("D")):
                clock.tick(2)
                raise RuntimeError("the loop died")
        snap = pass_timings.snapshot()
    assert [(t["name"], t["total_s"]) for t in snap["theses"]] == [
        ("A", 1.0),
        ("B", 1.0),
        ("D", 2.0),
    ]


# --- FAIL-OPEN: a meter that raises costs the pass nothing ----------------------------------------------


def _boom_clock():
    raise RuntimeError("clock exploded")


def test_a_meter_whose_CLOCK_raises_changes_nothing_and_records_the_fault():
    with _Metered(_boom_clock) as timer:
        assert timed("form4")(lambda: "the work's result")() == "the work's result"
        assert [t.name for t in per_thesis(_theses("A", "B"))] == ["A", "B"]
        snap = pass_timings.snapshot()
    assert snap["total_s"] is None
    assert snap["errors"] and all("clock exploded" in e for e in snap["errors"])
    assert timer.errors == snap["errors"]
    line = pass_timings.summary_line(snap)
    assert line is not None and "timing error(s) — see the run artifact" in line


def test_the_error_log_is_CAPPED():
    with _Metered(_boom_clock):
        for _ in range(50):
            timed("form4")(lambda: None)()
        snap = pass_timings.snapshot()
    assert len(snap["errors"]) == 21 and snap["errors"][-1].startswith("... further timing errors")


def test_the_REAL_run_daily_records_identically_under_a_meter_that_RAISES(db, monkeypatch):
    """THE NO-BEHAVIOR-CHANGE PROOF on the real loop: every timer operation raises (``enter`` — so every leg
    and every thesis scope fails to open), and the pass still assembles and records one call-of-record per
    thesis — COUNTED in the table — while the meter carries the faults."""
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])
    tids = [_thesis(db, "Alpha"), _thesis(db, "Beta")]

    def broken_enter(self, leg):
        raise RuntimeError(f"enter {leg} exploded")

    monkeypatch.setattr(pass_timings.PassTimer, "enter", broken_enter)
    with _Metered(_Clock()) as timer:
        results = daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert [r.recorded for r in results] == [True, True]
    assert all(r.error is None for r in results)
    assert [len(calls_repo.list_for_thesis(db, t)) for t in tids] == [1, 1]  # COUNT the table
    assert any("exploded" in e for e in timer.errors)


def test_the_REAL_run_daily_is_scoped_per_thesis_with_its_call_and_record_legs(db, monkeypatch):
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])
    _thesis(db, "Alpha")
    _thesis(db, "Beta")
    with _Metered() as timer:
        results = daily.run_daily(db, asof=_ASOF, allow_live=True)
        snap = timer.snapshot()
    assert [r.recorded for r in results] == [True, True]
    assert [t["name"] for t in snap["theses"]] == [r.name for r in results]
    for t in snap["theses"]:
        assert {"call", "record"} <= set(t["legs"]) and t["total_s"] is not None
        assert abs(sum(t["legs"].values()) - t["total_s"]) < 0.01  # rounded per leg
    assert snap["errors"] == []


def _thesis(db, name: str) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
    db.commit()
    return tid


# --- the REAL clients: what the counters count ----------------------------------------------------------


def test_the_REAL_ingest_legs_and_the_EDGAR_clients_cache_decisions(
    db, security_id, monkeypatch, tmp_path
):
    """The real ``EdgarClient`` (real cache, real recurring TTL; only ``_fetch`` stubbed) under the real
    ``ingest_thesis``: every leg is timed, and the submissions index is ONE live fetch then three cache reads
    (the 8-K leg, the 13D/G leg, the delisting read) — the property G1 pins, now visible per leg — while the
    one Form 4 document fetch counts on the form4 leg itself."""
    urls: list[str] = []

    def _fetch(url: str) -> str:
        urls.append(url)
        if "submissions" in url:
            return json.dumps(_subs("ACC-1"))
        return _XML

    client = EdgarClient(
        cache_dir=tmp_path, allow_live=True, user_agent="test ua", cache_ttl_s=RECURRING_CACHE_TTL_S
    )
    client._fetch = _fetch  # type: ignore[method-assign]
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _NoBars())
    tid = _member_thesis(db, security_id)
    with _Metered() as timer:
        results = IT.ingest_thesis(db, tid, allow_live=True, edgar_client=client)
        snap = timer.snapshot()

    assert results[0].error is None
    assert {"form4", "form8k", "schedule13", "price", "fund_shares", "submissions"} <= set(
        snap["legs"]
    )
    assert snap["counts"]["submissions"] == {"sec_cached": 3, "sec_live": 1}
    assert snap["counts"]["form4"] == {"sec_live": 1}  # the Form 4 document
    assert client.live_fetches == len(urls) == 2  # the freeze counter agrees with the meter


class _NoBars:
    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return []


def _subs(*accessions: str) -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["4"] * len(accessions),
                "accessionNumber": list(accessions),
                "primaryDocument": [f"xslF345X05/{a}.xml" for a in accessions],
                "filingDate": ["2026-05-01"] * len(accessions),
                "items": [""] * len(accessions),
            }
        }
    }


def _member_thesis(db, security_id) -> uuid.UUID:
    tid = _thesis(db, "Members")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), DEFAULT_TENANT_ID, tid, 0, "DEVCO", "—", security_id),
        )
    db.commit()
    return tid


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_polite_get_counts_every_ATTEMPT_by_host_and_its_retries_and_backoff(monkeypatch):
    replies = iter([_Resp(429), _Resp(200)])
    monkeypatch.setattr(httpx, "get", lambda url, **kw: next(replies))
    with _Metered(_Clock()):
        timed("price")(polite_get)(
            "https://query1.finance.yahoo.com/v8/finance/chart/ABC", sleep=lambda s: None
        )
        snap = pass_timings.snapshot()
    assert snap["counts"]["price"] == {
        "backoff_s": 1.0,
        "http:query1.finance.yahoo.com": 2,
        "http_429:query1.finance.yahoo.com": 1,  # X7 — the vendor's refusals, counted by status
        "http_retry:query1.finance.yahoo.com": 1,
    }


def test_the_rate_limiters_wait_is_counted_as_seconds():
    limiter = RateLimiter(max_per_sec=20.0)  # a 50 ms slot
    with _Metered(_Clock()):
        limiter.acquire()
        limiter.acquire()  # back-to-back: waits out (most of) the slot
        snap = pass_timings.snapshot()
    waited = snap["counts"]["other"]["rate_wait_s"]
    assert 0 < waited <= 0.05


# --- memory -------------------------------------------------------------------------------------------


def test_RSS_is_NULL_where_resource_is_unavailable_never_a_crash(monkeypatch):
    monkeypatch.setitem(sys.modules, "resource", None)  # `import resource` raises — Windows' shape
    assert pass_timings.peak_rss_kb() is None
    assert pass_timings.peak_rss_kb(children=True) is None
    with _Metered(_Clock()):
        for _ in per_thesis(_theses("A")):
            pass
        snap = pass_timings.snapshot()
    assert snap["peak_rss_kb"] is None and snap["peak_rss_children_kb"] is None
    assert snap["theses"][0]["peak_rss_kb"] is None
    assert pass_timings.summary_line(snap).endswith("peak RSS n/a")


@pytest.mark.skipif(sys.platform == "win32", reason="getrusage is POSIX-only")
def test_RSS_is_the_processes_real_peak_on_POSIX():
    peak = pass_timings.peak_rss_kb()
    assert isinstance(peak, int) and peak > 0


# --- the printed report ---------------------------------------------------------------------------------


def test_the_summary_line_names_the_total_top_three_legs_slowest_thesis_and_peak():
    timings = {
        "total_s": 846.94,
        "legs": {
            "form4": 300.06,
            "submissions": 200.3,
            "price": 100.24,
            "call": 50.0,
            "other": 1.0,
        },
        "theses": [
            {"name": "Rainbow Rush", "total_s": 106.8},
            {"name": "AI Memory & Storage", "total_s": 403.2},
            {"name": "never finished", "total_s": None},
        ],
        "peak_rss_kb": 512000,
        "errors": [],
    }
    assert pass_timings.summary_line(timings) == (
        "timings: 846.9 s total · top legs form4 300.1 s, submissions 200.3 s, price 100.2 s · "
        "slowest thesis AI Memory & Storage 403.2 s · peak RSS 500 MB"
    )
    assert pass_timings.summary_line({"total_s": None}) == (
        "timings: n/a total · top legs n/a · slowest thesis n/a · peak RSS n/a"
    )


def test_main_prints_ONE_timings_line_after_the_report(monkeypatch, capsys):
    monkeypatch.setattr(
        daily, "acquire_pass_lock", lambda **kw: SimpleNamespace(release=lambda: None)
    )
    timings = {
        "total_s": 12.0,
        "legs": {"call": 7.0, "other": 5.0},
        "theses": [],
        "peak_rss_kb": None,
    }
    at = datetime(2026, 10, 19, 2, 30, tzinfo=timezone.utc)
    outcome = daily.DailyPassOutcome(
        results=[],
        asof=_ASOF,
        allow_live=True,
        started_at=at,
        finished_at=at,
        log_path=None,
        timings=timings,
    )
    monkeypatch.setattr(daily, "run_daily_pass", lambda **kw: outcome)
    daily.main(["--no-live"])
    lines = capsys.readouterr().out.splitlines()
    assert [ln for ln in lines if ln.startswith("timings:")] == [
        "timings: 12.0 s total · top legs call 7.0 s, other 5.0 s · slowest thesis n/a · peak RSS n/a"
    ]


# --- the artifact: through the REAL pass and the REAL writer ----------------------------------------------


def _artifact(home: Path) -> dict:
    (path,) = sorted(home.glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_artifact_carries_the_FINAL_timings_block_and_the_running_file_its_CHECKPOINTS(
    monkeypatch, tmp_path
):
    """One stubbed pass through the REAL ``run_daily_pass`` and the REAL artifact writer, with stub legs that
    advance a deterministic clock: the finished artifact's ``timings`` is the FINAL block — the post-artifact
    legs (the shell sweep, the SPAC radar) included, which ran after the artifact was first written — and
    while the loop ran, the START artifact (still ``running``) carried the checkpoint after each thesis.
    """
    home = tmp_path / "runs"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", home)
    pager = harness.prime_pass(monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(pass_timings, "perf_counter", clock)
    checkpoints: list[dict] = []

    def run_daily(conn, **k):
        out = []
        for t in per_thesis(_theses("Alpha", "Beta")):
            if t.name == "Beta":
                checkpoints.append(_artifact(home))  # the start file, mid-loop
            timed("form4")(clock.tick)(3 if t.name == "Alpha" else 5)
            timed("call")(clock.tick)(1)
            out.append(daily.ThesisRunResult(thesis_id=uuid.uuid4(), name=t.name, recorded=False))
        return out

    monkeypatch.setattr(daily, "run_daily", run_daily)
    monkeypatch.setattr(
        "radar.spac.run_spac_radar",
        lambda conn, **k: (clock.tick(7), SimpleNamespace(summary="stub", errors=[]))[1],
    )
    monkeypatch.setattr(
        "radar.shell_sweep.run_shell_sweep",
        lambda conn, **k: (clock.tick(2), SimpleNamespace(summary="stub", errors=[]))[1],
    )
    outcome = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=pager)

    (mid,) = checkpoints
    assert mid["status"] == "running"
    assert [(t["name"], t["total_s"]) for t in mid["timings"]["theses"]] == [("Alpha", 4.0)]

    doc = _artifact(home)
    assert doc["status"] == "finished"
    t = doc["timings"]
    assert t == outcome.timings  # the artifact and the Admin poll payload read the same block
    assert t["total_s"] == 3 + 1 + 5 + 1 + 7 + 2 == 19.0
    assert t["legs"]["form4"] == 8.0 and t["legs"]["call"] == 2.0
    assert t["legs"]["spac_radar"] == 7.0 and t["legs"]["shell_sweep"] == 2.0  # post-artifact legs
    assert {"benchmarks", "baselines", "artifact_write"} <= set(t["legs"])  # pass-level legs ran
    assert [(x["name"], x["total_s"], x["legs"]) for x in t["theses"]] == [
        ("Alpha", 4.0, {"form4": 3.0, "call": 1.0, "other": 0.0}),
        ("Beta", 6.0, {"form4": 5.0, "call": 1.0, "other": 0.0}),
    ]
    assert sum(t["legs"].values()) == t["total_s"] and t["errors"] == []
    assert {"peak_rss_kb", "peak_rss_children_kb", "rss_kb"} <= set(t)
    # measurement only: the rest of the artifact is the pass's, unchanged
    assert doc["summary"]["theses"] == 2 and doc["mode"] == "live"


def test_a_pass_whose_meter_RAISES_still_finishes_its_artifact_with_the_error_marker(
    monkeypatch, tmp_path
):
    home = tmp_path / "runs"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", home)
    pager = harness.prime_pass(monkeypatch)

    def broken_enter(self, leg):
        raise RuntimeError(f"enter {leg} exploded")

    monkeypatch.setattr(pass_timings.PassTimer, "enter", broken_enter)
    # one fetch: a healthy night, so nothing pages and any page would be the meter's doing
    roster = [
        daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="Alpha", recorded=True, edgar_fetches=1)
    ]

    def run_daily(conn, **k):
        return [r for r in per_thesis(roster) if timed("call")(lambda: True)()]

    monkeypatch.setattr(daily, "run_daily", run_daily)
    outcome = daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=pager)

    assert outcome.results == roster and not daily._failed_to_record(outcome.results)
    doc = _artifact(home)
    assert doc["status"] == "finished" and doc["summary"]["appended"] == 1
    assert any("exploded" in e for e in doc["timings"]["errors"])  # the error marker
    assert pager.health == []  # nothing paged about the meter


def test_a_CRASHED_pass_keeps_the_timings_up_to_the_crash(monkeypatch, tmp_path):
    home = tmp_path / "runs"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", home)
    pager = harness.prime_pass(monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(pass_timings, "perf_counter", clock)

    def run_daily(conn, **k):
        for t in per_thesis(_theses("Alpha", "Beta")):
            timed("form4")(clock.tick)(3)
            if t.name == "Beta":
                raise RuntimeError("the pass died mid-loop")
        return []

    monkeypatch.setattr(daily, "run_daily", run_daily)
    with pytest.raises(RuntimeError, match="died mid-loop"):
        daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=pager)

    doc = _artifact(home)
    assert doc["status"] == "crashed"
    alpha, beta = doc["timings"]["theses"]
    assert (alpha["name"], alpha["total_s"], alpha["legs"]["form4"]) == ("Alpha", 3.0, 3.0)
    assert beta["name"] == "Beta"  # the thesis the pass died in is named, with what it had done


# --- the artifact writer's in-place timings write ------------------------------------------------------


def test_amend_rewrites_ONLY_this_passes_artifact_in_the_expected_status(tmp_path):
    at = datetime(2026, 10, 19, 2, 30, 1, tzinfo=timezone.utc)
    path = cron_run_log.write_run_start(
        asof=_ASOF, allow_live=True, started_at=at, base_dir=tmp_path
    )
    assert path is not None
    t1 = {"v": 1, "total_s": 1.0}

    # a finished-only amend never touches a running file (and vice versa)
    assert cron_run_log.amend_run_timings(path, started_at=at, timings=t1) is None
    assert "timings" not in json.loads(path.read_text(encoding="utf-8"))
    # another pass's started_at never matches
    other = datetime(2026, 10, 18, 2, 30, 1, tzinfo=timezone.utc)
    assert (
        cron_run_log.amend_run_timings(path, started_at=other, timings=t1, status="running") is None
    )
    # the checkpoint: running, this pass -> written in place, nothing else changed
    before = json.loads(path.read_text(encoding="utf-8"))
    assert cron_run_log.amend_run_timings(path, started_at=at, timings=t1, status="running") == path
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after == {**before, "timings": t1}
    # a crash keeps the last checkpoint when no newer timings are given
    cron_run_log.mark_run_crashed(started_at=at, crashed_at=at, error="boom", base_dir=tmp_path)
    crashed = json.loads(path.read_text(encoding="utf-8"))
    assert crashed["status"] == "crashed" and crashed["timings"] == t1
    # ...and a crashed file is never re-opened by a checkpoint
    t2 = {"v": 1, "total_s": 2.0}
    assert cron_run_log.amend_run_timings(path, started_at=at, timings=t2, status="running") is None
    # a missing file is a None, never a raise
    assert cron_run_log.amend_run_timings(tmp_path / "nope.json", started_at=at, timings=t2) is None


def test_the_payload_carries_timings_and_NULL_when_unmeasured():
    at = datetime(2026, 10, 19, 2, 30, tzinfo=timezone.utc)
    kw = {"asof": _ASOF, "allow_live": True, "started_at": at, "finished_at": at}
    assert cron_run_log.build_run_payload([], **kw)["timings"] is None
    t = {"v": 1, "total_s": 3.0}
    assert cron_run_log.build_run_payload([], **kw, timings=t)["timings"] == t
