import { useState } from "react";

import type { ChainDraftOut, SavedRunSummary } from "../api/hooks";
import { useLoadThesisRun, useThesisRuns } from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { errText } from "./format";

interface Props {
  thesisId: string;
  // Hand the loaded run's draft to the caller (in the workbench that's ChainEditor's `applyDraft`, which
  // reproduces the full editable workbench). The picker knows NOTHING about what onLoad does or where it's
  // mounted — the port-to-a-page seam: a future standalone page mounts this same component with its own loader.
  onLoad: (draft: ChainDraftOut) => void;
  disabled?: boolean; // the caller disables while a live draft is in-flight (avoids a load-vs-poll race)
}

// UTC (the artifact's written_at is UTC ISO) — a dev/test tool label, so no locale/tz ceremony; trim to minutes.
const runLabel = (r: SavedRunSummary): string => {
  const when = r.written_at ? `${r.written_at.replace("T", " ").slice(0, 16)} UTC` : r.run_id;
  return `${when} · ${r.placement_count} placed · ${r.segment_count} links`;
};

/** The run-loader picker (a dev/test cost-saver): pick a SAVED draft run and load it into the editable
 *  workbench — no re-draft, no draft-API call. Self-contained + location-agnostic (props: a thesis id + an
 *  onLoad callback). SELF-HIDING off-switch: the backend `/runs` endpoints 404 when the single flag
 *  (`ALPHADECK_RUN_LOADER_ENABLED`) is off, so the list query errors and this renders nothing; it also hides
 *  for a thesis with no saved runs. The loaded JSON is the seed — everything is editable after. */
export function RunPicker({ thesisId, onLoad, disabled }: Props) {
  const runsQ = useThesisRuns(thesisId);
  const loadRun = useLoadThesisRun(thesisId);
  const [selected, setSelected] = useState("");

  const runs = runsQ.data ?? [];
  // The single flag drives FE presence: a 404 (loader disabled) → `isError`; no saved runs → empty. Either
  // way the whole feature is absent — no chrome, no dead control.
  if (runsQ.isError || runs.length === 0) return null;

  const chosen = selected || runs[0].run_id;
  const busy = disabled || loadRun.isPending;
  const load = async () => {
    try {
      const draft = await loadRun.mutateAsync(chosen);
      onLoad(draft);
    } catch {
      /* surfaced by loadRun.isError below — never an unhandled rejection */
    }
  };

  return (
    <div className="wb-run-loader">
      <span className="wb-run-loader-lab">Load a past run</span>
      <select
        aria-label="saved draft runs"
        value={chosen}
        disabled={busy}
        onChange={(e) => setSelected(e.target.value)}
      >
        {runs.map((r) => (
          <option key={r.run_id} value={r.run_id}>
            {runLabel(r)}
          </option>
        ))}
      </select>
      <button type="button" className="wb-mini" disabled={busy} onClick={load}>
        {loadRun.isPending ? "Loading…" : "↺ Load run"}
      </button>
      <span className="note">Seeds the editor from a saved draft — no new draft is run.</span>
      {loadRun.isError && (
        <ErrorToast>Couldn't load that run — {errText(loadRun.error)}.</ErrorToast>
      )}
    </div>
  );
}
