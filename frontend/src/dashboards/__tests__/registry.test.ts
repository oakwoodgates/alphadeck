import { readdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { lazy } from "react";
import { describe, expect, it } from "vitest";

import { DASHBOARDS, HOME_DASHBOARD, buildRegistry, type DashboardEntry } from "../registry";

// The self-registering registry (docs/DASHBOARDS.md rule 8): every <slug>/entry.ts is collected by
// import.meta.glob, sorted by `order` then label, and a duplicate slug fails loud. The real list is pinned
// against the folders on disk, so a dashboard folder without an entry.ts can never silently fall off the menu.

const HERE = dirname(fileURLToPath(import.meta.url));
const DASHBOARDS_DIR = join(HERE, "..");

const fake = (slug: string, order: number, label = slug): DashboardEntry => ({
  slug,
  order,
  label,
  description: `${label}.`,
  status: "experimental",
  asofAware: true,
  component: lazy(() => Promise.resolve({ default: () => null })),
});

const mod = (entry: DashboardEntry) => ({ entry });

describe("the dashboard registry — the real entries", () => {
  it("collects every dashboard folder's entry.ts, the Board first, in today's menu order", () => {
    expect(DASHBOARDS.map((d) => d.slug)).toEqual([
      "board",
      "shared-names",
      "idea-timeline",
      "rotation-map",
      "what-changed",
      "cross-radar",
      "catalyst-calendar",
      "leaders",
      "landing",
      "narrative-index",
      "rotation-quadrant",
      "chain-wave",
      "filing-stream",
      "read-through",
    ]);
    expect(DASHBOARDS.map((d) => d.order)).toEqual([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130]);
    expect(DASHBOARDS[0]).toMatchObject({ slug: "board", status: "core", layout: "fill", asofAware: true });
  });

  it("every folder under dashboards/ registers itself (no folder without an entry.ts, no entry without a folder)", () => {
    const folders = readdirSync(DASHBOARDS_DIR, { withFileTypes: true })
      .filter((d) => d.isDirectory() && !d.name.startsWith("__"))
      .map((d) => d.name)
      .sort();
    for (const f of folders) expect(existsSync(join(DASHBOARDS_DIR, f, "entry.ts")), `${f}/entry.ts`).toBe(true);
    expect([...DASHBOARDS.map((d) => d.slug)].sort()).toEqual(folders);
  });

  it("names the home dashboard among its entries", () => {
    expect(DASHBOARDS.some((d) => d.slug === HOME_DASHBOARD)).toBe(true);
  });
});

describe("the dashboard registry — buildRegistry", () => {
  it("sorts by order, then label, whatever order the modules arrive in", () => {
    const got = buildRegistry({
      "./zeta/entry.ts": mod(fake("zeta", 20, "Zeta")),
      "./beta/entry.ts": mod(fake("beta", 10, "Beta")),
      "./alpha/entry.ts": mod(fake("alpha", 10, "Alpha")),
      "./home/entry.ts": mod(fake("home", 0, "Home")),
    });
    expect(got.map((d) => d.slug)).toEqual(["home", "alpha", "beta", "zeta"]);
  });

  it("fails loud on a duplicate slug", () => {
    expect(() =>
      buildRegistry({
        "./alpha/entry.ts": mod(fake("alpha", 10)),
        "./copy/alpha/entry.ts": mod(fake("alpha", 20)),
      }),
    ).toThrow('duplicate slug "alpha" (./alpha/entry.ts and ./copy/alpha/entry.ts)');
  });

  it("fails loud on a module with no entry, or an entry whose slug is not its folder", () => {
    expect(() => buildRegistry({ "./alpha/entry.ts": {} })).toThrow("does not export `entry`");
    expect(() => buildRegistry({ "./alpha/entry.ts": mod(fake("beta", 10)) })).toThrow(
      'registers slug "beta", not its folder "alpha"',
    );
  });
});
