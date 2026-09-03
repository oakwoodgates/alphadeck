"""The demo seed appends ZERO fact rows on a second run — COUNT THE TABLE, never a read.

The backend image runs ``pipeline.seed`` at EVERY container start, so a seed that is "idempotent" only on
the thesis spine still grew the fact tables by a full fixture version per boot (measured before the guard:
~140–155 stored versions of every seed price bar, 175 of each seed Form 4 fact). The bitemporal as-of read
DEDUPS those (``DISTINCT ON (natural key) … recorded_at DESC``), so the pile-up hid behind a perfectly
correct read while the table silently grew — which is exactly why these assertions count ``count(*)``
before and after a re-run rather than checking that the read still looks right.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import CatalystType, Grade
from ingest.catalyst import ingest_catalyst
from ingest.prices.eod_loader import parse_yahoo_chart
from pipeline import seed as seed_mod
from pipeline.seed import (
    _SEED_DATA,
    _UNH_FORM4S,
    HIMS_SECURITY_ID,
    LEU_ID,
    NNE_ID,
    OKLO_ID,
    SMR_ID,
    UNH_SECURITY_ID,
)

# every table the seed writes: the eight fact tables, plus the spine it upserts (already idempotent —
# asserted here so a regression on either half fails this test)
_FACT_TABLES = [
    "fact_price_eod",
    "fact_insider_txn",
    "fact_catalyst",
    "fact_dilution",
    "fact_theme_conviction",
    "fact_revenue_mix",
    "fact_shares_outstanding",
    "fact_cash_burn",
]
_SPINE_TABLES = [
    "thesis",
    "basket_member",
    "evidence",
    "catalyst",
    "kill_criterion",
    "security_master",
]
_ALL_TABLES = _FACT_TABLES + _SPINE_TABLES

# the six seed names -> the committed price fixture each one is loaded from
_SEED_PRICE_FIXTURES = {
    HIMS_SECURITY_ID: "HIMS",
    UNH_SECURITY_ID: "UNH",
    SMR_ID: "SMR",
    OKLO_ID: "OKLO",
    NNE_ID: "NNE",
    LEU_ID: "LEU",
}
_SEED_SECURITY_IDS = list(_SEED_PRICE_FIXTURES)


def _fixture_bars(ticker: str) -> list[dict]:
    return parse_yahoo_chart(
        json.loads((_SEED_DATA / "prices" / f"{ticker}.yahoo.json").read_text(encoding="utf-8"))
    )


def _seed_everything(conn) -> list:
    """The exact sequence ``pipeline.seed.main`` runs at container start. Returns the DOE feed's emitted
    catalysts (the only writer whose per-run output the caller can see)."""
    seed_mod.seed_hims(conn)
    seed_mod.seed_nuclear(conn)
    emitted = seed_mod.seed_doe_catalysts(conn)
    seed_mod.seed_nuclear_theme_conviction(conn)
    seed_mod.seed_nuclear_revenue_mix(conn)
    seed_mod.seed_nuclear_shares(conn)
    seed_mod.seed_nuclear_cash_burn(conn)
    seed_mod.seed_unh(conn)
    conn.commit()
    return emitted


def _counts(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in _ALL_TABLES:
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed literal list
            out[table] = cur.fetchone()["n"]
    return out


@pytest.fixture
def seeded(db):
    """Seed once, hand back ``(conn, counts_after_the_first_run, emitted_doe_catalysts)``."""
    emitted = _seed_everything(db)
    return db, _counts(db), emitted


def test_a_second_seed_run_appends_zero_rows_to_every_table(seeded):
    """The whole point: re-running the seed over an already-seeded DB writes NOTHING.

    Asserted on ``count(*)`` per table, not on what an as-of read returns — a duplicate version is
    invisible to the read and visible only in the row count.
    """
    conn, after_first, _ = seeded
    _seed_everything(conn)
    after_second = _counts(conn)
    assert after_second == after_first


def test_a_third_and_fourth_run_stay_flat_too(seeded):
    """The guard is a fixed point, not a one-shot: the boot loop runs this hundreds of times."""
    conn, after_first, _ = seeded
    _seed_everything(conn)
    _seed_everything(conn)
    _seed_everything(conn)
    assert _counts(conn) == after_first


def test_the_first_run_still_writes_every_fixture(seeded):
    """A guard that skipped everything would also pass the count test — so pin what run 1 must contain."""
    conn, counts, emitted = seeded

    # nothing the seed writes may be empty
    for table in _ALL_TABLES:
        assert counts[table] > 0, f"{table} is empty after the first seed run"

    # the insider fixtures: the HIMS Wells buy + the five UNH cluster filings, one stored fact each,
    # exactly one version apiece
    assert counts["fact_insider_txn"] == 6
    with conn.cursor() as cur:
        cur.execute(
            "SELECT accession, count(*) AS n FROM fact_insider_txn "
            "WHERE security_id = %s GROUP BY accession",
            (UNH_SECURITY_ID,),
        )
        unh = {r["accession"]: r["n"] for r in cur.fetchall()}
    assert set(unh) == {accession for accession, _ in _UNH_FORM4S}
    assert set(unh.values()) == {1}

    # the operator-ratified Workbench facts (one per nuclear name) and the single-fact fixtures
    assert counts["fact_revenue_mix"] == 4
    assert counts["fact_shares_outstanding"] == 4
    assert counts["fact_cash_burn"] == 4
    assert counts["fact_dilution"] == 1  # the HIMS convertible-notes overhang
    assert counts["fact_theme_conviction"] == 1  # the nuclear theme conviction (thesis-scoped)
    # every award the DOE feed emitted became exactly one stored catalyst fact
    assert counts["fact_catalyst"] == len(emitted)

    # prices: real history for all six seed names, and NOT ONE duplicate version
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS rows, count(DISTINCT (security_id, d)) AS bars FROM fact_price_eod"
        )
        row = cur.fetchone()
    assert row["rows"] == row["bars"], "the seed stored more than one version of a bar"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, count(DISTINCT d) AS bars FROM fact_price_eod "
            "WHERE security_id = ANY(%s) GROUP BY security_id",
            (_SEED_SECURITY_IDS,),
        )
        per_name = {r["security_id"]: r["bars"] for r in cur.fetchall()}
    # every bar in each committed fixture is stored — the guard skips duplicates, never real history
    expected = {sid: len(_fixture_bars(t)) for sid, t in _SEED_PRICE_FIXTURES.items()}
    assert per_name == expected
    assert all(n > 200 for n in expected.values()), expected  # real ~1y histories, not stubs


def test_the_guard_is_seed_local_a_real_restatement_still_appends(seeded):
    """The shared ``ingest_*`` writers are UNGUARDED by design: a genuine restatement through the normal
    ingest path must still append a new version. Only the seed's own fixture replay is suppressed.
    """
    conn, counts, emitted = seeded
    catalyst = next(c for c in emitted if c.ticker == "LEU")

    ingest_catalyst(  # same source_ref (the natural key), a different grade = a real re-version
        conn,
        LEU_ID,
        catalyst_type=CatalystType.GOV_FUNDING,
        grade=Grade.FLIP,
        label="re-graded on review",
        source="ratified",
        source_ref=catalyst.source_ref,
        event_date=catalyst.event_date,
    )
    conn.commit()
    assert _counts(conn)["fact_catalyst"] == counts["fact_catalyst"] + 1


def test_a_bar_the_store_is_missing_is_still_filled(db):
    """The price guard is per-DATE, not tail-only: it keeps the seed's promise that its fixture history is
    present. Ingesting a truncated fixture, then the whole one, appends exactly the absent bars."""
    sid = UUID("11150000-0000-0000-0000-0000000000ff")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "0009999999", "GAPCO", "Gap Test Co", "2026-01-01"),
        )
    bars = _fixture_bars("HIMS")
    assert len(bars) > 10
    head, tail = bars[:-5], bars[-5:]

    assert seed_mod._ingest_new_bars(db, sid, head) == len(head)
    assert seed_mod._ingest_new_bars(db, sid, bars) == len(tail)  # only the absent dates
    assert seed_mod._ingest_new_bars(db, sid, bars) == 0  # now a no-op
    db.commit()

    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS rows, count(DISTINCT d) AS bars FROM fact_price_eod "
            "WHERE security_id = %s",
            (sid,),
        )
        row = cur.fetchone()
    assert row["rows"] == row["bars"] == len(bars)


def test_form4_guard_skips_a_stored_accession_and_ingests_a_new_one(db):
    """``existing_accessions`` is the Form-4 guard's whole basis: an accession already stored is skipped,
    an unseen one is ingested — so a fixture ADDED to the seed later still lands."""
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "0000731766", "UNHT", "UNH fixture stand-in", "2026-01-01"),
        )
    first, second = _UNH_FORM4S[0], _UNH_FORM4S[1]

    assert seed_mod._ingest_form4_once(db, sid, [first]) == 1
    assert seed_mod._ingest_form4_once(db, sid, [first]) == 0  # already stored -> skipped
    assert seed_mod._ingest_form4_once(db, sid, [first, second]) == 1  # only the new accession
    db.commit()

    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn WHERE security_id = %s", (sid,))
        assert cur.fetchone()["n"] == 2
