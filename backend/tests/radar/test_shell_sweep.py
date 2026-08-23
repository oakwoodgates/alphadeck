"""The SPAC shell sweep end-to-end over a FIXTURE cache (allow_live=False — the suite never
touches the network): selector -> facts-only enrich -> the radar handoff -> idempotency ->
fail-open recall -> the CLI health gate -> the de-SPAC re-enrich -> the structural import guard.

The governing invariant under test throughout: the units-``U`` / "Acquisition Corp" pattern only
SELECTS which CIKs to fetch; the SEC ``sicDescription`` (stored verbatim) is the sole authority
for shell-or-not. Idempotency assertions COUNT THE TABLE (``master.enrich`` UPDATEs in place, so
the count never grows), per convention."""

from __future__ import annotations

import ast
import json
import uuid
from datetime import date
from pathlib import Path

from db.session import DEFAULT_TENANT_ID
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from pipeline import spac_sweep
from pipeline.provision_tenant import provision_tenant
from radar import repo
from radar.shell_sweep import (
    run_shell_sweep,
    select_known_shells_lru,
    select_unenriched_candidates,
)
from radar.spac import run_spac_radar
from securities import master

OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ad")
D = date(2026, 8, 3)

AIRCRAFT_PARTS = "Aircraft Parts & Auxiliary Equipment, NEC"  # SIC 3728 — the FACT II shape
PHARMA = "Pharmaceutical Preparations"


def _submissions(cik: str, sic_desc: str, accessions=(), items=(), forms=()) -> dict:
    """A genuine-shaped submissions doc (echoes a top-level ``cik``, like the real SEC payload);
    ``filings.recent`` carried only where a test's radar leg needs item resolution."""
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


def _cache_subs(tmp_path: Path, cik10: str, doc: dict) -> None:
    subs = tmp_path / "submissions"
    subs.mkdir(exist_ok=True)
    (subs / f"CIK{cik10}.json").write_text(json.dumps(doc), encoding="utf-8")


def _seed_master(
    db, cik10, ticker, name, sector=None, *, tenant_id=DEFAULT_TENANT_ID, is_primary=None
):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, sector, is_primary, "
            "valid_from) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, tenant_id, cik10, ticker, name, sector, is_primary, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _count(db, where: str = "TRUE") -> int:
    with db.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM security_master WHERE {where}"
        )  # noqa: S608 — test-only
        return cur.fetchone()["n"]


def _client(tmp_path: Path) -> EdgarClient:
    return EdgarClient(cache_dir=tmp_path, allow_live=False)


# --- 1. the selector: a fetch selector, never a classifier ---


def test_selector_returns_exactly_the_unenriched_spac_structured_ciks(db):
    """Selected: units-``U``-sibling CIKs and 'acquisition corp' names with NO non-NULL sector on
    any row. Excluded: already enriched (6770 OR other — the FACT II shape), non-SPAC-structured
    names, and other tenants' rows (tenant-scoped like every master read)."""
    provision_tenant(db, "other", tenant_id=OTHER_TENANT_ID)
    db.commit()
    # units pair, un-enriched -> selected (no name hit needed)
    _seed_master(db, "0000001111", "AAA", "Alpha SPAC I", is_primary=True)
    _seed_master(db, "0000001111", "AAAU", "Alpha SPAC I Units")
    # name hit, un-enriched -> selected
    _seed_master(db, "0000002222", "NEWS", "New Shell Acquisition Corp")
    # name hit but already enriched Blank Checks -> excluded (incremental by construction)
    _seed_master(db, "0000003333", "DONE", "Done Acquisition Corp", sector=repo.BLANK_CHECKS)
    # units pair enriched to an OPERATING sector on the primary row -> excluded (max(sector) non-null)
    _seed_master(
        db,
        "0000004444",
        "FACT",
        "FACT Two Acquisition Corp II",
        sector=AIRCRAFT_PARTS,
        is_primary=True,
    )
    _seed_master(db, "0000004444", "FACTU", "FACT Two Acquisition Corp II Units")
    # non-SPAC-structured, un-enriched -> excluded (not a candidate at all)
    _seed_master(db, "0000005555", "PHRM", "Ordinary Pharma Inc")
    # another tenant's candidate -> excluded (tenant-scoped)
    _seed_master(db, "0000006666", "FRGN", "Foreign Acquisition Corp", tenant_id=OTHER_TENANT_ID)

    assert select_unenriched_candidates(db) == ["0000001111", "0000002222"]
    assert select_unenriched_candidates(db, tenant_id=OTHER_TENANT_ID) == ["0000006666"]


# --- 2. facts-only: the stored sector is the SEC value, never the name/units pattern ---


