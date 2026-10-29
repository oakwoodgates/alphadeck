"""The Dashboards' guard rails (``docs/DASHBOARDS.md``), as source scans over the REAL tree — pure, no DB.

Rule 1 ("display only") is enforced by three scans, and the self-contained layout (rule 8) by the leaf scan:

1. LEAF: nothing outside ``app/``, ``dashboards/`` and ``tests/`` imports a dashboard, so the call path,
   the pipeline, signals, calls and editions can never depend on one. Inside ``app/``, only ``main.py``
   mounts the router package and only the dashboard routers import the ``dashboards`` package.
2. NO LLM, NO NOTIFICATIONS: neither dashboard tree imports ``llm``, ``notify`` or the ``anthropic`` SDK
   (rankings are never LLM-sourced; a dashboard never pushes).
3. NO WRITES: neither dashboard tree carries an SQL write in its strings, and neither calls
   ``call_for_thesis`` without an explicit ``record=False`` (its DEFAULT appends to the ``calls`` log).

Each scan has a twin below that feeds it a planted violation, so a regex or walker that silently stops
matching fails here instead of waving a real violation through.
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
DASHBOARDS_PKG = BACKEND / "dashboards"
DASHBOARD_ROUTERS = BACKEND / "app" / "routers" / "dashboards"
DASHBOARD_TREES = (DASHBOARDS_PKG, DASHBOARD_ROUTERS)

DOC = "docs/DASHBOARDS.md"


# ---------------------------------------------------------------------------------------------------------
# the scanners (pure functions of source text, so the self-tests below can feed them a planted violation)
# ---------------------------------------------------------------------------------------------------------


def _py_files(root: Path) -> Iterator[Path]:
    """Every ``.py`` under ``root``, pruning virtualenvs, caches and dot-directories (never scanned)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".")
            and d not in ("__pycache__", "node_modules", "site-packages")
            and not d.endswith(".egg-info")
            and not (Path(dirpath) / d / "pyvenv.cfg").exists()
        )
        for f in sorted(filenames):
            if f.endswith(".py"):
                yield Path(dirpath) / f


def imports_of(source: str) -> list[tuple[str, int]]:
    """``(dotted name, line)`` for every absolute import. ``from a.b import c`` yields both ``a.b`` and
    ``a.b.c``, so ``from app.routers import dashboards`` is seen as importing ``app.routers.dashboards``.
    """
    out: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.module, node.lineno))
            out += [(f"{node.module}.{a.name}", node.lineno) for a in node.names]
    return out


def _under(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def string_literals_of(source: str) -> list[tuple[str, int]]:
    """``(text, line)`` for every string the code can USE: docstrings and other bare string statements are
    skipped (prose, never executed), and an f-string is rejoined with ``{}`` for each placeholder so
    ``f"UPDATE {t} SET …"`` reads as one statement."""
    tree = ast.parse(source)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            skip.add(id(node.value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in node.values)
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.value, node.lineno))
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            out.append((text, node.lineno))
    return out


_IDENT = r"[\w.\"{}]+"  # a table name, quoted or dotted, or a psycopg/f-string placeholder
_OBJECT = (
    r"(?:table|index|view|materialized\s+view|sequence|schema|type|function|procedure|trigger"
    r"|extension|database|role|domain|policy|rule)"
)
# SQL-SHAPED, case-insensitive: each keyword with the object it acts on, so prose ("truncate the label",
# a ``created_at`` column) never trips it while every real write statement does.
SQL_WRITES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("INSERT INTO", re.compile(r"\binsert\s+into\b", re.I)),
    (
        "UPDATE ... SET",
        re.compile(rf"\bupdate\s+(?:only\s+)?{_IDENT}(?:\s+(?:as\s+)?\w+)?\s+set\b", re.I),
    ),
    ("DELETE FROM", re.compile(r"\bdelete\s+from\b", re.I)),
    (
        "TRUNCATE",
        re.compile(
            rf"\btruncate\s+(?:table\b|only\b|{_IDENT}\s*(?:[,;)]|$|\b(?:restart|continue|cascade|restrict)\b))",
            re.I,
        ),
    ),
    ("ALTER", re.compile(rf"\balter\s+{_OBJECT}\b", re.I)),
    (
        "CREATE",
        re.compile(
            rf"\bcreate\s+(?:or\s+replace\s+)?(?:(?:unique|temp|temporary|unlogged|global|local)\s+)*{_OBJECT}\b",
            re.I,
        ),
    ),
    ("DROP", re.compile(rf"\bdrop\s+{_OBJECT}\b", re.I)),
    ("MERGE INTO", re.compile(r"\bmerge\s+into\b", re.I)),
)


def sql_writes_in(text: str) -> list[str]:
    """The write keywords ``text`` carries (empty = none)."""
    return [label for label, rx in SQL_WRITES if rx.search(text)]


