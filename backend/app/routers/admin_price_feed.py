"""The Admin "Price feed repairs" surface (T2 + Q8 + T3) — the operator's ratification of what the nightly
tape monitor found, every action with a visible, EXACT inverse (WB#1).

What the pass finds (read, never applied — #3):

- **renamed** names (T1's rename guard) with the nightly SPLICE PROBE's verdict on each symbol EDGAR now
  lists (``pipeline/rename_probe.py`` — the verified successor carries OUR tape under its new symbol);
- **unpriceable** names (Q8): no stored bar ever, neither closed nor renamed (WONDF);
- **closed** names (T1): stopped trading — no repair; their vendor 404 is expected (T3).

What the operator can do here, and its inverse:

- **Adopt** a renamed name's probed successor -> ``price_symbol`` set through the EXISTING writer
  (``master.set_price_symbol(basis="operator:adopt")``), the display name refreshed from EDGAR's current
  entity name (read by the pass from the cached submissions — no fetch). **Undo adopt** restores the prior
  ``price_symbol`` / ``price_symbol_basis`` / ``name`` byte-for-byte (``price_symbol_change``, 0046).
- **Set price symbol** on an unpriceable name (the operator types a vendor symbol, e.g. a TSX listing) ->
  validated by ONE vendor fetch into a throwaway cache (bars must exist), then set the same way
  (``basis="operator:set"``). Same Undo.
- **Acknowledge, keep unpriced** -> an append-only ``price_feed_ack`` row: the name goes QUIET (no page, a
  quiet count, still listed — #9) and its 404 counts as expected (T3). **Revoke** appends the inverse.
- **Remove from basket** is NOT here: the panel links to the existing basket editor (no second delete path).

T7 — the SUCCESSOR CIK (``pipeline/successor_cik.py``): a member whose company now files under a NEW CIK
(the CIK we store lists no tickers, and SEC's ticker map assigns our symbol to another registrant — UROY,
REAX), so every filing leg reads the dead CIK.

- **Adopt successor CIK** -> ``security_master.cik`` re-pointed IN PLACE (``master.set_cik`` — the row id
  stays, so the tape, the facts, the basket links and the sign-off stay), and the CIK-dependent identity
  refreshed from the successor's submissions the detector already read (the ``enrich_identity``
  derivation — ``master.enrich`` over ``parse_identity`` — plus the entity name). Only the CIK the detector
  RECOMMENDED is accepted, and the click makes no network request. **Undo** restores every touched column
  byte-for-byte (``security_cik_change``, 0049).

Nothing runs without the operator's click; every write names the operator as its author (``basis``), runs
in one transaction under the master row's lock, and is append-only. The read model (``price_repairs_out``)
is consumed by ``GET /admin/status`` (``admin.py``), which stays a read: it reads the run artifacts and the
repair log and writes nothing. Auth stays deferred project-wide.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException

from app.deps import get_conn
from app.schemas_api import (
    AdminAcknowledgeIn,
    AdminCikChangeOut,
    AdminFilingRefOut,
    AdminFormerNameOut,
    AdminNameChangeFilingOut,
    AdminPriceFeedAckOut,
    AdminPriceRepairsOut,
    AdminPriceSymbolChangeOut,
    AdminPriceSymbolIn,
    AdminProbeCandidateOut,
    AdminRenamedRowOut,
    AdminStaleTapeOut,
    AdminSuccessorCandidateOut,
    AdminSuccessorCikIn,
    AdminSuccessorRowOut,
    AdminThesisRefOut,
    AdminUnpriceableRowOut,
)
from domain.feed_kinds import PRICE, feed_kind
from domain.security import SecurityIdentity
from ingest.prices.source import DEFAULT_RANGE, PriceSource, YahooPriceSource
from pipeline import successor_cik
from pipeline.cron_run_log import is_finished, is_full_roster, list_run_logs
from pipeline.tape_health import is_unpriceable
from repositories import cik_change_repo, price_feed_repo
from repositories.cik_change_repo import CikChange, StandingCikChange
from repositories.price_feed_repo import (
    PriceFeedAck,
    PriceSymbolChange,
    RepairConflict,
    StandingChange,
)

router = APIRouter(prefix="/admin/price-feed", tags=["admin"])

# A vendor symbol as an operator would type one: letters / digits plus the venue and class punctuation
# Yahoo uses (``WNDR.TO``, ``BRK-B``, ``^GSPC``, ``EURUSD=X``). Upper-cased before it is checked or stored.
_SYMBOL = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,23}$")
_DEFAULT_ACK_REASON = "kept unpriced by the operator"
_MAX_ERROR = 300

BASIS_ADOPT = "operator:adopt"
BASIS_SET = "operator:set"
BASIS_UNDO = "operator:undo"
BASIS_ACK = "operator:acknowledge"
BASIS_REVOKE = "operator:revoke"
BASIS_ADOPT_CIK = "operator:adopt-cik"


# --- the inventory: the newest pass that looked ---------------------------------------------------------


@dataclass
class _Row:
    """One price row of the inventory pass, folded across the theses that hold the name."""

    security_id: UUID
    ticker: str | None
    edge: date | None
    closed_at: date | None
    renamed: bool
    unpriceable: bool
    edgar_tickers: list[str] | None
    theses: list[AdminThesisRefOut] = field(default_factory=list)
    first_thesis: str = ""
    vendor_error: str | None = None


def inventory_doc(payloads: list[dict]) -> dict | None:
    """The newest LIVE, FINISHED (M1), FULL-ROSTER (M2) artifact that EVALUATED feed recency — the same pass
    the freshness panel and the newly-stale baseline read, so this panel and the page agree. ``None`` = no
    pass has looked yet."""
    for doc in payloads:
        if (
            doc.get("mode") == "live"
            and doc.get("tape_evaluated", False)
            and is_finished(doc)
            and is_full_roster(doc)
        ):
            return doc
    return None


def _date(v: object) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _inventory(doc: dict) -> dict[UUID, _Row]:
    """Every PRICE row of ``doc``'s stale inventory, deduped by security (first thesis names it; every
    holding thesis is listed), with the vendor error the price leg got that night (a name error, or the
    message T3 excused). Fail-open per row: a malformed row is skipped, never a raise."""
    rows: dict[UUID, _Row] = {}
    for t in doc.get("theses") or []:
        if not isinstance(t, dict):
            continue
        errors: dict[str, str] = {}
        for e in t.get("name_errors") or []:
            if isinstance(e, dict) and e.get("leg") == "price" and e.get("security_id"):
                errors.setdefault(str(e["security_id"]), str(e.get("error") or ""))
        for m in t.get("expected_price_misses") or []:
            if isinstance(m, dict) and m.get("security_id"):
                errors.setdefault(str(m["security_id"]), str(m.get("error") or ""))
        try:
            ref = AdminThesisRefOut(id=UUID(str(t["id"])), name=str(t.get("name") or ""))
        except Exception:  # noqa: BLE001 — a thesis entry without an id still contributes rows
            ref = None
        for s in t.get("tape_stale") or []:
            try:
                if not isinstance(s, dict) or feed_kind(s.get("kind")).key != PRICE.key:
                    continue
                sid = UUID(str(s["security_id"]))
                row = rows.get(sid)
                if row is None:
                    edge = _date(s.get("edge"))
                    closed_at = _date(s.get("closed_at"))
                    renamed = bool(s.get("renamed"))
                    listed = s.get("edgar_tickers")
                    row = _Row(
                        security_id=sid,
                        ticker=s.get("ticker"),
                        edge=edge,
                        closed_at=closed_at,
                        renamed=renamed,
                        # derived when the artifact predates the key (the one definition)
                        unpriceable=bool(
                            s.get(
                                "unpriceable",
                                is_unpriceable(
                                    kind=s.get("kind"),
                                    edge=edge,
                                    closed_at=closed_at,
                                    renamed=renamed,
                                ),
                            )
                        ),
                        edgar_tickers=(
                            [str(x) for x in listed] if isinstance(listed, list) else None
                        ),
                        first_thesis=str(t.get("name") or ""),
                        vendor_error=errors.get(str(sid)),
                    )
                    rows[sid] = row
                if ref is not None and all(x.id != ref.id for x in row.theses):
                    row.theses.append(ref)
            except Exception:  # noqa: BLE001 — skip a malformed row, never blank the panel
                continue
    return rows


def _probe_rows(doc: dict | None) -> tuple[dict[UUID, dict], bool, str | None]:
    """``(security_id -> probe row, evaluated, error)`` from an artifact's ``rename_probe`` block."""
    block = (doc or {}).get("rename_probe")
    if not isinstance(block, dict):
        return {}, False, None
    out: dict[UUID, dict] = {}
    for r in block.get("rows") or []:
        try:
            out[UUID(str(r["security_id"]))] = r
        except Exception:  # noqa: BLE001 — a malformed row is skipped
            continue
    err = block.get("error")
    return out, bool(block.get("evaluated")), (str(err) if err else None)


