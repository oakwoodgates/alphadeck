"""The shared filing reader keeps each company's rows as a piece (``dashboards/filings.py`` "Kept per company",
``docs/DASHBOARDS.md`` rule 7): after a basket edit a recompute reads no company and runs no 4/A SELECT; after one
company's change stamp it reads that company alone; and a stored result is reused only for exactly the same read
(the kinds, the window, the shown ticker, the tenant).

The pure tests COUNT the reads: the point-in-time views built and the companies each holds (a stand-in
``PointInTimeData``) and the 4/A SELECTs (a stand-in ``read_amendments`` that keeps the real one's window rule).
The token is injected. The DB-backed tests run the REAL token, the real view and the real SELECT against the test
database, and count the companies the reader built rows for and the SELECTs it ran. Every result is also compared
with a direct read (outside a page compute: pieces off)."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg.types.json import Json

from dashboards import baskets
from dashboards import cache as cache_mod
from dashboards import filings as filings_mod
from dashboards.cache import DashboardCache, Generation
from dashboards.filings import ALL_KINDS, FORM4_KINDS, filings_piece, read_filing_rows
from db.bitemporal import append_fact
from db.change_stamp import mark_changed
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from ingest.edgar.filing_index import ingest_filing_index
from tests.dashboards.test_filing_stream import (
    A_ACC,
    ACC,
    ASOF,
    CIK,
    EARLY_4A,
    EARLY_8K,
    EARLY_F4,
    HELD_4A,
    INGESTED,
    LATE_4A,
    LATE_8K,
    LATE_F4,
    LATER,
    NOW,
    PRIOR,
    S1_ACC,
    StubPit,
    _security,
    _seed_card,
    amendment,
    dilution,
    event,
    index_row,
    split,
    stake,
    txn,
    utc,
)

D = ASOF  # 2026-10-28
K = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)
CONN = object()
W = DEFAULT_TENANT_ID
V = uuid.UUID("00000000-0000-0000-0000-0000000000b2")  # a second workspace

A, B, C = (uuid.UUID(f"00000000-0000-0000-0000-0000000000c{i}") for i in (1, 2, 3))
SIDS = [A, B, C]
TICKERS = {A: "AAA", B: "BBB", C: "CCC"}
B_4A = "0000876543-26-000080"  # a 4/A the tape holds no rows for
B_424 = "0000876543-26-000081"


def _empty(**over) -> StubPit:
    base = dict(
        events=[],
        insider=[],
        stakes=[],
        fundamentals=[],
        splits=[],
        dilution=[],
        catalysts=[],
        cash=[],
        index=[],
    )
    return StubPit(**{**base, **over})


def _companies() -> dict[uuid.UUID, StubPit]:
    """Three companies:

    - A: a row of every kind but a ratified fact, with an APPLIED 4/A (its rows on the Form 4 tape plus its
      record), and two index rows: the 8-K's own (the 8-K row dedupes it) and an S-1 nothing else shows.
    - B: a 4/A RECORD with no tape rows, the index row of that same 4/A (the record row dedupes it) and a 424B5.
      The record has no ``filed`` date, so the SELECT dates it by its acceptance in market time (Friday Oct 9,
      in the evening), while EDGAR's index lists the filing under the next business day (Monday Oct 12).
    - C: nothing on file.
    """
    return {
        A: _empty(
            events=[event()],
            insider=[
                txn(),
                txn(
                    accession=A_ACC,
                    accepted=utc(2026, 10, 22, 20, 0),
                    amends_accession="0000876543-26-000009",
                ),
            ],
            stakes=[stake()],
            fundamentals=[PRIOR, NOW],
            splits=[split()],
            dilution=[dilution()],
            index=[index_row(form="8-K", accession=ACC), index_row(accession=S1_ACC)],
        ),
        B: _empty(
            index=[
                index_row(form="4/A", accession=B_4A, filed=date(2026, 10, 12)),
                index_row(form="424B5", accession=B_424, filed=date(2026, 10, 27)),
            ]
        ),
        C: _empty(),
    }


# the 4/A records as ``read_amendments`` returns them (``day`` = the date the SELECT windows on)
RECORDS = {
    A: [amendment()],  # A_ACC, 2026-10-22
    B: [amendment(accession=B_4A, filed=None, day=date(2026, 10, 9))],
}


class Views:
    """A stand-in ``PointInTimeData``: records the companies each view holds and serves each company's stub."""

    def __init__(self, data: dict[uuid.UUID, StubPit]):
        self.baskets: list[list[uuid.UUID]] = []
        self.data = data

    def __call__(self, conn, *, asof, known_at, data_tenant, workspace, basket, bounds, clock):
        views = self

        class _Pit:
            def __init__(self):
                self.asof, self.clock = asof, clock

            def security_cik(self, sid):
                return "0000876543"

            def security_name(self, sid):
                return "Acme Corp"

            def __getattr__(self, name):  # a fact accessor: the company's own stub answers
                return lambda sid: getattr(views.data[sid], name)(sid)

        self.baskets.append(list(basket))
        return _Pit()

    @property
    def read(self) -> list[uuid.UUID]:
        return [s for b in self.baskets for s in b]


