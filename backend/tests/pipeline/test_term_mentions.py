"""The weekly EDGAR term-mentions count (``python -m pipeline.term_mentions``, migration 0071) — display data for the
Narrative Adoption Curve dashboard, never a call input.

The parse is tested on REAL EFTS replies (``tests/fixtures/efts_month/``: two recorded live, date-ranged; two from
the discovery cache — the README says which). The run is tested DB-backed with a fake client that serves those
same recorded replies by (term, month) and never reaches the network; every idempotency claim COUNTS the table
before and after (the as-of read dedups, so a duplicate append would hide behind a correct read).
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from db.bitemporal import _FACT_IDENTITY, DISPLAY_ONLY_FACT_TABLES, append_fact
from db.public_clock import PUBLIC_TIME, SCOPE_COL, PublicTime
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.fulltext import EftsErrorBody
from pipeline import term_mentions as tm
from pipeline.run_lock import (
    LOCK_ID_MASTER_REFRESH,
    LOCK_ID_TERM_MENTIONS,
    acquire_job_lock,
    acquire_pass_lock,
)
from signals.base import PointInTimeData, SignalPointInTimeData

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "efts_month"
PSI = "psilocybin_2026-09"  # 14 documents, 6 filers, exact (live, date-ranged)
AI = "artificial_intelligence_2026-08"  # 5,706 documents, sampled (live, date-ranged)
THERAPY = "therapy_all_time"  # a capped "gte" total (cached)
CLASSIC = "classic_psychedelics_all_time"  # 106 documents, exact by the entity aggregation (cached)
_NOW = datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc)  # Saturday 10:00 ET
AUG, SEP, OCT = date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)


def _reply(name: str) -> dict:
    return json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))


def _page_ciks(doc: dict) -> set[str]:
    return {c for h in doc["hits"]["hits"] for c in h["_source"]["ciks"]}


# --- the parse, on real replies ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [PSI, AI, THERAPY, CLASSIC])
def test_the_four_form_groups_sum_to_the_documents_on_every_real_reply(name):
    doc = _reply(name)
    c = tm.parse_month(doc)
    assert c.reports + c.events + c.issuance + c.other == c.documents
    by_group = {g: 0 for g in ("reports", "events", "issuance", "other")}
    for b in doc["aggregations"]["form_filter"]["buckets"]:
        by_group[tm.form_group(b["key"])] += b["doc_count"]
    assert (c.reports, c.events, c.issuance) == (
        by_group["reports"],
        by_group["events"],
        by_group["issuance"],
    )
    # sum_other_doc_count (forms past the top 30) lands in other
    assert c.other == by_group["other"] + (
        c.documents - sum(b["doc_count"] for b in doc["aggregations"]["form_filter"]["buckets"])
    )


def test_an_exact_total_is_hits_total_and_its_groups_are_the_forms_filed():
    c = tm.parse_month(_reply(PSI))  # 9 6-K + 1 10-K, 2 8-K, a 253G1 and an N-CSR
    assert (c.documents, c.reports, c.events, c.issuance, c.other) == (14, 10, 2, 0, 2)


def test_a_CAPPED_total_counts_from_the_form_aggregation_which_is_not_capped():
    doc = _reply(THERAPY)
    assert doc["hits"]["total"] == {"value": 10000, "relation": "gte"}
    forms = doc["aggregations"]["form_filter"]
    agg_total = sum(b["doc_count"] for b in forms["buckets"]) + forms["sum_other_doc_count"]
    c = tm.parse_month(doc)
    assert c.documents == agg_total == 339_218 and c.documents > 10_000
    assert c.events == 73_223  # the 8-K bucket alone is above the capped total
    assert c.filers_sampled is True


def test_filers_are_EXACT_when_the_page_holds_every_document():
    doc = _reply(PSI)
    c = tm.parse_month(doc)
    assert c.documents == len(doc["hits"]["hits"]) == 14
    assert c.filers_sampled is False and set(c.filer_ciks) == _page_ciks(doc) and c.filers == 6


def test_filers_are_EXACT_when_the_entity_aggregation_lists_every_filer():
    """106 documents, 100 on the page — but the entity aggregation lists every filer with its CIK, so the union
    is the whole set: 9 filers where the page alone shows 8."""
    doc = _reply(CLASSIC)
    assert len(doc["hits"]["hits"]) < doc["hits"]["total"]["value"]
    assert doc["aggregations"]["entity_filter"]["sum_other_doc_count"] == 0
    c = tm.parse_month(doc)
    assert c.filers_sampled is False
    assert len(_page_ciks(doc)) == 8 and c.filers == 9 and _page_ciks(doc) < set(c.filer_ciks)


def test_filers_are_AT_LEAST_otherwise():
    doc = _reply(AI)
    c = tm.parse_month(doc)
    assert doc["hits"]["total"] == {"value": 5706, "relation": "eq"}
    assert c.filers_sampled is True
    assert _page_ciks(doc) <= set(c.filer_ciks) and c.filers == 80


def test_the_recorded_replies_are_the_date_ranged_request_the_job_makes():
    """The two live fixtures prove EFTS honors the month: SEC echoes a ``file_date`` range filter, and every
    hit of the 14-document reply (all on the page) was filed inside September 2026."""
    psi = _reply(PSI)
    assert psi["query"]["query"]["bool"]["filter"] == [
        {"range": {"file_date": {"gte": "2026-09-01", "lte": "2026-09-30"}}}
    ]
    assert all(
        "2026-09-01" <= h["_source"]["file_date"] <= "2026-09-30" for h in psi["hits"]["hits"]
    )
    ai = _reply(AI)
    assert ai["query"]["query"]["bool"]["filter"] == [
        {"range": {"file_date": {"gte": "2026-08-01", "lte": "2026-08-31"}}}
    ]
    assert tm.month_url("psilocybin", SEP) == (
        "https://efts.sec.gov/LATEST/search-index?q=%22psilocybin%22&dateRange=custom"
        "&startdt=2026-09-01&enddt=2026-09-30"
    )


@pytest.mark.parametrize(
    "form, group",
    [
        ("10-K", "reports"),
        ("10-K/A", "reports"),
        ("10-Q", "reports"),
        ("20-F", "reports"),
        ("40-F/A", "reports"),
        ("6-K", "reports"),
        ("10-KT", "reports"),
        ("10-QT/A", "reports"),
        ("8-K", "events"),
        ("8-K/A", "events"),
        ("S-1", "issuance"),
        ("S-1/A", "issuance"),
        ("S-1MEF", "issuance"),
        ("S-3ASR", "issuance"),
        ("F-3ASR", "issuance"),
        ("F-1/A", "issuance"),
        ("S-11", "issuance"),
        ("424B5", "issuance"),
        ("424B3", "issuance"),
        ("FWP", "issuance"),
        ("424H", "other"),
        ("S-4", "other"),
        ("8-K12B", "other"),
        ("DEF 14A", "other"),
        ("N-PX", "other"),
        ("10KSB", "other"),
    ],
)
def test_the_form_groups(form, group):
    assert tm.form_group(form) == group


def test_counts_that_cannot_be_stated_honestly_FAIL_never_clamp():
    """Direct-input contracts (synthetic shapes, not SEC data): each must raise, never return a row."""
    with pytest.raises(EftsErrorBody):  # SEC's 2xx error reply (MEASURED 2026-10-23)
        tm.parse_month({"errorType": "ConnectionError", "errorMessage": "getaddrinfo EBUSY"})
    capped_no_agg = {"hits": {"total": {"value": 10000, "relation": "gte"}, "hits": []}}
    with pytest.raises(tm.MonthParseError, match="capped"):
        tm.parse_month(capped_no_agg)
    over = {
        "hits": {"total": {"value": 3, "relation": "eq"}, "hits": []},
        "aggregations": {
            "form_filter": {"buckets": [{"key": "8-K", "doc_count": 5}], "sum_other_doc_count": 0}
        },
    }
    with pytest.raises(tm.MonthParseError, match="exceed"):
        tm.parse_month(over)
    no_forms = {"hits": {"total": {"value": 2, "relation": "eq"}, "hits": []}}
    with pytest.raises(tm.MonthParseError, match="no form_filter"):
        tm.parse_month(no_forms)


# --- the window, the request, the terms -------------------------------------------------------------------


def test_the_window_is_the_N_calendar_months_ending_with_the_current_one():
    w = tm.month_window(date(2026, 10, 29), 36)
    assert len(w) == 36 and w[0] == date(2023, 11, 1) and w[-1] == date(2026, 10, 1)
    assert tm.month_window(date(2026, 1, 5), 2) == [date(2025, 12, 1), date(2026, 1, 1)]
    assert tm.month_last_day(date(2024, 2, 1)) == date(2024, 2, 29)
    assert tm.month_ranges([date(2026, 1, 1), date(2026, 2, 1), date(2026, 4, 1)]) == (
        "2026-01..2026-02, 2026-04"
    )


def test_the_cache_key_is_its_own_prefix_and_never_shared_by_two_terms():
    key = tm.month_cache_key("Psilocybin", SEP)
    assert key.startswith("efts_month/") and key.endswith("_2026-09.json")
    assert key == tm.month_cache_key("psilocybin", SEP)  # one term, case-insensitively
    assert tm.month_cache_key("a.b", SEP) != tm.month_cache_key("a b", SEP)  # same slug, two terms


def test_SIGNAL_terms_only_stripped_and_deduplicated():
    ts = [
        {"term": " psilocybin ", "tier": "signal"},
        {"term": "Psilocybin", "tier": "signal"},  # the same term (case-insensitively)
        {"term": "   ", "tier": "signal"},
        {"term": "MDMA", "tier": "broad"},  # BROAD is never counted
        {"term": "ibogaine", "tier": "signal"},
        "not-an-entry",
    ]
    assert tm.signal_terms(ts) == ("psilocybin", "ibogaine")


# --- the run (DB-backed, a fake client serving the recorded replies) ------------------------------------


class FakeEfts:
    """Serves REAL recorded replies by (case-folded term, ``YYYY-MM``) — never the network. A value that is an
    exception is raised (a failed request, after the client's retries). Counts every request."""

    def __init__(self, replies: dict[tuple[str, str], object], default: object = None) -> None:
        self.replies = replies
        self.default = default
        self.calls: list[tuple[str, str]] = []
        self.live_fetches = 0
        self.live_completed = 0
        self._lock = threading.Lock()
        self._tl = threading.local()
        self.on_call = None

    def thread_completed(self) -> int:
        return getattr(self._tl, "n", 0)

    def get_json(self, url, cache_key, *, validate=None):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        key = (q["q"][0].strip('"').casefold(), q["startdt"][0][:7])
        assert cache_key == tm.month_cache_key(
            q["q"][0].strip('"'), date.fromisoformat(q["startdt"][0])
        )
        with self._lock:
            self.calls.append(key)
            self.live_fetches += 1
        if self.on_call is not None:
            self.on_call()
        r = self.replies.get(key, self.default)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise CacheMiss(f"{cache_key} not cached (live pulls disabled)")
        doc = _reply(r)
        if validate is not None:
            validate(doc)
        with self._lock:
            self.live_completed += 1
        self._tl.n = self.thread_completed() + 1
        return doc


def _card(db, name: str, terms, *, broad=(), archived: bool = False) -> UUID:
    tid = uuid.uuid4()
    ts = [{"term": t, "tier": "signal"} for t in terms] + [
        {"term": t, "tier": "broad"} for t in broad
    ]
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, term_set, archived_at) "
            "VALUES (%s, %s, %s, 'n', %s::jsonb, %s)",
            (tid, DEFAULT_TENANT_ID, name, json.dumps(ts), _NOW if archived else None),
        )
    db.commit()
    return tid


