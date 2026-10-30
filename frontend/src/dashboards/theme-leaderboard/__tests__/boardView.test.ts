import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  FORMULA,
  FRAMING,
  above200Cell,
  adoptionCell,
  armedCell,
  breadthCell,
  capCell,
  cardMatches,
  filterControls,
  filtersActive,
  firstDir,
  fmtMonth,
  fmtPct,
  fmtPp,
  fmtSigned,
  fmtSpan,
  insidersCell,
  keysCell,
  missingParts,
  namesCell,
  nextSort,
  ret7Cell,
  rsCell,
  scoreText,
  scoreTitle,
  signTone,
  sortCards,
  sortValue,
  stateOf,
  tapeCell,
  workLines,
  type Ctx,
} from "../boardView";
import { scoreByKey } from "../score";
import { CARD_A, CARD_B, CARD_C, CARD_D, adoption, breadth, capMix, card, insiders, keys, median, payload, sample, tape } from "./fixtures";

const CTX: Ctx = { min_priced: 3, iwm_ret_30d: 1.5, benchmark: "IWM", recent_months: 3, prior_months: 3 };

describe("small formatting", () => {
  it("signs, rounds and blanks", () => {
    expect(fmtSigned(4.84)).toBe("+4.8");
    expect(fmtSigned(-7.1)).toBe("-7.1");
    expect(fmtSigned(0)).toBe("0.0");
    expect(fmtSigned(null)).toBe("—");
    expect(fmtPct(12.34)).toBe("+12.3%");
    expect(fmtPct(116.67, 0)).toBe("+117%");
    expect(fmtPp(-2)).toBe("-2.0 pp");
    expect(fmtPp(undefined)).toBe("—");
  });

  it("words a month and a span of months (the year once when it is shared)", () => {
    expect(fmtMonth("2026-07-01")).toBe("Jul 2026");
    expect(fmtMonth(null)).toBe("—");
    expect(fmtSpan("2026-07-01", "2026-09-01")).toBe("Jul to Sep 2026");
    expect(fmtSpan("2025-11-01", "2026-01-01")).toBe("Nov 2025 to Jan 2026");
    expect(fmtSpan(null, "2026-01-01")).toBe("—");
  });

  it("tones only a signed figure", () => {
    expect([signTone(2), signTone(-2), signTone(0), signTone(null)]).toEqual(["pos", "neg", "", ""]);
  });
});

describe("the tape cell", () => {
  it("shows the figure in points with the median it rests on", () => {
    const c = card({ tape: tape({ value: 2.5, median: 4, k: 7 }) });
    const cell = tapeCell(c, CTX);
    expect(cell.text).toBe("+2.5 pp");
    expect(cell.tone).toBe("pos");
    expect(cell.title).toBe("Median of 7 priced names: +4.0% over 30 trading days, minus IWM's +1.5%");
    expect(tapeCell(card({ tape: tape({ value: -2 }) }), CTX).tone).toBe("neg");
  });

  it("says why there is no figure, never zero", () => {
    const thin = tapeCell(card({ tape: tape({ value: null, median: null, k: 2, gap: "thin" }) }), CTX);
    expect(thin).toMatchObject({ text: "—", gray: true });
    expect(thin.title).toBe("Median of 2 priced names: fewer than 3, so no median and no tape part");
    const none = tapeCell(card({ tape: tape({ value: null, median: 4, k: 5, gap: "no_iwm" }) }), CTX);
    expect(none.title).toContain("IWM has no 30-day return");
    expect(none.title).toContain("median of 5 priced names: +4.0%");
  });

  it("words the 7-day median the same way", () => {
    expect(ret7Cell(card({ ret_7d: median(-1.25, 8) }), CTX)).toMatchObject({ text: "-1.3%", tone: "neg" });
    expect(ret7Cell(card({ ret_7d: median(null, 1) }), CTX)).toMatchObject({ text: "—", gray: true });
  });
});

