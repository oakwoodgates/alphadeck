import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "peer-table",
  order: 150,
  label: "Peer Table",
  description:
    "The names across your cards side by side: size, revenue, runway, dilution, purity, tape and insider buying, grouped by business type, link or card, with every blank counted.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./PeerTable")),
};
