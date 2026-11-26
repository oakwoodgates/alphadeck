"""T2 — the RENAME RECOMMENDER: a nightly SPLICE PROBE of each symbol EDGAR now lists for a renamed name.

THE GAP (MEASURED on prod 2026-10-16). T1's rename guard marks a stopped price tape ``renamed`` when EDGAR
still lists the company but under symbols that include neither our ``ticker`` nor our ``price_symbol``
(HLLK -> SDCO, BRR -> SVIA / SVIAW, BTAI -> BTAIQ, VRME -> OPNW). The repair is one master write — point
``security_master.price_symbol`` at the new listing and the next pass re-pulls the full tape — but the old
resolver (``pipeline/resolve_price_symbols.py``) cannot reach these: it is OTC-only, thin-history-gated,
matches on the stale master name, and the cron never runs it. And ADOPTING EDGAR's symbol blind is unsafe:
EDGAR lists every class side by side (SVIA is BRR's common stock, SVIAW its WARRANTS — x0.12 the price), so a
wrong pick files another instrument's tape under the member (#4/#6).

THE PROBE (operator Q7 = nightly). For every stopped price row T1 marks ``renamed`` (deduped by security),
each candidate in its ``edgar_tickers`` is fetched ONCE through the ``PriceSource`` seam, into a THROWAWAY
cache dir (the ``pipeline/repair_benchmark_bars.py`` pattern — the shared price cache is never touched), and
spliced against OUR stored tape:

- **overlap** = the dates both tapes carry a positive close on;
- **ratio** = the MEDIAN of ``candidate close / stored close`` over those dates;
- **verified** = ``overlap >= VERIFY_MIN_OVERLAP`` (5) AND ``|ratio - 1| <= VERIFY_MAX_DEVIATION`` (1%).

A successor that IS our name carries our history under its new symbol, so its closes match ours to the
cent (MEASURED: HLLK/SDCO 30 shared bars, median x1.0000; BRR/SVIA 30, x1.0000), while another class or
another company does not (BRR/SVIAW 30 shared bars, median x0.121 — the warrants: REJECTED). The median makes
one restated bar irrelevant; the overlap floor stops a three-bar coincidence from verifying.

WHAT THIS IS, AND IS NOT:

- **A pending RECOMMENDATION (#3).** The probe itself writes no row anywhere: its result rides the run
  artifact, the cron's printed report and the Admin "Price feed repairs" panel, where the operator clicks
  **Adopt** (``app/routers/admin_price_feed.py`` -> ``price_feed_repo.apply_change`` ->
  ``master.set_price_symbol(basis="operator:adopt")``, with an exact Undo). Nothing reaches a detector or a
  CallCard. The one automatic step is separate (``run_auto_adopt``, below): a name the company-list refresh
  PINNED to its old ticker adopts SEC's new one only on the strict splice rule (``AUTO_ADOPT_MIN_OVERLAP``);
  every other rename stays the operator's Adopt.
- **Evidence beside the numbers (#6).** Each row carries EDGAR's current entity name, its ``formerNames``
  and its item-5.03 8-Ks (where a name change is filed) — read by the ingest from the SAME cached
  submissions as the stop classification (``NameResult.edgar_name`` / ``former_names`` /
  ``name_change_filings``), so the evidence costs no fetch.
- **Bounded cost.** ONE vendor request per distinct candidate symbol per pass (four renamed names, five
  symbols tonight), and nothing at all on a pass with no renamed row. A ``--no-live`` pass never probes.
- **Fail-open for the pass, loud for the operator.** A candidate whose fetch fails (a 404, a timeout) is
  recorded with its ``error`` and is simply not verified; a fault reading our own tape fails the whole probe
  into ``RenameProbe.error``, which the report prints loudly and the Admin panel shows — never an exception
  into the cron.

NEVER-PRICED NAMES (W4-1). An UNPRICEABLE row (no stored bar ever, neither closed nor renamed — Q8) whose
EDGAR list holds a symbol other than our ticker / price symbol is probed beside the renamed rows
(``unpriced_rows``), through the SAME one-fetch-per-symbol cache. The measured shape: an ADR member EDGAR
files as "<name>/ADR" whose own symbol 404s at the vendor every night, while EDGAR's list for its CIK also
carries the company's foreign ORDINARY line. It is not renamed (our ticker is in the list), so before this
nothing looked at the other symbol. There is no stored tape to splice against, so a candidate is judged only
by what the vendor serves (``bars``, ``last_bar``, ``error``) and by its LISTING CLASS (``listing_class``: an
EDGAR name ending "/ADR" marks an ADR; otherwise a five-letter alphabetic OTC symbol ending Y reads ADR and
one ending F a foreign ordinary — a naming CONVENTION, never proof; anything else is unknown).
``recommended`` is the first candidate with bars, no error, a last bar fresh by the tape monitor's own rule
and the member's own class mark (both unknown counts as the same): a CROSS-CLASS candidate is never
recommended, because pricing an ADR card on the ordinary line puts its cap off by the ADS ratio once a share
count exists. Adopting one anyway is the operator's explicit, confirmed click
(``app/routers/admin_price_feed.py``, setting ``price_feed_cross_class_adopt``). The rows ride the artifact
as ``rename_probe.unpriced`` beside ``rows``; ``run_auto_adopt`` reads ``rows`` only, so nothing here is
ever applied automatically.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING
from uuid import UUID

from domain.feed_kinds import PRICE, feed_kind
from ingest.edgar.submissions import FormerName, NameChangeFiling
from ingest.prices.source import DEFAULT_RANGE, PriceSource, YahooPriceSource
from pipeline.tape_health import is_tape_stale

if TYPE_CHECKING:  # the probe opens a connection; the pure core never needs psycopg
    import psycopg

# VERIFY_MIN_OVERLAP — the fewest shared dates a splice may be judged on: a week of sessions. Below it a
# couple of coincidental closes could "match"; a genuine successor carries our whole recent history, so it
# clears this by a wide margin (MEASURED 2026-10-16: 30 of 30 extracted dates for SDCO and SVIA).
VERIFY_MIN_OVERLAP = 5
# VERIFY_MAX_DEVIATION — how far the median close ratio may sit from 1.0: one percent. The same issuer's
# tape under its new symbol matches ours to float noise (MEASURED: SDCO / SVIA medians 1.000000, worst
# single bar 0.9974); another class or company misses by an order of magnitude (SVIAW 0.121).
VERIFY_MAX_DEVIATION = 0.01
# How much of an error message rides the artifact (the name-error convention)
_MAX_ERROR = 300

# THE AUTO-ADOPT RULE (the company-list refresh, 2026-10-24) — stricter than "verified": a name the refresh
# PINNED to its old ticker (``system:rename-pin``) is adopted onto SEC's new ticker WITHOUT the operator only
# when the splice shares at least AUTO_ADOPT_MIN_OVERLAP dates (a trading month, not a week) AND its median
# ratio reads exactly x1.000 at the three decimals the report prints (the same issuer's tape matches ours to
# float noise — MEASURED SDCO / SVIA medians 1.000000). Anything less stays the operator's Adopt on Admin.
AUTO_ADOPT_MIN_OVERLAP = 20

# W4-1 — the LISTING-CLASS marks (``listing_class``). A naming convention, never proof: EDGAR appends "/ADR"
# to the entity name of an ADR filer (the measured members read "<name> PLC/ADR", "<name> LTD/ADR"; the
# older "<name> /ADR/" spelling is read the same), and the OTC market's fifth letter marks a five-letter
# symbol's class (Y = an American depositary receipt, F = a foreign ordinary share).
CLASS_ADR = "adr"
CLASS_ORDINARY = "ordinary"
_ADR_NAME = re.compile(r"/\s*ADR\s*/?\s*$")
# The tape monitor's default freshness window (``Settings.tape_stale_days``); the nightly passes the live
# value, so a never-priced candidate is "fresh" by exactly the rule that would call its tape stale.
_DEFAULT_STALE_DAYS = 5


@dataclass(frozen=True)
class CandidateProbe:
    """One EDGAR-listed symbol, spliced against our stored tape: ``overlap`` shared dates, the MEDIAN
    ``ratio`` (candidate / ours; ``None`` with no overlap), ``verified`` per the module rule, ``bars`` the
    vendor served and the newest of them (``last_bar`` — whether adopting it would actually extend our
    tape), or the fetch ``error`` (then nothing else is known)."""

    symbol: str
    verified: bool = False
    overlap: int = 0
    ratio: float | None = None
    bars: int = 0
    last_bar: date | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol,
            "verified": self.verified,
            "overlap": self.overlap,
            "ratio": self.ratio,
            "bars": self.bars,
            "last_bar": self.last_bar.isoformat() if self.last_bar else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class RenameRecommendation:
    """One renamed name's recommendation: who it is (``security_id`` — the key, never the ticker), where
    our tape stops (``edge``), what EDGAR lists now, each candidate's splice, and the EDGAR evidence that
    it is the same filer. ``recommended`` is the FIRST verified candidate in EDGAR's order (``None`` = none
    verified: the operator may still adopt one, on their own judgment)."""

    security_id: UUID
    ticker: str | None
    edge: date | None
    edgar_tickers: tuple[str, ...]
    candidates: tuple[CandidateProbe, ...]
    edgar_name: str | None = None
    former_names: tuple[FormerName, ...] = ()
    name_change_filings: tuple[NameChangeFiling, ...] = ()

    @property
    def recommended(self) -> str | None:
        return next((c.symbol for c in self.candidates if c.verified), None)

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "edge": self.edge.isoformat() if self.edge else None,
            "edgar_tickers": list(self.edgar_tickers),
            "recommended": self.recommended,
            "candidates": [c.to_json() for c in self.candidates],
            "edgar_name": self.edgar_name,
            "former_names": [
                {"name": f.name, "since": f.since, "until": f.until} for f in self.former_names
            ],
            "name_change_filings": [
                {"filed": f.filed.isoformat(), "accession": f.accession}
                for f in self.name_change_filings
            ],
        }


@dataclass(frozen=True)
class AutoAdoption:
    """One AUTOMATIC adoption (``run_auto_adopt``): the pinned name, the old symbol it was priced under, the
    new SEC ticker adopted, the splice that justified it, and the repair-log row (``change_id``) its Undo
    names — or the ``error`` that stopped the write (then nothing changed)."""

    security_id: UUID
    ticker: str | None
    from_symbol: str | None
    symbol: str
    overlap: int
    ratio: float | None
    change_id: UUID | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "from_symbol": self.from_symbol,
            "symbol": self.symbol,
            "overlap": self.overlap,
            "ratio": self.ratio,
            "change_id": str(self.change_id) if self.change_id else None,
            "error": self.error,
        }

    def describe(self) -> str:
        if self.error:
            return f"{self.ticker or self.security_id}: auto-adopt of {self.symbol} FAILED — {self.error}"
        ratio = f"x{self.ratio:.3f}" if self.ratio is not None else "x?"
        return (
            f"{self.ticker or self.security_id}: adopted {self.symbol} automatically (was pinned to "
            f"{self.from_symbol}; {self.overlap} shared bars, {ratio}) — Undo on the Admin page"
        )


def listing_class(symbol: str | None, name: str | None = None) -> str | None:
    """The class mark of one listing (W4-1): ``"adr"``, ``"ordinary"`` or ``None`` (unknown — never
    guessed). An EDGAR entity ``name`` ending "/ADR" (or "/ADR/") marks an ADR; otherwise a five-letter
    alphabetic OTC ``symbol`` ending Y reads ADR and one ending F a foreign ordinary. A convention, labeled
    as one wherever it is shown. Pure."""
    if name and _ADR_NAME.search(name.strip().upper()):
        return CLASS_ADR
    s = (symbol or "").strip().upper()
    if len(s) == 5 and s.isascii() and s.isalpha():
        if s.endswith("Y"):
            return CLASS_ADR
        if s.endswith("F"):
            return CLASS_ORDINARY
    return None


@dataclass(frozen=True)
class UnpricedCandidate:
    """One other symbol EDGAR lists for a NEVER-PRICED name, as the probe fetched it (W4-1): what the vendor
    serves under it (``bars`` and the newest, ``last_bar``) or the fetch ``error``, and its ``listing_class``
    mark. There is no stored tape to splice against, so ``to_json`` carries the probe-candidate shape with
    ``verified`` False, no overlap and no ratio — the Admin panel renders both lists with one model.
    """

    symbol: str
    bars: int = 0
    last_bar: date | None = None
    error: str | None = None
    listing_class: str | None = None

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol,
            "verified": False,
            "overlap": 0,
            "ratio": None,
            "bars": self.bars,
            "last_bar": self.last_bar.isoformat() if self.last_bar else None,
            "error": self.error,
            "listing_class": self.listing_class,
        }


@dataclass(frozen=True)
class UnpricedRecommendation:
    """One never-priced name's probe (W4-1): who it is (``security_id``), what EDGAR lists and calls it,
    the member's own class mark (``member_class``, from our ticker and EDGAR's entity name), each other
    symbol's fetch, and ``recommended`` — the first candidate with bars, no error, a fresh last bar and the
    member's class (``None`` = none qualifies; a cross-class candidate never does, and the operator may
    still adopt one with the explicit class confirm)."""

    security_id: UUID
    ticker: str | None
    edgar_tickers: tuple[str, ...]
    candidates: tuple[UnpricedCandidate, ...]
    member_class: str | None = None
    edgar_name: str | None = None
    recommended: str | None = None

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "edgar_tickers": list(self.edgar_tickers),
            "edgar_name": self.edgar_name,
            "member_class": self.member_class,
            "recommended": self.recommended,
            "candidates": [c.to_json() for c in self.candidates],
        }


@dataclass
class RenameProbe:
    """One pass's rename-probe outcome. ``evaluated`` = the probe ran (a live pass whose stored-tape read
    succeeded — including the common night with nothing to probe); ``error`` = why it did not, on a live
    pass; ``fetches`` = vendor requests made (the Q7 cost, one per distinct candidate symbol, across both
    lists). ``auto_adopted`` / ``auto_adopt_error`` — what ``run_auto_adopt`` did with tonight's rows.
    ``unpriced`` (W4-1) — the never-priced names probed beside the renamed ``rows``."""

    evaluated: bool = False
    rows: tuple[RenameRecommendation, ...] = ()
    error: str | None = None
    fetches: int = 0
    duration_s: float = 0.0
    auto_adopted: tuple[AutoAdoption, ...] = ()
    auto_adopt_error: str | None = None
    unpriced: tuple[UnpricedRecommendation, ...] = ()

    def by_security(self) -> dict[UUID, RenameRecommendation]:
        return {r.security_id: r for r in self.rows}

    def unpriced_by_security(self) -> dict[UUID, UnpricedRecommendation]:
        return {r.security_id: r for r in self.unpriced}

    def payload(self) -> dict:
        """The run artifact's ``rename_probe`` key (ONE nested object, beside the flat split keys). Readers
        use ``.get``: an artifact written before T2 lacks it and reads as "not probed", never an alarm.
        """
        return {
            "rename_probe": {
                "evaluated": self.evaluated,
                "error": self.error,
                "fetches": self.fetches,
                "duration_s": round(self.duration_s, 3),
                "rows": [r.to_json() for r in self.rows],
                "auto_adopted": [a.to_json() for a in self.auto_adopted],
                "auto_adopt_error": self.auto_adopt_error,
                "unpriced": [r.to_json() for r in self.unpriced],
            }
        }


def _clip(s: str) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= _MAX_ERROR else s[: _MAX_ERROR - 1] + "…"


def _positive(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def splice(candidate: Iterable[Mapping], stored: Mapping[date, float]) -> tuple[int, float | None]:
    """``(overlap, median ratio)`` of a candidate's bars (``{d, close, ...}`` dicts, the ``PriceSource``
    shape) against our stored closes (``d -> close``). Only dates where BOTH closes are positive count.
    Pure."""
    ratios: list[float] = []
    for bar in candidate:
        d = bar.get("d")
        c = _positive(bar.get("close"))
        s = _positive(stored.get(d)) if d is not None else None
        if c is not None and s is not None:
            ratios.append(c / s)
    return len(ratios), (median(ratios) if ratios else None)


def judge(symbol: str, bars: Sequence[Mapping], stored: Mapping[date, float]) -> CandidateProbe:
    """One candidate's verdict from its fetched ``bars`` (pure — the rule in the module docstring)."""
    overlap, ratio = splice(bars, stored)
    verified = (
        overlap >= VERIFY_MIN_OVERLAP
        and ratio is not None
        and abs(ratio - 1.0) <= VERIFY_MAX_DEVIATION
    )
    days = [b.get("d") for b in bars if b.get("d") is not None]
    return CandidateProbe(
        symbol=symbol,
        verified=verified,
        overlap=overlap,
        ratio=round(ratio, 6) if ratio is not None else None,
        bars=len(bars),
        last_bar=max(days) if days else None,
    )


