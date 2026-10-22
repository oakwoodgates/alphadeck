"""The freeze page's RATIO FLOOR — FROZEN when fewer than ``Settings.freeze_completed_floor`` (50%) of the
EDGAR-eligible companies a live pass ingested got a COMPLETED SEC fetch (``pipeline/edgar_coverage.py``).

Real shapes, no fitting:

- the REAL counters end to end (DB-backed): ``EdgarClient`` through its real ``get_text`` / ``_fetch`` /
  ``polite_get`` (only the wire, ``httpx.get``, is stubbed) inside the REAL ``ingest_thesis`` on the test
  Postgres — per-name attribution, the no-CIK exclusion, the cross-thesis cache hit, and the July mechanism
  (a long-TTL client over a warm cache: nothing errors, almost nothing is fetched);
- two more prod run-of-record artifacts, copied out READ-ONLY and trimmed like #425's (thesis names replaced
  with ``thesis-<id8>``, the per-thesis ticker inventories dropped, the one run-level stale-tape label
  replaced by a placeholder; nothing a verdict reads was touched):
  * ``20260725T022958Z`` — 2026-07-24 22:29 ET: 4 EDGAR attempts across 255 basket names (1.6%), nothing
    errored, every call unchanged — the July freeze shape both earlier rules stayed silent on.
  * ``20261014T023001Z`` — 2026-10-13 22:30 ET: 710 attempts across 740 names (95.9%) — the LOWEST of the 25
    live passes since G1.
  No stored artifact carries per-company completions (none was written by O5 or later code), so a test that
  replays one through the floor states its mapping from the counts the artifact DOES hold, and says whether
  that mapping is a bound or a proxy.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.routers.admin import _admin_run_out
from db.session import DEFAULT_TENANT_ID
from domain.settings import Settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from pipeline import daily
from pipeline import ingest_thesis as IT
from pipeline.cron_run_log import build_run_payload
from pipeline.edgar_coverage import (
    DEFAULT_FREEZE_COMPLETED_FLOOR,
    EdgarCoverage,
    cik_key,
    clamp_floor,
    company_sets,
    coverage_of,
)
from pipeline.ingest_thesis import NameResult

_RUNS = Path(__file__).resolve().parent.parent / "fixtures" / "cron_runs"
_ASOF = date(2026, 10, 21)
_FLOOR = DEFAULT_FREEZE_COMPLETED_FLOOR
_T0 = datetime(2026, 10, 22, 2, 30, tzinfo=timezone.utc)
_T1 = datetime(2026, 10, 22, 3, 5, tzinfo=timezone.utc)


def _artifact(stem: str) -> dict:
    return json.loads((_RUNS / f"{stem}.json").read_text(encoding="utf-8"))


def _name(cik: str | None, completed: int = 0) -> NameResult:
    return NameResult(None, uuid.uuid4(), 0, 0, edgar_cik=cik, edgar_completed=completed)


def _thesis(names: list[NameResult], **kw) -> daily.ThesisRunResult:
    """A processed thesis built the way ``run_daily`` builds it: its names' facts through ``company_sets``."""
    r = daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", ingested=names, **kw)
    r.edgar_ciks, r.edgar_ciks_fresh = company_sets(names)
    return r


def _skipped() -> daily.ThesisRunResult:
    return daily.ThesisRunResult(
        thesis_id=uuid.uuid4(), name="T", skipped="on-time call-of-record already recorded"
    )


def _health(results, *, allow_live=True, floor=_FLOOR, **kw):
    return daily.assess_health(
        results,
        asof=_ASOF,
        allow_live=allow_live,
        edgar_coverage=coverage_of(results, floor=floor),
        **kw,
    )


def _round_trip(results, *, allow_live=True, floor=_FLOOR, catch_up=False):
    """The REAL writer then the REAL reader: what the Admin history shows for this pass."""
    payload = build_run_payload(
        results,
        asof=_ASOF,
        allow_live=allow_live,
        started_at=_T0,
        finished_at=_T1,
        catch_up=catch_up,
        edgar_coverage=coverage_of(results, floor=floor),
    )
    return payload, _admin_run_out(json.loads(json.dumps(payload)))  # the on-disk round trip


# --- the setting -----------------------------------------------------------------------------------------


