import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTERS,
  SIZE_FLOORS,
  capTitle,
  capWords,
  clusterTitle,
  clusterWords,
  dimValues,
  facetCounts,
  filterRows,
  filtersActive,
  groupFacts,
  groupRows,
  pct,
  plural,
  priceWords,
  sharesWords,
  sizeBand,
  toggleCount,
  toggleDiscriminates,
  tone,
  tradeNote,
  triggerTitle,
  usdWords,
  weekLabel,
  workLines,
  type Filters,
} from "../trackerView";
import { R1, R2, R3, R4, ROWS, page, row } from "./fixtures";

// Insider Buying's pure view logic: the size bands, the filters and the counts each chip restates, the grouping by
// filing week / name / card with its header facts, the words, and the work lines. No aggregate lives here.

const withFilters = (over: Partial<Filters>): Filters => ({ ...DEFAULT_FILTERS, ...over });

describe("size bands (PROPOSED: $100K, $1M, $10M)", () => {
  it("band by the lower edges, the edge itself in the higher band, a missing size on its own", () => {
    expect(SIZE_FLOORS).toEqual([100_000, 1_000_000, 10_000_000]);
    expect(sizeBand(99_999)).toBe("lt100k");
    expect(sizeBand(100_000)).toBe("100k");
    expect(sizeBand(999_999)).toBe("100k");
    expect(sizeBand(1_000_000)).toBe("1m");
    expect(sizeBand(9_999_999)).toBe("1m");
    expect(sizeBand(10_000_000)).toBe("10m");
    expect(sizeBand(0)).toBe("lt100k"); // a stated zero is a size
    expect(sizeBand(null)).toBe("none");
  });
});

describe("the words", () => {
  it("states dollars whole below a million, then in millions and billions, rolling over at the unit", () => {
    expect(usdWords(49_200)).toBe("$49,200");
    expect(usdWords(0)).toBe("$0");
    expect(usdWords(999_999)).toBe("$999,999");
    expect(usdWords(1_500_000)).toBe("$1.5M");
    expect(usdWords(999_949_999)).toBe("$999.9M");
    expect(usdWords(999_950_000)).toBe("$1.0B");
    expect(usdWords(-15_000)).toBe("-$15,000");
  });

  it("words shares, prices and the filing lag", () => {
    expect(sharesWords(12000)).toBe("12,000");
    expect(sharesWords(1234.5)).toBe("1,234.5");
    expect(sharesWords(null)).toBe("—");
    expect(priceWords(4.1)).toBe("$4.10");
    expect(priceWords(0.5)).toBe("$0.5000");
    expect(priceWords(null)).toBe("—");
    expect(tradeNote(R1)).toBe("filed 5 days after the trade");
    expect(tradeNote(row("x", { filed_days_after: 1 }))).toBe("filed 1 day after the trade");
    expect(tradeNote(R3)).toBeNull();
  });

  it("words a cluster and who else is in it, and a trigger and whether it is still on the card", () => {
    expect(clusterWords({ buyers: 3, others: [] })).toBe("cluster · 3 buyers");
    expect(clusterTitle(R1.cluster!)).toBe("Also filed within 30 days: Sam Poe (Oct 20)");
    expect(triggerTitle(R1.triggers[0])).toBe("First recorded Oct 27 on Card A · still on the card");
    expect(triggerTitle(R4.triggers[0])).toBe("First recorded Oct 21 on Card A · no longer on the card at the as-of");
    expect(triggerTitle({ ...R1.triggers[0], live: null })).toContain("could not be read");
  });

  it("signs a return and tones only a signed one", () => {
    expect(pct(0.1)).toBe("+10.0%");
    expect(pct(-0.042)).toBe("-4.2%");
    expect(pct(0)).toBe("0.0%");
    expect(pct(null)).toBe("—");
    expect([tone(0.1), tone(-0.042), tone(0), tone(null)]).toEqual(["pos", "neg", "flat", "flat"]);
  });

  it("states the cap as approximate with its tier, or says why it is blank", () => {
    expect(capWords(R1.cap)).toBe("≈ $250.0M small");
    expect(capWords(R3.cap)).toBe("—");
    expect(capTitle(R3.cap)).toContain("withheld, a data flag reaches its price");
    expect(capTitle(R3.cap)).toContain(R3.cap.note!);
    expect(capTitle(R4.cap)).toContain("awaiting shares");
    expect(capTitle(R1.cap)).toContain("Approximate");
  });

  it("labels a week by its Monday", () => {
    expect(weekLabel("2026-10-26")).toBe("Week of Oct 26");
  });
});

