import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "read-through",
  order: 130,
  label: "Read-Throughs",
  description:
    "Every role-tagged company across your cards (payers, counter-cases, direct reads, participants, financiers, benchmarks) with what it last filed, its next dated catalyst, and its tape against SPY.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./ReadThrough")),
};
