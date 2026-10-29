import { Link } from "react-router";

import { AppHeader } from "../AppHeader";
import { dashboardPath } from "../nav";
import { DASHBOARDS } from "./registry";

// The /dashboards page: every registry entry, in registry order (the Board first), each linking to its
// page with ?asof= carried. No as-of dial of its own (a list of pages, not a view of data). See
// docs/DASHBOARDS.md.

type Props = {
  /** The raw ?asof= (null when absent), carried onto every link so the operator's as-of survives. */
  asofParam: string | null;
};

export function DashboardsIndex({ asofParam }: Props) {
  return (
    <div className="dash-shell">
      <AppHeader current="dashboards" />
      <div className="dash-title">
        <h1>Dashboards</h1>
        <span className="dash-desc">Views across the cards.</span>
      </div>
      <main className="dash-body">
        {DASHBOARDS.length === 0 ? (
          <div className="dash-note">No dashboards yet.</div>
        ) : (
          <ul className="dash-index">
            {DASHBOARDS.map((d) => (
              <li key={d.slug}>
                <Link className="dash-tile" to={dashboardPath(d.slug, asofParam)}>
                  <span className="dash-tile-head">
                    <span className="dash-tile-label">{d.label}</span>
                    <span className="dash-status">{d.status}</span>
                  </span>
                  <span className="dash-desc">{d.description}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}
