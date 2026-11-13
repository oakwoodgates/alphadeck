"""No user-authored writes to the shared security master (security hardening PR 1a).

The security master is DATA (``domain.scopes``, docs/TENANT_SCOPES.md): every workspace reads it, so only the
steward may add to it or alter it. Built WITHOUT the suite's signed-in override: a real plain user and a real
super admin, each on their own workspace, signed in through ``POST /auth/login`` (the isolation proof's pattern).

- ``POST /workbench/securities/resolve-etf`` is steward-only: a plain user is 403 and the master is unchanged;
- ``POST /radar/funds/attach`` never creates or re-marks a master row for a plain user: it attaches only a row
  that already exists as a fund (``instrument_kind='etf'``), else 404 with a fixed message and nothing written;
  a steward keeps the lookup-or-create behavior;
- a hostile ticker (a path, an oversized string, a NUL, a space) is 422 on both routes, and
  ``securities.figi.map_ticker`` refuses it before it becomes a filename or an OpenFIGI request;
- the GREP GUARD: under ``app/routers/`` a call to a master WRITER appears only at an allowlisted
  (file, function) site, each steward-gated or inside a steward branch; and ONE HOP further, a master writer under
  ``backend/workbench/`` is reached from a router only behind a steward check (security hardening PR 1b).

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable), except the guard.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from domain.settings import get_settings
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
_BACKEND = Path(__file__).resolve().parents[2]
NOT_IN_MASTER = "This fund is not in the company list yet. A steward can add it."
HOSTILE = ["../x", "A" * 300, "AB\x00C", "AB C"]


def _workspace(db, label: str) -> uuid.UUID:
    wid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace')", (wid, label)
        )
    return wid


def _client(email: str) -> TestClient:
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


def _security(db, ticker: str, *, kind: str = "equity") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, instrument_kind, valid_from) "
            "VALUES (%s, '00000000-0000-0000-0000-000000000001', %s, %s, %s, '2026-01-01')",
            (sid, ticker, f"{ticker} Inc", kind),
        )
    db.commit()
    return sid


def _master_count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        return cur.fetchone()["n"]


def _kind(db, ticker: str) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT instrument_kind FROM security_master WHERE ticker = %s", (ticker,))
        return cur.fetchone()["instrument_kind"]


def _promote(c: TestClient, security_id: uuid.UUID) -> uuid.UUID:
    r = c.post(
        "/workbench/theses",
        json={
            "name": f"Shared-master card {uuid.uuid4().hex[:8]}",
            "narrative": "a synthetic narrative for the shared-master proof",
            "basket": [{"ticker": "DEVCO", "role": "the name", "security_id": str(security_id)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


@pytest.fixture
def no_vendor(monkeypatch):
    """Fail loudly if any path reaches OpenFIGI or SEC: these tests prove nothing is resolved live."""

    def boom(*a, **kw):
        raise AssertionError("a vendor was called")

    monkeypatch.setattr("securities.figi._fetch_live", boom)
    monkeypatch.setattr("securities.sec_tickers.cik_for", boom)


@pytest.fixture
def people(db, security_id, monkeypatch, no_vendor):
    """A plain user P and a steward S, each on their own workspace, each owning one card."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    w_p, w_s = (_workspace(db, f"shared-master {x}") for x in ("P", "S"))
    auth.create_user(db, email="p@sm.test", display_name="P", password=PASSWORD, workspace_id=w_p)
    auth.create_user(
        db,
        email="s@sm.test",
        display_name="S",
        password=PASSWORD,
        role="super_admin",
        workspace_id=w_s,
    )
    db.commit()
    p, s = _client("p@sm.test"), _client("s@sm.test")
    yield {"p": p, "s": s, "p_card": _promote(p, security_id), "s_card": _promote(s, security_id)}
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()


# --- resolve-etf is steward-only -------------------------------------------------------------------------------


def test_a_plain_users_resolve_etf_is_403_and_writes_nothing(people, db):
    _security(db, "SPYX")
    before = _master_count(db)
    for ticker in (
        "SPYX",
        "NEWF",
    ):  # an existing equity (would be re-marked) and an unknown one (created)
        r = people["p"].post("/workbench/securities/resolve-etf", json={"ticker": ticker})
        assert r.status_code == 403, (ticker, r.text)
    assert _master_count(db) == before
    assert _kind(db, "SPYX") == "equity"


