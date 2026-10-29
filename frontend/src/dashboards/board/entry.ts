import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "board",
  order: 0,
  label: "Board",
  description: "Every card in its lifecycle column, with the Decision Queue.",
  status: "core",
  asofAware: true,
  layout: "fill",
  component: lazy(() => import("./BoardDashboard")),
};
