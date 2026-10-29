import type { ComponentType, LazyExoticComponent } from "react";

// The ONE list of dashboards (docs/DASHBOARDS.md): experimental, self-contained cross-card views. The
// top nav's Dashboards menu, the /dashboards index and the /dashboards/:slug route all read this list, so
// adding a dashboard is one line here (plus its folder, frontend/src/dashboards/<slug>/) and cutting one is
// deleting that line and the folder. Every entry is lazy-loaded, so the Board never pays for dashboard code.

/** What the shell hands a dashboard body. `asof` is the app's as-of date (YYYY-MM-DD) for an as-of
 *  aware dashboard and today's date for a "now" one. `onSelectThesis` opens the Cockpit (optionally on one
 *  member, by ticker or security_id), with Back returning to the dashboard. */
export type DashboardProps = {
  asof: string;
  onSelectThesis: (thesisId: string, nameKey?: string | null) => void;
};

export type DashboardEntry = {
  /** The URL segment: /dashboards/<slug>. Also the folder name, frontend/src/dashboards/<slug>/. */
  slug: string;
  label: string;
  /** One line, shown under the title and in the menu. */
  description: string;
  status: "experimental";
  /** true = the page follows the app's as-of dial (the dial shows); false = a "now" view (no dial). */
  asofAware: boolean;
  /** React.lazy(() => import("./<slug>/<Component>")) — the component file default-exports its body. */
  component: LazyExoticComponent<ComponentType<DashboardProps>>;
};

// One line per dashboard; order = menu order; see docs/DASHBOARDS.md.
export const DASHBOARDS: readonly DashboardEntry[] = [];