def _count(db, table: str = "fact_term_mentions") -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test SQL
        return cur.fetchone()["n"]


def _newest(db, tid: UUID) -> dict[tuple[str, date], dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (term, month) * FROM fact_term_mentions WHERE thesis_id = %s "
            "ORDER BY term, month, recorded_at DESC, id DESC",
            (tid,),
        )
        return {(r["term"], r["month"]): r for r in cur.fetchall()}


def _run(tmp_path, client, **kw) -> int:
    args = dict(
        schedule=tm.BACKFILL,
        apply=True,
        live=True,
        tenant_id=DEFAULT_TENANT_ID,
        months=3,
        runs_dir=tmp_path / "runs",
        now=lambda: _NOW,
        client=client,
        workers=2,
    )
    args.update(kw)
    return tm.run(**args)


def _artifact(tmp_path) -> dict:
    return tm.latest_run(runs_dir=tmp_path / "runs")


def test_a_DRY_RUN_makes_no_request_writes_nothing_and_prints_both_costs(db, tmp_path, capsys):
    _card(db, "Psychedelics", ["psilocybin", "ibogaine"], broad=["MDMA"])
    _card(db, "Mental Health", ["Psilocybin"])
    _card(db, "Old card", ["mescaline"], archived=True)  # archived: never counted
    fake = FakeEfts({}, default=PSI)
    assert _run(tmp_path, fake, apply=False) == 0
    out = capsys.readouterr().out
    assert fake.calls == [] and _count(db) == 0 and not (tmp_path / "runs").exists()
    assert "2 cards · 3 SIGNAL terms (2 distinct) · 6 requests planned" in out
    assert "the backfill needs 6 requests now" in out
    assert "each weekly run after it: 2 distinct terms × 2 = 4 requests" in out
    assert '"Psilocybin": 3 months (2026-08..2026-10) · Mental Health, Psychedelics' in out
    assert "mescaline" not in out and "MDMA" not in out