def test_stored_sector_is_the_sec_value_never_the_name_pattern(db, tmp_path):
    """Both candidates match the NAME pattern; only the SEC SIC decides. The 6770 one flags
    (joins ``known_shell_ciks``); the operating '...Acquisition Corp' enriches to its real
    sector verbatim and correctly never flags. Nothing name-derived is stored."""
    shell = _seed_master(db, "0000002222", "NEWS", "New Shell Acquisition Corp")
    factii = _seed_master(db, "0000004444", "FACT", "FACT Two Acquisition Corp II")
    _cache_subs(tmp_path, "0000002222", _submissions("2222", repo.BLANK_CHECKS))
    _cache_subs(tmp_path, "0000004444", _submissions("4444", AIRCRAFT_PARTS))

    r = run_shell_sweep(db, edgar_client=_client(tmp_path))

    assert (r.candidates, r.attempted, r.enriched, r.remaining) == (2, 2, 2, 0)
    assert r.admitted == ["0000002222"] and r.other == 1
    assert r.flipped == [] and r.reconfirmed == 0 and r.errors == []
    assert master.get(db, shell).sector == repo.BLANK_CHECKS  # the SEC value, verbatim
    assert master.get(db, factii).sector == AIRCRAFT_PARTS  # verbatim too — no name leak
    assert repo.known_shell_ciks(db) == {"0000002222"}  # flags; FACT II correctly does not


# --- 3. the radar handoff: an admitted shell's previously-dropped 8-K is collected ---


def test_sweep_admission_feeds_the_next_radar_run(db, tmp_path):
    """The measured gap, reproduced then closed: an un-enriched shell's 8-K is DROPPED by the
    radar (8-K is a KNOWN-ONLY form — no accretion attempt); after the sweep enriches the CIK,
    the next radar pass — which reads ``known_shell_ciks`` at its start — collects it."""
    _seed_master(db, "0000002222", "NEWS", "New Shell Acquisition Corp")
    (tmp_path / "daily-index").mkdir()
    (tmp_path / "daily-index" / "master.20260803.idx").write_text(
        "CIK|Company Name|Form Type|Date Filed|Filename\n"
        + "-" * 80
        + "\n2222|New Shell Acquisition Corp|8-K|2026-08-03|edgar/data/2222/0002222222-26-000002.txt\n",
        encoding="utf-8",
    )
    _cache_subs(
        tmp_path,
        "0000002222",
        _submissions(
            "2222",
            repo.BLANK_CHECKS,
            accessions=["0002222222-26-000002"],
            items=["1.01,9.01"],
            forms=["8-K"],
        ),
    )
    client = _client(tmp_path)

    r1 = run_spac_radar(db, until=D, days=1, edgar_client=client, match=False)
    assert r1.filings_seen == 1 and r1.events_appended == 0  # seen, dropped: un-known shell
    assert _count(db, "TRUE") == 1  # nothing enriched either — no accretion for an 8-K

    sw = run_shell_sweep(db, edgar_client=client)
    assert sw.admitted == ["0000002222"] and sw.errors == []
    assert repo.known_shell_ciks(db) == {"0000002222"}

    r2 = run_spac_radar(db, until=D, days=1, edgar_client=client, match=False)
    assert r2.events_appended == 1 and r2.errors == []  # the previously-dropped 8-K, collected
    with db.cursor() as cur:
        cur.execute("SELECT form, cik, items FROM fact_spac_event")
        rows = cur.fetchall()
    assert len(rows) == 1
    assert (rows[0]["form"], rows[0]["cik"], rows[0]["items"]) == (
        "8-K",
        "0000002222",
        ["1.01", "9.01"],
    )


# --- 4. idempotency: count the table, not the read ---


def test_sweep_rerun_is_idempotent_count_the_table(db, tmp_path):
    _seed_master(db, "0000002222", "NEWS", "New Shell Acquisition Corp")
    _cache_subs(tmp_path, "0000002222", _submissions("2222", repo.BLANK_CHECKS))
    client = _client(tmp_path)

    run_shell_sweep(db, edgar_client=client)
    rows_before = _count(db, "TRUE")
    enriched_before = _count(db, "sector IS NOT NULL")

    r2 = run_shell_sweep(db, edgar_client=client)  # incremental: the enriched CIK dropped out
    assert (r2.candidates, r2.attempted, r2.enriched) == (0, 0, 0)
    assert _count(db, "TRUE") == rows_before  # UPDATE-in-place — the table never grows
    assert _count(db, "sector IS NOT NULL") == enriched_before


# --- 5. fail-open + recall: a per-CIK fault is recorded loud and the CIK stays selectable ---


def test_per_cik_fault_is_recorded_and_the_cik_stays_selectable(db, tmp_path):
    good = _seed_master(db, "0000002222", "NEWS", "New Shell Acquisition Corp")
    _seed_master(db, "0000007777", "BADQ", "Broken Acquisition Corp")  # no cached doc -> CacheMiss
    _cache_subs(tmp_path, "0000002222", _submissions("2222", repo.BLANK_CHECKS))

    r = run_shell_sweep(db, edgar_client=_client(tmp_path))  # must not raise

    assert r.enriched == 1 and master.get(db, good).sector == repo.BLANK_CHECKS
    assert r.skipped == 1 and len(r.errors) == 1 and "0000007777" in r.errors[0]
    # recall (#9): the faulted CIK is NOT dropped — still selectable, retried next run
    assert select_unenriched_candidates(db) == ["0000007777"]


# --- 6. the CLI's scriptable health gate (the enrich_identity exit-code precedent) ---


