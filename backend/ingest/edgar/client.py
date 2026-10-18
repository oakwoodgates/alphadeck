from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from domain.market_time import market_tz
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.http import RateLimiter, polite_get
from ingest.instrument import note, timed

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

# K1 (2026-10-17) — a FINISHED day's EDGAR index is immutable too. A key under `daily-index/` (or
# `full-index/`) that names a YYYYMMDD date — `daily-index/master.20261015.idx`, the SPAC radar's spine — is
# cached forever ONCE BOTH hold: the date is strictly before TODAY IN MARKET TIME (never UTC: at 22:30 ET the
# UTC date has already rolled over, and tonight's index is still growing), and the cached copy was written at
# or after 06:00 ET on the following day (`_INDEX_FINAL_HOUR`, when EDGAR reopens), i.e. after that day's
# index could still change. The second condition is load-bearing, not caution: the nightly radar caches
# TODAY's index at ~22:45 ET while it is still growing, and freezing that copy the moment the date turned
# "past" would lose every filing indexed after it — so an in-day copy is refreshed once more under the normal
# TTL, and only that later copy is frozen. Today's and future dates, an undated key (the quarterly
# `full-index/<yyyy>/QTR<q>/master.idx` still grows) and an impossible date all stay mutable. Same mechanism
# as `forms/`: decided on the key in `_is_stale`, never a per-call flag.
_DATED_INDEX = re.compile(r"^(?:daily-index|full-index)/(?:.*[/.])?(\d{8})\.idx$")
_INDEX_FINAL_HOUR = 6


def _now() -> float:
    """The clock the freshness rules read (one seam, so a test can pin "now" in market time)."""
    return time.time()


_DEFAULT_CACHE_TTL_S = (
    12 * 3600
)  # < the daily cron period, so the nightly run always sees an expired index

# THE RECURRING PASS's TTL (G1) — what `pipeline/daily.py`, `radar/spac.py` and `radar/shell_sweep.py`
# build their clients with. The 12h default above is an INTERACTIVE dial: its clock starts when a given
# COMPANY's key was last fetched, so any daytime read (the Admin "Run daily now", a boot catch-up, the
# on-promote ingest, a Workbench pull) left that company warm for up to 12h and the 22:30 pass served the
# warm index — blind to everything filed after the daytime fetch. A recurring pass must never read a
# DAYTIME-warm index.
#
# Five minutes, NOT zero, and the difference is measured, not stylistic:
#   - WITHIN one pass, the three filing legs (`_form4_leg`, `_form8k_leg`, `_schedule13_leg`) each call
#     `fetch_submissions` for the SAME `submissions/CIK<10>.json` key, milliseconds apart, and their
#     docstrings promise the 2nd and 3rd reads are free. `_is_stale` is `now - mtime > ttl`, so at ttl=0 a
#     file written milliseconds ago is already stale: MEASURED 3 live fetches for 3 back-to-back reads at
#     ttl=0 versus 1 at ttl=300 — i.e. zero TRIPLES the per-company index cost (~1,500 requests a night on
#     a 500-name universe) for no freshness gain. EDGAR politeness is a correctness requirement here, not a
#     courtesy (`docs/DATA_SOURCES.md` §EDGAR etiquette).
#   - ACROSS passes it still closes the gap: EDGAR accepts filings 06:00-22:00 ET, so nothing can be filed
#     in the five minutes before the 22:30 pass, and no daytime warmth survives five minutes. MEASURED: a
#     20-minute-old key refetches at ttl=300.
# The residual, accepted and documented: a SECOND pass started within five minutes of another reads the
# first pass's cache for the companies it reached last — which is why the R4 freeze page keeps its
# `--catch-up` exemption (`pipeline/daily.assess_health`).
RECURRING_CACHE_TTL_S = 300

