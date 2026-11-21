"""V2 W3c: the nightly pass's platform-extraction leg (``pipeline/nightly_extraction.py``, called LAST in
``pipeline.daily._run_daily_pass_locked``), its block in the run artifact, and the budget rule.

The pass runs for real against the test database: the REAL per-thesis loop (``run_daily``, ``record_if_changed``,
the commit), the REAL artifact writer (its home redirected to tmp). Every other leg is stubbed exactly as in
``tests/pipeline/test_daily.py`` (``_prime_pass``). No SEC request anywhere: the extraction is monkeypatched at
``platform_values.extraction`` (the submissions read and ``extract_with_annual_fallback``), the EDGAR cache home
points at an empty tmp directory, and ``EdgarClient._fetch`` raises if anything ever tries the network.

What is pinned (approved B2: the step runs LAST inside the pass, budgeted against the pass's limit):
- the finished artifact carries a ``platform_extraction`` block (counts, ``admin_line``, ``attention``);
- THE CALL OF RECORD DOES NOT MOVE: with the step on, off (the kill switch), raising, failing per company or out
  of time, the recorded ``calls`` rows and the pass's exit status are exactly those of the pass without the step;
- the step never runs before the last thesis's record is written AND committed;
- no-live -> ``skipped: no-live``; the kill switch -> ``off``; no time left -> ``no time left`` with the due count;
- the budget is the smaller of the setting and the pass's limit minus the time spent minus a margin; it never starts
  a company it cannot finish; what it could not do is deferred and taken the next night (a fake clock);
- nothing the step writes is read by the call path (a source check).
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from db.session import connect
from domain.extraction import ExtractionResult
from domain.market_time import market_now
from domain.settings import get_settings
from ingest.edgar import client as edgar_client
from pipeline import cron_run_log, daily
from platform_values import extraction as ex
from repositories import calls_repo
from repositories import platform_extraction_repo as attempts_repo
from tests.pipeline.test_daily import _prime_pass, _Silent
from tests.platform_values.test_extraction_run import (
    _card,
    _cash,
    _company,
    _filing,
    _old,
    _shares,
    _subs,
)
from tests.workspaces import new_workspace

_ASOF = date(2026, 6, 10)
_REAL_RUN_DAILY = daily.run_daily
_BACKEND = Path(__file__).resolve().parents[2]


class _FakeLock:
    def release(self) -> None:
        pass


def _no_sec(self, url: str) -> str:  # pragma: no cover - reaching it is the failure
    raise AssertionError(f"a test reached the SEC network: {url}")


def _now() -> datetime:
    return market_now().astimezone(timezone.utc)


def _key(cik: str, acc: str) -> str:
    return f"{cik}|10-Q:{acc}|10-K:|20-F:|40-F:"


class Extractor:
    """Stands in for ``extract_with_annual_fallback``: an AUTO share count and cash/burn per CIK (or an exception),
    and every call recorded in ``events``."""

    def __init__(self, events: list, raises: BaseException | None = None) -> None:
        self.events = events
        self.raises = raises
        self.calls: list[int] = []
        self.on_call = None

    def __call__(self, client, cik, *, values_only: bool = False, **_: Any) -> ExtractionResult:
        self.calls.append(int(cik))
        self.events.append(("extract", int(cik)))
        if self.on_call is not None:
            self.on_call()
        if self.raises is not None:
            raise self.raises
        ref = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/q/d.htm"
        return ExtractionResult(facts=[_shares(ref), _cash(ref)])


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    """Two live cards (two workspaces) on one company that is due (a backfill attempt failed on its 10-Q), a real
    pass with every other leg stubbed, the artifact home in tmp, and no way to reach SEC."""
    runs = tmp_path / "cron_runs"
    _prime_pass(monkeypatch)
    monkeypatch.setattr(daily, "connect", connect)  # the REAL database for the loop and the leg
    monkeypatch.setattr(daily, "run_daily", _REAL_RUN_DAILY)  # the REAL per-thesis loop and record
    monkeypatch.setattr(
        daily, "ingest_thesis", lambda *a, **k: []
    )  # facts are set here, not fetched
    monkeypatch.setattr(daily, "write_cron_run_log", cron_run_log.write_cron_run_log)
    monkeypatch.setattr(daily, "acquire_pass_lock", lambda **kw: _FakeLock())
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", runs)
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", tmp_path / "edgar")
    monkeypatch.setattr(edgar_client.EdgarClient, "_fetch", _no_sec)

    sid = _company(db, "ACME", "1001")
    cards = [
        _card(db, new_workspace(db, "A"), [(sid, None)]),
        _card(db, new_workspace(db, "B"), [(sid, None)]),
    ]
    acc = "0001001-26-000001"
    _filing(db, sid, "10-Q", acc, _old())
    db.commit()
    monkeypatch.setattr(ex, "fetch_submissions", lambda client, cik: _subs([("10-Q", acc, _old())]))
    events: list = []
    fake = Extractor(events)
    monkeypatch.setattr(ex, "extract_with_annual_fallback", fake)
    w = {"sid": sid, "cards": cards, "acc": acc, "runs": runs, "events": events, "fake": fake}
    _seed_backfill(db, w)
    return w


def _seed_backfill(db, w) -> None:
    """One failed backfill attempt: the company is due (``retry``). It does NOT satisfy B4 (no ``done`` backfill
    attempt), which this world never needs: its one company is already attempted."""
    attempts_repo.append_attempt(
        db,
        security_id=w["sid"],
        run_kind="backfill",
        inputs_key=_key("1001", w["acc"]),
        status="error",
        error="not cached",
    )
    db.commit()


def _artifact(runs: Path) -> dict:
    (path,) = sorted(runs.glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def _pass(*, allow_live: bool = True) -> daily.DailyPassOutcome:
    return daily.run_daily_pass(asof=_ASOF, allow_live=allow_live, notifier=_Silent())


def _exit_code() -> int:
    """The CLI's exit status for tonight's pass (``main`` raises ``SystemExit(1)`` only for a night with no call)."""
    try:
        daily.main(["--asof", _ASOF.isoformat(), "--late-ok", "--run-kind", "cron"])
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _calls(db) -> list[dict]:
    """Every recorded call row, minus the bookkeeping that differs between two identical passes."""
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM calls ORDER BY thesis_id, seq")
        rows = cur.fetchall()
    db.rollback()
    return [{k: v for k, v in r.items() if k not in ("id", "seq", "recorded_at")} for r in rows]


def _count(db, table: str) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed names
        n = cur.fetchone()["n"]
    db.rollback()
    return n


# --- the artifact block (the first test: RED before the leg existed) ---------------------------------------------


def test_the_finished_artifact_carries_the_platform_extraction_block(world):
    out = _pass()

    doc = _artifact(world["runs"])
    assert doc["status"] == "finished"  # the block never re-opens or changes the pass's own record
    block = doc["platform_extraction"]
    assert block["status"] == "ran" and block["run_kind"] == "nightly"
    assert (block["attempted"], block["companies"]["applied"], block["deferred"]) == (1, 1, 0)
    assert block["due_by_reason"] == {"retry": 1}
    assert block["attention"] is False
    assert block["admin_line"].startswith("Shared facts · ")
    assert "1 extracted: 1 applied, 0 to the steward, 0 nothing found" in block["admin_line"]
    assert out.platform_extraction == block
    assert world["fake"].calls == [1001]


# --- THE CALL OF RECORD DOES NOT MOVE ----------------------------------------------------------------------------


def _without_the_step(monkeypatch) -> None:
    monkeypatch.setattr(daily, "_platform_extraction_leg", lambda **kw: None)


def test_the_recorded_calls_and_the_exit_status_are_those_of_the_pass_without_the_step(
    db, world, monkeypatch
):
    """Each mode starts from the same database (the calls, the platform values and the attempts reset; the backfill
    attempt re-seeded), runs the CLI's pass, and must record exactly the rows and exit with exactly the status of
    the pass with the step removed."""
    from pipeline import nightly_extraction as leg

    def reset() -> None:
        db.rollback()
        with db.cursor() as cur:
            cur.execute(
                "TRUNCATE calls, fact_shares_outstanding, fact_cash_burn, extraction_candidate, "
                "platform_extraction_attempt CASCADE"
            )
        db.commit()
        _seed_backfill(db, world)
        for p in world["runs"].glob("*.json"):
            p.unlink()

    def run(mode: str) -> tuple[list[dict], int, str | None, tuple[int, int]]:
        reset()
        with monkeypatch.context() as m:
            if mode == "without":
                _without_the_step(m)
            elif mode == "kill switch":
                m.setenv("ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES", "0")
                get_settings.cache_clear()
            elif mode == "raises":

                def boom(*a, **k):
                    raise RuntimeError("an unexpected fault in the step")

                m.setattr(leg, "work_list", boom)
            elif mode == "fails per company":
                m.setattr(world["fake"], "raises", httpx.ReadTimeout("SEC read timed out"))
            elif mode == "no time left":
                m.setattr(leg, "_now", lambda: _now() + timedelta(hours=1, minutes=25))
            code = _exit_code()
            get_settings.cache_clear()
        block = _artifact(world["runs"]).get("platform_extraction")
        wrote = (_count(db, "fact_shares_outstanding"), _count(db, "platform_extraction_attempt"))
        return _calls(db), code, (block or {}).get("status"), wrote

    base_calls, base_code, base_status, base_wrote = run("without")
    assert len(base_calls) == 2 and base_code == 0 and base_status is None
    assert base_wrote == (0, 1)  # the re-seeded backfill attempt only
    # mode -> (the block's status, (share-count facts, attempts) after the pass)
    expected = {
        "on": ("ran", (1, 2)),  # it really extracted: one value applied, one done attempt
        "kill switch": ("off", (0, 1)),
        "raises": ("error", (0, 1)),
        "fails per company": ("ran", (0, 2)),  # an error attempt, no value
        "no time left": ("no time left", (0, 1)),
    }
    for mode, (status, wrote) in expected.items():
        calls, code, got, did = run(mode)
        assert (got, code, did) == (status, base_code, wrote), mode
        assert calls == base_calls, mode


def test_the_step_runs_only_after_the_last_thesis_is_recorded_and_committed(db, world, monkeypatch):
    events = world["events"]
    real = calls_repo.record_if_changed

    def record(conn, card, *a, **k):
        out = real(conn, card, *a, **k)
        events.append(("record", card.thesis_id))
        return out

    monkeypatch.setattr(calls_repo, "record_if_changed", record)
    seen: dict = {}

    def at_extraction() -> None:
        with connect() as other:  # ANOTHER connection: sees only what is committed
            seen["calls"] = other.execute("SELECT count(*) AS n FROM calls").fetchone()["n"]
        seen["artifact"] = _artifact(world["runs"])["status"]

    world["fake"].on_call = at_extraction

    _pass()

    kinds = [e[0] for e in events]
    assert kinds.count("record") == 2 and kinds.count("extract") == 1
    assert max(i for i, k in enumerate(kinds) if k == "record") < kinds.index("extract")
    assert seen == {"calls": 2, "artifact": "finished"}  # both calls committed, the artifact final


# --- the other states --------------------------------------------------------------------------------------------


def test_a_no_live_pass_skips_the_step(world):
    _pass(allow_live=False)
    block = _artifact(world["runs"])["platform_extraction"]
    assert block["status"] == "skipped: no-live" and block["attention"] is False
    assert "skipped" in block["admin_line"]
    assert world["fake"].calls == []


def test_the_kill_switch_is_off_and_touches_nothing(db, world, monkeypatch):
    monkeypatch.setenv("ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES", "0")
    get_settings.cache_clear()
    try:
        _pass()
    finally:
        get_settings.cache_clear()
    block = _artifact(world["runs"])["platform_extraction"]
    assert block["status"] == "off" and "off" in block["admin_line"]
    assert world["fake"].calls == [] and _count(db, "platform_extraction_attempt") == 1


def test_a_raising_step_is_an_error_block_and_the_pass_returns_normally(world, monkeypatch):
    from pipeline import nightly_extraction as leg

    def boom(*a, **k):
        raise RuntimeError("an unexpected fault in the step")

    monkeypatch.setattr(leg, "work_list", boom)
    out = _pass()  # must not raise
    doc = _artifact(world["runs"])
    assert doc["status"] == "finished"
    block = doc["platform_extraction"]
    assert block["status"] == "error" and block["attention"] is True
    assert "did not run" in block["admin_line"] and "RuntimeError" in block["admin_line"]
    assert out.results and all(r.recorded for r in out.results)


def test_no_time_left_counts_the_due_companies_and_fetches_nothing(world, monkeypatch):
    from pipeline import nightly_extraction as leg

    # 85 minutes into a 90-minute pass: 5400 - 5100 - 600 < 0
    monkeypatch.setattr(leg, "_now", lambda: _now() + timedelta(minutes=85))
    _pass()
    block = _artifact(world["runs"])["platform_extraction"]
    assert block["status"] == "no time left"
    assert (block["due_total"], block["deferred"], block["attempted"]) == (1, 1, 0)
    assert block["budget"]["budget_s"] == 0.0
    assert "no time left" in block["admin_line"] and "1 due" in block["admin_line"]
    assert world["fake"].calls == []


def test_a_held_back_backlog_is_loud(db, world):
    """Approved B4: before a backfill extracts a company, a nightly takes no never-extracted company; the block
    counts them and the line says so loudly (the backfill is owed)."""
    db.rollback()
    with db.cursor() as cur:
        cur.execute("TRUNCATE platform_extraction_attempt")
    db.commit()
    _pass()
    block = _artifact(world["runs"])["platform_extraction"]
    assert (block["status"], block["attempted"], block["withheld_never"]) == ("ran", 0, 1)
    assert block["attention"] is True
    assert "1 never extracted, held until a backfill extracts a company" in block["admin_line"]
    assert world["fake"].calls == []


# --- the budget ---------------------------------------------------------------------------------------------------


def test_the_budget_is_the_smaller_of_the_setting_and_the_pass_limit_minus_time_spent_and_margin():
    from pipeline.nightly_extraction import MARGIN_S, leg_budget

    assert MARGIN_S == 600.0
    # an ordinary night: 10 minutes into the pass, the setting (900 s) is the smaller
    b = leg_budget(setting_s=900.0, pass_timeout_s=5400.0, elapsed_s=600.0)
    assert (b.remaining_s, b.budget_s) == (4200.0, 900.0)
    # a slow night: 70 minutes in, 5400 - 4200 - 600 = 600 s is the smaller
    b = leg_budget(setting_s=900.0, pass_timeout_s=5400.0, elapsed_s=4200.0)
    assert (b.remaining_s, b.budget_s) == (600.0, 600.0)
    # past the margin: nothing
    b = leg_budget(setting_s=900.0, pass_timeout_s=5400.0, elapsed_s=4900.0)
    assert (b.remaining_s, b.budget_s) == (-100.0, 0.0)


def test_with_a_fake_clock_it_never_starts_a_company_it_cannot_finish_and_the_rest_waits_a_night(
    db, monkeypatch, tmp_path
):
    """Three due companies, 250 s each on a fake clock. The pass started 4,200 s ago (fake wall clock): the budget is
    min(900, 5400 - 4200 - 600) = 600 s. Company 1 starts at 0 and ends at 250; company 2 starts at 250 (250 + 250 =
    500 <= 600) and ends at 500; company 3 would start at 500 and end at 750 > 600: deferred. The next night (the
    pass 10 minutes in, a 900 s budget) takes exactly that one."""
    from pipeline import nightly_extraction as leg

    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", tmp_path / "edgar")
    monkeypatch.setattr(edgar_client.EdgarClient, "_fetch", _no_sec)
    w = new_workspace(db, "W")
    sids = [_company(db, t, c) for t, c in (("AAA", "21"), ("BBB", "22"), ("CCC", "23"))]
    _card(db, w, [(s, None) for s in sids])
    for s, c in zip(sids, ("21", "22", "23"), strict=True):
        _filing(db, s, "10-Q", f"{c}-q", _old())
        attempts_repo.append_attempt(
            db,
            security_id=s,
            run_kind="backfill",
            inputs_key=_key(c, f"{c}-q"),
            status="error",
            error="not cached",
        )
    db.commit()
    monkeypatch.setattr(
        ex, "fetch_submissions", lambda client, cik: _subs([("10-Q", f"{int(cik)}-q", _old())])
    )
    tick = [0.0]
    fake = Extractor([])
    fake.on_call = lambda: tick.__setitem__(0, tick[0] + 250.0)
    monkeypatch.setattr(ex, "extract_with_annual_fallback", fake)

    started = _now()
    night1 = leg.run_nightly_extraction(
        pass_started_at=started,
        live=True,
        connect=connect,
        now=lambda: started + timedelta(seconds=4200),
        clock=lambda: tick[0],
    )
    assert night1["budget"] == {
        "setting_s": 900.0,
        "pass_timeout_s": 5400.0,
        "elapsed_s": 4200.0,
        "margin_s": 600.0,
        "remaining_s": 600.0,
        "budget_s": 600.0,
    }
    assert (night1["attempted"], night1["deferred"], night1["stopped_by"]) == (2, 1, "budget")
    assert fake.calls == [21, 22]
    assert "1 still due" in night1["admin_line"] and night1["attention"] is False

    night2 = leg.run_nightly_extraction(
        pass_started_at=started,
        live=True,
        connect=connect,
        now=lambda: started + timedelta(seconds=600),
        clock=lambda: tick[0],
    )
    assert night2["budget"]["budget_s"] == 900.0
    assert (night2["attempted"], night2["deferred"], night2["due_total"]) == (1, 0, 1)
    assert fake.calls == [21, 22, 23]


def test_the_step_uses_the_recurring_sec_client_in_the_pass_s_mode():
    """The other nightly legs' client: the recurring dial (so the recurring lane of the ONE global SEC budget, the
    filing gate and the negative cache) and the pass's live mode."""
    from pipeline.nightly_extraction import recurring_client

    live, cache_only = recurring_client(True), recurring_client(False)
    assert live.recurring and cache_only.recurring
    assert (live.allow_live, cache_only.allow_live) == (True, False)
    assert live.cache_ttl_s == edgar_client.RECURRING_CACHE_TTL_S


