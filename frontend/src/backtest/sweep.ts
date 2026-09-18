// Pure display logic for the SWEEP curve (unit-tested, no React).
//
// The sweep payload is an untyped pass-through on the wire, because `backtest.sweep` imports the run
// writer and therefore pyarrow — which the lean api image does not carry, so its models cannot be
// published into the contract. This module is the tolerant reader for it: every field is optional,
// every absence degrades to "—", and a sweep written by an older engine renders what it has.
//
// THE RULE THIS FILE EXISTS TO HOLD: a sweep shows a CURVE, never a winner. There is no "best",
// no argmax, no sort by metric and no highlight of the top point. What is marked is the PLATEAU — a
// contiguous band the backend computed — and a plateau one point wide is the sweep finding nothing,
// which the copy says out loud rather than dressing up as a result.

export type SweepPointView = {
  /** Every run behind this point — ONE PER WINDOW (S1). A point is the pooled read across its windows,
   *  so it cites N runs, not one, and each of them stays addressable. */
  runIds: string[];
  configShort: string;
  /** The dial values at this point, as "dial=value" pairs in the backend's own order. */
  dials: { name: string; value: string }[];
  nEpisodes: number;
  nScored: number;
  metric: number | null;
  delta: number | null;
  /** The delta recomputed on each WINDOW, in the report's window order. */
  windowDeltas: (number | null)[];
  signAgreement: boolean;
  /** Every window strictly the same non-zero direction, and none unmeasurable — the rule from
   *  2026-09-18. Reported BESIDE `signAgreement`, which is never rewritten, so a pass registered under
   *  the older rule can still be read the way it was registered. */
  strictSignAgreement: boolean;
  /** Every MEASURABLE window strictly the same non-zero direction, at least two of them (A2b). The rule
   *  a SLICED curve needs: strict-over-all is unsatisfiable by construction when a family leaves windows
   *  empty. Never read without `nUnmeasurable`. */
  strictMeasurableAgreement: boolean;
  /** How many windows held nothing this point could be measured on. */
  nUnmeasurable: number;
  /** `moved_up | moved_down | unchanged | unmeasurable` per window, in window order. An unmeasurable
   *  window held no episode this dial could touch — it is not a zero. */
  windowStatus: string[];
  isBaseline: boolean;
  /** Two dial settings that resolve to the same config are ONE measurement — usually the baseline, which
   *  every ladder containing the production default shares. Shown so a shared point is never read as an
   *  independent confirmation. */
  runsShared: boolean;
  inPlateau: boolean;
};

export type SweepView = {
  dialNames: string[];
  metricName: string;
  /** The disjoint windows every point was run over, in order — a point's `windowDeltas` line up with
   *  these index for index. Empty on a curve written before the split. */
  windows: { start: string; end: string }[];
  /** How many window jobs ran at once. A saturated box measures contention as well as dials, so two
   *  passes are only comparable on wall time if this matches. */
  concurrency: number;
  /** False when the grid did not contain the production default and the first point stood in as the
   *  baseline. Every delta is then relative to a CHOSEN setting rather than to today's behavior, which is
   *  a different claim — so it is rendered only when false (honest loudness: mark the exception). */
  baselineIsDefault: boolean;
  /** The id every run of this curve carries on its own manifest — the grouping that survives even though
   *  `sweep.json` is latest-only. */
  passId: string;
  /** Which fact axis the one shared mirror carried — every point inherited it (CW). A record sweep and a
   *  public sweep are two experiments and must never be read as one series, so the curve says which. */
  clock: string;
  windowStart: string | null;
  windowEnd: string | null;
  subwindows: number;
  baselineConfigShort: string;
  mirrorHash: string;
  banner: string;
  /** WHICH cross-window agreement the band was keyed on. `strict_sign_agreement` (every window moved
   *  the same way, none unchanged and none unmeasurable) is the rule for passes registered from
   *  2026-09-18 on; `sign_agreement` is what the first pre-registered pass was read under, and a curve
   *  written before the field is one whose band used that rule — the fallback states a fact. */
  plateauRule: string;
  /** `key1_source=ratified_catalyst` when this curve was read on ONE algorithm slice, else empty. Said
   *  loudly on the surface because every n, level and delta on a sliced curve is the SLICE's: read
   *  against the pool they would all be wrong. */
  metricSlice: string;
  points: SweepPointView[];
  plateauWidth: number;
};

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

