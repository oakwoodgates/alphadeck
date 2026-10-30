import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  FRAMING,
  LIMITS,
  adoptionCell,
  breadthCell,
  coilingCell,
  daysWords,
  filterControls,
  filtersActive,
  fmtDay,
  fmtMonth,
  fmtSpan,
  hiddenIncubating,
  insiderCell,
  insiderNameTitle,
  keyLine,
  leadingCell,
  nextSort,
  riskCell,
  rowMatches,
  rowsOnShow,
  signTone,
  sinceWords,
  sortRows,
  sortValue,
  stateWord,
  triggerLine,
  turnedKeys,
  workLines,
} from "../quietView";
import { adoption, notCounted, payload, quiet, row, rows } from "./fixtures";

// Forming Quietly's pure view helpers: which rows the Incubating toggle and the filters show, the sorts, and the words
// of every cell. Hand-built rows only; every name is a neutral fixture name.

const names = (rs: { thesis_name: string }[]) => rs.map((r) => r.thesis_name);

describe("which rows", () => {
  it("shows every Warming card and only the Incubating cards with a quiet signal lit", () => {
    const rs = rows();
    expect(names(rowsOnShow(rs, false))).toEqual(["Card B", "Card A", "Card C"]);
    expect(hiddenIncubating(rs)).toBe(1);
  });

  it("the toggle adds the rest of the Incubating cards and keeps the server's order", () => {
    expect(names(rowsOnShow(rows(), true))).toEqual(["Card B", "Card A", "Card C", "Card D"]);
  });

  it("filters by state, insider buying, coiling and card, all together", () => {
    const [b, a, c] = rows();
    expect(rowMatches(a, DEFAULT_FILTERS)).toBe(true);
    expect(rowMatches(c, { ...DEFAULT_FILTERS, states: ["warming"] })).toBe(false);
    expect(rowMatches(a, { ...DEFAULT_FILTERS, states: ["warming"] })).toBe(true);
    expect(rowMatches(b, { ...DEFAULT_FILTERS, insider: true })).toBe(false);
    expect(rowMatches(c, { ...DEFAULT_FILTERS, insider: true })).toBe(true);
    expect(rowMatches(c, { ...DEFAULT_FILTERS, coiling: true })).toBe(false);
    expect(rowMatches(a, { ...DEFAULT_FILTERS, coiling: true })).toBe(true);
    expect(rowMatches(a, { ...DEFAULT_FILTERS, card: a.thesis_id })).toBe(true);
    expect(rowMatches(c, { ...DEFAULT_FILTERS, card: a.thesis_id })).toBe(false);
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    expect(filtersActive({ ...DEFAULT_FILTERS, coiling: true })).toBe(true);
  });

  it("offers only the controls that can tell the rows on show apart", () => {
    const full = filterControls(rowsOnShow(rows(), false));
    expect(full.states).toEqual(["warming", "incubating"]);
    expect(full.insider && full.coiling).toBe(true);
    expect(full.cards.map((c) => c.name)).toEqual(["Card A", "Card B", "Card C"]);
    // one state, one card, nothing lit: no control would change anything
    const lone = filterControls([row({ quiet: quiet({ insider: [], coiling: [] }) })]);
    expect(lone).toEqual({ states: [], insider: false, coiling: false, cards: [] });
  });
});

describe("the sorts", () => {
  it("keeps the server's order by default and for a tie", () => {
    const rs = rows();
    expect(names(sortRows(rs, DEFAULT_SORT))).toEqual(["Card B", "Card A", "Card C", "Card D"]);
  });

  it("sorts by days in state, longest first on the first click", () => {
    const rs = rows();
    expect(names(sortRows(rs, nextSort(DEFAULT_SORT, "days")))).toEqual(["Card D", "Card C", "Card A", "Card B"]);
    const flipped = nextSort(nextSort(DEFAULT_SORT, "days"), "days");
    expect(flipped).toEqual({ key: "days", dir: "asc" });
    expect(names(sortRows(rs, flipped))).toEqual(["Card B", "Card A", "Card C", "Card D"]);
  });

  it("sorts by card name A to Z first, then Z to A", () => {
    const rs = rows().reverse();
    expect(names(sortRows(rs, nextSort(DEFAULT_SORT, "card")))).toEqual(["Card A", "Card B", "Card C", "Card D"]);
    expect(names(sortRows(rs, { key: "card", dir: "desc" }))).toEqual(["Card D", "Card C", "Card B", "Card A"]);
  });

  it("sorts by distinct insider buyers, a card with none last in either direction", () => {
    const rs = rows();
    expect(sortValue(rs[1], "insider")).toBe(3); // 2 + 1 distinct buyers
    expect(sortValue(rs[0], "insider")).toBeNull();
    expect(names(sortRows(rs, { key: "insider", dir: "desc" }))).toEqual(["Card A", "Card C", "Card B", "Card D"]);
    expect(names(sortRows(rs, { key: "insider", dir: "asc" }))).toEqual(["Card C", "Card A", "Card B", "Card D"]);
  });

  it("sorts by adoption percent, a card with no percent last in either direction", () => {
    const rs = rows();
    expect(names(sortRows(rs, { key: "adoption", dir: "desc" }))).toEqual(["Card A", "Card C", "Card B", "Card D"]);
    expect(names(sortRows(rs, { key: "adoption", dir: "asc" }))).toEqual(["Card C", "Card A", "Card B", "Card D"]);
  });
});

