import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "shared-names",
  order: 10,
  label: "Shared Names",
  description: "Companies that sit on more than one card, and which cards share them.",
  status: "experimental",
  asofAware: false,
  component: lazy(() => import("./SharedNames")),
};
