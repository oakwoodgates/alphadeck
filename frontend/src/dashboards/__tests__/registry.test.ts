import { readdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { lazy } from "react";
import { describe, expect, it } from "vitest";

import { DASHBOARDS, HOME_DASHBOARD, buildRegistry, type DashboardEntry } from "../registry";

// The self-registering registry (docs/DASHBOARDS.md rule 8): every <slug>/entry.ts is collected by
// import.meta.glob and paired with its <slug>/thumbnail.svg, sorted by `order` then label, and a duplicate
// slug, an entry without a thumbnail or a thumbnail without an entry fails loud. The real list is pinned
// against the folders on disk, so a dashboard folder without an entry.ts or a thumbnail.svg can never
// silently fall off the menu or the index. (What a thumbnail may draw is thumbnails.test.ts.)

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

/** The thumbnail map for fake slugs: `{ "./<slug>/thumbnail.svg": "/<slug>.svg" }`. */
const thumbs = (...slugs: string[]): Record<string, string> =>
  Object.fromEntries(slugs.map((s) => [`./${s}/thumbnail.svg`, `/${s}.svg`]));

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
      "chain-map",
      "peer-table",
      "fundamentals-multiples",
      "narrative-adoption",
      "evidence-ledger",
      "hot-board",
      "entry-windows",
      "trigger-log",
      "ticker-lookup",
      "track-record",
      "forming-quietly",
      "theme-leaderboard",
      "insider-tracker",
    ]);
    expect(DASHBOARDS.map((d) => d.order)).toEqual([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260]);
    expect(DASHBOARDS[0]).toMatchObject({ slug: "board", status: "core", layout: "fill", asofAware: true });
  });

  it("every folder under dashboards/ registers itself (no folder without an entry.ts and a thumbnail.svg, no entry without a folder)", () => {
    const folders = readdirSync(DASHBOARDS_DIR, { withFileTypes: true })
      .filter((d) => d.isDirectory() && !d.name.startsWith("__"))
      .map((d) => d.name)
      .sort();
    for (const f of folders) {
      expect(existsSync(join(DASHBOARDS_DIR, f, "entry.ts")), `${f}/entry.ts`).toBe(true);
      expect(existsSync(join(DASHBOARDS_DIR, f, "thumbnail.svg")), `${f}/thumbnail.svg`).toBe(true);
    }
    expect([...DASHBOARDS.map((d) => d.slug)].sort()).toEqual(folders);
  });

  it("gives every real entry its OWN folder's thumbnail, as a URL", () => {
    // Under Vitest the URL is the file's path; a production build serves a hashed file under /assets instead.
    for (const d of DASHBOARDS) expect(d.thumbnail, `${d.slug} thumbnail`).toMatch(new RegExp(`/${d.slug}/thumbnail\\.svg$`));
  });

  it("names the home dashboard among its entries", () => {
    expect(DASHBOARDS.some((d) => d.slug === HOME_DASHBOARD)).toBe(true);
  });
});

describe("the dashboard registry — buildRegistry", () => {
  it("sorts by order, then label, whatever order the modules arrive in", () => {
    const got = buildRegistry(
      {
        "./zeta/entry.ts": mod(fake("zeta", 20, "Zeta")),
        "./beta/entry.ts": mod(fake("beta", 10, "Beta")),
        "./alpha/entry.ts": mod(fake("alpha", 10, "Alpha")),
        "./home/entry.ts": mod(fake("home", 0, "Home")),
      },
      thumbs("zeta", "alpha", "home", "beta"),
    );
    expect(got.map((d) => d.slug)).toEqual(["home", "alpha", "beta", "zeta"]);
  });

  it("pairs each entry with the thumbnail of its OWN folder, whatever order the maps arrive in", () => {
    const got = buildRegistry(
      { "./alpha/entry.ts": mod(fake("alpha", 10)), "./beta/entry.ts": mod(fake("beta", 20)) },
      thumbs("beta", "alpha"),
    );
    expect(got.map((d) => [d.slug, d.thumbnail])).toEqual([
      ["alpha", "/alpha.svg"],
      ["beta", "/beta.svg"],
    ]);
  });

  it("fails loud on a duplicate slug", () => {
    expect(() =>
      buildRegistry(
        {
          "./alpha/entry.ts": mod(fake("alpha", 10)),
          "./copy/alpha/entry.ts": mod(fake("alpha", 20)),
        },
        thumbs("alpha"),
      ),
    ).toThrow('duplicate slug "alpha" (./alpha/entry.ts and ./copy/alpha/entry.ts)');
  });

  it("fails loud on a module with no entry, or an entry whose slug is not its folder", () => {
    expect(() => buildRegistry({ "./alpha/entry.ts": {} }, thumbs("alpha"))).toThrow("does not export `entry`");
    expect(() => buildRegistry({ "./alpha/entry.ts": mod(fake("beta", 10)) }, thumbs("alpha"))).toThrow(
      'registers slug "beta", not its folder "alpha"',
    );
  });

  it("fails loud on an entry whose folder has no thumbnail.svg, naming the folder", () => {
    expect(() =>
      buildRegistry(
        { "./alpha/entry.ts": mod(fake("alpha", 10)), "./beta/entry.ts": mod(fake("beta", 20)) },
        thumbs("alpha"),
      ),
    ).toThrow('./beta/entry.ts has no thumbnail.svg beside it (folder "beta")');
    expect(() => buildRegistry({ "./alpha/entry.ts": mod(fake("alpha", 10)) }, {})).toThrow(
      'has no thumbnail.svg beside it (folder "alpha")',
    );
  });

  it("fails loud on a thumbnail.svg whose folder has no entry.ts, naming the folder", () => {
    expect(() =>
      buildRegistry({ "./alpha/entry.ts": mod(fake("alpha", 10)) }, thumbs("alpha", "orphan")),
    ).toThrow('./orphan/thumbnail.svg has no entry.ts beside it (folder "orphan")');
    expect(() => buildRegistry({}, thumbs("orphan"))).toThrow('has no entry.ts beside it (folder "orphan")');
  });
});
