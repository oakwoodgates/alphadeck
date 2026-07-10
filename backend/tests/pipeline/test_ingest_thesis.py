"""M2a — per-thesis back-half ingest. Offline: the network legs are faked (EdgarClient + fetch_submissions
+ fetch_eod monkeypatched), the DB is real (the `db` fixture, against alphadeck_test). The headline is the
idempotency gate: a re-run appends ZERO rows — asserted by COUNTING the tables, because the store dedups on
read, so a duplicate append would hide behind correct reads while the table silently grows."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest import CacheMiss
from pipeline import ingest_thesis as IT
from pipeline.provision_tenant import provision_tenant

_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)
_F4_PER_ACCESSION = 2  # form4_sample.xml has two dated non-derivative txns

_ALT_TENANT = uuid.UUID("00000000-0000-0000-0000-0000000002a0")


# --- fakes for the two network legs -----------------------------------------------------------------


class _FakeClient:
    """Stands in for EdgarClient: get_text always returns the sample Form 4 XML (no network)."""

    def __init__(self, **kwargs):
        pass

    def get_text(self, url: str, cache_key: str) -> str:
        return _XML


class _MixedClient(_FakeClient):
    """A per-filing-failure EdgarClient: accession ACC-BAD returns an unparseable doc (the pre-XML
    SGML/text era), ACC-404 raises the 404 an ancient document URL produces, anything else is the
    good sample XML."""

    def get_text(self, url: str, cache_key: str) -> str:
        if "ACC-BAD" in cache_key:
            return "-----BEGIN PRIVACY-ENHANCED MESSAGE-----\n<SEC-DOCUMENT>not xml"
        if "ACC-404" in cache_key:
            import httpx

            req = httpx.Request("GET", url)
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("404 Not Found", request=req, response=resp)
        return _XML


def _subs(accessions):
    """A submissions JSON exposing one Form 4 per accession (parallel `recent` arrays)."""
    accns = list(accessions)
    return {
        "filings": {
            "recent": {
                "form": ["4"] * len(accns),
                "accessionNumber": accns,
                "primaryDocument": [f"xslF345X05/{a}.xml" for a in accns],
                "filingDate": ["2026-05-01"] * len(accns),
            }
        }
    }


def _bars(dates):
    return [
        {"d": d, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0} for d in dates
    ]


class _FakePriceSource:
    """A PriceSource stub returning controlled bars (or delegating to a fn that may raise, for the
    one-bad-name test). Injected through the YahooPriceSource seam."""

    def __init__(self, fn):
        self._fn = fn

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return self._fn(ticker)


def _patch(monkeypatch, *, accessions=("ACC-1",), bar_dates=(date(2026, 6, 15),), eod_fn=None):
    monkeypatch.setattr(IT, "EdgarClient", _FakeClient)
    monkeypatch.setattr(IT, "fetch_submissions", lambda client, cik: _subs(accessions))
    fn = eod_fn or (lambda ticker: _bars(bar_dates))
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _FakePriceSource(fn))


# --- DB setup helpers -------------------------------------------------------------------------------


def _add_master(db, *, ticker, cik, tenant=DEFAULT_TENANT_ID) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, tenant, ticker, cik, "2026-01-01"),
        )
    db.commit()
    return sid


def _make_thesis(db, members, *, tenant=DEFAULT_TENANT_ID) -> uuid.UUID:
    """members: list of (ticker, security_id|None) in basket order."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, tenant, "Test thesis", "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, archetype, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), tenant, tid, i, ticker, "—", "high_beta", sid),
            )
    db.commit()
    return tid


def _counts(db, *, tenant=DEFAULT_TENANT_ID) -> tuple[int, int]:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) n FROM fact_insider_txn WHERE tenant_id = %s", (tenant,))
        ins = cur.fetchone()["n"]
        cur.execute("SELECT count(*) n FROM fact_price_eod WHERE tenant_id = %s", (tenant,))
        px = cur.fetchone()["n"]
    return ins, px


# --- tests ------------------------------------------------------------------------------------------


def test_ingest_lands_insider_and_price_facts(db, security_id, monkeypatch):
    _patch(monkeypatch, accessions=("ACC-1",), bar_dates=(date(2026, 6, 15), date(2026, 6, 16)))
    tid = _make_thesis(db, [("DEVCO", security_id)])

    results = IT.ingest_thesis(db, tid, allow_live=False)

    assert len(results) == 1 and results[0].error is None
    assert results[0].form4_appended == _F4_PER_ACCESSION
    assert results[0].price_bars_appended == 2
    assert _counts(db) == (2, 2)


