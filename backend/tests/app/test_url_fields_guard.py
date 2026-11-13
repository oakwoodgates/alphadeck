"""Guard (security hardening PR 4a, spec item 3): every URL a response carries passes the one wire rule.

A response model — any pydantic model reachable from a route's response model — that declares a field named
``url``, ``*_url``, ``href`` or ``source_ref`` typed as a string must type it ``domain.links.HttpUrlOut`` (the
value is ``None`` on the wire unless it is a plain http(s) URL with a host and no credentials), or be listed in
``EXEMPT`` below with the reason it stays text. The hostile-output cases then become ``null`` on the wire for
every such field.
"""

from __future__ import annotations

import re
import types
import typing
from collections.abc import Iterator
from typing import Annotated, Any, Union

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel, TypeAdapter

from app.main import app
from domain.links import HTTP_URL_RULE
from tests.domain.test_links import HOSTILE_URLS

_URL_NAME = re.compile(r"^(url|href|source_ref|.+_url)$")

# (model, field) -> why it stays text on the wire. Keep each reason honest; an entry that no longer matches a
# field fails ``test_every_exemption_is_still_needed``.
EXEMPT: dict[tuple[str, str], str] = {
    (
        "ExtractedFact",
        "source_ref",
    ): "round-tripped: the client sends the candidate back to /workbench/facts/explain and its source_ref "
    "into the ratify body (a null would 422 the request); the frontend renders it through ExternalLink",
    (
        "LocatedPassage",
        "source_ref",
    ): "the same domain model is the request body of /workbench/facts/explain (inside ExtractedFact); "
    "validating it would reject or rewrite the write path, so the frontend's ExternalLink decides",
    (
        "AgentsOut",
        "connector_url",
    ): "server-built (the configured public origin + the MCP path), shown as text and copied, never an href",
}


def _models_in(tp: Any) -> Iterator[type[BaseModel]]:
    origin = typing.get_origin(tp)
    if origin is Annotated:
        yield from _models_in(typing.get_args(tp)[0])
        return
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        yield tp
        return
    if origin is not None or isinstance(tp, types.UnionType):
        for arg in typing.get_args(tp):
            yield from _models_in(arg)


def _response_models() -> dict[str, type[BaseModel]]:
    seen: dict[type[BaseModel], None] = {}
    todo: list[type[BaseModel]] = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.response_model is not None:
            todo.extend(_models_in(route.response_model))
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen[m] = None
        for f in m.model_fields.values():
            todo.extend(_models_in(f.annotation))
    return {m.__name__: m for m in seen}


def _carries_str(tp: Any) -> bool:
    if tp is str:
        return True
    origin = typing.get_origin(tp)
    if origin is Annotated:
        return _carries_str(typing.get_args(tp)[0])
    if origin is Union or isinstance(tp, types.UnionType):
        return any(_carries_str(a) for a in typing.get_args(tp))
    return False


def _url_fields() -> list[tuple[str, str, bool]]:
    """(model, field, uses HttpUrlOut) for every url-named string field on a response model."""
    out = []
    for name, model in sorted(_response_models().items()):
        for fname, f in model.model_fields.items():
            if _URL_NAME.match(fname) and _carries_str(f.annotation):
                out.append((name, fname, any(m is HTTP_URL_RULE for m in f.metadata)))
    return out


def test_the_walk_sees_the_known_url_fields() -> None:
    # sanity: the walk reaches nested models in app/ and dashboards/ routers alike
    found = {(m, f) for m, f, _ in _url_fields()}
    assert ("ProvenanceOut", "url") in found
    assert ("EtfHoldingsOut", "source_ref") in found
    assert ("LocatedPassage", "source_ref") in found
    assert len(found) >= 25


def test_every_url_field_on_a_response_uses_the_wire_rule_or_is_exempt() -> None:
    bare = [f"{m}.{f}" for m, f, guarded in _url_fields() if not guarded and (m, f) not in EXEMPT]
    assert bare == [], (
        "type these HttpUrlOut (domain.links) or add an EXEMPT entry with the reason they stay text: "
        + ", ".join(bare)
    )


def test_every_exemption_is_still_needed() -> None:
    unguarded = {(m, f) for m, f, guarded in _url_fields() if not guarded}
    assert sorted(set(EXEMPT) - unguarded) == []


def _guarded_fields() -> list[tuple[str, str]]:
    return [(m, f) for m, f, guarded in _url_fields() if guarded]


@pytest.mark.parametrize("model_field", _guarded_fields(), ids=lambda mf: f"{mf[0]}.{mf[1]}")
def test_each_hostile_url_is_null_on_the_wire_for_every_guarded_field(
    model_field: tuple[str, str],
) -> None:
    model_name, field_name = model_field
    f = _response_models()[model_name].model_fields[field_name]
    adapter: TypeAdapter[Any] = TypeAdapter(Annotated[f.annotation, *f.metadata])  # type: ignore[valid-type]
    for hostile in HOSTILE_URLS:
        assert adapter.dump_python(adapter.validate_python(hostile), mode="json") is None
    good = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/"
    assert adapter.validate_python(good) == good
