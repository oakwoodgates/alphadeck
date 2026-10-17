"""T7 — the successor-CIK detector, on the REAL measured shapes (``tests/fixtures/edgar/
successor_cik_2026_10.json``): UROY (0001711570 -> 0002143673, 8-K12B 2026-07-28) and REAX (0001862461 ->
0002136387, 8-K12B 2026-08-24) are the positives; HLLK (renamed under the SAME CIK — its CIK lists SDCO) and
UCLE (a no-ticker CIK that still files) are the negatives the rule must leave alone.

The scan's two EDGAR clients are stand-ins serving the fixture's documents by cache key and COUNTING every
read; the master read runs on the real test DB (the scan opens its own read-only connection)."""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta, timezone

from db.session import connect
from ingest.edgar.submissions import parse_identity
from ingest.edgar.succession import (
    TICKER_MAP_CACHE_KEY,
    deregistration_filings,
    parse_ticker_map,
    succession_filings,
)
from pipeline import cron_run_log
from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult, assess_health
from pipeline.successor_cik import (
    SuccessorScan,
    cik10,
    is_baseline,
    keys_of,
    new_labels_of,
    previous_successor_keys,
    report_lines,
    run_successor_scan,
    successor_ciks,
)
from tests import successor_cik_fixtures as fx

_OCT16 = date(2026, 10, 16)
_ALL = ("UROY", "REAX", "HLLK", "UCLE")


def _tmap():
    return parse_ticker_map(fx.ticker_map_raw())


def _rule(ticker: str) -> dict:
    m = fx.case(ticker)["master"]
    return successor_ciks(
        stored_cik=m["cik"],
        ticker=m["ticker"],
        price_symbol=m["price_symbol"],
        listed=fx.name_result(ticker, uuid.uuid4()).edgar_tickers,
        ticker_map=_tmap(),
    )


# --- the pure rule -------------------------------------------------------------------------------------


def test_the_rule_flags_the_two_REAL_successor_issuers():
    assert _rule("UROY") == {"0002143673": ("UROY",)}
    assert _rule("REAX") == {"0002136387": ("REAX",)}


def test_the_rule_leaves_a_RENAMED_SAME_CIK_name_and_a_NO_TICKER_FILER_alone():
    """HLLK's stored CIK lists SDCO (renamed, same CIK — condition (a) fails); UCLE's lists nothing but SEC's
    map assigns UCLE to no one (condition (b) fails). Neither is a successor case."""
    assert fx.name_result("HLLK", uuid.uuid4()).edgar_tickers == ("SDCO",)
    assert _rule("HLLK") == {}
    assert fx.name_result("UCLE", uuid.uuid4()).edgar_tickers == ()
    assert "UCLE" not in _tmap()
    assert _rule("UCLE") == {}


def test_the_rule_gives_NO_verdict_without_a_read_and_reads_the_price_symbol_too():
    tmap = _tmap()
    # not read tonight (no CIK / a failed read) -> no verdict, never a guess
    assert (
        successor_ciks(
            stored_cik="0001711570", ticker="UROY", price_symbol=None, listed=None, ticker_map=tmap
        )
        == {}
    )
    # the price_symbol counts as ours too (a name priced under another symbol)
    assert successor_ciks(
        stored_cik="0001711570", ticker="ZZZZ", price_symbol="uroy", listed=(), ticker_map=tmap
    ) == {"0002143673": ("UROY",)}
    # SEC's map naming the STORED CIK is not a successor
    assert (
        successor_ciks(
            stored_cik="2143673", ticker="UROY", price_symbol=None, listed=(), ticker_map=tmap
        )
        == {}
    )


