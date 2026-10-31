import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";

import { Admin } from "./admin/Admin";
import { AppHeader } from "./AppHeader";
import { useClock, useTheses } from "./api/hooks";
import { Backtest } from "./backtest/Backtest";
import { browserClock, clockFromServer, ClockProvider, useAppClock } from "./clock";
import { Cockpit } from "./cockpit/Cockpit";
import { DashboardShell } from "./dashboards/DashboardShell";
import { DashboardsIndex } from "./dashboards/DashboardsIndex";
import { DASHBOARDS, HOME_DASHBOARD } from "./dashboards/registry";
import {
  ASOF,
  EDITION,
  NAME,
  RAIL,
  RUN,
  dashboardsPath,
  editionFrom,
  homePath,
  railOpenFrom,
  thesisPath,
  validAsof,
} from "./nav";
import { Radar } from "./radar/Radar";
import { Scoreboard } from "./scoreboard/Scoreboard";
import { Workbench } from "./workbench/Workbench";

// App is the routing shell: each route wrapper below translates the URL (path + params) into the
// pages' existing callback-prop contracts, so the pages themselves stay router-free. The history
// model: PAGE moves push (Back walks views), view dials like the as-of scrub REPLACE (no spam).

/** The shared as-of dial, URL-backed (?asof=YYYY-MM-DD). Absent or malformed = the CURRENT NIGHT (F1): the
 *  night the nightly pass last recorded, from the server's clock (GET /clock) — never the browser's date, so the
 *  morning view is the call that night recorded and alerted on. The seeded HIMS demo armed on 2026-06-01; scrub
 *  back to see the canonical loop checkpoint. `asofParam` is the raw nullable value so navigations don't pin the
 *  default into URLs. */
function useAsof() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { currentNight } = useAppClock();
  const asofParam = validAsof(searchParams.get(ASOF));
  const asof = asofParam ?? currentNight;
  const setAsof = (v: string) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(ASOF, v);
        return next;
      },
      { replace: true },
    );
  return { asof, asofParam, setAsof };
}

/** The current URL, pushed as location.state.from so the Cockpit's Back returns to the
 *  originating view (Home, a dashboard, the Scoreboard, …) — the old tab-state fall-back behavior.
 *  A fresh-tab deep link carries no state and falls back to Home (`/`). */
function useHere(): string {
  const location = useLocation();
  return location.pathname + location.search;
}

function ScoreboardRoute() {
  const navigate = useNavigate();
  const here = useHere();
  const { asof, asofParam, setAsof } = useAsof();
  // The edition rides ?edition= (editions S4) so an edition is a shareable link — the ?run= precedent. A view
  // dial, so it REPLACES. Absent = the server's default (the newest published edition — so the top nav's
  // plain /scoreboard lands on it); picking one always names it, so a shared link keeps showing that edition
  // even after a newer one becomes the default.
  const [searchParams, setSearchParams] = useSearchParams();
  const onEditionChange = (n: number) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(EDITION, String(n));
        return next;
      },
      { replace: true },
    );
  return (
    <Scoreboard
      header={<AppHeader current="scoreboard" asof={asof} onAsofChange={setAsof} />}
      asof={asof}
      edition={editionFrom(searchParams.get(EDITION))}
      onEditionChange={onEditionChange}
      onSelect={(id, nameKey) =>
        navigate(thesisPath(id, { asof: asofParam, name: nameKey ?? null }), {
          state: { from: here },
        })
      }
    />
  );
}

function BacktestRoute() {
  const navigate = useNavigate();
  const here = useHere();
  const { asof, asofParam, setAsof } = useAsof();
  // The selected run rides ?run= so a run is a shareable link. A view dial like the as-of scrub and
  // the Cockpit's name selection, so it REPLACES — browsing runs never spams history.
  const [searchParams, setSearchParams] = useSearchParams();
  const onSelectRun = (id: string) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(RUN, id);
        return next;
      },
      { replace: true },
    );
  return (
    <Backtest
      header={<AppHeader current="backtest" asof={asof} onAsofChange={setAsof} />}
      runId={searchParams.get(RUN)}
      onSelectRun={onSelectRun}
      onSelect={(id, nameKey) =>
        navigate(thesisPath(id, { asof: asofParam, name: nameKey ?? null }), {
          state: { from: here },
        })
      }
    />
  );
}

function WorkbenchRoute() {
  const { asof, setAsof } = useAsof();
  return (
    <Workbench
      header={<AppHeader current="workbench" asof={asof} onAsofChange={setAsof} />}
      asof={asof}
    />
  );
}

function AdminRoute() {
  // A "now" ops surface — no as-of dial; AppHeader still carries ?asof= on its nav links.
  return <Admin header={<AppHeader current="admin" />} />;
}

