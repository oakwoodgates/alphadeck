import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "rotation-quadrant",
  order: 100,
  label: "Rotation Quadrant",
  description:
    "Each group's strength vs SPY and its momentum, with an 8-week tail: leading, weakening, lagging, improving.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./RotationQuadrant")),
};
