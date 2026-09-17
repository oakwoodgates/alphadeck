import { describe, expect, it } from "vitest";

import { breadthLine, episodeKey, indexBreadth, key1Line } from "../breadth";

// The breadth join. These fields ride the RUN's own payload rather than the Scoreboard's shared
// episode model, so the join has to be explicit — and tolerant, because the payload is an untyped
// pass-through and an older artifact simply carries fewer fields.

const ep = (over: Record<string, unknown> = {}) => ({
  thesis_id: "t1",
  security_id: "s1",
  arm_date: "2026-01-05",
  co_arm_count: 3,
  armed_count_that_night: 9,
  co_arm_bucket: "4-7",
  key1_source: "insider",
  key1_sources: ["insider", "revenue_accel"],
  confirmation_grade: "core",
  ...over,
});

describe("indexBreadth", () => {
  it("keys on the episode's own identity — thesis, security, arm date", () => {
    const idx = indexBreadth([ep()]);
    expect(idx.get(episodeKey("t1", "s1", "2026-01-05"))?.co_arm_count).toBe(3);
  });

  it("skips a row missing its key instead of throwing", () => {
    // A research surface must not white-screen on an artifact written by another engine version.
    const idx = indexBreadth([ep(), { nope: true }, null, "junk"]);
    expect(idx.size).toBe(1);
  });

  it("defaults missing breadth fields rather than inventing them", () => {
    const idx = indexBreadth([{ thesis_id: "t1", security_id: "s1", arm_date: "2026-01-05" }]);
    const b = idx.get(episodeKey("t1", "s1", "2026-01-05"))!;
    expect(b.co_arm_count).toBe(0);
    expect(b.key1_source).toBeNull();
    expect(b.key1_sources).toEqual([]);
  });
});

describe("breadthLine", () => {
  it("reports the two counts separately — they answer different questions", () => {
    // How many OTHERS newly armed that session (the decision made that night) is not the same as how
    // many were armed in total (the loudness the operator saw), and one number cannot tell them apart.
    const line = breadthLine(indexBreadth([ep()]).get(episodeKey("t1", "s1", "2026-01-05")))!;
    expect(line).toContain("3 other newly-armed names");
    expect(line).toContain("9 armed in total");
    expect(line).toContain("bucket 4-7");
  });

  it("says ARMED ALONE rather than 'alongside 0 others'", () => {
    const b = indexBreadth([ep({ co_arm_count: 0, armed_count_that_night: 1 })]).get(
      episodeKey("t1", "s1", "2026-01-05"),
    );
    expect(breadthLine(b)).toContain("armed alone");
  });

  it("is null when the run carries no breadth for the episode", () => {
    expect(breadthLine(undefined)).toBeNull();
  });
});

describe("key1Line", () => {
  it("names the strongest claim and does not hide the second one", () => {
    // 70 armed member-nights on the measured record carry two Key-1 sources; showing only the
    // strongest would quietly drop half of a real observation.
    const b = indexBreadth([ep()]).get(episodeKey("t1", "s1", "2026-01-05"));
    expect(key1Line(b)).toBe("Key 1: insider (also revenue_accel)");
  });

  it("stays a single clause when only one source fired", () => {
    const b = indexBreadth([ep({ key1_sources: ["insider"] })]).get(
      episodeKey("t1", "s1", "2026-01-05"),
    );
    expect(key1Line(b)).toBe("Key 1: insider");
  });

  it("renders nothing when no key is recorded", () => {
    const b = indexBreadth([ep({ key1_source: null, key1_sources: [] })]).get(
      episodeKey("t1", "s1", "2026-01-05"),
    );
    expect(key1Line(b)).toBeNull();
  });
});
