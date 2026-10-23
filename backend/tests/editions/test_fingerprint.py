"""S7 — the ENGINE FINGERPRINT, version 2: it hashes the LOGIC, not the bytes.

A comment or a docstring edit, an edit to ``domain/settings.py`` or to the rule log, never moves it; a logic edit
does; the two ``Settings`` values the engine reads do (an environment override included). The normalized form
is our own AST walk, so it is the same on every Python the code runs on — the cron image is 3.11, a host venv
may be 3.13 (``ast.dump`` changed in 3.13): a golden over the constructs that differ pins it. Pure: no DB.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import time
from pathlib import Path
from types import SimpleNamespace

from domain.config import DEFAULT_CONFIG
from editions import fingerprint as fp

BACKEND = Path(__file__).resolve().parents[2]

# Every construct whose AST shape moved between 3.10 and 3.13 (type_params on defs, ast.dump's empty fields,
# f-string pieces, match, the u-prefix ``kind``) — 3.11-compatible syntax, since CI runs 3.11.
SAMPLE = '''
"""module docstring"""
from __future__ import annotations
import dataclasses as dc
from typing import Any

X: int = 1_000  # a comment
U = u"unicode" "joined"
B = b"bytes"
C = 3 + 4j
E = ...


@dc.dataclass(frozen=True)
class Thing:
    """class docstring"""

    a: int = 0
    "an attribute docstring"

    def go(self, /, b: int, *args: Any, c: int = 2, **kw: Any) -> str:
        """method docstring"""
        width = 8
        total = sum(x * 2 for x in range(b) if x % 2)
        label = f"{self.a!r:>{width}} {total=} {'q'}"
        return label if total > 0 else "none"


async def stream(xs):
    async with ctx() as a, ctx() as b:
        async for x in xs:
            yield x
    await done()


def flow(v):
    global X
    match v:
        case {"k": [1, *rest]} if rest:
            return rest
        case Thing(a=0) | None:
            return None
        case _:
            pass
    try:
        if (n := len(v)) > 3:
            raise ValueError(n) from None
    except* (TypeError, ValueError) as eg:
        del eg
    finally:
        assert 0 <= X < 10, "bounds"
    return [k for k in {1: 2}] + list({3, 4}) + [v[1:2:3], lambda q=1: q]
'''

# sha256(normalized_source(SAMPLE)) — MEASURED identical on 3.11.17 (the cron image), 3.12.10 and 3.13.12 on
# 2026-10-23, while ``ast.dump`` of the same sample differs between 3.12 and 3.13 (f34b5c10… vs 44b722d4…)
GOLDEN = "b493da24dbcd42fcaaf91a6d600da5d74da16e49766589700e00b6edc9a90cf0"


def _h(text: str) -> str:
    return hashlib.sha256(fp.normalized_source(text).encode()).hexdigest()


def test_the_normalized_form_is_stable_across_python_versions():
    assert _h(SAMPLE) == GOLDEN


def test_a_comment_or_a_docstring_edit_changes_nothing_a_logic_edit_does():
    base = _h(SAMPLE)
    assert _h(SAMPLE.replace("# a comment", "# another comment, longer")) == base
    assert _h(SAMPLE.replace('"""method docstring"""', '"""rewritten, two\n lines"""')) == base
    assert _h(SAMPLE.replace('"an attribute docstring"', '"reworded"')) == base
    assert _h(SAMPLE.replace('"""module docstring"""\n', "")) == base  # removed entirely
    assert _h(SAMPLE.replace("\n\n\n", "\n\n")) == base  # blank lines / layout
    # logic: a constant, an operator, a name — each moves it
    assert _h(SAMPLE.replace("X: int = 1_000", "X: int = 1_001")) != base
    assert _h(SAMPLE.replace("total > 0", "total >= 0")) != base
    assert _h(SAMPLE.replace("return rest", "return v")) != base
    # a string that is NOT a bare statement is logic
    assert _h(SAMPLE.replace('"none"', '"nothing"')) != base


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "backend"
    (root / "domain").mkdir(parents=True)
    (root / "calls").mkdir()
    (root / "calls" / "rule.py").write_text(
        '"""A rule."""\n\n\ndef grade(x):\n    # the threshold\n    return x > 3\n',
        encoding="utf-8",
    )
    (root / "domain" / "config.py").write_text("LIMIT = 3\n", encoding="utf-8")
    (root / "domain" / "settings.py").write_text("SLACK = 'a'\n", encoding="utf-8")
    (root / "domain" / "rule_log.py").write_text("RULE_LOG = ()\n", encoding="utf-8")
    return root


def test_source_hash_ignores_comments_docstrings_settings_and_the_rule_log(tmp_path):
    root = _tree(tmp_path)
    src = ("calls", "domain")
    base = fp.source_hash(root, src)
    rule = root / "calls" / "rule.py"
    rule.write_text(
        '"""A rule, now with a much longer docstring\nthat spans lines."""\n\n\ndef grade(x):\n'
        "    # the threshold, see CALL_LOGIC.md\n    return x > 3  # unchanged\n",
        encoding="utf-8",
    )
    assert fp.source_hash(root, src) == base
    (root / "domain" / "settings.py").write_text("SLACK = 'b'\nNEW_KNOB = 1\n", encoding="utf-8")
    (root / "domain" / "rule_log.py").write_text("RULE_LOG = ('x',)\n", encoding="utf-8")
    assert fp.source_hash(root, src) == base
    rule.write_text("def grade(x):\n    return x > 4\n", encoding="utf-8")
    assert fp.source_hash(root, src) != base


def test_the_real_tree_leaves_out_settings_and_the_rule_log_and_keeps_the_logic():
    files = {f.relative_to(BACKEND).as_posix() for f in fp.engine_files()}
    assert "domain/settings.py" not in files and "domain/rule_log.py" not in files
    assert {"domain/config.py", "pipeline/core.py", "db/public_clock.py"} <= files
    assert any(f.startswith("calls/") for f in files) and any(
        f.startswith("signals/") for f in files
    )


def test_the_two_settings_the_engine_reads_are_hashed_by_value():
    a = SimpleNamespace(market_tz="America/New_York", price_settle_time=time(18, 0))
    b = SimpleNamespace(market_tz="America/New_York", price_settle_time=time(18, 30))
    c = SimpleNamespace(market_tz="America/Chicago", price_settle_time=time(18, 0))
    assert len({fp.settings_hash(a), fp.settings_hash(b), fp.settings_hash(c)}) == 3
    assert fp.settings_values(a) == {
        "market_tz": "America/New_York",
        "price_settle_time": "18:00:00",
    }
    assert fp.ENGINE_SETTINGS == ("market_tz", "price_settle_time")


def test_the_fingerprint_carries_its_version_and_a_stored_one_without_it_is_version_1():
    eng = fp.engine_fingerprint(DEFAULT_CONFIG)
    assert eng["fingerprint_version"] == fp.FINGERPRINT_VERSION == 2
    assert set(eng) >= {"fingerprint", "config_hash", "source_hash", "settings_hash", "code_sha"}
    assert eng == fp.engine_fingerprint(DEFAULT_CONFIG)  # deterministic in-process
    assert fp.fingerprint_version({"fingerprint": "x"}) == 1
    assert fp.fingerprint_version(None) == 1
    assert fp.fingerprint_version(eng) == 2


def test_a_copied_tree_hashes_the_same(tmp_path):
    """Content-addressed: the same files anywhere hash the same (a checkout path is not logic)."""
    root = _tree(tmp_path)
    other = tmp_path / "elsewhere" / "backend"
    shutil.copytree(root, other)
    assert fp.source_hash(root, ("calls", "domain")) == fp.source_hash(other, ("calls", "domain"))
