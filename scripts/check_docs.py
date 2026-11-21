#!/usr/bin/env python3
"""Check the repo's Markdown against the docs standard (``docs/DOCS_STANDARD.md``).

Stdlib only: no project imports, no database, no network, no subprocess. It walks the filesystem from the
repo root instead of asking git: it skips ``.git``, every path the ``.gitignore`` files ignore, and always
``node_modules``, virtualenvs, ``.claude/worktrees`` and ``docs/temp``, so a local run and a CI checkout
see the same files.

Scope (the Markdown checks): ``README.md``, ``CLAUDE.md`` and every nested ``CLAUDE.md``, ``docs/**/*.md``,
``.claude/skills/**/SKILL.md``, ``.claude/agents/*.md`` and ``backend/securities/business_type/README.md``.
Prompt templates and test-fixture READMEs are out of scope, and generated recipe blocks (between
``<!-- recipe:NAME -->`` and ``<!-- /recipe -->``) are skipped.

Checks and severities:

- ``links`` (fail): every relative link resolves to a file or folder in the walk; every ``#anchor``
  matches a heading slug in the target (GitHub slug rules) or an HTML id.
- ``pointers`` (fail; report-only under ``backend/db/migrations/`` and ``docs/archive/``): every
  ``docs/<path>.md`` named in a code file or in a doc's text exists (a bare upper-case ``<NAME>.md``
  means ``docs/<NAME>.md``); a cited heading (section sign, ``("Heading")`` or a quoted name after the
  path) matches a heading in the target by case-insensitive prefix, slug or leading number; a
  DASHBOARDS rule number is 1-9.
- ``banned`` (fail or warn per pattern, see ``BANNED_PATTERNS``): the archive is exempt from content
  rules but not from the hygiene rules (scratch paths, line numbers, machine-local paths).
- ``budgets`` (warn over the soft budget, fail over the hard one): words per doc type, the skill
  description, and the always-loaded total (root ``CLAUDE.md`` plus every skill and agent description).
- ``parity`` (fail): every ``frontend/src/dashboards/<slug>/entry.ts`` has its doc
  (``docs/dashboards/<slug>.md``, or ``docs/BOARD.md`` for ``board``), every dashboard doc has an
  ``entry.ts``, and every dashboard doc carries the six template headings.
- ``frontmatter`` (fail): a skill's ``name`` is its folder, an agent's is its file name, and a
  description is at most 60 words.
- ``archive`` (fail): no doc outside ``docs/archive/`` links into it.

The baseline (``scripts/check_docs_baseline.json``) holds today's findings, keyed on (file, check, key)
with a count; keys never carry line numbers, so an unrelated edit does not make an old finding new.

    python scripts/check_docs.py                   # exit 1 only on a fail finding not in the baseline
    python scripts/check_docs.py --all             # print every finding, baselined or not
    python scripts/check_docs.py --write-baseline  # record the current findings as the baseline

Exit codes: 0 no new fail finding; 1 at least one; 2 a bad argument or an unreadable baseline.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

CHECKS = ("links", "pointers", "banned", "budgets", "parity", "frontmatter", "archive")
SEVERITIES = ("fail", "warn", "report")
DEFAULT_BASELINE = "scripts/check_docs_baseline.json"

# --- the walk ------------------------------------------------------------------------------------------

ALWAYS_SKIP_NAMES = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__"})
ALWAYS_SKIP_PATHS = frozenset({"docs/temp", ".claude/worktrees"})
# The checker, its baseline and its tests quote the patterns they look for; scanning them would report
# their own examples, and the baseline would report itself.
SELF_FILES = frozenset(
    {
        "scripts/check_docs.py",
        "scripts/check_docs_baseline.json",
        "backend/tests/ops/test_check_docs.py",
    }
)
MAX_SCAN_BYTES = 8_000_000

# --- doc types and budgets -----------------------------------------------------------------------------

BUDGETS = {  # type: (soft, hard) in words
    "README": (1300, 1600),
    "root CLAUDE.md": (2500, 3000),
    "nested CLAUDE.md": (1000, 1200),
    "subsystem": (3000, 3500),
    "catalog": (4500, 5000),
    "dashboard": (400, 500),
    "skill": (700, 900),
    "agent": (700, 900),
    "archive record": (600, 800),
    "glossary": (1200, 1500),
    "roadmap": (1500, 2000),
    "standard": (1500, 1800),
}
CATALOGS = frozenset({"docs/DETECTORS.md", "docs/DASHBOARDS.md"})
DESCRIPTION_BUDGET = (40, 60)
ALWAYS_LOADED_BUDGET = (3200, 3300)

DASHBOARD_HEADINGS = (
    "What it shows",
    "Who counts",
    "Data and code",
    "As-of behavior",
    "Rules not visible in code",
    "Known limits",
)
DASHBOARD_RULES = range(1, 10)
REPORT_ONLY_PREFIXES = ("backend/db/migrations/", "docs/archive/")
BARE_NAMES_NOT_DOCS = frozenset(
    {"README", "CLAUDE", "SKILL", "AGENTS", "CHANGELOG", "LICENSE", "CONTRIBUTING"}
)


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    check: str
    severity: str
    key: str
    message: str

    @property
    def ident(self) -> tuple[str, str, str]:
        return (self.file, self.check, self.key)

    def render(self) -> str:
        return f"{self.file}:{self.line}: {self.severity} [{self.check}] {self.message}"


# --- gitignore -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    base: str
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool
    anchored: bool


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end + 1
                continue
        elif char == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _read_gitignore(path: Path, base: str) -> list[_Rule]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rules = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = "/" in line
        line = line.lstrip("/")
        if line:
            rules.append(_Rule(base, _glob_to_regex(line), negate, dir_only, anchored))
    return rules


def _ignored(rel: str, is_dir: bool, rules: list[_Rule]) -> bool:
    ignored = False
    for rule in rules:
        if rule.dir_only and not is_dir:
            continue
        if rule.base:
            if not rel.startswith(rule.base + "/"):
                continue
            sub = rel[len(rule.base) + 1 :]
        else:
            sub = rel
        target = sub if rule.anchored else sub.rsplit("/", 1)[-1]
        if rule.regex.match(target):
            ignored = not rule.negate
    return ignored


def walk_files(root: Path) -> list[str]:
    """Every file under ``root`` that git would track, as posix paths relative to ``root``."""
    files: list[str] = []

    def visit(folder: Path, rel_dir: str, rules: list[_Rule]) -> None:
        ignore_file = folder / ".gitignore"
        if ignore_file.is_file():
            rules = rules + _read_gitignore(ignore_file, rel_dir)
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            return
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            if entry.name in ALWAYS_SKIP_NAMES or rel in ALWAYS_SKIP_PATHS:
                continue
            if entry.is_dir(follow_symlinks=False):
                if not _ignored(rel, True, rules):
                    visit(Path(entry.path), rel, rules)
            elif entry.is_file(follow_symlinks=False) and not _ignored(rel, False, rules):
                files.append(rel)

    visit(root, "", [])
    return files


# --- Markdown parsing ----------------------------------------------------------------------------------

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ATX_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?[ \t]*$")
SETEXT_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
RECIPE_OPEN_RE = re.compile(r"<!--\s*recipe:[^>]*-->")
RECIPE_CLOSE_RE = re.compile(r"<!--\s*/recipe\s*-->")
NOT_PARAGRAPH_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|\||<)")
CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
INLINE_LINK_RE = re.compile(
    r"(!?)\[((?:[^\[\]\\]|\\.|\[[^\[\]]*\])*)\]"
    r"\(\s*(<[^>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
REF_DEF_RE = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*(<[^>]*>|\S+)")
HTML_LINK_RE = re.compile(r"<(?:a|img)\b[^>]*?\b(?:href|src)\s*=\s*[\"']([^\"']+)[\"']", re.I)
HTML_ID_RE = re.compile(r"<[a-z][^>]*?\b(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.I)
BOLD_LEAD_RE = re.compile(r"^\s{0,4}(?:>\s*)?(?:[-*+]\s+|\d+[.)]\s+)?\*\*(?=\S)(.+?)(?<=\S)\*\*")
SCHEME_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")


@dataclass
class MdLine:
    no: int
    text: str
    code: bool = False  # inside a fenced block (the fence lines included)
    skip: bool = False  # front matter or a generated recipe block
    front: bool = False
    heading: str | None = None


def parse_markdown(text: str) -> list[MdLine]:
    raw = text.split("\n")
    front_end = -1
    if raw and raw[0].strip() == "---":
        for j in range(1, len(raw)):
            if raw[j].strip() in ("---", "..."):
                front_end = j
                break
    out: list[MdLine] = []
    fence: tuple[str, int] | None = None
    recipe = False
    for i, line in enumerate(raw):
        no = i + 1
        if i <= front_end:
            out.append(MdLine(no, line, skip=True, front=True))
            continue
        match = FENCE_RE.match(line)
        if fence is not None:
            out.append(MdLine(no, line, code=True, skip=recipe))
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1]
                and not match.group(2).strip()
            ):
                fence = None
            continue
        if match:
            fence = (match.group(1)[0], len(match.group(1)))
            out.append(MdLine(no, line, code=True, skip=recipe))
            continue
        if RECIPE_OPEN_RE.search(line):
            recipe = True
            out.append(MdLine(no, line, skip=True))
            continue
        if RECIPE_CLOSE_RE.search(line):
            recipe = False
            out.append(MdLine(no, line, skip=True))
            continue
        heading = None
        atx = ATX_RE.match(line)
        if atx:
            heading = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", atx.group(1) or "").strip()
        elif SETEXT_RE.match(line) and out and _setext_candidate(out):
            out[-1].heading = out[-1].text.strip()
        out.append(MdLine(no, line, skip=recipe, heading=heading))
    return out


def _setext_candidate(out: list[MdLine]) -> bool:
    prev = out[-1]
    if prev.code or prev.front or prev.heading is not None or not prev.text.strip():
        return False
    if NOT_PARAGRAPH_RE.match(prev.text):
        return False
    return len(out) == 1 or not out[-2].text.strip() or out[-2].heading is not None


def mask_code_spans(line: str) -> str:
    return CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)


def plain_text(text: str) -> str:
    """Heading text as GitHub renders it: no link syntax, tags, backticks or emphasis markers."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "")
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", text)
    return text.strip()


