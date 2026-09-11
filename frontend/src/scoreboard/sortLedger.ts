import type { OperatorSpanOut, ScoreboardEpisodeOut } from "../api/hooks";
import { awaitingForwardBar } from "./rows";
import { noForwardBar } from "./scorecard";

/** Sorting the Scoreboard ledger — a WITHIN-GROUP re-order, never a filter and never a flattening.
 *  The thesis group is the ledger's spine: a sort re-ranks the rows inside each group and the groups
 *  themselves never move, so the record stays readable as "what did THIS thesis do".
 *
 *  Pure + unit-tested, the way `cockpit/sortBasket.ts` is — it takes wire rows and returns wire rows,
 *  touches no hook and holds no state (the two hosts, `Scoreboard` and `ReplayPanel`, each hold their
 *  own). The three rules it inherits from the Cockpit's sort, because they are the same rules:
 *
 *  1. A key is read off the SAME access path the CELL renders from, including the cell's dash guard —
 *     so the ranking always matches what the operator sees.
 *  2. A "—" cell is ABSENT (a null key), never a low or high value, and sinks to the bottom in BOTH
 *     directions. A missing measurement is not a small one (#9/#2 — it is still visible, just last).
 *  3. The cycle is reversible: desc → asc → off, and "off" restores the record's own order. */

/** The sortable columns. Two columns are deliberately NOT here:
 *
 *  - **Path** is a shape, not a number — there is no honest scalar to rank a sparkline on (the same
 *    ruling the Cockpit's own Path column carries).
 *  - **Status** is a set of badges, not a value. Ranking OPEN/MATURED/CENSORED/INGEST against each
 *    other would invent an ordering the record does not have.
 *
 *  The Summary-only cells are out for the same reason: **Why** is a chip set, **Operator** is a
 *  sentence, and **Exit-by** is a horizon the operator reads per row rather than ranks (it is not in
 *  the operator's requested set). Every column below resolves to a well-defined per-row key. */
export type LedgerSortColId =
  | "name"
  | "armed"
  | "dearmed"
  | "ret"
  | "peak"
  | "peak_high"
  | "worst"
  | "worst_low"
  | "past_peak";

export type SortDir = "asc" | "desc";
export interface LedgerSort {
  col: LedgerSortColId;
  dir: SortDir;
}

/** A composite, comparable sort key (numbers by value, strings by locale). `null` means ABSENT. */
export type SortKey = (number | string)[];

function numKey(v: number | null | undefined): SortKey | null {
  return v == null ? null : [v];
}

/** An ISO date as epoch ms, or null for an absent/unparseable one (never 0 — that is a real date). */
function dateKey(d: string | null | undefined): SortKey | null {
  if (!d) return null;
  const ms = Date.parse(`${d}T00:00:00Z`);
  return Number.isNaN(ms) ? null : [ms];
}

/** The per-column key for an EPISODE row. Each guard mirrors its cell exactly:
 *
 *  - `ret` dashes while `awaitingForwardBar` (a single arm-day bar carries a degenerate 0.0%).
 *  - the four excursions and `past_peak` dash behind `noForwardBar` — the same honest-loudness guard
 *    the cells use, so a degenerate 0.0% / 0d never ranks as a measurement.
 *  - a wick field is independently null when the window's bars don't all carry one (the
 *    all-or-nothing wick rule), so `peak_high` / `worst_low` can be absent on a row whose close
 *    figures are present — exactly what the cell shows. */
