import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "catalyst-calendar",
  order: 60,
  label: "Catalyst Calendar",
  description:
    "Every dated catalyst and card deadline across your cards, with passed ones to prune and undated ones to date.",
  status: "experimental",
  asofAware: false,
  component: lazy(() => import("./CatalystCalendar")),
};
