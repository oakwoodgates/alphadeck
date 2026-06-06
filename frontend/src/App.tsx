import { useState } from "react";

import { useTheses } from "./api/hooks";
import { Cockpit } from "./cockpit/Cockpit";

export function App() {
  const { data: theses, isLoading, error } = useTheses();
  // Default to the canonical armed date for HIMS; the as-of control scrubs (warming -> armed -> lapse).
  const [asof, setAsof] = useState("2026-06-01");

  if (isLoading) return <div className="center-note">Loading…</div>;
  if (error || !theses?.length) {
    return (
      <div className="center-note err">
        API not reachable or no thesis seeded — start the backend on :8000 and run{" "}
        <code>&nbsp;python -m pipeline.seed</code>.
      </div>
    );
  }

  // HIMS-only for now: the first (and only) thesis. The Board (PR-3) adds the pipeline + navigation.
  return <Cockpit thesisId={theses[0].id} asof={asof} onAsofChange={setAsof} />;
}
