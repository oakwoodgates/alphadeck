import { describe, expect, it } from "vitest";

import {
  DEFAULT_THRESHOLD,
  defaultVenn,
  groupsWords,
  LABEL_MAX_FONT,
  LABEL_MAX_LINES,
  LABEL_MIN_FONT,
  labelFit,
  lineWidth,
  moveFocus,
  nameFits,
  neighborsOf,
  pairWords,
  setDefs,
  sharePct,
  sharedDots,
  validPicks,
  visiblePairs,
  workLine,
} from "../mapView";
import { overlapFixture } from "./fixture";

const d = overlapFixture();

describe("the threshold's lines", () => {
  it("defaults to 3 and only hides lines, never a bubble", () => {
    expect(DEFAULT_THRESHOLD).toBe(3);
    expect(visiblePairs(d, 1)).toHaveLength(4);
    expect(visiblePairs(d, 2).map((p) => [p.a, p.b])).toEqual([[0, 1]]);
    expect(visiblePairs(d, 3).map((p) => [p.a, p.b])).toEqual([[0, 1]]);
    expect(visiblePairs(d, 4)).toEqual([]);
  });

  it("words a hovered line with its percent of the combined names", () => {
    const p = d.pairs[0]; // Alpha-Beta share 3 of 4 + 4 - 3 = 5 names
    expect(sharePct(d, p)).toBe(60);
    expect(pairWords(d, p)).toBe("Alpha · Beta: 3 shared names · 60% of their combined names");
    expect(pairWords(d, d.pairs[1])).toContain("1 shared name ·");
  });

  it("grows the stroke with the shared count, capped", () => {
    expect(lineWidth(1)).toBeLessThan(lineWidth(5));
    expect(lineWidth(500)).toBe(7);
  });

  it("words the groups for a threshold, counts only", () => {
    expect(groupsWords(d, 1)).toBe("At 1+ shared names: one group of 4 cards; 1 card with no line");
    expect(groupsWords(d, 3)).toBe("At 3+ shared names: one group of 2 cards; 3 cards with no line");
    expect(groupsWords(d, 4)).toBe("At 4+ shared names: no two cards share that many; 5 cards with no line");
    const many = overlapFixture({ groups: [{ threshold: 3, sizes: [26, 6, 5, 2, 2], no_line: 24 }] });
    expect(groupsWords(many, 3)).toBe("At 3+ shared names: groups of 26, 6, 5, 2 and 2 cards; 24 cards with no line");
  });
});

describe("a bubble's neighbors", () => {
  it("lists the cards it shares names with, most shared first, with the tickers", () => {
    const nbs = neighborsOf(d, 2); // Gamma: Alpha 1 (DDD), Beta 1 (DDD), Delta 1 (GGG)
    expect(nbs.map((n) => [d.cards[n.card].name, n.shared])).toEqual([
      ["Alpha", 1],
      ["Beta", 1],
      ["Delta", 1],
    ]);
    const alpha = neighborsOf(d, 0);
    expect(alpha[0]).toMatchObject({ card: 1, shared: 3 });
    expect(alpha[0].companies.map((i) => d.companies[i].ticker)).toEqual(["BBB", "CCC", "DDD"]);
    expect(neighborsOf(d, 4)).toEqual([]);
  });
});

describe("the shared-company dots", () => {
  it("puts a company on two cards at the midpoint and one on three at the center", () => {
    const dots = sharedDots(d);
    const at = (t: string) => dots.find((x) => d.companies[x.company].ticker === t)!;
    expect(dots.map((x) => d.companies[x.company].ticker)).toEqual(["BBB", "CCC", "DDD", "GGG"]);
    expect(at("BBB")).toMatchObject({ x: 260, y: 200, cards: [0, 1] }); // midpoint of (200,200) and (320,200)
    expect(at("GGG")).toMatchObject({ x: 260, y: 370, cards: [2, 3] });
    expect(at("DDD").x).toBeCloseTo((200 + 320 + 260) / 3, 9);
    expect(at("DDD").y).toBeCloseTo((200 + 200 + 320) / 3, 9);
  });

  it("spreads dots that share the same cards on a short deterministic offset around the midpoint", () => {
    const dots = sharedDots(d);
    const b = dots.find((x) => d.companies[x.company].ticker === "BBB")!;
    const c = dots.find((x) => d.companies[x.company].ticker === "CCC")!;
    const off = Math.hypot(c.x - 260, c.y - 200);
    expect(off).toBeGreaterThan(0);
    expect(off).toBeLessThan(8);
    expect(b).toMatchObject({ x: 260, y: 200 });
    expect(sharedDots(d)).toEqual(dots); // deterministic
  });
});

