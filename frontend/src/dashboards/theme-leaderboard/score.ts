import type { LeaderboardCardOut } from "./useThemeLeaderboard";

// The Theme Leaderboard's score (docs/dashboards/theme-leaderboard.md), the stated formula, recomputed here for the
// cards the page SHOWS (the filters change it). A pure mirror of backend/dashboards/theme_leaderboard.py's `score`:
// the two are tested against the same hand-derived fixtures (__tests__/score-fixtures.json), so they cannot drift.
// Display only: the score is printed under the table and feeds nothing.

/** The five parts, in the order the page words them. A higher value ranks higher for every one. */
export type Part = "tape" | "breadth" | "keys" | "adoption" | "insiders";
export const PARTS: readonly Part[] = ["tape", "breadth", "keys", "adoption", "insiders"];

/** What the score reads of a card: the five parts. `null` = no reading (that part is blank). */
export type ScoreInput = {
  key: string;
  /** The median 30-day return minus IWM's, percentage points. */
  tape: number | null;
  /** The share of the counting names above their 50-day average, percent. */
  breadth: number | null;
  /** Keys turned on the recorded call, 0 to 2. */
  keys: number | null;
  /** The term-mention slope, percent. */
  adoption: number | null;
  /** Distinct open-market buyers in 30 days, summed. */
  insiders: number | null;
};

/** A card's five percentiles (0 to 1; `null` = that part has no reading) and its score (0 to 100; `null` when any
 *  part is missing). */
export type Score = Record<Part, number | null> & { score: number | null };

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

/** The score of each card among `rows` (the cards shown), in order. Each part's percentile is taken among the cards
 *  with that reading (a higher value ranks higher for all five); the score is the mean of the five times 100, and
 *  `null` for a card missing any part. Equal weights (PROPOSED). */
export function score(rows: readonly ScoreInput[]): Score[] {
  const cols = PARTS.map((part) => percentiles(rows.map((r) => r[part])));
  return rows.map((_, i) => {
    const parts = cols.map((c) => c[i]);
    const total = parts.some((p) => p === null) ? null : ((parts as number[]).reduce((a, b) => a + b, 0) / PARTS.length) * 100;
    return { tape: parts[0], breadth: parts[1], keys: parts[2], adoption: parts[3], insiders: parts[4], score: total };
  });
}

/** The five readings of a wire card. A part with no reading is `null` on the wire already (a thin median, no
 *  counted name, no recorded call, an adoption gap, no Form 4 reading). */
export function scoreInputOf(card: LeaderboardCardOut): ScoreInput {
  return {
    key: card.thesis_id,
    tape: card.tape.value,
    breadth: card.breadth.pct,
    keys: card.keys.turned,
    adoption: card.adoption.slope_pct,
    insiders: card.insiders.buyers,
  };
}

/** The score of each card among `cards`, by thesis id. */
export function scoreByKey(cards: readonly LeaderboardCardOut[]): Map<string, Score> {
  const got = score(cards.map(scoreInputOf));
  return new Map(cards.map((c, i) => [c.thesis_id, got[i]]));
}