# Q17 — THE FILING-GATED CLASS. A third key class, between immutable and plain-mutable: `companyfacts/`
# changes only when the company files something carrying XBRL facts, so on a RECURRING client (one built
# with the recurring dial — `recurring` below) it is re-pulled only when it is uncached, when the company's
# cached submissions index shows a qualifying filing accepted since it was fetched, or after a weekly
# backstop (`ingest/edgar/companyfacts_gate.py` — the rule, the MEASURED form set, and why). The decision is
# made HERE, on the key class, so no caller threads anything: the fundamentals leg just builds a recurring
# client. On an INTERACTIVE client (the 12h default: the Workbench extract, a standalone CLI) the class is
# plain-mutable exactly as before — the Workbench's freshness is unchanged.
#
# The gate reads the index from DISK only (never a fetch, never a `live_fetches` count): the nightly filing
# legs already re-pull it at the recurring TTL. Fail-safe still points one way — no index, an unreadable
# one, or an undatable qualifying row all mean DUE.
_FILING_GATED_PREFIXES = ("companyfacts/",)

# X8 — the NEGATIVE cache for that class: a 404 (a company data.sec.gov serves no companyfacts for — MEASURED
# 7 basket names on 2026-10-17, foreign / OTC filers) writes `<key>.404` beside where the document would be,
# and a recurring client then re-asks only when the SAME gate says due (a new qualifying filing, or the
# backstop) — instead of re-asking every night. The marker is a cache artifact like the document: an
# interactive client ignores it (it always re-asks, as before), and any successful fetch removes it.
_NOT_FOUND_SUFFIX = ".404"


class CachedNotFound(Exception):
    """A RECURRING client answered a filing-gated key from its negative cache: data.sec.gov returned 404 for
    it at ``observed_at`` and nothing the company filed since says to re-ask. No request was made.

    It carries ``response.status_code == 404`` so every caller that already treats a live companyfacts 404 as
    "this company has none" (``annual_shares._companyfacts_or_none``) reads it identically."""

    def __init__(self, cache_key: str, observed_at: datetime) -> None:
        self.cache_key = cache_key
        self.observed_at = observed_at
        self.response = SimpleNamespace(status_code=404)
        super().__init__(
            f"{cache_key}: no such document (HTTP 404, cached {observed_at.isoformat(timespec='seconds')}; "
            "re-asked on a new XBRL-bearing filing or the weekly backstop)"
        )


