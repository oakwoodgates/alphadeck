from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from domain.settings import get_settings
from ingest import CacheMiss
from ingest.http import RateLimiter

# Runtime cache lives under the repo's gitignored data/; tests/seed pass the committed fixtures dir.
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "doe_cache"


class UsaSpendingClient:
    """Thin, polite, cache-first USASpending client (the DOE feed's transport).

    Mirrors ``EdgarClient``: cache-first on disk; live pulls are explicit opt-in (``allow_live``). The
    test transport keeps ``allow_live=False`` so a cache miss raises ``CacheMiss`` and the suite never
    hits the network. ``spending_by_award`` is a POST, so the request body is hashed into the cache key
    — each distinct query (term × award-type group) caches as its own file.
    """

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        allow_live: bool = False,
        user_agent: str | None = None,
        max_per_sec: float | None = None,
    ) -> None:
        self.cache_dir = cache_dir or _DEFAULT_CACHE
        self.allow_live = allow_live
        s = get_settings()
        self.user_agent = user_agent or s.user_agent
        self._rate = RateLimiter(
            max_per_sec if max_per_sec is not None else s.usaspending_rate_per_sec
        )

    def search_awards(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST ``/search/spending_by_award/`` — cache key derived from the (stable-serialized) body."""
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return self._get_json(
            f"{get_settings().usaspending_api_base}/search/spending_by_award/",
            cache_key=f"search/{digest}.json",
            body=body,
        )

    def award_detail(self, generated_internal_id: str) -> dict[str, Any]:
        """GET ``/awards/{id}/`` — per-award detail (obligation, category, period of performance)."""
        safe = generated_internal_id.replace("/", "_")
        return self._get_json(
            f"{get_settings().usaspending_api_base}/awards/{generated_internal_id}/",
            cache_key=f"award/{safe}.json",
        )

    def _get_json(
        self, url: str, *, cache_key: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        path = self.cache_dir / cache_key
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if not self.allow_live:
            raise CacheMiss(f"{cache_key} not cached (live pulls disabled)")
        text = self._fetch(url, body)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return json.loads(text)

    def _fetch(self, url: str, body: dict[str, Any] | None) -> str:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.user_agent:  # USASpending doesn't mandate a UA, but we send one when configured
            headers["User-Agent"] = self.user_agent
        self._rate.acquire()
        timeout = get_settings().usaspending_timeout_s
        if body is None:
            resp = httpx.get(url, headers=headers, timeout=timeout)
        else:
            resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.text
