"""THE FETCH-AHEAD — a thesis's vendor reads run concurrently, AHEAD of its one serial writer (2026-10-21).

WHY. A nightly pass was latency-bound, not rate- or CPU-bound. MEASURED on prod's X5 timings (2026-10-20, a
686 s pass): the submissions leg spent 294 s on 1,001 SEC requests (~0.29 s each, strictly serial, ~3.4 req/s
against SEC's 10/s), the price leg 242 s on 740 Yahoo requests, also serial; the CPU-bound call assembly was
9.5 s for 12 theses. Repeated companies are not the lever (740 memberships, 688 distinct securities, 7%
overlap); overlapping the round trips, within the vendors' limits, is.

THE SHAPE — PARALLEL FETCH, SERIALIZED WRITE:

- ``ingest_thesis`` walks the basket exactly as before, on its ONE connection: per company it takes the W1
  company lock (``db/write_lock.py``), runs every leg, commits per leg. No second writer connection exists and
  no worker thread ever touches the database.
- Ahead of that walk, a pool of ``Settings.ingest_fetch_concurrency`` workers performs each upcoming company's
  VENDOR reads: its submissions index, the Form 4 and Form 4/A documents not yet stored, and its price pull.
  The writer PLANS each job (the master row and the accessions already stored — reads on its own connection,
  advisory only: the leg re-reads them under the lock) at most ``2 x workers`` companies ahead, and waits for
  the job when the walk reaches that company (the ``fetch_wait`` leg).
- THE CONTRACT: a fetched read IS the writer's first read of the same key, performed early. Its outcome — the
  document or the exception — is handed over (``Handoff``) and the writer's legs consume it in place of their
  own first network read of that key (``ReplayingEdgar`` / ``ReplayingPrices``); any later read of the key
  goes to the client as before (its disk cache, which the worker's fetch warmed). The legs therefore see
  byte-identical inputs and make byte-identical writes — the same facts, the same versions, the same call —
  and only WHEN each round trip happened moves. An exception is handed over too and raised at the very place
  the serial read would have raised it, so a failure stays per-name, per-leg and visible (#9): nothing is
  retried twice, swallowed, or dropped.
- The vendors' budgets, not this pool, are the throttle: every SEC request draws from the global SEC budget
  (``ingest/sec_budget.py``, X2), every Yahoo request from the Yahoo gate (``ingest.http.yahoo_limiter``, X7).

SERIAL SEMANTICS WHEREVER A HANDOFF COULD BE AMBIGUOUS — no job; the writer reads for itself as it always did:
a company's SECOND basket row (one security placed in two segments), a company whose CIK or price symbol an
earlier row of the same walk already fetched (its cache is warm, exactly as in the serial walk), a company the
W1 lock DEFERRED (it is ingested after a wait, so it re-reads fresh), a ``--no-live`` walk (no round trip to
overlap), and ``Settings.ingest_fetch_concurrency = 1`` — the kill switch: no pool, the serial loop verbatim.

X1 — A NAME STILL ONBOARDING (``pipeline/onboarding.py``) is fetched exactly as its legs will read it, never
more: one DEFERRED past the pass's onboarding cap gets no job at all (no planning read, no fetch); a WINDOWED
one's job fetches its submissions index, its price and only the Form 4 documents filed inside its window —
no 4/A — through the same ``form4_leg_filings`` call the Form 4 leg makes, so the two cannot drift and a cold
name never pulls its whole history inside the pass.

THE SETTLE GUARD (D2). The price leg judges which bars have settled at ``now``. With a handoff (and no
``now`` from the caller) that is the instant the worker STARTED the pull: the vendor's answer reflects that
moment, so a session that settled between the pull and the write is still withheld — conservative, never a
loss (the next pass stores it), and the T5 pull date is the pull's own market date.

MEMORY. A handoff holds one company's submissions document plus the new filing documents it fetched, up to
``_MEMO_DOC_BYTES``; past that (a cold name's whole insider history) a fetched document is not held — it is
in the immutable ``forms/`` disk cache, where the writer's read finds it. At most ``2 x workers`` handoffs
exist at once.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg

from domain.onboarding import OnboardingWindow
from domain.settings import get_settings
from domain.thesis import BasketMember
from ingest.edgar.form4 import existing_accessions
from ingest.edgar.form4a import existing_amendment_accessions, form4_leg_filings
from ingest.edgar.submissions import form4_doc_url
from pipeline import pass_timings
from pipeline.pass_timings import timed
from securities import master

# Filing documents one handoff holds in memory before relying on the immutable disk cache (see MEMORY).
_MEMO_DOC_BYTES = 8 * 1024 * 1024


def fetch_workers(allow_live: bool) -> int:
    """The walk's fetch workers: ``Settings.ingest_fetch_concurrency`` on a live walk; 1 (the serial walk, no
    fetch-ahead) on a cache-only one — with no round trip there is nothing to overlap."""
    if not allow_live:
        return 1
    return max(1, int(get_settings().ingest_fetch_concurrency))


@dataclass
class _Outcome:
    """One read's outcome: what kind of read made it (``text`` / ``json`` / ``bars``), and its value or the
    exception it raised."""

    kind: str
    value: Any = None
    error: Exception | None = None

    def settle(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.value


@dataclass
class Handoff:
    """One company's fetched reads, keyed exactly like the writer's reads that will consume them: the EDGAR
    cache key, and ``(symbol, allow_live, force_refresh)`` for the price pull."""

    edgar: dict[str, _Outcome] = field(default_factory=dict)
    prices: dict[tuple[str, bool, bool], _Outcome] = field(default_factory=dict)
    price_started_at: datetime | None = None
    doc_bytes: int = 0
    # the company's filing-document fetches, fanned out across the pool (each its own task, so a company
    # with hundreds of new documents keeps every worker busy); the writer waits for them in ``take``
    pending: list[Future] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # the EDGAR fetches that COMPLETED for this company's reads on the workers (``EdgarClient`` per-thread
    # counts, measured around each task) — the writer adds its own and reports the sum as the freeze
    # floor's per-company fact (``NameResult.edgar_completed``), exactly what the serial walk counted
    completed: int = 0

    def record(self, cache_key: str, outcome: _Outcome) -> None:
        with self.lock:
            self.edgar[cache_key] = outcome

    def add_completed(self, n: int) -> None:
        if n:
            with self.lock:
                self.completed += n

    def hold_text(self, size: int) -> bool:
        """Reserve ``size`` bytes of the in-memory document budget (``_MEMO_DOC_BYTES``); False = over it."""
        with self.lock:
            if self.doc_bytes + size > _MEMO_DOC_BYTES:
                return False
            self.doc_bytes += size
            return True

    def edgar_client(self, inner: Any) -> ReplayingEdgar:
        return ReplayingEdgar(inner, self.edgar)

    def price_source(self, inner: Any) -> ReplayingPrices:
        return ReplayingPrices(inner, self.prices)


# --- the writer's side: the first read of a fetched key is the fetch's outcome --------------------------


class ReplayingEdgar:
    """The EDGAR client as one company's legs see it: the FIRST ``get_text`` / ``get_json`` of a key the worker
    read returns (or raises) that read's outcome; every other call goes to the real client unchanged.
    """

    def __init__(self, inner: Any, outcomes: dict[str, _Outcome]) -> None:
        self._inner = inner
        self._outcomes = outcomes

    def _take(self, cache_key: str, kind: str) -> _Outcome | None:
        found = self._outcomes.get(cache_key)
        if found is None or found.kind != kind:
            return None
        del self._outcomes[cache_key]
        return found

    def get_text(self, url: str, cache_key: str) -> str:
        found = self._take(cache_key, "text")
        return self._inner.get_text(url, cache_key) if found is None else found.settle()

    def get_json(self, url: str, cache_key: str) -> dict[str, Any]:
        found = self._take(cache_key, "json")
        return self._inner.get_json(url, cache_key) if found is None else found.settle()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class ReplayingPrices:
    """The price source as one company's price leg sees it: the first pull for the fetched key returns (or
    raises) the fetch's outcome; anything else goes to the real source unchanged."""

    def __init__(self, inner: Any, outcomes: dict[tuple[str, bool, bool], _Outcome]) -> None:
        self._inner = inner
        self._outcomes = outcomes

    def get_bars(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> list[dict]:
        found = self._outcomes.pop((ticker, allow_live, force_refresh), None)
        if found is None:
            return self._inner.get_bars(ticker, allow_live=allow_live, force_refresh=force_refresh)
        return found.settle()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# --- the worker's side: perform the read, record its outcome, behave exactly like the client ------------


class _RecordingEdgar:
    def __init__(self, inner: Any, handoff: Handoff) -> None:
        self._inner = inner
        self._handoff = handoff

    @property
    def handoff(self) -> Handoff:
        return self._handoff

    def get_text(self, url: str, cache_key: str) -> str:
        try:
            text = self._inner.get_text(url, cache_key)
        except Exception as exc:
            self._handoff.record(cache_key, _Outcome("text", error=exc))
            raise
        if self._handoff.hold_text(len(text)):
            self._handoff.record(cache_key, _Outcome("text", value=text))
        # else: not held — the client's immutable forms/ cache serves the writer's read
        return text

    def get_json(self, url: str, cache_key: str) -> dict[str, Any]:
        try:
            value = self._inner.get_json(url, cache_key)
        except Exception as exc:
            self._handoff.record(cache_key, _Outcome("json", error=exc))
            raise
        self._handoff.record(cache_key, _Outcome("json", value=value))
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@dataclass(frozen=True)
class _Job:
    """What one company's fetch reads: its submissions (``cik``; None = no SEC part), the accessions already
    stored (so only NEW filing documents are fetched), and its price symbol (None = no price part).
    """

    label: str
    cik: str | None
    seen_form4: frozenset[str]
    seen_amendments: frozenset[str]
    symbol: str | None
    # X1 — a name still ONBOARDING: the Form 4 leg reads only the filings filed since this date and no 4/A,
    # so the job fetches exactly those (``form4_leg_filings`` — the one rule both use). None = the full walk.
    filed_since: date | None = None


class FetchAhead:
    """One walk's fetch-ahead: plan + submit up to ``lookahead`` companies ahead of the writer, hand each
    company's reads over when the writer reaches it (``take``). Owned and driven by the writer thread only;
    ``close`` it (``with`` or ``finally``) so no worker outlives the walk.

    A company's job reads its submissions index and its price; each NEW filing document it lists is then
    its own pool task (``_document``), so one company with hundreds of new documents (a cold name, a
    backlog night) is fetched by the whole pool, not by one worker while the rest idle. No task ever waits
    on another — only the writer waits (``take``) — so the pool cannot deadlock on itself."""

    def __init__(
        self,
        conn: psycopg.Connection,
        client: Any,
        source: Any,
        members: Sequence[BasketMember],
        *,
        tenant_id: UUID,
        allow_live: bool,
        force_refresh: bool,
        workers: int,
        fetch_submissions: Callable[[Any, str], dict[str, Any]],
        lookahead: int | None = None,
        onboarding: Mapping[UUID, OnboardingWindow] | None = None,
    ) -> None:
        self._conn = conn
        self._client = client
        self._source = source
        self._members = list(members)
        self._tenant_id = tenant_id
        self._allow_live = allow_live
        self._force_refresh = force_refresh
        self._fetch_submissions = fetch_submissions
        # X1 — the walk's onboarding treatment (``ingest_thesis(onboarding=...)``): a DEFERRED name gets no job
        # at all; a WINDOWED one's job fetches only what its windowed legs will read
        self._onboarding = dict(onboarding or {})
        self._lookahead = max(1, lookahead if lookahead is not None else 2 * workers)
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix="fetch-ahead"
        )
        # decided HERE, in the writer's context: a task runs under a child meter only on a metered pass
        self._job = pass_timings.in_worker(self._run)
        self._doc_job = pass_timings.in_worker(self._document)
        self._futures: dict[int, Future] = {}
        self._planned = 0
        self._sids: set[UUID] = set()
        self._ciks: set[Any] = set()
        self._symbols: set[str] = set()
        # forms/ keys a task has taken: one fetch per document per walk
        self._claimed: set[str] = set()
        self._claim_lock = threading.Lock()

    def __enter__(self) -> FetchAhead:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Cancel what has not started and wait for what has (each read is bounded by its HTTP timeout), so
        every count is final and no worker writes after the walk returns."""
        for fut in self._futures.values():
            fut.cancel()
        self._futures.clear()
        self._pool.shutdown(wait=True, cancel_futures=True)

    # --- the writer's calls ------------------------------------------------------------------------------

    def take(self, index: int) -> Handoff | None:
        """Plan ahead of member ``index``, then return its handoff once ALL its reads are done — the company
        job and every document task it fanned out (``None`` = the writer reads for itself). Called by the
        writer, between companies; the planning reads may open a transaction, which the caller ends.
        """
        self._plan_through(min(len(self._members), index + self._lookahead))
        fut = self._futures.pop(index, None)
        if fut is None:
            return None
        try:
            handoff, child = timed("fetch_wait")(fut.result)()
            pass_timings.absorb(child)
            for doc in handoff.pending:
                _, doc_child = timed("fetch_wait")(doc.result)()
                pass_timings.absorb(doc_child)
        except Exception as exc:  # noqa: BLE001 — a fetch-ahead fault never costs the name
            label = self._members[index].ticker or self._members[index].security_id
            print(
                f"  warn: {label} fetch-ahead failed ({type(exc).__name__}: {exc}) — read in line"
            )
            return None
        handoff.pending = []
        return handoff

    def _plan_through(self, stop: int) -> None:
        while self._planned < stop:
            index = self._planned
            self._planned += 1
            try:
                job = self._plan(self._members[index])
            except Exception as exc:  # noqa: BLE001 — a planning read failed: read in line instead
                self._conn.rollback()  # only planning reads are open here — every leg already committed
                label = self._members[index].ticker or self._members[index].security_id
                print(f"  warn: {label} fetch-ahead not planned ({type(exc).__name__}: {exc})")
                continue
            if job is not None:
                self._futures[index] = self._pool.submit(self._job, job)

    def _plan(self, m: BasketMember) -> _Job | None:
        sid = m.security_id
        if sid is None or sid in self._sids:
            return None  # a later basket row of the same company reads for itself (the serial walk's reads)
        self._sids.add(sid)
        window = self._onboarding.get(sid)
        if window is not None and window.defer:
            # X1 — past the pass's onboarding cap: the writer does not walk it, so nothing is read for it —
            # no planning read, no fetch (the onboarding job loads it outside the window)
            return None
        sec = master.get(self._conn, sid, tenant_id=self._tenant_id)
        if sec is None:
            return None
        cik: str | None = None
        seen4: frozenset[str] = frozenset()
        seen4a: frozenset[str] = frozenset()
        if sec.cik:
            key = _cik_key(sec.cik)
            if key not in self._ciks:  # a CIK an earlier row fetched: its index is warm on disk
                self._ciks.add(key)
                cik = sec.cik
                tenant = self._tenant_id
                seen4 = frozenset(existing_accessions(self._conn, sec.id, tenant_id=tenant))
                seen4a = frozenset(
                    existing_amendment_accessions(self._conn, sec.id, tenant_id=tenant)
                )
        symbol: str | None = None
        if sec.ticker:  # the price leg's own gate, and its own symbol choice
            wanted = sec.price_symbol or sec.ticker
            if wanted not in self._symbols:
                self._symbols.add(wanted)
                symbol = wanted
        if cik is None and symbol is None:
            return None
        return _Job(
            str(sec.ticker or sec.id),
            cik,
            seen4,
            seen4a,
            symbol,
            filed_since=window.filed_since if window is not None else None,
        )

    # --- the pool's tasks --------------------------------------------------------------------------------

    def _run(self, job: _Job) -> Handoff:
        """One company: its submissions index (then its new documents, fanned out), then its price pull."""
        handoff = Handoff()
        if job.cik is not None:
            before = thread_completed(self._client)
            # the serial walk makes these reads inside the Form 4 leg — counted there (submissions nested)
            timed("form4")(self._filings)(job, handoff)
            handoff.add_completed(thread_completed(self._client) - before)
        if job.symbol is not None:
            key = (job.symbol, self._allow_live, self._force_refresh)
            handoff.price_started_at = datetime.now(timezone.utc)  # BEFORE the gate: conservative
            try:
                bars = timed("price")(self._source.get_bars)(
                    job.symbol, allow_live=self._allow_live, force_refresh=self._force_refresh
                )
            except Exception as exc:  # noqa: BLE001 — handed over: the price leg raises it in place
                handoff.prices[key] = _Outcome("bars", error=exc)
            else:
                handoff.prices[key] = _Outcome("bars", value=bars)
        return handoff

    def _filings(self, job: _Job, handoff: Handoff) -> None:
        assert job.cik is not None
        rec = _RecordingEdgar(self._client, handoff)
        try:
            subs = self._fetch_submissions(rec, job.cik)
        except Exception:  # noqa: BLE001 — recorded: the writer's first read raises it in place
            return
        # exactly the documents the Form 4 leg will read (THE ONE RULE, ``form4_leg_filings``): for a name still
        # onboarding (X1) only the in-window Form 4s and no 4/A, so a cold name never pulls its history here
        originals, amendments = form4_leg_filings(subs, filed_since=job.filed_since)
        new = [f for f in originals if f["accession"] not in job.seen_form4]
        new += [f for f in amendments if f["accession"] not in job.seen_amendments]
        for f in new:
            # the SAME url + key the Form 4 / 4-A legs build (``ingest_thesis._form4_leg``, ``form4a``)
            key = f"forms/{f['accession']}/{f['primary_doc'].rsplit('/', 1)[-1]}"
            with self._claim_lock:
                if key in self._claimed:
                    continue  # another company of this walk lists the same filing: fetched once
                self._claimed.add(key)
            url = form4_doc_url(job.cik, f["accession"], f["primary_doc"])
            handoff.pending.append(self._pool.submit(self._doc_job, rec, url, key))

    def _document(self, rec: _RecordingEdgar, url: str, key: str) -> None:
        before = thread_completed(self._client)
        try:
            timed("form4")(rec.get_text)(url, key)
        except Exception:  # noqa: BLE001 — recorded: the writer's read raises it in place
            pass
        finally:
            rec.handoff.add_completed(thread_completed(self._client) - before)


def thread_completed(client: Any) -> int:
    """The completed EDGAR fetches the CALLING thread made on ``client`` (``EdgarClient.thread_completed``);
    a client without per-thread counts (a test double) falls back to its global ``live_completed`` — exact
    whenever one thread drives it, which is the serial walk."""
    per_thread = getattr(client, "thread_completed", None)
    if callable(per_thread):
        return int(per_thread())
    return int(getattr(client, "live_completed", 0) or 0)


def _cik_key(cik: Any) -> Any:
    """A CIK compared by value ("320193" and "0000320193" are one company)."""
    try:
        return int(cik)
    except (TypeError, ValueError):
        return str(cik)
