"""``pipeline.backfill`` — the reconstruction of a MISSED night's call-of-record on the PUBLIC clock (operator
decision 2026-10-21): every fact counts from when it became public, up to the end of the night's market day.
The DB is real (the ``db`` fixture); facts are seeded directly with explicit ``accepted`` / ``recorded_at``
stamps on BOTH sides of the night, so the headline tests prove the two things the module exists for: a fact
PUBLISHED after the night never leaks in, and a fact public before it is in the row however LATE our database
ingested it (the retired record-clock pin hid it, which only replayed our own outage). Idempotency and the dry
run COUNT THE TABLE (the log dedups on read, so a duplicate append hides behind a correct read while the table
silently grows). The import guard pins structurally that the module is a recompute-and-record and nothing else.
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
from domain.config import DEFAULT_CONFIG, config_hash
from domain.enums import State
from domain.market_time import known_at_for_asof, market_today
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices_backfill, parse_yahoo_chart
from pipeline import backfill
from pipeline.backfill import (
    PUBLIC_CLOCK_POLICY,
    BackfillResult,
    reconstruction_known_at,
    run_backfill,
    run_backfill_pass,
    thesis_existed_on,
)
from pipeline.backfill_log import write_backfill_log
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo
from tests.form4a_fixtures import as_issuer

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

# The missed night, and the clocks around it. The night is 2026-06-03; the reconstruction is judged at the END
# of that market day (06-04 03:59:59Z). The bars are backfill-stamped (recorded_at = the bar date). The Wells
# Form 4 is INGESTED 06-20 — long after the night — and its ACCEPTANCE is the variable: 05-28 (public before
# the night: the row must see it) or 06-10 (published after the night: the row must not).
_ASOF = date(2026, 6, 3)
_CAP = datetime(2026, 6, 4, 3, 59, 59, 999999, tzinfo=timezone.utc)  # 06-03 23:59:59.999999 EDT
_LATE = datetime(2026, 6, 20, 14, 0, 0, tzinfo=timezone.utc)
_PUBLIC_BEFORE = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
_PUBLIC_AFTER = datetime(2026, 6, 10, 21, 0, 0, tzinfo=timezone.utc)


# Every fixture thesis is BORN before the missed night unless a test says otherwise: the existence gate
# (``thesis_existed_on``) skips a thesis created after the night, and the column default (now()) would be
# months after _ASOF — the fixture models a thesis that WAS in that night's cron.
_BORN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _thesis(db, name, *, members=(), created_at=_BORN):
    """Persist a thesis (members = list of (ticker, security_id)) — test_daily's helper, with an explicit
    creation instant (see ``_BORN``)."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", created_at),
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


def _seed_hims(db, security_id, *, accepted: datetime | None):
    """The load-bearing fixture: bars on their own nights + the Wells buy INGESTED long after the night,
    accepted at ``accepted`` (None = no Form 4 at all yet). Real detectors over real facts."""
    tid = _thesis(db, "Public clock", members=[("DEVCO", security_id)])
    as_issuer(
        db, security_id, "0001773751"
    )  # R8-A: the security IS the Wells Form 4's issuer (HIMS)
    ingest_prices_backfill(db, security_id, _HIMS_BARS)  # recorded_at = bar date
    if accepted is not None:
        ingest_form4(
            db, security_id, _WELLS_XML, _WELLS_ACCESSION, recorded_at=_LATE, accepted=accepted
        )
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


def _run_identity(db, thesis_id):
    """(config_hash, code_sha, run_kind) per row, oldest first — the 0044 stamps, read from the RAW table
    because they live off the card and never come back through a CallCard read."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT config_hash, code_sha, run_kind FROM calls WHERE thesis_id = %s ORDER BY seq",
            (thesis_id,),
        )
        return [(r["config_hash"], r["code_sha"], r["run_kind"]) for r in cur.fetchall()]


def _reconstructed_flags(db, thesis_id):
    """The 0042 marker per row, in insertion order — read from the TABLE."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT reconstructed FROM calls WHERE thesis_id = %s ORDER BY seq", (thesis_id,)
        )
        return [r["reconstructed"] for r in cur.fetchall()]


