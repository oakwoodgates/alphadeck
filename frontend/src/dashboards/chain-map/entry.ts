import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "chain-map",
  order: 140,
  label: "Chain Map",
  description:
    "One card as a picture: its links in chain order, the names inside each, and the payers, counter-cases and direct reads drawn beside the chain by role.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./ChainMap")),
};
