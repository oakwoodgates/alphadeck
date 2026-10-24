"""The COMPANY-LIST REFRESH — plan, then apply, SEC's current ticker file onto THIS tenant's security master.

THE BUG THIS REPLACES (MEASURED on prod 2026-10-24, read-only). The broadener (``populate_universe``) keyed every
row on ``(cik, ticker)``: a company SEC now lists under a NEW ticker got a SECOND master row, the old row kept
``is_primary`` ("a row the SEC file no longer carries keeps its last flag"), and the new row was flagged primary
too. ``canonicalize_ids`` (every Workbench Save) then picks the newest primary, so the next Save re-pointed the
basket member to the new, EMPTY row — and every fact keyed by ``security_id`` (the price tape, the Form 4s, the
8-Ks, the 13D/Gs, the fundamentals, the onboarding checkpoint) stayed behind on the old one. 23 live basket
companies have a ticker that changed at SEC since the last load (2026-08-16).

THE RULE NOW — one record per company; a rename updates in place:

1. **Rename.** For a CIK already in the master whose CURRENT PRIMARY row's ticker is no longer in SEC's file for
   that CIK, that row is UPDATED IN PLACE to the file's primary ticker (+ name / exchange / ``is_primary``). The
   id stays, so every fact stays attached, and the old -> new pair is appended to ``security_ticker_history``
   (migration 0066). When the new ticker already sits on ANOTHER row of the same CIK that is EMPTY (no stored
   bar, in no live basket — a placeholder an earlier load inserted: VREOF beside VREOD, MEASURED on dev) the
   company's row is still renamed in place and the placeholder is left as it is, non-primary (never deleted);
   a sibling with history of its own is a real tracked line, so primacy moves to it instead (and a live-basket
   member that re-points is LISTED). An unflagged CIK's "company row" is the one its history is on.
2. **New classes.** Any other ticker new to the CIK (a warrant, a unit, another class) is INSERTED as a
   NON-primary row — it never takes primacy from the renamed row.
3. **Primary flags.** After a run, at most ONE ``is_primary`` per CIK in the file (checked inside the apply's
   transaction — a violation rolls the whole refresh back). The old "a vanished row keeps its last flag" case
   is fixed: a row the file no longer carries is demoted when another row of its CIK holds primacy. Primacy is
   STICKY on a tie: while the company's row is still listed, SEC's rank moves primacy to another line only on
   structural grounds (a common over its unit, a major venue over OTC), never on file order alone (LRHC / SRTM
   listed side by side — the new ticker is inserted non-primary, and renamed in place over once the old drops).
4. **Ticker reuse is a CONFLICT, never applied.** A rename (or a new class / a new primary line) onto a ticker
   another CIK's master row already holds — a ticker reused after a delisting, or a successor issuer — is
   surfaced and left alone: the row keeps its old ticker and flag. A NEW company whose ticker a LIVE-basket row
   of another CIK holds is a conflict too (it may be that member's successor issuer, which the operator's T7
   Adopt moves in place — an inserted row would block that Adopt, ``repositories/cik_change_repo.adopt``); a new
   company whose ticker only a non-basket row holds is inserted (recall, #9) and listed as a SHARED ticker.
5. **A CIK no longer in the file** (a real delisting) is left exactly as it is — never deleted, never re-flagged.

THE PRICE FEED FOLLOWS A RENAME ONLY WHEN SAFE (the price leg reads ``price_symbol or ticker``):

- **no stored bars** -> nothing to protect: the leg prices under the new ticker from the next pass;
- **stored bars, no price symbol** -> PIN ``price_symbol`` to the OLD ticker (the repair log's ``set``, author
  ``system:rename-pin``: visible on Admin, undoable), so the stored tape is never silently switched to a series
  whose overlap could re-version history. The nightly stale-tape machinery takes it from there: once the old
  symbol stops, the rename probe splice-verifies the new SEC ticker and, under the strict rule, ADOPTS it
  automatically (``pipeline/rename_probe.run_auto_adopt``, author ``system:auto-adopt``, undoable);
- **a price symbol already set** (the operator's, or the resolver's) -> never touched.

IDENTITY, never a fact (#1/#3): the master is UPDATE-in-place identity (nothing reads it as-of); no fact table is
written, and no call input changes. The plan is PURE over what it read; ``apply_plan`` writes it in the CALLER's
transaction (the caller commits).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.price_feed_basis import BASIS_RENAME_PIN
from securities import sec_tickers

# The file the refresh reads, as recorded on each ticker-history row and pin
SOURCE = "sec:company_tickers_exchange"

KIND_NEW_COMPANY = "new_company"  # a CIK the master has never held
KIND_NEW_CLASS = "new_class"  # another ticker on a CIK the master holds — inserted non-primary
KIND_NEW_PRIMARY = (
    "new_primary"  # the file's rank moves primacy to a ticker new to the CIK (old one still listed)
)
KIND_RENAME = "rename"


class RefreshConflict(RuntimeError):
    """The master moved between the plan and the apply (a renamed row no longer holds its old ticker), or the
    apply would leave a CIK with two primaries. Nothing is written — the caller rolls the transaction back.
    """


@dataclass(frozen=True)
class MasterRow:
    """One stored master row, as the plan read it."""

    id: UUID
    cik: str | None
    ticker: str
    name: str | None
    exchange: str | None
    is_primary: bool | None
    recorded_at: datetime
    price_symbol: str | None = None
    price_symbol_basis: str | None = None


@dataclass(frozen=True)
class FileRow:
    """One row of SEC's file, with the canonical-primary flag ``sec_tickers.flag_primaries`` gave it."""

    cik: str
    ticker: str
    name: str | None
    exchange: str | None
    is_primary: bool


