import type { OperatorSpanOut, ScoreboardEpisodeOut, ScoreboardThesisOut } from "../api/hooks";
import { groupCount } from "./rows";

/** Filtering the Scoreboard ledger — the ledger's FIRST true hide-from-view control, and the reason
 *  this module reads the way it does.
 *
 *  Everything else on this surface re-arranges: `sortLedger` is documented as "a re-order, never a
 *  filter", the Cockpit's lens re-groups, the triage find-bar grays. This one actually removes rows,
 *  which is the thing interaction principle #2 ("pruning hides, it never vanishes") is written about.
 *  So it is built as the compliant kind of hide, and the three rules are structural, not styling:
 *
 *  1. **Never the default.** Zero chips active is the identity — `filterEpisodes`/`filterSpans`
 *     return their input, same length, same order. The operator has to ASK for a smaller ledger, and
 *     `Clear` puts it back in one click.
 *  2. **Nothing vanishes silently.** `ledgerTally` is what the host needs to say "showing X of Y
 *     rows · N hidden", and `groupCountLabel` keeps a thesis heading honest ("2 of 4") instead of
 *     letting a group quietly shrink. A group whose every row filters out still renders its heading
 *     and says how many it is holding back — a filter must never make a thesis look like it has no
 *     record.
 *  3. **Honest loudness.** `filterMatchCounts` gives each chip its own live number, counted over the
 *     WHOLE record (every thesis, folded or not) so a count answers "how many are there" rather than
 *     "how many can I see right now" — and a chip with none says 0 rather than disappearing.
 *
 *  Pure + unit-tested, the `rows.ts` / `sortLedger.ts` idiom: wire rows in, wire rows out, no React,
 *  no state (the host holds it). Applied BEFORE the sort, never folded into it — `sortEpisodes` keeps
 *  its "same length in, same length out" contract precisely because filtering is a separate step.
 *
 *  Scope: the LIVE record ledger only. `ReplayPanel` (the historical, replayed panel) is deliberately
 *  untouched — its rows predate decision capture, so two of the three chips could only ever answer
 *  "no" there, which is a control that cannot discriminate (#7). */

/** The three questions the operator asks the record, as chips. Deliberately a small closed set:
 *  each one is a state the ledger can answer from the wire without inventing a judgment. */
export type LedgerFilterId = "open" | "in_position" | "needs_answer";

/** Render order + copy for the chip bar. The labels are the question, not the field name. */
export const LEDGER_FILTERS: readonly { id: LedgerFilterId; label: string; title: string }[] = [
  { id: "open", label: "Open", title: "still armed at the record edge" },
  {
    id: "in_position",
    label: "In position",
    title: "the operator took it and is still holding — a closed-out take is not in position",
  },
  {
    id: "needs_answer",
    label: "Needs an answer",
    title: "still armed, and no decision has been logged against it",
  },
];

/** The EPISODE predicates. `in_position` reads STILL HOLDING, not "ever taken": a take the operator
 *  has closed out is a finished decision, and listing it beside live positions would make the chip
 *  answer a different question than its label. */
const EPISODE_MATCH: Record<LedgerFilterId, (e: ScoreboardEpisodeOut) => boolean> = {
  open: (e) => e.status === "open",
  in_position: (e) => e.operator?.action === "took" && e.operator.running === true,
  needs_answer: (e) => e.status === "open" && e.operator == null,
};

/** The SPAN predicates. An off-record operator span is a logged TAKE, not an arm — the same asymmetry
 *  `sortLedger` encodes as absent keys — so two of the three chips can never match one:
 *
 *  - `open` asks about an ARMED state a span has never had (there is nothing to be open about).
 *  - `needs_answer` asks for a row with no decision logged, and a span IS the logged decision. It is
 *    structurally impossible, not merely rare.
 *
 *  Both return false as a statement about the row kind, rather than being left out of the map and
 *  read as an oversight. */
const SPAN_MATCH: Record<LedgerFilterId, (s: OperatorSpanOut) => boolean> = {
  open: () => false,
  in_position: (s) => s.running === true,
  needs_answer: () => false,
};

/** Does this episode match the given chip, on its own? (The single-chip contract, exported so the
 *  predicates can be stated and tested directly rather than only through a combination.) */
export function episodeMatches(id: LedgerFilterId, e: ScoreboardEpisodeOut): boolean {
  return EPISODE_MATCH[id](e);
}

/** Does this off-record span match the given chip, on its own? */
export function spanMatches(id: LedgerFilterId, s: OperatorSpanOut): boolean {
  return SPAN_MATCH[id](s);
}

