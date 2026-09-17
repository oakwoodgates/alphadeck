import { describe, expect, it } from "vitest";

import { fmtDelta, fmtMetric, plateauLine, readSweep } from "../sweep";

// The sweep's reader and its copy. One rule dominates: a sweep shows a CURVE, never a winner — so the
// reader preserves the backend's order, marks only the plateau, and the null result ("no plateau") is
// stated plainly instead of being dressed up as a best point.

const point = (over: Record<string, unknown> = {}) => ({
  dials: { insider_core_alpha_liveness_days: 180 },
  run_id: "r1",
  config_short: "abcd1234",
  n_episodes: 12,
  n_scored: 9,
  metric: 0.04,
  delta_vs_baseline: 0.01,
  subwindow_deltas: [0.01, 0.02],
  sign_agreement: true,
  is_baseline: false,
  ...over,
});

const sweep = (over: Record<string, unknown> = {}) => ({
  clock: "public",
  dial_names: ["insider_core_alpha_liveness_days"],
  metric_name: "arm_timing_forward_return_median",
  window_start: "2025-09-01",
  window_end: "2026-09-14",
  subwindows: 2,
  mirror_hash: "c".repeat(64),
  baseline_config_short: "abcd1234",
  points: [point({ run_id: "r1" }), point({ run_id: "r2" }), point({ run_id: "r3" })],
  plateau: [0, 1],
  banner: "a curve, not a winner",
  ...over,
});

describe("readSweep", () => {
  it("keeps the backend's point ORDER — never re-sorted by outcome", () => {
    const v = readSweep(
      sweep({
        points: [
          point({ run_id: "low", metric: -0.2 }),
          point({ run_id: "high", metric: 0.9 }),
          point({ run_id: "mid", metric: 0.1 }),
        ],
        plateau: [],
      }),
    );
    expect(v?.points.map((p) => p.runId)).toEqual(["low", "high", "mid"]);
  });

  it("marks membership of the PLATEAU and nothing else", () => {
    const v = readSweep(sweep());
    expect(v?.points.map((p) => p.inPlateau)).toEqual([true, true, false]);
    expect(v?.plateauWidth).toBe(2);
  });

  it("carries the fact axis the one shared mirror was exported on", () => {
    // A record sweep and a public sweep are two experiments; reading them as one series is the mistake
    // this field exists to prevent, so it has to survive onto the curve and not just onto each run.
    expect(readSweep(sweep())?.clock).toBe("public");
  });

  it("reads a curve written before the axis was recorded as the record clock", () => {
    // Not "unknown": the record clock is all the exporter could produce at the time.
    expect(readSweep(sweep({ clock: undefined }))?.clock).toBe("record");
  });

  it("tolerates a sweep written by an older engine", () => {
    // Untyped pass-through on the wire (the sweep module imports the run writer, so its models cannot
    // be published into the lean contract). Absence degrades; it never throws.
    const v = readSweep({ points: [{ run_id: "r1" }] });
    expect(v?.points[0].metric).toBeNull();
    expect(v?.points[0].dials).toEqual([]);
    expect(v?.plateauWidth).toBe(0);
  });

  it("is null for a payload that is not a sweep at all", () => {
    expect(readSweep(null)).toBeNull();
    expect(readSweep("nope")).toBeNull();
  });
});

describe("fmtDelta / fmtMetric", () => {
  it("signs a delta in percentage POINTS and a level in percent", () => {
    expect(fmtDelta(0.014)).toBe("+1.4pp");
    expect(fmtDelta(-0.014)).toBe("-1.4pp");
    expect(fmtMetric(0.041)).toBe("+4.1%");
  });

  it("dashes an unknown figure rather than printing a zero", () => {
    expect(fmtDelta(null)).toBe("—");
    expect(fmtMetric(null)).toBe("—");
  });
});

describe("plateauLine", () => {
  it("states the NULL RESULT plainly when no band formed", () => {
    // The most likely outcome of a sweep, and the one a research surface must not dress up.
    const v = readSweep(sweep({ plateau: [1] }))!;
    expect(plateauLine(v)).toContain("No plateau");
    expect(plateauLine(v)).toContain("noise until it has");
  });

  it("names a real band by its WIDTH and its sub-window agreement, never by a best point", () => {
    const v = readSweep(sweep())!;
    const line = plateauLine(v);
    expect(line).toContain("2 points wide");
    expect(line).toContain("sub-windows");
    expect(line.toLowerCase()).not.toContain("best");
    expect(line.toLowerCase()).not.toContain("optimal");
  });

  it("says so when the sweep has no points at all", () => {
    const v = readSweep(sweep({ points: [], plateau: [] }))!;
    expect(plateauLine(v)).toBe("No points in this sweep.");
  });
});