class _DeadEdgar:
    """Constructor-compatible fake: every fetch misses (the network/UA-fault shape)."""

    live_fetches = 0

    def __init__(self, **kw) -> None:
        pass

    def get_json(self, url, cache_key):
        raise CacheMiss(cache_key)


def test_cli_live_run_that_enriches_nothing_is_loud(db, capsys, monkeypatch):
    """--live + candidates existed + NOTHING enriched -> exit 1 (a fault wearing a clean exit).
    The SAME outcome without --live exits 0 — offline cache-miss skips are the expected shape."""
    _seed_master(db, "0000002222", "NEWS", "New Shell Acquisition Corp")
    monkeypatch.setattr("radar.shell_sweep.EdgarClient", _DeadEdgar)

    assert spac_sweep.main([]) == 0  # cache-first: skipped loud, but not a health fault
    assert "1 ERRORS" in capsys.readouterr().out

    assert spac_sweep.main(["--live"]) == 1
    assert "enriched nothing while candidates existed" in capsys.readouterr().out


# --- 7. the de-SPAC re-enrich: a flipped SIC is overwritten in place and stops flagging ---


def test_reenrich_flips_a_despacd_shell_and_it_stops_flagging(db, tmp_path):
    """A completed merger changes the SEC SIC; the incremental sweep skips the (non-NULL) row, so
    only ``reenrich=True`` catches it: ``master.enrich`` OVERWRITES the sector in place (no new
    version — the master is not an as-of surface) and the CIK drops out of ``known_shell_ciks``.
    A still-6770 shell in the same pool is quietly reconfirmed."""
    done = _seed_master(db, "0000008888", "DONE", "Done Acquisition Corp", sector=repo.BLANK_CHECKS)
    _seed_master(db, "0000009999", "STIL", "Still Shell Acquisition Corp", sector=repo.BLANK_CHECKS)
    _cache_subs(tmp_path, "0000008888", _submissions("8888", PHARMA))  # de-SPAC'd at the SEC
    _cache_subs(tmp_path, "0000009999", _submissions("9999", repo.BLANK_CHECKS))
    client = _client(tmp_path)
    rows_before = _count(db, "TRUE")

    r0 = run_shell_sweep(
        db, edgar_client=client
    )  # incremental: both rows non-NULL -> no candidates
    assert (r0.candidates, r0.enriched) == (0, 0)
    assert set(select_known_shells_lru(db)) == {"0000008888", "0000009999"}

    r = run_shell_sweep(db, edgar_client=client, reenrich=True)
    assert (r.candidates, r.enriched) == (2, 2)
    assert r.flipped == ["0000008888"] and r.reconfirmed == 1
    assert r.admitted == [] and r.other == 0  # a flip is never a false 'admit'
    assert master.get(db, done).sector == PHARMA  # the SEC value, overwritten in place
    assert repo.known_shell_ciks(db) == {"0000009999"}  # the de-SPAC no longer flags
    assert _count(db, "TRUE") == rows_before  # overwrite, never append


# --- 8. the structural guard: the sweep path can never touch the call machinery or the LLM ---

# The pattern is a FETCH SELECTOR, never a classification (#3): nothing in the sweep path may
# import the call machinery (a sweep outcome must be structurally unable to fire/arm/veto) or the
# LLM seam (facts-only — no model anywhere near shell-or-not).
_FORBIDDEN_IMPORTS = ("calls", "signals", "llm", "domain.signal", "domain.config")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_shell_sweep_cannot_touch_the_call_path_or_the_llm():
    import pipeline.spac_sweep as spac_sweep_mod
    import radar.shell_sweep as shell_sweep_mod

    for src in (Path(shell_sweep_mod.__file__), Path(spac_sweep_mod.__file__)):
        for mod in _imported_modules(src):
            for forbidden in _FORBIDDEN_IMPORTS:
                assert mod != forbidden and not mod.startswith(
                    forbidden + "."
                ), f"{src.name} imports {mod!r} — the sweep is facts-only, off the call path"


# --- bonus: the cost thread — the cap defers a visible remainder that self-continues ---


def test_cap_defers_a_visible_remainder_and_the_next_run_continues(db, tmp_path):
    for cik, ticker in (("0000001111", "AAA"), ("0000002222", "BBB"), ("0000003333", "CCC")):
        _seed_master(db, cik, ticker, f"{ticker} Acquisition Corp")
        _cache_subs(tmp_path, cik, _submissions(cik.lstrip("0"), repo.BLANK_CHECKS))
    client = _client(tmp_path)

    r1 = run_shell_sweep(db, edgar_client=client, cap=2)
    assert (r1.candidates, r1.attempted, r1.enriched, r1.remaining) == (3, 2, 2, 1)
    assert "remaining=1" in r1.summary  # the capped_terms-style visibility line

    r2 = run_shell_sweep(db, edgar_client=client, cap=2)  # self-continues: the tail, uncapped now
    assert (r2.candidates, r2.attempted, r2.enriched, r2.remaining) == (1, 1, 1, 0)
    assert repo.known_shell_ciks(db) == {"0000001111", "0000002222", "0000003333"}
