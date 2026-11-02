import { lazy } from "react";

import type { DashboardEntry } from "../registry";
import capability from "./capability.json";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  ...capability,
  status: capability.status as DashboardEntry["status"],
  component: lazy(() => import("./TrackRecord")),
};