def recording_calls_of(source: str) -> list[int]:
    """Lines that call ``call_for_thesis`` WITHOUT an explicit ``record=False``. Reading the live call goes
    through it (the Board's ``/theses/{id}/call`` passes ``record=False``), but its DEFAULT is
    ``record=True``, which appends the card to the ``calls`` log: the nightly's write, never a dashboard's.
    A ``**kwargs`` call cannot be checked, so it counts as recording."""
    out: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if name != "call_for_thesis":
            continue
        rec = next((k for k in node.keywords if k.arg == "record"), None)
        if not (rec and isinstance(rec.value, ast.Constant) and rec.value.value is False):
            out.append(node.lineno)
    return out


# the LLM + notification seams a dashboard must never reach (rule 1)
FORBIDDEN_PACKAGES = {
    "llm": "the LLM seam",
    "anthropic": "the Anthropic SDK (the LLM, around the llm seam)",
    "notify": "notifications",
}
FORBIDDEN_NAMES = {
    "app.deps.get_llm_client": "the LLM seam",
    "app.deps.get_decompose_client": "the LLM seam",
}


def llm_or_notify_imports(source: str) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for name, line in imports_of(source):
        why = FORBIDDEN_NAMES.get(name) or next(
            (w for pkg, w in FORBIDDEN_PACKAGES.items() if _under(name, pkg)), None
        )
        if why:
            out.append((name, line, why))
    return out


def _rel(p: Path) -> str:
    return p.relative_to(BACKEND).as_posix()


def _is_dashboard_import(name: str) -> bool:
    return _under(name, "dashboards") or _under(name, "app.routers.dashboards")


# Offenders are keyed by (file, line) and keep the FIRST message, so one offending line reports once
# (``from llm.client import X`` matches as both ``llm.client`` and ``llm.client.X``).
Offenders = dict[tuple[str, int], str]


def _fail_message(rule: str, offenders: Offenders) -> str:
    return f"{rule}:\n" + "\n".join(f"  {msg}" for msg in offenders.values())


# ---------------------------------------------------------------------------------------------------------
# the rules, over the real tree
# ---------------------------------------------------------------------------------------------------------


def test_the_scan_sees_both_dashboard_trees():
    """A moved or renamed tree would make every scan below pass on nothing."""
    for tree in DASHBOARD_TREES:
        assert (
            tree / "__init__.py"
        ).is_file(), f"{_rel(tree)} is missing - update the scans and {DOC}"


def test_leaf_rule_nothing_outside_app_dashboards_and_tests_imports_a_dashboard():
    offenders: Offenders = {}
    for p in _py_files(BACKEND):
        rel = _rel(p)
        if rel.split("/")[0] in ("app", "dashboards", "tests"):
            continue
        for name, line in imports_of(p.read_text(encoding="utf-8")):
            if _is_dashboard_import(name):
                offenders.setdefault((rel, line), f"{rel}:{line} imports {name!r}")
    assert not offenders, _fail_message(
        f"LEAF RULE ({DOC}): a dashboard is a leaf - the call path, pipeline, signals, calls and "
        "editions never import one",
        offenders,
    )


def test_leaf_rule_inside_app_only_main_mounts_dashboards_and_only_their_routers_import_them():
    offenders: Offenders = {}
    for p in _py_files(BACKEND / "app"):
        rel = _rel(p)
        in_routers = p.is_relative_to(DASHBOARD_ROUTERS)
        for name, line in imports_of(p.read_text(encoding="utf-8")):
            if _under(name, "app.routers.dashboards") and not (in_routers or rel == "app/main.py"):
                offenders.setdefault(
                    (rel, line), f"{rel}:{line} imports {name!r} (only app/main.py mounts it)"
                )
            elif _under(name, "dashboards") and not in_routers:
                offenders.setdefault(
                    (rel, line),
                    f"{rel}:{line} imports {name!r} (only app/routers/dashboards/ may; a dashboard's "
                    "wire models live in its own router module, not app/schemas_api.py)",
                )
    assert not offenders, _fail_message(f"LEAF RULE ({DOC}), inside app/", offenders)


def test_no_dashboard_imports_the_llm_or_notify():
    offenders: Offenders = {}
    for tree in DASHBOARD_TREES:
        for p in _py_files(tree):
            rel = _rel(p)
            for name, line, why in llm_or_notify_imports(p.read_text(encoding="utf-8")):
                offenders.setdefault((rel, line), f"{rel}:{line} imports {name!r} - {why}")
    assert not offenders, _fail_message(
        f"DISPLAY ONLY ({DOC}, rule 1): a dashboard never calls the LLM (rankings are computed by "
        "code, never LLM-sourced) and never sends a notification",
        offenders,
    )