describe("the breadth cells", () => {
  it("shows x/n with the share and the thin names in the hover", () => {
    const c = card({ breadth: breadth({ pct: 66.67, above: 2, counted: 3, thin: 1 }) });
    const cell = breadthCell(c);
    expect(cell.text).toBe("2/3");
    expect(cell.title).toContain("2 of 3 counting names are above their 50-day average (67%)");
    expect(cell.title).toContain("1 name with too little history, shown but not counted");
  });

  it("is blank when no name counted, and says whether that is thin history or no price", () => {
    expect(breadthCell(card({ breadth: breadth({ pct: null, above: 0, counted: 0, thin: 3 }) })).title).toContain("3 names with too little history");
    expect(breadthCell(card({ breadth: breadth({ pct: null, above: 0, counted: 0, thin: 0 }) })).title).toBe("No counting name with a price history");
  });

  it("carries the 200-day share beside it", () => {
    expect(above200Cell(card({ breadth: breadth({ above_200: 3, n_200: 6 }) }))).toMatchObject({ text: "3/6" });
    expect(above200Cell(card({ breadth: breadth({ above_200: 0, n_200: 0 }) }))).toMatchObject({ text: "—", gray: true });
  });
});

describe("the keys cell", () => {
  it("reads x of 2 and names the key that is off when exactly one is", () => {
    const one = keysCell(card({ keys: keys({ turned: 1, conviction: true, confirmation: false }) }));
    expect(one.text).toBe("1 of 2");
    expect(one.note).toBe("confirmation off");
    expect(one.title).toContain("keys 1 of 2");
    expect(keysCell(card({ keys: keys({ turned: 1, conviction: false, confirmation: true }) })).note).toBe("conviction off");
    const both = keysCell(card({ state: "armed", keys: keys({ turned: 2, conviction: true, confirmation: true }) }));
    expect(both).toMatchObject({ text: "2 of 2" });
    expect(both.note).toBeUndefined();
    expect(keysCell(card({ keys: keys({ turned: 0, conviction: false, confirmation: false }) })).title).toContain("neither key turned");
  });

  it("counts Managing as 2 and blanks a card with no recorded call", () => {
    const managing = keysCell(card({ state: "managing", keys: keys({ turned: 2, conviction: true, confirmation: true }) }));
    expect(managing.text).toBe("2 of 2");
    expect(managing.title).toContain("Managing counts as 2 of 2");
    expect(keysCell(card({ state: null, keys: keys({ turned: null, conviction: null, confirmation: null }) }))).toMatchObject({
      text: "—",
      gray: true,
    });
  });
});

describe("the adoption cell", () => {
  it("shows the signed percent with the two spans and the term counts in the hover", () => {
    const cell = adoptionCell(card({ adoption: adoption({ slope_pct: 116.67, recent_docs: 130, prior_docs: 60, terms: 5, terms_counted: 4 }) }), CTX);
    expect(cell.text).toBe("+117%");
    expect(cell.title).toContain("130 in Jul to Sep 2026 against 60 in Apr to Jun 2026");
    expect(cell.title).toContain("4 terms of 5 counted");
    expect(cell.title).toContain("a document matching two terms counts twice");
    expect(adoptionCell(card({ adoption: adoption({ slope_pct: -40 }) }), CTX).text).toBe("-40%");
  });

  it("says why the part has no reading", () => {
    const none = { slope_pct: null, recent_docs: null, prior_docs: null };
    expect(adoptionCell(card({ adoption: adoption({ ...none, terms_counted: 0, gap: "not_counted_yet" }) }), CTX)).toMatchObject({
      text: "not counted yet",
      gray: true,
    });
    expect(adoptionCell(card({ adoption: adoption({ ...none, terms: 0, terms_counted: 0, gap: "no_terms" }) }), CTX).text).toBe("no terms");
    const noBase = adoptionCell(card({ adoption: adoption({ ...none, recent_docs: 5, prior_docs: 0, gap: "no_base" }) }), CTX);
    expect(noBase.text).toBe("no base");
    expect(noBase.title).toContain("No documents mention the card's terms in Apr to Jun 2026");
  });
});

