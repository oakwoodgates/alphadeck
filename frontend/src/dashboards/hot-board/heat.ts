import type { HotRowOut } from "./useHotBoard";

// The Hot Board's heat (docs/dashboards/hot-board.md), the stated formula, recomputed here for the rows the page
// SHOWS (the toggles and filters change it). A pure mirror of backend/dashboards/hot_board.py's `heat`: the two
// are tested against the same fixtures (__tests__/heat-fixtures.json), so they cannot drift. Display only: heat is
// printed under the table and feeds nothing.

/** What the heat reads of a row: the four readings. `null` = no reading (that part is blank). */
export type HeatInput = {
  key: string;
  sessions_since: number | null;
  rvol20: number | null;
  /** The 30-day return minus IWM's, percentage points. */
  vs_iwm: number | null;
  /** The return since the arm night, percent. */
  since_arm_pct: number | null;
};

/** A row's four percentiles (0 to 1; `null` = that part has no reading) and its heat (0 to 100; `null` when any
 *  part is missing). */
export type Heat = {
  fresh: number | null;
  loud: number | null;
  leading: number | null;
  unextended: number | null;
  score: number | null;
};

export type HeatPart = "fresh" | "loud" | "leading" | "unextended";
export const HEAT_PARTS: readonly HeatPart[] = ["fresh", "loud", "leading", "unextended"];

/** Each present value's percentile among the present values, a higher value = a higher percentile, the mid-rank
 *  over n - 1: (below + (equal - 1) / 2) / (n - 1), `equal` counting the value itself. The best of n is 1, the
 *  worst 0, a tie shares the average of the ranks it spans, a lone value is 0.5 (nothing to compare it with). A
 *  blank stays blank. */
export function percentiles(values: ReadonlyArray<number | null>): Array<number | null> {
  const present = values.filter((v): v is number => v !== null);
  const n = present.length;
  return values.map((v) => {
    if (v === null) return null;
    if (n === 1) return 0.5;
    let below = 0;
    let equal = 0;
    for (const w of present) {
      if (w < v) below += 1;
      else if (w === v) equal += 1;
    }
    return (below + (equal - 1) / 2) / (n - 1);
  });
}

const neg = (v: number | null): number | null => (v === null ? null : -v);

/** The heat of each row among `rows` (the rows shown), in order. Fresh ranks fewer sessions since the arm night
 *  higher, loud a higher RVOL 20, leading a higher return-minus-IWM, unextended a smaller return since the arm
 *  night. Each part's percentile is taken among the rows with that reading; the heat is the mean of the four
 *  times 100, and `null` for a row missing any part. Equal weights (PROPOSED). */
export function heat(rows: readonly HeatInput[]): Heat[] {
  const fresh = percentiles(rows.map((r) => neg(r.sessions_since)));
  const loud = percentiles(rows.map((r) => r.rvol20));
  const leading = percentiles(rows.map((r) => r.vs_iwm));
  const unextended = percentiles(rows.map((r) => neg(r.since_arm_pct)));
  return rows.map((_, i) => {
    const parts = [fresh[i], loud[i], leading[i], unextended[i]];
    const score = parts.some((p) => p === null) ? null : ((parts as number[]).reduce((a, b) => a + b, 0) / 4) * 100;
    return { fresh: fresh[i], loud: loud[i], leading: leading[i], unextended: unextended[i], score };
  });
}

/** The four readings of a wire row. A flagged price history has no price-derived reading, so its parts read blank
 *  by the wire itself (the tape cells and the return are `null`). */
export function heatInputOf(row: HotRowOut): HeatInput {
  return {
    key: row.key,
    sessions_since: row.clocks.sessions_since,
    rvol20: row.tape.rvol20.value,
    vs_iwm: row.tape.vs_iwm.value,
    since_arm_pct: row.since_arm.ret_pct,
  };
}

/** The heat of each row among `rows`, by row key. */
export function heatByKey(rows: readonly HotRowOut[]): Map<string, Heat> {
  const got = heat(rows.map(heatInputOf));
  return new Map(rows.map((r, i) => [r.key, got[i]]));
}
