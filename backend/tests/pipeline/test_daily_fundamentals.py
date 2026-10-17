"""Q17 ZERO-LAG — the filing-gated fundamentals refresh runs PER THESIS inside ``run_daily``, after that
thesis's ``ingest_thesis`` re-pulled its members' submissions indexes and before its call is assembled.

DB-backed (``db``). The network is a stub EDGAR client (``daily.EdgarClient`` swapped): every fetch is
recorded, so each test COUNTS the requests, and the documents are the real EDGAR shapes (the
``filings.recent`` arrays, a companyfacts revenue concept). The price leg is a stub source (no network).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.client import EdgarClient
from ingest.edgar.companyfacts_gate import companyfacts_due
from pipeline import daily
from pipeline import ingest_thesis as IT

_ET = ZoneInfo("America/New_York")
_CIK = "0001234567"
_CF_KEY = f"companyfacts/CIK{_CIK}.json"
_SUBS_KEY = f"submissions/CIK{_CIK}.json"


class _NoBars:
    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return []


def _security(db, ticker: str, cik: str = _CIK) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01", "equity"),
        )
    db.commit()
    return sid


def _thesis(db, name: str, members) -> uuid.UUID:
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


def _subs(*rows: tuple[str, str, datetime]) -> dict:
    """A real-shape submissions document: ``(form, accession, accepted)`` rows in the ``recent`` arrays."""
    return {
        "cik": _CIK.lstrip("0"),
        "name": "DEVCO INC",
        "tickers": ["DEVCO"],
        "filings": {
            "recent": {
                "accessionNumber": [r[1] for r in rows],
                "filingDate": [r[2].astimezone(_ET).date().isoformat() for r in rows],
                "acceptanceDateTime": [
                    r[2].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") for r in rows
                ],
                "form": [r[0] for r in rows],
                "primaryDocument": ["doc.htm" for _ in rows],
                "items": ["" for _ in rows],
            }
        },
    }


def _revenue(*quarters: tuple[str, str, float, date, str]) -> dict:
    """A real-shape companyfacts document with ``(start, end, value, filed, accession)`` revenue rows."""
    return {
        "cik": int(_CIK),
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"start": s, "end": e, "val": v, "filed": f.isoformat(), "accn": a}
                            for s, e, v, f, a in quarters
                        ]
                    }
                }
            }
        },
    }


def _cache(cache: Path, key: str, doc: dict, *, written: datetime) -> None:
    p = cache / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")
    os.utime(p, (written.timestamp(),) * 2)


def _live_edgar(monkeypatch, cache: Path, docs: dict[str, dict]) -> list[str]:
    """Swap the per-thesis client the cron builds for one over ``cache`` whose network is ``docs`` (URL
    fragment -> document; anything else answers the real 404 shape). Returns the fetched-URL log."""
    fetched: list[str] = []

    class _Stub(EdgarClient):
        def __init__(self, **kw):
            kw.update(cache_dir=cache, user_agent="test ua")
            super().__init__(**kw)

        def _fetch(self, url: str) -> str:
            fetched.append(url)
            for frag, doc in docs.items():
                if frag in url:
                    return json.dumps(doc)
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                "404 Not Found", request=req, response=httpx.Response(404, request=req)
            )

    monkeypatch.setattr(daily, "EdgarClient", _Stub)
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _NoBars())
    return fetched


def _revenue_visible(conn, security_id, asof: date) -> list[date]:
    """The revenue quarters an as-of read of ``asof`` at transaction time NOW can see — the call's inputs."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT period_end FROM fact_fundamentals WHERE tenant_id = %s AND security_id = %s "
            "AND metric_key = 'revenue' AND valid_from <= %s AND recorded_at <= now() ORDER BY 1",
            (DEFAULT_TENANT_ID, security_id, asof),
        )
        return [r["period_end"] for r in cur.fetchall()]


