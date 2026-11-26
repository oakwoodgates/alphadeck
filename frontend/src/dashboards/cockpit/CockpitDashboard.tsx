import { Link, useSearchParams } from "react-router";

import { useTheses } from "../../api/hooks";
import { Cockpit } from "../../cockpit/Cockpit";
import { ASOF, NAME, RAIL, THESIS, railOpenFrom, workbenchPath } from "../../nav";
import type { DashboardProps } from "../registry";

// The Cockpit as a core dashboard (docs/DASHBOARDS.md, docs/dashboards/cockpit.md, docs/BOARD.md):
// a thin wrapper matching BoardDashboard.tsx so Cockpit itself stays router-free in frontend/src/cockpit/
// with its own tests. DashboardShell draws AppHeader (nav + as-of dial); this wrapper owns the URL-backed
// view dials (?thesis=, ?name=, ?rail=0) and feeds the active thesis list to the top switcher dropdown.

export default function CockpitDashboard({ asof }: DashboardProps) {
  const thesesQ = useTheses();
  const theses = thesesQ.data ?? [];
  const [searchParams, setSearchParams] = useSearchParams();

  const rawThesis = searchParams.get(THESIS);
  // Honor ?thesis= if it names a thesis (including a deep-linked archived card); otherwise default to
  // the first active thesis in workspace list order (never ranks theses, invariant #4).
  const activeThesisId = rawThesis || theses[0]?.id || "";

  const selectedName = searchParams.get(NAME);
  const railOpen = railOpenFrom(searchParams.get(RAIL));

  const setAsof = (v: string) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(ASOF, v);
        return next;
      },
      { replace: true },
    );

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

  const onSwitchThesis = (id: string, nameKey?: string | null) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(THESIS, id);
        if (nameKey) next.set(NAME, nameKey);
        else next.delete(NAME);
        return next;
      },
      { replace: true },
    );

  if (thesesQ.isLoading) return <div className="dash-note">Loading theses…</div>;
  if (!activeThesisId) {
    return (
      <div className="col-empty board-empty">
        No cards yet. Create your first thesis in the{" "}
        <Link to={workbenchPath(null)}>Workbench (+ New thesis)</Link>.
      </div>
    );
  }

  return (
    <Cockpit
      embedded
      thesisId={activeThesisId}
      theses={theses}
      asof={asof}
      onAsofChange={setAsof}
      selectedName={selectedName}
      onSelectName={onSelectName}
      railOpen={railOpen}
      onRailChange={onRailChange}
      onSwitchThesis={(id) => onSwitchThesis(id)}
      onSelectThesis={(id, nameKey) => onSwitchThesis(id, nameKey)}
    />
  );
}
