import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "entry-windows",
  order: 200,
  label: "Entry Windows",
  description:
    "Each Armed card's entry window and hold horizon on one date axis, where the as-of sits in them, and what the armed names have done since the arm night.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./EntryWindows")),
};