def renamed_rows(results: Iterable) -> list[tuple]:
    """``[(StaleTape, NameResult | None)]`` — every stopped PRICE row marked ``renamed`` across the pass's
    thesis results, deduped by security (first seen wins), each paired with that security's ingest result
    (the EDGAR evidence rides it). A renamed row with no EDGAR symbol to probe is skipped (it cannot be
    one — the guard needs a non-empty list — but a malformed row must not crash the pass)."""
    out: list[tuple] = []
    seen: set[UUID] = set()
    for r in results:
        names = {x.security_id: x for x in getattr(r, "ingested", ()) or ()}
        for s in getattr(r, "tape_stale", ()) or ():
            if feed_kind(s.kind).key != PRICE.key or not s.renamed or not s.edgar_tickers:
                continue
            if s.security_id in seen:
                continue
            seen.add(s.security_id)
            out.append((s, names.get(s.security_id)))
    return out


def _ours(s, name) -> set[str]:
    """Our own symbols for a stale row: the master ticker and the price symbol the ingest priced it under."""
    return {
        x.strip().upper()
        for x in (s.ticker, getattr(name, "price_symbol", None))
        if isinstance(x, str) and x.strip()
    }


def unpriced_candidates(s, name) -> tuple[str, ...]:
    """The symbols EDGAR lists for a never-priced row other than our own, in EDGAR's order, de-duplicated
    (case-insensitive) — what the probe fetches for it. Pure."""
    ours = _ours(s, name)
    out: dict[str, None] = {}
    for t in s.edgar_tickers or ():
        sym = str(t).strip().upper()
        if sym and sym not in ours:
            out.setdefault(sym)
    return tuple(out)


