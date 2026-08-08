"""Yahoo symbol search — the deterministic lookup that RESOLVES an OTC name's vendor price symbol.

The OTC price-symbol fix's discovery leg: ``security_master.ticker`` is the SEC's canonical ticker (FDCT),
but Yahoo indexes the full price history under a DIFFERENT vendor symbol (FDCTD). The suffix rule is
impossible (FDCT->FDCTD, VREOD->VREOF, CURLD->CURLF — different letters), so the symbol must be RESOLVED,
never guessed. ``search_quotes`` is the raw lookup: Yahoo's ``/v1/finance/search?q=…`` autocomplete, which
returns candidate quotes (symbol / name / quoteType / exchange). The DETERMINISTIC filtering + selection
(US-venue + exact-name-match + history confirmation) lives in the PURE ``securities/price_symbol.py`` — this
module only fetches and caches the payload (#3: no model, no guess — a vendor lookup, the same class as FIGI).

Cache-first, live only behind ``allow_live`` (the ``figi`` / ``eod_loader`` idiom): a resolved query's payload
is stable enough to cache, and the resolver is operator-triggered + infrequent, so the cache absorbs repeats.
``force_refresh`` (live only) bypasses a cache hit to re-pull. A cache MISS with live pulls disabled raises
``CacheMiss`` (the test-transport guard — the suite never hits the network). Polite by construction: a shared
``RateLimiter`` (~2/s) fronts every live fetch via ``polite_get``'s ``pre`` (429/5xx backoff on top).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from domain.settings import get_settings
from ingest import CacheMiss
from ingest.http import RateLimiter, polite_get

# Runtime cache lives under the repo's gitignored data/; tests pass a tmp dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "symbol_cache"

# Yahoo's search endpoint tolerates a modest cadence; keep it well under the EOD chart rate. Shared across
# callers (module-level) so a burst of resolutions still funnels through one ~2/s gate (the figi idiom).
_RATE = RateLimiter(2.0)


def _cache_key(query: str) -> str:
    """A filesystem-safe cache filename for a query (a ticker like "FDCT" or a name like "Vireo Growth").
    Lower-cased, non-alphanumeric runs collapsed to a single hyphen — a stable, human-readable key.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", query.strip().lower()).strip("-")
    return slug or "_blank"


def search_url(query: str) -> str:
    return f"{get_settings().yahoo_chart_base}/v1/finance/search?q={quote(query.strip())}"


def search_quotes(
    query: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    force_refresh: bool = False,
) -> dict:
    """Cache-first Yahoo symbol-search payload for ``query`` (``{"quotes": [...], ...}``). Live only behind
    ``allow_live``; ``force_refresh`` (live only) bypasses a cache hit to re-pull + overwrite. A cache MISS
    with live pulls disabled raises ``CacheMiss``. An empty/blank query returns an empty payload without any
    fetch (a genuinely-uncovered name — e.g. PMBHF — searches to nothing, honestly)."""
    if not query or not query.strip():
        return {"quotes": []}
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / f"{_cache_key(query)}.json"
    if path.exists() and not (force_refresh and allow_live):
        return json.loads(path.read_text(encoding="utf-8"))
    if not allow_live:
        raise CacheMiss(f"no cached symbol search for {query!r} (live pulls disabled)")
    resp = polite_get(  # 429/5xx backoff (D6 politeness), fronted by the shared ~2/s gate
        search_url(query),
        headers={"User-Agent": "Mozilla/5.0 (Alpha Deck research)"},
        timeout=get_settings().http_timeout_s,
        pre=_RATE.acquire,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.json()
