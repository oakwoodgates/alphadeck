import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "trigger-log",
  order: 210,
  label: "Trigger Log",
  description:
    "Every entry trigger and risk signal the record shows firing across the cards in the last 90 days, newest first, with its source, the state it moved, and what the name's tape did after.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./TriggerLog")),
};