def test_a_quarter_filed_TODAY_reaches_TONIGHTS_call(db, tmp_path, monkeypatch):
    """THE ZERO-LAG GUARANTEE. Last night's pass left a companyfacts holding Q1 and a submissions index
    from before today's 10-Q. Tonight the thesis's ingest re-pulls the index (it now lists the 10-Q
    accepted two hours ago), the gate reads THAT index, re-pulls companyfacts, and Q2 is stored — and it is
    visible to the as-of read at the moment this thesis's call is assembled. Counted: one index pull and
    one companyfacts pull."""
    now = datetime.now(timezone.utc)
    accepted = now - timedelta(hours=2)
    filed = accepted.astimezone(_ET).date()
    last_night = now - timedelta(hours=24)
    q1 = ("2026-01-01", "2026-03-31", 100.0, date(2026, 5, 1), "0001234567-26-000001")
    q2 = ("2026-04-01", "2026-06-30", 130.0, filed, "0001234567-26-000002")
    old_subs = _subs(("10-Q", q1[4], datetime(2026, 5, 1, 16, 30, tzinfo=_ET)))
    new_subs = _subs(
        ("10-Q", q2[4], accepted), ("10-Q", q1[4], datetime(2026, 5, 1, 16, 30, tzinfo=_ET))
    )
    _cache(tmp_path, _SUBS_KEY, old_subs, written=last_night)
    _cache(tmp_path, _CF_KEY, _revenue(q1), written=last_night)
    # what makes the pre-loop leg one pass late: last night's index says nothing is due
    assert not companyfacts_due(
        fetched_at=last_night.timestamp(), now=now.timestamp(), submissions=old_subs
    ).due

    fetched = _live_edgar(
        monkeypatch,
        tmp_path,
        {"/submissions/": new_subs, "/companyfacts/": _revenue(q1, q2)},
    )
    sid = _security(db, "DEVCO")
    tid = _thesis(db, "Zero lag", [("DEVCO", sid)])
    seen_at_call: list[list[date]] = []
    real_call = daily.call_for_thesis

    def _spy(conn, thesis_id, asof, **kw):
        seen_at_call.append(_revenue_visible(conn, sid, asof))
        return real_call(conn, thesis_id, asof, **kw)

    monkeypatch.setattr(daily, "call_for_thesis", _spy)

    (res,) = [r for r in daily.run_daily(db, asof=filed, allow_live=True) if r.thesis_id == tid]

    assert seen_at_call == [
        [date(2026, 3, 31), date(2026, 6, 30)]
    ]  # Q2 is IN tonight's call inputs
    assert [u for u in fetched if "/companyfacts/" in u] == [
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_CIK}.json"
    ]
    assert len([u for u in fetched if "/submissions/" in u]) == 1
    assert res.error is None and res.fundamentals_error is None
    assert [(f.ticker, f.appended) for f in res.fundamentals] == [("DEVCO", 2)]


def test_a_company_held_by_TWO_theses_is_pulled_ONCE_per_pass(db, tmp_path, monkeypatch):
    """The 24 h grace would make a name filed today due again for the second thesis; the pass memory
    keeps it to one companyfacts request, and the second thesis does not repeat the name."""
    now = datetime.now(timezone.utc)
    accepted = now - timedelta(hours=2)
    filed = accepted.astimezone(_ET).date()
    q = ("2026-04-01", "2026-06-30", 130.0, filed, "0001234567-26-000002")
    _cache(tmp_path, _CF_KEY, _revenue(), written=now - timedelta(days=2))
    fetched = _live_edgar(
        monkeypatch,
        tmp_path,
        {"/submissions/": _subs(("10-Q", q[4], accepted)), "/companyfacts/": _revenue(q)},
    )
    sid = _security(db, "DEVCO")
    _thesis(db, "A", [("DEVCO", sid)])
    _thesis(db, "B", [("DEVCO", sid)])

    results = daily.run_daily(db, asof=filed, allow_live=True)

    assert len([u for u in fetched if "/companyfacts/" in u]) == 1
    assert sorted(len(r.fundamentals) for r in results if r.name in ("A", "B")) == [0, 1]