def github_slug(text: str) -> str:
    text = plain_text(text).lower()
    kept = "".join(
        ch for ch in text if ch.isalnum() or ch in " -_" or unicodedata.category(ch).startswith("M")
    )
    return kept.replace(" ", "-")


def markdown_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    for line in parse_markdown(text):
        if line.front:
            continue
        if line.heading is not None:
            base = github_slug(line.heading)
            slug = base
            while slug in occurrences:
                occurrences[base] += 1
                slug = f"{base}-{occurrences[base]}"
            occurrences[slug] = 0
            if slug:
                anchors.add(slug)
        if not line.code:
            anchors.update(m.group(1).lower() for m in HTML_ID_RE.finditer(line.text))
    return anchors


def count_words(text: str) -> int:
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))


def split_front_matter(text: str) -> tuple[dict[str, str] | None, str]:
    """A small YAML subset: ``key: value``, quoted values, plain continuations and ``>``/``|`` blocks."""
    lines = text.lstrip("\ufeff").split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    end = next((j for j in range(1, len(lines)) if lines[j].strip() == "---"), None)
    if end is None:
        return None, text
    meta: dict[str, str] = {}
    block = lines[1:end]
    i = 0
    while i < len(block):
        match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", block[i])
        i += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        more: list[str] = []
        while i < len(block) and (block[i][:1] in (" ", "\t") or not block[i].strip()):
            more.append(block[i].strip())
            i += 1
        parts = [part for part in more if part]
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            value = ("\n" if value.startswith("|") else " ").join(parts)
        else:
            value = " ".join([value, *parts]) if parts else value
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
        meta[key] = value
    return meta, "\n".join(lines[end + 1 :])