# --- T7: the successor-CIK inventory -------------------------------------------------------------------


def successor_doc(payloads: list[dict]) -> dict | None:
    """The newest artifact that may serve as the successor-CIK inventory — a live, finished, full-roster
    pass whose scan RAN (``successor_cik.is_baseline``: the same artifact the next pass's page-once diff
    reads, so this panel and the page agree). ``None`` = no pass has scanned yet."""
    return next((d for d in payloads if successor_cik.is_baseline(d)), None)


def _successor_error(payloads: list[dict]) -> str | None:
    """The newest live, finished pass's successor-scan ERROR when that scan failed (``None`` when it ran,
    or no pass carries the block yet) — loud on the panel, so a broken detector is never silently absent.
    """
    for d in payloads:
        block = d.get("successor_cik")
        if d.get("mode") == "live" and is_finished(d) and isinstance(block, dict):
            return None if block.get("evaluated") else str(block.get("error") or "not evaluated")
    return None


def _successor_rows(doc: dict | None) -> dict[UUID, dict]:
    """``security_id -> row`` from an artifact's ``successor_cik`` block. Fail-open per row."""
    out: dict[UUID, dict] = {}
    block = (doc or {}).get("successor_cik")
    for r in (block.get("rows") if isinstance(block, dict) else None) or []:
        try:
            out[UUID(str(r["security_id"]))] = r
        except Exception:  # noqa: BLE001 — a malformed row is skipped, never a raise
            continue
    return out


