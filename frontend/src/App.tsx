import { useState } from "react";

import { useTheses } from "./api/hooks";
import { Board } from "./board/Board";
import { Cockpit } from "./cockpit/Cockpit";

export function App() {
  const { isLoading, error, data: theses } = useTheses();
  // Shared across Board + Cockpit; default to the canonical armed date for HIMS (scrub to see the loop).
  const [asof, setAsof] = useState("2026-06-01");
  const [selected, setSelected] = useState<string | null>(null);

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
  return <Board asof={asof} onAsofChange={setAsof} onSelect={setSelected} />;
}