# --- the artifact amend --------------------------------------------------------------------------------------------


def test_the_block_lands_only_on_this_pass_s_finished_artifact(tmp_path):
    started = datetime(2026, 11, 13, 3, 30, tzinfo=timezone.utc)
    path = tmp_path / "a.json"

    def write(status: str, at: datetime) -> None:
        path.write_text(json.dumps({"status": status, "started_at": at.isoformat(), "x": 1}))

    for status, at in (
        ("running", started),
        ("crashed", started),
        ("finished", started.replace(hour=4)),
    ):
        write(status, at)
        assert (
            cron_run_log.amend_run_block(path, started_at=started, key="k", block={"a": 1}) is None
        )
        assert "k" not in json.loads(path.read_text())
    write("finished", started)
    assert cron_run_log.amend_run_block(path, started_at=started, key="k", block={"a": 1}) == path
    assert json.loads(path.read_text()) == {
        "status": "finished",
        "started_at": started.isoformat(),
        "x": 1,
        "k": {"a": 1},
    }
    assert (
        cron_run_log.amend_run_block(
            tmp_path / "missing.json", started_at=started, key="k", block={}
        )
        is None
    )


# --- the operator's hand run (the fork: no cron, no User-Agent) ------------------------------------------------------


def test_the_hand_run_is_a_dry_run_by_default_and_cache_only_with_no_live(
    db, world, monkeypatch, tmp_path, capsys
):
    from pipeline import extraction_run_log
    from pipeline import nightly_extraction as leg

    monkeypatch.setattr(extraction_run_log, "EXTRACTION_RUNS_DIR", tmp_path / "extraction_runs")
    monkeypatch.setattr(leg, "_outside_the_window", lambda: None)
    monkeypatch.setattr(leg, "pass_holder", lambda conn: None)
    built: list[bool] = []
    real_client = leg.recurring_client
    monkeypatch.setattr(
        leg, "recurring_client", lambda live: built.append(live) or real_client(live)
    )

    leg.main([])
    out = capsys.readouterr().out
    assert "1 due (retry 1)" in out and "dry run" in out
    assert world["fake"].calls == [] and built == []
    assert _count(db, "platform_extraction_attempt") == 1

    leg.main(["--apply", "--no-live"])
    out = capsys.readouterr().out
    assert built == [False]  # cache-only: no User-Agent needed, no SEC request possible
    assert world["fake"].calls == [1001] and "Shared facts · " in out
    assert _count(db, "platform_extraction_attempt") == 2
    (doc,) = [json.loads(p.read_text()) for p in (tmp_path / "extraction_runs").glob("*.json")]
    assert doc["run_kind"] == "nightly" and doc["attempted"] == 1