# --- the repo ------------------------------------------------------------------------------------------


@dataclass
class Repo:
    root: Path
    files: list[str]
    file_set: set[str] = field(default_factory=set)
    dir_set: set[str] = field(default_factory=set)
    _text: dict[str, str | None] = field(default_factory=dict)
    _anchors: dict[str, set[str]] = field(default_factory=dict)
    _headings: dict[str, list[str]] = field(default_factory=dict)
    _citable: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.file_set = set(self.files)
        for rel in self.files:
            parent = posixpath.dirname(rel)
            while parent and parent not in self.dir_set:
                self.dir_set.add(parent)
                parent = posixpath.dirname(parent)

    def exists(self, rel: str) -> bool:
        return rel == "" or rel in self.file_set or rel in self.dir_set

    def text(self, rel: str) -> str | None:
        if rel not in self._text:
            self._text[rel] = None
            try:
                path = self.root / rel
                if path.stat().st_size <= MAX_SCAN_BYTES:
                    data = path.read_bytes()
                    if b"\x00" not in data[:8192]:
                        decoded = data.decode("utf-8", errors="replace").lstrip("\ufeff")
                        self._text[rel] = decoded.replace("\r\n", "\n").replace("\r", "\n")
            except OSError:
                pass
        return self._text[rel]

    def anchors(self, rel: str) -> set[str]:
        if rel not in self._anchors:
            self._anchors[rel] = markdown_anchors(self.text(rel) or "")
        return self._anchors[rel]

    def headings(self, rel: str) -> list[str]:
        if rel not in self._headings:
            lines = parse_markdown(self.text(rel) or "")
            self._headings[rel] = [ln.heading for ln in lines if ln.heading and not ln.front]
        return self._headings[rel]

    def citable(self, rel: str) -> list[str]:
        """Headings plus bold run-in heads (``**Name.** text``, ``- **Name** text``): code cites both."""
        if rel not in self._citable:
            names = list(self.headings(rel))
            for line in parse_markdown(self.text(rel) or ""):
                if not line.code and not line.front:
                    lead = BOLD_LEAD_RE.match(line.text)
                    if lead:
                        names.append(lead.group(1))
            self._citable[rel] = names
        return self._citable[rel]


def in_scope_markdown(rel: str) -> bool:
    if not rel.endswith(".md"):
        return False
    if rel in ("README.md", "CLAUDE.md") or rel.endswith("/CLAUDE.md"):
        return True
    if rel.startswith("docs/"):
        return True
    if rel.startswith(".claude/skills/") and rel.endswith("/SKILL.md"):
        return True
    if re.fullmatch(r"\.claude/agents/[^/]+\.md", rel):
        return True
    return rel == "backend/securities/business_type/README.md"


def doc_type(rel: str) -> str:
    if rel == "README.md":
        return "README"
    if rel == "CLAUDE.md":
        return "root CLAUDE.md"
    if rel.endswith("/CLAUDE.md"):
        return "nested CLAUDE.md"
    if rel.startswith(".claude/skills/"):
        return "skill"
    if rel.startswith(".claude/agents/"):
        return "agent"
    if rel.startswith("docs/dashboards/"):
        return "dashboard"
    if rel.startswith("docs/archive/"):
        return "archive record"
    if rel in CATALOGS:
        return "catalog"
    return {
        "docs/GLOSSARY.md": "glossary",
        "docs/ROADMAP.md": "roadmap",
        "docs/DOCS_STANDARD.md": "standard",
    }.get(rel, "subsystem")


def _is_archive(rel: str) -> bool:
    return rel.startswith("docs/archive/")


# --- links and archive citations -----------------------------------------------------------------------


def _link_targets(line: MdLine) -> list[str]:
    if line.code or line.skip:
        return []
    text = mask_code_spans(line.text)
    targets = [m.group(3) for m in INLINE_LINK_RE.finditer(text)]
    ref = REF_DEF_RE.match(text)
    if ref:
        targets.append(ref.group(2))
    targets.extend(m.group(1) for m in HTML_LINK_RE.finditer(text))
    return targets


def _resolve(src: str, path: str) -> str | None:
    joined = (
        path.lstrip("/") if path.startswith("/") else posixpath.join(posixpath.dirname(src), path)
    )
    norm = posixpath.normpath(joined) if joined else ""
    if norm == ".":
        return ""
    if norm == ".." or norm.startswith("../"):
        return None
    return norm


