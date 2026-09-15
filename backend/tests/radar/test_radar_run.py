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
from ingest import CacheMiss
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from radar import repo
from radar.spac import run_spac_radar
from radar.state import StateEvent, deal_state
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


def test_no_index_day_classifier_takes_403_and_404():
    """MEASURED live 2026-08-05: EDGAR's edge serves 403 (not 404) for an absent weekend/holiday
    master.idx — both must classify as the quiet no-index skip, never a logged error."""
    from radar.spac import _is_no_index_day

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    class _HttpErr(Exception):
        def __init__(self, code):
            self.response = _Resp(code)

    assert _is_no_index_day(_HttpErr(403))
    assert _is_no_index_day(_HttpErr(404))
    assert not _is_no_index_day(_HttpErr(500))
    assert not _is_no_index_day(RuntimeError("boom"))


# --- G1: the nightly radar leg builds its EDGAR client with the RECURRING TTL ------------------------


class _RecordingEdgar:
    """Constructor-compatible fake that RECORDS its kwargs; every fetch misses, so the radar skips the
    days quietly (its documented cache-miss path) and the run completes without network."""

    seen: list[dict] = []

    def __init__(self, **kw) -> None:
        self.live_fetches = 0
        _RecordingEdgar.seen.append(kw)

    def get_text(self, url, cache_key):
        raise CacheMiss(cache_key)

    def get_json(self, url, cache_key):
        raise CacheMiss(cache_key)


def test_radar_builds_its_edgar_client_with_the_RECURRING_TTL(db, monkeypatch):
    """G1 — no exceptions on the nightly path. BOTH keys this leg reads are mutable: TODAY's
    daily-index master file GROWS through the day as filings are accepted (a daytime scan cached an
    incomplete index that the 22:30 pass then served — the radar silently missed that evening's deal
    announcements), and submissions/CIK<10>.json is the same mutable index the call path enumerates from.
    Asserted against the CONSTANT (five minutes, not zero — measured in tests/ingest/test_edgar_client.py);
    this leg also memoizes submissions per CIK in-memory, so it never re-reads a key within a pass anyway.
    Only the INJECTED-client path (the tests above) keeps its own dial."""
    _RecordingEdgar.seen = []
    monkeypatch.setattr("radar.spac.EdgarClient", _RecordingEdgar)

    run_spac_radar(db, until=D, days=1, allow_live=True)

    assert len(_RecordingEdgar.seen) == 1
    assert _RecordingEdgar.seen[0]["cache_ttl_s"] == RECURRING_CACHE_TTL_S
    assert _RecordingEdgar.seen[0]["allow_live"] is True  # the other kwargs are unchanged


# --- the ANNOUNCED-only status page: a → announced transition on a thesis-matched shell (#7, #3) ------


class _CaptureNotifier:
    """A complete Notifier double that records each surface separately. The radar leg calls only
    notify_spac_status; implementing all three keeps this a faithful structural Protocol stand-in.
    """

    def __init__(self) -> None:
        self.spac_status: list = []
        self.transitions: list = []
        self.health: list = []

    def notify(self, event) -> None:  # pragma: no cover — the radar leg never calls this
        self.transitions.append(event)

    def notify_health(self, event) -> None:  # pragma: no cover — nor this
        self.health.append(event)

    def notify_spac_status(self, event) -> None:
        self.spac_status.append(event)


def _cache_for(tmp_path, *, index_rows, subs, forms):
    """Build a minimal EDGAR fixture cache for one 2026-08-03 index: ``index_rows`` = list of
    (cik, name, form, accession); ``subs`` = {cik10: submissions dict}; ``forms`` = {accession: text}.
    Mirrors the ``cache`` fixture's on-disk shape for tests needing their own filing set."""
    header = "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 80 + "\n"
    body = "".join(
        f"{cik}|{name}|{form}|2026-08-03|edgar/data/{cik}/{acc}.txt\n"
        for cik, name, form, acc in index_rows
    )
    (tmp_path / "daily-index").mkdir()
    (tmp_path / "daily-index" / "master.20260803.idx").write_text(header + body, encoding="utf-8")
    sdir = tmp_path / "submissions"
    sdir.mkdir()
    for cik10, payload in subs.items():
        (sdir / f"CIK{cik10}.json").write_text(json.dumps(payload), encoding="utf-8")
    for acc, text in forms.items():
        d = tmp_path / "forms" / acc
        d.mkdir(parents=True)
        (d / "full.txt").write_text(text, encoding="utf-8")
    return tmp_path


def _state_events(db, cik10: str) -> list[StateEvent]:
    """The CIK's full recorded event history, as the state walk reads it (for asserting deal_state)."""
    return [
        StateEvent(
            filed=r["filed"],
            form=r["form"],
            items=tuple(r["items"]) if r["items"] else None,
            accession=r["accession"],
        )
        for r in repo.events_for_ciks(db, [cik10])
    ]


def test_announced_transition_on_a_matched_company_pages_once(db, cache):
    """The headline: a shell whose DA moved it searching → announced AND whose DA doc hit a thesis's
    term set fires exactly ONE page (per matched thesis). A shell that ALSO went → announced but matched
    nothing does NOT page — loudness marks the exception (#7)."""
    _seed_master(
        db, "0000001111", "KNWN", "Blank Checks"
    )  # 8-K item 1.01 → announced, but no term hit
    _seed_master(
        db, "0000002222", "NEWS", None
    )  # the 425 → announced AND its doc hits "psilocybin"
    thesis = _seed_thesis(db)
    client = EdgarClient(cache_dir=cache, allow_live=False)
    notifier = _CaptureNotifier()

    r = run_spac_radar(db, until=D, days=1, edgar_client=client, notifier=notifier)

    assert r.status_notifications == 1 and len(notifier.spac_status) == 1
    evt = notifier.spac_status[0]
    assert evt.cik == "0000002222" and evt.ticker == "NEWS"  # 1111 (unmatched) did NOT page
    assert evt.thesis_id == thesis.id and evt.thesis_name == "Rainbow"
    assert evt.signal_terms == ("psilocybin",) and evt.broad_terms == ()
    assert evt.accession == "0002222222-26-000002"
    assert "announced" in evt.label and "Rainbow" in evt.label


