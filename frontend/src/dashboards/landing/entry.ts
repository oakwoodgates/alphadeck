import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "landing",
  order: 80,
  label: "Price & Volume Landing",
  description: "Where price closed in each window's range, and whether volume backed the move, by group.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./Landing")),
};
