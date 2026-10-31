"""The ENGINE FINGERPRINT (G3) — which rules produced an edition, in a form a deploy can't fake.

``code_sha`` changes on every image build (a frontend-only deploy included), so it cannot say whether the CALL
LOGIC changed. The fingerprint is the ``CallConfig`` hash, a hash of the LOGIC of every module the editions
engine's output depends on — the call path (``signals/``, ``calls/``, ``domain/``, ``pipeline/core.py``), the
readers and the public-clock definition (``db/bitemporal.py``, ``db/public_clock.py``, ``db/set_aside.py``,
``db/insider_resolution.py``, the repositories it loads through) and the engine itself (``replay/``,
``editions/``) — and the values of the two ``Settings`` fields that engine reads. Content-addressed and sorted
by path, so it is identical across processes and machines for identical logic. The automatic cut (S7,
``editions.cut``) compares it; every edition and every page is stamped with it.

VERSION 2 (S7, 2026-10-23) — THE LOGIC, NOT THE BYTES. Version 1 hashed raw source bytes, so a comment, a
docstring or an unrelated ``Settings`` field changed the fingerprint and would have started a trial for
nothing. Version 2 hashes a NORMALIZED form of each file instead:

- **the AST with every bare string statement removed** (``normalized_source``): comments never reach an AST,
  and a docstring — or any other string literal standing alone as a statement — has no runtime effect, so
  neither moves the fingerprint. Everything that executes still does: a constant, a name, an operator.
- **serialized by our own walk, not ``ast.dump``**: Python 3.13 changed ``ast.dump`` to omit empty fields,
  and the cron image runs 3.11 while a host venv may run 3.13. The walk emits each node's type and its
  ``_fields`` in order and SKIPS a field that is ``None`` or an empty list (a 3.12+ ``type_params=[]`` is then
  absent on every version, like 3.11's), so the same source hashes the same on 3.10 through 3.13 (MEASURED on
  this tree with 3.10 / 3.12 / 3.13 and a 3.11 image; a test pins a golden over the constructs that differ).
- **``domain/settings.py`` and ``domain/rule_log.py`` are left out** (``EXCLUDED_SOURCES``). Evidence (grep of
  every hashed module, 2026-10-23): the engine's output reads exactly two ``Settings`` fields —
  ``market_tz`` (``domain/market_time.market_tz``, the market calendar every night's cap is read in) and
  ``price_settle_time`` (``db/public_clock``, when a bar becomes public). Their VALUES are hashed instead
  (``ENGINE_SETTINGS``), which also catches an environment override that a source hash never could. (Since F1,
  2026-10-30, a hashed module reads a third field, ``cron_run_at``, in ``domain/market_time.current_night``; only
  the serve path's live test and ``GET /clock`` call it, never the engine — ``replay/`` and ``editions/`` cap each
  night with ``known_at_for_asof`` directly — so it is not an engine setting.) The rule
  log is the record of declared changes, not logic — appending an entry must not start a trial by itself.

A fingerprint made by an earlier version means something else: ``fingerprint_version`` is stored beside it
(``engine_fingerprint``'s dict, in every edition's ``reason`` and every ``trial_no_change`` event), and one
without the key is version 1. ``editions.cut`` treats a version mismatch as "the rules may have changed": the
first trial after this ships re-runs the edition with the current engine and records the version-2
fingerprint, so it is never repeated.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from domain.config import CallConfig, config_hash
from domain.settings import get_settings

_ROOT = Path(__file__).resolve().parent.parent  # backend/

FINGERPRINT_VERSION = 2

ENGINE_SOURCES: tuple[str, ...] = (
    "signals",
    "calls",
    "domain",
    "replay",
    "editions",
    "pipeline/core.py",
    "db/bitemporal.py",
    "db/public_clock.py",
    "db/set_aside.py",
    "db/insider_resolution.py",
    "repositories/thesis_repo.py",
    "repositories/decisions_repo.py",
    "repositories/mappers.py",
)

# inside ENGINE_SOURCES but not logic (the module docstring): their VALUES or their entries are what matter
EXCLUDED_SOURCES: frozenset[str] = frozenset({"domain/settings.py", "domain/rule_log.py"})

# the Settings fields the engine's output reads (the module docstring's evidence) — hashed by VALUE
ENGINE_SETTINGS: tuple[str, ...] = ("market_tz", "price_settle_time")


# --- the normalized form ------------------------------------------------------------------------------------


def _is_bare_string(node: ast.AST) -> bool:
    """A statement that is only a string literal: a docstring, or any other no-op string statement."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _canon(node: Any) -> Any:
    """A version-stable serialization of an AST (the module docstring): ``[type, [[field, value], ...]]``,
    fields in ``_fields`` order, a ``None`` or empty-list field skipped, positions never included (they are
    attributes, not fields), and a bare string statement dropped from every statement list."""
    if isinstance(node, ast.AST):
        fields = []
        for name in node._fields:
            value = getattr(node, name, None)
            if isinstance(value, list):
                value = [v for v in value if not _is_bare_string(v)]
            if value is None or (isinstance(value, list) and not value):
                continue
            if name == "kind" and isinstance(node, ast.Constant):
                continue  # the u"" prefix marker: not logic
            fields.append([name, _canon(value)])
        return [type(node).__name__, fields]
    if isinstance(node, list):
        return [_canon(v) for v in node]
    # a constant: its type and its repr (a float's repr is exact and stable across versions)
    return [type(node).__name__, repr(node)]