@dataclass(frozen=True)
class Insert:
    id: UUID
    cik: str
    ticker: str
    name: str | None
    exchange: str | None
    is_primary: bool
    kind: str  # KIND_NEW_COMPANY | KIND_NEW_CLASS | KIND_NEW_PRIMARY

    def to_json(self) -> dict:
        return {
            "cik": self.cik,
            "ticker": self.ticker,
            "name": self.name,
            "exchange": self.exchange,
            "is_primary": self.is_primary,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class Update:
    """An in-place identity write on a row whose ticker stays: a changed name / exchange / primary flag."""

    id: UUID
    cik: str | None
    ticker: str
    name: str | None
    exchange: str | None
    is_primary: bool | None
    prior_name: str | None
    prior_exchange: str | None
    prior_primary: bool | None

    @property
    def flag_changed(self) -> bool:
        return self.is_primary is not self.prior_primary

    def to_json(self) -> dict:
        return {
            "security_id": str(self.id),
            "cik": self.cik,
            "ticker": self.ticker,
            "is_primary": self.is_primary,
            "prior_primary": self.prior_primary,
            "name_changed": self.name != self.prior_name,
            "exchange_changed": self.exchange != self.prior_exchange,
        }


@dataclass(frozen=True)
class Rename:
    """A company's primary row, re-keyed IN PLACE from ``old_ticker`` to the file's ``new_ticker``."""

    id: UUID
    cik: str
    old_ticker: str
    new_ticker: str
    name: str | None
    exchange: str | None
    prior_name: str | None
    prior_exchange: str | None
    prior_primary: bool | None
    stored_bars: int = 0
    price_symbol: str | None = None
    price_symbol_basis: str | None = None
    in_basket: bool = False

    @property
    def pin(self) -> str | None:
        """The OLD ticker the price leg is pinned to (stored bars, no price symbol) — else ``None``."""
        return self.old_ticker if self.stored_bars > 0 and not self.price_symbol else None

    @property
    def price_action(self) -> str:
        """``kept`` (a price symbol is already set — never touched) · ``pin`` (stored bars: pinned to the
        old ticker until the nightly probe splice-verifies the new one) · ``follows`` (never priced: the leg
        prices under the new ticker from the next pass)."""
        if self.price_symbol:
            return "kept"
        return "pin" if self.stored_bars > 0 else "follows"

    def to_json(self) -> dict:
        return {
            "security_id": str(self.id),
            "cik": self.cik,
            "old_ticker": self.old_ticker,
            "new_ticker": self.new_ticker,
            "name": self.name,
            "prior_name": self.prior_name,
            "exchange": self.exchange,
            "stored_bars": self.stored_bars,
            "price_symbol": self.price_symbol,
            "price_action": self.price_action,
            "in_basket": self.in_basket,
        }


@dataclass(frozen=True)
class Holder:
    """A row (or a file listing) already holding a ticker a change wanted."""

    cik: str | None
    security_id: UUID | None  # None = another CIK in SEC's own file lists it
    ticker: str
    name: str | None = None
    in_basket: bool = False

    def to_json(self) -> dict:
        return {
            "cik": self.cik,
            "security_id": str(self.security_id) if self.security_id else None,
            "ticker": self.ticker,
            "name": self.name,
            "in_basket": self.in_basket,
        }


@dataclass(frozen=True)
class Conflict:
    """A change NOT applied because another company holds the ticker (see the module rule 4)."""

    kind: str  # KIND_RENAME | KIND_NEW_CLASS | KIND_NEW_PRIMARY | KIND_NEW_COMPANY
    cik: str
    ticker: str  # the ticker the change wanted
    holders: tuple[Holder, ...]
    security_id: UUID | None = None  # our row the change would have touched (a rename's)
    old_ticker: str | None = None
    name: str | None = None
    in_basket: bool = False

    def describe(self) -> str:
        who = "; ".join(
            f"{h.ticker} on CIK {h.cik or 'none'}"
            + (f" ({h.name})" if h.name else "")
            + (" — SEC's file lists it there too" if h.security_id is None else "")
            + (" [live basket]" if h.in_basket else "")
            for h in self.holders
        )
        if self.kind == KIND_RENAME:
            what = f"rename {self.old_ticker} -> {self.ticker} (CIK {self.cik}) NOT applied"
        elif self.kind == KIND_NEW_COMPANY:
            what = f"new company {self.ticker} (CIK {self.cik}) NOT inserted"
        else:
            what = f"new line {self.ticker} on CIK {self.cik} NOT inserted"
        return f"{what} — the ticker is held by {who}"

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "cik": self.cik,
            "ticker": self.ticker,
            "old_ticker": self.old_ticker,
            "security_id": str(self.security_id) if self.security_id else None,
            "name": self.name,
            "in_basket": self.in_basket,
            "holders": [h.to_json() for h in self.holders],
            "detail": self.describe(),
        }


