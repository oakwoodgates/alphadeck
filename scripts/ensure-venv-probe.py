"""The venv-readiness probe shared by ``scripts/ensure-venv.sh`` and ``scripts/ensure-venv.ps1``.

Run it WITH the venv's own interpreter, passing the checkout's ``backend/`` dir::

    backend/.venv/Scripts/python.exe scripts/ensure-venv-probe.py backend/

Exit 0 = ready (prints a one-line ``ready: ...``), exit 1 = not ready (prints WHY, one line). "Ready" means:

* the interpreter is >= 3.11 (``requires-python``);
* every import the suite needs is present -- the ``dev`` extra (pytest, pytest-xdist for ``pytest -n 6``,
  pytest-timeout the hang guard, the pinned ruff/black), the ``replay`` extra (duckdb + pyarrow, so the
  replay tests EXECUTE instead of being collect-skipped -- skipped != passed) and the runtime deps;
* the editable ``app`` package resolves to THIS checkout's ``backend/app`` -- a venv copied or borrowed from
  another worktree (or the main checkout) fails here, because its editable install points at the OTHER tree.

``sys.path[0]`` is this ``scripts/`` dir (never ``backend/``), so ``import app`` can only succeed through the
venv's editable install -- cwd shadowing (which makes a borrowed venv *look* like it works) is out of the picture.
Pure stdlib; it must run in a venv that is missing things.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# import name -> which extra / group provides it (for the "missing:" message)
REQUIRED: dict[str, str] = {
    "pytest": "dev",
    "xdist": "dev (pytest-xdist -- `pytest -n 6`)",
    "pytest_timeout": "dev (pytest-timeout -- the hang guard)",
    "ruff": "dev",
    "black": "dev",
    "duckdb": "replay",
    "pyarrow": "replay",
    "fastapi": "runtime",
    "psycopg": "runtime",
    "tzdata": "runtime",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ensure-venv-probe.py <path-to-backend-dir>")
        return 2
    backend = Path(argv[1])
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info < (3, 11):
        print(
            f"python {version} < 3.11 (requires-python) -- recreate the venv with a newer interpreter"
        )
        return 1
    missing = [f"{name} [{origin}]" for name, origin in REQUIRED.items() if _absent(name)]
    if missing:
        print("missing: " + ", ".join(missing))
        return 1
    try:
        import app  # the editable install of THIS checkout's backend/ (see the module docstring)
    except ImportError as exc:
        print(f"editable install missing (`import app` failed: {exc})")
        return 1
    got = Path(app.__file__).resolve().parent
    want = (backend / "app").resolve()
    if not (want.is_dir() and got.samefile(want)):
        print(
            f"editable `app` resolves to {got}, not {want} -- a borrowed/copied venv; re-point it"
        )
        return 1
    print(f"ready: python {version}, app -> {got}")
    return 0


def _absent(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):  # a half-installed package can raise here; treat as absent
        return True


if __name__ == "__main__":
    sys.exit(main(sys.argv))
