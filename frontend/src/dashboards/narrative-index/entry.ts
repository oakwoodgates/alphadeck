import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "narrative-index",
  order: 90,
  label: "Narrative Index",
  description:
    "Each card's basket as one equal-weighted line against SPY and IWM, with the day the card was created marked.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./NarrativeIndex")),
};