def unpriced_rows(results: Iterable) -> list[tuple]:
    """``[(StaleTape, NameResult | None)]`` — W4-1: every stopped PRICE row that is UNPRICEABLE (no stored
    bar ever, neither closed nor renamed) and NOT acknowledged, whose EDGAR list holds a symbol other than
    our ticker / price symbol (``unpriced_candidates``), across the pass's thesis results — deduped by
    security (first seen wins, the ``renamed_rows`` shape), each paired with that security's ingest result
    (EDGAR's entity name, which marks an ADR filer, rides it). An acknowledged name is the operator's
    "keep it, unpriced" and is never probed; a row EDGAR lists nothing else for has nothing to probe.
    """
    out: list[tuple] = []
    seen: set[UUID] = set()
    for r in results:
        names = {x.security_id: x for x in getattr(r, "ingested", ()) or ()}
        for s in getattr(r, "tape_stale", ()) or ():
            if feed_kind(s.kind).key != PRICE.key or not s.unpriceable or s.acknowledged:
                continue
            if s.security_id in seen:
                continue
            name = names.get(s.security_id)
            if not unpriced_candidates(s, name):
                continue
            seen.add(s.security_id)
            out.append((s, name))
    return out


def recommend_unpriced(
    candidates: Iterable[UnpricedCandidate],
    *,
    member_class: str | None,
    asof: date,
    stale_days: int,
) -> str | None:
    """The first candidate a never-priced name may be pointed at on the probe's word (W4-1): bars served,
    no fetch error, a last bar the tape monitor would NOT call stale on ``asof``, and the member's own class
    mark (both unknown counts as the same). A cross-class candidate is never recommended. Pure."""
    for c in candidates:
        if c.error or c.bars <= 0 or c.last_bar is None:
            continue
        if is_tape_stale(c.last_bar, asof=asof, stale_days=stale_days):
            continue
        if c.listing_class != member_class:
            continue
        return c.symbol
    return None


