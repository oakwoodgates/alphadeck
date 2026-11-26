"""Massive.com's split reference — the SECOND source the split list confirms against (split plan S1).

``GET {polygon_base}/stocks/v1/splits?execution_date.gte=YYYY-MM-DD&limit=5000&sort=execution_date.asc`` (W6-1;
``/v3/reference/splits`` is deprecated) returns the whole market's splits (``{id, ticker, execution_date,
split_from, split_to, adjustment_type, historical_adjustment_factor}``), paged by a ``next_url`` cursor. One query
covers every name: the request carries a DATE and nothing else — no ticker, no thesis term ever leaves the box.
``split_from`` OLD shares -> ``split_to`` NEW shares (16 -> 1 for a 1-for-16 reverse split), fractional values
included (PBM 6.25 -> 1), future dates included (the reference lists announced splits). MEASURED 2026-11-25: 3,521
rows from 2024-10-01 in ONE page (no ``next_url``), every value a JSON float (``16.0``, read as the v3 endpoint's
``16``). Against the v3 pull of the same night (3,760 rows, 4 pages): 3,518 shared ids with the same date and ratio
(3 under the current ticker); the 242 v3-only rows are 239 mutual funds and 3 ids re-issued with the same values.

- **Massive.com's label is evidence.** ``adjustment_type`` (``domain.splits.ADJUSTMENT_TYPES``: forward_split,
  reverse_split, stock_dividend) rides each row; a missing or unknown word reads ``None`` and is COUNTED
  (``SplitPull.unlabeled``), never a reason to drop the row. ``historical_adjustment_factor`` is never read: it is
  cumulative over LATER splits (GPUS 2024-11-25 35:1 = 147.639444 = 35 x 4.21827), so it moves whenever one lands.
- **The fallback.** ``Settings.polygon_splits_path`` = ``/v3/reference/splits`` reads the deprecated endpoint while
  it lives (``limit=1000&order=asc&sort=execution_date``; MEASURED 2026-10-20: 2,219 rows from 2025-08-01 in 3
  pages), with no label (``None``, not counted). The cache key is the URL, so a switch is a structural miss.
- **The key's ABSENCE is the off switch.** ``default_split_source`` returns ``None`` with no
  ``MASSIVE_API_KEY`` and the caller does nothing — never a keyless request.
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
from domain.splits import ADJUSTMENT_TYPES, PolygonEvidence
from ingest import CacheMiss
from ingest.funds import polygon as _funds_polygon
from ingest.http import polite_get

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "polygon_cache" / "splits"
SPLITS_PATH_V1 = "/stocks/v1/splits"  # the default (``Settings.polygon_splits_path``)
SPLITS_PATH_V3 = "/v3/reference/splits"  # the deprecated endpoint, the setting's fallback
PAGE_LIMIT = 5000  # v1: 3,521 rows from 2024-10-01 fit one page
PAGE_LIMIT_V3 = 1000
# a runaway cursor is an error, never an endless loop (MEASURED: v3 3 pages for 14 months of the whole market)
MAX_PAGES = 40


class PolygonSplitsError(Exception):
    """A split-reference fetch failed for real (auth / plan / server / network) — message key-scrubbed."""


@dataclass
class SplitPull:
    """One pull: every parsed row, the pages read, the rows skipped as unreadable and, on the v1 endpoint, the rows
    kept with no known label (each counted, #9)."""

    rows: list[PolygonEvidence] = field(default_factory=list)
    pages: int = 0
    skipped: int = 0
    live_pages: int = 0
    unlabeled: int = 0


def splits_url(since: date, path: str | None = None) -> str:
    """The first page's URL WITHOUT the apiKey (the cache identity and every message use this form). ``path``
    defaults to ``Settings.polygon_splits_path``; the v3 path keeps the v3 query."""
    path = path or get_settings().polygon_splits_path
    if path == SPLITS_PATH_V3:
        params: dict[str, object] = {
            "execution_date.gte": since.isoformat(),
            "limit": PAGE_LIMIT_V3,
            "order": "asc",
            "sort": "execution_date",
        }
    else:
        params = {
            "execution_date.gte": since.isoformat(),
            "limit": PAGE_LIMIT,
            "sort": "execution_date.asc",
        }
    return f"{get_settings().polygon_base}{path}?{urlencode(params)}"


def _dec(v: object) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        d = Decimal(str(v)) if not isinstance(v, Decimal) else v
    except (InvalidOperation, ValueError):
        return None
    if not d > 0:
        return None
    # an integral value reads as the integer: v1 serves ``16.0`` where v3 served ``16`` (the same stored numeric)
    return d.to_integral_value() if d == d.to_integral_value() else d


def _label(v: object) -> str | None:
    """Massive.com's ``adjustment_type``, or ``None`` when missing or not one of ``ADJUSTMENT_TYPES``."""
    word = v.strip().lower() if isinstance(v, str) else None
    return word if word in ADJUSTMENT_TYPES else None


def parse_splits_page(text: str) -> tuple[list[PolygonEvidence], str | None, int]:
    """``(rows, next_url, skipped)`` for one page body, v1 or v3. A row missing its id / ticker / date, or with a
    non-positive ratio, is skipped and counted. Each row carries Massive.com's label when it states a known one
    (``None`` otherwise: the v3 endpoint states none); ``historical_adjustment_factor`` is never read.
    """
    payload = json.loads(text, parse_float=Decimal)
    if not isinstance(payload, dict):
        raise ValueError("Massive.com splits: the page is not a JSON object")
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
                adjustment_type=_label(r.get("adjustment_type")),
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
                pre=_funds_polygon.LIMITER.acquire,  # the ONE Massive.com budget (shared with fund shares)
                backoff_base=_funds_polygon.RETRY_WAIT_S,
                backoff_cap=_funds_polygon.RETRY_WAIT_S,
            )
        except Exception as e:
            # `from None` throughout: an httpx message embeds the full URL, apiKey included
            if httpx is not None and isinstance(e, httpx.HTTPStatusError):
                raise PolygonSplitsError(
                    f"Massive.com splits: HTTP {e.response.status_code} for {clean_url}"
                ) from None
            raise PolygonSplitsError(
                f"Massive.com splits: {clean_url} failed: {self._scrub(str(e))}"
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
                f"no cached Massive.com splits page for {clean_url!r} (live pulls disabled)"
            )
        text = self._fetch(clean_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text, True

    def splits_since(self, since: date, *, allow_live: bool) -> SplitPull:
        """Every split with ``execution_date >= since``, all pages, from ``Settings.polygon_splits_path``."""
        out = SplitPull()
        path = get_settings().polygon_splits_path
        labeled = path != SPLITS_PATH_V3  # the v3 endpoint states no label: nothing to count
        url: str | None = splits_url(since, path)
        while url is not None:
            if out.pages >= MAX_PAGES:
                raise PolygonSplitsError(
                    f"Massive.com splits: more than {MAX_PAGES} pages from {since.isoformat()} — refusing a "
                    "runaway cursor"
                )
            clean = self._scrub(url)
            text, live = self._page(clean, allow_live=allow_live)
            try:
                rows, nxt, skipped = parse_splits_page(text)
            except ValueError as e:
                raise PolygonSplitsError(
                    f"Massive.com splits: unreadable page {clean}: {e}"
                ) from None
            out.rows.extend(rows)
            out.skipped += skipped
            if labeled:
                out.unlabeled += sum(1 for r in rows if r.adjustment_type is None)
            out.pages += 1
            out.live_pages += int(live)
            url = nxt
        return out


def default_split_source(**kw: object) -> PolygonSplitSource | None:
    """The configured source, or ``None`` when no ``MASSIVE_API_KEY`` is set (the off switch)."""
    key = get_settings().polygon_api_key
    if not key:
        return None
    return PolygonSplitSource(api_key=key, **kw)  # type: ignore[arg-type]
