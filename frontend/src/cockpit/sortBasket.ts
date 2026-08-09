import type { DisplaySignal } from "../api/hooks";
import type { BucketRow } from "./buckets";
import { SUPERSECTOR_ORDER } from "./buckets";

/** Sorting the cockpit basket table — a within-group re-order (never a filter, never a flat list).
 *  Pure + testable: it takes a row's already-resolved display signals (injected by the Cockpit off
 *  its four sid-bridged maps) so this module never touches a hook. The call hierarchy stays — each
 *  group's rows re-rank, the groups don't move — and a "—" cell (ABSENT, not a low/high value) sinks
 *  to the bottom of its group in BOTH directions (#9/#2). See the header of ``buckets.ts`` for the
 *  default order this sort replaces (call-rank then authored ordinal). */

/** The sortable columns (the status-dot column is not sortable). Each id resolves to a well-defined
 *  per-row sort key below — read off the SAME access paths the cells render from, so the ranking
 *  always matches what the operator sees. */
export type SortColId =
  | "ticker"
  | "name"
  | "type"
  | "sma"
  | "ret_1d"
  | "ret_7d"
  | "ret_30d"
  | "ret_90d"
  | "ret_1y"
  | "rvol8"
  | "rvol20"
  | "ins_30d"
  | "ins_90d"
  | "mktcap"
  | "exit_by";

export type SortDir = "asc" | "desc";
export interface SortState {
  col: SortColId;
  dir: SortDir;
}

/** The display signals for ONE row, resolved from the four sid-bridged maps the Cockpit already
 *  builds (smaBySid / trailBySid / rvolBySid / insiderBySid) and INJECTED here — so this module
 *  stays pure and unit-testable without the hooks. ``null`` when the row carries no such member. */
export interface RowSignals {
  sma: DisplaySignal | null;
  trail: DisplaySignal | null;
  rvol: DisplaySignal | null;
  insider: DisplaySignal | null;
}

export interface RowCtx {
  row: BucketRow;
  signals: RowSignals;
}

/** A composite, comparable sort key: components compared left→right (numbers by value, strings by
 *  locale). ``null`` means the value is ABSENT (a "—" cell) — never a low/high number, so it sorts
 *  LAST in both directions (``compareRows`` handles that BEFORE the direction flip). */
export type SortKey = (number | string)[];

/** One numeric display metric's value by key, or null when the metric is absent OR its value is null
 *  — the SAME predicate the cells use for a "—" (``m.value == null``), so the sort's nulls-last set
 *  is exactly the set of dashes the operator sees. */
function metricValue(sig: DisplaySignal | null, key: string): number | null {
  const m = (sig?.metrics ?? []).find((x) => x.key === key);
  return m && m.value != null ? m.value : null;
}

function numKey(v: number | null): SortKey | null {
  return v == null ? null : [v];
}

/** Insider sort key: **buyers-primary, buys-secondary** (breadth-first — a ≥2-buyer cluster is the
 *  conviction tell, matching the cell's accent). Null (→ last) when there are no buys OR the metric
 *  is absent — EXACTLY the cell's "—" predicate (``buys == null || buys === 0``). */
function insiderKey(
  sig: DisplaySignal | null,
  countKey: string,
  buyersKey: string,
): SortKey | null {
  const buys = metricValue(sig, countKey);
  if (buys == null || buys === 0) return null;
  const buyers = metricValue(sig, buyersKey) ?? 0;
  return [buyers, buys];
}

