import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "cross-radar",
  order: 50,
  label: "Cross Radar",
  description:
    "Golden and death crosses by group: clusters, first movers, names closing in, and the last 90 sessions.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./CrossRadar")),
};