def test_a_stewards_resolve_etf_still_marks_the_row(people, db):
    sid = _security(db, "SPYX")
    before = _master_count(db)
    r = people["s"].post("/workbench/securities/resolve-etf", json={"ticker": " spyx "})
    assert r.status_code == 200, r.text
    assert r.json()["security_id"] == str(sid) and r.json()["instrument_kind"] == "etf"
    assert _kind(db, "SPYX") == "etf" and _master_count(db) == before


# --- funds/attach writes no master row for a plain user --------------------------------------------------------


def _attach(c: TestClient, card: uuid.UUID, symbol: str):
    return c.post("/radar/funds/attach", json={"thesis_id": str(card), "symbol": symbol})


def test_a_plain_users_attach_of_an_unknown_symbol_is_404_and_writes_nothing(people, db):
    before = _master_count(db)
    r = _attach(people["p"], people["p_card"], "NEWF")
    assert r.status_code == 404
    assert r.json()["detail"] == NOT_IN_MASTER
    assert _master_count(db) == before


def test_a_plain_users_attach_of_an_existing_equity_is_404_and_leaves_its_kind(people, db):
    _security(db, "EQTY")
    before = _master_count(db)
    r = _attach(people["p"], people["p_card"], "eqty")
    assert r.status_code == 404
    assert r.json()["detail"] == NOT_IN_MASTER
    assert _kind(db, "EQTY") == "equity"
    assert _master_count(db) == before


def test_a_plain_users_attach_of_an_existing_fund_succeeds(people, db):
    sid = _security(db, "FUNDX", kind="etf")
    before = _master_count(db)
    r = _attach(people["p"], people["p_card"], "fundx")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] is True and body["security_id"] == str(sid) and body["ticker"] == "FUNDX"
    assert _master_count(db) == before
    card = people["p"].get(f"/theses/{people['p_card']}").json()
    assert [m["segment"] for m in card["basket"] if m["ticker"] == "FUNDX"] == ["ETFs"]


def test_a_stewards_attach_keeps_lookup_or_create(people, db):
    _security(db, "EQTY")
    r = _attach(people["s"], people["s_card"], "EQTY")
    assert r.status_code == 200, r.text
    assert r.json()["added"] is True
    assert _kind(db, "EQTY") == "etf"  # the steward's attach still marks an existing row


# --- a hostile ticker is 422 on both routes, and map_ticker refuses it --------------------------------------------


@pytest.mark.parametrize("ticker", HOSTILE)
def test_a_hostile_ticker_is_422_on_both_routes_for_a_steward(people, db, ticker):
    before = _master_count(db)
    r = people["s"].post("/workbench/securities/resolve-etf", json={"ticker": ticker})
    assert r.status_code == 422, r.text
    r = _attach(people["s"], people["s_card"], ticker)
    assert r.status_code == 422, r.text
    assert _master_count(db) == before


@pytest.mark.parametrize("ticker", ["BRK-B", "bf.b", " lit ", "A", "ABCDEFGHIJKL"])
def test_a_real_ticker_shape_passes_the_request_models(ticker):
    from app.schemas_api import FundAttachRequest, ResolveEtfRequest

    want = ticker.strip().upper()
    assert ResolveEtfRequest(ticker=ticker).ticker == want
    assert FundAttachRequest(thesis_id=uuid.uuid4(), symbol=ticker).symbol == want


@pytest.mark.parametrize("ticker", [*HOSTILE, "", "  ", "-AB", "ABCDEFGHIJKLM", "AB/C", "A\\B"])
def test_map_ticker_refuses_a_hostile_ticker_before_the_cache_or_the_network(
    ticker, tmp_path, monkeypatch
):
    from securities import figi

    def boom(*a, **kw):
        raise AssertionError("OpenFIGI was called")

    monkeypatch.setattr(figi, "_fetch_live", boom)
    with pytest.raises(ValueError):
        figi.map_ticker(ticker, cache_dir=tmp_path / "figi", allow_live=True)
    assert not (tmp_path / "figi").exists()


