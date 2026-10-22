"""X1 x #432 — the FETCH-AHEAD honors the nightly's onboarding treatment (``pipeline/fetch_ahead.py``).

The fetch-ahead pre-fetches each upcoming company's submissions index, every Form 4 / 4/A document not yet
stored, and its price. For a COLD name "not yet stored" is its whole history — so without X1's rule it would pull
hundreds of documents inside the nightly and defeat the staged cold-ingest. These tests prove, with only the HTTP
layer stubbed (the REAL ``EdgarClient``, the REAL legs, the REAL W1 lock) and every request COUNTED at that layer:

- a WINDOWED name (still onboarding) is fetched exactly as its legs read it: its submissions index, its price
  and ONLY the Form 4 documents filed inside its window — never an older one, never a 4/A (not even one filed
  inside the window: a 4/A waits for the full load);
- a DEFERRED name (past the pass's onboarding cap) makes ZERO vendor requests — no SEC, no price;
- and #432's contract still holds WITH the onboarding treatment: the concurrent walk makes the same requests and
  writes the same rows as the serial walk (``form4_leg_filings`` is the one rule both sides use).
"""

from __future__ import annotations

import collections
import json
import re
import threading
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.onboarding import OnboardingWindow
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from pipeline import ingest_thesis as IT
from tests.pipeline.test_fetch_ahead import (
    _changed,
    _NoBudget,
    _reset,
    _snapshot,
    _status,
    _subs_doc,
)

_SAMPLE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml"
).read_text(encoding="utf-8")
_SINCE = date(2026, 9, 1)  # the windowed name's window: filings filed on/after this date

_CIK = {"COLD": "0009100001", "DEFR": "0009100002", "WARM": "0009100003"}


def _acc(cik: str, i: int) -> str:
    return f"{cik}-26-{i:06d}"  # filing year 26: the sample's 2026 transactions are plausible


def _rows(cik: str) -> dict:
    """Per company: 3 Form 4s filed INSIDE the window, 5 filed long before it, 2 Form 4/As filed inside it."""
    inside = [("4", _acc(cik, i), f"x/{_acc(cik, i)}.xml", "2026-09-15", "") for i in range(1, 4)]
    before = [("4", _acc(cik, i), f"x/{_acc(cik, i)}.xml", "2025-03-03", "") for i in range(4, 9)]
    amended = [
        ("4/A", _acc(cik, i), f"x/{_acc(cik, i)}.xml", "2026-09-20", "") for i in range(9, 11)
    ]
    return _subs_doc(
        amended + inside + before + [("8-K", _acc(cik, 99), "ek.htm", "2026-09-02", "")]
    )


