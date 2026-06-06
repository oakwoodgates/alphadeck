from __future__ import annotations

import json
from pathlib import Path

from app.main import app

# Written to backend/openapi.json (the repo's API contract); the frontend's `gen:api` reads it.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def export(path: Path | None = None) -> Path:
    """Dump the OpenAPI schema. The frontend generates its TS types from this file (M3b's gen:api)."""
    target = path or DEFAULT_PATH
    # LF + trailing newline so the committed contract is byte-stable across platforms (CI regenerates
    # it and runs `git diff --exit-code`).
    target.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8", newline="\n")
    return target


if __name__ == "__main__":
    print(export())
