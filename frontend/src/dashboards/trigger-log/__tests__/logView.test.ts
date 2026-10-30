import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTERS,
  NO_AGGREGATE,
  cacheWords,
  dimValues,
  facetCounts,
  filterRows,
  filtersActive,
  fireNote,
  groupRows,
  liveWords,
  pct,
  roleWords,
  sourceView,
  stateChanged,
  statePair,
  statePairWords,
  toggleCount,
  toggleDiscriminates,
  tone,
  turnsWords,
  weekLabel,
  workLines,
  type Filters,
} from "../logView";
import { EDGAR, R1, R2, R3, R4, ROWS, page, row } from "./fixtures";

// The Trigger Log's pure view logic: grouping (weeks newest first, kinds and cards A→Z), the filters and the
// count each chip restates, and the words of every cell. No aggregate is computed anywhere in the module.

const withFilters = (over: Partial<Filters>): Filters => ({ ...DEFAULT_FILTERS, ...over });

describe("groupRows", () => {
  it("groups by week, newest week first, each group counting its rows by role", () => {
    const groups = groupRows(ROWS, "week");
    expect(groups.map((g) => [g.key, g.label, g.entry, g.risk])).toEqual([
      ["2026-10-26", "Week of Oct 26", 2, 0],
      ["2026-10-19", "Week of Oct 19", 1, 1],
    ]);
    // each group keeps the order it was given (the server's: the recorded night newest first, then ticker)
    expect(groups[1].rows.map((r) => r.key)).toEqual(["r3", "r4"]);
    expect(groups.every((g) => g.thesisId === null)).toBe(true);
  });

  it("groups by kind A→Z and by card A→Z, a card group carrying its id", () => {
    expect(groupRows(ROWS, "kind").map((g) => g.label)).toEqual([
      "dilution risk",
      "insider",
      "laggard",
      "technical breakout",
    ]);
    const cards = groupRows(ROWS, "card");
    expect(cards.map((g) => [g.label, g.thesisId, g.rows.length])).toEqual([
      ["Card A", "t-a", 2],
      ["Card B", "t-b", 2],
    ]);
  });

  it("an empty list is no group", () => {
    expect(groupRows([], "week")).toEqual([]);
  });

  it("words a header's counts by role and leaves a zero out", () => {
    expect(roleWords(3, 1)).toBe("3 entry triggers · 1 risk signal");
    expect(roleWords(1, 0)).toBe("1 entry trigger");
    expect(roleWords(0, 2)).toBe("2 risk signals");
    expect(weekLabel("2026-10-26")).toBe("Week of Oct 26");
  });
});

describe("the filters", () => {
  it("filters by role, kind, grade and card (a chip set: any of its values), and by the two toggles", () => {
    expect(filterRows(ROWS, withFilters({ roles: new Set(["risk_signal"]) })).map((r) => r.key)).toEqual(["r3"]);
    expect(filterRows(ROWS, withFilters({ kinds: new Set(["insider", "laggard"]) })).map((r) => r.key)).toEqual([
      "r1",
      "r4",
    ]);
    expect(filterRows(ROWS, withFilters({ grades: new Set(["flip"]) })).map((r) => r.key)).toEqual(["r2", "r4"]);
    expect(filterRows(ROWS, withFilters({ grades: new Set(["none"]) })).map((r) => r.key)).toEqual(["r3"]);
    expect(filterRows(ROWS, withFilters({ card: "t-b" })).map((r) => r.key)).toEqual(["r3", "r4"]);
    // live only keeps a row that IS live: a lapsed one and an unknown one both go
    expect(filterRows(ROWS, withFilters({ liveOnly: true })).map((r) => r.key)).toEqual(["r1", "r2"]);
    // state changed only: a new card began, an unchanged state did not move
    expect(filterRows(ROWS, withFilters({ changedOnly: true })).map((r) => r.key)).toEqual(["r2"]);
  });

  it("combines the dimensions (all of them must pass)", () => {
    const f = withFilters({ roles: new Set(["entry_trigger"]), card: "t-b" });
    expect(filterRows(ROWS, f).map((r) => r.key)).toEqual(["r4"]);
  });

  it("no filter is every row, and knows whether any is on", () => {
    expect(filterRows(ROWS, DEFAULT_FILTERS)).toHaveLength(4);
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    for (const over of [
      { roles: new Set(["risk_signal" as const]) },
      { kinds: new Set(["insider"]) },
      { grades: new Set(["core" as const]) },
      { card: "t-a" },
      { liveOnly: true },
      { changedOnly: true },
    ]) {
      expect(filtersActive(withFilters(over))).toBe(true);
    }
  });

  it("a state moved only when the row before was read and differs", () => {
    expect(stateChanged(R2)).toBe(true);
    expect(stateChanged(R1)).toBe(false); // new card
    expect(stateChanged(R3)).toBe(false); // unchanged
    expect(stateChanged(row("x", { from_state: null, new_card: false }))).toBe(false); // unreadable before
  });
});

