import { lazy } from "react";

import type { DashboardEntry } from "../registry";

// This dashboard's one registry entry (docs/DASHBOARDS.md rule 8): registry.ts collects every
// <slug>/entry.ts on its own, so adding or cutting a dashboard edits no shared list. `order` is its menu
// place (the Board 0, then steps of 10).
export const entry: DashboardEntry = {
  slug: "narrative-adoption",
  order: 170,
  label: "Narrative Adoption",
  description:
    "How many filings mention a card's terms each month, over three years, split by who files them: annual and quarterly reports, 8-Ks, or new-issuance paper, and new filers against repeat filers.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => import("./NarrativeAdoption")),
};