# --- 1. THE load-bearing ones: the reconstruction is judged on the PUBLIC clock -----------------------------


def test_the_cap_is_the_end_of_the_nights_market_day():
    assert reconstruction_known_at(_ASOF) == _CAP == known_at_for_asof(_ASOF)


def test_a_fact_public_before_the_night_is_in_the_row_however_late_we_ingested_it(db, security_id):
    """The Wells buy was ACCEPTED 05-28 — public days before the night — but our database ingested it 06-20.
    The night's reconstruction SEES it (both keys → ARMED): the public clock, not our ingest history.
    """
    tid = _seed_hims(db, security_id, accepted=_PUBLIC_BEFORE)
    public = call_for_thesis(db, tid, _ASOF, known_at=_CAP, clock="public", record=False)
    assert public.state is State.ARMED and public.key_conviction.turned

    results = run_backfill(db, asof=_ASOF)
    assert [(r.state, r.verdict, r.recorded, r.error) for r in results] == [
        (public.state.value, public.verdict.value, True, None)
    ]
    assert results[0].prior_state is None  # the night truly had no row

    logged = calls_repo.latest_for_thesis(db, tid)
    assert len(logged) == 1 and logged[0].asof == _ASOF
    # EXACTLY the public-clock assembly — the CLI adds nothing (a field-level equality through the jsonb
    # round trip), and record_if_changed's own canonical compare agrees: the direct call is not a change
    assert logged[0].model_dump(mode="json") == public.model_dump(mode="json")
    assert calls_repo.record_if_changed(db, public, DEFAULT_TENANT_ID) is False
    # the NULL ingest stamp: a reconstructed row carries no ingest health (there was no ingest)
    assert _ingest_stamp(db, tid) == [(None, None)]
    # the explicit marker (0042): the ONE thing the Scoreboard's record path filters on
    assert _reconstructed_flags(db, tid) == [True]
    # ...and its 0044 PROVENANCE twin. The two are different questions about the same row — `reconstructed`
    # is the Scoreboard's scoring FILTER, `run_kind` is the run's identity — so both are written explicitly
    # rather than one being derived from the other, and the policy fingerprint rides beside them.
    assert _run_identity(db, tid) == [(config_hash(DEFAULT_CONFIG), None, "backfill")]


def test_a_filing_published_after_the_night_never_leaks_in(db, security_id):
    """The SAME buy, accepted 06-10 — a week after the night. However it is stamped, the night cannot see
    it: the tape alone (the 06-01 breakout — confirmation only) → WARMING, while today's live knowledge
    arms. That live view is exactly the wrong row for a missed night (the MEASURED Modern Defense shape:
    ARMED between quieter neighbors)."""
    tid = _seed_hims(db, security_id, accepted=_PUBLIC_AFTER)
    now_view = call_for_thesis(db, tid, _ASOF, known_at=None, record=False)
    assert now_view.state is State.ARMED

    (res,) = run_backfill(db, asof=_ASOF)
    assert res.state == "warming" and res.recorded is True
    (logged,) = calls_repo.latest_for_thesis(db, tid)
    assert logged.state is State.WARMING
    assert logged.key_confirmation.turned and not logged.key_conviction.turned


# --- 2. idempotent, count the table ---------------------------------------------------------------------