describe("the bubbles' labels and keyboard moves", () => {
  it("shows a name on its bubble only when it fits", () => {
    expect(nameFits("Alpha", 40)).toBe(true);
    expect(nameFits("A card with a very long name", 20)).toBe(false);
  });

  it("wraps a long name at word boundaries inside the bubble, at most three lines", () => {
    const name = "Synthetic Widget Components And Assembly Supply Chain Study";
    const fit = labelFit(name, 60)!;
    expect(fit).not.toBeNull();
    expect(fit.lines.length).toBeGreaterThan(1);
    expect(fit.lines.length).toBeLessThanOrEqual(LABEL_MAX_LINES);
    expect(fit.fontSize).toBeGreaterThanOrEqual(LABEL_MIN_FONT);
    expect(fit.fontSize).toBeLessThanOrEqual(LABEL_MAX_FONT);
    // broken only between words, and nothing lost when no ellipsis was needed
    const words = new Set(name.split(" "));
    for (const ln of fit.lines) for (const w of ln.replace("…", "").split(" ").filter(Boolean)) expect(words.has(w) || ln.endsWith("…")).toBe(true);
    if (!fit.lines.at(-1)!.endsWith("…")) expect(fit.lines.join(" ")).toBe(name);
  });

  it("puts an ellipsis on the last line when the name overflows three lines at the floor", () => {
    const name = "An Unusually Long Synthetic Card Name About Many Different Interlinked Supply Relationships And Their Follow On Effects";
    const fit = labelFit(name, 40)!;
    expect(fit).not.toBeNull();
    expect(fit.fontSize).toBe(LABEL_MIN_FONT);
    expect(fit.lines).toHaveLength(LABEL_MAX_LINES);
    expect(fit.lines.at(-1)!.endsWith("…")).toBe(true);
    expect(fit.lines.every((l) => l.length >= 1)).toBe(true);
  });

  it("uses a larger font and one line for a short name in a big bubble", () => {
    const fit = labelFit("Alpha", 60)!;
    expect(fit.lines).toEqual(["Alpha"]);
    expect(fit.fontSize).toBe(LABEL_MAX_FONT);
  });

  it("never goes below the font floor, and a bubble too small for a line of 8 characters has no label", () => {
    for (const r of [20, 30, 45, 70]) {
      const fit = labelFit("A Synthetic Name That Goes On And On For A While", r);
      if (fit) expect(fit.fontSize).toBeGreaterThanOrEqual(LABEL_MIN_FONT);
    }
    expect(labelFit("A Synthetic Name That Goes On And On For A While", 14)).toBeNull();
    expect(labelFit("X", 3)).toBeNull();
  });

  it("moves to the nearest bubble in the arrow's direction, and stays when none lies that way", () => {
    expect(moveFocus(d, 0, "ArrowRight")).toBe(1);
    expect(moveFocus(d, 1, "ArrowLeft")).toBe(0);
    expect(moveFocus(d, 0, "ArrowDown")).toBe(2);
    expect(moveFocus(d, 2, "ArrowDown")).toBe(3);
    expect(moveFocus(d, 3, "ArrowDown")).toBe(3);
    expect(moveFocus(d, 4, "ArrowUp")).toBe(4); // the strip's card has no place on the map
  });
});

describe("the Venn's sets", () => {
  it("offers cards, links, supersectors and business types, each with its companies once", () => {
    const defs = setDefs(d);
    const kinds = [...new Set(defs.map((s) => s.kind))];
    expect(kinds).toEqual(["card", "link", "supersector", "business_type"]);
    const alpha = defs.find((s) => s.key === "card:t-alpha")!;
    expect(alpha.members).toEqual(["sec-0", "sec-1", "sec-2", "sec-3"]);
    const core = defs.find((s) => s.key === "link:t-alpha:Core")!;
    expect(core.label).toBe("Core · Alpha");
    expect(core.members).toEqual(["sec-0", "sec-1", "sec-2", "sec-3"]);
    expect(defs.find((s) => s.key === "link:t-alpha:Edge")!.members).toEqual(["sec-2"]);
    const tech = defs.find((s) => s.key === "supersector:technology")!;
    expect(tech.members).toEqual(["sec-0", "sec-1", "sec-7"]); // across every card, once
    expect(defs.find((s) => s.key === "supersector:")!.label).toBe("Unclassified");
  });

  it("defaults to the pair sharing the most names, and keeps valid picks", () => {
    expect(defaultVenn(d)).toEqual(["card:t-alpha", "card:t-beta"]);
    const none = overlapFixture({ pairs: [] });
    expect(defaultVenn(none)).toEqual(["card:t-alpha", "card:t-beta"]); // the first two when nothing is shared
    const defs = setDefs(d);
    expect(validPicks(d, defs, ["card:t-gamma", "card:t-delta", "card:t-nope"])).toEqual(["card:t-gamma", "card:t-delta"]);
    expect(validPicks(d, defs, ["card:t-gamma", "card:t-nope"])).toEqual(["card:t-alpha", "card:t-beta"]);
    expect(defaultVenn(overlapFixture({ cards: [d.cards[0]] }))).toEqual([]);
  });
});

describe("the quiet line", () => {
  it("carries every count the plan lists", () => {
    const line = workLine(d);
    for (const part of [
      "5 cards counted (1 created after the as-of left out)",
      "20 basket rows",
      "Context, ETFs and Bench left out (3 basket rows)",
      "1 basket row without a resolved security not matched",
      "9 companies in view, 4 on 2+ cards",
      "4 pairs of cards sharing a name",
      "Sector and business type come from today's master",
      "computed in 0.9 s",
      "as of Nov 4, 2026",
    ]) {
      expect(line).toContain(part);
    }
    expect(line).not.toContain("point-in-time only");
  });

  it("notes the roster history before Sep 15, 2026 and any card on today's roster", () => {
    const past = workLine(overlapFixture({ asof: "2026-08-01", roster_fallback_cards: 2, cache: "hit", include_silent: true }));
    expect(past).toContain("rosters are point-in-time only from Sep 15, 2026");
    expect(past).toContain("2 cards with no roster snapshot by the as-of");
    expect(past).toContain("Context, ETFs and Bench rows included");
    expect(past).toContain("cached");
  });
});
