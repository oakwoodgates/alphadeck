import { useNavigate, useSearchParams } from "react-router";

import { ASOF, adminPath, boardPath, radarPath, scoreboardPath, validAsof, workbenchPath } from "./nav";

// The ONE place a top-nav destination is declared (order = display order). Adding or removing a tab is
// a one-line change here, and every surface renders this exact bar: the routing shell (App.tsx) builds
// it per route and hands it to each page as its `header` slot, so the pages stay router-free — they
// never import a path or a navigate, and their tests need no Router. The active tab is inert; the rest
// navigate themselves, carrying ?asof= straight off the URL so even a "now" surface hands the param on.
export type NavKey = "board" | "workbench" | "scoreboard" | "radar" | "admin";

const LINKS: readonly { key: NavKey; label: string; to: (asof: string | null) => string }[] = [
  { key: "board", label: "Board", to: boardPath },
  { key: "workbench", label: "Workbench", to: workbenchPath },
  { key: "scoreboard", label: "Scoreboard", to: scoreboardPath },
  { key: "radar", label: "Radar", to: radarPath },
  { key: "admin", label: "Admin", to: adminPath },
];

type Props = {
  current: NavKey;
  // The as-of dial rides the bar on the as-of views (Board/Workbench/Scoreboard). The "now" surfaces
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
          l.key === current ? (
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