def test_rerun_appends_ZERO_rows_and_a_late_public_fact_appends_one(db, security_id):
    tid = _seed_hims(db, security_id, accepted=None)  # the tape only

    assert [r.recorded for r in run_backfill(db, asof=_ASOF)] == [True]
    assert _count(db, tid) == 1
    assert [r.recorded for r in run_backfill(db, asof=_ASOF)] == [False]
    assert _count(db, tid) == 1  # STILL one row, not two
    assert run_backfill(db, asof=_ASOF)[0].prior_state == "warming"

    # the Wells Form 4 arrives LATE — but it was public before the night (accepted 05-28): a GENUINE change
    # to what the night's public record holds, so exactly one more row
    ingest_form4(
        db, security_id, _WELLS_XML, _WELLS_ACCESSION, recorded_at=_LATE, accepted=_PUBLIC_BEFORE
    )
    db.commit()
    assert [r.recorded for r in run_backfill(db, asof=_ASOF)] == [True]
    assert _count(db, tid) == 2
    assert calls_repo.latest_for_thesis(db, tid)[0].state is State.ARMED
    assert _reconstructed_flags(db, tid) == [True, True]  # every backfill row carries the marker
    assert [r[2] for r in _run_identity(db, tid)] == [
        "backfill",
        "backfill",
    ]  # ...and the 0044 kind
    # ...and the HONEST read (the Scoreboard's) sees neither: a reconstruction is reported, never scored
    assert calls_repo.latest_for_thesis(db, tid, include_reconstructed=False) == []


# --- 3. --dry-run writes NOTHING (no row, no artifact) --------------------------------------------------


def test_dry_run_writes_no_row_and_no_artifact(db, security_id, monkeypatch, tmp_path):
    tid = _seed_hims(db, security_id, accepted=_PUBLIC_AFTER)

    results = run_backfill(db, asof=_ASOF, dry_run=True)
    assert results[0].state == "warming" and results[0].recorded is None  # assembled, not written
    assert _count(db, tid) == 0  # COUNT the table: nothing landed

    # the full pass: same DB (the fixture's connection, close() neutralized), an explicit artifact dir
    class _NoClose:
        def __getattr__(self, name):
            return getattr(db, name)

        def close(self):
            pass

    monkeypatch.setattr(backfill, "connect", lambda: _NoClose())
    out = run_backfill_pass(asof=_ASOF, dry_run=True, log_dir=tmp_path)
    assert out.log_path is None and list(tmp_path.glob("*.json")) == []  # no provenance either
    assert _count(db, tid) == 0

    # ...and the real pass writes BOTH: one row, one artifact in the backfill's OWN directory
    out = run_backfill_pass(asof=_ASOF, dry_run=False, log_dir=tmp_path)
    assert _count(db, tid) == 1
    assert out.log_path is not None and out.log_path.parent == tmp_path
    doc = json.loads(out.log_path.read_text(encoding="utf-8"))
    assert doc["asof"] == "2026-06-03" and doc["known_at"] == "2026-06-04T03:59:59.999999+00:00"
    assert doc["known_at_policy"] == PUBLIC_CLOCK_POLICY == "public-clock"
    assert doc["dry_run"] is False
    assert doc["summary"] == {
        "theses": 1,
        "appended": 1,
        "unchanged": 0,
        "skipped": 0,
        "errored": 0,
    }
    assert doc["theses"][0]["id"] == str(tid) and doc["theses"][0]["state"] == "warming"
    assert doc["theses"][0]["recorded"] is True and doc["theses"][0]["error"] is None
    assert doc["theses"][0]["skipped"] is None