export function episodeSortKey(col: LedgerSortColId, e: ScoreboardEpisodeOut): SortKey | null {
  const noBar = noForwardBar(e);
  switch (col) {
    case "name":
      return e.ticker ? [e.ticker.toLowerCase()] : null; // an unresolved ticker renders "—"
    case "armed":
      return dateKey(e.arm_date);
    case "dearmed":
      return dateKey(e.dearm_date); // still open → no de-arm → absent, not "someday"
    case "ret":
      return awaitingForwardBar(e) ? null : numKey(e.forward_return);
    case "peak":
      return noBar ? null : numKey(e.peak_return);
    case "peak_high":
      return noBar ? null : numKey(e.intraday_high_return);
    case "worst":
      return noBar ? null : numKey(e.trough_return);
    case "worst_low":
      return noBar ? null : numKey(e.intraday_low_return);
    case "past_peak":
      return noBar ? null : numKey(e.exit_vs_peak_days);
  }
}

/** The per-column key for an off-record OPERATOR SPAN. A span is a logged take, not an arm, so it
 *  carries NO platform lens: only its name and its take date are real values under these headers,
 *  and everything else is an absent (null) key that sinks it to the bottom of the span block — the
 *  same "—" the cells already render.
 *
 *  Return is the one that looks like it should rank and must not: the Summary span row puts the
 *  OPERATOR's return under the record-return header — a different measurement in a shared column —
 *  and the Timing span row dashes it outright. Ranking the two against each other would be inventing
 *  a comparison the record does not make. */
export function spanSortKey(col: LedgerSortColId, s: OperatorSpanOut): SortKey | null {
  switch (col) {
    case "name":
      return s.ticker ? [s.ticker.toLowerCase()] : null; // thesis-level (◇) or unresolved → absent
    case "armed":
      return dateKey(s.take_date);
    default:
      return null;
  }
}

/** Compare two keys component-wise (numbers by value, strings by locale); a shorter-but-equal prefix
 *  sorts first. Direction-independent — the caller applies asc/desc. */
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

/** The NULLS-LAST comparator: the null test runs BEFORE the direction flip, so an absent value is
 *  last whether the sort is desc or asc. Equal / both-absent rows return 0, so a STABLE sort keeps
 *  the record's own incoming order beneath the ranking. */
function compareBy<T>(
  a: T,
  b: T,
  sort: LedgerSort,
  keyOf: (col: LedgerSortColId, row: T) => SortKey | null,
): number {
  const ka = keyOf(sort.col, a);
  const kb = keyOf(sort.col, b);
  if (ka === null && kb === null) return 0;
  if (ka === null) return 1; // a is absent → after b, regardless of dir
  if (kb === null) return -1; // b is absent → after a, regardless of dir
  const base = compareKeys(ka, kb);
  return sort.dir === "asc" ? base : -base;
}

/** Re-order ONE group's episodes. `[...]` guarantees the same length out — a re-order, never a
 *  filter (#9): no episode can be sorted off the ledger. A null sort returns the record's order. */
export function sortEpisodes(
  eps: readonly ScoreboardEpisodeOut[],
  sort: LedgerSort | null,
): ScoreboardEpisodeOut[] {
  if (!sort) return [...eps];
  return [...eps].sort((a, b) => compareBy(a, b, sort, episodeSortKey));
}

/** Re-order ONE group's operator spans — sorted among THEMSELVES, never interleaved with the
 *  episodes above them. Two row kinds sharing a column set is not the same as sharing a ranking:
 *  an arm episode and a logged take are different objects, and merging them would be a structural
 *  change to the ledger, not a sort. */
export function sortSpans(
  spans: readonly OperatorSpanOut[],
  sort: LedgerSort | null,
): OperatorSpanOut[] {
  if (!sort) return [...spans];
  return [...spans].sort((a, b) => compareBy(a, b, sort, spanSortKey));
}

/** The 3-state header cycle: a fresh column starts DESC; the active column goes desc → asc → off
 *  (null restores the record's own order). Pure — the host holds the state. */
export function nextLedgerSort(cur: LedgerSort | null, col: LedgerSortColId): LedgerSort | null {
  if (!cur || cur.col !== col) return { col, dir: "desc" };
  if (cur.dir === "desc") return { col, dir: "asc" };
  return null;
}
