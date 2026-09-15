import { describe, expect, it } from "vitest";

import type {
  OperatorSpanOut,
  ScoreboardEpisodeOut,
  ScoreboardThesisOut,
} from "../../api/hooks";
import {
  episodeMatches,
  filterEpisodes,
  filterMatchCounts,
  filterSpans,
  groupCountLabel,
  groupMatchCount,
  ledgerTally,
  spanMatches,
  toggleLedgerFilter,
  type LedgerFilterId,
} from "../filters";

// The pure predicates behind the ledger's filter chips, stated with direct inputs — each contract is
// asserted on the boundary that actually decides it (no decision logged vs passed vs took-and-held vs
// took-and-closed; open vs closed), not on a snapshot of some fixture's shape. The three load-bearing
// properties beyond the predicates themselves: the chips combine as a UNION, an empty active set is
// the IDENTITY (the "never the default" rule, as an assertion), and every count is taken over the
// WHOLE record rather than the rendered subset.

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    ticker: "DEVCO",
    arm_date: "2026-07-10",
    dearm_date: null,
    close_reason: "window_end",
    status: "open",
    matured: false,
    censored_start: false,
    operator: null,
    ...over,
  } as ScoreboardEpisodeOut;
}

/** An episode operator slot: the wire's `took`/`passed` decision, with `running` deciding whether a
 *  take is STILL a position. */
function operator(over: Record<string, unknown> = {}): ScoreboardEpisodeOut["operator"] {
  return {
    action: "took",
    decision_id: "d1",
    decision_date: "2026-07-12",
    thesis_level: false,
    entry_inferred: false,
    exit_inferred: false,
    running: true,
    ...over,
  } as ScoreboardEpisodeOut["operator"];
}

function span(over: Partial<OperatorSpanOut> = {}): OperatorSpanOut {
  return {
    take_id: "d1",
    take_date: "2026-07-11",
    security_id: "s-j",
    ticker: "J",
    thesis_level: false,
    override: false,
    running: true,
    ...over,
  } as OperatorSpanOut;
}

function thesis(over: Partial<ScoreboardThesisOut> = {}): ScoreboardThesisOut {
  return {
    thesis_id: "t1",
    name: "A thesis",
    archived: false,
    episodes: [],
    operator_spans: [],
    ...over,
  } as ScoreboardThesisOut;
}

const set = (...ids: LedgerFilterId[]) => new Set<LedgerFilterId>(ids);

describe("ledger filters — the episode predicates", () => {
  it("Open is exactly the wire's armed-at-the-record-edge status", () => {
    expect(episodeMatches("open", ep({ status: "open" }))).toBe(true);
    expect(episodeMatches("open", ep({ status: "closed", dearm_date: "2026-08-01" }))).toBe(false);
  });

  // "In position" reads STILL HOLDING. A closed-out take is a finished decision, and counting it
  // would make the chip answer "ever taken" while its label says otherwise.
  it("In position is a take that is still running — never a pass, never a closed-out take", () => {
    expect(episodeMatches("in_position", ep({ operator: operator({ running: true }) }))).toBe(true);
    expect(episodeMatches("in_position", ep({ operator: operator({ running: false }) }))).toBe(false);
    expect(
      episodeMatches("in_position", ep({ operator: operator({ action: "passed", running: false }) })),
    ).toBe(false);
    expect(episodeMatches("in_position", ep({ operator: null }))).toBe(false);
  });

  // The whole point of the chip is the gap between "armed" and "decided" — a pass IS a decision, so a
  // passed episode has been answered even though nothing was bought.
  it("Needs an answer is an OPEN episode with no decision logged — a pass counts as an answer", () => {
    expect(episodeMatches("needs_answer", ep({ status: "open", operator: null }))).toBe(true);
    expect(
      episodeMatches("needs_answer", ep({ status: "open", operator: operator({ action: "passed" }) })),
    ).toBe(false);
    expect(episodeMatches("needs_answer", ep({ status: "open", operator: operator() }))).toBe(false);
    // a closed episode is past the question, answered or not
    expect(episodeMatches("needs_answer", ep({ status: "closed", operator: null }))).toBe(false);
  });
});

describe("ledger filters — the span predicates", () => {
  // A span is a logged TAKE, not an arm: two of the three chips ask about states it structurally
  // cannot be in, and that is a statement about the row kind rather than a gap in the map.
  it("a span is never Open and never Needs an answer — it has no armed state and IS a decision", () => {
    expect(spanMatches("open", span())).toBe(false);
    expect(spanMatches("open", span({ running: false, close_date: "2026-08-01" }))).toBe(false);
    expect(spanMatches("needs_answer", span())).toBe(false);
  });

  it("In position is the span's own running flag", () => {
    expect(spanMatches("in_position", span({ running: true }))).toBe(true);
    expect(spanMatches("in_position", span({ running: false }))).toBe(false);
  });
});

