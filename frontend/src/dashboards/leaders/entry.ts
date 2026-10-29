import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "leaders",
  order: 70,
  label: "Leaders",
  description:
    "Names across every card at new highs, leading the market, coiling, or with insider buying, and the groups where insiders cluster.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./Leaders")),
};
