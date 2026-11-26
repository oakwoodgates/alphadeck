"""Massive.com's grouped RAW daily bars — the one read a night behind the tape cross-check (W6-3a).

``GET {polygon_base}/v2/aggs/grouped/locale/us/market/stocks/{session}?adjusted=false&include_otc=true`` returns
every US stock's bar for one session, AS TRADED (no split adjustment), OTC included: ``{T, o, h, l, c, v, vw, n,
t}`` per row, plus ``otc: true`` on an OTC row. MEASURED 2026-11-25 (a read-only probe of the free key, sessions
11-18..11-25): 16,760-17,271 rows a session (~1.9 MB), a Saturday 200 with 0 rows; the free key served the
session that had just closed by 21:17 ET (``status`` ``DELAYED``; older days ``OK``), and a session not yet
served is an HTTP 403 ``NOT_AUTHORIZED`` "Attempted to request today's data before end of day" — never an
empty list. Massive.com writes a class share with a dot (``BRK.B``; the master and Yahoo write ``BRK-B``).

- **The key's ABSENCE is the off switch.** ``default_bar_source`` returns ``None`` with no ``MASSIVE_API_KEY``
  and the caller does nothing — never a keyless request.
- **Politeness.** The free tier is ~5 requests/min: every live read waits on the SAME module-shared limiter
  as the split reference and the fund-shares adapter (``ingest.funds.polygon.LIMITER``, >= 13 s apart), and a
  429 waits the minute out before the retry — the ``ingest/polygon_splits.py`` read exactly.
- **The key never leaks.** URLs are built and cached key-stripped; the key is appended only at the socket,
  every raised message is scrubbed (an httpx error embeds the full request URL) and raised ``from None``.
- **Not served yet is a status.** The 403 "before end of day" raises ``NotServedYet``, which is NOT a
  ``MassiveBarsError``: the caller records it and judges nothing. It is never cached.
- **Cache.** One file per session under ``data/polygon_cache/grouped/`` (``<session>.json``: the session
  names it, never the key or the URL), a COMPACT re-serialization (``T -> [o, h, l, c, v, otc]``), not the
  vendor body; files of sessions more than ``CACHE_KEEP_DAYS`` old are pruned on write.
- **Never a guess.** Values parse as exact decimals (``parse_float=Decimal``); a row missing its ticker,
  close or volume, or carrying a non-numeric or negative value, is skipped and COUNTED, never patched.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from domain.market_time import market_today
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.funds import polygon as _funds_polygon
from ingest.http import polite_get

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "polygon_cache" / "grouped"
GROUPED_PATH = "/v2/aggs/grouped/locale/us/market/stocks"
CACHE_KEEP_DAYS = 30

# The ONE Massive.com request budget (``ingest/funds/polygon.py``): the split reference and the fund shares pace
# on this same limiter, so the grouped read never spends the free tier a second time.
LIMITER = _funds_polygon.LIMITER
RETRY_WAIT_S = _funds_polygon.RETRY_WAIT_S
_SLEEP = time.sleep  # the 429-wait sleeper (``polite_get``'s ``sleep=`` seam)

# the words of the 403 Massive.com answers for a session it has not served yet (measured 2026-11-25 21:24 ET)
_NOT_SERVED = "before end of day"
_STATUS_HINTS = {
    401: " — bad MASSIVE_API_KEY",
    403: " — the key's plan does not cover this endpoint",
}
_CACHE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


class MassiveBarsError(Exception):
    """A grouped-bars read failed for real (auth / plan / server / network / an unreadable body) — message
    key-scrubbed."""


class NotServedYet(Exception):
    """Massive.com has not served the session yet (HTTP 403 "before end of day"): a STATUS, never an error."""


@dataclass(frozen=True)
class MassiveBar:
    """One raw (as-traded) daily bar. ``otc`` is Massive.com's own venue flag."""

    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal
    otc: bool = False