def check_links(repo: Repo, rel: str, lines: list[MdLine]) -> list[Finding]:
    findings = []
    for line in lines:
        for raw_target in _link_targets(line):
            target = raw_target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if not target or SCHEME_RE.match(target):
                continue
            path, _, fragment = target.partition("#")
            path = unquote(path.split("?", 1)[0])
            resolved = _resolve(rel, path) if path else rel
            if resolved is None or not repo.exists(resolved):
                findings.append(
                    Finding(
                        rel, line.no, "links", "fail", target, f"link {target} does not resolve"
                    )
                )
                continue
            if resolved.startswith("docs/archive/") and not _is_archive(rel):
                findings.append(
                    Finding(
                        rel,
                        line.no,
                        "archive",
                        "fail",
                        resolved,
                        f"links into the archive ({resolved}); docs never cite an archive record",
                    )
                )
            if fragment and resolved.endswith(".md") and resolved in repo.file_set:
                anchor = unquote(fragment).lower()
                if anchor not in repo.anchors(resolved):
                    findings.append(
                        Finding(
                            rel,
                            line.no,
                            "links",
                            "fail",
                            target,
                            f"anchor #{fragment} matches no heading in {resolved}",
                        )
                    )
    return findings


# --- code pointers -------------------------------------------------------------------------------------

DOC_PATH_RE = re.compile(r"docs/(?:[\w.-]+/)*[\w.-]+\.md\b")
BARE_DOC_RE = re.compile(r"(?<![\w/.-])([A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*)\.md\b")
AFTER_PATH_RE = re.compile(
    r"""^(?:``?)?(?:
        \#(?P<anchor>[\w-]+)
      | \s*§\s*(?P<sect>"[^"\n]+"|“[^”\n]+”|[^\s"“].*)
      | \s*\(\s*(?P<paren>"[^"\n]+"|“[^”\n]+”)
      | ,?\s+(?P<quoted>"[^"\n]+"|“[^”\n]+”)
      | \s+rules?\s+(?P<rule>\d+)
    )""",
    re.X,
)
SECTION_STOP_RE = re.compile(r"[)\]};,:\"'`*|<>\\]|\.(?:\s|$)| [—–] | -- | \+ |-->| \(")
LEADING_NUMBER_RE = re.compile(r"^(\d+[a-z]?)(?=[.)\s:]|$)")
GIT_REF_BEFORE_RE = re.compile(r"[\w./~^-]+:$")


def _norm(text: str) -> str:
    text = plain_text(text).lower().replace("’", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" .:;,\"'“”")


def _prefix(longer: str, shorter: str) -> bool:
    if not shorter or not longer.startswith(shorter):
        return False
    return len(longer) == len(shorter) or not longer[len(shorter)].isalnum()


def _heading_forms(heading: str) -> set[str]:
    """A heading as a cite may name it: in full, without a leading symbol or article, or by number."""
    full = re.sub(r"^[^\w]+", "", _norm(heading))
    forms = {full, re.sub(r"^(?:the|a|an) ", "", full)}
    number = LEADING_NUMBER_RE.match(full)
    if number:
        forms.add(number.group(1))
        forms.add(full[number.end() :].lstrip(" .):").strip())
    return {form for form in forms if form}


def heading_matches(cite: str, headings: list[str]) -> bool:
    cited = _norm(cite)
    if not cited:
        return True
    cited_slug = github_slug(cited)
    for heading in headings:
        for form in _heading_forms(heading):
            if _prefix(form, cited) or _prefix(cited, form):
                return True
            slug = github_slug(form)
            if _prefix(slug, cited_slug) or _prefix(cited_slug, slug):
                return True
    return False


def _section_text(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in '"“':
        return raw[1:-1]
    stop = SECTION_STOP_RE.search(raw)
    return (raw[: stop.start()] if stop else raw).strip()


def _pointer_refs(text: str) -> list[tuple[int, str, str]]:
    """(offset, doc path, the text after it) for each doc named in ``text``."""
    refs = []
    for match in DOC_PATH_RE.finditer(text):
        start = match.start()
        before = text[:start]
        if before and (before[-1].isalnum() or before[-1] in "_.-"):
            continue
        if before.endswith("/"):
            # "../docs/X.md" is the repo's doc; "https://.../docs/X.md" or "other/docs/X.md" is not
            token = re.split(r"[\s\"'`(<\[=]", before)[-1]
            if not re.fullmatch(r"(?:\.\.?/)+", token):
                continue
        if before.endswith(":") and GIT_REF_BEFORE_RE.search(before[-80:]):
            continue  # a git ref such as <commit>:docs/X.md names a file at that commit
        refs.append((start, match.group(0), text[match.end() :]))
    for match in BARE_DOC_RE.finditer(text):
        if match.group(1) in BARE_NAMES_NOT_DOCS:
            continue
        refs.append((match.start(), f"docs/{match.group(0)}", text[match.end() :]))
    return sorted(refs)


def _pointer_findings(repo: Repo, rel: str, lineno: int, text: str) -> list[Finding]:
    severity = "report" if rel.startswith(REPORT_ONLY_PREFIXES) else "fail"
    findings = []
    for _, doc, after in _pointer_refs(text):
        if doc not in repo.file_set:
            findings.append(
                Finding(rel, lineno, "pointers", severity, doc, f"{doc} does not exist")
            )
            continue
        match = AFTER_PATH_RE.match(after)
        if not match:
            continue
        if match.group("anchor"):
            anchor = match.group("anchor")
            if anchor.lower() not in repo.anchors(doc):
                key = f"{doc}#{anchor}"
                findings.append(
                    Finding(
                        rel, lineno, "pointers", severity, key, f"no heading in {doc} for #{anchor}"
                    )
                )
        elif match.group("rule"):
            if doc.endswith("/DASHBOARDS.md") and int(match.group("rule")) not in DASHBOARD_RULES:
                key = f"{doc} rule {match.group('rule')}"
                findings.append(
                    Finding(rel, lineno, "pointers", severity, key, f"{key}: the rules are 1-9")
                )
        else:
            raw = match.group("sect") or match.group("paren") or match.group("quoted") or ""
            cite = _section_text(raw)
            if cite and not heading_matches(cite, repo.citable(doc)):
                key = f"{doc} §{cite}"
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        "pointers",
                        severity,
                        key,
                        f"no heading in {doc} matches {cite!r}",
                    )
                )
    return findings


