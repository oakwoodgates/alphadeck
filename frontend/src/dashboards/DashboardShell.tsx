import { Suspense } from "react";

import { AppHeader } from "../AppHeader";
import { todayISO } from "../util/format";
import type { DashboardEntry, DashboardProps } from "./registry";

// The frame every dashboard renders inside (docs/DASHBOARDS.md): the shared top bar, a quiet title row
// (label, status, one-line description), then the entry's lazy body. A dashboard component renders ONLY its
// body, so every dashboard gets the same header, status tag and loading state without repeating them.

type Props = {
  entry: DashboardEntry;
  asof: string;
  onAsofChange: (v: string) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
};

export function DashboardShell({ entry, asof, onAsofChange, onSelectThesis }: Props) {
  const Body = entry.component;
  // An as-of aware dashboard shows the dial and reads the app's as-of. A "now" view (like Admin and Radar)
  // shows no dial, so it is handed today's date: a stale ?asof= in the URL can never quietly pin a page that
  // shows no dial to a past date.
  const bodyAsof = entry.asofAware ? asof : todayISO();
  return (
    <div className="dash-shell">
      {entry.asofAware ? (
        <AppHeader current="dashboards" asof={asof} onAsofChange={onAsofChange} />
      ) : (
        <AppHeader current="dashboards" />
      )}
      <div className="dash-title">
        <h1>{entry.label}</h1>
        <span className="dash-status">{entry.status}</span>
        <span className="dash-desc">{entry.description}</span>
      </div>
      <main className="dash-body">
        <Suspense fallback={<div className="dash-note">Loading…</div>}>
          <Body asof={bodyAsof} onSelectThesis={onSelectThesis} />
        </Suspense>
      </main>
    </div>
  );
}