@dataclass
class GroupedDay:
    """One session's grouped bars by Massive.com symbol. ``status`` is the body's (``OK`` / ``DELAYED``);
    ``skipped`` the unreadable rows; ``live`` = read from Massive.com on this call (else the cache).
    """

    session: date
    status: str | None
    bars: dict[str, MassiveBar] = field(default_factory=dict)
    skipped: int = 0
    live: bool = False

    def get(self, symbol: str | None) -> MassiveBar | None:
        return self.bars.get(symbol) if symbol else None


def grouped_url(session: date) -> str:
    """The session's URL WITHOUT the apiKey (every message and the request log use this form)."""
    return (
        f"{get_settings().polygon_base}{GROUPED_PATH}/{session.isoformat()}"
        "?adjusted=false&include_otc=true"
    )


def massive_symbol(symbol: str | None) -> str | None:
    """The vendor symbol as Massive.com writes it: a class share's dash becomes a dot (``BRK-B`` -> ``BRK.B``,
    measured); anything else is unchanged (upper-cased). ``None`` for a blank symbol (nothing to look up).
    """
    if symbol is None or not symbol.strip():
        return None
    return symbol.strip().upper().replace("-", ".")


def _num(v: object) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _bar(o: object, h: object, low: object, c: object, v: object, otc: object) -> MassiveBar | None:
    close, volume = _num(c), _num(v)
    if close is None or volume is None or close <= 0 or volume < 0:
        return None
    return MassiveBar(_num(o), _num(h), _num(low), close, volume, otc is True)


def parse_grouped(text: str, *, session: date) -> GroupedDay:
    """One grouped-day body as a ``GroupedDay``. A row missing ``T`` / ``c`` / ``v``, or carrying a non-numeric,
    non-positive close or a negative volume, is skipped and counted. Raises ``ValueError`` on a body that is not
    a JSON object."""
    payload = json.loads(text, parse_float=Decimal)
    if not isinstance(payload, dict):
        raise ValueError("Massive.com grouped bars: the body is not a JSON object")
    out = GroupedDay(session=session, status=_status(payload))
    for r in payload.get("results") or []:
        sym = r.get("T") if isinstance(r, Mapping) else None
        bar = (
            _bar(r.get("o"), r.get("h"), r.get("l"), r.get("c"), r.get("v"), r.get("otc"))
            if isinstance(sym, str) and sym.strip()
            else None
        )
        if bar is None:
            out.skipped += 1
            continue
        out.bars[sym.strip()] = bar  # the vendor's own spelling (a preferred reads ``SCHWpD``)
    return out


def _status(payload: Mapping[str, Any]) -> str | None:
    s = payload.get("status")
    return str(s) if s is not None else None


def _compact(day: GroupedDay) -> str:
    """The cache form: ``{"session", "status", "skipped", "bars": {T: [o, h, l, c, v, otc]}}``, every number
    written as its exact decimal text (``str(Decimal)`` is valid JSON for every finite value)."""

    def num(d: Decimal | None) -> str:
        return "null" if d is None else str(d)

    rows = ",".join(
        f"{json.dumps(sym)}:[{num(b.open)},{num(b.high)},{num(b.low)},{num(b.close)},{num(b.volume)},"
        f"{'true' if b.otc else 'false'}]"
        for sym, b in day.bars.items()
    )
    head = json.dumps(
        {"session": day.session.isoformat(), "status": day.status, "skipped": day.skipped}
    )
    return f'{head[:-1]},"bars":{{{rows}}}}}'


def _from_compact(text: str, *, session: date) -> GroupedDay:
    payload = json.loads(text, parse_float=Decimal)
    out = GroupedDay(
        session=session, status=_status(payload), skipped=int(payload.get("skipped") or 0)
    )
    for sym, row in (payload.get("bars") or {}).items():
        bar = _bar(*row) if isinstance(row, list) and len(row) == 6 else None
        if bar is None:
            out.skipped += 1
            continue
        out.bars[sym] = bar
    return out


Fetch = Callable[[str], str]


class Limiter(Protocol):
    def acquire(self) -> None: ...