function RadarRoute() {
  // Like Admin: a "now" watch surface — no as-of dial; AppHeader carries ?asof= on its nav links.
  return <Radar header={<AppHeader current="radar" />} />;
}

function DashboardsIndexRoute() {
  // A list of pages, not a view of data: no as-of dial; the index carries ?asof= onto every link.
  const { asofParam } = useAsof();
  return <DashboardsIndex asofParam={asofParam} />;
}

/** One dashboard in its shell. `home` = the route at `/`: it renders HOME_DASHBOARD in place (no redirect, so
 *  a `/` bookmark and its ?asof= keep working) with the Home tab lit. At /dashboards/:slug it renders that
 *  slug's entry with the Dashboards menu lit, the home dashboard included. */
function DashboardRoute({ home = false }: { home?: boolean }) {
  const navigate = useNavigate();
  const here = useHere();
  const params = useParams();
  const slug = home ? HOME_DASHBOARD : params.slug;
  const { asof, asofParam, setAsof } = useAsof();
  // The registry (dashboards/registry.ts) is the only list: a slug it does not name (a cut dashboard, a
  // typo) lands on the index, keeping the operator's as-of.
  const entry = DASHBOARDS.find((d) => d.slug === slug);
  if (!entry) return <Navigate to={dashboardsPath(asofParam)} replace />;
  return (
    <DashboardShell
      entry={entry}
      navKey={home ? "home" : "dashboards"}
      asof={asof}
      onAsofChange={setAsof}
      onSelectThesis={(id, nameKey) =>
        navigate(thesisPath(id, { asof: asofParam, name: nameKey ?? null }), {
          state: { from: here },
        })
      }
    />
  );
}

function CockpitRoute() {
  const navigate = useNavigate();
  const location = useLocation();
  const { thesisId } = useParams();
  const { asof, asofParam, setAsof } = useAsof();
  // The NamePanel's selection rides ?name= so a scoreboard row / shared link lands with the
  // panel open. Opening/closing/switching a name REPLACES (a selection, not a navigation — Back
  // still walks pages, and browsing names never spams history).
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedName = searchParams.get(NAME);
  const onSelectName = (key: string | null) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (key) next.set(NAME, key);
        else next.delete(NAME);
        return next;
      },
      { replace: true },
    );
  // The call rail's collapse rides ?rail=0 so it survives a reload and a shared link (app state
  // never goes to browser storage). A view dial like the as-of scrub and the name selection, so it
  // REPLACES; open is the default and writes by DELETING the param, keeping ordinary URLs clean.
  const railOpen = railOpenFrom(searchParams.get(RAIL));
  const onRailChange = (open: boolean) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (open) next.delete(RAIL);
        else next.set(RAIL, "0");
        return next;
      },
      { replace: true },
    );
  if (!thesisId) return <Navigate to="/" replace />;
  const from = (location.state as { from?: string } | null)?.from;
  return (
    <Cockpit
      thesisId={thesisId}
      asof={asof}
      onAsofChange={setAsof}
      onBack={() => navigate(from ?? homePath(asofParam))}
      selectedName={selectedName}
      onSelectName={onSelectName}
      railOpen={railOpen}
      onRailChange={onRailChange}
    />
  );
}

export function App() {
  const { isLoading, error, data: theses } = useTheses();
  // F1 — the server's clock decides the default as-of. Held until it settles (a reply or a failure), so no page
  // renders, and fires its reads, on a wrong date first; a failure falls back to the browser's date and the header
  // says so quietly.
  const clock = useClock();

  // The guards use no router hooks, so loading/error render identically on every URL.
  if (isLoading || clock.isPending) return <div className="center-note">Loading…</div>;
  if (error || !theses?.length) {
    return (
      <div className="center-note err">
        API not reachable or no thesis seeded — start the backend on :8000 and run{" "}
        <code>&nbsp;python -m pipeline.seed</code>.
      </div>
    );
  }

  return (
    <ClockProvider value={clock.data ? clockFromServer(clock.data) : browserClock(true)}>
      <AppRoutes />
    </ClockProvider>
  );
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<DashboardRoute home />} />
      <Route path="/workbench" element={<WorkbenchRoute />} />
      <Route path="/scoreboard" element={<ScoreboardRoute />} />
      <Route path="/backtest" element={<BacktestRoute />} />
      <Route path="/admin" element={<AdminRoute />} />
      <Route path="/radar" element={<RadarRoute />} />
      <Route path="/dashboards" element={<DashboardsIndexRoute />} />
      <Route path="/dashboards/:slug" element={<DashboardRoute />} />
      <Route path="/thesis/:thesisId" element={<CockpitRoute />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
