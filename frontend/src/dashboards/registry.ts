import { lazy, type ComponentType, type LazyExoticComponent } from "react";

// The ONE list of dashboards (docs/DASHBOARDS.md): self-contained cross-card views, the Board first. The
// top nav's Dashboards menu, the /dashboards index, the /dashboards/:slug route and the home page (`/`,
// HOME_DASHBOARD below) all read this list, so adding a dashboard is one line here (plus its folder,
// frontend/src/dashboards/<slug>/) and cutting one is deleting that line and the folder. Every entry is
// lazy-loaded, so a page loads only the dashboard it shows.

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
  /** "core" = a standing page of the app (the Board); "experimental" = a brainstorm view that may be cut.
   *  Both show as the same quiet tag. */
  status: "core" | "experimental";
  /** true = the page follows the app's as-of dial (the dial shows); false = a "now" view (no dial). */
  asofAware: boolean;
  /** "page" (the default) = the title row over a padded body that grows with its content. "fill" = no title
   *  row, and the body takes the rest of the viewport with no padding: for a page that scrolls inside
   *  itself, like the Board's columns. */
  layout?: "page" | "fill";
  /** React.lazy(() => import("./<slug>/<Component>")) — the component file default-exports its body. */
  component: LazyExoticComponent<ComponentType<DashboardProps>>;
};

/** The dashboard `/` renders (the top nav's Home tab), in place, never a redirect. Changing the home page
 *  is this one line: any slug in DASHBOARDS below. */
export const HOME_DASHBOARD = "board";

// One line per dashboard; order = menu order (the Board first; a new dashboard appends at the end); see
// docs/DASHBOARDS.md.
export const DASHBOARDS: readonly DashboardEntry[] = [
  {
    slug: "board",
    label: "Board",
    description: "Every card in its lifecycle column, with the Decision Queue.",
    status: "core",
    asofAware: true,
    layout: "fill",
    component: lazy(() => import("./board/BoardDashboard")),
  },
  {
    slug: "shared-names",
    label: "Shared Names",
    description: "Companies that sit on more than one card, and which cards share them.",
    status: "experimental",
    asofAware: false,
    component: lazy(() => import("./shared-names/SharedNames")),
  },
  {
    slug: "idea-timeline",
    label: "Idea Timeline",
    description: "Each card's recorded state over time, with its catalysts and your logged decisions.",
    status: "experimental",
    asofAware: false,
    component: lazy(() => import("./idea-timeline/IdeaTimeline")),
  },
  {
    slug: "rotation-map",
    label: "Rotation Map",
    description:
      "Which links, business types and sectors are moving: returns, breadth, volume and crosses by group.",
    status: "experimental",
    asofAware: true,
    component: lazy(() => import("./rotation-map/RotationMap")),
  },
  {
    slug: "what-changed",
    label: "What Changed",
    description: "What moved on your cards since last night: states, keys, names, triggers and clocks.",
    status: "experimental",
    asofAware: false,
    component: lazy(() => import("./what-changed/WhatChanged")),
  },
  {
    slug: "cross-radar",
    label: "Cross Radar",
    description:
      "Golden and death crosses by group: clusters, first movers, names closing in, and the last 90 sessions.",
    status: "experimental",
    asofAware: true,
    component: lazy(() => import("./cross-radar/CrossRadar")),
  },
  {
    slug: "catalyst-calendar",
    label: "Catalyst Calendar",
    description:
      "Every dated catalyst and card deadline across your cards, with passed ones to prune and undated ones to date.",
    status: "experimental",
    asofAware: false,
    component: lazy(() => import("./catalyst-calendar/CatalystCalendar")),
  },
  {
    slug: "leaders",
    label: "Leaders",
    description:
      "Names across every card at new highs, leading the market, coiling, or with insider buying, and the groups where insiders cluster.",
    status: "experimental",
    asofAware: true,
    component: lazy(() => import("./leaders/Leaders")),
  },
];
