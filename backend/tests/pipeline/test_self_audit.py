"""V1 — the nightly self-audit, each check proved PASSING and FAILING on a real test DB.

No fitting: every row a check reads is written by the REAL writers (``daily.run_daily`` → ``record_if_changed``
for nightly rows, the same writer with ``reconstructed=True`` for a backfill-shaped row, ``set_archived`` for
an archived thesis), and the on-time cutoff is placed BETWEEN two genuine writes — so a row the check calls
"before the cutoff" really was recorded before it. The ``every_thesis_finished`` check reads the results of a
real ``run_daily`` whose call step genuinely raised for one thesis.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest

from db.session import DEFAULT_TENANT_ID, connect
from pipeline import daily
from pipeline.self_audit import (
    DEFAULT_CHECKS,
    AuditContext,
    AuditResult,
    check_code_sha_known,
    check_every_thesis_finished,
    check_on_time_rows,
    check_splits_applied,
    failed_labels,
    run_audit,
)
from repositories import calls_repo, thesis_repo

_ASOF = date(2026, 6, 10)


def _thesis(db, name):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
    db.commit()
    return tid


def _db_now(db) -> datetime:
    """The SERVER clock — the same clock `recorded_at` defaults to, so a cutoff taken here sits genuinely
    between the writes before and after it."""
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        t = cur.fetchone()["t"]
    db.commit()
    return t


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _ctx(*, cutoff: datetime, results=(), started_at=None, allow_live=True, code_sha="abc1234"):
    """An audit context whose night cutoff IS ``cutoff`` exactly: the as-of is the cutoff's UTC date and
    RUN_AT its UTC wall time, in a UTC zone — so ``ctx.cutoff == cutoff`` to the microsecond."""
    c = _utc(cutoff)
    ctx = AuditContext(
        asof=c.date(),
        started_at=started_at or (c + timedelta(minutes=1)),
        allow_live=allow_live,
        run_kind="cron",
        code_sha=code_sha,
        results=list(results),
        run_at=c.time(),
        tz=timezone.utc,
        connect=connect,
    )
    assert ctx.cutoff == c
    return ctx


def _no_network(monkeypatch):
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])


# --- (a) on_time_rows — counted in the calls table ----------------------------------------------------


def test_on_time_rows_PASSES_when_every_active_thesis_has_a_post_cutoff_row(db, monkeypatch):
    _no_network(monkeypatch)
    _thesis(db, "a1")
    _thesis(db, "a2")
    cutoff = _db_now(db)
    daily.run_daily(db, asof=_utc(cutoff).date(), allow_live=True, run_kind="cron")  # AFTER cutoff

    res = check_on_time_rows(_ctx(cutoff=cutoff, started_at=_db_now(db)))

    assert res.ok is True, res.detail
    assert "2 of 2 active theses" in res.detail


def test_on_time_rows_FAILS_for_a_DAYTIME_row_a_RECONSTRUCTED_row_and_NO_row(db, monkeypatch):
    """Three genuine misses, one genuine hit, one archived thesis that is out of scope:
    - `early`: its only row was recorded BEFORE the cutoff (the daytime-pass shape);
    - `recon`: its only row after the cutoff is RECONSTRUCTED (a backfill row is never the night's record);
    - `none`: no row at all for the night;
    - `ok`: a nightly row after the cutoff;
    - `gone`: ARCHIVED before the cutoff, no row — the cron does not walk it, so the audit must not demand
      one.
    Every thesis is created BEFORE the cutoff (M1: the roster is the theses that existed when the night's
    pass was due — `pipeline/night_coverage.py`), and each row goes through the cron's own writer
    (`call_for_thesis` + `record_if_changed`) for that thesis alone."""
    _no_network(monkeypatch)
    early = _thesis(db, "thesis-early-name")
    ok = _thesis(db, "thesis-ok-name")
    recon = _thesis(db, "thesis-recon-name")
    none_ = _thesis(db, "thesis-none-name")
    gone = _thesis(db, "thesis-gone-name")
    thesis_repo.set_archived(db, gone, True)
    db.commit()
    asof = _utc(_db_now(db)).date()
    card_early = daily.call_for_thesis(db, early, asof, record=False)  # `early` — BEFORE the cutoff
    calls_repo.record_if_changed(db, card_early, DEFAULT_TENANT_ID, run_kind="manual")
    db.commit()
    cutoff = _db_now(db)
    if _utc(cutoff).date() != asof:  # pragma: no cover — the UTC date flipped between two reads
        pytest.skip("crossed UTC midnight mid-test")
    # `ok`: the real nightly writer, after the cutoff; `recon`: the same writer, marked reconstructed
    card_ok = daily.call_for_thesis(db, ok, asof, record=False)
    calls_repo.record_if_changed(db, card_ok, DEFAULT_TENANT_ID, run_kind="cron")
    card_recon = daily.call_for_thesis(db, recon, asof, record=False)
    calls_repo.record_if_changed(
        db, card_recon, DEFAULT_TENANT_ID, reconstructed=True, run_kind="backfill"
    )
    db.commit()

    res = check_on_time_rows(_ctx(cutoff=cutoff, started_at=_db_now(db)))

    assert res.ok is False
    assert (
        "3 of 4 active theses have NO on-time row" in res.detail
    )  # the archived one is not counted
    for tid in (early, recon, none_):
        assert str(tid)[:8] in res.detail
    assert str(ok)[:8] not in res.detail and str(gone)[:8] not in res.detail
    assert "thesis-" not in res.detail  # ids only — never a thesis NAME


def test_on_time_rows_judges_the_ROSTER_at_the_cutoff_created_after_is_out_archived_after_is_in(
    db, monkeypatch
):
    """M1 — the roster is the night's, not today's: a thesis CREATED after the cutoff was never in the pass's
    walk (no row demanded), while one ARCHIVED after the cutoff was (archiving never hides its missing row).
    Both theses are real rows; the archive stamp is the real writer's `now()`, after the cutoff."""
    _no_network(monkeypatch)
    kept = _thesis(db, "thesis-kept-name")  # in the roster, archived AFTER the cutoff, no row
    cutoff = _db_now(db)
    late_born = _thesis(db, "thesis-born-after-name")  # created AFTER the cutoff, no row
    thesis_repo.set_archived(db, kept, True)  # archived_at = now() > cutoff
    db.commit()

    res = check_on_time_rows(_ctx(cutoff=cutoff, started_at=_db_now(db)))

    assert res.ok is False
    assert "1 of 1 active theses have NO on-time row" in res.detail
    assert str(kept)[:8] in res.detail and str(late_born)[:8] not in res.detail


def test_on_time_rows_is_NOT_APPLICABLE_to_a_daytime_pass_or_a_no_live_pass(db):
    cutoff = _db_now(db)
    daytime = check_on_time_rows(_ctx(cutoff=cutoff, started_at=cutoff - timedelta(hours=8)))
    assert daytime.ok is None and "daytime" in daytime.detail
    no_live = check_on_time_rows(_ctx(cutoff=cutoff, allow_live=False))
    assert no_live.ok is None and "no-live" in no_live.detail


def test_on_time_rows_with_NO_active_theses_passes_quietly(db):
    res = check_on_time_rows(_ctx(cutoff=_db_now(db)))
    assert res.ok is True and "no active theses" in res.detail


# --- (b) code_sha_known ----------------------------------------------------------------------------


def test_code_sha_known_passes_and_FAILS_naming_the_run_kind():
    now = datetime(2026, 6, 11, 2, 30, tzinfo=timezone.utc)
    assert check_code_sha_known(_ctx(cutoff=now, code_sha="2f3cdfa")).ok is True
    bad = check_code_sha_known(_ctx(cutoff=now, code_sha=None))
    assert bad.ok is False
    assert "code_sha UNKNOWN on a cron pass" in bad.detail and "GIT_SHA" in bad.detail
    assert check_code_sha_known(_ctx(cutoff=now, code_sha=None, allow_live=False)).ok is None


# --- (c) every_thesis_finished — over a REAL run_daily ----------------------------------------------


def test_every_thesis_finished_PASSES_on_a_clean_real_pass(db, monkeypatch):
    _no_network(monkeypatch)
    _thesis(db, "f1")
    _thesis(db, "f2")
    results = daily.run_daily(db, asof=_ASOF, allow_live=True)
    res = check_every_thesis_finished(_ctx(cutoff=_db_now(db), results=results))
    assert res.ok is True and "2 of 2 theses finished" in res.detail


def test_every_thesis_finished_FAILS_when_a_real_call_step_raised(db, monkeypatch):
    _no_network(monkeypatch)
    good = _thesis(db, "thesis-good-name")
    bad = _thesis(db, "thesis-bad-name")
    real = daily.call_for_thesis

    def _flaky(conn, thesis_id, *a, **k):
        if thesis_id == bad:
            raise RuntimeError("assembly exploded")
        return real(conn, thesis_id, *a, **k)

    monkeypatch.setattr(daily, "call_for_thesis", _flaky)
    results = daily.run_daily(db, asof=_ASOF, allow_live=True)
    assert {r.thesis_id: r.error is not None for r in results} == {good: False, bad: True}

    res = check_every_thesis_finished(_ctx(cutoff=_db_now(db), results=results))

    assert res.ok is False
    assert "1 of 2 theses finished" in res.detail and "1 errored" in res.detail
    assert str(bad)[:8] in res.detail and "thesis-" not in res.detail


def test_every_thesis_finished_counts_a_NO_LIVE_withhold_as_finished(db, monkeypatch):
    _no_network(monkeypatch)
    _thesis(db, "nl")
    results = daily.run_daily(db, asof=_ASOF, allow_live=False)
    assert results[0].withheld_reason == "no-live"
    res = check_every_thesis_finished(_ctx(cutoff=_db_now(db), results=results))
    assert res.ok is True


# --- the runner -------------------------------------------------------------------------------------


def test_run_audit_ISOLATES_a_raising_check_and_still_runs_the_rest(db):
    def check_explodes(ctx):
        raise ValueError("bad\nmultiline")

    out = run_audit(_ctx(cutoff=_db_now(db)), checks=(check_explodes, check_code_sha_known))
    assert [a.check for a in out] == ["explodes", "code_sha_known"]
    assert out[0].ok is False and "could not run: ValueError: bad multiline" in out[0].detail
    assert out[1].ok is True


def test_the_audit_connection_is_READ_ONLY(db):
    ctx = _ctx(cutoff=_db_now(db))
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            ctx.conn().execute(
                "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'x', 'n')",
                (uuid.uuid4(), DEFAULT_TENANT_ID),
            )
    finally:
        ctx.close()


def test_failed_labels_pages_FAILURES_only_never_not_applicable():
    audit = [
        AuditResult("on_time_rows", None, "not applicable"),
        AuditResult("code_sha_known", False, "code_sha UNKNOWN"),
        AuditResult("every_thesis_finished", True, "2 of 2"),
    ]
    assert failed_labels(audit) == ("code_sha_known: code_sha UNKNOWN",)


def test_the_phase_1_checks_are_registered():
    assert [c.__name__ for c in DEFAULT_CHECKS] == [
        "check_on_time_rows",
        "check_code_sha_known",
        "check_every_thesis_finished",
        "check_split_defects_flagged",  # T4 — its branches are proved in tests/pipeline/test_split_basis.py
        "check_no_unsettled_bars",  # the settle guard — tests/pipeline/test_settle_audit.py
        "check_splits_applied",  # S2 — proved on real tapes in tests/pipeline/test_repair_split_basis.py
    ]


def test_the_PASS_writes_the_audit_into_the_artifact_and_PAGES_a_failure(db, monkeypatch, tmp_path):
    """End to end through run_daily_pass: the audit runs after the loop, rides the artifact, and a failure
    (here a genuinely unknown code_sha — the test image has none) pages through R4 — with no thesis name.
    """
    import json

    from pipeline import cron_run_log

    _no_network(monkeypatch)
    _thesis(db, "audited-thesis-name")
    monkeypatch.delenv("ALPHADECK_IMAGE_SHA", raising=False)
    from domain.settings import get_settings

    get_settings.cache_clear()
    for target in (
        "pipeline.ingest_benchmarks.ingest_benchmarks",
        "pipeline.ingest_fundamentals.ingest_fundamentals",
    ):
        monkeypatch.setattr(target, lambda conn, **k: [])
    for target in (
        "radar.shell_sweep.run_shell_sweep",
        "radar.spac.run_spac_radar",
        "radar.funds.run_fund_radar",
    ):
        monkeypatch.setattr(target, lambda conn, **k: SimpleNamespace(summary="s", errors=[]))
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    pages = []
    notifier = SimpleNamespace(
        notify=lambda e: None,
        notify_health=pages.append,
        notify_spac_status=lambda e: None,
    )
    try:
        out = daily.run_daily_pass(
            asof=_db_now(db).astimezone(daily.market_tz()).date(),
            allow_live=True,
            notifier=notifier,
            run_kind="cron",
        )
    finally:
        get_settings.cache_clear()

    checks = {a.check: a for a in out.audit}
    assert set(checks) == {
        "on_time_rows",
        "code_sha_known",
        "every_thesis_finished",
        "split_defects_flagged",
        "no_unsettled_bars",
        "splits_applied",
    }
    assert (
        checks["split_defects_flagged"].ok is True
    )  # the real scan ran over the (clean) test tapes
    # the settle check ran over the real table (the pass wrote no bar) through the pass's own window
    assert checks["no_unsettled_bars"].ok is True, checks["no_unsettled_bars"].detail
    assert checks["splits_applied"].ok is True  # no split listed on the test tapes
    assert checks["code_sha_known"].ok is False
    doc = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert {a["check"]: a["ok"] for a in doc["audit"]}["code_sha_known"] is False
    assert len(pages) == 1 and pages[0].audit_failed
    assert "code_sha_known" in pages[0].label
    assert "audited-thesis-name" not in pages[0].label  # a page never names a thesis


# --- (f) splits_applied — the repair command is named per ticker ---------------------------------------

_SPLIT_ASOF = date(2026, 11, 25)
_REPAIR = "python -m pipeline.repair_split_basis"


def _unapplied_split(db, tk: str, first_session: date, *, factor: int = 10) -> uuid.UUID:
    """A company in the active basket whose confirmed 1-for-``factor`` reverse split is in force and NOT on its
    stored tape: weekday bars from 14 days before the first session to 14 days after (or the as-of), written
    by the real price writer at the old scale (0.50, 1,000,000 shares) before the first session and at the
    raw post-split scale (x``factor``, volume / ``factor``) from it — the shape ``boundary`` reads as RAW. The
    split row goes through the repo's own writer, as the operator's confirm does.
    """
    from ingest.prices.eod_loader import ingest_prices
    from repositories import split_repo

    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, price_symbol, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, tk, f"{tk} Corp.", tk, "2020-01-01"),
        )
    rows = []
    d = first_session - timedelta(days=14)
    while d <= min(first_session + timedelta(days=14), _SPLIT_ASOF):
        if d.weekday() < 5:
            on_new_scale = d >= first_session
            close = Decimal("0.50") * (factor if on_new_scale else 1)
            volume = Decimal(1_000_000) / (factor if on_new_scale else 1)
            rows.append(
                {
                    "d": d,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": volume,
                }
            )
        d += timedelta(days=1)
    ingest_prices(db, sid, rows, tenant_id=DEFAULT_TENANT_ID)
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=first_session,
        values={
            "effective_date": first_session,
            "ratio_from": Decimal(factor),
            "ratio_to": Decimal(1),
            "status": "confirmed",
            "confirmed_via": "operator",
            "value_source": "polygon",
            "polygon_execution_date": first_session,
            "polygon_split_from": Decimal(factor),
            "polygon_split_to": Decimal(1),
            "valid_from": first_session - timedelta(days=6),
        },
        action=split_repo.ACTION_CONFIRM,
        author=split_repo.AUTHOR_OPERATOR,
    )
    db.commit()
    return sid