# --- the grep guard: master writers under app/routers/, and one hop into workbench/ ------------------------------

# Every function in ``securities.master`` that writes the shared master.
_WRITERS = {
    "resolve",
    "populate_universe",
    "enrich",
    "mark_instrument_kind",
    "set_price_symbol",
    "restore_price_symbol",
    "set_name",
    "set_cik",
    "restore_cik_identity",
    "set_business_type",
}
# The ONLY (file, function) sites under app/routers/ that may call a master writer.
#   "steward": the route carries require_super_admin (checked below);
#   "steward-branch": every such call sits inside an ``if`` whose test names the steward (checked below), so a
#     plain user's request never reaches it (security hardening PR 1b);
#   "server-sourced": the value written comes from the server, never the request (each one says why).
_ALLOWED: dict[tuple[str, str], str] = {
    ("workbench.py", "resolve_etf"): "steward",
    ("workbench.py", "set_business_type"): "steward",
    # the automatic OTC price-symbol resolve on a finalize pull: a steward's pull only (PR 1b)
    ("workbench.py", "ingest_security_prices"): "steward-branch",
}
# ONE HOP: the (file, function) sites under backend/workbench/ that call a master writer. A router may reach one
# of them only from a site in ``_ALLOWED_HOPS`` (steward-gated, or inside a steward branch).
_WORKBENCH_WRITERS: dict[tuple[str, str], str] = {
    # the draft's identity enrichment (sector / exchange / status onto the discovered names' rows)
    ("enrichment.py", "enrich_for_ciks"): "master.enrich",
}
_ALLOWED_HOPS: dict[tuple[str, str], str] = {
    # the draft pipeline: a steward's draft stores the identity; a plain user's computes it for the response only
    ("workbench.py", "execute_draft"): "steward-branch",
}


def _calls(path: Path, module: str, writers: set[str]) -> list[tuple[str, str, tuple[str, ...]]]:
    """``(function, writer, guards)`` for each call in ``path`` to a ``writers`` function of the dotted ``module``
    (``securities.master``), however it was imported. ``guards`` are the tests of the ``if`` statements whose BODY
    encloses the call (an ``else`` branch adds ``"else"``, never the test), innermost last."""
    package, _, leaf = module.rpartition(".")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()  # local names bound to the module
    names: dict[str, str] = {}  # local name -> writer, for ``from <module> import x``
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == package:
            modules |= {a.asname or a.name for a in node.names if a.name == leaf}
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            names |= {a.asname or a.name: a.name for a in node.names if a.name in writers}
        elif isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names if a.name == module and a.asname}
    found: list[tuple[str, str, tuple[str, ...]]] = []

    def visit(node: ast.AST, fn: str, guards: tuple[str, ...]) -> None:
        if isinstance(node, ast.If):
            visit(node.test, fn, guards)
            for child in node.body:
                visit(child, fn, (*guards, ast.unparse(node.test)))
            for child in node.orelse:
                visit(child, fn, (*guards, "else"))
            return
        here = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and here != fn:
            guards = ()  # a nested function's guards start fresh
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in writers:
                owner = ast.unparse(f.value)
                if owner in modules or owner == module:
                    found.append((here, f.attr, guards))
            elif isinstance(f, ast.Name) and f.id in names:
                found.append((here, names[f.id], guards))
        for child in ast.iter_child_nodes(node):
            visit(child, here, guards)

    visit(tree, "<module>", ())
    return found


def _writer_calls(path: Path) -> list[tuple[str, str]]:
    """``(function, writer)`` for each call to a ``securities.master`` writer in ``path``."""
    return [(fn, w) for fn, w, _ in _calls(path, "securities.master", _WRITERS)]