def test_the_ticker_map_parses_both_published_shapes_and_the_evidence_readers_read_real_docs():
    assert _tmap()["UROY"] == frozenset({"0002143673"})
    plain = {"0": {"cik_str": 2143673, "ticker": "uroy", "title": "Uranium Royalty Corp."}}
    assert parse_ticker_map(plain) == {"UROY": frozenset({"0002143673"})}
    assert cik10("1711570") == "0001711570" and cik10("x") is None and cik10("0") is None
    (k12b,) = succession_filings(fx.successor_doc("UROY"))
    assert (k12b.form, k12b.filed, k12b.accession) == (
        "8-K12B",
        date(2026, 7, 28),
        "0001493152-26-034947",
    )
    assert "5.03" in k12b.items and "3.01" in k12b.items
    (dereg,) = deregistration_filings(fx.stored_doc("REAX"))
    assert (dereg.form, dereg.filed, dereg.accession) == (
        "15-12G",
        date(2026, 9, 4),
        "0001104659-26-105528",
    )


# --- the nightly unit, on the real DB ------------------------------------------------------------------


def _seed(db) -> dict[str, uuid.UUID]:
    return {t: fx.insert_security(db, t) for t in _ALL}


def _results(ids: dict[str, uuid.UUID]) -> list[ThesisRunResult]:
    nb, ee = uuid.uuid4(), uuid.uuid4()
    return [
        ThesisRunResult(
            thesis_id=nb,
            name="Nuclear Buildout",
            recorded=False,
            ingested=[fx.name_result("UROY", ids["UROY"]), fx.name_result("UCLE", ids["UCLE"])],
        ),
        ThesisRunResult(
            thesis_id=ee,
            name="The Everything Exchange",
            recorded=False,
            ingested=[fx.name_result("REAX", ids["REAX"]), fx.name_result("HLLK", ids["HLLK"])],
        ),
    ]


def _scan(results, *, live=None, cache=None, previous=lambda: None) -> SuccessorScan:
    return run_successor_scan(
        results=results,
        allow_live=True,
        connect=connect,
        previous=previous,
        clock=time.perf_counter,
        live_client=live or fx.FixtureEdgar(fx.network_docs("UROY", "REAX")),
        cache_client=cache or fx.FixtureEdgar(fx.cache_docs(*_ALL)),
    )


def _counts(db) -> dict[str, int]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        tables = sorted(r["table_name"] for r in cur.fetchall())
        out = {}
        for t in tables:
            cur.execute(f'SELECT count(*) AS n FROM "{t}"')  # noqa: S608 — catalog names
            out[t] = cur.fetchone()["n"]
    return out


def test_the_scan_flags_EXACTLY_the_two_successors_with_their_REAL_evidence(db):
    ids = _seed(db)
    results = _results(ids)
    live = fx.FixtureEdgar(fx.network_docs("UROY", "REAX"))
    cache = fx.FixtureEdgar(fx.cache_docs(*_ALL))

    scan = _scan(results, live=live, cache=cache)

    assert scan.evaluated and scan.error is None
    assert scan.judged == 4 and scan.no_ticker == 3  # UROY, REAX, UCLE list none; HLLK lists SDCO
    assert [r.ticker for r in scan.rows] == ["REAX", "UROY"]  # HLLK and UCLE are not flagged
    reax, uroy = scan.rows
    assert (uroy.security_id, uroy.stored_cik, uroy.recommended) == (
        ids["UROY"],
        "0001711570",
        "0002143673",
    )
    assert uroy.old_entity_name == "Uranium Royalty Corp. /CA"
    assert uroy.old_former_names[0].name == "Uranium Royalty Corp."
    assert [(d.form, d.filed) for d in uroy.deregistrations] == [("15-12G", date(2026, 7, 31))]
    (c,) = uroy.candidates
    assert c.entity_name == "Uranium Royalty Corp." and c.edgar_tickers == ("UROY",)
    assert [(f.form, f.filed) for f in c.succession_filings] == [("8-K12B", date(2026, 7, 28))]
    # the identity an Adopt applies is the enrich_identity derivation of the successor's doc, verbatim
    assert c.identity == parse_identity(fx.successor_doc("UROY")).model_dump(mode="json")
    assert c.identity["files_domestic_forms"] is True and c.identity["recent_foreign_form"] is None
    assert uroy.theses == ((results[0].thesis_id, "Nuclear Buildout"),)
    assert reax.theses == ((results[1].thesis_id, "The Everything Exchange"),)
    assert reax.recommended == "0002136387"
    assert reax.candidates[0].entity_name == "Real REMAX Group Inc."
    assert reax.candidates[0].former_names[0].name == "Rome Wildlife, Inc."
    assert uroy.label == "UROY -> CIK 0002143673 (8-K12B 2026-07-28)"
    assert reax.label == "REAX -> CIK 0002136387 (8-K12B 2026-08-24)"
    # COST: the ticker map once + one successor read per flagged name; the old CIK's evidence comes from
    # the cache-only client (the ingest's copy), never the network
    assert scan.fetches == 3 and live.keys[0] == TICKER_MAP_CACHE_KEY
    assert sorted(live.keys[1:]) == [
        "submissions/CIK0002136387.json",
        "submissions/CIK0002143673.json",
    ]
    assert sorted(cache.keys) == [
        "submissions/CIK0001711570.json",
        "submissions/CIK0001862461.json",
    ]