describe("the cells", () => {
  it("words the state, the first night and the days in state", () => {
    const a = row();
    expect(stateWord("warming")).toBe("Warming");
    expect(stateWord("incubating")).toBe("Incubating");
    expect(sinceWords(a)).toBe("since Oct 25");
    expect(daysWords(a)).toBe("3 days");
    expect(daysWords(row({ days_in_state: 1 }))).toBe("1 day");
    expect(daysWords(row({ days_in_state: 0 }))).toBe("0 days");
    expect(sinceWords(row({ run_start: null, days_in_state: null }))).toBe("no recorded night");
    expect(daysWords(row({ days_in_state: null }))).toBe("");
  });

  it("quotes a key as the call recorded it and lists the turned ones", () => {
    const a = row();
    expect(keyLine(a.key_conviction)).toBe("Conviction · A conviction entry trigger fired — the 'why now is real'.");
    expect(keyLine({ turned: true, label: "Confirmation", detail: null })).toBe("Confirmation");
    expect(turnedKeys(a).map((k) => k.label)).toEqual(["Conviction"]);
  });

  it("words a trigger by its label, kind, grade and fire date", () => {
    const t = row().names[0].triggers[0];
    expect(triggerLine(t)).toBe("insider cluster · insider · core · fired 2026-10-22");
    expect(triggerLine({ ...t, grade: null, event_date: null })).toBe("insider cluster · insider · no grade · fired no date stated");
  });

  it("shows the risk count with each risk in the hover, and a blank when none", () => {
    const got = riskCell(row());
    expect(got.text).toBe("1");
    expect(got.title).toContain("AAA convertible overhang (dilution)");
    expect(riskCell(row({ risks: [] }))).toMatchObject({ text: "—", gray: true });
  });

  it("shows insider buying as n of the names read, and a blank with the reason when none", () => {
    const got = insiderCell(row());
    expect(got.text).toBe("2 of 11");
    expect(got.title).toContain("2 names have no Form 4 ingested");
    const none = insiderCell(row({ quiet: quiet({ insider: [] }) }));
    expect(none).toMatchObject({ text: "—", gray: true });
    expect(none.title).toContain("among 11 names read");
    expect(insiderNameTitle({ ticker: "AAA", buys_30d: 3, buyers_30d: 2, cluster: true })).toContain("a cluster: at least two distinct buyers");
    expect(insiderNameTitle({ ticker: "CCC", buys_30d: 1, buyers_30d: 1, cluster: false })).not.toContain("cluster");
  });

  it("shows coiling and leading as n of the names read", () => {
    expect(coilingCell(row()).text).toBe("1 of 11");
    expect(leadingCell(row()).text).toBe("1 of 11");
    expect(coilingCell(row({ quiet: quiet() }))).toMatchObject({ text: "—", gray: true });
    expect(coilingCell(row({ quiet: quiet() })).title).toContain("1 name has too little tape to read a base");
  });

  it("shows breadth as x of n above the 50-day and keeps the thin names counted", () => {
    const got = breadthCell(row());
    expect(got.text).toBe("5 of 10");
    expect(got.sub).toBe("above the 50-day");
    expect(got.title).toContain("1 name with too little history is not counted");
    const thrust = breadthCell(
      row({ quiet: quiet({ breadth: { above: 9, counted: 10, thin: 0, pct: 90, delta_pts: 40, thrust: true } }) }),
    );
    expect(thrust.sub).toBe("above the 50-day, thrust");
    expect(breadthCell(row({ quiet: quiet({ breadth: null }) }))).toMatchObject({ text: "—", gray: true });
    expect(
      breadthCell(row({ quiet: quiet({ breadth: { above: null, counted: 0, thin: 4, pct: null, delta_pts: null, thrust: false } }) })).title,
    ).toContain("4 names");
  });

  it("shows the adoption slope as a signed whole percent with both counts, and the sign tone is the only tone", () => {
    const up = adoptionCell(adoption());
    expect(up).toMatchObject({ text: "+38%", sub: "412 vs 298 docs", tone: "pos" });
    expect(up.title).toContain("Jul to Sep 2026: 412 documents, against Apr to Jun 2026: 298");
    expect(up.title).toContain("All 5 SIGNAL terms counted");
    const down = adoptionCell(adoption({ percent: -12.4, recent: 87, prior: 99 }));
    expect(down).toMatchObject({ text: "-12%", tone: "neg" });
    expect(adoptionCell(adoption({ percent: 0.3 }))).toMatchObject({ text: "0%", tone: "" });
    expect(signTone(null)).toBe("");
    expect(signTone(0.4)).toBe("");
  });

  it("says 'not counted yet' for an uncounted slope and shows the counts when the earlier span is empty", () => {
    const none = adoptionCell(notCounted());
    expect(none).toMatchObject({ text: "not counted yet", gray: true });
    expect(none.title).toContain("3 of the last 6 closed months are stored");
    const zero = adoptionCell(adoption({ percent: null, recent: 12, prior: 0 }));
    expect(zero).toMatchObject({ text: "—", sub: "12 vs 0 docs", gray: true });
    expect(zero.title).toContain("the earlier three months hold no documents");
    expect(zero.tone).toBeUndefined();
  });

  it("states the terms left out of the slope and a latest month read before it closed", () => {
    const got = adoptionCell(adoption({ terms_counted: 3, terms_partial: 1, terms_unstored: 1, latest_partial: true }));
    expect(got.title).toContain("3 terms of 5 counted");
    expect(got.title).toContain("1 partly stored and 1 not stored are left out");
    expect(got.title).toContain("read before it ended");
  });

  it("formats days and months", () => {
    expect(fmtDay("2026-10-27")).toBe("Oct 27");
    expect(fmtDay(null)).toBe("—");
    expect(fmtMonth("2026-07-01")).toBe("Jul 2026");
    expect(fmtSpan("2026-07-01", "2026-09-01")).toBe("Jul to Sep 2026");
    expect(fmtSpan("2025-11-01", "2026-01-01")).toBe("Nov 2025 to Jan 2026");
    expect(fmtSpan(null, "2026-01-01")).toBe("—");
  });
});

