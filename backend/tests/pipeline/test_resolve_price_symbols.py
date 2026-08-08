"""The OTC price-symbol resolve CLI (Slice D) — the standing sweep for existing basket members. Covers the
tier routing (AUTO adopts / FLAG surfaced / NONE kept), the thin-history candidate pre-gate (+ --all-otc),
only-fills-empty (+ --overwrite), the operator --adopt confirm path, --dry-run (zero writes), the per-thesis
fail-soft refusal, and the --live adopted-nothing exit gate. The resolver + bar-count are monkeypatched (both
have their own tests) so the CLI's ORCHESTRATION is what's exercised here."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.thesis import BasketMember, Thesis
from pipeline import resolve_price_symbols
from repositories import thesis_repo
from securities import master
from securities.price_symbol import PriceSymbolProposal


def _insert(db, ticker, *, name, cik, exchange="OTC", price_symbol=None):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, exchange, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, exchange, date(2026, 1, 1)),
        )
        if price_symbol is not None:
            cur.execute(
                "UPDATE security_master SET price_symbol = %s WHERE id = %s", (price_symbol, sid)
            )
    db.commit()
    return sid


def _mk_thesis(db, name, members, *, tenant_id=DEFAULT_TENANT_ID) -> uuid.UUID:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        narrative="n",
        basket=[BasketMember(ticker=tk, role="r", security_id=sid) for tk, sid in members],
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t.id


class _FakeResolver:
    """Canned proposals by ticker; records which tickers the resolver was CALLED on (so a healthy-skip /
    non-OTC / adopt can assert the resolver never ran)."""

    def __init__(self, by_ticker: dict[str, PriceSymbolProposal]):
        self.by_ticker = by_ticker
        self.called: list[str] = []

    def __call__(self, sec, **kw):
        self.called.append(sec.ticker)
        return self.by_ticker.get(
            sec.ticker, PriceSymbolProposal(tier="NONE", proposed_symbol=None, why="uncovered")
        )


def _patch(monkeypatch, *, resolver, counts: dict):
    monkeypatch.setattr(resolve_price_symbols, "resolve_price_symbol", resolver)
    monkeypatch.setattr(
        resolve_price_symbols, "recent_distinct_bar_counts", lambda conn, sids, **kw: counts
    )


_AUTO = PriceSymbolProposal(tier="AUTO", proposed_symbol="FDCTD", why="251 vs 16")
_FLAG = PriceSymbolProposal(
    tier="FLAG", proposed_symbol="CURLF", why="unverified", candidates=("CURLF",)
)
_NONE = PriceSymbolProposal(tier="NONE", proposed_symbol=None, why="uncovered")


def test_auto_adopts_flag_surfaces_none_kept(db, monkeypatch):
    """The tier routing: AUTO writes the symbol; FLAG is surfaced (no write); NONE is kept under the
    canonical ticker (#9 — never dropped). All three are thin candidates."""
    fdct = _insert(db, "FDCT", name="First Digital Corp", cik="0001111111")
    curld = _insert(db, "CURLD", name="Curaleaf Holdings, Inc.", cik="0002222222")
    wondf = _insert(db, "WONDF", name="Wonder Co", cik="0003333333")
    tid = _mk_thesis(db, "OTC basket", [("FDCT", fdct), ("CURLD", curld), ("WONDF", wondf)])

    resolver = _FakeResolver({"FDCT": _AUTO, "CURLD": _FLAG, "WONDF": _NONE})
    _patch(monkeypatch, resolver=resolver, counts={fdct: 16, curld: 12, wondf: 0})  # all thin

    (r,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True)

    assert (r.adopted, r.flagged, r.none_kept, r.candidates) == (1, 1, 1, 3)
    assert master.get(db, fdct).price_symbol == "FDCTD"  # AUTO written
    assert master.get(db, curld).price_symbol is None  # FLAG — surfaced, not written
    assert master.get(db, wondf).price_symbol is None  # NONE — kept, never dropped


def test_healthy_otc_skipped_without_resolver_call(db, monkeypatch):
    """The cost thread: an OTC name with ENOUGH history is skipped WITHOUT a resolver call (cost not spent);
    --all-otc widens the gate so it becomes a candidate."""
    fdct = _insert(db, "FDCT", name="First Digital Corp", cik="0001111111")
    tid = _mk_thesis(db, "OTC basket", [("FDCT", fdct)])

    resolver = _FakeResolver({"FDCT": _AUTO})
    _patch(monkeypatch, resolver=resolver, counts={fdct: 251})  # healthy (>= threshold)

    (r,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True)
    assert r.healthy_skipped == 1 and r.candidates == 0
    assert resolver.called == []  # NO resolver call — cost not spent
    assert master.get(db, fdct).price_symbol is None

    # --all-otc widens the gate: now a candidate, resolved and adopted
    (r2,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True, all_otc=True)
    assert r2.adopted == 1 and resolver.called == ["FDCT"]
    assert master.get(db, fdct).price_symbol == "FDCTD"


def test_dry_run_writes_nothing(db, monkeypatch):
    fdct = _insert(db, "FDCT", name="First Digital Corp", cik="0001111111")
    tid = _mk_thesis(db, "OTC basket", [("FDCT", fdct)])
    _patch(monkeypatch, resolver=_FakeResolver({"FDCT": _AUTO}), counts={fdct: 16})

    (r,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True, dry_run=True)
    assert r.adopted == 1  # would-adopt, classified
    assert master.get(db, fdct).price_symbol is None  # but ZERO writes


def test_only_fills_empty_then_overwrite(db, monkeypatch):
    """Default keeps a stored resolution (only-fills-empty); --overwrite re-resolves it (correction path)."""
    fdct = _insert(db, "FDCT", name="First Digital Corp", cik="0001111111", price_symbol="OLDSYM")
    tid = _mk_thesis(db, "OTC basket", [("FDCT", fdct)])
    resolver = _FakeResolver({"FDCT": _AUTO})
    _patch(monkeypatch, resolver=resolver, counts={fdct: 16})

    (r,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True)
    assert r.already_resolved == 1 and resolver.called == []  # kept, no resolver call
    assert master.get(db, fdct).price_symbol == "OLDSYM"

    (r2,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True, overwrite=True)
    assert r2.adopted == 1
    assert master.get(db, fdct).price_symbol == "FDCTD"  # the correction landed


def test_operator_adopt_bypasses_gates(db, monkeypatch):
    """--adopt CURLD=CURLF confirms a FLAG directly — bypasses the thin/OTC/fill-empty gates and the AUTO
    requirement, writing the operator's chosen symbol without a resolver call for that ticker."""
    curld = _insert(
        db, "CURLD", name="Curaleaf Holdings, Inc.", cik="0002222222", price_symbol=None
    )
    tid = _mk_thesis(db, "OTC basket", [("CURLD", curld)])
    resolver = _FakeResolver({"CURLD": _FLAG})
    _patch(
        monkeypatch, resolver=resolver, counts={curld: 300}
    )  # even healthy — the operator named it

    (r,) = resolve_price_symbols.run_resolve(
        db, thesis_id=tid, allow_live=True, adopt_map={"CURLD": "CURLF"}
    )
    assert (
        r.operator_adopted == 1 and resolver.called == []
    )  # no resolver call — an explicit override
    assert master.get(db, curld).price_symbol == "CURLF"


def test_non_otc_never_resolves(db, monkeypatch):
    nas = _insert(db, "HIMS", name="Hims & Hers", cik="0001773751", exchange="Nasdaq")
    tid = _mk_thesis(db, "Mixed basket", [("HIMS", nas)])
    resolver = _FakeResolver({"HIMS": _AUTO})
    _patch(monkeypatch, resolver=resolver, counts={nas: 5})

    (r,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True)
    assert r.not_otc == 1 and resolver.called == []


def test_per_member_resolver_error_is_soft_not_fatal(db, monkeypatch):
    """A per-member resolver error (offline / network) is counted and the thesis CONTINUES — recall-friendly,
    never a thesis refusal for one flaky fetch."""
    a = _insert(db, "FDCT", name="First Digital Corp", cik="0001111111")
    b = _insert(db, "CURLD", name="Curaleaf Holdings, Inc.", cik="0002222222")
    tid = _mk_thesis(db, "OTC basket", [("FDCT", a), ("CURLD", b)])

    def _resolver(sec, **kw):
        if sec.ticker == "FDCT":
            raise RuntimeError("yahoo down")
        return PriceSymbolProposal(tier="AUTO", proposed_symbol="CURLF", why="ok")

    monkeypatch.setattr(resolve_price_symbols, "resolve_price_symbol", _resolver)
    monkeypatch.setattr(
        resolve_price_symbols, "recent_distinct_bar_counts", lambda *args, **k: {a: 5, b: 5}
    )
    (r,) = resolve_price_symbols.run_resolve(db, thesis_id=tid, allow_live=True)
    assert r.errored == 1 and r.adopted == 1 and not r.refused  # one errored, the other adopted
    assert master.get(db, b).price_symbol == "CURLF"


def test_refused_thesis_continues_and_main_exits_1(db, monkeypatch, capsys):
    """A thesis-level fault refuses THAT thesis (no write) and the sweep continues; main exits 1."""
    good = _insert(db, "FDCT", name="First Digital Corp", cik="0001111111")
    bad = _insert(db, "CURLD", name="Curaleaf Holdings, Inc.", cik="0002222222")
    _mk_thesis(db, "Good", [("FDCT", good)])
    _mk_thesis(db, "Bad", [("CURLD", bad)])

    real = resolve_price_symbols.resolve_thesis

    def _sometimes(conn, thesis, **kw):
        if thesis.name == "Bad":
            raise RuntimeError("thesis fault")
        return real(conn, thesis, **kw)

    monkeypatch.setattr(resolve_price_symbols, "resolve_thesis", _sometimes)
    monkeypatch.setattr(
        resolve_price_symbols,
        "resolve_price_symbol",
        lambda sec, **kw: _AUTO,
    )
    monkeypatch.setattr(
        resolve_price_symbols, "recent_distinct_bar_counts", lambda *a, **k: {good: 5}
    )

    with pytest.raises(SystemExit) as exc:
        resolve_price_symbols.main(["--live"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out and "1 refused" in out
    assert master.get(db, good).price_symbol == "FDCTD"  # the good thesis still resolved


def test_live_adopted_nothing_with_candidates_exits_1(db, monkeypatch, capsys):
    """The scriptable-health gate: a --live run that adopted NOTHING while candidates existed exits 1
    (network/UA fault shape). The SAME scope without --live exits 0 (the offline / all-NONE shape).
    """
    wondf = _insert(db, "WONDF", name="Wonder Co", cik="0003333333")
    tid = _mk_thesis(db, "Uncovered basket", [("WONDF", wondf)])
    monkeypatch.setattr(resolve_price_symbols, "resolve_price_symbol", lambda sec, **kw: _NONE)
    monkeypatch.setattr(
        resolve_price_symbols, "recent_distinct_bar_counts", lambda *a, **k: {wondf: 0}
    )

    resolve_price_symbols.main(["--thesis", str(tid)])  # no --live -> exit 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        resolve_price_symbols.main(["--thesis", str(tid), "--live"])
    assert exc.value.code == 1
    assert "adopted nothing while candidates existed" in capsys.readouterr().out


def test_bad_adopt_format_errors(db):
    with pytest.raises(SystemExit):
        resolve_price_symbols.main(["--adopt", "FDCTonly"])


def test_main_unknown_thesis_raises(db):
    with pytest.raises(LookupError):
        resolve_price_symbols.main(["--thesis", str(uuid.uuid4())])
