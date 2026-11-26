"""Massive.com's ticker OVERVIEW — the reference evidence on a stale price row (W6-4).

``GET {polygon_base}/v3/reference/tickers/{T}`` returns one ticker's reference row; this reader keeps four of its
fields: ``ticker``, ``active``, ``type`` (Massive.com's code: ``CS``, ``ADRC``, ``WARRANT`` ...) and the date of
``delisted_utc``. MEASURED 2026-11-25 (a read-only probe of the free key): SNDK ``CS`` active; SCNI ``ADRC``
active; ACLEW and IXQWF — two stale price rows on prod — OTC ``WARRANT``s, active; NOTVQ, TMRC, MTMT and EULL an
HTTP 404 ``{"status": "NOT_FOUND", "message": "Ticker not found."}``. The delisted list
(``/v3/reference/tickers?active=false``) carried INDV inactive with ``delisted_utc`` 2026-11-03T00:00:00Z; that a
delisted ticker's OVERVIEW answers the same fields was not read (UNVERIFIED).

- **The key's ABSENCE is the off switch.** ``default_reference_source`` returns ``None`` with no
  ``MASSIVE_API_KEY``; the caller reads nothing.
- **Politeness.** The ONE Massive.com budget: every live read waits on the limiter the grouped bars, the split
  reference and the fund shares share (``ingest.massive_bars.LIMITER``, >= 13 s apart), and a 429 waits the
  minute out before the retry (the ``ingest/massive_bars.py`` read exactly).
- **The key never leaks.** URLs are built key-stripped; the key is appended only at the socket, every raised
  message is scrubbed (an httpx error embeds the full request URL) and raised ``from None``.
- **A 404 is ``None``:** Massive.com does not list the symbol — a clean miss, cached for the day like an answer.
- **Cache.** One file per (symbol, market day) under ``data/polygon_cache/reference/``
  (``<SYMBOL>.<day>.json``: the fields kept, never the vendor body, never the key); files of days more than
  ``CACHE_KEEP_DAYS`` before the read are pruned on write. A failure is never cached.
- **The evidence** a stale row carries is ``MassiveRef``: the symbol read, the market day of the read, whether
  Massive.com lists it, its ``active`` / ``type`` / delisted date, and the session the pass's grouped day shows
  it traded (``traded_on``; no request). Display and steward evidence only: never a class, never a call input.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from domain.settings import get_settings
from ingest import CacheMiss
from ingest import massive_bars as _bars
from ingest.http import polite_get

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "polygon_cache" / "reference"
REFERENCE_PATH = "/v3/reference/tickers"
CACHE_KEEP_DAYS = 30

# The ONE Massive.com request budget, by import (``ingest/massive_bars.py`` takes it from the fund-shares adapter)
LIMITER = _bars.LIMITER
RETRY_WAIT_S = _bars.RETRY_WAIT_S
_SLEEP = time.sleep  # the 429-wait sleeper (``polite_get``'s ``sleep=`` seam)

_STATUS_HINTS = {
    401: " — bad MASSIVE_API_KEY",
    403: " — the key's plan does not cover this endpoint",
}
# a symbol as Massive.com writes one (``massive_bars.massive_symbol``: upper case, a class share's dot); it names
# a URL path segment and a cache file, so nothing else is read
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,23}$")
# Massive.com's instrument type codes (``CS``, ``ADRC``, ``WARRANT``, ``ETF`` ...): anything else is not one
_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")
_CACHE_NAME = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,23}\.(\d{4}-\d{2}-\d{2})\.json$")


class MassiveReferenceError(Exception):
    """A ticker-overview read failed for real (auth / plan / server / network / an unreadable body) — message
    key-scrubbed. A 404 is not one (``None``)."""


@dataclass(frozen=True)
class TickerOverview:
    """The four fields kept from one ``/v3/reference/tickers/{T}`` answer. ``None`` = not stated (or not a value
    of the expected kind: never guessed)."""

    ticker: str
    active: bool | None
    type: str | None
    delisted_utc: date | None


def is_massive_symbol(symbol: object) -> bool:
    """Is ``symbol`` one this reader asks for (upper case, digits, a class share's dot or a dash; <= 24)?"""
    return isinstance(symbol, str) and _SYMBOL.match(symbol) is not None


def _symbol(symbol: str) -> str:
    if not is_massive_symbol(symbol):
        raise ValueError(f"{symbol!r} is not a Massive.com symbol")
    return symbol


def overview_url(symbol: str) -> str:
    """The symbol's overview URL WITHOUT the apiKey (every message uses this form). Raises ``ValueError`` for
    anything that is not a Massive.com symbol."""
    return f"{get_settings().polygon_base}{REFERENCE_PATH}/{_symbol(symbol)}"


def _bool(v: object) -> bool | None:
    return v if isinstance(v, bool) else None


def _type(v: object) -> str | None:
    return v if isinstance(v, str) and _TYPE.match(v) else None


def _day(v: object) -> date | None:
    """An ISO date, or the date of an ISO timestamp (``2026-11-03T00:00:00Z``); ``None`` otherwise."""
    if not isinstance(v, str) or len(v) < 10:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def parse_overview(text: str) -> TickerOverview:
    """One overview body. Raises ``ValueError`` on a body that is not a JSON object with a ``results`` object
    naming a ticker."""
    payload = json.loads(text)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict) or not isinstance(results.get("ticker"), str):
        raise ValueError("Massive.com ticker overview: no results object naming a ticker")
    return TickerOverview(
        ticker=results["ticker"],
        active=_bool(results.get("active")),
        type=_type(results.get("type")),
        delisted_utc=_day(results.get("delisted_utc")),
    )


