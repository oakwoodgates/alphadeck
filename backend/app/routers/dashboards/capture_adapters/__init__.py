"""Add a module exporting SLUG, INCLUDE_ARCHIVED and read(conn, request) to support a dashboard.

Discovery fails loud on duplicate/invalid adapters. Each adapter explicitly names its read
functions and frozen response paths; this is never a generic endpoint proxy.
"""

import importlib
import pkgutil
import re
import sys
from functools import lru_cache
from types import ModuleType


@lru_cache(maxsize=1)
def adapters() -> dict[str, ModuleType]:
    found: dict[str, ModuleType] = {}
    for item in sorted(pkgutil.iter_modules(sys.modules[__name__].__path__), key=lambda x: x.name):
        module = importlib.import_module(f"{__name__}.{item.name}")
        slug = getattr(module, "SLUG", None)
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise RuntimeError(f"invalid capture adapter slug: {item.name}")
        if slug in found or not callable(getattr(module, "read", None)):
            raise RuntimeError(f"duplicate or invalid capture adapter: {slug}")
        if not isinstance(getattr(module, "INCLUDE_ARCHIVED", None), bool):
            raise RuntimeError(f"capture adapter must declare archive scope: {slug}")
        if not isinstance(getattr(module, "THESIS_SCOPED", False), bool):
            raise RuntimeError(f"invalid thesis scope on capture adapter: {slug}")
        relationships = getattr(module, "relationships", None)
        if relationships is not None and not callable(relationships):
            raise RuntimeError(f"invalid relationships hook on capture adapter: {slug}")
        if not all(isinstance(value, str) for value in getattr(module, "LIMITATIONS", ())):
            raise RuntimeError(f"invalid limitations on capture adapter: {slug}")
        found[slug] = module
    return found