describe("ledger filters — combination and identity", () => {
  const openNoDecision = ep({ security_id: "a", status: "open", operator: null });
  const openHeld = ep({ security_id: "b", status: "open", operator: operator({ running: true }) });
  const closedHeld = ep({
    security_id: "c",
    status: "closed",
    dearm_date: "2026-08-01",
    operator: operator({ running: true }),
  });
  const closedDone = ep({
    security_id: "d",
    status: "closed",
    dearm_date: "2026-08-01",
    operator: operator({ running: false }),
  });
  const all = [openNoDecision, openHeld, closedHeld, closedDone];

  // Zero chips is the "never the default" rule as an assertion: the ledger before the chips existed
  // and the ledger with none pressed have to be the same ledger.
  it("zero chips active returns the input unchanged — same rows, same order, same length", () => {
    expect(filterEpisodes(all, set())).toEqual(all);
    expect(filterEpisodes(all, set())).toHaveLength(all.length);
    const spans = [span({ take_id: "s1" }), span({ take_id: "s2", running: false })];
    expect(filterSpans(spans, set())).toEqual(spans);
  });

  it("one chip gives exactly that chip's rows", () => {
    expect(filterEpisodes(all, set("open"))).toEqual([openNoDecision, openHeld]);
    expect(filterEpisodes(all, set("in_position"))).toEqual([openHeld, closedHeld]);
    expect(filterEpisodes(all, set("needs_answer"))).toEqual([openNoDecision]);
  });

  // The union is the decision that makes two chips useful: open ∪ in-position is the live picture.
  // Under AND this pair would return only the rows that are BOTH, and `in_position`+`needs_answer`
  // would be empty by construction — a control whose result can only be an empty ledger.
  it("two chips combine as a UNION, not an intersection", () => {
    expect(filterEpisodes(all, set("open", "in_position"))).toEqual([
      openNoDecision,
      openHeld,
      closedHeld,
    ]);
    // the pair AND would make structurally empty still returns both halves
    expect(filterEpisodes(all, set("in_position", "needs_answer"))).toEqual([
      openNoDecision,
      openHeld,
      closedHeld,
    ]);
  });

  it("filtering preserves the record's own order — it removes rows, it never re-ranks them", () => {
    const reversed = [closedDone, closedHeld, openHeld, openNoDecision];
    expect(filterEpisodes(reversed, set("open"))).toEqual([openHeld, openNoDecision]);
  });

  it("toggleLedgerFilter is its own inverse and never mutates the set it was given", () => {
    const empty = set();
    const one = toggleLedgerFilter(empty, "open");
    expect([...one]).toEqual(["open"]);
    expect(empty.size).toBe(0); // the caller's set is untouched — a new set every press
    const two = toggleLedgerFilter(one, "in_position");
    expect(two.size).toBe(2);
    expect([...toggleLedgerFilter(two, "in_position")]).toEqual(["open"]);
    expect(toggleLedgerFilter(one, "open").size).toBe(0);
  });
});

describe("ledger filters — the counts the operator reads", () => {
  // Two theses, and the SECOND is the one that matters: its rows would be out of view whenever the
  // operator folds it (archived groups start folded), so counting the rendered subset would quietly
  // undercount the record.
  const record: ScoreboardThesisOut[] = [
    thesis({
      thesis_id: "t-live",
      episodes: [
        ep({ security_id: "a", status: "open", operator: null }),
        ep({ security_id: "b", status: "open", operator: operator({ running: true }) }),
        ep({ security_id: "c", status: "closed", operator: operator({ running: false }) }),
      ],
      operator_spans: [span({ take_id: "s1", running: true })],
    }),
    thesis({
      thesis_id: "t-arch",
      archived: true,
      episodes: [ep({ security_id: "d", status: "open", operator: null })],
      operator_spans: [span({ take_id: "s2", running: false })],
    }),
  ];

  it("each chip counts over the WHOLE record — folded and archived groups included", () => {
    expect(filterMatchCounts(record)).toEqual({
      open: 3, // a + b (live) + d (archived, folded by default)
      in_position: 2, // b + the running span s1 (a span is a take, so it can be in position)
      needs_answer: 2, // a + d — never a span
    });
  });

  it("a chip's count is independent of what else is pressed — it is a fact about the record", () => {
    // no active set is passed at all: the number on the label answers "how many would this show",
    // so pressing a neighbor can never move it.
    const counts = filterMatchCounts(record);
    expect(filterMatchCounts(record)).toEqual(counts);
    expect(counts.open).toBe(3);
  });

  it("an empty record counts zero rather than throwing — a chip still renders its 0", () => {
    expect(filterMatchCounts([])).toEqual({ open: 0, in_position: 0, needs_answer: 0 });
  });

  it("the group heading reads its plain count at rest and 'N of M' while filtering", () => {
    const t = record[0];
    expect(groupCountLabel(t, set())).toBe("4"); // 3 episodes + 1 span
    expect(groupCountLabel(t, set("open"))).toBe("2 of 4");
    // a group the filter empties still declares every row it is holding — it never reads "0"
    expect(groupCountLabel(record[1], set("in_position"))).toBe("0 of 2");
    expect(groupMatchCount(record[1], set("in_position"))).toBe(0);
  });

  it("the tally is the receipt: showing + hidden always equals the record's own total", () => {
    expect(ledgerTally(record, set())).toEqual({ showing: 6, total: 6, hidden: 0 });
    const open = ledgerTally(record, set("open"));
    expect(open).toEqual({ showing: 3, total: 6, hidden: 3 });
    expect(open.showing + open.hidden).toBe(open.total);
    expect(ledgerTally(record, set("open", "in_position"))).toEqual({
      showing: 4,
      total: 6,
      hidden: 2,
    });
  });
});