# Our stored tape, latest version per date — the SAME pick the as-of read makes (recorded_at DESC, id DESC),
# tenant-joined through the master so a row can never be read under another tenant's security.
_STORED_SQL = """
    SELECT DISTINCT ON (p.d) p.d, p.close
      FROM fact_price_eod p
      JOIN security_master sm ON sm.id = p.security_id AND sm.tenant_id = p.tenant_id
     WHERE p.security_id = %(sid)s AND p.d <= %(asof)s
     ORDER BY p.d, p.recorded_at DESC, p.id DESC
"""


def read_stored_closes(
    conn: psycopg.Connection, security_id: UUID, *, asof: date
) -> dict[date, float]:
    """``d -> close`` for one security's stored tape up to ``asof`` (SELECT only)."""
    with conn.cursor() as cur:
        cur.execute(_STORED_SQL, {"sid": security_id, "asof": asof})
        return {r["d"]: float(r["close"]) for r in cur.fetchall() if r["close"] is not None}


def _default_source(cache_dir: Path) -> PriceSource:
    return YahooPriceSource(cache_dir=cache_dir, range_=DEFAULT_RANGE)


def run_rename_probe(
    *,
    results: Iterable,
    asof: date,
    allow_live: bool,
    connect: Callable[[], psycopg.Connection],
    clock: Callable[[], float],
    source_factory: Callable[[Path], PriceSource] | None = None,
    stale_days: int = _DEFAULT_STALE_DAYS,
) -> RenameProbe:
    """The nightly unit: collect the renamed rows and (W4-1) the never-priced ones, read our stored tapes
    for the renamed rows only (its own ``read_only`` connection — an unpriced row has no tape, so a night
    with none renamed opens no connection), fetch each distinct candidate ONCE into a throwaway cache shared
    by both lists, judge. FAIL-OPEN for the pass (see the module docstring). ``source_factory`` builds the
    ``PriceSource`` over the throwaway dir (tests inject a fake; default Yahoo with the member range);
    ``stale_days`` is the tape monitor's threshold (``Settings.tape_stale_days``), which a never-priced
    name's candidate must be fresh under to be recommended."""
    if not allow_live:
        return RenameProbe()
    t0 = clock()
    rows = renamed_rows(results)
    unpriced = unpriced_rows(results)
    if not rows and not unpriced:
        return RenameProbe(evaluated=True, duration_s=clock() - t0)
    stored: dict[UUID, dict[date, float]] = {}
    if rows:
        try:
            conn = connect()
            try:
                conn.read_only = (
                    True  # the probe reads our tape; structurally it cannot write a row
                )
                stored = {
                    s.security_id: read_stored_closes(conn, s.security_id, asof=asof)
                    for s, _ in rows
                }
                conn.rollback()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — a probe fault is reported, never a failed pass
            return RenameProbe(
                error=f"{type(e).__name__}: {_clip(str(e))}", duration_s=clock() - t0
            )
    fetched: dict[str, list[dict] | str] = (
        {}
    )  # symbol -> bars, or the fetch error (one request each)
    out: list[RenameRecommendation] = []
    out_unpriced: list[UnpricedRecommendation] = []
    with tempfile.TemporaryDirectory(prefix="alphadeck-t2-probe-") as tmp:
        src = (source_factory or _default_source)(Path(tmp))

        def _fetch(sym: str) -> list[dict] | str:
            if sym not in fetched:
                try:
                    fetched[sym] = list(src.get_bars(sym, allow_live=True, force_refresh=True))
                except Exception as e:  # noqa: BLE001 — one candidate's fetch, recorded
                    fetched[sym] = f"{type(e).__name__}: {_clip(str(e))}"
            return fetched[sym]

        for s, name in rows:
            candidates: list[CandidateProbe] = []
            for sym in dict.fromkeys(s.edgar_tickers):  # EDGAR order, de-duplicated
                got = _fetch(sym)
                if isinstance(got, str):
                    candidates.append(CandidateProbe(symbol=sym, error=got))
                else:
                    candidates.append(judge(sym, got, stored[s.security_id]))
            out.append(
                RenameRecommendation(
                    security_id=s.security_id,
                    ticker=s.ticker,
                    edge=s.edge,
                    edgar_tickers=tuple(s.edgar_tickers),
                    candidates=tuple(candidates),
                    edgar_name=getattr(name, "edgar_name", None),
                    former_names=tuple(getattr(name, "former_names", ()) or ()),
                    name_change_filings=tuple(getattr(name, "name_change_filings", ()) or ()),
                )
            )
        for s, name in unpriced:
            edgar_name = getattr(name, "edgar_name", None)
            member = listing_class(s.ticker, edgar_name)
            found: list[UnpricedCandidate] = []
            for sym in unpriced_candidates(s, name):
                got = _fetch(sym)
                mark = listing_class(sym)
                if isinstance(got, str):
                    found.append(UnpricedCandidate(symbol=sym, error=got, listing_class=mark))
                    continue
                days = [b.get("d") for b in got if b.get("d") is not None]
                found.append(
                    UnpricedCandidate(
                        symbol=sym,
                        bars=len(got),
                        last_bar=max(days) if days else None,
                        listing_class=mark,
                    )
                )
            out_unpriced.append(
                UnpricedRecommendation(
                    security_id=s.security_id,
                    ticker=s.ticker,
                    edgar_tickers=tuple(s.edgar_tickers or ()),
                    candidates=tuple(found),
                    member_class=member,
                    edgar_name=edgar_name,
                    recommended=recommend_unpriced(
                        found, member_class=member, asof=asof, stale_days=stale_days
                    ),
                )
            )
    return RenameProbe(
        evaluated=True,
        rows=tuple(out),
        fetches=len(fetched),
        duration_s=clock() - t0,
        unpriced=tuple(out_unpriced),
    )