def _steward_gated(path: Path, fn: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            gate = [ast.unparse(d) for d in node.decorator_list]
            gate += [ast.unparse(d) for d in (*node.args.defaults, *node.args.kw_defaults) if d]
            return any("require_super_admin" in g for g in gate)
    return False


def _in_steward_branch(guards: tuple[str, ...]) -> bool:
    """The call sits inside the body of an ``if`` whose test names the steward."""
    return any("steward" in g for g in guards if g != "else")


def _check_sites(
    found: dict[tuple[str, str], list[tuple[str, ...]]],
    allowed: dict[tuple[str, str], str],
    base: Path,
) -> None:
    offenders = [f"{rel}:{fn}" for (rel, fn) in found if (rel, fn) not in allowed]
    assert offenders == []
    assert set(found) == set(
        allowed
    )  # a stale allowlist entry is removed, not left to cover a future call
    for (rel, fn), why in allowed.items():
        if why == "steward":
            assert _steward_gated(base / rel, fn), f"{rel}:{fn} is not steward-gated"
        elif why == "steward-branch":
            for guards in found[(rel, fn)]:
                assert _in_steward_branch(guards), f"{rel}:{fn} writes outside a steward branch"


def test_master_writers_are_called_only_at_allowlisted_router_sites():
    routers = _BACKEND / "app" / "routers"
    found: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    for p in sorted(routers.rglob("*.py")):
        rel = p.relative_to(routers).as_posix()
        for fn, _writer, guards in _calls(p, "securities.master", _WRITERS):
            found.setdefault((rel, fn), []).append(guards)
    _check_sites(found, _ALLOWED, routers)


def test_master_writers_under_workbench_are_listed():
    """Every master-writer call under backend/workbench/ is a listed one-hop writer."""
    wb = _BACKEND / "workbench"
    seen = {
        (p.relative_to(wb).as_posix(), fn)
        for p in sorted(wb.rglob("*.py"))
        for fn, _w, _g in _calls(p, "securities.master", _WRITERS)
    }
    assert seen == set(_WORKBENCH_WRITERS)


def test_a_router_reaches_a_workbench_master_writer_only_behind_a_steward_check():
    routers = _BACKEND / "app" / "routers"
    found: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    for p in sorted(routers.rglob("*.py")):
        rel = p.relative_to(routers).as_posix()
        for wfile, wfn in _WORKBENCH_WRITERS:
            module = "workbench." + wfile.removesuffix(".py").replace("/", ".")
            for fn, _w, guards in _calls(p, module, {wfn}):
                found.setdefault((rel, fn), []).append(guards)
    _check_sites(found, _ALLOWED_HOPS, routers)


def test_the_guard_sees_a_new_writer_call(tmp_path):
    """The guard is not vacuous: each import shape it must catch is caught."""
    p = tmp_path / "x.py"
    p.write_text(
        "from securities import master\n"
        "from securities import master as m\n"
        "from securities.master import set_name as sn\n"
        "import securities.master\n"
        "def a(conn):\n    master.resolve(conn, 'X')\n"
        "def b(conn):\n    m.enrich(conn)\n"
        "def c(conn):\n    sn(conn)\n"
        "def d(conn):\n    securities.master.set_cik(conn)\n"
        "def e(conn):\n    master.get(conn)\n",
        encoding="utf-8",
    )
    assert sorted(_writer_calls(p)) == [
        ("a", "resolve"),
        ("b", "enrich"),
        ("c", "set_name"),
        ("d", "set_cik"),
    ]


def test_the_guard_reads_a_steward_branch(tmp_path):
    """A call in the body of ``if steward:`` is guarded; one in its ``else``, or under another test, is not."""
    p = tmp_path / "y.py"
    p.write_text(
        "from workbench.enrichment import enrich_for_ciks\n"
        "def a(conn, steward):\n    if steward:\n        enrich_for_ciks(conn)\n"
        "def b(conn, steward):\n    if steward:\n        pass\n    else:\n        enrich_for_ciks(conn)\n"
        "def c(conn, x):\n    if x:\n        enrich_for_ciks(conn)\n"
        "def d(conn, user):\n    if user.is_steward and x:\n        if y:\n            enrich_for_ciks(conn)\n",
        encoding="utf-8",
    )
    got = {
        fn: _in_steward_branch(g)
        for fn, _w, g in _calls(p, "workbench.enrichment", {"enrich_for_ciks"})
    }
    assert got == {"a": True, "b": False, "c": False, "d": True}
