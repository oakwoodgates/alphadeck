"""The request-limits GUARD (security hardening PR 3, item 9).

Every request body model and every query parameter reachable from a route is walked, nested models included. A
field typed plain ``str``, ``float``, ``date``, ``list[...]`` or ``dict[...]`` must carry a limit: one of the
bounded types of ``domain/text_types.py`` (the ``Bounded`` marker), or a standard constraint that bounds it (a
``max_length`` or a ``pattern`` on a string, ``allow_inf_nan=False`` on a float, a ``max_length`` on a list), or an
entry in the short, commented ``EXEMPT`` list below. Response-only models are not walked.

The other half of the hard constraint is structural too: no DOMAIN model (built from stored rows on every read) may
carry a request limit, so a stored value over a limit can never make a card unloadable.
"""

from __future__ import annotations

import enum
import types
import typing
from datetime import date, datetime
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from uuid import UUID

from annotated_types import GroupedMetadata, MaxLen
from fastapi.routing import APIRoute
from pydantic import AllowInfNan, BaseModel

from app.main import app
from domain.text_types import Bounded

# Fields that legitimately carry no limit of their own. Keep it SHORT; each entry says why.
EXEMPT: dict[str, str] = {
    # steward only (super admin, trusted): the date a steward ratifies for a held insider row; the route checks it
    # against the filing. Steward routes are outside PR 3's scope (docs/AUTH.md §Request limits).
    "AdminHoldApproveIn.ratified_date": "steward-only; checked against the filing by the route",
    # steward only: the Backtest sweep page's selectors, compared against the names of stored sweep files and
    # never used to build a path (backtest/store.py).
    # steward only: a backup's label, reduced to [a-z0-9-] before it names a file (pipeline/backup.py _slugify).
    "BackupCreateIn.label": "steward-only; slugified before use",
    "GET /backtest/sweep?pass_id": "steward-only; matched against stored sweep names",
    "GET /backtest/sweep?dial": "steward-only; matched against stored sweep names",
    "GET /backtest/sweep?metric_slice": "steward-only; matched against stored sweep names",
}

_OK_LEAVES = (bool, int, UUID, datetime, type(None), bytes)