describe("the filters and the counts each chip restates", () => {
  it("lists the values present in the page's order, and a dimension with one value offers none", () => {
    expect(dimValues(ROWS, "size")).toEqual(["lt100k", "1m", "10m", "none"]);
    expect(dimValues(ROWS, "role")).toEqual(["officer", "director", "ten_percent_owner", "other"]);
    expect(dimValues(ROWS, "tier")).toEqual(["small", "none"]);
    expect(dimValues(ROWS, "card")).toEqual(["t-a", "t-b"]);
    expect(dimValues([R1], "role")).toEqual(["officer"]);
  });

  it("filters by size band, role (any class), cap tier and card", () => {
    expect(filterRows(ROWS, withFilters({ sizes: new Set(["10m"]) })).map((r) => r.key)).toEqual(["r3"]);
    expect(filterRows(ROWS, withFilters({ sizes: new Set(["none"]) })).map((r) => r.key)).toEqual(["r4"]);
    // a row holding two roles passes on either
    expect(filterRows(ROWS, withFilters({ roles: new Set(["ten_percent_owner"]) })).map((r) => r.key)).toEqual(["r3"]);
    expect(filterRows(ROWS, withFilters({ roles: new Set(["director"]) })).map((r) => r.key)).toEqual(["r3", "r2"]);
    expect(filterRows(ROWS, withFilters({ tiers: new Set(["none"]) })).map((r) => r.key)).toEqual(["r3", "r4"]);
    expect(filterRows(ROWS, withFilters({ card: "t-b" })).map((r) => r.key)).toEqual(["r3"]);
    expect(filterRows(ROWS, withFilters({ card: "t-a" })).map((r) => r.key)).toEqual(["r1", "r2", "r4"]);
  });

  it("filters to clusters and to triggers", () => {
    expect(filterRows(ROWS, withFilters({ clustersOnly: true })).map((r) => r.key)).toEqual(["r1", "r2"]);
    expect(filterRows(ROWS, withFilters({ triggersOnly: true })).map((r) => r.key)).toEqual(["r1", "r4"]);
    expect(filterRows(ROWS, withFilters({ clustersOnly: true, triggersOnly: true })).map((r) => r.key)).toEqual(["r1"]);
  });

  it("restates, per chip, the rows it would show with every OTHER filter applied", () => {
    const f = withFilters({ roles: new Set(["director"]) });
    // sizes among the director rows (r3, r2)
    const sizes = facetCounts(ROWS, f, "size");
    expect(sizes.get("10m")).toBe(1);
    expect(sizes.get("1m")).toBe(1);
    expect(sizes.get("none")).toBe(0); // present in the payload, none among directors
    // the role chips ignore the role filter itself
    const roles = facetCounts(ROWS, f, "role");
    expect(roles.get("officer")).toBe(1);
    expect(roles.get("director")).toBe(2);
    expect(roles.get("ten_percent_owner")).toBe(1);
    // a multi-role row counts under each of its roles
    expect(facetCounts(ROWS, DEFAULT_FILTERS, "role").get("director")).toBe(2);
  });

  it("counts the toggles the same way, and offers one only where the payload holds both sides", () => {
    expect(toggleCount(ROWS, DEFAULT_FILTERS, "clustersOnly")).toBe(2);
    expect(toggleCount(ROWS, withFilters({ card: "t-b" }), "clustersOnly")).toBe(0);
    expect(toggleDiscriminates(ROWS, "clustersOnly")).toBe(true);
    expect(toggleDiscriminates([R1, R2], "clustersOnly")).toBe(false); // every row is one: it cannot discriminate
    expect(toggleDiscriminates([R3], "triggersOnly")).toBe(false); // no row is one
  });

  it("knows when a filter is active", () => {
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    expect(filtersActive(withFilters({ card: "t-a" }))).toBe(true);
    expect(filtersActive(withFilters({ triggersOnly: true }))).toBe(true);
  });
});

