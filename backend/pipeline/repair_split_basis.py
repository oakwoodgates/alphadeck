"""``python -m pipeline.repair_split_basis`` — S2: put the STORED price history of a split company back on ONE
scale, the split list's (split plan S2; the split-tape diagnosis section 7).

WHAT IT REPAIRS. Yahoo served some split histories on two scales and the price leg mirrored them bar for bar:
14 companies carry stored bars on the unadjusted basis next to adjusted ones (cliffs, islands, a stale tail),
PBM carries bars adjusted twice, ATRO bars adjusted by the wrong factor. The vendor still serves those very
values (MEASURED: 0 differing bars across two pulls), and two have lost their Yahoo history, so a re-pull
cannot fix them: the repair rescales the STORED bars (recipe B) — prices x the missing factor, volume / it.

HOW (``pipeline.split_repair``, pure): every stored bar dated before a split in force is labeled RAW,
ADJUSTED, OVER-ADJUSTED or MIS-FACTORED against the list's cumulative factor, by continuity with the bars
after the split (islands found first, scale changes allowed only where a vendor makes them); the last two only
from independent raw prints (``--spot-file``). Any bar that cannot be labeled refuses the WHOLE company — no
partial repair. The splits used are only those IN FORCE on the list (confirmed, or announced with the first
session arrived); a company whose split still needs review is shown "waiting for the split to be confirmed",
never silently skipped.

THE GATES, before ``--apply`` may write a company: the T4 monitor (``pipeline.split_basis.tape_findings``)
clean on the repaired window, no step the size of a split left at a first session or an edge the repair drew,
and every raw print within 1% or one tick of the repaired close (within 3% is reported as vendor noise).

DRY RUN (the default) — a READ ONLY connection, no lock, no write of any kind. Per company: the splits (in
force and waiting), the runs, before / after samples (the first and last changed bar and every island), T4
before / after, the Cockpit columns before / after (the shipped display ``compute`` functions), the T5 holds
on file, and what tonight's pull would do — the vendor's CACHED series (never fetched) run through the price
leg's own rules (``split_normalize`` -> ``plan_overlap`` -> T5) with the list and without it.

``--apply`` (needs ``--ticker``; every named company must pass, or nothing is written):

- holds the REPAIR lock (``pipeline.run_lock.acquire_repair_lock``: the call-of-record lock, then W1's
  fact-writer gate exclusively — no pass and no company writer runs beside it; exit 75 when either is held),
  ONE transaction;
- appends the corrected versions through the price writer (``eod_loader.ingest_prices``) stamped with ONE
  database instant — never an UPDATE, never a DELETE on the fact table. A HISTORY view reads them from the
  bar's settle on (a split re-scale is not new market information — ``db.public_clock``) and puts them on its
  own day's scale (``domain.splits.on_view_scale``), so a view before a split reads the pre-split scale;
- clears each repaired company's T5 hold through ``ingest_security.save_hold`` (operational state);
- counts the stamped rows against the plan before committing (a mismatch rolls back);
- writes a provenance JSON under ``data/repairs/`` (``/data/repairs`` in the container): per bar old -> new,
  the factor, the split (accession), the code sha, the stamp. Idempotent: a re-run plans 0.

``--revoke STAMP`` appends the prior values back as new versions (from that stamp's provenance file, refused
unless every bar's newest version is still the repair's), under the same lock and count check. Nothing is
deleted.

Run ``--apply`` on prod only after a labeled backup and a clean nightly, from the deployed image, outside
22:20-22:50 ET.

    python -m pipeline.repair_split_basis                                    # dry run, every listed company
    python -m pipeline.repair_split_basis --ticker BTOC,OPTT --spot-file pipeline/split_evidence/polygon_raw_2026-10.json
    python -m pipeline.repair_split_basis --ticker BTOC,OPTT --spot-file ... --apply
    python -m pipeline.repair_split_basis --revoke 2026-10-20T15:04:05.123456+00:00
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.clock import db_now
from db.session import DEFAULT_TENANT_ID, connect
from domain.market_time import market_today
from domain.settings import get_settings
from domain.splits import ListedSplit, applicable, listed_from_row, pending
from ingest import CacheMiss
from ingest.prices.eod_loader import ingest_prices
from ingest.prices.ingest_security import load_hold, plan_overlap, save_hold
from ingest.prices.restatement import classify as t5_classify
from ingest.prices.restatement import decide as t5_decide
from ingest.prices.source import YahooPriceSource
from ingest.prices.split_normalize import normalize
from pipeline import split_repair as sr
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_repair_lock
from repositories import split_repo
from signals.display import range52w, rvol, sma, trailing_returns

_log = logging.getLogger("alphadeck.repair_split_basis")

RULE = "split-basis-repair"
KIND = "repair_split_basis"
KIND_REVOKE = "repair_split_basis_revoke"
EXIT_REFUSED = 2
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "repairs"
_FIELDS = ("open", "high", "low", "close", "volume")

# company statuses (the dry-run's verdict per company)
REPAIR = "repair"  # labeled, gates pass, N bars to rescale
CLEAN = "clean"  # every stored bar already on the list's scale
REFUSED = "refused"  # a bar unlabeled, a gate failed, or no anchor: nothing written for the company
WAITING = (
    "waiting"  # only splits that still need review (or are announced ahead): nothing in force yet
)
NOTHING = "nothing"  # no stored bars before any split in force


@dataclass
class Change:
    """One bar the repair rescales: the stored latest version (Decimals) and the corrected values."""

    d: date
    label: str
    factor: str  # x16 / x4/25 / x1.04417
    old: dict[str, Any]
    new: dict[str, Any]
    old_recorded_at: datetime
    island: float | None = None


@dataclass
class CompanyPlan:
    security_id: UUID
    ticker: str | None
    price_symbol: str | None
    name: str | None
    in_force: list[ListedSplit]
    waiting: list[ListedSplit]
    status: str = NOTHING
    classification: sr.Classification | None = None
    gates: sr.GateResult | None = None
    changes: list[Change] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    display_before: dict[str, Any] = field(default_factory=dict)
    display_after: dict[str, Any] = field(default_factory=dict)
    forecast: dict[str, Any] = field(default_factory=dict)
    holds: int = 0
    spot_given: int = 0
    in_window: int = 0

    @property
    def label(self) -> str:
        return self.ticker or str(self.security_id)


# --- reads ---------------------------------------------------------------------------------------------------


def stored_tape(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID
) -> list[dict[str, Any]]:
    """The latest stored version of every bar (the as-of read's pick, ``recorded_at DESC, id DESC``), with
    the version's stamp and how many versions the date holds. Numerics stay Decimal."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (d) d, open, high, low, close, volume, recorded_at, "
            "count(*) OVER (PARTITION BY d) AS versions FROM fact_price_eod "
            "WHERE tenant_id = %s AND security_id = %s ORDER BY d, recorded_at DESC, id DESC",
            (tenant_id, security_id),
        )
        return [dict(r) for r in cur.fetchall()]