def auto_adoptable(c: CandidateProbe) -> bool:
    """The STRICT auto-adopt rule (see ``AUTO_ADOPT_MIN_OVERLAP``): no fetch error, at least 20 shared dates,
    and a median ratio that reads exactly x1.000 at three decimals. Pure."""
    return (
        c.error is None
        and c.ratio is not None
        and c.overlap >= AUTO_ADOPT_MIN_OVERLAP
        and round(c.ratio, 3) == 1.0
    )


_PINNED_SQL = """
    SELECT sm.id, sm.ticker, sm.price_symbol, sm.price_symbol_basis,
           EXISTS (SELECT 1 FROM price_symbol_change c
                    WHERE c.security_id = sm.id AND c.basis = %(auto)s) AS auto_before
      FROM security_master sm
     WHERE sm.id = ANY(%(ids)s)
"""


def pinned_ids(results: Iterable) -> set[UUID]:
    """The securities tonight's ingest saw priced under the company-list refresh's PIN (``NameResult
    .price_symbol_basis == system:rename-pin``) — the only names ``run_auto_adopt`` ever looks at. Pure.
    """
    from domain.price_feed_basis import is_rename_pin

    out: set[UUID] = set()
    for r in results:
        for x in getattr(r, "ingested", ()) or ():
            if is_rename_pin(getattr(x, "price_symbol_basis", None)) and x.security_id:
                out.add(x.security_id)
    return out