def test_the_hand_run_refuses_inside_the_nightly_window_or_while_a_pass_runs(world, monkeypatch):
    from pipeline import nightly_extraction as leg

    monkeypatch.setattr(leg, "_outside_the_window", lambda: "inside the nightly window")
    with pytest.raises(SystemExit) as exc:
        leg.main(["--apply", "--no-live"])
    assert exc.value.code == leg.EXIT_REFUSED
    monkeypatch.setattr(leg, "_outside_the_window", lambda: None)
    monkeypatch.setattr(leg, "pass_holder", lambda conn: "cron")
    with pytest.raises(SystemExit) as exc:
        leg.main(["--apply", "--no-live"])
    assert exc.value.code == leg.EXIT_REFUSED
    assert world["fake"].calls == []


# --- nothing the step writes is read by the call path ----------------------------------------------------------------


def test_nothing_the_step_writes_is_read_by_the_call_path():
    """SOURCE CHECK. The step writes platform values (``fact_shares_outstanding``, ``fact_cash_burn``), candidates,
    attempts, and a block in the run artifact. (1) No module the call is computed from names the step, its record or
    its candidates. (2) Inside that engine the only mentions of the two value tables are the read-side accessors in
    ``signals/base.py`` (display readers call them) and the as-of plumbing in ``db/``; no signal or call module calls
    them. (3) The pass's per-thesis loop (``run_daily``) does not name them either. (4) The artifact block only ADDS a
    key: the catch-up guard and the baselines read ``status`` and their own keys."""
    engine = [_BACKEND / d for d in ("signals", "calls", "replay", "editions")] + [
        _BACKEND / "pipeline" / "core.py",
        _BACKEND / "pipeline" / "call_for_thesis.py",
    ]
    files = [p for root in engine for p in ([root] if root.is_file() else root.rglob("*.py"))]
    assert len(files) > 20
    step = ("nightly_extraction", "platform_values", "platform_extraction", "extraction_candidate")
    assert [p.name for p in files if any(n in p.read_text(encoding="utf-8") for n in step)] == []
    readers = re.compile(r"\b(shares_outstanding_facts|cash_burn_facts)\(")
    callers = [
        p.relative_to(_BACKEND).as_posix()
        for p in files
        for line in p.read_text(encoding="utf-8").splitlines()
        if readers.search(line) and not line.lstrip().startswith("def ")
    ]
    assert callers == []
    src = inspect.getsource(daily.run_daily)
    assert not any(
        t in src for t in ("fact_shares_outstanding", "fact_cash_burn", "nightly_extraction")
    )
    keys = inspect.getsource(cron_run_log.already_ran_live) + inspect.getsource(
        cron_run_log.is_finished
    )
    assert "platform_extraction" not in keys


def test_compose_gives_the_cron_the_pass_limit_the_step_budgets_against():
    """The step reads ``Settings.cron_run_timeout_s``; the sidecar's ``timeout`` reads ``RUN_TIMEOUT_S``. Both come
    from the one host variable, with the Settings default as the fallback, so the budget cannot drift from the
    real kill."""
    compose = (_BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    cron = compose.split("\n  cron:", 1)[1]
    m = re.search(r"ALPHADECK_CRON_RUN_TIMEOUT_S: \$\{ALPHADECK_CRON_RUN_TIMEOUT_S:-(\d+)\}", cron)
    assert m is not None and float(m.group(1)) == get_settings().cron_run_timeout_s
    assert "RUN_TIMEOUT_S: ${ALPHADECK_CRON_RUN_TIMEOUT_S:-5400}" in cron
