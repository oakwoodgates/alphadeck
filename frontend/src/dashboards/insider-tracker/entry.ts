import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "insider-tracker",
  order: 260,
  label: "Insider Buying",
  description:
    "Every open-market insider purchase across the counted cards' names in the window, newest first: who bought, how much, whether others bought too, whether it became a trigger, and what the tape did since.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./InsiderTracker")),
};
