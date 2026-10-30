import { describe, expect, it } from "vitest";

import { HEAT_PARTS, heat, heatByKey, heatInputOf, percentiles, type HeatInput } from "../heat";
import fixtures from "./heat-fixtures.json";
import { cell, flaggedTape, row, since, tape, clocks } from "./fixtures";

// The page's heat is a pure mirror of the backend's (backend/dashboards/hot_board.py::heat). Both are asserted
// against the SAME fixture file, so the two implementations of the rule cannot drift.

type Case = {
  name: string;
  rows: HeatInput[];
  expected: Record<string, Record<(typeof HEAT_PARTS)[number] | "score", number | null>>;
};

describe("heat — the cases shared with the backend", () => {
  for (const c of fixtures.cases as Case[]) {
    it(c.name, () => {
      const got = heat(c.rows);
      expect(got).toHaveLength(c.rows.length);
      c.rows.forEach((r, i) => {
        for (const part of [...HEAT_PARTS, "score"] as const) {
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

describe("heat — the direction of each part", () => {
  const base = { sessions_since: 1, rvol20: 1, vs_iwm: 0, since_arm_pct: 0 };
  it("ranks fewer sessions, a louder tape, a higher lead and a smaller move higher", () => {
    const [old, fresh] = heat([
      { key: "old", ...base, sessions_since: 9 },
      { key: "new", ...base, sessions_since: 0 },
    ]);
    expect([fresh.fresh, old.fresh]).toEqual([1, 0]);
    const [quiet, loud] = heat([
      { key: "q", ...base, rvol20: 0.8 },
      { key: "l", ...base, rvol20: 3.1 },
    ]);
    expect([loud.loud, quiet.loud]).toEqual([1, 0]);
    const [lag, lead] = heat([
      { key: "g", ...base, vs_iwm: -4 },
      { key: "d", ...base, vs_iwm: 6 },
    ]);
    expect([lead.leading, lag.leading]).toEqual([1, 0]);
    const [far, near] = heat([
      { key: "f", ...base, since_arm_pct: 40 },
      { key: "n", ...base, since_arm_pct: 2 },
    ]);
    expect([near.unextended, far.unextended]).toEqual([1, 0]);
  });

  it("gives a row missing any part no heat, and reports the parts it has", () => {
    const got = heat([
      { key: "full", sessions_since: 1, rvol20: 2, vs_iwm: 3, since_arm_pct: 4 },
      { key: "no-bar", sessions_since: 1, rvol20: 2, vs_iwm: 3, since_arm_pct: null },
      { key: "no-tape", sessions_since: null, rvol20: null, vs_iwm: null, since_arm_pct: null },
    ]);
    expect(got[0].score).not.toBeNull();
    expect(got[1].score).toBeNull();
    expect(got[1].fresh).not.toBeNull();
    expect(got[2]).toEqual({ fresh: null, loud: null, leading: null, unextended: null, score: null });
    expect(heat([])).toEqual([]);
  });
});

describe("heat of wire rows", () => {
  it("reads the four readings off the row", () => {
    const r = row({
      clocks: clocks({ sessions_since: 3 }),
      tape: tape({ rvol20: cell(2.5), vs_iwm: cell(-1.5) }),
      since_arm: since({ ret_pct: 7.5 }),
    });
    expect(heatInputOf(r)).toEqual({ key: r.key, sessions_since: 3, rvol20: 2.5, vs_iwm: -1.5, since_arm_pct: 7.5 });
  });

  it("gives a flagged price history no heat (its price readings are blank on the wire)", () => {
    const flagged = row({ tape: flaggedTape(), since_arm: since({ ret_pct: null, gap: "flagged" }) });
    const ok = row();
    const got = heatByKey([flagged, ok]);
    expect(got.get(flagged.key)?.score).toBeNull();
    expect(got.get(ok.key)?.score).not.toBeNull();
  });

  it("is recomputed on the rows given: the same row scores differently among another set", () => {
    const a = row({ clocks: clocks({ sessions_since: 0 }), tape: tape({ rvol20: cell(2), vs_iwm: cell(8) }), since_arm: since({ ret_pct: 1 }) });
    const b = row({ clocks: clocks({ sessions_since: 2 }), tape: tape({ rvol20: cell(1), vs_iwm: cell(3) }), since_arm: since({ ret_pct: 4 }) });
    const c = row({ clocks: clocks({ sessions_since: 5 }), tape: tape({ rvol20: cell(3), vs_iwm: cell(-2) }), since_arm: since({ ret_pct: -1 }) });
    const three = heatByKey([a, b, c]).get(a.key)?.score;
    const two = heatByKey([a, b]).get(a.key)?.score;
    expect(three).toBeCloseTo(75, 9);
    expect(two).toBeCloseTo(100, 9);
  });
});
