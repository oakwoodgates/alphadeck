import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "fundamentals-multiples",
  order: 160,
  label: "Fundamentals Multiples",
  description:
    "One card's revenue series, name by name and link by link: the last twelve quarters as small bars, each dated by its filing, with the link's median growth above the row.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./FundamentalsMultiples")),
};