def run_auto_adopt(
    probe: RenameProbe,
    *,
    results: Iterable,
    allow_live: bool,
    connect: Callable[[], psycopg.Connection],
) -> RenameProbe:
    """AUTO-ADOPT the new SEC ticker for tonight's PINNED renamed names — the company-list refresh's hand-off
    to the stale-tape machinery. Only a probed row whose ingest result says it is priced under the refresh's
    pin (``pinned_ids(results)``) is looked at — a night without one returns ``probe`` UNCHANGED and opens no
    connection. Such a row qualifies only when ALL hold (re-read from the master, never trusted from the
    result):

    1. its master ``price_symbol`` is the refresh's PIN to the old ticker (``system:rename-pin``) and that pin
       is still the name's STANDING repair (nobody moved the master since — the operator's own Set wins);
    2. the candidate it adopts is the master's CURRENT ticker (SEC's new one), and that splice meets the strict
       rule (``auto_adoptable``: >= 20 shared bars, x1.000);
    3. the name was never auto-adopted before — an auto-adopt the operator UNDID is their decision, never
       re-applied by the next night (the recommendation stays on Admin for a manual Adopt).

    The write is the EXISTING Adopt path (``price_feed_repo.apply_change``, action ``adopt``, author
    ``system:auto-adopt``) — recorded with the master's prior and new values, shown on Admin as adopted
    automatically, undone exactly by its Undo. Each adoption commits on its own; a fault rides the row's
    ``error`` (or ``auto_adopt_error`` for the whole step), never an exception into the pass. A ``--no-live``
    pass, an unevaluated probe or a night with no renamed row does nothing (and opens no connection).
    """
    if not allow_live or not probe.evaluated or not probe.rows:
        return probe
    wanted = pinned_ids(results)
    rows = [r for r in probe.rows if r.security_id in wanted]
    if not rows:
        return probe  # the common night: nothing pinned among tonight's renamed rows
    from dataclasses import replace

    from domain.price_feed_basis import BASIS_AUTO_ADOPT, BASIS_RENAME_PIN, is_rename_pin
    from repositories import price_feed_repo

    out: list[AutoAdoption] = []
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    _PINNED_SQL,
                    {"auto": BASIS_AUTO_ADOPT, "ids": [r.security_id for r in rows]},
                )
                pinned = {r["id"]: r for r in cur.fetchall()}
            conn.rollback()
            for rec in rows:
                m = pinned.get(rec.security_id)
                if (
                    m is None
                    or not m["price_symbol"]
                    or not is_rename_pin(m["price_symbol_basis"])
                    or m["auto_before"]
                    or not m["ticker"]
                ):
                    continue
                new = str(m["ticker"]).upper()
                cand = next((c for c in rec.candidates if c.symbol.upper() == new), None)
                if cand is None or not auto_adoptable(cand):
                    continue
                standing = price_feed_repo.standing_change_for(conn, rec.security_id)
                conn.rollback()
                if (
                    standing is None
                    or standing.basis != BASIS_RENAME_PIN
                    or (standing.new_symbol, standing.new_basis)
                    != (m["price_symbol"], m["price_symbol_basis"])
                ):
                    continue  # the pin is not the standing repair any more — the operator's call
                row = AutoAdoption(
                    security_id=rec.security_id,
                    ticker=m["ticker"],
                    from_symbol=m["price_symbol"],
                    symbol=new,
                    overlap=cand.overlap,
                    ratio=cand.ratio,
                )
                try:
                    change = price_feed_repo.apply_change(
                        conn,
                        rec.security_id,
                        action="adopt",
                        symbol=new,
                        basis=BASIS_AUTO_ADOPT,
                        detail={"probe": rec.to_json(), "rule": "auto-adopt: >=20 shared, x1.000"},
                    )
                    conn.commit()
                    out.append(replace(row, change_id=change.id))
                except Exception as e:  # noqa: BLE001 — one name's write, recorded
                    conn.rollback()
                    out.append(replace(row, error=f"{type(e).__name__}: {_clip(str(e))}"))
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — the step is a passenger on the pass
        return replace(
            probe,
            auto_adopted=tuple(out),
            auto_adopt_error=f"{type(e).__name__}: {_clip(str(e))}",
        )
    return replace(probe, auto_adopted=tuple(out))


