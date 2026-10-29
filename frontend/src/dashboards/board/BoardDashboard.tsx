import { Board } from "../../board/Board";
import type { DashboardProps } from "../registry";

// The Board as a dashboard (docs/DASHBOARDS.md, docs/BOARD.md): a thin wrapper, so the Board itself stays in
// frontend/src/board/ with its own tests. The shell draws the top bar and its as-of dial, so the Board gets
// no `header`. A card click opens the Cockpit through the shell's onSelectThesis: ?name= and ?asof= carried,
// Back returning to the page the click came from, exactly as the Board's own route did.

export default function BoardDashboard({ asof, onSelectThesis }: DashboardProps) {
  return <Board asof={asof} onSelect={onSelectThesis} />;
}
