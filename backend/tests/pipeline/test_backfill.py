"""``pipeline.backfill`` — the faithful reconstruction of a MISSED night's call-of-record with a PINNED
``known_at``. The DB is real (the ``db`` fixture); facts are seeded directly with explicit ``recorded_at``
stamps on BOTH sides of the pin, so the headline test proves the one thing the module exists for: the pin
EXCLUDES knowledge that arrived after it (the naive ``known_at = now`` backfill would have included it).
Idempotency and the dry run COUNT THE TABLE (the log dedups on read, so a duplicate append hides behind a
correct read while the table silently grows). The resolver is pure over artifact-shaped payloads; the
import guard pins structurally that the module is a recompute-and-record and nothing else.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import State
from domain.market_time import market_today
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices_backfill, parse_yahoo_chart
from pipeline import backfill
from pipeline.backfill import (
    BackfillResult,
    parse_known_at,
    resolve_next_run_known_at,
    run_backfill,
    run_backfill_pass,
)
from pipeline.backfill_log import write_backfill_log
from pipeline.call_for_thesis import call_for_thesis
from pipeline.cron_run_log import build_run_payload
from repositories import calls_repo

_TESTS = Path(__file__).resolve().parents[1]
_SEED = _TESTS.parent / "seed_data"
# The real HIMS vertical slice, the same facts test_call_for_thesis arms on: the tape (2025-06-05 ..
# 2026-06-04, its 2026-06-01 breakout = the confirmation key) + the Wells Form 4 (a director's ~$1.2M
# open-market buy in late May 2026 = the conviction key). Real detectors over real facts.
_WELLS_XML = (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8")
_WELLS_ACCESSION = "0001773751-26-000086"
_HIMS_BARS = parse_yahoo_chart(
    json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
)
_TZ = ZoneInfo("America/New_York")

# The missed night, and the clocks around it. The night is 2026-06-03; the pin is the finish of the FIRST
# cron run after it (2026-06-04 22:41 ET = 06-05 02:41Z). The bars are backfill-stamped (recorded_at = the
# bar date, all BEFORE the pin); the Wells Form 4 is recorded 06-20 — a fact dated in May (valid_from <=
# asof) that the platform only LEARNED after the pin. A naive known_at=now backfill sees it; the pinned one
# must not.
_ASOF = date(2026, 6, 3)
_PIN = datetime(2026, 6, 5, 2, 41, 0, tzinfo=timezone.utc)
_LATE = datetime(2026, 6, 20, 14, 0, 0, tzinfo=timezone.utc)
_SEES_ALL = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _thesis(db, name, *, members=()):
    """Persist a thesis (members = list of (ticker, security_id)) — test_daily's helper."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _seed_split_clock_thesis(db, security_id):
    """The load-bearing fixture: bars knowable on their own night (before the pin) + the Wells buy the
    platform recorded only AFTER the pin. Real detectors over real facts — no fixture fakes a shape.
    """
    tid = _thesis(db, "Split clock", members=[("DEVCO", security_id)])
    ingest_prices_backfill(db, security_id, _HIMS_BARS)  # recorded_at = bar date (before the pin)
    ingest_form4(db, security_id, _WELLS_XML, _WELLS_ACCESSION, recorded_at=_LATE)  # after the pin
    db.commit()
    return tid


def _count(db, thesis_id) -> int:
    """COUNT THE TABLE — every row, never the deduped read."""
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls WHERE thesis_id = %s", (thesis_id,))
        return cur.fetchone()["n"]


def _ingest_stamp(db, thesis_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT ingest_fresh, ingest_errors FROM calls WHERE thesis_id = %s ORDER BY seq",
            (thesis_id,),
        )
        return [(r["ingest_fresh"], r["ingest_errors"]) for r in cur.fetchall()]


# --- 1. THE load-bearing one: the pin excludes later knowledge ---------------------------------------------