class Amendments:
    """A stand-in ``read_amendments`` that counts its calls and keeps the real SELECT's window rule (a record
    dated before ``start`` or after the as-of is not returned)."""

    def __init__(self):
        self.calls: list[tuple[list[uuid.UUID], date]] = []

    def __call__(self, conn, sids, asof, known_at, clock, *, start, tenant_id=None):
        self.calls.append((list(sids), start))
        out = {}
        for sid in sids:
            got = [r for r in RECORDS.get(sid, []) if start <= r["day"] <= asof]
            if got:
                out[sid] = got
        return out


@pytest.fixture
def views(monkeypatch):
    v = Views(_companies())
    monkeypatch.setattr(baskets, "PointInTimeData", v)
    monkeypatch.setattr(
        filings_mod.master,
        "tickers_for",
        lambda conn, ids, tenant_id=None: {s: TICKERS[s] for s in ids if s in TICKERS},
    )
    return v


@pytest.fixture
def selects(monkeypatch):
    a = Amendments()
    monkeypatch.setattr(filings_mod, "read_amendments", a)
    return a


@pytest.fixture
def stamps(monkeypatch):
    """A movable generation (hard, soft, stamps) and the stamp map the cache reads when the stamps move."""
    state: dict = {"hard": 1, "soft": 1, "fp": 1, "map": {}}
    monkeypatch.setattr(cache_mod, "stamp_map", lambda conn: dict(state["map"]))
    monkeypatch.setattr(cache_mod, "benchmark_security_ids", lambda conn: frozenset())
    return state


@pytest.fixture
def cache(stamps):
    return DashboardCache(
        token=lambda conn: Generation(stamps["hard"], stamps["soft"], stamps["fp"])
    )


def _read(sids=SIDS, *, days=365, kinds=None, tickers=None, workspace=W, **kw):
    return lambda: read_filing_rows(
        CONN,
        sids,
        D,
        K,
        "public",
        days=days,
        kinds=kinds,
        tickers=tickers,
        workspace=workspace,
        **kw,
    )


def _direct(views, selects, **kw):
    """The same read with pieces OFF (outside a page compute), its reads left out of the counts."""
    baskets_before, calls_before = list(views.baskets), list(selects.calls)
    got = _read(**kw)()
    views.baskets[:], selects.calls[:] = baskets_before, calls_before
    return got


# --- the piece's name ----------------------------------------------------------------------------------------


