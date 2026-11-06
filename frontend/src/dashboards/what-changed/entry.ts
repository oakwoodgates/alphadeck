import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "what-changed",
  order: 40,
  label: "What Changed",
  description: "What moved on your cards since last night: states, keys, names, triggers and clocks.",
  status: "experimental",
  asofAware: false,
  component: lazy(() => import("./WhatChanged")),
};