def test_no_dashboard_writes_to_the_database():
    offenders: Offenders = {}
    for tree in DASHBOARD_TREES:
        for p in _py_files(tree):
            rel = _rel(p)
            source = p.read_text(encoding="utf-8")
            for text, line in string_literals_of(source):
                for label in sql_writes_in(text):
                    offenders.setdefault(
                        (rel, line), f"{rel}:{line} carries an SQL write ({label})"
                    )
            for line in recording_calls_of(source):
                offenders.setdefault(
                    (rel, line),
                    f"{rel}:{line} calls call_for_thesis without record=False (its default appends "
                    "the card to the calls log)",
                )
        for p in sorted(tree.rglob("*.sql")):
            for label in sql_writes_in(p.read_text(encoding="utf-8")):
                offenders.setdefault((_rel(p), 0), f"{_rel(p)} carries an SQL write ({label})")
    assert not offenders, _fail_message(
        f"DISPLAY ONLY ({DOC}, rule 1): a dashboard READS; it never writes to the database",
        offenders,
    )


# ---------------------------------------------------------------------------------------------------------
# the scanners bite (planted violations, fed as text — nothing is written into the tree)
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import dashboards",
        "import dashboards.tape_breadth as tb",
        "from dashboards import tape_breadth",
        "from dashboards.tape_breadth import compute",
        "from app.routers import dashboards",
        "from app.routers.dashboards import router",
    ],
)
def test_the_leaf_scan_sees_every_import_shape(source):
    assert any(_is_dashboard_import(n) for n, _ in imports_of(source))


def test_the_leaf_scan_ignores_unrelated_names():
    source = (
        "import app.routers.theses\nfrom scoreboard import dashboard_helpers\nfrom app import deps"
    )
    assert not any(_is_dashboard_import(n) for n, _ in imports_of(source))


@pytest.mark.parametrize(
    "source",
    [
        "from llm.client import LLMClient",
        "import llm",
        "import anthropic",
        "from notify import slack",
        "import notify.slack",
        "from app.deps import get_conn, get_llm_client",
        "from app.deps import get_decompose_client",
    ],
)
def test_the_llm_notify_scan_bites(source):
    assert llm_or_notify_imports(source)


def test_the_llm_notify_scan_lets_ordinary_reads_through():
    source = "from app.deps import get_conn\nfrom repositories import thesis_repo\nimport psycopg"
    assert llm_or_notify_imports(source) == []


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO calls (id) VALUES (%s)",
        "insert into t values (1)",
        "UPDATE calls SET verdict = %s",
        "update thesis t set name = %s",
        "UPDATE {} SET x = %s",
        "DELETE FROM basket_member WHERE id = %s",
        "TRUNCATE fact_eod, fact_insider_txn CASCADE",
        "truncate table calls",
        "TRUNCATE calls",
        "ALTER TABLE calls ADD COLUMN x int",
        "CREATE TABLE dash_cache (k text)",
        "create unique index i on t (x)",
        "CREATE OR REPLACE VIEW v AS SELECT 1",
        "CREATE MATERIALIZED VIEW v AS SELECT 1",
        "DROP TABLE calls",
        "MERGE INTO t USING s ON t.id = s.id",
    ],
)
def test_the_write_scan_bites(sql):
    assert sql_writes_in(sql), sql


@pytest.mark.parametrize(
    "text",
    [
        "SELECT created_at, recorded_at FROM calls WHERE thesis_id = %s",
        "largest 1W move",
        "would truncate the label to 40 chars",
        "names updated since the last run",
        "the alternative reading",
    ],
)
def test_the_write_scan_lets_reads_and_prose_through(text):
    assert sql_writes_in(text) == [], text


def test_the_write_scan_reads_code_strings_but_not_docstrings():
    source = (
        '"""A docstring may say INSERT INTO and DELETE FROM — prose, never executed."""\n'
        "def f(conn, table):\n"
        '    """Nor here: UPDATE calls SET x."""\n'
        '    conn.execute(f"UPDATE {table} SET seen = true")\n'
    )
    found = [(line, sql_writes_in(text)) for text, line in string_literals_of(source)]
    assert [(line, labels) for line, labels in found if labels] == [(4, ["UPDATE ... SET"])]


@pytest.mark.parametrize(
    "source",
    [
        "card = call_for_thesis(conn, tid, asof)",  # the default records
        "card = call_for_thesis(conn, tid, asof, known_at=k, clock=c, record=True)",
        "card = pipeline.call_for_thesis.call_for_thesis(conn, tid, asof)",
        "card = call_for_thesis(conn, tid, asof, **opts)",  # unverifiable = recording
        "card = call_for_thesis(conn, tid, asof, record=flag)",
    ],
)
def test_the_record_scan_bites(source):
    assert recording_calls_of(source) == [1]


def test_the_record_scan_lets_the_boards_read_through():
    source = "card = call_for_thesis(conn, tid, asof, known_at=known_at, clock=clock, record=False)"
    assert recording_calls_of(source) == []