def test_BACKFILL_stores_every_month_once_per_shared_term_then_a_rerun_appends_nothing(
    db, tmp_path, capsys
):
    a = _card(db, "Psychedelics", ["psilocybin", "ibogaine"])
    b = _card(db, "Mental Health", ["Psilocybin"])
    fake = FakeEfts({}, default=PSI)
    repairs = _count(db, "data_repair")
    assert _run(tmp_path, fake) == 0
    # ONE request per (term, month) across cards: 2 distinct terms x 3 months, not 3 card-terms x 3
    assert sorted(fake.calls) == sorted(
        [(t, m) for t in ("psilocybin", "ibogaine") for m in ("2026-08", "2026-09", "2026-10")]
    )
    assert _count(db) == 9  # ...fanned out: 3 card-terms x 3 months
    rows_b = _newest(db, b)
    assert {k[0] for k in rows_b} == {"Psilocybin"}  # stored under the card's OWN spelling
    row = rows_b[("Psilocybin", SEP)]
    assert (row["documents"], row["reports"], row["events"], row["issuance"], row["other"]) == (
        14,
        10,
        2,
        0,
        2,
    )
    assert row["filers"] == 6 and row["filers_sampled"] is False
    assert row["valid_from"] == date(2026, 9, 30) and "startdt=2026-09-01" in row["source_ref"]
    assert _newest(db, a)[("psilocybin", AUG)]["new_filers"] is None  # no earlier month stored
    assert _newest(db, a)[("psilocybin", SEP)]["new_filers"] == 0  # the same six filers again
    out = capsys.readouterr().out
    assert (
        "term-mentions: backfill · 2 cards · 3 SIGNAL terms (2 distinct) · 6 requests (0 from cache) · "
        "9 rows appended · 0 unchanged · 0 failed · 0 deferred"
    ) in out
    doc = _artifact(tmp_path)
    assert doc["status"] == "finished" and doc["counts"]["network_attempts"] == 6
    assert doc["attention"] is False
    assert _count(db, "data_repair") == repairs  # display data flow: never a repair journal row

    # IDEMPOTENT, by COUNT: a backfill re-run fetches nothing (every month is stored) and appends nothing
    fake.calls.clear()
    assert _run(tmp_path, fake) == 0
    assert fake.calls == [] and _count(db) == 9
    # ...and the weekly re-fetches exactly two months per distinct term, identical -> no row
    assert _run(tmp_path, fake, schedule=tm.WEEKLY) == 0
    assert sorted(fake.calls) == sorted(
        [(t, m) for t in ("psilocybin", "ibogaine") for m in ("2026-09", "2026-10")]
    )
    assert _count(db) == 9
    assert "4 requests (0 from cache) · 0 rows appended · 6 unchanged" in capsys.readouterr().out


