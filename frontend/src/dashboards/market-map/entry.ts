import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "market-map",
  order: 270,
  label: "Market Map",
  description:
    "A heat map of every company on the cards: tiles sized by market cap, colored by the return over a chosen window.",
  status: "experimental",
  asofAware: true,
  layout: "page",
  component: lazy(() => import("./MarketMap")),
};