/** The per-column, per-row sort key — the crux. Returns null for an ABSENT value (nulls-last). */
export function sortKey(col: SortColId, ctx: RowCtx): SortKey | null {
  const { row, signals } = ctx;
  switch (col) {
    case "ticker":
      return [row.member.ticker.toLowerCase()];
    case "name": {
      const n = row.scored?.name;
      return n ? [n.toLowerCase()] : null;
    }
    case "type": {
      // primary = the super-sector's fixed display order (the type-lens order); secondary = the leaf
      // label. No super (un-enriched, or an ETF sleeve — a fund has no SIC) → null (last), as noted.
      const sup = row.scored?.business_supersector ?? null;
      if (!sup) return null;
      const idx = (SUPERSECTOR_ORDER as readonly string[]).indexOf(sup);
      const order = idx === -1 ? SUPERSECTOR_ORDER.length : idx; // a future super after the knowns
      return [order, row.scored?.business_type ?? ""];
    }
    case "sma":
      // signed distance vs the slow line (pct_vs_slow) — most-above → most-below
      return numKey(metricValue(signals.sma, "pct_vs_slow"));
    case "ret_1d":
      return numKey(metricValue(signals.trail, "ret_1d"));
    case "ret_7d":
      return numKey(metricValue(signals.trail, "ret_7d"));
    case "ret_30d":
      return numKey(metricValue(signals.trail, "ret_30d"));
    case "ret_90d":
      return numKey(metricValue(signals.trail, "ret_90d"));
    case "ret_1y":
      return numKey(metricValue(signals.trail, "ret_1y"));
    case "rvol8":
      return numKey(metricValue(signals.rvol, "rvol"));
    case "rvol20":
      return numKey(metricValue(signals.rvol, "rvol20"));
    case "ins_30d":
      return insiderKey(signals.insider, "buy_count_30d", "distinct_buyers_30d");
    case "ins_90d":
      return insiderKey(signals.insider, "buy_count", "distinct_buyers");
    case "mktcap": {
      const v = row.scored?.market_cap.value;
      return v == null ? null : [v];
    }
    case "exit_by": {
      const d = row.call?.exit_by;
      if (!d) return null;
      const ms = Date.parse(`${d}T00:00:00Z`);
      return Number.isNaN(ms) ? null : [ms];
    }
  }
}

/** Compare two sort keys component-wise (numbers by value, strings by locale). A shorter-but-equal
 *  prefix sorts first. Direction-independent; the caller applies asc/desc. */
function compareKeys(a: SortKey, b: SortKey): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i];
    const y = b[i];
    const c =
      typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    if (c !== 0) return c;
  }
  return a.length - b.length;
}

/** Row comparator with the NULLS-LAST hard rule (#9/#2): an absent value (null key) sinks to the
 *  bottom in BOTH directions — the null test runs BEFORE the direction flip. Equal / both-absent
 *  rows return 0 so a STABLE sort preserves the default order (call-rank then authored ordinal). */
export function compareRows(a: RowCtx, b: RowCtx, sort: SortState): number {
  const ka = sortKey(sort.col, a);
  const kb = sortKey(sort.col, b);
  if (ka === null && kb === null) return 0;
  if (ka === null) return 1; // a is absent → after b, regardless of dir
  if (kb === null) return -1; // b is absent → after a, regardless of dir
  const base = compareKeys(ka, kb);
  return sort.dir === "asc" ? base : -base;
}

/** Resolve a row's four display signals — the seam the Cockpit fills from its sid-bridged maps. */
export type SignalsFor = (row: BucketRow) => RowSignals;

/** Re-order a lens's render-rows by the active sort WITHOUT dropping any (a re-order, never a
 *  filter): ``[...rows]`` guarantees the same length out. Generic over the ``{ row, def }`` render
 *  shape all three lenses share, so ONE call at the render seam covers every lens. A stable sort
 *  (V8 / jsdom) keeps equal-and-absent rows in their incoming (default) order. */
export function sortRenderRows<T extends { row: BucketRow }>(
  rows: T[],
  sort: SortState,
  signalsFor: SignalsFor,
): T[] {
  return [...rows].sort((a, b) =>
    compareRows(
      { row: a.row, signals: signalsFor(a.row) },
      { row: b.row, signals: signalsFor(b.row) },
      sort,
    ),
  );
}

/** The 3-state header cycle: a fresh column starts DESC; the active column goes desc → asc → off
 *  (null restores the default order). Pure — the Cockpit holds the state. */
export function nextSort(cur: SortState | null, col: SortColId): SortState | null {
  if (!cur || cur.col !== col) return { col, dir: "desc" };
  if (cur.dir === "desc") return { col, dir: "asc" };
  return null;
}
