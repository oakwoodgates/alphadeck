import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "overlap-map",
  order: 300,
  label: "Overlap Map",
  description: "Where the cards overlap: cards as bubbles joined by the companies they share, a Venn of any two or three, and each card's sector mix.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./OverlapMap")),
};
