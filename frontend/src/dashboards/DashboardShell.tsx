import { Suspense } from "react";

import { AppHeader } from "../AppHeader";
import { useAppClock } from "../clock";
import type { DashboardEntry, DashboardProps } from "./registry";

// The frame every dashboard renders inside (docs/DASHBOARDS.md): the shared top bar, a quiet title row
// (label, status, one-line description), then the entry's lazy body. A dashboard component renders ONLY its
// body, so every dashboard gets the same header, status tag and loading state without repeating them. A
// `layout: "fill"` entry (the Board) gets no title row and a body that fills the rest of the viewport.

type Props = {
  entry: DashboardEntry;
  asof: string;
  onAsofChange: (v: string) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
  /** The lit top-nav item: "home" when this is the home dashboard at `/`, else the Dashboards menu (a
   *  dashboard at /dashboards/<slug>, the home one included). */
  navKey?: "home" | "dashboards";
};

export function DashboardShell({
  entry,
  asof,
  onAsofChange,
  onSelectThesis,
  navKey = "dashboards",
}: Props) {
  const Body = entry.component;
  // F1 — the server's today in market time (clock.ts), never the browser's date
  const { marketToday } = useAppClock();
  // An as-of aware dashboard shows the dial and reads the app's as-of. A "now" view (like Admin and Radar)
  // shows no dial, so it is handed today's date: a stale ?asof= in the URL can never quietly pin a page that
  // shows no dial to a past date.
  const bodyAsof = entry.asofAware ? asof : marketToday;
  const fill = entry.layout === "fill";
  return (
    <div className={fill ? "dash-shell fill" : "dash-shell"}>
      {entry.asofAware ? (
        <AppHeader current={navKey} asof={asof} onAsofChange={onAsofChange} />
      ) : (
        <AppHeader current={navKey} />
      )}
      {!fill && (
        <div className="dash-title">
          <h1>{entry.label}</h1>
          <span className="dash-status">{entry.status}</span>
          <span className="dash-desc">{entry.description}</span>
        </div>
      )}
      <main className={fill ? "dash-body fill" : "dash-body"}>
        <Suspense fallback={<div className="dash-note">Loading…</div>}>
          <Body asof={bodyAsof} onSelectThesis={onSelectThesis} />
        </Suspense>
      </main>
    </div>
  );
}
