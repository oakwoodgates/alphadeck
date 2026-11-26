import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, Cockpit 5 right beneath it in Daily & Calls, then steps of 10).
export const entry: DashboardEntry = {
  slug: "cockpit",
  order: 5,
  label: "Cockpit",
  description: "One thesis in full: its grouped basket, tape, catalysts, kill criteria and live call.",
  group: "daily",
  status: "core",
  asofAware: true,
  layout: "fill",
  openCompanyInCockpit: true,
  component: lazy(() => import("./CockpitDashboard")),
};