def test_the_DETECTOR_WRITES_NOTHING(db):
    ids = _seed(db)
    before = _counts(db)
    scan = _scan(_results(ids))
    assert len(scan.rows) == 2
    assert _counts(db) == before


def test_a_night_with_no_no_ticker_member_makes_NO_request_and_opens_no_connection():
    sid = uuid.uuid4()
    live = fx.FixtureEdgar({})

    def _no_conn():
        raise AssertionError("no connection on a night with nothing to judge")

    scan = run_successor_scan(
        results=[
            ThesisRunResult(
                thesis_id=uuid.uuid4(), name="T", ingested=[fx.name_result("HLLK", sid)]
            )
        ],
        allow_live=True,
        connect=_no_conn,
        previous=lambda: None,
        clock=time.perf_counter,
        live_client=live,
        cache_client=fx.FixtureEdgar({}),
    )
    assert scan.evaluated and scan.rows == () and scan.judged == 1 and scan.no_ticker == 0
    assert live.live_fetches == 0 and scan.fetches == 0


def test_a_NO_LIVE_pass_does_not_scan():
    scan = run_successor_scan(
        results=[],
        allow_live=False,
        connect=lambda: (_ for _ in ()).throw(AssertionError("never")),
        previous=lambda: (_ for _ in ()).throw(AssertionError("never")),
        clock=time.perf_counter,
    )
    assert scan == SuccessorScan() and scan.payload()["successor_cik"]["evaluated"] is False


def test_a_FAULT_is_reported_never_raised(db):
    """The ticker map's fetch fails: the scan returns not-evaluated with the error (printed loud, shown on
    Admin); the pass is never failed, and ``previous`` is never consulted."""
    ids = _seed(db)
    live = fx.FixtureEdgar(fx.network_docs(), fail=frozenset({TICKER_MAP_CACHE_KEY}))
    scan = _scan(
        _results(ids), live=live, previous=lambda: (_ for _ in ()).throw(AssertionError("never"))
    )
    assert scan.evaluated is False and scan.rows == ()
    assert scan.error.startswith("OSError: simulated fetch failure")
    assert report_lines(scan)[0].startswith("SUCCESSOR-CIK DETECTOR FAILED")


def test_a_failed_SUCCESSOR_read_still_flags_the_row_but_carries_no_identity(db):
    ids = _seed(db)
    live = fx.FixtureEdgar(
        fx.network_docs("UROY", "REAX"),
        fail=frozenset({"submissions/CIK0002143673.json"}),
    )
    scan = _scan(_results(ids), live=live)
    uroy = next(r for r in scan.rows if r.ticker == "UROY")
    (c,) = uroy.candidates
    assert c.identity is None and c.error.startswith("OSError")
    assert (
        uroy.recommended == "0002143673"
    )  # flagged by the rule; the evidence is display, not a gate


