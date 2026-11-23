import { Suspense, useCallback, useEffect, useState } from "react";

import { AppHeader } from "../AppHeader";
import { useAppClock } from "../clock";
import { CompanyDrawer, type CompanyDrawerSelection } from "./CompanyDrawer";
import type { DashboardEntry, DashboardProps } from "./registry";

// The frame every dashboard renders inside (docs/DASHBOARDS.md): the shared top bar, a quiet title row
// (label, status, one-line description), then the entry's lazy body. A dashboard component renders ONLY its
// body, so every dashboard gets the same header, status tag and loading state without repeating them. A
// `layout: "fill"` entry (the Board) gets no title row and a body that fills the rest of the viewport.
// Company clicks (`onSelectThesis(id, nameKey)`) open the slide-over `CompanyDrawer` in place so the user
// stays on the dashboard; thesis clicks (`nameKey` omitted) and entries opting out (`openCompanyInCockpit`)
// navigate to the Cockpit as before.

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

  const [company, setCompany] = useState<CompanyDrawerSelection | null>(null);
  // Reset local drawer state when switching from one dashboard slug to another.
  useEffect(() => setCompany(null), [entry.slug]);

  const handleSelectThesis = useCallback<DashboardProps["onSelectThesis"]>(
    (thesisId, nameKey) => {
      if (nameKey && !entry.openCompanyInCockpit) {
        setCompany({ thesisId, nameKey });
        return;
      }
      onSelectThesis(thesisId, nameKey);
    },
    [entry.openCompanyInCockpit, onSelectThesis],
  );

  const handleOpenCockpit = useCallback(
    (thesisId: string, nameKey?: string | null) => {
      setCompany(null);
      onSelectThesis(thesisId, nameKey);
    },
    [onSelectThesis],
  );

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
          <Body asof={bodyAsof} onSelectThesis={handleSelectThesis} />
        </Suspense>
      </main>
      {company && (
        <CompanyDrawer
          selection={company}
          asof={bodyAsof}
          onClose={() => setCompany(null)}
          onSwitchCard={(thesisId, nameKey) => setCompany({ thesisId, nameKey })}
          onOpenCockpit={handleOpenCockpit}
        />
      )}
    </div>
  );
}
