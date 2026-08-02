"""The surfaced-terms backfill CLI (Re-scope S1) — freeze each existing member's discovery-term
provenance from the SAME deterministic EFTS discovery a draft runs. Covers the value capture (identical
to draft-time display: ``sorted(filers[cik].keywords)``), the honest empties (no-CIK / unmatched), the
only-fills-empty freeze protection (Q2) + ``--overwrite``, idempotency (COUNT THE TABLE, not the read),
the degraded-coverage refusal (+ ``--force``), the empty-term-set skip, and the ``--live`` exit gate.
Fake EFTS per ``tests/workbench/test_discovery.py``; fake EdgarClient injection per
``tests/pipeline/test_enrich_identity.py``."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import TermTier
from domain.thesis import BasketMember, TermSetEntry, Thesis
from pipeline import backfill_surfaced_terms
from repositories import thesis_repo


def _terms(signal: list[str], broad: list[str]) -> list[TermSetEntry]:
    return [TermSetEntry(term=t, tier=TermTier.SIGNAL) for t in signal] + [
        TermSetEntry(term=t, tier=TermTier.BROAD) for t in broad
    ]


class _FakeEfts:
    """Canned EFTS pages by cache_key; an unknown key -> an empty page (the test_discovery fake)."""

    def __init__(self, pages: dict[str, dict]):
        self.pages = pages
        self.calls: list[str] = []

    def get_json(self, url, cache_key):
        self.calls.append(cache_key)
        return self.pages.get(cache_key, {"hits": {"total": {"value": 0}, "hits": []}})


class _FailingEfts(_FakeEfts):
    """Like _FakeEfts, but any page whose cache_key mentions a ``fail_term`` raises persistently — the
    within-tolerance partial-enumeration shape (``coverage.failed_terms``), not a full outage."""

    def __init__(self, pages: dict[str, dict], fail_term: str):
        super().__init__(pages)
        self.fail_term = fail_term

    def get_json(self, url, cache_key):
        if self.fail_term in cache_key:
            raise RuntimeError("EFTS down for this term")
        return super().get_json(url, cache_key)


def _page(total: int, *rows: tuple[str, str]) -> dict:
    return {
        "hits": {
            "total": {"value": total},
            "hits": [{"_source": {"ciks": [cik], "display_names": [dn]}} for cik, dn in rows],
        }
    }


def _insert_sec(db, ticker, *, name, cik, tenant_id=DEFAULT_TENANT_ID) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, tenant_id, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _mk_thesis(db, name, members, terms=None, *, tenant_id=DEFAULT_TENANT_ID) -> uuid.UUID:
    """A thesis with ``members`` = [(ticker, security_id-or-None, stored surfaced_terms)] and an optional
    persisted term set — built through the domain writers (upsert + set_term_set), like the app would.
    """
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        narrative="n",
        basket=[
            BasketMember(ticker=tk, role="r", security_id=sid, surfaced_terms=st)
            for tk, sid, st in members
        ],
    )
    thesis_repo.upsert(db, t)
    if terms is not None:
        thesis_repo.set_term_set(db, t.id, terms)
    db.commit()
    return t.id


def _stored(db, tid) -> dict[str, list[str]]:
    got = thesis_repo.get(db, tid)
    return {m.ticker: m.surfaced_terms for m in got.basket}


def _table_rows(db, tid) -> list[tuple]:
    """The RAW table state (the idempotency instrument): every row's identity + frozen value, in ordinal
    order — a re-run must leave this list byte-identical, not just the domain read."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT ticker, security_id, surfaced_terms FROM basket_member "
            "WHERE thesis_id = %s ORDER BY ordinal",
            (tid,),
        )
        return [(r["ticker"], r["security_id"], r["surfaced_terms"]) for r in cur.fetchall()]