def _in_basket(db, *sids: uuid.UUID) -> None:
    """One thesis whose basket holds ``sids`` — the active basket union the audit walks."""
    tid = _thesis(db, "s2-audit-basket")
    with db.cursor() as cur:
        for i, sid in enumerate(sids):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, "—", "—", sid),
            )
    db.commit()


def _splits_applied(asof: date) -> AuditResult:
    ctx = AuditContext(
        asof=asof,
        started_at=datetime.combine(asof, time(23), tzinfo=timezone.utc),
        allow_live=True,
        run_kind="cron",
        code_sha="abc1234",
        results=[],
        run_at=time(22, 30),
        tz=timezone.utc,
        connect=connect,
    )
    try:
        return check_splits_applied(ctx)
    finally:
        ctx.close()


def test_splits_applied_FAILS_a_recent_split_naming_the_repair_command_for_that_ticker(db):
    """GNS's 1-for-10 (first session 2026-11-20, three sessions before the 11-25 pass) is not on the stored
    tape. The detail does not blame \"the price leg's first night\" — GNS WAS applied, then undone by a vendor
    revert — and names the one command that fixes the company: the dry run, then --apply."""
    _in_basket(db, _unapplied_split(db, "GNS", date(2026, 11, 20)))

    res = _splits_applied(_SPLIT_ASOF)

    assert res.ok is False, res.detail
    assert "GNS 1-for-10 reverse split 2026-11-20" in res.detail
    assert f"{_REPAIR} --ticker GNS (dry run), then --apply" in res.detail
    assert "first night did not take" not in res.detail
    assert "never applied, or undone by a vendor revert" in res.detail