def test_the_setting_defaults_to_the_operators_floor():
    assert Settings.model_fields["freeze_completed_floor"].default == 0.5
    assert DEFAULT_FREEZE_COMPLETED_FLOOR == 0.5
    # lenient on a typo: clamped, never a crashed cron; 0 disables the floor
    assert (clamp_floor(-1), clamp_floor(0.5), clamp_floor(7)) == (0.0, 0.5, 1.0)
    assert not EdgarCoverage(fresh=0, eligible=740, floor=0.0).starved


# --- the ratio: one company per CIK, fresh anywhere in the pass ----------------------------------------


def test_a_company_is_a_CIK_two_share_classes_or_two_segments_are_ONE_company():
    assert cik_key("1652044") == cik_key("0001652044") == cik_key(1652044) == "0001652044"
    assert cik_key(None) is None and cik_key("  ") is None
    # one issuer, two share classes (same CIK); one company placed in two segments (two rows); no CIK
    names = [_name("1652044", 1), _name("0001652044", 0), _name("320193", 0), _name("320193", 2)]
    names.append(_name(None, 0))  # no CIK -> no EDGAR leg ran -> not eligible
    eligible, fresh = company_sets(names)
    assert eligible == {"0001652044", "0000320193"}
    assert fresh == {"0001652044", "0000320193"}  # fresh if ANY of its rows completed a fetch


def test_the_MEASURED_overlap_shape_a_thesis_of_cache_hits_is_fresh_for_the_PASS():
    """2026-10-19 22:30 ET (the X5 counters + the basket's CIKs, read-only): a 14-company thesis run right after
    a 190-company one holding 13 of them made exactly ONE submissions fetch — its other 13 were cache hits on
    indexes the first thesis had pulled minutes earlier (the five-minute recurring TTL). Per thesis that reads
    1 of 14; the pass judges the union, where all 191 companies are fresh."""
    big = [_name(str(1000 + i), 1) for i in range(190)]
    small = [_name(str(1000 + i), 0) for i in range(13)] + [_name("9999", 1)]
    results = [_thesis(big, edgar_fetches_completed=190), _thesis(small, edgar_fetches_completed=1)]
    cov = coverage_of(results, floor=_FLOOR)
    assert (cov.fresh, cov.eligible, cov.starved) == (191, 191, False)
    assert _health(results) is None
    # the per-thesis diagnostic on the artifact reads fresh ANYWHERE in the pass, never 1 of 14
    payload, row = _round_trip(results)
    assert [(t["edgar_companies"], t["edgar_companies_fresh"]) for t in payload["theses"]] == [
        (190, 190),
        (14, 14),
    ]
    assert (payload["edgar_companies"], payload["edgar_companies_fresh"]) == (191, 191)
    assert payload["freeze_completed_floor"] == _FLOOR and row.healthy is True


def test_the_boundary_EXACTLY_at_the_floor_does_not_page_one_company_below_does():
    def results(fresh: int, eligible: int):
        names = [_name(str(1 + i), 1 if i < fresh else 0) for i in range(eligible)]
        return [_thesis(names, edgar_fetches_completed=fresh)]

    at = results(340, 680)
    assert coverage_of(at, floor=_FLOOR).ratio == 0.5
    assert _health(at) is None  # "fewer than 50%" — exactly 50% is not fewer
    below = results(339, 680)
    h = _health(below)
    assert h is not None and h.frozen is True
    assert h.frozen_line == (
        "FROZEN — the SEC feed looks starved: only 339 of 680 companies (49.9%) got fresh SEC data "
        "this pass — below the 50% floor"
    )


# --- real artifact shapes ----------------------------------------------------------------------------


def _replay(doc: dict, *, fresh_of) -> list[daily.ThesisRunResult]:
    """The artifact's roster as a floor-era pass would write it: per thesis, ``names_ingested`` rows, each a
    DISTINCT company (a synthetic CIK — the artifact holds no CIKs), the first ``fresh_of(thesis)`` of them with
    a completed fetch; the thesis's own completed count = its stored attempts (every attempt completed).
    """
    out, next_cik = [], 1
    for t in doc["theses"]:
        n, k = t["names_ingested"], fresh_of(t)
        names = [_name(str(next_cik + i), 1 if i < k else 0) for i in range(n)]
        next_cik += n
        r = _thesis(
            names,
            edgar_fetches=t["edgar_fetches"],
            edgar_fetches_completed=t["edgar_fetches"],
            recorded=t["recorded"],
            withheld_reason=t["withheld_reason"],
            error=t["error"],
        )
        r.thesis_id, r.name = uuid.UUID(t["id"]), t["name"]
        out.append(r)
    return out


