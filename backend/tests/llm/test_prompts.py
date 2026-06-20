"""Slice 3 — the externalized LLM system prompts + their loader.

Two DURABLE guards (note: the existing LLM seam tests pin the TOOL dict, NOT the system-prompt string, so
prompt bytes were never tested — this is a NEW guard, not a preserved one):
- a frozen **sha256 of load_prompt()'s OUTPUT** — the model-visible bytes; a stray newline / CRLF / edit flips
  it (captured mechanically at cutover, where load_prompt(name) == the old SYSTEM_PROMPT constant was True),
- a prompt-**CONTRACT** check — the load-bearing guards (the no-number rule for decompose; grounding +
  direction-only for explain) are present in the LOADED prompt, so "easy to edit" can't silently drop a guard.
Plus the loader contract: CRLF/edge-newline normalization, fail-LOUD on a missing file, the prompt_reload re-read.
"""

from __future__ import annotations

import hashlib

import pytest

from domain.settings import get_settings
from llm import prompt_loader
from llm.prompt_loader import PromptNotFound, _normalize, load_prompt

# Frozen golden — sha256 of load_prompt()'s output, captured at cutover (load == the old SYSTEM_PROMPT
# constant verified True). An intended prompt change re-captures this DELIBERATELY and re-checks the guards.
_GOLDEN = {
    "chain_decompose": (
        "8682047909dd7e1d64140ad0f31967fd68a107a5e944429bc24a340384543b7d",
        1277,
    ),
    "flag_explain": (
        "d4e4d6b4ebbbc49ebea54c3d5890d511bed3ae660573896d62f9cb350494fe69",
        948,
    ),
}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.delenv("ALPHADECK_PROMPT_RELOAD", raising=False)
    prompt_loader._cache.clear()
    get_settings.cache_clear()
    yield
    prompt_loader._cache.clear()
    get_settings.cache_clear()


# --- the frozen-hash byte guard ---


@pytest.mark.parametrize("name", ["chain_decompose", "flag_explain"])
def test_loaded_prompt_matches_frozen_hash(name):
    loaded = load_prompt(name)
    digest = hashlib.sha256(loaded.encode("utf-8")).hexdigest()
    assert (digest, len(loaded)) == _GOLDEN[name]


# --- the prompt-contract semantic guard ---


def test_decompose_prompt_keeps_the_no_number_guard():
    """INVARIANT #3: the chain drafter's prompt must FORBID numbers — externalizing must not drop it."""
    p = load_prompt("chain_decompose")
    assert "FORBIDDEN from emitting ANY number" in p
    assert "draft_value_chain tool" in p  # still instructs the forced structured-tool call


def test_explain_prompt_keeps_the_grounding_and_direction_only_guards():
    p = load_prompt("flag_explain")
    assert "Use ONLY facts in the provided passage" in p  # grounding
    assert (
        "Do NOT compute or state any final adjusted value" in p
    )  # direction-only (no final value)
    assert "flag_explanation tool" in p


# --- the loader contract ---


def test_normalize_handles_crlf_and_edge_newlines():
    assert _normalize("\n\nline one\r\nline two\r\n\n") == "line one\nline two"
    assert _normalize("\rmac\rstyle\r") == "mac\nstyle"


def test_missing_prompt_fails_loud(tmp_path, monkeypatch):
    """A missing prompt is a DEPLOY bug — load_prompt RAISES (deliberately NOT the seams' fail-open)."""
    monkeypatch.setattr(prompt_loader, "_PROMPTS_DIR", tmp_path)
    prompt_loader._cache.clear()
    with pytest.raises(PromptNotFound):
        load_prompt("does_not_exist")


def test_prompt_reload_controls_caching(tmp_path, monkeypatch):
    """Cached after first read; with prompt_reload set, re-read per call (dev iteration, no restart)."""
    monkeypatch.setattr(prompt_loader, "_PROMPTS_DIR", tmp_path)
    prompt_loader._cache.clear()
    probe = tmp_path / "probe.md"
    probe.write_text("first", encoding="utf-8", newline="\n")
    assert load_prompt("probe") == "first"
    probe.write_text("second", encoding="utf-8", newline="\n")
    assert load_prompt("probe") == "first"  # cached — NOT re-read
    monkeypatch.setenv("ALPHADECK_PROMPT_RELOAD", "true")
    get_settings.cache_clear()
    assert load_prompt("probe") == "second"  # reload flag -> re-read
