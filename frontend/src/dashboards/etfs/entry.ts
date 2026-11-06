import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "etfs",
  order: 290,
  label: "ETFs",
  description: "The funds on the cards from five angles: each fund against its card's companies, money in and out, size and trading, price trend, and which cards have a fund.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./Etfs")),
};