def test_NEW_FILERS_is_NULL_for_the_first_month_then_counts_against_EVERY_earlier_month(
    db, tmp_path
):
    """Within one run: August (6 filers) is the first month (NULL); September (the 9-filer reply) counts the CIKs
    August did not have; October (the 6 again) counts none — they were all seen in an earlier month.
    """
    tid = _card(db, "Psychedelics", ["psilocybin"])
    fake = FakeEfts(
        {
            ("psilocybin", "2026-08"): PSI,
            ("psilocybin", "2026-09"): CLASSIC,
            ("psilocybin", "2026-10"): PSI,
        }
    )
    assert _run(tmp_path, fake) == 0
    rows = _newest(db, tid)
    aug = set(rows[("psilocybin", AUG)]["filer_ciks"])
    sep = set(rows[("psilocybin", SEP)]["filer_ciks"])
    assert rows[("psilocybin", AUG)]["new_filers"] is None
    assert rows[("psilocybin", SEP)]["new_filers"] == len(sep - aug) > 0
    assert rows[("psilocybin", OCT)]["new_filers"] == 0


def test_a_weekly_refetch_appends_a_version_ONLY_where_a_value_changed(db, tmp_path):
    tid = _card(db, "Psychedelics", ["psilocybin"])
    fake = FakeEfts({}, default=PSI)
    assert _run(tmp_path, fake) == 0
    assert _count(db) == 3
    # a late filing settles September: its reply now differs; October's does not
    fake.replies[("psilocybin", "2026-09")] = CLASSIC
    assert _run(tmp_path, fake, schedule=tm.WEEKLY) == 0
    assert (
        _count(db) == 4
    )  # exactly one new version (September); October unchanged, August not fetched
    rows = _newest(db, tid)
    assert rows[("psilocybin", SEP)]["documents"] == 106
    # the accessor reads the newest version, one row per (term, month)
    pit = PointInTimeData(db, asof=date(2026, 10, 31), known_at=datetime.now(timezone.utc))
    got = {(r["term"], r["month"]): r["documents"] for r in pit.term_mentions(tid)}
    assert got == {("psilocybin", AUG): 14, ("psilocybin", SEP): 106, ("psilocybin", OCT): 14}


