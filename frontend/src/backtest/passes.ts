import type { BacktestRunSummaryOut, BacktestSweepRefOut } from "../api/hooks";

// GROUPING BY PASS — pure, unit-tested, no React.
//
// A windowed pass writes dozens of runs and one curve per dial (S1/S2), so a flat registry stopped being
// readable the first time a real pass landed: 225 rows of one experiment, with the four smoke runs that
// preceded it scattered among them. What a reader wants is the SHAPE of the work — this pass, then the
// ones before it, with the calibration noise folded away.
//
// Two rules run through all of it.
//
// **Grouping is never filtering.** Every run and every curve is on screen; the groups only decide what is
// expanded. A row that disappeared would be a run that did not happen, and the registry's whole job is
// counting trials — an experiment you cannot see is one you cannot count against yourself.
//
// **The calibration split is a DISPLAY heuristic and says so.** A smoke or calibration pass is recognized
// by its own pre-registration text, which is operator prose and could be worded any way at all. That is
// acceptable precisely because the consequence is a collapsed group rather than a hidden row: if the
// heuristic misses, the pass renders in the main list, which is the honest failure direction.

/** A pass's runs or curves, in one group. */
export type PassGroup<T> = {
  passId: string;
  /** The pre-registered hypothesis, from the first member that carries one. */
  hypothesis: string;
  /** True when this looks like a smoke or calibration pass — see the module note. */
  calibration: boolean;
  items: T[];
};

const CALIBRATION_WORDS = ["smoke", "calibration", "calibrate", "dry run", "dry-run"];

/** Does this pass look like a smoke or a calibration, by its id and its own text? */
export function looksLikeCalibration(passId: string, hypothesis: string): boolean {
  const hay = `${passId} ${hypothesis}`.toLowerCase();
  return CALIBRATION_WORDS.some((w) => hay.includes(w));
}

/** A stable label for a run or curve with no pass id at all — a standalone run, which is a legitimate
 *  thing to have done and is not "ungrouped junk". */
export const STANDALONE = "(standalone runs)";

function group<T>(
  items: readonly T[],
  passIdOf: (x: T) => string,
  hypothesisOf: (x: T) => string,
): PassGroup<T>[] {
  const byPass = new Map<string, PassGroup<T>>();
  for (const it of items) {
    const passId = passIdOf(it) || STANDALONE;
    let g = byPass.get(passId);
    if (!g) {
      g = { passId, hypothesis: "", calibration: false, items: [] };
      byPass.set(passId, g);
    }
    g.items.push(it);
    if (!g.hypothesis) g.hypothesis = hypothesisOf(it) || "";
  }
  const out = [...byPass.values()];
  for (const g of out) {
    g.calibration = g.passId !== STANDALONE && looksLikeCalibration(g.passId, g.hypothesis);
  }
  // Newest pass first — a pass id begins with its own UTC timestamp, so this is chronological without a
  // second field to trust. Standalone runs sort last: they are not a pass, and a pass id never begins
  // with a bracket.
  out.sort((a, b) => (a.passId < b.passId ? 1 : a.passId > b.passId ? -1 : 0));
  return out;
}

/** The registry, grouped by pass. Order preserved within a group (the registry is newest first). */
export function groupRuns(runs: readonly BacktestRunSummaryOut[]): PassGroup<BacktestRunSummaryOut>[] {
  return group(
    runs,
    (r) => r.pass_id ?? "",
    (r) => r.hypothesis ?? "",
  );
}

/** The kept curves, grouped by pass. */
export function groupSweeps(sweeps: readonly BacktestSweepRefOut[]): PassGroup<BacktestSweepRefOut>[] {
  return group(
    sweeps,
    (s) => s.pass_id,
    (s) => s.hypothesis,
  );
}

/** Which groups open by default: the newest REAL pass, and nothing else.
 *
 *  Not "the newest group": a calibration pass immediately precedes the pass it calibrated, so keying on
 *  recency alone would open the smokes and collapse the experiment — which is exactly backwards. A store
 *  holding only calibrations opens the newest of them, because an empty page is worse than a smoke. */
export function defaultOpen<T>(groups: readonly PassGroup<T>[]): string | null {
  return (groups.find((g) => !g.calibration) ?? groups[0])?.passId ?? null;
}

/** One line describing a group for its header — what it IS, never how it came out. */
export function groupLabel<T>(g: PassGroup<T>, noun: string): string {
  const n = g.items.length;
  const kind = g.calibration ? " · calibration / smoke" : "";
  return `${g.passId} · ${n} ${noun}${n === 1 ? "" : "s"}${kind}`;
}
