import { describe, expect, it } from "vitest";

import {
  adminPath,
  dashboardPath,
  dashboardsPath,
  homePath,
  scoreboardPath,
  thesisPath,
  validAsof,
  workbenchPath,
} from "../nav";

describe("validAsof — the ?asof= guard", () => {
  it("accepts a well-formed ISO date", () => {
    expect(validAsof("2026-06-01")).toBe("2026-06-01");
  });

  it("treats absent and malformed identically (null)", () => {
    expect(validAsof(null)).toBeNull();
    expect(validAsof("")).toBeNull();
    expect(validAsof("junk")).toBeNull();
    expect(validAsof("2026-6-1")).toBeNull();
    expect(validAsof("06/01/2026")).toBeNull();
    expect(validAsof("2026-06-01T12:00:00Z")).toBeNull();
  });

  it("rejects an impossible calendar date the shape-regex alone would pass", () => {
    expect(validAsof("2026-02-31")).toBeNull();
    expect(validAsof("2026-13-01")).toBeNull();
  });
});

describe("path builders", () => {
  it("omit params that are absent", () => {
    expect(homePath(null)).toBe("/");
    expect(scoreboardPath(null)).toBe("/scoreboard");
    expect(workbenchPath(null)).toBe("/workbench");
    expect(adminPath(null)).toBe("/admin");
    expect(thesisPath("t-1")).toBe("/thesis/t-1");
    expect(thesisPath("t-1", { asof: null, name: null })).toBe("/thesis/t-1");
  });

  it("carry asof when set", () => {
    expect(homePath("2026-06-01")).toBe("/?asof=2026-06-01");
    expect(scoreboardPath("2026-06-01")).toBe("/scoreboard?asof=2026-06-01");
    expect(adminPath("2026-06-01")).toBe("/admin?asof=2026-06-01");
    expect(thesisPath("t-1", { asof: "2026-06-01" })).toBe("/thesis/t-1?asof=2026-06-01");
  });

  it("the Dashboards builders: the index and one dashboard, asof carried exactly like the other paths", () => {
    expect(dashboardsPath(null)).toBe("/dashboards");
    expect(dashboardsPath("2026-06-01")).toBe("/dashboards?asof=2026-06-01");
    expect(dashboardPath("tape-breadth", null)).toBe("/dashboards/tape-breadth");
    expect(dashboardPath("tape-breadth", "2026-06-01")).toBe(
      "/dashboards/tape-breadth?asof=2026-06-01",
    );
  });

  it("dashboardPath URL-encodes the slug", () => {
    expect(dashboardPath("a b/c?d", "2026-06-01")).toBe("/dashboards/a%20b%2Fc%3Fd?asof=2026-06-01");
  });

  it("thesisPath combines asof + name and URL-encodes both the id and the name", () => {
    expect(thesisPath("t 1/x", { asof: "2026-06-01", name: "BRK.A&co" })).toBe(
      "/thesis/t%201%2Fx?asof=2026-06-01&name=BRK.A%26co",
    );
  });
});