def describe(rec: RenameRecommendation) -> str:
    """The one-line splice summary the cron report and a page share: the verified successor (or why none
    is), e.g. ``"splice-verified SDCO (30 shared bars, x1.000, bars to 2026-10-15) — Adopt on the Admin
    page"``."""
    best = next((c for c in rec.candidates if c.verified), None)
    if best is not None:
        to = f", bars to {best.last_bar.isoformat()}" if best.last_bar else ""
        return (
            f"splice-verified {best.symbol} ({best.overlap} shared bars, x{best.ratio:.3f}{to}) — "
            "Adopt on the Admin page"
        )
    bits = []
    for c in rec.candidates:
        if c.error:
            bits.append(f"{c.symbol}: fetch failed")
        elif c.ratio is None:
            bits.append(f"{c.symbol}: no shared bars")
        else:
            bits.append(f"{c.symbol}: x{c.ratio:.3f} over {c.overlap}")
    return "no candidate splice-verified (" + "; ".join(bits) + ")" if bits else "nothing to probe"


_CLASS_WORDS = {CLASS_ADR: "ADR", CLASS_ORDINARY: "foreign ordinary"}
_MEMBER_WORDS = {CLASS_ADR: "an ADR", CLASS_ORDINARY: "a foreign ordinary"}