class _Sec:
    """The SEC's HTTP layer (``EdgarClient._fetch``): every URL recorded (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.urls: list[str] = []
        self.threads: dict[str, str] = {}  # url -> the name of the thread that requested it
        self._subs = {int(c): _rows(c) for c in _CIK.values()}

    def __call__(self, url: str) -> str:
        with self._lock:
            self.urls.append(url)
            self.threads[url] = threading.current_thread().name
        m = re.search(r"/submissions/CIK(\d{10})\.json$", url)
        if m:
            return json.dumps(self._subs[int(m.group(1))])
        m = re.search(r"/data/(\d+)/[0-9]+/([^/]+)\.xml$", url)
        if not m:
            raise _status(404, url)
        cik = int(m.group(1))
        return _SAMPLE.replace(
            "<issuerCik>0001234567</issuerCik>", f"<issuerCik>{cik:010d}</issuerCik>"
        )

    def for_cik(self, cik: str) -> list[str]:
        """The requests about one company: its submissions index and its filing documents."""
        n = str(int(cik))
        return [u for u in self.urls if f"CIK{cik}.json" in u or f"/data/{n}/" in u]


class _Prices:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[str] = []

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        with self._lock:
            self.calls.append(ticker)
        return [
            {"d": d, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0}
            for d in (date(2026, 9, 29), date(2026, 9, 30))
        ]


@pytest.fixture
def concurrency(monkeypatch):
    def set_(n: int) -> None:
        monkeypatch.setenv("ALPHADECK_INGEST_FETCH_CONCURRENCY", str(n))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


def _basket(db) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    sids: dict[str, uuid.UUID] = {}
    tid = uuid.uuid4()
    with db.cursor() as cur:
        for t, cik in _CIK.items():
            sids[t] = uuid.uuid4()
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (sids[t], DEFAULT_TENANT_ID, t, cik, f"{t} (fixture)", "2020-01-01"),
            )
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "Onboarding x fetch-ahead", "n"),
        )
        for i, t in enumerate(("DEFR", "COLD", "WARM")):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, "
                "security_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, t, "—", sids[t]),
            )
    db.commit()
    return tid, sids


def _walk(db, tid, sids, cache_dir):
    sec, prices = _Sec(), _Prices()
    client = EdgarClient(
        cache_dir=cache_dir,
        allow_live=True,
        user_agent="fixture ua",
        cache_ttl_s=RECURRING_CACHE_TTL_S,
        budget=_NoBudget(),
    )
    client._fetch = sec  # type: ignore[method-assign]  # ONLY the HTTP layer is stubbed
    onboarding = {
        sids["COLD"]: OnboardingWindow("queued", _SINCE),
        sids["DEFR"]: OnboardingWindow("queued", _SINCE, defer=True),
    }
    results = IT.ingest_thesis(
        db,
        tid,
        allow_live=True,
        force_refresh=True,
        edgar_client=client,
        price_source=prices,
        onboarding=onboarding,
    )
    return results, sec, prices


def _doc_accessions(urls: list[str]) -> set[str]:
    return {m.group(1) for u in urls if (m := re.search(r"/([^/]+)\.xml$", u))}


@pytest.mark.parametrize("workers", [4, 1], ids=["fetch-ahead", "serial"])
def test_a_WINDOWED_name_fetches_only_its_window_and_no_4A_and_a_DEFERRED_name_fetches_NOTHING(
    db, tmp_path, concurrency, workers
):
    concurrency(workers)
    tid, sids = _basket(db)

    results, sec, prices = _walk(db, tid, sids, tmp_path / f"cache{workers}")

    cold = _CIK["COLD"]
    # WINDOWED: its submissions index ONCE and only the 3 in-window Form 4 documents — never one of the 5 older
    # ones, never a 4/A (the two 4/As were filed INSIDE the window: they still wait for the full load)
    cold_urls = sec.for_cik(cold)
    assert sum(1 for u in cold_urls if u.endswith(f"CIK{cold}.json")) == 1
    assert _doc_accessions(cold_urls) == {_acc(cold, i) for i in range(1, 4)}
    assert len(cold_urls) == 1 + 3  # the index + the window's documents, each fetched exactly once
    if workers > 1:  # NOT VACUOUS: the fetch-ahead really made these reads, on its worker threads
        assert all(sec.threads[u].startswith("fetch-ahead") for u in cold_urls)
    assert prices.calls.count("COLD") == 1  # its price leg runs as for any name
    # DEFERRED: ZERO vendor requests — no SEC request about it, no price pull
    assert sec.for_cik(_CIK["DEFR"]) == []
    assert "DEFR" not in prices.calls
    # the warm name next to them walks its WHOLE history: all 8 Form 4s and both 4/As
    warm = _CIK["WARM"]
    assert _doc_accessions(sec.for_cik(warm)) == {_acc(warm, i) for i in range(1, 11)}
    # ...and the results say so: listed, never dropped (#9)
    by = {r.ticker: r for r in results}
    assert set(by) == {"DEFR", "COLD", "WARM"}
    assert by["DEFR"].onboarding_deferred and by["DEFR"].edgar_cik is None
    assert by["COLD"].onboarding == "queued" and by["COLD"].filed_since == _SINCE


def test_SERIAL_equals_CONCURRENT_with_the_onboarding_treatment(db, tmp_path, concurrency):
    """#432's contract, held WITH X1: the same requests, the same rows (every table COUNTED and compared)."""
    tid, sids = _basket(db)
    baseline = _snapshot(db)

    concurrency(1)
    r1, sec1, px1 = _walk(db, tid, sids, tmp_path / "serial")
    snap1 = _snapshot(db)
    written = _changed(baseline, snap1)
    assert {"fact_insider_txn", "fact_price_eod", "insider_amendment"} <= set(written)
    _reset(db, written)
    assert _snapshot(db) == baseline

    concurrency(4)
    r2, sec2, px2 = _walk(db, tid, sids, tmp_path / "concurrent")
    snap2 = _snapshot(db)

    assert collections.Counter(sec2.urls) == collections.Counter(sec1.urls)
    assert collections.Counter(px2.calls) == collections.Counter(px1.calls)
    assert {t: len(rows) for t, rows in snap2.items()} == {
        t: len(rows) for t, rows in snap1.items()
    }
    assert snap2 == snap1
    assert [(r.ticker, r.form4_appended, r.onboarding, r.onboarding_deferred) for r in r2] == [
        (r.ticker, r.form4_appended, r.onboarding, r.onboarding_deferred) for r in r1
    ]


def test_the_leg_and_the_fetch_ahead_share_ONE_rule():
    """``form4_leg_filings`` is the single source of what the Form 4 leg reads — the leg and the fetch-ahead both
    call it. Its contract, on direct inputs: the full walk reads every Form 4 and every 4/A; a window reads only
    the Form 4s filed since it, and no 4/A."""
    from ingest.edgar.form4a import form4_leg_filings

    subs = _rows(_CIK["COLD"])
    originals, amendments = form4_leg_filings(subs)
    assert len(originals) == 8 and len(amendments) == 2
    originals, amendments = form4_leg_filings(subs, filed_since=_SINCE)
    assert [f["filed"] for f in originals] == ["2026-09-15"] * 3 and amendments == []
    originals, _ = form4_leg_filings(subs, filed_since=_SINCE + timedelta(days=30))
    assert originals == []