def _compact(o: TickerOverview | None) -> str:
    if o is None:
        return json.dumps({"found": False})
    return json.dumps(
        {
            "found": True,
            "ticker": o.ticker,
            "active": o.active,
            "type": o.type,
            "delisted_utc": o.delisted_utc.isoformat() if o.delisted_utc else None,
        }
    )


def _from_compact(text: str) -> TickerOverview | None:
    payload = json.loads(text)
    if not isinstance(payload, dict) or "found" not in payload:
        raise ValueError("not a cached overview")
    if not payload["found"]:
        return None
    return parse_overview(json.dumps({"results": payload}))


Fetch = Callable[[str], "str | None"]


class Limiter(Protocol):
    def acquire(self) -> None: ...


class MassiveReferenceSource:
    """The keyed ticker-overview adapter. ``fetch`` is the socket seam (a test passes a fake that never touches
    the network; it returns the body, or ``None`` for a 404); by default it is the paced, key-appending
    ``polite_get`` on ``limiter`` (the shared budget)."""

    name = "polygon"

    def __init__(
        self,
        *,
        api_key: str,
        cache_dir: Path | None = None,
        fetch: Fetch | None = None,
        limiter: Limiter | None = None,
        sleep: Callable[[float], None] | None = None,
        keep_days: int = CACHE_KEEP_DAYS,
    ) -> None:
        if not api_key:
            raise ValueError("MassiveReferenceSource requires a non-empty api_key")
        self._api_key = api_key
        self._cache_dir = cache_dir or _DEFAULT_CACHE
        self._fetch = fetch or self._live_fetch
        self._limiter = limiter or LIMITER
        self._sleep = sleep or _SLEEP
        self._keep_days = keep_days

    def _scrub(self, s: str) -> str:
        return s.replace(self._api_key, "***")

    def _live_fetch(self, clean_url: str) -> str | None:
        sep = "&" if "?" in clean_url else "?"
        try:
            import httpx  # lazy, mirroring the other clients
        except ImportError:  # pragma: no cover — polite_get itself needs httpx
            httpx = None
        try:
            resp = polite_get(
                f"{clean_url}{sep}apiKey={self._api_key}",
                timeout=get_settings().http_timeout_s,
                pre=self._limiter.acquire,  # the ONE Massive.com budget
                backoff_base=RETRY_WAIT_S,  # a 429 on a per-MINUTE tier clears next minute
                backoff_cap=RETRY_WAIT_S,
                sleep=self._sleep,
            )
        except Exception as e:
            # `from None` throughout: an httpx message embeds the full URL, apiKey included
            if httpx is not None and isinstance(e, httpx.HTTPStatusError):
                code = e.response.status_code
                if code == 404:
                    return None  # Massive.com does not list the symbol: a clean miss
                raise MassiveReferenceError(
                    f"Massive.com ticker overview: HTTP {code} for {clean_url}"
                    f"{_STATUS_HINTS.get(code, '')}"
                ) from None
            raise MassiveReferenceError(
                f"Massive.com ticker overview: {clean_url} failed: {self._scrub(str(e))}"
            ) from None
        return resp.text

    def overview(self, symbol: str, *, day: date, allow_live: bool) -> TickerOverview | None:
        """``symbol``'s overview as of the market ``day`` — cache-first per (symbol, day). ``None`` = Massive.com
        does not list it (a 404). Raises ``CacheMiss`` (cache-only and not cached), ``MassiveReferenceError`` or
        ``ValueError`` (not a symbol)."""
        clean = overview_url(symbol)
        path = self._cache_dir / f"{symbol}.{day.isoformat()}.json"
        if path.exists():
            try:
                return _from_compact(path.read_text(encoding="utf-8"))
            except (ValueError, TypeError, AttributeError):
                if not allow_live:
                    raise CacheMiss(
                        f"the cached Massive.com overview of {symbol} for {day} is unreadable"
                    ) from None
        if not allow_live:
            raise CacheMiss(
                f"no cached Massive.com overview of {symbol} for {day} (live pulls disabled)"
            )
        text = self._fetch(clean)
        if text is None:
            found = None
        else:
            try:
                found = parse_overview(text)
            except ValueError as e:
                raise MassiveReferenceError(
                    f"Massive.com ticker overview: unreadable body for {clean}: {self._scrub(str(e))}"
                ) from None
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(_compact(found), encoding="utf-8")
        self._prune(day)
        return found

    def _prune(self, day: date) -> None:
        """Remove the cached reads of days more than ``keep_days`` before ``day`` (only ``<SYMBOL>.<day>.json``)."""
        cutoff = day - timedelta(days=self._keep_days)
        for p in self._cache_dir.iterdir():
            m = _CACHE_NAME.match(p.name)
            if m is None:
                continue
            try:
                if date.fromisoformat(m.group(1)) < cutoff:
                    p.unlink()
            except (ValueError, OSError):
                continue  # a prune never costs the read


