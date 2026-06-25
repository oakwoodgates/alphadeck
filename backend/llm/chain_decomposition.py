"""The narrative→chain DECOMPOSE drafter — the SECOND LLM seam (Slice 5b), on the proven `backend/llm`
plumbing the flag drafter (#59) established.

Given an operator's narrative, draft a value chain: 2-6 **segments** (links in the chain), the **names** that
sit in each, and one short thesis-fit **prose** sentence per name. The output is STRUCTURE + NAMES + REASONING
only — it is a drafting aid the operator ratifies, never a decision.

EDGAR-FIRST DISCOVERY (Slice 4): the names no longer come from the model's recall. The deterministic EDGAR
full-text enumerator finds the US-listed universe (``workbench.discovery``); the directed ``research_tail_sweep``
below adds the foreign / brand-new tail EFTS can't see. Their combined synthesis is threaded into
``decompose_narrative`` as ``research_context`` so the model only ORGANIZES a stable, deterministic name set into
value-chain segments — never enumerates it. Research is CONTEXT only — INVARIANT #3 stays structural (the tool
schema below has no number field). Fail-open throughout: with no context the decompose runs recall-only, never an
empty draft; the reconciler (``workbench.chain_draft.resolve_discovered_chain``) then guarantees per-CIK that no
deterministically-found name is dropped by the organizer's layout.

THE BOUNDS (carried from the gate-1 plan):
- **Never a number** (INVARIANT #1/#3). The prompt + tool schema forbid any price / %% / share count / cash /
  runway / market cap / catalyst value; the response carries no value field. This half of the bound rests on
  the PROMPT — Sonnet is the adherence lever, and the gate-2 MANUAL no-number-in-the-prose check is its real
  test (a fake-client unit test cannot exercise a prompt).
- **A name is a discovery suggestion, never a decision** (INVARIANT #2). This module proposes
  ``{name, ticker?, prose}``; exact master membership DECIDES, downstream in ``workbench.chain_draft`` — the
  model's ticker is a best-guess key, never trusted as the id.
- **Fail-open.** Every failure path (no key, live disabled, timeout, SDK error, no tool call, blank
  narrative) returns ``None`` — the draft endpoint then returns an empty draft and hand-authoring is
  untouched.
"""

from __future__ import annotations

from typing import Any

from domain.settings import get_settings
from llm.prompt_loader import load_prompt

# The structured-output contract — the model MUST call this tool; we read back its validated input. STRUCTURE
# + names + reasoning ONLY: there is no value/score/number field anywhere in the schema (INVARIANT #1).
DECOMPOSE_TOOL: dict[str, Any] = {
    "name": "draft_value_chain",
    "description": (
        "Return a value-chain decomposition of an investment narrative: 2-6 segments (links in the chain), "
        "the publicly-listed US companies in each, and one short reasoning sentence per company. Structure, "
        "names, and reasoning ONLY — never a number."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "minItems": 2,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short segment name, e.g. 'Enrichment & fuel'.",
                        },
                        "descriptor": {
                            "type": "string",
                            "description": "Optional one-phrase tag for the link's role in the chain (no numbers).",
                        },
                        "placements": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "The company's common name.",
                                    },
                                    "ticker": {
                                        "type": "string",
                                        "description": (
                                            "Your best-guess US exchange ticker (we verify it against our own "
                                            "master; omit ONLY if you truly have none — never fabricate one)."
                                        ),
                                    },
                                    "prose": {
                                        "type": "string",
                                        "description": (
                                            "At most 25 words: why this company sits in this segment, grounded "
                                            "in the narrative. NO numbers, prices, %, share counts, or valuations."
                                        ),
                                    },
                                },
                                "required": ["name", "prose"],
                            },
                        },
                    },
                    "required": ["label", "placements"],
                },
            }
        },
        "required": ["segments"],
    },
}


def decompose_narrative(
    client: Any, narrative: str, *, research_context: str | None = None
) -> dict[str, Any] | None:
    """Draft a value-chain decomposition from a narrative. Returns the validated tool input
    (``{"segments": [...]}``) or ``None`` on ANY failure — fail-open: no key / live disabled / timeout / SDK
    error / no tool call / blank narrative → ``None`` (the draft endpoint then returns an empty draft, and
    hand-authoring is untouched).

    When ``research_context`` is given (the Slice-1 research pass's synthesis of currently-listed companies),
    it is appended to the user message so the model decomposes using RESEARCHED current names instead of
    training recall — fixing the run-to-run instability + off-thesis drift. It is CONTEXT only: the tool schema
    carries no number field and the prompt forbids numbers, so the chain stays value-free regardless of what the
    research text contains (INVARIANT #3 is structural here, not trust). ``research_context=None`` is exactly
    today's recall-only behavior.

    ``client`` only needs a ``draft_structured(system, user, tool)`` method (the real ``LLMClient`` or a test
    fake). It sources NO number; the no-number bound rests on the schema + prompt — the gate-2 manual check is
    its real test. Parsing/validation of the shape happens downstream
    (``workbench.chain_draft.proposed_from_decomposition``), also fail-open.
    """
    if not narrative or not narrative.strip():
        return None
    # fail-loud: a missing prompt file is a deploy bug, raised HERE (outside the fail-open try below) so it
    # surfaces instead of being swallowed into an empty draft.
    system = load_prompt("chain_decompose")
    user = f"Narrative:\n{narrative.strip()}"
    if research_context and research_context.strip():
        user += (
            "\n\nCurrent research — publicly-listed companies in this space (prefer these CURRENT identities; "
            "this is CONTEXT for name selection, never facts and never numbers):\n"
            + research_context.strip()
        )
    try:
        out = client.draft_structured(system=system, user=user, tool=DECOMPOSE_TOOL)
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return None
    if not isinstance(out, dict):
        return None
    return out