def describe_unpriced(rec: UnpricedRecommendation) -> str:
    """The one-line summary of a never-priced name's probe (W4-1) the cron report prints beside its
    UNPRICEABLE row, e.g. ``"EDGAR also lists TKAAF — TKAAF: 480 bars to 2026-11-24, foreign ordinary (OTC
    naming convention) while the member is an ADR: cross-class, Adopt only with the class confirm (Admin
    page)"`` or ``"EDGAR also lists TKMMN — Adopt TKMMN on the Admin page (30 bars to 2026-11-24)"``.
    """
    head = "EDGAR also lists " + ", ".join(c.symbol for c in rec.candidates)
    best = next((c for c in rec.candidates if c.symbol == rec.recommended), None)
    if best is not None:
        to = f" to {best.last_bar.isoformat()}" if best.last_bar else ""
        return f"{head} — Adopt {best.symbol} on the Admin page ({best.bars} bars{to})"
    member = _MEMBER_WORDS.get(rec.member_class or "", "of unknown class")
    bits = []
    for c in rec.candidates:
        if c.error:
            bits.append(f"{c.symbol}: fetch failed")
            continue
        if not c.bars:
            bits.append(f"{c.symbol}: no bars")
            continue
        to = f" to {c.last_bar.isoformat()}" if c.last_bar else ""
        bit = f"{c.symbol}: {c.bars} bars{to}"
        if rec.member_class and c.listing_class and c.listing_class != rec.member_class:
            bit += (
                f", {_CLASS_WORDS[c.listing_class]} (OTC naming convention) while the member is "
                f"{member}: cross-class, Adopt only with the class confirm (Admin page)"
            )
        else:
            bit += f", class {_CLASS_WORDS.get(c.listing_class or '', 'unknown')}"
        bits.append(bit)
    return f"{head} — no candidate recommended ({'; '.join(bits)})"