@dataclass
class MasterPlan:
    """What a refresh WOULD do — pure data, computed from SEC's file and the master as read."""

    tenant_id: UUID
    file_rows: int = 0
    file_ciks: int = 0
    valid_from: date | None = None
    inserts: list[Insert] = field(default_factory=list)
    updates: list[Update] = field(default_factory=list)
    renames: list[Rename] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    shared: list[tuple[Insert, tuple[Holder, ...]]] = field(default_factory=list)
    unchanged: int = 0
    # CIKs OUTSIDE the file (delisted — left as they are) that already carry two primaries: reported, not fixed
    stray_duplicate_primaries: list[str] = field(default_factory=list)
    file_cik_set: frozenset[str] = frozenset()
    # a renamed row's EMPTY same-CIK placeholder rows already holding the new ticker (left as they are, non-primary)
    placeholders: dict[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    # live-basket members whose CIK will resolve to another row after the refresh (they re-point at their next
    # Save) — listed, never silent
    basket_repoints: list[dict] = field(default_factory=list)

    @property
    def new_companies(self) -> int:
        return len({i.cik for i in self.inserts if i.kind == KIND_NEW_COMPANY})

    @property
    def new_classes(self) -> int:
        return sum(1 for i in self.inserts if i.kind != KIND_NEW_COMPANY)

    @property
    def flag_changes(self) -> list[Update]:
        return [u for u in self.updates if u.flag_changed]

    @property
    def identity_updates(self) -> int:
        return sum(
            1 for u in self.updates if (u.name, u.exchange) != (u.prior_name, u.prior_exchange)
        )

    @property
    def pins(self) -> list[Rename]:
        return [r for r in self.renames if r.pin]

    @property
    def empty(self) -> bool:
        return not (self.inserts or self.updates or self.renames)

    def counts(self) -> dict[str, int]:
        return {
            "file_rows": self.file_rows,
            "file_ciks": self.file_ciks,
            "new_companies": self.new_companies,
            "new_company_rows": sum(1 for i in self.inserts if i.kind == KIND_NEW_COMPANY),
            "new_classes": self.new_classes,
            "renames": len(self.renames),
            "renames_in_basket": sum(1 for r in self.renames if r.in_basket),
            "conflicts": len(self.conflicts),
            "primary_flag_changes": len(self.flag_changes)
            + sum(1 for r in self.renames if r.prior_primary is not True),
            "identity_updates": self.identity_updates,
            "pins": len(self.pins),
            "price_follows": sum(1 for r in self.renames if r.price_action == "follows"),
            "price_kept": sum(1 for r in self.renames if r.price_action == "kept"),
            "shared_tickers": len(self.shared),
            "renamed_over_placeholder": len(self.placeholders),
            "basket_repoints": len(self.basket_repoints),
            "unchanged": self.unchanged,
        }

    def to_json(self) -> dict:
        return {
            "counts": self.counts(),
            "renames": [r.to_json() for r in self.renames],
            "conflicts": [c.to_json() for c in self.conflicts],
            "inserts": [i.to_json() for i in self.inserts],
            "flag_changes": [u.to_json() for u in self.flag_changes]
            + [
                {
                    "security_id": str(r.id),
                    "cik": r.cik,
                    "ticker": r.new_ticker,
                    "is_primary": True,
                    "prior_primary": r.prior_primary,
                    "rename": True,
                }
                for r in self.renames
                if r.prior_primary is not True
            ],
            "shared_tickers": [
                {**ins.to_json(), "holders": [h.to_json() for h in hs]} for ins, hs in self.shared
            ],
            "stray_duplicate_primaries": list(self.stray_duplicate_primaries),
            "placeholders": {str(k): [str(x) for x in v] for k, v in self.placeholders.items()},
            "basket_repoints": list(self.basket_repoints),
        }

    def report_lines(self) -> list[str]:
        """The plan in plain English — what the dry run prints (and the apply prints before it writes)."""
        c = self.counts()
        out = [
            f"company list: SEC's file has {c['file_rows']:,} rows / {c['file_ciks']:,} companies",
            f"  {c['new_companies']:,} new companies ({c['new_company_rows']:,} rows) · "
            f"{c['renames']:,} renames ({c['renames_in_basket']} in a live basket) · "
            f"{c['new_classes']:,} new classes · {c['conflicts']} conflicts · "
            f"{c['primary_flag_changes']:,} primary-flag changes · "
            f"{c['identity_updates']:,} name/exchange updates · {c['unchanged']:,} unchanged",
        ]
        if self.renames:
            out.append("  renames (old -> new, the same row — every fact stays attached):")
            for r in sorted(self.renames, key=lambda r: (not r.in_basket, r.old_ticker)):
                price = {
                    "pin": f"price leg PINNED to {r.old_ticker} ({r.stored_bars} stored bars) until the "
                    f"nightly probe splice-verifies {r.new_ticker}",
                    "follows": f"never priced — priced under {r.new_ticker} from the next pass",
                    "kept": f"price symbol {r.price_symbol} kept (never touched)",
                }[r.price_action]
                tag = "[basket] " if r.in_basket else ""
                over = (
                    " · over an EMPTY placeholder row already holding it (left as is, non-primary)"
                    if r.id in self.placeholders
                    else ""
                )
                out.append(
                    f"    {tag}{r.old_ticker} -> {r.new_ticker} ({r.name or r.prior_name or '?'}, "
                    f"CIK {r.cik}) · {price}{over}"
                )
        moves = [i for i in self.inserts if i.kind == KIND_NEW_PRIMARY]
        if moves:
            out.append(
                f"  {len(moves)} CIK(s) where SEC's rank moves primacy to a NEW line (the old line is still "
                "listed, so it is a different instrument — not a rename; a member re-points at its next "
                "Save): "
                + ", ".join(f"{i.ticker} (CIK {i.cik})" for i in moves[:15])
                + (f" … and {len(moves) - 15} more" if len(moves) > 15 else "")
            )
        if self.basket_repoints:
            out.append(
                f"  {len(self.basket_repoints)} live-basket member(s) will RE-POINT to another row of their "
                "company at the next Save (the row the CIK resolves to changes):"
            )
            for p in self.basket_repoints:
                dest = "a NEW row" if p["to_new_row"] else "an existing row"
                out.append(f"    {p['from_ticker']} -> {p['to_ticker']} ({dest}, CIK {p['cik']})")
        if self.conflicts:
            out.append(
                f"  CONFLICTS — {len(self.conflicts)} change(s) NOT applied (another company holds the "
                "ticker; repair by hand):"
            )
            for x in self.conflicts:
                tag = "[basket] " if x.in_basket else ""
                out.append(f"    {tag}{x.describe()}")
        if self.shared:
            out.append(
                f"  {len(self.shared)} new company row(s) share a ticker with an older master row of "
                "another CIK (inserted — the newest row answers a ticker lookup):"
            )
            for ins, hs in self.shared[:20]:
                out.append(
                    f"    {ins.ticker} (CIK {ins.cik}) — also on "
                    + ", ".join(f"CIK {h.cik or 'none'}" for h in hs)
                )
            if len(self.shared) > 20:
                out.append(f"    … and {len(self.shared) - 20} more (in the run artifact)")
        flags = self.flag_changes
        if flags:
            shown = ", ".join(
                f"{u.ticker} {_flag(u.prior_primary)}->{_flag(u.is_primary)}" for u in flags[:12]
            )
            more = f" … and {len(flags) - 12} more" if len(flags) > 12 else ""
            out.append(f"  primary-flag changes on unchanged tickers: {shown}{more}")
        if self.renames:
            out.append(
                f"  price feed: {c['pins']} pinned to the old ticker (auto-adopt candidates for the nightly "
                f"probe) · {c['price_follows']} follow the new ticker (never priced) · "
                f"{c['price_kept']} keep their price symbol"
            )
        if self.stray_duplicate_primaries:
            out.append(
                f"  NOTE — {len(self.stray_duplicate_primaries)} CIK(s) outside SEC's file already carry two "
                "primary rows (left as they are): " + ", ".join(self.stray_duplicate_primaries[:10])
            )
        return out

    def admin_line(self, when: date) -> str:
        """The one Admin / cron-log line: ``Company list · refreshed Oct 25 · 298 new · 23 renames · 0
        conflicts``."""
        c = self.counts()
        return (
            f"Company list · refreshed {when:%b} {when.day} · {c['new_companies']} new · "
            f"{c['renames']} renames · {c['conflicts']} conflicts"
        )


def _flag(v: bool | None) -> str:
    return {True: "primary", False: "secondary", None: "unflagged"}[v]


def _canon_key(r: MasterRow) -> tuple:
    """The ``canonicalize_ids`` / ``ids_for_ciks`` order: ``is_primary DESC NULLS LAST, recorded_at DESC, id``."""
    flag = 0 if r.is_primary is True else (1 if r.is_primary is False else 2)
    return (flag, -r.recorded_at.timestamp(), r.id.int)


def current_primary(
    rows: list[MasterRow], tracked: frozenset[UUID] | set[UUID] = frozenset()
) -> MasterRow | None:
    """The company's row TODAY: the flagged primary (``canonicalize_ids``' own order when several are flagged),
    else — an unflagged CIK (a seeded / resolve()-era row set) — the row the company's history is on (``tracked``:
    a live basket member or a row with stored bars), else the canonical RANK over its stored rows
    (``sec_tickers.flag_primaries``: the common over a warrant), so a refresh never renames a derivative line in
    place."""
    if not rows:
        return None
    flagged = [r for r in rows if r.is_primary is True]
    if flagged:
        return min(flagged, key=_canon_key)
    held = [r for r in rows if r.id in tracked]
    if held:
        rows = held
    ordered = sorted(rows, key=lambda r: (r.recorded_at, r.id.int))
    ranked = sec_tickers.flag_primaries(
        [(r.cik or "", r.ticker, r.name, r.exchange) for r in ordered]
    )
    for r, (_c, _t, _n, _e, primary) in zip(ordered, ranked):
        if primary:
            return r
    return ordered[0]


def _sticky(cur: MasterRow, primary: FileRow, frows: list[FileRow]) -> bool:
    """Does the company's still-listed row ``cur`` keep primacy over SEC's rank pick ``primary``? Yes unless the
    pick is STRUCTURALLY better (``sec_tickers.canonical_sort_key``'s class / exchange / F-ordinary parts — a
    common over its unit, Nasdaq over OTC); a pick that wins only on SEC file order never moves primacy.
    """
    siblings = [f.ticker for f in frows]
    mine = next((f for f in frows if f.ticker == cur.ticker), None)
    if mine is None:
        return False

    def structural(f: FileRow) -> tuple[int, int, int]:
        others = [t for t in siblings if t != f.ticker]
        return sec_tickers.canonical_sort_key(0, f.ticker, f.exchange, others)[:3]

    return structural(mine) <= structural(primary)


def _read_master(conn: psycopg.Connection, tenant_id: UUID) -> list[MasterRow]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, cik, ticker, name, exchange, is_primary, recorded_at, price_symbol, "
            "price_symbol_basis FROM security_master WHERE tenant_id = %s AND ticker IS NOT NULL",
            (tenant_id,),
        )
        return [
            MasterRow(
                id=r["id"],
                cik=r["cik"],
                ticker=str(r["ticker"]).upper(),
                name=r["name"],
                exchange=r["exchange"],
                is_primary=r["is_primary"],
                recorded_at=r["recorded_at"],
                price_symbol=r["price_symbol"],
                price_symbol_basis=r["price_symbol_basis"],
            )
            for r in cur.fetchall()
        ]