def _unescape_code(text: str) -> str:
    return (
        text.replace("\\u00a7", "§")
        .replace("\\u201c", "“")
        .replace("\\u201d", "”")
        .replace('\\"', '"')
    )


def check_code_pointers(repo: Repo, rel: str) -> list[Finding]:
    text = repo.text(rel)
    if not text or ".md" not in text:
        return []
    findings = []
    for i, line in enumerate(_unescape_code(text).split("\n"), start=1):
        if ".md" in line:
            findings.extend(_pointer_findings(repo, rel, i, line))
    return findings


def _mask_links(text: str) -> str:
    text = INLINE_LINK_RE.sub(lambda m: " " * len(m.group(0)), text)
    return REF_DEF_RE.sub(lambda m: " " * len(m.group(0)), text)


def check_doc_pointers(repo: Repo, rel: str, lines: list[MdLine]) -> list[Finding]:
    findings = []
    for line in lines:
        if (line.skip and not line.front) or ".md" not in line.text:
            continue
        text = line.text if line.code else _mask_links(line.text)
        findings.extend(_pointer_findings(repo, rel, line.no, text))
    return findings


# --- banned patterns -----------------------------------------------------------------------------------

STATUS_TAG_BODY_RE = re.compile(r"\[(BUILT|PROPOSED|PINNED)\b")
HEADING_TAG_RE = re.compile(r"\[([A-Za-z][^\]\n]{0,80})\](?![(\[])")
STATUS_WORDS = frozenset(
    "built approved proposed pinned specified open shipped planned done wip draft superseded retired "
    "deprecated partial live principle boundary closed resolved measured unbuilt todo experimental".split()
)
PR_REF_RE = re.compile(r"\bPRs?\s?#\d+|\(#\d+\)|(?<![\w&#/])#\d{3,}\b")
DOCS_TEMP_RE = re.compile(r"docs[/\\]temp(?:[/\\][^\s`'\")\]>]*|\b)")
LINE_NUMBER_RE = re.compile(r"[\w./-]*\.md(?::\d+(?:\s*[-–]\s*\d+)?|#L\d+)")
CODE_LINE_NUMBER_RE = re.compile(
    r"[\w./-]+\.(?:py|tsx?|jsx?|sh|ps1|sql|ya?ml|toml|json|css|html|ini|cfg|conf|csv):\d+"
)
LOCAL_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]|(?<![\w.])/Users/[\w.-]+/")
CONNECTION_RE = re.compile(r"\b(?:postgres(?:ql)?|mysql|redis|mongodb)(?:\+\w+)?://[^\s/@'\"`<>]+@")
STATUS_PHRASE_RE = re.compile(r"\bMEASURED\b|\b(?i:choices to confirm|not yet rehearsed)\b")
SLOP_PHRASE_RE = re.compile(r"\b(?:load[- ]bearing|honest by construction|the spine)\b", re.I)
CODENAME_RE = re.compile(r"\b[A-Z]{1,2}\d{1,2}[a-z]?\b")
CODENAME_ALLOWED_RE = re.compile(r"V\d|Q[1-4]|FY\d\d")
FORM_WORD_BEFORE_RE = re.compile(r"\b(?:Form|Forms|Schedule|Item|Rule)\s+$")
BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
CAPS_WORD_RE = re.compile(r"(?<![\w'])[A-Z][A-Z']+(?![\w'])")
# Common English words written in CAPS for emphasis. SQL keywords and acronyms (IN, ON, AS, BY, FROM, AND,
# OR, COUNT, DELETE, DROP) are left out: in this repo they are usually code, not shouting.
EMPHASIS_WORDS = frozenset(
    """never only not no must always all every one none nothing any own same before after exactly both
    either neither each once first last new old is are was be do does did don't cannot can't won't isn't
    aren't doesn't without but its the this that these those here there now then still also even
    already yet until unless if when why what how who which where real true false read reads write
    writes written wins stays keep keeps kept changes change changed refuses fails skip skips skipped
    stop stops out ever more less most least other another again too very just structurally safe stale
    fresh empty loud quiet visible hidden honest early late raw past future today tonight wrong right
    required allowed forbidden lost hard soft broken works ok""".split()
)
URL_RE = re.compile(r"(?:https?://|www\.)\S+")
LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
HTML_RE = re.compile(r"<!--.*?-->|<[^>\n]+>")
CURL_RE = re.compile(r"\bcurl(?:\.exe)?\b")
APP_URL_RE = re.compile(
    r"(?:https?://)?(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(?::(?P<port>\d+))?"
    r"(?P<path>/[^\s\"'`)>]*)?"
)
APP_VAR_URL_RE = re.compile(
    r"\$\{?(?P<var>[A-Za-z_]*(?:API|BASE|HOST|URL|ORIGIN)[A-Za-z_]*)\}?(?P<path>/[^\s\"'`)>]*)"
)
AUTH_RE = re.compile(r"authorization|--oauth2-bearer|(?:^|\s)(?:-u|--user|-b|--cookie)\s", re.I)
CURL_STOP_RE = re.compile(r"\s(?:\||&&|;|#)\s")
SPA_PORTS = frozenset({"5173", "8080", "8081"})