def test_the_piece_is_named_by_the_kinds_read_and_the_window():
    assert filings_piece(None, 365) == "filings.all.365d"
    assert filings_piece(ALL_KINDS, 365) == "filings.all.365d"  # every kind named IS the full read
    assert filings_piece(list(ALL_KINDS), 365) == "filings.all.365d"
    assert filings_piece(FORM4_KINDS, 60) == "filings.form4+form4a.60d"
    assert filings_piece(["form4a", "form4", "form4a"], 60) == "filings.form4+form4a.60d"
    assert filings_piece({"filing"}, 30) == "filings.filing.30d"
    assert len({filings_piece(k, d) for k in (None, FORM4_KINDS, {"8k"}) for d in (30, 365)}) == 6


# --- reads ---------------------------------------------------------------------------------------------------


def test_a_second_identical_read_reads_no_company_and_runs_no_4a_select(cache, views, selects):
    one = cache.get(CONN, "a", (), D, _read(), workspace=W)
    assert views.read == SIDS and len(selects.calls) == 1
    two = cache.get(CONN, "b", (), D, _read(), workspace=W)
    assert two.status == "computed"
    assert views.read == SIDS and len(selects.calls) == 1  # no view built, no SELECT run
    assert two.value == one.value
    assert all(two.value[s] is one.value[s] for s in SIDS)  # the stored tuples themselves
    assert two.value == _direct(views, selects)


def test_a_basket_edit_recomputes_the_page_reading_no_company(cache, views, selects, stamps):
    one = cache.get(CONN, "a", (), D, _read(), workspace=W)
    stamps["soft"] = 2  # a basket edit: the page entry clears, the pieces stay
    again = cache.get(CONN, "a", (), D, _read(), workspace=W)
    assert again.status == "computed" and again.value == one.value
    assert views.read == SIDS and len(selects.calls) == 1
    # a card with fewer names, then one with a name no page read yet: only the new name is read
    cache.get(CONN, "c", (), D, _read([B, A]), workspace=W)
    assert views.read == SIDS
    extra = uuid.UUID("00000000-0000-0000-0000-0000000000c4")
    views.data[extra] = _empty(events=[event()])
    got = cache.get(CONN, "d", (), D, _read([A, extra, B]), workspace=W)
    assert views.baskets[-1] == [extra] and list(got.value) == [A, extra, B]
    assert [r.ticker for r in got.value[extra]] == ["?"]  # no ticker anywhere: the reader's own "?"


def test_one_stamped_company_is_the_only_one_read_again(cache, views, selects, stamps):
    stamps["map"] = {s: 1 for s in SIDS}
    stamps["fp"] = 2
    cache.get(CONN, "a", (), D, _read(), workspace=W)
    assert views.baskets == [SIDS] and len(selects.calls) == 1
    # B files something new and its stamp moves: its pieces drop, nobody else's
    views.data[B].events.append(event(accession="0000876543-26-000090", filed=date(2026, 10, 27)))
    stamps["map"] = {**stamps["map"], B: 2}
    stamps["fp"] = 3
    got = cache.get(CONN, "a", (), D, _read(), workspace=W)
    assert views.baskets == [SIDS, [B]]  # exactly one more view, holding exactly that company
    assert len(selects.calls) == 2  # a company was read, so the SELECT ran (once)
    assert "0000876543-26-000090" in {r.accession for r in got.value[B]}
    assert got.value == _direct(views, selects)


def test_no_company_read_means_no_4a_select_and_no_form4a_kind_means_none_either(
    cache, views, selects
):
    cache.get(CONN, "a", (), D, _read(kinds={"8k", "filing"}), workspace=W)
    assert views.read == SIDS and selects.calls == []  # form4a not read: the SELECT never runs
    cache.get(CONN, "b", (), D, _read(kinds=FORM4_KINDS), workspace=W)
    assert len(selects.calls) == 1 and selects.calls[0][0] == SIDS
    cache.get(CONN, "c", (), D, _read(kinds=FORM4_KINDS), workspace=W)
    assert len(selects.calls) == 1