# CIKs in the EDGAR zero-padded form (what the master stores + EFTS returns) — the test_discovery cast.
_A = "0001816590"  # Compass — psilocybin + ibogaine
_C = "0001514183"  # Silo — psilocybin
_B = "0000000002"  # Alkermes — ketamine (broad -> verify tier; still a filer)

_PAGES = {
    "efts/psilocybin_0.json": _page(
        2,
        (_A, "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)"),
        (_C, "Silo Pharma, Inc.  (SILO)  (CIK 0001514183)"),
    ),
    "efts/ibogaine_0.json": _page(1, (_A, "COMPASS Pathways plc  (CMPS)  (CIK 0001816590)")),
    "efts/ketamine_0.json": _page(1, (_B, "Alkermes plc  (ALKS)  (CIK 0000000002)")),
}
_TERMS = _terms(["psilocybin", "ibogaine"], ["ketamine"])


def test_backfill_freezes_values_matching_the_fake_universe(db):
    """The captured value is IDENTICAL to draft-time display — ``sorted(filers[cik].keywords)`` from the
    raw enumerated universe: the 2-keyword name gets both (sorted), the 1-keyword names get theirs, and a
    verify-tier (broad-only) member still freezes — it is ALREADY a ratified member; provenance records
    what surfaced it, not its confidence tier."""
    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    c = _insert_sec(db, "SILO", name="Silo Pharma, Inc.", cik=_C)
    b = _insert_sec(db, "ALKS", name="Alkermes plc", cik=_B)
    tid = _mk_thesis(
        db, "Psychedelics", [("CMPS", a, []), ("SILO", c, []), ("ALKS", b, [])], _TERMS
    )

    (r,) = backfill_surfaced_terms.run_backfill(db, _FakeEfts(_PAGES), thesis_id=tid)
    assert (r.frozen, r.kept, r.unmatched, r.no_cik) == (3, 0, 0, 0)
    assert r.skipped is None and not r.refused
    assert _stored(db, tid) == {
        "CMPS": ["ibogaine", "psilocybin"],  # sorted — the draft-time display order
        "SILO": ["psilocybin"],
        "ALKS": ["ketamine"],
    }


def test_backfill_no_cik_and_unmatched_stay_empty(db):
    """The honest empties, counted: an UNRESOLVED placement and a CIK-less master row (the ETF-sleeve
    shape) have nothing to match -> no_cik, {} stays; a resolvable member whose CIK did not surface this
    run -> unmatched, {} stays. Nothing is guessed, nothing invented."""
    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    fund = _insert_sec(db, "PSIL", name="AdvisorShares Psychedelics ETF", cik=None)
    dark = _insert_sec(db, "DEVCO", name="Devco Inc.", cik="0001234567")  # not in any page
    tid = _mk_thesis(
        db,
        "Psychedelics",
        [("CMPS", a, []), ("PSIL", fund, []), ("KAIROS", None, []), ("DEVCO", dark, [])],
        _TERMS,
    )

    (r,) = backfill_surfaced_terms.run_backfill(db, _FakeEfts(_PAGES), thesis_id=tid)
    assert (r.frozen, r.kept, r.unmatched, r.no_cik) == (1, 0, 1, 2)
    assert _stored(db, tid) == {
        "CMPS": ["ibogaine", "psilocybin"],
        "PSIL": [],  # CIK-less fund sleeve — surfaced by no term, honestly
        "KAIROS": [],  # unresolved placement — no row to key the write by
        "DEVCO": [],  # resolvable but did not surface under the current terms
    }