def _is_http_404(exc: BaseException) -> bool:
    return getattr(getattr(exc, "response", None), "status_code", None) == 404


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

    @property
    def recurring(self) -> bool:
        """Is this a RECURRING-pass client — built with the recurring dial (``cache_ttl_s <=
        RECURRING_CACHE_TTL_S``)? The dial IS the marker: every recurring constructor already passes
        ``RECURRING_CACHE_TTL_S`` (G1), so no second knob can drift from it. It switches on the filing-gated
        class and the negative cache (Q17 / X8); an interactive client never sees either."""
        return self.cache_ttl_s <= RECURRING_CACHE_TTL_S

    def _gated(self, cache_key: str) -> bool:
        return self.recurring and cache_key.startswith(_FILING_GATED_PREFIXES)

    def _gate_due(self, fetched_at: float, cache_key: str) -> bool:
        """Q17 — is a filing-gated key (fetched, or 404-marked, at ``fetched_at``) due? Reads the company's
        cached submissions index from DISK (never a fetch); an absent or unreadable index is passed as
        ``None``, which the gate treats as due."""
        from ingest.edgar.companyfacts_gate import companyfacts_due  # lazy: it imports submissions

        name = cache_key.split("/", 1)[1]  # "CIK<10>.json" — the same file name under submissions/
        subs: dict[str, Any] | None
        try:
            subs = json.loads((self.cache_dir / "submissions" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            subs = None
        if not isinstance(subs, dict):
            subs = None
        return companyfacts_due(fetched_at=fetched_at, now=_now(), submissions=subs).due

    @staticmethod
    def _final_dated_index(path: Path, cache_key: str) -> bool:
        """K1 — is this a dated EDGAR index key for a FINISHED day whose cached copy was written after
        that day's index stopped changing? (The rule and why both halves: ``_DATED_INDEX`` above.)
        """
        m = _DATED_INDEX.match(cache_key)
        if m is None:
            return False
        try:
            day = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            return False  # not a real date -> stays mutable
        tz = market_tz()
        today: date = datetime.fromtimestamp(_now(), tz).date()
        if not day < today:
            return False  # today's (still growing) or a future date
        final_at = datetime.combine(
            day + timedelta(days=1), clock_time(_INDEX_FINAL_HOUR), tzinfo=tz
        )
        return path.stat().st_mtime >= final_at.timestamp()

    def _is_stale(self, path: Path, cache_key: str) -> bool:
        """Is this cached key past its freshness? The key classes: immutable prefixes are NEVER stale
        (cache forever), and neither is a FINISHED day's dated index cached after it finished (K1); a
        filing-gated key on a RECURRING client is stale only when the Q17 gate says due; every other prefix
        is mutable and expires after ``cache_ttl_s``. Default-refresh: an unrecognized
        prefix is treated as mutable, so a future endpoint is safe-by-default (staleness never silent).
        """
        if cache_key.startswith(_IMMUTABLE_PREFIXES):
            return False
        if self._final_dated_index(path, cache_key):
            return False  # K1 — a finished day's index, cached after it finished
        if self._gated(cache_key):
            return self._gate_due(path.stat().st_mtime, cache_key)
        return (_now() - path.stat().st_mtime) > self.cache_ttl_s

    def get_text(self, url: str, cache_key: str) -> str:
        path = self.cache_dir / cache_key
        marker = path.with_name(path.name + _NOT_FOUND_SUFFIX)
        if path.exists():
            # Serve the cache UNLESS it's a stale mutable key AND we're allowed to refetch. With
            # allow_live=False (tests / --no-live) a stale hit is still served — better stale than a
            # CacheMiss; the TTL only forces a refetch when we can actually fetch.
            if not self.allow_live or not self._is_stale(path, cache_key):
                note("sec_cached")  # X5 — a request answered from the disk cache (no network)
                return path.read_text(encoding="utf-8")
        elif not self.allow_live:
            raise CacheMiss(f"{cache_key} not cached (live pulls disabled)")
        elif self._gated(cache_key) and marker.exists():
            # X8 — a known 404 is re-asked only when the gate says due; otherwise answer it from the
            # negative cache, with no request and no `live_fetches` count (nothing reached out).
            observed = marker.stat().st_mtime
            if not self._gate_due(observed, cache_key):
                note("sec_cached_404")  # X5 — answered from the negative cache (no network)
                raise CachedNotFound(cache_key, datetime.fromtimestamp(observed, timezone.utc))
        # decided to hit the network (a cache miss or a stale-mutable refresh) — the freeze detector
        self.live_fetches += 1
        # X5 — the same decision, counted on the pass leg that made it (`pipeline/pass_timings.py`)
        note("sec_live")
        try:
            text = self._fetch(url)
        except Exception as exc:
            if cache_key.startswith(_FILING_GATED_PREFIXES) and _is_http_404(exc):
                # remember the 404 (any client may write the marker; only a recurring one reads it)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(url, encoding="utf-8")  # (re)write refreshes the mtime
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")  # overwrite refreshes the mtime → fresh again
        if cache_key.startswith(_FILING_GATED_PREFIXES):
            marker.unlink(missing_ok=True)  # the company has companyfacts now — any 404 is history
        return text

    def get_json(self, url: str, cache_key: str) -> dict[str, Any]:
        if cache_key.startswith("submissions/"):
            # X5 — a company's submissions index (fetch or cache read + parse) is timed as its OWN leg
            # inside whichever pass leg read it; a no-op outside a metered pass (`ingest/instrument.py`)
            return timed("submissions")(self._get_json)(url, cache_key)
        return self._get_json(url, cache_key)

    def _get_json(self, url: str, cache_key: str) -> dict[str, Any]:
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
