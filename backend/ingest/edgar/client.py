from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from domain.settings import get_settings
from ingest import CacheMiss
from ingest.http import RateLimiter, polite_get

# Runtime cache lives under the repo's gitignored data/; tests pass a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "edgar_cache"

# The cache is HETEROGENEOUS, so the freshness policy is keyed on the cache-key CLASS, not the call site
# (a per-call `ttl=` param is the #72 boolean wearing a timedelta — the next mutable endpoint forgets it).
# DEFAULT = REFRESH; only an explicit IMMUTABLE prefix is exempt. Fail-safe points one way: forgetting to
# exempt a truly-immutable key costs a re-fetch (bandwidth); forgetting to refresh a mutable one costs SILENT
# staleness — which is this whole class of bug (submissions=Form-4 discovery, efts=the universe,
# companyfacts=the extract's share counts all froze the same way; forms/<accession>/<doc> is the one
# genuinely immutable class — an accession's document never changes).
_IMMUTABLE_PREFIXES = ("forms/",)
_DEFAULT_CACHE_TTL_S = (
    12 * 3600
)  # < the daily cron period, so the nightly run always sees an expired index


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
        cache_ttl_s: float = _DEFAULT_CACHE_TTL_S,
    ) -> None:
        self.cache_dir = cache_dir or _DEFAULT_CACHE
        self.allow_live = allow_live
        self.cache_ttl_s = cache_ttl_s
        # How many times this client DECIDED to go to the network (a cache miss OR a stale-mutable refresh) —
        # the freeze detector. It counts ATTEMPTS, not successful pulls: incremented at the network-decision
        # point in `get_text` BEFORE `_fetch` runs, so it rises even if that fetch then errors. Counting
        # attempts (not successes) is the RIGHT thing here. A frozen index and a healthy nothing-filed night
        # produce IDENTICAL fact tallies (0 appended); the only difference is whether a request was even
        # attempted — so `0` on a live run == the cache never reached out == FROZEN (visible, pageable via the
        # daily cron's per-thesis run log). An EDGAR OUTAGE is the opposite shape — many attempts, zero data —
        # and must NOT read as a freeze: it doesn't (this counter climbs), and it's caught by `names_errored`,
        # not the freeze predicate. So `edgar_fetches` is "times we reached out", not "times we got data".
        self.live_fetches = 0
        s = get_settings()
        self.user_agent = user_agent or s.user_agent
        self._rate = RateLimiter(max_per_sec if max_per_sec is not None else s.edgar_rate_per_sec)

    def _is_stale(self, path: Path, cache_key: str) -> bool:
        """Is this cached key past its TTL? Immutable prefixes are NEVER stale (cache forever); every other
        prefix is mutable and expires after ``cache_ttl_s``. Default-refresh: an unrecognised prefix is
        treated as mutable, so a future endpoint is safe-by-default (staleness never silent)."""
        if cache_key.startswith(_IMMUTABLE_PREFIXES):
            return False
        return (time.time() - path.stat().st_mtime) > self.cache_ttl_s

    def get_text(self, url: str, cache_key: str) -> str:
        path = self.cache_dir / cache_key
        if path.exists():
            # Serve the cache UNLESS it's a stale mutable key AND we're allowed to refetch. With
            # allow_live=False (tests / --no-live) a stale hit is still served — better stale than a
            # CacheMiss; the TTL only forces a refetch when we can actually fetch.
            if not self.allow_live or not self._is_stale(path, cache_key):
                return path.read_text(encoding="utf-8")
        elif not self.allow_live:
            raise CacheMiss(f"{cache_key} not cached (live pulls disabled)")
        # decided to hit the network (a cache miss or a stale-mutable refresh) — the freeze detector
        self.live_fetches += 1
        text = self._fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")  # overwrite refreshes the mtime → fresh again
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
