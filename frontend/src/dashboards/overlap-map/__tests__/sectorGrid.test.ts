import { describe, expect, it } from "vitest";

import { CELL_MIN_R, buildSectorGrid, cellRadius } from "../sectorGrid";
import { COMPANIES, overlapFixture } from "./fixture";

// The Sectors grid (docs/dashboards/overlap-map.md): columns ordered by the cards each appears on with Other and
// Unclassified last; rows by each card's largest column, then that column's share of the card, then name.

const names = overlapFixture().cards.map((c) => c.name);

describe("the column order", () => {
  it("orders supersectors by cards, with Other then Unclassified last", () => {
    const g = buildSectorGrid(names, COMPANIES, "supersector");
    expect(g.columns.map((c) => c.label)).toEqual(["Materials", "Technology", "Healthcare", "Other", "Unclassified"]);
    // header counts: cards the column appears on, companies once across cards
    const by = Object.fromEntries(g.columns.map((c) => [c.label, [c.cards, c.companies]]));
    expect(by).toEqual({
      Materials: [3, 2], // Alpha, Beta, Gamma; CCC and DDD each once
      Technology: [3, 3], // Alpha, Beta (BBB sits on both) and Delta; AAA BBB HHH
      Healthcare: [1, 1],
      Other: [2, 1], // GGG on Gamma and Delta, once
      Unclassified: [2, 2], // EEE on Beta, SOL on Solo
    });
  });

  it("never drops a company with no sector: it is Unclassified", () => {
    const g = buildSectorGrid(names, COMPANIES, "business_type");
    const unc = g.columns.find((c) => c.label === "Unclassified");
    expect(unc?.companies).toBe(2);
    expect(g.columns.at(-1)?.key).toBe("");
    expect(g.columns.at(-2)?.key).toBe("other");
  });
});

describe("the row order", () => {
  it("sorts by the largest column in column order, then its share, then name", () => {
    const g = buildSectorGrid(names, COMPANIES, "supersector");
    // Alpha: tech 2, materials 2 (tie -> the earlier column, materials), share 1/2
    // Beta: materials 2 of 4 -> share 1/2 (name after Alpha)
    // Gamma: three singletons, the earliest column is materials, share 1/3
    // Delta: tech and other tie at 1 -> the earlier column, tech (a later column than materials), share 1/2
    // Solo: unclassified, the last column
    expect(g.rows.map((r) => names[r.card])).toEqual(["Alpha", "Beta", "Gamma", "Delta", "Solo"]);
  });

  it("counts a card's sectors and keeps each company once per cell", () => {
    const g = buildSectorGrid(names, COMPANIES, "supersector");
    const alpha = g.rows.find((r) => names[r.card] === "Alpha")!;
    expect(alpha.sectors).toBe(2);
    expect(alpha.total).toBe(4);
    expect(alpha.cells.materials).toEqual([2, 3]); // CCC (on two links of Alpha) counts once
    expect(alpha.cells.technology).toEqual([0, 1]);
  });

  it("puts a card with no companies last, A to Z", () => {
    const cards = ["Zed", "Alpha", "Empty", "Mid"];
    const onlyAlpha = COMPANIES.slice(0, 2).map((c) => ({ ...c, rows: c.rows.filter((r) => r.card === 1) }));
    const g = buildSectorGrid(cards, onlyAlpha, "supersector");
    expect(g.rows.map((r) => cards[r.card])).toEqual(["Alpha", "Empty", "Mid", "Zed"]);
  });
});

describe("cell size", () => {
  it("is area-proportional, with the biggest cell at the maximum", () => {
    expect(cellRadius(4, 4, 12)).toBe(12);
    expect(cellRadius(1, 4, 12)).toBeCloseTo(6, 6);
    expect(cellRadius(0, 4, 12)).toBe(0);
  });

  it("keeps a one-company cell visible: a floor of 3 px, area proportional above it", () => {
    expect(CELL_MIN_R).toBe(3);
    expect(cellRadius(1, 64, 16)).toBe(3); // 16 * sqrt(1/64) = 2 px, raised to the floor
    expect(cellRadius(16, 64, 16)).toBeCloseTo(8, 6);
    expect(cellRadius(64, 64, 16)).toBe(16);
  });
});
