import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "filing-stream",
  order: 120,
  label: "Filing Stream",
  description:
    "Every filing-derived fact the system holds for one card's names, newest first, as it was readable that night: 8-Ks with their items, Form 4s with the filer's words, 13D/G stakes, revenue prints, splits, dilution, ratified catalysts.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./FilingStream")),
};