# --- exact reuse only ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "other",
    [
        {"days": 5},  # a narrower window: never the wider one filtered
        {"days": 500},  # a wider one (it reaches the prior-year revenue print)
        {"kinds": FORM4_KINDS},
        {"kinds": {"filing"}},  # alone, nothing dedupes the index rows
    ],
    ids=["narrower", "wider", "form4-kinds", "index-only"],
)
def test_a_different_window_or_kinds_reads_again_and_equals_a_direct_read(
    cache, views, selects, other
):
    base = cache.get(CONN, "a", (), D, _read(), workspace=W).value
    got = cache.get(CONN, "b", (), D, _read(**other), workspace=W).value
    assert views.baskets == [
        SIDS,
        SIDS,
    ]  # every company read again: nothing served from the base read
    assert got == _direct(views, selects, **other)
    assert got != base and got[A] is not base[A]
    # each read is stored under its own name: asking either again reads no company
    assert cache.get(CONN, "c", (), D, _read(), workspace=W).value == base
    assert cache.get(CONN, "d", (), D, _read(**other), workspace=W).value == got
    assert views.baskets == [SIDS, SIDS]


def test_a_narrower_window_is_not_the_wider_one_filtered(cache, views, selects):
    """WHY a narrower window is its own read. B's 4/A record is dated Oct 9 and EDGAR's index lists the same
    filing under Oct 12. A window that starts Oct 12 leaves the record out of the 4/A SELECT, so nothing claims
    the filing and its index row shows; the wider read's record row claimed it, so filtering the wider stored
    rows down to the window would show nothing for that filing."""
    days = 16
    start = D - timedelta(days=days)
    assert start == date(2026, 10, 12)
    wide = cache.get(CONN, "a", (), D, _read(), workspace=W).value
    narrow = cache.get(CONN, "b", (), D, _read(days=days), workspace=W).value
    assert narrow == _direct(views, selects, days=days)
    filtered = tuple(r for r in wide[B] if start <= r.date <= D)
    assert B_4A not in {r.accession for r in filtered}
    assert [(r.kind, r.form) for r in narrow[B] if r.accession == B_4A] == [("filing", "4/A")]
    assert narrow[B] != filtered


def test_another_tenant_reads_again_and_replaces_the_stored_rows(cache, views, selects):
    base = cache.get(CONN, "a", (), D, _read(), workspace=W).value
    other = cache.get(CONN, "b", (), D, _read(tenant_id=V), workspace=W).value
    assert views.baskets == [SIDS, SIDS]  # the tenant is part of what a stored result depends on
    assert other == _direct(views, selects, tenant_id=V)
    # one stored result per company and name: the default tenant's read is read again, never served the other's
    assert cache.get(CONN, "c", (), D, _read(), workspace=W).value == base
    assert views.baskets == [SIDS, SIDS, SIDS]


def test_the_same_read_asked_another_way_shares_the_stored_rows(cache, views, selects):
    cache.get(CONN, "a", (), D, _read(), workspace=W)
    cache.get(CONN, "b", (), D, _read(kinds=ALL_KINDS), workspace=W)  # every kind named
    cache.get(CONN, "c", (), D, _read(tickers={A: "AAA"}), workspace=W)  # the same shown ticker
    cache.get(CONN, "d", (), D, _read(tickers={}), workspace=W)
    assert views.baskets == [SIDS] and len(selects.calls) == 1
    cache.get(CONN, "e", (), D, _read(kinds=FORM4_KINDS), workspace=W)
    cache.get(CONN, "f", (), D, _read(kinds=["form4a", "form4"]), workspace=W)
    assert views.baskets == [SIDS, SIDS]