def test_two_share_classes_of_ONE_company_cost_ONE_pull_and_both_store(db, tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    accepted = now - timedelta(hours=2)
    filed = accepted.astimezone(_ET).date()
    q = ("2026-04-01", "2026-06-30", 130.0, filed, "0001234567-26-000002")
    fetched = _live_edgar(
        monkeypatch,
        tmp_path,
        {"/submissions/": _subs(("10-Q", q[4], accepted)), "/companyfacts/": _revenue(q)},
    )
    a, b = _security(db, "DEVA"), _security(db, "DEVB")
    tid = _thesis(db, "Classes", [("DEVA", a), ("DEVB", b)])

    (res,) = [r for r in daily.run_daily(db, asof=filed, allow_live=True) if r.thesis_id == tid]

    assert (
        len([u for u in fetched if "/companyfacts/" in u]) == 1
    )  # the 2nd class read tonight's copy
    assert _revenue_visible(db, a, filed) == _revenue_visible(db, b, filed) == [date(2026, 6, 30)]
    assert sorted(f.appended for f in res.fundamentals) == [1, 1]


def test_a_fundamentals_fault_NEVER_costs_the_night_its_record(db, monkeypatch):
    """The savepoint trap, avoided: the step poisons the connection's transaction (a failing statement)
    and raises. It is rolled back where it runs, lands on ``fundamentals_error`` (never ``error``), and the
    thesis's call-of-record still records."""
    sid = _security(db, "DEVCO")
    tid = _thesis(db, "Fault", [("DEVCO", sid)])
    monkeypatch.setattr(
        daily,
        "ingest_thesis",
        lambda *a, **k: [
            IT.NameResult(ticker="DEVCO", security_id=sid, form4_appended=0, price_bars_appended=0)
        ],
    )

    def _poison(conn, secs, **kw):
        conn.execute("SELECT 1 / 0")  # aborts the transaction, then propagates

    monkeypatch.setattr("pipeline.ingest_fundamentals.ingest_fundamentals_for_securities", _poison)

    (res,) = [
        r
        for r in daily.run_daily(db, asof=date(2026, 6, 10), allow_live=True)
        if r.thesis_id == tid
    ]

    assert res.error is None and res.recorded is True
    assert res.fundamentals_error and "division by zero" in res.fundamentals_error


def test_a_NO_LIVE_pass_never_refreshes_fundamentals(db, monkeypatch):
    sid = _security(db, "DEVCO")
    _thesis(db, "NoLive", [("DEVCO", sid)])
    monkeypatch.setattr(
        daily,
        "ingest_thesis",
        lambda *a, **k: [
            IT.NameResult(ticker="DEVCO", security_id=sid, form4_appended=0, price_bars_appended=0)
        ],
    )
    called: list = []
    monkeypatch.setattr(
        "pipeline.ingest_fundamentals.ingest_fundamentals_for_securities",
        lambda *a, **k: called.append(1) or [],
    )
    daily.run_daily(db, asof=date(2026, 6, 10), allow_live=False)
    assert called == []


def test_the_step_rides_the_per_thesis_RECURRING_client(db, monkeypatch):
    """No flag threads the gate: the step is handed the same per-thesis client the ingest used — built with
    the recurring dial, on which companyfacts is filing-gated (``EdgarClient.recurring``)."""
    sid = _security(db, "DEVCO")
    _thesis(db, "Client", [("DEVCO", sid)])
    seen: list = []
    monkeypatch.setattr(
        daily,
        "ingest_thesis",
        lambda *a, **k: (
            seen.append(("ingest", k["edgar_client"]))
            or [
                IT.NameResult(
                    ticker="DEVCO", security_id=sid, form4_appended=0, price_bars_appended=0
                )
            ]
        ),
    )
    monkeypatch.setattr(
        "pipeline.ingest_fundamentals.ingest_fundamentals_for_securities",
        lambda conn, secs, **k: seen.append(("fund", k["client"])) or [],
    )
    daily.run_daily(db, asof=date(2026, 6, 10), allow_live=True)
    (ingest_client,) = [c for tag, c in seen if tag == "ingest"]
    (fund_client,) = [c for tag, c in seen if tag == "fund"]
    assert fund_client is ingest_client and fund_client.recurring is True


def test_the_pass_prints_ONE_fundamentals_summary(capsys):
    """X8 at the pass: known 404s fold into the summary as a count; a FRESH 404 and every other error stay
    loud; a thesis whose whole step failed gets its own WARNING line."""
    from pipeline.ingest_fundamentals import NameResult as FundResult

    sid = uuid.uuid4()
    results = [
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="T1",
            fundamentals=(
                FundResult("AAA", sid, appended=2),
                FundResult(
                    "NOCF",
                    sid,
                    error="companyfacts/CIK1.json: (HTTP 404, cached)",
                    known_absent=True,
                ),
            ),
        ),
        daily.ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="T2",
            fundamentals=(FundResult("BAD", sid, error="Client error '404 Not Found' for url x"),),
            fundamentals_error=None,
        ),
        daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T3", fundamentals_error="boom"),
    ]
    daily._print_fundamentals(results)
    out = capsys.readouterr().out
    assert "fundamentals refresh: +2 quarters, 3 names, 1 known without companyfacts" in out
    assert "fundamentals refresh ERROR: Client error '404 Not Found'" in out
    assert "ERROR: companyfacts/CIK1.json" not in out
    assert "WARNING: T3: fundamentals refresh failed: boom" in out
