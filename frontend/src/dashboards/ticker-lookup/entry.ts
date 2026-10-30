import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "ticker-lookup",
  order: 220,
  label: "Ticker Lookup",
  description:
    "Type a ticker: every card the company sits on and its part in each, the record's triggers on it, its tape, its filings and your decisions on it, at the as-of.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./TickerLookup")),
};
