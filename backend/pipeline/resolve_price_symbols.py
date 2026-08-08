"""Resolve OTC vendor price symbols across the baskets — the scope + backfill CLI for the #2 self-heal.

The finalize pull (Slice C) heals a name AS the operator finalizes it; this productizes the same resolver
across EXISTING basket members — the standing sweep for names promoted before the fix (the 6 starved OTC
names: VUECF, FDCT, VREOD, CURLD, PRTC, WONDF). Per OTC member with an unresolved ``price_symbol``, it runs
the SAME deterministic resolver (``ingest.prices.resolve_symbol.resolve_price_symbol`` — search + US/name
filter + history confirmation, no LLM, no guess, #3) and adopts an AUTO; a FLAG is surfaced for the operator
to ``--adopt``; a NONE is kept (priced under its canonical ticker — recall is sacred, #9). Writes go through
the narrow ``master.set_price_symbol`` (UPDATE-in-place), committed per thesis (resumable).

**Operator-triggered, never ambient (the cost thread):** this is a hand-run command, never the cron. The
candidate PRE-GATE keeps cost off healthy names: only an OTC member with THIN stored history (< the
longest-active-lookback threshold, ``THIN_HISTORY_BARS``) is a candidate — a name already carrying a full
year of tape is skipped WITHOUT a resolver call. ``--all-otc`` widens the gate to every OTC member.

Scopes (bare invocation = ``--baskets``):

- ``--thesis <id>`` — one thesis's resolved basket.
- ``--baskets`` — every (non-archived) thesis, each under its OWN tenant (the daily cron's pattern).

Modes:

- default — only-fills-empty: a member with a stored ``price_symbol`` is KEPT untouched (the frozen
  resolution wins). ``--overwrite`` re-resolves every candidate (the correction path).
- ``--dry-run`` — classify only, ZERO writes (would-adopt / flag / none-kept / healthy-skipped on the receipt).
- ``--adopt TICKER=SYMBOL`` (repeatable) — the operator-confirm path for a FLAG: adopt the named symbol
  directly (basis ``operator:adopt``), bypassing the AUTO gate and the thin/fill-empty pre-gates.
- ``--live`` gates live Yahoo fetches; cache-first offline (an uncached member errors softly — counted,
  never a false write). A per-member resolver error NEVER crashes the thesis (recall-friendly).

Exit codes: 0 on a normal run; 1 when any thesis was refused (a thesis-level fault), or a ``--live``
(non-dry-run) run ADOPTED NOTHING while candidates existed (the ``enrich_identity`` scriptable-health
precedent — that shape is a network/UA fault wearing a clean exit).

    python -m pipeline.resolve_price_symbols --dry-run                 # classify (bare = --baskets), no writes
    python -m pipeline.resolve_price_symbols --live                    # the real sweep (needs live Yahoo)
    python -m pipeline.resolve_price_symbols --thesis <uuid> --live --all-otc
    python -m pipeline.resolve_price_symbols --thesis <uuid> --live --adopt CURLD=CURLF   # confirm a FLAG
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID, connect
from domain.market_time import market_today
from domain.thesis import Thesis
from ingest.prices.eod_loader import recent_distinct_bar_counts
from ingest.prices.resolve_symbol import resolve_price_symbol
from repositories import thesis_repo
from securities import master
from securities.price_history_health import THIN_HISTORY_BARS, is_thin_history


@dataclass
class ThesisReceipt:
    """One thesis's resolve outcome: the member buckets (adopted / flagged / none-kept / healthy-skipped /
    not-OTC / no-ticker / already-resolved / errored) + the resolver-ran candidate count (drives the
    ``--live`` exit gate), plus the flag/adopt detail lines for the printout. 'Did the sweep run and what
    did it write' is answerable from this — an operator-visible action, not an ambient side effect.
    """

    thesis_id: UUID
    tenant_id: UUID
    name: str
    members: int = 0
    candidates: int = (
        0  # members the resolver RAN on (thin+empty, or --all-otc+empty) — the exit-gate base
    )
    adopted: int = 0  # AUTO adopted (would-adopt under --dry-run)
    operator_adopted: int = 0  # --adopt TICKER=SYMBOL applied
    flagged: int = 0  # FLAG — surfaced for the operator, nothing written
    none_kept: int = 0  # NONE — no usable quote, kept under the canonical ticker (#9)
    healthy_skipped: int = 0  # OTC but enough history — no resolver call (cost not spent)
    not_otc: int = 0  # not an OTC line — the fix is OTC-scoped
    no_ticker: int = 0  # no security_id / no ticker — nothing to resolve
    already_resolved: int = 0  # price_symbol set, no --overwrite — kept
    errored: int = (
        0  # a per-member resolver error (uncached offline / network) — counted, never fatal
    )
    refused: bool = False  # a thesis-level fault (drives exit 1)
    skipped: str | None = None
    adopts: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (ticker, symbol, basis) for print
    flags: list[tuple[str, str | None, str]] = field(
        default_factory=list
    )  # (ticker, candidate, why)


def resolve_thesis(
    conn: psycopg.Connection,
    thesis: Thesis,
    *,
    allow_live: bool,
    overwrite: bool = False,
    all_otc: bool = False,
    dry_run: bool = False,
    adopt_map: dict[str, str] | None = None,
) -> ThesisReceipt:
    """Resolve one thesis's OTC members. Classifies every member into the receipt buckets; runs the
    resolver ONLY on candidates (thin OTC, or all OTC under ``--all-otc``); writes only through
    ``set_price_symbol`` (skipped under ``--dry-run``). A per-member resolver error is caught and counted,
    never fatal. The caller owns the transaction (commit per thesis)."""
    adopt_map = adopt_map or {}
    tenant = thesis.tenant_id or DEFAULT_TENANT_ID
    r = ThesisReceipt(
        thesis_id=thesis.id, tenant_id=tenant, name=thesis.name, members=len(thesis.basket)
    )
    sids = [m.security_id for m in thesis.basket if m.security_id is not None]
    secs = master.get_many(conn, sids, tenant_id=tenant) if sids else {}
    asof = market_today()
    bar_counts = recent_distinct_bar_counts(conn, sids, asof=asof, tenant_id=tenant) if sids else {}

    for m in thesis.basket:
        sec = secs.get(m.security_id) if m.security_id else None
        if sec is None or not sec.ticker:
            r.no_ticker += 1
            continue
        tkr = sec.ticker.upper()

        # operator adopt — an explicit correction, bypasses the OTC/thin/fill-empty gates for the named ticker
        if tkr in adopt_map:
            sym = adopt_map[tkr]
            if not dry_run:
                master.set_price_symbol(conn, sec.id, sym, basis="operator:adopt", tenant_id=tenant)
            r.operator_adopted += 1
            r.adopts.append((tkr, sym, "operator:adopt"))
            continue

        if sec.exchange != "OTC":
            r.not_otc += 1  # the fix is OTC-scoped
            continue
        if sec.price_symbol is not None and not overwrite:
            r.already_resolved += 1  # the frozen resolution wins (only-fills-empty)
            continue
        bars = bar_counts.get(sec.id, 0)
        if not all_otc and not is_thin_history(bars):
            r.healthy_skipped += 1  # enough tape — cost not spent
            continue

        # a candidate: spend the resolver call (deliberately, on this member)
        r.candidates += 1
        try:
            proposal = resolve_price_symbol(sec, allow_live=allow_live)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — a per-member fetch fault is counted, never fatal (recall)
            r.errored += 1
            r.flags.append((tkr, None, f"resolve error: {exc}"))
            continue

        if proposal.tier == "AUTO" and proposal.proposed_symbol:
            if not dry_run:
                master.set_price_symbol(
                    conn,
                    sec.id,
                    proposal.proposed_symbol,
                    basis=f"resolver:auto {proposal.why}",
                    tenant_id=tenant,
                )
            r.adopted += 1
            r.adopts.append((tkr, proposal.proposed_symbol, "resolver:auto"))
        elif proposal.tier == "FLAG":
            r.flagged += 1
            r.flags.append((tkr, proposal.proposed_symbol, proposal.why))
        else:  # NONE — kept under the canonical ticker (#9), the thin flag marks it starved
            r.none_kept += 1
    return r


def run_resolve(
    conn: psycopg.Connection,
    *,
    thesis_id: UUID | None = None,
    allow_live: bool = False,
    overwrite: bool = False,
    all_otc: bool = False,
    dry_run: bool = False,
    adopt_map: dict[str, str] | None = None,
) -> list[ThesisReceipt]:
    """Resolve the scope (one thesis, or every non-archived thesis — each under its own tenant), resolve
    each, COMMIT PER THESIS (a crashed run keeps its progress; a thesis-level fault refuses THAT thesis and
    continues), return the receipts."""
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis {thesis_id} not found")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)
    receipts: list[ThesisReceipt] = []
    for thesis in theses:
        try:
            r = resolve_thesis(
                conn,
                thesis,
                allow_live=allow_live,
                overwrite=overwrite,
                all_otc=all_otc,
                dry_run=dry_run,
                adopt_map=adopt_map,
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 — one bad thesis never crashes the whole sweep
            conn.rollback()
            r = ThesisReceipt(
                thesis_id=thesis.id,
                tenant_id=thesis.tenant_id or DEFAULT_TENANT_ID,
                name=thesis.name,
                refused=True,
                skipped=f"thesis fault: {exc}",
            )
        receipts.append(r)
    return receipts


def _parse_adopts(raw: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--adopt TICKER=SYMBOL`` into ``{TICKER: SYMBOL}`` (upper-cased). A malformed entry
    (no ``=`` / empty side) is a hard error — a silently-ignored adopt is the invisible-failure class.
    """
    out: dict[str, str] = {}
    for entry in raw or []:
        if "=" not in entry:
            raise SystemExit(f"--adopt expects TICKER=SYMBOL, got {entry!r}")
        tkr, _, sym = entry.partition("=")
        tkr, sym = tkr.strip().upper(), sym.strip().upper()
        if not tkr or not sym:
            raise SystemExit(f"--adopt expects TICKER=SYMBOL, got {entry!r}")
        out[tkr] = sym
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Resolve OTC vendor price symbols across the baskets (the #2 self-heal sweep). Bare "
        f"invocation = --baskets. Candidates are OTC members with < {THIN_HISTORY_BARS} stored bar-dates "
        "in the trailing year (--all-otc widens to all OTC)."
    )
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--thesis", default=None, help="one thesis id (uuid): its resolved basket")
    scope.add_argument(
        "--baskets",
        action="store_true",
        help="every non-archived thesis (the DEFAULT when no scope is given)",
    )
    p.add_argument(
        "--live", action="store_true", help="allow live Yahoo fetches (else cache-first)"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="classify only — ZERO writes (would-adopt on the receipt)",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="re-resolve every candidate, even one with a stored price_symbol (the correction path; "
        "default only-fills-empty)",
    )
    p.add_argument(
        "--all-otc",
        action="store_true",
        help="widen the candidate gate to EVERY OTC member (default: only thin-history OTC members)",
    )
    p.add_argument(
        "--adopt",
        action="append",
        default=None,
        metavar="TICKER=SYMBOL",
        help="operator-confirm a FLAG: adopt SYMBOL for TICKER directly (repeatable; basis operator:adopt)",
    )
    a = p.parse_args(argv)
    adopt_map = _parse_adopts(a.adopt)

    conn = connect()
    try:
        receipts = run_resolve(
            conn,
            thesis_id=UUID(a.thesis) if a.thesis else None,
            allow_live=a.live,
            overwrite=a.overwrite,
            all_otc=a.all_otc,
            dry_run=a.dry_run,
            adopt_map=adopt_map,
        )
    finally:
        conn.close()

    verb = "would adopt" if a.dry_run else "adopted"
    for r in receipts:
        head = f"thesis {r.name!r} ({r.thesis_id}) [tenant {r.tenant_id}]: {r.members} member(s)"
        if r.refused:
            print(f"{head} -> REFUSED ({r.skipped})")
            continue
        print(
            f"{head} -> {r.adopted} {verb}, {r.operator_adopted} operator-adopted, {r.flagged} flagged, "
            f"{r.none_kept} none-kept, {r.healthy_skipped} healthy-skipped, {r.already_resolved} "
            f"already-resolved, {r.not_otc} non-OTC, {r.no_ticker} no-ticker, {r.errored} errored"
        )
        for tkr, sym, basis in r.adopts:
            print(f"    {'WOULD ADOPT' if a.dry_run else 'ADOPT'} {tkr} -> {sym} [{basis}]")
        for tkr, cand, why in r.flags:
            print(f"    FLAG {tkr}: {cand or '-'} — {why}")

    total_adopted = sum(r.adopted for r in receipts)
    total_candidates = sum(r.candidates for r in receipts)
    refused = sum(1 for r in receipts if r.refused)
    print(
        f"TOTAL: {total_adopted} {verb}, {sum(r.operator_adopted for r in receipts)} operator-adopted, "
        f"{sum(r.flagged for r in receipts)} flagged, {sum(r.none_kept for r in receipts)} none-kept "
        f"across {len(receipts)} thesis(es), {refused} refused"
    )

    if refused:
        # A thesis-level fault must surface to a wrapper, never a clean exit.
        raise SystemExit(1)
    if a.live and not a.dry_run and total_adopted == 0 and total_candidates > 0:
        # The scriptable-health gate (the enrich_identity / backfill precedent): a live sweep that adopted
        # NOTHING while candidates existed is a network/UA fault wearing a clean exit.
        print(
            "RESOLVE: --live run adopted nothing while candidates existed — investigate before trusting"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
