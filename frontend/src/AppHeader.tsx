import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { DASHBOARDS } from "./dashboards/registry";
import {
  ASOF,
  adminPath,
  backtestPath,
  dashboardPath,
  dashboardsPath,
  homePath,
  radarPath,
  scoreboardPath,
  validAsof,
  workbenchPath,
} from "./nav";

// The ONE place a top-nav destination is declared (order = display order). Adding or removing a tab is
// a one-line change here, and every surface renders this exact bar: the routing shell (App.tsx) builds
// it per route and hands it to each page as its `header` slot, so the pages stay router-free — they
// never import a path or a navigate, and their tests need no Router. The active tab is inert; the rest
// navigate themselves, carrying ?asof= straight off the URL so even a "now" surface hands the param on.
export type NavKey =
  | "home"
  | "dashboards"
  | "workbench"
  | "scoreboard"
  | "backtest"
  | "radar"
  | "admin";

const LINKS: readonly { key: NavKey; label: string; to: (asof: string | null) => string }[] = [
  // Home is `/`: the home dashboard (HOME_DASHBOARD in dashboards/registry.ts; today the Board). Lit only at
  // `/`; the same dashboard at /dashboards/<slug> lights the Dashboards menu instead.
  { key: "home", label: "Home", to: homePath },
  // A MENU, not a tab (DashboardsMenu below): "All dashboards" (this `to`, the index) and then one item per
  // entry in dashboards/registry.ts — the menu reads the registry, so a new dashboard adds no line here.
  { key: "dashboards", label: "Dashboards", to: dashboardsPath },
  { key: "workbench", label: "Workbench", to: workbenchPath },
  { key: "scoreboard", label: "Scoreboard", to: scoreboardPath },
  // ALWAYS rendered, never gated on data. The `available:false` idiom hides SECTIONS, not tabs, and
  // gating this one would cost a request on every page load to answer a question the page itself
  // answers in one quiet line. On prod the tab opens a page that says there are no runs.
  { key: "backtest", label: "Backtest", to: backtestPath },
  { key: "radar", label: "Radar", to: radarPath },
  { key: "admin", label: "Admin", to: adminPath },
];

type Props = {
  current: NavKey;
  // The as-of dial rides the bar on the as-of views (Home/Workbench/Scoreboard). The "now" surfaces
  // (Admin/Radar) pass neither and it is omitted — but the nav still carries ?asof= (read below).
  asof?: string;
  onAsofChange?: (v: string) => void;
};

export function AppHeader({ current, asof, onAsofChange }: Props) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const asofParam = validAsof(params.get(ASOF));
  return (
    <header className="topbar">
      <div className="brand">
        <span className="dot" />
        ALPHA&nbsp;DECK <small>// research cockpit</small>
      </div>
      <nav className="nav">
        {LINKS.map((l) =>
          l.key === "dashboards" ? (
            <DashboardsMenu
              key={l.key}
              label={l.label}
              on={current === "dashboards"}
              indexPath={l.to(asofParam)}
              asofParam={asofParam}
            />
          ) : l.key === current ? (
            <a key={l.key} className="on">
              {l.label}
            </a>
          ) : (
            <a key={l.key} onClick={() => navigate(l.to(asofParam))}>
              {l.label}
            </a>
          ),
        )}
      </nav>
      <div className="spacer" />
      {asof !== undefined && onAsofChange && (
        <label className="asof">
          as-of
          <input type="date" value={asof} onChange={(e) => onAsofChange(e.target.value)} />
        </label>
      )}
    </header>
  );
}

/** The Dashboards ▾ menu (docs/DASHBOARDS.md): "All dashboards" first, then every registry entry in
 *  registry order (label + its one-line description). Choosing an item navigates (carrying ?asof=) and
 *  closes the menu; Escape and a click outside close it too. Stays live on a dashboard page (`on`), so the
 *  operator can switch dashboards from any of them. */
function DashboardsMenu({
  label,
  on,
  indexPath,
  asofParam,
}: {
  label: string;
  on: boolean;
  indexPath: string;
  asofParam: string | null;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    // focus the first item so the menu is usable from the keyboard (the menu-button pattern)
    wrapRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const go = (path: string) => {
    setOpen(false);
    navigate(path);
  };

  return (
    <div className="nav-menu-wrap" ref={wrapRef}>
      <button
        type="button"
        ref={triggerRef}
        className={on ? "nav-menu-btn on" : "nav-menu-btn"}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {label} <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="nav-menu" role="menu" aria-label={label}>
          <button
            type="button"
            role="menuitem"
            className="nav-menu-item"
            onClick={() => go(indexPath)}
          >
            <span className="nav-menu-label">All dashboards</span>
          </button>
          {DASHBOARDS.length === 0 ? (
            <div className="nav-menu-empty" role="none">
              None yet
            </div>
          ) : (
            DASHBOARDS.map((d) => (
              <button
                key={d.slug}
                type="button"
                role="menuitem"
                className="nav-menu-item"
                onClick={() => go(dashboardPath(d.slug, asofParam))}
              >
                <span className="nav-menu-label">{d.label}</span>
                <span className="nav-menu-desc">{d.description}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