# The narration tool — one reasoning sentence per company, NO number (same #3 bound as the decompose prose).
NARRATE_TOOL: dict[str, Any] = {
    "name": "narrate_placements",
    "description": (
        "For each given US-listed company, return one short reasoning sentence (<=25 words, NO numbers) on why "
        "it fits the investment narrative and its value-chain segment. Reasoning only — never a number."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "placements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The company name, copied EXACTLY from the input list (the join key).",
                        },
                        "prose": {
                            "type": "string",
                            "description": (
                                "At most 25 words: why this company fits the narrative / its segment. NO "
                                "numbers, prices, %, share counts, cash, or valuations."
                            ),
                        },
                    },
                    "required": ["name", "prose"],
                },
            }
        },
        "required": ["placements"],
    },
}


def narrate_placements(client: Any, narrative: str, items: list[dict[str, Any]]) -> dict[str, str]:
    """Draft a per-name thesis-fit sentence for each placement in ``items`` (``{name, ticker?, segment?}``),
    returning ``{name: prose}``. The deterministic EDGAR reconciler appends discovered CIKs the organizer never
    narrated (``prose=""``); this fills that gap so EVERY placed/verify name carries reasoning — without the
    organizer (the reconciler stays deterministic + completeness-owning; this only adds DISPLAY prose).

    FAIL-OPEN + #9-safe: any failure (no key / live disabled / SDK error / no tool call / empty list) returns
    ``{}`` and the affected names keep ``prose=""`` (today's behavior) — a narration failure never drops a name,
    only its prose. Sources NO number (#3 — the tool schema + prompt forbid figures; reasoning only). ``client``
    needs a ``draft_structured(system, user, tool)`` method (the decompose client; the real ``LLMClient`` or a
    test fake)."""
    if not narrative or not narrative.strip() or not items:
        return {}
    system = load_prompt(
        "chain_narrate"
    )  # fail-loud on a missing prompt, outside the fail-open try
    lines: list[str] = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        ticker = f" ({it['ticker']})" if it.get("ticker") else ""
        seg = f" — segment: {it['segment']}" if it.get("segment") else ""
        lines.append(f"- {name}{ticker}{seg}")
    if not lines:
        return {}
    user = (
        f"Narrative:\n{narrative.strip()}\n\n"
        "Companies to narrate (one reasoning sentence each, copy the name exactly, NO numbers):\n"
        + "\n".join(lines)
    )
    try:
        out = client.draft_structured(system=system, user=user, tool=NARRATE_TOOL)
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return {}
    if not isinstance(out, dict):
        return {}
    result: dict[str, str] = {}
    for p in out.get("placements", []) or []:
        if (
            isinstance(p, dict)
            and isinstance(p.get("name"), str)
            and isinstance(p.get("prose"), str)
        ):
            result[p["name"]] = p["prose"]
    return result


def _web_search_tool() -> dict[str, Any]:
    """The server-side web_search tool spec for the research pass, built from Settings. The tool VERSION is a
    CODE-COUPLED capability field (see ``domain/settings.py``) — read here so it is single-source, NOT so it is
    a free env flip; ``max_uses`` is the per-draft search budget (``llm_research_max_searches``)."""
    _s = get_settings()
    return {
        "type": _s.research_web_search_tool,
        "name": "web_search",
        "max_uses": _s.llm_research_max_searches,
    }


def research_tail_sweep(client: Any, narrative: str, found_names: list[str]) -> str | None:
    """The DIRECTED tail-sweep (discovery Slice 3) — the LLM's SECOND bounded job in the EDGAR-first
    architecture. Given the names the EDGAR full-text enumerator already found, web-search the corners EFTS
    STRUCTURALLY can't see — foreign-with-US-listing (ADR/dual) / brand-new IPOs / DBA-or-very-recent-rebrand /
    no-US-filing — for on-thesis, US-tradeable names NOT in the found list. Returns a plain-text synthesis (the
    NEW names + tickers + roles), or ``None`` on any failure (fail-open).

    Framed as a directed sweep, NOT a bare "ignore these" (a bare exclusion makes the model re-list the core and
    stop early). The found list is threaded into the user message. ``client`` only needs a
    ``research(system, user, tool)`` method. It sources NO number (#3); discovery only PROPOSES (#2 — the
    resolver + the secondary name/ticker bridges decide membership, never auto-place).
    """
    if not narrative or not narrative.strip():
        return None
    system = load_prompt("tail_sweep")  # fail-loud on a missing prompt, outside the fail-open try
    found = ", ".join(n for n in found_names if n and n.strip()) or "(none yet)"
    user = (
        f"Narrative:\n{narrative.strip()}\n\n"
        f"Already-found names (do NOT re-list these — find what's MISSING):\n{found}"
    )
    try:
        return client.research(system=system, user=user, tool=_web_search_tool())
    except Exception:  # noqa: BLE001 — no key / live disabled / timeout / SDK error -> fail-open
        return None