def default_reference_source(**kw: Any) -> MassiveReferenceSource | None:
    """The configured source, or ``None`` when no ``MASSIVE_API_KEY`` is set (the off switch)."""
    key = get_settings().polygon_api_key
    if not key:
        return None
    return MassiveReferenceSource(api_key=key, **kw)


# --- the evidence a stale price row carries ----------------------------------------------------------------------


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


@dataclass(frozen=True)
class MassiveRef:
    """Massive.com's word on a stale price row's symbol (W6-4) — EVIDENCE, never a class (``StaleTape.massive_ref``).

    ``symbol`` = the Massive.com symbol it is about (the vendor price symbol, a class share's dash a dot);
    ``read_on`` = the market day the overview was read (``None`` = not read: the night's cap, no key, a failed
    read); ``listed`` = Massive.com lists the symbol (``False`` = a 404; ``None`` = not read); ``active``,
    ``type`` and ``delisted_utc`` = its overview's (``None`` = not stated); ``traded_on`` = the session the pass's
    grouped day shows a trade (volume > 0) for it, else ``None``."""

    symbol: str
    read_on: date | None = None
    listed: bool | None = None
    active: bool | None = None
    type: str | None = None
    delisted_utc: date | None = None
    traded_on: date | None = None

    @classmethod
    def read(cls, symbol: str, day: date, overview: TickerOverview | None) -> MassiveRef:
        """The evidence of one overview read on market ``day`` (``overview`` ``None`` = the 404)."""
        if overview is None:
            return cls(symbol, read_on=day, listed=False)
        return cls(
            symbol,
            read_on=day,
            listed=True,
            active=overview.active,
            type=overview.type,
            delisted_utc=overview.delisted_utc,
        )

    def traded(self, session: date | None) -> MassiveRef:
        """This evidence with tonight's trade (``None`` = no trade seen tonight)."""
        return replace(self, traded_on=session)

    def describe(self) -> str:
        """The nightly report's words: "Massive.com: WARRANT, active" / "Massive.com: CS, inactive, delisted
        2026-11-03" / "Massive.com lists no ticker TMRC" / "Massive.com: traded 2026-11-25"; ``""`` = nothing
        known. Massive.com's own type code, never paraphrased."""
        if self.listed is False:
            return f"Massive.com lists no ticker {self.symbol}"
        parts = []
        if self.type:
            parts.append(self.type)
        if self.active is not None:
            parts.append("active" if self.active else "inactive")
        if self.delisted_utc:
            parts.append(f"delisted {self.delisted_utc.isoformat()}")
        if self.traded_on:
            parts.append(f"traded {self.traded_on.isoformat()}")
        return f"Massive.com: {', '.join(parts)}" if parts else ""

    def to_json(self) -> dict[str, Any]:
        """The run artifact's shape (the row's ``massive_ref``)."""
        return {
            "symbol": self.symbol,
            "read_on": _iso(self.read_on),
            "listed": self.listed,
            "active": self.active,
            "type": self.type,
            "delisted_utc": _iso(self.delisted_utc),
            "traded_on": _iso(self.traded_on),
        }

    @classmethod
    def from_json(cls, raw: object) -> MassiveRef | None:
        """``to_json``'s inverse, fail-soft: ``None`` for anything without a Massive.com symbol; a field of the
        wrong kind reads ``None`` (never guessed)."""
        if not isinstance(raw, dict):
            return None
        symbol = raw.get("symbol")
        if not is_massive_symbol(symbol):
            return None
        return cls(
            symbol,
            read_on=_day(raw.get("read_on")),
            listed=_bool(raw.get("listed")),
            active=_bool(raw.get("active")),
            type=_type(raw.get("type")),
            delisted_utc=_day(raw.get("delisted_utc")),
            traded_on=_day(raw.get("traded_on")),
        )