def test_a_FAILED_month_is_never_a_zero_it_is_listed_and_the_later_months_wait(
    db, tmp_path, capsys
):
    tid = _card(db, "Psychedelics", ["psilocybin"])
    fault = EftsErrorBody("EFTS reply is not a search-results page (errorType='ConnectionError')")
    fake = FakeEfts({("psilocybin", "2026-09"): fault}, default=PSI)
    assert _run(tmp_path, fake) == 0
    assert set(_newest(db, tid)) == {("psilocybin", AUG)}  # September unstored, October deferred
    out = capsys.readouterr().out
    assert 'term-mentions: FAILED "psilocybin" 2026-09 — EftsErrorBody' in out
    assert "1 rows appended · 0 unchanged · 1 failed · 1 deferred" in out
    doc = _artifact(tmp_path)
    assert [(f["term"], f["month"]) for f in doc["failed"]] == [("psilocybin", "2026-09")]
    assert doc["deferred"][0]["months"] == "2026-10" and doc["attention"] is True
    # the fault clears: the next run fills September, then October, counted against August
    fake.replies.clear()
    assert _run(tmp_path, fake) == 0
    rows = _newest(db, tid)
    assert set(rows) == {("psilocybin", m) for m in (AUG, SEP, OCT)}
    assert rows[("psilocybin", SEP)]["new_filers"] == 0 and _count(db) == 3


def test_a_cache_only_run_lists_a_cache_miss_as_a_failure(db, tmp_path, capsys):
    _card(db, "Psychedelics", ["psilocybin"])
    assert _run(tmp_path, FakeEfts({}), live=False) == 0  # nothing cached
    assert _count(db) == 0
    assert "3 failed · 0 deferred" in capsys.readouterr().out
    assert {f["error"].split(":")[0] for f in _artifact(tmp_path)["failed"]} == {"CacheMiss"}


