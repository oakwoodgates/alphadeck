import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "evidence-ledger",
  order: 180,
  label: "Evidence Ledger",
  description:
    "Everything one card's call and facts rest on, in one list: each trigger with its source, each ratified and stored fact, each catalyst and kill criterion, marked by who put it there.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./EvidenceLedger")),
};