def test_backfill_rerun_is_idempotent_count_the_table(db):
    """THE IDEMPOTENCY GATE (count the table, not the read): a re-run leaves the RAW basket_member rows
    byte-identical — same count, same values — whether it re-freezes (nothing: frozen members are no
    longer eligible) or re-computes an unmatched member to the same {}."""
    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    dark = _insert_sec(db, "DEVCO", name="Devco Inc.", cik="0001234567")
    tid = _mk_thesis(db, "Psychedelics", [("CMPS", a, []), ("DEVCO", dark, [])], _TERMS)

    backfill_surfaced_terms.run_backfill(db, _FakeEfts(_PAGES), thesis_id=tid)
    before = _table_rows(db, tid)
    assert before[0][2] == ["ibogaine", "psilocybin"]  # run 1 froze

    (r2,) = backfill_surfaced_terms.run_backfill(db, _FakeEfts(_PAGES), thesis_id=tid)
    after = _table_rows(db, tid)
    assert after == before  # every value unchanged AND the row list identical
    assert len(after) == len(before) == 2  # count(*) unchanged — the table did not grow
    assert r2.frozen == 0 and r2.kept == 1 and r2.unmatched == 1  # run 2 wrote nothing new


def test_backfill_rerun_with_nothing_eligible_skips_the_efts_run(db):
    """Cost is the operator's to spend, never ambient: once every resolvable member is frozen, a re-run
    (without --overwrite) has nothing a write could touch — it skips VISIBLY without spending a single
    EFTS call."""
    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    tid = _mk_thesis(db, "Psychedelics", [("CMPS", a, [])], _TERMS)
    backfill_surfaced_terms.run_backfill(db, _FakeEfts(_PAGES), thesis_id=tid)

    edgar2 = _FakeEfts(_PAGES)
    (r2,) = backfill_surfaced_terms.run_backfill(db, edgar2, thesis_id=tid)
    assert r2.skipped == "nothing eligible" and r2.kept == 1
    assert edgar2.calls == []  # zero API spend
    assert _stored(db, tid) == {"CMPS": ["ibogaine", "psilocybin"]}  # untouched


def test_backfill_prefrozen_kept_without_overwrite_refrozen_with(db):
    """Freeze protection (Q2): a stored NON-EMPTY value is a frozen original — the default run keeps it
    verbatim (only-fills-empty); ``--overwrite`` is the explicit re-freeze and rewrites it from the
    current run. The empty sibling fills either way."""
    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    c = _insert_sec(db, "SILO", name="Silo Pharma, Inc.", cik=_C)
    tid = _mk_thesis(
        db, "Psychedelics", [("CMPS", a, ["original-frozen-term"]), ("SILO", c, [])], _TERMS
    )

    (r,) = backfill_surfaced_terms.run_backfill(db, _FakeEfts(_PAGES), thesis_id=tid)
    assert (r.frozen, r.kept) == (1, 1)
    assert _stored(db, tid) == {
        "CMPS": ["original-frozen-term"],  # the frozen original wins
        "SILO": ["psilocybin"],  # the empty sibling filled
    }

    (r2,) = backfill_surfaced_terms.run_backfill(
        db, _FakeEfts(_PAGES), thesis_id=tid, overwrite=True
    )
    assert r2.frozen == 2 and r2.kept == 0
    assert _stored(db, tid)["CMPS"] == ["ibogaine", "psilocybin"]  # the explicit re-freeze


def test_backfill_empty_term_set_skipped(db):
    """A thesis with no produced term set is the NOT-READY state (the DiscoveryNoTerms shape): skipped
    visibly, no EFTS call, no write, and a normal (0) exit — a seed thesis without terms is common, not a
    fault."""
    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    tid = _mk_thesis(db, "No terms yet", [("CMPS", a, [])], terms=None)

    edgar = _FakeEfts(_PAGES)
    (r,) = backfill_surfaced_terms.run_backfill(db, edgar, thesis_id=tid)
    assert r.skipped == "no term set" and not r.refused
    assert edgar.calls == []  # discovery never ran
    assert _stored(db, tid) == {"CMPS": []}  # nothing written