def test_a_different_shown_ticker_reads_that_company_again_and_never_serves_the_other_ticker(
    cache, views, selects
):
    base = cache.get(CONN, "a", (), D, _read(), workspace=W).value
    assert {r.ticker for r in base[A]} == {"AAA"}
    card = cache.get(CONN, "b", (), D, _read(tickers={A: "CARD"}), workspace=W).value
    assert views.baskets == [SIDS, [A]]  # only the company whose shown ticker differs
    assert {r.ticker for r in card[A]} == {"CARD"} and card[B] is base[B]
    assert card == _direct(views, selects, tickers={A: "CARD"})
    # the master's ticker again: read again, never handed the rows that say CARD
    again = cache.get(CONN, "c", (), D, _read(), workspace=W).value
    assert views.baskets == [SIDS, [A], [A]]
    assert again == base and {r.ticker for r in again[A]} == {"AAA"}


# --- the same rows with pieces on and off --------------------------------------------------------------------


@pytest.mark.parametrize(
    "days,kinds",
    [
        (365, None),
        (365, FORM4_KINDS),
        (365, {"form4a"}),
        (365, {"filing", "8k"}),
        (30, None),
        (16, None),  # B's 4/A record is outside this window, its index row inside
        (3, None),  # A's is outside too
    ],
)
def test_pieces_on_and_off_give_the_same_rows(cache, views, selects, days, kinds):
    off = _direct(views, selects, days=days, kinds=kinds)
    on = cache.get(CONN, "a", (), D, _read(days=days, kinds=kinds), workspace=W).value
    stored = cache.get(CONN, "b", (), D, _read(days=days, kinds=kinds), workspace=W).value
    assert on == off and stored == off
    assert list(on) == SIDS and on[C] == ()
    assert len(views.baskets) == 1  # the second compute was served whole from the stored rows


def test_the_fixture_holds_the_4a_and_index_cases_the_equality_is_about(cache, views, selects):
    got = cache.get(CONN, "a", (), D, _read(), workspace=W).value
    # A: the applied 4/A is ONE row (the record leads, the tape rows folded in), never two
    (a4,) = [r for r in got[A] if r.kind == "form4a"]
    assert a4.accession == A_ACC and "applied 4/A" in a4.tags
    assert a4.headline.startswith("4/A by Jane Roe amends the Form 4 filed")
    # A: the 8-K's own index row is deduped by the 8-K row; the S-1 nothing else shows stays
    assert [r.accession for r in got[A] if r.kind == "filing"] == [S1_ACC]
    assert [r.accession for r in got[A] if r.kind == "8k"] == [ACC]
    # B: a 4/A record with no tape rows is a row of its own, and it dedupes the 4/A's index row
    (b4,) = [r for r in got[B] if r.kind == "form4a"]
    assert b4.accession == B_4A and b4.tags == ()
    assert [r.accession for r in got[B] if r.kind == "filing"] == [B_424]
    # the index alone: nothing dedupes, so both of each company's index rows show
    alone = cache.get(CONN, "b", (), D, _read(kinds={"filing"}), workspace=W).value
    assert sorted(r.accession for r in alone[A]) == sorted([ACC, S1_ACC])
    assert sorted(r.accession for r in alone[B]) == sorted([B_4A, B_424])


# --- nothing shared or stored --------------------------------------------------------------------------------


def test_another_workspace_never_shares(cache, views, selects):
    cache.get(CONN, "a", (), D, _read(workspace=W), workspace=W)
    cache.get(CONN, "a", (), D, _read(workspace=V), workspace=V)
    assert views.baskets == [SIDS, SIDS] and len(selects.calls) == 2
    stored = cache.piece_count()
    # a read naming another workspace than the page's own: read as before, nothing stored
    cache.get(CONN, "b", (), D, _read(workspace=V), workspace=W)
    cache.get(CONN, "c", (), D, _read(workspace=V), workspace=W)
    assert views.baskets == [SIDS] * 4 and cache.piece_count() == stored


def test_outside_a_page_compute_every_company_is_read_and_nothing_is_stored(cache, views, selects):
    one = _read()()
    two = _read()()
    assert one == two and list(one) == SIDS
    assert views.baskets == [SIDS, SIDS] and len(selects.calls) == 2
    assert cache.piece_count() == 0 and cache_mod.CACHE.piece_count() == 0


