"""Keep the Claude Code skill equal to the prompts: ``python -m agent_tools.skill_sync --check | --write``.

A Claude Code session cannot run an MCP prompt (prompts are user-typed ``/mcp__<server>__<prompt>`` commands), so the
repository's ``alphadeck-agent`` skill carries each recipe as a block, embedded verbatim from ``prompts/<name>.md``::

    <!-- recipe:morning-brief -->
    ...the prompt file...
    <!-- /recipe -->

``--check`` exits 1 with a unified diff when a block differs from its file, a registered prompt (``PROMPT_NAMES``) has
no block, or a block names no prompt. ``--write`` regenerates the text between the markers in place; everything
hand-written stays. Pure: stdlib only, no I/O beyond the files named here (no client, server, httpx or mcp).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

from .prompt_args import PROMPT_NAMES

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SKILL_PATH = (
    Path(__file__).resolve().parents[2] / ".claude" / "skills" / "alphadeck-agent" / "SKILL.md"
)
_BLOCK = re.compile(
    r"(<!-- recipe:(?P<name>[\w-]+) -->\n)(?P<body>.*?)(\n<!-- /recipe -->)", re.DOTALL
)


def _norm(text: str) -> str:
    """A recipe as the skill holds it: LF line ends, no trailing newline."""
    return text.replace("\r\n", "\n").rstrip("\n")


def prompt_texts(prompts_dir: Path = PROMPTS_DIR) -> dict[str, str]:
    return {
        name: _norm((prompts_dir / f"{name}.md").read_text(encoding="utf-8"))
        for name in PROMPT_NAMES
    }


def problems(skill_text: str, prompts: dict[str, str]) -> list[str]:
    """What is out of step: a block that differs (as a unified diff), a prompt with no block, a block with no prompt."""
    skill = skill_text.replace("\r\n", "\n")
    found = {m.group("name"): m.group("body") for m in _BLOCK.finditer(skill)}
    out: list[str] = []
    for name in PROMPT_NAMES:
        if name not in found:
            out.append(f"prompt {name} has no <!-- recipe:{name} --> block in the skill")
        elif found[name] != prompts[name]:
            diff = difflib.unified_diff(
                prompts[name].splitlines(),
                found[name].splitlines(),
                fromfile=f"prompts/{name}.md",
                tofile=f"SKILL.md recipe:{name}",
                lineterm="",
            )
            out.append(f"recipe {name} differs from its prompt file:\n" + "\n".join(diff))
    for name in found:
        if name not in PROMPT_NAMES:
            out.append(f"the skill has a recipe block for {name}, which is not a registered prompt")
    return out


def rewrite(skill_text: str, prompts: dict[str, str]) -> str:
    """The skill with each known block's body replaced by its prompt file (hand-written text untouched)."""

    def swap(m: re.Match[str]) -> str:
        name = m.group("name")
        body = prompts.get(name, m.group("body"))
        return f"{m.group(1)}{body}{m.group(4)}"

    return _BLOCK.sub(swap, skill_text.replace("\r\n", "\n"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Keep the alphadeck-agent skill equal to the prompts."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="Exit 1 with a diff when the skill is out of step."
    )
    mode.add_argument("--write", action="store_true", help="Regenerate the recipe blocks in place.")
    parser.add_argument(
        "--skill", type=Path, default=SKILL_PATH, help="The skill file (default: the repository's)."
    )
    args = parser.parse_args(argv)
    if not args.skill.is_file():
        print(f"skill file not found: {args.skill}", file=sys.stderr)
        return 1
    prompts = prompt_texts()
    text = args.skill.read_text(encoding="utf-8")
    if args.write:
        args.skill.write_text(rewrite(text, prompts), encoding="utf-8", newline="\n")
        text = args.skill.read_text(encoding="utf-8")
    issues = problems(text, prompts)
    for issue in issues:
        print(issue, file=sys.stderr)
    if issues:
        print("run: python -m agent_tools.skill_sync --write", file=sys.stderr)
        return 1
    print(f"OK: {len(PROMPT_NAMES)} recipes equal their prompt files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