def test_a_month_stored_BEFORE_an_earlier_one_is_re_derived_without_a_request(db, tmp_path):
    """--months 1 stores October first (new_filers NULL: no earlier month); a later --months 3 fills August and
    September — and October's new_filers is re-derived from the stored CIK sets (a new version, no request).
    """
    tid = _card(db, "Psychedelics", ["psilocybin"])
    fake = FakeEfts({("psilocybin", "2026-10"): CLASSIC}, default=PSI)
    assert _run(tmp_path, fake, months=1) == 0
    assert _newest(db, tid)[("psilocybin", OCT)]["new_filers"] is None
    fake.calls.clear()
    assert _run(tmp_path, fake, months=3) == 0
    assert sorted(fake.calls) == [("psilocybin", "2026-08"), ("psilocybin", "2026-09")]
    rows = _newest(db, tid)
    oct_ciks = set(rows[("psilocybin", OCT)]["filer_ciks"])
    earlier = set(rows[("psilocybin", AUG)]["filer_ciks"]) | set(
        rows[("psilocybin", SEP)]["filer_ciks"]
    )
    assert rows[("psilocybin", OCT)]["new_filers"] == len(oct_ciks - earlier) > 0
    assert _count(db) == 4  # 3 months + October's re-derived version
    assert _artifact(tmp_path)["counts"]["rows_rederived"] == 1


def test_WEEKLY_on_an_EMPTY_table_is_a_no_op_never_the_first_backfill(db, tmp_path, capsys):
    _card(db, "Psychedelics", ["psilocybin"])
    fake = FakeEfts({}, default=PSI)
    assert _run(tmp_path, fake, schedule=tm.WEEKLY) == 0
    out = capsys.readouterr().out
    assert "no backfill yet" in out and "the first backfill is the operator's call" in out
    assert "(3 requests)" in out
    assert fake.calls == [] and _count(db) == 0 and not (tmp_path / "runs").exists()


def test_a_TERM_ADDED_later_gets_its_window_on_the_weekly(db, tmp_path):
    tid = _card(db, "Psychedelics", ["psilocybin"])
    fake = FakeEfts({}, default=PSI)
    assert _run(tmp_path, fake) == 0
    with db.cursor() as cur:
        cur.execute(
            "UPDATE thesis SET term_set = term_set || %s::jsonb WHERE id = %s",
            (json.dumps([{"term": "ibogaine", "tier": "signal"}]), tid),
        )
    db.commit()
    fake.calls.clear()
    assert _run(tmp_path, fake, schedule=tm.WEEKLY) == 0
    assert sorted(c for c in fake.calls if c[0] == "ibogaine") == [
        ("ibogaine", m) for m in ("2026-08", "2026-09", "2026-10")
    ]
    assert {k for k in _newest(db, tid) if k[0] == "ibogaine"} == {
        ("ibogaine", m) for m in (AUG, SEP, OCT)
    }


def test_the_job_REFUSES_in_the_window_holds_its_OWN_lock_and_YIELDS_to_a_pass(
    db, tmp_path, monkeypatch, capsys
):
    _card(db, "Psychedelics", ["psilocybin"])
    fake = FakeEfts({}, default=PSI)
    held = acquire_job_lock(lock_id=LOCK_ID_TERM_MENTIONS, holder="other", what="x")
    try:
        assert _run(tmp_path, fake) == 75
    finally:
        held.release()
    # its own id: never the call-of-record lock (1), the fact-writer gate (2), onboarding (3), editions (4) or
    # the company-list refresh (5)
    assert LOCK_ID_TERM_MENTIONS == 6 and LOCK_ID_MASTER_REFRESH == 5
    lock = acquire_pass_lock(holder="cron")
    try:
        assert _run(tmp_path, fake) == 75
    finally:
        lock.release()
    assert "yields to it" in capsys.readouterr().out
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_BEFORE_MIN", "60")
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_AFTER_MIN", "60")
    get_settings.cache_clear()
    try:
        late = datetime(2026, 10, 23, 2, 15, tzinfo=timezone.utc)  # 22:15 ET
        assert _run(tmp_path, fake, now=lambda: late) == 75
        assert "inside the nightly window" in capsys.readouterr().out
        assert _run(tmp_path, fake, now=lambda: late, apply=False) == 0  # a dry run is only a look
    finally:
        get_settings.cache_clear()
    assert fake.calls == [] and _count(db) == 0 and not (tmp_path / "runs").exists()