def test_a_failed_compute_stores_no_rows(cache, views, selects):
    def boom():
        _read()()
        raise RuntimeError("the page failed after the read")

    with pytest.raises(RuntimeError):
        cache.get(CONN, "a", (), D, boom, workspace=W)
    assert cache.piece_count() == 0
    cache.get(CONN, "a", (), D, _read(), workspace=W)
    assert views.baskets == [SIDS, SIDS]


# --- the stored rows are shared: nobody can change one --------------------------------------------------------


def test_the_stored_rows_cannot_be_changed_by_a_caller(cache, views, selects):
    got = cache.get(CONN, "a", (), D, _read(), workspace=W).value
    rows = [r for sid in SIDS for r in got[sid]]
    assert rows and all(isinstance(got[sid], tuple) for sid in SIDS)
    for r in rows:
        hash(
            r
        )  # every field holds a hashable (so immutable) value: no list or dict to edit in place
        assert isinstance(r.detail, tuple) and isinstance(r.tags, tuple)
    with pytest.raises(FrozenInstanceError):
        rows[0].ticker = "ZZZ"  # type: ignore[misc]
    assert {f.name for f in fields(rows[0])} >= {"detail", "tags", "ticker"}


# --- end to end: the REAL token, the real view, the real 4/A SELECT, the test database ------------------------


@pytest.fixture
def run(monkeypatch):
    """The newest cron run's identity (a run artifact on disk, not the database): held still."""
    monkeypatch.setattr(
        cache_mod, "latest_run_identity", lambda: ("2026-06-01T02:30:00+00:00", "finished", "x")
    )


@pytest.fixture
def counted(monkeypatch):
    """Count the companies the reader builds rows for and the 4/A SELECTs it runs (the real ones still run)."""
    seen: dict = {"companies": [], "selects": 0}
    real_rows, real_select = filings_mod.rows_for_company, filings_mod.read_amendments

    def rows_for_company(pit, who, **kw):
        seen["companies"].append(who.security_id)
        return real_rows(pit, who, **kw)

    def read_amendments(*a, **kw):
        seen["selects"] += 1
        return real_select(*a, **kw)

    monkeypatch.setattr(filings_mod, "rows_for_company", rows_for_company)
    monkeypatch.setattr(filings_mod, "read_amendments", read_amendments)
    return seen


def _snapshot(conn) -> None:
    """A roster edit: a card and a basket snapshot (the soft generation moves)."""
    tid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "a card", "a narrative"),
        )
        cur.execute(
            "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
            "VALUES (gen_random_uuid(), %s, %s, clock_timestamp(), %s, md5(random()::text))",
            (DEFAULT_TENANT_ID, tid, Json([])),
        )
    conn.commit()


def _eight_k(conn, sid, accession: str, filed: date) -> None:
    append_fact(
        conn,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "form": "8-K",
            "items": ["8.01"],
            "accession": accession,
            "filed": filed,
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/876543/{accession.replace('-', '')}/",
            "valid_from": filed,
            "recorded_at": INGESTED,
        },
    )


S1, P424 = "0000876543-26-000131", "0000876543-26-000132"
OTHER_8K, NEW_8K = "0000876543-26-000141", "0000876543-26-000142"


def _seed(db) -> list[uuid.UUID]:
    """Three companies: the Filing Stream fixture's (two 8-Ks, two Form 4s, three 4/A records with no tape rows)
    plus its filing index (an S-1, a 424B5 and the early 8-K's own index row, which the 8-K row dedupes); one
    with a single 8-K; one with nothing on file."""
    _, first = _seed_card(db)
    ingest_filing_index(
        db,
        first,
        CIK,
        [
            {"form": "S-1", "accession": S1, "primary_doc": "s1.htm", "filed": "2026-06-02"},
            {"form": "424B5", "accession": P424, "primary_doc": "", "filed": "2026-06-10"},
            {"form": "8-K", "accession": EARLY_8K, "primary_doc": "k.htm", "filed": "2026-06-03"},
        ],
        recorded_at=INGESTED,
    )
    second = _security(db, "BBB", "0000876544")
    _eight_k(db, second, OTHER_8K, date(2026, 6, 4))
    third = _security(db, "CCC", None)
    db.commit()
    return [first, second, third]


