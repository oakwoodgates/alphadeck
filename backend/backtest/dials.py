"""THE DIAL PARTITION — which dials change the EVENT STREAM, and which only change how it composes.

`pipeline/core.py` runs one seam: every registered detector produces `SignalEvent`s for each member, then
`assemble_call` composes them into a CallCard. A dial read BEFORE that line changes what fired; a dial read
AT it changes only what the firing MEANS. The backtest needs that split because it is the memoization key:
a variant that moves only composition dials can reuse a cached event stream, and a sweep over those dials
then costs the assembler rather than the whole replay.

**Derived, never hand-listed.** A typed list would rot the first time a dial moved, and it would rot
SILENTLY -- a mis-classified dial does not fail, it just serves a stale cache, which is the worst failure
this could have. So the partition is read off the AST:

    bind(module)  = parameters annotated ``CallConfig``, plus ``DEFAULT_CONFIG``
    reads(module) = every ``<bound>.<attr>`` in it
    DETECTOR  = read under signals/ only      ASSEMBLER = read under calls/ only
    BOTH      = read under both               UNUSED    = read under neither

**One indirection has to be resolved by hand, and it is the reason a naive grep is wrong.**
`signals/theme_conviction.py` reads `cfg.own_conviction_kinds`, a `@property` returning
`conviction_kinds - {THEME_CONVICTION}`. A scan for `.conviction_kinds` inside `signals/` finds NOTHING,
so the naive answer files `conviction_kinds` as assembler-only -- and a cache keyed on that would reuse an
event stream the dial had in fact changed. `_PROPERTY_READS` names it, and a test fails if the property's
body stops matching.

**What this deliberately does NOT model** (stated so the next reader does not assume more than it claims):

- *Nested sub-model fields.* `corporate_event_items` is attributed; its per-item `.score` / `.liveness_days`
  are read off a local `policy`, not off `cfg`, and are invisible here. Harmless while the partition is over
  top-level fields, which it is.
- *Effect class.* `risk_block_severity` is read in both trees, but its signals-side reads only pick a copy
  string. It lands in BOTH, which is the conservative direction for a cache key -- over-invalidating is a
  slower run, under-invalidating is a wrong one.
- *Workbench reads.* `workbench/scoring.py` calls two detector functions with a cfg, so four DETECTOR dials
  are also Workbench dials. True, and irrelevant to the event-stream question this answers.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from domain.config import CallConfig

_BACKEND = Path(__file__).resolve().parents[1]
_DETECTOR_ROOTS = ("signals",)
_ASSEMBLER_ROOTS = ("calls",)
# `signals/display/` is structurally barred from importing CallConfig (display signals can never reach the
# call), so scanning it would only add noise.
_SKIP = ("signals/display",)

# A `@property` on CallConfig -> the FIELDS its body reads. Without this, a read of the property is
# invisible to the field-level partition. See the module docstring: this is the one case where the naive
# answer is not merely incomplete but WRONG.
_PROPERTY_READS: dict[str, frozenset[str]] = {
    "own_conviction_kinds": frozenset({"conviction_kinds"}),
}


@dataclass(frozen=True)
class Partition:
    """The four sets, plus the union that a cache key is built from."""

    detector: frozenset[str]
    assembler: frozenset[str]
    both: frozenset[str]
    unused: frozenset[str]

    @property
    def event_layer(self) -> frozenset[str]:
        """The dials a cached event stream must be keyed on: DETECTOR plus BOTH.

        BOTH is included deliberately. Over-invalidating costs a re-run; under-invalidating serves a stale
        stream and reports it as a measurement, and only one of those is recoverable."""
        return self.detector | self.both


def _modules(roots: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        for p in sorted((_BACKEND / root).rglob("*.py")):
            rel = p.relative_to(_BACKEND).as_posix()
            if not any(rel.startswith(s) for s in _SKIP):
                out.append(p)
    return out


def _cfg_bindings(tree: ast.AST) -> set[str]:
    """Names bound to a ``CallConfig`` in this module — every parameter annotated with it, plus the
    module-level default. Verified complete by a test: every ``CallConfig``-typed parameter in the repo is
    literally named ``cfg``, but the scan does not rely on that."""
    names = {"DEFAULT_CONFIG"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                ann = getattr(a, "annotation", None)
                if ann is not None and "CallConfig" in ast.unparse(ann):
                    names.add(a.arg)
    return names


def _reads(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = _cfg_bindings(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in bound:
                found.add(node.attr)
    # a property read stands for the fields its body reads
    resolved: set[str] = set()
    for name in found:
        resolved |= _PROPERTY_READS.get(name, frozenset({name}))
    return resolved


@lru_cache(maxsize=1)
def partition() -> Partition:
    """The partition over ``CallConfig``'s declared fields. Cached: it parses the signal and call trees."""
    fields = set(CallConfig.model_fields)
    det: set[str] = set()
    asm: set[str] = set()
    for p in _modules(_DETECTOR_ROOTS):
        det |= _reads(p)
    for p in _modules(_ASSEMBLER_ROOTS):
        asm |= _reads(p)
    det &= fields
    asm &= fields
    both = det & asm
    return Partition(
        detector=frozenset(det - both),
        assembler=frozenset(asm - both),
        both=frozenset(both),
        unused=frozenset(fields - det - asm),
    )


def property_body_reads(name: str) -> frozenset[str]:
    """The ``CallConfig`` fields a named ``@property`` actually reads off ``self`` — read from its SOURCE,
    so ``_PROPERTY_READS`` cannot quietly drift away from the property it describes."""
    prop = getattr(CallConfig, name)
    # dedent, not strip: the source comes back at class-body indentation and the DECORATOR line sits
    # above the def, so a bare strip leaves the body indented under nothing and ast.parse raises.
    tree = ast.parse(textwrap.dedent(inspect.getsource(prop.fget)))
    return frozenset(
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
        and n.attr in CallConfig.model_fields
    )
