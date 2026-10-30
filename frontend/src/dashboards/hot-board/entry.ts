import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "hot-board",
  order: 190,
  label: "Hot Board",
  description:
    "Every name the record shows Armed, one row each, ranked by a stated heat formula: how recently it armed, how loud its tape is, how it leads the market, how far it has already moved.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./HotBoard")),
};
