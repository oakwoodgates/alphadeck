"""The ENGINE FINGERPRINT (G3) — which rules produced an edition, in a form a deploy can't fake.

``code_sha`` changes on every image build (a frontend-only deploy included), so it cannot say whether the CALL
LOGIC changed. The fingerprint is the ``CallConfig`` hash plus a hash of the SOURCE of every module the
editions engine's output depends on: the call path (``signals/``, ``calls/``, ``domain/``,
``pipeline/core.py``), the readers and the public-clock definition (``db/bitemporal.py``,
``db/public_clock.py``, ``db/insider_resolution.py``, the repositories it loads through) and the engine itself
(``replay/``, ``editions/``). Content-addressed and sorted by path, so it is identical across processes and
machines for identical source. The automatic cut (S7) compares it; S3 stamps it on every edition.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from domain.config import CallConfig, config_hash
from domain.settings import get_settings

_ROOT = Path(__file__).resolve().parent.parent  # backend/

ENGINE_SOURCES: tuple[str, ...] = (
    "signals",
    "calls",
    "domain",
    "replay",
    "editions",
    "pipeline/core.py",
    "db/bitemporal.py",
    "db/public_clock.py",
    "db/insider_resolution.py",
    "repositories/thesis_repo.py",
    "repositories/decisions_repo.py",
    "repositories/mappers.py",
)


def source_hash(root: Path = _ROOT, sources: tuple[str, ...] = ENGINE_SOURCES) -> str:
    """sha256 over ``(relative path, bytes)`` of every ``.py`` file under ``sources``, sorted by path."""
    files: list[Path] = []
    for s in sources:
        p = root / s
        files.extend(sorted(p.rglob("*.py")) if p.is_dir() else ([p] if p.is_file() else []))
    h = hashlib.sha256()
    for f in sorted(set(files), key=lambda x: x.relative_to(root).as_posix()):
        if "__pycache__" in f.parts:
            continue
        h.update(f.relative_to(root).as_posix().encode())
        h.update(b"\0")
        # a checkout's line endings are not the logic
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()


def engine_fingerprint(cfg: CallConfig) -> dict[str, str | None]:
    """``{fingerprint, config_hash, source_hash, code_sha}`` — ``fingerprint`` is the one value compared."""
    ch, sh = config_hash(cfg), source_hash()
    return {
        "fingerprint": hashlib.sha256(f"{ch}:{sh}".encode()).hexdigest(),
        "config_hash": ch,
        "source_hash": sh,
        "code_sha": get_settings().image_sha,
    }