def test_the_run_STOPS_between_terms_when_a_pass_starts_and_keeps_what_it_wrote(
    db, tmp_path, capsys
):
    tid = _card(db, "Psychedelics", ["ibogaine", "psilocybin"])
    fake = FakeEfts({}, default=PSI)
    started: list = []
    gate = threading.Lock()

    def a_pass_starts():  # the first term's requests start the nightly (a real call-of-record lock)
        with gate:
            if not started:
                started.append(acquire_pass_lock(holder="cron"))

    fake.on_call = a_pass_starts
    try:
        assert _run(tmp_path, fake) == 75
    finally:
        for lk in started:
            lk.release()
    assert {k[0] for k in _newest(db, tid)} == {"ibogaine"}  # the first term (sorted) was written
    assert {c[0] for c in fake.calls} == {"ibogaine"}  # the second was never requested
    doc = _artifact(tmp_path)
    assert doc["status"] == "stopped" and "call-of-record" in doc["stopped"]
    assert [(d["term"], d["months"]) for d in doc["deferred"]] == [
        ("psilocybin", "2026-08..2026-10")
    ]
    assert "STOPPED" in capsys.readouterr().out


def test_the_boot_catch_up_runs_only_for_a_missed_week(db, tmp_path):
    _card(db, "Psychedelics", ["psilocybin"])
    fake = FakeEfts({}, default=PSI)
    assert _run(tmp_path, fake, if_stale_days=7) == 0  # no applied run on record: never the first
    assert fake.calls == []
    assert _run(tmp_path, fake) == 0  # the operator's backfill
    fake.calls.clear()
    assert (
        _run(tmp_path, fake, schedule=tm.WEEKLY, if_stale_days=7) == 0
    )  # it just ran: no catch-up
    assert fake.calls == []
    later = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
    assert _run(tmp_path, fake, schedule=tm.WEEKLY, if_stale_days=7, now=lambda: later) == 0
    assert fake.calls  # ten days on: the missed week runs


def test_a_bad_thesis_scope_is_refused(db, tmp_path, capsys):
    tid = _card(db, "Old", ["psilocybin"], archived=True)
    assert _run(tmp_path, FakeEfts({}), thesis_id=tid) == 1
    assert "is not a live card" in capsys.readouterr().out


# --- the table: append-only, its CHECKs -------------------------------------------------------------------


def _row(tid: UUID, month: date = SEP, **over) -> dict:
    c = tm.parse_month(_reply(PSI))
    row = {
        "tenant_id": DEFAULT_TENANT_ID,
        "thesis_id": tid,
        "term": "psilocybin",
        "month": month,
        "documents": c.documents,
        "reports": c.reports,
        "events": c.events,
        "issuance": c.issuance,
        "other": c.other,
        "filers": c.filers,
        "filers_sampled": c.filers_sampled,
        "filer_ciks": list(c.filer_ciks),
        "new_filers": None,
        "source_ref": tm.month_url("psilocybin", month),
        "fetched_at": _NOW,
        "valid_from": tm.month_last_day(month),
    }
    row.update(over)
    return row


