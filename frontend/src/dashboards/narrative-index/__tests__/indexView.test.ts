import { describe, expect, it } from "vitest";

import { createdIndex, linePath, nextSort, sinceWords, sortCards, yDomain } from "../indexView";
import type { NarrativeCardOut } from "../useNarrativeIndex";

// The Narrative Index's pure helpers: the sort (— last both ways, ties by name), the sort flip, the since-creation
// words, the y range around 100 and the creation marker's position.

const card = (name: string, ret: number | null, over: Partial<NarrativeCardOut> = {}): NarrativeCardOut => ({
  thesis_id: `t-${name}`,
  thesis_name: name,
  state: null,
  created: null,
  created_in_window: false,
  created_base: null,
  names: 1,
  priced: 1,
  unresolved: 0,
  silent_left_out: 0,
  ret,
  vs_spy: ret,
  since_created: null,
  sessions_flat: 0,
  points: [100],
  ...over,
});

describe("indexView", () => {
  it("sorts a number highest first with — last both ways, ties by name", () => {
    const cards = [card("b", 1), card("a", 1), card("c", null), card("d", 5)];
    const names = (s: Parameters<typeof sortCards>[1]) => sortCards(cards, s).map((c) => c.thesis_name);
    expect(names({ key: "ret", dir: "desc" })).toEqual(["d", "a", "b", "c"]);
    expect(names({ key: "ret", dir: "asc" })).toEqual(["a", "b", "d", "c"]);
    expect(names({ key: "name", dir: "asc" })).toEqual(["a", "b", "c", "d"]);
    expect(names({ key: "name", dir: "desc" })).toEqual(["d", "c", "b", "a"]);
  });

  it("flips the active sort and starts a new one in its natural direction", () => {
    expect(nextSort({ key: "name", dir: "asc" }, "name")).toEqual({ key: "name", dir: "desc" });
    expect(nextSort({ key: "name", dir: "asc" }, "ret")).toEqual({ key: "ret", dir: "desc" });
    expect(nextSort({ key: "ret", dir: "desc" }, "name")).toEqual({ key: "name", dir: "asc" });
  });

  it("words since creation by where the creation day falls", () => {
    expect(sinceWords(card("x", 1, { created_in_window: true, created: "2026-10-27", since_created: 2.5 }))).toBe(
      "since created +2.5%",
    );
    expect(sinceWords(card("x", 1, { created: "2026-01-02" }))).toBe("created before the window");
    expect(sinceWords(card("x", 1))).toBe("created —");
  });

  it("keeps 100 inside the y range, padded, and draws left to right", () => {
    const [lo, hi] = yDomain([[105, 110], null, [102]]);
    expect(lo).toBeLessThan(100);
    expect(hi).toBeGreaterThan(110);
    expect(linePath([100, 110], [100, 110], 10, 10)).toBe("M0.0,10.0 L10.0,0.0");
  });

  it("places the creation marker on its session, or nowhere outside the window", () => {
    const dates = ["2026-10-26", "2026-10-27", "2026-10-28"];
    expect(createdIndex(dates, "2026-10-27")).toBe(1);
    expect(createdIndex(dates, null)).toBeNull();
    expect(createdIndex(dates, "2026-01-02")).toBeNull();
  });
});