# id: (severity, scope, description). scope: "all" = every in-scope doc including the archive;
# "content" = every in-scope doc except the archive.
BANNED_PATTERNS = {
    "status-tag": ("fail", "content", "status tag"),
    "pr-ref": ("fail", "content", "PR reference"),
    "docs-temp": ("fail", "all", "cites the local scratch folder"),
    "line-number": ("fail", "all", "line-number citation of a doc"),
    "line-number-code": ("warn", "content", "line-number citation of a code file"),
    "local-path": ("fail", "all", "machine-local path or connection string"),
    "status-phrase": ("fail", "content", "status or measurement phrase"),
    "slop-phrase": ("fail", "content", "banned phrase"),
    "em-dash": ("warn", "content", "em-dash chains"),
    "emphasis": ("warn", "content", "stacked bold or CAPS emphasis"),
    "codename": ("warn", "content", "letter-number codename"),
    "curl-noauth": ("fail", "content", "curl to an app route without a credential"),
}


def _banned(rel: str, line: int, pattern: str, detail: str, message: str | None = None) -> Finding:
    severity, _, description = BANNED_PATTERNS[pattern]
    key = f"{pattern}: {detail}"
    return Finding(rel, line, "banned", severity, key, message or f"{description}: {detail}")


def _prose(masked: str) -> str:
    text = HTML_RE.sub(" ", masked)
    text = LINK_TARGET_RE.sub("] ", text)
    return URL_RE.sub(" ", text)


def _codenames(rel: str, line: MdLine, prose: str) -> list[Finding]:
    findings = []
    for match in CODENAME_RE.finditer(prose):
        token = match.group(0)
        if CODENAME_ALLOWED_RE.fullmatch(token) or FORM_WORD_BEFORE_RE.search(
            prose[: match.start()]
        ):
            continue
        findings.append(_banned(rel, line.no, "codename", token))
    return findings


def _emphasis_marks(masked: str) -> int:
    bold = BOLD_RE.findall(masked)
    caps = [w for w in CAPS_WORD_RE.findall(masked) if len(w) > 1 and w.lower() in EMPHASIS_WORDS]
    return len(bold) + len(caps)


def _curl_findings(rel: str, lines: list[MdLine]) -> list[Finding]:
    findings = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.skip or "curl" not in line.text:
            continue
        text = line.text
        if line.code:
            while text.rstrip().endswith(("\\", "`", "^")) and i < len(lines) and lines[i].code:
                text = text.rstrip()[:-1] + " " + lines[i].text
                i += 1
        for match in CURL_RE.finditer(text):
            segment = text[match.end() :]
            if not line.code:
                segment = segment.split("`", 1)[0]
            segment = CURL_STOP_RE.split(segment, 1)[0]
            route = _app_route(segment)
            if route is not None and not AUTH_RE.search(segment):
                findings.append(_banned(rel, line.no, "curl-noauth", route))
    return findings


def _app_route(segment: str) -> str | None:
    """The app route a curl segment calls, or None when it calls no app route or a public one."""
    url = APP_URL_RE.search(segment)
    port = None
    if url:
        port, path = url.group("port"), url.group("path") or "/"
    else:
        var = APP_VAR_URL_RE.search(segment)
        if not var:
            return None
        path = var.group("path")
    path = path.split("?", 1)[0]
    if path in ("", "/") or path.startswith(("/health", "/api/health")):
        return None
    if port in SPA_PORTS and not path.startswith("/api/"):
        return None
    return path


