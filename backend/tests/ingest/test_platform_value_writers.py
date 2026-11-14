"""V2 W2d: GUARD EVERY WRITER of the two platform value tables (``fact_shares_outstanding``, ``fact_cash_burn``).

A share count and cash/burn are PLATFORM VALUES (``docs/PLATFORM_VALUES.md``): one value per company for everyone,
in the data tenant, set by the machine from a clean filing (``auto``) or by the steward (their email). Every value
a user sees is chosen by ONE pick and judged by ONE state rule, and the steward's undo works by setting a version
aside. A new writer that skips those paths (writes a workspace, re-appends an undone version, mints ``operator``
rows) would quietly undo V2, so this guard fails when ANY product code writes either table outside a short,
named allowlist. It reads source code only (AST), no database. The ``tests/dashboards/test_one_pick.py`` and
``tests/app/test_shared_master_writes.py`` pattern.

Three rules:

(1) ``ingest_shares_outstanding`` / ``ingest_cash_burn`` (the ingest functions: the data-tenant check, then the
    append, then the change stamp) are called only by the named writers in ``_INGEST_CALLERS``.
(2) No other product file appends to, updates, deletes from or moves rows of the two tables: a file that names
    either table (or imports the generic fact-table list ``_FACT_IDENTITY``) AND calls ``append_fact`` on it (or on
    a table it computes) or holds a write statement against it (or against a table it computes) is listed in
    ``_DIRECT_WRITERS``.
(3) No product code stamps ``ratified_by="operator"`` (the V1 word, which the state read shows as "verified")
    except ``pipeline/seed.py``.

A STALE entry fails too (a listed writer that no longer writes is removed, never left to cover a future one), with
one exception, stated where it applies: modules the parallel V2 W2 pull requests add or retire (below).

PARALLEL PULL REQUESTS (the W2 plan, section 4). PR 2 adds ``platform_values/machine.py`` (the machine's AUTO
writer) and moves ``auto_confirm_fact`` onto it; PR 3 adds ``platform_values/steward.py`` (the steward's approve /
correct / undo), moves ``ratify_fact`` onto it and deletes ``pipeline/ratify_shares.py`` and
``pipeline/ratify_cash_burn.py``. Either may merge before or after this guard, so:
- an EXPECTED writer (``_EXPECTED``) may be absent; once present it must call an ingest function;
- a RETIRING site (``_RETIRING``) may call an ingest function only while the module that replaces it is absent.
  Once that module exists the site must not call one. A retiring site that no longer exists, or no longer calls,
  passes; drop its entry once both PRs are merged.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_TABLES = ("fact_shares_outstanding", "fact_cash_burn")
_INGEST_FUNCTIONS = frozenset({"ingest_shares_outstanding", "ingest_cash_burn"})
_INGEST_MODULES = ("ingest.shares", "ingest.cash_burn")

# --- (1) the callers of the ingest functions --------------------------------------------------------------------
# file -> why it may write a platform value. Each is checked to still call an ingest function (stale = fail).
_INGEST_CALLERS: dict[str, str] = {
    # V2 W2b (PR 2): the machine's AUTO writer, under the company lock, honoring the pin (``ratified_by='auto'``)
    "platform_values/machine.py": "the machine writer (PR 2)",
    # V2 W2c (PR 3): the steward's approve / correct (``ratified_by=<the steward's email>``), under the lock
    "platform_values/steward.py": "the steward writer (PR 3)",
    # the demo seed: fixed 10-Q fixtures into the data tenant, ``operator`` (allowed by migration 0206's vocabulary)
    "pipeline/seed.py": "the demo seed's fixtures (W2 plan PR 4 step 3: no change)",
}
# The expected writers the parallel PRs add: tolerated while absent (``_INGEST_CALLERS`` names them).
_EXPECTED = frozenset({"platform_values/machine.py", "platform_values/steward.py"})
# (file, function) -> the module whose arrival retires it. Today's writers, removed by PR 2 / PR 3.
_RETIRING: dict[tuple[str, str], str] = {
    # get-data's auto-confirm: PR 2 moves it onto platform_values.machine.record_extraction
    ("app/routers/workbench.py", "auto_confirm_fact"): "platform_values/machine.py",
    # the Workbench confirm: PR 3 moves its shares / cash branches onto platform_values.steward.correct
    ("app/routers/workbench.py", "ratify_fact"): "platform_values/steward.py",
    # the two V1 ratify CLIs: PR 3 deletes them (approved decision D4 (a))
    ("pipeline/ratify_shares.py", "main"): "platform_values/steward.py",
    ("pipeline/ratify_cash_burn.py", "main"): "platform_values/steward.py",
}

# --- (2) direct writers of the two tables -----------------------------------------------------------------------
# file -> why it may write the tables directly. Each is checked to still be detected as a writer (stale = fail).
_DIRECT_WRITERS: dict[str, str] = {
    # the generic append every fact writer goes through (``INSERT INTO {table}``); it checks nothing itself
    "db/bitemporal.py": "the generic append_fact (the definition)",
    # the two ingest functions: require_write_tenant first, then the append, then mark_changed
    "ingest/shares.py": "ingest_shares_outstanding (the definition)",
    "ingest/cash_burn.py": "ingest_cash_burn (the definition)",
    # the two repairs (V2 W2d): data tenant only (require_write_tenant), never a fact a steward decision names,
    # mark_changed before the commit (pipeline/platform_repair_guard.py)
    "pipeline/repair_shares_cover_dates.py": "a guarded repair (W2d)",
    "pipeline/repair_filing_dates.py": "a guarded repair (W2d)",
    # V2 W0b: the one-time, fork-guarded move of the founding workspace's rows INTO the data tenant
    "pipeline/v2_move.py": "the W0b platform-facts move (into the data tenant only)",
    # NOT V2-AWARE (found by this guard; outside W2's plan): deletes byte-identical duplicate versions in every
    # ``_FACT_IDENTITY`` table, every tenant. Listed so a NEW writer still fails; its V2 rules are a follow-up.
    "pipeline/dedup_identical_versions.py": "the duplicate-version dedup (generic; follow-up)",
}

# --- (3) the V1 word ----------------------------------------------------------------------------------------------
_OPERATOR_STAMPERS: dict[str, str] = {
    "pipeline/seed.py": "the demo seed's fixtures (carried-over V1 vocabulary, allowed by 0206)",
}

# a write statement whose target is one of the tables, or a table the code computes ({...} / %s)
_WRITE_SQL = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|COPY)\s+"
    r"(?:ONLY\s+)?(?:\"?(?:" + "|".join(_TABLES) + r")\b|\{|%s)",
    re.IGNORECASE,
)


def _product_sources(root: Path) -> Iterator[Path]:
    """Every product module under ``root`` (the backend): its package directories, never tests or a venv."""
    for d in sorted(root.iterdir()):
        if (
            d.is_dir()
            and not d.name.startswith(".")
            and d.name not in ("tests", "__pycache__", "data")
            and not d.name.endswith(".egg-info")
        ):
            yield from sorted(d.rglob("*.py"))


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


# --- the detectors ----------------------------------------------------------------------------------------------


def _enclosing_calls(tree: ast.AST) -> Iterator[tuple[str, ast.Call]]:
    """``(innermost function name or "<module>", call)`` for every call in ``tree``."""

    def visit(node: ast.AST, fn: str) -> Iterator[tuple[str, ast.Call]]:
        here = node.name if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else fn
        if isinstance(node, ast.Call):
            yield here, node
        for child in ast.iter_child_nodes(node):
            yield from visit(child, here)

    yield from visit(tree, "<module>")


def ingest_calls(path: Path) -> set[str]:
    """The functions in ``path`` that call an ingest function, however it was imported (a bare or aliased name
    from ``ingest.shares`` / ``ingest.cash_burn``, or any ``<x>.ingest_shares_outstanding(...)``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _INGEST_MODULES:
            names |= {a.asname or a.name for a in node.names if a.name in _INGEST_FUNCTIONS}
    found: set[str] = set()
    for fn, call in _enclosing_calls(tree):
        f = call.func
        if (isinstance(f, ast.Name) and f.id in names) or (
            isinstance(f, ast.Attribute) and f.attr in _INGEST_FUNCTIONS
        ):
            found.add(fn)
    return found


def _strings(tree: ast.AST) -> Iterator[str]:
    """Every string constant, and every f-string as its source text (so ``f"UPDATE {t} ..."`` reads whole)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            yield ast.unparse(node)


def _names_a_table(tree: ast.AST) -> bool:
    if any(re.search(r"\b(?:" + "|".join(_TABLES) + r")\b", s) for s in _strings(tree)):
        return True
    return any(
        isinstance(n, ast.ImportFrom) and any(a.name == "_FACT_IDENTITY" for a in n.names)
        for n in ast.walk(tree)
    )


def _append_fact_targets_a_table(call: ast.Call) -> bool:
    """An ``append_fact(conn, <table>, ...)`` whose table is one of the two, or one the code computes."""
    f = call.func
    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    if name != "append_fact":
        return False
    arg = call.args[1] if len(call.args) > 1 else None
    arg = arg or next((k.value for k in call.keywords if k.arg == "table"), None)
    if isinstance(arg, ast.Constant):
        return arg.value in _TABLES
    return (
        True  # a computed table (a constant name, a dict lookup, a parameter): assume it may be one
    )


def direct_writer(path: Path) -> bool:
    """Does ``path`` write either table directly (rule 2)?"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if not _names_a_table(tree):
        return False
    if any(_append_fact_targets_a_table(c) for _fn, c in _enclosing_calls(tree)):
        return True
    return any(_WRITE_SQL.search(s) for s in _strings(tree))


def operator_stamps(path: Path) -> list[int]:
    """The lines in ``path`` that stamp ``ratified_by="operator"``: a keyword argument, a dict entry, or a
    parameter default."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []

    def is_operator(v: ast.AST | None) -> bool:
        return isinstance(v, ast.Constant) and v.value == "operator"

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "ratified_by" and is_operator(node.value):
            lines.append(node.value.lineno)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=True):
                if isinstance(k, ast.Constant) and k.value == "ratified_by" and is_operator(v):
                    lines.append(v.lineno)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            a = node.args
            pos = [*a.posonlyargs, *a.args]
            pairs = list(zip(pos[len(pos) - len(a.defaults) :], a.defaults, strict=True))
            pairs += [(arg, d) for arg, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d]
            lines += [d.lineno for arg, d in pairs if arg.arg == "ratified_by" and is_operator(d)]
    return sorted(lines)


# --- the checks (pure over a source tree, so the self-tests run them on a planted tree) -----------------------


def ingest_offenders(root: Path) -> list[str]:
    """``file:function`` sites calling an ingest function that rule (1) does not allow."""
    present = {_rel(root, p) for p in _product_sources(root)}
    out: list[str] = []
    for p in _product_sources(root):
        rel = _rel(root, p)
        for fn in sorted(ingest_calls(p)):
            if rel in _INGEST_CALLERS:
                continue
            retired_by = _RETIRING.get((rel, fn))
            if retired_by is not None and retired_by not in present:
                continue  # today's writer, until the module that replaces it lands
            out.append(f"{rel}:{fn}")
    return out


def direct_offenders(root: Path) -> list[str]:
    return [
        _rel(root, p)
        for p in _product_sources(root)
        if _rel(root, p) not in _DIRECT_WRITERS and direct_writer(p)
    ]


def operator_offenders(root: Path) -> list[str]:
    return [
        f"{_rel(root, p)}:{line}"
        for p in _product_sources(root)
        if _rel(root, p) not in _OPERATOR_STAMPERS
        for line in operator_stamps(p)
    ]


# --- (1) -----------------------------------------------------------------------------------------------------------


def test_the_ingest_writers_are_called_only_by_named_writers():
    offenders = ingest_offenders(_BACKEND)
    assert offenders == [], (
        f"{offenders} write a share count or cash/burn: a platform value is written only by the machine writer "
        "(platform_values/machine.py) or the steward writer (platform_values/steward.py) — or name the site in "
        "this guard's allowlist with the reason"
    )


def test_every_named_ingest_caller_still_writes():
    stale = []
    for rel in _INGEST_CALLERS:
        p = _BACKEND / rel
        if not p.exists():
            if rel not in _EXPECTED:
                stale.append(f"{rel} (missing)")
            continue
        if not ingest_calls(p):
            stale.append(f"{rel} (calls no ingest function)")
    assert stale == [], f"drop these from _INGEST_CALLERS (or restore the write): {stale}"


def test_a_retiring_site_is_named_where_it_is():
    """Each retiring entry names a real site while its replacement is absent (a typo would cover nothing)."""
    for (rel, fn), retired_by in _RETIRING.items():
        p = _BACKEND / rel
        if (_BACKEND / retired_by).exists() or not p.exists():
            continue
        assert fn in ingest_calls(p), f"{rel}:{fn} calls no ingest function: drop it from _RETIRING"


# --- (2) -----------------------------------------------------------------------------------------------------------


def test_the_tables_are_written_directly_only_by_named_writers():
    offenders = direct_offenders(_BACKEND)
    assert offenders == [], (
        f"{offenders} write fact_shares_outstanding / fact_cash_burn directly: go through the ingest functions "
        "from a named writer, or name the file in _DIRECT_WRITERS with the reason (and its V2 rules: the data "
        "tenant only, never over a steward's decision, the change stamp)"
    )


def test_every_named_direct_writer_still_writes():
    stale = [
        rel
        for rel in _DIRECT_WRITERS
        if not (_BACKEND / rel).exists() or not direct_writer(_BACKEND / rel)
    ]
    assert stale == [], f"drop these from _DIRECT_WRITERS: {stale}"


# --- (3) -----------------------------------------------------------------------------------------------------------


def test_only_the_seed_stamps_operator():
    offenders = operator_offenders(_BACKEND)
    assert offenders == [], (
        f"{offenders} stamp ratified_by='operator': a new platform value is 'auto' (the machine) or the steward's "
        "email, never the V1 word"
    )
    stale = [
        rel
        for rel in _OPERATOR_STAMPERS
        if not (_BACKEND / rel).exists() or not operator_stamps(_BACKEND / rel)
    ]
    assert stale == [], f"drop these from _OPERATOR_STAMPERS: {stale}"


# --- the guard is not vacuous: a planted writer of each shape is caught -------------------------------------------


def _plant(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_a_planted_ingest_caller_is_caught_in_every_import_shape(tmp_path):
    _plant(
        tmp_path,
        "pipeline/rogue.py",
        "from ingest.shares import ingest_shares_outstanding\n"
        "from ingest.cash_burn import ingest_cash_burn as icb\n"
        "import ingest.shares\n"
        "from ingest import cash_burn\n"
        "def a(conn):\n    ingest_shares_outstanding(conn, 1)\n"
        "def b(conn):\n    icb(conn, 1)\n"
        "def c(conn):\n    ingest.shares.ingest_shares_outstanding(conn, 1)\n"
        "def d(conn):\n    cash_burn.ingest_cash_burn(conn, 1)\n"
        "def e(conn):\n    ingest_revenue_mix(conn, 1)\n",
    )
    assert ingest_offenders(tmp_path) == [
        "pipeline/rogue.py:a",
        "pipeline/rogue.py:b",
        "pipeline/rogue.py:c",
        "pipeline/rogue.py:d",
    ]


def test_a_named_writer_passes_and_a_retiring_site_retires_when_its_replacement_lands(tmp_path):
    call = "from ingest.shares import ingest_shares_outstanding\n"
    _plant(
        tmp_path, "pipeline/seed.py", call + "def s(conn):\n    ingest_shares_outstanding(conn)\n"
    )
    _plant(
        tmp_path,
        "app/routers/workbench.py",
        call
        + "def auto_confirm_fact(conn):\n    ingest_shares_outstanding(conn)\n"
        + "def ratify_fact(conn):\n    ingest_shares_outstanding(conn)\n"
        + "def other_route(conn):\n    ingest_shares_outstanding(conn)\n",
    )
    # before PR 2 / PR 3: today's two routes are tolerated; any other route is not
    assert ingest_offenders(tmp_path) == ["app/routers/workbench.py:other_route"]
    # PR 2 lands (machine.py exists): the auto-confirm must no longer call an ingest function itself
    _plant(
        tmp_path,
        "platform_values/machine.py",
        call + "def w(conn):\n    ingest_shares_outstanding(conn)\n",
    )
    assert ingest_offenders(tmp_path) == [
        "app/routers/workbench.py:auto_confirm_fact",
        "app/routers/workbench.py:other_route",
    ]


def test_a_planted_direct_writer_is_caught(tmp_path):
    _plant(
        tmp_path,
        "pipeline/rogue_append.py",
        "from db.bitemporal import append_fact\n"
        "def a(conn):\n    append_fact(conn, 'fact_cash_burn', {})\n",
    )
    _plant(
        tmp_path,
        "pipeline/rogue_computed.py",
        "from db.bitemporal import append_fact\nTABLE = 'fact_shares_outstanding'\n"
        "def a(conn):\n    append_fact(conn, TABLE, {})\n",
    )
    _plant(
        tmp_path,
        "pipeline/rogue_sql.py",
        "def a(cur):\n    cur.execute('DELETE FROM fact_shares_outstanding WHERE id = %s', (1,))\n",
    )
    _plant(
        tmp_path,
        "pipeline/rogue_update.py",
        "T = 'fact_cash_burn'\ndef a(cur, t):\n    cur.execute(f'UPDATE {t} SET tenant_id = %s', (1,))\n",
    )
    _plant(
        tmp_path,
        "pipeline/rogue_generic.py",
        "from db.bitemporal import _FACT_IDENTITY\n"
        "def a(cur, t):\n    cur.execute(f'DELETE FROM {t} WHERE id = 1')\n",
    )
    # not writers: another table's append, a read of ours, a write of another table in a file that reads ours
    _plant(
        tmp_path,
        "pipeline/fine.py",
        "from db.bitemporal import append_fact\n"
        "def a(conn, cur):\n    append_fact(conn, 'fact_price_eod', {})\n"
        "    cur.execute('SELECT count(*) FROM fact_cash_burn')\n"
        "    cur.execute('UPDATE thesis SET name = %s', ('x',))\n",
    )
    assert direct_offenders(tmp_path) == [
        "pipeline/rogue_append.py",
        "pipeline/rogue_computed.py",
        "pipeline/rogue_generic.py",
        "pipeline/rogue_sql.py",
        "pipeline/rogue_update.py",
    ]


def test_a_planted_operator_stamp_is_caught(tmp_path):
    _plant(
        tmp_path,
        "pipeline/rogue_op.py",
        "def a(conn):\n    w(conn, ratified_by='operator')\n"
        "def b(conn):\n    return {'ratified_by': 'operator'}\n"
        "def c(conn, *, ratified_by='operator'):\n    pass\n"
        "def d(conn):\n    w(conn, ratified_by='auto')\n",
    )
    assert operator_offenders(tmp_path) == [
        "pipeline/rogue_op.py:2",
        "pipeline/rogue_op.py:4",
        "pipeline/rogue_op.py:5",
    ]