export function readSweep(raw: unknown): SweepView | null {
  if (!raw || typeof raw !== "object") return null;
  const s = raw as Record<string, unknown>;
  const plateau = new Set(
    Array.isArray(s.plateau) ? s.plateau.filter((i): i is number => typeof i === "number") : [],
  );
  const rawPoints = Array.isArray(s.points) ? s.points : [];
  const points: SweepPointView[] = rawPoints.map((p, i) => {
    const o = (p ?? {}) as Record<string, unknown>;
    const dials = (o.dials ?? {}) as Record<string, unknown>;
    // `window_deltas` (S1) with a fallback to the pre-split `subwindow_deltas`, and `run_ids` with a
    // fallback to the single `run_id`: an older curve still renders rather than reading as empty.
    const deltas = Array.isArray(o.window_deltas)
      ? o.window_deltas
      : Array.isArray(o.subwindow_deltas)
        ? o.subwindow_deltas
        : [];
    const ids = Array.isArray(o.run_ids)
      ? o.run_ids.filter((r): r is string => typeof r === "string")
      : typeof o.run_id === "string"
        ? [o.run_id]
        : [];
    return {
      runIds: ids,
      configShort: str(o.config_short),
      dials: Object.entries(dials).map(([name, value]) => ({ name, value: String(value) })),
      nEpisodes: num(o.n_episodes) ?? 0,
      nScored: num(o.n_scored) ?? 0,
      metric: num(o.metric),
      delta: num(o.delta_vs_baseline),
      windowDeltas: deltas.map(num),
      signAgreement: o.sign_agreement === true,
      strictSignAgreement: o.strict_sign_agreement === true,
      strictMeasurableAgreement: o.strict_measurable_agreement === true,
      nUnmeasurable: num(o.n_unmeasurable) ?? 0,
      windowStatus: Array.isArray(o.window_status) ? o.window_status.map((w) => String(w)) : [],
      isBaseline: o.is_baseline === true,
      runsShared: o.runs_shared === true,
      inPlateau: plateau.has(i),
    };
  });
  return {
    dialNames: Array.isArray(s.dial_names) ? s.dial_names.map((d) => String(d)) : [],
    metricName: str(s.metric_name, "metric"),
    windows: Array.isArray(s.windows)
      ? s.windows
          .filter((w): w is unknown[] => Array.isArray(w) && w.length >= 2)
          .map((w) => ({ start: String(w[0]), end: String(w[1]) }))
      : [],
    concurrency: num(s.concurrency) ?? 1,
    // a curve written before the field is one whose baseline WAS the default; that is what it meant then
    baselineIsDefault: s.baseline_is_default !== false,
    passId: str(s.pass_id),
    // a curve written before the axis was recorded is a record-clock curve — that is all this exporter
    // could produce at the time, so the fallback states a fact rather than an unknown
    clock: str(s.clock, "record"),
    windowStart: typeof s.window_start === "string" ? s.window_start : null,
    windowEnd: typeof s.window_end === "string" ? s.window_end : null,
    subwindows: num(s.subwindows) ?? 0,
    baselineConfigShort: str(s.baseline_config_short),
    mirrorHash: str(s.mirror_hash),
    banner: str(s.banner),
    plateauRule: str(s.plateau_rule, "sign_agreement"),
    metricSlice: str(s.metric_slice),
    points,
    plateauWidth: plateau.size,
  };
}

/** A signed percentage-point delta — "+1.4pp" — or "—" when the point has no comparable number. */
export function fmtDelta(x: number | null): string {
  if (x === null) return "—";
  const pp = (x * 100).toFixed(1);
  return `${x > 0 ? "+" : ""}${pp}pp`;
}