def check_banned(rel: str, lines: list[MdLine]) -> list[Finding]:
    archive = _is_archive(rel)
    findings: list[Finding] = []
    dashes = words = 0
    first_dash = loud_first = 0
    loud_lines = 0
    for line in lines:
        if line.skip and not line.front:
            continue
        raw = line.text
        # hygiene: every doc, the archive included, code included
        for match in DOCS_TEMP_RE.finditer(raw):
            findings.append(_banned(rel, line.no, "docs-temp", match.group(0).replace("\\", "/")))
        for match in LINE_NUMBER_RE.finditer(raw):
            findings.append(_banned(rel, line.no, "line-number", match.group(0)))
        for match in LOCAL_PATH_RE.finditer(raw):
            findings.append(_banned(rel, line.no, "local-path", match.group(0).replace("\\", "/")))
        for match in CONNECTION_RE.finditer(raw):
            scheme = match.group(0).split("://", 1)[0]
            findings.append(_banned(rel, line.no, "local-path", f"{scheme}://***@"))
        if archive:
            continue
        for match in CODE_LINE_NUMBER_RE.finditer(raw):
            findings.append(_banned(rel, line.no, "line-number-code", match.group(0)))
        if line.heading is not None:
            for match in HEADING_TAG_RE.finditer(line.heading):
                word = re.split(r"[^A-Za-z]", match.group(1), maxsplit=1)[0]
                if (word.isupper() and len(word) >= 3) or word.lower() in STATUS_WORDS:
                    findings.append(_banned(rel, line.no, "status-tag", f"[{word.upper()}]"))
        if line.code:
            continue
        masked = mask_code_spans(raw)
        if line.heading is None:
            for match in STATUS_TAG_BODY_RE.finditer(masked):
                findings.append(_banned(rel, line.no, "status-tag", f"[{match.group(1)}]"))
        for match in PR_REF_RE.finditer(masked):
            findings.append(_banned(rel, line.no, "pr-ref", re.sub(r"\s+", " ", match.group(0))))
        for match in STATUS_PHRASE_RE.finditer(masked):
            findings.append(_banned(rel, line.no, "status-phrase", match.group(0)))
        for match in SLOP_PHRASE_RE.finditer(masked):
            phrase = re.sub(r"load bearing", "load-bearing", match.group(0).lower())
            findings.append(_banned(rel, line.no, "slop-phrase", phrase))
        prose = _prose(masked)
        findings.extend(_codenames(rel, line, prose))
        count = prose.count("—")
        if count and not first_dash:
            first_dash = line.no
        dashes += count
        words += count_words(prose)
        if _emphasis_marks(masked) >= 3:
            loud_lines += 1
            loud_first = loud_first or line.no
    if archive:
        return findings
    findings.extend(_curl_findings(rel, lines))
    if dashes * 100 > 3 * max(words, 100):
        message = f"{dashes} em dashes in {words} words (more than 3 per 100)"
        findings.append(_banned(rel, first_dash, "em-dash", "more than 3 per 100 words", message))
    if loud_lines:
        message = f"{loud_lines} lines stack three or more bold or CAPS marks"
        findings.append(_banned(rel, loud_first, "emphasis", "stacked bold or CAPS", message))
    return findings


# --- budgets and front matter --------------------------------------------------------------------------


def _counted_text(rel: str, lines: list[MdLine]) -> str:
    """The words a budget counts: no recipe block; no front matter for a skill or an agent."""
    with_front = doc_type(rel) not in ("skill", "agent")
    return "\n".join(ln.text for ln in lines if not ln.skip or (ln.front and with_front))


def _budget_finding(rel: str, kind: str, words: int, budget: tuple[int, int]) -> Finding | None:
    soft, hard = budget
    if words > hard:
        level, limit, severity = "hard", hard, "fail"
    elif words > soft:
        level, limit, severity = "soft", soft, "warn"
    else:
        return None
    message = f"{kind}: {words} words; budget {soft} soft / {hard} hard"
    return Finding(rel, 1, "budgets", severity, f"{kind}: over {level} budget {limit}", message)


def check_budget(rel: str, lines: list[MdLine]) -> list[Finding]:
    kind = doc_type(rel)
    finding = _budget_finding(rel, kind, count_words(_counted_text(rel, lines)), BUDGETS[kind])
    return [finding] if finding else []


def check_front_matter(rel: str, text: str) -> tuple[list[Finding], str | None]:
    """Findings for a skill or agent file, and its description (for the always-loaded total)."""
    if rel.startswith(".claude/agents/"):
        owner, what = posixpath.splitext(posixpath.basename(rel))[0], "file name"
    else:
        owner, what = posixpath.basename(posixpath.dirname(rel)), "folder"
    meta, _ = split_front_matter(text)
    if meta is None:
        return [
            Finding(rel, 1, "frontmatter", "fail", "missing front matter", "no front matter")
        ], None
    findings = []
    name = meta.get("name")
    if not name:
        findings.append(Finding(rel, 1, "frontmatter", "fail", "missing name", "no name"))
    elif name != owner:
        key = f"name '{name}' is not the {what} '{owner}'"
        findings.append(Finding(rel, 1, "frontmatter", "fail", key, key))
    description = meta.get("description")
    if not description:
        findings.append(
            Finding(rel, 1, "frontmatter", "fail", "missing description", "no description")
        )
        return findings, None
    words = count_words(description)
    soft, hard = DESCRIPTION_BUDGET
    if words > hard:
        key = f"description over {hard} words"
        findings.append(
            Finding(rel, 1, "frontmatter", "fail", key, f"description is {words} words")
        )
    elif words > soft:
        key = f"description: over soft budget {soft}"
        message = f"description is {words} words; budget {soft} soft / {hard} hard"
        findings.append(Finding(rel, 1, "budgets", "warn", key, message))
    return findings, description


# --- dashboard parity ----------------------------------------------------------------------------------


def check_parity(repo: Repo) -> list[Finding]:
    findings = []
    for rel in repo.files:
        entry = re.fullmatch(r"frontend/src/dashboards/([^/]+)/entry\.ts", rel)
        if entry:
            slug = entry.group(1)
            doc = "docs/BOARD.md" if slug == "board" else f"docs/dashboards/{slug}.md"
            if doc not in repo.file_set:
                key = f"missing doc {doc}"
                findings.append(
                    Finding(rel, 1, "parity", "fail", key, f"dashboard {slug} has no {doc}")
                )
        doc_match = re.fullmatch(r"docs/dashboards/([^/]+)\.md", rel)
        if doc_match:
            slug = doc_match.group(1)
            entry_path = f"frontend/src/dashboards/{slug}/entry.ts"
            if entry_path not in repo.file_set:
                key = f"missing entry {entry_path}"
                findings.append(
                    Finding(rel, 1, "parity", "fail", key, f"no registered dashboard {slug}")
                )
            have = {_norm(h) for h in repo.headings(rel)}
            for heading in DASHBOARD_HEADINGS:
                if _norm(heading) not in have:
                    key = f"missing heading: {heading}"
                    message = f"missing the template heading '## {heading}'"
                    findings.append(Finding(rel, 1, "parity", "fail", key, message))
    return findings


