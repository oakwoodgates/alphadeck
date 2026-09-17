import type { PooledMetric, PooledSlice, PooledStat } from "../api/hooks";

// Pure display logic for the POOLED panel (the rows.ts idiom: unit-tested, no React).
//
// Two rules run through all of it.
//
// **Nothing here invents a number.** It formats what the backend computed and it never derives a new
// statistic — in particular it does NOT subtract a null's median from the actual to show a "gap". That
// subtraction is the number a reader would quote, and a difference of two medians has no n and no
// dispersion of its own; rendering it as a headline would manufacture a confidence the run never
// measured. The four figures sit side by side and are read across the row, which is what the
// backend-authored banner tells the reader to do.
//
// **An insufficient n is marked, never hidden.** A slice below the gate still renders (pruning hides,
// it never vanishes) with its count visible and its tone quiet, because "we looked and there were
// three" is a finding and an absent row is not.

/** One `Stat` as text: the median, then its n. "—" when the statistic has no values at all. */
export function fmtStat(s: PooledStat | null | undefined): string {
  if (!s || s.n === 0 || s.median === null || s.median === undefined) return "—";
  const pct = (s.median * 100).toFixed(1);
  return `${s.median > 0 ? "+" : ""}${pct}%`;
}

/** The tone class for a formatted stat — positive/negative/neutral, or none when unknowable. */
export function statTone(s: PooledStat | null | undefined): string {
  if (!s || s.n === 0 || s.median === null || s.median === undefined) return "";
  return s.median > 0 ? "pos" : s.median < 0 ? "neg" : "";
}

/** The sample-size caption for a stat — "n=41". Empty when there is nothing to size. */
export function fmtN(s: PooledStat | null | undefined): string {
  if (!s || s.n === 0) return "";
  return `n=${s.n}`;
}

/** The four columns of a pooled metric, in the order they must be READ: the actual first, then the
 *  three things that make it interpretable. The labels carry the meaning, so they live here (one
 *  place) rather than being spelled out at each render site. */
export type MetricColumn = {
  id: "actual" | "excess" | "vs_timing" | "vs_name";
  label: string;
  title: string;
  stat: PooledStat;
};

export function metricColumns(m: PooledMetric): MetricColumn[] {
  return [
    {
      id: "actual",
      label: "actual",
      title:
        "the realized median return over the hold window. On a universe assembled with hindsight this figure alone is not evidence — read it against the three beside it.",
      stat: m.actual,
    },
    {
      id: "excess",
      label: "excess over basket",
      title:
        "the same window measured against the thesis basket's own equal-weight move — the theme's drift stripped out, so what is left is the name-and-timing choice.",
      stat: m.excess,
    },
    {
      id: "vs_timing",
      label: "vs timing null",
      title:
        "the SAME name entered on a randomly drawn session instead — the timing null. If the algorithm's timing carries no edge, the actual sits on top of this.",
      stat: m.vs_timing,
    },
    {
      id: "vs_name",
      label: "vs name null",
      title:
        "a randomly drawn OTHER name from the same roster on the same day — the name-selection null.",
      stat: m.vs_name,
    },
  ];
}

/** The slice table's key columns, in a fixed order so every row reads down the same lanes. The names
 *  are ALGORITHM dimensions by construction — there is no thesis column here and never will be (see
 *  the pooled report's own docstring: slicing outcomes by thesis tests the idea, not the timing). */
export const SLICE_COLUMNS: readonly { id: string; label: string; title: string }[] = [
  {
    id: "key1_source",
    label: "Key 1",
    title: "which trigger family turned the first lock — insider, activist, catalyst, revenue, theme",
  },
  {
    id: "confirmation_grade",
    label: "Confirmation",
    title: "the confirmation grade at the arm — volume-backed core vs momentum-only flip",
  },
  {
    id: "co_arm_bucket",
    label: "Co-arm",
    title:
      "how many members armed together that thesis-session: alone | 2-3 | 4-7 | 8+. Episodes in a burst are not independent observations.",
  },
  {
    id: "close_reason",
    label: "Closed by",
    title: "why the arm run ended",
  },
];

/** Slices ordered largest-n first, so the rows that can carry weight are read first; ties fall back to
 *  the key text for a stable order. A gated slice keeps its place in the list — it is sorted, not
 *  filtered. */
export function sortedSlices(slices: readonly PooledSlice[]): PooledSlice[] {
  return [...slices].sort(
    (a, b) => b.n - a.n || JSON.stringify(a.key ?? {}).localeCompare(JSON.stringify(b.key ?? {})),
  );
}

/** A mix map ({value: count}) as rows ordered by count, then name — the firing diagnostics' shape.
 *  Percentages are of the TOTAL episodes, which is why the total is passed in rather than summed: a
 *  mix whose values do not cover every episode must not silently renormalize to 100%. */
export function mixRows(
  mix: Record<string, number> | undefined,
  total: number,
): { label: string; n: number; pct: number | null }[] {
  const entries = Object.entries(mix ?? {});
  entries.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return entries.map(([label, n]) => ({
    label,
    n,
    pct: total > 0 ? n / total : null,
  }));
}

/** "82%" from 0.8231 — the co-arm headline. Null in, "—" out (never "NaN%"). */
export function fmtPct(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return `${(x * 100).toFixed(0)}%`;
}

/** The quiet line that appears when the timing null had almost no sessions to draw from. A short
 *  window makes that null VACUOUS and the failure is silent otherwise: every draw prices a near-empty
 *  forward window, `vs_timing` comes back near the actual, and the row reads "the algorithm ties with
 *  chance" when it means "there was no chance to compare against". Null when there is nothing to warn
 *  about, so the line marks an exception rather than decorating every run (#7). */
export function timingNullCaveat(sessions: number, episodes: number): string | null {
  if (episodes === 0) return null;
  if (sessions >= 30) return null;
  return (
    `The timing null drew from ${sessions} session${sessions === 1 ? "" : "s"} — too few for the ` +
    `comparison to mean much. A vs-timing figure close to the actual here says the window was short, ` +
    `not that the timing is worthless.`
  );
}