def _flatten(meta: typing.Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for m in meta:
        if isinstance(m, GroupedMetadata):
            out.extend(_flatten(m))
        else:
            out.append(m)
            inner = getattr(m, "metadata", None)  # a FieldInfo inside Annotated
            if isinstance(inner, list):
                out.extend(_flatten(inner))
    return out


def _has(meta: list[Any], pred) -> bool:
    return any(pred(m) for m in meta)


def _bounded_str(meta: list[Any]) -> bool:
    return _has(
        meta,
        lambda m: isinstance(m, (Bounded, MaxLen)) or getattr(m, "pattern", None) is not None,
    )


def _bounded_float(meta: list[Any]) -> bool:
    return _has(
        meta,
        lambda m: isinstance(m, Bounded)
        or (isinstance(m, AllowInfNan) and m.allow_inf_nan is False)
        or getattr(m, "allow_inf_nan", None) is False,
    )


class _Walker:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.seen_models: set[type] = set()
        self.used_exemptions: set[str] = set()
        self.visited: list[str] = []

    def problem(self, where: str, what: str) -> None:
        if where in EXEMPT:
            self.used_exemptions.add(where)
            return
        self.problems.append(f"{where}: {what}")

    def check(self, tp: Any, meta: list[Any], where: str) -> None:
        origin = get_origin(tp)
        if origin is Annotated:
            base, *extra = get_args(tp)
            self.check(base, meta + _flatten(extra), where)
            return
        if origin in (Union, types.UnionType):
            for arg in get_args(tp):
                self.check(arg, meta, where)
            return
        if origin is Literal:
            return
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            self.model(tp)
            return
        if origin in (list, set, tuple, typing.Sequence):
            if not _has(meta, lambda m: isinstance(m, (MaxLen, Bounded))):
                self.problem(where, f"{tp} has no item limit")
            for arg in get_args(tp):
                if arg is not Ellipsis:
                    self.check(arg, [], f"{where}[]")
            return
        if origin is dict or tp is dict:
            if not _has(meta, lambda m: isinstance(m, Bounded)):
                self.problem(where, f"{tp} is an unbounded mapping")
            return
        if tp is str:
            if not _bounded_str(meta):
                self.problem(where, "plain str")
            return
        if tp is float:
            if not _bounded_float(meta):
                self.problem(where, "plain float (NaN / Infinity accepted)")
            return
        if tp is date:
            if not _has(meta, lambda m: isinstance(m, Bounded)):
                self.problem(where, "plain date (any year accepted)")
            return
        if isinstance(tp, type) and isinstance(getattr(tp, "BOUNDED", None), Bounded):
            return  # a bounded type written as a class (``AsofDate``)
        if tp is Any:
            self.problem(where, "Any")
            return
        if isinstance(tp, type) and (issubclass(tp, enum.Enum) or issubclass(tp, _OK_LEAVES)):
            return
        self.problem(where, f"unrecognized type {tp!r}")

    def model(self, model: type[BaseModel]) -> None:
        if model in self.seen_models:
            return
        self.seen_models.add(model)
        self.visited.append(model.__name__)
        for name, f in model.model_fields.items():
            self.check(f.annotation, _flatten(f.metadata), f"{model.__name__}.{name}")


def _walk_dependant(dep, route_name: str, walker: _Walker) -> None:
    for p in dep.query_params:
        walker.check(
            p.field_info.annotation, _flatten(p.field_info.metadata), f"{route_name}?{p.name}"
        )
    for p in dep.body_params:
        walker.check(p.field_info.annotation, _flatten(p.field_info.metadata), f"{route_name} body")
    for sub in dep.dependencies:
        _walk_dependant(sub, route_name, walker)


def _walk_app() -> _Walker:
    walker = _Walker()
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for method in sorted(r.methods):
            _walk_dependant(r.dependant, f"{method} {r.path}", walker)
    return walker


def test_every_request_field_and_query_parameter_carries_a_limit():
    walker = _walk_app()
    assert walker.problems == [], "\n".join(walker.problems)


def test_the_walk_reaches_the_nested_request_models():
    walker = _walk_app()
    for name in (
        "PromoteThesisRequest",
        "BasketMemberIn",
        "SegmentIn",
        "ExtractedFactIn",
        "LocatedPassageIn",
        "CatalystIn",
        "RatifyShares",
        "LoginRequest",
        "TriageSessionPut",
    ):
        assert name in walker.visited, name


def test_every_exemption_is_still_needed():
    walker = _walk_app()
    assert set(EXEMPT) == walker.used_exemptions


def _domain_models() -> list[type[BaseModel]]:
    from domain.extraction import ExtractionResult
    from domain.thesis import Thesis

    return [Thesis, ExtractionResult]


def test_no_domain_model_carries_a_request_limit():
    """The models built from stored rows and server-side extraction carry none of the request types: their reads can
    never be refused for a value stored before (or outside) a limit."""
    seen: set[type] = set()
    found: list[str] = []

    def visit(tp: Any, meta: list[Any], where: str) -> None:
        if _has(meta, lambda m: isinstance(m, Bounded)):
            found.append(where)
        origin = get_origin(tp)
        if origin is Annotated:
            base, *extra = get_args(tp)
            visit(base, meta + _flatten(extra), where)
            return
        for arg in get_args(tp):
            visit(arg, [], where)
        if isinstance(tp, type) and issubclass(tp, BaseModel) and tp not in seen:
            seen.add(tp)
            for name, f in tp.model_fields.items():
                visit(f.annotation, _flatten(f.metadata), f"{tp.__name__}.{name}")

    for m in _domain_models():
        visit(m, [], m.__name__)
    assert found == []
    assert {"Thesis", "BasketMember", "Segment", "ExtractedFact", "LocatedPassage"} <= {
        t.__name__ for t in seen
    }


def test_the_frontend_form_limits_mirror_the_backend():
    """``frontend/src/api/limits.ts`` (the forms' ``maxLength``) carries the same numbers as ``domain/text_types``."""
    import re
    from pathlib import Path

    from domain import text_types as tt

    src = (
        Path(__file__).resolve().parents[3] / "frontend" / "src" / "api" / "limits.ts"
    ).read_text(encoding="utf-8")
    fe = {k: int(v) for k, v in re.findall(r"^\s*(\w+): (\d+),", src, re.MULTILINE)}
    assert fe == {
        "line": tt.LINE_MAX,
        "prose": tt.PROSE_MAX,
        "narrative": tt.NARRATIVE_MAX,
        "term": tt.TERM_MAX,
        "url": tt.URL_MAX,
        "email": tt.EMAIL_MAX,
        "passwordMax": tt.PASSWORD_MAX,
        "search": tt.SEARCH_Q_MAX,
    }
