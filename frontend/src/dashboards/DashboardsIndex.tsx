import { useMemo, useState } from "react";
import { Link } from "react-router";

import { AppHeader } from "../AppHeader";
import { dashboardPath } from "../nav";
import { groupDashboards, preloadDashboard, type DashboardGroup } from "./groups";
import { DASHBOARDS } from "./registry";

// The /dashboards page: every registry entry organized by navigation group (the Board first), each linking to its
// page with ?asof= carried, under its thumbnail (the drawing in the dashboard's own folder). No as-of dial
// of its own (a list of pages, not a view of data). See docs/DASHBOARDS.md.

type Props = {
  /** The raw ?asof= (null when absent), carried onto every link so the operator's as-of survives. */
  asofParam: string | null;
};

export function DashboardsIndex({ asofParam }: Props) {
  const [query, setQuery] = useState("");
  const [pickedGroup, setPickedGroup] = useState<DashboardGroup | "all">("all");

  const allGroups = useMemo(() => groupDashboards(DASHBOARDS), []);

  const visibleGroups = useMemo(() => {
    const q = query.trim().toLocaleLowerCase();
    return allGroups
      .filter((g) => pickedGroup === "all" || g.key === pickedGroup)
      .map((g) => ({
        ...g,
        items: q
          ? g.items.filter(
              (d) =>
                d.label.toLocaleLowerCase().includes(q) ||
                d.description.toLocaleLowerCase().includes(q) ||
                d.slug.toLocaleLowerCase().includes(q),
            )
          : g.items,
      }))
      .filter((g) => g.items.length > 0);
  }, [allGroups, pickedGroup, query]);

  const showGroupHeadings = allGroups.length > 1;

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
          <>
            <div className="dash-index-controls">
              <input
                type="search"
                className="dash-index-search"
                aria-label="Filter dashboards"
                placeholder="Filter dashboards…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {showGroupHeadings && (
                <div className="dash-index-groups" role="group" aria-label="Dashboard groups">
                  <button
                    type="button"
                    className={`dash-group-pill${pickedGroup === "all" ? " on" : ""}`}
                    aria-pressed={pickedGroup === "all"}
                    onClick={() => setPickedGroup("all")}
                  >
                    All <span className="dash-group-n">{DASHBOARDS.length}</span>
                  </button>
                  {allGroups.map((g) => {
                    if (g.key === "other") return null;
                    const groupKey = g.key;
                    return (
                      <button
                        key={groupKey}
                        type="button"
                        className={`dash-group-pill${pickedGroup === groupKey ? " on" : ""}`}
                        aria-pressed={pickedGroup === groupKey}
                        onClick={() => setPickedGroup(pickedGroup === groupKey ? "all" : groupKey)}
                      >
                        {g.label} <span className="dash-group-n">{g.items.length}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              {(query.trim() !== "" || pickedGroup !== "all") && (
                <button
                  type="button"
                  className="dash-index-clear"
                  onClick={() => {
                    setQuery("");
                    setPickedGroup("all");
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            {visibleGroups.length === 0 ? (
              <div className="dash-note">No dashboards match that filter.</div>
            ) : (
              <div className="dash-sections">
                {visibleGroups.map((g) => (
                  <section key={g.key} className="dash-group-sec" aria-label={g.label}>
                    {showGroupHeadings && (
                      <h2 className="dash-group-h">
                        {g.label}
                        <span className="dash-group-ct"> · {g.items.length}</span>
                      </h2>
                    )}
                    <ul className="dash-index">
                      {g.items.map((d) => (
                        <li key={d.slug}>
                          <Link
                            className="dash-tile"
                            to={dashboardPath(d.slug, asofParam)}
                            onMouseEnter={() => preloadDashboard(d)}
                            onFocus={() => preloadDashboard(d)}
                          >
                            {/* The drawing from the dashboard's own folder. Empty alt: the label sits right beside it. */}
                            <span className="dash-tile-thumb">
                              <img src={d.thumbnail} alt="" width={320} height={200} loading="lazy" decoding="async" />
                            </span>
                            <span className="dash-tile-head">
                              <span className="dash-tile-label">{d.label}</span>
                              <span className="dash-status">{d.status}</span>
                            </span>
                            <span className="dash-desc">{d.description}</span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