def test_a_second_run_over_the_same_transition_is_silent(db, cache):
    """Idempotency: the baseline is the PRIOR RECORDED state, not a fresh re-compare. A re-scan finds
    the events already stored, so prior == announced == new → no transition → no page (COUNT the pages).
    """
    _seed_master(db, "0000001111", "KNWN", "Blank Checks")
    _seed_master(db, "0000002222", "NEWS", None)
    _seed_thesis(db)
    client = EdgarClient(cache_dir=cache, allow_live=False)

    first = _CaptureNotifier()
    run_spac_radar(db, until=D, days=1, edgar_client=client, notifier=first)
    assert len(first.spac_status) == 1  # the → announced transition paged once

    second = _CaptureNotifier()
    r2 = run_spac_radar(db, until=D, days=1, edgar_client=client, notifier=second)
    assert second.spac_status == [] and r2.status_notifications == 0  # silent on the re-scan


def test_an_unchanged_status_pages_nothing(db, tmp_path):
    """A known shell files a DEF 14A (an extension proxy) — a WATCHED form, so it records an event, but
    it is neither an announce form nor an 8-K, so the deal stays `searching`: no → announced, no page.
    """
    _seed_master(db, "0000006666", "EXTN", "Blank Checks")
    _seed_thesis(db)
    cache = _cache_for(
        tmp_path,
        index_rows=[("6666", "Extending Corp", "DEF 14A", "0006666666-26-000001")],
        subs={"0000006666": _submissions("6666", "Blank Checks")},
        forms={},  # DEF 14A is not a DA-match form — never fetched for matching
    )
    client = EdgarClient(cache_dir=cache, allow_live=False)
    notifier = _CaptureNotifier()

    r = run_spac_radar(db, until=D, days=1, edgar_client=client, notifier=notifier)

    assert r.events_appended == 1  # the proxy WAS recorded (a watched event) …
    assert deal_state(_state_events(db, "0000006666")) == "searching"  # … but the deal did not move
    assert notifier.spac_status == [] and r.status_notifications == 0


def test_announced_on_a_non_matched_universe_pages_nothing(db, cache):
    """→ announced on a shell whose DA matches NO thesis pages nothing (matched-only, #7). Both fixture
    shells transition, but the only thesis's terms hit neither DA doc, so there is no page."""
    _seed_master(db, "0000001111", "KNWN", "Blank Checks")
    _seed_master(db, "0000002222", "NEWS", None)
    t = Thesis(id=uuid.uuid4(), name="Elsewhere", narrative="x", tenant_id=DEFAULT_TENANT_ID)
    thesis_repo.upsert(db, t)
    thesis_repo.set_term_set(
        db, t.id, [TermSetEntry(term="quantum computing", tier=TermTier.SIGNAL)]
    )
    db.commit()
    client = EdgarClient(cache_dir=cache, allow_live=False)
    notifier = _CaptureNotifier()

    r = run_spac_radar(db, until=D, days=1, edgar_client=client, notifier=notifier)

    assert r.matches_appended == 0  # nothing matched the thesis …
    assert notifier.spac_status == [] and r.status_notifications == 0  # … so nothing paged


@pytest.mark.parametrize("item,expected", [("1.02", "terminated"), ("2.01", "completed")])
def test_terminated_and_completed_do_NOT_page_announced_only(db, tmp_path, item, expected):
    """ANNOUNCED-only: 5555 is ALREADY announced (a prior 425 in the log); this run's 8-K (item 1.02 /
    2.01) moves the deal announced → terminated / completed — a REAL transition, asserted non-vacuously —
    but it is NOT → announced, so nothing pages."""
    sid = _seed_master(db, "0000005555", "TERM", "Blank Checks")
    _seed_thesis(db)  # a live thesis exists to match against — yet the move still must not page
    with (
        db.cursor() as cur
    ):  # seed the prior announce so this run's baseline deal-state is `announced`
        cur.execute(
            "INSERT INTO fact_spac_event (tenant_id, cik, security_id, company_name, form, items, "
            "filed, accession, source_ref, valid_from) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                DEFAULT_TENANT_ID,
                "0000005555",
                sid,
                "Terminating Corp",
                "425",
                None,
                date(2026, 7, 1),
                "0005555555-26-000001",
                "http://x",
                date(2026, 7, 1),
            ),
        )
    db.commit()
    cache = _cache_for(
        tmp_path,
        index_rows=[("5555", "Terminating Corp", "8-K", "0005555555-26-000009")],
        subs={
            "0000005555": _submissions(
                "5555",
                "Blank Checks",
                accessions=["0005555555-26-000009"],
                items=[item],
                forms=["8-K"],
            )
        },
        forms={"0005555555-26-000009": "<html>Business combination update.</html>"},
    )
    client = EdgarClient(cache_dir=cache, allow_live=False)
    notifier = _CaptureNotifier()

    r = run_spac_radar(db, until=D, days=1, edgar_client=client, notifier=notifier)

    assert deal_state(_state_events(db, "0000005555")) == expected  # the move really happened …
    assert notifier.spac_status == [] and r.status_notifications == 0  # … just not one we page on
