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

// Word-boundary matched, so a real pass whose text merely CONTAINS one of these (e.g. "recalibrate")
// is not mistaken for a smoke. Still a display heuristic — see the module note.
const CALIBRATION_RE = /\b(smoke|calibration|calibrate|dry[ -]run)\b/;

/** Does this pass look like a smoke or a calibration, by its id and its own text? */
export function looksLikeCalibration(passId: string, hypothesis: string): boolean {
  return CALIBRATION_RE.test(`${passId} ${hypothesis}`.toLowerCase());
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

// SUB-GROUPING A PASS INTO ARMS (C) — pure, unit-tested, no React.
//
// Within one pass the runs form a policy-arm × window matrix: a phase sweeps a handful of policy
// configurations, and each is replayed over every window. `config_hash` identifies the policy (it is the
// fingerprint of the whole CallConfig), so grouping the pass's runs by it recovers the arms — and each
// arm's rows then differ only in their window. The picker can say, once per arm, WHICH dials that arm
// moved and to WHAT value, and leave the rows below it to carry only what varies.
//
// This is the same discipline as the pass grouping above: it is not a filter (every run stays on screen)
// and it names what a run IS, never how it came out. There is no outcome anywhere in an arm.

/** One policy arm within a pass: a single CallConfig, and every window run of it. */
export type Arm = {
  configHash: string;
  configShort: string;
  /** The dials this arm moved off the production defaults — empty for the baseline arm. */
  dialsMoved: string[];
  /** Each moved dial → the value it took (from `dial_values`, COMMIT B). Empty for a pre-backfill run. */
  dialValues: Record<string, unknown>;
  /** The arm's runs, newest window first. */
  runs: BacktestRunSummaryOut[];
};

// Format a dial value for a label. Mirrors ManifestCard's `fmt`, except a missing value reads as "none"
// rather than an em dash: a label is prose ("liveness = none" reads; "liveness = —" does not).
function fmtDialValue(v: unknown): string {
  if (v === null || v === undefined) return "none";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** A one-line name for an arm — what it IS. The baseline says so; a variant names each moved dial and the
 *  value it took. The value comes from `dial_values`; a run that predates that field (pre-backfill) has
 *  none, and then the bare dial name stands in rather than an empty "name = ". */
export function armLabel(arm: Arm): string {
  if (arm.dialsMoved.length === 0) return "baseline · production dials";
  return arm.dialsMoved
    .map((d) => (d in arm.dialValues ? `${d} = ${fmtDialValue(arm.dialValues[d])}` : d))
    .join(", ");
}

/** A pass's runs, split into arms by `config_hash`. The baseline arm (no dial moved) sorts FIRST, then the
 *  variants by `config_short` — a stable, outcome-free order. Runs within an arm are newest window first.
 *
 *  A mixed store (some rows enriched by the backfill, some not) can put a value-less row first, so the
 *  arm takes its `dial_values` from whichever of its runs carries them — the label is filled whenever ANY
 *  run of the arm has been enriched. */
export function groupArms(runs: readonly BacktestRunSummaryOut[]): Arm[] {
  const byConfig = new Map<string, Arm>();
  for (const r of runs) {
    let a = byConfig.get(r.config_hash);
    if (!a) {
      a = {
        configHash: r.config_hash,
        configShort: r.config_short,
        dialsMoved: r.dials_moved ?? [],
        dialValues: r.dial_values ?? {},
        runs: [],
      };
      byConfig.set(r.config_hash, a);
    }
    if (Object.keys(a.dialValues).length === 0 && Object.keys(r.dial_values ?? {}).length > 0) {
      a.dialValues = r.dial_values ?? {};
    }
    a.runs.push(r);
  }
  const arms = [...byConfig.values()];
  // window_start is an ISO date, so a string compare is chronological — newest window first.
  for (const a of arms) {
    a.runs.sort((x, y) =>
      x.window_start < y.window_start ? 1 : x.window_start > y.window_start ? -1 : 0,
    );
  }
  arms.sort((a, b) => {
    const aBase = a.dialsMoved.length === 0;
    const bBase = b.dialsMoved.length === 0;
    if (aBase !== bBase) return aBase ? -1 : 1; // baseline first
    return a.configShort < b.configShort ? -1 : a.configShort > b.configShort ? 1 : 0;
  });
  return arms;
}