describe("the count a chip restates", () => {
  it("counts each value with every OTHER filter applied", () => {
    // no filter: a count per value of the dimension
    expect([...facetCounts(ROWS, DEFAULT_FILTERS, "role")]).toEqual([
      ["entry_trigger", 3],
      ["risk_signal", 1],
    ]);
    // a card filter narrows the role counts, but not the card counts (a chip never counts against itself)
    const onB = withFilters({ card: "t-b" });
    expect(Object.fromEntries(facetCounts(ROWS, onB, "role"))).toEqual({ entry_trigger: 1, risk_signal: 1 });
    expect(Object.fromEntries(facetCounts(ROWS, onB, "card"))).toEqual({ "t-a": 2, "t-b": 2 });
    // its own selection does not change its own counts either
    const roleOn = withFilters({ roles: new Set(["risk_signal"]) });
    expect(Object.fromEntries(facetCounts(ROWS, roleOn, "role"))).toEqual({ entry_trigger: 3, risk_signal: 1 });
  });

  it("keeps a value present in the payload at zero when the other filters exclude every row of it", () => {
    const f = withFilters({ liveOnly: true });
    expect(Object.fromEntries(facetCounts(ROWS, f, "kind"))).toEqual({
      insider: 1,
      technical_breakout: 1,
      dilution_risk: 0,
      laggard: 0,
    });
  });

  it("counts a toggle with every other filter applied, and renders one only if it can discriminate", () => {
    expect(toggleCount(ROWS, DEFAULT_FILTERS, "liveOnly")).toBe(2);
    expect(toggleCount(ROWS, withFilters({ card: "t-b" }), "liveOnly")).toBe(0);
    expect(toggleCount(ROWS, DEFAULT_FILTERS, "changedOnly")).toBe(1);
    expect(toggleDiscriminates(ROWS, "liveOnly")).toBe(true);
    // every row live, or none changed: the control could not discriminate
    expect(toggleDiscriminates([R1, R2], "liveOnly")).toBe(false);
    expect(toggleDiscriminates([R1, R3], "changedOnly")).toBe(false);
    expect(toggleDiscriminates([], "liveOnly")).toBe(false);
  });
});

describe("dimValues", () => {
  it("lists the values the payload holds in the page's order", () => {
    expect(dimValues(ROWS, "role")).toEqual(["entry_trigger", "risk_signal"]);
    expect(dimValues(ROWS, "kind")).toEqual(["dilution_risk", "insider", "laggard", "technical_breakout"]);
    expect(dimValues(ROWS, "grade")).toEqual(["flip", "core", "none"]);
    expect(dimValues(ROWS, "card")).toEqual(["t-a", "t-b"]); // by name
    expect(dimValues([R1], "role")).toEqual(["entry_trigger"]); // one value: the page draws no control for it
  });
});