describe("the other cells", () => {
  it("sums the buyers with the names on hover, and blanks when no name has a Form 4 reading", () => {
    const cell = insidersCell(card({ insiders: insiders({ buyers: 5, read: 9, names: [{ name: "AAA", buyers: 3 }, { name: "BBB", buyers: 2 }] }) }));
    expect(cell.text).toBe("5");
    expect(cell.title).toContain("9 counting names with a Form 4 reading: AAA 3, BBB 2");
    expect(insidersCell(card({ insiders: insiders({ buyers: 0, read: 4, names: [] }) })).text).toBe("0");
    expect(insidersCell(card({ insiders: insiders({ buyers: null, read: 0, names: [] }) }))).toMatchObject({ text: "—", gray: true });
  });

  it("words names priced / counting with the flagged ones as a quiet note", () => {
    const flagged = namesCell(card({ counting: 14, priced: 12, flagged: 1 }));
    expect(flagged).toMatchObject({ text: "12/14", note: "1 flagged" });
    expect(flagged.title).toContain("1 left out of every price reading for a flagged price history");
    expect(namesCell(card({ counting: 5, priced: 5, flagged: 0 })).note).toBeUndefined();
  });

  it("counts the RS leaders, the cap bands and the armed names", () => {
    expect(rsCell(card({ rs_leaders: 2, rs_n: 9 }))).toMatchObject({ text: "2/9" });
    expect(rsCell(card({ rs_leaders: 0, rs_n: 0 }))).toMatchObject({ text: "—", gray: true });
    const c = card({ cap_mix: capMix({ nano: 0, small: 4, blank: 2 }) });
    expect(capCell(c, "small")).toMatchObject({ text: "4", gray: false });
    expect(capCell(c, "nano")).toMatchObject({ text: "0", gray: true });
    expect(capCell(c, "mid").title).toContain("2 counting names with no cap");
    expect(armedCell(card({ armed_names: 3 }))).toMatchObject({ text: "3" });
    expect(armedCell(card({ armed_names: null }))).toMatchObject({ text: "—", gray: true });
  });
});

describe("the score in words", () => {
  it("rounds the score and lists the five parts on hover", () => {
    const got = scoreByKey(sample());
    expect(scoreText(got.get(CARD_A))).toBe("100");
    expect(scoreText(got.get(CARD_B))).toBe("27");
    expect(scoreTitle(got.get(CARD_B))).toBe("tape 0.50 · breadth 0.33 · keys 0.50 · adoption 0.00 · insiders 0.00");
  });

  it("says which parts a card without a score is missing", () => {
    const got = scoreByKey(sample());
    expect(scoreText(got.get(CARD_C))).toBe("—");
    expect(scoreTitle(got.get(CARD_C))).toMatch(/^No score: no reading for adoption \(tape 0\.00 · breadth 0\.67 · keys 0\.00 · adoption — · insiders 0\.50\)$/);
    expect(scoreTitle(got.get(CARD_D))).toMatch(/^No score: no reading for tape, keys, adoption, insiders/);
    expect(scoreText(undefined)).toBe("—");
    expect(scoreTitle(undefined)).toBe("No score");
  });
});