def _filings_out(raw: object) -> list[AdminFilingRefOut]:
    return [
        AdminFilingRefOut(**f)
        for f in (raw if isinstance(raw, list) else [])
        if isinstance(f, dict) and f.get("filed") and f.get("form")
    ]


def _formers_out(raw: object) -> list[AdminFormerNameOut]:
    return [
        AdminFormerNameOut(**f)
        for f in (raw if isinstance(raw, list) else [])
        if isinstance(f, dict) and f.get("name")
    ]


def _candidate_out(c: dict) -> AdminSuccessorCandidateOut:
    ident = c.get("identity") if isinstance(c.get("identity"), dict) else None
    return AdminSuccessorCandidateOut(
        cik=str(c["cik"]),
        matched_on=[str(x) for x in c.get("matched_on") or []],
        entity_name=c.get("entity_name"),
        edgar_tickers=[str(x) for x in c.get("edgar_tickers") or []],
        former_names=_formers_out(c.get("former_names")),
        succession_filings=_filings_out(c.get("succession_filings")),
        identity_read=ident is not None,
        recent_foreign_form=(ident or {}).get("recent_foreign_form"),
        files_domestic_forms=(ident or {}).get("files_domestic_forms"),
        incorporation=(ident or {}).get("incorporation"),
        error=c.get("error"),
    )


def _cik_change_out(c: CikChange, *, ticker: str | None = None) -> AdminCikChangeOut:
    standing = isinstance(c, StandingCikChange)
    return AdminCikChangeOut(
        id=c.id,
        security_id=c.security_id,
        ticker=c.ticker if standing else ticker,  # type: ignore[attr-defined]
        action=c.action,
        prior_cik=c.prior_cik,
        new_cik=c.new_cik,
        prior_name=c.prior_values.get("name"),
        new_name=c.new_values.get("name"),
        basis=c.basis,
        created_at=c.created_at,
        undoable=c.undoable if standing else False,  # type: ignore[attr-defined]
        current_cik=c.current_cik if standing else None,  # type: ignore[attr-defined]
    )


