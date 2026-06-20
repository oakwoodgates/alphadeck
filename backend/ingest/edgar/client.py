from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from domain.settings import get_settings
from ingest import CacheMiss
from ingest.http import polite_get

# Runtime cache lives under the repo's gitignored data/; tests pass a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "edgar_cache"


class RateLimiter:
    """A minimal token-bucket gate: at most ``max_per_sec`` requests/second (SEC etiquette)."""

    def __init__(self, max_per_sec: float = 8.0) -> None:
        self._min_interval = 1.0 / max_per_sec
        self._last = 0.0

    def acquire(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


class EdgarClient:
    """Thin, polite, cache-first SEC client.

    Cache-first on disk; live pulls are explicit opt-in (``allow_live``) and require a declared
    User-Agent (SEC rule). The test transport keeps ``allow_live=False`` so a cache miss raises
    ``CacheMiss`` and the suite never hits the network.
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
        self._rate = RateLimiter(max_per_sec if max_per_sec is not None else s.edgar_rate_per_sec)

    def get_text(self, url: str, cache_key: str) -> str:
        path = self.cache_dir / cache_key
        if path.exists():
            return path.read_text(encoding="utf-8")
        if not self.allow_live:
            raise CacheMiss(f"{cache_key} not cached (live pulls disabled)")
        text = self._fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text

    def get_json(self, url: str, cache_key: str) -> dict[str, Any]:
        return json.loads(self.get_text(url, cache_key))

    def _fetch(self, url: str) -> str:
        if not self.user_agent:
            raise RuntimeError(
                "set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent with contact)"
            )
        # The token bucket throttles BEFORE each attempt (pre=acquire); polite_get adds 429/5xx backoff
        # on top, so a rate-limit response is retried politely instead of aborting the run.
        resp = polite_get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=get_settings().http_timeout_s,
            pre=self._rate.acquire,
        )
        return resp.text