describe("the filters", () => {
  const cards = sample();

  it("filters by state, by adoption months and by a piece of the name", () => {
    expect(cards.filter((c) => cardMatches(c, { ...DEFAULT_FILTERS, states: ["armed", "warming"] })).map((c) => c.thesis_id)).toEqual([CARD_A, CARD_B]);
    expect(cards.filter((c) => cardMatches(c, { ...DEFAULT_FILTERS, states: ["none"] })).map((c) => c.thesis_id)).toEqual([CARD_D]);
    expect(cards.filter((c) => cardMatches(c, { ...DEFAULT_FILTERS, adoptionOnly: true })).map((c) => c.thesis_id)).toEqual([CARD_A, CARD_B]);
    expect(cards.filter((c) => cardMatches(c, { ...DEFAULT_FILTERS, search: "  CARD c " })).map((c) => c.thesis_id)).toEqual([CARD_C]);
    expect(cards.filter((c) => cardMatches(c, DEFAULT_FILTERS))).toHaveLength(4);
  });

  it("knows when a filter is active", () => {
    expect(filtersActive(DEFAULT_FILTERS)).toBe(false);
    expect(filtersActive({ ...DEFAULT_FILTERS, search: "  " })).toBe(false);
    expect(filtersActive({ ...DEFAULT_FILTERS, search: "a" })).toBe(true);
    expect(filtersActive({ ...DEFAULT_FILTERS, adoptionOnly: true })).toBe(true);
    expect(filtersActive({ ...DEFAULT_FILTERS, states: ["armed"] })).toBe(true);
  });

  it("offers only the controls that discriminate (a control every card shares does not render)", () => {
    const all = filterControls(cards);
    expect(all.states).toEqual(["armed", "warming", "incubating", "none"]);
    expect(all.adoption).toBe(true);
    expect(all.search).toBe(true);
    const same = [card({ state: "warming" }), card({ state: "warming" })];
    expect(filterControls(same)).toEqual({ states: [], adoption: false, search: true });
    expect(filterControls([card()])).toEqual({ states: [], adoption: false, search: false });
    expect(filterControls([])).toEqual({ states: [], adoption: false, search: false });
  });

  it("reads a card with no recorded call as the 'none' state", () => {
    expect(stateOf(card({ state: null }))).toBe("none");
    expect(stateOf(card({ state: "armed" }))).toBe("armed");
  });
});

describe("the sorts", () => {
  const cards = sample();
  const scores = scoreByKey(cards);
  const ids = (sorted: readonly { thesis_id: string }[]) => sorted.map((c) => c.thesis_id);

  it("orders by score, highest first, a card without a score last and by name", () => {
    expect(ids(sortCards(cards, DEFAULT_SORT, scores))).toEqual([CARD_A, CARD_B, CARD_C, CARD_D]);
    expect(ids(sortCards(cards, { key: "score", dir: "asc" }, scores))).toEqual([CARD_B, CARD_A, CARD_C, CARD_D]);
  });

  it("puts a blank last in either direction, never read as zero", () => {
    // the tape: D has none
    expect(ids(sortCards(cards, { key: "tape", dir: "desc" }, scores))).toEqual([CARD_A, CARD_B, CARD_C, CARD_D]);
    expect(ids(sortCards(cards, { key: "tape", dir: "asc" }, scores))).toEqual([CARD_C, CARD_B, CARD_A, CARD_D]);
    // keys: D has no recorded call
    expect(ids(sortCards(cards, { key: "keys", dir: "desc" }, scores))).toEqual([CARD_A, CARD_B, CARD_C, CARD_D]);
    expect(ids(sortCards(cards, { key: "keys", dir: "asc" }, scores))).toEqual([CARD_C, CARD_B, CARD_A, CARD_D]);
    // adoption: C and D have none, and tie by name
    expect(ids(sortCards(cards, { key: "adoption", dir: "desc" }, scores))).toEqual([CARD_A, CARD_B, CARD_C, CARD_D]);
    expect(ids(sortCards(cards, { key: "adoption", dir: "asc" }, scores))).toEqual([CARD_B, CARD_A, CARD_C, CARD_D]);
  });

  it("sorts the state in lifecycle order with no call last, and the name A to Z", () => {
    expect(ids(sortCards(cards, { key: "state", dir: "asc" }, scores))).toEqual([CARD_A, CARD_B, CARD_C, CARD_D]);
    expect(ids(sortCards(cards, { key: "state", dir: "desc" }, scores))).toEqual([CARD_C, CARD_B, CARD_A, CARD_D]);
    expect(ids(sortCards(cards, { key: "card", dir: "desc" }, scores))).toEqual([CARD_D, CARD_C, CARD_B, CARD_A]);
  });

  it("reads every other column's value", () => {
    const [a] = cards;
    const v = (key: Parameters<typeof sortValue>[1]) => sortValue(a, key, scores.get(a.thesis_id));
    expect(v("breadth")).toBe(80);
    expect(v("above200")).toBe(0.5);
    expect(v("insiders")).toBe(5);
    expect(v("names")).toBe(12);
    expect(v("ret7")).toBe(2.4);
    expect(v("rs")).toBe(3);
    expect(v("small")).toBe(4);
    expect(v("armed")).toBe(2);
    expect(sortValue(cards[3], "rs", undefined)).toBeNull(); // no relative-strength reading is blank, not zero
    expect(sortValue(cards[3], "above200", undefined)).toBeNull();
    expect(sortValue(cards[1], "armed", undefined)).toBeNull();
  });

  it("starts a text column A to Z and a figure biggest first, and flips on a second click", () => {
    expect(firstDir("card")).toBe("asc");
    expect(firstDir("state")).toBe("asc");
    expect(firstDir("tape")).toBe("desc");
    expect(nextSort(DEFAULT_SORT, "tape")).toEqual({ key: "tape", dir: "desc" });
    expect(nextSort({ key: "tape", dir: "desc" }, "tape")).toEqual({ key: "tape", dir: "asc" });
    expect(nextSort({ key: "tape", dir: "desc" }, "card")).toEqual({ key: "card", dir: "asc" });
  });
});