def test_end_to_end_a_basket_edit_reads_no_company_and_a_stamp_reads_one(db, run, counted):
    sids = _seed(db)
    first, second, _ = sids
    known_at = known_at_for_asof(LATER)

    def page():
        return read_filing_rows(db, sids, LATER, known_at, "public", days=365, workspace=W)

    direct = page()  # pieces off
    assert counted == {"companies": sids, "selects": 1}
    # the fixture's cases, on the real view: the 4/A records (no tape rows), the deduped index row
    assert sorted(r.accession for r in direct[first] if r.kind == "form4a") == sorted(
        [EARLY_4A, LATE_4A, HELD_4A]
    )
    assert sorted(r.accession for r in direct[first] if r.kind == "filing") == sorted([S1, P424])
    assert sorted(r.accession for r in direct[first] if r.kind == "8k") == sorted(
        [EARLY_8K, LATE_8K]
    )
    assert sorted(r.accession for r in direct[first] if r.kind == "form4") == sorted(
        [EARLY_F4, LATE_F4]
    )

    counted.update(companies=[], selects=0)
    cache = DashboardCache()
    one = cache.get(db, "page", (), LATER, page, workspace=W)
    assert one.status == "computed" and one.value == direct
    assert counted == {"companies": sids, "selects": 1}

    _snapshot(db)  # a roster edit: the page entry clears, the pieces stay
    again = cache.get(db, "page", (), LATER, page, workspace=W)
    assert again.status == "computed" and again.value == direct
    assert counted == {"companies": sids, "selects": 1}  # no company read, no SELECT run

    # the second company files a new 8-K; its writer stamps it in the same transaction
    _eight_k(db, second, NEW_8K, date(2026, 6, 11))
    mark_changed(db, [second])
    db.commit()
    last = cache.get(db, "page", (), LATER, page, workspace=W)
    assert last.status == "computed"
    assert counted == {
        "companies": sids + [second],
        "selects": 2,
    }  # exactly that company, read again
    assert sorted(r.accession for r in last.value[second]) == sorted([OTHER_8K, NEW_8K])
    assert last.value[first] is one.value[first]  # the others are the stored rows
    assert last.value == page()  # and the whole answer is the direct read's


def test_end_to_end_a_narrower_window_and_one_family_are_their_own_reads(db, run, counted):
    sids = _seed(db)
    known_at = known_at_for_asof(LATER)

    def page(**kw):
        opts = {"days": 365, **kw}
        return lambda: read_filing_rows(db, sids, LATER, known_at, "public", workspace=W, **opts)

    cache = DashboardCache()
    cache.get(db, "wide", (), LATER, page(), workspace=W)
    assert counted == {"companies": sids, "selects": 1}
    for slug, opts in (
        ("narrow", {"days": 5}),
        ("family", {"kinds": FORM4_KINDS}),
        ("card", {"tickers": {sids[0]: "CARD"}}),
    ):
        counted.update(companies=[], selects=0)
        got = cache.get(db, slug, (), LATER, page(**opts), workspace=W).value
        read_again = [sids[0]] if slug == "card" else sids
        assert counted == {"companies": read_again, "selects": 1}, slug
        counted.update(companies=[], selects=0)
        assert got == page(**opts)(), slug  # a direct read, pieces off
    # the narrow window on the real SELECT: what was filed in the last 5 days alone
    narrow = cache.get(db, "narrow", (), LATER, page(days=5), workspace=W).value
    assert all(r.date >= LATER - timedelta(days=5) for r in narrow[sids[0]])
    assert sorted(r.accession for r in narrow[sids[0]] if r.kind == "form4a") == [LATE_4A]