def _live_basket_ids(conn: psycopg.Connection, tenant_id: UUID) -> set[UUID]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT bm.security_id FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            "WHERE bm.tenant_id = %s AND bm.security_id IS NOT NULL AND t.archived_at IS NULL",
            (tenant_id,),
        )
        return {r["security_id"] for r in cur.fetchall()}


def _priced_ids(conn: psycopg.Connection, tenant_id: UUID) -> set[UUID]:
    """Every security of this tenant with at least one stored price bar (one indexed scan)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT security_id FROM fact_price_eod WHERE tenant_id = %s", (tenant_id,)
        )
        return {r["security_id"] for r in cur.fetchall()}


def _stored_bars(conn: psycopg.Connection, ids: list[UUID]) -> dict[UUID, int]:
    """Stored price-bar dates per security (only the few renamed rows are asked)."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, count(DISTINCT d) AS n FROM fact_price_eod "
            "WHERE security_id = ANY(%s) GROUP BY security_id",
            (ids,),
        )
        return {r["security_id"]: int(r["n"]) for r in cur.fetchall()}


def duplicate_primaries(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    ciks: Iterable[str] | None = None,
) -> list[str]:
    """The CIKs carrying MORE THAN ONE ``is_primary`` row in this tenant (optionally only among ``ciks``) — the
    invariant the refresh holds (at most one primary per CIK). Read-only; sorted."""
    sql = (
        "SELECT cik FROM security_master WHERE tenant_id = %s AND cik IS NOT NULL AND is_primary "
        "{extra}GROUP BY cik HAVING count(*) > 1 ORDER BY cik"
    )
    args: list[Any] = [tenant_id]
    extra = ""
    if ciks is not None:
        extra = "AND cik = ANY(%s) "
        args.append(list(ciks))
    with conn.cursor() as cur:
        cur.execute(sql.format(extra=extra), args)
        return [r["cik"] for r in cur.fetchall()]


