# Alpha Deck — backend

Python project root. Packages land per the build plan:

- `domain/` — the spine: `Thesis`, `SignalEvent`, `CallCard`, `CallConfig` (Pydantic).
- `calls/` — the **call-assembler**: pure, deterministic, golden-tested `assemble_call(...)`.
- (M1+) `db/`, `securities/`, `ingest/`, `signals/`, `repositories/`, `pipeline/`, `app/`, `llm/`, `replay/`.

## Dev setup

`uv` is not installed on this machine, so we use a stdlib venv + pip (pyproject stays the source of truth).

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install pydantic pytest ruff black
```

## Run (from the repo root)

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m pytest backend\tests      # golden tests
backend\.venv\Scripts\python -m ruff check backend         # lint
backend\.venv\Scripts\python -m black --check backend      # format check
```