describe("the work lines and the page's own sentences", () => {
  it("states the as-of, the clock, every count, the names read, the term months, the members and the cache", () => {
    const lines = workLines(payload(), 1, false);
    expect(lines[0]).toBe("as of 2026-10-28 · the public clock: each fact counts from when it became public, up to the end of that day");
    expect(lines[1]).toContain("8 cards counted of 9 live");
    expect(lines[1]).toContain("1 card created after the as-of left out");
    expect(lines[1]).toContain("2 Warming · 1 Incubating with a quiet signal · 1 Incubating without (hidden) · 2 Armed · 1 Managing · 1 no call yet");
    expect(lines[2]).toBe("40 names read · 1 left out for a flagged price history · adoption counted for 3 cards, no term months stored for 1");
    expect(lines[3]).toBe("members run: insider_flow_90d, vcp, relative_strength, plus one theme-breadth read per card · computed in 1234 ms");
    expect(workLines(payload(), 1, true)[1]).toContain("1 Incubating without (shown)");
    expect(workLines(payload({ clock: "record", cache: "hit" }), 0, false)[0]).toContain("the record clock");
    expect(workLines(payload({ cache: "hit" }), 0, false)[3]).toContain("from the cache");
  });

  it("frames the page in the one sentence and uses none of the words the page never says", () => {
    expect(FRAMING).toBe("Nothing on this page is Armed. It lists what the record shows forming and what each card still needs.");
    const spoken = [FRAMING, ...LIMITS, ...workLines(payload(), 1, false)].join(" ");
    expect(spoken).not.toMatch(/early|setting up|about to|best|strongest|promising|hot\b/i);
  });
});