def plan_refresh(
    conn: psycopg.Connection,
    rows: Iterable[tuple[str, str, str | None, str | None]],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    effective_date: date | None = None,
) -> MasterPlan:
    """Plan the refresh of THIS tenant's master from SEC's ``(cik, ticker, name, exchange)`` rows (file order).
    READ-ONLY: it reads the master, the live baskets and the renamed rows' stored-bar counts, and writes nothing.
    The rules are the module docstring's."""
    # De-dup defensively (the SEC file shouldn't repeat a (cik, ticker)) BEFORE the primary rank, so a duplicate
    # row can't split a CIK's rank group; then flag exactly one primary per CIK (file order kept).
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str, str | None, str | None]] = []
    for cik, ticker, name, exchange in rows:
        if not cik or not ticker:
            continue  # exact mappings only (INVARIANT #2)
        key = (cik, str(ticker).upper())
        if key in seen:
            continue
        seen.add(key)
        deduped.append((cik, str(ticker).upper(), name, exchange))
    file_rows = [FileRow(c, t, n, e, p) for c, t, n, e, p in sec_tickers.flag_primaries(deduped)]

    by_cik_file: dict[str, list[FileRow]] = {}
    file_ciks_by_ticker: dict[str, set[str]] = {}
    for f in file_rows:
        by_cik_file.setdefault(f.cik, []).append(f)
        file_ciks_by_ticker.setdefault(f.ticker, set()).add(f.cik)

    stored = _read_master(conn, tenant_id)
    basket = _live_basket_ids(conn, tenant_id)
    tracked = basket | _priced_ids(conn, tenant_id)  # the rows a company's history is on
    by_cik_master: dict[str, list[MasterRow]] = {}
    rows_by_ticker: dict[str, list[MasterRow]] = {}
    for r in stored:
        rows_by_ticker.setdefault(r.ticker, []).append(r)
        if r.cik:
            by_cik_master.setdefault(r.cik, []).append(r)

    plan = MasterPlan(
        tenant_id=tenant_id,
        file_rows=len(file_rows),
        file_ciks=len(by_cik_file),
        valid_from=effective_date or market_today(),
        file_cik_set=frozenset(by_cik_file),
    )

    def holders(ticker: str, cik: str) -> tuple[Holder, ...]:
        """Who ELSE holds ``ticker``: a master row of another (or no) CIK, or another CIK in SEC's file."""
        out = [
            Holder(r.cik, r.id, r.ticker, r.name, r.id in basket)
            for r in rows_by_ticker.get(ticker, ())
            if r.cik != cik
        ]
        known = {h.cik for h in out}
        out += [
            Holder(c, None, ticker)
            for c in sorted(file_ciks_by_ticker.get(ticker, set()) - {cik})
            if c not in known
        ]
        return tuple(out)

    pending_renames: list[tuple[MasterRow, FileRow]] = []
    for cik, frows in by_cik_file.items():
        primary = next(f for f in frows if f.is_primary)
        mrows = by_cik_master.get(cik, [])
        if not mrows:
            _plan_new_company(plan, cik, frows, primary, holders)
            continue
        file_by_ticker = {f.ticker: f for f in frows}
        mine: dict[str, list[MasterRow]] = {}
        for r in mrows:
            mine.setdefault(r.ticker, []).append(r)
        cur = current_primary(mrows, tracked)
        target: MasterRow | Insert | None = None
        renamed: MasterRow | None = None
        vanished = cur is not None and cur.ticker not in file_by_ticker
        # the file's primary ticker already sits on ANOTHER row of this CIK — but an EMPTY one (no stored bar, in
        # no live basket: a placeholder an earlier load inserted, e.g. VREOF beside VREOD). The company's row is
        # renamed in place over it, exactly like a new ticker; the placeholder stays as it is, non-primary (never
        # deleted). A sibling with history of its own is a real tracked line, so primacy moves to it instead.
        placeholder = (
            vanished
            and primary.ticker in mine
            and not any(s.id in tracked for s in mine[primary.ticker])
        )
        if vanished and (primary.ticker not in mine or placeholder):
            held = holders(primary.ticker, cik)
            if held:
                plan.conflicts.append(
                    Conflict(
                        kind=KIND_RENAME,
                        cik=cik,
                        ticker=primary.ticker,
                        holders=held,
                        security_id=cur.id,
                        old_ticker=cur.ticker,
                        name=primary.name or cur.name,
                        in_basket=cur.id in basket,
                    )
                )
            else:
                pending_renames.append((cur, primary))
                renamed = cur
                if placeholder:
                    plan.placeholders[cur.id] = tuple(s.id for s in mine[primary.ticker])
            target = cur  # renamed or conflict-held, the company's row keeps primacy
        elif cur is not None and cur.ticker != primary.ticker and _sticky(cur, primary, frows):
            # PRIMACY IS STICKY on a tie: the company's row is still listed and SEC's rank prefers the other line
            # only by FILE ORDER (same class, venue and F-ordinary standing — MEASURED on dev 2026-10-24: LRHC /
            # SRTM and WELPM / WELPP, both pairs listed side by side). Moving it would re-point the member to
            # another (often empty) row on nothing but the order SEC wrote its file in; a new ticker is then
            # inserted non-primary below, and renamed in place over once the old one drops off the file.
            target = cur
            primary_new = primary.ticker not in mine
            if primary_new:
                held = holders(primary.ticker, cik)
                if held:
                    plan.conflicts.append(
                        Conflict(
                            kind=KIND_NEW_CLASS,
                            cik=cik,
                            ticker=primary.ticker,
                            holders=held,
                            name=primary.name,
                            in_basket=any(r.id in basket for r in mrows),
                        )
                    )
                else:
                    plan.inserts.append(
                        Insert(
                            uuid4(),
                            cik,
                            primary.ticker,
                            primary.name,
                            primary.exchange,
                            False,
                            KIND_NEW_CLASS,
                        )
                    )
        elif primary.ticker in mine:
            target = min(mine[primary.ticker], key=_canon_key)
        else:
            # the file's rank moves primacy to a ticker NEW to this CIK while the old primary is still listed
            held = holders(primary.ticker, cik)
            if held:
                plan.conflicts.append(
                    Conflict(
                        kind=KIND_NEW_PRIMARY,
                        cik=cik,
                        ticker=primary.ticker,
                        holders=held,
                        name=primary.name,
                        in_basket=any(r.id in basket for r in mrows),
                    )
                )
                target = cur
            else:
                ins = Insert(
                    uuid4(),
                    cik,
                    primary.ticker,
                    primary.name,
                    primary.exchange,
                    True,
                    KIND_NEW_PRIMARY,
                )
                plan.inserts.append(ins)
                target = ins
        # the other tickers new to this CIK: inserted NON-primary (never taking primacy from the company's row)
        for f in frows:
            if f is primary or f.ticker in mine:
                continue
            held = holders(f.ticker, cik)
            if held:
                plan.conflicts.append(
                    Conflict(
                        kind=KIND_NEW_CLASS,
                        cik=cik,
                        ticker=f.ticker,
                        holders=held,
                        name=f.name,
                        in_basket=any(r.id in basket for r in mrows),
                    )
                )
                continue
            plan.inserts.append(
                Insert(uuid4(), cik, f.ticker, f.name, f.exchange, False, KIND_NEW_CLASS)
            )
        # a LIVE-basket member on the row the CIK resolves to TODAY that will resolve to ANOTHER row after the
        # refresh re-points at its next Save (canonicalize) — listed, so the move is never silent
        before = min(mrows, key=_canon_key)
        if target is not None and target is not before and before.id in basket:
            plan.basket_repoints.append(
                {
                    "cik": cik,
                    "security_id": str(before.id),
                    "from_ticker": before.ticker,
                    "to_ticker": target.ticker if renamed is None else primary.ticker,
                    "to_new_row": isinstance(target, Insert),
                }
            )
        # identity + flags on the CIK's existing rows: exactly the target ends primary
        for r in mrows:
            if renamed is not None and r.id == renamed.id:
                continue  # the Rename carries its own write
            want_primary = target is r
            f = file_by_ticker.get(r.ticker)
            if f is not None:
                name, exchange = f.name, f.exchange
            else:
                # a row SEC no longer lists: left exactly as it is, unless it is the company's row (kept
                # primary — a conflict-held rename) or it still holds primacy beside the actual primary —
                # THE VANISHED-ROW FIX (the old "keeps its last flag" is how two primaries arose)
                name, exchange = r.name, r.exchange
                if not want_primary and r.is_primary is not True:
                    continue
            if (name, exchange, want_primary) != (r.name, r.exchange, r.is_primary):
                plan.updates.append(
                    Update(
                        r.id,
                        r.cik,
                        r.ticker,
                        name,
                        exchange,
                        want_primary,
                        r.name,
                        r.exchange,
                        r.is_primary,
                    )
                )
            elif f is not None:
                plan.unchanged += 1

    bars = _stored_bars(conn, [r.id for r, _ in pending_renames])
    for r, f in pending_renames:
        plan.renames.append(
            Rename(
                id=r.id,
                cik=f.cik,
                old_ticker=r.ticker,
                new_ticker=f.ticker,
                name=f.name,
                exchange=f.exchange,
                prior_name=r.name,
                prior_exchange=r.exchange,
                prior_primary=r.is_primary,
                stored_bars=bars.get(r.id, 0),
                price_symbol=r.price_symbol,
                price_symbol_basis=r.price_symbol_basis,
                in_basket=r.id in basket,
            )
        )
    plan.stray_duplicate_primaries = [
        c
        for c, rs in sorted(by_cik_master.items())
        if c not in by_cik_file and sum(1 for r in rs if r.is_primary is True) > 1
    ]
    return plan


