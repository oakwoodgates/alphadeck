"""``pipeline.set_aside_fundamentals`` + the set-aside read (migration 0062, ``db.set_aside``).

The stored revenue rows #417's data-quality screen would have refused are set aside — append-only, revocable,
the fact rows kept — and every read skips them: the live record read, the basket prefetch, the public-clock
history read, and the replay mirror (both engines alike).

The DB tests use REAL data: LFMD's companyfacts (``tests/fundamentals_fixtures.py``, trimmed to its revenue
nodes) stored exactly as prod held it before #417 (``_store_as_prod_held_it`` — the first-present ``Revenues``
label, unscreened, the pre-0057 stamp). Its one stored bad row is the one prod holds (MEASURED on the dev copy of
prod, 2026-10-22: LFMD Q4-2017 = 98,213 from the 2019-04-01 10-K, an FY-restated derived Q4 against a
3,681,827 year; the original 10-K's 1,471,092 from 2018-04-02 is the version before it). The same-filing
re-parse fallback is VICR's real 2025-08-01 supersession. The no-lookahead and conflict tests state the
matcher's contract with direct inputs; they fabricate no outcome. DB-backed tests skip without Postgres.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import date

import psycopg
import pytest

from db.bitemporal import KNOWABLE_FROM
from domain.market_time import known_at_for_asof
from ingest.edgar import client as edgar_client
from ingest.edgar.client import EdgarClient
from ingest.fundamentals import SCREEN_FY_IN_QUARTER, SCREEN_RESTATED_FY_Q4, select_revenue
from pipeline import set_aside_fundamentals as SA
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories import fundamentals_set_aside_repo as repo
from signals.base import PointInTimeData
from tests.fundamentals_fixtures import COMPANYFACTS_CIK, companyfacts_cache, companyfacts_revenue
from tests.ingest.test_revenue_label import _reingest, _security, _store_as_prod_held_it
from tests.replay.test_pit_parity import _match

Q4_2017 = date(2017, 12, 31)
BAD = 98_213.0  # the 2019-04-01 10-K's FY-restated derived Q4 (prod holds it)
GOOD = 1_471_092.0  # the 2018-04-02 10-K's derived Q4 — the version before it


# --------------------------------------------------------------------------------------------------------
# the matcher (pure)
# --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ticker", sorted(COMPANYFACTS_CIK))
def test_the_screen_is_the_ingests_own(ticker):
    """Under the current label, the rows the matcher would set aside are EXACTLY the rows the #417 ingest
    screens out (``select_revenue(...).screened``) — the same function, never a re-implementation.
    """
    doc = companyfacts_revenue(ticker)
    sel = select_revenue(doc)
    hits, _kept = SA.screen_hits(doc)
    mine = sorted(
        (h.reason, h.period_end, h.filed, h.value, h.reference, h.accession)
        for h in hits.get(sel.concept, {}).values()
    )
    theirs = sorted(
        (x.reason, x.period_end, x.filed, x.value, x.reference, x.accession) for x in sel.screened
    )
    assert mine == theirs


def _cf(rows: list[tuple[str, str, float, str, str, str]]) -> dict:
    """A one-label companyfacts doc from ``(start, end, val, filed, form, accn)`` rows."""
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"start": s, "end": e, "val": v, "filed": f, "form": fm, "accn": a}
                            for s, e, v, f, fm, a in rows
                        ]
                    }
                }
            }
        }
    }


def _stored(
    period_end: date, filed: date, value: float, accession: str, basis: str = "native"
) -> dict:
    return {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "security_id": uuid.uuid4(),
        "metric_key": "revenue",
        "period_end": period_end,
        "valid_from": filed,
        "value": value,
        "accession": accession,
        "basis": basis,
        "source": "companyfacts",
        "supersedes": None,
        "recorded_at": filed,
    }


def test_no_lookahead_a_row_is_judged_only_by_filings_on_or_before_its_own():
    """Contract (direct inputs): a $10M Q1 filed 2025-05-12, a later 10-K (2026-03-02) whose FY total equals
    it while Q2+Q3 are a material share, and the 10-K's own Q1-context re-report of that $10M. Judged by its
    OWN filing the 10-Q's Q1 has no year to equal — never set aside (a later filing must never decide whether a
    public quarter existed). The 10-K's re-report is judged with the FY total beside it — set aside.
    """
    doc = _cf(
        [
            ("2025-01-01", "2025-03-31", 10_000_000, "2025-05-12", "10-Q", "q1"),
            ("2025-04-01", "2025-06-30", 2_000_000, "2025-08-11", "10-Q", "q2"),
            ("2025-07-01", "2025-09-30", 2_000_000, "2025-11-10", "10-Q", "q3"),
            ("2025-01-01", "2025-03-31", 10_000_000, "2026-03-02", "10-K", "k"),
            ("2025-01-01", "2025-12-31", 10_000_000, "2026-03-02", "10-K", "k"),
        ]
    )
    q1_10q = _stored(date(2025, 3, 31), date(2025, 5, 12), 10_000_000, "q1")
    q1_10k = _stored(date(2025, 3, 31), date(2026, 3, 2), 10_000_000, "k")
    cands, conflicts = SA.match_company([q1_10q, q1_10k], doc)
    assert [(c.valid_from, c.hit.reason) for c in cands] == [
        (date(2026, 3, 2), SCREEN_FY_IN_QUARTER)
    ]
    assert conflicts == []


def test_a_number_another_label_keeps_is_never_set_aside():
    """Contract (direct inputs; recall #9): a stored derived Q4 one label's screen rejects but another label
    reports as a genuine quarter (same period, filing, value) is a CONFLICT — reported, left in the read
    (MEASURED: one such row on the dev copy of prod, RDNW Q4-2021)."""
    rev = [  # FY restated after the 9-month: FY − 9mo = 400 is screened under Revenues
        ("2021-01-01", "2021-09-30", 900, "2021-11-10", "10-Q", "q3"),
        ("2021-01-01", "2021-12-31", 1_400, "2022-03-01", "10-K", "k1"),
        ("2021-01-01", "2021-12-31", 1_300, "2023-03-16", "10-K", "k2"),
    ]
    doc = _cf(rev)
    doc["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] = {
        "units": {
            "USD": [
                {
                    "start": "2021-10-01",
                    "end": "2021-12-31",
                    "val": 400,
                    "filed": "2023-03-16",
                    "form": "10-K",
                    "accn": "k2",
                }
            ]
        }
    }
    row = _stored(date(2021, 12, 31), date(2023, 3, 16), 400, "k2", basis="derived")
    hits, _ = SA.screen_hits(doc)
    assert any(h.reason == SCREEN_RESTATED_FY_Q4 for h in hits["Revenues"].values())
    cands, conflicts = SA.match_company([row], doc)
    assert cands == [] and [c.kept_under for c in conflicts] == [
        ("RevenueFromContractWithCustomerExcludingAssessedTax",)
    ]


# --------------------------------------------------------------------------------------------------------
# the real LFMD row: found, set aside (count the table), the read falls back, revoke restores it
# --------------------------------------------------------------------------------------------------------


@pytest.fixture
def lfmd(db, tmp_path):
    sec = _security(db, "LFMD")
    _store_as_prod_held_it(db, sec, "LFMD")
    cache = companyfacts_cache(tmp_path / "edgar", "LFMD")
    return sec, EdgarClient(cache_dir=cache, allow_live=False, cache_ttl_s=math.inf), cache


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test literal
        return cur.fetchone()["n"]


def _q4_2017(db, sid, asof: date, *, clock: str = "record", basket: bool = False) -> dict:
    known_at = known_at_for_asof(asof)
    pit = PointInTimeData(
        db, asof=asof, known_at=known_at, clock=clock, basket=[sid] if basket else None
    )
    return next(r for r in pit.fundamentals_facts(sid) if r["period_end"] == Q4_2017)


def test_the_real_stored_row_is_found_and_set_aside_once_count_the_table(db, lfmd):
    sec, client, _ = lfmd
    facts = _count(db, "fact_fundamentals")
    res = SA.scan(db, client=client)
    db.rollback()
    [c] = res.candidates()
    assert (c.period_end, c.valid_from, c.value, c.hit.reason, c.hit.label, c.basis) == (
        Q4_2017,
        date(2019, 4, 1),
        BAD,
        SCREEN_RESTATED_FY_Q4,
        "Revenues",
        "derived",
    )
    assert c.hit.reference == 3_681_827.0 and c.status == SA.NEW and not c.outranked

    SA.apply(db, res)
    assert len(res.appended) == 1 and _count(db, "fundamentals_set_aside") == 1
    [row] = repo.active(db, family=SA.RULE_FAMILY)
    assert row.fact_id == c.fact_id and row.reason == SCREEN_RESTATED_FY_Q4
    assert row.author == SA.AUTHOR and row.rule.startswith(f"{SA.RULE_FAMILY}@")
    assert (row.period_end, row.valid_from, row.security_id) == (
        Q4_2017,
        date(2019, 4, 1),
        sec.id,
    )
    assert row.detail["value"] == BAD and row.detail["label"] == "Revenues"

    again = SA.scan(db, client=client)
    db.rollback()
    assert [x.status for x in again.candidates()] == [SA.ACTIVE]
    SA.apply(db, again)
    assert again.appended == []
    assert _count(db, "fundamentals_set_aside") == 1  # COUNT the table: zero more
    assert _count(db, "fact_fundamentals") == facts  # the fact rows are kept, never touched


@pytest.mark.parametrize(
    ("clock", "basket"), [("record", False), ("record", True), ("public", False), ("public", True)]
)
def test_the_read_falls_back_to_the_earlier_filing_and_revoke_restores_it(db, lfmd, clock, basket):
    """At every asof after the bad 10-K, on both clocks, scoped and basket-prefetched: before, Q4-2017 reads
    the bad 98,213; set aside, it reads the original 10-K's 1,471,092, dated at ITS filing — as if the bad row
    had never been stored; revoked, the bad row is back."""
    sec, client, _ = lfmd
    asof = date(2019, 6, 3)
    before = _q4_2017(db, sec.id, asof, clock=clock, basket=basket)
    assert float(before["value"]) == BAD and before[KNOWABLE_FROM] == date(2019, 4, 1)

    res = SA.scan(db, client=client)
    db.rollback()
    SA.apply(db, res)
    after = _q4_2017(db, sec.id, asof, clock=clock, basket=basket)
    assert float(after["value"]) == GOOD
    assert after["valid_from"] == after[KNOWABLE_FROM] == date(2018, 4, 2)
    # before the bad 10-K was filed nothing changes: the set-aside row was never visible there
    early = _q4_2017(db, sec.id, date(2018, 6, 1), clock=clock, basket=basket)
    assert float(early["value"]) == GOOD

    [(sid, _c)] = res.appended
    SA.revoke(db, [sid], reason="test: undo", apply=True)
    assert repo.active(db) == []
    back = _q4_2017(db, sec.id, asof, clock=clock, basket=basket)
    assert float(back["value"]) == BAD and back[KNOWABLE_FROM] == date(2019, 4, 1)

    # a revoke the operator made sticks: a re-run never judges the version again
    rerun = SA.scan(db, client=client)
    db.rollback()
    assert [x.status for x in rerun.candidates()] == [SA.REVOKED]
    SA.apply(db, rerun)
    assert rerun.appended == [] and repo.active(db) == []


def test_both_engines_skip_a_set_aside_version(db, lfmd, tmp_path):
    """THE PARITY GATE for 0062: with the row set aside, the mirror (record AND public export) reads
    row-for-row what Postgres reads (scoped and prefetched), the set-aside version is absent from the
    Parquet, and the manifest names how many were left out."""
    sec, client, _ = lfmd
    res = SA.scan(db, client=client)
    db.rollback()
    SA.apply(db, res)
    [(_sid, cand)] = res.appended
    for clock in ("record", "public"):
        out = tmp_path / f"mirror-{clock}"
        m = export_snapshot(db, out, clock=clock)
        assert m["tables"]["fact_fundamentals"]["rows_set_aside"] == 1
        con = connect_mirror(out)
        try:
            ids = {r[0] for r in con.execute("SELECT id FROM fact_fundamentals").fetchall()}
            assert str(cand.fact_id) not in ids
            for asof in (date(2018, 6, 1), date(2019, 6, 3), date(2026, 10, 21)):
                known_at = known_at_for_asof(asof)
                live = PointInTimeData(db, asof=asof, known_at=known_at, clock=clock)
                live_b = PointInTimeData(
                    db, asof=asof, known_at=known_at, clock=clock, basket=[sec.id]
                )
                rep = ReplayPointInTimeData(con, asof=asof, known_at=known_at)
                rep_b = ReplayPointInTimeData(con, asof=asof, known_at=known_at, basket=[sec.id])
                lr, rr = live.fundamentals_facts(sec.id), rep.fundamentals_facts(sec.id)
                assert len(lr) == len(rr) == len(rep_b.fundamentals_facts(sec.id)) > 0
                _match(lr, rr, ["security_id", "metric_key", "period_end"])
                _match(
                    live_b.fundamentals_facts(sec.id),
                    rr,
                    ["security_id", "metric_key", "period_end"],
                )
                q4 = next(r for r in rr if r["period_end"] == Q4_2017)
                assert float(q4["value"]) == GOOD
        finally:
            con.close()


def test_a_set_aside_reparse_falls_back_to_the_version_it_superseded(db, tmp_path):
    """Contract for the read's fallback rule on the 0060 chain (the CLI never sets a current-label number
    aside — this states what the READ does if a re-parse is): VICR's real 2025-08-01 10-Q holds Q2-2025 =
    141,046,000 (the old ``Revenues`` parse) and its re-parse 96,046,000 (the current tag, ``supersedes`` it).
    The re-parse set aside, the filing reads its earlier parse again."""
    sec = _security(db, "VICR")
    _store_as_prod_held_it(db, sec, "VICR")
    _reingest(db, sec, tmp_path, "VICR")
    q2 = date(2025, 6, 30)
    asof = date(2025, 9, 2)
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, value FROM fact_fundamentals WHERE security_id = %s AND period_end = %s "
            "AND valid_from = %s AND supersedes IS NOT NULL",
            (sec.id, q2, date(2025, 8, 1)),
        )
        reparse = cur.fetchone()
    assert float(reparse["value"]) == 96_046_000.0

    def read() -> float:
        pit = PointInTimeData(db, asof=asof, known_at=known_at_for_asof(asof), clock="public")
        return float(
            next(r for r in pit.fundamentals_facts(sec.id) if r["period_end"] == q2)["value"]
        )

    assert read() == 96_046_000.0
    repo.append_set_aside(db, reparse["id"], reason="test", rule="test@x", author="test")
    db.commit()
    assert read() == 141_046_000.0


# --------------------------------------------------------------------------------------------------------
# the table's guards
# --------------------------------------------------------------------------------------------------------


def test_the_log_is_append_only_and_a_revoke_is_structural(db, lfmd):
    sec, client, _ = lfmd
    res = SA.scan(db, client=client)
    db.rollback()
    SA.apply(db, res)
    [(sid, _c)] = res.appended
    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE fundamentals_set_aside SET reason = 'x' WHERE id = %s", (sid,))
    db.rollback()
    [rid] = SA.revoke(db, [sid], reason="undo", apply=True)
    with pytest.raises(LookupError):  # already revoked
        SA.revoke(db, [sid], reason="again", apply=True)
    with pytest.raises(psycopg.errors.RaiseException):  # a revoke of a revoke, hand-typed
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO fundamentals_set_aside (tenant_id, fact_id, security_id, metric_key, "
                "period_end, valid_from, action, reason, rule, author, revokes) SELECT tenant_id, fact_id, "
                "security_id, metric_key, period_end, valid_from, 'revoke', 'x', 'x', 'x', id "
                "FROM fundamentals_set_aside WHERE id = %s",
                (rid,),
            )
    db.rollback()


# --------------------------------------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------------------------------------


def test_the_cli_dry_run_then_apply_then_rerun_then_revoke_all(
    db, lfmd, monkeypatch, tmp_path, capsys
):
    _sec, _client, cache = lfmd
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", cache)
    monkeypatch.setattr(SA, "_DEFAULT_LOG_DIR", tmp_path / "repairs")

    SA.main([])
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert (
        "LFMD: quarter ending 2017-12-31 · value 98,213 (vs 3,681,827) · filed 2019-04-01 "
        "(0001213900-19-005312) · q4_restated_fy_basis · label Revenues" in out
    )
    assert "would set aside 1" in out
    assert _count(db, "fundamentals_set_aside") == 0 and not (tmp_path / "repairs").exists()

    SA.main(["--apply"])
    out = capsys.readouterr().out
    assert "set aside 1" in out and _count(db, "fundamentals_set_aside") == 1
    [prov] = list((tmp_path / "repairs").glob("*-set-aside-fundamentals.json"))
    payload = json.loads(prov.read_text(encoding="utf-8"))
    assert payload["applied"] is True and len(payload["appended"]) == 1
    assert payload["appended"][0]["reason"] == SCREEN_RESTATED_FY_Q4
    assert payload["set_aside_rows_after"] - payload["set_aside_rows_before"] == 1

    SA.main(["--apply"])
    out = capsys.readouterr().out
    assert "set aside 0" in out and "1 already set aside" in out
    assert _count(db, "fundamentals_set_aside") == 1

    with pytest.raises(SystemExit):
        SA.main(["--revoke-all"])  # a revoke needs a reason
    SA.main(["--revoke-all", "--reason", "test"])  # dry run
    assert "DRY-RUN" in capsys.readouterr().out and repo.count_active(db) == 1
    SA.main(["--revoke-all", "--reason", "test", "--apply"])
    assert "revoked=1" in capsys.readouterr().out and repo.count_active(db) == 0


def test_the_cli_apply_exits_75_while_the_lock_is_held(db, lfmd, monkeypatch, tmp_path, capsys):
    _sec, _client, cache = lfmd
    monkeypatch.setattr(edgar_client, "_DEFAULT_CACHE", cache)
    monkeypatch.setattr(SA, "_DEFAULT_LOG_DIR", tmp_path / "repairs")
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            SA.main(["--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        assert "ALREADY RUNNING" in capsys.readouterr().out
        assert _count(db, "fundamentals_set_aside") == 0
        SA.main([])  # a dry run takes no lock and still runs
    finally:
        held.release()


def test_a_company_without_cached_companyfacts_is_reported_never_judged(db, tmp_path, capsys):
    """Recall-visible: a company whose document is not cached cannot be judged — it is printed, and nothing of
    it is set aside (the CLI never goes to the network)."""
    sec = _security(db, "LFMD")
    _store_as_prod_held_it(db, sec, "LFMD")
    empty = EdgarClient(cache_dir=tmp_path / "none", allow_live=False, cache_ttl_s=math.inf)
    res = SA.scan(db, client=empty)
    db.rollback()
    assert res.candidates() == [] and [co.source for co in res.companies] == ["not_cached"]
    SA.report(res)
    assert "LFMD: not judged — companyfacts not_cached" in capsys.readouterr().out