def test_rerun_appends_zero_rows_count_the_table(db, security_id, monkeypatch):
    """THE idempotency gate: re-running on unchanged data appends NOTHING — verified by counting the
    tables, not by reading (the read dedups, so a duplicate append would hide behind a correct read).
    """
    _patch(monkeypatch, accessions=("ACC-1",), bar_dates=(date(2026, 6, 15), date(2026, 6, 16)))
    tid = _make_thesis(db, [("DEVCO", security_id)])

    IT.ingest_thesis(db, tid, allow_live=False)
    before = _counts(db)
    results = IT.ingest_thesis(db, tid, allow_live=False)  # identical second run
    after = _counts(db)

    assert after == before  # the TABLE did not grow
    assert all(r.form4_appended == 0 and r.price_bars_appended == 0 for r in results)


def test_incremental_appends_only_what_is_new(db, security_id, monkeypatch):
    _patch(monkeypatch, accessions=("ACC-1",), bar_dates=(date(2026, 6, 15),))
    tid = _make_thesis(db, [("DEVCO", security_id)])
    IT.ingest_thesis(db, tid, allow_live=False)
    base = _counts(db)

    # second run: a NEW filing + a NEW (later) bar, with the old ones re-served
    _patch(
        monkeypatch, accessions=("ACC-1", "ACC-2"), bar_dates=(date(2026, 6, 15), date(2026, 6, 16))
    )
    r = IT.ingest_thesis(db, tid, allow_live=False)[0]

    assert r.form4_appended == _F4_PER_ACCESSION  # only ACC-2's txns
    assert r.price_bars_appended == 1  # only the 2026-06-16 bar (d > latest)
    assert _counts(db) == (base[0] + _F4_PER_ACCESSION, base[1] + 1)


def test_unresolved_member_is_skipped(db, monkeypatch):
    _patch(monkeypatch)
    tid = _make_thesis(db, [("NOPE", None)])  # no security_id → nothing to ingest against

    results = IT.ingest_thesis(db, tid, allow_live=False)

    assert results == []  # skipped, no error
    assert _counts(db) == (0, 0)


def test_one_bad_name_does_not_abort_and_is_reported(db, security_id, monkeypatch):
    """Fail-visible + per-LEG isolation: a name whose price leg raises is captured and skipped; the other
    name still ingests AND the bad name's OWN form4 leg (which succeeded) is committed."""
    bad = _add_master(db, ticker="BADCO", cik="0007654321")

    def eod(ticker, allow_live=False):
        if ticker == "BADCO":
            raise RuntimeError("yahoo 500")
        return _bars([date(2026, 6, 15)])

    _patch(monkeypatch, accessions=("ACC-1",), eod_fn=eod)
    tid = _make_thesis(db, [("DEVCO", security_id), ("BADCO", bad)])

    by = {r.ticker: r for r in IT.ingest_thesis(db, tid, allow_live=False)}

    assert by["DEVCO"].error is None and by["DEVCO"].price_bars_appended == 1
    assert by["BADCO"].error and "yahoo 500" in by["BADCO"].error
    assert by["BADCO"].price_bars_appended == 0  # the failed leg
    assert by["BADCO"].form4_appended == _F4_PER_ACCESSION  # its form4 leg still committed
    # 2 names × 2 form4 txns committed; only DEVCO's 1 price bar
    assert _counts(db) == (2 * _F4_PER_ACCESSION, 1)


