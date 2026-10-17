"""T7 — the SUCCESSOR-CIK detector: a basket member whose company now files under a NEW CIK.

THE GAP (MEASURED read-only on prod, 2026-10-17). Every filing leg reads ``fetch_submissions(client,
sec.cik)`` (``pipeline/ingest_thesis.py``), and nothing ever changes a stored CIK. Two signed-off members
re-registered under a successor issuer (Rule 12g-3) and kept their ticker:

- **UROY** — stored CIK 0001711570 (now "Uranium Royalty Corp. /CA", ``tickers=[]``, a 15-12G 2026-07-31);
  the live CIK is 0002143673 (8-K12B 2026-07-28). Missed: the CEO's open-market Form 4 buy (2026-07-30),
  two 8-Ks, a 13D, the 10-K / 10-K/A / 10-Q.
- **REAX** — stored CIK 0001862461 (``tickers=[]``, a 15-12G 2026-09-04); the live CIK is 0002136387 "Real
  REMAX Group Inc." (8-K12B 2026-08-24). Missed: 25 Form 4s + 6 Form 4/As, an 8-K, a 13D.

Nothing alerted: the tape monitor only reads ``edgar_tickers`` for a STALE tape, and both tapes were fresh
(the price feed follows the ticker, not the CIK). The master still said ``recent_foreign_form=40-F`` /
``files_domestic_forms=false``, so the app told the operator insider flow was structurally unavailable.

THE RULE — for EVERY member the pass ingested (fresh tapes included), flag it when BOTH hold:

  (a) the STORED CIK's submissions list NO tickers (``NameResult.edgar_tickers == ()`` — read tonight
      by the ingest from the cached doc, zero extra fetches); AND
  (b) SEC's ticker map assigns the member's ``ticker`` or ``price_symbol`` to a DIFFERENT CIK.

(a) alone also matches a closed name (acquired / delisted — the ticker leaves the map with it), a
no-ticker filer that still files (UCLE, KAYS) and a never-listed one (WONDF); (b) is what separates the
successor: SEC itself now assigns OUR symbol to another registrant. MEASURED across prod's 688 basket
securities: exactly UROY and REAX, no false positive.

THE EVIDENCE (display, never a gate): the old CIK's deregistration (15-12B / 15-12G / 15-15D), its
current entity name and ``formerNames`` (EDGAR re-labels a predecessor — "Uranium Royalty Corp. /CA"), and
the successor's 8-K12B / 8-K12G3, entity name, ``formerNames`` and symbols. The successor's IDENTITY
(``parse_identity`` — the ``enrich_identity`` derivation, not a fork) rides the row too, so the operator's
Adopt can refresh the CIK-dependent identity fields with NO fetch of its own.

WHAT THIS IS, AND IS NOT:

- **A pending RECOMMENDATION, never applied (#3).** It writes no row anywhere. The operator's Adopt on the
  Admin "Price feed repairs" panel re-points ``security_master.cik`` in place (``app/routers/
  admin_price_feed.py`` -> ``repositories/cik_change_repo.py``), with an exact Undo.
- **Bounded cost.** ONE request for SEC's ticker map (only on a night some member lists no tickers), plus
  ONE submissions fetch per flagged candidate CIK (two today). The old CIK's evidence is re-read from the
  cache the ingest just filled (a cache-only client — zero requests).
- **Pages ONCE.** A row whose key (security + successor CIK) was absent from the previous evaluated pass
  is NEW and pages through the R4 health event; a known row is one quiet report line and stays on Admin.
- **Fail-open for the pass, loud for the operator.** Any fault returns ``evaluated=False`` with the
  ``error`` (printed loud, shown on Admin) — never an exception into the cron, never a lost record.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.edgar.submissions import (
    FormerName,
    edgar_tickers,
    entity_name,
    fetch_submissions,
    former_names,
    parse_identity,
)
from ingest.edgar.succession import (
    FilingRef,
    deregistration_filings,
    fetch_ticker_map,
    succession_filings,
)

if TYPE_CHECKING:  # the scan opens a connection; the pure core never needs psycopg
    import psycopg

_MAX_ERROR = 300  # how much of an error message rides the artifact (the name-error convention)


def cik10(v: object) -> str | None:
    """A CIK as the master stores it (10 digits, zero-padded), or ``None`` when ``v`` is not one."""
    s = str(v or "").strip()
    if not s.isdigit():
        return None
    n = int(s)
    return f"{n:010d}" if 0 < n < 10**10 else None


def _clip(s: str) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= _MAX_ERROR else s[: _MAX_ERROR - 1] + "…"


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {_clip(str(e))}"


# --- the pure rule -------------------------------------------------------------------------------------


def successor_ciks(
    *,
    stored_cik: str | None,
    ticker: str | None,
    price_symbol: str | None,
    listed: tuple[str, ...] | None,
    ticker_map: Mapping[str, frozenset[str]],
) -> dict[str, tuple[str, ...]]:
    """THE RULE (module docstring), pure: ``{successor CIK: (our symbols SEC maps to it)}`` — empty when the
    member is not a successor case. ``listed`` is EDGAR's symbol list for the STORED CIK as read tonight
    (``None`` = not read: no verdict, never a guess). Condition (a) is ``listed == ()``; condition (b) is a
    ticker-map entry for ``ticker`` or ``price_symbol`` naming a CIK other than ``stored_cik``."""
    stored = cik10(stored_cik)
    if stored is None or listed is None or listed != ():
        return {}
    out: dict[str, list[str]] = {}
    for sym in (ticker, price_symbol):
        s = (sym or "").strip().upper()
        if not s:
            continue
        for c in sorted(ticker_map.get(s, ())):
            if c != stored and s not in out.setdefault(c, []):
                out[c].append(s)
    return {c: tuple(v) for c, v in sorted(out.items())}


# --- the evidence + the row ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SuccessorCandidate:
    """One CIK SEC's ticker map now assigns our symbol to, with the evidence read from ITS submissions:
    who it is (``entity_name``, ``formerNames``), what it lists, its succession reports (8-K12B / 8-K12G3),
    and the IDENTITY an Adopt would apply (``parse_identity`` dumped to JSON — ``None`` when the read
    failed, then ``error`` says why)."""

    cik: str
    matched_on: tuple[str, ...]
    entity_name: str | None = None
    edgar_tickers: tuple[str, ...] = ()
    former_names: tuple[FormerName, ...] = ()
    succession_filings: tuple[FilingRef, ...] = ()
    identity: dict[str, Any] | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "cik": self.cik,
            "matched_on": list(self.matched_on),
            "entity_name": self.entity_name,
            "edgar_tickers": list(self.edgar_tickers),
            "former_names": [
                {"name": f.name, "since": f.since, "until": f.until} for f in self.former_names
            ],
            "succession_filings": [f.to_json() for f in self.succession_filings],
            "identity": self.identity,
            "error": self.error,
        }


@dataclass(frozen=True)
class SuccessorRow:
    """One flagged member: who it is (``security_id`` is the key, never the ticker), the CIK the master
    holds, the theses that hold it, the OLD CIK's evidence (its current EDGAR name, ``formerNames`` and
    deregistration filings — read from the cache the ingest filled), and each successor candidate.
    ``recommended`` = the one candidate when there is exactly one (``None`` when SEC's map is ambiguous:
    shown, never adoptable — the operator repairs that by hand)."""

    security_id: UUID
    ticker: str | None
    price_symbol: str | None
    stored_cik: str
    master_name: str | None
    candidates: tuple[SuccessorCandidate, ...]
    theses: tuple[tuple[UUID, str], ...] = ()
    old_entity_name: str | None = None
    old_former_names: tuple[FormerName, ...] = ()
    deregistrations: tuple[FilingRef, ...] = ()
    old_error: str | None = None

    @property
    def recommended(self) -> str | None:
        return self.candidates[0].cik if len(self.candidates) == 1 else None

    @property
    def key(self) -> str:
        """The page-once diff key: the security AND the successor it points at (a different successor on
        a later night is news again)."""
        return f"{self.security_id}:{'+'.join(c.cik for c in self.candidates)}"

    @property
    def label(self) -> str:
        """``"UROY -> CIK 0002143673 (8-K12B 2026-07-28)"`` — the page / report line for one row."""
        who = self.ticker or str(self.security_id)
        bits = []
        for c in self.candidates:
            ev = c.succession_filings[0] if c.succession_filings else None
            tail = f" ({ev.form} {ev.filed.isoformat()})" if ev else ""
            bits.append(f"CIK {c.cik}{tail}")
        return f"{who} -> {' or '.join(bits)}"

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "price_symbol": self.price_symbol,
            "stored_cik": self.stored_cik,
            "master_name": self.master_name,
            "theses": [{"id": str(i), "name": n} for i, n in self.theses],
            "old_entity_name": self.old_entity_name,
            "old_former_names": [
                {"name": f.name, "since": f.since, "until": f.until} for f in self.old_former_names
            ],
            "deregistrations": [f.to_json() for f in self.deregistrations],
            "old_error": self.old_error,
            "candidates": [c.to_json() for c in self.candidates],
            "recommended": self.recommended,
            "key": self.key,
            "label": self.label,
        }


@dataclass
class SuccessorScan:
    """One pass's successor-CIK outcome — the artifact's ``successor_cik`` block, the printed report and
    the R4 page. ``evaluated`` = the scan ran (a live pass whose reads succeeded — including the common
    night with nothing flagged); ``error`` = why it did not, on a live pass. ``judged`` = members whose
    stored CIK's symbol list was read tonight; ``no_ticker`` = of those, the ones listing none (condition
    (a)); ``new`` = rows absent from the previous evaluated pass (what PAGES, once); ``fetches`` = SEC
    requests the scan itself made (the ticker map + one per successor read)."""

    evaluated: bool = False
    rows: tuple[SuccessorRow, ...] = ()
    new: tuple[SuccessorRow, ...] = ()
    error: str | None = None
    judged: int = 0
    no_ticker: int = 0
    fetches: int = 0
    duration_s: float = 0.0

    @property
    def known(self) -> int:
        return len(self.rows) - len(self.new)

    @property
    def new_labels(self) -> tuple[str, ...]:
        return tuple(r.label for r in self.new)

    def payload(self) -> dict:
        """The run artifact's ``successor_cik`` key (ONE nested object, like ``rename_probe``). Readers use
        ``.get``: an artifact written before T7 lacks it and reads as "not evaluated", never an alarm.
        """
        return {
            "successor_cik": {
                "evaluated": self.evaluated,
                "error": self.error,
                "judged": self.judged,
                "no_ticker": self.no_ticker,
                "fetches": self.fetches,
                "duration_s": round(self.duration_s, 3),
                "rows": [r.to_json() for r in self.rows],
                "new": [{"key": r.key, "label": r.label} for r in self.new],
            }
        }


# --- the nightly unit ----------------------------------------------------------------------------------


@dataclass
class _Member:
    security_id: UUID
    listed: tuple[str, ...] | None
    theses: list[tuple[UUID, str]] = field(default_factory=list)


def members_of(results: Iterable) -> dict[UUID, _Member]:
    """Every member the pass ingested, deduped by security (EDGAR's symbol list for its stored CIK, as
    the ingest read it), with every thesis that holds it. Tolerates a result without ``ingested``.
    """
    out: dict[UUID, _Member] = {}
    for r in results:
        ref = (getattr(r, "thesis_id", None), str(getattr(r, "name", "") or ""))
        for x in getattr(r, "ingested", ()) or ():
            m = out.get(x.security_id)
            if m is None:
                m = out[x.security_id] = _Member(x.security_id, getattr(x, "edgar_tickers", None))
            elif m.listed is None:
                m.listed = getattr(x, "edgar_tickers", None)
            if ref[0] is not None and ref not in m.theses:
                m.theses.append(ref)
    return out


_MASTER_SQL = "SELECT id, cik, ticker, price_symbol, name FROM security_master WHERE id = ANY(%s)"


def _read_master(conn: psycopg.Connection, ids: list[UUID]) -> dict[UUID, dict]:
    with conn.cursor() as cur:
        cur.execute(_MASTER_SQL, (ids,))
        return {r["id"]: dict(r) for r in cur.fetchall()}


def _successor(client: EdgarClient, cik: str, matched_on: tuple[str, ...]) -> SuccessorCandidate:
    """ONE submissions read of a successor CIK -> its evidence + the identity an Adopt applies. Only a
    GENUINE submissions doc yields an identity (a top-level ``cik`` — the ``enrich_for_ciks`` guard), so a
    broken response can never become a false identity. Never raises."""
    try:
        subs = fetch_submissions(client, cik)
    except Exception as e:  # noqa: BLE001 — one candidate's read, recorded
        return SuccessorCandidate(cik=cik, matched_on=matched_on, error=_err(e))
    if not isinstance(subs, dict) or not subs.get("cik"):
        return SuccessorCandidate(
            cik=cik, matched_on=matched_on, error="not a submissions document (no top-level cik)"
        )
    return SuccessorCandidate(
        cik=cik,
        matched_on=matched_on,
        entity_name=entity_name(subs),
        edgar_tickers=edgar_tickers(subs),
        former_names=former_names(subs),
        succession_filings=succession_filings(subs),
        identity=parse_identity(subs).model_dump(mode="json"),
    )


def _old_evidence(client: EdgarClient, cik: str) -> tuple[str | None, tuple, tuple, str | None]:
    """The OLD CIK's evidence from the cache the ingest just filled (``client`` is cache-only): ``(entity
    name, formerNames, deregistration filings, error)``. Never raises."""
    try:
        subs = fetch_submissions(client, cik)
    except Exception as e:  # noqa: BLE001 — evidence only; the flag does not depend on it
        return None, (), (), _err(e)
    return entity_name(subs), former_names(subs), deregistration_filings(subs), None


def _default_live_client() -> EdgarClient:
    """The scan's NETWORK client: the recurring pass's five-minute TTL (``RECURRING_CACHE_TTL_S``), so
    tonight's ticker map and each successor's submissions are tonight's, never a daytime-warm copy.
    """
    return EdgarClient(allow_live=True, cache_ttl_s=RECURRING_CACHE_TTL_S)


def _default_cache_client() -> EdgarClient:
    """The scan's CACHE-ONLY client for the old CIK's evidence: the ingest fetched that doc minutes ago, so
    a stale-by-TTL copy is still tonight's — re-fetching it would cost one request per flagged name for
    nothing."""
    return EdgarClient(allow_live=False)


def run_successor_scan(
    *,
    results: Iterable,
    allow_live: bool,
    connect: Callable[[], psycopg.Connection],
    previous: Callable[[], set[str] | None],
    clock: Callable[[], float],
    live_client: EdgarClient | None = None,
    cache_client: EdgarClient | None = None,
) -> SuccessorScan:
    """The nightly unit (module docstring): judge every ingested member, fetch SEC's ticker map ONCE when
    some member lists no tickers, read each flagged candidate's evidence, diff against the previous
    evaluated pass. FAIL-OPEN for the pass. A ``--no-live`` pass does not scan (its symbol lists are cache
    reads, and it must never become the diff baseline). ``previous`` is called only after a successful
    scan. The clients are seams (tests inject a cache dir); by default the network client carries the
    recurring TTL and the evidence client is cache-only."""
    if not allow_live:
        return SuccessorScan()
    t0 = clock()
    live = None
    before = 0
    try:
        members = members_of(results)
        judged = [m for m in members.values() if m.listed is not None]
        empty = [m for m in judged if m.listed == ()]
        rows: list[SuccessorRow] = []
        if empty:
            conn = connect()
            try:
                conn.read_only = (
                    True  # the scan reads the master; structurally it cannot write a row
                )
                stored = _read_master(conn, [m.security_id for m in empty])
                conn.rollback()
            finally:
                conn.close()
            live = live_client or _default_live_client()
            before = live.live_fetches
            ticker_map = fetch_ticker_map(live)
            cache = cache_client or _default_cache_client()
            for m in sorted(empty, key=lambda x: str(x.security_id)):
                s = stored.get(m.security_id)
                if s is None:
                    continue
                found = successor_ciks(
                    stored_cik=s["cik"],
                    ticker=s["ticker"],
                    price_symbol=s["price_symbol"],
                    listed=m.listed,
                    ticker_map=ticker_map,
                )
                if not found:
                    continue
                old_name, old_formers, dereg, old_err = _old_evidence(cache, s["cik"])
                rows.append(
                    SuccessorRow(
                        security_id=m.security_id,
                        ticker=s["ticker"],
                        price_symbol=s["price_symbol"],
                        # AS STORED (the Adopt's drift check compares it to the master verbatim)
                        stored_cik=str(s["cik"]),
                        master_name=s["name"],
                        candidates=tuple(_successor(live, c, on) for c, on in found.items()),
                        theses=tuple(m.theses),
                        old_entity_name=old_name,
                        old_former_names=old_formers,
                        deregistrations=dereg,
                        old_error=old_err,
                    )
                )
        rows.sort(key=lambda r: (r.ticker or "", str(r.security_id)))
    except Exception as e:  # noqa: BLE001 — a monitor fault is reported, never a failed pass
        return SuccessorScan(
            error=_err(e),
            fetches=(live.live_fetches - before) if live is not None else 0,
            duration_s=clock() - t0,
        )
    try:
        prior = previous()
    except (
        Exception
    ):  # noqa: BLE001 — an unreadable baseline errs LOUD (a repeat page, never a miss)
        prior = None
    new = tuple(r for r in rows if prior is None or r.key not in prior)
    return SuccessorScan(
        evaluated=True,
        rows=tuple(rows),
        new=new,
        judged=len(judged),
        no_ticker=len(empty),
        fetches=(live.live_fetches - before) if live is not None else 0,
        duration_s=clock() - t0,
    )


# --- the baseline + the readers ------------------------------------------------------------------------


def keys_of(doc: Mapping) -> set[str]:
    """The row keys one artifact's ``successor_cik`` block holds — what the next pass diffs against.
    Fail-open: a malformed row is skipped."""
    block = doc.get("successor_cik")
    if not isinstance(block, Mapping):
        return set()
    return {
        str(r["key"]) for r in block.get("rows") or [] if isinstance(r, Mapping) and r.get("key")
    }


def is_baseline(doc: Mapping) -> bool:
    """May this artifact be the diff baseline (and the Admin inventory)? A LIVE, FINISHED (M1), FULL-ROSTER
    (M2) pass whose scan was ``evaluated`` (a pre-T7 artifact or a failed scan carries no inventory — read as
    "nothing flagged" it would re-page every known row). (The stale-tape and name-error baselines no longer
    use the full-roster rule: they carry each thesis forward, ``cron_run_log.carried_inventory``. This scan
    judges only the ingested members, so a partial catch-up has the same question here — still open.)
    """
    from pipeline.cron_run_log import (  # lazy: cron_run_log imports this module
        is_finished,
        is_full_roster,
    )

    block = doc.get("successor_cik")
    return (
        doc.get("mode") == "live"
        and isinstance(block, Mapping)
        and bool(block.get("evaluated"))
        and is_finished(doc)  # type: ignore[arg-type]
        and is_full_roster(doc)  # type: ignore[arg-type]
    )


def previous_successor_keys(*, base_dir: Path | None = None) -> set[str] | None:
    """The row keys of the most recent BASELINE artifact (``is_baseline``) — ``None`` (distinct from an
    empty set) when none exists yet: the first pass after T7 deploys pages its whole inventory ONCE.
    Newest-first, first match wins; fail-open per artifact. Pure file read."""
    from pipeline.cron_run_log import list_run_logs  # lazy: cron_run_log imports this module

    for doc in list_run_logs(base_dir=base_dir):
        if is_baseline(doc):
            return keys_of(doc)
    return None


def new_labels_of(doc: Mapping) -> tuple[str, ...]:
    """The NEW-row labels one artifact recorded (what that pass paged) — the Admin history re-derives the
    run's page from these. ``()`` for an artifact without the block; a malformed entry is skipped.
    """
    block = doc.get("successor_cik")
    if not isinstance(block, Mapping):
        return ()
    return tuple(
        str(r["label"]) for r in block.get("new") or [] if isinstance(r, Mapping) and r.get("label")
    )


def report_lines(scan: SuccessorScan) -> list[str]:
    """The cron's printed block (``pipeline.daily._report``): NEW rows LOUD (one line each, with the old
    CIK's deregistration), KNOWN ones ONE quiet lowercase line, a failed scan LOUD, nothing at all on a
    clean or un-scanned (``--no-live``) night — loudness marks the exception."""
    if scan.error:
        return [f"SUCCESSOR-CIK DETECTOR FAILED — {scan.error} (tonight's CIKs were not checked)"]
    if not scan.evaluated or not scan.rows:
        return []
    lines: list[str] = []
    if scan.new:
        lines.append(
            "SUCCESSOR CIK — NEW: the company now files under another CIK, so the filing legs read the "
            "dead one (Form 4 / 8-K / 13D/G missed); Adopt on the Admin page:"
        )
        for r in scan.new:
            d = r.deregistrations[0] if r.deregistrations else None
            dereg = f"; CIK {r.stored_cik} filed {d.form} {d.filed.isoformat()}" if d else ""
            lines.append(f"  {r.label}{dereg}")
    if scan.known:
        fresh = {r.key for r in scan.new}
        known = [r for r in scan.rows if r.key not in fresh]
        lines.append(
            f"{scan.known} known successor-CIK name(s) awaiting the operator's Adopt (Admin page): "
            f"{', '.join(r.ticker or str(r.security_id) for r in known)}"
        )
    return lines