/** The active set as a UNION, never an intersection.
 *
 *  One chip gives exactly "only X". Two give the live picture — open ∪ in-position is "everything I
 *  still have to think about", which is the actual question. AND would be worse than merely narrow:
 *  `in_position` requires a logged take and `needs_answer` requires that no decision exists, so that
 *  pair is empty BY CONSTRUCTION — a control whose two-chip result can only ever be an empty ledger
 *  is a trap, not a filter. */
function matchesAny<T>(
  row: T,
  active: ReadonlySet<LedgerFilterId>,
  test: (id: LedgerFilterId, row: T) => boolean,
): boolean {
  for (const id of active) if (test(id, row)) return true;
  return false;
}

/** Filter ONE group's episodes. An empty active set is the IDENTITY — the same rows, same order,
 *  same length — because "no filter" must be indistinguishable from "before the chips existed". */
export function filterEpisodes(
  eps: readonly ScoreboardEpisodeOut[],
  active: ReadonlySet<LedgerFilterId>,
): ScoreboardEpisodeOut[] {
  if (active.size === 0) return [...eps];
  return eps.filter((e) => matchesAny(e, active, episodeMatches));
}

/** Filter ONE group's off-record spans, under the same identity rule. */
export function filterSpans(
  spans: readonly OperatorSpanOut[],
  active: ReadonlySet<LedgerFilterId>,
): OperatorSpanOut[] {
  if (active.size === 0) return [...spans];
  return spans.filter((s) => matchesAny(s, active, spanMatches));
}

/** How many rows a group would show under the active set (episodes + spans, the `groupCount` pair). */
export function groupMatchCount(
  t: ScoreboardThesisOut,
  active: ReadonlySet<LedgerFilterId>,
): number {
  if (active.size === 0) return groupCount(t);
  return (
    filterEpisodes(t.episodes, active).length + filterSpans(t.operator_spans, active).length
  );
}

/** The thesis heading's count — `groupCount` unchanged while nothing is filtered, and "N of M" while
 *  something is. Wrapping rather than rewriting `groupCount` keeps ONE definition of what a group's
 *  rows are; and the heading has to keep declaring M because fold state is the operator's own and a
 *  folded group must still say what it is holding, filter or no filter. */
export function groupCountLabel(
  t: ScoreboardThesisOut,
  active: ReadonlySet<LedgerFilterId>,
): string {
  const total = groupCount(t);
  if (active.size === 0) return String(total);
  return `${groupMatchCount(t, active)} of ${total}`;
}

/** Each chip's own match count, over EVERY row in the record. Counted per chip INDEPENDENTLY (not
 *  against the active set), so a chip's number never moves when a neighbor is pressed — the label
 *  answers "how many rows would this chip show", which is a fact about the record, not about the
 *  current selection. Folded groups are counted too: fold is a view state, and a count that dropped
 *  when the operator collapsed a thesis would be reporting the furniture. */
export function filterMatchCounts(
  theses: readonly ScoreboardThesisOut[],
): Record<LedgerFilterId, number> {
  const out: Record<LedgerFilterId, number> = { open: 0, in_position: 0, needs_answer: 0 };
  for (const t of theses) {
    for (const f of LEDGER_FILTERS) {
      out[f.id] +=
        t.episodes.filter((e) => episodeMatches(f.id, e)).length +
        t.operator_spans.filter((s) => spanMatches(f.id, s)).length;
    }
  }
  return out;
}

/** The "showing X of Y rows · N hidden by filter" arithmetic — the receipt that keeps a hide from
 *  being a vanish (#2). Over the whole record, like the chip counts: the line reports what the
 *  FILTER did, and folding is a separate, already-visible act of the operator's own. */
export function ledgerTally(
  theses: readonly ScoreboardThesisOut[],
  active: ReadonlySet<LedgerFilterId>,
): { showing: number; total: number; hidden: number } {
  let total = 0;
  let showing = 0;
  for (const t of theses) {
    total += groupCount(t);
    showing += groupMatchCount(t, active);
  }
  return { showing, total, hidden: total - showing };
}

/** Flip one chip. Pure, returning a NEW set (the host holds the state, the `nextLedgerSort` idiom) —
 *  and every press has its own inverse, which is interaction principle #1 at the state level rather
 *  than only in the `Clear` button. */
export function toggleLedgerFilter(
  active: ReadonlySet<LedgerFilterId>,
  id: LedgerFilterId,
): Set<LedgerFilterId> {
  const next = new Set(active);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}
