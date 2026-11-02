"""One bounded HTTP capture; no arbitrary URLs, writes, proxies, or redirects."""

import ipaddress
import json
import re
from datetime import date, datetime
from urllib.parse import parse_qsl, urlsplit

import httpx

from .errors import AccessError
from .references import context_hash, validate_references

MAX_CAPTURE_BYTES = 8 * 1024 * 1024
UUID_PATH = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def compile_response_paths(descriptor: dict):
    patterns = []
    for template in descriptor["response_paths"]:
        response_key("GET", template.replace("{uuid}", "00000000-0000-0000-0000-000000000000"), {})
        if "{" in template.replace("{uuid}", "") or "}" in template.replace("{uuid}", ""):
            raise AccessError(
                "invalid_configuration", "Unsupported response path template in catalog."
            )
        patterns.append(
            re.compile(re.escape(template).replace(re.escape("{uuid}"), UUID_PATH) + r"\Z")
        )
    return patterns


def loopback_base(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError
        host = parsed.hostname or ""
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError
        if not parsed.port:
            raise ValueError
    except ValueError:
        raise AccessError(
            "invalid_configuration",
            "Base URLs must be loopback HTTP(S) origins with an explicit port.",
        ) from None
    return value.rstrip("/")


def response_key(method: str, path: str, query: dict) -> tuple:
    if method != "GET" or not path.startswith("/") or path.startswith("//"):
        raise AccessError(
            "invalid_capture", "Capture contains an unsafe response path or HTTP method."
        )
    if any(char in path for char in ("%", "\\", "?", "#")) or any(
        part in {".", ".."} for part in path.split("/")
    ):
        raise AccessError("invalid_capture", "Capture contains a noncanonical response path.")
    if not isinstance(query, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in query.items()
    ):
        raise AccessError("invalid_capture", "Capture query fields must be strings.")
    return method, path, tuple(sorted(query.items()))


def request_key(method: str, url: str) -> tuple:
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len(dict(pairs)) != len(pairs):
        raise AccessError("blocked_request", "Duplicate API query fields are blocked.")
    return response_key(method, parsed.path.removeprefix("/api"), dict(pairs))


def validate_capture(envelope: dict, dashboard: str, descriptor: dict | None = None) -> dict:
    if descriptor is None:
        from .catalog import Catalog

        descriptor = Catalog().describe(dashboard)
    patterns = compile_response_paths(descriptor)
    try:
        if envelope["schema_version"] != "1" or envelope["dashboard"] != dashboard:
            raise ValueError
        context, consistency = envelope["context"], envelope["consistency"]
        if (
            date.fromisoformat(envelope["asof"]).isoformat() != envelope["asof"]
            or datetime.fromisoformat(envelope["captured_at"].replace("Z", "+00:00")).tzinfo is None
        ):
            raise ValueError
        if context["scope"] != "deployment_visibility_not_authorization" or context[
            "clock"
        ] not in {"record", "public"}:
            raise ValueError
        if not isinstance(context["clock_response"], dict):
            raise ValueError
        for key in ("current_night", "action_day", "market_today"):
            date.fromisoformat(context["clock_response"][key])
        for key in ("decisions_asof", "countdown_origin"):
            date.fromisoformat(context[key])
        context_hash(envelope)
        if not isinstance(context["clock_response"]["market_tz"], str) or (
            context["clock"] == "public" and not context["known_at"]
        ):
            raise ValueError
        if (
            consistency["database"] != "repeatable_read_read_only"
            or consistency["generation_stable"] is not True
            or consistency["generation_before"] != consistency["generation_after"]
        ):
            raise ValueError
        responses = envelope["responses"]
        if not isinstance(responses, list) or not responses or len(responses) > 2048:
            raise ValueError
        seen = set()
        for response in responses:
            key = response_key(response["method"], response["path"], response["query"])
            if (
                key in seen
                or response["status"] != 200
                or "body" not in response
                or not any(pattern.fullmatch(response["path"]) for pattern in patterns)
            ):
                raise ValueError
            seen.add(key)
        if not any(
            response["path"] == "/clock"
            and response["query"] == {}
            and response["body"] == context["clock_response"]
            for response in responses
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise AccessError(
            "invalid_capture", "Backend returned an incomplete or inconsistent capture envelope."
        ) from None
    validate_references(envelope)
    return envelope


class CaptureClient:
    def __init__(self, base: str, *, timeout: float = 130, transport=None):
        self.base = loopback_base(base)
        self.timeout = timeout
        self.transport = transport

    async def capture(
        self,
        descriptor: dict,
        settings: dict,
        asof: str | None,
        *,
        expected_context_hash: str | None = None,
    ) -> dict:
        slug = descriptor["slug"]
        if not descriptor["capabilities"]["data"] or not descriptor.get("response_paths"):
            raise AccessError(
                "unsupported_capability",
                "No read-only capture route is enabled for this dashboard.",
            )
        query = {"dashboard": slug}
        if asof is not None:
            query["asof"] = asof
        if expected_context_hash is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", expected_context_hash):
                raise AccessError("invalid_configuration", "Expected context hash is invalid.")
            query["expected_context_hash"] = expected_context_hash
        for field in descriptor["backend_query_fields"]:
            if field not in descriptor["settings_schema"]["properties"] or not isinstance(
                settings[field], (str, bool, int, float)
            ):
                raise AccessError(
                    "invalid_configuration", "Catalog requested an unsupported backend field."
                )
            value = settings[field]
            query[field] = str(value).lower() if isinstance(value, bool) else str(value)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "GET", self.base + "/dashboards/capture", params=query
                ) as response:
                    if response.status_code != 200:
                        if response.status_code == 409:
                            error_content = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(error_content) + len(chunk) > 4096:
                                    break
                                error_content.extend(chunk)
                            try:
                                if json.loads(error_content).get("detail") == "context_changed":
                                    raise AccessError(
                                        "context_changed",
                                        "Temporal context changed; read a new parent snapshot before following its link.",
                                    )
                            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                                pass
                        known_errors = {
                            409: (
                                "capture_changed",
                                "Capture changed while reading; retry for a stable snapshot.",
                            ),
                            413: ("capture_limit", "Backend capture exceeds its configured limit."),
                            422: (
                                "invalid_view",
                                "Backend rejected this dashboard date or view settings.",
                            ),
                        }
                        if response.status_code in known_errors:
                            code, message = known_errors[response.status_code]
                            raise AccessError(code, message, retryable=response.status_code == 409)
                        raise AccessError(
                            "capture_failed",
                            "Backend capture returned HTTP " + str(response.status_code) + ".",
                            retryable=response.status_code >= 500,
                        )
                    if "json" not in response.headers.get("content-type", ""):
                        raise AccessError("invalid_capture", "Backend capture did not return JSON.")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_CAPTURE_BYTES:
                            raise AccessError(
                                "capture_too_large", "Backend capture exceeds the 8 MiB limit."
                            )
            envelope = validate_capture(json.loads(content), slug, descriptor)
            expected_asof = (
                asof
                or envelope["context"]["clock_response"][
                    "current_night" if descriptor["asofAware"] else "market_today"
                ]
            )
            if envelope["asof"] != expected_asof:
                raise AccessError(
                    "capture_mismatch",
                    "Backend capture did not acknowledge the requested dashboard date.",
                )
            return envelope
        except httpx.HTTPError:
            raise AccessError(
                "backend_unavailable", "Local backend capture failed or timed out.", retryable=True
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AccessError("invalid_capture", "Backend capture is not valid JSON.") from None