/** A signed percentage — the point's own metric level. */
export function fmtMetric(x: number | null): string {
  if (x === null) return "—";
  const pct = (x * 100).toFixed(1);
  return `${x > 0 ? "+" : ""}${pct}%`;
}

/** What the plateau MEANS, in one sentence, including the case where it means nothing.
 *
 *  A one-point plateau is the honest null result of a sweep and the most likely outcome, so it gets
 *  the plainest words available. Nothing here names a best point: the widest band is the only thing a
 *  sweep licenses, and a band of one is not a band. */
export function plateauLine(v: SweepView): string {
  if (!v.points.length) return "No points in this sweep.";
  if (v.plateauWidth <= 1) {
    return (
      "No plateau: the settings that behaved alike do not form a band wider than a single point. " +
      "That is a sweep finding nothing worth adopting — a lone high point is noise until it has " +
      `neighbors that agree with it. (${ruleLine(v)})`
    );
  }
  const agreeing = v.points.filter((p) => p.inPlateau && agreesUnderBandRule(v, p)).length;
  const n = v.windows.length || v.subwindows;
  const missing = unmeasurableLine(v);
  return (
    `A plateau ${v.plateauWidth} points wide. ` +
    `${agreeing} of them also hold their sign across all ${n} window${n === 1 ? "" : "s"} — ` +
    "a pooled number that cannot survive being recomputed on each window separately has not found " +
    `anything. (${ruleLine(v)})` +
    (missing ? ` ${missing}` : "")
  );
}

/** Does this point agree under the rule THIS curve's band was keyed on?
 *
 *  Read off the curve rather than fixed, because the rule changed and BOTH fields ride every point: a
 *  band keyed on the strict rule must not be described with the other rule's counts. */
export function agreesUnderBandRule(v: SweepView, p: SweepPointView): boolean {
  if (v.plateauRule === "strict_sign_agreement") return p.strictSignAgreement;
  if (v.plateauRule === "strict_measurable_agreement") return p.strictMeasurableAgreement;
  return p.signAgreement;
}

/** The band's rule, in the words a reader needs rather than the field name. */
export function ruleLine(v: SweepView): string {
  if (v.plateauRule === "strict_sign_agreement") {
    return (
      "band keyed on STRICT agreement: every window moved the same way, none unchanged and none " +
      "unmeasurable"
    );
  }
  if (v.plateauRule === "strict_measurable_agreement") {
    return (
      "band keyed on STRICT agreement over the windows that could answer: every measurable window " +
      "moved the same way, at least two of them — a window that did not move still withholds " +
      "agreement, a window with nothing in play is excluded and counted"
    );
  }
  return (
    "band keyed on the pre-2026-09-18 rule, under which a window that did not move — or held no " +
    "episode to move — still counts as agreeing"
  );
}

/** What the band could NOT see, in the band's own words. Empty when every window was measurable.
 *
 *  It rides the band's sentence rather than a column because it qualifies the BAND: "agrees across the
 *  seven windows that had episodes" and "agrees across nine" are different claims, and only one of them
 *  is what a sliced curve can support. */
export function unmeasurableLine(v: SweepView): string {
  const band = v.points.filter((p) => p.inPlateau);
  if (!band.length) return "";
  const counts = band.map((p) => p.nUnmeasurable);
  const lo = Math.min(...counts);
  const hi = Math.max(...counts);
  if (hi === 0) return "";
  const n = v.windows.length || v.subwindows;
  const many = lo === hi ? `${hi}` : `${lo}–${hi}`;
  return (
    `${many} of ${n} window${n === 1 ? "" : "s"} held nothing these settings could be measured on, ` +
    "and were excluded from the agreement rather than counted as agreeing."
  );
}

/** What each window did to a point, in words. The unmeasurable one is what this exists to say out
 *  loud: it is not a zero, it is a window that held nothing the dial could touch. */
export function statusLine(p: SweepPointView): string {
  const words: Record<string, string> = {
    moved_up: "up",
    moved_down: "down",
    unchanged: "no change",
    unmeasurable: "nothing to measure",
  };
  return p.windowStatus.map((st) => words[st] ?? st).join(" · ");
}
