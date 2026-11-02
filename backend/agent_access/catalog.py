"""Descriptors are generated from the frontend registry; never infer capabilities."""

import json
from datetime import date
from importlib.resources import files

from jsonschema import Draft202012Validator

from .errors import AccessError


class Catalog:
    def __init__(self):
        document = json.loads(files("agent_access").joinpath("catalog.json").read_text("utf-8"))
        if document["schema_version"] != "1":
            raise ValueError("Unsupported dashboard catalog version")
        self.dashboards = sorted(document["dashboards"], key=lambda item: item["order"])
        self._by_slug = {item["slug"]: item for item in self.dashboards}
        for item in self.dashboards:
            Draft202012Validator.check_schema(item["settings_schema"])

    def describe(self, slug: str) -> dict:
        if slug not in self._by_slug:
            raise AccessError("unknown_dashboard", "Unknown dashboard slug; call list_dashboards.")
        return self._by_slug[slug]

    def prepare(self, slug: str, settings: dict | None, asof: str | None) -> tuple[dict, dict]:
        descriptor = self.describe(slug)
        if not descriptor["capabilities"]["observe"]:
            raise AccessError(
                "unsupported_capability",
                "This dashboard is discoverable but not yet capture-enabled.",
            )
        if asof is not None:
            try:
                if date.fromisoformat(asof).isoformat() != asof:
                    raise ValueError
            except (TypeError, ValueError):
                raise AccessError(
                    "invalid_asof", "asof must be an ISO date (YYYY-MM-DD)."
                ) from None
            if not descriptor["asofAware"]:
                raise AccessError("unsupported_asof", "This dashboard supports current data only.")
        merged = {**descriptor["defaults"], **(settings or {})}
        validator = Draft202012Validator(
            descriptor["settings_schema"], format_checker=Draft202012Validator.FORMAT_CHECKER
        )
        errors = sorted(validator.iter_errors(merged), key=lambda error: str(error.path))
        if errors:
            raise AccessError(
                "invalid_settings",
                "Settings do not match this dashboard's settings_schema; call describe_dashboard for valid fields and values.",
            )
        return descriptor, merged