# --- the page fires ONCE, across passes with the REAL artifact writer ----------------------------------


def _write(results, scan: SuccessorScan, at: datetime, base) -> None:
    write_cron_run_log(
        results,
        asof=_OCT16,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        base_dir=base,
        successor=scan,
    )


def test_the_page_fires_ONCE_new_then_known_then_new_again_after_an_undo(db, tmp_path):
    ids = _seed(db)
    results = _results(ids)
    at = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)

    def _previous():
        return previous_successor_keys(base_dir=tmp_path)

    # night 1 — no baseline yet: the inventory is news, ONCE
    s1 = _scan(results, previous=_previous)
    assert [r.ticker for r in s1.new] == ["REAX", "UROY"]
    h1 = assess_health(
        [], asof=_OCT16, allow_live=True, successor_cik_new=s1.new_labels, freeze_check=False
    )
    assert h1 is not None and h1.successor_cik_new == (
        "REAX -> CIK 0002136387 (8-K12B 2026-08-24)",
        "UROY -> CIK 0002143673 (8-K12B 2026-07-28)",
    )
    assert "now file under a NEW CIK" in h1.label
    _write(results, s1, at, tmp_path)

    # night 2 — the same two: known, silent (one quiet report line), still on the artifact
    s2 = _scan(results, previous=_previous)
    assert s2.new == () and len(s2.rows) == 2
    assert (
        assess_health(
            [], asof=_OCT16, allow_live=True, successor_cik_new=s2.new_labels, freeze_check=False
        )
        is None
    )
    assert report_lines(s2) == [
        "2 known successor-CIK name(s) awaiting the operator's Adopt (Admin page): REAX, UROY"
    ]
    _write(results, s2, at + timedelta(days=1), tmp_path)

    # night 3 — UROY adopted (its new CIK lists UROY: not flagged), REAX still known
    adopted = [
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="The Everything Exchange",
            ingested=[fx.name_result("REAX", ids["REAX"])],
        )
    ]
    s3 = _scan(adopted, previous=_previous)
    assert [r.ticker for r in s3.rows] == ["REAX"] and s3.new == ()
    _write(adopted, s3, at + timedelta(days=2), tmp_path)

    # night 4 — the adopt was UNDONE: UROY is flagged again, and pages ONCE as the news it is
    s4 = _scan(results, previous=_previous)
    assert [r.ticker for r in s4.new] == ["UROY"]


def test_an_unreadable_baseline_errs_LOUD_every_row_reads_new(db):
    ids = _seed(db)

    def _broken():
        raise OSError("run home unreadable")

    scan = _scan(_results(ids), previous=_broken)
    assert scan.evaluated and [r.ticker for r in scan.new] == ["REAX", "UROY"]


def test_the_baseline_rule_and_the_artifact_readers(db, tmp_path):
    ids = _seed(db)
    results = _results(ids)
    scan = _scan(results)
    at = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)
    assert previous_successor_keys(base_dir=tmp_path) is None  # no artifact: None, not an empty set
    _write(results, scan, at, tmp_path)
    (doc,) = cron_run_log.list_run_logs(base_dir=tmp_path)
    assert is_baseline(doc)
    assert keys_of(doc) == {r.key for r in scan.rows}
    assert new_labels_of(doc) == scan.new_labels
    assert doc["successor_cik"]["fetches"] == 3 and doc["successor_cik"]["judged"] == 4
    # a failed scan, a no-live pass, an unfinished pass, a partial catch-up: never the baseline
    assert not is_baseline({**doc, "successor_cik": {"evaluated": False, "error": "x"}})
    assert not is_baseline({**doc, "mode": "no-live"})
    assert not is_baseline({**doc, "status": "running"})
    assert not is_baseline(
        {**doc, "theses": [{**doc["theses"][0], "skipped": "on-time row stands"}]}
    )
    assert not is_baseline({k: v for k, v in doc.items() if k != "successor_cik"})  # pre-T7