def normalized_source(text: str) -> str:
    """The logic of one Python file, as a canonical string: comments gone (the AST never sees them), every
    bare string statement (docstrings) removed, serialized by ``_canon``."""
    tree = ast.parse(text)
    return json.dumps(_canon(tree), separators=(",", ":"), ensure_ascii=True)


def engine_files(root: Path = _ROOT, sources: tuple[str, ...] = ENGINE_SOURCES) -> list[Path]:
    files: list[Path] = []
    for s in sources:
        p = root / s
        files.extend(sorted(p.rglob("*.py")) if p.is_dir() else ([p] if p.is_file() else []))
    out = []
    for f in sorted(set(files), key=lambda x: x.relative_to(root).as_posix()):
        rel = f.relative_to(root).as_posix()
        if "__pycache__" in f.parts or rel in EXCLUDED_SOURCES:
            continue
        out.append(f)
    return out


def source_hash(root: Path = _ROOT, sources: tuple[str, ...] = ENGINE_SOURCES) -> str:
    """sha256 over ``(relative path, normalized logic)`` of every engine ``.py`` file, sorted by path."""
    h = hashlib.sha256()
    for f in engine_files(root, sources):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(normalized_source(f.read_text(encoding="utf-8")).encode())
        h.update(b"\0")
    return h.hexdigest()


def settings_values(settings: Any | None = None) -> dict[str, str]:
    s = settings or get_settings()
    out = {}
    for name in ENGINE_SETTINGS:
        v = getattr(s, name)
        out[name] = v.isoformat() if hasattr(v, "isoformat") else str(v)
    return out


def settings_hash(settings: Any | None = None) -> str:
    return hashlib.sha256(
        json.dumps(settings_values(settings), sort_keys=True).encode()
    ).hexdigest()


def engine_fingerprint(cfg: CallConfig) -> dict[str, Any]:
    """``{fingerprint, fingerprint_version, config_hash, source_hash, settings_hash, code_sha}`` —
    ``fingerprint`` (with its version) is the value compared; ``code_sha`` is reported, never compared.
    """
    ch, sh, seth = config_hash(cfg), source_hash(), settings_hash()
    return {
        "fingerprint": hashlib.sha256(
            f"v{FINGERPRINT_VERSION}:{ch}:{sh}:{seth}".encode()
        ).hexdigest(),
        "fingerprint_version": FINGERPRINT_VERSION,
        "config_hash": ch,
        "source_hash": sh,
        "settings_hash": seth,
        "code_sha": get_settings().image_sha,
    }


def fingerprint_version(engine: Mapping[str, Any] | None) -> int:
    """The version a stored engine dict was made by — one without the key predates S7 (version 1)."""
    if not engine:
        return 1
    return int(engine.get("fingerprint_version") or 1)