describe("grouping", () => {
  it("groups by filing week, newest week first, rows in the server's order", () => {
    const groups = groupRows(ROWS, "week");
    expect(groups.map((g) => [g.key, g.label, g.rows.map((r) => r.key)])).toEqual([
      ["2026-10-26", "Week of Oct 26", ["r1", "r3"]],
      ["2026-10-19", "Week of Oct 19", ["r2", "r4"]],
    ]);
    expect(groupFacts(groups[0], "week")).toEqual(["2 purchases", "2 names"]);
  });

  it("groups by name with the buyers, the dollars stated and the cluster word's size", () => {
    const groups = groupRows(ROWS, "name");
    // newest filing first (AAA's is Oct 27, as BBB's; ties by ticker), CCC last
    expect(groups.map((g) => g.label)).toEqual(["AAA", "BBB", "CCC"]);
    const aaa = groups[0];
    expect(aaa.buyers).toBe(2);
    expect(aaa.usd).toBe(49_200 + 1_500_000);
    expect(aaa.usdFrom).toBe(2);
    expect(aaa.clusterBuyers).toBe(2);
    expect(groupFacts(aaa, "name")).toEqual(["2 buyers", "$1.5M total"]);
    const ccc = groups[2];
    expect(ccc.usd).toBeNull();
    expect(groupFacts(ccc, "name")).toEqual(["1 buyer", "size not stated"]);
    expect(ccc.clusterBuyers).toBeNull();
  });

  it("counts a buyer once however many filings and however it is spelled, and none for an unnamed filer", () => {
    const rows = [row("a", { filer: "Ann Lee" }), row("b", { filer: "ann  LEE" }), row("c", { filer: null })];
    const [g] = groupRows(rows, "name");
    expect(g.buyers).toBe(1);
    expect(groupFacts({ ...g, buyers: 0 }, "name")[0]).toBe("buyer not named");
  });

  it("groups by card A→Z and lists a name on two cards under both", () => {
    const both = row("two", {
      cards: [
        { thesis_id: "t-a", thesis_name: "Card A", ticker: "AAA", silent: null },
        { thesis_id: "t-b", thesis_name: "Card B", ticker: "AAA", silent: null },
      ],
    });
    const groups = groupRows([both, R3], "card");
    expect(groups.map((g) => [g.label, g.thesisId, g.rows.map((r) => r.key)])).toEqual([
      ["Card A", "t-a", ["two"]],
      ["Card B", "t-b", ["two", "r3"]],
    ]);
    expect(groupFacts(groups[1], "card")).toEqual(["2 purchases", "2 names"]);
  });
});

describe("the work lines", () => {
  it("state the as-of, the window, what was read, what is listed and the rule for the tape", () => {
    const lines = workLines(page(), 4, false);
    expect(lines[0]).toBe(
      "As of Oct 28 (public clock: what was public by the end of that date) · window Sep 28 → Oct 28 (30 days, by filing date)",
    );
    expect(lines[1]).toContain("2 cards counted");
    expect(lines[1]).toContain("7 names read");
    expect(lines[1]).toContain("Context and Bench left out (2 rows)");
    expect(lines[1]).toContain("1 card created after this date left out");
    expect(lines[2]).toContain("4 purchases listed");
    expect(lines[2]).toContain("11 Form 4 filings read");
    expect(lines[2]).toContain("6 filings with no purchase folded away");
    expect(lines[2]).toContain("1 4/A with no purchase row");
    expect(lines[3]).toContain("2 rows in a cluster (1 name)");
    expect(lines[3]).toContain("2 rows are recorded triggers");
    expect(lines[3]).toContain("14 recorded nights read");
    expect(lines[3]).toContain("2 rows with a blank cap");
    expect(lines[4]).toContain("first close on or after the filing date");
  });

  it("restates the filtered count and says when Context and Bench are included", () => {
    const lines = workLines(page(ROWS, { include_silent: true }), 2, true);
    expect(lines[1]).toContain("Context and Bench included");
    expect(lines[2]).toContain("Showing 2 of 4 purchases");
  });

  it("states a live read", () => {
    expect(workLines(page(ROWS, { clock: "record" }), 4, false)[0]).toContain("live read");
  });
});

describe("plural", () => {
  it("counts with its commas and its singular", () => {
    expect(plural(1, "name", "names")).toBe("1 name");
    expect(plural(1234, "name", "names")).toBe("1,234 names");
  });
});