def test_no_lookahead_recorded_at_is_now(db, security_id, monkeypatch):
    """Facts are ingested with recorded_at=now (never backdated), so an as-of read pinned at an earlier
    transaction time cannot see them — the replay/no-lookahead guarantee."""
    _patch(monkeypatch, accessions=("ACC-1",), bar_dates=(date(2026, 6, 15), date(2026, 6, 16)))
    tid = _make_thesis(db, [("DEVCO", security_id)])
    IT.ingest_thesis(db, tid, allow_live=False)

    asof = date(2026, 12, 31)  # event-time covers the bars
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    future = datetime(2100, 1, 1, tzinfo=timezone.utc)

    invisible = as_of(
        db,
        "fact_price_eod",
        security_id=security_id,
        asof=asof,
        known_at=past,
        tenant_id=DEFAULT_TENANT_ID,
    )
    visible = as_of(
        db,
        "fact_price_eod",
        security_id=security_id,
        asof=asof,
        known_at=future,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert invisible == []  # ingested "now" → not knowable at a past transaction time
    assert len(visible) == 2


def test_ingest_writes_under_the_thesis_tenant(db, monkeypatch):
    """Tenant isolation (#4): facts land under the THESIS's tenant, never the demo default."""
    provision_tenant(db, "m2a-ingest", tenant_id=_ALT_TENANT)
    db.commit()
    sid = _add_master(db, ticker="ALTCO", cik="0009999999", tenant=_ALT_TENANT)
    _patch(monkeypatch, accessions=("ACC-1",), bar_dates=(date(2026, 6, 15),))
    tid = _make_thesis(db, [("ALTCO", sid)], tenant=_ALT_TENANT)

    r = IT.ingest_thesis(db, tid, allow_live=False)[0]

    assert r.error is None and r.form4_appended == _F4_PER_ACCESSION and r.price_bars_appended == 1
    assert _counts(db, tenant=_ALT_TENANT) == (_F4_PER_ACCESSION, 1)  # under the alt tenant
    assert _counts(db, tenant=DEFAULT_TENANT_ID) == (0, 0)  # nothing leaked to the default


def test_unparseable_filing_is_skipped_not_leg_fatal(db, security_id, monkeypatch, capsys):
    """Per-filing tolerance: an unparseable filing (the pre-2004 SGML/text era) is skipped-and-counted
    with a visible warning; the walk CONTINUES and the good filing still lands — one ancient filing
    never blanks the name's whole insider history (the NVEC/INTT live failure)."""
    _patch(monkeypatch, accessions=("ACC-BAD", "ACC-1"), bar_dates=(date(2026, 6, 15),))
    monkeypatch.setattr(IT, "EdgarClient", _MixedClient)
    tid = _make_thesis(db, [("DEVCO", security_id)])

    r = IT.ingest_thesis(db, tid, allow_live=False)[0]

    assert r.error is None  # the leg did NOT abort
    assert r.form4_appended == _F4_PER_ACCESSION  # ACC-1, walked AFTER the bad filing, still landed
    assert r.form4_skipped == 1
    assert _counts(db) == (_F4_PER_ACCESSION, 1)
    out = capsys.readouterr().out
    assert "ACC-BAD" in out and "skipped" in out  # the per-filing warning is visible


def test_unfetchable_filing_is_skipped_not_leg_fatal(db, security_id, monkeypatch):
    """The fetch side of the same tolerance: a document URL that 404s (the ASYS/CVV year-2000 URL
    shape) is skipped-and-counted; the rest of the walk still ingests."""
    _patch(monkeypatch, accessions=("ACC-404", "ACC-1"), bar_dates=(date(2026, 6, 15),))
    monkeypatch.setattr(IT, "EdgarClient", _MixedClient)
    tid = _make_thesis(db, [("DEVCO", security_id)])

    r = IT.ingest_thesis(db, tid, allow_live=False)[0]

    assert r.error is None
    assert r.form4_appended == _F4_PER_ACCESSION
    assert r.form4_skipped == 1
    assert _counts(db) == (_F4_PER_ACCESSION, 1)


def test_skip_tolerance_keeps_rerun_at_zero_rows_count_the_table(db, security_id, monkeypatch):
    """COUNT-the-table idempotency holds WITH a skip in the walk: the re-run re-attempts the bad
    filing (never stored → never in ``existing_accessions``), re-skips it, and appends ZERO rows —
    the skip stays visible on every run instead of being silently marked done."""
    _patch(monkeypatch, accessions=("ACC-BAD", "ACC-1"), bar_dates=(date(2026, 6, 15),))
    monkeypatch.setattr(IT, "EdgarClient", _MixedClient)
    tid = _make_thesis(db, [("DEVCO", security_id)])

    IT.ingest_thesis(db, tid, allow_live=False)
    before = _counts(db)
    r = IT.ingest_thesis(db, tid, allow_live=False)[0]  # identical second run

    assert _counts(db) == before  # the TABLE did not grow
    assert r.form4_appended == 0 and r.price_bars_appended == 0
    assert r.form4_skipped == 1  # still counted, still visible


def test_cache_miss_still_aborts_the_form4_leg(db, security_id, monkeypatch):
    """The tolerance is per-FILING, not per-anything: a ``CacheMiss`` (live pulls disabled, cold
    cache) is an environment condition, not one filing's fault — it still fails the leg visibly
    instead of dissolving into a skip count. Per-LEG isolation still commits the price leg."""

    class _ColdClient(_FakeClient):
        def get_text(self, url: str, cache_key: str) -> str:
            raise CacheMiss(f"{cache_key} not cached (live pulls disabled)")

    _patch(monkeypatch, accessions=("ACC-1",), bar_dates=(date(2026, 6, 15),))
    monkeypatch.setattr(IT, "EdgarClient", _ColdClient)
    tid = _make_thesis(db, [("DEVCO", security_id)])

    r = IT.ingest_thesis(db, tid, allow_live=False)[0]

    assert r.error and "form4" in r.error and "not cached" in r.error
    assert r.form4_appended == 0 and r.form4_skipped == 0
    assert r.price_bars_appended == 1  # the price leg still committed
    assert _counts(db) == (0, 1)