def _successor_out(
    conn: psycopg.Connection, payloads: list[dict]
) -> tuple[dict | None, list[AdminSuccessorRowOut], list[AdminCikChangeOut]]:
    """``(the inventory artifact, its rows overlaid with the LIVE CIK log + the master's current CIK, every
    standing re-point)``. SELECTs only."""
    doc = successor_doc(payloads)
    rows = _successor_rows(doc)
    standing = {c.security_id: c for c in cik_change_repo.standing_changes(conn)}
    current: dict[UUID, str | None] = {}
    if rows:
        with conn.cursor() as cur:
            cur.execute("SELECT id, cik FROM security_master WHERE id = ANY(%s)", (list(rows),))
            current = {r["id"]: r["cik"] for r in cur.fetchall()}
    out: list[AdminSuccessorRowOut] = []
    for sid, r in rows.items():
        try:
            out.append(
                AdminSuccessorRowOut(
                    security_id=sid,
                    ticker=r.get("ticker"),
                    theses=[
                        AdminThesisRefOut(id=UUID(str(t["id"])), name=str(t.get("name") or ""))
                        for t in r.get("theses") or []
                        if isinstance(t, dict) and t.get("id")
                    ],
                    stored_cik=str(r["stored_cik"]),
                    master_name=r.get("master_name"),
                    old_entity_name=r.get("old_entity_name"),
                    old_former_names=_formers_out(r.get("old_former_names")),
                    deregistrations=_filings_out(r.get("deregistrations")),
                    candidates=[
                        _candidate_out(c)
                        for c in r.get("candidates") or []
                        if isinstance(c, dict) and c.get("cik")
                    ],
                    recommended=r.get("recommended"),
                    current_cik=current.get(sid),
                    change=_cik_change_out(standing[sid]) if sid in standing else None,
                )
            )
        except Exception:  # noqa: BLE001 — skip a malformed row, never blank the panel
            continue
    return doc, out, [_cik_change_out(c) for c in standing.values()]


# --- the read model ------------------------------------------------------------------------------------


def _change_out(c: PriceSymbolChange, *, ticker: str | None = None) -> AdminPriceSymbolChangeOut:
    standing = isinstance(c, StandingChange)
    return AdminPriceSymbolChangeOut(
        id=c.id,
        security_id=c.security_id,
        ticker=c.ticker if standing else ticker,  # type: ignore[attr-defined]
        action=c.action,
        prior_symbol=c.prior_symbol,
        new_symbol=c.new_symbol,
        prior_basis=c.prior_basis,
        new_basis=c.new_basis,
        name_changed=c.name_changed,
        prior_name=c.prior_name,
        new_name=c.new_name,
        basis=c.basis,
        created_at=c.created_at,
        undoable=c.undoable if standing else False,  # type: ignore[attr-defined]
        current_symbol=c.current_symbol if standing else None,  # type: ignore[attr-defined]
    )


def _ack_out(
    a: PriceFeedAck, *, ticker: str | None, still_unpriceable: bool | None
) -> AdminPriceFeedAckOut:
    return AdminPriceFeedAckOut(
        id=a.id,
        security_id=a.security_id,
        ticker=ticker,
        action=a.action,
        reason=a.reason,
        basis=a.basis,
        created_at=a.created_at,
        still_unpriceable=still_unpriceable,
    )