# --- the run -------------------------------------------------------------------------------------------


def run_checks(root: Path, skip: frozenset[str] = frozenset()) -> list[Finding]:
    """Every finding under ``root``; ``skip`` names extra files to leave out (the baseline in use)."""
    repo = Repo(Path(root), walk_files(Path(root)))
    findings: list[Finding] = []
    descriptions: list[str] = []
    for rel in repo.files:
        if rel in SELF_FILES or rel in skip:
            continue
        if not rel.endswith(".md"):
            findings.extend(check_code_pointers(repo, rel))
            continue
        if not in_scope_markdown(rel):
            continue
        text = repo.text(rel)
        if text is None:
            continue
        lines = parse_markdown(text)
        findings.extend(check_links(repo, rel, lines))
        findings.extend(check_doc_pointers(repo, rel, lines))
        findings.extend(check_banned(rel, lines))
        findings.extend(check_budget(rel, lines))
        if rel.startswith((".claude/skills/", ".claude/agents/")):
            front, description = check_front_matter(rel, text)
            findings.extend(front)
            if description:
                descriptions.append(description)
    findings.extend(check_parity(repo))
    claude = repo.text("CLAUDE.md") if "CLAUDE.md" in repo.file_set else ""
    total = count_words(claude or "") + sum(count_words(d) for d in descriptions)
    always = _budget_finding("CLAUDE.md", "always-loaded", total, ALWAYS_LOADED_BUDGET)
    if always:
        findings.append(always)
    return sorted(findings, key=lambda f: (f.file, f.line, f.check, f.key))


# --- the baseline and the CLI --------------------------------------------------------------------------


def load_baseline(path: Path) -> Counter[tuple[str, str, str]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    if not path.exists():
        return counts
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["findings"]:
        counts[(entry["file"], entry["check"], entry["key"])] += int(entry.get("count", 1))
    return counts


def write_baseline(path: Path, findings: list[Finding]) -> int:
    counts = Counter(f.ident for f in findings)
    severity = {f.ident: f.severity for f in findings}
    rows = []
    for ident in sorted(counts):
        entry = {
            "file": ident[0],
            "check": ident[1],
            "severity": severity[ident],
            "key": ident[2],
            "count": counts[ident],
        }
        rows.append("    " + json.dumps(entry, ensure_ascii=False))
    note = (
        "Known findings of scripts/check_docs.py. Regenerate with --write-baseline; "
        "never edit by hand. Keys carry no line numbers."
    )
    body = ",\n".join(rows)
    text = (
        "{\n"
        '  "version": 1,\n'
        f'  "note": {json.dumps(note)},\n'
        '  "findings": [\n'
        f"{body}{chr(10) if rows else ''}"
        "  ]\n"
        "}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return len(rows)


def split_new(
    findings: list[Finding], baseline: Counter[tuple[str, str, str]]
) -> tuple[list[Finding], int]:
    """The findings beyond the baseline's count for their key, and the baseline entries left unused."""
    remaining = Counter(baseline)
    new = []
    for finding in findings:
        if remaining[finding.ident] > 0:
            remaining[finding.ident] -= 1
        else:
            new.append(finding)
    return new, sum(remaining.values())


def summary(findings: list[Finding], new: list[Finding], stale: int | None) -> str:
    sev = Counter(f.severity for f in findings)
    new_sev = Counter(f.severity for f in new)
    parts = [
        f"check_docs: {len(findings)} findings (fail {sev['fail']}, warn {sev['warn']}, "
        f"report {sev['report']}); not in baseline: {len(new)} (fail {new_sev['fail']})"
    ]
    for check in CHECKS:
        per = Counter(f.severity for f in findings if f.check == check)
        total = sum(per.values())
        parts.append(f"{check} {total} (f{per['fail']} w{per['warn']} r{per['report']})")
    line = " | ".join(parts)
    if stale:
        line += f" | stale baseline entries {stale}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the repo's Markdown against DOCS_STANDARD.")
    parser.add_argument("--root", help="repo root (default: the folder above scripts/)")
    parser.add_argument("--baseline", help=f"baseline file (default: <root>/{DEFAULT_BASELINE})")
    parser.add_argument("--write-baseline", action="store_true", help="record the current findings")
    parser.add_argument("--all", action="store_true", help="print every finding, baselined or not")
    args = parser.parse_args(argv)

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    baseline_path = Path(args.baseline).resolve() if args.baseline else root / DEFAULT_BASELINE
    try:
        skip = frozenset({baseline_path.relative_to(root).as_posix()})
    except ValueError:
        skip = frozenset()
    findings = run_checks(root, skip)

    if args.write_baseline:
        rows = write_baseline(baseline_path, findings)
        print(summary(findings, [], None))
        print(f"OK: wrote {rows} baseline entries ({len(findings)} findings) to {baseline_path}")
        return 0

    try:
        baseline = load_baseline(baseline_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: cannot read the baseline {baseline_path}: {exc}")
        return 2
    new, stale = split_new(findings, baseline)
    for finding in findings if args.all else new:
        print(finding.render())
    print(summary(findings, new, stale))
    new_fails = [f for f in new if f.severity == "fail"]
    if new_fails:
        print(
            f"FAIL: {len(new_fails)} fail findings are not in the baseline. Fix them; a forward link to a "
            "doc that a later change adds may go in the baseline (--write-baseline), named in the PR."
        )
        return 1
    print("OK: no fail finding outside the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
