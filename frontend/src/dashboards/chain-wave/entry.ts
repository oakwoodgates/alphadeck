import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "chain-wave",
  order: 110,
  label: "Chain Wave",
  description:
    "One card's links in chain order, week by week against SPY, so a move traveling along the chain shows as a diagonal; its payers beside its counting names.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./ChainWave")),
};
