import { Cockpit } from "./Cockpit";
import { useCaptureView } from "../mcp/viewContext";

export default function CockpitCapture({ asof }: { asof: string }) {
  const { settings } = useCaptureView();
  return <Cockpit thesisId={settings.thesis_id as string} asof={asof} onAsofChange={() => undefined}
    selectedName={(settings.selected_name as string | null) ?? null} onSelectName={() => undefined}
    railOpen={settings.rail_open !== false} onRailChange={() => undefined} />;
}
