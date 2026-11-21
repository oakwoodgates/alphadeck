"""``scripts/check_docs.py``: the stdlib docs checker, run in-process against tiny fixture trees.

Each test builds a throwaway repo root in ``tmp_path`` (a few Markdown files, a code file, a dashboard
``entry.ts``) and calls the checker's functions directly. No subprocess, no database, no network: the
script is loaded from the repo root with importlib, exactly as CI runs it (``python scripts/check_docs.py``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "check_docs.py"

DASH_HEADINGS = (
    "What it shows",
    "Who counts",
    "Data and code",
    "As-of behavior",
    "Rules not visible in code",
    "Known limits",
)


@pytest.fixture(scope="module")
def cd():
    spec = importlib.util.spec_from_file_location("check_docs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_docs"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _found(findings, check=None, file=None, severity=None):
    return [
        f
        for f in findings
        if (check is None or f.check == check)
        and (file is None or f.file == file)
        and (severity is None or f.severity == severity)
    ]


def _keys(findings, check=None, file=None):
    return sorted(f.key for f in _found(findings, check, file))


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _dash_doc(title: str, extra: str = "") -> str:
    body = "".join(f"## {h}\n\nText.\n\n" for h in DASH_HEADINGS)
    return f"# {title}\n\n{extra}\n\n{body}"


# --- slugs and anchors ---------------------------------------------------------------------------------


def test_github_slug_follows_github_rules(cd):
    assert (
        cd.github_slug("CALL_LOGIC.md — How a Call Is Made") == "call_logicmd--how-a-call-is-made"
    )
    assert cd.github_slug("2b. The split-night hold `[BUILT]`") == "2b-the-split-night-hold-built"
    assert cd.github_slug("The `exit_by` horizon **now**") == "the-exit_by-horizon-now"
    assert cd.github_slug("See [the map](MAP.md) here") == "see-the-map-here"


def test_anchors_number_duplicates_and_read_setext_and_html_ids(cd):
    text = '# Top\n\n## Notes\n\n## Notes\n\nSetext title\n------------\n\n<a id="custom"></a>\n'
    anchors = cd.markdown_anchors(text)
    assert {"top", "notes", "notes-1", "setext-title", "custom"} <= anchors


def test_headings_inside_fences_are_not_anchors(cd):
    text = "# Real\n\n```bash\n# not a heading\n```\n"
    assert cd.markdown_anchors(text) == {"real"}


# --- relative links ------------------------------------------------------------------------------------


def test_links_resolve_files_dirs_and_anchors(cd, tmp_path):
    _tree(
        tmp_path,
        {
            "docs/B.md": "# B\n\n## Real part\n",
            "backend/x.py": "x = 1\n",
            "docs/A.md": (
                "# A\n\n## Top heading\n\n"
                "[ok](B.md) [ok](B.md#real-part) [ok](#top-heading) [ok](../backend/) [ok](/docs/B.md)\n"
                "[gone](C.md) [bad anchor](B.md#nope) [bad self](#nope)\n"
                "![img](img/missing.png)\n\n[ref]: Missing.md\n"
                "[ext](https://example.com/x.md) [mail](mailto:a@b.c)\n"
                "`[code](Gone1.md)`\n\n```\n[fenced](Gone2.md)\n```\n"
            ),
        },
    )
    findings = cd.run_checks(tmp_path)
    assert _keys(findings, "links", "docs/A.md") == sorted(
        ["C.md", "B.md#nope", "#nope", "img/missing.png", "Missing.md"]
    )
    assert all(f.severity == "fail" for f in _found(findings, "links"))
    line = next(f.line for f in _found(findings, "links") if f.key == "C.md")
    assert line == 6


def test_links_into_ignored_paths_do_not_resolve(cd, tmp_path):
    _tree(
        tmp_path,
        {
            ".gitignore": "data/\n",
            "docs/temp/plan.md": "# Plan\n",
            "data/cache.md": "# Cache\n",
            "docs/A.md": "# A\n\n[plan](temp/plan.md) [cache](../data/cache.md)\n",
        },
    )
    keys = _keys(cd.run_checks(tmp_path), "links", "docs/A.md")
    assert keys == ["../data/cache.md", "temp/plan.md"]


# --- code pointers -------------------------------------------------------------------------------------


def _pointer_tree(cd, tmp_path: Path, code: dict[str, str]) -> list:
    files = {
        "docs/A.md": "# A\n\n## Real heading here\n\n## Cache freshness\n",
        "docs/DASHBOARDS.md": "# Dashboards\n\n## The rules\n",
        "docs/CALL_LOGIC.md": "# Call logic\n\n## 2. State\n\n### 3c. Thesis catalysts\n",
        "docs/INVARIANTS.md": "# Invariants\n\n## 1. No lookahead\n",
    }
    files.update(code)
    return _run(cd, tmp_path, files)


def _run(cd, tmp_path: Path, files: dict[str, str]) -> list:
    _tree(tmp_path, files)
    return cd.run_checks(tmp_path)


def test_pointer_to_missing_doc_fails(cd, tmp_path):
    findings = _pointer_tree(cd, tmp_path, {"backend/x.py": "# see docs/GONE.md for the rule\n"})
    assert _keys(findings, "pointers") == ["docs/GONE.md"]
    assert _found(findings, "pointers")[0].severity == "fail"


def test_pointer_headings_match_by_case_insensitive_prefix(cd, tmp_path):
    code = (
        "# docs/A.md §Real heading\n"
        "# docs/A.md §real heading here, then prose\n"
        '# docs/A.md ("Real heading")\n'
        '# ``docs/A.md`` "Real heading"\n'
        "# docs/A.md §cache-freshness\n"
        "# INVARIANTS.md §1: each\n"
        "# docs/A.md §Missing part\n"
        '# docs/A.md ("Nope")\n'
    )
    findings = _pointer_tree(cd, tmp_path, {"backend/x.py": code})
    assert _keys(findings, "pointers") == ["docs/A.md §Missing part", "docs/A.md §Nope"]


def test_pointer_headings_include_bold_run_in_heads_and_loose_forms(cd, tmp_path):
    files = {
        "docs/R.md": (
            "# R\n\n## The Settings page\n\n## ⚠️ KNOWN LIMITATION — the roster\n\n"
            "**What the hash promises.** Text.\n\n- **No silent loudness** — text.\n"
        ),
        "backend/x.py": (
            "# docs/R.md §Settings page\n"
            "# docs/R.md §Known limitation\n"
            '# docs/R.md §"What the hash promises"\n'
            '# docs/R.md "no silent loudness"\n'
            "# docs/R.md §The Settings page (where a user sets the webhook)\n"
            "# docs/R.md §Gone heading\n"
        ),
    }
    assert _keys(_run(cd, tmp_path, files), "pointers") == ["docs/R.md §Gone heading"]


def test_pointer_dashboard_rules_and_call_logic_sections(cd, tmp_path):
    code = (
        "// docs/DASHBOARDS.md rule 9\n"
        "// ``docs/DASHBOARDS.md`` rule 7\n"
        "// docs/DASHBOARDS.md rule 10\n"
        "// docs/CALL_LOGIC.md §3c\n"
        "// CALL_LOGIC.md §2\n"
        "// docs/CALL_LOGIC.md §12\n"
    )
    findings = _pointer_tree(cd, tmp_path, {"frontend/src/x.ts": code})
    assert _keys(findings, "pointers") == ["docs/CALL_LOGIC.md §12", "docs/DASHBOARDS.md rule 10"]


def test_pointer_reads_json_escapes(cd, tmp_path):
    body = '{"d": "see docs/A.md \\u00a7Real heading", "e": "docs/A.md (\\"Nope\\")"}\n'
    findings = _pointer_tree(cd, tmp_path, {"backend/openapi.json": body})
    assert _keys(findings, "pointers") == ["docs/A.md §Nope"]


def test_pointer_findings_in_migrations_and_archive_are_report_only(cd, tmp_path):
    files = {
        "backend/db/migrations/0001_x.sql": "-- docs/GONE.md\n",
        "docs/archive/RECORD.md": "# Record\n\nThe old docs/GONE.md had the steps.\n",
    }
    findings = _pointer_tree(cd, tmp_path, files)
    pointers = _found(findings, "pointers")
    assert sorted(f.file for f in pointers) == [
        "backend/db/migrations/0001_x.sql",
        "docs/archive/RECORD.md",
    ]
    assert {f.severity for f in pointers} == {"report"}


def test_pointer_mentions_in_docs_are_checked_once(cd, tmp_path):
    files = {
        "docs/B.md": (
            "# B\n\nSee `docs/GONE.md` and [GONE.md](GONE.md).\n"
            "Read it with `git show 8094406a:docs/OLD.md`. README.md and SKILL.md are names.\n"
        ),
    }
    findings = _pointer_tree(cd, tmp_path, files)
    assert _keys(findings, "pointers", "docs/B.md") == ["docs/GONE.md"]
    assert _keys(findings, "links", "docs/B.md") == ["GONE.md"]


# --- banned patterns -----------------------------------------------------------------------------------


def _banned(cd, tmp_path, text: str, path: str = "docs/A.md") -> list[str]:
    _tree(tmp_path, {path: text})
    return _keys(cd.run_checks(tmp_path), "banned", path)


def _ids(keys: list[str]) -> list[str]:
    return sorted(k.split(":")[0] for k in keys)


def test_status_tags_fail(cd, tmp_path):
    text = "# A\n\n## Grade `[PINNED]`\n\n## Inputs `[built — insider]`\n\nShipped [BUILT — x].\n"
    keys = _banned(cd, tmp_path, text)
    assert _ids(keys) == ["status-tag"] * 3


def test_pr_references_fail(cd, tmp_path):
    text = "# A\n\nMerged in PR #12. Fixed by #123 (#61). Invariant #10 and WB#1 stay.\n"
    assert _ids(_banned(cd, tmp_path, text)) == ["pr-ref"] * 3


def test_hygiene_patterns_fail(cd, tmp_path):
    text = (
        "# A\n\nSee docs/temp/plan.md and FEED_LOOP.md:45 and `CALL_LOGIC.md:45–58`.\n"
        "Path C:/Users/me/x and `postgresql://u:secret@host/db`.\n"
    )
    assert _ids(_banned(cd, tmp_path, text)) == [
        "docs-temp",
        "line-number",
        "line-number",
        "local-path",
        "local-path",
    ]


def test_code_line_numbers_warn(cd, tmp_path):
    _tree(
        tmp_path,
        {"docs/A.md": "# A\n\nThe list is at `registry.ts:32`; the host is localhost:8000.\n"},
    )
    found = _found(cd.run_checks(tmp_path), "banned", "docs/A.md")
    assert [(f.severity, f.key) for f in found] == [("warn", "line-number-code: registry.ts:32")]


def test_status_and_slop_phrases_fail_outside_code(cd, tmp_path):
    text = (
        "# A\n\nMEASURED 300 s. Choices to confirm. Not yet rehearsed.\n"
        "A load-bearing rule, honest by construction, is the spine.\n"
        "Quoted as `load-bearing` is a mention.\n"
    )
    assert _ids(_banned(cd, tmp_path, text)) == ["slop-phrase"] * 3 + ["status-phrase"] * 3


def test_archive_is_exempt_from_content_rules_but_not_hygiene(cd, tmp_path):
    text = "# R\n\nPR #123, load-bearing, MEASURED, since R8. See docs/temp/x.md.\n"
    assert _ids(_banned(cd, tmp_path, text, "docs/archive/R.md")) == ["docs-temp"]


def test_codenames_warn_outside_code_and_skip_form_names(cd, tmp_path):
    text = (
        "# A\n\nSince R8 and W7a the pass runs. `R9` is code.\n"
        "Forms 8-K, S-1, 13D/G, 10-K, DEF 14A and N-1A; the V2 release; Q3 filings.\n"
        "See [the R2 link](https://example.com/X9).\n"
    )
    _tree(tmp_path, {"docs/A.md": text})
    found = _found(cd.run_checks(tmp_path), "banned", "docs/A.md")
    assert sorted(f.key for f in found) == ["codename: R2", "codename: R8", "codename: W7a"]
    assert {f.severity for f in found} == {"warn"}


def test_em_dash_density_and_stacked_emphasis_warn(cd, tmp_path):
    dashes = "# A\n\n" + " — ".join([_words(10)] * 6) + "\n"
    keys = _banned(cd, tmp_path, dashes)
    assert _ids(keys) == ["em-dash"]
    loud = (
        "# B\n\nThe pass **NEVER** writes, **ONLY** reads, and ALWAYS stops. " + _words(80) + "\n"
    )
    assert _ids(_banned(cd, tmp_path, loud, "docs/B.md")) == ["emphasis"]
    calm = "# C\n\n" + " — ".join([_words(40)] * 2) + " **Bold** once.\n"
    assert _banned(cd, tmp_path, calm, "docs/C.md") == []
    sql = "# D\n\nA dump with NO DROP or DELETE IN it replays only into an EMPTY database.\n"
    assert _banned(cd, tmp_path, sql, "docs/D.md") == []


def test_unauthenticated_curl_to_app_routes_fails(cd, tmp_path):
    text = (
        "# A\n\n```bash\n"
        "curl http://localhost:8000/admin/status\n"
        'curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/admin/status\n'
        "curl http://localhost:8001/health\n"
        "curl -o /dev/null -w '%{http_code}' http://localhost:8080/api/health\n"
        "curl https://www.sec.gov/cgi-bin/browse-edgar\n"
        "curl -X POST http://127.0.0.1:8001/workbench/theses/1/draft-chain \\\n"
        '  -H "Authorization: Bearer $TOKEN"\n'
        "```\n\nOr `curl -X POST http://localhost:8001/workbench/x`.\n"
    )
    keys = _banned(cd, tmp_path, text, ".claude/skills/s/SKILL.md")
    assert _ids(keys) == ["curl-noauth", "curl-noauth"]


def test_recipe_blocks_are_excluded(cd, tmp_path):
    text = (
        "---\nname: alphadeck-agent\ndescription: Use when reading.\n---\n\n# Agent\n\n"
        "<!-- recipe:morning-brief -->\nPR #123 is load-bearing.\n<!-- /recipe -->\n\n"
        "Hand-written: PR #456.\n"
    )
    path = ".claude/skills/alphadeck-agent/SKILL.md"
    assert _ids(_banned(cd, tmp_path, text, path)) == ["pr-ref"]


# --- budgets -------------------------------------------------------------------------------------------


def _dash_tree(cd, tmp_path: Path, words: int) -> list:
    _tree(
        tmp_path,
        {
            "frontend/src/dashboards/x/entry.ts": "export {}\n",
            "docs/dashboards/x.md": _dash_doc("X", _words(words)),
        },
    )
    return _run(cd, tmp_path, {})


def test_dashboard_budget_warns_at_soft_and_fails_at_hard(cd, tmp_path):
    soft = _found(_dash_tree(cd, tmp_path / "soft", 420), "budgets")
    assert [(f.severity, f.key) for f in soft] == [("warn", "dashboard: over soft budget 400")]
    hard = _found(_dash_tree(cd, tmp_path / "hard", 520), "budgets")
    assert [(f.severity, f.key) for f in hard] == [("fail", "dashboard: over hard budget 500")]


def _always_loaded(cd, root: Path, claude_words: int, desc_words: int) -> list[tuple[str, str]]:
    skill = "---\nname: s\ndescription: >-\n  " + _words(desc_words) + "\n---\n\n# S\n"
    _tree(root, {"CLAUDE.md": _words(claude_words), ".claude/skills/s/SKILL.md": skill})
    findings = _found(cd.run_checks(root), "budgets", "CLAUDE.md")
    return [(f.severity, f.key) for f in findings if f.key.startswith("always-loaded")]


def test_always_loaded_total_counts_root_claude_md_and_descriptions(cd, tmp_path):
    assert _always_loaded(cd, tmp_path / "under", 2400, 40) == []
    assert _always_loaded(cd, tmp_path / "soft", 3170, 40) == [
        ("warn", "always-loaded: over soft budget 3200")
    ]
    assert _always_loaded(cd, tmp_path / "hard", 3270, 50) == [
        ("fail", "always-loaded: over hard budget 3300")
    ]


# --- dashboard parity ----------------------------------------------------------------------------------


def test_dashboard_parity(cd, tmp_path):
    files = {
        "frontend/src/dashboards/good/entry.ts": "export {}\n",
        "docs/dashboards/good.md": _dash_doc("Good"),
        "frontend/src/dashboards/nodoc/entry.ts": "export {}\n",
        "docs/dashboards/orphan.md": _dash_doc("Orphan"),
        "frontend/src/dashboards/thin/entry.ts": "export {}\n",
        "docs/dashboards/thin.md": "# Thin\n\n## What it shows\n\n## Known limits\n",
        "frontend/src/dashboards/board/entry.ts": "export {}\n",
        "docs/BOARD.md": "# Board\n",
        "frontend/src/dashboards/__tests__/x.test.ts": "\n",
    }
    findings = _found(_run(cd, tmp_path, files), "parity")
    got = sorted((f.file, f.key) for f in findings)
    assert got == sorted(
        [
            ("frontend/src/dashboards/nodoc/entry.ts", "missing doc docs/dashboards/nodoc.md"),
            ("docs/dashboards/orphan.md", "missing entry frontend/src/dashboards/orphan/entry.ts"),
            ("docs/dashboards/thin.md", "missing heading: Who counts"),
            ("docs/dashboards/thin.md", "missing heading: Data and code"),
            ("docs/dashboards/thin.md", "missing heading: As-of behavior"),
            ("docs/dashboards/thin.md", "missing heading: Rules not visible in code"),
        ]
    )
    assert {f.severity for f in findings} == {"fail"}


def test_board_without_its_doc_fails(cd, tmp_path):
    findings = _found(
        _run(cd, tmp_path, {"frontend/src/dashboards/board/entry.ts": "x\n"}), "parity"
    )
    assert [f.key for f in findings] == ["missing doc docs/BOARD.md"]


# --- skill front matter --------------------------------------------------------------------------------


def test_skill_front_matter(cd, tmp_path):
    files = {
        ".claude/skills/good/SKILL.md": "---\nname: good\ndescription: >-\n  Use when testing.\n---\n# G\n",
        ".claude/skills/wrong/SKILL.md": "---\nname: other\ndescription: Use when testing.\n---\n# W\n",
        ".claude/skills/long/SKILL.md": (
            "---\nname: long\ndescription: >-\n  "
            + _words(30)
            + "\n  "
            + _words(31)
            + "\n---\n# L\n"
        ),
        ".claude/skills/bare/SKILL.md": "# No front matter\n",
        ".claude/agents/builder.md": "---\nname: builder\ndescription: Builds.\n---\n# B\n",
    }
    findings = _found(_run(cd, tmp_path, files), "frontmatter")
    got = sorted((f.file, f.key) for f in findings)
    assert got == [
        (".claude/skills/bare/SKILL.md", "missing front matter"),
        (".claude/skills/long/SKILL.md", "description over 60 words"),
        (".claude/skills/wrong/SKILL.md", "name 'other' is not the folder 'wrong'"),
    ]
    assert {f.severity for f in findings} == {"fail"}


def test_front_matter_parser_reads_folded_and_quoted_values(cd):
    text = '---\nname: "x"\ndescription: >-\n  Use when a.\n  Then b.\nother: 1\n---\nbody\n'
    meta, body = cd.split_front_matter(text)
    assert meta == {"name": "x", "description": "Use when a. Then b.", "other": "1"}
    assert body == "body\n"


# --- archive citations ---------------------------------------------------------------------------------


def test_docs_outside_archive_may_not_link_into_it(cd, tmp_path):
    files = {
        "docs/archive/R.md": "# R\n\n[other](S.md)\n",
        "docs/archive/S.md": "# S\n",
        "docs/A.md": "# A\n\n[record](archive/R.md)\n",
        "backend/x.py": "# evidence: docs/archive/R.md\n",
    }
    findings = _found(_run(cd, tmp_path, files), "archive")
    assert [(f.file, f.key, f.severity) for f in findings] == [
        ("docs/A.md", "docs/archive/R.md", "fail")
    ]


# --- scope ---------------------------------------------------------------------------------------------


def test_scope_skips_scratch_prompts_worktrees_and_vendored_trees(cd, tmp_path):
    bad = "# X\n\nPR #123 is load-bearing. [gone](NOPE.md)\n"
    files = {
        "docs/temp/x.md": bad,
        "backend/llm/prompts/p.md": bad,
        "backend/tests/fixtures/f/README.md": bad,
        ".claude/worktrees/w/docs/A.md": bad,
        "frontend/node_modules/pkg/README.md": bad,
        ".claude/worktrees/w/backend/x.py": "# docs/GONE.md\n",
        "backend/securities/business_type/README.md": bad,
    }
    findings = _run(cd, tmp_path, files)
    assert {f.file for f in findings} == {"backend/securities/business_type/README.md"}


def test_gitignored_files_are_not_scanned(cd, tmp_path):
    files = {
        ".gitignore": "*.log\nscratch/\n",
        "backend/run.log": "docs/GONE.md\n",
        "backend/scratch/x.py": "# docs/GONE.md\n",
        "backend/y.py": "# docs/GONE.md\n",
    }
    findings = _run(cd, tmp_path, files)
    assert sorted(f.file for f in _found(findings, "pointers")) == ["backend/y.py"]


# --- baseline and CLI ----------------------------------------------------------------------------------


def _main(cd, root: Path, *extra: str) -> int:
    return cd.main(["--root", str(root), "--baseline", str(root / "baseline.json"), *extra])


def test_baseline_gates_only_new_fail_findings(cd, tmp_path, capsys):
    _tree(tmp_path, {"docs/A.md": "# A\n\n[gone](GONE.md)\n"})
    assert _main(cd, tmp_path, "--write-baseline") == 0
    saved = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    assert saved["findings"] == [
        {"file": "docs/A.md", "check": "links", "severity": "fail", "key": "GONE.md", "count": 1}
    ]
    assert _main(cd, tmp_path) == 0

    # moving the baselined line down does not make it new: keys never carry line numbers
    _tree(tmp_path, {"docs/A.md": "# A\n\nIntro.\n\nMore.\n\n[gone](GONE.md)\n"})
    assert _main(cd, tmp_path) == 0

    # a warning alone never fails the run
    _tree(tmp_path, {"docs/B.md": "# B\n\nSince R8 it runs.\n"})
    assert _main(cd, tmp_path) == 0

    _tree(tmp_path, {"docs/B.md": "# B\n\n[new](NEW.md)\n"})
    capsys.readouterr()
    assert _main(cd, tmp_path) == 1
    out = capsys.readouterr().out
    assert "docs/B.md:3: fail [links]" in out
    assert "GONE.md" not in out


def test_baseline_counts_repeats(cd, tmp_path):
    _tree(tmp_path, {"docs/A.md": "# A\n\n[gone](GONE.md)\n"})
    assert _main(cd, tmp_path, "--write-baseline") == 0
    _tree(tmp_path, {"docs/A.md": "# A\n\n[gone](GONE.md) and again [gone](GONE.md)\n"})
    assert _main(cd, tmp_path) == 1


def test_missing_baseline_means_every_fail_is_new(cd, tmp_path):
    _tree(tmp_path, {"docs/A.md": "# A\n\n[gone](GONE.md)\n"})
    assert _main(cd, tmp_path) == 1


def test_all_prints_baselined_findings_and_summary_counts_per_check(cd, tmp_path, capsys):
    _tree(tmp_path, {"docs/A.md": "# A\n\n[gone](GONE.md) PR #123\n"})
    assert _main(cd, tmp_path, "--write-baseline") == 0
    capsys.readouterr()
    assert _main(cd, tmp_path) == 0
    quiet = capsys.readouterr().out
    assert "GONE.md" not in quiet
    assert _main(cd, tmp_path, "--all") == 0
    out = capsys.readouterr().out
    assert "docs/A.md:3: fail [links]" in out
    assert "docs/A.md:3: fail [banned]" in out
    summary = [line for line in out.splitlines() if line.startswith("check_docs:")]
    assert len(summary) == 1
    for check in ("links", "pointers", "banned", "budgets", "parity", "frontmatter", "archive"):
        assert check in summary[0]
    assert "links 1" in summary[0] and "banned 1" in summary[0]


def test_clean_tree_passes_with_no_findings(cd, tmp_path, capsys):
    _tree(tmp_path, {"docs/A.md": "# A\n\nPlain words. [b](B.md)\n", "docs/B.md": "# B\n"})
    assert cd.run_checks(tmp_path) == []
    assert _main(cd, tmp_path) == 0
    assert "check_docs: 0 findings" in capsys.readouterr().out
