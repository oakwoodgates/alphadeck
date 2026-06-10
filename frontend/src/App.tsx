import { useState } from "react";

import { useTheses } from "./api/hooks";
import { Board } from "./board/Board";
import { Cockpit } from "./cockpit/Cockpit";
import { Workbench } from "./workbench/Workbench";

export function App() {
  const { isLoading, error, data: theses } = useTheses();
  // Shared across Board + Cockpit + Workbench; default to the canonical armed date for HIMS (scrub to
  // see the loop).
  const [asof, setAsof] = useState("2026-06-01");
  const [selected, setSelected] = useState<string | null>(null);
  // The top-level view (tab-state, no router): the Board, or the Workbench front half. A selected
  // thesis opens the Cockpit and takes precedence.
  const [view, setView] = useState<"board" | "workbench">("board");

  if (isLoading) return <div className="center-note">Loading…</div>;
  if (error || !theses?.length) {
    return (
      <div className="center-note err">
        API not reachable or no thesis seeded — start the backend on :8000 and run{" "}
        <code>&nbsp;python -m pipeline.seed</code>.
      </div>
    );
  }

  if (selected) {
    return (
      <Cockpit
        thesisId={selected}
        asof={asof}
        onAsofChange={setAsof}
        onBack={() => setSelected(null)}
      />
    );
  }
  if (view === "workbench") {
    return <Workbench asof={asof} onAsofChange={setAsof} onBack={() => setView("board")} />;
  }
  return (
    <Board
      asof={asof}
      onAsofChange={setAsof}
      onSelect={setSelected}
      onOpenWorkbench={() => setView("workbench")}
    />
  );
}