def test_the_JULY_shape_1_6_percent_PAGES_where_both_earlier_rules_were_silent():
    """2026-07-24 22:29 ET: 4 EDGAR attempts across 255 basket names, nothing errored, every call unchanged.
    A BOUND, not a fit: each completed fetch serves at most one company, so even if all 4 attempts completed
    for 4 different companies the night had <= 4 fresh — and 4 of the largest thesis's 159 names alone is
    2.5%. The zero rule (4 completed) and the pre-O5 attempt rule both stay silent; the floor pages.
    """
    doc = _artifact("20260725T022958Z")
    results = _replay(doc, fresh_of=lambda t: min(t["edgar_fetches"], t["names_ingested"]))
    cov = coverage_of(results, floor=_FLOOR)
    assert (cov.fresh, cov.eligible) == (4, 255)

    assert daily.assess_health(results, asof=_ASOF, allow_live=True) is None  # O5's zero rule alone
    h = _health(results)
    assert h is not None and h.frozen is True and h.edgar_fetches_completed == 4
    assert h.frozen_line == (
        "FROZEN — the SEC feed looks starved: only 4 of 255 companies (1.6%) got fresh SEC data "
        "this pass — below the 50% floor"
    )
    assert h.label == f"cron 2026-10-21: {h.frozen_line}"  # the page: the floor's line, alone

    # the Admin history pages what the pass paged (the REAL writer -> disk -> the REAL reader)
    payload, row = _round_trip(results)
    assert row.healthy is False
    assert row.problems == [h.frozen_line]
    assert (payload["edgar_companies"], payload["edgar_companies_fresh"]) == (255, 4)


def test_the_LOWEST_healthy_night_since_G1_does_not_page():
    """2026-10-13 22:30 ET, the minimum of the 25 live passes since G1 (710 attempts / 740 names = 95.9%).
    A PROXY, labeled as one: each thesis's attempts stand in for its fresh companies (min(attempts, rows)) —
    the reading the "every pass since G1 >= 96%" figure was made in. 702 of 740 = 94.9%, far above 50%. The
    per-company reading MEASURED on the next healthy night's cache file times was 686 of 687 companies.
    """
    doc = _artifact("20261014T023001Z")
    results = _replay(doc, fresh_of=lambda t: min(t["edgar_fetches"], t["names_ingested"]))
    cov = coverage_of(results, floor=_FLOOR)
    assert (cov.fresh, cov.eligible, cov.starved) == (702, 740, False)
    h = _health(results, tape_stale_new=())
    assert h is None
    _, row = _round_trip(results)
    assert row.healthy is True and not any(p.startswith("FROZEN") for p in row.problems)


# --- catch-up · --no-live · the old-artifact contract ---------------------------------------------------


def test_a_CATCH_UP_is_judged_on_the_companies_of_the_theses_it_PROCESSED_only():
    skipped = [_skipped() for _ in range(9)]
    starved = _thesis(
        [_name(str(1 + i), 1 if i < 3 else 0) for i in range(40)], edgar_fetches_completed=3
    )
    cov = coverage_of(skipped + [starved], floor=_FLOOR)
    assert (cov.fresh, cov.eligible) == (3, 40)  # the 9 skipped theses add nothing to either side
    h = _health(skipped + [starved], catch_up=True)
    assert h is not None and h.frozen and h.freeze_theses == 1
    assert "only 3 of 40 companies (7.5%)" in h.label

    alive = _thesis([_name(str(1 + i), 1) for i in range(40)], edgar_fetches_completed=40)
    assert _health(skipped + [alive], catch_up=True) is None
    # a catch-up that skipped every thesis processed nothing: no coverage, no page
    assert coverage_of(skipped, floor=_FLOOR) is None
    assert _health(skipped, catch_up=True) is None
    # the artifact: null for a skipped thesis, the processed one's counts, through the Admin reader
    payload, row = _round_trip(skipped + [starved], catch_up=True)
    assert [t["edgar_companies"] for t in payload["theses"]] == [None] * 9 + [40]
    assert row.healthy is False and row.problems[0].startswith(
        "FROZEN — the SEC feed looks starved"
    )


def test_a_NO_LIVE_pass_is_never_frozen_by_the_floor():
    """A cache-only pass fetches nothing by design: 0 fresh of 50 is recorded as the fact it is, and the
    verdict is the benign no-live note, as before."""
    no_live = _thesis(
        [_name(str(1 + i), 0) for i in range(50)],
        edgar_fetches_completed=0,
        withheld_reason="no-live",
    )
    h = _health([no_live], allow_live=False)
    assert h is not None and h.frozen is False and h.withheld_no_live == 1
    payload, row = _round_trip([no_live], allow_live=False)
    assert payload["edgar_companies_fresh"] == 0
    assert not any(p.startswith("FROZEN") for p in row.problems)


