"""Polygon's split reference — the SECOND source the split list confirms against (split plan S1).

``GET {polygon_base}/v3/reference/splits?execution_date.gte=YYYY-MM-DD&limit=1000&order=asc&sort=execution_date``
returns the whole market's splits (``{id, ticker, execution_date, split_from, split_to}``), paged by a
``next_url`` cursor. One query covers every name: the request carries a DATE and nothing else — no ticker, no
thesis term ever leaves the box. MEASURED 2026-10-20 (the split-tape diagnosis): 2,219 rows from 2025-08-01 in
3 pages, ``split_from`` OLD shares -> ``split_to`` NEW shares (16 -> 1 for a 1-for-16 reverse split),
fractional values included (PBM 6.25 -> 1; a 121.6 -> 122.6 stock-dividend row), future dates included (the
reference lists announced splits).

- **The key's ABSENCE is the off switch.** ``default_split_source`` returns ``None`` with no
  ``POLYGON_API_KEY`` and the caller does nothing — never a keyless request.
- **Politeness.** The free tier is ~5 requests/min: every live page waits on the SAME module-shared limiter
  the fund-shares adapter uses (``ingest.funds.polygon.LIMITER``, >= 13 s apart), and a 429 waits the minute
  out before the retry.
- **The key never leaks.** URLs are built and cached key-stripped; the key is appended only at the socket,
  and every raised message is scrubbed (an httpx error embeds the full request URL).
- **Cache.** Each page is cached under ``data/polygon_cache/splits/`` keyed by its key-stripped URL AND the
  market day it was pulled — a new day is a structural miss, so a dry run and the ``--apply`` that follows it
  the same day read the same pages, and tomorrow re-pulls.
- **Never a guess.** Values parse as exact decimals (``parse_float=Decimal``); a row missing a field or
  carrying a non-positive ratio is skipped and COUNTED, never patched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from domain.market_time import market_today
from domain.settings import get_settings
from domain.splits import PolygonEvidence
from ingest import CacheMiss
from ingest.funds import polygon as _funds_polygon
from ingest.http import polite_get

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "polygon_cache" / "splits"
SPLITS_PATH = "/v3/reference/splits"
PAGE_LIMIT = 1000
# a runaway cursor is an error, never an endless loop (MEASURED: 3 pages for 14 months of the whole market)
MAX_PAGES = 40


class PolygonSplitsError(Exception):
    """A split-reference fetch failed for real (auth / plan / server / network) — message key-scrubbed."""


@dataclass
class SplitPull:
    """One pull: every parsed row, the pages read, and the rows skipped as unreadable (counted, #9)."""

    rows: list[PolygonEvidence] = field(default_factory=list)
    pages: int = 0
    skipped: int = 0
    live_pages: int = 0


def splits_url(since: date) -> str:
    """The first page's URL WITHOUT the apiKey (the cache identity and every message use this form)."""
    q = urlencode(
        {
            "execution_date.gte": since.isoformat(),
            "limit": PAGE_LIMIT,
            "order": "asc",
            "sort": "execution_date",
        }
    )
    return f"{get_settings().polygon_base}{SPLITS_PATH}?{q}"


def _dec(v: object) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        d = Decimal(str(v)) if not isinstance(v, Decimal) else v
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def parse_splits_page(text: str) -> tuple[list[PolygonEvidence], str | None, int]:
    """``(rows, next_url, skipped)`` for one page body. A row missing its id / ticker / date, or with a
    non-positive ratio, is skipped and counted."""
    payload = json.loads(text, parse_float=Decimal)
    if not isinstance(payload, dict):
        raise ValueError("polygon splits: the page is not a JSON object")
    rows: list[PolygonEvidence] = []
    skipped = 0
    for r in payload.get("results") or []:
        try:
            ev = PolygonEvidence(
                id=str(r["id"]),
                ticker=str(r["ticker"]).strip().upper(),
                execution_date=date.fromisoformat(str(r["execution_date"])),
                split_from=_dec(r.get("split_from")),  # type: ignore[arg-type]
                split_to=_dec(r.get("split_to")),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if not ev.ticker or ev.split_from is None or ev.split_to is None:
            skipped += 1
            continue
        rows.append(ev)
    nxt = payload.get("next_url")
    return rows, (str(nxt) if nxt else None), skipped


Fetch = Callable[[str], str]


class PolygonSplitSource:
    """The keyed split-reference adapter. ``fetch`` is the socket seam (a test passes a fake that never
    touches the network); by default it is the paced, key-appending ``polite_get``."""

    name = "polygon"

    def __init__(
        self,
        *,
        api_key: str,
        cache_dir: Path | None = None,
        fetch: Fetch | None = None,
        today: Callable[[], date] = market_today,
    ) -> None:
        if not api_key:
            raise ValueError("PolygonSplitSource requires a non-empty api_key")
        self._api_key = api_key
        self._cache_dir = cache_dir or _DEFAULT_CACHE
        self._fetch = fetch or self._live_fetch
        self._today = today

    def _scrub(self, s: str) -> str:
        return s.replace(self._api_key, "***")

    def _live_fetch(self, clean_url: str) -> str:
        sep = "&" if "?" in clean_url else "?"
        try:
            import httpx  # lazy, mirroring the other clients
        except ImportError:  # pragma: no cover — polite_get itself needs httpx
            httpx = None
        try:
            resp = polite_get(
                f"{clean_url}{sep}apiKey={self._api_key}",
                timeout=get_settings().http_timeout_s,
                pre=_funds_polygon.LIMITER.acquire,  # the ONE Polygon budget (shared with fund shares)
                backoff_base=_funds_polygon.RETRY_WAIT_S,
                backoff_cap=_funds_polygon.RETRY_WAIT_S,
            )
        except Exception as e:
            # `from None` throughout: an httpx message embeds the full URL, apiKey included
            if httpx is not None and isinstance(e, httpx.HTTPStatusError):
                raise PolygonSplitsError(
                    f"polygon splits: HTTP {e.response.status_code} for {clean_url}"
                ) from None
            raise PolygonSplitsError(
                f"polygon splits: {clean_url} failed: {self._scrub(str(e))}"
            ) from None
        return resp.text

    def _page(self, clean_url: str, *, allow_live: bool) -> tuple[str, bool]:
        """``(body, live)`` — cache-first per (URL, market day)."""
        digest = hashlib.sha1(clean_url.encode("utf-8")).hexdigest()[:20]
        path = self._cache_dir / f"{self._today().isoformat()}.{digest}.json"
        if path.exists():
            return path.read_text(encoding="utf-8"), False
        if not allow_live:
            raise CacheMiss(
                f"no cached polygon splits page for {clean_url!r} (live pulls disabled)"
            )
        text = self._fetch(clean_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text, True

    def splits_since(self, since: date, *, allow_live: bool) -> SplitPull:
        """Every split with ``execution_date >= since``, all pages."""
        out = SplitPull()
        url: str | None = splits_url(since)
        while url is not None:
            if out.pages >= MAX_PAGES:
                raise PolygonSplitsError(
                    f"polygon splits: more than {MAX_PAGES} pages from {since.isoformat()} — refusing a "
                    "runaway cursor"
                )
            clean = self._scrub(url)
            text, live = self._page(clean, allow_live=allow_live)
            try:
                rows, nxt, skipped = parse_splits_page(text)
            except ValueError as e:
                raise PolygonSplitsError(f"polygon splits: unreadable page {clean}: {e}") from None
            out.rows.extend(rows)
            out.skipped += skipped
            out.pages += 1
            out.live_pages += int(live)
            url = nxt
        return out


def default_split_source(**kw: object) -> PolygonSplitSource | None:
    """The configured source, or ``None`` when no ``POLYGON_API_KEY`` is set (the off switch)."""
    key = get_settings().polygon_api_key
    if not key:
        return None
    return PolygonSplitSource(api_key=key, **kw)  # type: ignore[arg-type]
