"""Load the externalized LLM system prompts from files (refactor Slice 3).

The prose ``SYSTEM_PROMPT``s live as files (``backend/llm/prompts/*.md``) so a prompt tweak is a reviewable
one-file PR, not a code edit + redeploy. The tool schemas (``DECOMPOSE_TOOL`` / ``EXPLAIN_TOOL``) and the
user-message builders STAY in code — they're structured logic that should break loudly on a typo.

**Fail-LOUD:** a missing prompt file is a DEPLOY bug, so this raises ``PromptNotFound`` — deliberately NOT the
seams' API-key fail-open. The seams call ``load_prompt`` OUTSIDE their fail-open ``try``, so a missing prompt
surfaces immediately instead of silently drafting nothing.

**Newlines normalized:** CRLF/CR -> LF and leading/trailing newlines stripped, so a file saved with Windows
line endings (or an editor-added trailing newline) can't change the prompt the model sees. The golden hash in
the tests is computed on THIS function's output, so it pins the model-visible bytes, not the on-disk bytes.

Cached after first read; with ``Settings.prompt_reload`` set, re-read per call (dev iteration, no restart).
The files ship via the editable install (``COPY . .`` + ``pip install -e .``), read relative to ``__file__``,
exactly like ``seed_data/`` — no ``package_data`` needed (a non-editable wheel build would be the exception).
"""

from __future__ import annotations

from pathlib import Path

from domain.settings import get_settings

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_cache: dict[str, str] = {}


class PromptNotFound(RuntimeError):
    """A prompt file is missing — a deploy error, deliberately NOT fail-open (distinct from a missing key)."""


def _normalize(raw: str) -> str:
    """CRLF/CR -> LF, then strip leading/trailing newlines — so a line-ending or trailing-newline artifact
    can't drift the prompt the model receives. (Strips only newline chars, never content or other whitespace.)
    """
    return raw.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def load_prompt(name: str) -> str:
    """Return the system prompt ``name`` (``backend/llm/prompts/{name}.md``), newline-normalized.

    Cached after first read unless ``Settings.prompt_reload`` is set. Raises ``PromptNotFound`` if the file is
    missing (a deploy bug — callers MUST invoke this OUTSIDE their fail-open path so it isn't swallowed).
    """
    reload = get_settings().prompt_reload
    if not reload and name in _cache:
        return _cache[name]
    path = _PROMPTS_DIR / f"{name}.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptNotFound(f"prompt file not found: {path}") from exc
    text = _normalize(raw)
    if not reload:
        _cache[name] = text
    return text
