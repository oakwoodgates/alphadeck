import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10). The menu entry carries no count (invariant #7).
export const entry: DashboardEntry = {
  slug: "daily-digest",
  order: 280,
  label: "Daily Digest",
  description:
    "The morning page: what last night's pass changed, the positions, what is due this week, the insider buys just filed, the tape on the armed names and what is forming, each line opening its full page.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./DailyDigest")),
};
