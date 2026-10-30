import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10). The menu entry carries no count (invariant #7).
export const entry: DashboardEntry = {
  slug: "forming-quietly",
  order: 240,
  label: "Forming Quietly",
  description:
    "The cards where a key has turned or signals are gathering but nothing is Armed yet, at low energy, with what is still missing.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./FormingQuietly")),
};