describe("the work lines", () => {
  const data = payload();
  const scores = scoreByKey(data.cards);

  it("counts the cards by score and the parts that are missing", () => {
    expect(missingParts(data.cards, scores)).toEqual({ tape: 1, breadth: 0, keys: 1, adoption: 2, insiders: 1 });
    const lines = workLines(data, data.cards, scores);
    expect(lines[0]).toBe("as of 2026-10-20, read on the public clock: what was public by the end of that date");
    expect(lines[1]).toBe(
      "4 cards counted (5 live; 1 created after the as-of left out) · 4 shown · 2 scored · 2 without a score (a part has no reading: tape 1 · keys 1 · adoption 2 · insiders 1)",
    );
    expect(lines[2]).toBe(
      "34 counting names across the cards · 33 read for price · 1 flagged · Context and Bench never counted (7 rows left out)",
    );
    expect(lines[3]).toBe("adoption: 2 cards with six closed months of term counts, 2 without (not counted yet 1 · no signal terms 1)");
    expect(lines.at(-2)).toBe(
      "IWM 30-day return +1.5% · a median needs 3 priced names · adoption: the last 3 closed months against the 3 before · equal weights",
    );
    expect(lines.at(-1)).toBe("computed · compute 41,250 ms");
  });

  it("adds the lines that mark an exception, only when there is one", () => {
    const quiet = workLines(data, data.cards, scores);
    expect(quiet.some((l) => l.includes("rebuilt by the backfill"))).toBe(false);
    expect(quiet.some((l) => l.includes("roster snapshot"))).toBe(false);
    const withRec = payload({ roster_fallback_cards: 2, unresolved_rows: 3, cache: "hit", clock: "record" });
    withRec.cards[0] = { ...withRec.cards[0], reconstructed: true };
    const lines = workLines(withRec, withRec.cards, scoreByKey(withRec.cards));
    expect(lines).toContain("1 card whose recorded state was rebuilt by the backfill (R): reported, never scored");
    expect(lines).toContain("2 cards with no roster snapshot by then: today's roster stands in");
    expect(lines.some((l) => l.includes("3 rows with no resolved security, not read"))).toBe(true);
    expect(lines[0]).toBe("as of 2026-10-20, read on the live read");
    expect(lines.at(-1)).toBe("served from the cache · compute 41,250 ms");
  });
});

describe("the words the page prints", () => {
  it("frames the page as a rank of what the data shows, not a judgment of any thesis", () => {
    expect(FRAMING).toBe(
      "A rank of what the data shows about each card's names and record, by the formula below. It is not a judgment of any thesis.",
    );
  });

  it("states the formula: five parts, equal weights, relative to the cards shown, feeding nothing", () => {
    for (const w of ["mean of five percentiles", "tape", "breadth", "keys", "adoption", "insiders", "Equal weights", "Ties share a percentile", "feeds nothing"]) {
      expect(FORMULA).toContain(w);
    }
  });
});
