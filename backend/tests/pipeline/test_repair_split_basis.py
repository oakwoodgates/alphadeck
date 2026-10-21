"""``pipeline.repair_split_basis`` (S2), DB-backed on the REAL stored tapes: the dry run is read-only, ``--apply``
appends exactly the plan under one stamp and a second run appends nothing, the as-of read sees the old bars
before the stamp and the repaired ones after, the T5 hold is cleared, the lock / refusals write nothing, and
``--revoke`` appends the old values back. COUNT THE TABLE throughout."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from db.bitemporal import as_of
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from ingest.prices.eod_loader import ingest_prices
from ingest.prices.ingest_security import load_hold, save_hold
from ingest.prices.restatement import HeldRevert
from pipeline import repair_split_basis as tool
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from repositories import split_repo
from tests import split_s2_fixtures as fx

T = fx.TODAY


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_price_eod")
        return cur.fetchone()["n"]


def _seed(db, *tks, confirmed=()):
    ids = {}
    for tk in tks:
        sid = fx.seed_security(db, tk)
        fx.seed_splits(db, sid, tk, confirmed=confirmed)
        ingest_prices(db, sid, fx.rows(tk), tenant_id=DEFAULT_TENANT_ID)
        ids[tk] = sid
    db.commit()
    return ids


def test_dry_run_is_read_only_and_names_every_company(db):
    _seed(db, "BTOC", "OPTT", "MI", "SMX")
    n0 = _count(db)
    db.rollback()
    db.read_only = True  # the CLI's dry run: a READ ONLY connection, so a write would raise
    plans = tool.plan_all(db, today=T)
    db.rollback()
    db.read_only = False
    assert _count(db) == n0
    by = {p.ticker: p for p in plans}
    assert by["BTOC"].status == tool.REPAIR and len(by["BTOC"].changes) == 527
    assert by["OPTT"].status == tool.REPAIR and len(by["OPTT"].changes) == 503
    assert (
        by["MI"].status == tool.WAITING and by["MI"].waiting
    )  # needs review: shown, never applied
    assert by["SMX"].status == tool.REFUSED  # without raw prints its 11-18 step is ambiguous
    assert by["BTOC"].gates.t4_before and not by["BTOC"].gates.t4_after
    # the Cockpit columns the diagnosis named: BTOC's 7-day return was +1,190%, the truth about -19%
    assert by["BTOC"].display_before["ret_7d"] > 1000
    assert -30 < by["BTOC"].display_after["ret_7d"] < 0


def test_apply_appends_exactly_the_plan_under_one_stamp_and_a_rerun_appends_nothing(db):
    ids = _seed(db, "BTOC", "OPTT")
    n0 = _count(db)
    plans = tool.plan_all(db, tickers=["BTOC", "OPTT"], today=T)
    planned = sum(len(p.changes) for p in plans)
    assert planned == 527 + 503
    res = tool.apply_plans(db, plans)
    assert res.appended == planned
    assert _count(db) == n0 + planned
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n, count(DISTINCT recorded_at) AS stamps FROM fact_price_eod "
            "WHERE recorded_at = %s",
            (res.recorded_at,),
        )
        row = cur.fetchone()
    assert (row["n"], row["stamps"]) == (planned, 1)
    again = tool.plan_all(db, tickers=["BTOC", "OPTT"], today=T)
    assert [p.status for p in again] == [tool.CLEAN, tool.CLEAN]
    assert tool.apply_plans(db, again).appended == 0
    assert _count(db) == n0 + planned
    assert set(ids)  # both seeded


def test_as_of_reads_before_and_after_the_stamp(db):
    ids = _seed(db, "BTOC")
    sid = ids["BTOC"]
    res = tool.apply_plans(db, tool.plan_all(db, tickers=["BTOC"], today=T))
    d = date(2026, 10, 13)

    def close_at(known_at):
        rows = as_of(
            db,
            "fact_price_eod",
            security_id=sid,
            asof=T,
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
        )
        return next(float(r["close"]) for r in rows if r["d"] == d)

    old = close_at(res.recorded_at - timedelta(microseconds=1))
    new = close_at(res.recorded_at)
    assert new == pytest.approx(old * 16, rel=1e-12)


def test_apply_clears_the_t5_hold_through_its_own_api(db):
    ids = _seed(db, "OPTT")
    sid = ids["OPTT"]
    save_hold(
        db,
        sid,
        HeldRevert(date(2026, 10, 19), {date(2026, 3, 2): {"close": 0.1, "volume": 1.0}}),
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    plans = tool.plan_all(db, tickers=["OPTT"], today=T)
    assert plans[0].holds == 1
    res = tool.apply_plans(db, plans)
    assert res.holds_cleared == 1
    assert load_hold(db, sid, tenant_id=DEFAULT_TENANT_ID) is None


def _audit(asof):
    from datetime import datetime, time, timezone

    from db.session import connect
    from pipeline.self_audit import AuditContext, check_splits_applied

    ctx = AuditContext(
        asof=asof,
        started_at=datetime.combine(asof, time(23), tzinfo=timezone.utc),
        allow_live=True,
        run_kind="cron",
        code_sha="abc",
        results=[],
        run_at=time(22, 30),
        tz=timezone.utc,
        connect=connect,
    )
    try:
        return check_splits_applied(ctx)
    finally:
        ctx.close()


def _basket(db, ids: dict) -> None:
    """One thesis holding ``ids`` — the active basket union the audit walks."""
    import uuid

    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "s2-audit", "n"),
        )
        for i, (tk, sid) in enumerate(ids.items()):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, tk, "—", sid),
            )
    db.commit()


def test_the_self_audit_pages_a_recent_unapplied_split_and_names_the_backlog(db):
    """BTOC's 1-for-16 (first session 10-15, three sessions before 10-20) is still a raw cliff on its stored
    tape — the price leg's first night did not take (S2 was not live): FAIL. DUKR's (03-06) is the repair
    tool's backlog: named, not paged. After the repair: every split in force applied."""
    ids = _seed(db, "BTOC", "DUKR")
    _basket(db, ids)
    first = _audit(T)
    assert first.ok is False and "BTOC" in first.detail and "DUKR" not in first.detail
    tool.apply_plans(db, tool.plan_all(db, tickers=["BTOC"], today=T))
    second = _audit(T)
    assert second.ok is True and "DUKR" in second.detail and "repair_split_basis" in second.detail
    tool.apply_plans(db, tool.plan_all(db, tickers=["DUKR"], today=T))
    third = _audit(T)
    assert third.ok is True and third.detail == "every split in force is applied (2 checked)"