def test_backfill_degraded_coverage_refuses_and_exits_1(db, capsys, monkeypatch):
    """THE FREEZE-SPECIFIC REFUSAL: a run whose coverage carries failed terms enumerated LESS than the
    term set asks — freezing under it would under-match originals FOREVER. The thesis is REFUSED (no
    write) and the command exits 1; ``--force`` is the deliberate override and freezes the partial match.
    (Ratio widened via the env dial so the 1-of-3-terms failure stays under DiscoveryDegraded's hard
    raise and rides ``coverage.failed_terms`` — the within-tolerance partial shape.)"""
    from domain.settings import get_settings

    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    tid = _mk_thesis(db, "Psychedelics", [("CMPS", a, [])], _TERMS)
    monkeypatch.setattr(
        backfill_surfaced_terms, "EdgarClient", lambda **kw: _FailingEfts(_PAGES, "ketamine")
    )
    monkeypatch.setenv("ALPHADECK_DISCOVERY_DEGRADED_RATIO", "0.5")
    get_settings.cache_clear()  # re-read the env (the singleton may have been built at the default)
    try:
        with pytest.raises(SystemExit) as exc:
            backfill_surfaced_terms.main(["--thesis", str(tid)])
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "REFUSED" in out and "ketamine" in out
        assert _stored(db, tid) == {"CMPS": []}  # refusal wrote NOTHING

        backfill_surfaced_terms.main(["--thesis", str(tid), "--force"])  # no SystemExit -> exit 0
        assert "1 frozen" in capsys.readouterr().out
        assert _stored(db, tid) == {"CMPS": ["ibogaine", "psilocybin"]}  # the deliberate override
    finally:
        get_settings.cache_clear()  # drop the widened-ratio singleton; monkeypatch restores the env


def test_main_bare_run_prints_the_receipt(db, capsys, monkeypatch):
    """Bare invocation = --baskets: every non-archived thesis, and the per-thesis receipt + TOTAL print —
    'did the freeze run, and what did it write' is answerable from the output alone."""
    a = _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    _mk_thesis(db, "Psychedelics", [("CMPS", a, [])], _TERMS)
    monkeypatch.setattr(backfill_surfaced_terms, "EdgarClient", lambda **kw: _FakeEfts(_PAGES))

    backfill_surfaced_terms.main([])  # no SystemExit -> exit 0
    out = capsys.readouterr().out
    assert "'Psychedelics'" in out and "1 member(s)" in out
    assert "1 frozen, 0 kept, 0 unmatched, 0 no-CIK" in out
    assert "TOTAL: 1 frozen across 1 thesis(es), 0 refused" in out


def test_main_live_gate_froze_nothing_with_eligible_members_exits_1(db, monkeypatch, capsys):
    """The scriptable-health gate (the enrich_identity precedent): a --live run that froze NOTHING while
    eligible members existed is a network/UA/term fault wearing a clean exit -> 1. The SAME outcome
    without --live exits 0 (the expected cache-first / genuinely-unmatched shape)."""
    # the universe surfaces CMPS (placeable, so discovery succeeds) but the BASKET holds only DEVCO,
    # whose CIK never surfaces -> eligible 1, frozen 0
    _insert_sec(db, "CMPS", name="COMPASS Pathways plc", cik=_A)
    dark = _insert_sec(db, "DEVCO", name="Devco Inc.", cik="0001234567")
    tid = _mk_thesis(db, "Dark basket", [("DEVCO", dark, [])], _TERMS)
    monkeypatch.setattr(backfill_surfaced_terms, "EdgarClient", lambda **kw: _FakeEfts(_PAGES))

    backfill_surfaced_terms.main(["--thesis", str(tid)])  # cache-first: still exit 0
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        backfill_surfaced_terms.main(["--thesis", str(tid), "--live"])
    assert exc.value.code == 1
    assert "froze nothing" in capsys.readouterr().out


def test_main_unknown_thesis_raises(db):
    with pytest.raises(LookupError):
        backfill_surfaced_terms.main(["--thesis", str(uuid.uuid4())])