def test_the_pin_EXCLUDES_knowledge_recorded_after_it(db, security_id):
    tid = _seed_split_clock_thesis(db, security_id)

    # the two clocks, straight from the assembler: pinned (what the cron WOULD have logged) vs now
    pinned_view = call_for_thesis(db, tid, _ASOF, known_at=_PIN, record=False)
    now_view = call_for_thesis(db, tid, _ASOF, known_at=None, record=False)
    # at the pin only the tape was on file: the 06-01 breakout is live (confirmation) but no conviction
    assert pinned_view.state is State.WARMING
    assert pinned_view.key_confirmation.turned and not pinned_view.key_conviction.turned
    # today's knowledge includes the Wells buy recorded 06-20 → both keys → ARMED. That is exactly the
    # wrong row for a missed night (the MEASURED Modern Defense shape: ARMED between quieter neighbors).
    assert now_view.state is State.ARMED
    assert now_view.key_conviction.turned

    results = run_backfill(db, asof=_ASOF, known_at=_PIN)
    assert [(r.state, r.verdict, r.recorded, r.error) for r in results] == [
        (pinned_view.state.value, pinned_view.verdict.value, True, None)
    ]
    assert results[0].prior_state is None  # the night truly had no row

    logged = calls_repo.latest_for_thesis(db, tid)
    assert len(logged) == 1 and logged[0].asof == _ASOF
    # EXACTLY the pinned assembly — the CLI adds nothing (a field-level equality through the jsonb round
    # trip), and record_if_changed's own canonical compare agrees: the direct call is not a change
    assert logged[0].model_dump(mode="json") == pinned_view.model_dump(mode="json")
    assert calls_repo.record_if_changed(db, pinned_view, DEFAULT_TENANT_ID) is False
    assert logged[0].state is State.WARMING and logged[0].state is not now_view.state
    # the NULL ingest stamp: a reconstructed row carries no ingest health (there was no ingest)
    assert _ingest_stamp(db, tid) == [(None, None)]


# --- 2. idempotent, count the table ---------------------------------------------------------------------


def test_rerun_with_the_same_pin_appends_ZERO_rows_count_the_table(db, security_id):
    tid = _seed_split_clock_thesis(db, security_id)

    assert [r.recorded for r in run_backfill(db, asof=_ASOF, known_at=_PIN)] == [True]
    assert _count(db, tid) == 1
    assert [r.recorded for r in run_backfill(db, asof=_ASOF, known_at=_PIN)] == [False]
    assert _count(db, tid) == 1  # STILL one row, not two
    assert run_backfill(db, asof=_ASOF, known_at=_PIN)[0].prior_state == "warming"

    # a DIFFERENT pin that sees the later-recorded fact is a GENUINE change: exactly one more row
    assert [r.recorded for r in run_backfill(db, asof=_ASOF, known_at=_SEES_ALL)] == [True]
    assert _count(db, tid) == 2
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.ARMED


# --- 3. --dry-run writes NOTHING (no row, no artifact) --------------------------------------------------


def test_dry_run_writes_no_row_and_no_artifact(db, security_id, monkeypatch, tmp_path):
    tid = _seed_split_clock_thesis(db, security_id)

    results = run_backfill(db, asof=_ASOF, known_at=_PIN, dry_run=True)
    assert results[0].state == "warming" and results[0].recorded is None  # assembled, not written
    assert _count(db, tid) == 0  # COUNT the table: nothing landed

    # the full pass: same DB (the fixture's connection, close() neutralized), an explicit artifact dir
    class _NoClose:
        def __getattr__(self, name):
            return getattr(db, name)

        def close(self):
            pass

    monkeypatch.setattr(backfill, "connect", lambda: _NoClose())
    out = run_backfill_pass(
        asof=_ASOF, known_at=_PIN, known_at_policy="explicit", dry_run=True, log_dir=tmp_path
    )
    assert out.log_path is None and list(tmp_path.glob("*.json")) == []  # no provenance either
    assert _count(db, tid) == 0

    # ...and the real pass writes BOTH: one row, one artifact in the backfill's OWN directory
    out = run_backfill_pass(
        asof=_ASOF, known_at=_PIN, known_at_policy="next-run", dry_run=False, log_dir=tmp_path
    )
    assert _count(db, tid) == 1
    assert out.log_path is not None and out.log_path.parent == tmp_path
    doc = json.loads(out.log_path.read_text(encoding="utf-8"))
    assert doc["asof"] == "2026-06-03" and doc["known_at"] == "2026-06-05T02:41:00+00:00"
    assert doc["known_at_policy"] == "next-run" and doc["dry_run"] is False
    assert doc["summary"] == {"theses": 1, "appended": 1, "unchanged": 0, "errored": 0}
    assert doc["theses"][0]["id"] == str(tid) and doc["theses"][0]["state"] == "warming"
    assert doc["theses"][0]["recorded"] is True and doc["theses"][0]["error"] is None