def test_the_self_audit_never_counts_an_unreadable_or_an_announced_split_as_applied(db):
    """The B1 / B7 review fix. Three splits the old audit called "applied" (its boundary read every unreadable
    tape as "on scale"):

    - KITT's 1-for-6 (09-25) on a RAW store (CONTRACT, the stated edit: the real tape's run before it put back
      raw — the store a misread first night leaves): never "applied" — the backlog or NOT VERIFIED, named;
    - ATRO's 5-for-6 (the stand-in confirm): a x0.83 step the tape cannot read — NOT VERIFIED (unreadable);
    - an ANNOUNCED 1-for-10 on BTOC for 2026-10-06 that never happened (CONTRACT: a postponed split) on the
      repaired, flat tape — NOT VERIFIED (announced, not confirmed): a flat tape is equally "never happened".

    The audit passes (the tape cannot say; never a page) but names all three."""
    ids = _seed(db, "BTOC", "ATRO", confirmed=("ATRO",))
    tool.apply_plans(db, tool.plan_all(db, tickers=["BTOC"], today=T))
    kitt = fx.seed_security(db, "KITT")
    fx.seed_splits(db, kitt, "KITT")
    eff, k = date(2026, 9, 25), 6
    rows = []
    for r in fx.rows("KITT"):
        if r["d"] < eff:
            r = {
                **r,
                **{f: r[f] / k for f in ("open", "high", "low", "close") if r[f] is not None},
                "volume": None if r["volume"] is None else r["volume"] * k,
            }
        rows.append(r)
    ingest_prices(db, kitt, rows, tenant_id=DEFAULT_TENANT_ID)
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=ids["BTOC"],
        key_date=date(2026, 10, 6),
        values={
            "effective_date": date(2026, 10, 6),
            "ratio_from": Decimal(10),
            "ratio_to": Decimal(1),
            "status": "announced",
            "value_source": "sec",
            "valid_from": date(2026, 9, 30),
        },
        action=split_repo.ACTION_BACKFILL,
        author=split_repo.AUTHOR_BACKFILL,
    )
    db.commit()
    _basket(db, {**ids, "KITT": kitt})
    res = _audit(T)
    assert res.ok is True
    assert "every split in force is applied" not in res.detail
    assert "KITT 1-for-6" in res.detail  # backlog or not verified — never applied
    assert "NOT VERIFIED" in res.detail
    assert "ATRO" in res.detail and "(unreadable)" in res.detail
    assert "BTOC 1-for-10" in res.detail and "(announced, not confirmed)" in res.detail
    assert "BTOC 1-for-16" not in res.detail  # the repaired, confirmed one IS verified applied


