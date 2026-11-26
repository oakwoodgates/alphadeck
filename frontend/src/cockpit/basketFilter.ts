import type { DisplaySignal } from "../api/hooks";
import { businessTypeLabel } from "../util/format";
import { isSilentLink } from "../workbench/useChainDraft";
import type { BucketDef, BucketKey, BucketRow } from "./buckets";
import { metricValue, type RowSignals, type SortColId } from "./sortBasket";

/** Reversible view-only filters for the Cockpit basket table (WB#1, WB#2, WB#3).
 *  Off by default; never deletes or alters any call input (invariant #4). */
export interface BasketFilterState {
  query: string;
  actionableOnly: boolean;
  insiderBuysOnly: boolean;
  hotRvolOnly: boolean;
  hideSilent: boolean;
}

export const DEFAULT_BASKET_FILTER: BasketFilterState = {
  query: "",
  actionableOnly: false,
  insiderBuysOnly: false,
  hotRvolOnly: false,
  hideSilent: false,
};

/** Buckets carrying an active call, trigger, or watch confirmation (anything beyond Quiet / Silent). */
const ACTIONABLE_BUCKETS: ReadonlySet<BucketKey> = new Set<BucketKey>([
  "managing",
  "armed",
  "lapsing",
  "theme_armed",
  "warming",
  "watch",
]);

export function isActionableBucket(bucket: BucketKey): boolean {
  return ACTIONABLE_BUCKETS.has(bucket);
}

export function isFilterActive(f: BasketFilterState): boolean {
  return (
    f.query.trim() !== "" ||
    f.actionableOnly ||
    f.insiderBuysOnly ||
    f.hotRvolOnly ||
    f.hideSilent
  );
}

/** True when either RVOL window meets its own server-provided loud threshold (never hardcoded). */
export function isHotRvol(sig: DisplaySignal | null): boolean {
  if (!sig) return false;
  const r8 = metricValue(sig, "rvol");
  const l8 = sig.basis.params?.loud_mult;
  if (r8 != null && typeof l8 === "number" && r8 >= l8) return true;
  const r20 = metricValue(sig, "rvol20");
  const l20 = sig.basis.params?.loud_mult_20;
  if (r20 != null && typeof l20 === "number" && r20 >= l20) return true;
  return false;
}

/** True when the 90-day insider flow signal shows at least one open-market buy. */
export function hasInsiderBuys(sig: DisplaySignal | null): boolean {
  const buys = metricValue(sig, "buy_count");
  return buys != null && buys > 0;
}

export function rowMatchesFilter(
  row: BucketRow,
  def: BucketDef,
  signals: RowSignals,
  f: BasketFilterState,
): boolean {
  if (f.actionableOnly && !isActionableBucket(def.key)) return false;
  if (f.hideSilent && isSilentLink(row.member.segment)) return false;
  if (f.insiderBuysOnly && !hasInsiderBuys(signals.insider)) return false;
  if (f.hotRvolOnly && !isHotRvol(signals.rvol)) return false;
  const q = f.query.trim().toLowerCase();
  if (q) {
    const tk = row.member.ticker.toLowerCase();
    const nm = (row.scored?.name ?? "").toLowerCase();
    const bt = row.scored?.business_type ? businessTypeLabel(row.scored.business_type).toLowerCase() : "";
    const seg = (row.member.segment ?? "").toLowerCase();
    const role = (row.member.context_role ?? "").toLowerCase();
    if (!tk.includes(q) && !nm.includes(q) && !bt.includes(q) && !seg.includes(q) && !role.includes(q)) {
      return false;
    }
  }
  return true;
}

export interface BasketFilterCounts {
  total: number;
  actionable: number;
  insiderBuys: number;
  hotRvol: number;
  silent: number;
}

/** Count how many rows match each quick-filter predicate so the UI renders a chip ONLY when it
 *  discriminates (0 < count < total; WB#3 honest loudness). */
export function countFilterCandidates(
  rows: readonly { row: BucketRow; def: BucketDef }[],
  signalsFor: (r: BucketRow) => RowSignals,
): BasketFilterCounts {
  let actionable = 0;
  let insiderBuys = 0;
  let hotRvol = 0;
  let silent = 0;
  for (const { row, def } of rows) {
    const sigs = signalsFor(row);
    if (isActionableBucket(def.key)) actionable += 1;
    if (hasInsiderBuys(sigs.insider)) insiderBuys += 1;
    if (isHotRvol(sigs.rvol)) hotRvol += 1;
    if (isSilentLink(row.member.segment)) silent += 1;
  }
  return { total: rows.length, actionable, insiderBuys, hotRvol, silent };
}

export const SORT_COL_LABEL: Record<SortColId, string> = {
  ticker: "Ticker",
  name: "Name",
  type: "Type",
  last: "Last close",
  sma: "SMA",
  ret_1d: "1d",
  ret_7d: "7d",
  ret_30d: "30d",
  ret_90d: "90d",
  ret_1y: "1Y",
  off52w: "Off 52w high",
  rvol8: "RVOL 8D",
  rvol20: "RVOL 20D",
  ins_30d: "Ins 30d",
  ins_90d: "Ins 90d",
  mktcap: "Mkt cap",
  adv: "ADV 20d",
  entry_by: "Entry-by",
  exit_by: "Exit-by",
};
