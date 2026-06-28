"""Lazy master identity enrichment (Workbench enrichment, Slice 2) — ``enrich_for_ciks``.

Pins: a genuine submissions doc enriches the master row; a broken / non-submissions response is SKIPPED (never
a false 'inactive'); a per-CIK fetch fault skips just that name; a re-run UPDATEs in place (count the table).
"""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from ingest import CacheMiss
from securities import master
from workbench.enrichment import enrich_for_ciks


def _insert(db, ticker, *, name=None, cik=None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _key(cik: str) -> str:
    """The submissions cache_key ``fetch_submissions`` builds for a CIK."""
    return f"submissions/CIK{int(cik):010d}.json"


def _subs(cik, *, sic="Electric Services", exchanges=("NYSE",), tickers=("OKLO",)) -> dict:
    """A genuine-shaped submissions doc (echoes a top-level ``cik`` like the real SEC payload)."""
    return {
        "cik": cik,
        "sicDescription": sic,
        "exchanges": list(exchanges),
        "tickers": list(tickers),
        "formerNames": [],
    }


class _FakeEdgar:
    """Canned submissions JSON by cache_key; an unknown key raises ``CacheMiss`` (like the real EdgarClient when
    a doc isn't cached and live is disabled)."""

    def __init__(self, docs: dict) -> None:
        self.docs = docs

    def get_json(self, url, cache_key):
        if cache_key not in self.docs:
            raise CacheMiss(cache_key)
        return self.docs[cache_key]


def test_enrich_for_ciks_writes_identity_from_a_genuine_submissions(db):
    sid = _insert(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    edgar = _FakeEdgar(
        {_key("0001849056"): _subs("1849056", exchanges=("Nasdaq",), tickers=("OKLO",))}
    )
    out = enrich_for_ciks(db, edgar, {"0001849056": sid})
    assert out == {"enriched": 1, "skipped": 0}
    sec = master.get(db, sid)
    assert (sec.sector, sec.exchange, sec.status) == ("Electric Services", "Nasdaq", "active")


def test_enrich_for_ciks_marks_no_listing_inactive(db):
    """A genuine submissions with no current ticker / exchange -> 'inactive' (the listing-presence heuristic)."""
    sid = _insert(db, "DEAD", name="Defunct Co", cik="0000000001")
    edgar = _FakeEdgar({_key("0000000001"): _subs("1", exchanges=(), tickers=())})
    assert enrich_for_ciks(db, edgar, {"0000000001": sid}) == {"enriched": 1, "skipped": 0}
    assert master.get(db, sid).status == "inactive"


def test_enrich_for_ciks_skips_a_non_submissions_response(db):
    """A broken / EFTS-shaped response (no top-level 'cik') is SKIPPED, never written — so a bad fetch can't
    harden into a false 'inactive' (the operator-note honesty bound)."""
    sid = _insert(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    edgar = _FakeEdgar({_key("0001849056"): {"hits": {"total": {"value": 0}, "hits": []}}})
    assert enrich_for_ciks(db, edgar, {"0001849056": sid}) == {"enriched": 0, "skipped": 1}
    assert master.get(db, sid).status is None  # never written — abstains


def test_enrich_for_ciks_is_failvisible_per_cik(db):
    """A fetch fault on one CIK skips just that name (left un-enriched) and never aborts the rest."""
    good = _insert(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    bad = _insert(
        db, "BAD", name="Bad Co", cik="0000000009"
    )  # its submissions key is absent -> CacheMiss
    edgar = _FakeEdgar({_key("0001849056"): _subs("1849056")})
    out = enrich_for_ciks(db, edgar, {"0001849056": good, "0000000009": bad})
    assert out == {"enriched": 1, "skipped": 1}
    assert master.get(db, good).status == "active"
    assert master.get(db, bad).status is None  # the fault left it un-enriched


def test_enrich_for_ciks_is_idempotent_count_the_table(db):
    """A re-run UPDATEs in place — the security_master row COUNT never grows (count the table, not the read)."""
    sid = _insert(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    edgar = _FakeEdgar({_key("0001849056"): _subs("1849056")})
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        before = cur.fetchone()["n"]
    enrich_for_ciks(db, edgar, {"0001849056": sid})
    enrich_for_ciks(db, edgar, {"0001849056": sid})
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before  # UPDATE-in-place, never appended