def _plan_new_company(plan: MasterPlan, cik: str, frows, primary: FileRow, holders) -> None:
    """A CIK the master has never held: insert its rows (the file's flags), unless a LIVE-basket row of another
    CIK holds its primary ticker (a possible successor issuer of that member — the operator's T7 Adopt moves the
    member in place, and an inserted row would block it) — then the whole company is one conflict. A ticker only
    a non-basket row holds is inserted (recall, #9) and listed as SHARED."""
    held = holders(primary.ticker, cik)
    if any(h.in_basket for h in held):
        plan.conflicts.append(
            Conflict(
                kind=KIND_NEW_COMPANY,
                cik=cik,
                ticker=primary.ticker,
                holders=held,
                name=primary.name,
                in_basket=True,
            )
        )
        return
    for f in frows:
        ins = Insert(uuid4(), cik, f.ticker, f.name, f.exchange, f.is_primary, KIND_NEW_COMPANY)
        plan.inserts.append(ins)
        shared = holders(f.ticker, cik)
        if any(h.security_id is not None for h in shared):
            plan.shared.append((ins, tuple(h for h in shared if h.security_id is not None)))


def apply_plan(
    conn: psycopg.Connection, plan: MasterPlan, *, source: str = SOURCE
) -> dict[str, int]:
    """Write ``plan`` in the CALLER's transaction (the caller commits; on any exception it must roll back):
    the renames IN PLACE (+ their ``security_ticker_history`` rows), the identity / flag updates, the inserts,
    then each rename's price PIN through the repair log (``price_feed_repo.apply_change``, action ``set``, author
    ``system:rename-pin``). Then the invariant — at most one ``is_primary`` per CIK in the file — is checked
    inside the same transaction; a violation raises ``RefreshConflict``. A rename whose row no longer holds its
    old ticker (the master moved since the plan) raises ``RefreshConflict`` too. Returns the written counts.
    """
    from repositories import price_feed_repo  # lazy: the repo imports securities.master

    tenant_id = plan.tenant_id
    pinned = 0
    with conn.cursor() as cur:
        for r in plan.renames:
            cur.execute(
                "UPDATE security_master SET ticker = %s, name = %s, exchange = %s, is_primary = TRUE, "
                "recorded_at = now() WHERE tenant_id = %s AND id = %s AND ticker = %s",
                (r.new_ticker, r.name, r.exchange, tenant_id, r.id, r.old_ticker),
            )
            if cur.rowcount != 1:
                raise RefreshConflict(
                    f"{r.old_ticker} (security {r.id}) no longer holds that ticker — the master moved since "
                    "the plan; nothing written, re-run the refresh"
                )
            cur.execute(
                "INSERT INTO security_ticker_history "
                "(tenant_id, security_id, old_ticker, new_ticker, source) VALUES (%s, %s, %s, %s, %s)",
                (tenant_id, r.id, r.old_ticker, r.new_ticker, source),
            )
        if plan.updates:
            cur.executemany(
                "UPDATE security_master SET name = %s, exchange = %s, is_primary = %s, "
                "recorded_at = now() WHERE tenant_id = %s AND id = %s",
                [(u.name, u.exchange, u.is_primary, tenant_id, u.id) for u in plan.updates],
            )
        if plan.inserts:
            cur.executemany(
                "INSERT INTO security_master "
                "(id, tenant_id, cik, ticker, name, exchange, is_primary, valid_from) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        i.id,
                        tenant_id,
                        i.cik,
                        i.ticker,
                        i.name,
                        i.exchange,
                        i.is_primary,
                        plan.valid_from or market_today(),
                    )
                    for i in plan.inserts
                ],
            )
    for r in plan.pins:
        price_feed_repo.apply_change(
            conn,
            r.id,
            action="set",
            symbol=r.old_ticker,
            basis=BASIS_RENAME_PIN,
            detail={
                "rename": {"old_ticker": r.old_ticker, "new_ticker": r.new_ticker},
                "stored_bars": r.stored_bars,
                "source": source,
                "why": (
                    "the company list renamed this row in place; its stored tape stays on the old "
                    "symbol until the nightly rename probe splice-verifies the new one"
                ),
            },
        )
        pinned += 1
    dupes = duplicate_primaries(conn, tenant_id=tenant_id, ciks=plan.file_cik_set)
    if dupes:
        raise RefreshConflict(
            f"the refresh would leave {len(dupes)} CIK(s) with two primary rows "
            f"({', '.join(dupes[:5])}) — nothing written"
        )
    return {
        "inserted": len(plan.inserts),
        "updated": len(plan.updates),
        "renamed": len(plan.renames),
        "pinned": pinned,
    }


def former_tickers(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[UUID, tuple[str, ...]]:
    """Each security's FORMER tickers (``security_ticker_history.old_ticker``), oldest first. Read-only."""
    ids = list({i for i in security_ids if i is not None})
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, old_ticker FROM security_ticker_history "
            "WHERE tenant_id = %s AND security_id = ANY(%s) ORDER BY seen_at, id",
            (tenant_id, ids),
        )
        out: dict[UUID, list[str]] = {}
        for r in cur.fetchall():
            out.setdefault(r["security_id"], []).append(r["old_ticker"])
    return {k: tuple(dict.fromkeys(v)) for k, v in out.items()}


def legacy_counts(written: Mapping[str, int], plan: MasterPlan) -> dict[str, int]:
    """The broadener's original return shape (``{"inserted", "updated", "skipped"}``) — a rename counts as an
    update of its row."""
    return {
        "inserted": written["inserted"],
        "updated": written["updated"] + written["renamed"],
        "skipped": plan.unchanged,
    }