def test_backfill_log_is_FAIL_OPEN(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    now = datetime(2026, 9, 9, 13, 0, tzinfo=timezone.utc)
    assert (
        write_backfill_log(
            [BackfillResult(thesis_id=uuid.uuid4(), name="T", recorded=True)],
            asof=date(2026, 9, 8),
            known_at=_PIN,
            known_at_policy="explicit",
            dry_run=False,
            started_at=now,
            finished_at=now,
            base_dir=blocker,
        )
        is None
    )


# --- 4. the refusals — all loud (exit 2), all BEFORE a connection ---------------------------------------


def _no_connect(monkeypatch):
    """Make connect() explode, so a test proves the refusal fired BEFORE the run reached the DB."""

    def boom():
        raise AssertionError("reached connect() — the refusal did not fire first")

    monkeypatch.setattr(backfill, "connect", boom)


@pytest.mark.parametrize("offset_days", [0, 1])
def test_refuses_an_asof_that_is_not_in_the_past(monkeypatch, capsys, offset_days):
    _no_connect(monkeypatch)
    asof = market_today() + timedelta(days=offset_days)
    with pytest.raises(SystemExit) as exc:
        backfill.main(["--asof", asof.isoformat(), "--known-at", "2027-01-01T00:00:00Z"])
    assert exc.value.code == 2
    assert "not in the past" in capsys.readouterr().err


def test_refuses_a_NAIVE_known_at(monkeypatch, capsys):
    _no_connect(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        backfill.main(["--asof", "2026-09-08", "--known-at", "2026-09-09T13:22:07"])
    assert exc.value.code == 2
    assert "no timezone" in capsys.readouterr().err


def test_refuses_a_known_at_BEFORE_the_asof_day(monkeypatch, capsys):
    _no_connect(monkeypatch)
    # 2026-09-08 00:00 ET is 04:00Z; 03:59Z is still 09-07 in market time → refused
    with pytest.raises(SystemExit) as exc:
        backfill.main(["--asof", "2026-09-08", "--known-at", "2026-09-08T03:59:00Z"])
    assert exc.value.code == 2
    assert "BEFORE the as-of day" in capsys.readouterr().err


def test_refuses_next_run_when_no_live_run_follows_the_night(monkeypatch, capsys):
    _no_connect(monkeypatch)
    monkeypatch.setattr(backfill, "list_run_logs", lambda: [])
    with pytest.raises(SystemExit) as exc:
        backfill.main(["--asof", "2026-09-08", "--known-at", "next-run"])
    assert exc.value.code == 2
    assert "no live run after 2026-09-08" in capsys.readouterr().err


def test_parse_known_at_accepts_Z_and_offsets_normalized_to_UTC():
    assert parse_known_at("2026-09-09T13:22:07Z") == datetime(
        2026, 9, 9, 13, 22, 7, tzinfo=timezone.utc
    )
    assert parse_known_at("2026-09-09T09:22:07-04:00") == datetime(
        2026, 9, 9, 13, 22, 7, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError, match="no timezone"):
        parse_known_at("2026-09-09T13:22:07")
    with pytest.raises(ValueError, match="not an ISO-8601"):
        parse_known_at("yesterday")


# --- 5. resolve_next_run_known_at — pure over artifact-shaped payloads ----------------------------------


def _run(
    started: datetime, *, minutes: int = 11, live: bool = True, asof: date | None = None
) -> dict:
    """A run-of-record payload in the REAL artifact shape (build_run_payload, zero results)."""
    return build_run_payload(
        [],
        asof=asof or started.astimezone(_TZ).date(),
        allow_live=live,
        started_at=started,
        finished_at=started + timedelta(minutes=minutes),
    )


def _et(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_TZ)


def test_resolver_picks_the_EARLIEST_live_run_after_the_night():
    asof = date(2026, 9, 8)  # Tuesday
    payloads = [  # newest-first, as list_run_logs returns them
        _run(_et(2026, 9, 10, 22, 30)),  # Thu night — later, not the first
        _run(_et(2026, 9, 9, 22, 31), minutes=9),  # Wed night — THE first live run after the night
        _run(
            _et(2026, 9, 9, 8, 0), live=False
        ),  # a --no-live dev run Wed morning: refreshed nothing
        _run(_et(2026, 9, 8, 22, 30)),  # Tue 22:30 — ON the as-of day, not after it
        _run(_et(2026, 9, 7, 22, 30)),  # Mon — before
    ]
    got = resolve_next_run_known_at(payloads, asof, _TZ)
    assert got == _et(2026, 9, 9, 22, 40).astimezone(timezone.utc)
    assert got.tzinfo is timezone.utc  # always a UTC instant


def test_resolver_a_no_live_run_does_NOT_count():
    asof = date(2026, 9, 8)
    assert resolve_next_run_known_at([_run(_et(2026, 9, 9, 22, 30), live=False)], asof, _TZ) is None


def test_resolver_ignores_runs_started_ON_the_asof_day():
    asof = date(2026, 9, 8)
    # a same-day manual run (09:00) and the night's own 22:30 fire are not "after the night"; and a run
    # at 23:59:59 market time is still on the day
    payloads = [
        _run(_et(2026, 9, 8, 9, 0)),
        _run(_et(2026, 9, 8, 22, 30)),
        _run(datetime(2026, 9, 8, 23, 59, 59, tzinfo=_TZ)),
    ]
    assert resolve_next_run_known_at(payloads, asof, _TZ) is None
    # ...while 00:00:00 the next day is after it
    assert resolve_next_run_known_at(
        [_run(datetime(2026, 9, 9, 0, 0, 0, tzinfo=_TZ))], asof, _TZ
    ) == datetime(2026, 9, 9, 0, 11, 0, tzinfo=_TZ).astimezone(timezone.utc)


def test_resolver_returns_None_when_there_is_no_run_after():
    assert resolve_next_run_known_at([], date(2026, 9, 8), _TZ) is None
    assert resolve_next_run_known_at([_run(_et(2026, 9, 7, 22, 30))], date(2026, 9, 8), _TZ) is None


def test_resolver_skips_an_unreadable_payload_FAIL_OPEN():
    asof = date(2026, 9, 8)
    good = _run(_et(2026, 9, 9, 22, 30))
    junk = [
        "not a dict",
        {"mode": "live"},  # no stamps
        {"mode": "live", "started_at": "garbage", "finished_at": "garbage"},
        {"mode": "live", "started_at": "2026-09-09T22:30:00", "finished_at": "2026-09-09T22:41:00"},
    ]
    assert resolve_next_run_known_at(junk + [good], asof, _TZ) == datetime.fromisoformat(
        good["finished_at"]
    ).astimezone(timezone.utc)


# --- the CLI → pass wiring (the pass is stubbed; this pins what main threads) ----------------------------


def _stub_pass(monkeypatch):
    seen: dict = {}
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    def _pass(**kw):
        seen.update(kw)
        return backfill.BackfillPassOutcome(
            results=[],
            asof=kw["asof"],
            known_at=kw["known_at"],
            known_at_policy=kw["known_at_policy"],
            dry_run=kw["dry_run"],
            started_at=now,
            finished_at=now,
            log_path=None,
        )

    monkeypatch.setattr(backfill, "run_backfill_pass", _pass)
    return seen


def test_main_threads_an_EXPLICIT_pin_and_prints_it_in_both_clocks(monkeypatch, capsys):
    seen = _stub_pass(monkeypatch)
    tid = uuid.uuid4()
    backfill.main(
        [
            "--asof",
            "2026-09-08",
            "--known-at",
            "2026-09-09T13:22:07Z",
            "--thesis",
            str(tid),
            "--dry-run",
        ]
    )
    assert seen["asof"] == date(2026, 9, 8)
    assert seen["known_at"] == datetime(2026, 9, 9, 13, 22, 7, tzinfo=timezone.utc)
    assert seen["known_at_policy"] == "explicit" and seen["dry_run"] is True
    assert seen["thesis_id"] == tid
    out = capsys.readouterr().out
    assert "known_at=2026-09-09T13:22:07+00:00 (UTC)" in out  # the instant, in UTC...
    assert "2026-09-09T09:22:07-04:00 (market)" in out  # ...and in market time (EDT)
    assert "DRY-RUN" in out


def test_main_resolves_next_run_from_the_run_log(monkeypatch, capsys):
    seen = _stub_pass(monkeypatch)
    monkeypatch.setattr(
        backfill, "list_run_logs", lambda: [_run(_et(2026, 9, 9, 22, 31), minutes=9)]
    )
    backfill.main(["--asof", "2026-09-08", "--known-at", "next-run"])
    assert seen["known_at"] == _et(2026, 9, 9, 22, 40).astimezone(timezone.utc)
    assert seen["known_at_policy"] == "next-run" and seen["dry_run"] is False
    assert seen["thesis_id"] is None  # every live thesis
    assert "policy=next-run" in capsys.readouterr().out


def test_main_exits_1_when_a_thesis_errored(monkeypatch, capsys):
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)
    results = [
        BackfillResult(
            thesis_id=uuid.uuid4(),
            name="GOOD",
            state="incubating",
            verdict="no_call",
            recorded=True,
        ),
        BackfillResult(thesis_id=uuid.uuid4(), name="BAD", error="call: boom"),
    ]
    monkeypatch.setattr(
        backfill,
        "run_backfill_pass",
        lambda **kw: backfill.BackfillPassOutcome(
            results=results,
            asof=kw["asof"],
            known_at=kw["known_at"],
            known_at_policy=kw["known_at_policy"],
            dry_run=False,
            started_at=now,
            finished_at=now,
            log_path=None,
        ),
    )
    with pytest.raises(SystemExit) as exc:
        backfill.main(["--asof", "2026-09-08", "--known-at", "2026-09-09T13:22:07Z"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "GOOD: incubating / no_call · 0 armed · no row for this as-of yet · APPENDED" in out
    assert "BAD: ERROR: call: boom" in out
    assert "done: 2 theses · 1 appended · 0 unchanged · 1 errored" in out


# --- 6. per-thesis isolation ------------------------------------------------------------------------------


def test_one_thesis_failure_does_not_abort_the_rest(db, monkeypatch):
    good = _thesis(db, "GOOD")
    bad = _thesis(db, "BAD")  # sorts first in list_all (by name) -> an EARLY failure isn't fatal

    real = backfill.call_for_thesis

    def flaky(conn, thesis_id, asof, **kw):
        if thesis_id == bad:
            raise RuntimeError("boom")
        return real(conn, thesis_id, asof, **kw)

    monkeypatch.setattr(backfill, "call_for_thesis", flaky)

    by = {r.thesis_id: r for r in run_backfill(db, asof=_ASOF, known_at=_PIN)}
    assert by[good].recorded is True and by[good].error is None
    assert by[bad].recorded is None and by[bad].error and "boom" in by[bad].error
    assert _count(db, good) == 1  # the good thesis still got its reconstructed row
    assert _count(db, bad) == 0  # the failed one wrote nothing


def test_thesis_scoped_run_touches_only_that_thesis_and_unknown_is_LOUD(db):
    a = _thesis(db, "A")
    b = _thesis(db, "B")
    res = run_backfill(db, asof=_ASOF, known_at=_PIN, thesis_id=a)
    assert [r.thesis_id for r in res] == [a]
    assert _count(db, a) == 1 and _count(db, b) == 0
    with pytest.raises(LookupError):
        run_backfill(db, asof=_ASOF, known_at=_PIN, thesis_id=uuid.uuid4())


def test_run_backfill_refuses_a_naive_pin(db):
    with pytest.raises(ValueError, match="timezone-aware"):
        run_backfill(db, asof=_ASOF, known_at=datetime(2026, 6, 5, 2, 41))


# --- 7. the import guard: a recompute-and-record, structurally unable to ingest or notify ---------------

# The module must not import anything that fetches, refreshes, sweeps, or notifies — nor construct an
# EdgarClient / PriceSource. `pipeline.daily` is forbidden too: importing it would drag the whole ingest +
# notify surface in transitively (its report shape is MIRRORED, not imported).
_FORBIDDEN_IMPORTS = (
    "ingest",
    "notify",
    "radar",
    "pipeline.daily",
    "pipeline.ingest_thesis",
    "pipeline.ingest_benchmarks",
    "pipeline.ingest_fundamentals",
    "pipeline.spac_radar",
    "pipeline.spac_sweep",
)
_FORBIDDEN_NAMES = ("EdgarClient", "PriceSource", "TransitionEvent", "Notifier", "get_notifier")


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def _referenced_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
    return out


def test_backfill_cannot_ingest_refresh_sweep_or_notify():
    import pipeline.backfill as backfill_mod
    import pipeline.backfill_log as backfill_log_mod

    for src in (Path(backfill_mod.__file__), Path(backfill_log_mod.__file__)):
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for mod in _imported_modules(tree):
            for forbidden in _FORBIDDEN_IMPORTS:
                assert mod != forbidden and not mod.startswith(
                    forbidden + "."
                ), f"{src.name} imports {mod!r} — a backfill is a pure recompute-and-record"
        names = _referenced_names(tree)
        for forbidden in _FORBIDDEN_NAMES:
            assert forbidden not in names, f"{src.name} references {forbidden!r}"