@pytest.mark.parametrize(
    ("stem", "healthy"),
    [
        ("20260723T015952Z", False),  # 5 withheld (the empty User-Agent) — never FROZEN
        ("20260725T010517Z", False),  # 3 withheld — never FROZEN
        ("20260725T022958Z", True),  # the July 1.6% night: SILENT then, and its row stays green
        ("20261013T205743Z", True),  # the one stored catch-up
        (
            "20261014T023001Z",
            False,
        ),  # the lowest since G1: one benign newly-stale tape, never FROZEN
    ],
)
def test_a_PRE_FLOOR_artifact_keeps_the_verdict_it_was_paged_under(stem, healthy):
    """No coverage keys on the artifact -> the floor is never judged: the history does not recolor a night."""
    doc = _artifact(stem)
    assert "edgar_companies" not in doc
    row = _admin_run_out(doc)
    assert row.healthy is healthy
    assert not any(p.startswith("FROZEN") for p in row.problems)


def test_an_O5_artifact_without_coverage_keeps_the_ZERO_rule_it_was_paged_under():
    """Written between O5 and the floor: completed counts, no coverage. 4 completed of 255 names paged
    nothing then, so its row stays green; 0 completed paged FROZEN then, and still reads so."""
    doc = _artifact("20260725T022958Z")
    for t in doc["theses"]:
        t["edgar_fetches_completed"] = t["edgar_fetches"]
    doc["edgar_fetches_completed"] = 4
    assert _admin_run_out(doc).healthy is True
    for t in doc["theses"]:
        t["edgar_fetches_completed"] = 0
    assert _admin_run_out(doc).problems[0].startswith("FROZEN — 0 of 4 EDGAR fetches COMPLETED")


def test_the_history_re_judges_with_the_floor_THE_PASS_RECORDED_not_todays_setting():
    names = [_name(str(1 + i), 1 if i < 40 else 0) for i in range(100)]
    results = [_thesis(names, edgar_fetches_completed=40)]  # 40% fresh
    paged, row = _round_trip(results, floor=0.5)
    assert paged["freeze_completed_floor"] == 0.5 and row.healthy is False
    quiet, row = _round_trip(results, floor=0.3)
    assert quiet["freeze_completed_floor"] == 0.3 and row.healthy is True


# --- the REAL counters through the REAL ingest (DB-backed) ----------------------------------------------

_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)


def _subs(cik: int, accessions: tuple[str, ...]) -> dict:
    rows = [("4", a, f"xslF345X05/{a}.xml", "2026-05-01", "") for a in accessions]
    return {
        "cik": str(cik),
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "primaryDocument": [r[2] for r in rows],
                "filingDate": [r[3] for r in rows],
                "items": [r[4] for r in rows],
            }
        },
    }


def _sec_wire(monkeypatch) -> list[str]:
    """Stub ONLY the wire (``httpx.get``): a submissions index with one Form 4 per company, and that Form 4 as
    the requested company's own filing. Every request is recorded."""
    sent: list[str] = []

    def _get(url, **kw):
        sent.append(url)
        req = httpx.Request("GET", url)
        if "/submissions/CIK" in url:
            cik = int(url.rsplit("CIK", 1)[1].split(".")[0])
            return httpx.Response(200, text=json.dumps(_subs(cik, (f"ACC-{cik}",))), request=req)
        cik = int(url.rsplit("/", 3)[1])
        body = _XML.replace(
            "<issuerCik>0001234567</issuerCik>", f"<issuerCik>{cik:010d}</issuerCik>"
        )
        return httpx.Response(200, text=body, request=req)

    monkeypatch.setattr(httpx, "get", _get)
    return sent


class _Prices:
    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return [
            {"d": d, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0}
            for d in (date(2026, 6, 8), date(2026, 6, 9))
        ]


def _master(db, ticker: str, cik: str | None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    db.commit()
    return sid


def _db_thesis(db, members: list[tuple[str, uuid.UUID]]) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, f"t-{tid.hex[:6]}", "n"),
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


