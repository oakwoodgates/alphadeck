"""The Claude Code skill (``.claude/skills/alphadeck-agent/SKILL.md``) carries the five recipes verbatim: the sync check
runs in-process (the same check ``python -m agent_tools.skill_sync --check`` runs), the size and frontmatter bars hold,
and the tracked skill names no live card, company or ticker.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from agent_tools import skill_sync
from agent_tools.prompt_args import PROMPT_NAMES

SKILL = skill_sync.SKILL_PATH
MAX_CHARS = 12_000
MAX_LINES = 250
MAX_HAND_LINES = 80
MAX_DESCRIPTION = 1_000


def _text() -> str:
    return SKILL.read_text(encoding="utf-8").replace("\r\n", "\n")


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "the skill starts with a YAML frontmatter block"
    fields: dict[str, str] = {}
    key = None
    for line in match.group(1).splitlines():
        if re.match(r"^[a-z-]+:", line):
            key, _, value = line.partition(":")
            fields[key] = value.strip()
        elif key is not None:
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields


def test_every_recipe_block_equals_its_prompt_file():
    problems = skill_sync.problems(_text(), skill_sync.prompt_texts())
    assert problems == [], "\n".join(problems)


def test_the_check_command_exits_zero_and_a_one_character_edit_exits_one(tmp_path, capsys):
    assert skill_sync.main(["--check"]) == 0
    edited = _text().replace(
        "exit_by is a signal-validity horizon", "exit_by is a signal-validity horizons", 1
    )
    assert edited != _text()
    broken = tmp_path / "SKILL.md"
    broken.write_text(edited, encoding="utf-8")
    assert skill_sync.main(["--check", "--skill", str(broken)]) == 1
    assert "differs from its prompt file" in capsys.readouterr().err
    assert (
        skill_sync.main(["--write", "--skill", str(broken)]) == 0
    )  # --write puts the recipes back
    assert skill_sync.main(["--check", "--skill", str(broken)]) == 0
    assert (
        broken.read_text(encoding="utf-8").replace("\r\n", "\n") == _text()
    )  # only the blocks changed


def test_a_missing_or_unknown_recipe_block_is_a_problem():
    prompts = skill_sync.prompt_texts()
    text = _text()
    missing = text.replace("<!-- recipe:ticker-check -->", "<!-- recipe:ticker-checked -->", 1)
    found = skill_sync.problems(missing, prompts)
    assert any("ticker-check has no" in p for p in found)
    assert any("ticker-checked" in p and "not a registered prompt" in p for p in found)


def test_the_skill_stays_within_its_size_bars_and_the_hand_written_part_is_short():
    text = _text()
    assert len(text) <= MAX_CHARS, len(text)
    assert len(text.splitlines()) <= MAX_LINES
    inside = sum(
        m.group(0).count("\n") + 1 for m in skill_sync._BLOCK.finditer(text)  # noqa: SLF001
    )
    assert len(text.splitlines()) - inside <= MAX_HAND_LINES, (len(text.splitlines()), inside)


def test_the_frontmatter_names_the_skill_and_each_recipe_and_fits_1000_characters():
    fields = _frontmatter(_text())
    assert set(fields) == {"name", "description", "argument-hint"}
    assert fields["name"] == "alphadeck-agent"
    assert 0 < len(fields["description"]) <= MAX_DESCRIPTION
    description = fields["description"].lower()
    for needle in (
        "morning brief",
        "review the board",
        "explain this call",
        "ticker check",
        "compare the rotation map",
    ):
        assert needle in description, needle
    for name in PROMPT_NAMES:
        assert name in fields["argument-hint"], name
    assert "$ARGUMENTS" in _text()


def test_the_tracked_skill_names_no_live_card_or_company():
    text = _text()
    # only synthetic placeholders appear as examples
    assert "Thesis 01" in text and "TK01" in text
    assert not re.findall(r"\bThesis (?!01\b)\d+", text)


def test_skill_sync_is_pure():
    tree = ast.parse(Path(skill_sync.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"httpx", "mcp", "mcp_types", "anthropic", "pydantic"}, imported
    for node in ast.walk(tree):  # the one relative import is the pure argument module
        if isinstance(node, ast.ImportFrom) and node.level:
            assert node.module == "prompt_args"