def test_splits_applied_FAIL_detail_names_a_command_per_ticker_when_several_are_recent(db):
    """Two recent splits, two commands — one per company, never the bare tool (which dry-runs every one)."""
    _in_basket(
        db,
        _unapplied_split(db, "GNS", date(2026, 11, 20)),
        _unapplied_split(db, "RCNT", date(2026, 11, 19), factor=4),
    )

    res = _splits_applied(_SPLIT_ASOF)

    assert res.ok is False
    assert f"{_REPAIR} --ticker GNS (dry run), then --apply" in res.detail
    assert f"{_REPAIR} --ticker RCNT (dry run), then --apply" in res.detail
    assert "RCNT 1-for-4 reverse split 2026-11-19" in res.detail


def test_splits_applied_BACKLOG_passes_and_names_the_repair_command_per_ticker(db):
    """Older splits are the repair tool's backlog: the audit passes, and each name carries its own command."""
    _in_basket(
        db,
        _unapplied_split(db, "OLDR", date(2026, 10, 1)),
        _unapplied_split(db, "ELDR", date(2026, 10, 6), factor=4),
    )

    res = _splits_applied(_SPLIT_ASOF)

    assert res.ok is True, res.detail
    assert "2 older one(s) not yet applied to the stored tape" in res.detail
    assert (
        f"OLDR 1-for-10 reverse split 2026-10-01 — run {_REPAIR} --ticker OLDR (dry run), then --apply"
        in res.detail
    )
    assert (
        f"ELDR 1-for-4 reverse split 2026-10-06 — run {_REPAIR} --ticker ELDR (dry run), then --apply"
        in res.detail
    )