def _walk(db, tid, cache_dir, *, ttl: float) -> daily.ThesisRunResult:
    """One thesis exactly as ``run_daily`` walks it: a FRESH client per thesis, the REAL ingest, then the
    per-thesis facts ``run_daily`` records."""
    client = EdgarClient(
        cache_dir=cache_dir,
        allow_live=True,
        user_agent="test ua",
        max_per_sec=1000,
        cache_ttl_s=ttl,
    )
    res = daily.ThesisRunResult(thesis_id=tid, name="t")
    res.ingested = IT.ingest_thesis(db, tid, allow_live=True, edgar_client=client)
    res.edgar_fetches = client.live_fetches
    res.edgar_fetches_completed = client.live_completed
    res.edgar_ciks, res.edgar_ciks_fresh = company_sets(res.ingested)
    return res


def test_REAL_counters_attribute_completions_per_company_and_the_pass_unions_them(
    db, tmp_path, monkeypatch
):
    """Two theses back to back on the recurring dial, sharing one company: the first fetches it, the second
    reads the five-minute-fresh index (a cache hit — 0 completed for that row) and is still FRESH for the
    pass. A member with no CIK makes no SEC request and is out of the denominator."""
    sent = _sec_wire(monkeypatch)
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _Prices())
    shared = _master(db, "SHRD", "0000000101")
    only_a = _master(db, "ONLA", "0000000102")
    no_cik = _master(db, "NOCK", None)
    only_b = _master(db, "ONLB", "0000000103")
    a = _db_thesis(db, [("SHRD", shared), ("ONLA", only_a), ("NOCK", no_cik)])
    b = _db_thesis(db, [("SHRD", shared), ("ONLB", only_b)])

    ra = _walk(db, a, tmp_path, ttl=RECURRING_CACHE_TTL_S)
    rb = _walk(db, b, tmp_path, ttl=RECURRING_CACHE_TTL_S)

    by_ticker = {n.ticker: n for n in ra.ingested + rb.ingested[1:]}
    assert all(n.error is None for n in ra.ingested + rb.ingested)
    # each CIK'd name: its submissions index + its one Form 4 completed while ITS legs ran
    assert (by_ticker["SHRD"].edgar_completed, by_ticker["ONLA"].edgar_completed) == (2, 2)
    assert (by_ticker["NOCK"].edgar_cik, by_ticker["NOCK"].edgar_completed) == (None, 0)
    assert rb.ingested[0].edgar_cik == "0000000101" and rb.ingested[0].edgar_completed == 0
    assert ra.edgar_fetches_completed == sum(n.edgar_completed for n in ra.ingested) == 4
    assert rb.edgar_fetches_completed == 2  # only ONLB went out
    assert len(sent) == 6  # 3 companies x (index + Form 4); the shared one only once

    assert rb.edgar_ciks_fresh == {"0000000103"}  # per thesis: 1 of 2
    cov = coverage_of([ra, rb], floor=_FLOOR)
    assert (cov.fresh, cov.eligible, cov.starved) == (3, 3, False)  # per pass: all three fresh
    assert _health([ra, rb]) is None


def test_REAL_counters_the_JULY_mechanism_a_long_TTL_client_over_a_warm_cache_PAGES(
    db, tmp_path, monkeypatch
):
    """The pre-G1 shape, reproduced: a pass on the 12h INTERACTIVE dial reads indexes another pass warmed
    minutes ago — nothing errors, and the only request that leaves the box is for the one company nobody had
    fetched. 1 completed fetch: the zero rule is silent; 1 fresh company of 3 pages the floor."""
    _sec_wire(monkeypatch)
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _Prices())
    x, y, z = (_master(db, t, c) for t, c in (("XX", "201"), ("YY", "202"), ("ZZ", "203")))
    warm = _db_thesis(db, [("XX", x), ("YY", y)])
    _walk(db, warm, tmp_path, ttl=RECURRING_CACHE_TTL_S)  # a daytime pass warms XX + YY

    night = _db_thesis(db, [("XX", x), ("YY", y), ("ZZ", z)])
    r = _walk(db, night, tmp_path, ttl=12 * 3600)
    assert all(n.error is None for n in r.ingested)
    assert (
        r.edgar_fetches_completed == 2
    )  # ZZ's index + its Form 4; XX/YY served off the warm cache
    cov = coverage_of([r], floor=_FLOOR)
    assert (cov.fresh, cov.eligible, cov.starved) == (1, 3, True)
    assert daily.assess_health([r], asof=_ASOF, allow_live=True) is None  # the zero rule alone
    h = _health([r])
    assert h is not None and h.frozen is True
    assert "only 1 of 3 companies (33.3%) got fresh SEC data this pass" in h.label
