"""Transport-only navigation and captured JSON references; no domain interpretation."""

import hashlib
import json
import re

from .errors import AccessError

CONTEXT_FIELDS = (
    "clock_response",
    "clock",
    "known_at",
    "decisions_asof",
    "countdown_origin",
    "scope",
)
MAX_REFERENCES = 2048


def context_hash(envelope: dict) -> str:
    payload = {
        "asof": envelope["asof"],
        **{field: envelope["context"][field] for field in CONTEXT_FIELDS},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()


def pointer_value(body, pointer: str):
    if (
        not isinstance(pointer, str)
        or len(pointer.encode("utf-8")) > 4096
        or (pointer and not pointer.startswith("/"))
    ):
        raise AccessError("invalid_capture", "Captured reference has an invalid JSON pointer.")
    if not pointer:
        return body
    parts = pointer[1:].split("/")
    if len(parts) > 64 or any(re.search(r"~(?![01])", part) for part in parts):
        raise AccessError("invalid_capture", "Captured reference has an invalid JSON pointer.")
    value = body
    for part in parts:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif (
            isinstance(value, list)
            and re.fullmatch(r"0|[1-9][0-9]*", key)
            and len(key) <= 10
            and int(key) < len(value)
        ):
            value = value[int(key)]
        else:
            raise AccessError(
                "invalid_capture", "Captured reference does not resolve to captured JSON."
            )
    return value


def validate_references(envelope: dict) -> None:
    """Validate every backend recipe once, before exposing or following any recipe."""
    for field, kind in (("navigation", "fresh_capture"), ("references", "captured_json")):
        entries = envelope.get(field, [])
        if not isinstance(entries, list) or len(entries) > MAX_REFERENCES:
            raise AccessError(
                "invalid_capture", "Capture navigation/reference limits were exceeded."
            )
        ids = set()
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("id"), str)
                or not 1 <= len(entry["id"]) <= 256
                or entry["id"] in ids
                or entry.get("kind") != kind
            ):
                raise AccessError(
                    "invalid_capture",
                    "Capture contains an invalid or duplicate navigation/reference identifier.",
                )
            ids.add(entry["id"])
            if field == "navigation":
                destination = entry.get("destination")
                if (
                    not isinstance(destination, dict)
                    or not isinstance(destination.get("dashboard"), str)
                    or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", destination["dashboard"])
                    or not isinstance(destination.get("settings"), dict)
                    or len(destination["settings"]) > 32
                    or len(json.dumps(destination).encode()) > 65536
                ):
                    raise AccessError(
                        "invalid_capture", "Capture contains an invalid navigation destination."
                    )
            else:
                index = entry.get("response_index")
                if type(index) is not int or not 0 <= index < len(envelope["responses"]):
                    raise AccessError(
                        "invalid_capture", "Captured reference response index is out of range."
                    )
                for key in ("label", "category"):
                    if (
                        key in entry
                        and entry[key] is not None
                        and (not isinstance(entry[key], str) or len(entry[key]) > 2000)
                    ):
                        raise AccessError(
                            "invalid_capture", "Captured reference metadata exceeds its limits."
                        )
                pointer_value(envelope["responses"][index]["body"], entry.get("pointer"))


def advertised_entry(snapshot, target_id: str, entry_id: str, *, field: str, kind: str) -> dict:
    targets = [
        target for target in snapshot.view.get("targets", []) if target.get("id") == target_id
    ]
    if len(targets) != 1:
        raise AccessError("unknown_target", "Target ID is absent or ambiguous in this snapshot.")
    advertised = targets[0].get("links" if field == "navigation" else "source_refs", [])
    if not isinstance(advertised, list) or not any(
        isinstance(entry, dict) and entry.get("id") == entry_id and entry.get("kind") == kind
        for entry in advertised
    ):
        raise AccessError(
            "unknown_link" if field == "navigation" else "unknown_reference",
            "Identifier is not advertised by this captured target.",
        )
    entries = [
        entry
        for entry in snapshot.envelope.get(field, [])
        if entry.get("id") == entry_id and entry.get("kind") == kind
    ]
    if len(entries) != 1:
        raise AccessError(
            "unknown_link" if field == "navigation" else "unknown_reference",
            "Identifier is absent or ambiguous in this captured envelope.",
        )
    return entries[0]
