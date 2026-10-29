import { describe, expect, it } from "vitest";

import {
  AUTHOR_TAG,
  ageGlyph,
  ageWords,
  columnTag,
  filingWords,
  filterCards,
  fmtDay,
  linkWords,
  nodeWords,
  plural,
  roleWords,
  sideWords,
  typeLabel,
  workLine,
} from "../mapView";
import { CARDS, counts, node, payload } from "./fixtures";

// The Chain Map's pure words: the neutral strings built from the payload (the 50-day side, the filing line, the
// node tooltip, the column tags, the work line). Numbers come from the server; this only words them.

describe("small formats", () => {
  it("words dates, counts and the picker filter", () => {
    expect(fmtDay("2026-10-28")).toBe("Oct 28, 2026");
    expect(fmtDay(null)).toBe("—");
    expect(plural(1, "link", "links")).toBe("1 link");
    expect(plural(1234, "name", "names")).toBe("1,234 names");
    expect(filterCards(CARDS.cards, "nuc").map((c) => c.name)).toEqual(["Nuclear"]);
    expect(filterCards(CARDS.cards, "  ").map((c) => c.name)).toEqual(["Copper", "Grid", "Nuclear"]);
  });

  it("tags a description by who wrote it, the Cockpit's own words", () => {
    expect(AUTHOR_TAG).toEqual({
      system_drafted: "model draft",
      operator_set: "model draft",
      operator_edited: "your words",
    });
  });

  it("reads the links of a name, a null link as No link", () => {
    expect(linkWords(["Reactors", "Fuel"])).toBe("Reactors · Fuel");
    expect(linkWords([null])).toBe("No link");
  });
});

describe("a node's glyphs", () => {
  it("shows the business type's short label, nothing when unclassified", () => {
    expect(typeLabel(node("A", { business_type: "software_it" }))).toBe("software/IT");
    expect(typeLabel(node("A", { business_type: "miner" }))).toBe("miner");
    expect(typeLabel(node("A", { business_type: null }))).toBe("");
  });

  it("shows the age in whole days, a dash with no filing", () => {
    expect(ageGlyph(node("A"))).toBe("3d");
    expect(ageGlyph(node("A", { last_filing: { date: "2026-10-28", age_days: 0, form: "4", headline: "x" } }))).toBe("0d");
    expect(ageGlyph(node("A", { last_filing: null }))).toBe("—");
    expect(ageWords(0)).toBe("on the as-of day");
    expect(ageWords(1)).toBe("1 day before the as-of");
    expect(ageWords(12)).toBe("12 days before the as-of");
  });
});

describe("sideWords", () => {
  it("words the 50-day side with its percent", () => {
    expect(sideWords(node("A"))).toBe("above its 50-day average (+4.2%)");
    expect(sideWords(node("A", { side_50d: "below", pct_vs_50d: -1.5 }))).toBe("below its 50-day average (-1.5%)");
    expect(sideWords(node("A", { side_50d: "at", pct_vs_50d: 0 }))).toBe("at its 50-day average");
  });

  it("says why there is none: a flagged price history, no security, no reading", () => {
    expect(sideWords(node("A", { side_50d: null, flag: "mixed-scale price history" }))).toBe(
      "50-day side left out: mixed-scale price history",
    );
    expect(sideWords(node("A", { security_id: null, side_50d: null }))).toBe("no resolved security");
    expect(sideWords(node("A", { side_50d: null, pct_vs_50d: null }))).toBe(
      "no 50-day reading (no price, or fewer than 50 bars)",
    );
  });
});

describe("filingWords", () => {
  it("words the newest filing with its date and age", () => {
    expect(filingWords(node("A"))).toBe("last filing: 8-K · Oct 25, 2026 (3 days before the as-of) · 8-K · 9.01");
  });

  it("says 'none stored' as a statement, and why a company with no security has none", () => {
    expect(filingWords(node("A", { last_filing: null }))).toBe("no filing stored in the last year");
    expect(filingWords(node("A", { last_filing: null }), 90)).toBe("no filing stored in the last 90 days");
    expect(filingWords(node("A", { security_id: null, last_filing: null }))).toBe(
      "no resolved security, so no filings are read",
    );
  });
});

describe("nodeWords and roleWords", () => {
  it("joins the name's identity, links, role, type, side and filing for the tooltip", () => {
    const n = node("SMR", { role: "counter_case", links: ["Reactors", "Fuel"] });
    expect(roleWords(n)).toBe("counter-case: Loses if the thesis plays out, or wins if it fails.");
    expect(nodeWords(n)).toBe(
      [
        "SMR · SMR Corp",
        "Reactors · Fuel",
        "counter-case: Loses if the thesis plays out, or wins if it fails.",
        "utilities",
        "above its 50-day average (+4.2%)",
        "last filing: 8-K · Oct 25, 2026 (3 days before the as-of) · 8-K · 9.01",
      ].join(" · "),
    );
    expect(roleWords(node("A"))).toBeNull();
  });

  it("leaves out what a name does not have: no name, no role, no type", () => {
    const n = node("UNR", { name: null, business_type: null, security_id: null, side_50d: null, last_filing: null });
    expect(nodeWords(n)).toBe("UNR · Reactors · no resolved security · no resolved security, so no filings are read");
  });
});

describe("columnTag", () => {
  it("tags an old link, No link and the silent columns; a real link shows its descriptor", () => {
    expect(columnTag("link", "make the power")).toBe("make the power");
    expect(columnTag("link", null)).toBe("");
    expect(columnTag("old_link", null)).toBe("not a link today");
    expect(columnTag("no_link", null)).toBe("rows with no link");
    expect(columnTag("silent", null)).toBe("no role · not in the call");
  });
});

describe("workLine", () => {
  it("shows the work: the links, rows, roles, the two glyph coverages, the flagged, the clock and the cache", () => {
    const line = workLine(payload(), false);
    expect(line).toContain("2 links today and 1 roster link that is not on the card today");
    expect(line).toContain("11 roster rows (10 resolved, 1 without a resolved security), 11 companies");
    expect(line).toContain("roles set: payer 1, financier 1, direct read 1, counter-case 2");
    expect(line).toContain("4 companies on the rails");
    expect(line).toContain("a 50-day side for 6 of 11 names (5 without)");
    expect(line).toContain("a filing in the last 365 days for 5 (6 without)");
    expect(line).toContain("1 name drawn grayed for a flagged price history");
    expect(line).toContain("1 Context or Bench name with no role hidden");
    expect(line).toContain("public clock · cached · as of Oct 28, 2026");
  });

  it("words the toggle, a card with no roles, no old link and no flagged name", () => {
    const line = workLine(
      payload({
        flagged: [],
        cache: "computed",
        compute_ms: 4200,
        clock: "record",
        counts: counts({ roles: [], old_links: 0, untagged_silent: 3, on_rails: 0 }),
      }),
      true,
    );
    expect(line).toContain("2 links today ·");
    expect(line).not.toContain("not on the card today");
    expect(line).toContain("no context roles are set on this card");
    expect(line).toContain("names with a flagged price history are drawn grayed");
    expect(line).toContain("3 Context and Bench names with no role shown");
    expect(line).toContain("live read · computed in 4.2 s");
    expect(workLine(payload({ counts: counts({ untagged_silent: 0 }) }), false)).not.toContain("with no role");
  });
});
