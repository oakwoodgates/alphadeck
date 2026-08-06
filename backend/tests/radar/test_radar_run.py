"""The radar watcher end-to-end over a FIXTURE cache (allow_live=False — the suite never touches
the network): daily-index scan → lazy accretion (an unknown 425-filer classified from its own
submissions SIC, its master row durably enriched) → event persistence → term matching. The
idempotency assertions COUNT THE TABLES (convention: the as-of read dedups, so only a count can
catch a silent re-append)."""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import TermTier
from domain.thesis import TermSetEntry, Thesis
from ingest.edgar.client import EdgarClient
from radar.spac import run_spac_radar
from repositories import thesis_repo

D = date(2026, 8, 3)

INDEX = """CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
1111|KNOWN SHELL CORP|8-K|2026-08-03|edgar/data/1111/0001111111-26-000001.txt
2222|New Shell Acquisition Corp|425|2026-08-03|edgar/data/2222/0002222222-26-000002.txt
3333|Ordinary Pharma Inc|425|2026-08-03|edgar/data/3333/0003333333-26-000003.txt
4444|Noise Filer Inc|10-K|2026-08-03|edgar/data/4444/0004444444-26-000004.txt
"""


def _submissions(cik: str, sic_desc: str, accessions=(), items=(), forms=()) -> dict:
    return {
        "cik": cik,
        "sicDescription": sic_desc,
        "tickers": [],
        "exchanges": [],
        "filings": {
            "recent": {
                "accessionNumber": list(accessions),
                "items": list(items),
                "form": list(forms),
                "primaryDocument": ["x.htm"] * len(list(accessions)),
                "filingDate": ["2026-08-03"] * len(list(accessions)),
                "reportDate": [""] * len(list(accessions)),
            }
        },
    }


@pytest.fixture
def cache(tmp_path):
    """A pre-seeded EDGAR cache dir — exactly the keys the watcher reads."""
    (tmp_path / "daily-index").mkdir()
    (tmp_path / "daily-index" / "master.20260803.idx").write_text(INDEX, encoding="utf-8")
    subs = tmp_path / "submissions"
    subs.mkdir()
    (subs / "CIK0000001111.json").write_text(
        json.dumps(
            _submissions(
                "1111",
                "Blank Checks",
                accessions=["0001111111-26-000001"],
                items=["1.01,9.01"],
                forms=["8-K"],
            )
        ),
        encoding="utf-8",
    )
    (subs / "CIK0000002222.json").write_text(
        json.dumps(_submissions("2222", "Blank Checks")), encoding="utf-8"
    )
    (subs / "CIK0000003333.json").write_text(
        json.dumps(_submissions("3333", "Pharmaceutical Preparations")), encoding="utf-8"
    )
    for accession, text in (
        ("0001111111-26-000001", "<html>completion-of-nothing boilerplate</html>"),
        ("0002222222-26-000002", "<html>A Psilocybin therapeutics business combination.</html>"),
    ):
        d = tmp_path / "forms" / accession
        d.mkdir(parents=True)
        (d / "full.txt").write_text(text, encoding="utf-8")
    return tmp_path


def _seed_master(db, cik10: str, ticker: str, sector: str | None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, sector, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik10, sector, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _seed_thesis(db) -> Thesis:
    t = Thesis(
        id=uuid.uuid4(),
        name="Rainbow",
        narrative="psychedelics",
        tenant_id=DEFAULT_TENANT_ID,
    )
    thesis_repo.upsert(db, t)
    thesis_repo.set_term_set(
        db,
        t.id,
        [
            TermSetEntry(term="psilocybin", tier=TermTier.SIGNAL),
            TermSetEntry(term="uranium enrichment", tier=TermTier.BROAD),
        ],
    )
    db.commit()
    return t


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test-only, fixed names
        return cur.fetchone()["n"]


def test_radar_run_accretes_persists_matches_and_is_idempotent(db, cache):
    _seed_master(db, "0000001111", "KNWN", "Blank Checks")  # known shell (source (i))
    news_sid = _seed_master(db, "0000002222", "NEWS", None)  # unknown until the 425 (source (ii))
    thesis = _seed_thesis(db)
    client = EdgarClient(cache_dir=cache, allow_live=False)

    r = run_spac_radar(db, until=D, days=1, edgar_client=client)

    # accretion: the unknown 425-filer classified from its own SIC; the pharma 425-filer was not
    assert r.shells_admitted == ["0000002222"]
    with db.cursor() as cur:
        cur.execute("SELECT sector FROM security_master WHERE id = %s", (news_sid,))
        assert cur.fetchone()["sector"] == "Blank Checks"  # durably enriched — next run source (i)

    # events: the known shell's 8-K (items resolved) + the admitted shell's 425; nothing else
    assert r.events_appended == 2 and r.events_unchanged == 0
    assert _count(db, "fact_spac_event") == 2
    with db.cursor() as cur:
        cur.execute("SELECT form, items, security_id FROM fact_spac_event ORDER BY form")
        rows = cur.fetchall()
    assert rows[0]["form"] == "425" and rows[0]["security_id"] == news_sid
    assert rows[1]["form"] == "8-K" and rows[1]["items"] == ["1.01", "9.01"]

    # matching: both events are DA-class (425; 8-K with 1.01) — only the 425's doc carries the term
    assert r.docs_matched == 2
    assert r.matches_appended == 1
    assert _count(db, "fact_spac_match") == 1
    with db.cursor() as cur:
        cur.execute("SELECT thesis_id, matched_signal, matched_broad FROM fact_spac_match")
        m = cur.fetchone()
    assert m["thesis_id"] == thesis.id
    assert m["matched_signal"] == ["psilocybin"] and m["matched_broad"] == []

    # IDEMPOTENCY — the re-scan appends nothing; the TABLES do not grow (count, not the read)
    r2 = run_spac_radar(db, until=D, days=1, edgar_client=client)
    assert r2.events_appended == 0 and r2.events_unchanged == 2
    assert r2.matches_appended == 0 and r2.matches_unchanged == 1
    assert r2.shells_admitted == []  # now known via the enriched master row, not re-admitted
    assert _count(db, "fact_spac_event") == 2
    assert _count(db, "fact_spac_match") == 1

    assert r.errors == [] and r2.errors == []


def test_uncached_day_skips_quietly_no_live(db, cache):
    client = EdgarClient(cache_dir=cache, allow_live=False)
    r = run_spac_radar(db, until=date(2026, 8, 4), days=1, edgar_client=client)
    assert r.dates_scanned == [] and r.events_appended == 0
    assert any("not cached" in s for s in r.dates_skipped)
    assert r.errors == []
