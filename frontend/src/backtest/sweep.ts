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
  runId: string;
  configShort: string;
  /** The dial values at this point, as "dial=value" pairs in the backend's own order. */
  dials: { name: string; value: string }[];
  nEpisodes: number;
  nScored: number;
  metric: number | null;
  delta: number | null;
  subwindowDeltas: (number | null)[];
  signAgreement: boolean;
  isBaseline: boolean;
  inPlateau: boolean;
};

export type SweepView = {
  dialNames: string[];
  metricName: string;
  /** Which fact axis the one shared mirror carried — every point inherited it (CW). A record sweep and a
   *  public sweep are two experiments and must never be read as one series, so the curve says which. */
  clock: string;
  windowStart: string | null;
  windowEnd: string | null;
  subwindows: number;
  baselineConfigShort: string;
  mirrorHash: string;
  banner: string;
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
    return {
      runId: str(o.run_id),
      configShort: str(o.config_short),
      dials: Object.entries(dials).map(([name, value]) => ({ name, value: String(value) })),
      nEpisodes: num(o.n_episodes) ?? 0,
      nScored: num(o.n_scored) ?? 0,
      metric: num(o.metric),
      delta: num(o.delta_vs_baseline),
      subwindowDeltas: Array.isArray(o.subwindow_deltas) ? o.subwindow_deltas.map(num) : [],
      signAgreement: o.sign_agreement === true,
      isBaseline: o.is_baseline === true,
      inPlateau: plateau.has(i),
    };
  });
  return {
    dialNames: Array.isArray(s.dial_names) ? s.dial_names.map((d) => String(d)) : [],
    metricName: str(s.metric_name, "metric"),
    // a curve written before the axis was recorded is a record-clock curve — that is all this exporter
    // could produce at the time, so the fallback states a fact rather than an unknown
    clock: str(s.clock, "record"),
    windowStart: typeof s.window_start === "string" ? s.window_start : null,
    windowEnd: typeof s.window_end === "string" ? s.window_end : null,
    subwindows: num(s.subwindows) ?? 0,
    baselineConfigShort: str(s.baseline_config_short),
    mirrorHash: str(s.mirror_hash),
    banner: str(s.banner),
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
      "neighbors that agree with it."
    );
  }
  const agreeing = v.points.filter((p) => p.inPlateau && p.signAgreement).length;
  return (
    `A plateau ${v.plateauWidth} points wide. ` +
    `${agreeing} of them also hold their sign across all ${v.subwindows} sub-windows — ` +
    "a pooled number that cannot survive cutting the window has not found anything."
  );
}
