import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "track-record",
  order: 230,
  label: "Track Record",
  description:
    "What happened after each Armed call, by market-cap tier at the event: fixed horizons, against SPY and IWM, with n and the hit rate in every cell.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./TrackRecord")),
};