def test_the_table_is_APPEND_ONLY_and_its_counts_are_CHECKED(db):
    tid = _card(db, "Psychedelics", ["psilocybin"])
    fid = append_fact(db, "fact_term_mentions", _row(tid))
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE fact_term_mentions SET documents = 0 WHERE id = %s", (fid,))
    db.rollback()
    for bad in (
        {"other": 3},  # the four groups no longer sum to documents
        {"filers": 5},  # filers is the CIK set's size
        {"month": date(2026, 9, 2), "valid_from": date(2026, 9, 30)},  # a month is its first day
        {"valid_from": date(2026, 9, 29)},  # valid_from is the month's last day
        {"new_filers": 7},  # never more new filers than filers
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            append_fact(db, "fact_term_mentions", _row(tid, **bad))
        db.rollback()


# --- the accessor: both clocks, off the detectors' protocol -----------------------------------------------


def test_the_accessor_gates_on_valid_from_and_recorded_at_and_reads_the_public_clock(db):
    """Three versions, each recorded at its own instant:
    - MAY (valid_from 05-31), recorded 06-02; a RE-COUNT of May recorded 07-10;
    - JUNE (valid_from 06-30), recorded 07-02."""
    tid = _card(db, "Psychedelics", ["psilocybin"])
    may, june = date(2026, 5, 1), date(2026, 6, 1)
    ai = tm.parse_month(_reply(AI))
    for row, rec in (
        (_row(tid, may), datetime(2026, 6, 2, 14, tzinfo=timezone.utc)),
        (_row(tid, june, new_filers=0), datetime(2026, 7, 2, 14, tzinfo=timezone.utc)),
        (
            _row(
                tid,
                may,
                documents=ai.documents,
                reports=ai.reports,
                events=ai.events,
                issuance=ai.issuance,
                other=ai.other,
                filers=ai.filers,
                filers_sampled=True,
                filer_ciks=list(ai.filer_ciks),
            ),
            datetime(2026, 7, 10, 14, tzinfo=timezone.utc),
        ),
    ):
        append_fact(db, "fact_term_mentions", {**row, "recorded_at": rec})
    db.commit()

    def docs(asof, known_at, clock="record"):
        pit = PointInTimeData(db, asof=asof, known_at=known_at, clock=clock)
        return {r["month"]: r["documents"] for r in pit.term_mentions(tid)}

    # RECORD clock: valid_from <= asof (June has not closed on 06-15) ...
    assert docs(date(2026, 6, 15), datetime(2026, 6, 15, 23, tzinfo=timezone.utc)) == {may: 14}
    # ... and recorded_at <= known_at (the 07-10 re-count is not yet held on 07-05)
    assert docs(date(2026, 7, 5), datetime(2026, 7, 5, 23, tzinfo=timezone.utc)) == {
        may: 14,
        june: 14,
    }
    assert docs(date(2026, 7, 15), datetime(2026, 7, 15, 23, tzinfo=timezone.utc)) == {
        may: 5706,
        june: 14,
    }
    # PUBLIC clock (a history view): a month counts from its valid_from, whenever we stored it — the re-count of
    # the closed May is read on 07-05; June is still invisible on 06-15
    assert docs(date(2026, 7, 5), known_at_for_asof(date(2026, 7, 5)), "public") == {
        may: 5706,
        june: 14,
    }
    assert docs(date(2026, 6, 15), known_at_for_asof(date(2026, 6, 15)), "public") == {may: 5706}
    assert "term_mentions" not in SignalPointInTimeData.__dict__  # display data: off the protocol


# --- the registries: thesis-scoped, public clock, out of the replay mirror -------------------------------


def test_the_registries_keep_the_table_live_readable_thesis_scoped_and_out_of_the_mirror():
    from replay.export import FACT_TABLES

    t = "fact_term_mentions"
    assert _FACT_IDENTITY[t] == ["term", "month"]
    assert PUBLIC_TIME[t] == PublicTime("date", "valid_from") and SCOPE_COL[t] == "thesis_id"
    assert t in DISPLAY_ONLY_FACT_TABLES and t not in FACT_TABLES
    assert FACT_TABLES == tuple(x for x in _FACT_IDENTITY if x not in DISPLAY_ONLY_FACT_TABLES)


def test_an_export_never_writes_the_table_and_the_mirror_opens_without_it(db, tmp_path):
    pytest.importorskip("duckdb")
    from replay.export import FACT_TABLES, export_snapshot
    from replay.pit import connect_mirror

    tid = _card(db, "Psychedelics", ["psilocybin"])
    append_fact(db, "fact_term_mentions", _row(tid))
    db.commit()
    for clock in ("record", "public"):
        out = tmp_path / clock
        manifest = export_snapshot(db, out, clock=clock)
        assert not (out / "fact_term_mentions.parquet").exists()
        assert "fact_term_mentions" not in manifest["tables"]
        con = connect_mirror(out)
        try:
            names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()
        assert set(FACT_TABLES) <= names and "fact_term_mentions" not in names