def _master_rows(conn: psycopg.Connection, ids: Iterable[UUID], *, tenant_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker, price_symbol, name FROM security_master "
            "WHERE tenant_id = %s AND id = ANY(%s)",
            (tenant_id, list(ids)),
        )
        return {r["id"]: r for r in cur.fetchall()}


def load_spots(path: Path | None) -> dict[str, dict[date, float]]:
    """``{ticker: {date: raw close}}`` from a spot file (``{"prints": {ticker: {date: {"close": ...}}}}``, or
    the same without the ``prints`` wrapper, or with bare numbers)."""
    if path is None:
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))
    prints = blob.get("prints", blob)
    out: dict[str, dict[date, float]] = {}
    for tk, rows in prints.items():
        if tk.startswith("_"):
            continue
        out[tk.upper()] = {
            date.fromisoformat(d): float(v["close"] if isinstance(v, Mapping) else v)
            for d, v in rows.items()
        }
    return out


def _bar(r: Mapping) -> sr.Bar:
    def f(x: Any) -> float | None:
        return None if x is None else float(x)

    return sr.Bar(r["d"], f(r["open"]), f(r["high"]), f(r["low"]), f(r["close"]), f(r["volume"]))


def _display(bars: Sequence[sr.Bar], *, asof: date) -> dict[str, Any]:
    """The Cockpit's columns from the shipped pure ``compute`` functions (trailing returns, RVOL, SMA, 52w)."""
    rows = [
        {
            "d": b.d,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
        if b.d <= asof and b.close
    ]
    out: dict[str, Any] = {}
    for mod in (trailing_returns, rvol, sma, range52w):
        sig = mod.compute(
            [r for r in rows if r["d"] > asof - timedelta(days=mod.LOOKBACK_DAYS)], asof
        )
        if sig is None:
            continue
        for m in sig.metrics:
            out[m.key] = None if m.value is None else round(m.value, 4)
        if mod is sma and sig.headline is not None:
            out["sma_headline"] = sig.headline.label
    return out


def _forecast(
    sec: Mapping,
    before: Sequence[Mapping],
    after: Sequence[sr.Bar],
    in_force: Sequence[ListedSplit],
    *,
    cache_dir: Path | None,
    today: date,
) -> dict[str, Any]:
    """What tonight's pull would do: the vendor's CACHED series (``allow_live=False`` — nothing fetched,
    nothing written) through the price leg's own rules, against the REPAIRED store, with the list and
    without it."""
    symbol = sec["price_symbol"] or sec["ticker"]
    if not symbol:
        return {"note": "no ticker: the price leg pulls nothing"}
    try:
        fresh = YahooPriceSource(cache_dir=cache_dir).get_bars(symbol, allow_live=False)
    except CacheMiss:
        return {"note": f"no cached vendor series for {symbol} (nothing fetched)"}
    except Exception as e:  # noqa: BLE001 — a forecast fault is reported, never a failed dry run
        return {"note": f"cached series unreadable: {type(e).__name__}: {e}"}
    if not fresh:
        return {"note": f"the cached series for {symbol} is empty"}
    old_by_d = {r["d"]: r for r in before}
    stored = {b.d: {"close": b.close, "volume": b.volume} for b in after}
    last = max(stored)
    out: dict[str, Any] = {
        "symbol": symbol,
        "cached_bars": len(fresh),
        "cached_through": str(fresh[-1]["d"]),
    }
    for use in (True, False):
        bars = normalize(fresh, stored, in_force).bars if use else list(fresh)
        plan = plan_overlap(bars, stored, last=last)
        # the superseded versions after a repair: the version the repair replaced (any older ones only make a
        # REVERT likelier — the count here is the lower bound the classification needs)
        superseded = {
            r["d"]: [
                {"close": _f(old_by_d[r["d"]]["close"]), "volume": _f(old_by_d[r["d"]]["volume"])}
            ]
            for r in plan.old
            if r["d"] in old_by_d
        }
        kind, reverts = t5_classify(plan.old, superseded)
        n1 = t5_decide(
            kind,
            old=plan.old,
            pending=None,
            covered={r["d"] for r in bars},
            pull_date=today,
            refreshed=True,
        )
        n2 = (
            t5_decide(
                kind,
                old=plan.old,
                pending=n1.state,
                covered={r["d"] for r in bars},
                pull_date=today + timedelta(days=1),
                refreshed=True,
            )
            if n1.state is not None
            else None
        )
        key = "with_list" if use else "without_list"
        out[key] = {
            "recent_restated": len(plan.recent),
            "older_restated": len(plan.old),
            "holes": len(plan.holes),
            "t5": kind,
            "night1": "apply" if n1.apply else f"hold {n1.held}",
            "night2": None if n2 is None else ("apply" if n2.apply else f"hold {n2.held}"),
        }
        if use:
            out[key]["normalized"] = normalize(fresh, stored, in_force).scaled
    return out


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def _holds(conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID) -> int:
    return 0 if load_hold(conn, security_id, tenant_id=tenant_id) is None else 1


# --- the plan --------------------------------------------------------------------------------------------------


def plan_company(
    conn: psycopg.Connection,
    sec: Mapping,
    splits: Sequence[ListedSplit],
    *,
    tenant_id: UUID,
    today: date,
    spot: Mapping[date, float] | None,
    cache_dir: Path | None = None,
) -> CompanyPlan:
    """The dry-run verdict for one company (reads only)."""
    in_force = applicable(splits, on=today)
    waiting = pending(splits, on=today)
    p = CompanyPlan(
        security_id=sec["id"],
        ticker=sec["ticker"],
        price_symbol=sec["price_symbol"],
        name=sec["name"],
        in_force=in_force,
        waiting=waiting,
        spot_given=len(spot or {}),
    )
    p.holds = _holds(conn, sec["id"], tenant_id=tenant_id)
    if not in_force:
        p.status = WAITING if waiting else NOTHING
        return p
    rows = stored_tape(conn, sec["id"], tenant_id=tenant_id)
    bars = [_bar(r) for r in rows]
    c = sr.classify(bars, in_force, today=today, transparent=waiting, spot=spot)
    p.classification = c
    if not c.labels and not c.refusals:
        p.status = NOTHING
        return p
    after = sr.apply_labels(bars, c.labels)
    p.display_before = _display(bars, asof=today)
    p.display_after = _display(after, asof=today)
    if c.refusals:
        p.status = REFUSED
        p.refusals = list(c.refusals)
        return p
    p.gates = sr.plan_gates(bars, after, in_force, asof=today, spot=spot)
    by_d = {r["d"]: r for r in rows}
    for lab in c.to_change:
        r = by_d[lab.d]
        new = sr.repaired_bar(r, sr.correction(lab))
        p.changes.append(
            Change(
                d=lab.d,
                label=lab.label,
                factor=sr.correction_label(lab),
                old={k: r[k] for k in _FIELDS},
                new={k: new[k] for k in _FIELDS},
                old_recorded_at=r["recorded_at"],
                island=lab.island,
            )
        )
    since = today - timedelta(days=sr.window_days())
    p.in_window = sum(1 for ch in p.changes if ch.d >= since)
    if not p.gates.ok:
        p.status = REFUSED
        p.refusals = p.gates.reasons()
    else:
        p.status = REPAIR if p.changes else CLEAN
    p.forecast = _forecast(sec, rows, after, in_force, cache_dir=cache_dir, today=today)
    return p


def plan_all(
    conn: psycopg.Connection,
    *,
    tickers: Sequence[str] | None = None,
    today: date | None = None,
    spots: Mapping[str, Mapping[date, float]] | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    cache_dir: Path | None = None,
) -> list[CompanyPlan]:
    """Every listed company (or the named ``tickers``, matched on ticker or price symbol), planned."""
    today = today or market_today()
    versions = split_repo.latest_versions(conn, tenant_id=tenant_id)
    by_sid: dict[UUID, list[ListedSplit]] = {}
    for v in versions:
        by_sid.setdefault(v.security_id, []).append(listed_from_row(v.model_dump()))
    master = _master_rows(conn, by_sid, tenant_id=tenant_id)
    want = {t.strip().upper() for t in tickers} if tickers else None
    plans = []
    for sid in sorted(by_sid, key=lambda s: ((master.get(s) or {}).get("ticker") or "", str(s))):
        sec = master.get(sid)
        if sec is None:
            continue
        names = {(sec["ticker"] or "").upper(), (sec["price_symbol"] or "").upper()}
        if want is not None and not (names & want):
            continue
        spot = None
        for nm in names:
            if spots and nm in spots:
                spot = spots[nm]
        plans.append(
            plan_company(
                conn,
                sec,
                by_sid[sid],
                tenant_id=tenant_id,
                today=today,
                spot=spot,
                cache_dir=cache_dir,
            )
        )
    if want is not None:
        found = {
            n for p in plans for n in ((p.ticker or "").upper(), (p.price_symbol or "").upper())
        }
        for t in sorted(want - found):
            plans.append(
                CompanyPlan(
                    security_id=UUID(int=0),
                    ticker=t,
                    price_symbol=None,
                    name=None,
                    in_force=[],
                    waiting=[],
                    status=NOTHING,
                    refusals=["no split on the list for this ticker"],
                )
            )
    return plans


# --- apply / revoke ----------------------------------------------------------------------------------------


@dataclass
class ApplyResult:
    recorded_at: datetime | None = None
    appended: int = 0
    holds_cleared: int = 0
    companies: list[str] = field(default_factory=list)


def _count_stamped(
    conn: psycopg.Connection, ids: Iterable[UUID], stamp: datetime, *, tenant_id: UUID
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE tenant_id = %s AND security_id = ANY(%s) "
            "AND recorded_at = %s",
            (tenant_id, list(ids), stamp),
        )
        return int(cur.fetchone()["n"])


def apply_plans(
    conn: psycopg.Connection, plans: Sequence[CompanyPlan], *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> ApplyResult:
    """Append every planned change in ONE transaction, one stamp; clear the T5 holds; count the table.
    The caller holds the repair lock and owns the connection (commit happens here)."""
    todo = [p for p in plans if p.status == REPAIR and p.changes]
    result = ApplyResult()
    if not todo:
        conn.rollback()
        return result
    try:
        stamp = db_now(conn)
        for p in todo:
            rows = [{"d": ch.d, **ch.new} for ch in p.changes]
            result.appended += ingest_prices(
                conn, p.security_id, rows, tenant_id=tenant_id, recorded_at=stamp
            )
            if load_hold(conn, p.security_id, tenant_id=tenant_id) is not None:
                save_hold(conn, p.security_id, None, tenant_id=tenant_id)
                result.holds_cleared += 1
            result.companies.append(p.label)
        planned = sum(len(p.changes) for p in todo)
        landed = _count_stamped(conn, (p.security_id for p in todo), stamp, tenant_id=tenant_id)
        if landed != planned or result.appended != planned:
            raise RuntimeError(
                f"stamped-row count mismatch (planned {planned}, wrote {result.appended}, found {landed}) "
                "— ROLLED BACK, nothing written"
            )
        conn.commit()
        result.recorded_at = stamp
    except BaseException:
        conn.rollback()
        result.appended = 0
        raise
    return result


def _jsonable(x: Any) -> Any:
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, UUID):
        return str(x)
    return x


def build_payload(
    plans: Sequence[CompanyPlan], result: ApplyResult, *, spot_file: Path | None
) -> dict[str, Any]:
    """The provenance payload — PURE."""
    sha = None
    if spot_file is not None and spot_file.is_file():
        sha = hashlib.sha256(spot_file.read_bytes()).hexdigest()
    done = set(result.companies)
    return {
        "kind": KIND,
        "rule": f"{RULE}@{get_settings().image_sha or 'unknown'}",
        "recorded_at": _jsonable(result.recorded_at),
        "appended": result.appended,
        "holds_cleared": result.holds_cleared,
        "spot_file": None if spot_file is None else str(spot_file),
        "spot_file_sha256": sha,
        "companies": [
            {
                "ticker": p.ticker,
                "price_symbol": p.price_symbol,
                "security_id": str(p.security_id),
                "splits": [
                    {
                        "key_date": s.key_date.isoformat(),
                        "effective_date": s.effective_date.isoformat(),
                        "ratio": f"{s.ratio_from}->{s.ratio_to}",
                        "factor": str(s.factor),
                        "label": s.label,
                        "status": s.status,
                        "sec_accession": s.sec_accession,
                        "version_id": s.version_id,
                    }
                    for s in p.in_force
                ],
                "runs": [
                    [a.isoformat(), z.isoformat(), lab, n, c]
                    for a, z, lab, n, c in (p.classification.runs() if p.classification else [])
                ],
                "islands": [
                    {"dates": [d.isoformat() for d in i.dates], "factor": i.factor}
                    for i in (p.classification.islands if p.classification else [])
                ],
                "gates": (
                    None
                    if p.gates is None
                    else {
                        "t4_before": p.gates.t4_before,
                        "t4_after": p.gates.t4_after,
                        "k_steps_after": p.gates.k_steps_after,
                        "spot_checked": p.gates.spot_checked,
                        "spot_near": p.gates.spot_near,
                    }
                ),
                "bars": [
                    {
                        "d": ch.d.isoformat(),
                        "label": ch.label,
                        "factor": ch.factor,
                        "island": ch.island,
                        "old": {k: _jsonable(v) for k, v in ch.old.items()},
                        "old_recorded_at": _jsonable(ch.old_recorded_at),
                        "new": {k: _jsonable(v) for k, v in ch.new.items()},
                    }
                    for ch in p.changes
                ],
            }
            for p in plans
            if p.label in done
        ],
    }


def write_payload(payload: dict[str, Any], base_dir: Path | None = None, *, suffix: str) -> Path:
    run_dir = base_dir or _DEFAULT_LOG_DIR
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = run_dir / f"{stamp}-{suffix}.json"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


class RevokeRefused(ValueError):
    pass


def find_provenance(stamp: str, base_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """The apply's provenance file for ``stamp`` (its ``recorded_at``, compared as instants)."""
    want = datetime.fromisoformat(stamp)
    run_dir = base_dir or _DEFAULT_LOG_DIR
    for path in sorted(run_dir.glob("*-split-basis.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if blob.get("kind") == KIND and blob.get("recorded_at"):
            if datetime.fromisoformat(blob["recorded_at"]) == want:
                return path, blob
    raise RevokeRefused(f"no {KIND} provenance file under {run_dir} for recorded_at {stamp}")


def revoke(
    conn: psycopg.Connection,
    provenance: Mapping[str, Any],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> ApplyResult:
    """Append the prior values of every bar a repair wrote, as NEW versions (one stamp, one transaction),
    refused unless every bar's newest version is still the repair's. Nothing is deleted."""
    stamp = datetime.fromisoformat(provenance["recorded_at"])
    result = ApplyResult()
    try:
        plan: list[tuple[UUID, dict[str, Any]]] = []
        changed: list[str] = []
        for comp in provenance["companies"]:
            sid = UUID(comp["security_id"])
            latest = {r["d"]: r for r in stored_tape(conn, sid, tenant_id=tenant_id)}
            for b in comp["bars"]:
                d = date.fromisoformat(b["d"])
                cur = latest.get(d)
                if cur is None or cur["recorded_at"] != stamp:
                    changed.append(f"{comp['ticker']} {d.isoformat()}")
                    continue
                plan.append((sid, {"d": d, **{k: _dec(v) for k, v in b["old"].items()}}))
            result.companies.append(comp["ticker"])
        if changed:
            raise RevokeRefused(
                f"{len(changed)} bar(s) changed since the repair (newest version is no longer the "
                f"repair's) — nothing revoked: {', '.join(changed[:10])}"
            )
        new_stamp = db_now(conn)
        by_sid: dict[UUID, list[dict[str, Any]]] = {}
        for sid, row in plan:
            by_sid.setdefault(sid, []).append(row)
        for sid, rows in by_sid.items():
            result.appended += ingest_prices(
                conn, sid, rows, tenant_id=tenant_id, recorded_at=new_stamp
            )
            if load_hold(conn, sid, tenant_id=tenant_id) is not None:
                save_hold(conn, sid, None, tenant_id=tenant_id)
                result.holds_cleared += 1
        landed = _count_stamped(conn, by_sid, new_stamp, tenant_id=tenant_id)
        if landed != len(plan) or result.appended != len(plan):
            raise RuntimeError(
                f"stamped-row count mismatch (planned {len(plan)}, wrote {result.appended}, found "
                f"{landed}) — ROLLED BACK, nothing written"
            )
        conn.commit()
        result.recorded_at = new_stamp
    except BaseException:
        conn.rollback()
        result.appended = 0
        raise
    return result


def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# --- the report ------------------------------------------------------------------------------------------------


def _fmt_bar(r: Mapping[str, Any]) -> str:
    c, v = r.get("close"), r.get("volume")
    cs = "—" if c is None else f"{float(c):.6g}"
    vs = "—" if v is None else f"{float(v):,.0f}"
    return f"close {cs} vol {vs}"


def _display_diff(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    keys = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    if not keys:
        return "no change"
    return "; ".join(f"{k} {a.get(k)} -> {b.get(k)}" for k in keys)


def report(plans: Sequence[CompanyPlan], *, verbose: bool = True) -> None:
    for p in plans:
        print("-" * 100)
        head = f"{p.label}" + (f" (prices as {p.price_symbol})" if p.price_symbol else "")
        print(f"{head}: {p.status.upper()}" + (f" — {p.name}" if p.name else ""))
        for s in p.in_force:
            print(
                f"  split in force: {s.label}, first session {s.effective_date.isoformat()} "
                f"(x{float(s.factor):.6g}; {s.status}; SEC {s.sec_accession or '—'})"
            )
        for s in p.waiting:
            print(
                f"  WAITING for the split to be confirmed: {s.label}, {s.effective_date.isoformat()} "
                f"({s.status}) — not applied"
            )
        if p.holds:
            print(f"  T5 hold on file: {p.holds} (cleared on --apply)")
        c = p.classification
        if c is not None and c.labels:
            runs = c.runs()
            shown = runs if len(runs) <= 14 else runs[:3] + runs[-11:]
            print(
                f"  runs ({len(runs)}): "
                + "; ".join(
                    f"{a.isoformat()}..{z.isoformat()} {lab} {cc} ({n})"
                    for a, z, lab, n, cc in shown
                )
            )
            if c.islands:
                print(
                    "  islands: "
                    + ", ".join(
                        f"{i.dates[0].isoformat()}(+{len(i.dates) - 1}) x{i.factor:g}"
                        for i in c.islands
                    )
                )
            if c.spot_near:
                print(f"  raw prints within vendor noise (1-3%): {len(c.spot_near)}")
        for r in p.refusals:
            print(f"  REFUSED: {r}")
        if p.changes:
            print(
                f"  bars to rescale: {len(p.changes)} ({p.in_window} inside the {sr.window_days()}-day window)"
            )
            sample = [p.changes[0], p.changes[-1]] + [ch for ch in p.changes if ch.island]
            seen = set()
            for ch in sample:
                if ch.d in seen:
                    continue
                seen.add(ch.d)
                print(
                    f"    {ch.d.isoformat()} {ch.label} {ch.factor}: {_fmt_bar(ch.old)} -> {_fmt_bar(ch.new)}"
                    + (" [island]" if ch.island else "")
                )
        if p.gates is not None:
            g = p.gates
            print(f"  T4 before: {g.t4_before or 'clean'} | after: {g.t4_after or 'clean'}")
            if g.k_steps_after:
                print(f"  split-sized steps left: {g.k_steps_after}")
            if g.spot_checked:
                print(
                    f"  raw prints: {g.spot_checked} checked, {len(g.spot_near)} within vendor noise, "
                    f"{len(g.spot_fail)} failed"
                )
        if verbose and (p.display_before or p.display_after):
            print(f"  Cockpit columns: {_display_diff(p.display_before, p.display_after)}")
        if verbose and p.forecast:
            print(
                f"  tonight's pull (cached series, nothing fetched): {json.dumps(p.forecast, default=str)}"
            )
    by: dict[str, list[str]] = {}
    for p in plans:
        by.setdefault(p.status, []).append(p.label)
    print("=" * 100)
    print(
        "SUMMARY: "
        + " · ".join(f"{k} {len(v)}" for k, v in sorted(by.items()))
        + f" · bars to rescale {sum(len(p.changes) for p in plans if p.status == REPAIR)}"
    )
    for k in (REPAIR, REFUSED, WAITING):
        if by.get(k):
            print(f"  {k}: {', '.join(by[k])}")


# --- the CLI -----------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="S2: rescale STORED price bars onto the split list's scale (append-only, one stamp). "
        "Dry-run by default."
    )
    p.add_argument(
        "--ticker", help="comma-separated tickers (or price symbols); --apply requires it"
    )
    p.add_argument(
        "--spot-file", type=Path, help="independent raw prints (JSON) — see split_evidence/"
    )
    p.add_argument("--apply", action="store_true", help="write (default: dry-run, read-only)")
    p.add_argument("--revoke", metavar="STAMP", help="append the prior values of that repair back")
    p.add_argument(
        "--today", type=date.fromisoformat, help="the market day to judge from (default: today)"
    )
    p.add_argument("--quiet", action="store_true", help="omit the Cockpit / forecast lines")
    p.add_argument(
        "--cache-dir",
        type=Path,
        help="the vendor cache the forecast READS (default: the price leg's; never fetched, never written)",
    )
    args = p.parse_args(argv)
    if args.apply and args.revoke:
        p.error("--apply and --revoke are separate runs")
    if args.apply and not args.ticker:
        p.error("--apply needs --ticker (name the companies to write)")
    tickers = [t for t in (args.ticker or "").split(",") if t.strip()] or None

    if args.revoke:
        path, prov = find_provenance(args.revoke)
        print(f"repair_split_basis · REVOKE {args.revoke} · provenance {path}")
        _with_lock(lambda conn: _do_revoke(conn, prov))
        return

    spots = load_spots(args.spot_file)
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    print(
        f"repair_split_basis · {mode} · today {args.today or market_today()} · "
        f"companies {','.join(tickers) if tickers else 'every listed'} · raw prints "
        f"{'from ' + str(args.spot_file) if args.spot_file else 'none (a mis-factor or a doubled split is not visible on the tape)'}"
    )
    if not args.apply:
        conn = connect()
        try:
            conn.read_only = True
            plans = plan_all(
                conn, tickers=tickers, today=args.today, spots=spots, cache_dir=args.cache_dir
            )
            conn.rollback()
        finally:
            conn.close()
        report(plans, verbose=not args.quiet)
        return

    def run(conn: psycopg.Connection) -> None:
        plans = plan_all(
            conn, tickers=tickers, today=args.today, spots=spots, cache_dir=args.cache_dir
        )
        report(plans, verbose=not args.quiet)
        bad = [p for p in plans if p.status not in (REPAIR, CLEAN)]
        if bad:
            conn.rollback()
            print(
                "REFUSED (nothing was written): every named company must pass — "
                + ", ".join(f"{p.label} ({p.status})" for p in bad)
            )
            raise SystemExit(EXIT_REFUSED)
        result = apply_plans(conn, plans)
        if result.recorded_at is None:
            print("nothing to write: every named company is already on the list's scale (0 bars)")
            return
        path = write_payload(
            build_payload(plans, result, spot_file=args.spot_file), suffix="split-basis"
        )
        print(
            f"APPLIED: appended {result.appended} version(s) · recorded_at {result.recorded_at.isoformat()} "
            f"· holds cleared {result.holds_cleared} · {', '.join(result.companies)}\nprovenance: {path}"
        )

    _with_lock(run)


def _do_revoke(conn: psycopg.Connection, prov: Mapping[str, Any]) -> None:
    try:
        result = revoke(conn, prov)
    except RevokeRefused as exc:
        print(f"REFUSED (nothing was written): {exc}")
        raise SystemExit(EXIT_REFUSED) from None
    payload = {
        "kind": KIND_REVOKE,
        "rule": f"{RULE}@{get_settings().image_sha or 'unknown'}",
        "revokes": prov["recorded_at"],
        "recorded_at": _jsonable(result.recorded_at),
        "appended": result.appended,
        "companies": result.companies,
    }
    path = write_payload(payload, suffix="split-basis-revoke")
    print(
        f"REVOKED: appended {result.appended} prior version(s) · recorded_at "
        f"{_jsonable(result.recorded_at)} · {', '.join(result.companies)}\nprovenance: {path}"
    )


def _with_lock(fn) -> None:
    try:
        lock = acquire_repair_lock(holder="repair_split_basis")
    except PassAlreadyRunning as exc:
        print(f"repair: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})")
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    try:
        conn = connect()
        try:
            fn(conn)
        finally:
            conn.close()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