class MassiveBarSource:
    """The keyed grouped-bars adapter. ``fetch`` is the socket seam (a test passes a fake that never touches the
    network); by default it is the paced, key-appending ``polite_get`` on ``limiter`` (the shared budget).
    """

    name = "polygon"

    def __init__(
        self,
        *,
        api_key: str,
        cache_dir: Path | None = None,
        fetch: Fetch | None = None,
        today: Callable[[], date] = market_today,
        limiter: Limiter | None = None,
        sleep: Callable[[float], None] | None = None,
        keep_days: int = CACHE_KEEP_DAYS,
    ) -> None:
        if not api_key:
            raise ValueError("MassiveBarSource requires a non-empty api_key")
        self._api_key = api_key
        self._cache_dir = cache_dir or _DEFAULT_CACHE
        self._fetch = fetch or self._live_fetch
        self._today = today
        self._limiter = limiter or LIMITER
        self._sleep = sleep or _SLEEP
        self._keep_days = keep_days

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
                pre=self._limiter.acquire,  # the ONE Massive.com budget (split reference, fund shares)
                backoff_base=RETRY_WAIT_S,  # a 429 on a per-MINUTE tier clears next minute
                backoff_cap=RETRY_WAIT_S,
                sleep=self._sleep,
            )
        except Exception as e:
            # `from None` throughout: an httpx message embeds the full URL, apiKey included
            if httpx is not None and isinstance(e, httpx.HTTPStatusError):
                code = e.response.status_code
                if code == 403 and _NOT_SERVED in _body(e.response).lower():
                    raise NotServedYet(
                        f"Massive.com had not served {_session_of(clean_url)} yet (HTTP 403: before end of "
                        "day)"
                    ) from None
                raise MassiveBarsError(
                    f"Massive.com grouped bars: HTTP {code} for {clean_url}{_STATUS_HINTS.get(code, '')}"
                ) from None
            raise MassiveBarsError(
                f"Massive.com grouped bars: {clean_url} failed: {self._scrub(str(e))}"
            ) from None
        return resp.text

    def day(self, session: date, *, allow_live: bool) -> GroupedDay:
        """One session's grouped bars — cache-first. Raises ``CacheMiss`` (cache-only and not cached),
        ``NotServedYet`` (a status) or ``MassiveBarsError``."""
        path = self._cache_dir / f"{session.isoformat()}.json"
        if path.exists():
            try:
                return _from_compact(path.read_text(encoding="utf-8"), session=session)
            except (ValueError, TypeError, AttributeError):
                if not allow_live:
                    raise CacheMiss(
                        f"the cached Massive.com grouped day {session.isoformat()} is unreadable"
                    ) from None
        if not allow_live:
            raise CacheMiss(
                f"no cached Massive.com grouped day for {session.isoformat()} (live pulls disabled)"
            )
        clean = grouped_url(session)
        text = self._fetch(clean)
        try:
            day = parse_grouped(text, session=session)
        except ValueError as e:
            raise MassiveBarsError(
                f"Massive.com grouped bars: unreadable body for {clean}: {self._scrub(str(e))}"
            ) from None
        day.live = True
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(_compact(day), encoding="utf-8")
        self._prune()
        return day

    def _prune(self) -> None:
        """Remove the cached days of sessions more than ``keep_days`` before today (only ``<date>.json``)."""
        cutoff = self._today() - timedelta(days=self._keep_days)
        for p in self._cache_dir.iterdir():
            m = _CACHE_NAME.match(p.name)
            if m is None:
                continue
            try:
                if date.fromisoformat(m.group(1)) < cutoff:
                    p.unlink()
            except (ValueError, OSError):
                continue  # a prune never costs the read


def _body(resp: Any) -> str:
    try:
        return str(resp.text)
    except Exception:  # noqa: BLE001 — an unreadable error body reads as no body
        return ""


def _session_of(clean_url: str) -> str:
    tail = clean_url.split("?", 1)[0].rsplit("/", 1)[-1]
    return tail or clean_url


def default_bar_source(**kw: Any) -> MassiveBarSource | None:
    """The configured source, or ``None`` when no ``MASSIVE_API_KEY`` is set (the off switch)."""
    key = get_settings().polygon_api_key
    if not key:
        return None
    return MassiveBarSource(api_key=key, **kw)