def test_main_refuses_while_the_call_of_record_lock_is_held(db, capsys, monkeypatch, tmp_path):
    _seed(db, "BTOC")
    n0 = _count(db)
    monkeypatch.setattr(tool, "_DEFAULT_LOG_DIR", tmp_path)
    lock = acquire_pass_lock(holder="test-nightly")
    try:
        with pytest.raises(SystemExit) as exc:
            tool.main(["--ticker", "BTOC", "--apply", "--today", T.isoformat()])
    finally:
        lock.release()
    assert exc.value.code == EXIT_ALREADY_RUNNING
    assert _count(db) == n0
    assert "ALREADY RUNNING" in capsys.readouterr().out


@pytest.mark.parametrize(
    "tk,status",
    [("SMX", "refused"), ("MI", "waiting")],  # an unlabeled bar; a split not in force
)
def test_main_apply_writes_nothing_unless_every_named_company_passes(
    db, monkeypatch, tmp_path, tk, status
):
    _seed(db, "BTOC", tk)
    n0 = _count(db)
    monkeypatch.setattr(tool, "_DEFAULT_LOG_DIR", tmp_path)
    with pytest.raises(SystemExit) as exc:
        tool.main(["--ticker", f"BTOC,{tk}", "--apply", "--today", T.isoformat(), "--quiet"])
    assert exc.value.code == tool.EXIT_REFUSED
    assert _count(db) == n0
    assert list(tmp_path.iterdir()) == []


def test_main_apply_with_raw_prints_writes_provenance_and_revoke_appends_the_old_values(
    db, monkeypatch, tmp_path, capsys
):
    ids = _seed(db, "SMX", "PBM")
    n0 = _count(db)
    monkeypatch.setattr(tool, "_DEFAULT_LOG_DIR", tmp_path)
    tool.main(
        [
            "--ticker",
            "SMX,PBM",
            "--spot-file",
            str(fx.EVIDENCE),
            "--apply",
            "--today",
            T.isoformat(),
            "--quiet",
        ]
    )
    out = capsys.readouterr().out
    assert "APPLIED: appended 765" in out  # SMX 421 + PBM 344
    assert _count(db) == n0 + 765
    (path,) = tmp_path.glob("*-split-basis.json")
    prov = json.loads(path.read_text(encoding="utf-8"))
    assert prov["kind"] == tool.KIND and prov["appended"] == 765
    smx = next(c for c in prov["companies"] if c["ticker"] == "SMX")
    assert len(smx["bars"]) == 421 and {s["sec_accession"] for s in smx["splits"]} >= {
        "0001493152-25-023264",
        "0001493152-26-021807",
    }
    pbm_bar = next(c for c in prov["companies"] if c["ticker"] == "PBM")["bars"][0]
    assert pbm_bar["label"] == "over_adjusted" and pbm_bar["factor"] == "x4/25"
    assert prov["spot_file_sha256"]

    tool.main(["--revoke", prov["recorded_at"]])
    assert "REVOKED: appended 765" in capsys.readouterr().out
    assert _count(db) == n0 + 765 + 765  # nothing deleted: the old values are a third version
    rows = as_of(
        db,
        "fact_price_eod",
        security_id=ids["PBM"],
        asof=T,
        known_at=db_now(db),
        tenant_id=DEFAULT_TENANT_ID,
    )
    by = {r["d"]: float(r["close"]) for r in rows}
    stored = {r["d"]: float(r["close"]) for r in fx.rows("PBM")}
    assert by[date(2025, 6, 2)] == pytest.approx(stored[date(2025, 6, 2)], rel=1e-12)
    # a second revoke of the same stamp is refused: the newest versions are no longer the repair's
    with pytest.raises(SystemExit) as exc:
        tool.main(["--revoke", prov["recorded_at"]])
    assert exc.value.code == tool.EXIT_REFUSED
