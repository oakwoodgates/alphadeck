import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";

import { useTheses } from "./api/hooks";
import { Board } from "./board/Board";
import { Cockpit } from "./cockpit/Cockpit";
import { ASOF, boardPath, scoreboardPath, thesisPath, validAsof, workbenchPath } from "./nav";
import { Scoreboard } from "./scoreboard/Scoreboard";
import { todayISO } from "./util/format";
import { Workbench } from "./workbench/Workbench";

// App is the routing shell: each route wrapper below translates the URL (path + params) into the
// pages' existing callback-prop contracts, so the pages themselves stay router-free. The history
// model: PAGE moves push (Back walks views), view dials like the as-of scrub REPLACE (no spam).

/** The shared as-of dial, URL-backed (?asof=YYYY-MM-DD). Absent or malformed = TODAY (real
 *  operation) — the seeded HIMS demo armed on 2026-06-01; scrub back to see the canonical loop
 *  checkpoint. `asofParam` is the raw nullable value so navigations don't pin today into URLs. */
function useAsof() {
  const [searchParams, setSearchParams] = useSearchParams();
  const asofParam = validAsof(searchParams.get(ASOF));
  const asof = asofParam ?? todayISO();
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
 *  originating view (Board, Scoreboard, …) — the old tab-state fall-back behavior. A fresh-tab
 *  deep link carries no state and falls back to the Board. */
function useHere(): string {
  const location = useLocation();
  return location.pathname + location.search;
}

function BoardRoute() {
  const navigate = useNavigate();
  const here = useHere();
  const { asof, asofParam, setAsof } = useAsof();
  return (
    <Board
      asof={asof}
      onAsofChange={setAsof}
      onSelect={(id) => navigate(thesisPath(id, { asof: asofParam }), { state: { from: here } })}
      onOpenWorkbench={() => navigate(workbenchPath(asofParam))}
      onOpenScoreboard={() => navigate(scoreboardPath(asofParam))}
    />
  );
}

function ScoreboardRoute() {
  const navigate = useNavigate();
  const here = useHere();
  const { asof, asofParam, setAsof } = useAsof();
  return (
    <Scoreboard
      asof={asof}
      onAsofChange={setAsof}
      onBack={() => navigate(boardPath(asofParam))}
      onOpenWorkbench={() => navigate(workbenchPath(asofParam))}
      onSelect={(id) => navigate(thesisPath(id, { asof: asofParam }), { state: { from: here } })}
    />
  );
}

function WorkbenchRoute() {
  const navigate = useNavigate();
  const { asof, asofParam, setAsof } = useAsof();
  return (
    <Workbench
      asof={asof}
      onAsofChange={setAsof}
      onBack={() => navigate(boardPath(asofParam))}
      onOpenScoreboard={() => navigate(scoreboardPath(asofParam))}
    />
  );
}

function CockpitRoute() {
  const navigate = useNavigate();
  const location = useLocation();
  const { thesisId } = useParams();
  const { asof, asofParam, setAsof } = useAsof();
  if (!thesisId) return <Navigate to="/" replace />;
  const from = (location.state as { from?: string } | null)?.from;
  return (
    <Cockpit
      thesisId={thesisId}
      asof={asof}
      onAsofChange={setAsof}
      onBack={() => navigate(from ?? boardPath(asofParam))}
    />
  );
}

export function App() {
  const { isLoading, error, data: theses } = useTheses();

  // The guards use no router hooks, so loading/error render identically on every URL.
  if (isLoading) return <div className="center-note">Loading…</div>;
  if (error || !theses?.length) {
    return (
      <div className="center-note err">
        API not reachable or no thesis seeded — start the backend on :8000 and run{" "}
        <code>&nbsp;python -m pipeline.seed</code>.
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/" element={<BoardRoute />} />
      <Route path="/workbench" element={<WorkbenchRoute />} />
      <Route path="/scoreboard" element={<ScoreboardRoute />} />
      <Route path="/thesis/:thesisId" element={<CockpitRoute />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
