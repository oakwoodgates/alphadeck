"""The identity-enrich CLI (the identity lifecycle) — scope resolution, tenant threading, the receipt,
and the --live exit gate. Enrichment SEMANTICS (the genuine-doc guard, per-CIK isolation, idempotent
UPDATE-in-place) stay covered where they live — ``tests/workbench/test_enrichment.py``; this file tests
the scope-resolver + receipt wrapper around ``enrich_for_ciks``, which it calls unchanged.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from db.session import DEFAULT_TENANT_ID
from ingest import CacheMiss
from pipeline import enrich_identity
from pipeline.provision_tenant import provision_tenant
from securities import master

OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ad")


def _key(cik: str) -> str:
    """The submissions cache_key ``fetch_submissions`` builds for a CIK (the test_enrichment pattern)."""
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
    """Canned submissions JSON by cache_key; an unknown key raises ``CacheMiss`` (like the real
    EdgarClient when a doc isn't cached and live is disabled)."""

    def __init__(self, docs: dict) -> None:
        self.docs = docs

    def get_json(self, url, cache_key):
        if cache_key not in self.docs:
            raise CacheMiss(cache_key)
        return self.docs[cache_key]


def _sec(db, ticker, *, cik=None, tenant_id=DEFAULT_TENANT_ID, is_primary=None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, is_primary, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, tenant_id, ticker, cik, is_primary, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _thesis(db, name, members, *, tenant_id=DEFAULT_TENANT_ID, archived=False) -> uuid.UUID:
    """A thesis with ``members`` = [(ticker, security_id-or-None)]; archetype stays NULL (item F)."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, tenant_id, name, "n", datetime.now(timezone.utc) if archived else None),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, 'r', %s)",
                (uuid.uuid4(), tenant_id, tid, i, ticker, sid),
            )
    db.commit()
    return tid


# --- scope resolution ---


def test_thesis_scope_resolves_cik_map_and_counts_unenrichable(db):
    """--thesis: resolved members key ``{cik: sid}``; an UNRESOLVED placement (security_id NULL) has no
    exact member and contributes nothing; a resolved member whose master row has NO CIK (an OpenFIGI-era
    row / a fund sleeve) is counted ``unenrichable`` — there is no submissions doc to parse, and nothing
    is guessed."""
    oklo = _sec(db, "OKLO", cik="0001849056")
    lit = _sec(db, "LIT", cik=None)  # CIK-less (the fund-sleeve shape)
    tid = _thesis(db, "Nuclear", [("OKLO", oklo), ("KAIROS", None), ("LIT", lit)])

    scopes = enrich_identity.resolve_baskets_scope(db, thesis_id=tid)
    assert set(scopes.keys()) == {DEFAULT_TENANT_ID}
    scope = scopes[DEFAULT_TENANT_ID]
    assert scope.cik_to_sid == {"0001849056": oklo}
    assert scope.unenrichable == 1  # LIT only — the unresolved KAIROS is not a member to enrich


def test_thesis_scope_unknown_thesis_raises(db):
    with pytest.raises(LookupError):
        enrich_identity.resolve_baskets_scope(db, thesis_id=uuid.uuid4())


def test_baskets_scope_covers_all_theses_dedups_and_skips_archived(db):
    """--baskets (the bare default): every thesis's resolved members, deduped by CIK across theses;
    ARCHIVED theses are excluded (the ``list_all`` default — the daily cron's walk), so an archived test
    basket stops driving enrichment fetches."""
    oklo = _sec(db, "OKLO", cik="0001849056")
    leu = _sec(db, "LEU", cik="0001065059")
    dead = _sec(db, "DEAD", cik="0000000001")
    _thesis(db, "Nuclear", [("OKLO", oklo), ("LEU", leu)])
    _thesis(db, "Fuel", [("LEU", leu)])  # the shared member dedups into one CIK entry
    _thesis(db, "Old", [("DEAD", dead)], archived=True)  # archived -> not walked

    scopes = enrich_identity.resolve_baskets_scope(db)
    assert scopes[DEFAULT_TENANT_ID].cik_to_sid == {"0001849056": oklo, "0001065059": leu}


def test_baskets_scope_is_tenant_intrinsic_and_fail_closed_across_tenants(db):
    """Tenant threading (#5): each thesis contributes to ITS OWN tenant's map (theses are
    tenant-intrinsic — the daily cron's pattern), and a member pointing at ANOTHER tenant's master row
    resolves to nothing under its own tenant (``get_many`` is tenant-scoped) -> counted unenrichable,
    NEVER written under either tenant."""
    provision_tenant(db, "other", tenant_id=OTHER_TENANT_ID)
    db.commit()
    mine = _sec(db, "OKLO", cik="0001849056")
    theirs = _sec(db, "FRGN", cik="0009999999", tenant_id=OTHER_TENANT_ID)
    _thesis(db, "Mine", [("OKLO", mine)])
    _thesis(db, "Theirs", [("FRGN", theirs)], tenant_id=OTHER_TENANT_ID)
    # the poison member: a demo-tenant thesis holding the OTHER tenant's id (shouldn't happen post-guard)
    _thesis(db, "Poison", [("FRGN", theirs)])

    scopes = enrich_identity.resolve_baskets_scope(db)
    assert scopes[DEFAULT_TENANT_ID].cik_to_sid == {"0001849056": mine}
    assert scopes[DEFAULT_TENANT_ID].unenrichable == 1  # the poison member — visible, never guessed
    assert scopes[OTHER_TENANT_ID].cik_to_sid == {"0009999999": theirs}

    # run the enrich: each tenant's rows written under ITS tenant only
    edgar = _FakeEdgar(
        {
            _key("0001849056"): _subs("1849056", sic="Electric Services"),
            _key("0009999999"): _subs("9999999", sic="Metal Mining"),
        }
    )
    receipts = {r.tenant_id: r for r in enrich_identity.run_enrich(db, edgar, scopes)}
    assert (receipts[DEFAULT_TENANT_ID].enriched, receipts[DEFAULT_TENANT_ID].skipped) == (1, 0)
    assert (receipts[OTHER_TENANT_ID].enriched, receipts[OTHER_TENANT_ID].skipped) == (1, 0)
    assert master.get(db, mine).sector == "Electric Services"
    assert master.get(db, theirs, tenant_id=OTHER_TENANT_ID).sector == "Metal Mining"


def test_universe_scope_targets_the_canonical_primary_row_per_cik(db):
    """--universe: every CIK in the tenant's master, resolved to its CANONICAL (is_primary) sibling —
    identity is company-level, so one submissions doc enriches ONE row per CIK: the instrument the
    operator trades (ASML, never the ASMLF foreign ordinary). CIK-less rows aren't in the scope."""
    asml = _sec(db, "ASML", cik="0000937966", is_primary=True)
    asmlf = _sec(db, "ASMLF", cik="0000937966")
    _sec(db, "LIT", cik=None)
    scopes = enrich_identity.resolve_universe_scope(db, tenant_id=DEFAULT_TENANT_ID)
    assert scopes == {
        DEFAULT_TENANT_ID: enrich_identity.TenantScope(cik_to_sid={"0000937966": asml})
    }

    edgar = _FakeEdgar({_key("0000937966"): _subs("937966", sic="Semiconductors")})
    (receipt,) = enrich_identity.run_enrich(db, edgar, scopes)
    assert (receipt.ciks, receipt.enriched, receipt.skipped) == (1, 1, 0)
    assert master.get(db, asml).sector == "Semiconductors"
    assert master.get(db, asmlf).sector is None  # the non-primary sibling is untouched


# --- main(): the CLI wiring — bare default, the receipt, and the --live exit gate ---


def test_main_bare_invocation_is_baskets_and_prints_the_receipt(db, capsys, monkeypatch):
    """Bare invocation = --baskets (the operator's one-off, productized): the resolved members enrich and
    the per-tenant receipt + totals print — the command's whole point is that 'did it run, what did it
    do' is answerable from this output."""
    oklo = _sec(db, "OKLO", cik="0001849056")
    _thesis(db, "Nuclear", [("OKLO", oklo)])
    monkeypatch.setattr(
        enrich_identity,
        "EdgarClient",
        lambda **kw: _FakeEdgar({_key("0001849056"): _subs("1849056")}),
    )
    enrich_identity.main([])  # no SystemExit -> exit 0
    out = capsys.readouterr().out
    assert f"tenant {DEFAULT_TENANT_ID}: 1 CIK(s) -> 1 enriched, 0 skipped" in out
    assert "TOTAL: 1 enriched, 0 skipped" in out
    assert master.get(db, oklo).sector == "Electric Services"  # the write actually landed


def test_main_live_gate_exits_nonzero_when_nothing_enriched_and_names_skipped(
    db, capsys, monkeypatch
):
    """The scriptable health gate (D7, the audit_identity precedent): a --live run that enriched NOTHING
    while skipping names exits 1 (that shape is a network/UA fault wearing a clean exit). The SAME
    outcome without --live exits 0 — offline skips (uncached CIKs) are the expected cache-first shape.
    """
    oklo = _sec(db, "OKLO", cik="0001849056")
    _thesis(db, "Nuclear", [("OKLO", oklo)])
    monkeypatch.setattr(enrich_identity, "EdgarClient", lambda **kw: _FakeEdgar({}))  # no docs

    enrich_identity.main([])  # cache-first: 0 enriched / 1 skipped -> still exit 0
    assert "TOTAL: 0 enriched, 1 skipped" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc:
        enrich_identity.main(["--live"])
    assert exc.value.code == 1
    assert "enriched nothing" in capsys.readouterr().out


def test_main_live_all_enriched_exits_zero(db, monkeypatch):
    """The green path: a plain --live run that enriches everything exits 0 (the gate is for the
    nothing-enriched-something-skipped fault shape only)."""
    oklo = _sec(db, "OKLO", cik="0001849056")
    _thesis(db, "Nuclear", [("OKLO", oklo)])
    monkeypatch.setattr(
        enrich_identity,
        "EdgarClient",
        lambda **kw: _FakeEdgar({_key("0001849056"): _subs("1849056")}),
    )
    enrich_identity.main(["--live"])  # no SystemExit
    assert master.get(db, oklo).sector == "Electric Services"


def test_main_rejects_tenant_id_outside_universe(db):
    """--tenant-id belongs to --universe only (basket scopes take each thesis's own tenant) — passing it
    elsewhere errors LOUDLY (argparse exit 2), never a silently-ignored flag."""
    with pytest.raises(SystemExit) as exc:
        enrich_identity.main(["--baskets", "--tenant-id", str(DEFAULT_TENANT_ID)])
    assert exc.value.code == 2


def test_main_universe_respects_tenant_id(db, capsys, monkeypatch):
    """--universe --tenant-id enriches ONLY the named tenant's rows (the populate_master pattern)."""
    provision_tenant(db, "other", tenant_id=OTHER_TENANT_ID)
    db.commit()
    _sec(db, "OKLO", cik="0001849056")  # demo tenant — must NOT be touched
    theirs = _sec(db, "FRGN", cik="0009999999", tenant_id=OTHER_TENANT_ID)
    monkeypatch.setattr(
        enrich_identity,
        "EdgarClient",
        lambda **kw: _FakeEdgar({_key("0009999999"): _subs("9999999", sic="Metal Mining")}),
    )
    enrich_identity.main(["--universe", "--tenant-id", str(OTHER_TENANT_ID)])
    out = capsys.readouterr().out
    assert f"tenant {OTHER_TENANT_ID}: 1 CIK(s) -> 1 enriched, 0 skipped" in out
    assert master.get(db, theirs, tenant_id=OTHER_TENANT_ID).sector == "Metal Mining"
