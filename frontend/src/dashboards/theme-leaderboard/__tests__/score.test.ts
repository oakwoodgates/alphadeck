import { describe, expect, it } from "vitest";

import { PARTS, percentiles, score, scoreByKey, scoreInputOf, type ScoreInput } from "../score";
import fixtures from "./score-fixtures.json";
import { CARD_A, CARD_B, CARD_C, CARD_D, adoption, breadth, card, insiders, keys, sample, tape } from "./fixtures";

// The page's score is a pure mirror of the backend's (backend/dashboards/theme_leaderboard.py::score). Both are
// asserted against the SAME fixture file, so the two implementations of the rule cannot drift.

type Case = {
  name: string;
  rows: ScoreInput[];
  expected: Record<string, Record<(typeof PARTS)[number] | "score", number | null>>;
};

describe("score — the cases shared with the backend", () => {
  for (const c of fixtures.cases as Case[]) {
    it(c.name, () => {
      const got = score(c.rows);
      expect(got).toHaveLength(c.rows.length);
      c.rows.forEach((r, i) => {
        for (const part of [...PARTS, "score"] as const) {
          const want = c.expected[r.key][part];
          if (want === null) expect(got[i][part], `${r.key} ${part}`).toBeNull();
          else expect(got[i][part], `${r.key} ${part}`).toBeCloseTo(want, 9);
        }
      });
    });
  }
});

describe("percentiles", () => {
  it("is the mid-rank over n - 1, a blank stays blank", () => {
    expect(percentiles([10, 30, 20, null])).toEqual([0, 1, 0.5, null]);
    expect(percentiles([1, 1, 3])).toEqual([0.25, 0.25, 1]);
    expect(percentiles([5])).toEqual([0.5]);
    expect(percentiles([null, null])).toEqual([null, null]);
    expect(percentiles([])).toEqual([]);
  });

  it("reads zero and negative zero as equal", () => {
    expect(percentiles([0, -0, 1])).toEqual([0.25, 0.25, 1]);
  });
});

describe("score — the direction of each part", () => {
  const lo: ScoreInput = { key: "lo", tape: 0, breadth: 10, keys: 0, adoption: -5, insiders: 0 };
  const hi: ScoreInput = { key: "hi", tape: 1, breadth: 11, keys: 1, adoption: 5, insiders: 1 };

  it("ranks a bigger tape, breadth, key count, adoption slope and buyer count higher", () => {
    const [a, b] = score([lo, hi]);
    expect(PARTS.map((p) => a[p])).toEqual([0, 0, 0, 0, 0]);
    expect(PARTS.map((p) => b[p])).toEqual([1, 1, 1, 1, 1]);
    expect([a.score, b.score]).toEqual([0, 100]);
  });

  it("scores nothing for no cards", () => {
    expect(score([])).toEqual([]);
  });
});

describe("score of wire cards", () => {
  it("reads the five parts off the card", () => {
    const c = card({
      tape: tape({ value: 2.5 }),
      breadth: breadth({ pct: 55 }),
      keys: keys({ turned: 2 }),
      adoption: adoption({ slope_pct: -12 }),
      insiders: insiders({ buyers: 4 }),
    });
    expect(scoreInputOf(c)).toEqual({ key: c.thesis_id, tape: 2.5, breadth: 55, keys: 2, adoption: -12, insiders: 4 });
  });

  it("gives a card missing a part no score (a thin tape, no call, an adoption gap, no Form 4 reading)", () => {
    const got = scoreByKey(sample());
    expect(got.get(CARD_A)?.score).toBeCloseTo(100, 9);
    expect(got.get(CARD_B)?.score).toBeCloseTo((0.5 + 1 / 3 + 0.5) / 5 * 100, 9);
    expect(got.get(CARD_C)?.score).toBeNull(); // no adoption reading yet
    expect(got.get(CARD_D)?.score).toBeNull(); // no tape, no call, no adoption, no Form 4 reading
    // the parts C does have still rank the others: its 60% breadth sits between A's 80 and B's 40
    expect(got.get(CARD_C)?.breadth).toBeCloseTo(2 / 3, 9);
    expect(got.get(CARD_D)?.tape).toBeNull();
  });

  it("is recomputed on the cards given: the same card scores differently among another set", () => {
    const [a, b, c, d] = sample();
    expect(scoreByKey([a, b, c, d]).get(CARD_B)?.score).toBeCloseTo(26.666666666666668, 9);
    expect(scoreByKey([a, b]).get(CARD_A)?.score).toBeCloseTo(100, 9);
    expect(scoreByKey([a, b]).get(CARD_B)?.score).toBeCloseTo(0, 9);
    // a lone card has nothing to compare with
    expect(scoreByKey([a]).get(CARD_A)?.score).toBeCloseTo(50, 9);
  });
});