def test_backfill_log_is_FAIL_OPEN(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    now = datetime(2026, 9, 9, 13, 0, tzinfo=timezone.utc)
    assert (
        write_backfill_log(
            [BackfillResult(thesis_id=uuid.uuid4(), name="T", recorded=True)],
            asof=date(2026, 9, 8),
            known_at=_CAP,
            known_at_policy=PUBLIC_CLOCK_POLICY,
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
        backfill.main(["--asof", asof.isoformat()])
    assert exc.value.code == 2
    assert "not in the past" in capsys.readouterr().err


@pytest.mark.parametrize("pin", ["next-run", "2026-09-09T13:22:07Z"])
def test_the_retired_known_at_pin_is_refused_with_the_reason(monkeypatch, capsys, pin):
    """The record-clock pin ("what our database held at the next live run") is retired: passing it is a
    loud refusal that names the public clock — never silently ignored, never honored."""
    _no_connect(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        backfill.main(["--asof", "2026-09-08", "--known-at", pin])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--known-at is retired" in err and "PUBLIC clock" in err


# --- the CLI → pass wiring (the pass is stubbed; this pins what main threads) ----------------------------


def _stub_pass(monkeypatch):
    seen: dict = {}
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    def _pass(**kw):
        seen.update(kw)
        return backfill.BackfillPassOutcome(
            results=[],
            asof=kw["asof"],
            known_at=reconstruction_known_at(kw["asof"]),
            known_at_policy=PUBLIC_CLOCK_POLICY,
            dry_run=kw["dry_run"],
            started_at=now,
            finished_at=now,
            log_path=None,
        )

    monkeypatch.setattr(backfill, "run_backfill_pass", _pass)
    return seen


def test_main_threads_the_night_and_prints_the_public_cap_in_both_clocks(monkeypatch, capsys):
    seen = _stub_pass(monkeypatch)
    tid = uuid.uuid4()
    backfill.main(["--asof", "2026-09-08", "--thesis", str(tid), "--dry-run"])
    assert seen == {"asof": date(2026, 9, 8), "thesis_id": tid, "dry_run": True}
    out = capsys.readouterr().out
    assert "public clock, capped at 2026-09-09T03:59:59.999999+00:00 (UTC)" in out  # the instant...
    assert "2026-09-08T23:59:59.999999-04:00 (market)" in out  # ...and in market time (EDT)
    assert "DRY-RUN" in out


def test_main_defaults_to_every_live_thesis(monkeypatch, capsys):
    seen = _stub_pass(monkeypatch)
    backfill.main(["--asof", "2026-09-08"])
    assert seen["thesis_id"] is None and seen["dry_run"] is False


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
            known_at=reconstruction_known_at(kw["asof"]),
            known_at_policy=PUBLIC_CLOCK_POLICY,
            dry_run=False,
            started_at=now,
            finished_at=now,
            log_path=None,
        ),
    )
    with pytest.raises(SystemExit) as exc:
        backfill.main(["--asof", "2026-09-08"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "GOOD: incubating / no_call · 0 armed · no row for this as-of yet · APPENDED" in out
    assert "BAD: ERROR: call: boom" in out
    assert (
        "done: 2 theses · 1 appended · 0 unchanged · 0 skipped (did not exist on the night) · "
        "1 errored" in out
    )


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

    by = {r.thesis_id: r for r in run_backfill(db, asof=_ASOF)}
    assert by[good].recorded is True and by[good].error is None
    assert by[bad].recorded is None and by[bad].error and "boom" in by[bad].error
    assert _count(db, good) == 1  # the good thesis still got its reconstructed row
    assert _count(db, bad) == 0  # the failed one wrote nothing


def test_thesis_scoped_run_touches_only_that_thesis_and_unknown_is_LOUD(db):
    a = _thesis(db, "A")
    b = _thesis(db, "B")
    res = run_backfill(db, asof=_ASOF, thesis_id=a)
    assert [r.thesis_id for r in res] == [a]
    assert _count(db, a) == 1 and _count(db, b) == 0
    with pytest.raises(LookupError):
        run_backfill(db, asof=_ASOF, thesis_id=uuid.uuid4())


# --- 6b. the existence gate: a thesis created AFTER the night gets NO row, loudly -------------------------

# The night is 2026-06-03. In market time (EDT, UTC-4) that day ends at 06-04 03:59:59Z.
_BORN_ON_THE_NIGHT = datetime(
    2026, 6, 4, 3, 59, 59, tzinfo=timezone.utc
)  # 06-03 23:59:59 EDT: existed
_BORN_AFTER = datetime(
    2026, 6, 4, 4, 0, 0, tzinfo=timezone.utc
)  # 06-04 00:00:00 EDT: did NOT exist
_SKIP_REASON = "did not exist on 2026-06-03 — created 2026-06-04 (market time)"


def test_thesis_existed_on_is_market_time_by_the_calendar_day():
    assert thesis_existed_on(_BORN, _ASOF, _TZ) is True
    assert (
        thesis_existed_on(_BORN_ON_THE_NIGHT, _ASOF, _TZ) is True
    )  # created DURING the day: in the cron
    assert (
        thesis_existed_on(_BORN_AFTER, _ASOF, _TZ) is False
    )  # one second later, the next market day
    # the same boundary spelled in market time (an instant is an instant)
    assert thesis_existed_on(datetime(2026, 6, 3, 23, 59, 59, tzinfo=_TZ), _ASOF, _TZ) is True
    assert thesis_existed_on(datetime(2026, 6, 4, 0, 0, 0, tzinfo=_TZ), _ASOF, _TZ) is False
    with pytest.raises(ValueError, match="timezone-aware"):
        thesis_existed_on(datetime(2026, 6, 4, 3, 59, 59), _ASOF, _TZ)


def test_skips_a_thesis_created_after_the_night_and_reports_it_LOUDLY(
    db, monkeypatch, tmp_path, capsys
):
    existed = _thesis(db, "A existed")
    edge = _thesis(db, "B born on the night", created_at=_BORN_ON_THE_NIGHT)
    later = _thesis(db, "C born after", created_at=_BORN_AFTER)

    results = run_backfill(db, asof=_ASOF)
    by = {r.thesis_id: r for r in results}
    assert by[existed].recorded is True and by[existed].skipped is None
    assert by[edge].recorded is True and by[edge].skipped is None  # in that night's cron: a row
    assert by[later].recorded is None and by[later].error is None
    assert by[later].skipped == _SKIP_REASON
    assert by[later].state is None and by[later].prior_state is None  # nothing was even assembled
    assert _count(db, existed) == 1 and _count(db, edge) == 1
    assert _count(db, later) == 0  # COUNT THE TABLE: no row for a thesis that did not exist

    # the CLI report: its own loud line + its own count — neither an error (exit signal 0) nor an
    # "unchanged"
    assert backfill._report(results, dry_run=False) == 0
    out = capsys.readouterr().out
    assert f"C born after: SKIPPED — {_SKIP_REASON} (no row written)" in out
    assert (
        "done: 3 theses · 2 appended · 0 unchanged · 1 skipped (did not exist on the night) · "
        "0 errored" in out
    )

    # --dry-run reports the skip too: it is a fact about the night, not about writing
    dry = {r.thesis_id: r for r in run_backfill(db, asof=_ASOF, dry_run=True)}
    assert dry[later].skipped == _SKIP_REASON and dry[existed].skipped is None
    assert _count(db, later) == 0

    # the provenance artifact carries the skip — per thesis AND in the summary count
    class _NoClose:
        def __getattr__(self, name):
            return getattr(db, name)

        def close(self):
            pass

    monkeypatch.setattr(backfill, "connect", lambda: _NoClose())
    outcome = run_backfill_pass(asof=_ASOF, log_dir=tmp_path)
    doc = json.loads(outcome.log_path.read_text(encoding="utf-8"))
    assert doc["summary"] == {
        "theses": 3,
        "appended": 0,
        "unchanged": 2,
        "skipped": 1,
        "errored": 0,
    }
    assert {t["name"]: t["skipped"] for t in doc["theses"]} == {
        "A existed": None,
        "B born on the night": None,
        "C born after": _SKIP_REASON,
    }
    assert _count(db, later) == 0


def test_a_cron_row_stays_false_while_the_backfill_row_reads_true(db, security_id):
    """The marker is the backfill's alone: the cron's writer — ``record_if_changed`` with the R2b
    stamp and no ``reconstructed`` kwarg, exactly ``pipeline.daily``'s call shape — leaves the column
    at its default; the backfill's row is true. Same thesis, two nights, both rows counted."""
    tid = _seed_hims(db, security_id, accepted=_PUBLIC_AFTER)
    assert [r.recorded for r in run_backfill(db, asof=_ASOF)] == [True]
    nightly = call_for_thesis(db, tid, _ASOF + timedelta(days=1), known_at=None, record=False)
    assert (
        calls_repo.record_if_changed(
            db, nightly, DEFAULT_TENANT_ID, ingest_fresh=True, ingest_errors=0
        )
        is True
    )
    db.commit()
    assert _count(db, tid) == 2
    assert _reconstructed_flags(db, tid) == [True, False]


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