describe("the cell words", () => {
  it("words what a trigger turns", () => {
    expect(turnsWords(R1)).toBe("conviction");
    expect(turnsWords(R2)).toBe("confirmation");
    expect(turnsWords(R3)).toBe("risk");
    expect(turnsWords(row("x", { turns: null }))).toBe("no key");
  });

  it("notes a fire date that trails the recorded night, and nothing else", () => {
    expect(fireNote(R1)).toBe("recorded 6 days after");
    expect(fireNote(row("x", { recorded_days_after: 1 }))).toBe("recorded 1 day after");
    expect(fireNote(R2)).toBeNull();
  });

  it("words the state pair: new card, unchanged, moved, unknown before", () => {
    expect(statePair(R1)).toEqual({ kind: "new", to: "warming" });
    expect(statePair(R3)).toEqual({ kind: "same", to: "armed" });
    expect(statePair(R2)).toEqual({ kind: "moved", from: "warming", to: "armed" });
    expect(statePair(row("x", { from_state: null, new_card: false }))).toEqual({
      kind: "unknown-before",
      to: "armed",
    });
    expect(statePairWords(statePair(R2))).toBe("warming → armed");
    expect(statePairWords(statePair(R1))).toBe("new card · warming");
    expect(statePairWords(statePair(R3))).toBe("armed");
  });

  it("words live: live, off the card, or unknown", () => {
    expect([R1, R3, R4].map(liveWords)).toEqual(["live", "off the card", "—"]);
  });

  it("formats a return as a signed percent with its tone, and a missing one as a dash", () => {
    expect(pct(0.1234)).toBe("+12.3%");
    expect(pct(-0.042)).toBe("-4.2%");
    expect(pct(0)).toBe("0.0%");
    expect(pct(0.00004)).toBe("0.0%"); // rounds to zero: no sign, no tone
    expect(pct(null)).toBe("—");
    expect([0.1, -0.1, 0, 0.00004, null].map(tone)).toEqual(["pos", "neg", "flat", "flat", "flat"]);
  });

  it("shows a source as an EDGAR link, a stored link, a bar date or its kind, detail on hover", () => {
    const [edgar] = R1.sources;
    expect(sourceView(edgar)).toEqual({
      text: "EDGAR ↗",
      href: EDGAR,
      title: "form4 · 0000876543-26-000010\nshares: 1000",
    });
    expect(sourceView({ source: "doc", ref: "x", url: "https://example.test/a", bar_date: null, detail: [] })).toMatchObject({
      text: "source ↗",
      href: "https://example.test/a",
    });
    expect(sourceView(R2.sources[0])).toMatchObject({ text: "bar Oct 27", href: null });
    expect(sourceView({ source: "xbrl", ref: null, url: null, bar_date: null, detail: [] })).toMatchObject({
      text: "xbrl",
      href: null,
    });
  });
});

describe("the work lines", () => {
  const data = page();

  it("states the as-of, the clock, the window, the cards, the nights, the rows and the tape rule", () => {
    const text = workLines(data, 4, false).join("\n");
    expect(text).toContain("As of Oct 28 (public clock: what was public by the end of that date)");
    expect(text).toContain("window Jul 30 → Oct 28 (90 days)");
    expect(text).toContain("3 cards counted · 2 with a night in the window · 1 quiet");
    expect(text).toContain("1 card created after this date left out");
    expect(text).toContain("9 nights read · 1 reconstructed (†)");
    expect(text).toContain("4 triggers listed · 3 entry triggers · 1 risk signal");
    expect(text).toContain("2 live triggers first recorded before the window (not listed)");
    expect(text).toContain("1 row with no ticker");
    expect(text).toContain("first close on or after the recorded night (the Scoreboard's entry rule)");
    expect(text).toContain("IWM on the same two dates");
    expect(text).toContain("sessions counted on IWM's bar calendar (63 sessions, Jul 30 → Oct 28)");
  });

  it("restates the row count when a filter is on and says so when a read could not be used", () => {
    expect(workLines(data, 2, true)[3]).toContain("Showing 2 of 4 triggers");
    const rough = page(ROWS, {
      counts: { ...data.counts, nights_unreadable: 2, roster_fallback: 1, cards_no_call: 1 },
      sessions: { source: "none", count: 0, first: null, last: null },
    });
    const text = workLines(rough, 4, false).join("\n");
    expect(text).toContain("2 unreadable, not listed");
    expect(text).toContain("1 card on today's roster (no snapshot by then)");
    expect(text).toContain("1 with no call recorded yet");
    expect(text).toContain("no session calendar: no bars at this as-of");
  });

  it("words the live read and the cache", () => {
    expect(workLines(page(ROWS, { clock: "record" }), 4, false)[0]).toContain("(live read)");
    expect(cacheWords(data)).toBe("computed · 412 ms");
    expect(cacheWords(page(ROWS, { cache: "hit" }))).toBe("cache hit · 412 ms when computed");
  });

  it("never words an aggregate: no median, mean or average outside the fixed pointer to the Track Record", () => {
    const all = [...workLines(data, 4, false), cacheWords(data)].join("\n");
    expect(all).not.toMatch(/\b(median|mean|average|avg|win rate|hit rate)\b/i);
    expect(NO_AGGREGATE).toBe("no aggregate here: medians by tier are on the Track Record page");
  });
});