def _master_symbols(
    conn: psycopg.Connection, ids: list[UUID]
) -> dict[UUID, tuple[str | None, str | None]]:
    """``id -> (ticker, price_symbol)`` for the listed securities (one SELECT)."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker, price_symbol FROM security_master WHERE id = ANY(%s)", (ids,)
        )
        return {r["id"]: (r["ticker"], r["price_symbol"]) for r in cur.fetchall()}


def price_repairs_out(conn: psycopg.Connection, payloads: list[dict]) -> AdminPriceRepairsOut:
    """The panel (see ``AdminPriceRepairsOut``): the inventory from the newest pass that looked, overlaid
    with the LIVE repair logs (standing changes, active acknowledgments, standing CIK re-points) and the
    master's current price symbols / CIKs. SELECTs only — the caller runs it inside a savepoint so a fault
    can never poison the status read."""
    doc = inventory_doc(payloads)
    inv = _inventory(doc) if doc is not None else {}
    probes, probe_evaluated, probe_error = _probe_rows(doc)
    standing = {c.security_id: c for c in price_feed_repo.standing_changes(conn)}
    acks = price_feed_repo.active_acks(conn)
    acked = {a.security_id: a for a in acks}
    symbols = _master_symbols(conn, list({*inv, *acked}))

    renamed: list[AdminRenamedRowOut] = []
    unpriceable: list[AdminUnpriceableRowOut] = []
    closed: list[AdminStaleTapeOut] = []
    for sid, row in inv.items():
        current = symbols.get(sid, (row.ticker, None))[1]
        change = _change_out(standing[sid]) if sid in standing else None
        if row.closed_at is not None:
            closed.append(
                AdminStaleTapeOut(
                    security_id=sid,
                    ticker=row.ticker,
                    edge=row.edge,
                    thesis=row.first_thesis,
                    kind=PRICE.key,
                    closed_at=row.closed_at,
                    edgar_tickers=row.edgar_tickers,
                )
            )
        elif row.renamed:
            p = probes.get(sid) or {}
            renamed.append(
                AdminRenamedRowOut(
                    security_id=sid,
                    ticker=row.ticker,
                    edge=row.edge,
                    theses=row.theses,
                    edgar_tickers=row.edgar_tickers or [],
                    edgar_name=p.get("edgar_name"),
                    former_names=[
                        AdminFormerNameOut(**f)
                        for f in p.get("former_names") or []
                        if isinstance(f, dict) and f.get("name")
                    ],
                    name_change_filings=[
                        AdminNameChangeFilingOut(**f)
                        for f in p.get("name_change_filings") or []
                        if isinstance(f, dict) and f.get("filed")
                    ],
                    candidates=[
                        AdminProbeCandidateOut(**c)
                        for c in p.get("candidates") or []
                        if isinstance(c, dict) and c.get("symbol")
                    ],
                    recommended=p.get("recommended"),
                    probed=bool(p),
                    price_symbol=current,
                    change=change,
                )
            )
        elif row.unpriceable and sid not in acked:
            unpriceable.append(
                AdminUnpriceableRowOut(
                    security_id=sid,
                    ticker=row.ticker,
                    theses=row.theses,
                    edgar_tickers=row.edgar_tickers,
                    vendor_error=row.vendor_error,
                    price_symbol=current,
                    change=change,
                )
            )
    acknowledged = [
        _ack_out(
            a,
            ticker=symbols.get(a.security_id, (None, None))[0],
            still_unpriceable=(inv[a.security_id].unpriceable if a.security_id in inv else None),
        )
        for a in acks
    ]
    expected = {
        str(m.get("security_id"))
        for t in ((doc or {}).get("theses") or [])
        if isinstance(t, dict)
        for m in (t.get("expected_price_misses") or [])
        if isinstance(m, dict)
    }
    # T7 — the successor-CIK block reads its OWN inventory artifact (the newest pass whose scan ran) and the
    # live CIK change log; independent of the price inventory above
    sdoc, successor, cik_applied = _successor_out(conn, payloads)
    return AdminPriceRepairsOut(
        successor_asof=_date((sdoc or {}).get("asof")),
        successor_evaluated=sdoc is not None,
        successor_error=_successor_error(payloads),
        successor=successor,
        cik_applied=cik_applied,
        asof=_date((doc or {}).get("asof")),
        ran_at=str(doc["started_at"]) if doc and doc.get("started_at") else None,
        probe_evaluated=probe_evaluated,
        probe_error=probe_error,
        renamed=renamed,
        unpriceable=unpriceable,
        acknowledged=acknowledged,
        closed=closed,
        applied=[_change_out(c) for c in standing.values()],
        expected_misses=len(expected),
    )


# --- the writes ----------------------------------------------------------------------------------------


def _symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not _SYMBOL.match(s):
        raise HTTPException(status_code=422, detail=f"{raw!r} is not a vendor symbol")
    return s


def _ticker_of(conn: psycopg.Connection, security_id: UUID) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM security_master WHERE id = %s", (security_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="security not found")
    return row["ticker"]


def _inventory_row(security_id: UUID) -> tuple[dict | None, _Row | None]:
    doc = inventory_doc(list_run_logs(limit=20))
    if doc is None:
        return None, None
    return doc, _inventory(doc).get(security_id)


def _validation_source(cache_dir: Path) -> PriceSource:
    """The ``PriceSource`` a Set validates through — Yahoo over a THROWAWAY cache dir (the shared price
    cache is never written by a validation). A seam tests replace."""
    return YahooPriceSource(cache_dir=cache_dir, range_=DEFAULT_RANGE)


def _probe_symbol(symbol: str, source: Callable[[Path], PriceSource] | None = None) -> dict:
    """ONE live vendor fetch of ``symbol`` into a throwaway cache: ``{symbol, bars, first_bar, last_bar}``
    or ``{symbol, error}``. Never raises."""
    with tempfile.TemporaryDirectory(prefix="alphadeck-set-symbol-") as tmp:
        try:
            bars = (source or _validation_source)(Path(tmp)).get_bars(
                symbol, allow_live=True, force_refresh=True
            )
        except Exception as e:  # noqa: BLE001 — a failed validation is the answer, not a 500
            msg = " ".join(f"{type(e).__name__}: {e}".split())
            return {"symbol": symbol, "error": msg[:_MAX_ERROR]}
    days = [b.get("d") for b in bars if b.get("d") is not None]
    return {
        "symbol": symbol,
        "bars": len(bars),
        "first_bar": min(days).isoformat() if days else None,
        "last_bar": max(days).isoformat() if days else None,
    }


def _commit(conn: psycopg.Connection, fn: Callable[[], object]) -> object:
    """Run one repair write in its own transaction: commit on success; on ANY failure roll back and map
    the repository's errors to the operator-facing status (404 unknown, 409 conflict, 422 invalid).
    """
    try:
        out = fn()
        conn.commit()
        return out
    except LookupError as e:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RepairConflict as e:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except BaseException:
        conn.rollback()
        raise


@router.post("/{security_id}/adopt", response_model=AdminPriceSymbolChangeOut)
def adopt_successor(
    security_id: UUID,
    body: AdminPriceSymbolIn,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminPriceSymbolChangeOut:
    """ADOPT a renamed name's successor symbol — the operator's ratification of the nightly splice probe.
    The symbol must be one EDGAR lists for this name on the newest pass that looked (verified or not: the
    probe recommends, the operator decides). Sets ``price_symbol`` through the existing writer (basis
    ``operator:adopt``) and refreshes the display name from EDGAR's current entity name when it differs;
    the change row records the prior values, so ``POST /admin/price-feed/changes/{id}/undo`` restores them
    exactly. The next nightly pass re-pulls the tape under the new symbol. **404** unknown security, **409**
    not a listed candidate / no rename recommendation / nothing to change, **422** a malformed symbol.
    """
    symbol = _symbol(body.symbol)
    ticker = _ticker_of(conn, security_id)
    doc, row = _inventory_row(security_id)
    if doc is None or row is None or not row.renamed:
        raise HTTPException(
            status_code=409,
            detail=f"{ticker or security_id} has no rename recommendation on the last pass — use Set "
            "price symbol (it validates the symbol with a vendor fetch)",
        )
    probes, _, _ = _probe_rows(doc)
    rec = probes.get(security_id) or {}
    cands = {str(c.get("symbol")): c for c in rec.get("candidates") or [] if isinstance(c, dict)}
    listed = row.edgar_tickers or []
    if symbol not in cands and symbol not in listed:
        raise HTTPException(
            status_code=409,
            detail=f"{symbol} is not a symbol EDGAR lists for {ticker or security_id} "
            f"({', '.join(listed) or 'none'}) — use Set price symbol",
        )
    detail = {
        "pass_asof": doc.get("asof"),
        "edgar_tickers": listed,
        "probe": cands.get(symbol),
        "recommended": rec.get("recommended"),
        "edgar_name": rec.get("edgar_name"),
    }
    change = _commit(
        conn,
        lambda: price_feed_repo.apply_change(
            conn,
            security_id,
            action="adopt",
            symbol=symbol,
            basis=BASIS_ADOPT,
            new_name=rec.get("edgar_name"),
            detail=detail,
        ),
    )
    return _change_out(change, ticker=ticker)  # type: ignore[arg-type]


@router.post("/{security_id}/set-symbol", response_model=AdminPriceSymbolChangeOut)
def set_symbol(
    security_id: UUID,
    body: AdminPriceSymbolIn,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminPriceSymbolChangeOut:
    """SET a vendor price symbol the operator typed (an unpriceable name — e.g. its TSX listing).
    VALIDATED first by ONE live vendor fetch into a throwaway cache: the symbol must return at least one
    bar, else **422** and nothing is written. Then set through the existing writer (basis
    ``operator:set``), recorded with the prior values for an exact Undo. **404** unknown security, **409**
    nothing to change, **422** a malformed symbol or no vendor bars."""
    symbol = _symbol(body.symbol)
    ticker = _ticker_of(conn, security_id)
    check = _probe_symbol(symbol)
    if check.get("error") or not check.get("bars"):
        why = check.get("error") or "the vendor returned no bars"
        raise HTTPException(
            status_code=422,
            detail=f"{symbol} did not validate — {why}; nothing was set",
        )
    change = _commit(
        conn,
        lambda: price_feed_repo.apply_change(
            conn,
            security_id,
            action="set",
            symbol=symbol,
            basis=BASIS_SET,
            detail={"validation": check},
        ),
    )
    return _change_out(change, ticker=ticker)  # type: ignore[arg-type]


@router.post("/changes/{change_id}/undo", response_model=AdminPriceSymbolChangeOut)
def undo_change(
    change_id: UUID, conn: psycopg.Connection = Depends(get_conn)
) -> AdminPriceSymbolChangeOut:
    """UNDO an Adopt or a Set — restore the master's prior ``price_symbol`` / ``price_symbol_basis`` (and
    ``name``, when the Adopt refreshed it) EXACTLY, appending an ``undo`` row (basis ``operator:undo``).
    Only the name's standing change, and only while the master still holds its values. **404** unknown
    change, **409** already undone / a later change stands / the master moved since."""
    out = _commit(conn, lambda: price_feed_repo.undo_change(conn, change_id, basis=BASIS_UNDO))
    return _change_out(out, ticker=_ticker_of(conn, out.security_id))  # type: ignore[union-attr,arg-type]


@router.post("/{security_id}/acknowledge", response_model=AdminPriceFeedAckOut)
def acknowledge_unpriceable(
    security_id: UUID,
    body: AdminAcknowledgeIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminPriceFeedAckOut:
    """ACKNOWLEDGE an unpriceable name — "keep it, unpriced" (an append-only ``price_feed_ack``, basis
    ``operator:acknowledge``). From the next pass the name is QUIET (no page, a quiet count, still listed)
    and its vendor 404 counts as expected. Idempotent: an already-acknowledged name returns its active row.
    Revoke with ``POST /admin/price-feed/acks/{id}/revoke``. **404** unknown security, **409** the name is
    not unpriceable on the newest pass that looked."""
    ticker = _ticker_of(conn, security_id)
    doc, row = _inventory_row(security_id)
    if row is None or not row.unpriceable:
        raise HTTPException(
            status_code=409,
            detail=f"{ticker or security_id} is not unpriceable on the last pass — only a never-priced "
            "name (neither closed nor renamed) can be acknowledged",
        )
    reason = ((body.reason if body else None) or "").strip() or _DEFAULT_ACK_REASON
    detail = {
        "pass_asof": (doc or {}).get("asof"),
        "edgar_tickers": row.edgar_tickers,
        "vendor_error": row.vendor_error,
    }
    ack, _created = _commit(  # type: ignore[misc]
        conn,
        lambda: price_feed_repo.acknowledge(
            conn, security_id, reason=reason, basis=BASIS_ACK, detail=detail
        ),
    )
    return _ack_out(ack, ticker=ticker, still_unpriceable=True)


@router.post("/acks/{ack_id}/revoke", response_model=AdminPriceFeedAckOut)
def revoke_acknowledgment(
    ack_id: UUID, conn: psycopg.Connection = Depends(get_conn)
) -> AdminPriceFeedAckOut:
    """REVOKE an acknowledgment — append the inverse row (basis ``operator:revoke``); from the next pass the
    name is a loud unpriceable row again (and pages once). **404** when no active acknowledgment has that
    id."""
    out = _commit(
        conn,
        lambda: price_feed_repo.revoke(
            conn, ack_id, reason="revoked by the operator", basis=BASIS_REVOKE
        ),
    )
    return _ack_out(
        out,  # type: ignore[arg-type]
        ticker=_ticker_of(conn, out.security_id),  # type: ignore[union-attr]
        still_unpriceable=None,
    )


# --- T7: the successor-CIK writes ----------------------------------------------------------------------


def _cik(raw: str) -> str:
    """The operator's CIK in the master's stored form (10 digits, zero-padded); **422** when it is not one."""
    c = successor_cik.cik10((raw or "").strip())
    if c is None:
        raise HTTPException(status_code=422, detail=f"{raw!r} is not a CIK")
    return c


def _successor_row(security_id: UUID) -> tuple[dict | None, dict | None]:
    doc = successor_doc(list_run_logs(limit=20))
    if doc is None:
        return None, None
    return doc, _successor_rows(doc).get(security_id)


@router.post("/{security_id}/adopt-cik", response_model=AdminCikChangeOut)
def adopt_successor_cik(
    security_id: UUID,
    body: AdminSuccessorCikIn,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminCikChangeOut:
    """ADOPT a member's SUCCESSOR CIK — the operator's ratification of the nightly successor-CIK detector
    (the CIK we store lists no tickers; SEC's ticker map assigns our symbol to this registrant). Only the
    CIK the detector RECOMMENDED on the newest pass that scanned is accepted. Re-points
    ``security_master.cik`` IN PLACE (the row id stays — tape, facts, basket links and sign-off are kept)
    and refreshes the CIK-dependent identity from the successor's submissions the detector already read
    (``master.enrich`` over ``parse_identity`` — the ``enrich_identity`` derivation — plus the entity name);
    the click makes no network request. The change row records every touched column's prior value, so
    ``POST /admin/price-feed/cik-changes/{id}/undo`` restores them exactly. The next nightly pass reads the
    successor's filings. **404** unknown security, **409** no recommendation for this name / not the
    recommended CIK / the successor was not read / the master moved since the pass / another master row
    already holds that CIK, **422** a malformed CIK."""
    cik = _cik(body.cik)
    ticker = _ticker_of(conn, security_id)
    doc, row = _successor_row(security_id)
    who = ticker or security_id
    if doc is None or row is None:
        raise HTTPException(
            status_code=409,
            detail=f"{who} has no successor-CIK recommendation on the last pass",
        )
    rec = row.get("recommended")
    if not rec:
        raise HTTPException(
            status_code=409,
            detail=f"SEC's ticker map names more than one CIK for {who} — no single recommendation; "
            "repair it by hand",
        )
    if cik != rec:
        raise HTTPException(
            status_code=409,
            detail=f"CIK {cik} is not the successor the detector recommended for {who} ({rec})",
        )
    cand = next(
        (c for c in row.get("candidates") or [] if isinstance(c, dict) and c.get("cik") == rec),
        None,
    )
    if cand is None or not isinstance(cand.get("identity"), dict):
        why = (cand or {}).get("error") or "no identity was recorded"
        raise HTTPException(
            status_code=409,
            detail=f"CIK {rec}'s submissions were not read on that pass ({why}) — its identity cannot be "
            "applied; the next pass retries the read",
        )
    identity = SecurityIdentity.model_validate(cand["identity"])
    detail = {
        "pass_asof": doc.get("asof"),
        "stored_cik": row.get("stored_cik"),
        "recommended": rec,
        "matched_on": cand.get("matched_on"),
        "entity_name": cand.get("entity_name"),
        "succession_filings": cand.get("succession_filings"),
        "deregistrations": row.get("deregistrations"),
        "old_entity_name": row.get("old_entity_name"),
        "identity": cand["identity"],
    }
    change = _commit(
        conn,
        lambda: cik_change_repo.adopt(
            conn,
            security_id,
            new_cik=rec,
            expected_cik=str(row.get("stored_cik")),
            identity=identity,
            entity_name=cand.get("entity_name"),
            basis=BASIS_ADOPT_CIK,
            detail=detail,
        ),
    )
    return _cik_change_out(change, ticker=ticker)  # type: ignore[arg-type]


@router.post("/cik-changes/{change_id}/undo", response_model=AdminCikChangeOut)
def undo_cik_change(
    change_id: UUID, conn: psycopg.Connection = Depends(get_conn)
) -> AdminCikChangeOut:
    """UNDO a successor-CIK Adopt — restore the master's prior CIK, name and every identity column the Adopt
    refreshed EXACTLY, appending an ``undo`` row (basis ``operator:undo``). Only the name's standing
    re-point, and only while the master still holds its values. **404** unknown change, **409** already
    undone / a later change stands / the master moved since."""
    out = _commit(conn, lambda: cik_change_repo.undo(conn, change_id, basis=BASIS_UNDO))
    return _cik_change_out(out, ticker=_ticker_of(conn, out.security_id))  # type: ignore[union-attr,arg-type]
