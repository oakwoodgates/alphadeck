from __future__ import annotations

import json
from pathlib import Path

from app.main import app

# Written to backend/openapi.json (the repo's API contract); the frontend's `gen:api` reads it.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def export(path: Path | None = None) -> Path:
    """Dump the OpenAPI schema. The frontend generates its TS types from this file (M3b's gen:api)."""
    target = path or DEFAULT_PATH
    target.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(export())
