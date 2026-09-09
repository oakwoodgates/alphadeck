import type { DisplaySignal } from "../api/hooks";
import type { BucketRow } from "./buckets";
import { metricValue } from "./sortBasket";

/** The group header's "is this group moving?" line — a CLIENT-SIDE aggregate over the 7d
 *  trailing-return values ALREADY on the wire (the `trailing_returns` display member's `ret_7d`, the
 *  SAME value the 7d column renders — nothing new is fetched, nothing is re-derived). Pure + testable
 *  (the ``sortBasket.ts`` precedent): the Cockpit injects each row's resolved trail signal.
 *
 *  The approved rules (operator sign-off — build exactly these):
 *    - MEDIAN, not mean: robust to a single blowup (one +40% name must not drag a group of −1%s
 *      into "moving").
 *    - ONE window, 7d. No other window in v1.
 *    - `n` is the FULL group size — every row counts, priced or not (recall-honest, #9); the median
 *      is over the PRICED subset (rows carrying a 7d return). A thin-history "—" is excluded, never
 *      counted as 0.
 *    - fewer than `AGG_MIN_PRICED` priced rows → NO median (null → the header reads "—"): a "median"
 *      of one or two names is not a group read, and a fabricated 0.0% is worse than none.
 *
 *  A DISPLAY aggregate, never a call input (#4): it changes no membership, no order, no call. */

/** The one metric key the aggregate reads — the 7d column's own key on the `trailing_returns` member. */
export const AGG_RETURN_KEY = "ret_7d";
/** Below this many priced rows the median is withheld (the header shows "—"). */
export const AGG_MIN_PRICED = 3;

export interface GroupMoving {
  /** The FULL group size — every row, priced or not. */
  n: number;
  /** How many of those rows carry a 7d return (the median's population). */
  priced: number;
  /** The median 7d return (%) over the priced rows; null when fewer than `AGG_MIN_PRICED` are priced. */
  median: number | null;
}

/** The median of a list: the middle value, or the mean of the two middles on an even count. Null on
 *  an empty list. Never mutates its input (a sorted COPY). */
export function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 === 1 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** The moving line for ONE rendered group (any lens — the `{ row, def }` render shape they share).
 *  `trailFor` resolves a row's `trailing_returns` display signal off the Cockpit's sid-bridged map
 *  (null when the row carries none). The 7d value is read through the cells' + the sort's own
 *  predicate (`metricValue`: an absent metric OR a null value → not priced), so the priced set is
 *  exactly the set of rows whose 7d cell shows a number. Reads nothing else: a name with only a 30d
 *  return is unpriced here (the one-window rule). */
export function groupMoving<T extends { row: BucketRow }>(
  rows: readonly T[],
  trailFor: (row: BucketRow) => DisplaySignal | null,
): GroupMoving {
  const values: number[] = [];
  for (const { row } of rows) {
    const v = metricValue(trailFor(row), AGG_RETURN_KEY);
    if (v != null) values.push(v);
  }
  return {
    n: rows.length,
    priced: values.length,
    median: values.length >= AGG_MIN_PRICED ? median(values) : null,
  };
}
